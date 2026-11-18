//! The edit app (membrane/packs/edit.c1f): a hunk applied to a file by a write, guarded and undoable.
//! The pack is plain c1f over the fs stdlib: `Edit:<n> {path, old, new, base_mtime}`, `apply` and `revert` are writes, the refusal
//! is a reason in `problem`, and the file is changed by the ordinary `File.write` effect (real files, in a temp folder).
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
    let d = std::env::temp_dir().join(format!("edit-{}-{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn root() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }

fn world(d: &std::path::Path) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for n in ["fs", "edit"] {
        let text = std::fs::read_to_string(root().join(format!("membrane/packs/{n}.c1f"))).unwrap();
        f.set_pack(n, &text).unwrap();
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(d.to_str().unwrap()));
    c.load("Edit:e".into(), "Edit".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, eid: &str, p: &str, v: J) {
    let w = Write { eid: eid.into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
fn land(c: &mut Core, f: &mut Fields) {
    let w = c.store.as_ref().unwrap().waiter();
    for _ in 0..3 {
        let t = std::time::Instant::now();
        while w.pending() > 0 && t.elapsed().as_secs() < 10 { w.wait(Some(std::time::Duration::from_millis(50))); let mut s = 30; f.settle(c, &H, 3.0, &mut s); }
        let mut s = 40; f.settle(c, &H, 3.0, &mut s);
    }
}
fn prop(c: &Core, p: &str) -> J { c.docs["Edit:e"].props.get(p).cloned().unwrap_or(J::Null) }

/// a world with `a.txt` holding `body`, and an edit of it drafted
fn draft(body: &str, old: &str, new: &str) -> (std::path::PathBuf, std::path::PathBuf, Core, Fields) {
    let d = dir();
    let file = d.join("a.txt");
    std::fs::write(&file, body).unwrap();
    let (mut c, mut f) = world(&d);
    put(&mut c, &mut f, "Edit:e", "path", json!(file.to_str().unwrap()));
    put(&mut c, &mut f, "Edit:e", "old", json!(old));
    put(&mut c, &mut f, "Edit:e", "new", json!(new));
    land(&mut c, &mut f);
    (d, file, c, f)
}

#[test]
fn a_unique_hunk_is_applied_and_the_receipt_is_the_diff() {
    let (_d, file, mut c, mut f) = draft("one\ntwo\nthree\n", "two", "2");
    assert_eq!(prop(&c, "problem"), J::Null, "a hunk that matches once is ready");
    assert_eq!(prop(&c, "hits").as_f64(), Some(1.0));
    assert!(prop(&c, "diff").as_str().unwrap().contains("- two") && prop(&c, "diff").as_str().unwrap().contains("+ 2"));
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\nthree\n", "a draft changes nothing (the dry run is the diff getter)");
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\n2\nthree\n");
    assert_eq!(prop(&c, "status"), json!("applied"));
    assert_eq!(prop(&c, "pre"), json!("one\ntwo\nthree\n"), "the pre-image is kept for revert");
    assert_eq!(prop(&c, "text"), json!("one\n2\nthree\n"), "the entity reads the file as it is now");
}

#[test]
fn a_hunk_that_matches_nowhere_or_twice_is_refused_with_its_reason() {
    let (_d, file, mut c, mut f) = draft("x\nx\ny\n", "z", "w");
    assert!(prop(&c, "problem").as_str().unwrap().contains("not found"));
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "x\nx\ny\n");
    assert_eq!(prop(&c, "status"), json!("refused"));
    put(&mut c, &mut f, "Edit:e", "old", json!("x"));
    land(&mut c, &mut f);
    assert!(prop(&c, "problem").as_str().unwrap().contains("matches 2 times"), "{:?}", prop(&c, "problem"));
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a2"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "x\nx\ny\n", "no guessing between two matches");
    assert_eq!(prop(&c, "status"), json!("refused"));
    assert!(prop(&c, "refused").as_str().unwrap().contains("matches 2 times"));
}

#[test]
fn a_file_that_changed_since_it_was_read_is_refused() {
    let (_d, file, mut c, mut f) = draft("one\ntwo\n", "two", "2");
    let mtime = prop(&c, "info")["mtime"].clone();
    assert!(mtime.is_number(), "the entity read the file's stat");
    put(&mut c, &mut f, "Edit:e", "base_mtime", json!(mtime.as_f64().unwrap() - 100.0));    // the agent read an older file
    land(&mut c, &mut f);
    assert!(prop(&c, "problem").as_str().unwrap().contains("changed since"), "{:?}", prop(&c, "problem"));
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\n");
    assert_eq!(prop(&c, "status"), json!("refused"));
    put(&mut c, &mut f, "Edit:e", "base_mtime", mtime);                                           // the agent read this one
    land(&mut c, &mut f);
    assert_eq!(prop(&c, "problem"), J::Null);
}

#[test]
fn revert_restores_the_pre_image_unless_the_file_moved_on() {
    let (_d, file, mut c, mut f) = draft("one\ntwo\nthree\n", "two", "2");
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    put(&mut c, &mut f, "Edit:e", "revert", json!({"id": "r1"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\nthree\n");
    assert_eq!(prop(&c, "status"), json!("reverted"));
    // apply again, then someone else edits the file: the revert must not clobber that
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a2"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\n2\nthree\n");
    std::fs::write(&file, "one\n2\nthree\nfour\n").unwrap();
    put(&mut c, &mut f, "Edit:e", "revert", json!({"id": "r2"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\n2\nthree\nfour\n", "the later change survives");
    assert_eq!(prop(&c, "status"), json!("refused"));
    assert!(prop(&c, "refused").as_str().unwrap().contains("changed since"), "{:?}", prop(&c, "refused"));
}

#[test]
fn apply_reads_the_file_again_so_a_change_made_outside_is_not_clobbered() {
    let (_d, file, mut c, mut f) = draft("one\ntwo\nthree\n", "two", "2");
    // somebody else edits the file after the draft was read, and nobody wrote `seen`
    std::fs::write(&file, "one\ntwo\nTHREE\nfour\n").unwrap();
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\n2\nTHREE\nfour\n", "the hunk lands on the file as it is now, the other change stays");
    assert_eq!(prop(&c, "status"), json!("applied"));
    assert_eq!(prop(&c, "pre"), json!("one\ntwo\nTHREE\nfour\n"), "the pre-image is the file that was really replaced");
}

#[test]
fn apply_with_a_base_mtime_is_refused_when_the_file_changed_after_the_draft() {
    let (_d, file, mut c, mut f) = draft("one\ntwo\n", "two", "2");
    let mtime = prop(&c, "info")["mtime"].clone();
    put(&mut c, &mut f, "Edit:e", "base_mtime", mtime);
    land(&mut c, &mut f);
    assert_eq!(prop(&c, "problem"), J::Null);
    std::thread::sleep(std::time::Duration::from_millis(1100));      // an mtime that differs on every filesystem
    std::fs::write(&file, "one\ntwo\nthree\n").unwrap();
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(&file).unwrap(), "one\ntwo\nthree\n", "the lock held: nothing was written");
    assert_eq!(prop(&c, "status"), json!("refused"));
    assert!(prop(&c, "refused").as_str().unwrap().contains("changed since"), "{:?}", prop(&c, "refused"));
}

#[test]
fn a_missing_file_is_refused_not_left_pending() {
    let d = dir();
    let (mut c, mut f) = world(&d);
    put(&mut c, &mut f, "Edit:e", "path", json!(d.join("nope.txt").to_str().unwrap()));
    put(&mut c, &mut f, "Edit:e", "old", json!("a"));
    put(&mut c, &mut f, "Edit:e", "new", json!("b"));
    land(&mut c, &mut f);
    put(&mut c, &mut f, "Edit:e", "apply", json!({"id": "a1"}));
    land(&mut c, &mut f);
    assert_eq!(prop(&c, "status"), json!("refused"), "{:?}", prop(&c, "problem"));
    assert!(prop(&c, "refused").as_str().unwrap().contains("no such file"), "{:?}", prop(&c, "refused"));
    assert!(!d.join("nope.txt").exists(), "a refused edit creates nothing");
}
