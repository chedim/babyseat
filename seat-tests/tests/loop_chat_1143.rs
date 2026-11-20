//! THE LOOP IS THE CHAT (#1143, operator 2026-11-19: "the loop was replacing the default chat routing"): when the minds name a brain peer for the chat (`Minds:seat.chat` = the peer's namespace), a message starts NO turn on any rung
//! (no claude Command, no LlmRequest); the peer answers by WRITING the message's answer entity, and the unchanged page route shows a done turn. Plus the clifflet runner for peers (`Cliff:<ns>:<id>.code` -> `result` / `error`).
//! The peer is played by the test (a fake brain over shared memory). Needs cliffvm 0.3.2 (the engine that reads the grants claim).
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::{Op, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::Shm;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }
struct Run { child: Child, _lines: Receiver<String>, port: u16, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("cliffvm-peerauth-{tag}-{}-{}", std::process::id(), common::unique()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn peer_name() -> String { format!("a{}x{}", std::process::id() % 100000, common::unique().rsplit('-').next().unwrap()) }
struct Names(String);
impl Drop for Names { fn drop(&mut self) { for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{}-{part}", self.0)); } } }

fn command(dir: &std::path::Path, facts: &[String]) -> Command { command_ex(dir, facts, &[]) }
fn command_ex(dir: &std::path::Path, facts: &[String], without: &[&str]) -> Command {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#') && !without.contains(l)).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(cliffvm_bin());
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    cmd.arg("--fact").arg(format!("door_secret_file={}", dir.join("door.secret").display()));
    cmd.arg("--fact").arg(format!("seat_data_dir={}", dir.display()));
    for f in facts { cmd.arg("--fact").arg(f); }
    cmd
}
fn start(dir: &std::path::Path, facts: &[String]) -> Run { start_ex(dir, facts, &[]) }
fn start_ex(dir: &std::path::Path, facts: &[String], without: &[&str]) -> Run {
    let mut child = command_ex(dir, facts, without).stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).unwrap_or_else(|_| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()));
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Run { child, _lines: rx, port, dir: dir.to_path_buf() }
}
fn read(run: &Run, spec: &str, prop: &str) -> String {
    let token = std::fs::read_to_string(run.dir.join("admin.token")).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": {"spec": spec, "prop": prop}}}).to_string();
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", token.trim(), body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let r: J = serde_json::from_str(text.split_once("\r\n\r\n").map_or("", |x| x.1)).unwrap_or(J::Null);
    r["result"]["content"][0]["text"].as_str().unwrap_or("").to_string()
}
fn wait_for(run: &Run, spec: &str, prop: &str, want: &str, secs: u64) -> String {
    let end = Instant::now() + Duration::from_secs(secs);
    loop { let v = read(run, spec, prop); if v.contains(want) || Instant::now() > end { return v; } std::thread::sleep(Duration::from_millis(100)); }
}

fn decl(name: &str, ns: Option<&str>) -> String {
    let mut o = json!({"name": name, "ring_bytes": 65536, "slots": 64, "key_cap": 128, "val_cap": 1024, "spins": 1_000_000, "depth": 32});
    if let Some(ns) = ns { o["ns"] = json!(ns); }
    json!([o]).to_string()
}

/// the peer's end, with the token it puts in its hello (or none)
struct PeerEnd { ctl: Shm, link: Link, events: Vec<Event> }
impl PeerEnd {
    fn attach(name: &str, ns: &str, token: Option<String>) -> PeerEnd {
        let end = Instant::now() + Duration::from_secs(60);
        let ctl = loop { match Shm::attach(format!("/gl-{name}-ctl")) { Ok(s) => break s, Err(e) => { assert!(Instant::now() < end, "the seat never made its control rings: {e}"); std::thread::sleep(Duration::from_millis(50)); } } };
        let mut link = Link::new(Me { ns: ns.into(), build: "test-peer".into(), epoch: 1.0, support: vec![VERSION], initiator: false });
        if let Some(t) = token { link = link.with_token(t); }
        let mut p = PeerEnd { ctl, link, events: vec![] };
        let hello = p.link.open();
        p.ctl.send(&hello).unwrap();
        p
    }
    fn until(&mut self, ms: u64, done: impl Fn(&[Event]) -> bool) -> bool {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end {
            while let Some(f) = self.ctl.recv().unwrap() {
                let (ev, reply) = self.link.receive(f);
                self.events.extend(ev);
                if let Some(r) = reply { self.ctl.send(&r).unwrap(); }
            }
            if done(&self.events) { return true; }
            std::thread::sleep(Duration::from_millis(10));
        }
        false
    }
}

fn secret(dir: &std::path::Path) -> Vec<u8> { std::fs::read_to_string(dir.join("door.secret")).unwrap().trim().as_bytes().to_vec() }
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }


fn say(p: &mut PeerEnd, ops: Vec<Op>) { let f = p.link.send(ops).expect("the link is open"); p.ctl.send(&f).unwrap(); }
fn wr(wid: &str, id: &str, prop: &str, value: J) -> Op { Op::Wr { wid: wid.into(), id: id.into(), prop: prop.into(), value } }
fn receipt(ev: &[Event], wid: &str) -> Option<Option<String>> {
    ev.iter().find_map(|e| if let Event::Op(Op::Res { wid: w, refused }) = e { if w == wid { Some(refused.clone()) } else { None } } else { None })
}
fn put(run: &Run, eid: &str, prop: &str, v: J) {
    let token = std::fs::read_to_string(run.dir.join("admin.token")).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": eid, "prop": prop, "value": v}}}).to_string();
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", token.trim(), body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
}
/// a seat with one peer `kitchen` whose hello token carries `grants` (None: no claim) and `facts` more; the peer is open
fn seat_with_grants(tag: &str, grants: Option<J>, facts: &[String]) -> (Run, Names, PeerEnd) { seat_ex(tag, grants, facts, &[]) }
fn seat_ex(tag: &str, grants: Option<J>, facts: &[String], without: &[&str]) -> (Run, Names, PeerEnd) {
    let name = peer_name();
    let names = Names(name.clone());
    let mut all = vec![format!("peers={}", decl(&name, Some("kitchen")))];
    all.extend(facts.iter().cloned());
    let run = start_ex(&tmp(tag), &all, without);
    let mut claims = vec![("ns", json!("kitchen"))];
    if let Some(g) = grants { claims.push(("grants", g)); }
    let token = graphlink_auth::mint_with(&secret(&run.dir), "peer:kitchen", "P", &claims, 600.0, now()).unwrap();
    let mut peer = PeerEnd::attach(&name, "kitchen", Some(token));
    assert!(peer.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "the link never opened: {:?}", peer.events);
    (run, names, peer)
}
fn ask(peer: &mut PeerEnd, id: &str, prop: &str, value: J) -> Option<String> {
    say(peer, vec![wr("w1", id, prop, value)]);
    assert!(peer.until(30_000, |ev| receipt(ev, "w1").is_some()), "no receipt: {:?}", peer.events);
    receipt(&peer.events, "w1").unwrap()
}

fn authed(run: &Run, method: &str, path: &str, body: &str) -> (u16, String) {
    let token = std::fs::read_to_string(run.dir.join("admin.token")).unwrap();
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", token.trim(), body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn send(run: &Run, text: &str) -> String {
    let (st, body) = authed(run, "POST", "/membrane/chat/message", &json!({"text": text}).to_string());
    assert_eq!(st, 202, "{body}");
    serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string()
}
fn view(run: &Run, id: &str) -> J {
    let (st, body) = authed(run, "GET", &format!("/membrane/chat/message/{id}"), "");
    assert_eq!(st, 200, "{body}");
    serde_json::from_str(&body).unwrap()
}
fn page_row(run: &Run, id: &str) -> J {
    let (st, body) = authed(run, "GET", "/membrane/chat/messages?limit=5", "");
    assert_eq!(st, 200, "{body}");
    let v: J = serde_json::from_str(&body).unwrap();
    v["messages"].as_array().unwrap().iter().find(|m| m["id"] == id).cloned().unwrap_or(J::Null)
}
fn state_is(run: &Run, id: &str, want: &str, secs: u64) -> J {
    let end = Instant::now() + Duration::from_secs(secs);
    loop { let v = view(run, id); if v["state"] == want || Instant::now() > end { return v; } std::thread::sleep(Duration::from_millis(100)); }
}
/// the seat has seen the peer's link come up (the peer's Opened event is its own end of the handshake)
fn link_up(run: &Run) { assert!(wait_for(run, "Vm:kitchen", "link", "up", 60).contains("up"), "the peer's link is up on the seat"); }
fn brain_facts(chat: &str) -> Vec<String> {
    vec![format!("minds={}", json!({"loops": "", "cortex": "", "workers": "", "seat_default": "", "chat": chat})), "seat_backend=http://127.0.0.1:1".into(), "router_on=false".into()]
}
fn eid(id: &str, tail: &str) -> String { format!("membrane::Chat:main:message:{id}{tail}") }

#[test]
fn a_message_with_the_brain_taking_the_chat_starts_no_turn_and_waits_for_the_brain() {
    let (run, _n, _peer) = seat_with_grants("lc-wait", Some(json!(["*"])), &brain_facts("kitchen"));
    link_up(&run);
    let id = send(&run, "hello brain");
    std::thread::sleep(Duration::from_secs(3));
    let v = view(&run, &id);
    assert_eq!(v["state"], "running", "the brain has it: pending, not an error: {v}");
    assert_eq!(read(&run, &eid(&id, ":turn"), "brain"), "kitchen", "the turn says plainly who has it");
    let (_, cmds) = authed(&run, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_query", "arguments": {"q": "name~:membrane::Command:chat"}}}).to_string());
    assert!(cmds.contains("0 entity"), "no claude run was started: {cmds}");
    assert!(!read(&run, &format!("membrane::LlmRequest:main/message/{id}-1"), "kind").contains("request"), "no LlmRequest");
}

#[test]
fn the_brain_writes_the_answer_and_the_page_route_shows_a_done_turn() {
    let (run, _n, mut peer) = seat_with_grants("lc-answer", Some(json!(["*"])), &brain_facts("kitchen"));
    link_up(&run);
    let id = send(&run, "hello brain");
    state_is(&run, &id, "running", 20);
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:response"), "text", json!("hello person")), None, "the write is accepted");
    let v = state_is(&run, &id, "done", 30);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "hello person", "{v}");
    let row = page_row(&run, &id);
    assert_eq!(row["state"], "done", "{row}");
    assert_eq!(row["response"], "hello person", "{row}");
    assert!(read(&run, &eid(&id, ":turn"), "ended").trim().parse::<f64>().is_ok(), "the turn ended is stamped like a rung's finish");
}

#[test]
fn a_brain_that_is_not_up_is_said_in_plain_words_not_a_hung_turn() {
    // the peer is declared but never attaches: no link
    let name = peer_name();
    let _names = Names(name.clone());
    let mut facts = vec![format!("peers={}", decl(&name, Some("kitchen")))];
    facts.extend(brain_facts("kitchen"));
    let run = start(&tmp("lc-down"), &facts);
    let id = send(&run, "anyone there?");
    let v = state_is(&run, &id, "error", 30);
    assert_eq!(v["state"], "error", "{v}");
    let said = v["response"].as_str().unwrap_or("");
    assert!(said.contains("kitchen") && said.contains("not up"), "plain words: {said}");
}

#[test]
fn stop_ends_a_turn_the_brain_has() {
    let (run, _n, _peer) = seat_with_grants("lc-stop", Some(json!(["*"])), &brain_facts("kitchen"));
    link_up(&run);
    let id = send(&run, "think a long time");
    state_is(&run, &id, "running", 20);
    let (st, _) = authed(&run, "POST", &format!("/membrane/chat/message/{id}/abort"), "");
    assert_eq!(st, 200);
    let v = state_is(&run, &id, "error", 30);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("stopped"), "{v}");
}

