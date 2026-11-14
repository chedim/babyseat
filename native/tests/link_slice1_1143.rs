//! The graph link, slice 1 (#1143, docs/graph-link.md "Slice 1: built"): the page tells the seat what it loaded and what it can do. The seat side, on the shipped
//! barest seat (no real `claude`, an ephemeral port): `GET /ui/fieldvm` names the page (`page`: `p` + 8 hex digits, one per load, and the entity `Link:page` the page's packs read),
//! the classes `Vm` (stored, one row per page, swept when the seat starts) and `Page` (virtual, held by its `Vm`) take the page's writes through the ordinary door (`POST /mcp`
//! `entity_write`), the SEAT stamps `Vm.seen`, `Vm.state` is `connected` or `stale` by the fact `link_stale_s`, and `Help:page` tells a model how to read it all.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
struct Run { child: Child, _lines: Receiver<String>, port: u16 }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-active-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
/// a start on a store another seat has just left can meet its lock for a moment ("database is locked"): try again
fn start(dir: &std::path::Path, facts: &[&str]) -> Run {
    for _ in 0..4 { if let Some(r) = try_start(dir, facts) { return r; } std::thread::sleep(Duration::from_millis(500)); }
    try_start(dir, facts).unwrap_or_else(|| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()))
}
fn try_start(dir: &std::path::Path, facts: &[&str]) -> Option<Run> {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).ok()?;
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Some(Run { child, _lines: rx, port })
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

fn snap(port: u16) -> J { let (s, b) = http(port, "GET", "/ui/fieldvm", ""); assert_eq!(s, 200, "{b}"); serde_json::from_str(&b).unwrap() }
fn is_ns(s: &str) -> bool { s.len() == 9 && s.starts_with('p') && s[1..].bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) }
fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": spec, "prop": prop})) }
fn caps() -> J { json!({"protocol": 1, "encodings": ["json"], "provides": [{"pack": "dom", "major": 1}], "limits": {"subscriptions": "none yet"}}) }
fn report() -> J { json!({"view": "chat", "hidden": false, "load_errors": {}, "packs": {"shell_view": 1234}}) }
fn tell(port: u16, ns: &str) {
    call(port, "entity_write", json!({"spec": format!("Vm:{ns}"), "prop": "caps", "value": caps()}));
    call(port, "entity_write", json!({"spec": format!("{ns}::Page:main"), "prop": "report", "value": report()}));
}

#[test]
fn the_snapshot_names_the_page_and_every_load_gets_its_own_name() {
    let run = start(&tmp("n"), &[]);
    let (a, b) = (snap(run.port), snap(run.port));
    let (pa, pb) = (a["page"].as_str().unwrap_or(""), b["page"].as_str().unwrap_or(""));
    assert!(is_ns(pa) && is_ns(pb), "page names are p + 8 hex digits: {pa:?} {pb:?}");
    assert_ne!(pa, pb, "two loads, two names");
    let link = a["entities"].as_array().unwrap().iter().find(|e| e["eid"] == "Link:page").unwrap_or_else(|| panic!("no Link:page in {a}"));
    assert_eq!(link["props"]["ns"], json!(pa));
    assert!(link["props"]["build"].as_str().unwrap_or("").starts_with("cliffvm-"), "{link}");
    assert!(a["entities"].as_array().unwrap().iter().any(|e| e["eid"] == "Boot:seat"), "the settings entity is still there");
}

#[test]
fn what_the_page_tells_is_readable_by_the_model_and_the_seat_stamps_the_time() {
    let run = start(&tmp("r"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    tell(run.port, &ns);
    assert_eq!(read(run.port, &format!("Vm:{ns}"), "state"), "connected");
    let seen: f64 = read(run.port, &format!("Vm:{ns}"), "seen").parse().unwrap_or_else(|e| panic!("seen is a time the seat took: {e}"));
    assert!(seen > 1.0e9, "an epoch time: {seen}");
    assert!(read(run.port, &format!("Vm:{ns}"), "connected_at").parse::<f64>().is_ok());
    // the report of the page is held by its Vm: it is still there after other rounds, and says what the page wrote
    std::thread::sleep(Duration::from_millis(200));
    let rep = read(run.port, &format!("{ns}::Page:main"), "report");
    assert!(rep.contains("shell_view") && rep.contains("chat"), "{rep}");
    let whole = call(run.port, "entity_read", json!({"spec": format!("Vm:{ns}")}));
    assert!(whole.contains("caps") && whole.contains("provides"), "the Vm row lists what the page can do: {whole}");
}

#[test]
fn two_pages_are_two_roots_and_a_silent_one_is_stale_by_the_fact() {
    let run = start(&tmp("t"), &[]);
    let (a, b) = (snap(run.port)["page"].as_str().unwrap().to_string(), snap(run.port)["page"].as_str().unwrap().to_string());
    tell(run.port, &a); tell(run.port, &b);
    let q = call(run.port, "entity_query", json!({"q": "kind:Vm"}));
    assert!(q.contains(&format!("Vm:{a}")) && q.contains(&format!("Vm:{b}")), "{q}");
    assert_eq!(q.lines().count(), 2, "{q}");
    drop(run);
    let dir = tmp("t2");
    let run = start(&dir, &["link_stale_s=0"]);
    let a = snap(run.port)["page"].as_str().unwrap().to_string();
    tell(run.port, &a);
    assert_eq!(read(run.port, &format!("Vm:{a}"), "state"), "stale", "0 seconds of silence allowed: stale at once");
}

#[test]
fn a_restart_sweeps_the_page_roots() {
    let dir = tmp("s");
    let run = start(&dir, &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    tell(run.port, &ns);
    assert!(call(run.port, "entity_query", json!({"q": "kind:Vm"})).contains(&ns));
    std::thread::sleep(Duration::from_millis(300));
    drop(run);
    let run = start(&dir, &[]);
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(call(run.port, "entity_query", json!({"q": "kind:Vm"})), "0 entity(ies)", "the tabs of the last run are gone");
}

#[test]
fn the_help_topic_tells_a_model_how_to_read_the_page() {
    let run = start(&tmp("h"), &[]);
    let t = call(run.port, "entity_read", json!({"spec": "Help:page", "prop": "text"}));
    assert!(t.contains("entity_query") && t.contains("kind:Vm") && t.contains("caps"), "{t}");
}
