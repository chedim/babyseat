//! The seat's local peers over shared memory (docs/graph-link.md, section 18). The seat is started with the fact `peers`; for each declared peer it makes the control rings and its
//! own cell table; the peer (the test, using graphlink-shm and graphlink-core as any other program would) attaches, opens a link with a `hello`, and the seat shows it as `Vm:<ns>`.
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::{Frame, Op, Target, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::cells::{Cell, CellState, Cells, CellsReader, Reclaim};
use graphlink_shm::Shm;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }
struct Run { child: Child, _lines: Receiver<String>, port: u16 }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("cliffvm-peers-{tag}-{}-{}", std::process::id(), common::unique()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
/// a peer name short enough for a region name (the longest region name is 31 bytes: `/gl-<name>-ctl`)
fn peer_name() -> String { format!("t{}x{}", std::process::id() % 100000, common::unique().rsplit('-').next().unwrap()) }

/// removes the region names of a peer when the test ends: the seat is killed, not stopped, so nobody else would
struct Names(String);
impl Drop for Names { fn drop(&mut self) { for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{}-{part}", self.0)); } } }

fn command(dir: &std::path::Path, facts: &[String]) -> Command { command_with(dir, facts, &[]) }
fn command_with(dir: &std::path::Path, facts: &[String], extra: &[std::path::PathBuf]) -> Command {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let mut packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    packs.extend(extra.iter().map(|p| p.to_string_lossy().to_string()));
    let mut cmd = Command::new(cliffvm_bin());
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
    cmd
}
fn start(dir: &std::path::Path, facts: &[String]) -> Run { start_with(dir, facts, &[]) }
fn start_with(dir: &std::path::Path, facts: &[String], extra: &[std::path::PathBuf]) -> Run {
    let mut child = command_with(dir, facts, extra).stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).unwrap_or_else(|_| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()));
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Run { child, _lines: rx, port }
}
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    (head.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0), body.to_string())
}
fn call(port: u16, tool: &str, args: J) -> String {
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    assert_eq!(st, 200, "{body}");
    let r: J = serde_json::from_str(&body).unwrap();
    r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string()
}
fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": spec, "prop": prop})) }

fn decl(name: &str) -> String { decl_caps(name, 1024) }

/// the peer's end of the control rings: attach (the seat makes them at start), and run a Link over them
struct PeerEnd { ctl: Shm, link: Link, events: Vec<Event> }
impl PeerEnd {
    fn attach(name: &str, ns: &str) -> PeerEnd {
        let end = Instant::now() + Duration::from_secs(60);
        let ctl = loop { match Shm::attach(format!("/gl-{name}-ctl")) { Ok(s) => break s, Err(e) => { assert!(Instant::now() < end, "the seat never made its control rings: {e}"); std::thread::sleep(Duration::from_millis(50)); } } };
        let mut p = PeerEnd { ctl, link: Link::new(Me { ns: ns.into(), build: "test-peer".into(), epoch: 1.0, support: vec![VERSION], initiator: false }), events: vec![] };
        let hello = p.link.open();
        p.ctl.send(&hello).unwrap();
        p
    }
    fn until(&mut self, ms: u64, done: impl Fn(&[Event]) -> bool) -> bool {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end {
            while let Some(f) = self.ctl.recv().unwrap() {
                let f: Frame = f;
                let (ev, reply) = self.link.receive(f);
                self.events.extend(ev);
                if let Some(r) = reply { self.ctl.send(&r).unwrap(); }
            }
            if done(&self.events) { return true; }
            std::thread::sleep(Duration::from_millis(10));
        }
        false
    }
}

/// the owner's numbers for one peer: the seat's own table (slots, key_cap, val_cap), the rings, and how it reads the peer's table (`spins`: tries of a slot being written, `depth`: how deep a value nests)
fn decl_caps(name: &str, val_cap: u32) -> String { json!([{"name": name, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": val_cap, "spins": 1_000_000, "depth": 32}]).to_string() }

fn wait_for(port: u16, spec: &str, prop: &str, want: &str, secs: u64) -> String {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        let v = read(port, spec, prop);
        if v.contains(want) || Instant::now() > end { return v; }
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[test]
fn a_declared_peer_opens_a_link_over_shared_memory_and_the_seat_shows_it_as_a_vm() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("open"), &[format!("peers={}", decl(&name))]);
    let mut peer = PeerEnd::attach(&name, "kitchen");
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "the seat's hello never came: {:?}", peer.events);
    let opened = peer.events.iter().find_map(|e| if let Event::Opened { peer_ns, version, .. } = e { Some((peer_ns.clone(), *version)) } else { None }).unwrap();
    assert_eq!(opened, ("membrane".to_string(), 1), "the seat says who it is: its namespace, and the version agreed");
    // the seat has taken the peer's hello: the peer is a Vm of kind peer, connected
    assert!(wait_for(run.port, "Vm:kitchen", "state", "connected", 30).contains("connected"), "the Vm of the peer: {}", read(run.port, "Vm:kitchen", "state"));
    assert!(read(run.port, "Vm:kitchen", "kind").contains("peer"));
    assert!(read(run.port, "Vm:kitchen", "link").contains("up"));
}

#[test]
fn a_peer_that_goes_away_is_shown_as_gone_not_deleted() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("gone"), &[format!("peers={}", decl(&name))]);
    let mut peer = PeerEnd::attach(&name, "garage");
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))));
    assert!(wait_for(run.port, "Vm:garage", "state", "connected", 30).contains("connected"));
    drop(peer);
    assert!(wait_for(run.port, "Vm:garage", "state", "gone", 30).contains("gone"), "{}", read(run.port, "Vm:garage", "state"));
    assert!(read(run.port, "Vm:garage", "link").contains("gone"), "the entity is still there");
}

