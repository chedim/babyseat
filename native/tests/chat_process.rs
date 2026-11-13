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
fn start(tag: &str, names: &[&str], extra: &[&str]) -> (Run, u16) { start_env(tag, names, extra, &[]) }
fn start_env(tag: &str, names: &[&str], extra: &[&str], env: &[(&str, &str)]) -> (Run, u16) {
    let dir = tmp(tag);
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(names) { std::fs::write(pd.join(n), t).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .args(extra).envs(env.iter().copied()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
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
        assert!(t.elapsed() < Duration::from_secs(90), "timed out waiting for {what}");
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
    assert_eq!(names, ["entity_query", "entity_read", "entity_write", "run_cliff"]);
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
padp = os.path.join(here, "pad")   # a file `pad`: ~200 KB of partial-message lines before the answer (the raw output of a long turn)
if os.path.exists(padp):
    for i in range(200):
        out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": ("pad %d " % i) * 160}}})
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
out({"type": "result", "subtype": "success", "is_error": False, "result": "Final answer to: " + prompt, "num_turns": 3, "duration_ms": 4321, "total_cost_usd": 0.0123, "usage": {"input_tokens": 11, "cache_creation_input_tokens": 22, "cache_read_input_tokens": 33, "output_tokens": 44}, "session_id": "fake-1"})
"#;

const CHAT_PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "chat.c1f", "chat_turn.c1f"];

/// the seat's folder holds the fake; the binary is told its path as a fact
fn fake_seat(tag: &str, extra: &[&str]) -> (Run, u16, std::path::PathBuf) { fake_seat_with(tag, extra, &[]) }
/// the same, with more packs (name, text) in the seat's pack folder: a model's own banner pack, for one
fn fake_seat_with(tag: &str, extra: &[&str], packs: &[(&str, &str)]) -> (Run, u16, std::path::PathBuf) { fake_seat_script(tag, extra, packs, FAKE) }
/// the same, with the fake's own script text
fn fake_seat_script(tag: &str, extra: &[&str], packs: &[(&str, &str)], script: &str) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, script).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(CHAT_PACKS) { std::fs::write(pd.join(n), t).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();   // the barest app's default banner pack
    for (n, t) in packs { std::fs::write(pd.join(n), t).unwrap(); }
    let (run, port) = launch(&dir, &bin, extra);
    (run, port, dir)
}

/// start the binary in `dir` (its packs folder and store are there; a second launch in the same folder is a RESTART)
fn launch(dir: &std::path::Path, bin: &std::path::Path, extra: &[&str]) -> (Run, u16) {
    let pd = dir.join("packs");
    let fact = format!("chat_claude_bin={}", bin.display());
    let mut child = Command::new(BIN).current_dir(dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap(), "--fact", &fact])
        .args(extra).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.to_path_buf() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port)
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

// ---- the chat's event stream (#1143, the SSE slice) ---------------------------------------------------------------------------------------
// A fake that talks in PIECES: it prints stream-json `stream_event` lines (a tool_use block start, then text deltas) one at a time, each
// released by a file `go.<tag>.<i>` the test writes, then the finished assistant message and the result line after `go.<tag>.end`.
const PIECES: &str = r#"#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.argv[-1]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
json.dump({"argv": sys.argv[1:]}, open(os.path.join(here, "argv." + tag + ".json"), "w"))
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def gate(name):
    for _ in range(300):
        if os.path.exists(os.path.join(here, "go." + tag + "." + name)): return
        time.sleep(0.05)
out({"type": "system", "subtype": "init", "session_id": "p-1"})
pieces = ["Hel", "lo ", "wor", "ld"]
for i, t in enumerate(pieces):
    gate(str(i))
    if i == 1:
        out({"type": "stream_event", "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_read", "input": {}}}})
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}})
gate("end")
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello world"}, {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_read", "input": {}}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Hello world", "num_turns": 1, "duration_ms": 99, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "p-1"})
"#;

fn collector() -> String { std::fs::read_to_string(format!("{}/../membrane/apps/barest.c1f/collector.c1f", env!("CARGO_MANIFEST_DIR"))).unwrap() }

/// An open event stream: the status line and headers, then the events as they arrive ((event name, json data)).
struct Sse { r: BufReader<TcpStream>, head: String }
impl Sse {
    fn open(port: u16, path: &str) -> Sse {
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
        s.write_all(format!("GET {path} HTTP/1.1\r\nHost: t\r\nAccept: text/event-stream\r\n\r\n").as_bytes()).unwrap();
        let mut r = BufReader::new(s);
        let mut head = String::new();
        loop { let mut l = String::new(); r.read_line(&mut l).unwrap(); head.push_str(&l); if l == "\r\n" || l.is_empty() { break; } }
        Sse { r, head }
    }
    /// the next event, None at the end of the stream
    fn next(&mut self) -> Option<(String, J)> {
        let (mut name, mut data) = (String::new(), String::new());
        loop {
            let mut l = String::new();
            if self.r.read_line(&mut l).unwrap() == 0 { return None; }
            let l = l.trim_end_matches(['\r', '\n']);
            if l.is_empty() { if data.is_empty() { continue; } return Some((name, serde_json::from_str(&data).unwrap_or_else(|e| panic!("{e}: {data}")))); }
            if let Some(v) = l.strip_prefix("event: ") { name = v.to_string(); }
            if let Some(v) = l.strip_prefix("data: ") { data.push_str(v); }
        }
    }
}

/// the Request entities of event streams the seat still holds (a query by kind, then each one's path)
fn stream_requests(port: u16) -> Vec<String> {
    let (t, _) = call(port, "entity_query", json!({"q": "kind:Request"}));
    t.lines().filter_map(|l| l.split_whitespace().next()).filter(|e| e.starts_with("Request:")).filter(|e| {
        let (p, _) = call(port, "entity_read", json!({"spec": e, "prop": "path"}));
        p.ends_with("/events")
    }).map(String::from).collect()
}

fn release(dir: &std::path::Path, prompt: &str, name: &str) { std::fs::write(dir.join(format!("go.{}.{}", tag_of(prompt), name)), "").unwrap(); }

#[test]
fn the_event_stream_carries_the_answer_as_it_is_written_and_the_request_is_retired_after_it_closes() {
    let (mut run, port, dir) = fake_seat_script("sse", &["--fact", "request_keep_s=0"], &[("collector.c1f", &collector())], PIECES);
    let id = send(port, "stream me");
    let mut sse = Sse::open(port, &format!("/membrane/chat/message/{id}/events"));
    let lh = sse.head.to_ascii_lowercase();
    assert!(sse.head.starts_with("HTTP/1.1 200") && lh.contains("content-type: text/event-stream") && lh.contains("cache-control: no-store") && !lh.contains("content-length"), "{}", sse.head);
    // the first event is the state as it is (running or still queued), the answer not yet there
    let (name, first) = sse.next().expect("a first event");
    assert_eq!(name, "message");
    assert_eq!(first["id"], json!(id));
    assert!(first["response"].is_null(), "{first}");
    // each piece comes as an event BEFORE the turn ends: the partial answer grows, the tool name shows when its block starts
    let mut texts: Vec<String> = vec![];
    let mut tools_seen = false;
    for (i, want) in ["Hel", "Hello ", "Hello wor", "Hello world"].iter().enumerate() {
        wait("the fake's argv file", || dir.join(format!("argv.{}.json", tag_of("stream me"))).exists().then_some(()));
        release(&dir, "stream me", &i.to_string());
        loop {
            let (n, e) = sse.next().expect("an event before the end");
            assert_eq!(n, "message");
            assert_eq!(e["state"], json!("running"), "the turn is still running while pieces arrive: {e}");
            if e["tools"] == json!(["entity_read"]) { tools_seen = true; }
            if e["response"].as_str() == Some(want) { texts.push(want.to_string()); break; }
        }
        assert_eq!(view(port, &id)["state"], "running", "the event arrived before the turn ended");
    }
    assert_eq!(texts, ["Hel", "Hello ", "Hello wor", "Hello world"]);
    assert!(tools_seen, "the tool the model started shows in the events");
    // while the stream is open its Request is held (not retired, not dropped); request_keep_s=0 retires every other request at once
    let held = stream_requests(port);
    assert_eq!(held.len(), 1, "{held:?}");
    let (b, _) = call(port, "entity_read", json!({"spec": held[0], "prop": "dropped"}));
    assert!(!b.contains("true"), "an open stream is not dropped: {b}");
    // the end: the final event is the answer, `done`, then the connection closes
    release(&dir, "stream me", "end");
    let (n, last) = sse.next().expect("the final event");
    assert_eq!((n.as_str(), last["state"].clone(), last["response"].clone()), ("message", json!("done"), json!("Hello world")));
    assert!(sse.next().is_none(), "the stream closes after done");
    wait("the stream's Request retired", || stream_requests(port).is_empty().then_some(()));
    // the turn's partial chunks are retired with the finish (the receipt is the answer)
    wait("the chunks retired", || { let (n, _) = call(port, "entity_query", json!({"q": "kind:Chunk", "count": true})); (n == "0 entity(ies)").then_some(()) });
    // the poll route is still there
    assert_eq!(view(port, &id)["response"], "Hello world");
    assert!(terminate(&mut run));
}

