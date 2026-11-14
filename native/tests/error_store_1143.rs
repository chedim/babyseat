//! `Error` in the field (#1143, docs/c1f-error.md section (c), operator decisions 2026-11-13): a derived Error is stored as a plain null
//! with its kind in `Core.why` (so the old routes see null); a getter reading an Error returns the same Error unchanged; a WHEN over an
//! Error is that Error (it does not fall through to the next arm); a setter that writes an Error is refused with a `refused` receipt.
use membrane_vm::fields::Fields;
use membrane_vm::host::{Host, Options};
use membrane_vm::vm::{Core, Host as VmHost, Write};
use serde_json::{json, Value as J};

fn host(pack: &str) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("er", pack).unwrap_or_else(|e| panic!("pack refused: {e}"));
    h.rebuild();
    h
}
fn born(h: &Host, eid: &str, props: J) { h.born(eid, eid.split(':').next().unwrap(), props.as_object().unwrap().clone(), "person"); h.pump(); }

#[test]
fn a_getter_that_makes_an_error_stores_a_plain_null_and_reads_back_as_an_error() {
    let h = host("PACK er 1\nBox.bad getter: Error('no such file; make it first')\nBox.nul getter: None('not applicable')\n\
Box.is_bad getter: this.bad is Error\nBox.is_nul getter: this.nul is Error\nBox.bad_none getter: this.bad == None\nBox.nul_none getter: this.nul == None\n\
Box.bad_why getter: why(this.bad)\nBox.nul_why getter: why(this.nul)\n");
    born(&h, "Box:a", json!({}));
    let s = |p: &str| h.get("Box:a", p);
    assert_eq!(s("bad"), J::Null, "stored, an Error is a plain null: the old routes see null");
    assert_eq!(s("is_bad"), json!(true));
    assert_eq!(s("is_nul"), json!(false));
    assert_eq!(s("bad_none"), json!(false), "`x == None` is False for an Error");
    assert_eq!(s("nul_none"), json!(true));
    assert_eq!(s("bad_why"), json!("no such file; make it first"));
    assert_eq!(s("nul_why"), json!("not applicable"));
}

#[test]
fn a_getter_reading_an_error_returns_the_same_error_unchanged_down_a_chain() {
    let h = host("PACK er 1\nBox.a getter: Error('root cause; fix the root')\nBox.b getter: this.a + 1\nBox.c getter: this.b * 2\nBox.d getter: [this.c, 1][0]\n\
Box.cw getter: why(this.c)\nBox.dw getter: why(this.d)\nBox.cis getter: this.c is Error\nBox.dis getter: this.d is Error\nBox.dflt getter: this.c or 'default'\nBox.dflt_is getter: this.dflt is Error\n");
    born(&h, "Box:a", json!({}));
    let s = |p: &str| h.get("Box:a", p);
    assert_eq!(s("cw"), json!("root cause; fix the root"), "the same reason all the way: no via chain");
    assert_eq!(s("dw"), json!("root cause; fix the root"));
    assert_eq!(s("cis"), json!(true));
    assert_eq!(s("dis"), json!(true));
    assert_eq!(s("dflt_is"), json!(true), "`or` is the default operator and never hides an Error");
    assert_eq!(s("dflt"), J::Null);
}

#[test]
fn an_error_goes_when_its_input_moves() {
    let h = host("PACK er 1\nBox.q getter: Error('no price; set price') if this.price == None else this.price\nBox.qis getter: this.q is Error\nBox.qn getter: this.q == None\n");
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "qis"), json!(true));
    h.write("Box:a", "price", json!(5), "person"); h.pump();
    assert_eq!(h.get("Box:a", "q"), json!(5));
    assert_eq!(h.get("Box:a", "qis"), json!(false));
    h.write("Box:a", "price", J::Null, "person"); h.pump();
    assert_eq!(h.get("Box:a", "qis"), json!(true), "back to the failure when the input goes again");
}

#[test]
fn a_when_over_an_error_is_that_error_and_does_not_fall_through() {
    let h = host("PACK er 1\nBox.bad getter: Error('boom')\nBox.v getter:\n  WHEN this.bad: 'yes'\n  ELSE: 'no'\nBox.vis getter: this.v is Error\nBox.vw getter: why(this.v)\n\
Box.u getter:\n  WHEN this.bad is Error: 'caught'\n  ELSE: 'fine'\n");
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "vis"), json!(true));
    assert_eq!(h.get("Box:a", "v"), J::Null);
    assert_eq!(h.get("Box:a", "vw"), json!("boom"));
    assert_eq!(h.get("Box:a", "u"), json!("caught"), "a WHEN that asks `is Error` is no problem");
}

