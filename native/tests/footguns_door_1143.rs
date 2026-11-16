//! Silent foot-guns made loud at the model's door (#1143, operator 2026-11-14): `entity_write` answers a refusal with its reason, in the reply, for a bare id, for a name the VM provides, for the
//! host's own status props of a Pack, for the seat's start and for a request's identity; and a Pack whose text was emptied reads `state: removed`. The seat is the shipped barest one.
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
struct Dir(std::path::PathBuf);
impl Drop for Dir { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); } }

const FAKE: &str = r#"#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
_stdin = __import__("json").loads(sys.stdin.readline())   # the one user message on stdin: [<banner> block, the user's text block]
prompt = _stdin["message"]["content"][-1]["text"]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
json.dump({"argv": sys.argv[1:]}, open(os.path.join(here, "argv." + tag + ".json"), "w"))
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "q-1"})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.0, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}, "session_id": "q-1"})
"#;
const KEY: &str = "sk-ant-settings-sentinel-7731";

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn setup(tag: &str) -> Dir {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-settings-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    let bin = d.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    Dir(d)
}
/// the shipped barest seat on the folder's store (a second launch on the same folder is a restart); `secret` gives it a door secret and admin token (a strict door)
fn start(dir: &Dir, secret: bool, facts: &[&str]) -> Run {
    for _ in 0..4 { if let Some(r) = try_start(dir, secret, facts) { return r; } std::thread::sleep(Duration::from_millis(500)); }
    try_start(dir, secret, facts).unwrap_or_else(|| panic!("the door came up: {}", std::fs::read_to_string(dir.0.join("err.log")).unwrap_or_default()))
}
fn try_start(dir: &Dir, secret: bool, facts: &[&str]) -> Option<Run> {
    let (r, d) = (repo(), &dir.0);
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(d).env("ANTHROPIC_API_KEY", KEY).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f"))
        .args(["--store", d.join("seat.sqlite").to_str().unwrap(), "--port", "0", "--fact", &format!("chat_claude_bin={}", d.join("fake-claude").display())]);
    if secret { cmd.args(["--fact", &format!("door_secret_file={}", d.join("door.secret").display()), "--fact", &format!("seat_data_dir={}", d.display())]); }
    for f in facts { cmd.arg("--fact").arg(f); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(std::fs::File::create(d.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).ok()?;
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Some(Run { child, _lines: rx, port })
}
fn http(port: u16, method: &str, path: &str, tok: Option<&str>, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let auth = tok.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{auth}\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    (head.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0), body.to_string())
}
fn rpc(port: u16, tok: Option<&str>, tool: &str, args: J) -> (u16, String) {
    let (st, body) = http(port, "POST", "/mcp", tok, &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    let t = serde_json::from_str::<J>(&body).ok().and_then(|r| r["result"]["content"][0]["text"].as_str().map(String::from)).unwrap_or(body);
    (st, t)
}
fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop { if let Some(v) = f() { return v; } assert!(t.elapsed() < Duration::from_secs(90), "timed out waiting for {what}"); std::thread::sleep(Duration::from_millis(100)); }
}
/// one property of an entity, as JSON (a text that is not JSON comes back as a string)
fn get(port: u16, eid: &str, prop: &str) -> J {
    let (st, t) = rpc(port, None, "entity_read", json!({"spec": eid, "prop": prop}));
    assert_eq!(st, 200, "{t}");
    if t.ends_with(&format!(" has no {prop}")) { return J::Null; }   // a property nobody wrote (a getter with no value reads the same)
    serde_json::from_str(&t).unwrap_or(J::String(t))
}

fn http_x(port: u16, method: &str, path: &str, tok: Option<&str>, extra: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let auth = tok.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{auth}{extra}\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    (head.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0), body.to_string())
}
fn rpc_x(port: u16, turn: Option<&str>, tool: &str, args: J) -> String {
    let extra = turn.map(|t| format!("X-Turn: {t}\r\n")).unwrap_or_default();
    let (_, body) = http_x(port, "POST", "/mcp", None, &extra, &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    serde_json::from_str::<J>(&body).ok().and_then(|r| r["result"]["content"][0]["text"].as_str().map(String::from)).unwrap_or(body)
}
fn read(port: u16, spec: &str, prop: &str) -> String { rpc_x(port, None, "entity_read", json!({"spec": spec, "prop": prop})) }

fn write(port: u16, spec: &str, prop: &str, v: J) -> String { rpc_x(port, None, "entity_write", json!({"spec": spec, "prop": prop, "value": v})) }

#[test]
fn the_door_refuses_what_it_used_to_accept_silently_and_says_why() {
    let dir = setup("footguns");
    let run = start(&dir, false, &[]);
    let port = run.port;
    // an id with no colon is no entity
    let t = write(port, "nocolon", "n", json!(1));
    assert!(t.starts_with("refused") && t.contains("<Class>:<key>") && t.contains("nocolon"), "{t}");
    // a name the VM provides
    for p in ["cls", "eid", "updated", "pinned", "born_by"] {
        write(port, "Thing:fg1", "n", json!(1));
        let t = write(port, "Thing:fg1", p, json!("x"));
        assert!(t.starts_with("refused") && t.contains(p) && t.contains("read-only"), "{p}: {t}");
    }
    assert_eq!(rpc_x(port, None, "entity_read", json!({"spec": "Thing:fg1", "prop": "cls"})), "Thing");
    // the host's status props of a Pack, the seat's start, a request's identity
    for (spec, p) in [("Pack:fgp", "loaded"), ("Pack:fgp", "error"), ("Pack:fgp", "state"), ("Pack:fgp", "granted_by"), ("Boot:seat", "started"), ("Request:99999", "authed"), ("Request:99999", "sub"), ("Request:99999", "claims")] {
        let t = write(port, spec, p, json!("x"));
        assert!(t.starts_with("refused") && t.contains(p), "{spec}.{p}: {t}");
    }
    assert!(!read(port, "Boot:seat", "started").contains("x"));
    // an ordinary write still works
    assert!(write(port, "Thing:fg1", "n", json!(2)).starts_with("wrote Thing:fg1.n"));
    // a pack: loaded, then its text emptied reads `removed`, never the stale `loaded`/`waiting`
    let t = write(port, "Pack:fgq", "text", json!("PACK fgq 1\nFgq.shown getter: this.eid\n"));
    assert!(t.contains("pack fgq loaded"), "{t}");
    assert_eq!(read(port, "Pack:fgq", "state"), "loaded");
    write(port, "Pack:fgq", "text", J::Null);
    assert_eq!(read(port, "Pack:fgq", "state"), "removed");
}
