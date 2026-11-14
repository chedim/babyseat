//! The settings window's server side (#1143, docs/settings-groups.md): the `Setting:<group>.<key>` entities of `membrane/packs/settings.c1f`. One row per setting: its descriptor
//! (label, help, kind, limits, default, live) is data, `current` is a getter over the one owner of the value, `set` is the one write door (it validates, then writes the owner, or
//! writes `refused = <reason>` and nothing else). The seat is the shipped one (barest.packs + the app) with a fake `claude`; the writes go through `POST /mcp entity_write`, the
//! door the window uses (owner-checked). Every wait has a timeout and the child is always reaped.
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
/// the shipped barest seat on the folder's store (a second launch on the same folder is a restart); `secret` gives it a door secret and owner token (a strict door)
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
/// the window's write: ONE entity_write of `Setting:<id>.set`
fn set(port: u16, id: &str, v: J) -> (u16, String) { rpc(port, None, "entity_write", json!({"spec": format!("Setting:{id}"), "prop": "set", "value": v})) }
fn cur(port: u16, id: &str) -> J { get(port, &format!("Setting:{id}"), "current") }
fn refused(port: u16, id: &str) -> String { get(port, &format!("Setting:{id}"), "refused").as_str().unwrap_or("").to_string() }
/// every stored property of an entity, as the full read prints it
fn full(port: u16, eid: &str) -> J {
    let (_, t) = rpc(port, None, "entity_read", json!({"spec": eid}));
    serde_json::from_str(t.split_once(") ").map(|x| x.1).unwrap_or("null")).unwrap_or(J::Null)
}
fn owner_token(dir: &Dir) -> String { wait("the owner token", || std::fs::read_to_string(dir.0.join("owner.token")).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty())) }
fn person_token(dir: &Dir, sub: &str) -> String {
    let secret = wait("the secret", || std::fs::read_to_string(dir.0.join("door.secret")).ok().filter(|t| !t.trim().is_empty()));
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    membrane_vm::jwt::mint(secret.trim().as_bytes(), sub, sub, 3600.0, now).unwrap()
}

/// (id, kind, live): the rows of the design after the operator's answers
const ROWS: [(&str, &str, &str); 32] = [
    ("model.chat", "text", "live"), ("model.default", "readonly", "restart"), ("model.list", "readonly", "restart"), ("model.cloud", "readonly", "restart"),
    ("model.signin", "readonly", "restart"), ("model.cli", "readonly", "restart"),
    ("chat.max_steps", "number", "live"), ("chat.timeout", "number", "live"),
    ("map.hidden", "list", "live"), ("map.hidden_prefix", "list", "live"), ("map.window", "number", "live"), ("map.poll_ms", "number", "live"),
    ("map.tile", "readonly", "restart"), ("map.fade_s", "readonly", "restart"),
    ("home.allow", "allowlist", "live"),
    ("import.sources", "readonly", "restart"), ("import.history", "readonly", "restart"), ("import.page", "readonly", "restart"),
    ("access.owner", "readonly", "restart"), ("access.token_life", "readonly", "restart"), ("access.token_file", "readonly", "restart"), ("access.mode", "readonly", "restart"),
    ("access.people", "readonly", "restart"), ("access.port", "readonly", "restart"),
    ("limits.command_timeout_s", "readonly", "restart"), ("limits.effect_workers", "readonly", "restart"), ("limits.warm_rows", "readonly", "restart"),
    ("limits.memory_budget_mb", "readonly", "restart"), ("limits.unload_soft", "readonly", "restart"), ("limits.unload_hard", "readonly", "restart"), ("limits.unload_batch", "readonly", "restart"),
    ("limits.eval_stats", "readonly", "restart"), ("limits.eval_stats_window_s", "readonly", "restart"),
];