#[test]
fn a_page_is_still_a_page_a_vm_without_a_link_keeps_kind_page() {
    let run = start(&tmp("page"), &[]);
    // a Vm written the way a page does it (caps), no peer involved
    call(run.port, "entity_write", json!({"spec": "Vm:p00000001", "prop": "caps", "value": {"protocol": 1}}));
    assert!(read(run.port, "Vm:p00000001", "kind").contains("page"));
}

#[test]
fn a_declaration_the_seat_cannot_use_stops_the_start_and_names_the_fact() {
    for (bad, why) in [
        (json!([{"name": "x".repeat(40), "ring_bytes": 4096, "slots": 8, "key_cap": 16, "val_cap": 16, "spins": 10, "depth": 4}]).to_string(), "name"),
        (json!([{"name": "ok", "slots": 8, "key_cap": 16, "val_cap": 16, "spins": 10, "depth": 4}]).to_string(), "ring_bytes"),
        (json!([{"name": "ok", "ring_bytes": 4096, "slots": 0, "key_cap": 16, "val_cap": 16, "spins": 10, "depth": 4}]).to_string(), "slots"),
        (json!([{"name": "ok", "ring_bytes": 4096, "slots": 8, "key_cap": 16, "val_cap": 16, "depth": 4}]).to_string(), "spins"),
        (json!([{"name": "ok", "ring_bytes": 4096, "slots": 8, "key_cap": 16, "val_cap": 16, "spins": 10}]).to_string(), "depth"),
        (json!({"name": "not a list"}).to_string(), "list"),
    ] {
        let dir = tmp("bad");
        let out = command(&dir, &[format!("peers={bad}")]).stdout(Stdio::null()).stderr(Stdio::piped()).output().unwrap();
        assert!(!out.status.success(), "{bad}");
        let err = String::from_utf8_lossy(&out.stderr);
        assert!(err.contains("peers") && err.contains(why), "{bad}: {err}");
    }
}

#[test]
fn a_seat_that_died_leaves_stale_region_names_and_the_next_start_clears_its_own() {
    let name = peer_name();
    let _names = Names(name.clone());
    // the names a dead seat would have left: made and never dropped
    std::mem::forget(Shm::create(format!("/gl-{name}-ctl"), 4096).unwrap());
    std::mem::forget(graphlink_shm::cells::Cells::create(format!("/gl-{name}-sc"), 4, 16, 16, 1).unwrap());
    let run = start(&tmp("stale"), &[format!("peers={}", decl(&name))]);
    let mut peer = PeerEnd::attach(&name, "kitchen");
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "the seat could not use its own names: {}", std::fs::read_to_string(std::env::temp_dir()).unwrap_or_default());
    drop(run);
}

// ---- subscriptions and cells: a peer asks for seat nodes, the seat publishes them in its cell table (the peer maps it read-only) -----------------------------------------------------

fn key(eid: &str, prop: &str) -> String { format!("{eid}\u{1}{prop}") }
fn put(port: u16, eid: &str, prop: &str, v: J) { call(port, "entity_write", json!({"spec": eid, "prop": prop, "value": v})); }

impl PeerEnd {
    fn say(&mut self, ops: Vec<Op>) { let f = self.link.send(ops).expect("the link is open"); self.ctl.send(&f).unwrap(); }
    fn open(name: &str, ns: &str) -> PeerEnd {
        let mut p = PeerEnd::attach(name, ns);
        assert!(p.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "the link never opened");
        p
    }
}
fn seat_cells(name: &str) -> CellsReader {
    let end = Instant::now() + Duration::from_secs(60);
    loop { match CellsReader::attach(format!("/gl-{name}-sc")) { Ok(r) => return r, Err(e) => { assert!(Instant::now() < end, "no cell table: {e}"); std::thread::sleep(Duration::from_millis(50)); } } }
}
/// poll a cell until `ok` holds (or the time is up) and return what it was last
fn cell_when(r: &CellsReader, k: &str, secs: u64, ok: impl Fn(&Option<Cell>) -> bool) -> Option<Cell> {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        let c = r.get(k, 1_000_000, 32).unwrap();
        if ok(&c) || Instant::now() > end { return c; }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn a_peer_that_subscribes_to_a_seat_node_reads_its_cells_and_sees_them_change() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("sub"), &[format!("peers={}", decl(&name))]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    put(run.port, "Lamp:desk", "level", json!(3));
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    peer.say(vec![Op::Sub { target: Target::Id("Lamp:desk".into()), props: None, limit: None }]);
    let power = cell_when(&cells, &key("Lamp:desk", "power"), 30, |c| c.is_some()).expect("the power cell");
    assert_eq!((power.state, power.value.clone(), power.rev), (CellState::Live, json!("off"), 1.0));
    assert_eq!(cell_when(&cells, &key("Lamp:desk", "level"), 30, |c| c.is_some()).unwrap().value, json!(3));
    assert_eq!(cells.get(&key("Lamp:desk", ""), 1_000_000, 32).unwrap().unwrap().state, CellState::Live, "the entity itself is a cell too");
    // the seat's node changes: the cell follows, with a higher revision
    put(run.port, "Lamp:desk", "power", json!("on"));
    let after = cell_when(&cells, &key("Lamp:desk", "power"), 30, |c| matches!(c, Some(c) if c.value == json!("on"))).unwrap();
    assert_eq!((after.value, after.rev), (json!("on"), 2.0));
    // a value that did not change does not move the revision
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(cells.get(&key("Lamp:desk", "level"), 1_000_000, 32).unwrap().unwrap().rev, 1.0);
}

