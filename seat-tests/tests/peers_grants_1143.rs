//! A write a peer asks for is accepted when the peer's hello token GRANTED it that right (#1143, docs/graph-link.md, section 18): the claim `grants` is a list of id prefixes (or the string "*"),
//! the engine puts it on the `LinkWrite` question and `packs/link.c1f` decides: a rule of the fact `peer_writes` OR a grant. Needs cliffvm 0.3.2 (the engine that reads the claim).
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::{Op, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::Shm;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }
struct Run { child: Child, _lines: Receiver<String>, port: u16, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("cliffvm-peerauth-{tag}-{}-{}", std::process::id(), common::unique()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn peer_name() -> String { format!("a{}x{}", std::process::id() % 100000, common::unique().rsplit('-').next().unwrap()) }
struct Names(String);
impl Drop for Names { fn drop(&mut self) { for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{}-{part}", self.0)); } } }

fn command(dir: &std::path::Path, facts: &[String]) -> Command {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(cliffvm_bin());
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    cmd.arg("--fact").arg(format!("door_secret_file={}", dir.join("door.secret").display()));
    cmd.arg("--fact").arg(format!("seat_data_dir={}", dir.display()));
    for f in facts { cmd.arg("--fact").arg(f); }
    cmd
}
fn start(dir: &std::path::Path, facts: &[String]) -> Run {
    let mut child = command(dir, facts).stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).unwrap_or_else(|_| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()));
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Run { child, _lines: rx, port, dir: dir.to_path_buf() }
}
fn read(run: &Run, spec: &str, prop: &str) -> String {
    let token = std::fs::read_to_string(run.dir.join("admin.token")).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_cliff", "arguments": {"code": format!("${{'{}'}}['{}']", spec, prop)}}}).to_string();
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", token.trim(), body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let r: J = serde_json::from_str(text.split_once("\r\n\r\n").map_or("", |x| x.1)).unwrap_or(J::Null);
    r["result"]["content"][0]["text"].as_str().unwrap_or("").to_string()
}
fn wait_for(run: &Run, spec: &str, prop: &str, want: &str, secs: u64) -> String {
    let end = Instant::now() + Duration::from_secs(secs);
    loop { let v = read(run, spec, prop); if v.contains(want) || Instant::now() > end { return v; } std::thread::sleep(Duration::from_millis(100)); }
}

fn decl(name: &str, ns: Option<&str>) -> String {
    let mut o = json!({"name": name, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024, "spins": 1_000_000, "depth": 32});
    if let Some(ns) = ns { o["ns"] = json!(ns); }
    json!([o]).to_string()
}

/// the peer's end, with the token it puts in its hello (or none)
struct PeerEnd { ctl: Shm, link: Link, events: Vec<Event> }
impl PeerEnd {
    fn attach(name: &str, ns: &str, token: Option<String>) -> PeerEnd {
        let end = Instant::now() + Duration::from_secs(60);
        let ctl = loop { match Shm::attach(format!("/gl-{name}-ctl")) { Ok(s) => break s, Err(e) => { assert!(Instant::now() < end, "the seat never made its control rings: {e}"); std::thread::sleep(Duration::from_millis(50)); } } };
        let mut link = Link::new(Me { ns: ns.into(), build: "test-peer".into(), epoch: 1.0, support: vec![VERSION], initiator: false });
        if let Some(t) = token { link = link.with_token(t); }
        let mut p = PeerEnd { ctl, link, events: vec![] };
        let hello = p.link.open();
        p.ctl.send(&hello).unwrap();
        p
    }
    fn until(&mut self, ms: u64, done: impl Fn(&[Event]) -> bool) -> bool {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end {
            while let Some(f) = self.ctl.recv().unwrap() {
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

fn secret(dir: &std::path::Path) -> Vec<u8> { std::fs::read_to_string(dir.join("door.secret")).unwrap().trim().as_bytes().to_vec() }
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }


fn say(p: &mut PeerEnd, ops: Vec<Op>) { let f = p.link.send(ops).expect("the link is open"); p.ctl.send(&f).unwrap(); }
fn wr(wid: &str, id: &str, prop: &str, value: J) -> Op { Op::Wr { wid: wid.into(), id: id.into(), prop: prop.into(), value } }
fn receipt(ev: &[Event], wid: &str) -> Option<Option<String>> {
    ev.iter().find_map(|e| if let Event::Op(Op::Res { wid: w, refused }) = e { if w == wid { Some(refused.clone()) } else { None } } else { None })
}
fn put(run: &Run, eid: &str, prop: &str, v: J) {
    let token = std::fs::read_to_string(run.dir.join("admin.token")).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": eid, "prop": prop, "value": v}}}).to_string();
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", token.trim(), body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
}
/// a seat with one peer `kitchen` whose hello token carries `grants` (None: no claim) and `facts` more; the peer is open
fn seat_with_grants(tag: &str, grants: Option<J>, facts: &[String]) -> (Run, Names, PeerEnd) {
    let name = peer_name();
    let names = Names(name.clone());
    let mut all = vec![format!("peers={}", decl(&name, Some("kitchen")))];
    all.extend(facts.iter().cloned());
    let run = start(&tmp(tag), &all);
    let mut claims = vec![("ns", json!("kitchen"))];
    if let Some(g) = grants { claims.push(("grants", g)); }
    let token = graphlink_auth::mint_with(&secret(&run.dir), "peer:kitchen", "P", &claims, 600.0, now()).unwrap();
    let mut peer = PeerEnd::attach(&name, "kitchen", Some(token));
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "the link never opened: {:?}", peer.events);
    (run, names, peer)
}
fn ask(peer: &mut PeerEnd, id: &str, prop: &str, value: J) -> Option<String> {
    say(peer, vec![wr("w1", id, prop, value)]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w1").is_some()), "no receipt: {:?}", peer.events);
    receipt(&peer.events, "w1").unwrap()
}

#[test]
fn a_grant_of_a_prefix_lets_the_peer_write_the_nodes_under_it_and_only_those() {
    let (run, _n, mut peer) = seat_with_grants("g-prefix", Some(json!(["Lamp:"])), &[]);
    put(&run, "Lamp:desk", "power", json!("off"));
    put(&run, "Door:front", "locked", json!(true));
    assert_eq!(ask(&mut peer, "Lamp:desk", "power", json!("on")), None, "granted: accepted");
    assert!(wait_for(&run, "Lamp:desk", "power", "on", 30).contains("on"));
    say(&mut peer, vec![wr("w2", "Door:front", "locked", json!(false))]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w2").is_some()));
    let why = receipt(&peer.events, "w2").unwrap().expect("outside the grant: refused");
    assert!(why.contains("peer_writes") && why.contains("grants"), "the reason names both the fact and the grants: {why}");
    assert!(read(&run, "Door:front", "locked").to_lowercase().contains("true"));
}

#[test]
fn a_grant_of_star_is_every_prefix() {
    let (run, _n, mut peer) = seat_with_grants("g-star", Some(json!(["*"])), &[]);
    put(&run, "Door:front", "locked", json!(true));
    assert_eq!(ask(&mut peer, "Door:front", "locked", json!(false)), None);
    assert!(wait_for(&run, "Door:front", "locked", "false", 30).to_lowercase().contains("false"));
}

#[test]
fn a_peer_with_no_grants_and_no_rule_is_refused_naming_the_fact_and_the_grants() {
    let (run, _n, mut peer) = seat_with_grants("g-none", None, &[]);
    put(&run, "Lamp:desk", "power", json!("off"));
    let why = ask(&mut peer, "Lamp:desk", "power", json!("on")).expect("refused");
    assert!(why.contains("peer_writes") && why.contains("grants"), "{why}");
    assert!(read(&run, "Lamp:desk", "power").contains("off"));
}

#[test]
fn a_rule_of_the_fact_still_lets_a_peer_with_no_grants_write() {
    let rule = format!("peer_writes={}", json!([{"peer": "kitchen", "prefix": "Lamp:"}]));
    let (run, _n, mut peer) = seat_with_grants("g-rule", None, &[rule]);
    put(&run, "Lamp:desk", "power", json!("off"));
    assert_eq!(ask(&mut peer, "Lamp:desk", "power", json!("on")), None);
}

#[test]
fn a_grant_never_reaches_a_node_of_another_namespace() {
    let (_run, _n, mut peer) = seat_with_grants("g-ns", Some(json!(["*"])), &[]);
    let why = ask(&mut peer, "garage::Lamp:1", "power", json!("on")).expect("refused");
    assert!(why.contains("not a node of this seat"), "{why}");
}

// ---- the privileged classes: a grant of `*` reaches them; what each gate of the seat does with a write of the writer `link:<ns>` -----------------------------------------------------------------

fn pack_text() -> &'static str { "PACK peerpack 1\nLamp.glow getter: 'lit'\n" }

#[test]
fn a_star_grant_loads_a_pack_by_writing_its_text() {
    let (run, _n, mut peer) = seat_with_grants("g-pack", Some(json!(["*"])), &[]);
    assert_eq!(ask(&mut peer, "Pack:peerpack", "text", json!(pack_text())), None, "accepted");
    assert!(wait_for(&run, "Pack:peerpack", "state", "loaded", 30).contains("loaded"), "the pack the peer wrote is loaded: {}", read(&run, "Pack:peerpack", "error"));
}

#[test]
fn a_prefix_grant_that_does_not_name_pack_cannot_load_a_pack() {
    let (run, _n, mut peer) = seat_with_grants("g-nopack", Some(json!(["Lamp:"])), &[]);
    assert!(ask(&mut peer, "Pack:peerpack", "text", json!(pack_text())).is_some(), "refused");
    assert!(!read(&run, "Pack:peerpack", "state").contains("loaded"));
}

#[test]
fn a_star_grant_runs_a_command_the_gate_of_effects_is_the_engines() {
    let (run, _n, mut peer) = seat_with_grants("g-cmd", Some(json!(["*"])), &[]);
    assert_eq!(ask(&mut peer, "Command:hi", "run", json!({"id": "r1", "cmd": ["echo", "granted"]})), None, "accepted by the policy");
    let out = wait_for(&run, "Command:hi", "output", "granted", 30);
    assert!(out.contains("granted"), "the command ran for the granted peer: {out} / {}", read(&run, "Command:hi", "run_result"));
}