#[test]
fn routing_back_to_a_rung_is_the_same_switch_changed() {
    let (run, _n, _peer) = seat_with_grants("lc-back", Some(json!(["*"])), &brain_facts("kitchen"));
    put(&run, "Minds:seat", "chat", json!(""));
    let id = send(&run, "now a rung");
    let v = state_is(&run, &id, "error", 30);
    assert_eq!(v["state"], "error", "no brain: the default routing answers (here it refuses, no model is chosen): {v}");
    assert!(read(&run, &eid(&id, ":turn"), "brain").contains("has no"), "no brain on the turn");
}

// ---- the clifflet runner for peers -----------------------------------------------------------------------------------------------------------------------

#[test]
fn a_peer_writes_code_and_gets_the_value_back_on_the_same_node() {
    let (run, _n, mut peer) = seat_with_grants("lc-cliff", Some(json!(["*"])), &[]);
    assert_eq!(ask(&mut peer, "membrane::Cliff:kitchen:one", "code", json!("1 + 2 * 3")), None);
    assert!(wait_for(&run, "membrane::Cliff:kitchen:one", "result", "7", 60).contains('7'), "{}", read(&run, "membrane::Cliff:kitchen:one", "error"));
    assert!(read(&run, "membrane::Cliff:kitchen:one", "error").contains("has no"), "a value has no error");
}

