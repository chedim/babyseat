//! The whole path, in real processes: a seat (`cliffvm run`, with a door secret and a declared peer), the `graphlink-bridge` program, and a remote over TCP (the test plays it with graphlink-core and
//! graphlink-net). The seat has no socket for the remote: it reads and writes the remote's nodes through shared memory and the bridge (docs/graph-link.md, section 18).
#![cfg(all(feature = "bin", unix))]
mod common;

use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
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

fn command(dir: &std::path::Path, facts: &[String], extra: &[std::path::PathBuf]) -> Command {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let mut packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    packs.extend(extra.iter().map(|p| p.to_string_lossy().to_string()));
    let mut cmd = Command::new(cliffvm_bin());
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    cmd.arg("--fact").arg(format!("door_secret_file={}", dir.join("door.secret").display()));
    cmd.arg("--fact").arg(format!("seat_data_dir={}", dir.display()));
    for f in facts { cmd.arg("--fact").arg(f); }
    cmd
}
fn start(dir: &std::path::Path, facts: &[String]) -> Run { start_with(dir, facts, &[]) }
fn start_with(dir: &std::path::Path, facts: &[String], extra: &[std::path::PathBuf]) -> Run {
    let mut child = command(dir, facts, extra).stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
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


use graphlink_core::frame::{Frame, Op, Target, ValBody, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_net::{Listener, Tcp};

fn bridge_bin() -> &'static str { common::bin("graphlink-bridge") }
struct Kill(Child);
impl Drop for Kill { fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); } }

fn put(run: &Run, eid: &str, prop: &str, v: J) {
    let token = std::fs::read_to_string(run.dir.join("admin.token")).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": eid, "prop": prop, "value": v}}}).to_string();
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", token.trim(), body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
}

fn probe_pack(dir: &std::path::Path) -> std::path::PathBuf {
    let p = dir.join("probe.c1f");
    std::fs::write(&p, "PACK probe 1\nIMPORT boot 1\nRESIDENT Probe\nProbe.seen getter: kitchen::Lamp:desk.power\n").unwrap();
    p
}

/// the remote: speaks GraphLink over TCP as the namespace `kitchen`, owns `kitchen::Lamp:desk`
struct Remote { link: Link, tcp: Option<Tcp>, listener: Option<Listener>, subs: Vec<Target>, events: Vec<Event> }
impl Remote {
    fn listening() -> (Remote, String) {
        let l = Listener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        (Remote { link: Link::new(Me { ns: "kitchen".into(), build: "t".into(), epoch: 1.0, support: vec![VERSION], initiator: false }), tcp: None, listener: Some(l), subs: vec![], events: vec![] }, addr)
    }
    fn dial(addr: &str) -> Remote {
        let mut tcp = Tcp::connect(addr).unwrap();
        let mut link = Link::new(Me { ns: "kitchen".into(), build: "t".into(), epoch: 1.0, support: vec![VERSION], initiator: true });
        tcp.send(&link.open()).unwrap();
        Remote { link, tcp: Some(tcp), listener: None, subs: vec![], events: vec![] }
    }
    fn pump(&mut self) {
        // a connection that closed is forgotten: a listening remote takes the next one (a restarted bridge dials again), with a new link
        if self.listener.is_some() && self.tcp.as_ref().map_or(false, |t| t.is_closed()) {
            self.tcp = None;
            self.link = Link::new(Me { ns: "kitchen".into(), build: "t".into(), epoch: 1.0, support: vec![VERSION], initiator: false });
            self.subs.clear();
        }
        if self.tcp.is_none() {
            if let Some(Some(mut t)) = self.listener.as_ref().map(|l| l.accept().unwrap()) { t.send(&self.link.open()).unwrap(); self.tcp = Some(t); }
        }
        let Some(tcp) = self.tcp.as_mut() else { return };
        while let Ok(Some(f)) = tcp.recv() {
            let f: Frame = f;
            let (ev, reply) = self.link.receive(f);
            if let Some(r) = reply { tcp.send(&r).unwrap(); }
            for e in &ev { if let Event::Op(Op::Sub { target, .. }) = e { self.subs.push(target.clone()); } }
            self.events.extend(ev);
        }
    }
    fn until(&mut self, ms: u64, done: impl Fn(&Remote) -> bool) -> bool {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end { self.pump(); if done(self) { return true; } std::thread::sleep(Duration::from_millis(10)); }
        false
    }
    fn say(&mut self, ops: Vec<Op>) { let f = self.link.send(ops).expect("the link is open"); self.tcp.as_mut().unwrap().send(&f).unwrap(); }
    fn set(&mut self, id: &str, r: f64, props: J) { self.say(vec![Op::Val { id: id.into(), r, rid: None, body: ValBody::Props(props) }]); }
    fn receipt(&self, wid: &str) -> Option<Option<String>> {
        self.events.iter().find_map(|e| if let Event::Op(Op::Res { wid: w, refused }) = e { if w == wid { Some(refused.clone()) } else { None } } else { None })
    }
}