#[test]
fn a_subscription_that_names_props_publishes_only_those() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("props"), &[format!("peers={}", decl(&name))]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    put(run.port, "Lamp:desk", "level", json!(3));
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    peer.say(vec![Op::Sub { target: Target::Id("Lamp:desk".into()), props: Some(vec!["power".into()]), limit: None }]);
    assert!(cell_when(&cells, &key("Lamp:desk", "power"), 30, |c| c.is_some()).is_some());
    std::thread::sleep(Duration::from_millis(300));
    assert!(cells.get(&key("Lamp:desk", "level"), 1_000_000, 32).unwrap().is_none(), "level was not asked for");
}

#[test]
fn a_prefix_subscription_publishes_every_node_under_it_and_the_ones_born_later() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("prefix"), &[format!("peers={}", decl(&name))]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    put(run.port, "Lamp:bed", "power", json!("on"));
    put(run.port, "Door:front", "locked", json!(true));
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    peer.say(vec![Op::Sub { target: Target::Prefix("Lamp:".into()), props: None, limit: None }]);
    assert!(cell_when(&cells, &key("Lamp:desk", "power"), 30, |c| c.is_some()).is_some());
    assert_eq!(cell_when(&cells, &key("Lamp:bed", "power"), 30, |c| c.is_some()).unwrap().value, json!("on"));
    put(run.port, "Lamp:hall", "power", json!("off"));
    assert!(cell_when(&cells, &key("Lamp:hall", "power"), 30, |c| c.is_some()).is_some(), "a node born after the subscription is published too");
    assert!(cells.get(&key("Door:front", "locked"), 1_000_000, 32).unwrap().is_none(), "outside the prefix");
}

#[test]
fn after_an_unsubscribe_the_cells_stop_following() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("unsub"), &[format!("peers={}", decl(&name))]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    peer.say(vec![Op::Sub { target: Target::Id("Lamp:desk".into()), props: None, limit: None }]);
    assert!(cell_when(&cells, &key("Lamp:desk", "power"), 30, |c| c.is_some()).is_some());
    peer.say(vec![Op::Unsub { target: Target::Id("Lamp:desk".into()) }]);
    std::thread::sleep(Duration::from_millis(400));
    put(run.port, "Lamp:desk", "power", json!("on"));
    std::thread::sleep(Duration::from_millis(600));
    assert_eq!(cells.get(&key("Lamp:desk", "power"), 1_000_000, 32).unwrap().unwrap().value, json!("off"), "nobody is subscribed: the cell keeps its last value");
}

#[test]
fn a_node_that_does_not_exist_is_a_gone_cell_and_a_value_that_does_not_fit_is_a_refused_one() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("edge"), &[format!("peers={}", decl_caps(&name, 64))]);
    put(run.port, "Lamp:desk", "note", json!("x".repeat(500)));
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    peer.say(vec![Op::Sub { target: Target::Id("Nope:1".into()), props: None, limit: None }, Op::Sub { target: Target::Id("Lamp:desk".into()), props: None, limit: None }]);
    let gone = cell_when(&cells, &key("Nope:1", ""), 30, |c| c.is_some()).expect("a cell for a node that is not there");
    assert_eq!(gone.state, CellState::Gone);
    let big = cell_when(&cells, &key("Lamp:desk", "note"), 30, |c| c.is_some()).expect("a cell for the big value");
    assert_eq!(big.state, CellState::Refused);
    assert!(big.value.as_str().unwrap().contains("value"), "the reason says which limit: {:?}", big.value);
}

// ---- the inbound side: a seat rule reads a node a peer owns; the seat asks for it (`sub`) and follows the peer's cells ---------------------------------------------------------------

