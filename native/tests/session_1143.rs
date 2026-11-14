//! The opencode session API on cliffvm (#1143, swap step 2): `/session*`, `/event`, `/global/event` (membrane/packs/door_session.c1f), over the
//! barest chat's machinery (a session is a chat, a message a ChatMessage and its turn). Every wait has a timeout and the child is always reaped.
//! There is NEVER a real `claude` here: the turn's binary is a fake script (a Boot fact names it). The answers are compared in SHAPE with the
//! recorded answers of the released Python seat (tests/golden/session/*.json, scripts/golden_session.py): `shape_misses`.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn shipped(names: &[&str]) -> Vec<(String, String)> {
    let d = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    names.iter().map(|n| (n.to_string(), std::fs::read_to_string(format!("{d}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}")))).collect()
}

/// the barest chat's packs plus the session door
const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f", "door_session.c1f", "door_auth.c1f", "router.c1f", "door_models.c1f"];

/// the seat's folder holds the fake claude; the binary is told its path as a fact
fn seat(tag: &str, script: &str) -> (Run, u16, std::path::PathBuf) { seat_with(tag, script, false) }
/// `strict`: the seat has a door secret (and so an owner token file, `<dir>/owner.token`): every route but the public set needs the token
fn seat_with(tag: &str, script: &str, strict: bool) -> (Run, u16, std::path::PathBuf) { seat_facts(tag, script, strict, &[]) }
/// `facts`: more `--fact name=value` lines
fn seat_facts(tag: &str, script: &str, strict: bool, facts: &[&str]) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, script).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(PACKS) { std::fs::write(pd.join(n), t).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let fact = format!("chat_claude_bin={}", bin.display());
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap(), "--fact", &fact,
               "--fact", "seat_cwd=/srv/seat", "--fact", "seat_project=membrane", "--fact", "seat_version=1.0"])
        .args(facts.iter().flat_map(|f| ["--fact".to_string(), f.to_string()]))
        .args(if strict { vec!["--fact".to_string(), format!("door_secret_file={}", dir.join("door.secret").display()), "--fact".to_string(), format!("seat_data_dir={}", dir.display())] } else { vec![] })
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port, dir)
}

/// one HTTP request on its own connection; -> (status, body text). A bounded retry (3 tries) when the connection gives no answer at all: under a loaded
/// machine a test seat can stall; every route the tests send this way is safe to send again (a retry of a POST that did reach the seat is not made: it only
/// retries when NO byte came back, and the tests then check the rows they expect).
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    for _ in 0..2 { if let Some(r) = http_once(port, method, path, body) { return r; } }
    http_once(port, method, path, body).expect("an HTTP answer")
}
fn http_once(port: u16, method: &str, path: &str, body: &str) -> Option<(u16, String)> {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    Some((status, String::from_utf8_lossy(&out[i + 4..]).to_string()))
}
/// -> (status, the JSON body or null when it is empty)
fn api(port: u16, method: &str, path: &str, body: J) -> (u16, J) {
    let (st, t) = http(port, method, path, &if body.is_null() { String::new() } else { body.to_string() });
    (st, if t.trim().is_empty() { J::Null } else { serde_json::from_str(&t).unwrap_or_else(|e| panic!("{e}: {t}")) })
}

fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(90), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

// ---- the shape comparer -------------------------------------------------------------------------------------------------------------------------
/// What the answer lacks against a recorded fixture: every key of the fixture must be in the answer with the same JSON type (a `null` in the fixture
/// accepts any type: the recorded value was empty), recursively; extra keys are fine; each element of an answer's list must match SOME element of the
/// fixture's list (a parts list holds text, tool and step parts), an empty fixture list accepts anything. -> the misses, each a path and a reason.
fn shape_misses(fix: &J, got: &J) -> Vec<String> { let mut out = vec![]; shape_at("$", fix, got, &mut out); out }
fn kind(v: &J) -> &'static str { match v { J::Null => "null", J::Bool(_) => "bool", J::Number(_) => "number", J::String(_) => "string", J::Array(_) => "array", J::Object(_) => "object" } }
fn shape_at(p: &str, fix: &J, got: &J, out: &mut Vec<String>) {
    match (fix, got) {
        (J::Null, _) => {}
        (J::Object(f), J::Object(g)) => {
            for (k, fv) in f {
                // a map keyed by session ids: any key of the answer stands for it
                if k.starts_with("ses_") { if let Some(gv) = g.values().next() { shape_at(&format!("{p}.<session id>"), fv, gv, out); } continue; }
                match g.get(k) { Some(gv) => shape_at(&format!("{p}.{k}"), fv, gv, out), None => out.push(format!("{p}.{k}: missing")) }
            }
        }
        (J::Array(f), J::Array(g)) => {
            if f.is_empty() { return; }
            for (i, gv) in g.iter().enumerate() {
                let best = f.iter().map(|fv| { let mut m = vec![]; shape_at(&format!("{p}[{i}]"), fv, gv, &mut m); m }).min_by_key(|m| m.len()).unwrap();
                out.extend(best);
            }
        }
        (f, g) if kind(f) == kind(g) => {}
        (f, g) => out.push(format!("{p}: {} expected, {} found", kind(f), kind(g))),
    }
}
fn golden(name: &str) -> J { golden_in("session", name) }
fn golden_in(dir: &str, name: &str) -> J {
    let p = format!("{}/../tests/golden/{dir}/{name}.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{p}: {e}"))).unwrap()
}
/// the answer's body against the fixture's body, and the status against the fixture's
fn same_shape(name: &str, status: u16, body: &J) { same_shape_in("session", name, status, body) }
fn same_shape_in(dir: &str, name: &str, status: u16, body: &J) {
    let g = golden_in(dir, name);
    assert_eq!(status as i64, g["status"].as_i64().unwrap(), "{name}: the status; body {body}");
    let m = shape_misses(&g["body"], body);
    assert!(m.is_empty(), "{name}: {m:?}\nanswer: {body}");
}