#[test]
fn every_row_has_its_descriptor_as_data_in_the_designed_group_and_order() {
    let dir = setup("rows");
    let run = start(&dir, false, &[]);
    let mut last = (0usize, 0.0f64);
    for (id, kind, live) in ROWS {
        let eid = format!("Setting:{id}");
        let (g, k) = id.split_once('.').unwrap();
        assert_eq!(get(run.port, &eid, "group"), json!(g), "{id}");
        assert_eq!(get(run.port, &eid, "key"), json!(k), "{id}");
        assert_eq!(get(run.port, &eid, "kind"), json!(kind), "{id}");
        assert_eq!(get(run.port, &eid, "live"), json!(live), "{id}");
        for p in ["label", "help"] { assert!(get(run.port, &eid, p).as_str().map_or(false, |t| t.len() > 3), "{id} has a {p}"); }
        let order = get(run.port, &eid, "order").as_f64().unwrap_or_else(|| panic!("{id} has an order"));
        let gi = ["model", "chat", "map", "home", "import", "access", "limits"].iter().position(|x| *x == g).unwrap();
        assert!((gi, order) >= last, "{id}: groups run Model, Chat, Map, Home, Import, Access, Limits and rows keep their order");
        last = (gi, order);
    }
    // the Map group is hidden from the window while the field view is disabled (operator 2026-11-14); every other row is listed
    for (id, _, _) in ROWS { assert_eq!(get(run.port, &format!("Setting:{id}"), "hidden"), if id.starts_with("map.") { json!("True") } else { J::Null }, "{id}: hidden only for the Map rows"); }
    // what the operator dropped is not a row: the dead settings and the page's own constants
    let (_, all) = rpc(run.port, None, "entity_query", json!({"q": "kind:Setting", "k": 100}));
    assert_eq!(all.lines().filter(|l| l.starts_with("Setting:")).count(), ROWS.len(), "exactly the designed rows: {all}");
    for gone in ["cortex", "workers", "loops_auth", "think", "self_cpu_share", "code_runs_keep", "page_size", "poll_size", "stick_px", "tool_chars"] { assert!(!all.contains(&format!(".{gone} ")) && !all.contains(&format!("Setting:{gone} ")), "no row for {gone}: {all}"); }
}

#[test]
fn a_number_is_refused_with_the_reason_never_clamped_and_nothing_changes() {
    let dir = setup("number");
    let run = start(&dir, false, &[]);
    assert_eq!(cur(run.port, "chat.timeout"), json!(21600), "the pack's default stands");
    for (v, needle) in [(json!(0), "at least"), (json!(-5), "at least"), (json!(99999999), "at most"), (json!("soon"), "number"), (json!(true), "number")] {
        let before = full(run.port, "Setting:chat.timeout");
        set(run.port, "chat.timeout", v.clone());
        let why = refused(run.port, "chat.timeout");
        assert!(why.contains(needle), "{v}: {why}");
        assert_eq!(cur(run.port, "chat.timeout"), json!(21600), "{v}: nothing changed");
        let mut after = full(run.port, "Setting:chat.timeout");
        let mut b = before.clone();
        for m in [&mut after, &mut b] { if let Some(o) = m.as_object_mut() { o.remove("refused"); o.remove("set"); o.remove("summary"); } }
        assert_eq!(b, after, "{v}: the refusal wrote nothing but `refused` (and the ask itself)");
    }
    set(run.port, "chat.max_steps", json!(2.5));
    assert!(refused(run.port, "chat.max_steps").contains("whole"), "a step count is a whole number: {}", refused(run.port, "chat.max_steps"));
    assert_eq!(cur(run.port, "chat.max_steps"), json!(2048));
    set(run.port, "map.window", json!(0));
    assert!(refused(run.port, "map.window").contains("at least"));
    assert_eq!(cur(run.port, "map.window"), json!(150), "the default window stands");
}

#[test]
fn a_valid_write_is_current_at_once_and_clears_an_earlier_refusal() {
    let dir = setup("valid");
    let run = start(&dir, false, &[]);
    set(run.port, "chat.timeout", json!(0));
    assert!(!refused(run.port, "chat.timeout").is_empty());
    set(run.port, "chat.timeout", json!(90));
    assert_eq!(cur(run.port, "chat.timeout"), json!(90));
    assert_eq!(refused(run.port, "chat.timeout"), "", "a good write clears the reason");
    assert!(get(run.port, "Setting:chat.timeout", "applied").as_f64().is_some(), "and says when it was applied");
    // the model's name: the owner is the chat's own `Minds:seat.loops`
    set(run.port, "model.chat", json!("has a space"));
    assert!(refused(run.port, "model.chat").contains("model name"), "{}", refused(run.port, "model.chat"));
    set(run.port, "model.chat", json!("claude-agent:sonnet"));
    assert_eq!(get(run.port, "Minds:seat", "loops"), json!("claude-agent:sonnet"));
    assert_eq!(cur(run.port, "model.chat"), json!("claude-agent:sonnet"));
    // a list: hidden classes are names, an empty name would hide everything and is refused
    set(run.port, "map.hidden", json!(["A", ""]));
    assert!(refused(run.port, "map.hidden").contains("empty"), "{}", refused(run.port, "map.hidden"));
    set(run.port, "map.hidden", json!(["A", "B"]));
    assert_eq!(cur(run.port, "map.hidden"), json!(["A", "B"]));
}