#[test]
fn an_unknown_message_has_no_stream_and_a_finished_one_streams_its_answer_and_closes() {
    let (mut run, port, dir) = fake_seat_script("ssedone", &[], &[], PIECES);
    let (st, body) = http(port, "GET", "/membrane/chat/message/nope/events", "");
    assert_eq!(st, 404, "{body}");
    let id = send(port, "already done");
    wait("the fake's argv file", || dir.join(format!("argv.{}.json", tag_of("already done"))).exists().then_some(()));
    for i in 0..4 { release(&dir, "already done", &i.to_string()); }
    release(&dir, "already done", "end");
    wait("done", || (view(port, &id)["state"] == "done").then_some(()));
    let mut sse = Sse::open(port, &format!("/membrane/chat/message/{id}/events"));
    let (_, e) = sse.next().expect("the snapshot");
    assert_eq!((e["state"].clone(), e["response"].clone()), (json!("done"), json!("Hello world")));
    assert!(sse.next().is_none(), "closed at once");
    assert!(terminate(&mut run));
}

#[test]
fn a_client_that_leaves_early_leaves_nothing_stuck_and_the_turn_completes() {
    let (mut run, port, dir) = fake_seat_script("sseleave", &["--fact", "request_keep_s=0"], &[("collector.c1f", &collector())], PIECES);
    let id = send(port, "walk away");
    let mut sse = Sse::open(port, &format!("/membrane/chat/message/{id}/events"));
    sse.next().expect("a first event");
    wait("the fake's argv file", || dir.join(format!("argv.{}.json", tag_of("walk away"))).exists().then_some(()));
    release(&dir, "walk away", "0");
    let (_, e) = sse.next().expect("a piece");
    assert_eq!(e["state"], json!("running"));
    drop(sse);                                           // the page was closed mid-turn
    for i in 1..4 { release(&dir, "walk away", &i.to_string()); }
    release(&dir, "walk away", "end");
    let v = wait("the turn's end", || { let v = view(port, &id); (v["state"] == "done").then_some(v) });
    assert_eq!(v["response"], "Hello world", "the turn finished though nobody listened");
    wait("no stream Request left", || stream_requests(port).is_empty().then_some(()));
    assert!(terminate(&mut run));
}

