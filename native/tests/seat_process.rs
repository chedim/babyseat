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
fn seat(tag: &str, with_fake: bool) -> (Run, u16) { seat_with(tag, with_fake, &[]) }

/// the packs the barest seat loads: `membrane/apps/barest.packs`, one shipped pack name per line (what scripts/seat passes too), as single-file apps
fn barest_packs() -> Vec<String> {
    let r = repo();
    std::fs::read_to_string(r.join("membrane/apps/barest.packs")).expect("membrane/apps/barest.packs lists the packs of the barest seat")
        .lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect()
}

fn seat_with(tag: &str, with_fake: bool, facts: &[&str]) -> (Run, u16) { seat_at(tmp(tag), with_fake, facts) }

/// the same, in a folder the caller made (its `seat.sqlite` may hold rows of an earlier run)
fn seat_at(dir: std::path::PathBuf, with_fake: bool, facts: &[&str]) -> (Run, u16) {
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let r = repo();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(&dir).arg("run").args(barest_packs()).arg(r.join("membrane/apps/barest.c1f"))
        .args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
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

/// the LIVE Request entities: a Request is VIRTUAL (RAM only, not a row), so `entity_query` never lists it; a block run by `run_cliff` reads the engine's `instances`
fn clifflet(port: u16, code: &str) -> String { let (t, e) = call(port, "run_cliff", json!({"code": code})); assert!(!e, "{t}"); t }

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
    assert!(std::fs::read_to_string(app_ui().join("graph.js")).unwrap().contains("['zoomin', 1.15], ['zoomout', 1 / 1.15]") && std::fs::read_to_string(app_ui().join("graph.js")).unwrap().contains("this.zoomAt(this.w / 2, this.h / 2, f)"), "graph.js wires both buttons to zoomAt");
    let (st, ct, html) = http(port, "GET", "/", "");
    assert_eq!(st, 200);
    assert!(ct.starts_with("text/html"), "{ct}");
    assert!(html.contains("<canvas") && html.contains("id=\"composer\""), "the page holds the graph canvas and the chat composer");
    assert_eq!(http(port, "GET", "/index.html", "").0, 200);
    assert!(html.contains("id=\"zoomin\"") && html.contains("id=\"zoomout\"") && html.contains("aria-label=\"Zoom in\"") && html.contains("aria-label=\"Zoom out\""), "the zoom buttons are in the page");
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
    assert!(msgs.contains(&format!("membrane::Chat:message:{id} (Chat)")) && msgs.contains(&format!("membrane::Chat:message:{id}:turn:response (Chat)")), "{msgs}");
    let (all, _) = call(port, "entity_query", json!({"q": "name~:membrane::", "k": 100}));
    let rows: Vec<&str> = all.lines().collect();
    assert!(rows.len() >= 4, "{all}");
    let (page2, _) = call(port, "entity_query", json!({"q": "name~:membrane::", "k": 100, "offset": 1}));
    assert_eq!(page2.lines().collect::<Vec<_>>(), rows[1..].to_vec(), "offset skips rows");
    let (page, _) = call(port, "entity_query", json!({"q": "name~:membrane::", "k": 2, "offset": 2}));
    assert_eq!(page.lines().collect::<Vec<_>>(), rows[2..4].to_vec(), "k and offset page");
    // a Request is VIRTUAL: live in the engine (a block sees it through `instances`), never a row (the query does not list it)
    let (every, _) = call(port, "entity_query", json!({"q": "", "k": 100}));
    assert!(!every.lines().any(|l| l.ends_with("(Request)")), "the requests are not rows: {every}");
    assert_ne!(clifflet(port, "len(instances('Request')) > 0"), "False", "the live requests are in the engine");
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

#[test]
fn the_barest_seat_loads_only_its_own_packs_and_seeds_no_command_or_ledger_file() {
    let names = barest_packs();
    for gone in ["decisions", "dev", "door_self", "door_models", "door_voice", "router", "door_state", "door_decisions"] {
        assert!(!names.iter().any(|n| n.ends_with(&format!("/{gone}.c1f"))), "{gone} is not a barest pack");
    }
    let (mut run, port) = seat("fresh", true);
    let (none, _) = call(port, "entity_query", json!({"q": "name~:Command:", "k": 100}));
    assert!(!none.contains("Command:git") && !none.contains("Command:gh"), "no seeded commands: {none}");
    let (led, _) = call(port, "entity_query", json!({"q": "name~:File:", "k": 100}));
    assert!(!led.contains("operator-ledger"), "no seeded ledger file: {led}");
    // (the door's requests carry none of the not-ported families' getters: those packs are not loaded, checked above; a Request is no row to read them from)
    assert!(terminate(&mut run));
}

#[test]
fn an_answered_request_is_collected_at_once_whatever_the_old_request_keep_s_fact_says() {
    // the fact is ACCEPTED and does nothing: the engine collects a Request the round after its reply is delivered (docs/cliffvm.md, "Garbage collection")
    let (mut run, port) = seat_with("collect", true, &["request_keep_s=600"]);
    call(port, "entity_query", json!({"q": "name~:membrane::", "k": 5}));
    // the count is read through the door, itself a request: at most that one is live (a Request is VIRTUAL: a block reads the live ones through `instances`)
    wait("no answered request left", || { let n: u32 = clifflet(port, "len(instances('Request'))").trim().parse().unwrap(); (n <= 1).then_some(()) });
    // nothing named `dropped` or `kept_at` is left to read, and the door still serves the page after collecting
    assert_eq!(clifflet(port, "len([r for r in instances('Request') if r.dropped == True])"), "0");
    let (st, _, html) = http(port, "GET", "/", "");
    assert_eq!(st, 200); assert!(html.contains("<html") || html.contains("<!doctype") || html.contains("<!DOCTYPE"));
    assert!(terminate(&mut run));
}

#[test]
fn a_request_is_collected_as_soon_as_it_is_answered_and_the_door_still_serves() {
    let (mut run, port) = seat_with("keep0", true, &["request_keep_s=0"]);
    let (st, _, html) = http(port, "GET", "/", "");
    assert_eq!(st, 200); assert!(html.contains("<html") || html.contains("<!doctype") || html.contains("<!DOCTYPE"));
    // no Request stays: the count is read through the door, itself a request, so it counts at most that one
    wait("no answered request left", || {
        let n: u32 = clifflet(port, "len(instances('Request'))").trim().parse().unwrap();
        (n <= 1).then_some(())
    });
    // the chat still works
    let (st, _, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": "hello zero"}).to_string());
    assert_eq!(st, 202, "{body}");
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    let v = wait("the answer", || {
        let (_, _, b) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        let v: J = serde_json::from_str(&b).unwrap();
        if v["state"] == "done" || v["state"] == "error" { Some(v) } else { None }
    });
    assert_eq!(v["state"], "done", "{v}");
    assert!(terminate(&mut run));
}

#[test]
fn an_entity_query_without_full_returns_a_small_reply_for_an_entity_with_large_state() {
    let (mut run, port) = seat("small", true);
    let big = "x".repeat(200_000);
    call(port, "entity_write", json!({"spec": "Heavy:one", "prop": "blob", "value": big}));
    let (r, _) = wait("the entity stored", || { let (r, e) = call(port, "entity_query", json!({"q": "kind:Heavy"})); r.contains("Heavy:one").then_some((r, e)) });
    assert!(r.len() < 200, "a query without full lists the entity only: {} bytes", r.len());
    let (full, _) = call(port, "entity_query", json!({"q": "kind:Heavy", "full": true}));
    assert!(full.len() > 200_000, "full adds the stored properties: {} bytes", full.len());
    assert!(terminate(&mut run));
}

#[test]
fn request_rows_of_an_earlier_run_are_retired_when_the_seat_starts() {
    let dir = tmp("sweep");
    {
        let db = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
        db.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
        for n in [900001, 900002] { db.execute("INSERT INTO c_field (k, body) VALUES (?1, ?2)", rusqlite::params![format!("Request:{n}"), r#"{"cls":"Request","props":{"path":"/old"}}"#]).unwrap(); }
        db.execute("INSERT INTO c_field (k, body) VALUES ('Note:keep', '{\"cls\":\"Note\",\"props\":{\"a\":1}}')", []).unwrap();
    }
    let (mut run, port) = seat_at(dir, true, &["request_keep_s=100000"]);
    wait("the old Request rows gone", || {
        let (rows, _) = call(port, "entity_query", json!({"q": "kind:Request", "k": 100}));
        (!rows.contains("Request:900001") && !rows.contains("Request:900002")).then_some(())
    });
    let (notes, _) = call(port, "entity_query", json!({"q": "kind:Note"}));
    assert!(notes.contains("Note:keep"), "only Request rows are swept: {notes}");
    assert!(terminate(&mut run));
}

#[test]
fn help_topics_are_entities_the_model_lists_and_reads() {
    let (mut run, port) = seat("help", true);
    let (list, _) = wait("the help topics", || { let (l, e) = call(port, "entity_query", json!({"q": "kind:Help"})); (l.lines().count() == 6).then_some((l, e)) });
    for t in ["c1f", "packs", "upgrade", "banner", "cliff", "semtext"] { assert!(list.contains(&format!("Help:{t} (Help)")), "{list}"); }
    for t in ["c1f", "packs", "upgrade", "banner", "cliff", "semtext"] {
        let disk = std::fs::read_to_string(repo().join(format!("membrane/apps/barest.c1f/help/{t}.md"))).unwrap();
        let (text, err) = wait("the topic's text", || { let (x, e) = call(port, "entity_read", json!({"spec": format!("Help:{t}"), "prop": "text"})); (x.len() > 100).then_some((x, e)) });
        assert!(!err && text == disk, "Help:{t}.text is the file's text");
    }
    let (whole, _) = call(port, "entity_read", json!({"spec": "Help:c1f"}));
    assert!(whole.contains("the language of the field"), "a plain read shows the text: {}", &whole[..whole.len().min(200)]);
    assert!(terminate(&mut run));
}

// ---- the banner is the model's (#1143) ---------------------------------------------------------------------------------------------------
fn banner(port: u16) -> String { call(port, "entity_read", json!({"spec": "membrane::Banner:owner:main", "prop": "text"})).0 }
const MINE: &str = "PACK mine 1\nIMPORT default_banner 1\nBanner.text getter: 'stops done: ' + str(len([s for s in instances('Stop') if s.done == True]))\n";

#[test]
fn a_fresh_seats_banner_is_the_default_banner_with_no_lines_and_no_instructions() {
    let (mut run, port) = seat("bannerfresh", true);
    let b = wait("the banner", || { let b = banner(port); (b.contains("CONVERSATION: main")).then_some(b) });
    assert!(b.contains("NOW (for reference, not an event):"), "{b}");
    let low = b.to_lowercase();
    for bad in ["placeholder", "no history", "cannot", "does not", "not yet", "bannerline", "howto"] { assert!(!low.contains(bad), "the default banner says {bad:?}: {b}"); }
    let (q, _) = call(port, "entity_query", json!({"q": "kind:BannerLine"}));
    assert_eq!(q, "0 entity(ies)", "{q}");
    assert!(terminate(&mut run));
}

#[test]
fn a_pack_the_model_loads_through_entity_write_replaces_the_banner_completely_and_stays_live() {
    let (mut run, port) = seat("bannerpack", true);
    wait("the banner", || banner(port).contains("CONVERSATION: main").then_some(()));
    call(port, "entity_write", json!({"spec": "Stop:a", "prop": "done", "value": false}));
    let (r, e) = call(port, "entity_write", json!({"spec": "Pack:mine", "prop": "text", "value": MINE}));
    assert!(!e, "{r}");
    let (st, _) = wait("the pack state", || { let (s, _) = call(port, "entity_read", json!({"spec": "Pack:mine", "prop": "state"})); (s != "waiting").then_some((s, ())) });
    assert_eq!(st, "loaded");
    let b = wait("the model's text", || { let b = banner(port); b.contains("stops done: 0").then_some(b) });
    assert_eq!(b, "stops done: 0", "the whole banner is the model's: nothing of the default is left, nothing is added");
    call(port, "entity_write", json!({"spec": "Stop:a", "prop": "done", "value": true}));
    wait("1 done", || (banner(port) == "stops done: 1").then_some(()));
    assert!(terminate(&mut run));
}

#[test]
fn a_bare_entity_id_names_the_membrane_one_when_only_that_exists() {
    let (mut run, port) = seat("bareid", true);
    wait("the banner", || banner(port).contains("CONVERSATION: main").then_some(()));
    let (t, _) = call(port, "entity_read", json!({"spec": "Banner:owner:main", "prop": "text"}));
    assert!(t.contains("CONVERSATION: main"), "{t}");
    let (w, e) = call(port, "entity_write", json!({"spec": "Banner:owner:main", "prop": "note", "value": "x"}));
    assert!(!e, "{w}");
    let (n, _) = call(port, "entity_read", json!({"spec": "membrane::Banner:owner:main", "prop": "note"}));
    assert_eq!(n, "x", "the write landed on the membrane:: entity");
    let (q, _) = call(port, "entity_query", json!({"q": "name~:Banner:owner"}));
    assert_eq!(q, "0 entity(ies)", "no second Banner:owner:main was made: {q}");
    assert!(terminate(&mut run));
}

// ---- a restart keeps what the model wrote (#1143) -----------------------------------------------------------------------------------------
/// stop a seat and give its store to a new folder (the old folder is removed with the run)
fn handover(run: &mut Run) -> std::path::PathBuf {
    assert!(terminate(run));
    let next = tmp("restart");
    for f in std::fs::read_dir(&run.dir).unwrap().filter_map(Result::ok) {
        if f.file_name().to_string_lossy().starts_with("seat.sqlite") { std::fs::copy(f.path(), next.join(f.file_name())).unwrap(); }
    }
    next
}

#[test]
fn a_restart_restores_the_models_entities_and_still_sweeps_old_requests() {
    let (mut run, port) = seat("restart1", true);
    wait("the banner", || banner(port).contains("CONVERSATION: main").then_some(()));
    call(port, "entity_write", json!({"spec": "Stop:a", "prop": "done", "value": true}));
    let (r, e) = call(port, "entity_write", json!({"spec": "Pack:mine", "prop": "text", "value": MINE}));
    assert!(!e, "{r}");
    call(port, "entity_write", json!({"spec": "Note:keep", "prop": "a", "value": 7}));
    wait("the model's banner", || (banner(port) == "stops done: 1").then_some(()));
    wait("the note", || call(port, "entity_query", json!({"q": "kind:Note"})).0.contains("Note:keep").then_some(()));
    let dir = handover(&mut run);
    {   // an old Request row an earlier run left
        let db = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
        db.execute("INSERT INTO c_field (k, body) VALUES ('Request:900001', '{\"cls\":\"Request\",\"props\":{\"path\":\"/old\"}}')", []).unwrap();
    }
    let (mut run2, port2) = seat_at(dir, true, &["request_keep_s=100000"]);
    let b = wait("the model's banner is back", || { let b = banner(port2); b.contains("stops done").then_some(b) });
    assert_eq!(b, "stops done: 1", "the model's pack survived the restart and still replaces the default");
    let (n, _) = call(port2, "entity_read", json!({"spec": "Note:keep", "prop": "a"}));
    assert_eq!(n, "7", "the note is back");
    let (q, _) = call(port2, "entity_query", json!({"q": "kind:Banner"}));
    assert_eq!(q.lines().filter(|l| l.contains("(Banner)")).count(), 1, "the default banner entity was born once: {q}");
    wait("the old Request row swept", || (!call(port2, "entity_query", json!({"q": "kind:Request", "k": 100})).0.contains("Request:900001")).then_some(()));
    // the restored banner stays live
    call(port2, "entity_write", json!({"spec": "Stop:b", "prop": "done", "value": true}));
    wait("the banner follows", || (banner(port2) == "stops done: 2").then_some(()));
    assert!(terminate(&mut run2));
}

#[test]
fn the_previous_turns_error_is_still_in_the_banner_after_a_restart_with_the_warm_boot_off() {
    // the turns are a lazy class: after the restart none is loaded (warm_rows=0); the banner reaches the previous turn through the chat's `last_turn`
    let (mut run, port) = seat_with("lastturn", false, &["warm_rows=0"]);
    let (_, _, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": "hi"}).to_string());
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    wait("the error turn", || { let (_, _, b) = http(port, "GET", &format!("/membrane/chat/message/{id}"), ""); (serde_json::from_str::<J>(&b).unwrap()["state"] == "error").then_some(()) });
    wait("the banner names it", || banner(port).contains("THE PREVIOUS TURN ENDED WITH AN ERROR").then_some(()));
    let dir = handover(&mut run);
    let (mut run2, port2) = seat_at(dir, false, &["warm_rows=0"]);
    let b = wait("the banner after the restart", || { let b = banner(port2); b.contains("CONVERSATION: main").then_some(b) });
    wait("the error line", || banner(port2).contains(&format!("THE PREVIOUS TURN ENDED WITH AN ERROR: membrane::Chat:message:{id}:turn:response")).then_some(()));
    let _ = b;
    assert!(terminate(&mut run2));
}

// ---- an entity of two extra classes (#1143) -----------------------------------------------------------------------------------------------
const TWO: &str = "PACK two 1\nAlpha.sa getter: 'alpha:' + str(this.n)\nBeta.sb getter: 'beta:' + str(this.n)\nAlpha.go_a on-setter run-a:\n  WHEN this.go_a == True:\n    this.did_a = True\nBeta.go_b on-setter run-b:\n  WHEN this.go_b == True:\n    this.did_b = True\n";
fn read(port: u16, eid: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": eid, "prop": prop})).0 }
fn w(port: u16, eid: &str, prop: &str, v: J) { let (r, e) = call(port, "entity_write", json!({"spec": eid, "prop": prop, "value": v})); assert!(!e, "{r}"); }

#[test]
fn an_entity_carries_two_extra_classes_getters_setters_queries_and_a_restart() {
    let (mut run, port) = seat("twoclasses", true);
    w(port, "Pack:two", "text", json!(TWO));
    wait("the pack loaded", || (read(port, "Pack:two", "state") == "loaded").then_some(()));
    w(port, "cls:alpha", "name", json!("Alpha"));          // the ISA parent entities a kind's setter needs
    w(port, "cls:beta", "name", json!("Beta"));
    w(port, "Thing:x", "n", json!(3));
    w(port, "Thing:x", "parents", json!(["cls:alpha", "cls:beta"]));      // the two kinds, by the one list
    wait("both getters apply", || (read(port, "Thing:x", "sa") == "alpha:3" && read(port, "Thing:x", "sb") == "beta:3").then_some(()));
    w(port, "Thing:x", "go_a", json!(true));
    w(port, "Thing:x", "go_b", json!(true));
    wait("both setters fire", || (read(port, "Thing:x", "did_a") == "True" && read(port, "Thing:x", "did_b") == "True").then_some(()));
    for k in ["Alpha", "Beta"] {
        let (q, _) = call(port, "entity_query", json!({"q": format!("kind:{k}")}));
        assert!(q.contains("Thing:x"), "kind:{k} lists the entity: {q}");
    }
    let (q, _) = call(port, "entity_query", json!({"q": "kind:Thing"}));
    assert!(q.contains("Thing:x"), "its own class still lists it: {q}");
    // a restart: the stamp comes back with the entity, both getters and both setters work again
    let dir = handover(&mut run);
    let (mut run2, port2) = seat_at(dir, true, &[]);
    wait("both getters after the restart", || (read(port2, "Thing:x", "sa") == "alpha:3" && read(port2, "Thing:x", "sb") == "beta:3").then_some(()));
    w(port2, "Thing:x", "n", json!(4));
    wait("both getters follow a write", || (read(port2, "Thing:x", "sa") == "alpha:4" && read(port2, "Thing:x", "sb") == "beta:4").then_some(()));
    w(port2, "Thing:x", "go_a", json!(false));
    w(port2, "Thing:x", "did_a", json!(false));
    w(port2, "Thing:x", "go_a", json!(true));
    wait("a setter fires after the restart", || (read(port2, "Thing:x", "did_a") == "True").then_some(()));
    for k in ["Alpha", "Beta"] { assert!(call(port2, "entity_query", json!({"q": format!("kind:{k}")})).0.contains("Thing:x"), "kind:{k} after the restart"); }
    assert!(terminate(&mut run2));
}

// ---- a turn is an entity that owns its answer and its tools (#1143) -----------------------------------------------------------------------
#[test]
fn a_turn_is_an_entity_that_owns_its_answer_and_tools_and_retiring_it_retires_them() {
    let (mut run, port) = seat("turn", true);
    let (_, _, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": "hello turn"}).to_string());
    let id = serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string();
    let v = wait("the answer", || {
        let (_, _, b) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
        let v: J = serde_json::from_str(&b).unwrap();
        (v["state"] == "done").then_some(v)
    });
    let msg = format!("membrane::Chat:message:{id}");
    let turn = format!("{msg}:turn");
    assert_eq!(v["turn"]["state"], "done", "the page's view carries the turn: {v}");
    assert!(v["turn"]["started"].is_number() && v["turn"]["ended"].as_f64() >= v["turn"]["started"].as_f64(), "{v}");
    let (q, _) = call(port, "entity_query", json!({"q": format!("name~:{msg}"), "k": 100}));
    for e in [msg.clone(), turn.clone(), format!("{turn}:response"), format!("{turn}:tool:tu1")] { assert!(q.lines().any(|l| l.starts_with(&format!("{e} "))), "{e} in {q}"); }
    assert!(!q.contains(&format!("{msg}:response")) && !q.contains(&format!("{msg}:tool:")), "the answer and the tools live under the turn: {q}");
    let (st, _) = call(port, "entity_read", json!({"spec": turn, "prop": "status"}));
    assert_eq!(st, "done");
    assert!(!call(port, "entity_read", json!({"spec": turn, "prop": "refused"})).0.contains("loop"), "no refused write on the turn");
    let (m, _) = call(port, "entity_read", json!({"spec": turn, "prop": "message"}));
    assert_eq!(m, msg);
    // retiring the turn retires what it owns (the answer's body too); the message and its body stay
    call(port, "entity_write", json!({"spec": turn, "prop": "retire", "value": true}));
    wait("the turn and its children gone", || {
        let (q, _) = call(port, "entity_query", json!({"q": format!("name~:{msg}"), "k": 100}));
        (q.lines().count() == 2 && q.starts_with(&format!("{msg} ")) && q.contains(&format!("{msg}:body "))).then_some(())
    });
    assert!(terminate(&mut run));
}

#[test]
fn the_seat_script_forwards_each_fact_to_cliffvm_run() {
    // a fake cliffvm (the CLIFFVM variable) writes the arguments it was given, one per line; no seat starts
    let dir = tmp("seatscript");
    let fake = dir.join("fake-cliffvm");
    std::fs::write(&fake, "#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done > \"$ARGS_OUT\"\n").unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let out = dir.join("args");
    let st = Command::new(repo().join("scripts/seat"))
        .args(["--store", dir.join("s.sqlite").to_str().unwrap(), "--port", "0", "--fact", "request_keep_s=0", "--fact", "chat_max_turns=5"])
        .env("CLIFFVM", &fake).env("CLAUDE_BIN", "/bin/echo").env("ARGS_OUT", &out)
        .stdout(Stdio::null()).status().unwrap();
    assert!(st.success());
    let args: Vec<String> = std::fs::read_to_string(&out).unwrap().lines().map(String::from).collect();
    let facts: Vec<&str> = args.windows(2).filter(|w| w[0] == "--fact").map(|w| w[1].as_str()).collect();
    assert_eq!(facts, ["request_keep_s=0", "chat_max_turns=5", "chat_claude_bin=/bin/echo"], "{args:?}");
    assert_eq!(args[0], "run");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn updated_is_readable_from_a_clifflet_and_entity_read_and_moves_with_a_write() {
    let (mut run, port) = seat("updated", true);
    w(port, "Note:u", "text", json!("one"));
    let first: f64 = read(port, "Note:u", "updated").trim().parse().unwrap_or_else(|_| panic!("a number"));
    assert!(first > 1.0e9, "an epoch time: {first}");
    assert_eq!(clifflet(port, "Note:u.updated > 1000000000"), "True");
    std::thread::sleep(Duration::from_millis(50));
    w(port, "Note:u", "text", json!("two"));
    let second: f64 = read(port, "Note:u", "updated").trim().parse().unwrap();
    assert!(second > first, "a write moves it: {first} -> {second}");
    // read-only: the door refuses a write of it
    let (r, _) = call(port, "entity_write", json!({"spec": "Note:u", "prop": "updated", "value": 5}));
    assert!(r.contains("refused") || r.contains("updated"), "{r}");
    assert_eq!(read(port, "Note:u", "updated").trim().parse::<f64>().unwrap(), second);
    assert!(terminate(&mut run));
}

#[test]
fn stats_eval_is_told_by_the_seat_and_readable_through_entity_read_and_a_clifflet() {
    let (mut run, port) = seat_with("stats_on", true, &["eval_stats_window_s=1"]);
    for i in 0..3 { w(port, "Note:s", "text", json!(format!("t{i}"))); }
    let top = wait("a published window with calls", || { let t = read(port, "Stats:eval", "top_total"); (t.contains("\"calls\"")).then_some(t) });
    let rows: J = serde_json::from_str(&top).unwrap();
    for k in ["class", "prop", "pack", "kind", "calls", "ms", "steps"] { assert!(rows[0].get(k).is_some(), "{k} in {top}"); }
    assert!(rows.as_array().unwrap().len() <= 20);
    assert_eq!(clifflet(port, "len(Stats:eval.top) <= 20"), "True");
    assert!(read(port, "Stats:eval", "life_calls").trim().parse::<u64>().unwrap() > 0);
    assert!(terminate(&mut run));
}

#[test]
fn the_boot_fact_eval_stats_false_turns_the_accounting_off() {
    let (mut run, port) = seat_with("stats_off", true, &["eval_stats=false", "eval_stats_window_s=1"]);
    w(port, "Note:s", "text", json!("x"));
    std::thread::sleep(Duration::from_millis(2500));
    let (r, _) = call(port, "entity_read", json!({"spec": "Stats:eval"}));
    assert!(!r.contains("top_total"), "no Stats:eval entity: {r}");
    assert!(terminate(&mut run));
}