fn bridge(mode: &str, addr: &str, name: &str, dir: &std::path::Path) -> Kill {
    let token = dir.join("peers").join(format!("{name}.token"));
    Kill(Command::new(bridge_bin()).args([mode, addr, "--peer", name, "--slots", "64", "--key-cap", "128", "--val-cap", "1024", "--epoch", "1", "--spins", "1000000", "--depth", "32", "--token-file"]).arg(token)
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(std::fs::File::create(dir.join("bridge.log")).unwrap()).spawn().unwrap())
}

#[test]
fn a_seat_rule_reads_a_remote_node_through_the_bridge_with_no_socket_in_the_seat() {
    let name = peer_name();
    let _n = Names(name.clone());
    let dir = tmp("read");
    let (mut remote, addr) = Remote::listening();
    let run = start_with(&dir, &[format!("peers={}", decl(&name, Some("kitchen")))], &[probe_pack(&dir)]);
    let _b = bridge("connect", &addr, &name, &run.dir);
    put(&run, "Probe:1", "x", json!(1));
    // the seat's rule reads kitchen::Lamp:desk.power: the remote is asked (a sub through the bridge)
    assert!(remote.until(60_000, |r| r.subs.iter().any(|t| matches!(t, Target::Id(i) if i == "kitchen::Lamp:desk"))), "the remote was never asked: {:?} {}", remote.events, std::fs::read_to_string(run.dir.join("bridge.log")).unwrap_or_default());
    remote.set("kitchen::Lamp:desk", 1.0, json!({"power": "off"}));
    assert!(wait_for(&run, "Probe:1", "seen", "off", 30).contains("off"), "the rule never saw the remote's value: {}", read(&run, "Probe:1", "seen"));
    assert!(wait_for(&run, "Vm:kitchen", "state", "connected", 30).contains("connected"), "the remote is a Vm of the seat");
    remote.set("kitchen::Lamp:desk", 2.0, json!({"power": "on"}));
    let now = wait_for(&run, "Probe:1", "seen", "\"on\"", 30);
    assert!(now.contains("on") && !now.contains("off"), "{now}");
}

fn rule(peer: &str, prefix: &str) -> String { format!("peer_writes={}", json!([{"peer": peer, "prefix": prefix}])) }

