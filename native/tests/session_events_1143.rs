//! The session event stream never drops an event because two producers speak in one round (#1143, w72). The engine runs ONE cascade per round and refuses a
//! second write of a (entity, property) in it ("loop: ... already written this round"). A chunk of the CLI's output and the turn's end receipt can land in ONE
//! round (the host takes every receipt that is ready at the start of any round), so the producers of door_session.c1f must not share a write that the engine
//! refuses. This test makes that merge DETERMINISTIC instead of waiting for a loaded machine to do it: the seat runs IN this process (the same boot as
//! `cliffvm run`), and a gate between two pumps holds the engine until the fake CLI has finished, so the chunk(s) and the receipt are all waiting for the very
//! next round. There is NEVER a real `claude` here: the turn's binary is a fake script.
#![cfg(feature = "bin")]
mod common;
use membrane_vm::cli::{self, RunArgs};
use membrane_vm::host::{self, Host, Options};
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f", "door_session.c1f", "door_auth.c1f", "router.c1f", "door_models.c1f"];

/// a fake claude: the words and the answer in ONE write (one read of its output: one chunk), and it finishes at once
const QUICK: &str = r#"#!/usr/bin/env python3
import json, sys
def line(o): return json.dumps(o) + "\n"
prompt = sys.argv[-1]
sys.stdout.write(line({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}}) + line({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "q-1"}))
sys.stdout.flush()
"#;

/// the same, but the init line is a chunk of its own: two chunks, the second one brings the words (and the answer)
const SPLIT: &str = r#"#!/usr/bin/env python3
import json, sys, time
def line(o): return json.dumps(o) + "\n"
prompt = sys.argv[-1]
sys.stdout.write(line({"type": "system", "subtype": "init", "session_id": "q-1"})); sys.stdout.flush()
time.sleep(0.5)
sys.stdout.write(line({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}}) + line({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "q-1"}))
sys.stdout.flush()
"#;

struct Seat { port: u16, hold: Arc<AtomicUsize>, stop: Arc<AtomicBool>, thread: Option<std::thread::JoinHandle<()>>, dir: std::path::PathBuf }
impl Drop for Seat { fn drop(&mut self) { self.stop.store(true, Ordering::SeqCst); if let Some(t) = self.thread.take() { let _ = t.join(); } let _ = std::fs::remove_dir_all(&self.dir); } }

/// The seat of `cliffvm run` in this process. `hold` is the gate: while it is N > 0, the engine does NOT take another round once N turns' commands have
/// finished (their receipts are in) until all N receipts are there; then it takes them together with the chunks that came before them.
fn seat(tag: &str) -> Seat { seat_with(tag, QUICK) }
fn seat_with(tag: &str, script: &str) -> Seat {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let dir = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, script).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let from = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for name in PACKS { std::fs::copy(format!("{from}/{name}"), pd.join(name)).unwrap_or_else(|e| panic!("{name}: {e}")); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    std::env::set_var("ANTHROPIC_API_KEY", "sk-ant-harness-default-0000");
    let r = RunArgs { apps: vec![pd.join("boot.c1f").display().to_string(), pd.display().to_string()], store: dir.join("field.sqlite3").display().to_string(), port: Some(0), bind: None,
                      ui_dir: Some(dir.display().to_string()), facts: vec![("chat_claude_bin".into(), json!(bin.display().to_string())), ("seat_cwd".into(), json!("/srv/seat")), ("seat_project".into(), json!("membrane")), ("seat_version".into(), json!("1.0"))] };
    let hold = Arc::new(AtomicUsize::new(0));
    let stop = Arc::new(AtomicBool::new(false));
    let (tx, rx) = std::sync::mpsc::channel();
    let (hold2, stop2) = (hold.clone(), stop.clone());
    let thread = std::thread::spawn(move || {
        let mut o = Options::new(Some(r.store.clone()));
        o.plain = true;
        let h = Host::open(o).unwrap();
        for (i, app) in r.apps.iter().enumerate() { h.load_app(std::path::Path::new(app), i == 0).unwrap_or_else(|e| panic!("{app}: {e}")); }
        h.load_stored_packs();
        h.load_stored_entities();
        h.rebuild();
        host::boot_started(&h, &cli::facts(&r), true);
        let mut told = false;
        h.run(&stop2, 1.0, &mut |h| {
            if !told { let res = h.get("Http:door", "listen_result"); if !res.is_null() { told = true; let _ = tx.send(res["port"].as_u64().unwrap() as u16); } }
            let want = hold2.load(Ordering::SeqCst);
            if want == 0 { return; }
            // the turns' commands are running (an effect in flight on each): wait until all `want` receipts are in, take them out and put them back, so the next round has them
            let st = { let g = h.engine().lock().unwrap(); g.0.store.as_ref().unwrap().clone_handle() };
            let cmds: Vec<String> = { let g = h.engine().lock().unwrap(); g.0.docs.keys().filter(|k| k.starts_with("membrane::Command:chat:")).cloned().collect() };
            if cmds.iter().filter(|c| st.holds(c)).count() < want { return; }   // not every turn's command has started yet: the engine must take the next request
            let mut got = vec![];
            common::wait_for("the commands' receipts", common::WAIT, || {
                got.extend(st.take_receipts());
                (got.iter().filter(|(e, v, _)| e.starts_with("membrane::Command:chat:") && v == "run").count() >= want).then_some(())
            });
            for (e, v, rec) in got { st.note_receipt(&e, &v, rec); }
            hold2.store(0, Ordering::SeqCst);
        });
    });
    let port = rx.recv_timeout(common::DOOR_UP).expect("the door came up");
    Seat { port, hold, stop, thread: Some(thread), dir }
}

fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(common::IO)).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn api(port: u16, method: &str, path: &str, body: J) -> (u16, J) {
    let (st, t) = http(port, method, path, &if body.is_null() { String::new() } else { body.to_string() });
    (st, if t.trim().is_empty() { J::Null } else { serde_json::from_str(&t).unwrap_or_else(|e| panic!("{e}: {t}")) })
}
fn new_session(port: u16, title: &str) -> String { api(port, "POST", "/session", json!({"title": title})).1["id"].as_str().unwrap().to_string() }
fn say(port: u16, sid: &str, text: &str) -> u16 { api(port, "POST", &format!("/session/{sid}/prompt_async"), json!({"agent": "build", "parts": [{"type": "text", "text": text}]})).0 }

struct Sse { r: BufReader<TcpStream> }
impl Sse {
    fn open(port: u16, path: &str) -> Sse {
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
        s.write_all(format!("GET {path} HTTP/1.1\r\nHost: t\r\nAccept: text/event-stream\r\n\r\n").as_bytes()).unwrap();
        let mut r = BufReader::new(s);
        loop { let mut l = String::new(); r.read_line(&mut l).unwrap(); if l == "\r\n" || l.is_empty() { break; } }
        Sse { r }
    }
    fn next(&mut self) -> Option<(String, J)> {
        let (mut name, mut data) = (String::new(), String::new());
        loop {
            let mut l = String::new();
            match self.r.read_line(&mut l) { Ok(0) | Err(_) => return None, Ok(_) => {} }
            let l = l.trim_end_matches(['\r', '\n']);
            if l.is_empty() { if data.is_empty() { continue; } return Some((name, serde_json::from_str(&data).unwrap())); }
            if let Some(v) = l.strip_prefix("event: ") { name = v.to_string(); }
            if let Some(v) = l.strip_prefix("data: ") { data.push_str(v); }
        }
    }
    /// frames of `sid` that arrive within `secs` (a quiet stream: none)
    fn quiet_for(&mut self, secs: u64) -> Vec<(String, J)> {
        self.r.get_ref().set_read_timeout(Some(Duration::from_secs(secs))).unwrap();
        let mut out = vec![];
        while let Some(f) = self.next() { out.push(f); }
        out
    }
    /// every frame up to and including the first `session.idle` of each session in `sids`; panics (the stream ended or 30 s of silence) naming what was seen
    fn until_idle(&mut self, sids: &[String]) -> Vec<(String, J)> {
        let (mut out, mut idle) = (vec![], std::collections::HashSet::new());
        while idle.len() < sids.len() {
            let (n, d) = self.next().unwrap_or_else(|| panic!("no session.idle for {:?}; the stream gave {:?}", sids, out.iter().map(|x: &(String, J)| x.0.clone()).collect::<Vec<_>>()));
            if n == "session.idle" { idle.insert(d["properties"]["sessionID"].as_str().unwrap().to_string()); }
            out.push((n, d));
        }
        out
    }
}
fn sid_of(d: &J) -> String { let p = &d["properties"]; p["sessionID"].as_str().or(p["info"]["sessionID"].as_str()).or(p["part"]["sessionID"].as_str()).unwrap().to_string() }
fn names(f: &[(String, J)], sid: &str) -> Vec<String> { f.iter().filter(|x| sid_of(&x.1) == sid).map(|x| x.0.clone()).collect() }

