//! SemText through the MCP door (#1143): the real binary on the shipped packs, a pack the MODEL writes makes a SemText, `entity_read` shows its
//! layers. No model, no claude.
//! Every wait has a timeout and the child is always reaped.
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


const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f"];

fn start(tag: &str) -> (Run, u16) {
    let dir = tmp(tag);
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port)
}

fn http(port: u16, body: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    let raw = format!("POST /mcp HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    String::from_utf8_lossy(&out[i + 4..]).to_string()
}

fn call(port: u16, tool: &str, args: J) -> (String, bool) {
    let body = http(port, &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    let r: J = serde_json::from_str(&body).unwrap_or_else(|e| panic!("{e}: {body}"));
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

fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": spec, "prop": prop})).0 }
fn write(port: u16, spec: &str, prop: &str, v: J) { let (t, e) = call(port, "entity_write", json!({"spec": spec, "prop": prop, "value": v})); assert!(!e, "{t}"); }
fn read_until(port: u16, spec: &str, prop: &str, want: &str) -> String {
    wait(&format!("{spec}.{prop} = {want}"), || { let t = read(port, spec, prop); if t == want { Some(t) } else { None } })
}

#[test]
fn entity_read_shows_the_layers_of_a_semtext_a_pack_of_the_model_made_and_a_text_change_recomputes_them() {
    let (_run, port) = start("semtext-door");
    let pack = "PACK mine 1\nIMPORT semtext 1\nLamp.say on-setter make:\n  ALWAYS:\n    Lamp:a:body = SemText(text: this.say)\n";
    write(port, "Pack:mine", "text", json!(pack));
    read_until(port, "Pack:mine", "state", "loaded");
    write(port, "Lamp:a", "say", json!("Open my_fn now. Then stop."));
    let words = wait("the words of the body", || { let t = read(port, "Lamp:a:body", "words"); if t.contains("Open") { Some(t) } else { None } });
    assert!(words.contains("my_fn") && words.contains("\"start\""), "{words}");
    let whole = wait("the whole read", || { let t = call(port, "entity_read", json!({"spec": "Lamp:a:body"})).0; if t.contains("sentences") { Some(t) } else { None } });
    for k in ["text", "sentences", "words", "identifiers", "paths"] { assert!(whole.contains(&format!("\"{k}\"")), "entity_read lacks {k}: {whole}"); }
    assert!(whole.contains("Then stop."), "{whole}");
    write(port, "Lamp:a:body", "text", json!("Only one."));
    wait("the recomputed sentences", || { let t = read(port, "Lamp:a:body", "sentences"); if t.contains("Only one.") && !t.contains("Then stop.") { Some(t) } else { None } });
}