#[test]
fn read_only_rows_refuse_a_write_with_the_launch_line_and_the_window_cannot_set_a_sql_source_to_rw() {
    let dir = setup("readonly");
    let run = start(&dir, false, &["sql_sources=[{\"name\":\"old\",\"path\":\"/srv/old.db\",\"mode\":\"ro\"}]"]);
    for id in ["limits.command_timeout_s", "access.port", "model.cloud", "import.sources", "model.signin"] {
        set(run.port, id, json!(5));
        let why = refused(run.port, id);
        assert!(why.contains("read-only") && why.contains("restart"), "{id}: {why}");
    }
    assert_eq!(get(run.port, "SqlSource:old", "mode"), json!("ro"));
    set(run.port, "import.sources", json!([{"name": "old", "path": "/srv/old.db", "mode": "rw"}]));
    assert_eq!(get(run.port, "SqlSource:old", "mode"), json!("ro"), "rw lets the model change the file: the window cannot set it");
    let shown = cur(run.port, "import.sources");
    assert_eq!(shown[0]["name"], json!("old"));
    assert_eq!(shown[0]["mode"], json!("ro"), "the mode is shown");
    assert_eq!(cur(run.port, "limits.command_timeout_s"), json!(120), "the host's own default is shown");
}

#[test]
fn a_chat_turn_picks_up_a_new_step_limit_and_time_limit_without_a_restart() {
    let dir = setup("turn");
    let run = start(&dir, false, &["chat_max_turns=11", "chat_turn_timeout_s=111"]);
    assert_eq!(cur(run.port, "chat.max_steps"), json!(11), "the launch fact is the default of the live row");
    assert_eq!(cur(run.port, "chat.timeout"), json!(111));
    let sid = serde_json::from_str::<J>(&http(run.port, "POST", "/session", None, "{\"title\":\"S\"}").1).unwrap()["id"].as_str().unwrap().to_string();
    let say = |text: &str| { http(run.port, "POST", &format!("/session/{sid}/prompt_async"), None, &json!({"agent": "build", "parts": [{"type": "text", "text": text}]}).to_string()); };
    let argv = |text: &str| -> Vec<String> {
        let tag: String = text.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).take(40).collect();
        wait("the CLI run", || std::fs::read_to_string(dir.0.join(format!("argv.{tag}.json"))).ok().map(|t| serde_json::from_str::<J>(&t).unwrap()["argv"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()))
    };
    let after = |a: &[String], flag: &str| a[a.iter().position(|x| x == flag).unwrap() + 1].clone();
    say("first");
    assert_eq!(after(&argv("first"), "--max-turns"), "11");
    set(run.port, "chat.max_steps", json!(7));
    set(run.port, "chat.timeout", json!(99));
    say("second");
    assert_eq!(after(&argv("second"), "--max-turns"), "7", "the next turn runs with the new step limit");
    let t = wait("the second turn's command", || { let (_, t) = rpc(run.port, None, "entity_query", json!({"q": "kind:Command & name~:membrane::Command:chat:", "full": true, "k": 100})); (t.matches("\"timeout_s\":99").count() >= 1).then_some(t) });
    assert!(t.matches("\"timeout_s\":111").count() >= 1 && t.matches("\"timeout_s\":99").count() == 1, "the first turn kept its 111 s, the second ran with 99 s: {t}");
}