// ---- resource hygiene (#1143): a finished turn keeps what matters, not the raw output ------------------------------------------------------
/// the whole stored row of an entity as the door reads it (its length is the row's size)
fn row_text(port: u16, eid: &str) -> String { call(port, "entity_read", json!({"spec": eid})).0 }
/// the stored bodies of the seat's store file: (rows, bytes of c_field, journal lines, bytes of c_journal)
fn store_sizes(dir: &std::path::Path) -> (i64, i64, i64, i64) {
    let db = rusqlite::Connection::open_with_flags(dir.join("field.sqlite3"), rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let q = |sql: &str| -> (i64, i64) { db.query_row(sql, [], |r| Ok((r.get(0)?, r.get::<_, Option<i64>>(1)?.unwrap_or(0)))).unwrap() };
    let (a, b) = q("SELECT count(*), sum(length(body)) FROM c_field WHERE k NOT LIKE 'Request:%'");   // a door request is the collector's (apps/barest.c1f/collector.c1f)
    let (c, d) = q("SELECT count(*), sum(length(body)) FROM c_journal");
    (a, b, c, d)
}
/// THE BOUND (a threshold, flagged for the operator): a finished turn's Command row and message row are each under 16 KB. What stays is the
/// command (argv: the system prompt with the banner), the answer's numbers and the small derived values; the 200 KB the CLI printed is gone.
const ROW_BOUND: usize = 16 * 1024;

#[test]
fn a_finished_turn_keeps_its_answer_tools_and_usage_but_not_the_raw_output_and_survives_a_restart() {
    let (mut run, port, dir) = fake_seat("hygiene", &[]);
    std::fs::write(dir.join("pad"), "").unwrap();
    let before = store_sizes(&dir);
    let id = send(port, "big output");
    let t0 = Instant::now();
    let v = finish(port, &dir, &id, "big output");
    eprintln!("200 KB turn, 200 chunks: released to done in {:?}", t0.elapsed());
    assert!(t0.elapsed() < Duration::from_secs(30), "each chunk is parsed once, not again with every later chunk: {:?}", t0.elapsed());
    assert_eq!(v["state"], "done", "{v}");
    let (cmd, msg) = (format!("membrane::Command:chat:{id}"), format!("membrane::Chat:message:{id}"));
    // the rows shrink after the turn (a later round than `done`): wait for both
    let (c, m) = wait("the finished turn's rows are small", || {
        let (c, m) = (row_text(port, &cmd), row_text(port, &msg));
        (c.len() < ROW_BOUND && m.len() < ROW_BOUND).then_some((c, m))
    });
    assert!(!c.contains("pad 199") && !m.contains("pad 199"), "the raw stream is not kept");
    assert!(c.contains("\"rc\":0") || c.contains("\"rc\": 0"), "the rc stays: {c}");
    // what matters is in the view, the tool entities and the turn
    let check = |port: u16| {
        let v = view(port, &id);
        assert_eq!((v["state"].clone(), v["response"].clone(), v["tools"].clone()), (json!("done"), json!("Final answer to: big output"), json!(["entity_write", "entity_read"])), "{v}");
        let (n, _) = call(port, "entity_read", json!({"spec": format!("{msg}:turn:tool:tu1"), "prop": "name"}));
        assert_eq!(n, "mcp__membrane__entity_write");
        let (r, _) = call(port, "entity_read", json!({"spec": format!("{msg}:turn:tool:tu2"), "prop": "result"}));
        assert!(r.contains("echo"), "the tool result is kept: {r}");
    };
    check(port);
    // the journal: numbers for the report (the Chunk births carry the raw lines; the receipt is not journalled)
    let after = store_sizes(&dir);
    eprintln!("hygiene numbers: c_field rows {} -> {} ({} -> {} bytes); journal lines {} -> {} ({} -> {} bytes)", before.0, after.0, before.1, after.1, before.2, after.2, before.3, after.3);
    assert!(after.1 - before.1 < 200 * 1024, "the field grew by a small part of the 200 KB output: {} bytes", after.1 - before.1);
    assert!(terminate(&mut run));
    let (mut run2, port2) = launch(&dir, &dir.join("fake-claude"), &[]);
    check(port2);
    assert!(row_text(port2, &cmd).len() < ROW_BOUND && row_text(port2, &msg).len() < ROW_BOUND, "small after the restart too");
    assert!(terminate(&mut run2));
}

// ---- the return entity and parallel effects (#1143) -------------------------------------------------------------------------------------------
fn put(port: u16, spec: &str, prop: &str, v: J) -> (String, bool) { call(port, "entity_write", json!({"spec": spec, "prop": prop, "value": v})) }
fn put_wait(port: u16, spec: &str, prop: &str, v: J, wait: f64) -> (String, bool) { call(port, "entity_write", json!({"spec": spec, "prop": prop, "value": v, "wait": wait})) }

#[test]
fn a_write_that_starts_an_effect_returns_its_receipt_when_it_waits_and_names_it_when_it_does_not() {
    let (mut run, port) = start("ret", MCP_PACKS, &[]);
    put(port, "Command:e1", "cmd", json!(["echo", "hi"]));
    // wait: the reply holds until the receipt: stdout, stderr, rc, ok, and the Return entity that keeps them
    let (t, e) = put_wait(port, "Command:e1", "run", json!({"id": "r1", "args": ["there"]}), 20.0);
    assert!(!e, "{t}");
    assert!(t.starts_with("wrote Command:e1.run\nreceipt: "), "{t}");
    let line = t.lines().find(|l| l.starts_with("receipt: ")).unwrap();
    let rc: J = serde_json::from_str(&line["receipt: ".len()..]).unwrap_or_else(|e| panic!("{e}: {line}"));
    assert_eq!((rc["rc"].clone(), rc["ok"].clone(), reply_part(&t, "stdout").unwrap()["stdout"].as_str().unwrap().trim().to_string()), (json!(0), json!(true), "hi there".to_string()), "{t}");
    assert_eq!(rc["stderr_bytes"], json!(0));
    assert!(!t.contains("Return:"), "the Return entity is a carrier, the reply does not name it: {t}");
    let (r, _) = call(port, "entity_read", json!({"spec": "Command:e1", "prop": "run_result"}));
    assert!(r.contains("hi there"), "the receipt stays on the entity: {r}");
    // no wait: a slow command answers at once, naming the receipt; the Return entity shows it when it lands
    put(port, "Command:slow", "cmd", json!(["sh", "-c", "sleep 2; echo late"]));
    let t0 = Instant::now();
    let (t, _) = put(port, "Command:slow", "run", json!({"id": "s1", "args": []}));
    assert!(t0.elapsed() < Duration::from_millis(1800), "no wait, no holding: {:?}", t0.elapsed());
    assert!(t.contains("status: running") && t.contains("read Command:slow.run_result later"), "{t}");
    assert!(!t.contains("Return:"), "{t}");
    let r = wait("the late receipt on the entity", || { let (r, _) = call(port, "entity_read", json!({"spec": "Command:slow", "prop": "run_result"})); r.contains("late").then_some(r) });
    assert!(r.contains("late"), "{r}");
    // a wait that is too short answers pending
    put(port, "Command:slow2", "cmd", json!(["sh", "-c", "sleep 4; echo x"]));
    let (t, _) = put_wait(port, "Command:slow2", "run", json!({"id": "s2", "args": []}), 1.0);
    assert!(t.contains("status: running") && t.contains("read Command:slow2.run_result later"), "{t}");
    // a plain write has no receipt to wait for
    let (t, _) = put_wait(port, "Note:a", "n", json!(1), 30.0);
    assert!(t.starts_with("wrote Note:a.n\nreactions: ") && !t.contains("pending"), "{t}");
    assert!(terminate(&mut run));
}

const BOXES: &str = "PACK boxes 1\nBox.double getter: this.n * 2\nBox.n on-setter mirror:\n  ALWAYS:\n    this.last = this.n\nBox.gone getter: None('nothing there yet') if this.n == None else this.n\n";

#[test]
fn a_write_returns_the_reactions_of_its_round_with_the_reason_of_a_null() {
    let (mut run, port) = start("react", MCP_PACKS, &[]);
    let (r, e) = put(port, "Pack:boxes", "text", json!(BOXES));
    assert!(!e, "{r}");
    wait("the pack", || (call(port, "entity_read", json!({"spec": "Pack:boxes", "prop": "state"})).0 == "loaded").then_some(()));
    let (t, e) = put(port, "Box:1", "n", json!(4));
    assert!(!e, "{t}");
    let line = t.lines().find_map(|l| l.strip_prefix("reactions: ")).unwrap_or_else(|| panic!("{t}"));
    let rs: Vec<J> = serde_json::from_str(line).unwrap_or_else(|e| panic!("{e}: {line}"));
    let find = |p: &str| rs.iter().find(|r| r["eid"] == "Box:1" && r["prop"] == p).cloned().unwrap_or_else(|| panic!("{p} in {rs:?}"));
    assert_eq!(find("double")["new"], json!(8), "a getter that recomputed");
    assert_eq!(find("last")["new"], json!(4), "a setter that fired");
    assert!(find("last")["by"].as_str().unwrap().contains("mirror"), "{rs:?}");
    // a null carries its reason
    let (t, _) = put(port, "Box:2", "other", json!(1));
    let rs: Vec<J> = serde_json::from_str(t.lines().find_map(|l| l.strip_prefix("reactions: ")).unwrap()).unwrap();
    let g = rs.iter().find(|r| r["eid"] == "Box:2" && r["prop"] == "gone").unwrap_or_else(|| panic!("gone in {rs:?}"));
    assert_eq!(g["why"], json!("nothing there yet"), "{rs:?}");
    assert!(terminate(&mut run));
}

/// start `n` slow commands (each marks `+` when it starts and `-` when it ends in the file `ev`), in parallel tool calls
fn slow_commands(port: u16, ev: &std::path::Path, n: usize, secs: u32, wait: f64) -> Vec<std::thread::JoinHandle<String>> {
    for i in 0..n { put(port, &format!("Command:p{i}"), "cmd", json!(["sh", "-c", format!("echo + >> {p}; sleep {secs}; echo - >> {p}; echo done{i}", p = ev.display())])); }
    (0..n).map(|i| std::thread::spawn(move || put_wait(port, &format!("Command:p{i}"), "run", json!({"id": format!("p{i}"), "args": []}), wait).0)).collect()
}

#[test]
fn two_slow_commands_from_parallel_tool_calls_take_the_time_of_one_and_a_held_call_blocks_nothing() {
    let (mut run, port) = start("par", MCP_PACKS, &[]);
    let t0 = Instant::now();
    let hs = slow_commands(port, &run.dir.join("ev"), 2, 3, 30.0);
    std::thread::sleep(Duration::from_millis(700));
    // while both are held: the door answers other calls at once, and the page's files
    let q = Instant::now();
    let (n, _) = call(port, "entity_query", json!({"q": "kind:Command", "count": true}));
    assert!(n.contains("entity(ies)") && q.elapsed() < Duration::from_millis(2500), "a held call blocks nothing: {:?}", q.elapsed());
    for h in hs { let t = h.join().unwrap(); assert!(t.contains("receipt: ") && t.contains("done"), "{t}"); }
    let took = t0.elapsed();
    assert!(took < Duration::from_millis(5500), "two 3 s commands in parallel took {took:?} (serial is 6 s or more)");
    assert!(terminate(&mut run));
}

#[test]
fn the_effect_workers_fact_caps_the_commands_that_run_at_once() {
    let (mut run, port) = start("cap", MCP_PACKS, &["--fact", "effect_workers=2"]);
    let dir = run.dir.clone();
    let hs = slow_commands(port, &dir.join("ev"), 5, 1, 60.0);
    for h in hs { let t = h.join().unwrap(); assert!(t.contains("receipt: "), "{t}"); }
    let ev = std::fs::read_to_string(dir.join("ev")).unwrap_or_else(|e| panic!("{e}: {:?}", std::fs::read_dir(&dir).unwrap().filter_map(Result::ok).map(|f| f.file_name()).collect::<Vec<_>>()));
    let _ev0 = std::fs::read_to_string(dir.join("ev")).unwrap();
    let (mut now, mut peak) = (0i32, 0i32);
    for c in ev.lines() { now += if c == "+" { 1 } else { -1 }; peak = peak.max(now); }
    assert_eq!(ev.lines().count(), 10, "{ev}");
    assert_eq!(peak, 2, "five commands with the cap 2 ran at most two at once, and two did run together: {ev:?}");
    assert!(terminate(&mut run));
}

// ---- the turn carries the usage of the result event (#1143) --------------------------------------------------------------------------------------
#[test]
fn the_turn_carries_usage_cost_calls_and_duration_of_the_result_event_in_the_view_and_the_streams_last_event() {
    let (mut run, port, dir) = fake_seat("usage", &[]);
    let id = send(port, "count me");
    let v = finish(port, &dir, &id, "count me");
    assert_eq!(v["state"], "done", "{v}");
    let want = json!({"input_tokens": 11, "cache_creation_input_tokens": 22, "cache_read_input_tokens": 33, "output_tokens": 44});
    assert_eq!(v["turn"]["usage"], want, "{v}");
    assert_eq!((v["turn"]["cost"].clone(), v["turn"]["calls"].clone(), v["turn"]["duration"].clone()), (json!(0.0123), json!(3), json!(4321)), "{v}");
    // the turn entity holds them as properties
    let (u, _) = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn"), "prop": "cost"}));
    assert_eq!(u, "0.0123");
    // the event stream's final event carries them too
    let mut sse = Sse::open(port, &format!("/membrane/chat/message/{id}/events"));
    let (_, e) = sse.next().expect("the snapshot");
    assert_eq!(e["turn"]["usage"], want, "{e}");
    assert!(terminate(&mut run));
}

#[test]
fn a_turn_that_was_running_when_the_seat_stopped_is_an_error_after_the_restart() {
    let (mut run, port, dir) = fake_seat("interrupted", &[]);
    let id = send(port, "stopped midway");
    wait("running", || (view(port, &id)["state"] == "running").then_some(()));
    assert!(terminate(&mut run));
    let (mut run2, port2) = launch(&dir, &dir.join("fake-claude"), &[]);
    let v = wait("the turn marked error", || { let v = view(port2, &id); (v["state"] == "error").then_some(v) });
    assert_eq!(v["response"], "interrupted: the seat stopped while this turn was running", "{v}");
    // a new message is answered as usual
    let id2 = send(port2, "after the restart");
    assert_eq!(finish(port2, &dir, &id2, "after the restart")["state"], "done");
    assert_eq!(view(port2, &id)["state"], "error", "the interrupted turn stays an error");
    assert!(terminate(&mut run2));
}

fn returns(port: u16) -> String { call(port, "entity_query", json!({"q": "kind:Return", "count": true})).0 }

#[test]
fn the_return_entity_is_retired_as_soon_as_its_reply_is_delivered() {
    let (mut run, port) = start("retret", MCP_PACKS, &[]);
    assert_eq!(returns(port), "0 entity(ies)");
    for i in 0..20 { let (t, e) = put(port, &format!("Note:n{i}"), "v", json!(i)); assert!(!e && t.starts_with("wrote Note:"), "{t}"); }
    wait("no Return left after 20 writes", || (returns(port) == "0 entity(ies)").then_some(()));
    // a held call: while it waits its carrier is not made yet; after it answers none is left
    put(port, "Command:w1", "cmd", json!(["sh", "-c", "sleep 1; echo w"]));
    let (t, _) = put_wait(port, "Command:w1", "run", json!({"id": "w1", "args": []}), 20.0);
    assert!(t.contains("receipt: "), "{t}");
    wait("no Return left after a waiting call", || (returns(port) == "0 entity(ies)").then_some(()));
    // the receipt is where it always lived
    assert!(call(port, "entity_read", json!({"spec": "Command:w1", "prop": "run_result"})).0.contains("\"rc\":0"));
    assert!(terminate(&mut run));
}

// ---- run_cliff: a clifflet in one call (#1143) ------------------------------------------------------------------------------------------------
fn cliff(port: u16, code: &str) -> (String, bool) { call(port, "run_cliff", json!({"code": code})) }
/// the entities that are not door requests (the collector retires those; this seat has none)
fn count_all(port: u16) -> String { call(port, "entity_query", json!({"q": "kind!:Request", "count": true})).0 }

#[test]
fn a_clifflet_reads_and_joins_several_entities_in_one_call_and_its_errors_say_why() {
    let (mut run, port) = start("cliff1", MCP_PACKS, &[]);
    for (n, v) in [("a", 3), ("b", 4), ("c", 5)] { put(port, &format!("Note:{n}"), "n", json!(v)); }
    put(port, "Tag:x", "of", json!("Note:b"));
    let (t, e) = cliff(port, "ns = [e.n for e in instances('Note')]\nbig = [e.eid for e in instances('Note') if e.n > 3]\n{'count': len(ns), 'sum_big': len(big), 'big': big, 'tagged': [prop(prop(g.of, 'eid'), 'n') for g in instances('Tag')]}");
    assert!(!e, "{t}");
    let v: J = serde_json::from_str(&t).unwrap_or_else(|e| panic!("{e}: {t}"));
    assert_eq!((v["count"].clone(), v["big"].clone()), (json!(3), json!(["Note:b", "Note:c"])), "{t}");
    // a one-line value, a plain number, a null
    assert_eq!(cliff(port, "1 + 2"), ("3".to_string(), false));
    assert_eq!(cliff(port, "None").0, "null");
    // the error shapes: a block that does not load says the loader's words, a null with a reason says it, an empty block says what is needed
    let (t, e) = cliff(port, "[x for x in");
    assert!(e && t.contains("the block did not load") && t.contains("not an expression"), "{t}");
    let (t, e) = cliff(port, "None('nothing here yet')");
    assert!(e && t == "error: nothing here yet", "{t}");
    let (t, e) = cliff(port, "1 / 0");
    assert!(e && t.starts_with("error: "), "{t}");
    let (t, e) = cliff(port, "  ");
    assert!(e && t.contains("run_cliff needs code"), "{t}");
    assert!(terminate(&mut run));
}

#[test]
fn a_clifflet_that_writes_lands_its_writes_and_returns_their_reactions() {
    let (mut run, port) = start("cliff2", MCP_PACKS, &[]);
    let (t, e) = cliff(port, "q = 5\nNote:w1.n = q\nNote:w2.n = q + 1");
    assert!(!e && t.contains("ran the writes") && t.contains("reactions: ") && t.contains("read <entity>.<verb>_result"), "{t}");
    assert_eq!(call(port, "entity_read", json!({"spec": "Note:w1", "prop": "n"})).0, "5");
    assert_eq!(call(port, "entity_read", json!({"spec": "Note:w2", "prop": "n"})).0, "6");
    assert!(t.contains("Note:w2"), "the reactions list the writes the block made: {t}");
    // an effect: the verb runs, the receipt arrives in a later round on the entity
    let (t, e) = cliff(port, "Command:cc.cmd = ['echo', 'from-cliff']\nCommand:cc.run = {'id': 'c1', 'args': []}");
    assert!(!e, "{t}");
    let r = wait("the receipt", || { let (r, _) = call(port, "entity_read", json!({"spec": "Command:cc", "prop": "run_result"})); r.contains("from-cliff").then_some(r) });
    assert!(r.contains("from-cliff"), "{r}");
    assert!(terminate(&mut run));
}

#[test]
fn a_runaway_clifflet_stops_on_the_budget_and_the_seat_stays_up_and_nothing_accumulates() {
    let (mut run, port) = start_env("cliff3", MCP_PACKS, &[], &[("MEMBRANE_EVAL_BUDGET", "300")]);
    for i in 0..14 { put(port, &format!("Note:r{i}"), "n", json!(i)); }
    let (t, e) = cliff(port, "[e.n for e in instances('Note') if e.n > 0 and len([1 for f in instances('Note')]) > 0]");
    assert!(e && t.contains("budget exceeded"), "{t}");
    assert_eq!(call(port, "entity_read", json!({"spec": "Note:r3", "prop": "n"})).0, "3", "the seat is up");
    // scratch entities and packs do not accumulate: the same count after 20 calls of every shape
    wait("the first scratch is gone", || (call(port, "entity_query", json!({"q": "name~:Cliff", "count": true})).0 == "0 entity(ies)" && call(port, "entity_query", json!({"q": "kind:Pack", "count": true})).0 == "1 entity(ies)").then_some(()));
    cliff(port, "Note:acc.n = 0");
    wait("settled", || (call(port, "entity_query", json!({"q": "name~:Cliff", "count": true})).0 == "0 entity(ies)").then_some(()));
    let before = count_all(port);
    for i in 0..20 {
        match i % 4 { 0 => cliff(port, "1 + 1"), 1 => cliff(port, "[x for x in"), 2 => cliff(port, "Note:acc.n = 1"), _ => cliff(port, "None('x')") };
    }
    let t0 = Instant::now();
    let (mut a, mut b, mut c) = (String::new(), String::new(), String::new());
    while t0.elapsed() < Duration::from_secs(30) {
        (a, b, c) = (count_all(port), call(port, "entity_query", json!({"q": "name~:Cliff", "count": true})).0, call(port, "entity_query", json!({"q": "kind:Pack", "count": true})).0);
        if a == before && b == "0 entity(ies)" && c == "1 entity(ies)" { break; }
        std::thread::sleep(Duration::from_millis(300));
    }
    assert_eq!((a, b, c), (before, "0 entity(ies)".to_string(), "1 entity(ies)".to_string()), "entities, scratch, packs after 20 calls");
    assert!(terminate(&mut run));
}

#[test]
fn a_command_run_again_with_the_same_id_runs_and_its_receipt_is_new_by_its_timestamps() {
    let (mut run, port) = start("rerun", MCP_PACKS, &[]);
    let log = run.dir.join("ticks");
    put(port, "Command:tick", "cmd", json!(["sh", "-c", format!("echo x >> {}", log.display())]));
    let n = || std::fs::read_to_string(&log).map(|t| t.lines().count()).unwrap_or(0);
    let (t, _) = put_wait(port, "Command:tick", "run", json!({"id": "t1", "args": []}), 20.0);
    assert!(t.contains("receipt: "), "{t}");
    // the same id and the same output: it runs again, and its receipt differs by `started` (the reply recognises it)
    let (t, _) = put_wait(port, "Command:tick", "run", json!({"id": "t1", "args": []}), 20.0);
    assert!(t.contains("receipt: "), "{t}");
    assert_eq!(n(), 2);
    // a new id: a new receipt, the reply carries it
    let (t, _) = put_wait(port, "Command:tick", "run", json!({"id": "t2", "args": []}), 20.0);
    assert!(t.contains("receipt: ") && t.contains("\"id\":\"t2\""), "{t}");
    assert_eq!(n(), 3);
    assert!(terminate(&mut run));
}

#[test]
fn a_banner_change_returns_reactions_that_do_not_grow_with_the_finished_messages() {
    let pack = "PACK mine 1\nIMPORT default_banner 1\nBanner.text getter: 'n=' + str(prop('Note:x', 'n'))\n";
    let (mut run, port, _dir) = fake_seat_with("reactfinished", &[], &[("mine.c1f", pack)]);
    put(port, "Note:x", "n", json!(0));
    let size = |port: u16, n: i64| -> (usize, String) { let (t, e) = put(port, "Note:x", "n", json!(n)); assert!(!e, "{t}"); (t.len(), t) };
    let (small, _) = size(port, 1);
    // 50 FINISHED messages, as the chat makes them (class Chat, kind in parents, the state on the :turn entity)
    for i in 0..50 {
        let (t, e) = call(port, "run_cliff", json!({"code": format!("membrane::Chat:message[{i}] = ChatMessage(text: 'q{i}')\nmembrane::Chat:message[{i}]:turn = ChatTurn(message: 'membrane::Chat:message:{i}')\nmembrane::Chat:message[{i}]:turn.state = 'done'")}));
        assert!(!e, "{t}");
    }
    let (n50, t) = size(port, 2);
    eprintln!("reactions reply: {small} bytes with no finished message, {n50} bytes with 50");
    assert!(!t.contains("membrane::Chat:message:"), "no finished message is in the reactions: {}", &t[..t.len().min(600)]);
    assert!(n50 < small + 1500, "the reply does not grow with the finished messages: {small} -> {n50}");
    assert!(terminate(&mut run));
}

#[test]
fn a_clifflet_reads_eid_cls_and_parents_and_lists_the_same_packs_as_entity_query() {
    let (mut run, port) = start("cliffpacks", MCP_PACKS, &[]);
    put(port, "Pack:boxes", "text", json!(BOXES));
    wait("the pack", || (call(port, "entity_read", json!({"spec": "Pack:boxes", "prop": "state"})).0 == "loaded").then_some(()));
    put(port, "Box:1", "n", json!(4));
    put(port, "Box:1", "parents", json!(["cls:box", "cls:thing"]));
    // eid, cls and parents are readable on any entity (parents is the entity's class list when it has one)
    let (t, e) = cliff(port, "b = instances('Box')[0]\n[b.eid, b.cls, b.parents]");
    assert!(!e, "{t}");
    let v: J = serde_json::from_str(&t).unwrap_or_else(|e| panic!("{e}: {t}"));
    assert_eq!((v[0].clone(), v[1].clone()), (json!("Box:1"), json!("Box")), "{t}");
    assert!(v[2].is_array() && v[2][0] == json!("cls:box"), "parents is the class list: {t}");
    // the packs a clifflet lists are the ones entity_query lists (its own scratch packs are not among them)
    let (q, _) = call(port, "entity_query", json!({"q": "kind:Pack", "k": 100}));
    let (t, e) = cliff(port, "[[p.eid, p.state] for p in instances('Pack')]");
    assert!(!e, "{t}");
    let listed: Vec<String> = serde_json::from_str::<Vec<J>>(&t).unwrap_or_else(|e| panic!("{e}: {t}")).iter().map(|r| r[0].as_str().unwrap().to_string()).collect();
    assert!(listed.iter().all(|p| !p.contains("cliffg") && !p.contains("cliffs")), "the clifflet's scratch packs are listed: {listed:?}");
    for p in &listed { assert!(q.contains(p.as_str()), "{p} is not in entity_query: {q}"); }
    assert_eq!(listed.len(), q.matches("Pack:").count(), "the same set: {listed:?} vs {q}");
    assert!(terminate(&mut run));
}

// ---- the reply of a write that ran a command: size, timing, noise, one write (#1143, the model's findings) -----------------------------------------
/// the lines of a reply: `receipt: {json}`, `stdout: {json}`, `stderr: {json}` as parsed objects by their label
fn reply_part(t: &str, label: &str) -> Option<J> {
    t.lines().find_map(|l| l.strip_prefix(&format!("{label}: "))).map(|j| serde_json::from_str(j).unwrap_or_else(|e| panic!("{label}: {e}: {j}")))
}

#[test]
fn a_big_output_comes_back_as_its_first_and_last_8_kb_with_its_size_and_the_full_text_stays_on_the_entity() {
    let (mut run, port) = start("big", MCP_PACKS, &[]);
    put(port, "Command:big", "cmd", json!(["sh", "-c", "printf HEAD; head -c 50000 /dev/zero | tr '\\0' x; printf TAIL; printf oops >&2"]));
    let (t, e) = put_wait(port, "Command:big", "run", json!({"id": "b1", "args": []}), 20.0);
    assert!(!e, "{t}");
    assert!(t.len() < 20000, "the reply is small ({} bytes): the CLI refuses a tool result over its limit", t.len());
    let rc = reply_part(&t, "receipt").unwrap_or_else(|| panic!("{t}"));
    assert_eq!((rc["stdout_bytes"].clone(), rc["stderr_bytes"].clone()), (json!(50008), json!(4)), "{t}");
    assert!(rc.get("head").is_none() && rc.get("stdout").is_none(), "no duplicate of the output in the receipt line: {t}");
    let so = reply_part(&t, "stdout").unwrap_or_else(|| panic!("{t}"));
    let (h, tl) = (so["stdout_head"].as_str().unwrap_or_else(|| panic!("{}", &t[..600.min(t.len())])), so["stdout_tail"].as_str().unwrap_or_else(|| panic!("tail {}", &t[..600.min(t.len())])));
    assert!(h.starts_with("HEAD") && h.chars().count() == 8192 && tl.ends_with("TAIL") && tl.chars().count() == 8192, "{} {}", h.len(), tl.len());
    assert!(so.get("stdout").is_none(), "{so}");
    // a short stream comes whole, under its plain name
    assert_eq!(reply_part(&t, "stderr").unwrap()["stderr"], json!("oops"), "{t}");
    assert!(t.contains("full output: read Command:big.output"), "{t}");
    let (full, _) = call(port, "entity_read", json!({"spec": "Command:big", "prop": "output"}));
    assert!(full.len() >= 50008 && full.starts_with("HEAD") && full.ends_with("TAIL"), "the full text stays on the entity: {}", full.len());
    // an output of exactly 16 KB still comes whole
    put(port, "Command:edge", "cmd", json!(["sh", "-c", "head -c 16384 /dev/zero | tr '\\0' y"]));
    let (t, _) = put_wait(port, "Command:edge", "run", json!({"id": "e1", "args": []}), 20.0);
    assert!(reply_part(&t, "stdout").unwrap()["stdout"].as_str().unwrap().len() == 16384 && !t.contains("full output"), "{}", t.len());
    assert!(terminate(&mut run));
}

#[test]
fn the_receipt_in_the_reply_echoes_the_run_id() {
    let (mut run, port) = start("rid", MCP_PACKS, &[]);
    put(port, "Command:i", "cmd", json!(["echo", "x"]));
    let (t, _) = put_wait(port, "Command:i", "run", json!({"id": "my-run-7", "args": []}), 20.0);
    assert_eq!(reply_part(&t, "receipt").unwrap()["id"], json!("my-run-7"), "{t}");
    assert!(terminate(&mut run));
}

#[test]
fn the_receipt_carries_started_finished_and_ms_so_a_rerun_with_the_same_output_is_new() {
    let (mut run, port) = start("tim", MCP_PACKS, &[]);
    put(port, "Command:t", "cmd", json!(["sh", "-c", "sleep 0.4; echo same"]));
    let now = || std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    let t0 = now();
    let (a, _) = put_wait(port, "Command:t", "run", json!({"id": "same", "args": []}), 20.0);
    let t1 = now();
    let ra = reply_part(&a, "receipt").unwrap_or_else(|| panic!("{a}"));
    let (st, fin, ms) = (ra["started"].as_f64().unwrap_or_else(|| panic!("{a}")), ra["finished"].as_f64().unwrap(), ra["ms"].as_f64().unwrap());
    assert!(st >= t0 - 0.01 && fin <= t1 + 0.01 && st < fin, "{st} {fin} within {t0}..{t1}");
    assert!(ms >= 350.0 && ms < 3000.0 && ((fin - st) * 1000.0 - ms).abs() < 5.0, "ms {ms} vs {st}..{fin}");
    // the SAME id and the same output again: still a new receipt (the timestamps differ), never `pending`
    let (b, _) = put_wait(port, "Command:t", "run", json!({"id": "same", "args": []}), 20.0);
    let rb = reply_part(&b, "receipt").unwrap_or_else(|| panic!("a rerun with the same id and output answers its receipt: {b}"));
    assert!(rb["started"].as_f64().unwrap() > fin - 0.01, "{ra} {rb}");
    assert!(terminate(&mut run));
}

#[test]
fn a_running_command_reads_as_running_with_its_id_and_start_and_a_pending_reply_says_so() {
    let (mut run, port) = start("runn", MCP_PACKS, &[]);
    put(port, "Command:r", "cmd", json!(["sh", "-c", "sleep 2; echo late"]));
    // never started: no status
    let (s, _) = call(port, "entity_read", json!({"spec": "Command:r", "prop": "status"}));
    assert!(s != "running", "a command that was never run is not running: {s}");
    let (t, _) = put(port, "Command:r", "run", json!({"id": "r9", "args": []}));
    assert!(t.contains("pending: ") && t.contains("r9") && t.contains("status: running") && t.contains("started: "), "{t}");
    assert!(t.contains("read Command:r.run_result"), "{t}");
    let (s, _) = call(port, "entity_read", json!({"spec": "Command:r", "prop": "status"}));
    assert_eq!(s, "running");
    let (s, _) = call(port, "entity_read", json!({"spec": "Command:r", "prop": "running"}));
    assert!(s.contains("r9") && s.contains("started"), "{s}");
    wait("the receipt", || (call(port, "entity_read", json!({"spec": "Command:r", "prop": "status"})).0 == "ok").then_some(()));
    // a rerun reads running again until its own receipt lands
    let (_, _) = put(port, "Command:r", "run", json!({"id": "r9", "args": []}));
    let (s, _) = call(port, "entity_read", json!({"spec": "Command:r", "prop": "status"}));
    assert_eq!(s, "running", "the old receipt is not the new run's");
    assert!(terminate(&mut run));
}

#[test]
fn reactions_list_only_real_changes_not_null_to_null_rebuilds_or_the_consumed_verb() {
    let (mut run, port) = start("noise", MCP_PACKS, &[]);
    let reactions = |t: &str| -> Vec<J> { serde_json::from_str(t.lines().find_map(|l| l.strip_prefix("reactions: ")).unwrap_or_else(|| panic!("{t}"))).unwrap() };
    // the live shape: a new Command's cmd, then its run
    let (t, _) = put(port, "Command:q", "cmd", json!(["echo", "hi"]));
    let rs = reactions(&t);
    assert!(rs.iter().all(|r| !(r["new"].is_null() && r["by"] == "rebuild")), "no null-to-null rebuild reactions: {t}");
    let (t, _) = put_wait(port, "Command:q", "run", json!({"id": "q1", "args": []}), 20.0);
    let rs = reactions(&t);
    assert!(rs.iter().all(|r| !r["by"].as_str().unwrap().contains("consumed")), "the consumed verb is not a reaction: {t}");
    assert!(rs.iter().any(|r| r["prop"] == "running"), "real changes stay: {t}");
    assert!(terminate(&mut run));
}

#[test]
fn a_run_value_may_carry_the_cmd_so_one_write_starts_a_command() {
    let (mut run, port) = start("one", MCP_PACKS, &[]);
    // ONE write on a Command that does not exist yet: the cmd rides in the run value
    let (t, e) = put_wait(port, "Command:solo", "run", json!({"id": "o1", "cmd": ["echo", "one", "write"]}), 20.0);
    assert!(!e, "{t}");
    assert_eq!(reply_part(&t, "stdout").unwrap_or_else(|| panic!("{t}"))["stdout"].as_str().unwrap().trim(), "one write", "{t}");
    // the cmd is written on the entity too, so a later run (no cmd in the value) uses it
    let (c, _) = call(port, "entity_read", json!({"spec": "Command:solo", "prop": "cmd"}));
    assert_eq!(serde_json::from_str::<J>(&c).unwrap(), json!(["echo", "one", "write"]), "{c}");
    let (t, _) = put_wait(port, "Command:solo", "run", json!({"id": "o2", "args": ["again"]}), 20.0);
    assert!(reply_part(&t, "stdout").unwrap()["stdout"].as_str().unwrap().contains("one write again"), "{t}");
    // a string cmd runs by sh -c, as before
    let (t, _) = put_wait(port, "Command:sh1", "run", json!({"id": "s", "cmd": "echo $((1+2))"}), 20.0);
    assert!(reply_part(&t, "stdout").unwrap()["stdout"].as_str().unwrap().trim() == "3", "{t}");
    assert!(terminate(&mut run));
}

#[test]
fn a_run_the_driver_refused_says_why_in_the_reply() {
    let (mut run, port) = start("why", MCP_PACKS, &[]);
    let (t, _) = put_wait(port, "Command:nocmd", "run", json!({"id": "n1", "args": []}), 20.0);
    assert!(t.contains("error: a command needs `cmd`"), "the reason of a refusal is in the reply (the receipt line has no head): {t}");
    // a command that ran and failed keeps its output lines, no duplicated error line
    let (t, _) = put_wait(port, "Command:bad", "run", json!({"id": "n2", "cmd": ["sh", "-c", "echo boom >&2; exit 3"]}), 20.0);
    assert!(t.contains("\"rc\":3") && t.contains("boom") && !t.contains("\nerror: "), "{t}");
    assert!(terminate(&mut run));
}

// ---- the paginated history route (#1143): GET /membrane/chat/messages?limit=N&before=<id> ------------------------------------------------
// A fast fake without the door round trips: a system line, one tool call with its result, the result line. A prompt that starts with `hold`
// waits for the file `go.<tag>` first, so a message can stay running.
const QUICK: &str = r#"#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.argv[-1]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "q-1"})
if prompt.startswith("hold"):
    for _ in range(600):
        if os.path.exists(os.path.join(here, "go." + tag)): break
        time.sleep(0.05)
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "tq1", "name": "mcp__membrane__entity_read", "input": {"spec": "Q:" + prompt}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tq1", "content": "read " + prompt}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Answer to " + prompt, "num_turns": 1, "duration_ms": 1, "total_cost_usd": 0.0, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}, "session_id": "q-1"})
"#;