#[test]
fn a_chunk_and_the_turns_end_that_land_in_one_round_both_reach_the_stream_in_order() {
    let s = seat("ev-merge");
    let a = new_session(s.port, "A");
    let mut sse = Sse::open(s.port, "/event");
    sse.next().expect("server.connected");
    s.hold.store(1, Ordering::SeqCst);
    assert_eq!(say(s.port, &a, "quick a"), 204);
    let mut f = sse.until_idle(&[a.clone()]);
    f.extend(sse.quiet_for(3));   // the rest of the end batch, and anything that would follow it
    let n = names(&f, &a);
    let at = |t: &str| n.iter().position(|x| x == t).unwrap_or_else(|| panic!("{t} missing in {n:?}"));
    assert!(n.contains(&"message.part.delta".to_string()), "the words of the chunk are on the stream: {n:?}");
    assert!(at("message.part.delta") < at("session.idle"), "the chunk's words come before the end: {n:?}");
    assert_eq!(n[n.len() - 3..].to_vec(), ["session.status", "session.idle", "session.updated"].map(String::from).to_vec(), "the end batch is the last thing on the stream: {n:?}");
    assert_eq!(n.iter().filter(|x| *x == "session.idle").count(), 1, "{n:?}");
}

#[test]
fn a_chunk_taken_after_its_turn_is_over_says_nothing_so_no_late_delta_follows_the_idle() {
    let s = seat_with("ev-stale", SPLIT);
    let a = new_session(s.port, "A");
    let mut sse = Sse::open(s.port, "/event");
    sse.next().expect("server.connected");
    s.hold.store(1, Ordering::SeqCst);
    assert_eq!(say(s.port, &a, "split a"), 204);
    let mut f = sse.until_idle(&[a.clone()]);
    f.extend(sse.quiet_for(3));
    let n = names(&f, &a);
    let idle = n.iter().position(|x| x == "session.idle").unwrap();
    assert_eq!(n[idle + 1..].to_vec(), ["session.updated".to_string()], "after the idle only the session's last update: {n:?}");
    let text = f.iter().filter(|x| x.0 == "message.part.updated").filter_map(|x| x.1["properties"]["part"]["text"].as_str().map(String::from)).last();
    assert_eq!(text.as_deref(), Some("Done: split a"), "the final part carries the answer");
}

#[test]
fn two_turns_that_end_in_one_round_each_reach_the_stream_and_no_event_crosses_sessions() {
    let s = seat("ev-two");
    let (a, b) = (new_session(s.port, "A"), new_session(s.port, "B"));
    let mut sse = Sse::open(s.port, "/event");
    sse.next().expect("server.connected");
    s.hold.store(2, Ordering::SeqCst);
    assert_eq!(say(s.port, &a, "quick a"), 204);
    assert_eq!(say(s.port, &b, "quick b"), 204);
    let mut f = sse.until_idle(&[a.clone(), b.clone()]);
    f.extend(sse.quiet_for(3));
    for (sid, text) in [(&a, "Done: quick a"), (&b, "Done: quick b")] {
        let n = names(&f, sid);
        assert_eq!(n[n.len() - 3..].to_vec(), ["session.status", "session.idle", "session.updated"].map(String::from).to_vec(), "{sid}: the end batch is the last of its session: {n:?}");
        assert_eq!(n.iter().filter(|x| *x == "session.idle").count(), 1, "{sid}: {n:?}");
        let last = f.iter().filter(|x| sid_of(&x.1) == *sid && x.0 == "message.part.updated").filter_map(|x| x.1["properties"]["part"]["text"].as_str().map(String::from)).last();
        assert_eq!(last.as_deref(), Some(text), "{sid}: the final part carries the answer");
    }
    let sids: std::collections::HashSet<String> = f.iter().map(|x| sid_of(&x.1)).collect();
    assert_eq!(sids, [a, b].into_iter().collect(), "only the two sessions' ids appear");
}
