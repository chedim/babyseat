//! Supervision of a local peer's process (docs/graph-link.md, section 18): a peer declared with `run` is started by the seat, started again when it exits, given up on after the owner's number of
//! restarts, and its counter reset after it stayed up the owner's time. Every number is the owner's (the fact says them; there are no defaults). The tests use a shell as the peer's program.
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
fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "run_cliff", json!({"code": format!("${{'{}'}}['{}']", spec, prop)})) }

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


/// a peer declared with a process to run: `argv` is the owner's, and so are the three numbers
fn decl_run(name: &str, argv: &[&str], wait: f64, max: u64, reset: f64) -> String {
    json!([{"name": name, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024, "spins": 1_000_000, "depth": 32,
            "run": {"argv": argv, "restart_wait_s": wait, "max_restarts": max, "reset_after_s": reset}}]).to_string()
}
/// a program that notes each start in `marker`, then lives `lives` seconds
fn script(marker: &std::path::Path, lives: &str) -> Vec<String> { vec!["sh".into(), "-c".into(), format!("echo started >> {}; sleep {lives}", marker.display())] }
fn starts(marker: &std::path::Path) -> usize { std::fs::read_to_string(marker).map(|t| t.lines().count()).unwrap_or(0) }
fn wait_starts(marker: &std::path::Path, n: usize, secs: u64) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end { if starts(marker) >= n { return true; } std::thread::sleep(Duration::from_millis(50)); }
    false
}

#[test]
fn the_seat_starts_the_process_a_peer_is_declared_with() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("start");
    let marker = dir.join("marker");
    let argv = script(&marker, "30");
    let _run = start(&dir, &[format!("peers={}", decl_run(&name, &argv.iter().map(String::as_str).collect::<Vec<_>>(), 0.2, 3, 60.0))]);
    assert!(wait_starts(&marker, 1, 60), "the seat never started the peer's process");
    std::thread::sleep(Duration::from_millis(1500));
    assert_eq!(starts(&marker), 1, "a process that is still running is not started again");
}

#[test]
fn a_process_that_exits_is_started_again_after_the_owners_wait_and_given_up_after_the_owners_restarts() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("restart");
    let marker = dir.join("marker");
    let argv = script(&marker, "0");   // exits at once
    let _run = start(&dir, &[format!("peers={}", decl_run(&name, &argv.iter().map(String::as_str).collect::<Vec<_>>(), 0.3, 2, 600.0))]);
    assert!(wait_starts(&marker, 3, 60), "the process was not started again: {} starts", starts(&marker));
    std::thread::sleep(Duration::from_secs(3));
    assert_eq!(starts(&marker), 3, "one start and two restarts, and then the seat gives up");
    let log = std::fs::read_to_string(dir.join("err.log")).unwrap_or_default();
    assert!(log.contains("giving up") && log.contains(&name), "the seat says it gave up: {log}");
}

#[test]
fn the_restart_count_starts_again_after_the_process_stayed_up_the_owners_time() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("reset");
    let marker = dir.join("marker");
    let argv = script(&marker, "1.5");   // lives longer than reset_after_s, then exits
    // max_restarts is 1: without the reset the seat would stop after two starts
    let _run = start(&dir, &[format!("peers={}", decl_run(&name, &argv.iter().map(String::as_str).collect::<Vec<_>>(), 0.2, 1, 1.0))]);
    assert!(wait_starts(&marker, 4, 90), "the count was not reset: {} starts", starts(&marker));
}

#[test]
fn a_run_without_all_three_numbers_stops_the_start_and_names_the_fact() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("bad");
    let decl = json!([{"name": name, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024, "spins": 1, "depth": 1, "run": {"argv": ["true"], "restart_wait_s": 1}}]).to_string();
    let out = command(&dir, &[format!("peers={decl}")]).stdin(Stdio::null()).output().unwrap();
    assert_ne!(out.status.code(), Some(0));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("peers") && err.contains("max_restarts"), "{err}");
}

#[test]
fn a_restarted_peer_gets_fresh_rings_and_its_vm_comes_back_up() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("fresh");
    let marker = dir.join("marker");
    // the supervised program only notes its start; the test plays the peer on the rings, and kills the program to make the seat restart it
    let argv = vec!["sh".to_string(), "-c".to_string(), format!("echo $$ >> {}; sleep 600", marker.display())];
    let run = start(&dir, &[format!("peers={}", decl_run(&name, &argv.iter().map(String::as_str).collect::<Vec<_>>(), 0.3, 3, 600.0))]);
    let mut peer = PeerEnd::attach(&name, "kitchen");
    assert!(peer.until(60_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))));
    assert!(wait_for(run.port, "Vm:kitchen", "link", "up", 30).contains("up"));
    let pid = std::fs::read_to_string(&marker).unwrap().lines().next().unwrap().trim().to_string();
    let _ = Command::new("kill").arg(&pid).status();
    assert!(wait_for(run.port, "Vm:kitchen", "link", "gone", 30).contains("gone"), "the Vm is gone while its process is");
    assert!(wait_starts(&marker, 2, 30), "the seat did not start the process again");
    // the rings are new: the old end is dead, a new attach gets a new link
    drop(peer);
    let mut again = PeerEnd::attach(&name, "kitchen");
    assert!(again.until(60_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "no hello came over the new rings: {:?}", again.events);
    assert!(wait_for(run.port, "Vm:kitchen", "link", "up", 30).contains("up"), "the Vm did not come back up");
}