#[test]
fn a_remote_that_reaches_the_seat_through_a_listening_bridge_writes_a_node_only_when_a_rule_allows_it() {
    let name = peer_name();
    let _n = Names(name.clone());
    let dir = tmp("write");
    let run = start(&dir, &[format!("peers={}", decl(&name, Some("kitchen"))), rule("kitchen", "Lamp:")]);
    // the listening bridge prints its address
    let mut child = Command::new(bridge_bin()).args(["listen", "127.0.0.1:0", "--peer", &name, "--slots", "64", "--key-cap", "128", "--val-cap", "1024", "--epoch", "1", "--spins", "1000000", "--depth", "32", "--token-file"])
        .arg(run.dir.join("peers").join(format!("{name}.token"))).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("bridge.log")).unwrap()).spawn().unwrap();
    let mut line = String::new();
    BufReader::new(child.stdout.as_mut().unwrap()).read_line(&mut line).unwrap();
    let _b = Kill(child);
    let addr = line.trim().strip_prefix("listening ").unwrap_or_else(|| panic!("the first line was {line:?}")).to_string();
    put(&run, "Lamp:desk", "power", json!("off"));
    put(&run, "Door:front", "locked", json!(true));
    let mut remote = Remote::dial(&addr);
    assert!(remote.until(30_000, |r| r.events.iter().any(|e| matches!(e, Event::Opened { .. }))), "{}", std::fs::read_to_string(dir.join("bridge.log")).unwrap_or_default());
    remote.say(vec![Op::Wr { wid: "w1".into(), id: "Lamp:desk".into(), prop: "power".into(), value: json!("on") }, Op::Wr { wid: "w2".into(), id: "Door:front".into(), prop: "locked".into(), value: json!(false) }]);
    assert!(remote.until(30_000, |r| r.receipt("w1").is_some() && r.receipt("w2").is_some()), "{:?}", remote.events);
    assert_eq!(remote.receipt("w1").unwrap(), None, "the rule allows Lamp: ok");
    assert!(remote.receipt("w2").unwrap().is_some(), "Door: is outside the rule: refused");
    assert!(wait_for(&run, "Lamp:desk", "power", "on", 30).contains("on"));
    assert!(read(&run, "Door:front", "locked").to_lowercase().contains("true"), "the door was not touched");
}