fn history(port: u16, query: &str) -> J {
    let (st, body) = http(port, "GET", &format!("/membrane/chat/messages{query}"), "");
    assert_eq!(st, 200, "{body}");
    serde_json::from_str(&body).unwrap_or_else(|e| panic!("{e}: {body}"))
}
fn texts(h: &J) -> Vec<String> { h["messages"].as_array().unwrap().iter().map(|m| m["text"].as_str().unwrap().to_string()).collect() }
/// the request number inside a message id `<ms>-<n>`: the Request:<n> that posted it
fn req_n(id: &str) -> u64 { id.rsplit('-').next().unwrap().parse().unwrap() }
/// send `n` messages `m00`.. one after the other and wait for each to be done; -> their ids
fn fill(port: u16, n: usize) -> Vec<String> {
    let ids: Vec<String> = (0..n).map(|i| send(port, &format!("m{i:02}"))).collect();
    for id in &ids { wait("a message done", || (view(port, id)["state"] == "done").then_some(())); }
    ids
}

#[test]
fn the_history_route_pages_the_newest_turns_oldest_first_with_their_tools_and_a_cursor_to_older_ones() {
    let (mut run, port, _dir) = fake_seat_script("history", &[], &[], QUICK);
    let ids = fill(port, 60);
    // the newest 20 by default, oldest first inside the page, each the message view plus its tools
    let h = history(port, "");
    let want: Vec<String> = (40..60).map(|i| format!("m{i:02}")).collect();
    assert_eq!(texts(&h), want, "{h}");
    assert_eq!(h["more"], json!(true));
    assert_eq!(h["next"], json!(ids[40]), "the cursor is the oldest id of the page");
    let last = &h["messages"][19];
    assert_eq!((last["id"].clone(), last["state"].clone(), last["response"].clone()), (json!(ids[59]), json!("done"), json!("Answer to m59")), "{last}");
    assert_eq!(last["tool_calls"], json!([{"name": "entity_read", "input": {"spec": "Q:m59"}, "result": "read m59"}]), "{last}");
    assert_eq!(last["tools"], json!(["entity_read"]), "the view's own `tools` stay the names");
    assert_eq!(last, &view(port, &ids[59]).as_object().map(|v| { let mut v = v.clone(); v.insert("tool_calls".into(), last["tool_calls"].clone()); J::Object(v) }).unwrap(), "the per-message view plus tool_calls");
    // `before` pages to older turns, across the boundary, to the very first
    let h2 = history(port, &format!("?before={}", ids[40]));
    assert_eq!(texts(&h2), (20..40).map(|i| format!("m{i:02}")).collect::<Vec<_>>());
    assert_eq!((h2["more"].clone(), h2["next"].clone()), (json!(true), json!(ids[20])));
    let h3 = history(port, &format!("?before={}&limit=20", ids[20]));
    assert_eq!(texts(&h3), (0..20).map(|i| format!("m{i:02}")).collect::<Vec<_>>());
    assert_eq!((h3["more"].clone(), h3["next"].clone()), (json!(false), J::Null), "the end: no older turns");
    // a limit of its own; an exact fit has no more; an unknown cursor is a 404
    let h4 = history(port, "?limit=7");
    assert_eq!(texts(&h4), (53..60).map(|i| format!("m{i:02}")).collect::<Vec<_>>());
    assert_eq!(history(port, "?limit=60")["more"], json!(false));
    assert_eq!(history(port, &format!("?limit=5&before={}", ids[5]))["messages"].as_array().unwrap().len(), 5);
    assert_eq!(http(port, "GET", "/membrane/chat/messages?before=nope", "").0, 404);
    assert_eq!(http(port, "POST", "/membrane/chat/messages", "{}").0, 405);
    assert!(terminate(&mut run));
}

