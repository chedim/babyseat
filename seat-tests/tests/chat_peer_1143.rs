//! The chat turn over a Claude peer (docs/peer-chat.md): a `cloud_models` entry with `backend: "claude-peer"` selects the same rung as membrama (packs/chat_membrama.c1f), with the peer's own namespace
//! (`claude` here). The peer is played by the test (a fake peer attaching to the seat's rings, answering `claude::Reply:<rid>` and `claude::Chunk:<rid>:<k>` cells), as in peers_1143.rs: no Claude is involved.
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

fn peer_cells(name: &str) -> graphlink_shm::cells::Cells { graphlink_shm::cells::Cells::create(format!("/gl-{name}-pc"), 64, 128, 1024, 1).unwrap() }
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


fn start(dir: &std::path::Path, facts: &[String]) -> Run { start_with(dir, facts, &[]) }
fn decl_peer(name: &str) -> String { json!([{"name": name, "ns": "claude", "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 4096, "spins": 1_000_000, "depth": 32}]).to_string() }

/// the seat with a model `claude-via-peer` answered by the peer `name` in the namespace `claude`; the entry has no `max_new` and no `enable_thinking`: they do not apply to Claude
fn claude_turn(tag: &str) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) {
    let name = peer_name();
    let names = Names(name.clone());
    let entry = json!([{"name": "claude-via-peer", "backend": "claude-peer", "peer": name, "ns": "claude"}]);
    let facts = [format!("cloud_models={entry}"), "router_on=false".to_string(), "seat_backend=http://127.0.0.1:1".to_string(),
                 r#"minds={"loops":"claude-via-peer","cortex":"claude-via-peer","workers":"claude-via-peer","seat_default":"claude-via-peer"}"#.to_string(), format!("peers={}", decl_peer(&name))];
    let run = start(&tmp(tag), &facts);
    let mine = peer_cells(&name);
    let peer = PeerEnd::open(&name, "claude");
    (run, names, peer, mine)
}
fn send(run: &Run, text: &str) -> String {
    let (st, body) = http(run.port, "POST", "/membrane/chat/message", &json!({"text": text}).to_string());
    assert_eq!(st, 202, "{body}");
    serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string()
}
/// the node id of the reply the seat asks the peer for, and that it is in the peer's namespace; `not` skips an earlier round's
fn reply_ask(peer: &mut PeerEnd, not: Option<&str>) -> String {
    let find = |ev: &[Event]| ev.iter().find_map(|e| if let Event::Op(Op::Sub { target: Target::Id(i), .. }) = e { (i.starts_with("claude::Reply:") && Some(i.as_str()) != not).then(|| i.clone()) } else { None });
    assert!(peer.until(60_000, |ev| find(ev).is_some()), "the seat never asked the claude peer for a reply: {:?}", peer.events);
    find(&peer.events).unwrap()
}
/// the peer answers: the chunk, then the reply that says how many chunks and how it finished
fn answer(mine: &mut graphlink_shm::cells::Cells, reply: &str, text: &str, finish: &str, calls: Option<J>) {
    let chunk = reply.replace("claude::Reply:", "claude::Chunk:") + ":0";
    for (k, v) in [("", json!(true)), ("text", json!(text))] { mine.put(&key(&chunk, k), 1.0, &v).unwrap(); }
    let mut cells = vec![("", json!(true)), ("state", json!("done")), ("finish", json!(finish)), ("chunks", json!(1)), ("usage", json!({"read": 10, "loaded": 0, "generated": 5}))];
    if let Some(c) = calls { cells.push(("tool_calls", c)); }
    for (k, v) in cells { mine.put(&key(reply, k), 1.0, &v).unwrap(); }
}

#[test]
fn a_plain_turn_is_answered_by_a_claude_peer_in_its_own_namespace_and_the_request_carries_no_claude_settings() {
    let (run, names, mut peer, mut mine) = claude_turn("cp-plain");
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let id = send(&run, "hello");
    let reply = reply_ask(&mut peer, None);
    // the request names the peer and has NO max_new / enable_thinking: the entry gave none and none is invented
    let seen = seat_cells(&names.0);
    let rid = reply.replace("claude::Reply:", "membrane::LlmRequest:");
    let live = |c: &Option<Cell>| matches!(c, Some(c) if c.state == CellState::Live);
    let settings = cell_when(&seen, &key(&rid, "settings"), 30, live).expect("the request's settings");
    assert_eq!(settings.value, json!({}), "claude settings: {settings:?}");
    answer(&mut mine, &reply, "Hello from the peer", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Hello from the peer", "{v}");
}

#[test]
fn a_tool_call_of_a_claude_peer_is_run_by_the_seat_and_fed_back_in_the_next_round() {
    let (run, _names, mut peer, mut mine) = claude_turn("cp-tool");
    put(run.port, "Lamp:desk", "power", json!("zebra-42"));
    let id = send(&run, "read the lamp");
    let r1 = reply_ask(&mut peer, None);
    answer(&mut mine, &r1, "", "tool_calls", Some(json!([{"name": "entity_read", "arguments": {"spec": "Lamp:desk", "prop": "power"}}])));
    let r2 = reply_ask(&mut peer, Some(&r1));
    assert!(r2.ends_with("-2"), "round 2: {r2}");
    answer(&mut mine, &r2, "The lamp says zebra-42", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert!(v["tools"].as_array().map_or(false, |t| t.iter().any(|n| n == "entity_read")), "the seat should have run entity_read: {v}");
    assert!(v["response"].as_str().unwrap_or("").contains("zebra-42"), "{v}");
}

#[test]
fn a_claude_peer_that_goes_away_fails_the_turn_and_names_its_namespace() {
    let (run, _names, mut peer, _mine) = claude_turn("cp-gone");
    let id = send(&run, "hello");
    let _ = reply_ask(&mut peer, None);
    drop(peer);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("claude is not up"), "the turn should say the peer is not up: {v}");
}
