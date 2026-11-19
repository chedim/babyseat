//! The chat turn over membrama (docs/membrama-chat.md): a turn answered by the local model peer membrama (github.com/chedim/membrama), a `cloud_models` entry with `backend: "membrama"`,
//! through packs/chat_membrama.c1f: the messages and the door's tools written as nodes, the reply read from membrama's table, the same tool loop as `chat_oai`.
//! It needs a membrama program and a Qwen3 chat model file: MEMBRAMA_BIN names the program and MEMBRAMA_TEST_MODEL the gguf. Without them each test says it was skipped and passes, so CI needs neither.
//! The seat is a scratch seat on its own port and folder; no person's seat is touched.
#![cfg(all(feature = "bin", unix))]
mod common;

use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }
fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }

/// the membrama program and the model, or None (said loudly) so the test passes without them
fn gate() -> Option<(String, String)> {
    match (std::env::var("MEMBRAMA_BIN"), std::env::var("MEMBRAMA_TEST_MODEL")) {
        (Ok(b), Ok(m)) => Some((b, m)),
        _ => { eprintln!("SKIPPED: MEMBRAMA_BIN and MEMBRAMA_TEST_MODEL are not set, so the chat turn over membrama was NOT tested"); None }
    }
}

struct Run { child: Child, _lines: Receiver<String>, port: u16, peer: String }
impl Drop for Run {
    fn drop(&mut self) {
        let _ = self.child.kill(); let _ = self.child.wait();
        for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{}-{part}", self.peer)); }
    }
}

