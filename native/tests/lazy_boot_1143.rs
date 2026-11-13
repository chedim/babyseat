//! A big store boots without loading it (#1143, operator 2026-11-13): the packs and the RESIDENT rows load at start, the door answers at once, and the rest of the rows load
//! on first reference and in the background (the warm boot, newest `updated` first, up to the boot fact `warm_rows`). The seat is the shipped one (the packs of
//! `membrane/apps/barest.packs` and the barest app) against a store of synthetic entities; there is no real `claude`, every wait has a timeout, the port is ephemeral.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
/// The bound the door must come up within on a store of `SYNTHETIC` entities (a threshold, flagged: a debug build of the test binary, a busy machine).
const BOOT_BOUND: Duration = Duration::from_secs(20);
const SYNTHETIC: usize = 5000;

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-lazy-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// a store an earlier run left: `n` Note rows (a lazy class here), in the shape of before the `cls` and `updated` columns (the seat migrates it)
fn old_store(dir: &std::path::Path, n: usize) {
    let db = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
    db.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_journal (k TEXT PRIMARY KEY, body TEXT NOT NULL); BEGIN;").unwrap();
    let mut st = db.prepare("INSERT INTO c_field (k, body) VALUES (?1, ?2)").unwrap();
    for i in 0..n { st.execute(rusqlite::params![format!("Note:{i}"), json!({"cls": "Note", "props": {"n": i, "text": format!("synthetic note {i} {}", "x".repeat(200))}, "writers": {"n": "person", "text": "person"}}).to_string()]).unwrap(); }
    drop(st);
    db.execute_batch("COMMIT").unwrap();
}

fn start(dir: &std::path::Path, facts: &[&str]) -> (Run, u16, Duration) {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let t = Instant::now();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f"))
        .args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.to_path_buf() };
    let line = run._lines.recv_timeout(Duration::from_secs(120)).expect("the door came up");
    let took = t.elapsed();
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port, took)
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
fn clifflet(port: u16, code: &str) -> String { call(port, "run_cliff", json!({"code": code})) }
fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop { if let Some(v) = f() { return v; } assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}"); std::thread::sleep(Duration::from_millis(100)); }
}
fn rss_kb(run: &Run) -> u64 { String::from_utf8_lossy(&Command::new("ps").args(["-o", "rss=", "-p", &run.child.id().to_string()]).output().unwrap().stdout).trim().parse().unwrap_or(0) }

#[test]
fn a_store_of_five_thousand_entities_boots_and_the_door_answers_within_the_bound_with_no_row_loaded() {
    let dir = tmp("cold");
    old_store(&dir, SYNTHETIC);
    let (mut run, port, took) = start(&dir, &["warm_rows=0"]);
    eprintln!("lazy boot: door up in {took:?} on {SYNTHETIC} stored entities, RSS {} kB", rss_kb(&run));
    assert!(took < BOOT_BOUND, "the door took {took:?} (bound {BOOT_BOUND:?})");
    // the door answers, the store is complete for the query tools, and no Note is loaded
    let (st, _) = http(port, "GET", "/", ""); assert_eq!(st, 200);
    assert_eq!(call(port, "entity_query", json!({"q": "kind:Note", "count": true})), format!("{SYNTHETIC} entity(ies)"));
    assert_eq!(clifflet(port, "len(instances('Note'))"), "0", "a lazy class lists only what is loaded: nothing, with the warm boot off");
    // one reference loads one row, and the class listing follows
    assert_eq!(call(port, "entity_read", json!({"spec": "Note:42", "prop": "n"})), "42");
    assert_eq!(clifflet(port, "len(instances('Note'))"), "1");
    assert_eq!(clifflet(port, "Note:43.n + 1"), "44");
    assert_eq!(clifflet(port, "len(instances('Note'))"), "2");
    // a write to a row nobody loaded is made on what is stored
    call(port, "entity_write", json!({"spec": "Note:7", "prop": "extra", "value": "added"}));
    assert_eq!(call(port, "entity_read", json!({"spec": "Note:7", "prop": "text"})).contains("synthetic note 7"), true, "the stored text is still there");
    assert!(call(port, "entity_read", json!({"spec": "Note:7"})).contains("\"extra\":\"added\"") || call(port, "entity_read", json!({"spec": "Note:7"})).contains("'extra': 'added'"));
    assert_eq!(call(port, "entity_query", json!({"q": "kind:Note", "count": true})), format!("{SYNTHETIC} entity(ies)"), "no row was made twice");
    let _ = run.child.kill();
}

#[test]
fn the_warm_boot_loads_the_budget_in_the_background_and_the_door_keeps_answering() {
    let dir = tmp("warm");
    old_store(&dir, SYNTHETIC);
    let (mut run, port, took) = start(&dir, &["warm_rows=1200"]);
    assert!(took < BOOT_BOUND, "the door took {took:?}");
    // the door answers while rows are still loading (a request is never kept waiting for a slice of the warm boot), and the loaded count reaches the budget and stops there
    let mut answered = 0;
    let n = wait("the warm boot reaches its budget", || {
        let (st, _) = http(port, "GET", "/", ""); assert_eq!(st, 200); answered += 1;
        let n: usize = clifflet(port, "len(instances('Note'))").trim().parse().unwrap();
        (n >= 1200).then_some(n)
    });
    assert_eq!(n, 1200, "exactly the budget (answered {answered} requests meanwhile)");
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(clifflet(port, "len(instances('Note'))"), "1200", "it stops at the budget");
    eprintln!("warm boot: {answered} requests answered while loading, RSS {} kB", rss_kb(&run));
    let _ = run.child.kill();
}