/// a fake claude that finishes at once: an answer "Done: <prompt>" with no tool
const QUICK: &str = r#"#!/usr/bin/env python3
import json, sys
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
prompt = sys.argv[-1]
out({"type": "system", "subtype": "init", "session_id": "q-1"})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "q-1"})
"#;

#[test]
fn sessions_are_created_listed_read_renamed_and_deleted_in_the_shape_the_python_seat_answers() {
    let (_run, port, _dir) = seat("ses-crud", QUICK);
    let (st, s) = api(port, "POST", "/session", json!({"title": "T"}));
    same_shape("session_create", st, &s);
    let sid = s["id"].as_str().unwrap().to_string();
    assert!(sid.starts_with("ses_") && sid.len() == 28 && sid[4..].chars().all(|c| c.is_ascii_hexdigit()), "{sid}");
    assert_eq!(s["title"], "T");
    let (st, s2) = api(port, "POST", "/session", json!({}));
    assert_eq!(st, 200);
    assert!(s2["title"].as_str().unwrap().starts_with("New session"), "{s2}");
    let (st, l) = api(port, "GET", "/session", J::Null);
    same_shape("session_list", st, &l);
    let ids: Vec<&str> = l.as_array().unwrap().iter().map(|x| x["id"].as_str().unwrap()).collect();
    assert_eq!(ids, [sid.as_str(), s2["id"].as_str().unwrap()], "oldest first, like the Python seat");
    let (st, one) = api(port, "GET", &format!("/session/{sid}"), J::Null);
    same_shape("session_get", st, &one);
    assert_eq!(one["id"], json!(sid));
    let (st, miss) = api(port, "GET", "/session/ses_000000000000000000000000", J::Null);
    same_shape("session_missing", st, &miss);
    let (st, p) = api(port, "PATCH", &format!("/session/{sid}"), json!({"title": "Renamed"}));
    same_shape("session_patch", st, &p);
    assert_eq!(p["title"], "Renamed");
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).1["title"], "Renamed");
    let (st, status) = api(port, "GET", "/session/status", J::Null);
    same_shape("session_status_idle", st, &status);
    assert_eq!(status[&sid], json!({"type": "idle"}));
    assert_eq!(api(port, "POST", &format!("/session/{sid}/nonsense"), json!({})).0, 404);
    assert_eq!(api(port, "PUT", "/session", json!({})).0, 405);
    let (st, _) = api(port, "DELETE", &format!("/session/{sid}"), J::Null);
    assert_eq!(st, 200);
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).0, 404);
    let (_, l) = api(port, "GET", "/session", J::Null);
    assert_eq!(l.as_array().unwrap().len(), 1, "the deleted session is gone from the list");
}

// ---- messages --------------------------------------------------------------------------------------------------------------------------------
/// a fake claude that streams: text deltas, a tool call (start, the full block, its result), more text, the result; each step waits for a file
/// `go.<tag>.<step>` in the seat's folder (so a test sees the turn running between steps). The prompt `this will fail` ends the CLI with rc 3;
/// `hang` waits for ever (an abort kills it); `quick...` answers at once.
const STEPS: &str = r#"#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.argv[-1]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
json.dump({"argv": sys.argv[1:]}, open(os.path.join(here, "argv." + tag + ".json"), "w"))
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def gate(name):
    for _ in range(1200):
        if os.path.exists(os.path.join(here, "go." + tag + "." + name)): return
        time.sleep(0.05)
if prompt == "this will fail":
    sys.stderr.write("fake: not logged in\n"); sys.exit(3)
out({"type": "system", "subtype": "init", "session_id": "s-1"})
def delta(t): out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}})
if prompt == "hang":
    delta("starting")
    gate("never")
elif not prompt.startswith("quick"):
    gate("0"); delta("Looking ")
    gate("1"); delta("now.")
    gate("2")
    out({"type": "stream_event", "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_read", "input": {}}}})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking now."}, {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_read", "input": {"spec": "Command:probe"}}]}})
    gate("3")
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": "the result"}]}]}})
    gate("4"); delta("Done.")
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}})
    gate("end")
    out({"type": "result", "subtype": "success", "is_error": False, "result": "Done.", "num_turns": 2, "duration_ms": 99, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "s-1"})
    sys.exit(0)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "s-1"})
"#;

fn tag_of(prompt: &str) -> String { prompt.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).take(40).collect() }
fn release(dir: &std::path::Path, prompt: &str, step: &str) { std::fs::write(dir.join(format!("go.{}.{}", tag_of(prompt), step)), "").unwrap(); }
/// release every step of a streaming prompt, in order
fn release_all(dir: &std::path::Path, prompt: &str) { for s in ["0", "1", "2", "3", "4", "end"] { release(dir, prompt, s); } }
fn new_session(port: u16, title: &str) -> String { api(port, "POST", "/session", json!({"title": title})).1["id"].as_str().unwrap().to_string() }
fn say(port: u16, sid: &str, text: &str) -> u16 { api(port, "POST", &format!("/session/{sid}/prompt_async"), json!({"agent": "build", "parts": [{"type": "text", "text": text}]})).0 }
fn rows(port: u16, sid: &str) -> Vec<J> { let (st, b) = api(port, "GET", &format!("/session/{sid}/message"), J::Null); assert_eq!(st, 200, "{b}"); b.as_array().unwrap().clone() }
/// wait until the session has an assistant row that is completed
fn finished(port: u16, sid: &str, n: usize) -> Vec<J> {
    wait("the answer", || { let r = rows(port, sid); (r.len() >= n && r.iter().filter(|x| x["info"]["role"] == "assistant").all(|x| x["info"]["time"]["completed"].is_number())).then_some(r) })
}

