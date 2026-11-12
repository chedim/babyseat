//! A getter may store entity values (a list of them, a single one, nested in a dict): an entity is kept as a reference
//! `{"$ref": "<eid>"}` and reads back as the entity, so another getter can iterate it and read its fields.
//! (Before: "cannot write a EntityView" -- the field kept the old value.)
use membrane_vm::fields::Fields;
use membrane_vm::vm::{Core, Host};
use serde_json::{json, Map, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, _: &str) -> Option<String> { None }
    fn resolve_exact(&self, _: &str) -> Option<String> { None }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

const PACK: &str = "PACK ents 1\n\
Box.open getter: [t for t in instances('Thing') if t.on]\n\
Box.first getter: head([t for t in instances('Thing') if t.on], 1)\n\
Box.names getter: [t.name for t in this.open]\n\
Box.count getter: len(this.open)\n";

fn world() -> (Core, Fields) {
    let mut core = Core::new();
    let mut f = Fields::goalgraph();
    f.set_pack("ents", PACK).unwrap();
    for (k, on) in [("Thing:a", true), ("Thing:b", false), ("Thing:c", true)] {
        let mut p = Map::new();
        p.insert("name".into(), json!(k.trim_start_matches("Thing:")));
        p.insert("on".into(), json!(on));
        core.load(k.into(), "Thing".into(), p, None, 0.0);
    }
    core.load("Box:x".into(), "Box".into(), Map::new(), None, 0.0);
    let mut seq = 0i64;
    f.settle(&mut core, &H, 1.0, &mut seq);
    (core, f)
}

fn prop(core: &Core, k: &str) -> J { core.docs["Box:x"].props.get(k).cloned().unwrap_or(J::Null) }

#[test]
fn a_getter_stores_a_list_of_entities_as_references() {
    let (core, _f) = world();
    assert_eq!(prop(&core, "open"), json!([{"$ref": "Thing:a"}, {"$ref": "Thing:c"}]));
    assert_eq!(prop(&core, "first"), json!([{"$ref": "Thing:a"}]));
}

#[test]
fn another_getter_reads_the_stored_entities_back_as_entities() {
    let (core, _f) = world();
    assert_eq!(prop(&core, "names"), json!(["a", "c"]));
    assert_eq!(prop(&core, "count"), json!(2));
}

#[test]
fn the_stored_list_follows_the_entities() {
    let (mut core, mut f) = world();
    let mut seq = 0i64;
    let old = core.docs["Thing:b"].props.clone();
    let mut p = old.clone();
    p.insert("on".into(), json!(true));
    core.load("Thing:b".into(), "Thing".into(), p, None, 2.0);
    let new = core.docs["Thing:b"].props.clone();
    f.reloaded("Thing:b", &old, &new);                    // what the host's load tells the engine
    f.settle(&mut core, &H, 3.0, &mut seq);
    assert_eq!(prop(&core, "count"), json!(3));
}

// ---- setters write entity values the same way (they are the getters' syntactic mirror) ----

use membrane_vm::vm::Write;

const SETTER_PACK: &str = "PACK sets 1\nIMPORT ents 1\n\
Box.fill on-setter fill:\n  ALWAYS:\n    this.chosen = [t for t in instances('Thing') if t.on]\n    this.one = head([t for t in instances('Thing') if t.on], 1)\n    this.both = {'first': head([t for t in instances('Thing') if t.on], 1), 'n': 2}\n\
Box.chosen_names getter: [t.name for t in this.chosen]\n";

#[test]
fn a_setter_writes_a_list_of_entities_and_a_getter_reads_it_back() {
    let (mut core, mut f) = world();
    f.set_pack("sets", SETTER_PACK).unwrap();
    let mut seq = 0i64;
    f.settle(&mut core, &H, 2.0, &mut seq);
    seq += 1;
    let w = Write { eid: "Box:x".into(), prop: "fill".into(), new: json!(1), old: J::Null, writer: "t".into(), chain: json!({}), seq };
    core.hear(w.clone(), None, 3.0);
    let mut s = seq;
    f.react(&mut core, &H, w, 3.0, &mut s);
    assert_eq!(prop(&core, "chosen"), json!([{"$ref": "Thing:a"}, {"$ref": "Thing:c"}]));
    assert_eq!(prop(&core, "one"), json!([{"$ref": "Thing:a"}]));
    assert_eq!(prop(&core, "both"), json!({"first": [{"$ref": "Thing:a"}], "n": 2}));
    assert_eq!(prop(&core, "chosen_names"), json!(["a", "c"]));
}
