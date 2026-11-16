//! PRIVILEGE (docs/c1f-layers.md, "Privilege"): exemption from rule B (a pack writes only classes it owns or imports) ONLY. Rule A stays
//! (only the owner defines getters and setters on a class). The grant is a write, `Pack:<name>.privileged = true`, by a privileged writer;
//! the host records `granted_by` from the writer; the boot pack is the root of trust.
use membrane_vm::expr;
use membrane_vm::host::{is_outside, Host, Line, Options};
use serde_json::{json, Map, Value as J};
use std::cell::Cell;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

thread_local! { static NOW: Cell<u64> = const { Cell::new(0) }; }
fn fake() -> u64 { NOW.with(|n| n.get()) }

fn ticking() -> Box<dyn Fn() -> f64 + Send + Sync> {
    let n = Arc::new(AtomicU64::new(0));
    Box::new(move || 1000.0 + n.fetch_add(1, Ordering::SeqCst) as f64)
}
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn host() -> Host {
    NOW.with(|n| n.set(0));
    expr::set_clock(Some(fake));
    let h = Host::open({ let mut o = Options::new(None); o.clock = ticking(); o.effects = false; o }).unwrap();
    h.rebuild();
    h
}

/// `own` owns Cls; `plain` is a pack that imports nothing and tries to write it, several ways.
const OWN: &str = "PACK own 1\nCls.tag getter: 'tag'\n";
const PLAIN: &str = "PACK plain 1\nTrig.go on-setter w:\n  ALWAYS: Cls:a.val = 1\nTrig.mk on-setter c:\n  ALWAYS: Cls:made.val = 1\n";

fn tried(h: &Host) { h.write("Trig:t", "go", json!(1), "person"); h.write("Trig:t", "mk", json!(1), "person"); }

#[test]
fn an_unprivileged_pack_still_cannot_write_or_create_a_class_it_does_not_own() {
    let h = host();
    h.load_pack("own", OWN).unwrap();
    h.load_pack("plain", PLAIN).unwrap();
    h.born("Cls:a", "Cls", props(json!({})), "person");
    h.born("Trig:t", "Trig", props(json!({})), "person");
    tried(&h);
    assert_eq!(h.get("Cls:a", "val"), J::Null);
    assert!(h.get("Cls:a", "refused").as_str().map_or(false, |r| r.contains("can't write")) || h.get("Trig:t", "refused").as_str().map_or(false, |r| r.contains("can't write")), "the receipt: {:?}", h.snapshot());
    assert!(!h.snapshot().contains_key("Cls:made"), "creation of a foreign class is dropped");
}

#[test]
fn a_granted_pack_writes_and_creates_any_class_and_a_revoke_takes_it_back() {
    let h = host();
    h.load_pack("own", OWN).unwrap();
    h.load_pack("plain", PLAIN).unwrap();
    h.born("Cls:a", "Cls", props(json!({})), "person");
    h.born("Trig:t", "Trig", props(json!({})), "person");
    assert!(!h.pack_privileged("plain"));
    h.born("Pack:plain", "Pack", props(json!({"privileged": true})), "person");   // the grant (an outside write: the person's)
    assert!(h.pack_privileged("plain"));
    assert_eq!(h.get("Pack:plain", "granted_by"), json!("person"));
    tried(&h);
    assert_eq!(h.get("Cls:a", "val"), json!(1));
    assert_eq!(h.get("Cls:made", "val"), json!(1), "created");
    // (g) revoke: a write of false; the next tries are refused again
    h.write("Pack:plain", "privileged", json!(false), "person");
    assert!(!h.pack_privileged("plain"));
    h.write("Cls:a", "val", json!(0), "person");
    h.write("Trig:t", "go", json!(2), "person");
    assert_eq!(h.get("Cls:a", "val"), json!(0), "refused after the revoke");
}

#[test]
fn a_creation_of_a_class_no_pack_owns_works_for_a_privileged_pack_only() {
    let h = host();
    h.load_pack("plain", "PACK plain 1\nTrig.mk on-setter c:\n  ALWAYS: Nobody:x.val = 1\n").unwrap();
    h.born("Trig:t", "Trig", props(json!({})), "person");
    h.write("Trig:t", "mk", json!(1), "person");
    assert!(!h.snapshot().contains_key("Nobody:x"));
    h.born("Pack:plain", "Pack", props(json!({"privileged": true})), "person");
    h.write("Trig:t", "mk", json!(2), "person");
    assert_eq!(h.get("Nobody:x", "val"), json!(1));
}

