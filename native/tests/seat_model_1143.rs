//! The SEAT-WIDE model (#1143): the page's own writes that set the model the seat talks with (the first-run "Which model?" form and the gear window
//! POST `{"loops": <model>}` to `/membrane/options`; v0.7.53 `ui/index.html:100`, `ui/home.js:892`; the minds' route `/membrane/models` takes the same
//! keys, `server.py:4923`), through a strict-door seat as the owner, with a fake `claude` that records its argv. The model that answers a message with
//! no pick of its own is that setting; a session's own pick wins; only the owner writes it; a model the seat cannot run is refused and the old one
//! stays; it survives a restart on the same store. There is NEVER a real `claude` here. Every wait has a timeout and the child is always reaped.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, _lines: Receiver<String> }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }
struct Dir(std::path::PathBuf);
impl Drop for Dir { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); } }

const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f", "door_session.c1f", "door_auth.c1f", "router.c1f", "door_models.c1f"];
/// a launcher's seat that answers through the claude CLI only: three claude rungs, no router, a backend nobody listens on, `claude` picked everywhere
const FACTS: [&str; 4] = [
    r#"cloud_models=[{"name":"claude","backend":"claude-agent"},{"name":"claude-agent:sonnet","backend":"claude-agent"},{"name":"claude-agent:opus","backend":"claude-agent"}]"#,
    "router_on=false", "seat_backend=http://127.0.0.1:1",
    r#"minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude"}"#];

const FAKE: &str = r#"#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.argv[-1]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
json.dump({"argv": sys.argv[1:]}, open(os.path.join(here, "argv." + tag + ".json"), "w"))
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "q-1"})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "q-1"})
"#;

fn setup(tag: &str) -> Dir {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    let pd = d.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap()).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let bin = d.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    Dir(d)
}

/// a strict-door seat on the folder's store (a second launch on the same folder is a restart on the same store)
fn launch(dir: &Dir, facts: &[&str]) -> (Run, u16) {
    let d = &dir.0;
    let pd = d.join("packs");
    let mut child = Command::new(BIN).current_dir(d).env("ANTHROPIC_API_KEY", "sk-ant-harness-default-0000")
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", d.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", d.to_str().unwrap(),
               "--fact", &format!("chat_claude_bin={}", d.join("fake-claude").display()), "--fact", "seat_cwd=/srv/seat", "--fact", "seat_project=membrane", "--fact", "seat_version=1.0",
               "--fact", &format!("door_secret_file={}", d.join("door.secret").display()), "--fact", &format!("seat_data_dir={}", d.display())])
        .args(facts.iter().flat_map(|f| ["--fact".to_string(), f.to_string()]))
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    (run, line.rsplit(':').next().unwrap().trim().parse().unwrap())
}

fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(90), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}
fn owner_token(dir: &Dir) -> String { wait("the owner token", || std::fs::read_to_string(dir.0.join("owner.token")).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty())) }
fn person_token(dir: &Dir, sub: &str) -> String {
    let secret = wait("the secret", || std::fs::read_to_string(dir.0.join("door.secret")).ok().filter(|t| !t.trim().is_empty()));
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    membrane_vm::jwt::mint(secret.trim().as_bytes(), sub, sub, 3600.0, now).unwrap()
}
/// a bounded retry (3 tries) when the connection gives no answer at all (a loaded machine can stall a seat; the posts here are safe to send again)
fn http(port: u16, method: &str, path: &str, tok: &str, body: &str) -> (u16, J) {
    for _ in 0..2 { if let Some(r) = http_once(port, method, path, tok, body) { return r; } }
    http_once(port, method, path, tok, body).expect("an HTTP answer")
}
fn http_once(port: u16, method: &str, path: &str, tok: &str, body: &str) -> Option<(u16, J)> {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\nAuthorization: Bearer {tok}\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n")?;
    let st: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    let t = String::from_utf8_lossy(&out[i + 4..]).to_string();
    Some((st, if t.trim().is_empty() { J::Null } else { serde_json::from_str(&t).unwrap_or(J::String(t)) }))
}
fn tag_of(prompt: &str) -> String { prompt.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).take(40).collect() }
fn argv_of(dir: &Dir, prompt: &str) -> Option<Vec<String>> {
    let t = std::fs::read_to_string(dir.0.join(format!("argv.{}.json", tag_of(prompt)))).ok()?;
    Some(serde_json::from_str::<J>(&t).ok()?["argv"].as_array()?.iter().map(|x| x.as_str().unwrap().to_string()).collect())
}
/// the `--model` value of the CLI run for this prompt (None: no flag); waits for the run
fn model_of(dir: &Dir, prompt: &str) -> Option<String> {
    let a = wait("the CLI run", || argv_of(dir, prompt));
    a.iter().position(|x| x == "--model").map(|i| a[i + 1].clone())
}
fn new_session(port: u16, tok: &str) -> String { http(port, "POST", "/session", tok, "{\"title\":\"M\"}").1["id"].as_str().unwrap().to_string() }
fn say(port: u16, tok: &str, sid: &str, text: &str, model: Option<&str>) -> u16 {
    let mut b = json!({"agent": "build", "parts": [{"type": "text", "text": text}]});
    if let Some(m) = model { b["model"] = json!({"providerID": "membrane", "modelID": m}); }
    http(port, "POST", &format!("/session/{sid}/prompt_async"), tok, &b.to_string()).0
}
fn settled(port: u16, tok: &str, sid: &str, n: usize) -> J {
    wait("the answer", || { let (_, r) = http(port, "GET", &format!("/session/{sid}/message"), tok, ""); let a = r.as_array()?; (a.len() >= n && a.iter().filter(|x| x["info"]["role"] == "assistant").all(|x| x["info"]["time"]["completed"].is_number())).then(|| r.clone()) })
}
fn options(port: u16, tok: &str) -> J { let (st, b) = http(port, "GET", "/membrane/options", tok, ""); assert_eq!(st, 200, "{b}"); b }
fn minds(port: u16, tok: &str) -> J { let (st, b) = http(port, "GET", "/membrane/models", tok, ""); assert_eq!(st, 200, "{b}"); b }

