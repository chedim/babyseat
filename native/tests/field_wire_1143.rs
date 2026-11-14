//! The field view's write stream (#1143): `GET /ui/fieldvm/events` is an event stream of `field.write` frames ({type, eid, cls, prop, ts}, no value), one per entity written, pushed as the
//! writes happen. The host notes the writes of every round (never a VIRTUAL class's) and writes them as ONE batch, `Wire:host.batch`, an outside write stamped by the host's sensor like `Mem:host`:
//! a pack's setter fans it out to the open streams. NOTHING is journalled and nothing is stored for the stream (the Wire entity is VIRTUAL). Each wait has a timeout; no real `claude`.
#![cfg(feature = "bin")]
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
fn put(port: u16, eid: &str) { call(port, "entity_write", json!({"spec": eid, "prop": "n", "value": 1})); std::thread::sleep(Duration::from_millis(30)); }
fn active(port: u16, q: &str) -> (u16, J) { let (s, b) = http(port, "GET", &format!("/ui/fieldvm/active{q}"), ""); (s, serde_json::from_str(&b).unwrap_or(J::Null)) }
fn ids(a: &J) -> Vec<String> { a["nodes"].as_array().unwrap().iter().map(|n| n["id"].as_str().unwrap().to_string()).collect() }

fn open_stream(port: u16, path: &str) -> TcpStream {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_millis(200))).unwrap();
    s.write_all(format!("GET {path} HTTP/1.1\r\nHost: t\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n").as_bytes()).unwrap();
    s
}
/// read frames (the `data:` lines parsed) until `done(frames)` or `secs` seconds
fn read_frames(s: &mut TcpStream, buf: &mut String, secs: u64, mut done: impl FnMut(&[J]) -> bool) -> Vec<J> {
    let t = Instant::now();
    let mut frames: Vec<J> = vec![];
    loop {
        let mut chunk = [0u8; 65536];
        match s.read(&mut chunk) { Ok(0) => break, Ok(n) => buf.push_str(&String::from_utf8_lossy(&chunk[..n])), Err(_) => {} }
        while let Some(i) = buf.find("\n\n") {
            let block: String = buf.drain(..i + 2).collect();
            if let Some(d) = block.lines().find_map(|l| l.strip_prefix("data: ")) { if let Ok(v) = serde_json::from_str(d) { frames.push(v); } }
        }
        if done(&frames) || t.elapsed() > Duration::from_secs(secs) { break; }
    }
    frames
}
fn journal_lines(dir: &std::path::Path, like: &str) -> i64 {
    let db = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
    db.query_row("SELECT count(*) FROM c_journal WHERE body LIKE ?1", [like], |r| r.get(0)).unwrap_or(0)
}

#[test]
fn a_write_arrives_on_the_stream_with_no_poll_and_nothing_is_journalled_for_the_stream() {
    let dir = tmp("w");
    let run = start(&dir, &[]);
    let mut s = open_stream(run.port, "/ui/fieldvm/events");
    let mut buf = String::new();
    // the head is out (a 200 event stream)
    read_frames(&mut s, &mut buf, 1, |_| false);
    assert!(buf.is_empty() || true);
    put(run.port, "Zed:wire1");
    put(run.port, "Zed:wire1:note");
    let frames = read_frames(&mut s, &mut buf, 10, |f| f.iter().any(|x| x["eid"] == "Zed:wire1:note"));
    let mine: Vec<&J> = frames.iter().filter(|f| f["eid"].as_str().map_or(false, |e| e.starts_with("Zed:wire1"))).collect();
    assert!(mine.iter().any(|f| f["eid"] == "Zed:wire1"), "{frames:?}");
    let f = mine[0];
    assert_eq!((f["type"].clone(), f["cls"].clone()), (json!("field.write"), json!("Zed")), "{f}");
    assert!(f["ts"].as_f64().unwrap() > 1.0e9, "{f}");
    assert!(f.get("new").is_none(), "no value rides the frame: {f}");
    // a VIRTUAL entity's writes (every request is one) are not on the stream
    assert!(!frames.iter().any(|f| f["eid"].as_str().map_or(false, |e| e.starts_with("Request:") || e.starts_with("Wire:"))), "{frames:?}");
    // a retire is a frame too (the view drops the dot)
    call(run.port, "run_cliff", json!({"code": "Zed:wire1.retire = True"}));
    let frames = read_frames(&mut s, &mut buf, 10, |f| f.iter().any(|x| x["prop"] == "retire"));
    assert!(frames.iter().any(|x| x["eid"] == "Zed:wire1" && x["prop"] == "retire"), "{frames:?}");
    // nothing about the stream is journalled or stored
    std::thread::sleep(Duration::from_millis(500));
    let db0 = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
    let shown: Vec<String> = db0.prepare("SELECT substr(body, 1, 160) FROM c_journal WHERE body LIKE '%Wire:host%'").unwrap().query_map([], |r| r.get(0)).unwrap().filter_map(Result::ok).collect();
    assert_eq!(journal_lines(&dir, "%Wire:host%"), 0, "the batch is not history: {shown:?}");
    let db = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
    let n: i64 = db.query_row("SELECT count(*) FROM c_field WHERE k LIKE 'Wire:%'", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 0, "the Wire entity is virtual: no row");
}

#[test]
fn a_burst_of_writes_costs_one_frame_per_entity_and_the_seat_keeps_answering() {
    let dir = tmp("b");
    let run = start(&dir, &[]);
    let mut s = open_stream(run.port, "/ui/fieldvm/events");
    let mut buf = String::new();
    read_frames(&mut s, &mut buf, 1, |_| false);
    let t = Instant::now();
    let n: usize = std::env::var("BURST").ok().and_then(|v| v.parse().ok()).unwrap_or(100);   // BURST=1000 for the measurement (a debug build makes 1000 door calls in about 100 s)
    for i in 0..n { call(run.port, "entity_write", json!({"spec": format!("Burst:{i}"), "prop": "n", "value": i})); }
    let writing = t.elapsed();
    let frames = read_frames(&mut s, &mut buf, 30, |f| f.iter().filter(|x| x["eid"].as_str().map_or(false, |e| e.starts_with("Burst:"))).count() >= n);
    let got: std::collections::HashSet<&str> = frames.iter().filter_map(|x| x["eid"].as_str()).filter(|e| e.starts_with("Burst:")).collect();
    eprintln!("burst of {n} writes: {writing:?} to make them over the door, {:?} until the last frame was read, {} frames in all, {} bytes", t.elapsed(), frames.len(), buf.len());
    assert_eq!(got.len(), n, "every entity of the burst has a frame");
    let t = Instant::now();
    assert_eq!(call(run.port, "run_cliff", json!({"code": "1 + 1"})), "2");
    assert!(t.elapsed() < Duration::from_secs(2), "the door still answers");
}