/// a pack with a rule that reads a foreign node the peer owns, and one that reads a node nobody owns
fn probe_pack(dir: &std::path::Path) -> std::path::PathBuf {
    let p = dir.join("probe.c1f");
    std::fs::write(&p, "PACK probe 1\nIMPORT boot 1\nRESIDENT Probe\nProbe.seen getter: kitchen::Lamp:desk.power\nProbe.lost getter: nobody::Thing:1.state\n").unwrap();
    p
}
fn peer_cells(name: &str) -> graphlink_shm::cells::Cells { graphlink_shm::cells::Cells::create(format!("/gl-{name}-pc"), 64, 128, 1024, 1).unwrap() }
fn asked(ev: &[Event], eid: &str) -> Option<Option<Vec<String>>> {
    ev.iter().find_map(|e| if let Event::Op(Op::Sub { target: Target::Id(i), props, .. }) = e { if i == eid { Some(props.clone()) } else { None } } else { None })
}

#[test]
fn a_seat_rule_that_reads_a_peers_node_asks_for_it_and_follows_the_peers_cells() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("inbound");
    let run = start_with(&dir, &[format!("peers={}", decl(&name))], &[probe_pack(&dir)]);
    let mut mine = peer_cells(&name);
    let mut peer = PeerEnd::open(&name, "kitchen");
    put(run.port, "Probe:1", "x", json!(1));
    // the rule reads kitchen::Lamp:desk.power: the seat asks the peer for exactly that prop
    assert!(peer.until(30_000, |ev| asked(ev, "kitchen::Lamp:desk").is_some()), "the peer was never asked: {:?}", peer.events);
    assert_eq!(asked(&peer.events, "kitchen::Lamp:desk").unwrap(), Some(vec!["power".to_string()]));
    let before = read(run.port, "Probe:1", "seen");
    assert!(!before.contains("off"), "nothing has arrived yet: {before}");
    // the peer publishes its node: the rule follows
    mine.put("kitchen::Lamp:desk\u{1}", 1.0, &json!(true)).unwrap();
    mine.put("kitchen::Lamp:desk\u{1}power", 1.0, &json!("off")).unwrap();
    assert!(wait_for(run.port, "Probe:1", "seen", "off", 30).contains("off"), "the rule never saw the peer's value: {}", read(run.port, "Probe:1", "seen"));
    mine.put("kitchen::Lamp:desk\u{1}power", 2.0, &json!("on")).unwrap();
    let now_on = wait_for(run.port, "Probe:1", "seen", "\"on\"", 30);
    assert!(now_on.contains("on") && !now_on.contains("off"), "the change did not arrive: {now_on}");
    // the peer says the node is gone: the rule no longer sees the old value
    mine.mark("kitchen::Lamp:desk\u{1}", 3.0, CellState::Gone, "").unwrap();
    let end = Instant::now() + Duration::from_secs(30);
    loop { let v = read(run.port, "Probe:1", "seen"); if !v.contains("on") || Instant::now() > end { assert!(!v.contains("\"on\""), "still shows the gone node's value: {v}"); break; } std::thread::sleep(Duration::from_millis(100)); }
}

#[test]
fn a_node_no_peer_owns_is_not_asked_of_anyone() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("nobody");
    let run = start_with(&dir, &[format!("peers={}", decl(&name))], &[probe_pack(&dir)]);
    let _mine = peer_cells(&name);
    let mut peer = PeerEnd::open(&name, "kitchen");
    put(run.port, "Probe:1", "x", json!(1));
    assert!(peer.until(30_000, |ev| asked(ev, "kitchen::Lamp:desk").is_some()));
    peer.until(600, |_| false);
    assert!(asked(&peer.events, "nobody::Thing:1").is_none(), "kitchen was asked for a node of the namespace `nobody`");
}

#[test]
fn when_no_rule_reads_the_node_any_more_the_peer_is_told_to_stop() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("drop");
    let run = start_with(&dir, &[format!("peers={}", decl(&name))], &[probe_pack(&dir)]);
    let _mine = peer_cells(&name);
    let mut peer = PeerEnd::open(&name, "kitchen");
    put(run.port, "Probe:1", "x", json!(1));
    assert!(peer.until(30_000, |ev| asked(ev, "kitchen::Lamp:desk").is_some()));
    put(run.port, "Probe:1", "retire", json!(true));   // the rule goes with its entity
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Op(Op::Unsub { target: Target::Id(i) }) if i == "kitchen::Lamp:desk"))), "no unsub came: {:?}", peer.events);
}

// ---- forwarded writes: a peer asks the seat to write one of ITS nodes; a c1f policy (the fact `peer_writes`) decides, and the default is no ------------------------------------------------

fn wr(wid: &str, id: &str, prop: &str, value: J) -> Op { Op::Wr { wid: wid.into(), id: id.into(), prop: prop.into(), value } }
/// the receipt the peer gets for a write: None until it comes
fn receipt(ev: &[Event], wid: &str) -> Option<Option<String>> {
    ev.iter().find_map(|e| if let Event::Op(Op::Res { wid: w, refused }) = e { if w == wid { Some(refused.clone()) } else { None } } else { None })
}
fn rule_fact() -> String { format!("peer_writes={}", json!([{"peer": "kitchen", "prefix": "Lamp:"}])) }

