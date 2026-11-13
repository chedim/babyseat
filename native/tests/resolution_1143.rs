//! ONE precedence rule (#1143, operator 2026-11-13). For each (entity, property) the getters form ONE ordered list of arms: the entity's own
//! class first, then its `parents` in list order, then the root `Entity`; within one class the top layer first. The first arm whose WHEN
//! holds wins; an arm without a WHEN (a default) ends the search. A WHEN that fails falls through to the next-nearer class's arm.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};

fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn fresh(packs: &[(&str, &str)]) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    for (n, t) in packs { h.load_pack(n, t).unwrap_or_else(|e| panic!("{n}: {e}")); }
    h.rebuild();
    h
}

const ROOT: &str = "PACK root 1\nEntity.summary getter: 'root:' + this.eid\n";

#[test]
fn a_subclass_getter_beats_the_root_entity_getter() {
    let h = fresh(&[("root", ROOT), ("thing", "PACK thing 1\nIMPORT root 1\nThing.summary getter: 'thing'\n")]);
    h.born("Thing:x", "Thing", props(json!({"n": 1})), "person");
    h.born("Other:y", "Other", Map::new(), "person");
    assert_eq!(h.get("Thing:x", "summary"), json!("thing"));
    assert_eq!(h.get("Other:y", "summary"), json!("root:Other:y"), "every entity ends in Entity");
}

const ABC: &str = "PACK abc 1\nOwn.text getter: 'own'\nAlpha.text getter: 'alpha'\nBeta.text getter: 'beta'\n";

#[test]
fn nearest_class_first_own_class_then_parents_in_list_order_then_entity() {
    let h = fresh(&[("root", ROOT), ("abc", "PACK abc 1\nIMPORT root 1\nOwn.text getter: 'own'\nAlpha.text getter: 'alpha'\nBeta.text getter: 'beta'\nEntity.text getter: 'root'\n")]);
    h.born("Own:x", "Own", props(json!({"parents": ["cls:alpha", "cls:beta"]})), "person");
    assert_eq!(h.get("Own:x", "text"), json!("own"), "the eid class is nearest");
    h.born("Plain:ab", "Plain", props(json!({"parents": ["cls:alpha", "cls:beta"]})), "person");
    h.born("Plain:ba", "Plain", props(json!({"parents": ["cls:beta", "cls:alpha"]})), "person");
    assert_eq!(h.get("Plain:ab", "text"), json!("alpha"), "parents[0] before parents[1]");
    assert_eq!(h.get("Plain:ba", "text"), json!("beta"));
    h.write("Plain:ab", "parents", json!(["cls:beta", "cls:alpha"]), "person");
    assert_eq!(h.get("Plain:ab", "text"), json!("beta"), "reordering the list moves the winner");
    h.born("Plain:none", "Plain", Map::new(), "person");
    assert_eq!(h.get("Plain:none", "text"), json!("root"), "the root answers last");
    let _ = ABC;
}

#[test]
fn a_top_layer_arm_of_the_farther_class_does_not_beat_the_nearer_class() {
    let h = fresh(&[
        ("far", "PACK far 1\nFar.x getter: 'far base'\n"),
        ("fartop", "PACK fartop 1\nIMPORT far 1\nFar.x getter WHEN this.n > 0: 'far top'\n"),
        ("near", "PACK near 1\nNear.x getter: 'near'\n"),
    ]);
    h.born("Near:a", "Near", props(json!({"n": 1, "parents": ["cls:far"]})), "person");
    assert_eq!(h.get("Near:a", "x"), json!("near"));
    h.born("Far:b", "Far", props(json!({"n": 1})), "person");
    assert_eq!(h.get("Far:b", "x"), json!("far top"), "layers still work inside a class");
    h.write("Far:b", "n", json!(0), "person");
    assert_eq!(h.get("Far:b", "x"), json!("far base"));
}

#[test]
fn a_when_that_fails_falls_through_to_the_next_nearer_class() {
    let h = fresh(&[
        ("far", "PACK far 1\nFar.x getter: 'far'\n"),
        ("near", "PACK near 1\nNear.x getter WHEN this.n > 5: 'near big'\n"),
    ]);
    h.born("Near:a", "Near", props(json!({"n": 1, "parents": ["cls:far"]})), "person");
    assert_eq!(h.get("Near:a", "x"), json!("far"), "no arm of Near holds: Far answers");
    h.write("Near:a", "n", json!(9), "person");
    assert_eq!(h.get("Near:a", "x"), json!("near big"));
    h.write("Near:a", "n", json!(2), "person");
    assert_eq!(h.get("Near:a", "x"), json!("far"), "and back");
}

