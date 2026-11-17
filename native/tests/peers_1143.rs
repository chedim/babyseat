//! The seat's local peers over shared memory (docs/graph-link.md, section 18). The seat is started with the fact `peers`; for each declared peer it makes the control rings and its
//! own cell table; the peer (the test, using graphlink-shm and graphlink-core as any other program would) attaches, opens a link with a `hello`, and the seat shows it as `Vm:<ns>`.
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::{Frame, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
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

fn command(dir: &std::path::Path, facts: &[String]) -> Command {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
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

fn decl(name: &str) -> String { json!([{"name": name, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024}]).to_string() }

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
        (json!([{"name": "x".repeat(40), "ring_bytes": 4096, "slots": 8, "key_cap": 16, "val_cap": 16}]).to_string(), "name"),
        (json!([{"name": "ok", "slots": 8, "key_cap": 16, "val_cap": 16}]).to_string(), "ring_bytes"),
        (json!([{"name": "ok", "ring_bytes": 4096, "slots": 0, "key_cap": 16, "val_cap": 16}]).to_string(), "slots"),
        (json!({"name": "not a list"}).to_string(), "list"),
    ] {
        let dir = tmp("bad");
        let out = command(&dir, &[format!("peers={bad}")]).stdout(Stdio::null()).stderr(Stdio::piped()).output().unwrap();
        assert!(!out.status.success(), "{bad}");
        let err = String::from_utf8_lossy(&out.stderr);
        assert!(err.contains("peers") && err.contains(why), "{bad}: {err}");
    }
}
