//! The barest agent seat as a process (#1143): the standalone binary on the shipped packs, the MCP door (`POST /mcp`, packs/door_tools.c1f)
//! and the chat (packs/chat.c1f, packs/chat_turn.c1f). Every wait has a timeout and the child is always reaped. There is NEVER a real
//! `claude` here: the turn's binary is a fake script the test writes (a Boot fact names it), so no model, no login, no network.
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

/// start the binary in a fresh seat folder on the shipped packs `names`, with the extra arguments; -> the run and the door's port.
/// The seat folder (the store's) is where a test puts its fake `claude`: the driver refuses work in other temporary folders.
fn start(tag: &str, names: &[&str], extra: &[&str]) -> (Run, u16) {
    let dir = tmp(tag);
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(names) { std::fs::write(pd.join(n), t).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .args(extra).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port)
}

/// one HTTP request on its own connection; -> (status, body text)
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}

fn rpc(port: u16, id: i64, method: &str, params: J) -> J {
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string());
    assert_eq!(st, 200, "{body}");
    serde_json::from_str(&body).unwrap_or_else(|e| panic!("{e}: {body}"))
}

/// a tool call's text (and whether it is an error)
fn call(port: u16, tool: &str, args: J) -> (String, bool) {
    let r = rpc(port, 1, "tools/call", json!({"name": tool, "arguments": args}));
    let res = &r["result"];
    (res["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string(), res["isError"].as_bool().unwrap())
}

/// poll `f` until it gives Some (a hard timeout, then a panic naming what was last seen)
fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(40), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn terminate(run: &mut Run) -> bool {
    unsafe { libc::kill(run.child.id() as i32, libc::SIGTERM); }
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(15) {
        if run.child.try_wait().unwrap().is_some() { return true; }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}

const MCP_PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f"];

#[test]
fn the_mcp_door_speaks_the_handshake_lists_the_field_tools_and_runs_them() {
    let (mut run, port) = start("mcp", MCP_PACKS, &[]);
    let r = rpc(port, 1, "initialize", json!({"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}));
    assert_eq!(r["result"]["serverInfo"]["name"], "membrane");
    assert_eq!(r["result"]["protocolVersion"], "2025-06-18");
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "method": "notifications/initialized"}).to_string());
    assert_eq!((st, body.as_str()), (202, ""));
    let names: Vec<String> = rpc(port, 2, "tools/list", json!({}))["result"]["tools"].as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap().to_string()).collect();
    assert_eq!(names, ["entity_query", "entity_read", "entity_write"]);
    assert_eq!(http(port, "GET", "/mcp", "").0, 405);

    // a Command made and run by two writes; the receipt is read back by property
    let (t, e) = call(port, "entity_write", json!({"spec": "Command:echo1", "prop": "cmd", "value": ["echo", "hi"]}));
    assert!(!e, "{t}");
    let (t, e) = call(port, "entity_write", json!({"spec": "Command:echo1", "prop": "run", "value": {"id": "r1", "args": ["there"]}}));
    assert!(!e, "{t}");
    let out = wait("the run's receipt", || { let (t, _) = call(port, "entity_read", json!({"spec": "Command:echo1", "prop": "output"})); if t.contains("hi there") { Some(t) } else { None } });
    assert!(out.contains("hi there"), "{out}");
    // the store's rows: a query by kind and a whole read (the host journals and stores each write)
    let q = wait("the Command in the store", || { let (t, _) = call(port, "entity_query", json!({"q": "kind:Command"})); if t.contains("Command:echo1") { Some(t) } else { None } });
    assert!(q.contains("Command:echo1 (Command)"), "{q}");
    let (n, _) = call(port, "entity_query", json!({"q": "kind:Command & name~:Command:echo", "count": true}));
    assert_eq!(n, "1 entity(ies)");
    let (t, e) = call(port, "entity_query", json!({"q": "status:open"}));
    assert!(e && t.contains("unsupported term status:open"), "{t}");
    let whole = wait("the whole read", || { let (t, _) = call(port, "entity_read", json!({"spec": "Command:echo1"})); if t.contains("\"cmd\"") { Some(t) } else { None } });
    assert!(whole.starts_with("Command:echo1 (Command) "), "{whole}");
    let (t, _) = call(port, "entity_read", json!({"spec": "Command:nothing"}));
    assert_eq!(t, "no such entity: Command:nothing");
    assert!(terminate(&mut run));
}

// ---- the bare chat, end to end, with a FAKE claude ----------------------------------------------------------------------------------------
// The fake is a python3 script the test writes into the seat's folder. It records its argv and environment (one file per prompt), prints a
// system init line, then talks to the door's /mcp ITSELF over HTTP (tools/list, a write, a read, as the real CLI would), waits for a
// `go.<prompt>` file (so the test can see the message `running`), and prints stream-json lines: assistant text and tool_use, the tool
// results, a result line. It never calls a model and uses no login. FAIL_RC (a file `fail`) makes it exit 3 with a message on stderr.
const FAKE: &str = r#"#!/usr/bin/env python3
import json, os, sys, time, urllib.request
here = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
prompt = argv[-1]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
json.dump({"argv": argv, "auto_memory": os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"), "cwd": os.getcwd()}, open(os.path.join(here, "argv." + tag + ".json"), "w"))
errp = os.path.join(here, "err")   # a file `err`: the CLI ends with a result line that reports this text as an error
if os.path.exists(os.path.join(here, "fail")):
    sys.stderr.write(open(os.path.join(here, "fail")).read() or "fake: not logged in\n"); sys.exit(3)
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": ["mcp__membrane__entity_read"]})
cfg = json.loads(argv[argv.index("--mcp-config") + 1])
url = cfg["mcpServers"]["membrane"]["url"]
def rpc(method, params, i):
    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))["result"]
rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake", "version": "0"}}, 1)
names = [t["name"] for t in rpc("tools/list", {}, 2)["tools"]]
for _ in range(300):
    if os.path.exists(os.path.join(here, "go." + tag)): break
    time.sleep(0.1)
w = rpc("tools/call", {"name": "entity_write", "arguments": {"spec": "Command:probe", "prop": "cmd", "value": ["echo", "x"]}}, 3)["content"][0]["text"]
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Listing " + ",".join(names)}, {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_write", "input": {"spec": "Command:probe", "prop": "cmd", "value": ["echo", "x"]}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": w}]}]}})
r = rpc("tools/call", {"name": "entity_read", "arguments": {"spec": "Command:probe", "prop": "cmd"}}, 4)["content"][0]["text"]
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tu2", "name": "mcp__membrane__entity_read", "input": {"spec": "Command:probe", "prop": "cmd"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu2", "content": r}]}})
if os.path.exists(errp):
    out({"type": "result", "subtype": "success", "is_error": True, "result": open(errp).read(), "num_turns": 1, "session_id": "fake-1"}); sys.exit(0)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Final answer to: " + prompt, "num_turns": 3, "session_id": "fake-1"})