#[test]
fn a_message_becomes_a_user_row_and_an_assistant_row_with_text_and_tool_parts_in_the_python_seats_shape() {
    let (_run, port, dir) = seat("ses-msg", STEPS);
    let sid = new_session(port, "T");
    assert_eq!(rows(port, &sid).len(), 0, "a new session has no rows");
    assert_eq!(say(port, &sid, "look at the probe"), 204);
    // running: the status says busy and the assistant row is there, not completed
    let busy = wait("busy", || { let (_, s) = api(port, "GET", "/session/status", J::Null); (s[&sid]["type"] == "busy").then_some(s) });
    assert_eq!(busy[&sid]["type"], "busy");
    let r = wait("the assistant row", || { let r = rows(port, &sid); (r.len() == 2).then_some(r) });
    assert_eq!(r[0]["info"]["role"], "user");
    assert_eq!(r[0]["parts"][0]["text"], "look at the probe");
    assert_eq!(r[1]["info"]["role"], "assistant");
    assert!(r[1]["info"]["time"]["completed"].is_null(), "{}", r[1]["info"]);
    release_all(&dir, "look at the probe");
    let r = finished(port, &sid, 2);
    let (user, asst) = (&r[0], &r[1]);
    let (st, golden_body) = (200u16, json!(r));
    same_shape("messages_ok", st, &golden_body);
    assert_eq!(asst["info"]["parentID"], user["info"]["id"]);
    assert_eq!(asst["info"]["sessionID"], json!(sid));
    for id in [user["info"]["id"].as_str().unwrap(), asst["info"]["id"].as_str().unwrap()] { assert!(id.starts_with("msg_") && id.len() == 28 && id[4..].chars().all(|c| c.is_ascii_digit()), "{id}"); }
    let parts = asst["parts"].as_array().unwrap();
    let tool = parts.iter().find(|p| p["type"] == "tool").expect("a tool part");
    assert_eq!((tool["callID"].as_str(), tool["tool"].as_str(), tool["state"]["status"].as_str()), (Some("tu1"), Some("entity_read"), Some("completed")), "{tool}");
    assert_eq!(tool["state"]["input"], json!({"spec": "Command:probe"}));
    assert_eq!(tool["state"]["output"], "the result");
    let text = parts.iter().find(|p| p["type"] == "text").expect("a text part");
    assert_eq!(text["text"], "Done.");
    assert_eq!(asst["info"]["finish"], "stop");
    assert_eq!(asst["info"]["tokens"]["input"], 1);
    assert!(asst["info"]["error"].is_null());
    for p in parts { assert_eq!(p["messageID"], asst["info"]["id"]); assert_eq!(p["sessionID"], json!(sid)); let id = p["id"].as_str().unwrap(); assert!(id.starts_with("prt_") && id.len() == 28, "{id}"); }
    // one row by its id, and the session is idle again
    let (st, one) = api(port, "GET", &format!("/session/{sid}/message/{}", asst["info"]["id"].as_str().unwrap()), J::Null);
    assert_eq!(st, 200, "{one}");
    same_shape("message_one", st, &one);
    assert_eq!(one["info"]["id"], asst["info"]["id"]);
    let (st, one) = api(port, "GET", &format!("/session/{sid}/message/{}", user["info"]["id"].as_str().unwrap()), J::Null);
    assert_eq!((st, one["info"]["role"].as_str()), (200, Some("user")));
    assert_eq!(api(port, "GET", &format!("/session/{sid}/message/msg_000000000000000000000000"), J::Null).0, 404);
    assert_eq!(api(port, "GET", "/session/status", J::Null).1[&sid], json!({"type": "idle"}));
    // the session moved: its `updated` is at or after its `created`
    let (_, s) = api(port, "GET", &format!("/session/{sid}"), J::Null);
    assert!(s["time"]["updated"].as_i64().unwrap() >= s["time"]["created"].as_i64().unwrap());
}

#[test]
fn the_synchronous_message_route_answers_when_the_turn_ends_with_info_and_parts() {
    let (_run, port, _dir) = seat("ses-sync", STEPS);
    let sid = new_session(port, "T");
    let (st, b) = api(port, "POST", &format!("/session/{sid}/message"), json!({"parts": [{"type": "text", "text": "quick one"}]}));
    assert_eq!(st, 200, "{b}");
    assert_eq!(b["info"]["role"], "assistant");
    assert!(b["info"]["time"]["completed"].is_number(), "{b}");
    assert_eq!(b["parts"].as_array().unwrap().iter().find(|p| p["type"] == "text").unwrap()["text"], "Done: quick one");
    assert_eq!(rows(port, &sid).len(), 2);
    // bad requests: no text part, no such session, a wrong method
    assert_eq!(api(port, "POST", &format!("/session/{sid}/prompt_async"), json!({"parts": [{"type": "text", "text": "  "}]})).0, 400);
    assert_eq!(api(port, "POST", &format!("/session/{sid}/prompt_async"), json!({})).0, 400);
    assert_eq!(api(port, "POST", "/session/ses_000000000000000000000000/prompt_async", json!({"parts": [{"type": "text", "text": "x"}]})).0, 404);
    assert_eq!(api(port, "GET", "/session/ses_000000000000000000000000/message", J::Null).0, 404);
    assert_eq!(api(port, "PUT", &format!("/session/{sid}/prompt_async"), json!({})).0, 405);
    assert_eq!(rows(port, &sid).len(), 2, "the refused ones left no rows");
}