#[test]
fn an_else_arm_ends_the_search() {
    let h = fresh(&[
        ("far", "PACK far 1\nFar.x getter: 'far'\n"),
        ("near", "PACK near 1\nNear.x getter WHEN this.n > 5: 'near big'\nNear.x getter: 'near default'\n"),
    ]);
    h.born("Near:a", "Near", props(json!({"n": 1, "parents": ["cls:far"]})), "person");
    assert_eq!(h.get("Near:a", "x"), json!("near default"));
}

#[test]
fn the_same_result_after_a_replay_and_a_restart() {
    let packs = [("root", ROOT), ("far", "PACK far 1\nFar.x getter: 'far'\n"),
        ("near", "PACK near 1\nNear.x getter WHEN this.n > 5: 'near big'\nNear.summary getter: 'near'\n")];
    let h = fresh(&packs);
    h.born("Near:a", "Near", props(json!({"n": 1, "parents": ["cls:far"]})), "person");
    h.write("Near:a", "n", json!(9), "person");
    h.write("Near:a", "n", json!(2), "person");
    h.born("Plain:p", "Plain", Map::new(), "person");
    let r = fresh(&packs);
    r.replay(&h.journal());
    assert_eq!(r.snapshot(), h.snapshot(), "journal replay");
    assert_eq!(r.get("Near:a", "x"), json!("far"));
    assert_eq!(r.get("Near:a", "summary"), json!("near"));
    assert_eq!(r.get("Plain:p", "summary"), json!("root:Plain:p"));
    // a restart: the stored entities are loaded into a fresh engine, then the getters are rebuilt
    let snap = h.snapshot();
    let s = fresh(&packs);
    for (eid, e) in &snap { s.born(eid, e["cls"].as_str().unwrap_or(""), e["props"].as_object().cloned().unwrap_or_default(), "person"); }
    s.rebuild();
    assert_eq!(s.get("Near:a", "x"), json!("far"));
    assert_eq!(s.get("Plain:p", "summary"), json!("root:Plain:p"));
}

fn seat() -> Host {
    let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap();
    let h = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
    let list = std::fs::read_to_string(repo.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&repo.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
    }
    h.load_app(&repo.join("membrane/apps/barest.c1f"), false).unwrap();
    h.rebuild();
    h
}

#[test]
fn the_barest_seats_entity_pack_gives_every_entity_a_summary_from_its_id_class_and_parents() {
    let h = seat();
    h.born("Trip:main", "Trip", props(json!({"parents": ["cls:semnoun"]})), "person");
    h.born("Note:a", "Note", props(json!({"text": "secret value"})), "person");
    h.pump();
    assert_eq!(h.get("Trip:main", "summary"), json!("Trip:main (cls:trip, cls:semnoun)"));
    assert_eq!(h.get("Note:a", "summary"), json!("Note:a (Note)"), "no values of the entity, only its id and classes");
}

#[test]
fn a_model_pack_overrides_summary_for_its_class_with_one_import_and_the_root_stays_for_the_rest() {
    let h = seat();
    h.load_pack("mine", "PACK mine 1\nIMPORT entity 1\nTrip.summary getter: 'trip ' + str(this.n)\n").unwrap();
    h.rebuild();
    h.born("Trip:main", "Trip", props(json!({"n": 3})), "person");
    h.born("Note:a", "Note", Map::new(), "person");
    h.pump();
    assert_eq!(h.get("Trip:main", "summary"), json!("trip 3"));
    assert_eq!(h.get("Note:a", "summary"), json!("Note:a (Note)"));
    // a subclass getter needs no import (the class is not the entity pack's); a declaration on `Entity` itself does: it overrides the owner's
    let e = h.load_pack("bare", "PACK bare 1\nEntity.summary getter: 'x'\n").unwrap_err();
    assert!(e.contains("IMPORT entity"), "{e}");
    h.load_pack("imp", "PACK imp 1\nIMPORT entity 1\nEntity.summary getter: 'y'\n").unwrap();
    h.rebuild(); h.pump();
    assert_eq!(h.get("Note:a", "summary"), json!("y"), "with the import the upper layer wins");
}