struct H;
impl VmHost for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn world(packs: &[(&str, &str)]) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for (n, t) in packs { f.set_pack(n, t).unwrap_or_else(|e| panic!("{n}: {e}")); }
    let mut c = Core::new();
    c.load("Light:a".into(), "Light".into(), json!({"level": 0}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f)
}

#[test]
fn a_when_over_an_error_in_a_layered_getter_is_that_error_not_the_next_arm() {
    let base = "PACK base 1\nLight.word getter WHEN this.level > 0: 'on'\nLight.word getter: 'off'\nLight.bad getter: Error('layer boom')\n";
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter WHEN this.bad: 'never'\n";
    let (c, _f) = world(&[("base", base), ("upper", upper)]);
    let key = ("Light:a".to_string(), "word".to_string());
    assert_eq!(c.docs["Light:a"].props.get("word").cloned().unwrap_or(J::Null), J::Null, "not 'off': the arm cannot be chosen");
    assert_eq!(c.docs["Light:a"].failure("word"), Some("layer boom"), "the failure is in the entity's `failed`");
}

#[test]
fn the_failure_lives_in_the_entitys_failed_and_a_null_keeps_its_note_in_core_why() {
    let (c, _f) = world(&[("p", "PACK p 1\nLight.bad getter: Error('x broke')\nLight.nul getter: None('x absent')\n")]);
    let (bad, nul) = (("Light:a".to_string(), "bad".to_string()), ("Light:a".to_string(), "nul".to_string()));
    assert_eq!(c.docs["Light:a"].failure("bad"), Some("x broke"));
    assert_eq!(c.docs["Light:a"].failure("nul"), None);
    assert_eq!(c.why.get(&bad), None, "a failure's reason is not in the side map any more");
    assert_eq!(c.why.get(&nul).map(String::as_str), Some("x absent"));
}

#[test]
fn a_setter_that_writes_an_error_is_refused_with_a_receipt_and_the_old_value_stays() {
    let h = host("PACK er 1\nBox.go on-setter run:\n  ALWAYS:\n    this.out = Error('would hide a failure')\n");
    born(&h, "Box:a", json!({"out": "kept"}));
    h.write("Box:a", "go", json!(true), "person"); h.pump();
    assert_eq!(h.get("Box:a", "out"), json!("kept"), "nothing is stored, the old value stays");
    let r = h.get("Box:a", "refused");
    let r = r.as_str().unwrap_or_else(|| panic!("no refused receipt: {r}"));
    assert!(r.starts_with("refused:") && r.contains("would hide a failure") && r.contains("never stored"), "{r}");
}

#[test]
fn a_setter_writing_a_value_that_is_a_plain_null_is_not_refused() {
    let h = host("PACK er 1\nBox.go on-setter run:\n  ALWAYS:\n    this.out = None('not yet')\n");
    born(&h, "Box:a", json!({"out": "old"}));
    h.write("Box:a", "go", json!(true), "person"); h.pump();
    assert_eq!(h.get("Box:a", "out"), J::Null);
    assert_eq!(h.get("Box:a", "refused"), J::Null);
}

#[test]
fn a_setters_condition_over_an_error_stops_the_setter_it_does_not_fall_through_to_else() {
    let h = host("PACK er 1\nBox.bad getter: Error('cond boom')\nBox.go on-setter run:\n  WHEN this.bad:\n    this.out = 'yes'\n  ELSE:\n    this.out = 'no'\n");
    born(&h, "Box:a", json!({}));
    h.write("Box:a", "go", json!(true), "person"); h.pump();
    assert_eq!(h.get("Box:a", "out"), J::Null, "neither arm ran");
    let r = h.get("Box:a", "refused");
    assert!(r.as_str().is_some_and(|s| s.contains("cond boom")), "{r}");
}

#[test]
fn an_error_inside_a_list_or_dict_is_a_plain_null_across_a_store_and_an_error_inside_the_evaluation() {
    let h = host("PACK er 1\nBox.l getter: [1, Error('item broke'), 3]\nBox.in_eval getter: [1, Error('item broke')][1] is Error\nBox.stored_is getter: this.l[1] is Error\n\
Box.stored_none getter: this.l[1] == None\nBox.n getter: len([x for x in [1, Error('x'), 3] if not (x is Error)])\n");
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "l"), json!([1, null, 3]), "the old routes see a plain null");
    assert_eq!(h.get("Box:a", "in_eval"), json!(true));
    assert_eq!(h.get("Box:a", "stored_is"), json!(false), "across a store the element is a plain null");
    assert_eq!(h.get("Box:a", "stored_none"), json!(true));
    assert_eq!(h.get("Box:a", "n"), json!(2));
}