#[test]
fn the_field_feed_honours_a_new_window_and_hidden_lists_as_they_are_written() {
    let dir = setup("feed");
    let run = start(&dir, false, &[]);
    for i in 0..6 { rpc(run.port, None, "entity_write", json!({"spec": format!("Zed:{i}"), "prop": "n", "value": 1})); std::thread::sleep(Duration::from_millis(20)); }
    rpc(run.port, None, "entity_write", json!({"spec": "Qux:1", "prop": "n", "value": 1}));
    let feed = |q: &str| -> J { serde_json::from_str(&http(run.port, "GET", &format!("/ui/fieldvm/active{q}"), None, "").1).unwrap() };
    let ids = |j: &J| -> Vec<String> { j["nodes"].as_array().unwrap().iter().map(|n| n["id"].as_str().unwrap().to_string()).collect() };
    assert_eq!(feed("")["m"], json!(150), "no `m` and nothing written: the default window of the setting");
    assert!(!ids(&feed("?m=500")).iter().any(|i| i.starts_with("Setting:")), "the settings are hidden by default");
    assert_eq!(cur(run.port, "map.hidden").as_array().unwrap().last().unwrap(), &json!("Setting"));
    assert_eq!(cur(run.port, "map.poll_ms"), json!(2000));
    set(run.port, "map.window", json!(3));
    assert_eq!(feed("")["nodes"].as_array().unwrap().len(), 3, "the written window is the feed's size when the page names none");
    assert_eq!(feed("?m=5")["nodes"].as_array().unwrap().len(), 5, "a window the page names wins");
    assert!(ids(&feed("?m=50")).iter().any(|i| i.starts_with("Zed:")));
    set(run.port, "map.hidden", json!(["Zed", "Setting"]));
    let j = feed("?m=50");
    assert!(!ids(&j).iter().any(|i| i.starts_with("Zed:") || i.starts_with("Setting:")), "{:?}", ids(&j));
    assert!(ids(&j).iter().any(|i| i.starts_with("Qux:")));
    assert_eq!(j["hidden"], json!(["Zed", "Setting"]));
    set(run.port, "map.hidden_prefix", json!(["Qux:"]));
    assert!(!ids(&feed("?m=50")).iter().any(|i| i.starts_with("Qux:")), "a hidden prefix takes effect at once too");
    // the page's snapshot carries the window and the poll period it reads (the same owner)
    set(run.port, "map.poll_ms", json!(1500));
    let snap = http(run.port, "GET", "/ui/fieldvm", None, "").1;
    assert!(snap.contains("\"field_window\":3") && snap.contains("\"field_poll_ms\":1500"), "{snap}");
}

#[test]
fn the_launch_fact_is_the_default_of_a_live_row_and_a_written_value_wins_over_it() {
    let dir = setup("fact");
    let run = start(&dir, false, &["map_hidden=Zed,Qux", "field_window=9"]);
    assert_eq!(cur(run.port, "map.hidden"), json!(["Zed", "Qux"]));
    assert_eq!(cur(run.port, "map.window"), json!(9));
    set(run.port, "map.window", json!(4));
    assert_eq!(cur(run.port, "map.window"), json!(4));
}

#[test]
fn the_settings_survive_a_restart_on_the_same_store() {
    let dir = setup("replay");
    let run = start(&dir, false, &["chat_turn_timeout_s=111"]);
    set(run.port, "chat.timeout", json!(77));
    set(run.port, "map.hidden", json!(["Zed"]));
    set(run.port, "model.chat", json!("claude-agent:opus"));
    set(run.port, "chat.max_steps", json!(0));   // refused: must not come back either
    drop(run);
    let run2 = start(&dir, false, &["chat_turn_timeout_s=111"]);
    assert_eq!(cur(run2.port, "chat.timeout"), json!(77), "the journal's value wins over the launch fact");
    assert_eq!(cur(run2.port, "map.hidden"), json!(["Zed"]));
    assert_eq!(cur(run2.port, "model.chat"), json!("claude-agent:opus"));
    assert_eq!(cur(run2.port, "chat.max_steps"), json!(2048));
    assert_eq!(get(run2.port, "Setting:chat.timeout", "label").as_str().is_some(), true, "the descriptors are there once, not doubled");
}

#[test]
fn a_person_who_is_not_the_owner_gets_the_403_text_and_the_owner_writes() {
    let dir = setup("owner");
    let run = start(&dir, true, &[]);
    let own = owner_token(&dir);
    let ann = person_token(&dir, "ann");
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": "Setting:chat.timeout", "prop": "set", "value": 5}}}).to_string();
    let (st, b) = http(run.port, "POST", "/mcp", Some(&ann), &body);
    assert_eq!((st, serde_json::from_str::<J>(&b).unwrap()), (403, json!({"type": "error", "error": {"message": "for the owner"}})));
    let rd = |tok: &str| rpc(run.port, Some(tok), "entity_read", json!({"spec": "Setting:chat.timeout", "prop": "current"})).1;
    assert_eq!(rd(&ann), "21600", "a person reads it");
    let (st, _) = http(run.port, "POST", "/mcp", Some(&own), &body);
    assert_eq!(st, 200);
    assert_eq!(rd(&own), "5");
}

