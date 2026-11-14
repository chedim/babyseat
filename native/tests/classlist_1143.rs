//! The class list (#1143, operator 2026-11-13): an entity has NO privileged own class. Its classes are ONE ordered list, its `parents`
//! (`cls:<name>` entries); the nearest class is the first entry, the root `Entity` is implicitly last. The eid prefix is only the BIRTH
//! DEFAULT (no `parents` = the list [prefix]; a born entity with extra `parents` gets the prefix class put first); after birth it has no
//! standing: a write of `parents` reorders, adds or replaces classes, and getters, setters, kind matching and rule B follow the list.
use membrane_vm::host::{self, Host, Line, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn fresh(packs: &[(&str, &str)]) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    for (n, t) in packs { h.load_pack(n, t).unwrap_or_else(|e| panic!("{n}: {e}")); }
    h.rebuild();
    h
}

const PACK: &str = "PACK abc 1\nOwn.text getter: 'own'\nAlpha.text getter: 'alpha'\nBeta.text getter: 'beta'\nAlpha.sa getter: 'a:' + str(this.n)\nBeta.sb getter: 'b:' + str(this.n)\n";

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
const B: &str = "membrane::Banner:owner:main";

#[test]
fn writing_parents_to_a_class_with_its_own_text_getter_replaces_the_default_banner_text() {
    let own = "PACK own 1\nIMPORT default_banner 1\nOwnBanner.text getter: 'own text'\n";
    let seat = |before: &dyn Fn(&Host)| {
        let h = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
        let r = repo();
        let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
        for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
            h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
        }
        h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
        h.rebuild();
        before(&h);
        host::boot_started(&h, &Map::new(), true);
        h.pump();
        h
    };
    let t = |h: &Host| h.get(B, "text").as_str().unwrap_or_default().to_string();
    // pack first, then the write
    let h = seat(&|_| {}); h.load_pack("own", own).unwrap(); h.rebuild(); h.pump();
    assert!(t(&h).contains("CONVERSATION: main"), "the default shows before the write");
    h.write(B, "parents", json!(["cls:ownbanner"]), "person"); h.pump();
    assert_eq!(t(&h), "own text");
    // the write first, then the pack
    let h = seat(&|_| {}); h.write(B, "parents", json!(["cls:ownbanner"]), "person"); h.pump();
    h.load_pack("own", own).unwrap(); h.rebuild(); h.pump();
    assert_eq!(t(&h), "own text");
    // and back: the list [cls:banner] gives the default again
    h.write(B, "parents", json!(["cls:banner"]), "person"); h.pump();
    assert!(t(&h).contains("CONVERSATION: main"), "{}", t(&h));
}

#[test]
fn a_birth_without_parents_is_its_prefix_class_and_one_with_extras_gets_the_prefix_first() {
    let h = fresh(&[("abc", PACK)]);
    h.born("Own:plain", "Own", Map::new(), "person");
    h.born("Own:x", "Own", props(json!({"parents": ["cls:alpha", "cls:beta"]})), "person");
    assert_eq!(h.get("Own:plain", "text"), json!("own"));
    assert_eq!(h.get("Own:x", "text"), json!("own"), "the prefix class is the birth default, first");
    assert_eq!(h.get("Own:x", "parents"), json!(["cls:own", "cls:alpha", "cls:beta"]));
    h.born("Other:y", "Other", props(json!({"parents": ["cls:beta", "cls:own"]})), "person");
    assert_eq!(h.get("Other:y", "parents"), json!(["cls:other", "cls:beta", "cls:own"]));
    assert_eq!(h.get("Other:y", "text"), json!("beta"), "Other has no getter: the next class answers");
}

#[test]
fn reordering_the_list_reorders_the_winner_and_the_prefix_has_no_standing_after_birth() {
    let h = fresh(&[("abc", PACK)]);
    h.born("Own:x", "Own", props(json!({"parents": ["cls:alpha", "cls:beta"]})), "person");
    h.write("Own:x", "parents", json!(["cls:beta", "cls:alpha", "cls:own"]), "person");
    assert_eq!(h.get("Own:x", "text"), json!("beta"));
    h.write("Own:x", "parents", json!(["cls:alpha", "cls:beta"]), "person");
    assert_eq!(h.get("Own:x", "text"), json!("alpha"), "the eid prefix does not stay in front");
    h.write("Own:x", "parents", json!(["cls:own"]), "person");
    assert_eq!(h.get("Own:x", "text"), json!("own"));
}

#[test]
fn replacing_the_list_drops_the_old_classes_getters_and_computes_the_new_ones() {
    let h = fresh(&[("abc", PACK)]);
    h.born("Thing:x", "Thing", props(json!({"n": 3, "parents": ["cls:alpha"]})), "person");
    assert_eq!((h.get("Thing:x", "sa"), h.get("Thing:x", "sb")), (json!("a:3"), J::Null));
    h.write("Thing:x", "parents", json!(["cls:beta"]), "person");
    assert_eq!(h.get("Thing:x", "sa"), J::Null, "the dropped class's value is cleared");
    assert_eq!(h.get("Thing:x", "sb"), json!("b:3"));
    assert_eq!(h.rows()["Thing:x"]["props"].get("sa"), None, "and the store row");
    assert_eq!(h.get("Thing:x", "n"), json!(3), "plain properties stay");
}

