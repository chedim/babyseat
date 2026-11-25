//! The chat turn over a Claude peer (docs/peer-chat.md): a `cloud_models` entry with `backend: "claude-peer"` selects the same rung as membrama (packs/chat_membrama.c1f), with the peer's own namespace
//! (`claude` here). The peer is played by the test (a fake peer attaching to the seat's rings, answering `claude::Reply:<rid>` and `claude::Chunk:<rid>:<k>` cells), as in peers_1143.rs: no Claude is involved.
#![cfg(all(feature = "bin", unix))]
mod common;

use graphlink_core::frame::{Frame, Op, Target, VERSION};
use graphlink_core::link::{Event, Link, Me};
use graphlink_core::transport::Transport;
use graphlink_shm::cells::{Cell, CellState, CellsReader};
use graphlink_shm::Shm;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }
struct Run { child: Child, _lines: Receiver<String>, port: u16 }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("cliffvm-peers-{tag}-{}-{}", std::process::id(), common::unique()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
/// a peer name short enough for a region name (the longest region name is 31 bytes: `/gl-<name>-ctl`)
fn peer_name() -> String { format!("t{}x{}", std::process::id() % 100000, common::unique().rsplit('-').next().unwrap()) }

/// removes the region names of a peer when the test ends: the seat is killed, not stopped, so nobody else would
struct Names(String);
impl Drop for Names { fn drop(&mut self) { for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{}-{part}", self.0)); } } }

fn command_with(dir: &std::path::Path, facts: &[String], extra: &[std::path::PathBuf]) -> Command {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let mut packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    packs.extend(extra.iter().map(|p| p.to_string_lossy().to_string()));
    let mut cmd = Command::new(cliffvm_bin());
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
    cmd
}
fn start_with(dir: &std::path::Path, facts: &[String], extra: &[std::path::PathBuf]) -> Run {
    let mut child = command_with(dir, facts, extra).stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).unwrap_or_else(|_| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()));
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Run { child, _lines: rx, port }
}
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    (head.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0), body.to_string())
}
fn call(port: u16, tool: &str, args: J) -> String {
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    assert_eq!(st, 200, "{body}");
    let r: J = serde_json::from_str(&body).unwrap();
    r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string()
}

