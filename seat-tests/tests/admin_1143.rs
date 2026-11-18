//! THE ADMIN (#1143, operator 2026-11-14: "no users in this release"): every request that carries a VALID token (or the page cookie) is THE ADMIN, whatever its `sub` says.
//! No `Person:<sub>` is made, there is no `operator` fact, no non-admin tier and no 403 `for the owner`: the model's writing tools (`entity_write`, `run_cliff`, a pack
//! written by `entity_write`) run for any verified token. A request with no token is 401 as before. `cliffvm token mint` still signs a token (its `--sub` is a label).
//! The barest seat (no secret) is open by decision. Every wait has a timeout and the child is always reaped.
#![cfg(feature = "bin")]
mod common;

use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = common::bin("cliffvm");

struct Run { child: Child, _lines: Receiver<String> }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = common::unique();
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
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{auth}\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }
fn secret_of(dir: &std::path::Path) -> Vec<u8> { std::fs::read_to_string(dir.join("door.secret")).unwrap().trim().as_bytes().to_vec() }
fn admin_token(dir: &std::path::Path) -> String { std::fs::read_to_string(dir.join("admin.token")).unwrap().trim().to_string() }
fn token_with_sub(dir: &std::path::Path, sub: &str, name: &str) -> String { cliffvm::jwt::mint(&secret_of(dir), sub, name, 3600.0, now()).unwrap() }

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

#[test]
fn a_token_with_any_sub_is_the_admin_and_writes_through_the_door() {
    let dir = tmp("admin-any-sub");
    let (_run, port) = launch(&dir, true, &[]);
    let adm = admin_token(&dir);
    // `bob` and `ha` used to be second persons (403 `for the owner`); now each is the admin: entity_write, run_cliff and a pack written by entity_write all run
    for (i, sub) in ["bob", "ha", "owner", "seat:internal"].iter().enumerate() {
        let t = token_with_sub(&dir, sub, "Anybody");
        let (st, b) = call(port, &t, "entity_write", json!({"spec": format!("Note:w{i}"), "prop": "text", "value": "hi"}));
        assert_eq!(st, 200, "sub {sub}: {b}");
        assert_eq!(read_prop(port, &adm, &format!("Note:w{i}"), "text"), json!("hi"), "sub {sub}");
        let (st, b) = call(port, &t, "run_cliff", json!({"code": format!("('Note:r{i}').text = 'yo'")}));
        assert_eq!(st, 200, "sub {sub}: {b}");
        assert_eq!(read_prop(port, &adm, &format!("Note:r{i}"), "text"), json!("yo"), "sub {sub}");
    }
    let (st, b) = call(port, &token_with_sub(&dir, "bob", "Bob"), "entity_write", json!({"spec": "Pack:harmless", "prop": "text", "value": "PACK harmless 1\nHarmless.x getter: 1\n"}));
    assert_eq!(st, 200, "a pack is written by any verified token: {b}");
    // and the barest answers: no token is still 401, whatever it is asked
    let (st, _) = call(port, "", "entity_write", json!({"spec": "Note:none", "prop": "text", "value": "x"}));
    assert_eq!(st, 401);
    assert_eq!(read_prop(port, &adm, "Note:none", "text"), J::Null, "a tokenless write changed nothing");
}

#[test]
fn no_person_is_made_by_a_verified_request_and_nobody_reads_a_sub_as_an_identity() {
    let dir = tmp("admin-no-person");
    let (_run, port) = launch(&dir, true, &[]);
    let (adm, ann) = (admin_token(&dir), token_with_sub(&dir, "ann", "Ann"));
    assert_eq!(http(port, "GET", "/global/health", Some(&ann), "").0, 200);
    assert_eq!(http(port, "GET", "/global/health", Some(&adm), "").0, 200);
    for who in ["ann", "admin", "owner"] { assert_eq!(read_prop(port, &adm, &format!("Person:{who}"), "sub"), J::Null, "no Person:{who}"); }
    let (st, q) = call(port, &adm, "entity_query", json!({"q": "kind:Person"}));
    assert_eq!(st, 200);
    assert!(!text_of(&q).contains("Person:"), "{q}");
    // the page's config names the admin, not the token's sub
    let (st, b) = http(port, "GET", "/ui/conf", Some(&ann), "");
    assert_eq!(st, 200);
    let who: J = serde_json::from_str(&b).unwrap();
    assert_eq!(who["who"]["sub"], "admin", "{b}");
    // a refused token is 401 (the control: nothing about the sub opens the door)
    let eve = cliffvm::jwt::mint(b"another secret of at least 32 bytes!!", "admin", "Admin", 3600.0, now()).unwrap();
    assert_eq!(http(port, "GET", "/global/health", Some(&eve), "").0, 401);
    assert_eq!(http(port, "GET", "/global/health", None, "").0, 401);
}

#[test]
fn the_boot_token_is_the_admin_token_in_admin_token_and_there_is_no_operator_fact() {
    let dir = tmp("admin-boot");
    let (_run, port) = launch(&dir, true, &[]);
    assert!(!dir.join(format!("{}.token", "ow".to_string() + "ner")).exists() && dir.join("admin.token").exists(), "the file is admin.token now");
    let t = admin_token(&dir);
    let c = cliffvm::jwt::verify(&t, &secret_of(&dir), now()).unwrap();
    assert_eq!(c["sub"], "admin");
    assert_eq!(http(port, "GET", "/global/health", Some(&t), "").0, 200);
    // `--fact operator=<name>` names nobody any more: the boot token is still the admin's
    let dir2 = tmp("admin-operator-fact");
    let (_run2, _port2) = launch(&dir2, true, &["--fact", "operator=ann"]);
    assert_eq!(cliffvm::jwt::verify(&admin_token(&dir2), &secret_of(&dir2), now()).unwrap()["sub"], "admin");
    let facts = std::fs::read_to_string(format!("{}/../membrane/packs/facts.json", env!("CARGO_MANIFEST_DIR"))).unwrap();
    assert!(!facts.contains("\"operator\"") && !facts.contains(&("ow".to_string() + "ner_token")) && facts.contains("admin_token_file"), "the facts name no operator and no admin token");
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
    let claims = cliffvm::jwt::verify(tok, &secret_of(&dir), now()).unwrap();
    assert_eq!((claims["sub"].as_str(), claims["name"].as_str()), (Some("cara"), Some("Cara")));
    assert_eq!(claims["exp"].as_f64().unwrap() - claims["iat"].as_f64().unwrap(), 7200.0);
    assert_eq!(http(port, "GET", "/global/health", Some(tok), "").0, 200, "the running door takes it");
    assert_eq!(read_prop(port, &admin_token(&dir), "Person:cara", "name"), J::Null, "and the first request made no person");
    // the units of the released CLI: seconds, s, m, h, d
    for (ttl, secs) in [("90", 90.0), ("45s", 45.0), ("15m", 900.0), ("12h", 43200.0), ("30d", 2592000.0)] {
        let (rc, out, err) = mint(&["--sub", "x", "--ttl", ttl, "--secret-file", sf.to_str().unwrap()]);
        assert_eq!(rc, 0, "{ttl}: {err}");
        let c = cliffvm::jwt::verify(out.trim(), &secret_of(&dir), now()).unwrap();
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