#[test]
fn a_failed_turn_is_an_error_on_the_assistant_row_with_the_cli_s_words() {
    let (_run, port, _dir) = seat("ses-err", STEPS);
    let sid = new_session(port, "E");
    assert_eq!(say(port, &sid, "this will fail"), 204);
    let r = finished(port, &sid, 2);
    same_shape("messages_error", 200, &json!(r));
    let info = &r[1]["info"];
    assert_eq!(info["error"]["name"], "Error");
    assert!(info["error"]["data"]["message"].as_str().unwrap().contains("claude failed (rc 3): fake: not logged in"), "{info}");
    assert!(info["finish"].is_null(), "an error turn has no finish");
    let text = r[1]["parts"].as_array().unwrap().iter().find(|p| p["type"] == "text").expect("the error as a text part");
    assert!(text["text"].as_str().unwrap().contains("not logged in"));
}

#[test]
fn two_sessions_do_not_mix_their_rows_status_or_deletion() {
    let (_run, port, dir) = seat("ses-two", STEPS);
    let (a, b) = (new_session(port, "A"), new_session(port, "B"));
    assert_eq!(say(port, &a, "quick for a"), 204);
    finished(port, &a, 2);
    assert_eq!(say(port, &b, "look at b"), 204);
    wait("b busy", || { let (_, s) = api(port, "GET", "/session/status", J::Null); (s[&b]["type"] == "busy").then_some(()) });
    assert_eq!(api(port, "GET", "/session/status", J::Null).1[&a], json!({"type": "idle"}), "a is idle while b runs");
    release_all(&dir, "look at b");
    finished(port, &b, 2);
    let (ra, rb) = (rows(port, &a), rows(port, &b));
    assert_eq!((ra.len(), rb.len()), (2, 2));
    assert_eq!(ra[0]["parts"][0]["text"], "quick for a");
    assert_eq!(rb[0]["parts"][0]["text"], "look at b");
    assert!(ra.iter().chain(rb.iter()).all(|r| r["info"]["sessionID"] == json!(a) || r["info"]["sessionID"] == json!(b)));
    assert!(ra.iter().all(|r| r["info"]["sessionID"] == json!(a)) && rb.iter().all(|r| r["info"]["sessionID"] == json!(b)));
    // a message id of one session is not found under the other
    let mid = ra[0]["info"]["id"].as_str().unwrap();
    assert_eq!(api(port, "GET", &format!("/session/{b}/message/{mid}"), J::Null).0, 404);
    // deleting a leaves b's rows
    assert_eq!(api(port, "DELETE", &format!("/session/{a}"), J::Null).0, 200);
    assert_eq!(rows(port, &b).len(), 2);
    assert_eq!(api(port, "GET", &format!("/session/{a}/message"), J::Null).0, 404);
}

#[test]
fn the_messages_page_by_rows_limit_and_before() {
    let (_run, port, _dir) = seat("ses-page", STEPS);
    let sid = new_session(port, "P");
    for t in ["quick 1", "quick 2", "quick 3"] { assert_eq!(say(port, &sid, t), 204); finished(port, &sid, 0); std::thread::sleep(Duration::from_millis(30)); wait("rows", || (rows(port, &sid).len() >= 2 * (["quick 1", "quick 2", "quick 3"].iter().position(|x| *x == t).unwrap() + 1)).then_some(())); finished(port, &sid, 0); }
    let all = finished(port, &sid, 6);
    assert_eq!(all.len(), 6);
    let (st, last2) = api(port, "GET", &format!("/session/{sid}/message?limit=2"), J::Null);
    assert_eq!(st, 200);
    assert_eq!(last2.as_array().unwrap().iter().map(|r| r["info"]["id"].clone()).collect::<Vec<_>>(), all[4..].iter().map(|r| r["info"]["id"].clone()).collect::<Vec<_>>());
    let before = all[4]["info"]["id"].as_str().unwrap();
    let (_, older) = api(port, "GET", &format!("/session/{sid}/message?limit=2&before={before}"), J::Null);
    assert_eq!(older.as_array().unwrap().iter().map(|r| r["info"]["id"].clone()).collect::<Vec<_>>(), all[2..4].iter().map(|r| r["info"]["id"].clone()).collect::<Vec<_>>());
}

