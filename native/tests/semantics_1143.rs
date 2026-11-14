//! Null reasons and the mention cascade (#1143, the model's live findings): a missing property is a null that says `absent`
//! (naming the entity and the property); a reasoned null inside a list or a dict keeps its reason.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Value as J};

fn host(pack: &str) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("sem", pack).unwrap_or_else(|e| panic!("pack refused: {e}"));
    h.rebuild();
    h
}
fn born(h: &Host, eid: &str, props: J) {
    h.born(eid, eid.split(':').next().unwrap(), props.as_object().unwrap().clone(), "person");
    h.pump();
}

#[test]
fn a_missing_property_is_a_null_that_says_absent_and_names_the_entity_and_the_property() {
    let h = host("PACK sem 1\nBox.why getter: why(this.price)\nBox.viaprop getter: why(prop(this, 'cost'))\n");
    born(&h, "Box:a", json!({}));
    let w = h.get("Box:a", "why");
    let s = w.as_str().unwrap_or_else(|| panic!("why(missing) is {w}, the help says the reason `absent`"));
    assert!(s.starts_with("absent") && s.contains("Box:a") && s.contains("price"), "{s}");
    let p = h.get("Box:a", "viaprop");
    assert!(p.as_str().is_some_and(|s| s.starts_with("absent") && s.contains("cost")), "{p}");
}

#[test]
fn a_missing_property_is_still_null_the_old_ways() {
    let h = host("PACK sem 1\nBox.a getter: this.price or 7\nBox.b getter: this.price == None\nBox.c getter: 'x' if this.price else 'y'\n");
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "a"), json!(7));
    assert_eq!(h.get("Box:a", "b"), json!(true));
    assert_eq!(h.get("Box:a", "c"), json!("y"));
}

// ---- item 2: a reasoned null inside a list or a dict keeps its reason ----

#[test]
fn a_reasoned_null_in_a_list_or_a_dict_keeps_its_reason_in_an_expression_and_in_a_stored_value() {
    let h = host("PACK sem 1\n\
Box.l getter: [this.price, 3]\n\
Box.d getter: {'a': this.price, 'b': {'c': None('boom')}}\n\
Box.in_expr getter: why([this.price][0])\n\
Box.lw getter: why(this.l[0])\n\
Box.lw1 getter: why(this.l[1])\n\
Box.dw getter: why(this.d['a'])\n\
Box.dw2 getter: why(this.d['b']['c'])\n");
    born(&h, "Box:a", json!({}));
    let s = |p: &str| h.get("Box:a", p);
    assert!(s("in_expr").as_str().is_some_and(|x| x.starts_with("absent") && x.contains("price")), "in an expression: {}", s("in_expr"));
    assert!(s("lw").as_str().is_some_and(|x| x.starts_with("absent") && x.contains("price")), "in a stored list: {}", s("lw"));
    assert_eq!(s("lw1"), J::Null, "a number has no reason");
    assert!(s("dw").as_str().is_some_and(|x| x.contains("price")), "in a stored dict: {}", s("dw"));
    assert_eq!(s("dw2"), json!("boom"), "nested in a dict in a dict");
    assert_eq!(s("l"), json!([null, 3]), "what is stored and sent is unchanged: a plain null");
    // the value moves on: the reason goes
    h.write("Box:a", "price", json!(5), "person");
    h.pump();
    assert_eq!(s("l"), json!([5, 3]));
    assert_eq!(s("lw"), J::Null, "a number has no reason any more");
}

#[test]
fn str_of_a_container_shows_the_reason_of_a_null_inside_it() {
    let h = host("PACK sem 1\nBox.l getter: [this.price, 3]\nBox.t getter: str(this.l)\nBox.t2 getter: str({'k': None('boom')})\nBox.t3 getter: str([None])\n");
    born(&h, "Box:a", json!({}));
    let t = h.get("Box:a", "t");
    assert!(t.as_str().is_some_and(|x| x.contains("absent") && x.contains("price")), "{t}");
    assert!(h.get("Box:a", "t2").as_str().is_some_and(|x| x.contains("boom")));
    assert_eq!(h.get("Box:a", "t3"), json!("[null]"), "a null with no reason stays null");
}