#[test]
fn the_pages_loading_path_costs_at_most_three_requests_for_sixty_messages() {
    let (mut run, port, _dir) = fake_seat_script("historycount", &[], &[], QUICK);
    let ids = fill(port, 60);
    // Request:<n> numbers the door's requests, and a message id carries the number of the POST that made it: two POSTs with nothing but the
    // page's loading between them count exactly the requests the load made (no polling in between)
    let a = send(port, "m60");
    let h = history(port, "");
    assert_eq!(h["messages"].as_array().unwrap().len(), 20);
    let _ = history(port, &format!("?before={}", h["next"].as_str().unwrap()));   // the 'load older' click
    let b = send(port, "m61");
    let between = req_n(&b) - req_n(&a) - 1;
    assert_eq!(between, 2, "the first load and one 'load older' click are the only requests between the two POSTs (the old page: ~2,000)");
    assert!(between <= 3);
    let _ = ids;
    assert!(terminate(&mut run));
}

#[test]
fn an_empty_chat_has_an_empty_history_and_a_running_message_is_in_the_newest_page_with_its_state() {
    let (mut run, port, dir) = fake_seat_script("historyrun", &[], &[], QUICK);
    let h = history(port, "");
    assert_eq!((h["messages"].clone(), h["more"].clone(), h["next"].clone()), (json!([]), json!(false), J::Null), "{h}");
    let done = fill(port, 3);
    let id = send(port, "hold on");
    wait("the message running", || (view(port, &id)["state"] == "running").then_some(()));
    let h = history(port, "");
    let m = h["messages"].as_array().unwrap();
    assert_eq!(m.len(), 4, "{h}");
    assert_eq!((m[3]["id"].clone(), m[3]["state"].clone(), m[3]["text"].clone()), (json!(id), json!("running"), json!("hold on")), "{h}");
    assert_eq!(m[3]["tool_calls"], json!([]));
    assert_eq!(m[0]["id"], json!(done[0]));
    let queued = send(port, "hold again");
    let h = history(port, "");
    assert!(["queued", "running"].contains(&h["messages"][4]["state"].as_str().unwrap()), "an unfinished message shows its state: {h}");
    std::fs::write(dir.join(format!("go.{}", tag_of("hold on"))), "").unwrap();
    wait("done", || (view(port, &id)["state"] == "done").then_some(()));
    std::fs::write(dir.join(format!("go.{}", tag_of("hold again"))), "").unwrap();
    wait("done", || (view(port, &queued)["state"] == "done").then_some(()));
    let h = history(port, "?limit=2");
    assert_eq!(h["messages"][1]["tool_calls"][0]["result"], json!("read hold again"));
    assert!(terminate(&mut run));
}