#[test]
fn a_write_a_peer_asks_for_is_refused_by_default_with_the_reason_and_changes_nothing() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("wr-no"), &[format!("peers={}", decl(&name))]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    let mut peer = PeerEnd::open(&name, "kitchen");
    peer.say(vec![wr("w1", "Lamp:desk", "power", json!("on"))]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w1").is_some()), "no receipt came: {:?}", peer.events);
    let why = receipt(&peer.events, "w1").unwrap().expect("refused");
    assert!(why.contains("peer_writes"), "the reason names the fact that decides: {why}");
    assert!(read(run.port, "Lamp:desk", "power").contains("off"), "nothing was written");
}

#[test]
fn a_rule_in_the_fact_lets_a_peer_write_the_nodes_it_names_and_only_those() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("wr-yes"), &[format!("peers={}", decl(&name)), rule_fact()]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    put(run.port, "Door:front", "locked", json!(true));
    let mut peer = PeerEnd::open(&name, "kitchen");
    peer.say(vec![wr("w1", "Lamp:desk", "power", json!("on")), wr("w2", "Door:front", "locked", json!(false))]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w1").is_some() && receipt(ev, "w2").is_some()), "{:?}", peer.events);
    assert_eq!(receipt(&peer.events, "w1").unwrap(), None, "accepted: ok");
    assert!(receipt(&peer.events, "w2").unwrap().is_some(), "outside the prefix: refused");
    assert!(wait_for(run.port, "Lamp:desk", "power", "on", 30).contains("on"));
    let door = read(run.port, "Door:front", "locked");
    assert!(door.to_lowercase().contains("true") && !door.to_lowercase().contains("false"), "the door was not touched: {door}");
}

#[test]
fn a_rule_is_for_one_peer_another_peer_is_refused_by_it() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("wr-other"), &[format!("peers={}", decl(&name)), rule_fact()]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    let mut peer = PeerEnd::open(&name, "garage");   // the rule names `kitchen`
    peer.say(vec![wr("w1", "Lamp:desk", "power", json!("on"))]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w1").is_some()));
    assert!(receipt(&peer.events, "w1").unwrap().is_some());
    assert!(read(run.port, "Lamp:desk", "power").contains("off"));
}

#[test]
fn a_peer_cannot_ask_the_seat_to_write_a_node_of_another_namespace() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("wr-ns"), &[format!("peers={}", decl(&name)), format!("peer_writes={}", json!([{"peer": "kitchen", "prefix": ""}]))]);   // a rule that allows everything it can
    let mut peer = PeerEnd::open(&name, "kitchen");
    peer.say(vec![wr("w1", "garage::Lamp:1", "power", json!("on"))]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w1").is_some()));
    let why = receipt(&peer.events, "w1").unwrap().expect("refused");
    assert!(why.contains("not a node of this seat"), "{why}");
    drop(run);
}

// ---- reclaiming slots (graphlink docs/shared-memory.md, 4b): a retired entity becomes Gone cells, and the seat's table is reused once the peer has acknowledged the removals

/// the same declaration with the number of slots the test chooses
fn decl_slots(name: &str, slots: u64) -> String { json!([{"name": name, "ring_bytes": 65536, "slots": slots, "key_cap": 128, "val_cap": 1024, "spins": 1_000_000, "depth": 32}]).to_string() }
fn state_of(r: &CellsReader, eid: &str, prop: &str) -> Option<CellState> { r.get(&key(eid, prop), 1_000_000, 32).unwrap().map(|c| c.state) }

#[test]
fn a_retired_entity_under_a_subscribed_prefix_becomes_gone_cells_node_and_props() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("retire"), &[format!("peers={}", decl(&name))]);
    put(run.port, "Lamp:desk", "power", json!("off"));
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    peer.say(vec![Op::Sub { target: Target::Prefix("Lamp:".into()), props: None, limit: None }]);
    assert!(cell_when(&cells, &key("Lamp:desk", "power"), 30, |c| matches!(c, Some(c) if c.state == CellState::Live)).is_some());
    put(run.port, "Lamp:desk", "retire", json!(true));
    let node = cell_when(&cells, &key("Lamp:desk", ""), 30, |c| matches!(c, Some(c) if c.state == CellState::Gone));
    assert!(matches!(node, Some(ref c) if c.state == CellState::Gone), "the retired entity's own cell never became Gone: {node:?}");
    assert_eq!(state_of(&cells, "Lamp:desk", "power"), Some(CellState::Gone), "its props are Gone too, or their slots could never be reclaimed");
}