#[test]
fn a_block_that_fails_writes_error_text_not_a_result() {
    let (run, _n, mut peer) = seat_with_grants("lc-cliff-err", Some(json!(["*"])), &[]);
    assert_eq!(ask(&mut peer, "membrane::Cliff:kitchen:bad", "code", json!("this is not a block ((")), None);
    let e = wait_for(&run, "membrane::Cliff:kitchen:bad", "error", "did not load", 60);
    assert!(e.contains("did not load"), "{e}");
    assert!(read(&run, "membrane::Cliff:kitchen:bad", "result").contains("has no"));
}

#[test]
fn code_on_a_node_of_a_namespace_that_is_no_peer_up_is_not_run() {
    let (run, _n, _peer) = seat_with_grants("lc-cliff-nopeer", Some(json!(["*"])), &[]);
    // a person's write (the door) of a Cliff node that names no connected peer: the runner is for peers only
    put(&run, "membrane::Cliff:nobody:one", "code", json!("1 + 1"));
    std::thread::sleep(Duration::from_secs(4));
    assert!(read(&run, "membrane::Cliff:nobody:one", "result").contains("has no"), "{}", read(&run, "membrane::Cliff:nobody:one", "result"));
}

// ---- a refused node ANSWERS (a peer must never wait on a silence) ---------------------------------------------------------------------------------------