// ---- the page: one request for the newest turns, a 'load older' button (ui/chat.js run under node with a fake DOM and a counted fetch) -------
fn node_ok() -> bool { Command::new("node").arg("--version").stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false) }
fn chat_js() -> std::path::PathBuf { std::path::PathBuf::from(format!("{}/../membrane/apps/barest.c1f/ui/chat.js", env!("CARGO_MANIFEST_DIR"))) }

#[test]
fn chat_js_is_valid_and_its_load_is_one_history_request_then_one_per_load_older_click() {
    if !node_ok() { println!("note: node is not on PATH, chat.js is not run"); return; }
    let ui = chat_js().parent().unwrap().to_path_buf();
    let d = tmp("chatjs");
    // chat.js is an ES module with no import of the MCP helpers (it lists and reads no entity): copied as .mjs
    let src = std::fs::read_to_string(ui.join("chat.js")).unwrap();
    assert!(!src.contains("mcp.js"), "the chat page no longer reads entities one by one");
    std::fs::write(d.join("chat.mjs"), src).unwrap();
    std::fs::write(d.join("package.json"), "{\"type\":\"module\"}").unwrap();
    let o = Command::new("node").arg("--check").arg(d.join("chat.mjs")).output().unwrap();
    assert!(o.status.success(), "node --check chat.js: {}", String::from_utf8_lossy(&o.stderr));
    std::fs::write(d.join("run.mjs"), HARNESS).unwrap();
    let o = Command::new("node").arg(d.join("run.mjs")).current_dir(&d).output().unwrap();
    let _ = std::fs::remove_dir_all(&d);
    assert!(o.status.success(), "harness failed: {}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr));
}

const HARNESS: &str = r#"
const calls = [];
const mk = (id, state) => ({ id, text: 't' + id, state, response: state === 'done' ? 'a' + id : null, tools: [], tool_calls: state === 'done' ? [{ name: 'entity_read', input: { spec: 'x' }, result: 'r' }] : [], turn: { state, started: 1, ended: 2 } });
const ids = Array.from({ length: 45 }, (_, i) => String(1000 + i) + '-' + (i + 1));
const page = (limit, before) => {
  const end = before ? ids.indexOf(before) : ids.length, start = Math.max(0, end - limit);
  return { messages: ids.slice(start, end).map((i) => mk(i, 'done')), more: start > 0, next: start > 0 ? ids[start] : null };
};
globalThis.fetch = async (url) => {
  calls.push(String(url));
  const u = new URL(String(url), 'http://x');
  if (u.pathname !== '/membrane/chat/messages') return { ok: false, status: 404, json: async () => ({}) };
  const j = page(Number(u.searchParams.get('limit') || 20), u.searchParams.get('before'));
  return { ok: true, status: 200, json: async () => j };
};
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.dataset = {}; this.className = ''; this.listeners = {}; this._text = ''; this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0; this.disabled = false; this.value = ''; }
  get isConnected() { let e = this; while (e.parent) e = e.parent; return e.root === true; }
  get nextSibling() { if (!this.parent) return null; const c = this.parent.children; return c[c.indexOf(this) + 1] || null; }
  get firstChild() { return this.children[0] || null; }
  set textContent(t) { this._text = t; } get textContent() { return this._text; }
  adopt(c) { for (const x of c) { x.remove && x.remove(); x.parent = this; } }
  append(...c) { this.adopt(c); this.children.push(...c); }
  prepend(...c) { this.adopt(c); this.children.unshift(...c); }
  insertBefore(n, ref) { this.adopt([n]); const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); }
  replaceChildren(...c) { for (const x of this.children) x.parent = null; this.children = []; this.append(...c); }
  addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); }
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  focus() {}
  walk(f) { f(this); for (const c of this.children) c.walk(f); }
}
globalThis.document = { hidden: false, createElement: (t) => new El(t) };
const { startChat } = await import('./chat.mjs');
const ui = { log: Object.assign(new El('ol'), { root: true }), form: new El('form'), input: new El('textarea'), send: new El('button') };
startChat(ui);
await new Promise((r) => setTimeout(r, 400));
const hist = () => calls.filter((c) => c.startsWith('/membrane/chat/messages'));
const must = (c, m) => { if (!c) { console.log(calls); throw new Error(m); } };
must(calls.length === 1 && hist().length === 1 && /limit=20/.test(calls[0]), 'the load is ONE history request for 20 turns: ' + calls);
let lis = 0, btn = null;
ui.log.walk((e) => { if (e.tag === 'li' && /\bmsg\b/.test(e.className)) lis++; if (e.tag === 'button' && /older/i.test(e.textContent)) btn = e; });
must(lis === 20, 'twenty turns drawn, got ' + lis);
must(btn, 'a load older button');
btn.listeners.click[0]({ preventDefault() {} });
await new Promise((r) => setTimeout(r, 300));
must(hist().length === 2 && /before=1025-26/.test(calls[1]), 'one more request, before the oldest drawn: ' + calls);
lis = 0; btn = null;
ui.log.walk((e) => { if (e.tag === 'li' && /\bmsg\b/.test(e.className)) lis++; if (e.tag === 'button' && /older/i.test(e.textContent)) btn = e; });
must(lis === 40, 'forty turns drawn, got ' + lis);
btn.listeners.click[0]({ preventDefault() {} });
await new Promise((r) => setTimeout(r, 300));
lis = 0; btn = null;
ui.log.walk((e) => { if (e.tag === 'li' && /\bmsg\b/.test(e.className)) lis++; if (e.tag === 'button' && /older/i.test(e.textContent)) btn = e; });
must(lis === 45 && !btn, 'all 45 drawn and the button gone, got ' + lis);
const order = []; ui.log.walk((e) => { if (e.tag === 'li' && /\bmsg\b/.test(e.className)) order.push(e.children[0]._text); });
must(order[0] === 't1000-1' && order[44] === 't1044-45' && order.every((t, i) => i === 0 || t > order[i - 1]), 'oldest first across the pages: ' + order);
// the periodic refresh does not re-list or re-read finished turns
await new Promise((r) => setTimeout(r, 3500));
must(!calls.some((c) => c.startsWith('/membrane/chat/message/')), 'finished turns are not fetched one by one: ' + calls);
process.exit(0);
"#;
