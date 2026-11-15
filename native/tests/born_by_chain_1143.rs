//! The chain of a turn's creations (#1143, operator 2026-11-14): the turn's MCP config carries `X-Turn: <the message's id>`; an entity a door `entity_write` creates says `born_by` = the call's Request
//! (the VM's own), and the Request's `mc_message` is the message of the turn: entity -> born_by -> Request -> message, with plain reads. The seat is the shipped one with a fake `claude`.
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
prompt = sys.argv[-1]
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
const MSG: &str = "membrane::Chat:main:message:t1";

fn cause_of(port: u16, e: &str) -> String { wait("the entity names its cause", || { let r = read(port, e, "born_by"); (!r.contains("has no born_by")).then_some(r) }) }

#[test]
fn what_a_call_creates_says_the_message_of_the_turn_and_keeps_saying_it_after_the_request_is_collected() {
    let dir = setup("bornchain");
    let run = start(&dir, false, &[]);
    let port = run.port;
    rpc_x(port, None, "entity_write", json!({"spec": MSG, "prop": "text", "value": "hello"}));
    // (a) an entity_write with the turn's header
    let t = std::thread::spawn(move || rpc_x(port, Some(MSG), "entity_write", json!({"spec": "Command:slow", "prop": "run", "value": {"id": "r1", "cmd": "sleep 1"}, "wait": 30})));
    assert_eq!(cause_of(port, "Command:slow"), MSG, "the message is the cause");
    let reqs = rpc_x(port, None, "run_cliff", json!({"code": "join([r.eid for r in instances('Request') if r.mc_turn == '".to_string() + MSG + "' and r.mc_tool == 'entity_write'], ' ')"}));
    let req = reqs.trim().to_string();
    assert!(req.starts_with("Request:"), "the call's Request is alive while the call is open: {reqs}");
    let _ = t.join();
    wait("the call's Request is collected", || (read(port, "Command:slow", "born_by") == MSG && read(port, &req, "mc_turn").contains("collected")).then_some(()));
    assert_eq!(read(port, "Command:slow", "born_by"), MSG, "after the Request is gone the chain still ends at the message");
    // (b) a run_cliff block creating an entity
    rpc_x(port, Some(MSG), "run_cliff", json!({"code": "Box:9.n = 1"}));
    assert_eq!(cause_of(port, "Box:9"), MSG, "the block's creations are charged to the message, not to the scratch entity that runs it");
    // (c) a call without the header: the Request, as before
    rpc_x(port, None, "entity_write", json!({"spec": "Box:2", "prop": "n", "value": 1}));
    let r2 = cause_of(port, "Box:2");
    assert!(r2.starts_with("Request:"), "{r2}");
    rpc_x(port, None, "run_cliff", json!({"code": "Box:8.n = 1"}));
    let r3 = cause_of(port, "Box:8");
    assert!(r3.starts_with("Request:") && r3 != r2, "{r3}");
    // a header that names no chat message is no message
    rpc_x(port, Some("membrane::Box:1"), "entity_write", json!({"spec": "Box:3", "prop": "n", "value": 1}));
    assert!(cause_of(port, "Box:3").starts_with("Request:"));
    // which entities did the message create: a plain query over born_by
    let q = rpc_x(port, None, "run_cliff", json!({"code": "join([x.eid for x in instances('Box') if x.born_by == '" .to_string() + MSG + "'], ' ')"}));
    assert_eq!(q.trim(), "Box:9", "{q}");
}
