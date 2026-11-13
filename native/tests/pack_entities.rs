//! The model loads its OWN packs (docs/cliffvm.md, "Pack entities"): the real binary, the MCP door, no model, no claude.
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

const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f"];

fn start(tag: &str, dir: Option<std::path::PathBuf>) -> (Run, u16) {
    let dir = dir.unwrap_or_else(|| tmp(tag));
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
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
fn read_until_has(port: u16, spec: &str, prop: &str, want: &str) -> String {
    wait(&format!("{spec}.{prop} has {want}"), || { let t = read(port, spec, prop); if t.contains(want) { Some(t) } else { None } })
}

/// The model's pack. It imports `door_tools` (which owns the class Pack) so its setter can hear `Pack:mine.mode`, and it makes its own
/// entity: the door's writes are checked like any pack's, and a class a pack declares is OWNED by it (the door can not write a Lamp).
fn lamps(glow: &str) -> String {
    format!("PACK mine 1\nIMPORT door_tools 1\nLamp.label getter: 'lamp ' + str(this.power)\nLamp.power on-setter glow:\n  ALWAYS: this.lit = '{glow}'\n\
             Pack.mode on-setter drive:\n  ALWAYS:\n    Lamp:desk = Lamp(kind: 'desk')\n    Lamp:desk.power = this.mode\n")
}

#[test]
fn the_model_writes_a_pack_entity_and_its_getters_and_setters_run_reload_and_refuse() {
    let (_run, port) = start("packs", None);
    // 1. the first text loads: a getter and a setter of a class the pack declares
    write(port, "Pack:mine", "text", json!(lamps("one")));
    read_until(port, "Pack:mine", "loaded", "True");
    assert_eq!(read_until(port, "Pack:mine", "state", "loaded"), "loaded");
    write(port, "Pack:mine", "mode", json!("on"));
    read_until(port, "Lamp:desk", "lit", "one");
    assert_eq!(read_until(port, "Lamp:desk", "label", "lamp on"), "lamp on");
    // 2. hot reload: a changed text replaces the pack
    write(port, "Pack:mine", "text", json!(lamps("two")));
    write(port, "Pack:mine", "mode", json!("dim"));
    read_until(port, "Lamp:desk", "lit", "two");
    read_until(port, "Lamp:desk", "label", "lamp dim");
    // 3. a text that does not parse: error set, the old behaviour still live
    write(port, "Pack:mine", "text", json!("Lamp.power on-setter:\n  this.lit = 1\n"));
    let e = read_until_has(port, "Pack:mine", "error", "pack mine");
    assert_eq!(read(port, "Pack:mine", "state"), "error");
    assert!(e.contains(": "), "{e}");
    write(port, "Pack:mine", "mode", json!("bright"));
    read_until(port, "Lamp:desk", "label", "lamp bright");
    read_until(port, "Lamp:desk", "lit", "two");
    // 4. a text the loader refuses (a setter reading a stale getter): the message is in `error`
    write(port, "Pack:mine", "text", json!("PACK mine 1\nIMPORT door_tools 1\nLamp.label getter: 'x' + str(this.power)\nLamp.power on-setter stale:\n  ALWAYS: this.lit = this.label\n"));
    let e = read_until_has(port, "Pack:mine", "error", "stale");
    assert!(e.contains("Lamp.label"), "{e}");
    write(port, "Pack:mine", "mode", json!("off"));
    read_until(port, "Lamp:desk", "label", "lamp off");                 // the pack that loaded last still runs
    // 5. a PACK line that names another pack is refused with what it found
    write(port, "Pack:mine", "text", json!("PACK other 1\nLamp.label getter: 'y'\n"));
    read_until_has(port, "Pack:mine", "error", "its PACK line says other");
    // 6. a good text clears the error
    write(port, "Pack:mine", "text", json!(lamps("three")));
    read_until(port, "Pack:mine", "state", "loaded");
    assert_eq!(read(port, "Pack:mine", "error"), "Pack:mine has no error");
}

#[test]
fn a_restart_loads_the_stored_packs_and_a_broken_text_falls_back_to_its_last_good_one() {
    let (mut run, port) = start("restart", None);
    write(port, "Pack:mine", "text", json!(lamps("one")));
    read_until(port, "Pack:mine", "state", "loaded");
    write(port, "Pack:mine", "text", json!("Lamp.power on-setter:\n  this.lit = 1\n"));
    read_until(port, "Pack:mine", "state", "error");
    // the seat dies with the broken text in the store (a kill: nothing is flushed on purpose)
    let _ = run.child.kill();
    let _ = run.child.wait();
    let dir = run.dir.clone();
    std::mem::forget(run);                                              // its folder (the store) is the next run's
    let (_run2, port2) = start("restart", Some(dir));
    read_until_has(port2, "Pack:mine", "error", "pack mine");           // the error is still shown...
    write(port2, "Pack:mine", "mode", json!("on"));                     // ...and the last good pack runs
    read_until(port2, "Lamp:desk", "lit", "one");
    read_until(port2, "Lamp:desk", "label", "lamp on");
}

/// A model's pack that owns the class Lamp and does NOT import door_tools: no setter of it reaches the door's Pack class either.
fn owner_pack() -> &'static str { "PACK mine 1\nLamp.label getter: 'lamp ' + str(this.power)\nLamp.power on-setter glow:\n  ALWAYS: this.lit = 'yes'\n" }

#[test]
fn boot_grants_the_tools_pack_the_privilege_at_start_and_it_says_who_granted_it() {
    let (_run, port) = start("grant", None);
    read_until(port, "Pack:door_tools", "privileged", "True");
    let by = read_until_has(port, "Pack:door_tools", "granted_by", "pack:boot:");
    assert!(by.starts_with("pack:boot:"), "{by}");
}

#[test]
fn the_tools_write_and_create_an_entity_of_a_class_the_model_pack_owns_directly() {
    let (_run, port) = start("direct", None);
    write(port, "Pack:mine", "text", json!(owner_pack()));
    read_until(port, "Pack:mine", "loaded", "True");
    // the privileged tools pack writes AND creates a Lamp (no setter workaround): the class is the model pack's, not the door's
    write(port, "Lamp:desk", "power", json!("on"));
    read_until(port, "Lamp:desk", "lit", "yes");
    assert_eq!(read_until(port, "Lamp:desk", "label", "lamp on"), "lamp on");
    // an unprivileged pack still can not: the model's pack grants nothing to itself, a setter of it writing a foreign class is refused
    write(port, "Pack:thief", "text", json!("PACK thief 1\nSpy.go on-setter s:\n  ALWAYS: Lamp:desk.power = 'stolen'\n"));
    read_until(port, "Pack:thief", "loaded", "True");
    write(port, "Spy:s", "go", json!(1));
    read_until_has(port, "Spy:s", "refused", "can't write");
    assert_eq!(read(port, "Lamp:desk", "power"), "on");
    // ...and it can not promote itself or another pack
    write(port, "Pack:thief", "text", json!("PACK thief 1\nSpy.go on-setter s:\n  ALWAYS: Pack:thief.privileged = True\n"));
    write(port, "Spy:s", "go", json!(2));
    read_until_has(port, "Spy:s", "refused", "can't grant");
    assert_eq!(read(port, "Pack:thief", "privileged"), "Pack:thief has no privileged");
}