#[test]
fn the_pages_post_of_the_seat_model_changes_both_readbacks_and_the_next_turn_with_no_pick() {
    let dir = setup("seatmodel-set");
    let (_run, port) = launch(&dir, &FACTS);
    let tok = owner_token(&dir);
    assert_eq!(options(port, &tok)["background"]["loops"], "claude");
    let sid = new_session(port, &tok);
    assert_eq!(say(port, &tok, &sid, "turn one", None), 204);
    assert_eq!(model_of(&dir, "turn one"), None, "the plain default: no flag");
    settled(port, &tok, &sid, 2);
    // the page's own shape (index.html askModel / the gear window): one key
    let (st, b) = http(port, "POST", "/membrane/options", &tok, &json!({"loops": "claude-agent:sonnet"}).to_string());
    assert_eq!(st, 200, "{b}");
    assert_eq!(b["background"]["loops"], "claude-agent:sonnet", "the post answers with what the seat saved");
    assert_eq!(options(port, &tok)["background"]["loops"], "claude-agent:sonnet");
    assert_eq!(minds(port, &tok)["loops"], "claude-agent:sonnet");
    // the next turn, no pick in the message and none on the session: the CLI runs on it
    assert_eq!(say(port, &tok, &sid, "turn two", None), 204);
    assert_eq!(model_of(&dir, "turn two").as_deref(), Some("sonnet"), "the seat's setting is the model that answers");
    settled(port, &tok, &sid, 4);
    // and back to the plain default: no flag again
    assert_eq!(http(port, "POST", "/membrane/options", &tok, &json!({"loops": "claude"}).to_string()).0, 200);
    assert_eq!(say(port, &tok, &sid, "turn three", None), 204);
    assert_eq!(model_of(&dir, "turn three"), None);
    settled(port, &tok, &sid, 6);
    // the minds' own route sets it the same way
    assert_eq!(http(port, "POST", "/membrane/models", &tok, &json!({"loops": "claude-agent:opus"}).to_string()).0, 200);
    assert_eq!(options(port, &tok)["background"]["loops"], "claude-agent:opus");
    assert_eq!(say(port, &tok, &sid, "turn four", None), 204);
    assert_eq!(model_of(&dir, "turn four").as_deref(), Some("opus"));
}