// ---- the event streams -----------------------------------------------------------------------------------------------------------------------
/// An open event stream: the head, then the frames as they arrive: (event name, json data); None at the end of the stream or after `secs` of quiet.
struct Sse { r: BufReader<TcpStream>, head: String }
impl Sse {
    fn open(port: u16, path: &str) -> Sse { Sse::open_with(port, path, "") }
    /// `extra`: more header lines, each ending in \r\n (a Bearer token)
    fn open_with(port: u16, path: &str, extra: &str) -> Sse {
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
        s.write_all(format!("GET {path} HTTP/1.1\r\nHost: t\r\nAccept: text/event-stream\r\n{extra}\r\n").as_bytes()).unwrap();
        let mut r = BufReader::new(s);
        let mut head = String::new();
        loop { let mut l = String::new(); r.read_line(&mut l).unwrap(); head.push_str(&l); if l == "\r\n" || l.is_empty() { break; } }
        Sse { r, head }
    }
    fn next(&mut self) -> Option<(String, J)> {
        let (mut name, mut data) = (String::new(), String::new());
        loop {
            let mut l = String::new();
            match self.r.read_line(&mut l) { Ok(0) | Err(_) => return None, Ok(_) => {} }
            let l = l.trim_end_matches(['\r', '\n']);
            if l.is_empty() { if data.is_empty() { continue; } return Some((name, serde_json::from_str(&data).unwrap_or_else(|e| panic!("{e}: {data}")))); }
            if let Some(v) = l.strip_prefix("event: ") { name = v.to_string(); }
            if let Some(v) = l.strip_prefix("data: ") { data.push_str(v); }
        }
    }
    /// the frames up to and including the first that `stop` accepts
    fn until(&mut self, what: &str, stop: impl Fn(&str, &J) -> bool) -> Vec<(String, J)> {
        let mut out = vec![];
        loop {
            let (n, d) = self.next().unwrap_or_else(|| panic!("the stream ended before {what}; got {:?}", out.iter().map(|x: &(String, J)| x.0.clone()).collect::<Vec<_>>()));
            let done = stop(&n, &d);
            out.push((n, d));
            if done { return out; }
        }
    }
}
/// the properties of a bare frame
fn props(d: &J) -> &J { &d["properties"] }
fn types(v: &[(String, J)]) -> Vec<String> { v.iter().map(|x| x.0.clone()).collect() }

#[test]
fn the_event_stream_carries_a_turn_in_order_as_the_python_seat_does_and_the_page_can_rebuild_the_row_from_it() {
    let (_run, port, dir) = seat("ses-ev", STEPS);
    let sid = new_session(port, "T");
    let mut sse = Sse::open(port, "/event");
    let lh = sse.head.to_ascii_lowercase();
    assert!(sse.head.starts_with("HTTP/1.1 200") && lh.contains("content-type: text/event-stream") && !lh.contains("content-length"), "{}", sse.head);
    let (n, hello) = sse.next().expect("server.connected");
    assert_eq!((n.as_str(), hello["type"].as_str(), hello["properties"].clone()), ("server.connected", Some("server.connected"), json!({})));
    assert!(hello["id"].as_str().unwrap().starts_with("evt_"));
    let fx = golden("events_bare");
    let check = |name: &str, d: &J| { let m = shape_misses(&fx.as_array().unwrap().iter().map(|f| f["data"].clone()).collect::<Vec<_>>().into(), &json!([d])); assert!(m.is_empty(), "{name}: {m:?} in {d}"); };
    check("hello", &hello);
    assert_eq!(say(port, &sid, "look at the probe"), 204);
    // the start: the user's message and part, the session, busy; then the assistant's message and the session once the turn runs
    let start = sse.until("the assistant's session.updated", |n, _| n == "session.updated");
    let start2 = sse.until("busy", |n, d| n == "session.status" && d["properties"]["status"]["type"] == "busy");
    let start3 = sse.until("the assistant message", |n, d| n == "message.updated" && d["properties"]["info"]["role"] == "assistant");
    let all: Vec<(String, J)> = start.into_iter().chain(start2).chain(start3).collect();
    assert_eq!(types(&all), ["message.updated", "message.part.updated", "session.updated", "session.status", "message.updated"], "{:?}", types(&all));
    assert_eq!(props(&all[0].1)["info"]["role"], "user");
    assert_eq!(props(&all[1].1)["part"]["text"], "look at the probe");
    assert_eq!(props(&all[0].1)["sessionID"], json!(sid));
    for (n, d) in &all { assert_eq!(&d["type"], n); check(n, d); }
    let uid = props(&all[0].1)["info"]["id"].clone();
    assert_eq!(props(&all[4].1)["info"]["parentID"], uid);
    assert!(props(&all[4].1)["info"]["time"]["completed"].is_null());
    // the words come as the CLI writes them: the part opens EMPTY, then one delta per piece
    release(&dir, "look at the probe", "0");
    let f = sse.until("the first delta", |n, _| n == "message.part.delta");
    assert!(types(&f).contains(&"message.part.updated".to_string()), "{:?}", types(&f));
    let open = f.iter().find(|x| x.0 == "message.part.updated").expect("the text part opens");
    assert_eq!((props(&open.1)["part"]["type"].as_str(), props(&open.1)["part"]["text"].as_str()), (Some("text"), Some("")));
    let text_pid = props(&open.1)["part"]["id"].clone();
    let d0 = f.last().unwrap();
    assert_eq!((props(&d0.1)["delta"].as_str(), props(&d0.1)["partID"].clone(), props(&d0.1)["field"].as_str()), (Some("Looking "), text_pid.clone(), Some("text")));
    check("delta", &d0.1);
    release(&dir, "look at the probe", "1");
    let (_, d1) = sse.until("the second delta", |n, _| n == "message.part.delta").pop().unwrap();
    assert_eq!(props(&d1)["delta"], "now.");
    // the tool starts: its part is running, with the tool's name and call id
    release(&dir, "look at the probe", "2");
    let f = sse.until("the tool running", |n, d| n == "message.part.updated" && d["properties"]["part"]["type"] == "tool");
    let running = &props(&f.last().unwrap().1)["part"];
    assert_eq!((running["tool"].as_str(), running["state"]["status"].as_str(), running["callID"].as_str()), (Some("entity_read"), Some("running"), Some("tu1")), "{running}");
    check("tool running", &f.last().unwrap().1);
    // and completed when its result arrives, the same part
    release(&dir, "look at the probe", "3");
    let f = sse.until("the tool completed", |n, d| n == "message.part.updated" && d["properties"]["part"]["state"]["status"] == "completed");
    let done_tool = &props(&f.last().unwrap().1)["part"];
    assert_eq!((done_tool["id"].clone(), done_tool["state"]["output"].as_str()), (running["id"].clone(), Some("the result")));
    check("tool completed", &f.last().unwrap().1);
    release(&dir, "look at the probe", "4");
    release(&dir, "look at the probe", "end");
    // the end: the final parts, the message completed, idle (status and the idle event), the session
    let f = sse.until("the session.updated after idle", |n, _| n == "session.updated");
    assert!(types(&f).ends_with(&["message.updated", "session.status", "session.idle", "session.updated"].map(String::from)), "{:?}", types(&f));
    let fin_text = f.iter().filter(|x| x.0 == "message.part.updated").map(|x| props(&x.1)["part"].clone()).find(|p| p["type"] == "text").expect("the text part final");
    assert_eq!((fin_text["id"].clone(), fin_text["text"].as_str()), (text_pid, Some("Done.")));
    assert!(fin_text["time"]["end"].is_number());
    let fin_msg = f.iter().rev().find(|x| x.0 == "message.updated").unwrap();
    assert!(props(&fin_msg.1)["info"]["time"]["completed"].is_number());
    assert_eq!(props(&fin_msg.1)["info"]["finish"], "stop");
    assert_eq!(f[f.len() - 3].1["properties"]["status"]["type"], "idle");
    for (n, d) in &f { check(n, d); }
}

