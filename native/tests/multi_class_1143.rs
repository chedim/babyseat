//! An entity of TWO extra classes at once (#1143). Its classes are the list in its `parents` property (`cls:<name>` ids, the ISA kinds): the
//! getters of both apply, the setter of each fires (a kind's setter needs its ISA parent entity `cls:<name>`), and the list survives a
//! journal replay. A `parents` write on an entity that already exists binds the getters of its kinds (they are bound at birth otherwise).
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};

const TWO: &str = "PACK two 1\nAlpha.sa getter: 'alpha:' + str(this.n)\nBeta.sb getter: 'beta:' + str(this.n)\nAlpha.go_a on-setter run-a:\n  WHEN this.go_a == True:\n    this.did_a = True\nBeta.go_b on-setter run-b:\n  WHEN this.go_b == True:\n    this.did_b = True\n";

fn fresh() -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("two", TWO).unwrap();
    h.rebuild();
    h
}
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

#[test]
fn a_parents_write_on_an_existing_entity_binds_the_getters_and_setters_of_both_kinds_and_replays() {
    let h = fresh();
    h.born("cls:alpha", "cls", props(json!({"name": "Alpha"})), "person");     // the ISA parent entities a kind's setter needs
    h.born("cls:beta", "cls", props(json!({"name": "Beta"})), "person");
    h.born("Thing:x", "Thing", props(json!({"n": 3})), "person");
    assert_eq!(h.get("Thing:x", "sa"), J::Null, "no kind yet: no getter");
    h.write("Thing:x", "parents", json!(["cls:alpha", "cls:beta"]), "person");
    assert_eq!((h.get("Thing:x", "sa"), h.get("Thing:x", "sb")), (json!("alpha:3"), json!("beta:3")), "both getters apply");
    h.write("Thing:x", "n", json!(4), "person");
    assert_eq!((h.get("Thing:x", "sa"), h.get("Thing:x", "sb")), (json!("alpha:4"), json!("beta:4")), "both follow a write");
    h.write("Thing:x", "go_a", json!(true), "person");
    h.write("Thing:x", "go_b", json!(true), "person");
    assert_eq!((h.get("Thing:x", "did_a"), h.get("Thing:x", "did_b")), (json!(true), json!(true)), "both setters fire");
    // a journal replay into a fresh engine gives the same entity
    let r = fresh();
    r.replay(&h.journal());
    assert_eq!(r.snapshot()["Thing:x"], h.snapshot()["Thing:x"]);
}

/// Today there is no root class: a getter on `Entity` applies to entities whose class or parents say `Entity`, and to no other.
#[test]
fn a_getter_on_entity_applies_to_no_entity_without_that_class_today() {
    let h = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
    h.load_pack("root", "PACK root 1\nEntity.summary getter: this.cls + ' ' + this.eid\n").unwrap();
    h.rebuild();
    h.born("Thing:x", "Thing", props(json!({"n": 1})), "person");
    assert_eq!(h.get("Thing:x", "summary"), J::Null, "no implicit root class");
    h.born("Entity:y", "Entity", Map::new(), "person");
    assert_eq!(h.get("Entity:y", "summary"), json!("Entity Entity:y"));
}
