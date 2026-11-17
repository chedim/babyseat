//! The seat's local peers over shared memory (docs/graph-link.md, section 18). The seat is started with the fact `peers`; for each declared peer it makes the control rings and its
//! own cell table; the peer (the test, using graphlink-shm and graphlink-core as any other program would) attaches, opens a link with a `hello`, and the seat shows it as `Vm:<ns>`.
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::{Frame, Op, Target, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::cells::{Cell, CellState, CellsReader};
use graphlink_shm::Shm;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
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
    let mut cmd = Command::new(BIN);
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