#[test]
fn a_bad_id_answers_error_in_plain_words() {
    let (run, _n, mut peer) = seat_with_grants("lc-cliff-id", Some(json!(["*"])), &[]);
    assert_eq!(ask(&mut peer, "membrane::Cliff:kitchen:call_2_2_0", "code", json!("1 + 1")), None);
    let e = wait_for(&run, "membrane::Cliff:kitchen:call_2_2_0", "error", "refused", 60);
    assert!(e.contains("refused") && e.contains("letters and digits"), "{e}");
    assert!(read(&run, "membrane::Cliff:kitchen:call_2_2_0", "result").contains("has no"));
}

#[test]
fn a_namespace_that_is_not_a_connected_peer_answers_error() {
    let (run, _n, _peer) = seat_with_grants("lc-cliff-ns", Some(json!(["*"])), &[]);
    put(&run, "membrane::Cliff:nobody:one", "code", json!("1 + 1"));
    let e = wait_for(&run, "membrane::Cliff:nobody:one", "error", "refused", 60);
    assert!(e.contains("refused") && e.contains("nobody") && e.contains("not a connected peer"), "{e}");
}

#[test]
fn empty_code_answers_error() {
    let (run, _n, mut peer) = seat_with_grants("lc-cliff-empty", Some(json!(["*"])), &[]);
    assert_eq!(ask(&mut peer, "membrane::Cliff:kitchen:e1", "code", json!("  ")), None);
    let e = wait_for(&run, "membrane::Cliff:kitchen:e1", "error", "refused", 60);
    assert!(e.contains("code is empty"), "{e}");
}

