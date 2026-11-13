//! The owner's token (#1143, operator 2026-11-13): a localhost peer is NOT the owner (#842). When the seat has a door secret, `cliffvm run` makes sure the secret
//! exists (0600), mints an owner JWT at every boot and writes it to `<seat_data_dir>/owner.token` (0600), so local tools read a token like anyone else.
//! The barest seat (no secret) has none and stays open. Every wait has a timeout and the child is always reaped.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
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
    for n in ["boot.c1f", "door.c1f", "door_auth.c1f", "door_tools.c1f", "entity.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f"] {
        let src = format!("{}/../membrane/packs/{n}", env!("CARGO_MANIFEST_DIR"));
        if let Ok(t) = std::fs::read_to_string(src) { std::fs::write(d.join("packs").join(n), t).unwrap(); }
    }
    std::fs::write(d.join("index.html"), "<html>the page</html>").unwrap();
    d
}

/// start the binary in `dir` (a second start in the same folder is a restart); -> the run and the door's port
fn launch(dir: &std::path::Path, extra: &[&str]) -> (Run, u16) {
    let pd = dir.join("packs");
    let mut child = Command::new(BIN).current_dir(dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .args(extra).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port)
}

fn secret_facts(dir: &std::path::Path) -> Vec<String> {
    vec!["--fact".into(), format!("door_secret_file={}", dir.join("door.secret").display()), "--fact".into(), format!("seat_data_dir={}", dir.display())]
}
fn launch_secret(dir: &std::path::Path, more: &[&str]) -> (Run, u16) {
    let f = secret_facts(dir);
    let mut a: Vec<&str> = f.iter().map(|s| s.as_str()).collect();
    a.extend_from_slice(more);
    launch(dir, &a)
}

/// one request; `extra` = more header lines; -> (status, head, body)
fn http(port: u16, method: &str, path: &str, extra: &str, body: &str) -> (u16, String, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, head, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn bearer(t: &str) -> String { format!("Authorization: Bearer {t}\r\n") }
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }
fn mode(p: &std::path::Path) -> u32 { std::fs::metadata(p).unwrap().permissions().mode() & 0o777 }
fn token_of(dir: &std::path::Path) -> String { std::fs::read_to_string(dir.join("owner.token")).unwrap().trim().to_string() }
fn secret_of(dir: &std::path::Path) -> Vec<u8> { std::fs::read_to_string(dir.join("door.secret")).unwrap().trim().as_bytes().to_vec() }

#[test]
fn the_boot_makes_the_secret_and_writes_an_owner_token_that_the_door_accepts_and_a_bare_caller_does_not_get_in() {
    let dir = tmp("owner-token");
    let (_run, port) = launch_secret(&dir, &[]);
    // the secret was made (0600, long enough), and the owner's token file beside it (0600), signed with it
    assert_eq!(mode(&dir.join("door.secret")), 0o600);
    assert!(secret_of(&dir).len() >= 32);
    assert_eq!(mode(&dir.join("owner.token")), 0o600);
    let tok = token_of(&dir);
    let claims = membrane_vm::jwt::verify(&tok, &secret_of(&dir), now()).expect("the token verifies with the door's secret");
    assert_eq!(claims["sub"], "owner");
    assert_eq!(claims["exp"].as_f64().unwrap() - claims["iat"].as_f64().unwrap(), 2592000.0, "the default life is 30 days");
    // a bare loopback caller: the released 401; with the file's token: served
    let (st, head, body) = http(port, "GET", "/global/health", "", "");
    assert_eq!(st, 401, "{body}");
    assert!(head.to_lowercase().contains("www-authenticate: bearer realm=\"membrane\""), "{head}");
    assert_eq!(serde_json::from_str::<J>(&body).unwrap(), json!({"error": {"message": "missing or invalid token", "type": "unauthorized"}}));
    assert_eq!(http(port, "GET", "/ui/serve/global/health", "", "").0, 401);
    assert_eq!(http(port, "GET", "/global/health", &bearer(&tok), "").0, 200);
    assert_eq!(http(port, "GET", "/ui/serve/global/health", &bearer(&tok), "").0, 200);
    // another key, and a token past its life: 401
    let other = membrane_vm::jwt::mint(b"another secret of at least 32 bytes!!", "owner", "owner", 3600.0, now()).unwrap();
    assert_eq!(http(port, "GET", "/global/health", &bearer(&other), "").0, 401);
    let old = membrane_vm::jwt::mint(&secret_of(&dir), "owner", "owner", 60.0, now() - 7200.0).unwrap();
    assert_eq!(http(port, "GET", "/global/health", &bearer(&old), "").0, 401);
    // the public set stays public, and the page's cookie flow works: the page from this machine is given the owner cookie, /ui/login turns a token into one
    for p in ["/", "/index.html", "/membrane"] { assert_ne!(http(port, "GET", p, "", "").0, 401, "{p}"); }
    assert_ne!(http(port, "GET", "/ui/www/nothing.js", "", "").0, 401);
    let (st, head, _) = http(port, "GET", "/", "", "");
    assert_eq!(st, 200);
    assert!(head.to_lowercase().contains("set-cookie: membrane_jwt_"), "{head}");
    let (st, head, _) = http(port, "POST", "/ui/login", "", &json!({"token": tok}).to_string());
    assert_eq!(st, 200, "{head}");
    assert!(head.to_lowercase().contains("set-cookie: membrane_jwt_"), "{head}");
}