#[test]
fn the_global_stream_wraps_each_event_and_an_error_turn_ends_with_the_error_and_idle() {
    let (_run, port, _dir) = seat("ses-glob", STEPS);
    let sid = new_session(port, "G");
    let mut g = Sse::open(port, "/global/event");
    let mut b = Sse::open(port, "/event");
    let (_, hello) = g.next().expect("server.connected, wrapped");
    assert_eq!(hello["payload"]["type"], "server.connected");
    assert!(hello["directory"].is_string() && hello["project"] == "global", "{hello}");
    b.next().unwrap();
    assert_eq!(say(port, &sid, "this will fail"), 204);
    let f = g.until("idle", |n, _| n == "session.idle");
    let fx = golden("events_global");
    for (n, d) in &f {
        let m = shape_misses(&fx.as_array().unwrap().iter().map(|x| x["data"].clone()).collect::<Vec<_>>().into(), &json!([d]));
        assert!(m.is_empty(), "{n}: {m:?} in {d}");
        assert_eq!(&d["payload"]["type"], n);
    }
    let last_msg = f.iter().rev().find(|x| x.0 == "message.updated").unwrap();
    let info = &last_msg.1["payload"]["properties"]["info"];
    assert_eq!(info["role"], "assistant");
    assert!(info["error"]["data"]["message"].as_str().unwrap().contains("not logged in"), "{info}");
    // the bare stream saw the same events, unwrapped
    let fb = b.until("idle", |n, _| n == "session.idle");
    assert_eq!(types(&fb), types(&f));
}

#[test]
fn a_stream_sees_every_session_and_events_never_cross_session_ids() {
    let (_run, port, dir) = seat("ses-evtwo", STEPS);
    let (a, b) = (new_session(port, "A"), new_session(port, "B"));
    let mut sse = Sse::open(port, "/event");
    sse.next().unwrap();
    assert_eq!(say(port, &a, "quick a"), 204);
    let mut fa = sse.until("a idle", |n, _| n == "session.idle");
    fa.extend(sse.until("a's last session.updated", |n, _| n == "session.updated"));
    assert_eq!(say(port, &b, "quick b"), 204);
    let mut fb = sse.until("b idle", |n, _| n == "session.idle");
    fb.extend(sse.until("b's last session.updated", |n, _| n == "session.updated"));
    let sid_of = |d: &J| props(d)["sessionID"].as_str().map(String::from).or_else(|| props(d)["info"]["sessionID"].as_str().map(String::from)).or_else(|| props(d)["part"]["sessionID"].as_str().map(String::from)).unwrap();
    assert!(fa.iter().all(|x| sid_of(&x.1) == a), "{:?}", fa.iter().map(|x| sid_of(&x.1)).collect::<Vec<_>>());
    assert!(fb.iter().all(|x| sid_of(&x.1) == b));
    let _ = dir;
}