/// the peer's end of the control rings: attach (the seat makes them at start), and run a Link over them
struct PeerEnd { ctl: Shm, link: Link, events: Vec<Event> }
impl PeerEnd {
    fn attach(name: &str, ns: &str) -> PeerEnd {
        let end = Instant::now() + Duration::from_secs(60);
        let ctl = loop { match Shm::attach(format!("/gl-{name}-ctl")) { Ok(s) => break s, Err(e) => { assert!(Instant::now() < end, "the seat never made its control rings: {e}"); std::thread::sleep(Duration::from_millis(50)); } } };
        let mut p = PeerEnd { ctl, link: Link::new(Me { ns: ns.into(), build: "test-peer".into(), epoch: 1.0, support: vec![VERSION], initiator: false }), events: vec![] };
        let hello = p.link.open();
        p.ctl.send(&hello).unwrap();
        p
    }
    fn until(&mut self, ms: u64, done: impl Fn(&[Event]) -> bool) -> bool {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end {
            while let Some(f) = self.ctl.recv().unwrap() {
                let f: Frame = f;
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

fn key(eid: &str, prop: &str) -> String { format!("{eid}\u{1}{prop}") }
fn put(port: u16, eid: &str, prop: &str, v: J) { call(port, "entity_write", json!({"spec": eid, "prop": prop, "value": v})); }

impl PeerEnd {
    fn say(&mut self, ops: Vec<Op>) { let f = self.link.send(ops).expect("the link is open"); self.ctl.send(&f).unwrap(); }
    fn open(name: &str, ns: &str) -> PeerEnd {
        let mut p = PeerEnd::attach(name, ns);
        assert!(p.until(30_000, |ev| ev.iter().any(|e| matches!(e, Event::Opened { .. }))), "the link never opened");
        p
    }
}
fn seat_cells(name: &str) -> CellsReader {
    let end = Instant::now() + Duration::from_secs(60);
    loop { match CellsReader::attach(format!("/gl-{name}-sc")) { Ok(r) => return r, Err(e) => { assert!(Instant::now() < end, "no cell table: {e}"); std::thread::sleep(Duration::from_millis(50)); } } }
}
/// poll a cell until `ok` holds (or the time is up) and return what it was last
fn cell_when(r: &CellsReader, k: &str, secs: u64, ok: impl Fn(&Option<Cell>) -> bool) -> Option<Cell> {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        let c = r.get(k, 1_000_000, 32).unwrap();
        if ok(&c) || Instant::now() > end { return c; }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn peer_cells(name: &str, slots: u64) -> graphlink_shm::cells::Cells { graphlink_shm::cells::Cells::create(format!("/gl-{name}-pc"), slots.max(64), 128, 1024, 1).unwrap() }   // the fake peer never retires its replies, so its own table is as big as the seat's
fn turn_ends(port: u16, id: &str) -> J {
    let end = Instant::now() + Duration::from_secs(60);
    loop {
        let (_, body) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        let v: J = serde_json::from_str(&body).unwrap_or(J::Null);
        if v["state"] == "done" || v["state"] == "error" { return v; }
        assert!(Instant::now() < end, "the turn never ended (it waits for a reply that will not come): {v}");
        std::thread::sleep(Duration::from_millis(200));
    }
}


fn start(dir: &std::path::Path, facts: &[String]) -> Run { start_with(dir, facts, &[]) }
fn decl_peer(name: &str, ns: &str) -> String { decl_peer_slots(name, ns, 64) }
fn decl_peer_slots(name: &str, ns: &str, slots: u64) -> String { json!([{"name": name, "ns": ns, "ring_bytes": 65536, "slots": slots, "key_cap": 128, "val_cap": 4096, "spins": 1_000_000, "depth": 32}]).to_string() }

/// the seat with a model `claude-via-peer` answered by the peer `name` in the namespace `claude`; the entry has no `max_new` and no `enable_thinking`: they do not apply to Claude
fn claude_turn(tag: &str) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) { peer_turn(tag, "claude-peer", "claude", json!({})) }
/// the same for any backend the peer rung serves: `extra` is merged into the entry (`max_new`, for instance)
fn peer_turn(tag: &str, backend: &str, ns: &str, extra: J) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) { peer_turn_slots(tag, backend, ns, extra, 64) }
fn peer_turn_slots(tag: &str, backend: &str, ns: &str, extra: J, slots: u64) -> (Run, Names, PeerEnd, graphlink_shm::cells::Cells) {
    let name = peer_name();
    let names = Names(name.clone());
    let mut e = json!({"name": "claude-via-peer", "backend": backend, "peer": name, "ns": ns});
    for (k, v) in extra.as_object().unwrap() { e[k] = v.clone(); }
    let entry = json!([e]);
    let facts = [format!("cloud_models={entry}"), "router_on=false".to_string(), "seat_backend=http://127.0.0.1:1".to_string(),
                 r#"minds={"loops":"claude-via-peer","cortex":"claude-via-peer","workers":"claude-via-peer","seat_default":"claude-via-peer"}"#.to_string(), format!("peers={}", decl_peer_slots(&name, ns, slots))];
    let run = start(&tmp(tag), &facts);
    let mine = peer_cells(&name, slots * 2);
    let peer = PeerEnd::open(&name, ns);
    // a turn starts only on a peer whose root the seat has made: the peer's own end opening is not that (the seat reads the hello a moment later)
    wait_until("the root of the peer never came up", 60, || call(run.port, "run_cliff", json!({"code": format!("${{'{}'}}['{}']", format!("Vm:{ns}"), "link")})) == "up");
    (run, names, peer, mine)
}
fn send(run: &Run, text: &str) -> String {
    let (st, body) = http(run.port, "POST", "/membrane/chat/message", &json!({"text": text}).to_string());
    assert_eq!(st, 202, "{body}");
    serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string()
}
/// the node id of the reply the seat asks the peer for, and that it is in the peer's namespace; `not` skips an earlier round's
fn reply_ask(peer: &mut PeerEnd, not: Option<&str>) -> String { reply_ask_in(peer, "claude", not) }
fn reply_ask_in(peer: &mut PeerEnd, ns: &str, not: Option<&str>) -> String {
    let want = format!("{ns}::Reply:");
    let find = |ev: &[Event]| ev.iter().find_map(|e| if let Event::Op(Op::Sub { target: Target::Id(i), .. }) = e { (i.starts_with(&want) && Some(i.as_str()) != not).then(|| i.clone()) } else { None });
    assert!(peer.until(60_000, |ev| find(ev).is_some()), "the seat never asked the claude peer for a reply: {:?}", peer.events);
    find(&peer.events).unwrap()
}
/// the peer answers: the chunk, then the reply that says how many chunks and how it finished
fn answer(mine: &mut graphlink_shm::cells::Cells, reply: &str, text: &str, finish: &str, calls: Option<J>) {
    let chunk = reply.replacen("::Reply:", "::Chunk:", 1) + ":0";
    for (k, v) in [("", json!(true)), ("text", json!(text))] { mine.put(&key(&chunk, k), 1.0, &v).unwrap(); }
    let mut cells = vec![("", json!(true)), ("state", json!("done")), ("finish", json!(finish)), ("chunks", json!(1)), ("usage", json!({"read": 10, "loaded": 0, "generated": 5}))];
    if let Some(c) = calls { cells.push(("tool_calls", c)); }
    for (k, v) in cells { mine.put(&key(reply, k), 1.0, &v).unwrap(); }
}

#[test]
fn a_plain_turn_is_answered_by_a_claude_peer_in_its_own_namespace_and_the_request_carries_no_claude_settings() {
    let (run, names, mut peer, mut mine) = claude_turn("cp-plain");
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let id = send(&run, "hello");
    let reply = reply_ask(&mut peer, None);
    // the request names the peer and has NO max_new / enable_thinking: the entry gave none and none is invented
    let seen = seat_cells(&names.0);
    let rid = reply.replace("claude::Reply:", "membrane::LlmRequest:");
    let live = |c: &Option<Cell>| matches!(c, Some(c) if c.state == CellState::Live);
    let settings = cell_when(&seen, &key(&rid, "settings"), 30, live).expect("the request's settings");
    assert_eq!(settings.value, json!({}), "claude settings: {settings:?}");
    answer(&mut mine, &reply, "Hello from the peer", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Hello from the peer", "{v}");
}

#[test]
fn a_tool_call_of_a_claude_peer_is_run_by_the_seat_and_fed_back_in_the_next_round() {
    let (run, _names, mut peer, mut mine) = claude_turn("cp-tool");
    put(run.port, "Lamp:desk", "power", json!("zebra-42"));
    let id = send(&run, "read the lamp");
    let r1 = reply_ask(&mut peer, None);
    answer(&mut mine, &r1, "", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Lamp:desk'}['power']"}}])));
    let r2 = reply_ask(&mut peer, Some(&r1));
    assert!(r2.ends_with("-2"), "round 2: {r2}");
    answer(&mut mine, &r2, "The lamp says zebra-42", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert!(v["tools"].as_array().map_or(false, |t| t.iter().any(|n| n == "run_cliff")), "the seat should have run run_cliff: {v}");
    assert!(v["response"].as_str().unwrap_or("").contains("zebra-42"), "{v}");
}

#[test]
fn a_claude_peer_that_goes_away_fails_the_turn_and_names_its_namespace() {
    let (run, _names, mut peer, _mine) = claude_turn("cp-gone");
    let id = send(&run, "hello");
    let _ = reply_ask(&mut peer, None);
    drop(peer);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("claude is not up"), "the turn should say the peer is not up: {v}");
}

#[test]
fn a_peer_turns_raw_exchange_is_in_the_model_chat_entity_in_the_same_shape_as_every_other_rung() {
    let (run, _names, mut peer, mut mine) = claude_turn("cp-mchat");
    put(run.port, "Lamp:desk", "power", json!("zebra-42"));
    let id = send(&run, "read the lamp");
    let r1 = reply_ask(&mut peer, None);
    answer(&mut mine, &r1, "<think>look at it</think>reading", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Lamp:desk'}['power']"}}])));
    let r2 = reply_ask(&mut peer, Some(&r1));
    answer(&mut mine, &r2, "The lamp says zebra-42", "stop", None);
    assert_eq!(turn_ends(run.port, &id)["state"], "done");
    let end = Instant::now() + Duration::from_secs(30);
    let t = loop {
        let t = call(run.port, "run_cliff", json!({"code": "${'ModelChat:main'}['text']"}));
        if t.contains("[assistant]\nThe lamp says zebra-42") { break t; }
        assert!(Instant::now() < end, "the model chat never held the answer: {t}");
        std::thread::sleep(Duration::from_millis(200));
    };
    let at = |s: &str| t.find(s).unwrap_or_else(|| panic!("{s:?} is not in the model chat:\n{t}"));
    assert!(at("[system]") < at("[user]\nread the lamp") && at("[user]\nread the lamp") < at("<think>look at it</think>reading"), "{t}");
    assert!(at("<think>look at it</think>reading") < at("[call] run_cliff") && at("[call] run_cliff") < at("[tool]\n") && at("[tool]\n") < at("[assistant]\nThe lamp says zebra-42"), "{t}");
    assert!(t.contains("zebra-42"), "the tool's result, as the model got it: {t}");
}

// ---- the OpenAI-compatible and the Claude API peers (graphlink-openai, graphlink-anthropic): the same rung, their own backend names ------------------------------------------------------

fn settings_of(names: &Names, reply: &str, ns: &str) -> J {
    let rid = reply.replace(&format!("{ns}::Reply:"), "membrane::LlmRequest:");
    let live = |c: &Option<Cell>| matches!(c, Some(c) if c.state == CellState::Live);
    cell_when(&seat_cells(&names.0), &key(&rid, "settings"), 30, live).expect("the request's settings").value
}

#[test]
fn an_openai_peer_answers_in_its_own_namespace_and_the_entrys_max_new_is_the_requests_max_tokens() {
    let (run, names, mut peer, mut mine) = peer_turn("pb-oai", "openai-peer", "oai", json!({"max_new": 256}));
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let id = send(&run, "hello");
    let reply = reply_ask_in(&mut peer, "oai", None);
    assert_eq!(settings_of(&names, &reply, "oai"), json!({"max_new": 256}), "the owner's number goes to the peer as it is, nothing else");
    answer(&mut mine, &reply, "Hello from the OpenAI peer", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Hello from the OpenAI peer", "{v}");
}

#[test]
fn an_openai_peer_entry_with_no_max_new_sends_none_and_the_server_chooses() {
    let (run, names, mut peer, mut mine) = peer_turn("pb-oai0", "openai-peer", "oai", json!({}));
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let id = send(&run, "hello");
    let reply = reply_ask_in(&mut peer, "oai", None);
    assert_eq!(settings_of(&names, &reply, "oai"), json!({}), "no number is invented");
    answer(&mut mine, &reply, "ok", "stop", None);
    assert_eq!(turn_ends(run.port, &id)["state"], "done");
}

#[test]
fn an_anthropic_peer_gets_no_settings_its_max_tokens_is_the_owners_launch_option() {
    let (run, names, mut peer, mut mine) = peer_turn("pb-ant", "anthropic-peer", "ant", json!({"max_new": 99}));
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let id = send(&run, "hello");
    let reply = reply_ask_in(&mut peer, "ant", None);
    assert_eq!(settings_of(&names, &reply, "ant"), json!({}), "the Claude API peer reads no settings");
    answer(&mut mine, &reply, "Hello from the Claude API peer", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Hello from the Claude API peer", "{v}");
}

#[test]
fn a_tool_call_of_an_openai_peer_and_of_an_anthropic_peer_is_run_by_the_seat_and_fed_back() {
    for (backend, ns, tag) in [("openai-peer", "oai", "pb-oai-t"), ("anthropic-peer", "ant", "pb-ant-t")] {
        let (run, _names, mut peer, mut mine) = peer_turn(tag, backend, ns, json!({}));
        put(run.port, "Lamp:desk", "power", json!("zebra-42"));
        let id = send(&run, "read the lamp");
        let r1 = reply_ask_in(&mut peer, ns, None);
        answer(&mut mine, &r1, "", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Lamp:desk'}['power']"}}])));
        let r2 = reply_ask_in(&mut peer, ns, Some(&r1));
        answer(&mut mine, &r2, "The lamp says zebra-42", "stop", None);
        let v = turn_ends(run.port, &id);
        assert_eq!(v["state"], "done", "{backend}: {v}");
        assert!(v["response"].as_str().unwrap_or("").contains("zebra-42"), "{backend}: {v}");
    }
}

#[test]
fn an_answer_the_server_cut_says_so_and_a_complete_one_says_nothing_more() {
    for (finish, said) in [("length", "reached its limit on tokens"), ("content_filter", "content filter")] {
        let (run, _names, mut peer, mut mine) = peer_turn(&format!("pb-cut-{finish}"), "openai-peer", "oai", json!({}));
        let id = send(&run, "write a long story");
        let reply = reply_ask_in(&mut peer, "oai", None);
        answer(&mut mine, &reply, "Once upon a", finish, None);
        let v = turn_ends(run.port, &id);
        assert_eq!(v["state"], "done", "{v}");
        let r = v["response"].as_str().unwrap_or("");
        assert!(r.starts_with("Once upon a"), "what the model wrote stays: {v}");
        assert!(r.contains("cut") && r.contains(said), "the person is told the answer is cut ({finish}): {v}");
    }
    let (run, _names, mut peer, mut mine) = peer_turn("pb-whole", "anthropic-peer", "ant", json!({}));
    let id = send(&run, "hi");
    let reply = reply_ask_in(&mut peer, "ant", None);
    answer(&mut mine, &reply, "Complete.", "stop", None);
    assert_eq!(turn_ends(run.port, &id)["response"], "Complete.");
}

#[test]
fn an_openai_or_anthropic_entry_without_peer_or_ns_refuses_the_turn_and_says_what_it_needs() {
    for (backend, tag) in [("openai-peer", "pb-r1"), ("anthropic-peer", "pb-r2")] {
        let entry = json!([{"name": "claude-via-peer", "backend": backend}]);
        let facts = [format!("cloud_models={entry}"), "router_on=false".to_string(), "seat_backend=http://127.0.0.1:1".to_string(),
                     r#"minds={"loops":"claude-via-peer","cortex":"claude-via-peer","workers":"claude-via-peer","seat_default":"claude-via-peer"}"#.to_string()];
        let run = start(&tmp(tag), &facts);
        let id = send(&run, "hello");
        let v = turn_ends(run.port, &id);
        assert_eq!(v["state"], "error", "{backend}: {v}");
        let r = v["response"].as_str().unwrap_or("");
        assert!(r.contains("\"peer\"") && r.contains("\"ns\""), "{backend}: the refusal names peer and ns: {v}");
        assert!(r.contains("claude-via-peer"), "{backend}: {v}");
    }
}

/// The models come from the peer (graphlink docs/claude-peer.md, "The models it serves"): a peer publishes `<ns>::Models:main` {names, backend}; with NO cloud_models entry the seat
/// lists those names on /v1/models while the link is up, routes a turn on one of them to that peer, and the request names the model (`membrane::LlmRequest:<rid>.model`).
#[test]
fn a_model_a_live_peer_publishes_is_listed_and_answered_by_that_peer_and_the_request_names_it() {
    let name = peer_name();
    let names = Names(name.clone());
    let facts = ["cloud_models=[]".to_string(), "router_on=false".to_string(), "seat_backend=http://127.0.0.1:1".to_string(),
                 r#"minds={"loops":"haiku","cortex":"haiku","workers":"haiku","seat_default":"haiku"}"#.to_string(), format!("peers={}", decl_peer(&name, "claude"))];
    let run = start(&tmp("cp-published"), &facts);
    let mut mine = peer_cells(&name, 64);
    for (k, v) in [("", json!(true)), ("names", json!(["claude", "sonnet", "opus", "haiku"])), ("backend", json!("claude-peer"))] { mine.put(&key("claude::Models:main", k), 1.0, &v).unwrap(); }
    let mut peer = PeerEnd::open(&name, "claude");
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    // the picker's list has the peer's names, owned by its backend
    let end = Instant::now() + Duration::from_secs(30);
    let listed = loop {
        let (_, body) = http(run.port, "GET", "/v1/models", "");
        let v: J = serde_json::from_str(&body).unwrap_or(J::Null);
        let ids: Vec<(String, String)> = v["data"].as_array().into_iter().flatten().map(|m| (m["id"].as_str().unwrap_or("").to_string(), m["owned_by"].as_str().unwrap_or("").to_string())).collect();
        if ids.iter().any(|(i, _)| i == "haiku") || Instant::now() > end { break ids; }
        std::thread::sleep(Duration::from_millis(200));
    };
    for n in ["claude", "sonnet", "opus", "haiku"] { assert!(listed.contains(&(n.to_string(), "membrane->claude-peer".to_string())), "{n} not listed: {listed:?}"); }
    let id = send(&run, "hello");
    let reply = reply_ask(&mut peer, None);
    let seen = seat_cells(&names.0);
    let rid = reply.replace("claude::Reply:", "membrane::LlmRequest:");
    let live = |c: &Option<Cell>| matches!(c, Some(c) if c.state == CellState::Live);
    assert_eq!(cell_when(&seen, &key(&rid, "model"), 30, live).expect("the request's model").value, json!("haiku"));
    assert_eq!(cell_when(&seen, &key(&rid, "server"), 30, live).expect("the request's server").value, json!(name));
    answer(&mut mine, &reply, "Hello from haiku", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Hello from haiku", "{v}");
}

// ---- the root of a peer: `Vm:<ns>` holds the replicas of everything the peer publishes, so a seat that loses it loses every reply ------------------------------------

/// the seat's facts for a claude-peer model answered by the peer `name`
fn peer_facts(name: &str) -> Vec<String> {
    let entry = json!([{"name": "claude-via-peer", "backend": "claude-peer", "peer": name, "ns": "claude"}]);
    vec![format!("cloud_models={entry}"), "router_on=false".to_string(), "seat_backend=http://127.0.0.1:1".to_string(),
         r#"minds={"loops":"claude-via-peer","cortex":"claude-via-peer","workers":"claude-via-peer","seat_default":"claude-via-peer"}"#.to_string(), format!("peers={}", decl_peer(name, "claude"))]
}
fn vm_link(port: u16) -> String { call(port, "run_cliff", json!({"code": "${'Vm:claude'}['link']"})) }
fn wait_until(what: &str, secs: u64, f: impl Fn() -> bool) {
    let end = Instant::now() + Duration::from_secs(secs);
    while !f() { assert!(Instant::now() < end, "{what}"); std::thread::sleep(Duration::from_millis(100)); }
}

/// a test pack that holds the sweep of the last run's roots (`Boot.old_vms`) back until `Gate:sweep.open`: a peer is a program of its own and may say hello before or after the sweep reads the store
/// (it did after, on the seat that lost its claude peer), so the test makes the late order the only one
const SWEEP_GATE: &str = "PACK sweepgate 1\nIMPORT boot 1\nIMPORT link 1\nGate.kind getter: 'gate'\nBoot.old_vms getter:\n  WHEN ${'Gate:sweep'}['open'] != True: None\n";

#[test]
fn a_restart_does_not_retire_the_root_of_a_peer_that_connected_before_the_sweep_of_the_last_runs_roots() {
    let name = peer_name();
    let _names = Names(name.clone());
    let facts = peer_facts(&name);
    let dir = tmp("cp-restart");
    let gate = dir.join("sweepgate.c1f");
    std::fs::write(&gate, SWEEP_GATE).unwrap();
    {   // the first run: the peer connects, its root `Vm:claude` is stored
        let run = start_with(&dir, &facts, &[gate.clone()]);
        let _mine = peer_cells(&name, 64);
        let _peer = PeerEnd::open(&name, "claude");
        wait_until("the root of the peer never came up", 30, || vm_link(run.port) == "up");
    }   // the seat is gone (killed, as every test's run is) and the next run finds the old root in the store
    for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{name}-{part}")); }
    let run = start_with(&dir, &facts, &[gate]);
    let _mine = peer_cells(&name, 64);
    let _peer = PeerEnd::open(&name, "claude");
    wait_until("the peer never connected in the second run", 30, || vm_link(run.port) == "up");
    // only now the sweep reads the store: it names the old root, which is the root of the peer that is connected
    put(run.port, "Gate:sweep", "open", json!(true));
    wait_until("the sweep never read the old roots", 60, || call(run.port, "run_cliff", json!({"code": "${'Boot:seat'}['old_vms']"})).contains("Vm:claude"));
    std::thread::sleep(Duration::from_millis(1500));   // and it has had its time to retire what it was given
    let times = format!("started {} root {}", call(run.port, "run_cliff", json!({"code": "${'Boot:seat'}['started']"})), call(run.port, "run_cliff", json!({"code": "${'Vm:claude'}['updated']"})));
    assert_eq!(vm_link(run.port), "up", "the sweep retired the root of a peer that is connected: its replies have no root to live under ({times})");
}

#[test]
fn a_restart_still_retires_the_root_of_a_peer_the_seat_no_longer_declares() {
    let name = peer_name();
    let _names = Names(name.clone());
    let dir = tmp("cp-undeclared");
    {   // the first run declares the peer and its root is stored
        let run = start(&dir, &peer_facts(&name));
        let _mine = peer_cells(&name, 64);
        let _peer = PeerEnd::open(&name, "claude");
        wait_until("the root of the peer never came up", 30, || vm_link(run.port) == "up");
    }
    for part in ["ctl", "sc", "pc"] { let _ = graphlink_shm::remove_region(&format!("/gl-{name}-{part}")); }
    // the second run declares no peer: the old root is the last run's and is swept
    let run = start(&dir, &[]);
    wait_until("the sweep never read the old roots", 60, || call(run.port, "run_cliff", json!({"code": "${'Boot:seat'}['old_vms']"})).contains("Vm:claude"));
    wait_until("the root of an undeclared peer was not swept", 30, || call(run.port, "run_cliff", json!({"code": "${'Vm:claude'}['link']"})).starts_with("null"));
}

#[test]
fn a_turn_whose_peer_root_is_gone_fails_and_does_not_wait_for_ever() {
    let (run, _names, mut peer, _mine) = claude_turn("cp-noroot");
    let id = send(&run, "hello");
    let _ = reply_ask(&mut peer, None);
    put(run.port, "Vm:claude", "retire", json!(true));   // no root: nothing holds the peer's replicas, and no reply can be read
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("claude is not up"), "the turn should say the peer is not up: {v}");
}

#[test]
fn a_message_for_a_peer_that_is_not_up_is_refused_at_once_and_says_so() {
    let name = peer_name();
    let _names = Names(name.clone());
    let run = start(&tmp("cp-notup"), &peer_facts(&name));   // declared, never started: no hello, no root
    let id = send(&run, "hello");
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap_or("").contains("claude is not up"), "the turn should say the peer is not up: {v}");
}

#[test]
fn a_tool_result_far_over_the_peer_tables_value_capacity_reaches_the_peer_whole_and_the_turn_is_not_refused() {
    // the failure seen on a seat: `the seat's table could not store it (the value is 77978 bytes encoded, the capacity of a value is 65536)`. A value over a cell is graphlink's to carry (docs/shared-memory.md 4c):
    // here a 200,000-byte result goes through a table of 4,096-byte cells
    let (run, names, mut peer, mut mine) = peer_turn_slots("cp-big", "claude-peer", "claude", json!({}), 256);
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    let blob: String = (0..200_000).map(|i| (b'a' + (i % 26) as u8) as char).collect();
    put(run.port, "Blob:big", "text", json!(blob.clone()));
    let id = send(&run, "read the blob");
    let r1 = reply_ask(&mut peer, None);
    answer(&mut mine, &r1, "", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Blob:big'}['text']"}}])));
    let r2 = reply_ask(&mut peer, Some(&r1));
    // the peer reads the seat's table: the tool message of round 2 is there whole
    let seen = seat_cells(&names.0);
    let end = Instant::now() + Duration::from_secs(60);
    let whole = loop {
        let rows = seen.scan("membrane::LlmMsg:", 1_000_000, 32).unwrap_or_default();
        assert!(rows.iter().all(|(_, c)| c.state != CellState::Refused), "a message was refused: {:?}", rows.iter().filter(|(_, c)| c.state == CellState::Refused).map(|(k, c)| (k.clone(), c.value.clone())).collect::<Vec<_>>());
        if let Some((_, c)) = rows.iter().find(|(k, c)| k.ends_with("content") && c.state == CellState::Live && c.value.as_str().map_or(false, |t| t.len() >= 200_000)) { break c.value.as_str().unwrap().to_string(); }
        assert!(Instant::now() < end, "the 200,000-byte tool message never reached the peer's view of the seat's table");
        std::thread::sleep(Duration::from_millis(50));
    };
    assert!(whole.contains(&blob), "the tool message holds the whole result");
    answer(&mut mine, &r2, "Read all of it", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Read all of it", "{v}");
}

// ---- a stateful peer (claude-peer) is not sent the history again: a round carries its own tool results, and an old round's request is retired --------------------


/// the next reply the seat asks the peer for: one not asked before (`reply_ask` only skips one earlier id, and a turn of many rounds has asked for several)
fn next_reply_ask(peer: &mut PeerEnd, ns: &str, seen: &[String]) -> String {
    let want = format!("{ns}::Reply:");
    let find = |ev: &[Event]| ev.iter().find_map(|e| if let Event::Op(Op::Sub { target: Target::Id(i), .. }) = e { (i.starts_with(&want) && !seen.contains(i)).then(|| i.clone()) } else { None });
    assert!(peer.until(60_000, |ev| find(ev).is_some()), "the seat never asked for another reply: {:?}", peer.events);
    find(&peer.events).unwrap()
}

fn messages_of(names: &Names, reply: &str) -> usize {
    let rid = reply.replace("::Reply:", "").replace("claude", "membrane::LlmRequest").replace("oai", "membrane::LlmRequest");
    let _ = rid;
    let req = reply.splitn(2, "::Reply:").nth(1).map(|r| format!("membrane::LlmRequest:{r}")).unwrap();
    let seen = seat_cells(&names.0);
    let c = cell_when(&seen, &key(&req, "messages"), 30, |c| matches!(c, Some(c) if c.state == CellState::Live)).expect("the request's messages");
    c.value.as_array().map_or(0, |a| a.len())
}

#[test]
fn a_claude_peer_turn_of_many_rounds_fits_a_small_table_because_a_round_carries_only_its_own_tool_results() {
    // the failure seen on a seat: a turn of 27 rounds kept every round's request and every message until its end, filled the 512 slots, and the next request waited for ever.
    // here 12 rounds go through a table of 128 slots, which the whole history of the turn would not fit
    let (run, names, mut peer, mut mine) = peer_turn_slots("cp-many", "claude-peer", "claude", json!({}), 128);
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    put(run.port, "Lamp:desk", "power", json!("zebra-42"));
    let id = send(&run, "read the lamp many times");
    let mut seen: Vec<String> = Vec::new();
    for round in 1..=12 {
        let r = next_reply_ask(&mut peer, "claude", &seen);
        assert!(r.ends_with(&format!("-{round}")), "round {round}: {r}");
        if round >= 2 { assert_eq!(messages_of(&names, &r), 1, "round {round} lists only its own tool result, not the history"); }
        answer(&mut mine, &r, "", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Lamp:desk'}['power']"}}])));
        seen.push(r);
    }
    let r13 = next_reply_ask(&mut peer, "claude", &seen);
    answer(&mut mine, &r13, "done after twelve reads", "stop", None);
    let v = turn_ends(run.port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "done after twelve reads", "{v}");
}

#[test]
fn a_stateless_peer_is_still_sent_the_whole_history_each_round() {
    // an OpenAI-compatible or Claude API peer keeps nothing between calls: round 3 lists the system and user messages and both earlier rounds' assistant and tool messages
    let (run, names, mut peer, mut mine) = peer_turn_slots("op-hist", "openai-peer", "oai", json!({}), 256);
    peer.say(vec![Op::Sub { target: Target::Prefix("membrane::Llm".into()), props: None, limit: None }]);
    put(run.port, "Lamp:desk", "power", json!("zebra-42"));
    let id = send(&run, "read the lamp twice");
    let r1 = reply_ask_in(&mut peer, "oai", None);
    answer(&mut mine, &r1, "", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Lamp:desk'}['power']"}}])));
    let r2 = next_reply_ask(&mut peer, "oai", &[r1.clone()]);
    answer(&mut mine, &r2, "", "tool_calls", Some(json!([{"name": "run_cliff", "arguments": {"code": "${'Lamp:desk'}['power']"}}])));
    let r3 = next_reply_ask(&mut peer, "oai", &[r1.clone(), r2.clone()]);
    assert_eq!(messages_of(&names, &r3), 6, "system, user, then the assistant and tool messages of two rounds");
    answer(&mut mine, &r3, "twice", "stop", None);
    assert_eq!(turn_ends(run.port, &id)["state"], "done");
}
