//! The Command driver's optional `stdin` (#1143): when the run value carries `stdin` (text) the child gets a piped stdin, the text is written to
//! it on its own thread (a big text cannot deadlock against the output pipes) and the pipe is CLOSED so the child sees end of input; with no
//! `stdin` the child's stdin stays null (it reads end of input at once). Every wait has a timeout: a driver that never closed the pipe would make `cat` hang.
#![cfg(feature = "bin")]
mod common;
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};

const BIN: &str = common::bin("cliffvm");
struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn start() -> (Run, u16) {
    // pid + clock is not unique: the clock ticks in microseconds on macOS, so tests starting in the same microsecond (parallel threads, one pid) shared ONE dir and ONE store
    // (two migrations raced: "duplicate column name: cls"); a process-wide counter makes every start its own dir
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let dir = std::env::temp_dir().join(format!("cliffvm-stdin-{}-{n}", std::process::id()));
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for f in ["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f"] { std::fs::copy(format!("{src}/{f}"), pd.join(f)).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("stderr.txt")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir };
    let line = run._lines.recv_timeout(common::DOOR_UP).unwrap_or_else(|e| panic!("the door came up: {e}; the child's stderr: {}", std::fs::read_to_string(run.dir.join("stderr.txt")).unwrap_or_default()));
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port)
}
fn call(port: u16, args: J) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(common::IO)).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": args}}).to_string();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut o = vec![]; let _ = s.read_to_end(&mut o);
    String::from_utf8_lossy(&o).to_string()
}
fn read(port: u16, spec: &str, prop: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(common::IO)).unwrap();
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": {"spec": spec, "prop": prop}}}).to_string();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut o = vec![]; let _ = s.read_to_end(&mut o);
    let t = String::from_utf8_lossy(&o).to_string();
    let i = t.find("\r\n\r\n").map(|i| i + 4).unwrap_or(0);
    let v: J = serde_json::from_str(&t[i..]).unwrap_or(J::Null);
    v["result"]["content"][0]["text"].as_str().unwrap_or("").to_string()
}
/// run `cmd` with the run value `extra` merged into {id, timeout_s}; -> the receipt's text (rc, stdout_bytes ...) once it lands
fn run_cmd(port: u16, name: &str, cmd: J, extra: J) -> J {
    call(port, json!({"spec": format!("Command:{name}"), "prop": "cmd", "value": cmd}));
    let mut v = json!({"id": "r1", "timeout_s": 20});
    for (k, x) in extra.as_object().unwrap() { v[k] = x.clone(); }
    call(port, json!({"spec": format!("Command:{name}"), "prop": "run", "value": v}));
    common::wait_for("the receipt", common::WAIT, || { let t = read(port, &format!("Command:{name}"), "run_result"); serde_json::from_str::<J>(&t).ok().filter(|v| v["rc"] != J::Null) })
}

#[test]
fn a_run_with_stdin_hands_the_text_to_the_child_and_closes_the_pipe() {
    let (_run, port) = start();
    let text = "quote \" newline\nbackslash \\ umlaut é emoji \u{1F600} end";
    let r = run_cmd(port, "withstdin", json!(["cat"]), json!({"stdin": text}));
    assert_eq!(r["rc"], 0, "cat reads to end of input, so the pipe was closed: {r}");
    assert_eq!(r["stdout"], text);
}

#[test]
fn a_run_without_stdin_leaves_the_childs_stdin_null() {
    let (_run, port) = start();
    let r = run_cmd(port, "nostdin", json!(["cat"]), json!({}));
    assert_eq!(r["rc"], 0, "{r}");
    assert_eq!(r["stdout"], "", "a null stdin is end of input at once");
}

#[test]
fn a_very_large_stdin_does_not_deadlock_against_the_output_pipes() {
    let (_run, port) = start();
    // cat echoes what it reads: a driver that wrote the whole text before reading the output would stall once both pipes are full
    let big = "x".repeat(2_000_000);
    let r = run_cmd(port, "bigstdin", json!(["cat"]), json!({"stdin": big}));
    assert_eq!(r["rc"], 0, "{}", &r.to_string()[..r.to_string().len().min(300)]);
    assert_eq!(r["stdout_bytes"], 2_000_000);
}