#[test]
fn no_row_holds_or_shows_a_secret_only_the_names_of_environment_variables() {
    let dir = setup("secret");
    let run = start(&dir, true, &["cloud_models=[{\"name\":\"gpt\",\"backend\":\"openai\",\"base_url\":\"https://user:hunter2pw@example.test/v1\",\"api_key_env\":\"OTHER_PROVIDER_KEY\"}]"]);
    let own = owner_token(&dir);
    let secret = std::fs::read_to_string(dir.0.join("door.secret")).unwrap();
    let (_, all) = rpc(run.port, Some(&own), "entity_query", json!({"q": "kind:Setting", "full": true, "k": 100}));
    for s in [KEY, secret.trim(), own.as_str(), "hunter2pw"] { assert!(!all.contains(s), "a setting shows a secret ({}...)", &s[..6]); }
    assert!(all.contains("OTHER_PROVIDER_KEY"), "the variable's NAME is shown: {all}");
    assert_eq!(rpc(run.port, Some(&own), "entity_read", json!({"spec": "Setting:model.signin", "prop": "current"})).1, "api_key");
    let f = rpc(run.port, Some(&own), "entity_read", json!({"spec": "Setting:access.token_file", "prop": "current"})).1;
    assert!(f.contains("owner.token") && f != own, "the token file's PATH only: {f}");
}

#[test]
fn the_home_allow_list_is_written_through_the_row_and_an_entry_left_out_is_retired() {
    let dir = setup("home");
    let run = start(&dir, false, &[]);
    assert_eq!(cur(run.port, "home.allow"), json!([]), "nothing is allowed by default");
    set(run.port, "home.allow", json!([{"name": "kitchen", "services": ["light.turn_on"], "entities": ["light.*"]}, {"name": "temp", "services": ["states.read"], "entities": ["sensor.t"]}]));
    assert_eq!(refused(run.port, "home.allow"), "");
    assert_eq!(get(run.port, "HaAllow:kitchen", "services"), json!(["light.turn_on"]));
    assert_eq!(get(run.port, "HaAllow:temp", "entities"), json!(["sensor.t"]));
    assert_eq!(cur(run.port, "home.allow").as_array().unwrap().len(), 2);
    set(run.port, "home.allow", json!([{"name": "kitchen", "services": ["light.*"], "entities": ["light.*"]}]));
    assert_eq!(get(run.port, "HaAllow:kitchen", "services"), json!(["light.*"]));
    let names: Vec<J> = cur(run.port, "home.allow").as_array().unwrap().iter().map(|e| e["name"].clone()).collect();
    assert_eq!(names, vec![json!("kitchen")], "the entry the new list leaves out is gone");
    set(run.port, "home.allow", json!([{"name": "", "services": [], "entities": []}]));
    assert!(refused(run.port, "home.allow").contains("name"), "{}", refused(run.port, "home.allow"));
    assert_eq!(cur(run.port, "home.allow").as_array().unwrap().len(), 1, "a refused list changes nothing");
}

