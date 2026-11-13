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
        .args(["run", pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
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
