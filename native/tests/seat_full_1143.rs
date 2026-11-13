//! scripts/seat-full: the full pack set on the current page, claude the only model (a fake `claude`: nothing runs a model here).
#![cfg(feature = "bin")]
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, _lines: Receiver<String>, port: u16 }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("seat-full-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn script() -> String { format!("{}/../scripts/seat-full", env!("CARGO_MANIFEST_DIR")) }

fn start(data: &std::path::Path, extra: &[&str]) -> Run {
    let mut child = Command::new("sh").arg(script()).args(["--data-dir", data.to_str().unwrap(), "--port", "0"]).args(extra)
        .env("CLIFFVM", BIN).env("CLAUDE_BIN", "/usr/bin/true").stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let mut port = 0u16;
    while let Ok(l) = rx.recv_timeout(Duration::from_secs(30)) {
        if l.starts_with("cliffvm: door listening on") { port = l.rsplit(':').next().unwrap().trim().parse().unwrap(); break; }
    }
    assert!(port != 0, "the door came up");
    Run { child, _lines: rx, port }
}
fn get(port: u16, path: &str, extra: &str) -> (String, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(15))).unwrap();
    s.write_all(format!("GET {path} HTTP/1.1\r\nHost: t\r\n{extra}\r\n").as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    (String::from_utf8_lossy(&out[..i]).to_string(), String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn cookie_of(head: &str) -> String {
    head.lines().find(|l| l.to_lowercase().starts_with("set-cookie:")).expect("a cookie").split_once(':').unwrap().1.trim().split(';').next().unwrap().to_string()
}

#[test]
fn the_first_run_makes_a_private_secret_and_serves_the_page_with_its_cookie_and_the_models_read() {
    let data = tmp("a");
    let run = start(&data, &[]);
    let secret = data.join("door.secret");
    let text = std::fs::read_to_string(&secret).unwrap();
    assert!(text.trim().len() >= 64 && text.trim().chars().all(|c| c.is_ascii_hexdigit()), "a long hex secret");
    assert_eq!(std::fs::metadata(&secret).unwrap().permissions().mode() & 0o777, 0o600, "readable by its owner only");
    let (head, body) = get(run.port, "/", "");
    assert!(head.starts_with("HTTP/1.1 200"), "{head}");
    assert!(body.contains("<html") || body.contains("<!DOCTYPE") || body.contains("<!doctype"), "the page");
    let ck = cookie_of(&head);
    let (h, b) = get(run.port, "/ui/serve/global/health", &format!("Cookie: {ck}\r\n"));
    assert!(h.starts_with("HTTP/1.1 200") && b.contains("healthy"), "{h} {b}");
    let (h, _) = get(run.port, "/ui/serve/global/health", "");
    assert!(h.starts_with("HTTP/1.1 401"), "{h}");
    // the facts the dry run said were needed: the minds' models answer (503 without `minds`), claude is the model
    let (h, b) = get(run.port, "/ui/serve/membrane/models", &format!("Cookie: {ck}\r\n"));
    assert!(h.starts_with("HTTP/1.1 200"), "{h} {b}");
    assert!(b.contains("claude"), "{b}");
    drop(run);
    // a second run keeps the secret: the cookie a person holds still works
    let before = text;
    let run2 = start(&data, &[]);
    assert_eq!(std::fs::read_to_string(&secret).unwrap(), before, "the secret is kept");
    let (h, _) = get(run2.port, "/ui/serve/global/health", &format!("Cookie: {ck}\r\n"));
    assert!(h.starts_with("HTTP/1.1 200"), "the old cookie is still good: {h}");
    let _ = std::fs::remove_dir_all(&data);
}

#[test]
fn a_fact_passes_through_and_wins_over_the_defaults() {
    let data = tmp("b");
    let run = start(&data, &["--fact", "operator=ann"]);
    let (head, _) = get(run.port, "/", "");
    let ck = cookie_of(&head);
    let (_, b) = get(run.port, "/ui/serve/ui/conf", &format!("Cookie: {ck}\r\n"));
    assert!(b.contains("\"sub\":\"ann\""), "the owner cookie is for the operator fact: {b}");
    let _ = std::fs::remove_dir_all(&data);
}