// ---- the brain resolves a turn without answering: an explicit write ---------------------------------------------------------------------------------------

#[test]
fn a_decline_resolves_the_turn_as_done_with_the_reason_shown_and_no_list_is_needed() {
    let (run, _n, mut peer) = seat_with_grants("lc-decline", Some(json!(["*"])), &brain_facts("kitchen"));
    link_up(&run);
    let a = send(&run, "not for you");
    state_is(&run, &a, "running", 20);
    assert_eq!(ask(&mut peer, &eid(&a, ":turn:response"), "declined", json!("not a question")), None);
    let v = state_is(&run, &a, "done", 30);
    assert_eq!(v["state"], "done", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("not a question"), "the reason is shown: {v}");
}

// ---- the tool rows of a RUNNING brain turn are on the page as they are issued ------------------------------------------------------------------------------

fn tools_names(v: &J) -> Vec<String> { v["tools"].as_array().map(|a| a.iter().map(|x| x.as_str().unwrap_or("").to_string()).collect()).unwrap_or_default() }
fn rows(v: &J) -> Vec<(String, String, J)> { v["tool_calls"].as_array().map(|a| a.iter().map(|r| (r["name"].as_str().unwrap_or("").to_string(), r["input"].to_string(), r["result"].clone())).collect()).unwrap_or_default() }
fn until_tools(run: &Run, id: &str, n: usize, secs: u64) -> J {
    let end = Instant::now() + Duration::from_secs(secs);
    loop { let v = view(run, id); if tools_names(&v).len() >= n || Instant::now() > end { return v; } std::thread::sleep(Duration::from_millis(100)); }
}

#[test]
fn a_running_brain_turn_shows_its_tool_rows_on_the_page_routes_as_they_are_issued() {
    let (run, _n, mut peer) = seat_with_grants("lc-tools", Some(json!(["*"])), &brain_facts("kitchen"));
    link_up(&run);
    let id = send(&run, "use two tools");
    state_is(&run, &id, "running", 20);
    let t = |c: &str, p: &str| eid(&id, &format!(":turn:tool:{c}")) + "\u{0}" + p;
    let _ = t;
    for (c, name, input) in [("c1", "entity_read", json!("{\"spec\":\"a\"}")), ("c2", "entity_write", json!("{\"spec\":\"b\"}"))] {
        assert_eq!(ask(&mut peer, &eid(&id, &format!(":turn:tool:{c}")), "name", json!(name)), None);
        assert_eq!(ask(&mut peer, &eid(&id, &format!(":turn:tool:{c}")), "input", input), None);
    }
    assert_eq!(ask(&mut peer, &eid(&id, ""), "tool_ids", json!("c1 c2")), None);
    let v = until_tools(&run, &id, 2, 30);
    assert_eq!(v["state"], "running", "{v}");
    assert_eq!(tools_names(&v), vec!["entity_read", "entity_write"], "the message route lists the rows so far: {v}");
    let row = page_row(&run, &id);
    assert_eq!(tools_names(&row), vec!["entity_read", "entity_write"], "{row}");
    let r = rows(&row);
    assert_eq!(r.len(), 2, "{row}");
    assert_eq!((r[0].0.as_str(), r[1].0.as_str()), ("entity_read", "entity_write"));
    assert!(r[1].2.is_null() || r[1].2 == json!(""), "no result yet: {row}");
    assert!(read(&run, &eid(&id, ""), "stream_view").contains("entity_write"), "the event stream carries them: {}", read(&run, &eid(&id, ""), "stream_view"));
    // the second gets its result: the page route shows it (the page row has no result; the history view has)
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:tool:c2"), "result", json!("ok done")), None);
    assert!(wait_for(&run, &eid(&id, ""), "history_view", "ok done", 30).contains("ok done"));
    // the answer finishes the turn; the rows stay in order
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:response"), "text", json!("answer")), None);
    let v = state_is(&run, &id, "done", 30);
    assert_eq!(v["state"], "done", "{v}");
    let row = page_row(&run, &id);
    assert_eq!(tools_names(&row), vec!["entity_read", "entity_write"], "after the finish: {row}");
    assert_eq!(rows(&row).len(), 2, "{row}");
}