// ---- abort -------------------------------------------------------------------------------------------------------------------------------------
#[test]
fn abort_stops_the_running_turn_which_ends_as_an_aborted_error_and_idle_on_the_stream() {
    let (_run, port, _dir) = seat("ses-abort", STEPS);
    let (a, b) = (new_session(port, "A"), new_session(port, "B"));
    assert_eq!(api(port, "POST", &format!("/session/{a}/abort"), json!({})), (200, json!(true)), "an idle session: nothing to stop");
    let mut sse = Sse::open(port, "/event");
    sse.next().unwrap();
    assert_eq!(say(port, &a, "hang"), 204);
    assert_eq!(say(port, &b, "hang"), 204);
    sse.until("a delta of a", |n, d| n == "message.part.delta" && d["properties"]["sessionID"] == json!(a));
    // the store row of a turn that has just started may lag (under load), and so would the status: the abort is sent again, three times; one that finds no running turn is harmless
    for _ in 0..3 {
        assert_eq!(api(port, "POST", &format!("/session/{a}/abort"), json!({})), (200, json!(true)));
        std::thread::sleep(Duration::from_millis(400));
    }
    // a ends: error, aborted, idle; b is still running
    let f = sse.until("a idle", |n, d| n == "session.idle" && d["properties"]["sessionID"] == json!(a));
    let fin = f.iter().rev().find(|x| x.0 == "message.updated" && x.1["properties"]["sessionID"] == json!(a)).unwrap();
    assert_eq!(props(&fin.1)["info"]["error"]["name"], "MessageAbortedError");
    assert!(props(&fin.1)["info"]["time"]["completed"].is_number());
    let r = finished(port, &a, 2);
    assert_eq!(r[1]["info"]["error"]["name"], "MessageAbortedError");
    assert_eq!(api(port, "GET", "/session/status", J::Null).1[&a], json!({"type": "idle"}));
    assert_eq!(api(port, "GET", "/session/status", J::Null).1[&b], json!({"type": "busy"}), "the other session runs on");
    // the session can speak again
    assert_eq!(say(port, &a, "quick again"), 204);
    wait("the next answer", || { let r = rows(port, &a); (r.len() == 4 && r[3]["info"]["time"]["completed"].is_number()).then_some(()) });
    assert_eq!(api(port, "POST", &format!("/session/{b}/abort"), json!({})).0, 200);
    assert_eq!(api(port, "POST", "/session/ses_000000000000000000000000/abort", json!({})).0, 404);
}

// ---- a seat WITH a secret (the strict door) ------------------------------------------------------------------------------------------------
fn http_tok(port: u16, method: &str, path: &str, token: Option<&str>, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let auth = token.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{auth}\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    (String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap(), String::from_utf8_lossy(&out[i + 4..]).to_string())
}

#[test]
fn on_a_seat_with_a_secret_the_session_routes_and_streams_need_the_owner_token_and_work_with_it() {
    let (_run, port, dir) = seat_with("ses-strict", QUICK, true);
    let token = wait("the owner token file", || std::fs::read_to_string(dir.join("owner.token")).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty()));
    // without the token: the released 401 on every session route, the streams included, prefixed or not
    for (m, p) in [("GET", "/session"), ("POST", "/session"), ("GET", "/session/status"), ("GET", "/ui/serve/session"), ("GET", "/ui/serve/event")] {
        let (st, body) = http_tok(port, m, p, None, "{}");
        assert_eq!(st, 401, "{m} {p}: {body}");
        assert!(body.contains("missing or invalid token"), "{body}");
    }
    let refused = Sse::open(port, "/event");
    assert!(refused.head.starts_with("HTTP/1.1 401"), "{}", refused.head);
    let refused = Sse::open(port, "/global/event");
    assert!(refused.head.starts_with("HTTP/1.1 401"), "{}", refused.head);
    // with it: a session, a stream, a message, the events
    let (st, b) = http_tok(port, "POST", "/session", Some(&token), "{\"title\":\"S\"}");
    assert_eq!(st, 200, "{b}");
    let sid = serde_json::from_str::<J>(&b).unwrap()["id"].as_str().unwrap().to_string();
    let hdr = format!("Authorization: Bearer {token}\r\n");
    let mut sse = Sse::open_with(port, "/event", &hdr);
    assert!(sse.head.starts_with("HTTP/1.1 200"), "{}", sse.head);
    assert_eq!(sse.next().unwrap().0, "server.connected");
    let (st, _) = http_tok(port, "POST", &format!("/ui/serve/session/{sid}/prompt_async"), Some(&token), "{\"parts\":[{\"type\":\"text\",\"text\":\"quick s\"}]}");
    assert_eq!(st, 204);
    let f = sse.until("idle", |n, _| n == "session.idle");
    assert_eq!(types(&f).first().map(String::as_str), Some("message.updated"));
    wait("the rows", || { let (st, b) = http_tok(port, "GET", &format!("/session/{sid}/message"), Some(&token), ""); (st == 200 && serde_json::from_str::<J>(&b).map(|v| v.as_array().map(|a| a.len()) == Some(2)).unwrap_or(false)).then_some(()) });
}