/// a pack whose setter tries to promote `Pack:x`, by every way a pack can write
fn promoters() -> Vec<(&'static str, String)> {
    vec![
        ("direct", "PACK evil 1\nTrig.go on-setter w:\n  ALWAYS: Pack:x.privileged = True\n".into()),
        ("computed", "PACK evil 1\nTrig.go on-setter w:\n  ALWAYS: Pack:x.['privileged'] = True\n".into()),
        ("constructor", "PACK evil 1\nTrig.go on-setter w:\n  ALWAYS: Pack:x = Pack(privileged: True)\n".into()),
        ("chain", "PACK evil 1\nTrig.go on-setter w:\n  ALWAYS: Hop:h.n = 1\nHop.n on-setter v:\n  ALWAYS: Pack:x.privileged = True\n".into()),
    ]
}

#[test]
fn an_unprivileged_pack_cannot_set_privileged_by_any_path() {
    // `owned`: another pack owns the class Pack (as door_tools does) and the evil pack IMPORTS it, so rule B lets it write Pack entities:
    // only the grant check stands between it and the flag
    for owned in [false, true] {
        for exists in [true, false] {
            for (how, text) in promoters() {
                let h = host();
                let text = if owned { text.replacen("PACK evil 1\n", "PACK evil 1\nIMPORT packown 1\n", 1) } else { text };
                if owned { h.load_pack("packown", "PACK packown 1\nPack.state getter: 'x'\n").unwrap(); }
                h.load_pack("evil", &text).unwrap();
                if exists { h.born("Pack:x", "Pack", props(json!({})), "person"); }
                h.born("Trig:t", "Trig", props(json!({})), "person");
                h.write("Trig:t", "go", json!(1), "person");
                assert!(!h.pack_privileged("x"), "{how} (owned {owned}, exists {exists}) granted");
                assert_eq!(h.get("Pack:x", "privileged"), J::Null, "{how} (owned {owned}, exists {exists}) wrote the flag");
            }
        }
    }
}

#[test]
fn a_privileged_pack_still_cannot_define_getters_or_setters_on_a_class_it_does_not_own_rule_a() {
    let h = host();
    h.load_pack("own", OWN).unwrap();
    h.born("Pack:pp", "Pack", props(json!({"privileged": true})), "person");
    let e = h.load_pack("pp", "PACK pp 1\nCls.extra getter: 'x'\n").unwrap_err();
    assert!(e.contains("belongs to pack own"), "{e}");
    let e = h.load_pack("pp", "PACK pp 1\nCls.n on-setter s:\n  ALWAYS: this.m = 1\n").unwrap_err();
    assert!(e.contains("belongs to pack own"), "{e}");
    h.load_pack("pp", "PACK pp 1\nIMPORT own 1\nCls.extra getter: 'x'\n").unwrap();   // with the import it is the old rule: allowed
}

#[test]
fn the_grant_is_recorded_with_its_writer_a_pack_setter_grants_and_the_grant_outlives_the_granter() {
    let h = host();
    h.load_pack("root", "PACK root 1\nTrig.go on-setter grant-kid:\n  ALWAYS: Pack:kid.privileged = True\n").unwrap();
    h.born("Pack:kid", "Pack", props(json!({"text": "PACK kid 1\nBox.k getter: 'k'\n"})), "person");
    h.born("Trig:t", "Trig", props(json!({})), "person");
    h.write("Trig:t", "go", json!(1), "person");
    assert!(!h.pack_privileged("kid"), "root is not privileged: its grant is refused");
    h.born("Pack:root", "Pack", props(json!({"privileged": true})), "person");
    h.write("Trig:t", "go", json!(2), "person");
    assert!(h.pack_privileged("kid"));
    assert_eq!(h.get("Pack:kid", "granted_by"), json!("pack:root:grant-kid"), "the writer that wrote it");
    assert_eq!(h.get("Pack:kid", "privileged"), json!(true));
    // the journal holds the outside writes only: the grant of a setter and `granted_by` are derived
    let j = h.journal();
    assert!(j.iter().all(|l| is_outside(l.writer())), "{j:?}");
    assert!(!j.iter().any(|l| matches!(l, Line::Write { prop, .. } if prop == "granted_by")), "{j:?}");
    // revoking the granter does not revoke what it granted
    h.write("Pack:root", "privileged", json!(false), "person");
    assert!(!h.pack_privileged("root") && h.pack_privileged("kid"));
    // a privileged writer revokes
    h.born("Pack:rev", "Pack", props(json!({"privileged": true, "text": "PACK rev 1\nRev.go on-setter r:\n  ALWAYS: Pack:kid.privileged = False\n"})), "person");
    h.born("Rev:r", "Rev", props(json!({})), "person");
    h.write("Rev:r", "go", json!(3), "person");
    assert!(!h.pack_privileged("kid"));
    assert_eq!(h.get("Pack:kid", "granted_by"), J::Null);
}

