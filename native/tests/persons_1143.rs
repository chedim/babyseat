//! Persons (#1143, swap step "persons"): a verified request makes and keeps `Person:<sub>` (name, sub, first_seen, last_seen), the owner is a person,
//! the model's writing tools through the door (`entity_write`, `run_cliff`, a pack written by `entity_write`) are the owner's, and `cliffvm token mint`
//! signs a token for another person. The oracle is the Python release (git tag v0.7.53): its JWT mode has no owner-only write check, its one 403 is
//! `{"type":"error","error":{"message":...}}` (server.py `_json_err`), so that is the shape here; the wording of the message is flagged for the operator.
//! The barest seat (no secret) is open by decision and makes no persons. Every wait has a timeout and the child is always reaped.
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

#[test]
fn a_verified_request_makes_the_person_and_keeps_first_seen_while_last_seen_moves() {
    let dir = tmp("persons-make");
    let (_run, port) = launch(&dir, true, &[]);
    let (own, ann) = (owner_token(&dir), person_token(&dir, "ann", "Ann"));
    assert_eq!(http(port, "GET", "/global/health", Some(&ann), "").0, 200);
    // the owner reads the person back: name and sub from the token, both times set, and the entity is of the class Person
    let first = read_prop(port, &own, "Person:ann", "first_seen");
    assert!(first.as_f64().unwrap() > 1.0e9, "{first}");
    assert_eq!(read_prop(port, &own, "Person:ann", "name"), json!("Ann"));
    assert_eq!(read_prop(port, &own, "Person:ann", "sub"), json!("ann"));
    let (st, q) = call(port, &own, "entity_query", json!({"q": "kind:Person"}));
    assert_eq!(st, 200);
    assert!(text_of(&q).contains("Person:ann"), "{q}");
    std::thread::sleep(Duration::from_millis(1500));
    assert_eq!(http(port, "GET", "/global/health", Some(&ann), "").0, 200);
    assert_eq!(read_prop(port, &own, "Person:ann", "first_seen"), first, "first_seen is the first request's, kept");
    assert!(read_prop(port, &own, "Person:ann", "last_seen").as_f64().unwrap() >= first.as_f64().unwrap() + 1.0, "last_seen moved");
}

#[test]
fn a_refused_request_makes_no_person_and_the_owner_is_a_person_too() {
    let dir = tmp("persons-refused");
    let (_run, port) = launch(&dir, true, &[]);
    let own = owner_token(&dir);
    let eve = membrane_vm::jwt::mint(b"another secret of at least 32 bytes!!", "eve", "Eve", 3600.0, now()).unwrap();
    assert_eq!(http(port, "GET", "/global/health", Some(&eve), "").0, 401);
    assert_eq!(http(port, "GET", "/global/health", None, "").0, 401);
    assert_eq!(read_prop(port, &own, "Person:eve", "sub"), J::Null, "a refused token is nobody");
    assert_eq!(read_prop(port, &own, "Person:owner", "sub"), json!("owner"), "the owner's token made the owner's person");
}

#[test]
fn only_the_owner_writes_through_the_door_and_the_refusal_is_a_403_that_changes_nothing() {
    let dir = tmp("persons-gate");
    let (_run, port) = launch(&dir, true, &[]);
    let (own, bob) = (owner_token(&dir), person_token(&dir, "bob", "Bob"));
    // bob: entity_write, run_cliff and a pack written by entity_write are all refused: 403 in the released error shape, and nothing happened
    let (st, b) = call(port, &bob, "entity_write", json!({"spec": "Note:one", "prop": "text", "value": "hi"}));
    assert_eq!((st, &b), (403, &FORBIDDEN()), "{b}");
    let (st, b) = call(port, &bob, "run_cliff", json!({"code": "('Note:two').text = 'hi'"}));
    assert_eq!((st, &b), (403, &FORBIDDEN()), "{b}");
    let (st, b) = call(port, &bob, "entity_write", json!({"spec": "Pack:evil", "prop": "text", "value": "PACK evil 1\nEvil.x getter: 1\n"}));
    assert_eq!((st, &b), (403, &FORBIDDEN()), "{b}");
    assert_eq!(read_prop(port, &own, "Note:one", "text"), J::Null);
    assert_eq!(read_prop(port, &own, "Note:two", "text"), J::Null);
    assert_eq!(read_prop(port, &own, "Pack:evil", "text"), J::Null);
    // reads stay open to any verified person, and so does the tool list
    assert_eq!(call(port, &bob, "entity_read", json!({"spec": "Person:bob"})).0, 200);
    assert_eq!(call(port, &bob, "entity_query", json!({"q": "kind:Person"})).0, 200);
    assert_eq!(http(port, "POST", "/mcp", Some(&bob), &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).to_string()).0, 200);
    // the owner writes
    let (st, b) = call(port, &own, "entity_write", json!({"spec": "Note:one", "prop": "text", "value": "hi"}));
    assert_eq!(st, 200, "{b}");
    assert_eq!(read_prop(port, &own, "Note:one", "text"), json!("hi"));
    let (st, b) = call(port, &own, "run_cliff", json!({"code": "('Note:two').text = 'yo'"}));
    assert_eq!(st, 200, "{b}");
    assert_eq!(read_prop(port, &own, "Note:two", "text"), json!("yo"));
}

