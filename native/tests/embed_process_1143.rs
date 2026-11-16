//! The real seat and the sentence vectors (#1143, membrane/packs/embed.c1f): a message posted to the barest chat and the answer of a fake `claude` both get their bodies ASKED (`embed = true`),
//! and with a model server that is not there (a closed port) the queue is a visible, reasoned state on the entity and in the Settings row, never a silent stall, and the turn is answered all the same.
//! No Ollama, no real `claude`: the CLI is a script in the test folder and the model server's address is a port nothing listens on.
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
        .args(["--store", d.join("seat.sqlite").to_str().unwrap(), "--port", "0", "--fact", &format!("chat_claude_bin={}", d.join("fake-claude").display()), "--fact", "seat_default=claude"]);
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
/// the window's write: ONE entity_write of `Setting:<id>.set`
fn set(port: u16, id: &str, v: J) -> (u16, String) { rpc(port, None, "entity_write", json!({"spec": format!("Setting:{id}"), "prop": "set", "value": v})) }
fn cur(port: u16, id: &str) -> J { get(port, &format!("Setting:{id}"), "current") }
fn refused(port: u16, id: &str) -> String { get(port, &format!("Setting:{id}"), "refused").as_str().unwrap_or("").to_string() }
/// every stored property of an entity, as the full read prints it
fn full(port: u16, eid: &str) -> J {
    let (_, t) = rpc(port, None, "entity_read", json!({"spec": eid}));
    serde_json::from_str(t.split_once(") ").map(|x| x.1).unwrap_or("null")).unwrap_or(J::Null)
}

#[test]
fn the_chat_asks_for_the_vectors_of_a_message_and_of_its_answer_and_a_model_server_that_is_not_there_is_a_visible_state() {
    let dir = setup("embed");
    let run = start(&dir, false, &["seat_backend=http://127.0.0.1:9", "embed_model=test-embed"]);
    let (st, body) = http(run.port, "POST", "/membrane/chat/message", None, &json!({"text": "Hello there. Remember this."}).to_string());
    assert_eq!(st, 202, "{body}");
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    wait("the turn done", || { let (_, b) = http(run.port, "GET", &format!("/membrane/chat/message/{id}"), None, ""); if b.contains("\"state\":\"done\"") { Some(()) } else { None } });
    let msg = format!("membrane::Chat:main:message:{id}:body");
    let ans = format!("membrane::Chat:main:message:{id}:turn:response:body");
    // every SemText is asked when its text is written (no flag): the asks are `Embed:ask:<the text's id with :: written />`, pending while nothing answers
    let ask = |eid: &str| format!("membrane::Embed:ask:{}", eid.replace("::", "/"));
    wait("the asks", || if get(run.port, &ask(&msg), "of") == json!(msg) { Some(()) } else { None });
    assert_eq!(get(run.port, &ask(&ans), "of"), json!(ans), "the answer's body is asked too");
    // nothing listens at the model server's address: the queue says so, in words, and the pending sentences stay pending
    let q = "membrane::Embed:queue";
    wait("a visible reason", || { let r = get(run.port, q, "reason"); if r.as_str().map_or(false, |t| t.contains("did not answer")) { Some(()) } else { None } });
    assert_eq!(get(run.port, q, "state"), json!("stuck"));
    let row = cur(run.port, "import.embeddings");
    assert!(row.as_str().unwrap().contains("did not answer") && row.as_str().unwrap().contains("texts have sentence vectors"), "{row}");
    assert_eq!(get(run.port, "Setting:import.embeddings", "kind"), json!("readonly"));
    // the turn was answered all the same (the failed embedding is no error of the round)
    let (_, b) = http(run.port, "GET", &format!("/membrane/chat/message/{id}"), None, "");
    assert!(b.contains("Done: Hello there"), "{b}");
}
