//! The field view's feed (#1143): `GET /ui/fieldvm/active?m=<n>` answers the `m` most recently ACTIVE entities (by the `updated` column), the hidden classes left out,
//! each with the owner it sits under (the eid's colon prefix) when that owner is in the window too. It reads COLUMNS of `c_field` (`k`, `cls`, `updated`) and never a body,
//! so an entity nobody loaded stays unloaded (docs/field-view-plan.md section 4). The seat is the shipped one (barest.packs + the app), no real `claude`, an ephemeral port.
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
fn start(dir: &std::path::Path, facts: &[&str]) -> Run {
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
    let line = rx.recv_timeout(Duration::from_secs(120)).unwrap_or_else(|e| panic!("the door came up: {e}: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()));
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
fn put(port: u16, eid: &str) { call(port, "entity_write", json!({"spec": eid, "prop": "n", "value": 1})); std::thread::sleep(Duration::from_millis(30)); }
fn active(port: u16, q: &str) -> (u16, J) { let (s, b) = http(port, "GET", &format!("/ui/fieldvm/active{q}"), ""); (s, serde_json::from_str(&b).unwrap_or(J::Null)) }
fn ids(a: &J) -> Vec<String> { a["nodes"].as_array().unwrap().iter().map(|n| n["id"].as_str().unwrap().to_string()).collect() }

/// a store with entities written one after another (so `updated` grows), the seat stopped and started again on it with the warm boot off: nothing is loaded
fn store() -> std::path::PathBuf {
    let dir = tmp("s");
    let run = start(&dir, &[]);
    for e in ["Zed:a", "Message:1", "Zed:a:note", "Span:1", "Zed:b", "SemText:1", "Zed:c", "Zed:c:x", "Zed:d"] { put(run.port, e); }
    std::thread::sleep(Duration::from_millis(300));
    drop(run);
    dir
}

#[test]
fn the_active_route_answers_the_newest_entities_with_the_hidden_classes_left_out() {
    let dir = store();
    let run = start(&dir, &["warm_rows=0"]);
    // no `m`: the route does not guess a window
    assert_eq!(active(run.port, "").0, 400);
    let (st, a) = active(run.port, "?m=4");
    assert_eq!(st, 200, "{a}");
    // the four newest of the classes that are not hidden by default (Message, SemText, Span, Request, DomNode, View, Fetch), newest first (the boot's own entities are among them)
    let (_, all) = active(run.port, "?m=100");
    let all_ids = ids(&all);
    assert_eq!(ids(&a), all_ids[..4].to_vec(), "{a}");
    let upd: Vec<f64> = a["nodes"].as_array().unwrap().iter().map(|n| n["updated"].as_f64().unwrap()).collect();
    assert!(upd.windows(2).all(|w| w[0] >= w[1]), "newest first: {upd:?}");
    let zed: Vec<&String> = all_ids.iter().filter(|i| i.starts_with("Zed:")).collect();
    assert_eq!(zed, ["Zed:d", "Zed:c:x", "Zed:c", "Zed:b", "Zed:a:note", "Zed:a"], "{all}");
    // each row: cls, updated, and the owner when the owner is in the window too
    let by = |id: &str| all["nodes"].as_array().unwrap().iter().find(|n| n["id"] == id).unwrap().clone();
    assert_eq!(by("Zed:c:x")["parent"], json!("Zed:c"), "{all}");
    assert_eq!(by("Zed:c")["parent"], J::Null);
    assert_eq!(by("Zed:c")["cls"], json!("Zed"));
    // a window that cuts the owner off names no owner
    let (_, cut) = active(run.port, &format!("?m={}", all_ids.iter().position(|i| i == "Zed:c").unwrap()));
    assert!(ids(&cut).contains(&"Zed:c:x".to_string()) && !ids(&cut).contains(&"Zed:c".to_string()), "{cut}");
    let x = cut["nodes"].as_array().unwrap().iter().find(|n| n["id"] == "Zed:c:x").unwrap().clone();
    assert_eq!(x["parent"], J::Null, "{cut}");
    // the whole count of the not-hidden entities, so the view can say "4 of N"
    assert_eq!(a["n_total"].as_i64().unwrap() >= 6, true, "{a}");
    let a = all;
    let got = ids(&a);
    assert!(got.contains(&"Zed:a".to_string()) && got.contains(&"Zed:a:note".to_string()), "{a}");
    assert!(!got.iter().any(|i| i.starts_with("Message:") || i.starts_with("SemText:") || i.starts_with("Span:")), "hidden by default: {a}");
    let note = a["nodes"].as_array().unwrap().iter().find(|n| n["id"] == "Zed:a:note").unwrap().clone();
    assert_eq!(note["parent"], json!("Zed:a"), "{a}");
    // the route read columns only: nothing it listed was loaded into the engine
    assert_eq!(call(run.port, "run_cliff", json!({"code": "len(instances('Zed'))"})), "0", "an entity the view lists stays unloaded");
}

#[test]
fn the_hidden_classes_are_a_launch_fact() {
    let dir = store();
    // the fact names the classes (a JSON list): SemText is shown, Zed is hidden
    let run = start(&dir, &["warm_rows=0", r#"map_hidden=["Zed","Message","Span"]"#]);
    let (st, a) = active(run.port, "?m=100");
    assert_eq!(st, 200, "{a}");
    let got = ids(&a);
    assert!(got.contains(&"SemText:1".to_string()), "SemText shown when the fact does not hide it: {a}");
    assert!(!got.iter().any(|i| i.starts_with("Zed:")), "{a}");
}