#[test]
fn the_owner_is_the_person_the_operator_fact_names() {
    let dir = tmp("persons-operator");
    let (_run, port) = launch(&dir, true, &["--fact", "operator=ann"]);
    let (own, ann, owner_sub) = (owner_token(&dir), person_token(&dir, "ann", "Ann"), person_token(&dir, "owner", "Owner"));
    // the boot's owner token is ann's; a token with sub ann is the owner; a token with the sub `owner` is not when the operator is ann
    assert_eq!(call(port, &own, "entity_write", json!({"spec": "Note:a", "prop": "text", "value": "1"})).0, 200);
    assert_eq!(call(port, &ann, "entity_write", json!({"spec": "Note:b", "prop": "text", "value": "1"})).0, 200);
    assert_eq!(call(port, &owner_sub, "entity_write", json!({"spec": "Note:c", "prop": "text", "value": "1"})).0, 403);
}

#[test]
fn the_barest_seat_is_open_makes_no_persons_and_the_caller_may_write() {
    let dir = tmp("persons-barest");
    let (_run, port) = launch(&dir, false, &[]);
    let (st, b) = http(port, "POST", "/mcp", None, &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": "Note:one", "prop": "text", "value": "hi"}}}).to_string());
    assert_eq!(st, 200, "{b}");
    let (st, b) = http(port, "POST", "/mcp", None, &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "entity_query", "arguments": {"q": "kind:Person"}}}).to_string());
    assert_eq!(st, 200);
    assert!(!b.contains("Person:"), "an open seat verifies nobody: {b}");
}

// ---- cliffvm token mint -------------------------------------------------------------------------------------------------------------------

fn mint(args: &[&str]) -> (i32, String, String) {
    let o = Command::new(BIN).arg("token").arg("mint").args(args).output().unwrap();
    (o.status.code().unwrap_or(-1), String::from_utf8_lossy(&o.stdout).to_string(), String::from_utf8_lossy(&o.stderr).to_string())
}
fn secret_file(tag: &str) -> std::path::PathBuf {
    let d = tmp(tag);
    std::fs::write(d.join("door.secret"), "0123456789abcdef0123456789abcdef0123456789abcdef\n").unwrap();
    d.join("door.secret")
}

#[test]
fn token_mint_prints_a_token_the_door_accepts_with_the_sub_the_name_and_the_ttl() {
    let dir = tmp("persons-mint");
    let (_run, port) = launch(&dir, true, &[]);
    let sf = dir.join("door.secret");
    let (rc, out, err) = mint(&["--sub", "cara", "--name", "Cara", "--ttl", "2h", "--secret-file", sf.to_str().unwrap()]);
    assert_eq!(rc, 0, "{err}");
    let tok = out.trim();
    assert_eq!(out.lines().count(), 1, "one line: the token");
    let claims = membrane_vm::jwt::verify(tok, &secret_of(&dir), now()).unwrap();
    assert_eq!((claims["sub"].as_str(), claims["name"].as_str()), (Some("cara"), Some("Cara")));
    assert_eq!(claims["exp"].as_f64().unwrap() - claims["iat"].as_f64().unwrap(), 7200.0);
    assert_eq!(http(port, "GET", "/global/health", Some(tok), "").0, 200, "the running door takes it");
    assert_eq!(read_prop(port, &owner_token(&dir), "Person:cara", "name"), json!("Cara"), "and the first request made the person");
    // the units of the released CLI: seconds, s, m, h, d
    for (ttl, secs) in [("90", 90.0), ("45s", 45.0), ("15m", 900.0), ("12h", 43200.0), ("30d", 2592000.0)] {
        let (rc, out, err) = mint(&["--sub", "x", "--ttl", ttl, "--secret-file", sf.to_str().unwrap()]);
        assert_eq!(rc, 0, "{ttl}: {err}");
        let c = membrane_vm::jwt::verify(out.trim(), &secret_of(&dir), now()).unwrap();
        assert_eq!(c["exp"].as_f64().unwrap() - c["iat"].as_f64().unwrap(), secs, "{ttl}");
        assert_eq!(c["name"], "x", "no name: the sub is the name, as the person line reads it");
    }
}

#[test]
fn token_mint_refuses_what_it_cannot_sign_and_has_no_default_ttl() {
    let sf = secret_file("persons-mint-refuse");
    let s = sf.to_str().unwrap();
    for (args, why) in [
        (vec!["--sub", "a", "--secret-file", s], "ttl is required"),
        (vec!["--ttl", "1h", "--secret-file", s], "sub is required"),
        (vec!["--sub", "a", "--ttl", "1h"], "no secret file named"),
        (vec!["--sub", "a", "--ttl", "0", "--secret-file", s], "ttl must be positive"),
        (vec!["--sub", "a", "--ttl", "soon", "--secret-file", s], "bad ttl"),
        (vec!["--sub", "seat:x", "--ttl", "1h", "--secret-file", s], "seat: ids are reserved"),
        (vec!["--sub", "a", "--ttl", "1h", "--secret-file", "/nonexistent/door.secret"], "secret file missing: never made here"),
    ] {
        let (rc, out, err) = mint(&args);
        assert_ne!(rc, 0, "{why}: {args:?}");
        assert!(out.is_empty(), "{why}: nothing on stdout, {out:?}");
        assert!(!err.is_empty(), "{why}: says why");
    }
    assert!(!std::path::Path::new("/nonexistent").exists());
}