#[test]
fn a_bridge_the_seat_supervises_is_started_again_when_it_dies_and_the_remotes_values_flow_again() {
    let name = peer_name();
    let _n = Names(name.clone());
    let dir = tmp("super");
    let marker = dir.join("pids");
    let (mut remote, addr) = Remote::listening();
    // the seat starts the bridge itself: the program is `sh`, which notes its pid and becomes the bridge
    let token = dir.join("peers").join(format!("{name}.token"));
    let bridge = bridge_bin();
    let line = format!("echo $$ >> {}; exec {bridge} connect {addr} --peer {name} --slots 64 --key-cap 128 --val-cap 1024 --epoch 1 --spins 1000000 --depth 32 --token-file {} --die-with-parent", marker.display(), token.display());
    let mut decl = json!([{"name": name, "ns": "kitchen", "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024, "spins": 1_000_000, "depth": 32,
                           "run": {"argv": ["sh", "-c", line], "restart_wait_s": 0.5, "max_restarts": 3, "reset_after_s": 600}}]);
    decl[0]["ns"] = json!("kitchen");
    let run = start_with(&dir, &[format!("peers={decl}")], &[probe_pack(&dir)]);
    put(&run, "Probe:1", "x", json!(1));
    assert!(remote.until(60_000, |r| r.subs.iter().any(|t| matches!(t, Target::Id(i) if i == "kitchen::Lamp:desk"))), "the remote was never asked: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default());
    remote.set("kitchen::Lamp:desk", 1.0, json!({"power": "off"}));
    assert!(wait_for(&run, "Probe:1", "seen", "off", 30).contains("off"));
    // the bridge dies hard
    let pid = std::fs::read_to_string(&marker).unwrap().lines().next().unwrap().trim().to_string();
    let _ = Command::new("kill").args(["-9", &pid]).status();
    assert!(wait_for(&run, "Vm:kitchen", "link", "gone", 30).contains("gone"), "the Vm is gone while the bridge is");
    // the seat starts it again; the remote is asked again; its new value reaches the rule
    remote.subs.clear();
    assert!(remote.until(60_000, |r| r.subs.iter().any(|t| matches!(t, Target::Id(i) if i == "kitchen::Lamp:desk"))), "the restarted bridge never asked the remote: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default());
    remote.set("kitchen::Lamp:desk", 5.0, json!({"power": "second"}));
    assert!(wait_for(&run, "Probe:1", "seen", "second", 30).contains("second"), "the value did not flow again: {}", read(&run, "Probe:1", "seen"));
    assert!(wait_for(&run, "Vm:kitchen", "link", "up", 30).contains("up"));
}

/// a TCP port nobody listens on right now (the listening bridge of a seat is started by that seat, so its address is chosen first)
fn free_port() -> u16 { std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port() }

fn probe_of(dir: &std::path::Path, reads: &str) -> std::path::PathBuf {
    let p = dir.join("probe.c1f");
    std::fs::write(&p, format!("PACK probe 1\nIMPORT boot 1\nRESIDENT Probe\nProbe.seen getter: {reads}\n")).unwrap();
    p
}

/// the supervised program of a seat: `sh` notes nothing, it becomes the bridge
fn bridge_run(mode: &str, addr: &str, name: &str, dir: &std::path::Path, seat_name: &str) -> J {
    let token = dir.join("peers").join(format!("{name}.token"));
    let bridge = bridge_bin();
    let line = format!("exec {bridge} {mode} {addr} --peer {name} --seat-name {seat_name} --slots 64 --key-cap 128 --val-cap 1024 --epoch 1 --spins 1000000 --depth 32 --token-file {} --die-with-parent", token.display());
    json!({"argv": ["sh", "-c", line], "restart_wait_s": 0.5, "max_restarts": 20, "reset_after_s": 600})
}

#[test]
fn two_seats_that_are_both_membrane_read_each_others_nodes_through_two_bridges() {
    // seat `a` listens (its bridge says `a` across the link), seat `b` dials (`b`); each declares the other under that name; each rule reads a node of the other
    let (pa, pb) = (peer_name(), peer_name());
    let (_na, _nb) = (Names(pa.clone()), Names(pb.clone()));
    let (da, db) = (tmp("two-a"), tmp("two-b"));
    let addr = format!("127.0.0.1:{}", free_port());
    let decl = |peer: &str, ns: &str, run: J| json!([{"name": peer, "ns": ns, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024, "spins": 1_000_000, "depth": 32, "run": run}]);
    let run_a = start_with(&da, &[format!("peers={}", decl(&pa, "b", bridge_run("listen", &addr, &pa, &da, "a")))], &[probe_of(&da, "b::Lamp:kitchen.power")]);
    let run_b = start_with(&db, &[format!("peers={}", decl(&pb, "a", bridge_run("connect", &addr, &pb, &db, "b")))], &[probe_of(&db, "a::Lamp:desk.power")]);
    // each seat owns a bare node; the other reads it under the name of its owner
    put(&run_a, "Lamp:desk", "power", json!("off"));
    put(&run_b, "Lamp:kitchen", "power", json!("dim"));
    put(&run_a, "Probe:1", "x", json!(1));
    put(&run_b, "Probe:1", "x", json!(1));
    assert!(wait_for(&run_b, "Probe:1", "seen", "off", 90).contains("off"), "b never saw a's Lamp:desk: {}", read(&run_b, "Probe:1", "seen"));
    assert!(wait_for(&run_a, "Probe:1", "seen", "dim", 90).contains("dim"), "a never saw b's Lamp:kitchen: {}", read(&run_a, "Probe:1", "seen"));
    assert!(wait_for(&run_b, "Vm:a", "link", "up", 30).contains("up"), "a is a Vm of b");
    assert!(wait_for(&run_a, "Vm:b", "link", "up", 30).contains("up"), "b is a Vm of a");
    // a change on a reaches b
    put(&run_a, "Lamp:desk", "power", json!("on"));
    let now = wait_for(&run_b, "Probe:1", "seen", "\"on\"", 60);
    assert!(now.contains("on") && !now.contains("off"), "{now}");
}