#[test]
fn a_kinds_setter_binds_through_the_list_and_stops_when_the_class_is_dropped() {
    let p = "PACK s 1\nAlpha.go on-setter r:\n  WHEN this.go == True:\n    this.did = True\n";
    let h = fresh(&[("s", p)]);
    h.born("Thing:x", "Thing", props(json!({})), "person");
    h.write("Thing:x", "parents", json!(["cls:alpha"]), "person");
    h.write("Thing:x", "go", json!(true), "person");
    assert_eq!(h.get("Thing:x", "did"), json!(true), "bound by the list alone: no cls:alpha entity needed");
    h.born("Alpha:own", "Alpha", Map::new(), "person");
    h.write("Alpha:own", "go", json!(true), "person");
    assert_eq!(h.get("Alpha:own", "did"), json!(true), "the birth default binds as before");
    h.write("Alpha:own", "parents", json!(["cls:beta"]), "person");
    h.write("Alpha:own", "did", json!(false), "person");
    h.write("Alpha:own", "go", json!(false), "person");
    h.write("Alpha:own", "go", json!(true), "person");
    assert_eq!(h.get("Alpha:own", "did"), json!(false), "dropped from the list: its setter no longer fires");
}

#[test]
fn the_list_survives_a_journal_replay_and_a_restart() {
    let h = fresh(&[("abc", PACK)]);
    h.born("Own:x", "Own", props(json!({"n": 1, "parents": ["cls:alpha"]})), "person");
    h.write("Own:x", "parents", json!(["cls:beta"]), "person");
    h.born("Own:y", "Own", props(json!({"n": 2})), "person");
    let r = fresh(&[("abc", PACK)]);
    r.replay(&h.journal());
    assert_eq!(r.snapshot()["Own:x"], h.snapshot()["Own:x"]);
    assert_eq!(r.get("Own:x", "text"), json!("beta"));
    assert_eq!(r.get("Own:y", "text"), json!("own"));
    assert!(h.journal().iter().any(|l| matches!(l, Line::Born { eid, props, .. } if eid == "Own:x" && props["parents"] == json!(["cls:alpha"]))), "the journal keeps what was said");

    let file = std::env::temp_dir().join(format!("classlist-{}-{}.sqlite3", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos())).to_string_lossy().to_string();
    let open = || { let mut o = Options::new(Some(file.clone())); o.effects = false; Host::open(o).unwrap() };
    {
        let h = open(); h.load_pack("abc", PACK).unwrap(); h.rebuild();
        h.born("Own:x", "Own", props(json!({"n": 1, "parents": ["cls:alpha"]})), "person");
        h.write("Own:x", "parents", json!(["cls:beta"]), "person");
        h.born("Own:y", "Own", props(json!({"n": 2})), "person");
    }
    let h = open(); h.load_pack("abc", PACK).unwrap();
    h.load_stored_entities();
    assert_eq!(h.load_everything(), 2, "Own is a lazy class: its rows load on reference (here: all at once)");
    h.rebuild();
    assert_eq!(h.get("Own:x", "parents"), json!(["cls:beta"]), "the restored list is the stored one: no prefix put back");
    assert_eq!(h.get("Own:x", "text"), json!("beta"));
    assert_eq!((h.get("Own:x", "sa"), h.get("Own:x", "sb")), (J::Null, json!("b:1")));
    assert_eq!(h.get("Own:y", "text"), json!("own"));
    let _ = std::fs::remove_file(&file);
}

/// Rule B follows the list: a pack may write an entity when ANY of its classes is one the pack owns or imports (or no class has an owner);
/// a write of `parents` is a write like any other. The rule guards a pack's own mistakes; the seat has no security by decision.
#[test]
fn rule_b_holds_when_any_class_of_the_entity_is_owned_and_a_parents_write_follows_it() {
    let own = "PACK own 1\nCls.tag getter: 'tag'\n";
    let plain = "PACK plain 1\nMine.x getter: 1\nTrig.go on-setter w:\n  ALWAYS: Cls:a.val = 1\nTrig.kind on-setter k:\n  ALWAYS: Cls:a.parents = ['cls:cls', 'cls:mine']\nTrig.kind2 on-setter k2:\n  ALWAYS: Cls:b.parents = ['cls:mine']\nTrig.kind3 on-setter k3:\n  ALWAYS: Cls:a.parents = ['cls:cls']\n";
    let h = fresh(&[("own", own), ("plain", plain)]);
    h.born("Cls:a", "Cls", Map::new(), "person");
    h.born("Cls:b", "Cls", Map::new(), "person");
    h.born("Trig:t", "Trig", Map::new(), "person");
    h.write("Trig:t", "go", json!(1), "person");
    assert_eq!(h.get("Cls:a", "val"), J::Null, "only Cls (owned by `own`): refused");
    h.write("Trig:t", "kind2", json!(1), "person");
    assert_eq!(h.get("Cls:b", "parents"), J::Null, "a parents write is a write: refused while no class is the pack's");
    h.write("Cls:a", "parents", json!(["cls:cls", "cls:mine"]), "person");   // the outside (person) adds a class the pack owns
    h.write("Trig:t", "go", json!(2), "person");
    assert_eq!(h.get("Cls:a", "val"), json!(1), "one class of the entity is the pack's: allowed");
    h.write("Trig:t", "kind3", json!(1), "person");
    assert_eq!(h.get("Cls:a", "parents"), json!(["cls:cls"]), "and so is a parents write on it");
}