#[test]
fn entities_born_and_retired_round_after_round_pass_through_a_seat_table_too_small_for_them_all() {
    // 12 slots; each round makes three lamps of THREE cells each (the entity's own cell, its prop, and the `summary` the seat adds): nine cells, so two rounds cannot fit without reclaiming;
    // the peer acknowledges the removals as the library tells it to
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("churn"), &[format!("peers={}", decl_slots(&name, 12))]);
    let mut peer = PeerEnd::open(&name, "kitchen");
    let cells = seat_cells(&name);
    let _ = graphlink_shm::remove_region(&format!("/gl-{name}-pc"));
    let mut mine = Cells::create(format!("/gl-{name}-pc"), 8, 128, 1024, 1).unwrap();   // the peer's own table: the seat reads it, and the acknowledgements go in it
    let mut rec = Reclaim::new();
    peer.say(vec![Op::Sub { target: Target::Prefix("Lamp:".into()), props: None, limit: None }]);
    for round in 0..4 {
        let ids: Vec<String> = (0..3).map(|i| format!("Lamp:r{round}e{i}")).collect();
        for id in &ids { put(run.port, id, "power", json!("on")); }
        for id in &ids { for prop in ["", "power"] { assert!(cell_when(&cells, &key(id, prop), 30, |c| matches!(c, Some(c) if c.state == CellState::Live)).is_some_and(|c| c.state == CellState::Live), "round {round}: {id} {prop:?} never became a cell"); } }
        for id in &ids { put(run.port, id, "retire", json!(true)); }
        for id in &ids { assert!(cell_when(&cells, &key(id, ""), 30, |c| matches!(c, Some(c) if c.state == CellState::Gone)).is_some_and(|c| c.state == CellState::Gone), "round {round}: {id} never became Gone"); }
        // the peer has seen the removals and says so
        let seen = Reclaim::before(&cells);
        let _ = cells.scan("", 1_000_000, 32).unwrap();
        rec.after(seen, &cells, &mut mine, 1_000_000, 32).unwrap();
    }
}

// ---- the chat turn over membrama: a reply cell the peer refuses, or says is gone, ends the turn with the reason (no model: the peer is played by the test) ----------------------------

fn membrama_turn(tag: &str) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) { membrama_turn_in(&tmp(tag)) }
fn membrama_turn_in(dir: &std::path::Path) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) { membrama_turn_caps(dir, 1024) }
/// the seat with the peer `membrama` and a value cap of `val_cap` bytes (a page of the log must fit one cell)
fn membrama_turn_caps(dir: &std::path::Path, val_cap: u32) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) {
    let name = peer_name();
    let names = Names(name.clone());
    let peers = json!([{"name": name, "ns": "membrama", "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": val_cap, "spins": 1_000_000, "depth": 32}]);
    let entry = json!([{"name": "local", "backend": "membrama", "peer": name, "ns": "membrama", "max_new": 50, "enable_thinking": false}]);
    let facts = [format!("cloud_models={entry}"), "router_on=false".to_string(), "seat_backend=http://127.0.0.1:1".to_string(),
                 r#"minds={"loops":"local","cortex":"local","workers":"local","seat_default":"local"}"#.to_string(), format!("peers={peers}")];
    let run = start(dir, &facts);
    let mine = peer_cells(&name);
    let peer = PeerEnd::open(&name, "membrama");
    (run, names, peer, mine)
}
/// send a message and wait until the seat asks the peer for the reply of its first round: that is the reply's node id
fn send_and_wait_for_the_reply_ask(run: &Run, peer: &mut PeerEnd) -> (String, String) {
    let (st, body) = http(run.port, "POST", "/membrane/chat/message", &json!({"text": "hello"}).to_string());
    assert_eq!(st, 202, "{body}");
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    let reply = |ev: &[Event]| ev.iter().find_map(|e| if let Event::Op(Op::Sub { target: Target::Id(i), .. }) = e { i.starts_with("membrama::Reply:").then(|| i.clone()) } else { None });
    assert!(peer.until(60_000, |ev| reply(ev).is_some()), "the seat never asked for a reply: {:?}", peer.events);
    (id, reply(&peer.events).unwrap())
}
fn turn_ends(port: u16, id: &str) -> J {
    let end = Instant::now() + Duration::from_secs(60);
    loop {
        let (_, body) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        let v: J = serde_json::from_str(&body).unwrap_or(J::Null);
        if v["state"] == "done" || v["state"] == "error" { return v; }
        assert!(Instant::now() < end, "the turn never ended (it waits for a reply that will not come): {v}");
        std::thread::sleep(Duration::from_millis(200));
    }
}

#[test]
fn a_reply_cell_the_peer_refuses_ends_the_turn_and_says_why() {
    let (run, _names, mut peer, mut mine) = membrama_turn("mb-refused");
    let (id, reply) = send_and_wait_for_the_reply_ask(&run, &mut peer);
    mine.mark(&key(&reply, ""), 1.0, CellState::Refused, "the reply does not fit in a cell").unwrap();
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("the reply does not fit in a cell"), "the turn should say the peer's reason: {v}");
}

#[test]
fn a_reply_cell_the_peer_says_is_gone_ends_the_turn() {
    let (run, _names, mut peer, mut mine) = membrama_turn("mb-gone");
    let (id, reply) = send_and_wait_for_the_reply_ask(&run, &mut peer);
    mine.mark(&key(&reply, ""), 1.0, CellState::Gone, "").unwrap();
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("gone"), "the turn should say the reply is gone: {v}");
}

