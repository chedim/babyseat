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
const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f", "door_session.c1f"];

/// the seat's folder holds the fake claude; the binary is told its path as a fact
fn seat(tag: &str, script: &str) -> (Run, u16, std::path::PathBuf) {
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
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port, dir)
}

/// one HTTP request on its own connection; -> (status, body text)
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
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
fn golden(name: &str) -> J {
    let p = format!("{}/../tests/golden/session/{name}.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{p}: {e}"))).unwrap()
}
/// the answer's body against the fixture's body, and the status against the fixture's
fn same_shape(name: &str, status: u16, body: &J) {
    let g = golden(name);
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
    assert_eq!(rows(&port, &sid).len(), 2);
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