#[test]
fn the_path_is_a_fact_of_the_boot_and_the_token_is_nowhere_in_the_field() {
    let dir = tmp("owner-token-field");
    let (_run, port) = launch_secret(&dir, &[]);
    let tok = token_of(&dir);
    let read = |spec: &str, prop: Option<&str>| -> String {
        let mut args = json!({"spec": spec});
        if let Some(p) = prop { args["prop"] = json!(p); }
        let (st, _, body) = http(port, "POST", "/mcp", &bearer(&tok), &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": args}}).to_string());
        assert_eq!(st, 200, "{body}");
        body
    };
    assert!(read("Boot:seat", Some("owner_token_file")).contains(&dir.join("owner.token").display().to_string()));
    assert!(!read("Boot:seat", None).contains(&tok), "the token value is never a property of the field");
    let (st, _, qb) = http(port, "POST", "/mcp", &bearer(&tok), &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "entity_query", "arguments": {"q": "Boot"}}}).to_string());
    assert_eq!(st, 200);
    assert!(!qb.contains(&tok));
}

#[test]
fn the_operator_and_the_life_are_boot_facts() {
    let dir = tmp("owner-token-facts");
    let (_run, _port) = launch_secret(&dir, &["--fact", "operator=ann", "--fact", "owner_token_ttl_s=120"]);
    let claims = membrane_vm::jwt::verify(&token_of(&dir), &secret_of(&dir), now()).unwrap();
    assert_eq!(claims["sub"], "ann");
    assert_eq!(claims["exp"].as_f64().unwrap() - claims["iat"].as_f64().unwrap(), 120.0);
}

#[test]
fn a_restart_keeps_the_secret_and_replaces_the_token_file_with_a_fresh_one() {
    let dir = tmp("owner-token-restart");
    let (run, _p) = launch_secret(&dir, &["--fact", "operator=ann"]);
    let (first, secret) = (token_of(&dir), secret_of(&dir));
    drop(run);
    std::thread::sleep(Duration::from_millis(1100));   // iat is in whole seconds: a second boot in the same second would mint the same token
    let (_run2, port) = launch_secret(&dir, &["--fact", "operator=bob"]);
    let second = token_of(&dir);
    assert_ne!(first, second, "every boot rotates the token");
    assert_eq!(secret_of(&dir), secret, "the secret is kept");
    assert_eq!(mode(&dir.join("owner.token")), 0o600);
    assert_eq!(membrane_vm::jwt::verify(&second, &secret, now()).unwrap()["sub"], "bob");
    assert_eq!(http(port, "GET", "/global/health", &bearer(&second), "").0, 200);
    let leftovers: Vec<_> = std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string()).filter(|n| n.contains("owner.token") && n != "owner.token").collect();
    assert!(leftovers.is_empty(), "the replace leaves no temp file: {leftovers:?}");
}

#[test]
fn the_barest_seat_has_no_secret_no_token_file_and_stays_open() {
    let dir = tmp("owner-token-barest");
    let (_run, port) = launch(&dir, &[]);
    assert!(!dir.join("owner.token").exists());
    assert!(!dir.join("door.secret").exists());
    assert_eq!(http(port, "GET", "/global/health", "", "").0, 200, "the barest seat is open by decision");
}