// ---- the model: the list the picker reads, the pick a session holds, the pick the turn runs on (#1143, step 2) ---------------------------------
/// the launcher's facts of a seat that answers through the claude CLI only: two claude rungs (the CLI's own default, and `sonnet` the way the Python seat
/// names a rung: `claude-agent:<model>`), no router, a backend nobody listens on (a refused connection is an empty tag list), `claude` the seat default
const CLAUDE_FACTS: [&str; 4] = [
    r#"cloud_models=[{"name":"claude","backend":"claude-agent"},{"name":"claude-agent:sonnet","backend":"claude-agent"}]"#,
    "router_on=false", "seat_backend=http://127.0.0.1:1",
    r#"minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude"}"#];
fn argv_of(dir: &std::path::Path, prompt: &str) -> Option<Vec<String>> {
    let t = std::fs::read_to_string(dir.join(format!("argv.{}.json", tag_of(prompt)))).ok()?;
    Some(serde_json::from_str::<J>(&t).ok()?["argv"].as_array()?.iter().map(|x| x.as_str().unwrap().to_string()).collect())
}

#[test]
fn the_model_list_names_the_claude_models_the_seat_can_run_even_with_no_router_in_the_shape_the_python_seat_lists() {
    let (_run, port, _dir) = seat_facts("ses-models", QUICK, false, &CLAUDE_FACTS);
    let (st, l) = api(port, "GET", "/v1/models", J::Null);
    same_shape_in("boot", "v1_models", st, &l);
    let rows = l["data"].as_array().unwrap();
    for id in ["claude", "claude-agent:sonnet"] {
        let r = rows.iter().find(|r| r["id"] == id).unwrap_or_else(|| panic!("{id} is listed: {l}"));
        assert_eq!(r["owned_by"], "membrane->claude-agent", "the page's picker reads this owner: {r}");
    }
}

#[test]
fn a_session_keeps_the_model_its_patch_names_and_shows_it_in_the_released_shape() {
    let (_run, port, _dir) = seat_facts("ses-patch-model", QUICK, false, &CLAUDE_FACTS);
    let sid = new_session(port, "M");
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).1["model"]["id"], "claude", "the seat default until a pick");
    let (st, p) = api(port, "PATCH", &format!("/session/{sid}"), json!({"model": {"providerID": "membrane", "modelID": "claude-agent:sonnet"}}));
    same_shape_in("session", "session_patch_model", st, &p);
    assert_eq!(p["model"], json!({"id": "claude-agent:sonnet", "providerID": "membrane", "variant": "default"}), "{p}");
    assert_eq!(p["title"], "M", "a patch with no title leaves it");
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).1["model"]["id"], "claude-agent:sonnet");
    assert_eq!(api(port, "GET", "/session", J::Null).1[0]["model"]["id"], "claude-agent:sonnet");
    // an empty model name changes nothing (the Python seat's check: modelID or id must be there)
    let (_, p) = api(port, "PATCH", &format!("/session/{sid}"), json!({"model": {"providerID": "membrane", "modelID": ""}}));
    assert_eq!(p["model"]["id"], "claude-agent:sonnet");
}

#[test]
fn a_message_that_names_a_model_makes_it_the_sessions_and_the_turn_runs_the_cli_on_it() {
    let (_run, port, dir) = seat_facts("ses-pick", STEPS, false, &CLAUDE_FACTS);
    let sid = new_session(port, "P");
    // no pick: no --model (the CLI's own default), the rows name the seat's default
    assert_eq!(say(port, &sid, "quick plain"), 204);
    let r = finished(port, &sid, 2);
    let a = argv_of(&dir, "quick plain").expect("the CLI ran");
    assert!(!a.iter().any(|x| x == "--model"), "{a:?}");
    assert_eq!(r[0]["info"]["model"]["modelID"], "claude");
    // a pick in the body, the way the page sends it
    let (st, _) = api(port, "POST", &format!("/session/{sid}/prompt_async"), json!({"agent": "build", "model": {"providerID": "membrane", "modelID": "claude-agent:sonnet"}, "parts": [{"type": "text", "text": "quick sonnet"}]}));
    assert_eq!(st, 204);
    let r = finished(port, &sid, 4);
    let a = argv_of(&dir, "quick sonnet").expect("the CLI ran");
    let i = a.iter().position(|x| x == "--model").unwrap_or_else(|| panic!("--model in {a:?}"));
    assert_eq!(a[i + 1], "sonnet", "the rung's prefix is the Python seat's namespace, the CLI knows the bare alias: {a:?}");
    assert!(i < a.iter().position(|x| x == "--").unwrap(), "an option, before the `--` that ends the options: {a:?}");
    assert_eq!(r[2]["info"]["model"]["modelID"], "claude-agent:sonnet");
    assert_eq!(r[3]["info"]["modelID"], "claude-agent:sonnet", "the assistant row names the model that answered");
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).1["model"]["id"], "claude-agent:sonnet", "the pick sticks to the session");
    // the next message, no model in the body: the session's pick still holds
    assert_eq!(say(port, &sid, "quick again"), 204);
    finished(port, &sid, 6);
    let a = argv_of(&dir, "quick again").unwrap();
    assert!(a.iter().any(|x| x == "claude-agent:sonnet" || x == "sonnet") && a.iter().any(|x| x == "--model"), "{a:?}");
    // the seat's own default picked again: back to the CLI's default
    let (st, _) = api(port, "PATCH", &format!("/session/{sid}"), json!({"model": {"providerID": "membrane", "modelID": "claude"}}));
    assert_eq!(st, 200);
    assert_eq!(say(port, &sid, "quick back"), 204);
    finished(port, &sid, 8);
    assert!(!argv_of(&dir, "quick back").unwrap().iter().any(|x| x == "--model"));
}

#[test]
fn a_model_the_seat_cannot_run_is_refused_in_the_turn_with_the_reason_and_the_cli_never_starts() {
    let (_run, port, dir) = seat_facts("ses-refuse", STEPS, false, &CLAUDE_FACTS);
    let sid = new_session(port, "R");
    let (st, _) = api(port, "POST", &format!("/session/{sid}/prompt_async"), json!({"model": {"providerID": "membrane", "modelID": "qwen3:8b"}, "parts": [{"type": "text", "text": "quick refused"}]}));
    assert_eq!(st, 204);
    let r = finished(port, &sid, 2);
    let err = r[1]["info"]["error"]["data"]["message"].as_str().unwrap_or_else(|| panic!("an error on the row: {}", r[1]["info"]));
    assert!(err.contains("qwen3:8b") && err.contains("claude"), "the reason names the model and what runs here: {err}");
    assert!(argv_of(&dir, "quick refused").is_none(), "nothing was run");
    assert_eq!(api(port, "GET", "/session/status", J::Null).1[&sid], json!({"type": "idle"}));
}