#[test]
fn the_brain_tools_pack_loads_on_a_running_seat_and_the_rows_show() {
    let (run, _n, mut peer) = seat_ex("lc-tools-live", Some(json!(["*"])), &brain_facts("kitchen"), &["brain_tools"]);
    link_up(&run);
    let id = send(&run, "tools, pack loaded later");
    state_is(&run, &id, "running", 20);
    let text = std::fs::read_to_string(repo().join("membrane/packs/brain_tools.c1f")).unwrap();
    put(&run, "Pack:brain_tools", "text", json!(text));
    assert!(wait_for(&run, "Pack:brain_tools", "state", "loaded", 60).contains("loaded"), "{}", read(&run, "Pack:brain_tools", "error"));
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:tool:c1"), "name", json!("entity_read")), None);
    assert_eq!(ask(&mut peer, &eid(&id, ""), "tool_ids", json!("c1")), None);
    let v = until_tools(&run, &id, 1, 30);
    assert_eq!(tools_names(&v), vec!["entity_read"], "{v}");
    assert_eq!(rows(&page_row(&run, &id)).len(), 1);
}

#[test]
fn a_running_brain_turn_carries_the_results_and_the_brains_live_text_and_a_finished_one_does_not() {
    let (run, _n, mut peer) = seat_with_grants("lc-live", Some(json!(["*"])), &brain_facts("kitchen"));
    link_up(&run);
    let id = send(&run, "show me live");
    state_is(&run, &id, "running", 20);
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:tool:c1"), "name", json!("entity_read")), None);
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:tool:c1"), "input", json!("{\"spec\":\"a\"}")), None);
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:tool:c1"), "result", json!("the whole result")), None);
    assert_eq!(ask(&mut peer, &eid(&id, ""), "tool_ids", json!("c1")), None);
    // the brain's live text: its own cell `<ns>::Loop:main.live` (the test stands in for the replica; see the report)
    put(&run, "kitchen::Loop:main", "live", json!("so far the model wrote this"));
    let end = Instant::now() + Duration::from_secs(30);
    let row = loop { let r = page_row(&run, &id); if (r["live"] == "so far the model wrote this" && r["tool_calls"][0]["result"] == "the whole result") || Instant::now() > end { break r; } std::thread::sleep(Duration::from_millis(100)); };
    assert_eq!(row["live"], "so far the model wrote this", "{row}");
    assert_eq!(row["tool_calls"][0]["result"], "the whole result", "a running brain turn's rows carry the result: {row}");
    assert_eq!(ask(&mut peer, &eid(&id, ":turn:response"), "text", json!("answer")), None);
    state_is(&run, &id, "done", 30);
    let row = page_row(&run, &id);
    assert!(row["live"].is_null() || row["live"] == "", "the live text goes with the turn: {row}");
    assert!(row["tool_calls"][0]["result"].is_null(), "a finished turn folds the rows as today (no result in the page row): {row}");
}