fn tmp(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("cliffvm-membrama-{tag}-{}-{}", std::process::id(), common::unique()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// a scratch seat whose only model is `local`, answered by membrama (the peer `peer`, the owner's numbers: 512 slots, key cap 128, value cap 65,536)
fn seat(tag: &str, bin: &str, model: &str) -> Run { seat_with(tag, bin, model, 5, 200) }
/// the same with the seconds the seat waits before it starts the peer again (a test that kills the peer makes it long, so the peer stays gone)
fn seat_with(tag: &str, bin: &str, model: &str, restart_wait_s: u64, max_new: u64) -> Run {
    let dir = tmp(tag);
    let peer = format!("m{}x{}", std::process::id() % 100000, common::unique().rsplit('-').next().unwrap());
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let numbers = ["--slots", "512", "--key-cap", "128", "--val-cap", "65536", "--epoch", "1", "--spins", "1000000", "--depth", "32"];
    let mut argv: Vec<String> = vec![bin.into(), "serve".into(), "--peer".into(), peer.clone(), "--ns".into(), "membrama".into(), "--model".into(), model.into(), "--n-ctx".into(), "4096".into(), "--bank-cap".into(), "2000000000".into(), "--die-with-parent".into()];
    argv.extend(numbers.iter().map(|s| s.to_string()));
    let peers = json!([{"name": peer, "ns": "membrama", "ring_bytes": 65536, "slots": 512, "key_cap": 128, "val_cap": 65536, "spins": 1_000_000, "depth": 32,
                        "run": {"argv": argv, "restart_wait_s": restart_wait_s, "max_restarts": 5, "reset_after_s": 60}}]);
    let entry = json!([{"name": "local", "backend": "membrama", "peer": peer, "ns": "membrama", "max_new": max_new, "enable_thinking": false}]);
    let mut cmd = Command::new(cliffvm_bin());
    cmd.current_dir(&dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"])
        .args(["--fact", &format!("cloud_models={entry}"), "--fact", "router_on=false", "--fact", "seat_backend=http://127.0.0.1:1",
               "--fact", r#"minds={"loops":"local","cortex":"local","workers":"local","seat_default":"local"}"#, "--fact", &format!("peers={peers}")]);
    for v in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"] { cmd.env_remove(v); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).unwrap_or_else(|_| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()));
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Run { child, _lines: rx, port, peer }
}

fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn call(port: u16, tool: &str, args: J) -> String {
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    assert_eq!(st, 200, "{body}");
    let r: J = serde_json::from_str(&body).unwrap();
    r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string()
}
fn send(port: u16, text: &str) -> String {
    let (st, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": text}).to_string());
    assert_eq!(st, 202, "{body}");
    serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string()
}
/// the turn's view once it is done or failed (the model loads first, so this waits longer than the other rungs' tests)
fn ended(port: u16, id: &str) -> J {
    let t = Instant::now();
    loop {
        let (st, body) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        assert_eq!(st, 200, "{body}");
        let v: J = serde_json::from_str(&body).unwrap();
        if v["state"] == "done" || v["state"] == "error" { return v; }
        assert!(t.elapsed() < Duration::from_secs(240), "timed out waiting for the turn: {v}");
        std::thread::sleep(Duration::from_millis(200));
    }
}

#[test]
fn a_plain_turn_is_answered_by_the_local_model_through_membrama() {
    let Some((bin, model)) = gate() else { return };
    let run = seat("plain", &bin, &model);
    let id = send(run.port, "What is 2 plus 2? Answer with just the number.");
    let v = ended(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains('4'), "the answer should be 4: {v}");
}

/// how many entities of a class the seat has (`entity_query` says `0 entity(ies)` for none and lists one entity a line otherwise)
/// the nodes of the rung are VIRTUAL (not rows: `entity_query` does not list them), so each is read by its id; a collected or retired one reads as no `kind`
fn kind_of(port: u16, eid: &str) -> String { call(port, "entity_read", json!({"spec": eid, "prop": "kind"})) }
fn wait_until(what: &str, mut f: impl FnMut() -> bool) { let t = Instant::now(); while !f() { assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}"); std::thread::sleep(Duration::from_millis(200)); } }

#[test]
fn when_the_turn_ends_the_requests_and_messages_of_its_rounds_are_retired_and_the_shared_tools_stay() {
    let Some((bin, model)) = gate() else { return };
    let run = seat("retire", &bin, &model);
    call(run.port, "entity_write", json!({"spec": "Lamp:desk", "prop": "power", "value": "zebra-42"}));
    let id = send(run.port, "Use the entity_read tool to read the property power of the entity Lamp:desk, then tell me its value exactly.");   // two rounds
    let v = ended(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    let turn = format!("main/message/{id}");
    wait_until("the request nodes to be retired", || kind_of(run.port, &format!("membrane::LlmRequest:{turn}-1")) != "request" && kind_of(run.port, &format!("membrane::LlmRequest:{turn}-2")) != "request");
    wait_until("the message nodes to be retired", || kind_of(run.port, &format!("membrane::LlmMsg:{turn}:0")) != "message");
    assert_eq!(kind_of(run.port, "membrane::LlmTool:door:0"), "tool", "the shared tool nodes stay: they are the same for every turn");
}

#[test]
fn a_tool_call_runs_through_the_doors_own_tool_logic_and_the_model_answers_from_its_result() {
    let Some((bin, model)) = gate() else { return };
    let run = seat("tool", &bin, &model);
    call(run.port, "entity_write", json!({"spec": "Lamp:desk", "prop": "power", "value": "zebra-42"}));
    let id = send(run.port, "Use the entity_read tool to read the property power of the entity Lamp:desk, then tell me its value exactly.");
    let v = ended(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert!(v["tools"].as_array().map_or(false, |t| t.iter().any(|n| n == "entity_read")), "the model should have called entity_read: {v}");
    assert!(v["response"].as_str().unwrap_or("").contains("zebra-42"), "the answer should come from the tool's result: {v}");
}

fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": spec, "prop": prop})) }

#[test]
fn a_turn_whose_peer_is_gone_fails_and_says_membrama_is_not_up() {
    let Some((bin, model)) = gate() else { return };
    let run = seat_with("gone", &bin, &model, 600, 200);
    wait_until("the peer to come up", || read(run.port, "Vm:membrama", "link") == "up");
    let _ = Command::new("pkill").args(["-f", &format!("membrama serve --peer {}", run.peer)]).status();   // the peer dies; the seat waits 600 s before it would start it again
    wait_until("the link to say gone", || read(run.port, "Vm:membrama", "link") == "gone");
    let id = send(run.port, "What is 2 plus 2? Answer with just the number.");
    let v = ended(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("membrama is not up"), "the turn should say why: {v}");
}

#[test]
/// What this proves is that the turn ends stopped. That `abort` reaches the open request and membrama stops at its next piece of text is not asserted here (the request is retired when the turn ends, so
/// its cell cannot be read afterwards); membrama's own tests cover an abort before a run starts.
fn stopping_a_turn_ends_it_as_an_error_that_says_it_was_stopped() {
    let Some((bin, model)) = gate() else { return };
    let run = seat_with("stop", &bin, &model, 5, 1500);   // a long answer, so the stop lands in the middle of it
    wait_until("the peer to come up", || read(run.port, "Vm:membrama", "link") == "up");
    let id = send(run.port, "Count from 1 to 1000, one number per line, and nothing else.");
    wait_until("the request to be written", || kind_of(run.port, &format!("membrane::LlmRequest:main/message/{id}-1")) == "request");
    let (st, body) = http(run.port, "POST", &format!("/membrane/chat/message/{id}/abort"), "");
    assert_eq!(st, 200, "{body}");
    let v = ended(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").to_lowercase().contains("stopped"), "the turn should say it was stopped: {v}");
}
