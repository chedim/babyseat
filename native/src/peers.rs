//! The seat's local peers over shared memory (docs/graph-link.md, section 18): the one native piece of the move to GraphLink. For each peer the owner declares (the fact `peers`) the
//! seat makes the control rings and its own cell table, runs a `graphlink_core::Link` over the rings, and shows the peer as `Vm:<its namespace>` (a `link` of `up` or `gone`). It is
//! driven from the loop's per-turn callback on the engine's thread (`Peers::pump`): it never waits, starts no thread, and every decision is the engine's.
#![cfg(unix)]
use graphlink_core::frame::VERSION;
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::{cells::Cells, Shm};
use serde_json::{json, Map, Value as J};

use crate::host::Host;

/// who the writes of the peer driver are, in the journal
const WRITER: &str = "link:peer";

struct Peer {
    name: String,
    ctl: Shm,
    /// the seat's own cell table for this peer (written when the peer subscribes; the peer maps it read-only)
    _cells: Cells,
    link: Link,
    /// the peer's namespace, from its hello
    ns: Option<String>,
    gone: bool,
}

pub struct Peers { list: Vec<Peer> }

fn field(o: &Map<String, J>, name: &str, k: &str) -> Result<u64, String> {
    o.get(k).and_then(J::as_u64).filter(|n| *n > 0).ok_or_else(|| format!("peers: `{k}` of {name:?} must be a number above zero (the owner's number: there is no default)"))
}

impl Peers {
    /// The fact `peers`: a list of `{name, ring_bytes, slots, key_cap, val_cap}`; the seat makes the regions `/gl-<name>-ctl` (the control rings) and `/gl-<name>-sc` (its cell table).
    pub fn from_fact(seat_ns: &str, v: &J) -> Result<Peers, String> {
        let items = v.as_array().ok_or("peers: the fact must be a list of {name, ring_bytes, slots, key_cap, val_cap}")?;
        let mut list = vec![];
        for item in items {
            let o = item.as_object().ok_or("peers: each peer is an object {name, ring_bytes, slots, key_cap, val_cap}")?;
            let name = o.get("name").and_then(J::as_str).filter(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')).ok_or("peers: `name` of a peer must be letters, digits, `-` and `_`")?;
            if format!("/gl-{name}-ctl").len() > 31 { return Err(format!("peers: the name {name:?} is too long (a region name is at most 31 bytes: /gl-<name>-ctl)")); }
            let ring = field(o, name, "ring_bytes")? as usize;
            let (slots, key_cap, val_cap) = (field(o, name, "slots")?, field(o, name, "key_cap")?, field(o, name, "val_cap")?);
            let (key_cap, val_cap) = (u32::try_from(key_cap).map_err(|_| "peers: `key_cap` is too big")?, u32::try_from(val_cap).map_err(|_| "peers: `val_cap` is too big")?);
            let epoch = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            let mut ctl = Shm::create(format!("/gl-{name}-ctl"), ring).map_err(|e| format!("peers: {name}: {e}"))?;
            let cells = Cells::create(format!("/gl-{name}-sc"), slots, key_cap, val_cap, epoch).map_err(|e| format!("peers: {name}: {e}"))?;
            let mut link = Link::new(Me { ns: seat_ns.into(), build: format!("cliffvm {}", env!("CARGO_PKG_VERSION")), epoch: epoch as f64, support: vec![VERSION], initiator: true });
            ctl.send(&link.open()).map_err(|e| format!("peers: {name}: {e}"))?;
            list.push(Peer { name: name.to_string(), ctl, _cells: cells, link, ns: None, gone: false });
        }
        Ok(Peers { list })
    }

    /// One turn of every peer: take the frames that arrived, tell the engine what they mean, answer. Never waits.
    pub fn pump(&mut self, h: &Host) {
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
        }
    }
}

impl Peer {
    fn eid(&self) -> Option<String> { self.ns.as_ref().map(|n| format!("Vm:{n}")) }

    fn event(&mut self, h: &Host, ev: Event) {
        match ev {
            Event::Opened { peer_ns, peer_build, peer_epoch, .. } => {
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
            _ => {}
        }
    }

    /// the peer is gone: its `Vm` stays (a peer is durable), its `link` says so
    fn go(&mut self, h: &Host) {
        self.gone = true;
        if let Some(eid) = self.eid() { h.write(&eid, "link", json!("gone"), WRITER); }
    }
}
