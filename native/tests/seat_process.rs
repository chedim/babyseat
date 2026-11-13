//! The barest seat as the operator launches it (#1143): `cliffvm run membrane/packs/boot.c1f membrane/packs membrane/apps/barest.c1f`
//! with the page (plain static JavaScript under the app's `ui/`) served by the door, the MCP door the graph viewer reads and the chat
//! the page writes to. The packs and the app are the SHIPPED folders (not copies); the store and the fake `claude` live in a temp folder.
//! There is NEVER a real `claude` here, only a fake script; every wait has a timeout and the child is always reaped; the port is ephemeral.
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

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn app_ui() -> std::path::PathBuf { repo().join("membrane/apps/barest.c1f/ui") }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// a fake claude: prints the stream-json the chat reads (a system line, one tool use, the result), no model, no network
const FAKE: &str = r#"#!/usr/bin/env python3
import json, sys
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
prompt = sys.argv[-1]
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": []})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_query", "input": {"q": "kind:Chat"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "none"}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Seat answer to: " + prompt, "num_turns": 2, "session_id": "fake-1"})
"#;

/// the operator's command line (boot, the packs folder, the barest app), against a temp store, an ephemeral port and the fake claude
/// (`with_fake` false: no `chat_claude_bin` fact, as when no CLI is found)
fn seat(tag: &str, with_fake: bool) -> (Run, u16) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let r = repo();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(&dir).args(["run", r.join("membrane/packs/boot.c1f").to_str().unwrap(), r.join("membrane/packs").to_str().unwrap(), r.join("membrane/apps/barest.c1f").to_str().unwrap(),
        "--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    if with_fake { cmd.arg("--fact").arg(format!("chat_claude_bin={}", bin.display())); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir };
    let line = run._lines.recv_timeout(Duration::from_secs(60)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port)
}

/// one HTTP request on its own connection; -> (status, content-type, body text)
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    let ct = head.lines().find_map(|l| l.to_lowercase().strip_prefix("content-type:").map(|v| v.trim().to_string())).unwrap_or_default();
    (status, ct, String::from_utf8_lossy(&out[i + 4..]).to_string())
}