/// the message nodes of a turn that are on the seat (they are VIRTUAL: not rows, so `entity_query` does not list them; each is read by its id)
fn messages_of(port: u16, turn: &str) -> usize {
    (0..64).take_while(|i| read(port, &format!("membrane::LlmMsg:{}:{i}", turn.replace(':', "/")), "kind") == "message").count()
}
fn request_is_there(port: u16, turn: &str, round: u32) -> bool { read(port, &format!("membrane::LlmRequest:{}-{round}", turn.replace(':', "/")), "kind") == "request" }

#[test]
fn a_message_is_one_node_for_the_whole_turn_a_later_round_writes_only_its_new_messages() {
    // round 1 sends two messages (the system one and the user's); the peer answers with a tool call; round 2 sends the same two and the assistant's and the tool's: four nodes in all, not six
    let (run, names, mut peer, mut mine) = membrama_turn("mb-turn-nodes");
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);   // membrama reads the request through its nodes
    let (id, reply) = send_and_wait_for_the_reply_ask(&run, &mut peer);
    // the nodes are transient (never stored), and the peer still reads them: the request and its first message are live cells in the seat's table
    let seen = seat_cells(&names.0);
    let rid = reply.replace("membrama::Reply:", "membrane::LlmRequest:");
    let live = |c: &Option<Cell>| matches!(c, Some(c) if c.state == CellState::Live);
    assert!(cell_when(&seen, &key(&rid, "messages"), 30, live).is_some_and(|c| c.state == CellState::Live), "the peer never saw the request's messages");
    let first = format!("membrane::LlmMsg:main/message/{id}:0");
    assert!(cell_when(&seen, &key(&first, "role"), 30, live).is_some_and(|c| c.state == CellState::Live), "the peer never saw the first message");
    let turn = format!("main:message:{id}");
    let m0 = format!("membrane::LlmMsg:{}:0", turn.replace(':', "/"));
    assert_eq!(messages_of(run.port, &turn), 2, "round 1: {:?} {:?}", read(run.port, &m0, "kind"), read(run.port, &m0, "role"));
    let chunk = reply.replace("membrama::Reply:", "membrama::Chunk:") + ":0";
    for (k, v) in [("", json!(true)), ("text", json!(""))] { mine.put(&key(&chunk, k), 1.0, &v).unwrap(); }
    for (k, v) in [("", json!(true)), ("state", json!("done")), ("finish", json!("tool_calls")), ("chunks", json!(1)),
                   ("tool_calls", json!([{"name": "entity_read", "arguments": {"spec": "Lamp:x", "prop": "power"}}]))] { mine.put(&key(&reply, k), 1.0, &v).unwrap(); }
    let second = |ev: &[Event]| ev.iter().find_map(|e| if let Event::Op(Op::Sub { target: Target::Id(i), .. }) = e { (i.starts_with("membrama::Reply:") && *i != reply).then(|| i.clone()) } else { None });
    assert!(peer.until(60_000, |ev| second(ev).is_some()), "round 2 never asked for its reply: {:?}", peer.events);
    assert_eq!(messages_of(run.port, &turn), 4, "round 2 wrote its whole list again as new nodes");
}

