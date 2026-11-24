//! The model loads its OWN packs (docs/cliffvm.md, "Pack entities"): the real binary, the MCP door, no model, no claude.
//! Every wait has a timeout and the child is always reaped.
#![cfg(feature = "bin")]
mod common;

use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f"];

fn start(tag: &str, dir: Option<std::path::PathBuf>) -> (Run, u16) {
    let dir = dir.unwrap_or_else(|| tmp(tag));
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    let mut child = Command::new(cliffvm_bin()).current_dir(&dir)
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

fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "run_cliff", json!({"code": format!("${{'{}'}}['{}']", spec, prop)})).0 }
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
    assert!(read(port, "Pack:mine", "error").starts_with("null"), "a loaded pack has no error");
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
    assert!(read(port, "Pack:thief", "privileged").starts_with("null"), "the pack was not promoted");
}

fn wtext(port: u16, spec: &str, prop: &str, v: J) -> String { let (t, e) = call(port, "entity_write", json!({"spec": spec, "prop": prop, "value": v})); assert!(!e, "{t}"); t }

/// THE INCIDENT (#1143, 2026-11-14): a model-written pack that did not load was silent in the write's reply, and the model believed its banner carried the route.
/// The reply now says on its first lines `pack <name> loaded` or `pack <name> did not load: <the engine's error, verbatim>`; the pack that blocks it is named by that text.
#[test]
fn the_reply_of_a_pack_write_says_whether_the_pack_loaded_and_why_not() {
    let (_run, port) = start("packreply", None);
    let first = wtext(port, "Pack:notes", "text", json!("PACK notes 1\nBanner.text getter: 'notes'\n"));
    assert!(first.lines().any(|l| l == "pack notes loaded"), "{first}");
    // the second pack defines the same getter and does NOT import the first
    let second = wtext(port, "Pack:route", "text", json!("PACK route 1\nBanner.text getter: 'route'\nBanner.undone getter: 'x'\n"));
    let err = read_until_has(port, "Pack:route", "error", "belongs to pack notes");
    assert!(second.lines().nth(1).map_or(false, |l| l.starts_with("pack route did not load: ")), "{second}");
    assert!(second.contains(&err), "the engine's error verbatim: {second} / {err}");
    assert!(second.contains("add IMPORT notes 1"), "{second}");
    assert!(second.contains("Pack:route.loaded") && second.contains(".error"), "{second}");
    assert!(!second.contains("pack route loaded"), "{second}");
    // the first still loaded
    assert_eq!(read(port, "Pack:notes", "state"), "loaded");
    // a third pack that imports the first loads
    let third = wtext(port, "Pack:more", "text", json!("PACK more 1\nIMPORT notes 1\nBanner.text getter: 'more'\n"));
    assert!(third.lines().any(|l| l == "pack more loaded"), "{third}");
    // the same through a run_cliff block that writes a pack text
    let (t, e) = call(port, "run_cliff", json!({"code": "Pack:blk.text = 'PACK blk 1\\nBanner.text getter: 1\\n'"}));
    assert!(!e, "{t}");
    assert!(t.contains("pack blk did not load: "), "{t}");
}

/// wait until `eid.prop` no longer reads `old` (a missing entity reads as a sentence, not as the value)
fn gone_until(port: u16, eid: &str, prop: &str, old: &str) { wait(&format!("{eid}.{prop} to stop reading {old}"), || if read(port, eid, prop) != old { Some(()) } else { None }); }

fn probe_pack(name: &str, imp: &str, v: &str) -> String { format!("PACK {name} 1\n{imp}Lamp.probe getter: '{v}'\n") }

/// Unloading a pack (#1143): `Pack:<name>.retire = true` and an empty `text` each remove the pack's definitions AND what its getters left on entities; the reply says
/// `pack <name> unloaded`; the same name loads fresh afterwards; retiring a top layer re-exposes the one below.
#[test]
fn retire_and_an_empty_text_unload_a_pack_its_getters_read_as_absent_and_the_name_loads_fresh() {
    let (_run, port) = start("unload", None);
    let gone = |port: u16, what: &str| { let t = wait(&format!("{what} to read as absent"), || { let t = read(port, "Lamp:one", "probe"); if !t.contains("alive") && !t.contains("top") { Some(t) } else { None } }); t };
    wtext(port, "Lamp:one", "x", json!(1));
    // 1. retire
    assert!(wtext(port, "Pack:p3", "text", json!(probe_pack("p3", "", "alive3"))).contains("pack p3 loaded"));
    read_until(port, "Lamp:one", "probe", "alive3");
    let r = wtext(port, "Pack:p3", "retire", json!(true));
    assert!(r.lines().any(|l| l == "pack p3 unloaded"), "{r}");
    gone(port, "retire");
    gone_until(port, "Pack:p3", "text", &probe_pack("p3", "", "alive3"));
    // the same name loads fresh
    wtext(port, "Pack:p3", "text", json!(probe_pack("p3", "", "alive3b")));
    read_until(port, "Lamp:one", "probe", "alive3b");
    // 2. an empty text
    let r = wtext(port, "Pack:p3", "text", json!(""));
    assert!(r.lines().any(|l| l == "pack p3 unloaded"), "{r}");
    gone(port, "empty text");
    // and a restart-proof state: the pack does not come back as `waiting` with the old getter
    assert_eq!(read(port, "Pack:p3", "loaded"), "False");
    wtext(port, "Pack:p3", "text", json!(probe_pack("p3", "", "alive3c")));
    read_until(port, "Lamp:one", "probe", "alive3c");
    // 3. layers: retiring the top layer re-exposes the one below
    wtext(port, "Pack:p3", "retire", json!(true)); gone(port, "p3");
    wtext(port, "Pack:base", "text", json!(probe_pack("base", "", "below")));
    read_until(port, "Lamp:one", "probe", "below");
    wtext(port, "Pack:topl", "text", json!(probe_pack("topl", "IMPORT base 1\n", "top")));
    read_until(port, "Lamp:one", "probe", "top");
    wtext(port, "Pack:topl", "retire", json!(true));
    read_until(port, "Lamp:one", "probe", "below");
}

