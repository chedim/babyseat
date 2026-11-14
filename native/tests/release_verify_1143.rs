//! RELEASE VERIFICATION (#1143, 2026-11-14): the auth checklist of the release, proved against REAL private seats started by the release launcher (`scripts/seat`: the shipped packs,
//! the strict door, its own data folder, a free port, a FAKE `claude` that never reaches a model). docs/release-verification.md is the table of what each test proves.
//!   1. the JWT secret: persists across restarts, mode 0600, never in a reply, a log, the store; reuse by a second seat; missing / empty / short / unreadable secret files;
//!   2. the owner cookie of the page, and the way in for a person who has only a token;
//!   3. a second person: reads work, every owner-only write is a 403 `for the owner` and changes nothing; the facts of the one chat;
//!   4. the bypasses: forged and out-of-life tokens, token placement, path tricks, methods, proxy headers, and a TOKENLESS request that must change nothing.
//! (4 of the Home Assistant item is `tests/ha/test_release_launcher.py`, the browser part `tests/js/release_verify.test.mjs`.)
//! No wall-clock assertions: every wait polls a condition with a deadline that only bounds a hang. Every child is reaped; no live seat is touched.
#![cfg(feature = "bin")]
mod common;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::channel;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

fn repo() -> PathBuf { Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("rv-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }

/// a fake claude: answers `Seat answer: <prompt>`; a prompt that starts with `DO:` first writes `Note:via-chat` through the seat's /mcp with the OWNER's token, which the turn
/// hands to its CLI in the environment (what a model that obeys the message could do): the fact test of the one chat reads it back
const FAKE: &str = r#"#!/usr/bin/env python3
import json, os, re, sys, urllib.request
prompt = sys.argv[-1]
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": []})
if prompt.startswith("DO:"):
    cfg = sys.argv[sys.argv.index("--mcp-config") + 1]
    url = re.search(r"http://[^'\"]+/mcp", cfg).group(0)
    req = urllib.request.Request(url, method="POST", headers={"Authorization": "Bearer " + os.environ["MEMBRANE_OWNER_TOKEN"], "Content-Type": "application/json"},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": "Note:via-chat", "prop": "text", "value": "written by a turn"}}}).encode())
    urllib.request.urlopen(req, timeout=60).read()
out({"type": "result", "subtype": "success", "is_error": False, "result": "Seat answer: " + prompt, "num_turns": 1, "session_id": "fake-1"})
"#;

struct Seat { child: Child, dir: PathBuf, port: u16, lines: Arc<Mutex<Vec<String>>>, own_dir: bool }
impl Drop for Seat { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); if self.own_dir { let _ = std::fs::remove_dir_all(&self.dir); } } }

enum Refused { Exit(i32, String) }

fn barest_packs() -> Vec<String> {
    let r = repo();
    std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|n| format!("membrane/packs/{n}.c1f")).collect::<Vec<_>>().into_iter().map(|p| r.join(p).to_string_lossy().to_string()).collect()
}

fn write_fake(dir: &Path) -> PathBuf {
    let p = dir.join("fake-claude");
    std::fs::write(&p, FAKE).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    p
}

/// Start a seat in `dir`. `bind` None: through the release launcher `scripts/seat` (strict door, `--port 0`); Some(addr): `cliffvm run` with the same packs and facts and `--bind addr`
/// (scripts/seat has no --bind). `facts` come after the launcher's own, so they win. The seat's stderr goes to `<dir>/seat.err`.
fn try_start(dir: &Path, bind: Option<&str>, facts: &[&str]) -> Result<Seat, Refused> {
    let fake = write_fake(dir);
    let err = std::fs::File::create(dir.join("seat.err")).unwrap();
    let r = repo();
    let mut cmd;
    if bind.is_none() {
        cmd = Command::new("sh");
        cmd.arg(r.join("scripts/seat")).args(["--data-dir", dir.to_str().unwrap(), "--port", "0"]);
        for f in facts { cmd.arg("--fact").arg(f); }
        cmd.env("CLIFFVM", BIN).env("CLAUDE_BIN", &fake);
    } else {
        cmd = Command::new(BIN);
        cmd.arg("run").args(barest_packs()).arg(r.join("membrane/apps/barest.c1f"))
            .args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0", "--ui-dir", r.join("membrane/ui").to_str().unwrap(), "--bind", bind.unwrap()])
            .args(["--fact", &format!("door_secret_file={}", dir.join("door.secret").display()), "--fact", &format!("seat_data_dir={}", dir.display()), "--fact", &format!("chat_claude_bin={}", fake.display())]);
        for f in facts { cmd.arg("--fact").arg(f); }
    }
    cmd.current_dir(&r).env("ANTHROPIC_API_KEY", "sk-ant-harness-default-0000");
    let mut child = cmd.stdout(Stdio::piped()).stderr(err).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let lines = Arc::new(Mutex::new(Vec::new()));
    let (tx, rx) = channel();
    let l2 = lines.clone();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { l2.lock().unwrap().push(l.clone()); if l.contains("door listening on") { let _ = tx.send(l); } });
    let t = Instant::now();
    loop {
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(line) => {
                let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
                return Ok(Seat { child, dir: dir.to_path_buf(), port, lines, own_dir: false });
            }
            Err(_) => {
                if let Some(st) = child.try_wait().unwrap() {
                    std::thread::sleep(Duration::from_millis(200));
                    return Err(Refused::Exit(st.code().unwrap_or(-1), std::fs::read_to_string(dir.join("seat.err")).unwrap_or_default()));
                }
                assert!(t.elapsed() < common::DOOR_UP, "the door did not come up in time");
            }
        }
    }
}
fn start(dir: &Path, bind: Option<&str>, facts: &[&str]) -> Seat { match try_start(dir, bind, facts) { Ok(s) => s, Err(Refused::Exit(c, e)) => panic!("the seat did not start (exit {c}): {e}") } }
/// a seat in a fresh folder of its own, removed with it
fn seat(tag: &str) -> Seat { let d = tmp(tag); let mut s = start(&d, None, &[]); s.own_dir = true; s }

fn stop(s: &mut Seat) {
    unsafe { libc::kill(s.child.id() as i32, libc::SIGTERM); }
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(30) { if s.child.try_wait().unwrap().is_some() { return; } std::thread::sleep(Duration::from_millis(50)); }
    let _ = s.child.kill(); let _ = s.child.wait();
}

// ---- a raw HTTP client: the request target is sent as written (no normalising), every header as given ------------------------------------------------------------------------------
struct Resp { status: u16, headers: Vec<(String, String)>, body: String }
impl Resp {
    fn header(&self, n: &str) -> Option<&str> { self.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(n)).map(|(_, v)| v.as_str()) }
    fn all(&self, n: &str) -> Vec<&str> { self.headers.iter().filter(|(k, _)| k.eq_ignore_ascii_case(n)).map(|(_, v)| v.as_str()).collect() }
    fn json(&self) -> J { serde_json::from_str(&self.body).unwrap_or_else(|_| json!(self.body)) }
}
fn raw(host: &str, port: u16, method: &str, target: &str, headers: &[(&str, &str)], body: &str) -> Resp {
    let mut s = TcpStream::connect((host, port)).unwrap();
    s.set_read_timeout(Some(common::IO)).unwrap();
    let mut req = format!("{method} {target} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n");
    for (k, v) in headers { req.push_str(&format!("{k}: {v}\r\n")); }
    if !body.is_empty() || method == "POST" || method == "PUT" || method == "PATCH" { req.push_str(&format!("Content-Length: {}\r\n", body.len())); }
    req.push_str("\r\n");
    s.write_all(req.as_bytes()).unwrap();
    s.write_all(body.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").unwrap_or_else(|| panic!("an HTTP answer to {method} {target}: {:?}", String::from_utf8_lossy(&out)));
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let mut ls = head.split("\r\n");
    let status: u16 = ls.next().unwrap().split_whitespace().nth(1).unwrap().parse().unwrap();
    let headers = ls.filter_map(|l| l.split_once(':').map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))).collect();
    Resp { status, headers, body: String::from_utf8_lossy(&out[i + 4..]).to_string() }
}
fn get(s: &Seat, path: &str, tok: Option<&str>) -> Resp { send(s, "GET", path, tok, "") }
fn send(s: &Seat, method: &str, path: &str, tok: Option<&str>, body: &str) -> Resp {
    let auth = tok.map(|t| format!("Bearer {t}"));
    let mut h: Vec<(&str, &str)> = vec![("Content-Type", "application/json")];
    if let Some(a) = &auth { h.push(("Authorization", a)); }
    raw("127.0.0.1", s.port, method, path, &h, body)
}
fn mcp_call(s: &Seat, tok: &str, tool: &str, args: J) -> Resp {
    send(s, "POST", "/mcp", Some(tok), &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string())
}
fn mcp_text(r: &Resp) -> String { r.json()["result"]["content"][0]["text"].as_str().unwrap_or("").to_string() }
fn read_prop(s: &Seat, tok: &str, spec: &str, prop: &str) -> String { mcp_text(&mcp_call(s, tok, "entity_read", json!({"spec": spec, "prop": prop}))) }
/// every entity of the seat, whole, page after page (the owner's tool)
fn all_entities(s: &Seat, tok: &str) -> String {
    let mut all = String::new();
    for page in 0..100 {
        let r = mcp_call(s, tok, "entity_query", json!({"q": "", "full": true, "k": 100, "offset": page * 100}));
        let t = mcp_text(&r);
        if t.starts_with("0 entity") || t.is_empty() { break; }
        all.push_str(&t); all.push('\n');
    }
    all
}
/// the keys only (what exists), sorted
fn keys(s: &Seat, tok: &str) -> Vec<String> {
    let mut v: Vec<String> = vec![];
    for page in 0..100 {
        let t = mcp_text(&mcp_call(s, tok, "entity_query", json!({"q": "", "k": 100, "offset": page * 100})));
        if t.starts_with("0 entity") || t.is_empty() { break; }
        v.extend(t.lines().map(String::from));
    }
    v.retain(|k| !k.starts_with("Mem:"));   // the seat's own memory gauge, rewritten as the seat runs
    v.sort();
    v
}

