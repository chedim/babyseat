//! The stdlib drivers (#1044): fs reads as asynchronous getter built-ins; File and Command effects run by the engine
//! off its lock, their receipts landing as `<verb>_result`; entity scripts never trigger one.
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn dir() -> std::path::PathBuf {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let d = std::env::temp_dir().join(format!("drivers-{}-{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
    std::fs::create_dir_all(&d).unwrap();
    d
}

const PACK: &str = "PACK std 1
File.text getter: read(this.path) if this.path else None
File.info getter: stat(this.path) if this.path else None
Dir.names getter: [prop(e, 'name') for e in (listdir(this.path) or [])] if this.path else []
Command.kind getter: 'command'
";

fn world(d: &std::path::Path) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("std", PACK).unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(d.to_str().unwrap()));
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, eid: &str, p: &str, v: J, writer: &str) {
    let w = Write { eid: eid.into(), prop: p.into(), new: v, old: J::Null, writer: writer.into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
fn land(c: &mut Core, f: &mut Fields) {
    let w = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    while w.pending() > 0 && t.elapsed().as_secs() < 10 { w.wait(Some(std::time::Duration::from_millis(100))); let mut s = 30; f.settle(c, &H, 3.0, &mut s); }
    let mut s = 40; f.settle(c, &H, 3.0, &mut s);
}

#[test]
fn files_are_read_listed_and_statted_by_getters() {
    let d = dir();
    std::fs::write(d.join("a.txt"), "hello").unwrap();
    std::fs::create_dir(d.join("sub")).unwrap();
    let (mut c, mut f) = world(&d);
    c.load("File:a".into(), "File".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    c.load("Dir:root".into(), "Dir".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "File:a", "path", json!("a.txt"), "person");               // relative: the seat's folder
    put(&mut c, &mut f, "Dir:root", "path", json!(d.to_str().unwrap()), "person");
    land(&mut c, &mut f);
    assert_eq!(c.docs["File:a"].props["text"], json!("hello"));
    assert_eq!(c.docs["File:a"].props["info"]["size"], json!(5));
    assert_eq!(c.docs["Dir:root"].props["names"], json!(["a.txt", "sub"]));
}

#[test]
fn a_file_write_is_an_effect_with_a_receipt_and_scripts_are_refused() {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("File:n".into(), "File".into(), json!({"path": "notes/n.txt"}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "File:n", "write", json!({"id": "w1", "content": "line one\n"}), "person");
    assert_eq!(c.docs["File:n"].props["write"], json!(""));                           // the slot is consumed at once
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(d.join("notes/n.txt")).unwrap(), "line one\n");
    assert_eq!(c.docs["File:n"].props["write_result"]["id"], json!("w1"));
    assert_eq!(c.docs["File:n"].props["write_rc"], json!(0));
    put(&mut c, &mut f, "File:n", "write", json!({"id": "w2", "content": "x"}), "script:Component:x");
    assert_eq!(c.docs["File:n"].props["write_result"]["rc"], json!(2));
    assert_eq!(std::fs::read_to_string(d.join("notes/n.txt")).unwrap(), "line one\n");
}

#[test]
fn a_command_runs_off_the_engine_and_can_be_stopped() {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("Command:echo".into(), "Command".into(), json!({"cmd": ["sh", "-c", "echo hi $0 && echo oops >&2", "there"]}).as_object().unwrap().clone(), None, 0.0);
    c.load("Command:sleep".into(), "Command".into(), json!({"cmd": "sleep 30"}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "Command:echo", "run", json!({"id": "r1"}), "person");
    land(&mut c, &mut f);
    let r = &c.docs["Command:echo"].props["run_result"];
    assert_eq!(r["rc"], json!(0));
    assert_eq!(r["stdout"], json!("hi there\n"));
    assert_eq!(r["stderr"], json!("oops\n"));
    let t = std::time::Instant::now();
    put(&mut c, &mut f, "Command:sleep", "run", json!({"id": "s1"}), "person");
    assert!(t.elapsed().as_millis() < 500, "the engine waited for the command");
    std::thread::sleep(std::time::Duration::from_millis(200));
    put(&mut c, &mut f, "Command:sleep", "stop", json!({"id": "x"}), "person");
    land(&mut c, &mut f);
    assert_eq!(c.docs["Command:sleep"].props["stop_result"]["head"], json!("stopped"));
    assert_ne!(c.docs["Command:sleep"].props["run_rc"], json!(0));
    assert!(t.elapsed().as_secs() < 10);
}

// ---- no work under a temporary folder (the operator, 2026-11-12: a battery restart wiped work done under /tmp) ----

fn refused_write(path: &str) -> J {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("File:w".into(), "File".into(), json!({"path": path}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "File:w", "write", json!({"id": "t", "content": "x"}), "model");
    land(&mut c, &mut f);
    c.docs["File:w"].props.get("write_result").cloned().unwrap_or(J::Null)
}

#[test]
fn a_file_is_not_written_under_a_temporary_folder() {
    for p in ["/tmp/work/a.txt", "/private/tmp/work/a.txt", "/var/tmp/a.txt", "/var/folders/xx/T/a.txt", "/private/var/folders/xx/T/a.txt"] {
        let r = refused_write(p);
        assert_eq!(r["rc"], json!(2), "{p}: {r}");
        assert!(r["head"].as_str().unwrap().contains("temporary folder"), "{r}");
        assert!(!std::path::Path::new(p).exists(), "{p} was written");
    }
}

#[test]
fn a_move_or_delete_into_or_out_of_a_temporary_folder_is_refused_too() {
    let d = dir();
    std::fs::write(d.join("keep.txt"), "x").unwrap();
    let (mut c, mut f) = world(&d);
    c.load("File:m".into(), "File".into(), json!({"path": d.join("keep.txt").to_str().unwrap()}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "File:m", "move", json!({"id": "m", "to": "/tmp/elsewhere.txt"}), "model");
    land(&mut c, &mut f);
    let r = c.docs["File:m"].props.get("move_result").cloned().unwrap();
    assert_eq!(r["rc"], json!(2), "{r}");
    assert!(d.join("keep.txt").exists());
}

#[test]
fn the_seats_own_folder_may_be_a_temporary_one_and_reading_a_temporary_file_is_fine() {
    let d = dir();                                          // a throwaway seat's data folder lives under the OS temp dir
    let (mut c, mut f) = world(&d);
    c.load("File:ok".into(), "File".into(), json!({"path": d.join("ok.txt").to_str().unwrap()}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "File:ok", "write", json!({"id": "w", "content": "fine"}), "model");
    land(&mut c, &mut f);
    assert_eq!(c.docs["File:ok"].props["write_result"]["rc"], json!(0));
    assert_eq!(std::fs::read_to_string(d.join("ok.txt")).unwrap(), "fine");
}

fn run_result(cwd: Option<&str>, args: J) -> J {
    let d = dir();
    let (mut c, mut f) = world(&d);
    let mut p = json!({"cmd": ["echo"]}).as_object().unwrap().clone();
    if let Some(w) = cwd { p.insert("cwd".into(), json!(w)); }
    c.load("Command:c".into(), "Command".into(), p, None, 0.0);
    put(&mut c, &mut f, "Command:c", "run", json!({"id": "r", "args": args}), "model");
    land(&mut c, &mut f);
    c.docs["Command:c"].props.get("run_result").cloned().unwrap_or(J::Null)
}

#[test]
fn a_command_does_not_run_in_or_on_a_temporary_folder() {
    let r = run_result(Some("/tmp"), json!(["hi"]));
    assert_eq!(r["rc"], json!(2), "{r}");
    assert!(r["head"].as_str().unwrap().contains("temporary folder"));
    let r = run_result(None, json!(["clone", "/private/tmp/wt"]));
    assert_eq!(r["rc"], json!(2), "{r}");
    let r = run_result(None, json!(["hello", "world"]));
    assert_eq!(r["rc"], json!(0), "{r}");
}