"#;

const CHAT_PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "chat.c1f", "chat_turn.c1f"];

/// the seat's folder holds the fake; the binary is told its path as a fact
fn fake_seat(tag: &str, extra: &[&str]) -> (Run, u16, std::path::PathBuf) { fake_seat_with(tag, extra, &[]) }
/// the same, with more packs (name, text) in the seat's pack folder: a model's own banner pack, for one
fn fake_seat_with(tag: &str, extra: &[&str], packs: &[(&str, &str)]) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(CHAT_PACKS) { std::fs::write(pd.join(n), t).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();   // the barest app's default banner pack
    for (n, t) in packs { std::fs::write(pd.join(n), t).unwrap(); }
    let fact = format!("chat_claude_bin={}", bin.display());
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap(), "--fact", &fact])
        .args(extra).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port, dir)
}

fn tag_of(prompt: &str) -> String { prompt.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).take(40).collect() }

fn send(port: u16, text: &str) -> String {
    let (st, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": text}).to_string());
    assert_eq!(st, 202, "{body}");
    serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string()
}
fn view(port: u16, id: &str) -> J {
    let (st, body) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
    assert_eq!(st, 200, "{body}");
    serde_json::from_str(&body).unwrap()
}

#[test]
fn a_message_is_answered_by_one_fake_cli_run_that_uses_the_mcp_door_and_no_history_is_sent() {
    let (mut run, port, dir) = fake_seat("chat", &["--fact", "chat_max_turns=5"]);
    let id = send(port, "what is up");
    // waiting for the fake: the message is running, no answer yet
    let v = wait("the message running", || { let v = view(port, &id); if v["state"] == "running" { Some(v) } else { None } });
    assert_eq!(v["text"], "what is up");
    assert!(v["response"].is_null(), "{v}");
    wait("the fake's argv file", || if dir.join(format!("argv.{}.json", tag_of("what is up"))).exists() { Some(()) } else { None });
    std::fs::write(dir.join(format!("go.{}", tag_of("what is up"))), "").unwrap();
    let v = wait("the answer", || { let v = view(port, &id); if v["state"] == "done" || v["state"] == "error" { Some(v) } else { None } });
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Final answer to: what is up");
    assert_eq!(v["tools"], json!(["entity_write", "entity_read"]));

    // what the CLI was started with: the mode of the rung, the ceiling, the system prompt, the message alone
    let a: J = serde_json::from_str(&std::fs::read_to_string(dir.join(format!("argv.{}.json", tag_of("what is up")))).unwrap()).unwrap();
    let args: Vec<String> = a["argv"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect();
    let after = |flag: &str| -> String { let i = args.iter().position(|x| x == flag).unwrap_or_else(|| panic!("no {flag} in {args:?}")); args[i + 1].clone() };
    assert_eq!(a["auto_memory"], "1");
    assert!(args.contains(&"-p".to_string()) && args.contains(&"--verbose".to_string()) && args.contains(&"--include-partial-messages".to_string()), "{args:?}");
    assert_eq!(after("--output-format"), "stream-json");
    assert_eq!(after("--tools"), "", "the built-in tools are off");
    assert!(args.contains(&"--strict-mcp-config".to_string()), "no other MCP config");
    assert_eq!(after("--allowedTools"), "mcp__membrane__*");
    assert_eq!(after("--setting-sources"), "", "no settings files");
    assert_eq!(after("--max-turns"), "5", "the ceiling is the fact");
    assert!(after("--system-prompt").contains("CONVERSATION: main") && !after("--system-prompt").contains("BannerLine"), "the system prompt carries the default banner");
    assert!(after("--system-prompt").contains("Modify your banner to help you maintain continuity."), "the system prompt carries the doctrine");
    assert!(!after("--system-prompt").to_lowercase().contains("history reaches"), "the prompt never says what the model lacks (it makes Claude panic)");
    assert!(after("--mcp-config").contains(&format!("127.0.0.1:{port}/mcp")));
    assert_eq!(args[args.len() - 2], "--", "the options end before the prompt");
    assert_eq!(args[args.len() - 1], "what is up", "the prompt is the message alone");
    assert!(!args.iter().any(|x| x == "--bare" || x == "--resume" || x == "--continue"), "{args:?}");

    // the tools it ran are entities under the message, and the write it made through the door is in the field
    let (n, _) = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn:tool:tu1"), "prop": "name"}));
    assert_eq!(n, "mcp__membrane__entity_write");
    let (n, _) = wait("the tool result", || { let r = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn:tool:tu2"), "prop": "result"})); if r.0.contains("echo") { Some(r) } else { None } });
    assert!(n.contains("echo") && n.contains("x"), "{n}");
    let (c, _) = call(port, "entity_read", json!({"spec": "Command:probe", "prop": "cmd"}));
    assert!(c.contains("echo"), "{c}");

    // a second message: NO history, the first message's words are nowhere in its argv
    let id2 = send(port, "second question");
    wait("the second run", || if dir.join(format!("argv.{}.json", tag_of("second question"))).exists() { Some(()) } else { None });
    std::fs::write(dir.join(format!("go.{}", tag_of("second question"))), "").unwrap();
    let v2 = wait("the second answer", || { let v = view(port, &id2); if v["state"] == "done" { Some(v) } else { None } });
    assert_eq!(v2["response"], "Final answer to: second question");
    let raw = std::fs::read_to_string(dir.join(format!("argv.{}.json", tag_of("second question")))).unwrap();
    assert!(!raw.contains("what is up") && !raw.contains("Final answer"), "history leaked into the second run: {raw}");
    assert_ne!(id, id2);
    assert!(terminate(&mut run));
}

#[test]
fn a_cli_that_fails_is_an_error_answer_with_its_stderr_and_a_missing_one_says_how_to_name_it() {
    let (mut run, port, dir) = fake_seat("chatfail", &[]);
    std::fs::write(dir.join("fail"), "").unwrap();
    let id = send(port, "hello");
    let v = wait("the error answer", || { let v = view(port, &id); if v["state"] == "error" || v["state"] == "done" { Some(v) } else { None } });
    assert_eq!(v["state"], "error", "{v}");
    assert_eq!(v["response"], "claude failed (rc 3): fake: not logged in");
    assert!(terminate(&mut run));
    // no binary named: nothing runs, the answer says how
    let (mut run, port) = start("chatnobin", CHAT_PACKS, &[]);
    let id = send(port, "hello");
    let v = wait("the refusal", || { let v = view(port, &id); if v["state"] == "error" { Some(v) } else { None } });
    assert!(v["response"].as_str().unwrap().contains("chat_claude_bin"), "{v}");
    assert!(terminate(&mut run));
}

/// the system prompt of the run the fake recorded for `prompt`
fn system_prompt(dir: &std::path::Path, prompt: &str) -> String {
    let a: J = wait("the fake's argv file", || std::fs::read_to_string(dir.join(format!("argv.{}.json", tag_of(prompt)))).ok().and_then(|t| serde_json::from_str(&t).ok()));
    let args = a["argv"].as_array().unwrap();
    let i = args.iter().position(|x| x == "--system-prompt").expect("a --system-prompt");
    args[i + 1].as_str().unwrap().to_string()
}
/// let the fake run on and wait for the message's end state
fn finish(port: u16, dir: &std::path::Path, id: &str, prompt: &str) -> J {
    wait("the fake's argv file", || dir.join(format!("argv.{}.json", tag_of(prompt))).exists().then_some(()));
    std::fs::write(dir.join(format!("go.{}", tag_of(prompt))), "").unwrap();
    wait("the end state", || { let v = view(port, id); if v["state"] == "done" || v["state"] == "error" { Some(v) } else { None } })
}

#[test]
fn a_failed_runs_whole_stderr_reaches_the_response_and_the_next_banner() {
    let (mut run, port, dir) = fake_seat("chatfaillong", &[]);
    let long = format!("start {} end-of-the-stderr", "word ".repeat(500));   // 2500+ characters
    std::fs::write(dir.join("fail"), &long).unwrap();
    let id1 = send(port, "first");
    let v = wait("the error answer", || { let v = view(port, &id1); if v["state"] == "error" { Some(v) } else { None } });
    let want = format!("claude failed (rc 3): {}", long.trim());
    assert_eq!(v["response"], want.as_str(), "the response carries the whole stderr");
    std::fs::remove_file(dir.join("fail")).unwrap();
    send(port, "second");
    let sys = system_prompt(&dir, "second");
    assert!(sys.contains(&want), "the banner's error line carries all of it: {sys}");
    assert!(terminate(&mut run));
}

#[test]
fn a_banner_without_text_still_runs_the_turn_and_the_system_prompt_shows_the_banner_error() {
    // the model's pack leaves Banner.text null (it reads an entity that is not there): the message must not sit queued
    let pack = "PACK nulltext 1\nIMPORT default_banner 1\nBanner.text getter: prop('Missing:one', 'text')\n";
    let (mut run, port, dir) = fake_seat_with("bannernull", &[], &[("nulltext.c1f", pack)]);
    let id = send(port, "still there");
    let sys = system_prompt(&dir, "still there");
    assert!(sys.contains("BANNER ERROR:") && sys.contains("Help:banner"), "{sys}");
    assert!(sys.contains("has no `text`"), "{sys}");
    assert!(sys.contains("Modify your banner to help you maintain continuity."), "the doctrine is still there: {sys}");
    assert_eq!(finish(port, &dir, &id, "still there")["state"], "done");
    assert!(terminate(&mut run));
    // a null with a reason of its own: the reason is in the banner error
    let pack = "PACK nulltext 1\nIMPORT default_banner 1\nBanner.text getter: None('the model emptied it')\n";
    let (mut run, port, dir) = fake_seat_with("bannernull2", &[], &[("nulltext.c1f", pack)]);
    send(port, "again");
    let sys = system_prompt(&dir, "again");
    assert!(sys.contains("BANNER ERROR:") && sys.contains("the reason: the model emptied it"), "{sys}");
    assert!(terminate(&mut run));
}

#[test]
fn the_banner_shows_the_previous_errors_entity_and_its_whole_text_until_a_turn_succeeds() {
    let (mut run, port, dir) = fake_seat("bannererr", &[]);
    let long = format!("start {} end-of-the-error", "word ".repeat(400));   // 2000+ characters
    std::fs::write(dir.join("err"), &long).unwrap();
    let id1 = send(port, "first");
    assert_eq!(finish(port, &dir, &id1, "first")["state"], "error");
    std::fs::remove_file(dir.join("err")).unwrap();
    let id2 = send(port, "second");
    let sys = system_prompt(&dir, "second");
    let line = format!("THE PREVIOUS TURN ENDED WITH AN ERROR: membrane::Chat:message:{id1}:turn:response - claude reported an error: {long}");
    assert!(sys.contains(&line), "the eid, the summary, no cap: {sys}");
    assert_eq!(finish(port, &dir, &id2, "second")["state"], "done");
    let id3 = send(port, "third");
    let sys = system_prompt(&dir, "third");
    assert!(!sys.contains("ENDED WITH AN ERROR"), "a later successful turn clears the line: {sys}");
    assert_eq!(finish(port, &dir, &id3, "third")["state"], "done");
    assert!(terminate(&mut run));
}
