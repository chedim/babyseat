//! STOP on the one chat (#1143, the page's Stop button): `POST /membrane/chat/message/<id>/abort` ends the running turn of THAT message. The child CLI is killed (the fake CLI writes its pid;
//! the test asks the OS whether it is gone), the turn ends as an `error` whose answer is what was said so far plus a stopped line, the
//! message list shows it, the chat can speak again. A message with nothing running: 200 and nothing happens; an id the chat does not have: 404.
//! No real `claude`: the turn's binary is a fake script (a Boot fact names it).
#![cfg(feature = "bin")]
mod common;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }
struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "model_chat.c1f", "chat_turn.c1f", "door_session.c1f", "door_auth.c1f", "router.c1f", "door_models.c1f"];
/// `hang`: says "starting", then waits for ever (its pid in `pid.<prompt tag>`); any other prompt answers at once
const FAKE: &str = r#"#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
_stdin = __import__("json").loads(sys.stdin.readline())   # the one user message on stdin: [<banner> block, the user's text block]
prompt = _stdin["message"]["content"][-1]["text"]
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "s-1"})
if prompt == "hang":
    open(os.path.join(here, "pid.hang"), "w").write(str(os.getpid()))
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "starting the work"}}})
    time.sleep(3600)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {}})
"#;
fn seat(tag: &str) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::copy(format!("{src}/{n}"), pd.join(n)).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let mut child = Command::new(cliffvm_bin()).current_dir(&dir).env("ANTHROPIC_API_KEY", "sk-ant-harness-default-0000")
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap(),
               "--fact", &format!("chat_claude_bin={}", bin.display()), "--fact", "seat_default=claude"])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(common::DOOR_UP).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port, dir)
}
fn api(port: u16, method: &str, path: &str, body: J) -> (u16, J) {
    let body = if body.is_null() { String::new() } else { body.to_string() };
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(common::IO)).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let st: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    let t = String::from_utf8_lossy(&out[i + 4..]).to_string();
    (st, if t.trim().is_empty() { J::Null } else { serde_json::from_str(&t).unwrap_or_else(|e| panic!("{e}: {t}")) })
}
fn wait<T>(what: &str, f: impl FnMut() -> Option<T>) -> T { common::wait_for(what, common::WAIT, f) }
fn alive(pid: i32) -> bool { Command::new("kill").args(["-0", &pid.to_string()]).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false) }
fn view(port: u16, id: &str) -> J { api(port, "GET", &format!("/membrane/chat/message/{id}"), J::Null).1 }

#[test]
fn stop_ends_the_running_turn_keeps_what_was_said_and_kills_the_child() {
    let (_run, port, dir) = seat("chat-abort");
    // nothing to stop yet: an unknown id is a 404
    assert_eq!(api(port, "POST", "/membrane/chat/message/1-00000001/abort", json!({})).0, 404);
    let (st, b) = api(port, "POST", "/membrane/chat/message", json!({"text": "hang"}));
    assert_eq!(st, 202);
    let id = b["id"].as_str().unwrap().to_string();
    // running, the child is up and has said something
    let pid: i32 = wait("the child's pid", || std::fs::read_to_string(dir.join("pid.hang")).ok().and_then(|t| t.trim().parse().ok()));
    wait("the turn running", || (view(port, &id)["state"] == "running").then_some(()));
    assert!(alive(pid), "the child runs");
    // Stop: 200 true (the store row of a turn that has just started may lag: send it again, a stop that finds nothing running is harmless)
    let ended = wait("the turn ends", || {
        assert_eq!(api(port, "POST", &format!("/membrane/chat/message/{id}/abort"), json!({})), (200, json!(true)));
        let v = view(port, &id);
        (v["state"] == "error").then_some(v)
    });
    let said = ended["response"].as_str().unwrap();
    assert!(said.contains("starting the work"), "the answer so far is kept: {said}");
    assert!(said.to_lowercase().contains("stopped"), "the stop is said in the answer: {said}");
    wait("the child gone", || (!alive(pid)).then_some(()));
    // the history route shows the same ended turn
    let (_, h) = api(port, "GET", "/membrane/chat/messages?limit=5", J::Null);
    assert_eq!(h["messages"][0]["state"], "error");
    // the chat speaks again; a stop on a finished message is a no-op
    let (_, b2) = api(port, "POST", "/membrane/chat/message", json!({"text": "quick again"}));
    let id2 = b2["id"].as_str().unwrap().to_string();
    wait("the next answer", || (view(port, &id2)["state"] == "done").then_some(()));
    assert_eq!(api(port, "POST", &format!("/membrane/chat/message/{id2}/abort"), json!({})), (200, json!(true)));
    assert_eq!(view(port, &id2)["state"], "done", "a finished turn stays finished");
    // GET on the abort route is not a stop
    assert_eq!(api(port, "GET", &format!("/membrane/chat/message/{id}/abort"), J::Null).0, 405);
}