/// Death by null (#1143, operator 2026-11-14): `entity_write {spec, value: null}` with no prop destroys the entity (and its children), the mirror of a birth; a Pack unloads.
#[test]
fn a_plain_null_written_to_an_entity_destroys_it_and_its_children_and_a_pack_unloads() {
    let (_run, port) = start("death", None);
    wtext(port, "Pack:p5", "text", json!(probe_pack("p5", "", "alive5")));
    wtext(port, "Lamp:a", "x", json!(1)); wtext(port, "Lamp:a:kid", "x", json!(2)); wtext(port, "Lamp:ab", "x", json!(3));
    read_until(port, "Lamp:a", "probe", "alive5");
    let r = call(port, "entity_write", json!({"spec": "Lamp:a", "value": null})).0;
    assert!(r.starts_with("removed Lamp:a"), "{r}");
    gone_until(port, "Lamp:a", "x", "1");
    gone_until(port, "Lamp:a:kid", "x", "2");
    assert_eq!(read(port, "Lamp:ab", "x"), "3", "a sibling that only shares the prefix stays");
    // a call that forgets the prop and the value is refused, nothing is destroyed
    let (t, _) = call(port, "entity_write", json!({"spec": "Lamp:ab"}));
    assert!(t.contains("needs spec"), "{t}");
    assert_eq!(read(port, "Lamp:ab", "x"), "3");
    // the pack
    let r = call(port, "entity_write", json!({"spec": "Pack:p5", "value": null})).0;
    assert!(r.lines().any(|l| l.starts_with("pack p5 unloaded")), "{r}");
    wait("the getter reads as absent", || { let t = read(port, "Lamp:ab", "probe"); if !t.contains("alive5") { Some(()) } else { None } });
    wtext(port, "Pack:p5", "text", json!(probe_pack("p5", "", "fresh5")));
    read_until(port, "Lamp:ab", "probe", "fresh5");
    // `retire = true` still works
    wtext(port, "Lamp:ab", "retire", json!(true));
    gone_until(port, "Lamp:ab", "x", "3");
}

/// A pack's setter destroys with `<entity> = None`; ONLY a plain None: a reasoned null, an Error or a non-null value refuses and destroys nothing.
#[test]
fn a_setter_destroys_with_a_plain_none_and_a_reasoned_null_or_an_error_destroys_nothing() {
    let (_run, port) = start("deathset", None);
    let pack = "PACK reaper 1\nIMPORT door_tools 1\nPack.how on-setter reap:\n  ALWAYS:\n    Lamp:z = None\n\
                Pack.how2 on-setter reap2:\n  ALWAYS:\n    Lamp:z = ${'Nothing:here'}['x']\n\
                Pack.how3 on-setter reap3:\n  ALWAYS:\n    Lamp:z = 1 + None\n\
                Pack.how4 on-setter reap4:\n  ALWAYS:\n    Lamp:z = 5\n";
    wtext(port, "Lamp:z", "x", json!(1));
    wtext(port, "Pack:reaper", "text", json!(pack));
    wait("reaper", || { let t = read(port, "Pack:reaper", "state"); if t == "loaded" { Some(()) } else if t == "error" { panic!("{}", read(port, "Pack:reaper", "error")) } else { None } });
    for how in ["how2", "how3", "how4"] {
        wtext(port, "Pack:reaper", how, json!(1));
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(read(port, "Lamp:z", "x"), "1", "{how} destroyed nothing");
        let why = read_until_has(port, "Pack:reaper", "refused", "nothing was");   // the receipt says why, loudly
        assert!(why.contains("Lamp:z"), "{why}");
    }
    wtext(port, "Pack:reaper", "how", json!(1));
    gone_until(port, "Lamp:z", "x", "1");
}