/// The page's read feed (`GET /ui/settings`, #1143 the settings view): the browser VM cannot read entities by itself, so the seat answers every row with its descriptor and its
/// `current` / `refused` / `applied` in the window's order. Hidden rows are flagged (the view leaves them out), a secret is never in it.
#[test]
fn the_view_feed_lists_every_row_in_group_and_order_with_its_current_value_and_no_secret() {
    let dir = setup("feedview");
    let run = start(&dir, true, &["cloud_models=[{\"name\":\"gpt\",\"backend\":\"openai\",\"base_url\":\"https://user:hunter2pw@example.test/v1\",\"api_key_env\":\"OTHER_PROVIDER_KEY\",\"api_key\":\"sk-feed-leak\"}]"]);
    let own = owner_token(&dir);
    let feed = |tok: &str| -> (u16, J) { let (st, b) = http(run.port, "GET", "/ui/settings", Some(tok), ""); (st, serde_json::from_str(&b).unwrap_or(J::Null)) };
    let set = |id: &str, v: J| { rpc(run.port, Some(&own), "entity_write", json!({"spec": format!("Setting:{id}"), "prop": "set", "value": v})); };
    let (st, j) = feed(&own);
    assert_eq!(st, 200, "{j}");
    let rows = j["settings"].as_array().expect("a `settings` list");
    assert_eq!(rows.len(), ROWS.len(), "every row, hidden ones included (flagged)");
    for (r, (id, kind, live)) in rows.iter().zip(ROWS) {
        assert_eq!((r["id"].as_str(), r["kind"].as_str(), r["live"].as_str()), (Some(id), Some(kind), Some(live)), "group/order: {id}");
        assert_eq!(r["group"], json!(id.split_once('.').unwrap().0));
        for k in ["label", "help", "order", "hidden", "current", "refused"] { assert!(r.get(k).is_some(), "{id} has {k}"); }
        assert_eq!(r["hidden"], json!(id.starts_with("map.")), "{id}: hidden is a bool");
        assert_eq!(r["refused"], json!(""), "{id}: nothing refused yet");
    }
    let by = |id: &str| rows.iter().find(|r| r["id"] == json!(id)).unwrap().clone();
    assert_eq!((by("chat.timeout")["current"].clone(), by("chat.timeout")["min"].clone(), by("chat.timeout")["max"].clone(), by("chat.timeout")["unit"].clone()), (json!(21600), json!(1), json!(604800), json!("seconds")));
    assert_eq!(by("chat.max_steps")["whole"], json!(true));
    assert_eq!(by("chat.max_steps")["default"], json!(2048));
    assert!(by("limits.command_timeout_s")["how"].as_str().unwrap().contains("--fact command_timeout_s"), "a restart row carries its launch line");
    assert_eq!(by("model.cloud")["current"][0]["api_key_env"], json!("OTHER_PROVIDER_KEY"));
    // the answer is the owner's data and holds no secret: the key, the door secret, the owner key, the url's password, the provider's key
    let (_, raw) = http(run.port, "GET", "/ui/settings", Some(&own), "");
    let secret = std::fs::read_to_string(dir.0.join("door.secret")).unwrap();
    for s in [KEY, secret.trim(), own.as_str(), "hunter2pw", "sk-feed-leak"] { assert!(!raw.contains(s), "the feed shows a secret ({}...)", &s[..6]); }
    // a write shows in the next read: applied (current, applied time) or refused (the reason, the value unchanged)
    set("chat.timeout", json!(0));
    let r = by_id(&feed(&own).1, "chat.timeout");
    assert!(r["refused"].as_str().unwrap().contains("at least"), "{r}");
    assert_eq!(r["current"], json!(21600));
    set("chat.timeout", json!(90));
    let r = by_id(&feed(&own).1, "chat.timeout");
    assert_eq!((r["current"].clone(), r["refused"].clone()), (json!(90), json!("")));
    assert!(r["applied"].as_f64().is_some(), "{r}");
    // a person who is not the owner reads it too (the write is the owner's door, not this one); no token at all is the door's own refusal
    assert_eq!(feed(&person_token(&dir, "ann")).0, 200);
    assert_eq!(http(run.port, "GET", "/ui/settings", None, "").0, 401);
    assert_eq!(http(run.port, "POST", "/ui/settings", Some(&own), "{}").0, 405, "read only");
}
fn by_id(j: &J, id: &str) -> J { j["settings"].as_array().unwrap().iter().find(|r| r["id"] == json!(id)).unwrap().clone() }

/// The chat model row carries the seat's model list as `choices` (the view shows a select when it is not empty, a text input when it is). The barest seat loads no router pack, so
/// it knows no models: `choices` is the empty list there, the same as `model.list`'s `current`, and only the model row has the key.
#[test]
fn the_chat_model_row_carries_the_seats_model_list_as_choices_empty_on_the_barest_seat() {
    let dir = setup("choices");
    let run = start(&dir, false, &[]);
    let j: J = serde_json::from_str(&http(run.port, "GET", "/ui/settings", None, "").1).unwrap();
    assert_eq!(by_id(&j, "model.chat")["choices"], json!([]));
    assert_eq!(by_id(&j, "model.list")["current"], json!([]), "the same list");
    assert!(by_id(&j, "chat.timeout")["choices"].is_null(), "only the model row has choices");
}