fn secret_of(dir: &Path) -> Vec<u8> { std::fs::read_to_string(dir.join("door.secret")).unwrap().trim().as_bytes().to_vec() }
fn owner_token(dir: &Path) -> String { std::fs::read_to_string(dir.join("owner.token")).unwrap().trim().to_string() }
fn mode(p: &Path) -> u32 { std::fs::metadata(p).unwrap().permissions().mode() & 0o777 }
fn mint_cli(dir: &Path, sub: &str, name: &str) -> String {
    let o = Command::new(BIN).args(["token", "mint", "--sub", sub, "--name", name, "--ttl", "1h", "--secret-file", dir.join("door.secret").to_str().unwrap()]).output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}
fn b64u(b: &[u8]) -> String { membrane_vm::jwt::base64_encode(b).trim_end_matches('=').replace('+', "-").replace('/', "_") }
/// a token signed by hand: the header and the claims are the caller's JSON, the signature is HS256 of `secret` (or the given text)
fn forge(secret: &[u8], header: J, claims: J) -> String {
    let (h, c) = (b64u(header.to_string().as_bytes()), b64u(claims.to_string().as_bytes()));
    let sig = b64u(&membrane_vm::jwt::hmac_sha256(secret, format!("{h}.{c}").as_bytes()));
    format!("{h}.{c}.{sig}")
}
fn hs256() -> J { json!({"alg": "HS256", "typ": "JWT"}) }
fn claims(sub: &str, exp_off: f64) -> J { json!({"sub": sub, "name": sub, "iat": (now() - 100.0) as i64, "exp": (now() + exp_off) as i64}) }

fn lan_ip() -> Option<String> {
    let s = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    s.connect("192.0.2.1:9").ok()?;
    let a = s.local_addr().ok()?.ip();
    if a.is_loopback() || a.is_unspecified() { None } else { Some(a.to_string()) }
}
fn cookie_of(r: &Resp) -> Option<String> { r.header("set-cookie").map(String::from) }
const FORBIDDEN: fn() -> J = || json!({"type": "error", "error": {"message": "for the owner"}});
const UNAUTH: fn() -> J = || json!({"error": {"message": "missing or invalid token", "type": "unauthorized"}});

// =====================================================================================================================================================================================
// 1. THE JWT SECRET
// =====================================================================================================================================================================================

/// every route a person's browser, Home Assistant or a script reads, plus the whole entity list: their replies, collected
fn reply_corpus(s: &Seat, tok: &str) -> String {
    let mut all = String::new();
    for p in ["/ui/conf", "/global/health", "/ui/settings", "/membrane/stats", "/membrane/seat", "/membrane/models", "/membrane/options", "/membrane/state", "/state", "/session", "/session/status",
              "/membrane/chat/messages", "/ui/fieldvm", "/v1/models", "/membrane/quota", "/membrane/minds", "/membrane/self/card", "/config", "/agent", "/path", "/project", "/command", "/vcs",
              "/membrane/ha/calls", "/membrane/ha/metrics", "/ui/serve/ui/conf"] {
        let r = get(s, p, Some(tok));
        all.push_str(&format!("{p} {} {:?} {}\n", r.status, r.headers, r.body));
    }
    all.push_str(&all_entities(s, tok));
    for (spec, p) in [("Http:door", "listen"), ("Http:door", "listen_result"), ("membrane::presence", "state")] { all.push_str(&read_prop(s, tok, spec, p)); }
    all
}