fn call(port: u16, tool: &str, args: J) -> (String, bool) {
    let (st, _, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    assert_eq!(st, 200, "{body}");
    let r: J = serde_json::from_str(&body).unwrap();
    (r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string(), r["result"]["isError"].as_bool().unwrap())
}

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

/// every `src="..."` / `href="..."` of the page that names a file of the app (not a URL, not a fragment)
fn page_files(html: &str) -> Vec<String> {
    let mut v = vec![];
    for attr in ["src=\"", "href=\""] {
        for part in html.split(attr).skip(1) {
            let r = part.split('"').next().unwrap();
            if !r.is_empty() && !r.contains("://") && !r.starts_with('#') && !r.starts_with("data:") { v.push(r.to_string()); }
        }
    }
    v
}

/// the files a module imports (`from './x.js'` / `import './x.js'`), as app-relative names
fn module_imports(js: &str) -> Vec<String> {
    let mut v = vec![];
    for part in js.split("from './").skip(1).chain(js.split("import './").skip(1)) { v.push(part.split('\'').next().unwrap().to_string()); }
    v
}

#[test]
fn the_page_and_its_files_are_served_with_their_content_types_and_every_reference_exists() {
    let (mut run, port) = seat("page", true);
    let (st, ct, html) = http(port, "GET", "/", "");
    assert_eq!(st, 200);
    assert!(ct.starts_with("text/html"), "{ct}");
    assert!(html.contains("<canvas") && html.contains("id=\"composer\""), "the page holds the graph canvas and the chat composer");
    assert_eq!(http(port, "GET", "/index.html", "").0, 200);
    let files = page_files(&html);
    assert!(files.iter().any(|f| f.ends_with("app.js")) && files.iter().any(|f| f.ends_with("app.css")), "{files:?}");
    let mut js: Vec<String> = vec![];
    for f in &files {
        let rel = f.trim_start_matches("ui/www/");
        assert!(app_ui().join(rel).is_file(), "the page references {f}, which is not under the app folder");
        let (st, ct, body) = http(port, "GET", &format!("/ui/www/{rel}"), "");
        assert_eq!(st, 200, "{f}");
        let want = if rel.ends_with(".js") { "application/javascript" } else if rel.ends_with(".css") { "text/css" } else if rel.ends_with(".svg") { "image/svg+xml" } else { "" };
        assert!(ct.starts_with(want), "{f}: {ct}");
        assert_eq!(body, std::fs::read_to_string(app_ui().join(rel)).unwrap(), "{f} is served as it is on disk");
        if rel.ends_with(".js") { js.push(rel.to_string()); }
    }
    // the modules' own imports exist and are served too
    let mut i = 0;
    while i < js.len() {
        let src = std::fs::read_to_string(app_ui().join(&js[i])).unwrap();
        for m in module_imports(&src) {
            assert!(app_ui().join(&m).is_file(), "{} imports {m}, which is not under the app folder", js[i]);
            assert_eq!(http(port, "GET", &format!("/ui/www/{m}"), "").0, 200, "{m}");
            if !js.contains(&m) { js.push(m); }
        }
        i += 1;
    }
    assert!(terminate(&mut run));
    // a syntax check of every script, when node is there
    if Command::new("node").arg("--version").stdout(Stdio::null()).stderr(Stdio::null()).status().map(|s| s.success()).unwrap_or(false) {
        for f in &js {
            let p = app_ui().join(f);
            let tmpf = std::env::temp_dir().join(format!("seatcheck-{}-{}.mjs", std::process::id(), f.replace('/', "_")));
            std::fs::copy(&p, &tmpf).unwrap();
            let o = Command::new("node").arg("--check").arg(&tmpf).output().unwrap();
            let _ = std::fs::remove_file(&tmpf);
            assert!(o.status.success(), "node --check {f}: {}", String::from_utf8_lossy(&o.stderr));
        }
    } else { println!("note: node is not on PATH, the scripts' syntax is not checked"); }
}

#[test]
fn a_message_posted_to_the_chat_is_answered_by_the_fake_and_found_through_the_mcp_door() {
    let (mut run, port) = seat("chat", true);
    let (st, _, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": "hello seat"}).to_string());
    assert_eq!(st, 202, "{body}");
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    let v = wait("the answer", || {
        let (st, _, b) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        assert_eq!(st, 200, "{b}");
        let v: J = serde_json::from_str(&b).unwrap();
        if v["state"] == "done" || v["state"] == "error" { Some(v) } else { None }
    });
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Seat answer to: hello seat");
    assert_eq!(v["tools"], json!(["entity_query"]));
    // the viewer's reads: the message is an entity of kind ChatMessage; the query takes an offset, an exclusion and the properties
    // (the message's class is Chat, as its eid names it: the viewer finds the chat by the eid prefix, not by a ChatMessage kind)
    let (msgs, _) = call(port, "entity_query", json!({"q": "name~:membrane::Chat:message:", "k": 100}));
    assert!(msgs.contains(&format!("membrane::Chat:message:{id} (Chat)")) && msgs.contains(&format!("membrane::Chat:message:{id}:response (Chat)")), "{msgs}");
    let (all, _) = call(port, "entity_query", json!({"q": "name~:membrane::", "k": 100}));
    let rows: Vec<&str> = all.lines().collect();
    assert!(rows.len() >= 4, "{all}");
    let (page2, _) = call(port, "entity_query", json!({"q": "name~:membrane::", "k": 100, "offset": 1}));
    assert_eq!(page2.lines().collect::<Vec<_>>(), rows[1..].to_vec(), "offset skips rows");
    let (page, _) = call(port, "entity_query", json!({"q": "name~:membrane::", "k": 2, "offset": 2}));
    assert_eq!(page.lines().collect::<Vec<_>>(), rows[2..4].to_vec(), "k and offset page");
    let (every, _) = call(port, "entity_query", json!({"q": "", "k": 100}));
    assert!(every.lines().any(|l| l.ends_with("(Request)")), "the requests are rows when not excluded");
    let (no_req, _) = call(port, "entity_query", json!({"q": "kind!:Request", "k": 100}));
    assert!(no_req.lines().count() >= 4 && !no_req.lines().any(|l| l.ends_with("(Request)")), "{no_req}");
    let (n_all, _) = call(port, "entity_query", json!({"q": "kind!:Request & name~:membrane::", "count": true}));
    assert_eq!(n_all, format!("{} entity(ies)", rows.len()));
    let (full, _) = call(port, "entity_query", json!({"q": "name~:membrane::Chat:message:", "k": 100, "full": true}));
    assert!(full.contains(":response (Chat) {") && full.contains("Seat answer to: hello seat"), "{full}");
    assert!(terminate(&mut run));
}

#[test]
fn with_no_claude_named_the_answer_is_a_plain_instruction() {
    let (mut run, port) = seat("noclaude", false);
    let (_, _, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": "hi"}).to_string());
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    let v = wait("the answer", || {
        let (_, _, b) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        let v: J = serde_json::from_str(&b).unwrap();
        if v["state"] == "error" || v["state"] == "done" { Some(v) } else { None }
    });
    let t = v["response"].as_str().unwrap();
    assert!(t.contains("chat_claude_bin") && t.contains("CLAUDE_BIN"), "the answer says how to name the CLI: {t}");
    assert!(terminate(&mut run));
}
