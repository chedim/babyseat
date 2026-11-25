//! The seat's local peers with a door secret: a peer's hello must carry a token the seat minted for it (docs/graph-link.md, section 13 and 18). The seat writes `<data dir>/peers/<name>.token` (mode 0600)
//! for each declared peer at its start, with the `ns` claim the owner declared; a hello with no token, a bad one, or another namespace is refused and shows no `Vm`.
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::VERSION;
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::Shm;
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

#[test]
fn the_seat_writes_a_token_for_each_declared_peer_with_the_namespace_it_declared() {
    let name = peer_name();
    let _n = Names(name.clone());
    let run = start(&tmp("token"), &[format!("peers={}", decl(&name, Some("kitchen")))]);
    let file = run.dir.join("peers").join(format!("{name}.token"));
    let mode = std::fs::metadata(&file).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode, 0o600, "the token is private");
    let token = std::fs::read_to_string(&file).unwrap();
    let claims = graphlink_auth::verify(token.trim(), &secret(&run.dir), now()).expect("the token verifies against the seat's own secret");
    assert_eq!(claims["ns"], json!("kitchen"));
    // its life is the admin token's (the fact admin_token_ttl_s, 30 days when unset)
    assert_eq!(claims["exp"].as_f64().unwrap() - claims["iat"].as_f64().unwrap(), 2_592_000.0);
}

#[test]
fn a_peer_whose_hello_carries_that_token_is_a_connected_vm() {
    let name = peer_name();
    let _n = Names(name.clone());
    let run = start(&tmp("good"), &[format!("peers={}", decl(&name, Some("kitchen")))]);
    let token = std::fs::read_to_string(run.dir.join("peers").join(format!("{name}.token"))).unwrap();
    let mut peer = PeerEnd::attach(&name, "kitchen", Some(token.trim().to_string()));
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))));
    assert!(wait_for(&run, "Vm:kitchen", "state", "connected", 30).contains("connected"));
}

#[test]
fn a_hello_with_no_token_is_refused_and_shows_no_vm() {
    let name = peer_name();
    let _n = Names(name.clone());
    let run = start(&tmp("none"), &[format!("peers={}", decl(&name, Some("kitchen")))]);
    let mut peer = PeerEnd::attach(&name, "kitchen", None);
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Closed { .. }))), "the seat never closed the link: {:?}", peer.events);
    let why = peer.events.iter().find_map(|e| if let Event::Closed { why } = e { Some(why.clone()) } else { None }).unwrap();
    assert!(why.contains("refused: auth") && !why.contains("token"), "the peer is told only `refused: auth`: {why}");
    std::thread::sleep(Duration::from_millis(500));
    assert_ne!(read(&run, "Vm:kitchen", "link"), "up");
    assert!(!read(&run, "Vm:kitchen", "state").contains("connected"));
}

#[test]
fn a_token_of_another_secret_or_another_namespace_is_refused() {
    for (tag, ns_in_hello, make) in [
        ("forged", "kitchen", Box::new(|_d: &std::path::Path| graphlink_auth::mint_with(b"another secret of more than thirty-two bytes", "p", "P", &[("ns", json!("kitchen"))], 60.0, now()).unwrap()) as Box<dyn Fn(&std::path::Path) -> String>),
        ("other-ns", "kitchen", Box::new(|d: &std::path::Path| graphlink_auth::mint_with(&secret(d), "p", "P", &[("ns", json!("hall"))], 60.0, now()).unwrap())),
        // a good token for the namespace the hello says, but not the namespace this peer was declared for
        ("undeclared", "hall", Box::new(|d: &std::path::Path| graphlink_auth::mint_with(&secret(d), "p", "P", &[("ns", json!("hall"))], 60.0, now()).unwrap())),
    ] {
        let name = peer_name();
        let _n = Names(name.clone());
        let run = start(&tmp(tag), &[format!("peers={}", decl(&name, Some("kitchen")))]);
        let mut peer = PeerEnd::attach(&name, ns_in_hello, Some(make(&run.dir)));
        assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Closed { .. }))), "{tag}: not refused: {:?}", peer.events);
        assert!(!read(&run, &format!("Vm:{ns_in_hello}"), "state").contains("connected"), "{tag}");
    }
}

#[test]
fn with_a_door_secret_a_peer_must_declare_its_namespace() {
    let name = peer_name();
    let _n = Names(name.clone());
    let dir = tmp("nons");
    let out = command(&dir, &[format!("peers={}", decl(&name, None))]).stdin(Stdio::null()).output().unwrap();
    assert_ne!(out.status.code(), Some(0));
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(err.contains("peers") && err.contains("ns"), "{err}");
}
