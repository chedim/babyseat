//! The seat's local peers over shared memory (docs/graph-link.md, section 18): the one native piece of the move to GraphLink. For each peer the owner declares (the fact `peers`) the
//! seat makes the control rings and its own cell table, runs a `graphlink_core::Link` over the rings, and shows the peer as `Vm:<its namespace>` (a `link` of `up` or `gone`). It is
//! driven from the loop's per-turn callback on the engine's thread (`Peers::pump`): it never waits, starts no thread, and every decision is the engine's.
#![cfg(unix)]
use std::collections::{BTreeMap, HashMap, HashSet};

use graphlink_core::frame::{Op, Target, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::{cells::{CellState, Cells, CellsReader}, Shm};
use serde_json::{json, Map, Value as J};

use crate::fields::LinkVal;
use crate::host::Host;

/// who the writes of the peer driver are, in the journal
const WRITER: &str = "link:peer";

struct Peer {
    name: String,
    ctl: Shm,
    /// the seat's own cell table for this peer (the peer maps it read-only): one cell per `(eid, prop)` the peer subscribed to, and one per entity (`eid` and an empty prop)
    cells: Cells,
    /// what the peer asked for: a node or a prefix, and the props it named (all when none)
    subs: Vec<(Target, Option<Vec<String>>)>,
    /// what is in the cells now: key -> (revision, value, state); a cell is rewritten only when its value or state changes
    published: HashMap<String, (f64, J, CellState)>,
    /// the peer's own cell table (it makes `/gl-<name>-pc`; mapped read-only once it exists), and the owner's numbers for reading it
    reader: Option<CellsReader>,
    spins: u64,
    depth: usize,
    /// what the seat has asked this peer for: node -> the props named in the `sub` sent
    asked: HashMap<String, Vec<String>>,
    /// the revision of each peer cell already given to the engine (key -> revision)
    seen: HashMap<String, f64>,
    /// the nodes the engine was told are gone or refused (so it is told once)
    told_down: HashSet<String>,
    /// cells that could not be read (reported once each)
    reported: HashSet<String>,
    link: Link,
    /// the peer's namespace, from its hello
    ns: Option<String>,
    gone: bool,
    /// with a door secret: the namespace the owner declared for this peer (its token says it), and the secret every hello is checked against
    declared: Option<String>,
    secret: Option<Vec<u8>>,
    /// a hello that failed its check: the link is closed and nothing the peer sends is taken
    refused: bool,
}

pub struct Peers {
    list: Vec<Peer>,
    /// the foreign keys the engine reads (from `link_notices`): node -> props; each goes to the peer that owns the namespace
    wanted: BTreeMap<String, Vec<String>>,
}

fn field(o: &Map<String, J>, name: &str, k: &str) -> Result<u64, String> {
    o.get(k).and_then(J::as_u64).filter(|n| *n > 0).ok_or_else(|| format!("peers: `{k}` of {name:?} must be a number above zero (the owner's number: there is no default)"))
}

impl Peers {
    /// The fact `peers`: a list of `{name, ring_bytes, slots, key_cap, val_cap, spins, depth}` (all the owner's numbers: the first four size the seat's side, `spins` and `depth` say how the seat reads the peer's cell table); the seat makes the regions `/gl-<name>-ctl` (the control rings) and `/gl-<name>-sc` (its cell table).
    pub fn from_fact(seat_ns: &str, v: &J, auth: Option<crate::admin::PeerAuth>, now: f64) -> Result<Peers, String> {
        let items = v.as_array().ok_or("peers: the fact must be a list of {name, ring_bytes, slots, key_cap, val_cap}")?;
        let mut list = vec![];
        for item in items {
            let o = item.as_object().ok_or("peers: each peer is an object {name, ring_bytes, slots, key_cap, val_cap}")?;
            let name = o.get("name").and_then(J::as_str).filter(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')).ok_or("peers: `name` of a peer must be letters, digits, `-` and `_`")?;
            if format!("/gl-{name}-ctl").len() > 31 { return Err(format!("peers: the name {name:?} is too long (a region name is at most 31 bytes: /gl-<name>-ctl)")); }
            let ring = field(o, name, "ring_bytes")? as usize;
            let (slots, key_cap, val_cap) = (field(o, name, "slots")?, field(o, name, "key_cap")?, field(o, name, "val_cap")?);
            let (spins, depth) = (field(o, name, "spins")?, field(o, name, "depth")? as usize);
            // with a door secret a peer's namespace is declared: the seat mints the peer's token with it (`ns` claim), and a hello must carry that token
            let declared = match &auth {
                None => None,
                Some(a) => {
                    let ns = o.get("ns").and_then(J::as_str).filter(|n| !n.is_empty() && !n.contains("::") && *n != seat_ns).ok_or_else(|| format!("peers: `ns` of {name:?} must be the peer's namespace (a name without `::`, not {seat_ns:?}): the seat has a door secret, so a peer's token names the namespace it may own"))?;
                    std::fs::create_dir_all(&a.dir).map_err(|e| format!("peers: {}: {e}", a.dir.display()))?;
                    let token = crate::jwt::mint_with(&a.secret, &format!("peer:{name}"), name, &[("ns", json!(ns))], a.ttl, now)?;
                    crate::admin::replace_private(&a.dir.join(format!("{name}.token")), &token)?;
                    Some(ns.to_string())
                }
            };
            let (key_cap, val_cap) = (u32::try_from(key_cap).map_err(|_| "peers: `key_cap` is too big")?, u32::try_from(val_cap).map_err(|_| "peers: `val_cap` is too big")?);
            let epoch = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            // the names are THIS seat's (the owner declared them for it): what a dead seat left behind under them is removed first
            for part in ["ctl", "sc"] { graphlink_shm::remove_region(&format!("/gl-{name}-{part}")).map_err(|e| format!("peers: {name}: {e}"))?; }
            let mut ctl = Shm::create(format!("/gl-{name}-ctl"), ring).map_err(|e| format!("peers: {name}: {e}"))?;
            let cells = Cells::create(format!("/gl-{name}-sc"), slots, key_cap, val_cap, epoch).map_err(|e| format!("peers: {name}: {e}"))?;
            let mut link = Link::new(Me { ns: seat_ns.into(), build: format!("cliffvm {}", env!("CARGO_PKG_VERSION")), epoch: epoch as f64, support: vec![VERSION], initiator: true });
            ctl.send(&link.open()).map_err(|e| format!("peers: {name}: {e}"))?;
            list.push(Peer { name: name.to_string(), ctl, cells, subs: vec![], published: HashMap::new(), reader: None, spins, depth, asked: HashMap::new(), seen: HashMap::new(), told_down: HashSet::new(), reported: HashSet::new(), link, ns: None, gone: false, declared, secret: auth.as_ref().map(|a| a.secret.clone()), refused: false });
        }
        Ok(Peers { list, wanted: BTreeMap::new() })
    }

    /// One turn of every peer: take the frames that arrived, tell the engine what they mean, answer. Never waits.
    pub fn pump(&mut self, h: &Host) {
        // what the engine reads from other namespaces, since the last turn
        let (subs, unsubs, _due) = h.link_notices();
        for n in subs { if let Some(eid) = n["eid"].as_str() { let e = self.wanted.entry(eid.to_string()).or_default(); for p in n["props"].as_array().into_iter().flatten().filter_map(J::as_str) { if !e.iter().any(|x| x == p) { e.push(p.to_string()); } } } }
        for n in unsubs {
            let Some(eid) = n["eid"].as_str() else { continue };
            if let Some(e) = self.wanted.get_mut(eid) { let drop: Vec<&str> = n["props"].as_array().into_iter().flatten().filter_map(J::as_str).collect(); e.retain(|p| !drop.contains(&p.as_str())); if e.is_empty() { self.wanted.remove(eid); } }
        }
        for p in self.list.iter_mut() {
            loop {
                match p.ctl.recv() {
                    Ok(None) => break,
                    Ok(Some(frame)) => {
                        let (events, reply) = p.link.receive(frame);
                        if let Some(r) = reply { let _ = p.ctl.send(&r); }
                        for ev in events { p.event(h, ev); }
                    }
                    Err(e) => { eprintln!("cliffvm: peer {}: {e}", p.name); p.go(h); break; }
                }
            }
            if !p.gone && p.ctl.is_closed() { p.go(h); }
            if !p.gone && !p.subs.is_empty() { p.publish(h); }
            if !p.gone && p.link.is_open() { p.ask(&self.wanted); p.follow(h); }
        }
    }
}

impl Peer {
    fn eid(&self) -> Option<String> { self.ns.as_ref().map(|n| format!("Vm:{n}")) }

    fn event(&mut self, h: &Host, ev: Event) {
        if self.refused { return; }
        match ev {
            Event::Opened { peer_ns, peer_build, peer_epoch, peer_token, .. } => {
                // with a door secret the hello's token must verify and name the namespace the owner declared for this peer; the peer is told only `refused: auth`, the reason goes to the log
                if let Some(secret) = self.secret.as_ref() {
                    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                    let verdict = graphlink_auth::check_hello(peer_token.as_deref(), &peer_ns, &["membrane"], secret, now)
                        .and_then(|_| if self.declared.as_deref() == Some(peer_ns.as_str()) { Ok(()) } else { Err(format!("namespace: this peer was declared for {:?}, not {peer_ns:?}", self.declared)) });
                    if let Err(why) = verdict {
                        eprintln!("cliffvm: peer {}: hello refused: {why}", self.name);
                        self.refused = true;
                        if let Ok(f) = self.link.send(vec![Op::Bye { why: "refused: auth".into() }]) { let _ = self.ctl.send(&f); self.ctl.flush(); }
                        return;
                    }
                }
                let eid = format!("Vm:{peer_ns}");
                let mut props = Map::new();
                props.insert("link".into(), json!("up"));
                props.insert("peer_name".into(), json!(self.name));
                props.insert("peer_build".into(), json!(peer_build));
                props.insert("peer_epoch".into(), json!(peer_epoch));
                if h.get(&eid, "link").is_null() { h.born(&eid, "Vm", props, WRITER); } else { h.write(&eid, "link", json!("up"), WRITER); }
                self.ns = Some(peer_ns);
                self.gone = false;
            }
            Event::Closed { .. } | Event::Failed { .. } => self.go(h),
            Event::Op(Op::Sub { target, props, .. }) => {
                self.subs.retain(|(t, _)| *t != target);   // the same target again replaces the props it named
                self.subs.push((target, props));
            }
            Event::Op(Op::Unsub { target }) => self.subs.retain(|(t, _)| *t != target),
            Event::Op(Op::Wr { wid, id, prop, value }) => self.forwarded(h, wid, id, prop, value),
            _ => {}
        }
    }

    /// Tell the peer what the engine reads of its namespace: a `sub` for each node (again when the props change), an `unsub` for a node nothing reads any more.
    fn ask(&mut self, wanted: &BTreeMap<String, Vec<String>>) {
        let Some(ns) = self.ns.clone() else { return };
        let mine = |eid: &str| eid.split_once("::").map_or(false, |(n, _)| n == ns);
        let mut ops = vec![];
        for (eid, props) in wanted.iter().filter(|(e, _)| mine(e)) {
            if self.asked.get(eid) != Some(props) { ops.push(Op::Sub { target: Target::Id(eid.clone()), props: Some(props.clone()), limit: None }); self.asked.insert(eid.clone(), props.clone()); }
        }
        let dropped: Vec<String> = self.asked.keys().filter(|e| !wanted.contains_key(*e)).cloned().collect();
        for eid in dropped { ops.push(Op::Unsub { target: Target::Id(eid.clone()) }); self.asked.remove(&eid); self.seen.retain(|k, _| !k.starts_with(&format!("{eid}\u{1}"))); self.told_down.remove(&eid); }
        if !ops.is_empty() { if let Ok(f) = self.link.send(ops) { let _ = self.ctl.send(&f); } }
    }

    /// Read the peer's cells for what was asked and give the engine every value whose revision is newer than the last one it was given (`feed_val`: a replica, never stored).
    fn follow(&mut self, h: &Host) {
        if self.reader.is_none() { self.reader = CellsReader::attach(format!("/gl-{}-pc", self.name)).ok(); }
        let Some(reader) = self.reader.as_ref() else { return };
        let (spins, depth) = (self.spins, self.depth);
        let mut feed: Vec<(String, LinkVal, f64)> = vec![];
        for (eid, props) in self.asked.iter() {
            let ekey = format!("{eid}\u{1}");
            match reader.get(&ekey, spins, depth) {
                Ok(Some(c)) if c.state != CellState::Live => {
                    if self.told_down.insert(eid.clone()) { feed.push((eid.clone(), if c.state == CellState::Gone { LinkVal::Gone } else { LinkVal::Refused(c.value.as_str().unwrap_or("refused").to_string()) }, c.rev)); }
                    continue;
                }
                Ok(Some(_)) => { if self.told_down.remove(eid) { self.seen.retain(|k, _| !k.starts_with(&ekey)); } }   // back after being gone: everything is given again
                Ok(None) => {}
                Err(e) => { if self.reported.insert(ekey.clone()) { eprintln!("cliffvm: peer {}: cell {ekey:?}: {e}", self.name); } continue; }
            }
            let mut map = Map::new();
            let mut rev = 0f64;
            for prop in props {
                let key = format!("{eid}\u{1}{prop}");
                match reader.get(&key, spins, depth) {
                    Ok(Some(c)) if c.state == CellState::Live => {
                        if self.seen.get(&key).map_or(true, |r| c.rev > *r) { self.seen.insert(key, c.rev); rev = rev.max(c.rev); map.insert(prop.clone(), c.value); }
                    }
                    Ok(Some(c)) => { if self.reported.insert(key.clone()) { eprintln!("cliffvm: peer {}: cell {key:?} is {:?}: {}", self.name, c.state, c.value); } }
                    Ok(None) => {}
                    Err(e) => { if self.reported.insert(key.clone()) { eprintln!("cliffvm: peer {}: cell {key:?}: {e}", self.name); } }
                }
            }
            if !map.is_empty() { feed.push((eid.clone(), LinkVal::Props(map), rev)); }
        }
        for (eid, val, rev) in feed { h.feed_val(&eid, val, rev); }
    }

    /// A write the peer asks for: it becomes a `LinkWrite` (a question), the c1f verdict decides (packs/link.c1f, the fact `peer_writes`), and the seat applies an accepted one as an ordinary outside
    /// write with the writer `link:<peer>`. A node of another namespace is never written. The peer gets a receipt either way.
    fn forwarded(&mut self, h: &Host, wid: String, id: String, prop: String, value: J) {
        let refused = (|| -> Option<String> {
            let Some(ns) = self.ns.clone() else { return Some("refused: the peer has not said who it is (no hello yet)".into()) };
            if id.split_once("::").map_or(false, |(n, _)| n != "membrane") { return Some(format!("not a node of this seat: {id}")); }
            let q = format!("LinkWrite:{}:{}", self.name, wid);
            let mut props = Map::new();
            for (k, v) in [("peer", json!(ns)), ("id", json!(id)), ("prop", json!(prop)), ("value", value.clone())] { props.insert(k.into(), v); }
            h.born(&q, "LinkWrite", props, WRITER);
            let verdict = h.get(&q, "verdict");
            h.write(&q, "retire", json!(true), WRITER);   // the question has its answer: it goes
            if verdict.as_str() == Some("accept") { h.write(&id, &prop, value, &format!("link:{ns}")); None }
            else { Some(verdict.as_str().map(str::to_string).unwrap_or_else(|| "refused: the policy gave no verdict".into())) }
        })();
        if let Ok(f) = self.link.send(vec![Op::Res { wid, refused }]) { let _ = self.ctl.send(&f); }
    }

    /// Write what the peer subscribed to into the seat's cell table: each node's cells, for the props it named (all when none), whose value changed since the last turn.
    fn publish(&mut self, h: &Host) {
        let mut eids: Vec<String> = vec![];
        for (t, _) in &self.subs {
            match t { Target::Id(e) => eids.push(e.clone()), Target::Prefix(p) => eids.extend(h.eids_with_prefix(p)) }
        }
        eids.sort();
        eids.dedup();
        for eid in eids {
            // a node another namespace owns is not the seat's to publish (a replica of the peer's own node would go straight back to it)
            if eid.split_once("::").map_or(false, |(ns, _)| ns != "membrane") { continue; }
            let wanted: Option<Vec<String>> = {
                let named: Vec<&Option<Vec<String>>> = self.subs.iter().filter(|(t, _)| match t { Target::Id(e) => *e == eid, Target::Prefix(p) => eid.starts_with(p.as_str()) }).map(|(_, p)| p).collect();
                if named.iter().any(|p| p.is_none()) { None } else { Some(named.iter().filter_map(|p| p.as_ref()).flatten().cloned().collect()) }
            };
            match h.entity_props(&eid) {
                None => self.cell(&format!("{eid}\u{1}"), CellState::Gone, json!(null)),
                Some(props) => {
                    self.cell(&format!("{eid}\u{1}"), CellState::Live, json!(true));
                    for (k, v) in props.iter().filter(|(k, _)| wanted.as_ref().map_or(true, |w| w.contains(k))) { self.cell(&format!("{eid}\u{1}{k}"), CellState::Live, v.clone()); }
                }
            }
        }
    }

    /// One cell: written when its value or state differs from what is there, at the next revision; a value that does not fit becomes a `Refused` cell that says why.
    fn cell(&mut self, key: &str, state: CellState, value: J) {
        let (rev, same) = match self.published.get(key) { Some((r, v, st)) => (*r, *st == state && *v == value), None => (0.0, false) };
        if same { return; }
        let rev = rev + 1.0;
        let wrote = match state {
            CellState::Live => self.cells.put(key, rev, &value),
            CellState::Gone => self.cells.mark(key, rev, CellState::Gone, ""),
            CellState::Refused => self.cells.mark(key, rev, CellState::Refused, value.as_str().unwrap_or("")),
        };
        match wrote {
            Ok(()) => { self.published.insert(key.to_string(), (rev, value, state)); }
            Err(why) if state == CellState::Live => {
                // the value does not fit its slot (or the key its capacity, or the table is full): the cell says so, instead of keeping an old value
                let reason = json!(why);
                // what is remembered is what was ASKED (a live value), so an unchanged value that does not fit is not written again every turn
                if self.cells.mark(key, rev, CellState::Refused, &why).is_ok() { self.published.insert(key.to_string(), (rev, value, state)); }
                else { eprintln!("cliffvm: peer {}: cannot publish {key:?}: {reason}", self.name); self.published.insert(key.to_string(), (rev, value, state)); }
            }
            Err(why) => { eprintln!("cliffvm: peer {}: cannot publish {key:?}: {why}", self.name); self.published.insert(key.to_string(), (rev, value, state)); }
        }
    }

    /// the peer is gone: its `Vm` stays (a peer is durable), its `link` says so
    fn go(&mut self, h: &Host) {
        self.gone = true;
        if let Some(eid) = self.eid() { h.write(&eid, "link", json!("gone"), WRITER); }
    }
}
