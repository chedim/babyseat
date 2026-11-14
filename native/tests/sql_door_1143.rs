//! The Sql driver through the DOOR (#1143, the operator 2026-11-14): the MODEL creates a `SqlSource` with `entity_write` (the owner's tool), a query, runs it and
//! reads the rows back; the pack needs no grant beyond what Command and Fetch need (the tools pack imports `sql`, as it imports `proc` and `fetch`); a
//! non-owner is refused (403) and nothing is made; the seat's own store path is refused with a visible reason whoever writes it.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, _lines: Receiver<String> }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(d.join("packs")).unwrap();
    for n in ["boot.c1f", "door.c1f", "door_auth.c1f", "door_tools.c1f", "entity.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f"] {
        let src = format!("{}/../membrane/packs/{n}", env!("CARGO_MANIFEST_DIR"));
        if let Ok(t) = std::fs::read_to_string(src) { std::fs::write(d.join("packs").join(n), t).unwrap(); }
    }
    std::fs::write(d.join("index.html"), "<html>the page</html>").unwrap();
    d
}

fn launch(dir: &std::path::Path, secret: bool, more: &[&str]) -> (Run, u16) {
    let pd = dir.join("packs");
    let mut args: Vec<String> = ["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()].iter().map(|s| s.to_string()).collect();
    if secret { args.extend(["--fact".into(), format!("door_secret_file={}", dir.join("door.secret").display()), "--fact".into(), format!("seat_data_dir={}", dir.display())]); }
    args.extend(more.iter().map(|s| s.to_string()));
    let mut child = Command::new(BIN).current_dir(dir).args(&args).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port)
}

fn http(port: u16, method: &str, path: &str, tok: Option<&str>, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let auth = tok.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{auth}\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }
fn secret_of(dir: &std::path::Path) -> Vec<u8> { std::fs::read_to_string(dir.join("door.secret")).unwrap().trim().as_bytes().to_vec() }
fn owner_token(dir: &std::path::Path) -> String { std::fs::read_to_string(dir.join("owner.token")).unwrap().trim().to_string() }
fn person_token(dir: &std::path::Path, sub: &str, name: &str) -> String { membrane_vm::jwt::mint(&secret_of(dir), sub, name, 3600.0, now()).unwrap() }

/// one tools/call on /mcp; -> (http status, body)
fn call(port: u16, tok: &str, tool: &str, args: J) -> (u16, J) {
    let (st, b) = http(port, "POST", "/mcp", Some(tok), &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    (st, serde_json::from_str(&b).unwrap_or_else(|_| json!(b)))
}
fn text_of(r: &J) -> String { r["result"]["content"][0]["text"].as_str().unwrap_or("").to_string() }
fn read_prop(port: u16, tok: &str, spec: &str, prop: &str) -> J {
    let (st, r) = call(port, tok, "entity_read", json!({"spec": spec, "prop": prop}));
    assert_eq!(st, 200, "{r}");
    let t = text_of(&r);
    if t.contains(" has no ") { return J::Null; }   // the read tool's words for a property the entity does not have
    serde_json::from_str(&t).unwrap_or(json!(t))
}
const FORBIDDEN: fn() -> J = || json!({"type": "error", "error": {"message": "for the owner"}});

fn write(port: u16, tok: &str, spec: &str, prop: &str, value: J) -> (u16, J) { call(port, tok, "entity_write", json!({"spec": spec, "prop": prop, "value": value})) }
fn wait_prop(port: u16, tok: &str, spec: &str, prop: &str) -> J {
    for _ in 0..200 { let v = read_prop(port, tok, spec, prop); if !v.is_null() { return v; } std::thread::sleep(Duration::from_millis(50)); }
    panic!("{spec}.{prop} never came");
}

#[test]
fn the_owner_writes_a_source_and_a_query_through_the_door_and_reads_the_rows() {
    let dir = tmp("sqldoor-owner");
    let db = dir.join("wordnet.sqlite");
    let cx = rusqlite::Connection::open(&db).unwrap();
    cx.execute_batch("CREATE TABLE syn (w TEXT, s TEXT); INSERT INTO syn VALUES ('big','large'),('big','huge'),('small','tiny');").unwrap();
    drop(cx);
    let (_run, port) = launch(&dir, true, &[]);
    let own = owner_token(&dir);
    for (spec, prop, v) in [("SqlSource:lex", "path", json!(db.to_string_lossy())), ("SqlQuery:syn", "source", json!("lex")), ("SqlQuery:syn", "sql", json!("SELECT s FROM syn WHERE w = ? ORDER BY s"))] {
        let (st, b) = write(port, &own, spec, prop, v);
        assert_eq!(st, 200, "{spec}.{prop}: {b}");
    }
    let (st, b) = write(port, &own, "SqlQuery:syn", "run", json!({"id": "1", "args": ["big"]}));
    assert_eq!(st, 200, "{b}");
    wait_prop(port, &own, "SqlQuery:syn", "run_result");
    assert_eq!(read_prop(port, &own, "SqlQuery:syn", "rows"), json!([{"s": "huge"}, {"s": "large"}]));
    assert_eq!(read_prop(port, &own, "SqlSource:lex", "mode_eff"), json!("ro"));
}

#[test]
fn a_person_who_is_not_the_owner_cannot_make_a_source() {
    let dir = tmp("sqldoor-bob");
    let (_run, port) = launch(&dir, true, &[]);
    let (own, bob) = (owner_token(&dir), person_token(&dir, "bob", "Bob"));
    let (st, b) = write(port, &bob, "SqlSource:lex", "path", json!("/etc/passwd"));
    assert_eq!((st, &b), (403, &FORBIDDEN()), "{b}");
    assert_eq!(read_prop(port, &own, "SqlSource:lex", "path"), J::Null, "nothing was made");
}

#[test]
fn the_seats_own_store_path_written_through_the_door_is_refused_with_a_reason_and_a_fixed_path_recovers() {
    let dir = tmp("sqldoor-own");
    let good = dir.join("good.sqlite");
    rusqlite::Connection::open(&good).unwrap().execute_batch("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (7);").unwrap();
    let (_run, port) = launch(&dir, true, &[]);
    let own = owner_token(&dir);
    let store = dir.join("field.sqlite3");
    for (spec, prop, v) in [("SqlSource:s", "path", json!(store.to_string_lossy())), ("SqlSource:s", "mode", json!("rw")), ("SqlQuery:q", "source", json!("s")), ("SqlQuery:q", "sql", json!("SELECT x FROM t"))] {
        assert_eq!(write(port, &own, spec, prop, v).0, 200);
    }
    write(port, &own, "SqlQuery:q", "run", json!({"id": "1"}));
    let r = wait_prop(port, &own, "SqlQuery:q", "run_result");
    assert_eq!(r["status"], json!("error"), "{r}");
    assert!(r["head"].as_str().unwrap().contains("seat's own store"), "{r}");
    // fix the path and the mode, run again with a new id
    write(port, &own, "SqlSource:s", "path", json!(good.to_string_lossy()));
    write(port, &own, "SqlSource:s", "mode", json!("ro"));
    write(port, &own, "SqlQuery:q", "run", json!({"id": "2"}));
    for _ in 0..200 { if read_prop(port, &own, "SqlQuery:q", "rows") == json!([{"x": 7}]) { return; } std::thread::sleep(Duration::from_millis(50)); }
    panic!("the source never recovered: {}", read_prop(port, &own, "SqlQuery:q", "run_result"));
}