#[test]
fn a_sessions_own_pick_still_wins_over_the_seat_setting() {
    let dir = setup("seatmodel-pick");
    let (_run, port) = launch(&dir, &FACTS);
    let tok = owner_token(&dir);
    assert_eq!(http(port, "POST", "/membrane/options", &tok, &json!({"loops": "claude-agent:sonnet"}).to_string()).0, 200);
    let sid = new_session(port, &tok);
    // a message that names opus: opus, not the seat's sonnet
    assert_eq!(say(port, &tok, &sid, "pick opus", Some("claude-agent:opus")), 204);
    assert_eq!(model_of(&dir, "pick opus").as_deref(), Some("opus"));
    settled(port, &tok, &sid, 2);
    // the pick sticks to the session; a later seat change does not move it
    assert_eq!(http(port, "POST", "/membrane/options", &tok, &json!({"loops": "claude"}).to_string()).0, 200);
    assert_eq!(say(port, &tok, &sid, "still opus", None), 204);
    assert_eq!(model_of(&dir, "still opus").as_deref(), Some("opus"));
    settled(port, &tok, &sid, 4);
    // a session that never picked follows the seat
    let other = new_session(port, &tok);
    assert_eq!(say(port, &tok, &other, "follows seat", None), 204);
    assert_eq!(model_of(&dir, "follows seat"), None);
}

#[test]
fn only_the_owner_sets_the_seat_model_a_person_is_refused_with_a_403_and_nothing_changes() {
    let dir = setup("seatmodel-owner");
    let (_run, port) = launch(&dir, &FACTS);
    let own = owner_token(&dir);
    let ann = person_token(&dir, "ann");
    assert_eq!(http(port, "GET", "/membrane/options", &ann, "").0, 200, "a person reads it");
    for path in ["/membrane/options", "/membrane/models"] {
        let (st, b) = http(port, "POST", path, &ann, &json!({"loops": "claude-agent:sonnet"}).to_string());
        assert_eq!((st, &b), (403, &json!({"type": "error", "error": {"message": "for the owner"}})), "{path}: {b}");
    }
    assert_eq!(options(port, &own)["background"]["loops"], "claude", "nothing changed");
    assert_eq!(minds(port, &own)["loops"], "claude");
    let sid = new_session(port, &own);
    assert_eq!(say(port, &own, &sid, "after refusal", None), 204);
    assert_eq!(model_of(&dir, "after refusal"), None);
}

#[test]
fn a_model_the_seat_does_not_know_is_refused_with_the_reason_and_the_old_setting_stays() {
    let dir = setup("seatmodel-unknown");
    let (_run, port) = launch(&dir, &FACTS);
    let tok = owner_token(&dir);
    assert_eq!(http(port, "POST", "/membrane/options", &tok, &json!({"loops": "claude-agent:sonnet"}).to_string()).0, 200);
    for (path, body, needle) in [("/membrane/options", json!({"loops": "qwen3:8b"}), "qwen3:8b"), ("/membrane/models", json!({"loops": "no-such"}), "no-such"),
                                 ("/membrane/options", json!({"loops": "has a space"}), "not a model name"), ("/membrane/options", json!({"loops": 7}), "not a model name")] {
        let (st, b) = http(port, "POST", path, &tok, &body.to_string());
        assert_eq!(st, 400, "{path} {body}: {b}");
        assert!(b["error"].as_str().unwrap_or("").contains(needle), "the reason names it: {b}");
        assert_eq!(options(port, &tok)["background"]["loops"], "claude-agent:sonnet", "the old setting stays after {body}");
    }
    let sid = new_session(port, &tok);
    assert_eq!(say(port, &tok, &sid, "after bad posts", None), 204);
    assert_eq!(model_of(&dir, "after bad posts").as_deref(), Some("sonnet"));
}

#[test]
fn the_seat_model_survives_a_restart_on_the_same_store() {
    let dir = setup("seatmodel-restart");
    let (run, port) = launch(&dir, &FACTS);
    let tok = owner_token(&dir);
    assert_eq!(http(port, "POST", "/membrane/options", &tok, &json!({"loops": "claude-agent:sonnet"}).to_string()).0, 200);
    assert_eq!(options(port, &tok)["background"]["loops"], "claude-agent:sonnet");
    drop(run);   // the child is killed and reaped: the store is what is left
    // the launcher still names `claude` (its minds fact is the FIRST boot's); the saved pick is the later word
    let (_run2, port2) = launch(&dir, &FACTS);
    let tok = owner_token(&dir);
    assert_eq!(options(port2, &tok)["background"]["loops"], "claude-agent:sonnet", "the options read it back");
    assert_eq!(minds(port2, &tok)["loops"], "claude-agent:sonnet");
    let sid = new_session(port2, &tok);
    assert_eq!(say(port2, &tok, &sid, "after restart", None), 204);
    assert_eq!(model_of(&dir, "after restart").as_deref(), Some("sonnet"), "and the turn runs on it");
}