#[test]
fn the_secret_persists_across_restarts_tokens_survive_it_and_a_foreign_secret_is_refused() {
    let dir = tmp("secret-persist");
    let mut a = start(&dir, None, &[]);
    let (sec1, own1) = (secret_of(&dir), owner_token(&dir));
    let bob1 = mint_cli(&dir, "bob", "Bob");
    assert_eq!(sec1.len(), 64, "64 hex characters");
    assert!(sec1.iter().all(|c| c.is_ascii_hexdigit()), "hex");
    assert_eq!(mode(&dir.join("door.secret")), 0o600, "the secret file is private");
    assert_eq!(mode(&dir.join("owner.token")), 0o600, "so is the owner token file");
    for t in [&own1, &bob1] { assert_eq!(get(&a, "/global/health", Some(t)).status, 200); }
    stop(&mut a);

    let mut b = start(&dir, None, &[]);
    assert_eq!(secret_of(&dir), sec1, "the secret is kept, not remade");
    assert_eq!(mode(&dir.join("door.secret")), 0o600);
    let own2 = owner_token(&dir);
    assert_eq!(mode(&dir.join("owner.token")), 0o600);
    assert_ne!(own2, own1, "the owner token is regenerated at the boot");
    for (what, t) in [("the owner token of the first boot", &own1), ("the one regenerated at this boot", &own2), ("a person's token of the first boot", &bob1)] {
        assert_eq!(get(&b, "/global/health", Some(t)).status, 200, "{what} verifies after the restart");
    }
    let other = forge(b"another secret of at least 32 bytes!!", hs256(), claims("owner", 3600.0));
    assert_eq!(get(&b, "/global/health", Some(&other)).status, 401, "a token of a DIFFERENT secret is refused");
    assert_eq!(get(&b, "/global/health", Some(&forge(&sec1, hs256(), claims("owner", 3600.0)))).status, 200, "and one signed with this secret is not");
    stop(&mut b);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn the_secret_and_every_token_are_in_no_reply_log_store_or_journal() {
    let dir = tmp("secret-leak");
    let mut s = start(&dir, None, &[]);
    let (sec, own, bob) = (secret_of(&dir), owner_token(&dir), mint_cli(&dir, "bob", "Bob"));
    let sec_s = String::from_utf8(sec.clone()).unwrap();
    // exercise what writes: a page fetch (mints a cookie), a login, a person's first request, a chat turn, a setting, an allow-list entry
    let page = raw("127.0.0.1", s.port, "GET", "/", &[], "");
    let cookie_tok = cookie_of(&page).unwrap().split(';').next().unwrap().split_once('=').unwrap().1.to_string();
    assert_eq!(send(&s, "POST", "/ui/login", None, &json!({"token": bob}).to_string()).status, 200);
    assert_eq!(get(&s, "/session", Some(&bob)).status, 200);
    assert_eq!(send(&s, "POST", "/membrane/chat/message", Some(&own), &json!({"text": "hello there"}).to_string()).status, 202);
    assert_eq!(mcp_call(&s, &own, "entity_write", json!({"spec": "Setting:chat.max_steps", "prop": "set", "value": 33})).status, 200);
    assert_eq!(mcp_call(&s, &own, "entity_write", json!({"spec": "HaAllow:k", "prop": "services", "value": ["light.*"]})).status, 200);
    common::wait_for("the chat turn is done", common::WAIT, || { let r = get(&s, "/membrane/chat/messages", Some(&own)); if r.body.contains("\"state\":\"done\"") { Some(()) } else { None } });
    let corpus = reply_corpus(&s, &own);
    let needles: Vec<(&str, &str)> = vec![("the secret", &sec_s), ("the owner token", &own), ("a person's token", &bob), ("the page's cookie token", &cookie_tok)];
    for (what, n) in &needles {
        assert!(!corpus.contains(*n), "{what} is in a reply or an entity");
        // the secret in the other encodings a reply could carry it in
        assert!(!corpus.contains(&b64u(n.as_bytes())) && !corpus.contains(&membrane_vm::jwt::hex(n.as_bytes())), "{what} (base64url or hex) is in a reply or an entity");
    }
    assert!(!corpus.contains(&format!("{}", &own[own.find('.').unwrap() + 1..own.rfind('.').unwrap()])), "not even the owner token's claims part");
    let scan_files = |label: &str| {
        for e in std::fs::read_dir(&dir).unwrap() {
            let p = e.unwrap().path();
            let name = p.file_name().unwrap().to_string_lossy().to_string();
            if name == "door.secret" || name == "owner.token" || name == "fake-claude" || p.is_dir() { continue; }
            let bytes = std::fs::read(&p).unwrap();
            for (what, n) in &needles { assert!(!bytes.windows(n.len()).any(|w| w == n.as_bytes()), "{label}: {what} is in the file {name} (the store, its journal or the log)"); }
        }
    };
    scan_files("seat running (store, wal)");
    let out = s.lines.lock().unwrap().join("\n");
    for (what, n) in &needles { assert!(!out.contains(*n), "{what} is on the seat's stdout"); }
    stop(&mut s);
    let err = std::fs::read_to_string(dir.join("seat.err")).unwrap();
    for (what, n) in &needles { assert!(!err.contains(*n), "{what} is on the seat's stderr"); }
    scan_files("seat stopped");
    // the seat's own environment variable holds the owner token (docs/auth.md): the seat's `ps` line must not
    let ps = Command::new("ps").args(["-axww", "-o", "command"]).output().unwrap();
    let ps = String::from_utf8_lossy(&ps.stdout).to_string();
    assert!(!ps.contains(&sec_s) && !ps.contains(&own), "a process listing shows no secret and no token");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_second_seat_started_with_the_first_ones_secret_accepts_the_first_ones_tokens() {
    let d1 = tmp("reuse-1");
    let mut a = start(&d1, None, &[]);
    let (own_a, bob_a) = (owner_token(&d1), mint_cli(&d1, "bob", "Bob"));
    let d2 = tmp("reuse-2");
    std::fs::copy(d1.join("door.secret"), d2.join("door.secret")).unwrap();
    std::fs::set_permissions(d2.join("door.secret"), std::fs::Permissions::from_mode(0o600)).unwrap();
    let mut b = start(&d2, None, &[]);
    assert_eq!(secret_of(&d2), secret_of(&d1), "the file is kept as it is");
    for (what, t) in [("the first seat's owner token", &own_a), ("its person's token", &bob_a)] {
        assert_eq!(get(&b, "/global/health", Some(t)).status, 200, "the second seat accepts {what}");
    }
    assert_eq!(get(&b, "/global/health", Some(&owner_token(&d2))).status, 200);
    assert_eq!(get(&a, "/global/health", Some(&owner_token(&d2))).status, 200, "and the first accepts the second's");
    // the same secret is the same cookie name: two seats on one host would share (and overwrite) it
    let ca = cookie_of(&raw("127.0.0.1", a.port, "GET", "/", &[], "")).unwrap();
    let cb = cookie_of(&raw("127.0.0.1", b.port, "GET", "/", &[], "")).unwrap();
    assert_eq!(ca.split('=').next(), cb.split('=').next(), "the cookie's name is a hash of the secret");
    stop(&mut a); stop(&mut b);
    // the Python seat's own secret file (`secrets.token_urlsafe(48)`: 64 characters of the url-safe alphabet) is a secret like any other
    let d3 = tmp("reuse-py");
    let py = "Xk3-_Zq9wLmP2vTn7HdR5sYbC8fGjU1eA4oIuNxMtQyV0hWcBzSrKpDgEaJlFi6O";
    assert_eq!(py.len(), 64);
    std::fs::write(d3.join("door.secret"), format!("{py}\n")).unwrap();
    let mut c = start(&d3, None, &[]);
    assert_eq!(std::fs::read_to_string(d3.join("door.secret")).unwrap().trim(), py, "kept, not remade");
    assert_eq!(get(&c, "/global/health", Some(&forge(py.as_bytes(), hs256(), claims("alice", 600.0)))).status, 200, "a token the old seat signed with it");
    assert_eq!(get(&c, "/global/health", Some(&owner_token(&d3))).status, 200);
    stop(&mut c);
    for d in [d1, d2, d3] { let _ = std::fs::remove_dir_all(d); }
}

#[test]
fn a_missing_or_empty_secret_file_is_made_and_a_short_one_is_a_refusal_not_an_open_door() {
    // missing: made; empty: made again
    for (tag, content) in [("missing", None), ("empty", Some("")), ("blank", Some("  \n"))] {
        let dir = tmp(&format!("secret-{tag}"));
        if let Some(c) = content { std::fs::write(dir.join("door.secret"), c).unwrap(); }
        let mut s = start(&dir, None, &[]);
        assert_eq!(secret_of(&dir).len(), 64, "{tag}: a new secret");
        assert_eq!(mode(&dir.join("door.secret")), 0o600, "{tag}");
        assert_eq!(get(&s, "/global/health", None).status, 401, "{tag}: the door is strict");
        assert_eq!(get(&s, "/global/health", Some(&owner_token(&dir))).status, 200, "{tag}");
        stop(&mut s);
        let _ = std::fs::remove_dir_all(&dir);
    }
    // short (under 32 bytes): the seat REFUSES to start, says why, and opens no door
    let dir = tmp("secret-short");
    std::fs::write(dir.join("door.secret"), "tooshort\n").unwrap();
    let Err(Refused::Exit(code, err)) = try_start(&dir, None, &[]) else { panic!("a seat started with a 8-byte secret") };
    assert_ne!(code, 0);
    assert!(err.contains("32"), "the refusal names the rule: {err}");
    assert!(!dir.join("owner.token").exists(), "no owner token is written for a secret that cannot sign one");
    assert_eq!(std::fs::read_to_string(dir.join("door.secret")).unwrap(), "tooshort\n", "and the file is left as it was");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn an_unreadable_secret_file_is_a_refusal_the_secret_is_never_silently_replaced() {
    let dir = tmp("secret-unreadable");
    let mut a = start(&dir, None, &[]);
    let (sec, own) = (secret_of(&dir), owner_token(&dir));
    stop(&mut a);
    std::fs::set_permissions(dir.join("door.secret"), std::fs::Permissions::from_mode(0o000)).unwrap();
    let r = try_start(&dir, None, &[]);
    std::fs::set_permissions(dir.join("door.secret"), std::fs::Permissions::from_mode(0o600)).unwrap();
    match r {
        Err(Refused::Exit(code, err)) => { assert_ne!(code, 0); assert!(err.contains("door.secret"), "the refusal names the file: {err}"); }
        Ok(mut s) => { stop(&mut s); panic!("a seat started on a secret file it cannot read"); }
    }
    assert_eq!(secret_of(&dir), sec, "the secret on disk is the one it was: nobody's tokens were revoked");
    assert_eq!(owner_token(&dir), own, "and no new owner token was written");
    // a folder where the file should be: refused too
    let d2 = tmp("secret-dir");
    std::fs::create_dir(d2.join("door.secret")).unwrap();
    assert!(matches!(try_start(&d2, None, &[]), Err(Refused::Exit(c, _)) if c != 0), "a directory is no secret");
    for d in [dir, d2] { let _ = std::fs::remove_dir_all(d); }
}

#[test]
fn naming_no_secret_at_all_is_the_open_barest_seat_and_says_so() {
    // `--fact door_secret_file=` (empty) after the launcher's own: no secret, no owner token, an OPEN door, and the page's config says `auth: none`. By decision (docs/cliffvm.md): the
    // operator asked for it by name. The test pins that the open mode cannot be reached by ACCIDENT: a missing file is made, an empty one is made, a short one is refused (the tests above).
    let dir = tmp("secret-none");
    let mut s = start(&dir, None, &["door_secret_file="]);
    assert!(!dir.join("owner.token").exists());
    let r = get(&s, "/ui/conf", None);
    assert_eq!(r.status, 200, "open: {}", r.body);
    assert_eq!(r.json()["auth"], json!("none"), "and the page's config tells it");
    stop(&mut s);
    let _ = std::fs::remove_dir_all(&dir);
}

// =====================================================================================================================================================================================
// 4. THE BYPASSES: tokens
// =====================================================================================================================================================================================

#[test]
fn forged_expired_and_malformed_tokens_are_all_the_same_401_and_the_edges_of_the_leeway_hold() {
    let s = seat("forge");
    let sec = secret_of(&s.dir);
    let health = |t: &str| get(&s, "/global/health", Some(t));
    let signed = |h: J, c: J| forge(&sec, h, c);
    let ok = signed(hs256(), claims("zed", 600.0));
    let exp = |off: f64| signed(hs256(), claims("zed", off));
    let with = |f: &dyn Fn(&mut J)| { let mut c = claims("zed", 600.0); f(&mut c); signed(hs256(), c) };
    let none_alg = format!("{}.{}.", b64u(json!({"alg": "none", "typ": "JWT"}).to_string().as_bytes()), b64u(claims("owner", 600.0).to_string().as_bytes()));
    let parts: Vec<&str> = ok.split('.').collect();
    let flip = |s: &str| { let mut c: Vec<char> = s.chars().collect(); let i = c.len() / 2; c[i] = if c[i] == 'A' { 'B' } else { 'A' }; c.into_iter().collect::<String>() };
    let mut cases: Vec<(&str, String, u16)> = vec![
        ("a good token", ok.clone(), 200),
        ("alg none, no signature", none_alg.clone(), 401),
        ("alg none with the signature slot of a good token", format!("{}.{}.{}", b64u(json!({"alg": "none"}).to_string().as_bytes()), parts[1], parts[2]), 401),
        ("alg NONE", signed(json!({"alg": "NONE"}), claims("owner", 600.0)), 401),
        ("alg HS512 (signed with HS256's mac)", signed(json!({"alg": "HS512", "typ": "JWT"}), claims("zed", 600.0)), 401),
        ("alg hs256 in lower case", signed(json!({"alg": "hs256", "typ": "JWT"}), claims("zed", 600.0)), 401),
        ("alg RS256", signed(json!({"alg": "RS256", "typ": "JWT"}), claims("zed", 600.0)), 401),
        ("no alg at all", signed(json!({"typ": "JWT"}), claims("zed", 600.0)), 401),
        ("a header with crit", signed(json!({"alg": "HS256", "crit": ["exp"]}), claims("zed", 600.0)), 401),
        ("a header with jku / kid (ignored, not followed)", signed(json!({"alg": "HS256", "kid": "../../dev/null", "jku": "http://evil/x"}), claims("zed", 600.0)), 200),
        ("expired 10 s ago (inside the 30 s leeway)", exp(-10.0), 200),
        ("expired 60 s ago", exp(-60.0), 401),
        ("expired an hour ago", exp(-3600.0), 401),
        ("no exp", with(&|c| { c.as_object_mut().unwrap().remove("exp"); }), 401),
        ("exp as text", with(&|c| c["exp"] = json!("9999999999")), 401),
        ("exp null", with(&|c| c["exp"] = J::Null), 401),
        ("no sub", with(&|c| { c.as_object_mut().unwrap().remove("sub"); }), 401),
        ("an empty sub", with(&|c| c["sub"] = json!("")), 401),
        ("a sub that is not text", with(&|c| c["sub"] = json!(7)), 401),
        ("a sub with a control character", with(&|c| c["sub"] = json!("a\nb")), 401),
        ("nbf 10 s ahead (inside the leeway)", with(&|c| c["nbf"] = json!((now() + 10.0) as i64)), 200),
        ("nbf 60 s ahead", with(&|c| c["nbf"] = json!((now() + 60.0) as i64)), 401),
        ("nbf that is not a number", with(&|c| c["nbf"] = json!("soon")), 401),
        ("iat 60 s ahead", with(&|c| c["iat"] = json!((now() + 60.0) as i64)), 401),
        ("iat that is not a number", with(&|c| c["iat"] = json!("yesterday")), 401),
        ("an aud (a token scoped to someone else; this seat names no audience)", with(&|c| c["aud"] = json!("another-service")), 401),
        ("an iss (this seat names no issuer: not checked)", with(&|c| c["iss"] = json!("anyone")), 200),
        ("the signature changed by one character", format!("{}.{}.{}", parts[0], parts[1], flip(parts[2])), 401),
        ("the claims changed (sub -> owner), the old signature kept", format!("{}.{}.{}", parts[0], b64u(claims("owner", 600.0).to_string().as_bytes()), parts[2]), 401),
        ("a fourth segment", format!("{ok}.x"), 401),
        ("two segments", format!("{}.{}", parts[0], parts[1]), 401),
        ("the padded base64 form", format!("{}=.{}.{}", parts[0], parts[1], parts[2]), 401),
        ("the standard alphabet", format!("{}.{}.{}", parts[0], parts[1], parts[2].replace('-', "+").replace('_', "/") + "+"), 401),
        ("a token of 9000 characters", format!("{ok}{}", "A".repeat(9000)), 401),
        ("not a token at all", "hello".to_string(), 401),
        ("an empty token after Bearer", String::new(), 401),
    ];
    // the secret one byte different, and the secret in another case
    let mut other = sec.clone(); other[0] = if other[0] == b'a' { b'b' } else { b'a' };
    cases.push(("signed with the secret one character off", forge(&other, hs256(), claims("owner", 600.0)), 401));
    cases.push(("signed with the secret in upper case", forge(String::from_utf8(sec.clone()).unwrap().to_uppercase().as_bytes(), hs256(), claims("owner", 600.0)), 401));
    for (what, tok, want) in cases {
        let r = health(&tok);
        assert_eq!(r.status, want, "{what}");
        if want == 401 {
            assert_eq!(r.json(), UNAUTH(), "{what}: the one body, which never says why");
            assert_eq!(r.header("www-authenticate"), Some("Bearer realm=\"membrane\""), "{what}");
        }
    }
}

#[test]
fn a_token_is_read_only_from_the_authorization_header_or_this_seats_cookie() {
    let s = seat("placement");
    let (own, sec) = (owner_token(&s.dir), secret_of(&s.dir));
    let path = "/global/health";
    let cname = membrane_vm::jwt::cookie_name(&sec);
    let st = |target: &str, h: &[(&str, &str)]| raw("127.0.0.1", s.port, "GET", target, h, "").status;
    // never from the URL
    for q in ["access_token", "token", "jwt", "bearer", "auth", "api_key", "key"] {
        assert_eq!(st(&format!("{path}?{q}={own}"), &[]), 401, "?{q}=<token>");
    }
    assert_eq!(st(&format!("{path}?x=1&access_token={own}#"), &[]), 401);
    // the header: name in any case, `Bearer` in either case (HTTP is not case-sensitive about names), Basic with the token as the password (Home Assistant, opencode)
    for (h, v) in [("Authorization", format!("Bearer {own}")), ("authorization", format!("Bearer {own}")), ("AUTHORIZATION", format!("Bearer {own}")), ("aUtHoRiZaTiOn", format!("Bearer {own}")), ("Authorization", format!("bearer {own}"))] {
        assert_eq!(st(path, &[(h, &v)]), 200, "{h}: {}", &v[..12]);
    }
    let basic = format!("Basic {}", membrane_vm::jwt::base64_encode(format!("opencode:{own}").as_bytes()));
    assert_eq!(st(path, &[("Authorization", &basic)]), 200, "Basic with the token as the password");
    let basic_empty = format!("Basic {}", membrane_vm::jwt::base64_encode(b"opencode:"));
    assert_eq!(st(path, &[("Authorization", &basic_empty)]), 401, "Basic with an empty password");
    let basic_user = format!("Basic {}", membrane_vm::jwt::base64_encode(format!("{own}:x").as_bytes()));
    assert_eq!(st(path, &[("Authorization", &basic_user)]), 401, "the token as the USER NAME of Basic is nothing");
    for v in [format!("Token {own}"), format!("Bearer{own}"), format!("Bearer: {own}"), own.clone(), format!("Digest {own}")] { assert_eq!(st(path, &[("Authorization", &v)]), 401, "scheme {:?}", &v[..8]); }
    // the cookie: this seat's name works, the old shared name works, another seat's name does not
    assert_eq!(st(path, &[("Cookie", &format!("{cname}={own}"))]), 200, "this seat's cookie");
    assert_eq!(st(path, &[("Cookie", &format!("membrane_jwt={own}"))]), 200, "the old shared name is still read");
    assert_eq!(st(path, &[("Cookie", &format!("membrane_jwt_deadbeef={own}"))]), 401, "another seat's cookie name");
    assert_eq!(st(path, &[("Cookie", &format!("other=1; {cname}={own}; x=2"))]), 200, "among other cookies");
    // a bad Authorization never falls back to a good cookie
    assert_eq!(st(path, &[("Authorization", "Bearer not.a.jwt"), ("Cookie", &format!("{cname}={own}"))]), 401, "bad header + good cookie");
    // two Authorization headers: the door takes one of them (the last): it never mixes them to a better token
    let bad = forge(b"another secret of at least 32 bytes!!", hs256(), claims("owner", 600.0));
    assert_eq!(st(path, &[("Authorization", &format!("Bearer {own}")), ("Authorization", &format!("Bearer {bad}"))]), 401, "good then bad: the bad one decides");
}

// =====================================================================================================================================================================================
// 4/3. THE BYPASSES: paths, methods, proxies. A TOKENLESS request must change nothing.
// =====================================================================================================================================================================================

const PROTECTED: &[&str] = &["/mcp", "/membrane/ha/calls", "/membrane/ha/catalog", "/membrane/ha/metrics", "/membrane/options", "/membrane/models", "/membrane/seat/control", "/session", "/membrane/chat/message", "/ui/settings", "/ui/conf", "/global/health"];

#[test]
fn path_tricks_methods_and_headers_never_open_a_protected_route_to_a_caller_without_a_token() {
    let s = seat("paths");
    let own = owner_token(&s.dir);
    let bob = mint_cli(&s.dir, "bob", "Bob");
    let tricks = |p: &str| -> Vec<String> {
        vec![p.to_string(), format!("{p}/"), format!("{p}/."), format!("{p}/.."), format!("{p}/../{}", p.trim_start_matches('/')), format!("//{}", p.trim_start_matches('/')), format!("/./{}", p.trim_start_matches('/')),
             format!("{p}//"), p.to_uppercase(), format!("{p}%2f.."), format!("{p}%00"), format!("{p};x=1"), format!("{p}?x=/ui/www/"), format!("{p}#/ui/www/"), format!("/ui/serve{p}"), format!("/ui/serve/{p}"),
             format!("/ui/serve//{}", p.trim_start_matches('/')), format!("/ui/www/../{}", p.trim_start_matches('/')), format!("/ui/www/%2e%2e/{}", p.trim_start_matches('/')), format!("/ui/www/..%2f{}", p.trim_start_matches('/')),
             format!("/ui/login/../{}", p.trim_start_matches('/')), format!("/index.html/../{}", p.trim_start_matches('/')), format!("/favicon.ico/../{}", p.trim_start_matches('/')), format!("/%2e%2e{p}")]
    };
    let mut checked = 0;
    for p in PROTECTED {
        for t in tricks(p) {
            for m in ["GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE", "PATCH", "TRACE"] {
                let r = raw("127.0.0.1", s.port, m, &t, &[("Content-Type", "application/json")], if m == "POST" { "{}" } else { "" });
                let is_public_lookalike = t.starts_with("/ui/www/") || t.starts_with("/ui/login") || t.starts_with("/index.html") || t.starts_with("/favicon.ico");
                // no token: the protected ones are 401; the public shell's lookalikes may be 404/400/405 but are NEVER served a protected route's answer (no 2xx but the shell)
                if !is_public_lookalike { assert_eq!(r.status, 401, "{m} {t} with no token"); }
                else { assert!(r.status != 200 || r.header("content-type").map(|c| c.starts_with("text/html") || c.starts_with("image")).unwrap_or(false), "{m} {t} with no token answered {} {}", r.status, r.body); }
                assert!(!r.body.contains("\"calls\"") && !r.body.contains("\"rows\"") && !r.body.contains("\"jsonrpc\""), "{m} {t}: a protected body reached a caller with no token: {}", r.body);
                checked += 1;
            }
        }
    }
    assert!(checked > 2000, "{checked}");
    // a person who is not the owner reaches no owner-only route by any spelling: 403, 404 or 405, never the route's answer
    for t in tricks("/membrane/ha/calls").into_iter().chain(tricks("/membrane/ha/metrics")).chain(tricks("/membrane/ha/catalog")) {
        for m in ["GET", "HEAD", "OPTIONS", "POST"] {
            let r = raw("127.0.0.1", s.port, m, &t, &[("Authorization", &format!("Bearer {bob}")), ("Content-Type", "application/json")], if m == "POST" { r#"{"entities":[],"services":[]}"# } else { "" });
            assert!(!r.body.contains("\"calls\":") && !r.body.contains("\"rows\":") && !(r.status == 200 && r.body.contains("\"ok\":true")), "{m} {t} gave bob the owner's answer: {} {}", r.status, r.body);
        }
    }
    // the owner's own calls still work through the prefix
    assert_eq!(raw("127.0.0.1", s.port, "GET", "/ui/serve/membrane/ha/calls", &[("Authorization", &format!("Bearer {own}"))], "").status, 200);
    // a HEAD to a route whose reply is written for GET used to hold the owner's connection for ever (no answer at all): it is a 405 with `allow`, at once
    let h = raw("127.0.0.1", s.port, "HEAD", "/membrane/ha/metrics", &[("Authorization", &format!("Bearer {own}"))], "");
    assert_eq!(h.status, 405); assert_eq!(h.header("allow"), Some("GET"));
    assert_eq!(raw("127.0.0.1", s.port, "HEAD", "/session", &[("Authorization", &format!("Bearer {own}"))], "").status, 405);
    assert_eq!(raw("127.0.0.1", s.port, "HEAD", "/global/health", &[("Authorization", &format!("Bearer {own}"))], "").status, 200, "the probes and the page's files answer a HEAD themselves");
}

#[test]
fn a_request_with_no_token_changes_nothing_whatever_it_answers() {
    let s = seat("tokenless-writes");
    let own = owner_token(&s.dir);
    // a session and a message of the owner's, so there is something to rename, delete and abort
    let sid = send(&s, "POST", "/session", Some(&own), &json!({"title": "mine"}).to_string()).json()["id"].as_str().unwrap().to_string();
    assert_eq!(send(&s, "POST", &format!("/session/{sid}/prompt_async"), Some(&own), &json!({"parts": [{"type": "text", "text": "first"}]}).to_string()).status, 204);
    common::wait_for("the owner's turn is done", common::WAIT, || if get(&s, &format!("/session/{sid}/message"), Some(&own)).body.contains("Seat answer: first") { Some(()) } else { None });
    let before = (keys(&s, &own), all_entities(&s, &own));
    let _ = &before.1;
    let sessions_before = get(&s, "/session", Some(&own)).body;
    let hist_before = get(&s, "/membrane/chat/messages", Some(&own)).body;
    let writes: Vec<(&str, String, String)> = vec![
        ("POST", "/session".into(), json!({"title": "evil"}).to_string()),
        ("PATCH", format!("/session/{sid}"), json!({"title": "PWNED", "model": {"modelID": "evil-model"}}).to_string()),
        ("POST", format!("/session/{sid}/prompt_async"), json!({"parts": [{"type": "text", "text": "tokenless prompt"}], "model": {"modelID": "evil-model"}}).to_string()),
        ("POST", format!("/session/{sid}/message"), json!({"parts": [{"type": "text", "text": "tokenless message"}]}).to_string()),
        ("POST", format!("/session/{sid}/abort"), "{}".into()),
        ("GET", format!("/session/{sid}/message"), String::new()),
        ("GET", format!("/session/{sid}"), String::new()),
        ("DELETE", format!("/session/{sid}"), String::new()),
        ("POST", "/membrane/chat/message".into(), json!({"text": "tokenless hello"}).to_string()),
        ("POST", "/membrane/chat/message".into(), json!({"text": "tokenless hello", "sid": sid}).to_string()),
        ("POST", "/membrane/seat/control".into(), json!({"paused": true, "loops_max": 99}).to_string()),
        ("POST", "/membrane/seat/pause".into(), json!({"paused": true}).to_string()),
        ("POST", "/membrane/quota/resume".into(), "{}".into()),
        ("POST", "/membrane/ha/catalog".into(), json!({"entities": [{"entity_id": "x.y"}], "services": []}).to_string()),
        ("POST", "/membrane/ha/calls".into(), json!({"id": "k1", "claim": true}).to_string()),
        ("POST", "/membrane/models".into(), json!({"loops": "evil-model", "think": "on"}).to_string()),
        ("POST", "/membrane/options".into(), json!({"self_cpu_share": 0.9, "loops": "evil-model"}).to_string()),
        ("GET", "/v1/models".into(), String::new()),
        ("POST", "/mcp".into(), json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": "Note:evil", "prop": "text", "value": "x"}}}).to_string()),
        ("POST", "/mcp".into(), json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_cliff", "arguments": {"code": "('Note:evil2').text = 'x'"}}}).to_string()),
        ("POST", "/ui/login".into(), json!({"token": "garbage"}).to_string()),
        ("GET", "/event".into(), String::new()),
    ];
    for (m, p, b) in &writes {
        let r = raw("127.0.0.1", s.port, m, p, &[("Content-Type", "application/json")], b);
        assert_eq!(r.status, 401, "{m} {p}: {}", r.body);
    }
    // the replies were all 401: what the requests DID is what is checked now. A setter of a pack runs whatever the reply is, so every one of them must be gated itself.
    let after = (keys(&s, &own), all_entities(&s, &own));
    assert_eq!(after.0, before.0, "a request with no token made or removed an entity: {:?}", after.0.iter().filter(|k| !before.0.contains(k)).collect::<Vec<_>>());
    assert_eq!(get(&s, "/session", Some(&own)).body, sessions_before, "the sessions are as they were (a title, a model, a deletion)");
    assert_eq!(get(&s, "/membrane/chat/messages", Some(&own)).body, hist_before, "no message was added and no turn started");
    assert!(!after.0.iter().any(|k| k.starts_with("Note:")), "a Note was written by a request with no token");
    for (spec, prop) in [("membrane::presence", "control"), ("HaCatalog:ha", "entities"), ("Minds:seat", "loops"), ("Settings:seat", "self_cpu_share"), ("HaCall:k1", "claim")] {
        let t = read_prop(&s, &own, spec, prop);
        assert!(!t.contains("evil") && !t.contains("0.9") && !t.contains("true") && !t.contains("x.y"), "{spec}.{prop} was written by a request with no token: {t}");
    }
}

#[test]
fn a_request_through_a_proxy_is_not_this_machine_whichever_header_says_so() {
    let s = seat("proxy-headers");
    let plain = raw("127.0.0.1", s.port, "GET", "/", &[], "");
    assert!(cookie_of(&plain).is_some(), "the control: the bare loopback page is given the owner cookie");
    for (h, v) in [("X-Forwarded-For", "203.0.113.9"), ("x-forwarded-for", "203.0.113.9, 10.0.0.1"), ("Forwarded", "for=203.0.113.9"), ("X-Real-IP", "203.0.113.9")] {
        let r = raw("127.0.0.1", s.port, "GET", "/", &[(h, v)], "");
        assert_eq!(r.status, 200, "{h}: the shell stays public");
        assert_eq!(cookie_of(&r), None, "{h}: a proxied caller is given no cookie (docs/auth.md: a request carrying X-Forwarded-For, Forwarded or X-Real-IP is off-box even on loopback)");
    }
    // behind a TLS proxy the cookie a login sets is Secure
    let own = owner_token(&s.dir);
    let r = raw("127.0.0.1", s.port, "POST", "/ui/login", &[("Content-Type", "application/json"), ("X-Forwarded-Proto", "https")], &json!({"token": own}).to_string());
    assert_eq!(r.status, 200);
    assert!(cookie_of(&r).unwrap().contains("; Secure"), "{:?}", cookie_of(&r));
}

// =====================================================================================================================================================================================
// 2. THE OWNER COOKIE (the page's side is tests/js/release_verify.test.mjs); the login route
// =====================================================================================================================================================================================

#[test]
fn the_page_fetched_from_this_machine_is_given_the_owner_cookie_with_the_attributes_the_door_names() {
    let s = seat("cookie");
    let sec = secret_of(&s.dir);
    let r = raw("127.0.0.1", s.port, "GET", "/", &[], "");
    assert_eq!(r.status, 200);
    let ck = cookie_of(&r).expect("a cookie");
    let name = membrane_vm::jwt::cookie_name(&sec);
    assert!(ck.starts_with(&format!("{name}=")), "the name is this seat's: {ck}");
    for a in ["; Path=/", "; HttpOnly", "; SameSite=Strict", "; Max-Age=604800"] { assert!(ck.contains(a), "{a} in {ck}"); }
    assert!(!ck.contains("Secure") && !ck.contains("Domain"), "plain http, host-only: {ck}");
    let tok = ck.split(';').next().unwrap().split_once('=').unwrap().1;
    let c = membrane_vm::jwt::verify(tok, &sec, now()).unwrap();
    assert_eq!(c["sub"], "owner");
    assert!((c["exp"].as_f64().unwrap() - c["iat"].as_f64().unwrap() - 604800.0).abs() < 2.0, "7 days");
    assert_eq!(r.header("cache-control"), Some("no-store, max-age=0"), "a cookie-bearing page is never cached");
    // the cookie opens the protected routes, and /ui/conf says who it is
    let cookie = ck.split(';').next().unwrap().to_string();
    let who = raw("127.0.0.1", s.port, "GET", "/ui/conf", &[("Cookie", &cookie)], "").json();
    assert_eq!(who["who"]["sub"], "owner"); assert_eq!(who["auth"], "jwt");
    for f in ["/index.html", "/membrane", "/ui/www/boot.js"] { assert_eq!(raw("127.0.0.1", s.port, "GET", f, &[], "").status, 200, "{f}"); }
    assert_eq!(cookie_of(&raw("127.0.0.1", s.port, "GET", "/ui/www/boot.js", &[], "")), None, "only the page itself carries the cookie, not its files");
    // the operator fact names a different owner: the cookie is that person's
    let d2 = tmp("cookie-op");
    let mut s2 = start(&d2, None, &["operator=ann"]);
    let ck2 = cookie_of(&raw("127.0.0.1", s2.port, "GET", "/", &[], "")).unwrap();
    let c2 = membrane_vm::jwt::verify(ck2.split(';').next().unwrap().split_once('=').unwrap().1, &secret_of(&d2), now()).unwrap();
    assert_eq!(c2["sub"], "ann");
    stop(&mut s2);
    let _ = std::fs::remove_dir_all(&d2);
}

#[test]
fn the_login_route_turns_a_token_into_the_cookie_and_logout_clears_it() {
    let s = seat("login");
    let (own, sec) = (owner_token(&s.dir), secret_of(&s.dir));
    let bob = mint_cli(&s.dir, "bob", "Bob");
    let name = membrane_vm::jwt::cookie_name(&sec);
    let login = |ct: &str, body: &str| raw("127.0.0.1", s.port, "POST", "/ui/login", &[("Content-Type", ct)], body);
    let r = login("application/json", &json!({"token": bob}).to_string());
    assert_eq!(r.status, 200, "{}", r.body);
    assert_eq!(r.json(), json!({"ok": true, "who": {"sub": "bob", "name": "Bob"}}));
    let ck = cookie_of(&r).unwrap();
    for a in [format!("{name}={bob}"), "; Path=/".into(), "; HttpOnly".into(), "; SameSite=Strict".into()] { assert!(ck.contains(&a), "{a} in {ck}"); }
    let age: f64 = ck.split("Max-Age=").nth(1).unwrap().split(';').next().unwrap().parse().unwrap();
    assert!(age > 3500.0 && age <= 3600.0, "the cookie lives as long as the token: {age}");
    assert_eq!(raw("127.0.0.1", s.port, "GET", "/session", &[("Cookie", &format!("{name}={bob}"))], "").status, 200, "the cookie is bob's session");
    // refused: not JSON, a form, a bad token, a token of another secret, no token, a huge body, a GET
    assert_eq!(login("application/x-www-form-urlencoded", &format!("token={bob}")).status, 415, "a cross-site form cannot post a token");
    assert_eq!(login("text/plain", &json!({"token": bob}).to_string()).status, 415);
    for b in [json!({"token": "garbage"}).to_string(), json!({}).to_string(), json!({"token": ""}).to_string(), json!({"token": forge(b"another secret of at least 32 bytes!!", hs256(), claims("owner", 600.0))}).to_string(), "not json".to_string()] {
        let r = login("application/json", &b);
        assert_eq!(r.status, 401, "{b}"); assert_eq!(cookie_of(&r), None); assert_eq!(r.json(), UNAUTH());
    }
    assert_eq!(login("application/json", &format!("{{\"token\":\"{}\"}}", "a".repeat(20000))).status, 413);
    assert_eq!(raw("127.0.0.1", s.port, "GET", "/ui/login", &[], "").status, 405);
    // the seat's own internal token never logs in
    let internal = forge(&sec, hs256(), claims("seat:internal", 600.0));
    assert_eq!(login("application/json", &json!({"token": internal}).to_string()).status, 401);
    // an owner's login is the owner's cookie; logout clears it
    assert_eq!(login("application/json", &json!({"token": own}).to_string()).json()["who"]["sub"], "owner");
    let out = raw("127.0.0.1", s.port, "POST", "/ui/logout", &[("Content-Type", "application/json")], "{}");
    assert_eq!(out.status, 200);
    let c = cookie_of(&out).unwrap();
    assert!(c.starts_with(&format!("{name}=;")) && c.contains("Max-Age=0") && c.contains("HttpOnly"), "{c}");
    // the login is public but the rest is not: a login body is no way to smuggle a token into another route
    assert_eq!(raw("127.0.0.1", s.port, "POST", "/session", &[("Content-Type", "application/json")], &json!({"token": own}).to_string()).status, 401);
}

#[test]
fn the_page_through_the_machines_lan_address_is_given_no_cookie_and_the_routes_are_401() {
    let Some(ip) = lan_ip() else { println!("no LAN address on this machine: the LAN case is skipped"); return; };
    let dir = tmp("lan");
    let mut s = start(&dir, Some("0.0.0.0"), &[]);
    let own = owner_token(&dir);
    let page = raw(&ip, s.port, "GET", "/", &[], "");
    assert_eq!(page.status, 200, "the shell is public");
    assert_eq!(cookie_of(&page), None, "a peer that is not loopback is given no cookie");
    for p in ["/ui/fieldvm", "/ui/conf", "/session", "/membrane/chat/messages", "/ui/settings", "/global/health", "/membrane/ha/calls"] {
        let r = raw(&ip, s.port, "GET", p, &[], "");
        assert_eq!(r.status, 401, "{p}"); assert_eq!(r.json(), UNAUTH(), "{p}: a plain reason the page can show");
    }
    // the way in over the LAN: a token as a Bearer, or through /ui/login (the cookie then works from the same address)
    assert_eq!(raw(&ip, s.port, "GET", "/session", &[("Authorization", &format!("Bearer {own}"))], "").status, 200);
    let l = raw(&ip, s.port, "POST", "/ui/login", &[("Content-Type", "application/json")], &json!({"token": own}).to_string());
    assert_eq!(l.status, 200);
    let cookie = cookie_of(&l).unwrap().split(';').next().unwrap().to_string();
    assert_eq!(raw(&ip, s.port, "GET", "/ui/fieldvm", &[("Cookie", &cookie)], "").status, 200);
    // the page over the LAN with a cookie it already holds does NOT get a fresh one: only the loopback peer is given the owner's
    assert_eq!(cookie_of(&raw(&ip, s.port, "GET", "/", &[("Cookie", &cookie)], "")), None);
    stop(&mut s);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn fact_the_page_is_given_the_owner_cookie_whatever_host_name_the_request_carries() {
    // A FACT, NOT A POLICY (docs/release-verification.md, decision): the door tells "this machine" by the PEER address only. A loopback peer that names ANOTHER host in `Host:` (the shape of a
    // DNS-rebinding page: the browser's address bar says evil.example, the name resolves to 127.0.0.1) is given the owner's cookie, which that page's script cannot read (HttpOnly) but its own
    // fetches ride. When the operator decides on a host allow-list this test flips.
    let s = seat("host-rebind");
    let r = raw("127.0.0.1", s.port, "GET", "/", &[("Host", "evil.example:80")], "");
    assert_eq!(r.status, 200);
    assert!(cookie_of(&r).is_some(), "today a loopback peer with any Host is the owner's page");
}

// =====================================================================================================================================================================================
// 3. A SECOND PERSON
// =====================================================================================================================================================================================

#[test]
fn a_second_person_reads_everything_the_page_reads_and_is_refused_every_owner_only_write_with_nothing_changed() {
    let s = seat("bob");
    let (own, bob) = (owner_token(&s.dir), mint_cli(&s.dir, "bob", "Bob"));
    // reads: health, chat history, the settings feed, sessions, the page's boot read, tools list and reads
    for p in ["/global/health", "/membrane/chat/messages", "/ui/settings", "/session", "/session/status", "/ui/fieldvm", "/ui/conf", "/membrane/models", "/membrane/options", "/v1/models", "/membrane/state", "/membrane/stats"] {
        let r = get(&s, p, Some(&bob));
        assert_eq!(r.status, 200, "bob reads {p}: {}", r.body);
    }
    assert_eq!(get(&s, "/ui/conf", Some(&bob)).json()["who"], json!({"sub": "bob", "name": "Bob"}));
    assert!(get(&s, "/ui/settings", Some(&bob)).json()["settings"].as_array().map(|a| a.len() > 20).unwrap_or(false), "the whole settings feed");
    assert_eq!(send(&s, "POST", "/mcp", Some(&bob), &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).to_string()).status, 200);
    assert_eq!(mcp_call(&s, &bob, "entity_read", json!({"spec": "Setting:chat.max_steps", "prop": "current"})).status, 200);
    assert_eq!(mcp_call(&s, &bob, "entity_query", json!({"q": "kind:Setting", "k": 3})).status, 200);
    assert_eq!(get(&s, "/global/health", Some(&own)).status, 200);   // the owner's own person row exists before the snapshot
    let before = (keys(&s, &own), read_prop(&s, &own, "Setting:chat.max_steps", "current"), read_prop(&s, &own, "Minds:seat", "loops"), get(&s, "/membrane/options", Some(&own)).body);
    // writes: the owner's
    let refused = |what: &str, r: Resp| { assert_eq!((r.status, r.json()), (403, FORBIDDEN()), "{what}: {}", r.body); };
    refused("entity_write of a Note", mcp_call(&s, &bob, "entity_write", json!({"spec": "Note:bobs", "prop": "text", "value": "hi"})));
    refused("run_cliff", mcp_call(&s, &bob, "run_cliff", json!({"code": "('Note:bobs2').text = 'hi'"})));
    refused("a Setting write", mcp_call(&s, &bob, "entity_write", json!({"spec": "Setting:chat.max_steps", "prop": "set", "value": 7})));
    refused("the chat model setting", mcp_call(&s, &bob, "entity_write", json!({"spec": "Setting:model.chat", "prop": "set", "value": "evil-model"})));
    refused("the allow-list setting", mcp_call(&s, &bob, "entity_write", json!({"spec": "Setting:home.allow", "prop": "set", "value": [{"name": "all", "services": ["lock.*"], "entities": ["lock.*"]}]})));
    refused("a HaAllow write", mcp_call(&s, &bob, "entity_write", json!({"spec": "HaAllow:bob", "prop": "services", "value": ["lock.*"]})));
    refused("a HaCall write", mcp_call(&s, &bob, "entity_write", json!({"spec": "HaCall:b1", "prop": "request", "value": {"domain": "lock", "service": "unlock", "target": {"entity_id": "lock.front"}}})));
    refused("a pack", mcp_call(&s, &bob, "entity_write", json!({"spec": "Pack:evil", "prop": "text", "value": "PACK evil 1\n"})));
    refused("the owner's own Person row", mcp_call(&s, &bob, "entity_write", json!({"spec": "Person:owner", "prop": "sub", "value": "bob"})));
    refused("POST /membrane/options", send(&s, "POST", "/membrane/options", Some(&bob), &json!({"self_cpu_share": 0.9}).to_string()));
    refused("POST /membrane/options (a model)", send(&s, "POST", "/membrane/options", Some(&bob), &json!({"loops": "claude"}).to_string()));
    refused("POST /membrane/models", send(&s, "POST", "/membrane/models", Some(&bob), &json!({"loops": "claude"}).to_string()));
    refused("the same through the page's prefix", send(&s, "POST", "/ui/serve/membrane/models", Some(&bob), &json!({"loops": "claude"}).to_string()));
    refused("the same through the page's prefix (mcp)", send(&s, "POST", "/ui/serve/mcp", Some(&bob), &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": "Note:b3", "prop": "text", "value": "x"}}}).to_string()));
    for (m, p, b) in [("GET", "/membrane/ha/calls", ""), ("GET", "/membrane/ha/metrics", ""), ("POST", "/membrane/ha/calls", r#"{"id":"b1","claim":true}"#), ("POST", "/membrane/ha/catalog", r#"{"entities":[{"entity_id":"x.y"}],"services":[]}"#)] {
        refused(&format!("{m} {p}"), send(&s, m, p, Some(&bob), b));
    }
    // and not one of them changed anything
    let after = (keys(&s, &own), read_prop(&s, &own, "Setting:chat.max_steps", "current"), read_prop(&s, &own, "Minds:seat", "loops"), get(&s, "/membrane/options", Some(&own)).body);
    assert_eq!(after.0.iter().filter(|k| !before.0.contains(k)).collect::<Vec<_>>(), Vec::<&String>::new(), "a refused write made an entity");
    assert_eq!((&after.1, &after.2, &after.3), (&before.1, &before.2, &before.3));
    for (spec, prop) in [("HaCatalog:ha", "entities"), ("HaAllow:bob", "services"), ("HaCall:b1", "claim"), ("Setting:home.allow", "current")] { assert!(!read_prop(&s, &own, spec, prop).contains("lock") && !read_prop(&s, &own, spec, prop).contains("x.y"), "{spec}"); }
    // the same calls from the OWNER work (the control)
    assert_eq!(mcp_call(&s, &own, "entity_write", json!({"spec": "Setting:chat.max_steps", "prop": "set", "value": 7})).status, 200);
    assert_eq!(read_prop(&s, &own, "Setting:chat.max_steps", "current"), "7");
    assert_eq!(send(&s, "POST", "/membrane/options", Some(&own), &json!({"self_cpu_share": 0.7}).to_string()).status, 200);
    assert_eq!(get(&s, "/membrane/ha/metrics", Some(&own)).status, 200);
}

#[test]
fn a_token_with_the_sub_ha_cannot_reach_the_home_assistant_routes_and_the_operator_fact_moves_the_owner() {
    let s = seat("ha-sub");
    let own = owner_token(&s.dir);
    let ha = mint_cli(&s.dir, "ha", "Home Assistant");
    for (m, p) in [("GET", "/membrane/ha/calls"), ("GET", "/membrane/ha/metrics"), ("POST", "/membrane/ha/calls"), ("POST", "/membrane/ha/catalog")] {
        let r = send(&s, m, p, Some(&ha), "{}");
        assert_eq!((r.status, r.json()), (403, FORBIDDEN()), "{m} {p}");
    }
    assert_eq!(get(&s, "/global/health", Some(&ha)).status, 200, "it is a verified person: health and Assist's calls work");
    assert_eq!(get(&s, "/membrane/ha/calls", Some(&own)).status, 200);
    // a token minted with the sub `owner` IS the owner (that is how Home Assistant is given the owner's: docs/home-assistant.md)
    let minted_owner = mint_cli(&s.dir, "owner", "Home Assistant");
    assert_eq!(get(&s, "/membrane/ha/calls", Some(&minted_owner)).status, 200);
    // `--fact operator=ann` moves it: the sub `owner` is nobody special then
    let d2 = tmp("ha-op");
    let mut s2 = start(&d2, None, &["operator=ann"]);
    let owner_sub = mint_cli(&d2, "owner", "x");
    let ann = mint_cli(&d2, "ann", "Ann");
    assert_eq!(get(&s2, "/membrane/ha/calls", Some(&owner_sub)).status, 403);
    assert_eq!(get(&s2, "/membrane/ha/calls", Some(&ann)).status, 200);
    assert_eq!(get(&s2, "/membrane/ha/calls", Some(&owner_token(&d2))).status, 200, "the boot's owner token is ann's");
    stop(&mut s2);
    let _ = std::fs::remove_dir_all(&d2);
}

#[test]
fn a_tokenless_caller_meets_the_documented_public_set_and_nothing_else() {
    let s = seat("public-set");
    // public: the shell, its files, the login and logout, the favicon
    for p in ["/", "/index.html", "/membrane", "/favicon.ico", "/ui/www/boot.js", "/ui/www/app.css"] { assert_eq!(raw("127.0.0.1", s.port, "GET", p, &[], "").status, 200, "{p}"); }
    assert_eq!(raw("127.0.0.1", s.port, "GET", "/ui/www/nothing-here.js", &[], "").status, 404);
    assert_eq!(raw("127.0.0.1", s.port, "GET", "/ui/www/../../Cargo.toml", &[], "").status, 404, "no climbing out of the ui folder");
    assert_eq!(raw("127.0.0.1", s.port, "GET", "/ui/www/%2e%2e/%2e%2e/native/Cargo.toml", &[], "").status, 404);
    assert_eq!(raw("127.0.0.1", s.port, "POST", "/ui/logout", &[("Content-Type", "application/json")], "{}").status, 200);
    // NOT public (docs/auth.md names them): health, the opencode probes, the page's config
    for p in ["/global/health", "/command", "/vcs", "/path", "/project", "/project/current", "/agent", "/config", "/ui/conf", "/ui/fieldvm", "/ui/settings", "/session", "/event", "/global/event", "/v1/models", "/state", "/membrane/state"] {
        assert_eq!(raw("127.0.0.1", s.port, "GET", p, &[], "").status, 401, "{p}");
    }
}

#[test]
fn the_one_chat_has_no_second_person_a_message_of_bobs_is_the_chats_and_runs_with_the_owners_power() {
    // THE FACTS of the decision "everyone with access is the owner" (docs/release-verification.md, decision 1). Nothing here is changed policy; each assertion states what the door does TODAY.
    let s = seat("one-chat");
    let (own, bob) = (owner_token(&s.dir), mint_cli(&s.dir, "bob", "Bob"));
    // the owner has a session; bob sees it, writes into it, renames and deletes it (the session routes check only that the caller is verified)
    let sid = send(&s, "POST", "/session", Some(&own), &json!({"title": "owner's"}).to_string()).json()["id"].as_str().unwrap().to_string();
    assert!(get(&s, "/session", Some(&bob)).body.contains(&sid), "bob sees the owner's session: the sessions are not per person");
    assert_eq!(send(&s, "PATCH", &format!("/session/{sid}"), Some(&bob), &json!({"title": "bob renamed it"}).to_string()).status, 200, "bob renames it");
    assert_eq!(send(&s, "POST", "/membrane/chat/message", Some(&bob), &json!({"text": "hello from bob"}).to_string()).status, 202, "bob's message goes to THE chat");
    common::wait_for("bob's message is answered", common::WAIT, || if get(&s, "/membrane/chat/messages", Some(&own)).body.contains("Seat answer: hello from bob") { Some(()) } else { None });
    let hist = get(&s, "/membrane/chat/messages", Some(&own)).json();
    let m = hist["messages"].as_array().unwrap().iter().find(|m| m["text"] == "hello from bob").unwrap();
    assert!(m.get("person").is_none() && m.get("sub").is_none() && m.get("from").is_none(), "the message does not say who sent it: {m}");
    // the turn's CLI carries the OWNER's token: a message of bob's that makes the model write, writes as the owner
    assert_eq!(send(&s, "POST", "/membrane/chat/message", Some(&bob), &json!({"text": "DO: write a note"}).to_string()).status, 202);
    common::wait_for("the turn wrote the note with the owner's token", common::WAIT, || if read_prop(&s, &own, "Note:via-chat", "text").contains("written by a turn") { Some(()) } else { None });
    assert_eq!(mcp_call(&s, &bob, "entity_write", json!({"spec": "Note:direct", "prop": "text", "value": "x"})).status, 403, "while bob's own write through the same door is refused");
    // the seat's controls take bob's ask (the door records it for `membrane::presence`, as the page's pause button asks)
    let r = send(&s, "POST", "/membrane/seat/pause", Some(&bob), &json!({"paused": true}).to_string());
    assert!(r.status != 403 && r.status != 401, "bob's pause is not refused for who he is (this seat has no presence entity, so it answers {}: {})", r.status, r.body);
    assert_eq!(send(&s, "DELETE", &format!("/session/{sid}"), Some(&bob), "").status, 200, "bob deletes the owner's session");
}

#[test]
fn an_open_connection_of_the_owner_does_not_hand_its_credentials_to_a_person_who_reads_the_entities() {
    // the page holds `GET /event` open all the time; its Request entity is alive while it is held, and `entity_read` is any verified person's tool
    let s = seat("held-request");
    let (own, bob) = (owner_token(&s.dir), mint_cli(&s.dir, "bob", "Bob"));
    let mut held = TcpStream::connect(("127.0.0.1", s.port)).unwrap();
    held.write_all(format!("GET /event HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {own}\r\nCookie: x={own}\r\n\r\n").as_bytes()).unwrap();
    held.set_read_timeout(Some(common::IO)).unwrap();
    let mut buf = [0u8; 256];
    let n = held.read(&mut buf).unwrap();
    assert!(String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1.1 200"), "the stream is open");
    let live = mcp_text(&mcp_call(&s, &own, "run_cliff", json!({"code": "[r.eid for r in instances('Request')]"})));
    assert!(live.contains("Request:"), "a held request is a live entity: {live}");
    let ids: Vec<String> = live.trim_matches(|c| c == '[' || c == ']').split(',').map(|x| x.trim().trim_matches('"').to_string()).filter(|x| x.starts_with("Request:")).collect();
    for id in ids {
        for prop in ["headers", "body", "query"] {
            let t = read_prop(&s, &bob, &id, prop);
            assert!(!t.contains(&own), "bob read the owner's token from {id}.{prop}: {t}");
        }
        assert!(!mcp_text(&mcp_call(&s, &bob, "entity_read", json!({"spec": id}))).contains(&own), "{id} whole");
    }
    drop(held);
}

#[test]
fn the_token_checks_of_the_door_are_the_old_seats_not_a_weaker_reading() {
    // jwt.rs direct (the door's verifier): the rules the old seat held that a forged-token matrix cannot reach one by one
    let sec = b"0123456789abcdef0123456789abcdef";
    let t = now();
    let tok = |h: J, c: J| forge(sec, h, c);
    assert!(membrane_vm::jwt::verify(&tok(hs256(), claims("a", 600.0)), sec, t).is_ok());
    assert!(membrane_vm::jwt::verify(&tok(json!({"alg": "HS256", "crit": []}), claims("a", 600.0)), sec, t).is_err(), "crit");
    let mut c = claims("a", 600.0); c["aud"] = json!(["x"]);
    assert!(membrane_vm::jwt::verify(&tok(hs256(), c), sec, t).is_err(), "aud (the seat names no audience)");
    let mut c = claims("a", 600.0); c["nbf"] = json!("x");
    assert!(membrane_vm::jwt::verify(&tok(hs256(), c), sec, t).is_err(), "nbf that is not a number");
    let mut c = claims("a", 600.0); c["iat"] = json!([1]);
    assert!(membrane_vm::jwt::verify(&tok(hs256(), c), sec, t).is_err(), "iat that is not a number");
    // the leeway is exactly 30 s on each side
    let c = json!({"sub": "a", "exp": (t - 29.0) as i64}); assert!(membrane_vm::jwt::verify(&tok(hs256(), c), sec, t).is_ok());
    let c = json!({"sub": "a", "exp": (t - 31.0) as i64}); assert!(membrane_vm::jwt::verify(&tok(hs256(), c), sec, t).is_err());
}