#[test]
fn when_the_turn_ends_its_message_and_request_nodes_are_gone_from_the_seat() {
    let dir = tmp("mb-retire");
    let (run, _names, mut peer, mut mine) = membrama_turn_in(&dir);
    // membrama reads the request through a subscription on the nodes of the rung
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let (id, reply) = send_and_wait_for_the_reply_ask(&run, &mut peer);
    let turn = format!("main:message:{id}");
    assert!(messages_of(run.port, &turn) > 0 && request_is_there(run.port, &turn, 1));
    let chunk = reply.replace("membrama::Reply:", "membrama::Chunk:") + ":0";
    for (k, v) in [("", json!(true)), ("text", json!("OK"))] { mine.put(&key(&chunk, k), 1.0, &v).unwrap(); }
    for (k, v) in [("", json!(true)), ("state", json!("done")), ("finish", json!("stop")), ("chunks", json!(1))] { mine.put(&key(&reply, k), 1.0, &v).unwrap(); }
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    let end = Instant::now() + Duration::from_secs(30);
    while (messages_of(run.port, &turn) > 0 || request_is_there(run.port, &turn, 1)) && Instant::now() < end { std::thread::sleep(Duration::from_millis(200)); }
    assert_eq!((messages_of(run.port, &turn), request_is_there(run.port, &turn, 1)), (0, false), "the finished turn's nodes are still on the seat");
    // and in the store, or the next start loads them again and publishes every one as a live cell
    let rows = |c: &rusqlite::Connection| c.query_row("select count(*) from c_field where k like 'membrane::LlmMsg:%' or k like 'membrane::LlmRequest:%'", [], |r| r.get::<_, i64>(0)).unwrap();
    let db = rusqlite::Connection::open_with_flags(dir.join("seat.sqlite"), rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let end = Instant::now() + Duration::from_secs(30);
    while rows(&db) > 0 && Instant::now() < end { std::thread::sleep(Duration::from_millis(200)); }
    assert_eq!(rows(&db), 0, "the finished turn's nodes are retired on the seat but still in its store");
}

#[test]
fn the_requests_and_messages_of_a_running_turn_are_never_stored() {
    // they are the transport's, for the length of a round: a row would be loaded again at the next start and published as live cells, for a turn that is long over
    let dir = tmp("mb-ephemeral");
    let (run, _names, mut peer, _mine) = membrama_turn_in(&dir);
    let (id, _reply) = send_and_wait_for_the_reply_ask(&run, &mut peer);
    assert!(messages_of(run.port, &format!("main:message:{id}")) > 0, "the turn has no message nodes");
    let db = rusqlite::Connection::open_with_flags(dir.join("seat.sqlite"), rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let n = |like: &str| db.query_row("select count(*) from c_field where k like ?1", [like], |r| r.get::<_, i64>(0)).unwrap();
    // the store is being written: the turn's own entity is a row soon, and then the transport's nodes would be too
    let end = Instant::now() + Duration::from_secs(30);
    while n("membrane::Chat:%") == 0 && Instant::now() < end { std::thread::sleep(Duration::from_millis(200)); }
    assert!(n("membrane::Chat:%") > 0, "nothing is stored: the test proves nothing");
    std::thread::sleep(Duration::from_secs(3));
    assert_eq!((n("membrane::LlmMsg:%"), n("membrane::LlmRequest:%"), n("membrane::LlmTool:%")), (0, 0, 0), "the transport's nodes are stored");
}

// ---- the journal as a view: a peer reads the boundary log (what entered the VM) from its own cursor ---------------------------------------------------------------------------------------

/// the rows of the view's page, when the cell is live and holds a list of records
fn log_rows(c: &Option<Cell>) -> Option<Vec<J>> {
    match c { Some(c) if c.state == CellState::Live => c.value.as_array().cloned(), _ => None }
}

#[test]
fn a_peer_reads_the_journal_as_records_from_its_own_cursor() {
    // membrama's cursor is in ITS namespace (`membrama::LogAsk:main`: `from` is the first line it wants and `n` counts its asks, the cache breaker); the seat's view `membrane::LogView:membrama` answers with
    // `rows` (one small record per line: which entity and property changed, by whom, how big the new value is, NOT the value: the peer reads the entity itself), `next` and `head`. A node that goes
    // away is a row too (`op: "gone"`).
    let dir = tmp("logview");
    let (run, names, mut peer, mut mine) = membrama_turn_caps(&dir, 65_536);   // the owner's value cap: a page of 200 rows is about 30 KB
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::LogView:".into()), props: None, limit: None }]);
    put(run.port, "Lamp:desk", "power", json!("on"));
    let seen = seat_cells(&names.0);
    let view = "membrane::LogView:membrama";
    let ask_key = |p: &str| key("membrama::LogAsk:main", p);
    mine.put(&ask_key(""), 1.0, &json!(true)).unwrap();
    let mut asks = 0.0;
    // the peer asks for the page that starts at `from` and waits for it (the page says where it starts: its first row)
    let mut page = |from: u64| -> Vec<J> {
        asks += 1.0;
        mine.put(&ask_key("from"), asks, &json!(from)).unwrap();
        mine.put(&ask_key("n"), asks, &json!(asks as u64)).unwrap();
        let got = cell_when(&seen, &key(view, "rows"), 60, |c| log_rows(c).is_some_and(|r| r.first().is_some_and(|f| f["n"].as_u64() == Some(from))));
        log_rows(&got).unwrap_or_else(|| panic!("the page at {from} never came: {got:?}"))
    };
    let is_the_write = |r: &J| r["eid"] == "Lamp:desk" && r["prop"] == "power" && r["op"] != "gone";
    // the peer reads from the beginning, a page at a time, until it has the write
    let (mut from, mut found) = (0u64, None);
    for _ in 0..20 {
        let rows = page(from);
        let ns: Vec<u64> = rows.iter().map(|r| r["n"].as_u64().expect("a row has its number n")).collect();
        assert!(ns.windows(2).all(|w| w[0] < w[1]), "the rows are not in order: {ns:?}");
        if let Some(r) = rows.iter().find(|r| is_the_write(r)) { found = Some(r.clone()); break; }
        from = ns.last().unwrap() + 1;   // the page's own `next`
    }
    let row = found.expect("the write is not in the log the peer read");
    assert!(row["writer"].as_str().unwrap_or("").contains("mcp-write"), "the row does not say who wrote it: {row}");
    assert!(row.get("new").is_none() && row["size"].as_f64().is_some_and(|n| n > 0.0), "a row points at the change and says how big the value is, it does not carry it: {row}");
    // the node goes away: a row of its own, in a page after the one the peer has read
    let after = row["n"].as_u64().unwrap() + 1;
    put(run.port, "Lamp:desk", "retire", json!(true));
    let mut gone = None;
    for _ in 0..20 {
        let rows = page(after);
        assert!(rows.iter().all(|r| r["n"].as_u64().unwrap() >= after), "a page repeats lines the peer has read: {rows:?}");
        if let Some(r) = rows.iter().find(|r| r["eid"] == "Lamp:desk" && r["op"] == "gone") { gone = Some(r.clone()); break; }
        std::thread::sleep(Duration::from_millis(500));
    }
    assert!(gone.is_some(), "the end of the node never came in the log");
}