#[test]
fn a_grant_for_a_file_loaded_pack_does_not_remove_it_and_survives_a_restart() {
    let file = std::env::temp_dir().join(format!("priv-{}-{}.sqlite3", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos())).to_string_lossy().to_string();
    let open = || { let mut o = Options::new(Some(file.clone())); o.clock = ticking(); o.effects = false; Host::open(o).unwrap() };
    let t = "PACK filed 1\nBox.label getter: 'f' + str(this.w)\n";
    NOW.with(|n| n.set(0));
    expr::set_clock(Some(fake));
    {
        let h = open();
        h.load_pack("filed", t).unwrap();
        h.rebuild();
        h.born("Box:a", "Box", props(json!({"w": 1})), "person");
        h.born("Pack:filed", "Pack", props(json!({"privileged": true})), "person");   // no text
        assert!(h.pack_privileged("filed"));
        h.write("Box:a", "w", json!(2), "person");
        assert_eq!(h.get("Box:a", "label"), json!("f2"), "the pack still runs");
        assert_eq!(h.get("Pack:filed", "loaded"), json!(true));
    }
    let h = open();                                              // the seat's start: the folder's packs, then the stored Pack rows
    h.load_pack("filed", t).unwrap();
    h.load_stored_packs();
    h.rebuild();
    assert!(h.pack_privileged("filed"), "the stored grant is read back");
    h.born("Box:a", "Box", props(json!({"w": 3})), "person");      // (the engine starts empty: rows are not loaded back into it)
    assert_eq!(h.get("Box:a", "label"), json!("f3"), "a Pack entity with no text does not remove a file-loaded pack");
    // an explicit empty text does
    h.write("Pack:filed", "text", json!(""), "person");
    assert_eq!(h.get("Pack:filed", "loaded"), json!(false));
    let _ = std::fs::remove_file(&file);
}

#[test]
fn a_removed_pack_leaves_no_stale_values_of_its_getters() {
    let h = host();
    h.born("Box:a", "Box", props(json!({"w": 1, "keep": "me"})), "person");
    h.born("Pack:mine", "Pack", props(json!({"text": "PACK mine 1\nBox.label getter: 'l' + str(this.w)\nBox.size getter: this.w * 10\n"})), "person");
    assert_eq!((h.get("Box:a", "label"), h.get("Box:a", "size")), (json!("l1"), json!(10)));
    h.write("Pack:mine", "text", json!(""), "person");
    assert_eq!(h.get("Box:a", "label"), J::Null, "the getter's value is cleared");
    assert_eq!(h.get("Box:a", "size"), J::Null);
    assert_eq!((h.get("Box:a", "w"), h.get("Box:a", "keep")), (json!(1), json!("me")), "plain properties stay");
    assert_eq!(h.rows()["Box:a"]["props"].get("label"), None, "and the store row");
    let j = h.journal();
    assert!(!j.iter().any(|l| matches!(l, Line::Write { prop, .. } if prop == "label" || prop == "size")), "journal-neutral: {j:?}");
    // a getter another pack still defines is kept
    h.born("Pack:a", "Pack", props(json!({"text": "PACK a 1\nBox.label getter: 'a'\n"})), "person");
    h.born("Pack:b", "Pack", props(json!({"text": "PACK b 1\nIMPORT a 1\nBox.size getter: 1\n"})), "person");
    h.write("Pack:b", "text", json!(""), "person");
    assert_eq!(h.get("Box:a", "size"), J::Null);
    assert_eq!(h.get("Box:a", "label"), json!("a"));
}

#[test]
fn the_journal_replays_a_grant_and_ends_in_the_same_privilege_state() {
    let h = host();
    let root = "PACK root 1\nTrig.go on-setter grant-kid:\n  ALWAYS: Pack:kid.privileged = True\n";
    h.load_pack("root", root).unwrap();
    h.born("Pack:kid", "Pack", props(json!({"text": "PACK kid 1\nBox.k getter: 'k'\n"})), "person");
    h.born("Pack:root", "Pack", props(json!({"privileged": true})), "person");
    h.born("Trig:t", "Trig", props(json!({})), "person");
    h.write("Trig:t", "go", json!(1), "person");
    assert!(h.pack_privileged("kid") && h.pack_privileged("root"));
    let j = h.journal();
    let r = Host::open({ let mut o = Options::new(None); o.clock = ticking(); o.effects = false; o }).unwrap();
    r.load_pack("root", root).unwrap();
    r.rebuild();
    r.replay(&j);
    assert!(r.pack_privileged("kid") && r.pack_privileged("root"));
    assert_eq!(r.get("Pack:kid", "granted_by"), h.get("Pack:kid", "granted_by"));
    assert_eq!(r.get("Pack:kid", "privileged"), json!(true));
    assert_eq!(r.snapshot(), h.snapshot());
}
