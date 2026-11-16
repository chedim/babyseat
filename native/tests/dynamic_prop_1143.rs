//! A write whose PROPERTY NAME is computed (#1143, the MCP door's entity_write): `<target>.[<expr>] = value`. The expression gives the
//! property's name (non-empty text; anything else writes nothing). Everything else is a write as before: the target's class must be
//! one the pack owns or imports (a receipt `refused` otherwise), and the entity is created by the creation path.
use membrane_vm::fields::Fields;
use membrane_vm::syntax::{parse, print};
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

const OWN: &str = "Thing.shown getter: this.eid\n";
fn run(body: &str, props: J) -> Core {
    let mut f = Fields::goalgraph();
    f.set_pack("t", &format!("PACK t 1\n{OWN}Box.go on-setter t:\n  ALWAYS:\n{body}")).unwrap_or_else(|e| panic!("{e}"));
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), props.as_object().unwrap().clone(), None, 0.0);
    c.load("Thing:t1".into(), "Thing".into(), Default::default(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    seq += 1;
    let w = Write { eid: "Box:a".into(), prop: "go".into(), new: json!(1), old: J::Null, writer: "person".into(), chain: json!({}), seq };
    c.hear(w.clone(), None, 2.0);
    f.react(&mut c, &H, w, 2.0, &mut seq);
    c
}
fn p<'a>(c: &'a Core, e: &str) -> &'a serde_json::Map<String, J> { &c.docs.get(e).unwrap_or_else(|| panic!("no entity {e}")).props }

#[test]
fn the_property_name_is_an_expression() {
    let c = run("    (this.target).[this.field] = this.v\n", json!({"target": "Thing:t1", "field": "color", "v": {"a": [1, 2]}}));
    assert_eq!(p(&c, "Thing:t1")["color"], json!({"a": [1, 2]}));
}

#[test]
fn a_name_built_from_parts_and_an_entity_literal_target() {
    let c = run("    Thing:t1.['c_' + this.field] = 7\n", json!({"field": "x"}));
    assert_eq!(p(&c, "Thing:t1")["c_x"], json!(7));
}

#[test]
fn a_name_that_is_not_text_or_is_empty_writes_nothing() {
    for bad in [json!(""), json!(5), J::Null, json!(["a"])] {
        let c = run("    Thing:t1.[this.field] = 7\n", json!({"field": bad}));
        let keys: Vec<_> = p(&c, "Thing:t1").keys().filter(|k| *k != "shown").cloned().collect();   // `shown` is the getter's
        assert!(keys.is_empty(), "wrote {keys:?}");
    }
}

#[test]
fn it_creates_the_target_like_any_write_and_respects_ownership() {
    let c = run("    Thing:new1.[this.field] = 'v'\n", json!({"field": "k"}));
    assert_eq!(p(&c, "Thing:new1")["k"], json!("v"));
    // a class another pack owns is refused as a receipt, not written
    let mut f = Fields::goalgraph();
    f.set_pack("own", "PACK own 1\nCell.shown getter: this.eid\n").unwrap();
    f.set_pack("t", "PACK t 1\nBox.go on-setter t:\n  ALWAYS:\n    Cell:c1.[this.field] = 'v'\n").unwrap();
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), json!({"field": "k"}).as_object().unwrap().clone(), None, 0.0);
    c.load("Cell:c1".into(), "Cell".into(), Default::default(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    seq += 1;
    let w = Write { eid: "Box:a".into(), prop: "go".into(), new: json!(1), old: J::Null, writer: "person".into(), chain: json!({}), seq };
    c.hear(w.clone(), None, 2.0);
    f.react(&mut c, &H, w, 2.0, &mut seq);
    assert!(!p(&c, "Cell:c1").contains_key("k"));
    assert!(p(&c, "Box:a").get("refused").and_then(|r| r.as_str()).unwrap_or("").contains("IMPORT own"), "{:?}", p(&c, "Box:a"));
}

#[test]
fn it_prints_back_the_same_text() {
    let src = "PACK t 1\nBox.go on-setter t:\n  ALWAYS:\n    (this.target).[this.field] = this.v\n";
    let again = print(&parse(src).unwrap());
    assert!(again.contains("(this.target).[this.field] = this.v"), "{again}");
}
