//! `x if c else y` in expressions, and getter arms: alternative definitions of one field, the first whose WHEN holds
//! wins, an arm without WHEN is the default (the operator, 2026-11-10: "if arms as alternative definitions of the
//! same field"). Arms compile to bare ifs.
use membrane_vm::fields::Fields;
use membrane_vm::syntax::parse_full;
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

fn run(pack: &str, writes: &[(&str, J)]) -> Core {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Light:a".into(), "Light".into(), json!({"level": 0, "label": "start"}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    for (p, v) in writes {
        seq += 1;
        let w = Write { eid: "Light:a".into(), prop: p.to_string(), new: v.clone(), old: J::Null, writer: "person".into(), chain: json!({}), seq };
        c.hear(w.clone(), None, 2.0);
        f.react(&mut c, &H, w, 2.0, &mut seq);
    }
    c
}

#[test]
fn a_bare_if_picks_its_branch() {
    let c = run("PACK t 1\nLight.word getter: 'on' if this.level > 0 else 'off'\n", &[("level", json!(3))]);
    assert_eq!(c.docs["Light:a"].props["word"], json!("on"));
    let c = run("PACK t 1\nLight.word getter: 'on' if this.level > 0 else 'dim' if this.level == 0 else 'broken'\n", &[("level", json!(-1))]);
    assert_eq!(c.docs["Light:a"].props["word"], json!("broken"));
}

#[test]
fn the_first_arm_whose_when_holds_defines_the_field() {
    let pack = "PACK t 1\n\
Light.word getter WHEN this.level > 5: 'bright'\n\
Light.word getter WHEN this.level > 0: 'on'\n\
Light.word getter: 'off'\n";
    assert_eq!(run(pack, &[("level", json!(9))]).docs["Light:a"].props["word"], json!("bright"));
    assert_eq!(run(pack, &[("level", json!(2))]).docs["Light:a"].props["word"], json!("on"));
    assert_eq!(run(pack, &[("level", json!(-2))]).docs["Light:a"].props["word"], json!("off"));
}

#[test]
fn a_default_arm_reading_its_own_field_keeps_the_value() {
    let pack = "PACK t 1\nLight.shown getter WHEN this.level > 0: this.label\nLight.shown getter: this.shown\n";
    let c = run(pack, &[("level", json!(1)), ("label", json!("kitchen")), ("level", json!(0)), ("label", json!("hall"))]);
    assert_eq!(c.docs["Light:a"].props["shown"], json!("kitchen"));
}

#[test]
fn arms_are_checked_when_the_pack_loads() {
    let e = parse_full("PACK t 1\nLight.word getter: 'off'\nLight.word getter WHEN this.level > 0: 'on'\n").unwrap_err();
    assert!(e.contains("after its default"), "{e}");
    let e = parse_full("PACK t 1\nLight.word getter: 'a'\nLight.word getter: 'b'\n").unwrap_err();
    assert!(e.contains("two defaults"), "{e}");
    let e = parse_full("PACK t 1\nLight.word getter WHEN : 'on'\n").unwrap_err();
    assert!(e.contains("no condition"), "{e}");
    let p = parse_full("PACK t 1\nLight.word getter WHEN this.level > 0: 'on'\n").unwrap();
    assert_eq!(p.getters.len(), 1);
}

// ---- setters and getters support the same forms (the operator, 2026-11-10) ----

fn world(pack: &str) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Shelf:a".into(), "Shelf".into(), json!({"name": "a"}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, eid: &str, p: &str, v: J) {
    let w = Write { eid: eid.into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}

const SHELF: &str = "PACK t 1
Shelf.listed on-setter books:
  ALWAYS:
    FOR b IN this.listed:
      ('Book:' + this.name + ':' + prop(b, 'title')).title = prop(b, 'title')
      ('Book:' + this.name + ':' + prop(b, 'title')).shelf = this.eid
      FOR t IN (prop(b, 'tags') or []):
        ('Tag:' + t).seen = True
    this.count = len(this.listed or [])
";

#[test]
fn a_setter_for_block_writes_once_per_item_and_creates_entities() {
    let (mut c, mut f) = world(&format!("{}Book.title getter: this.title\nTag.kind getter: 'tag'\n", SHELF));
    put(&mut c, &mut f, "Shelf:a", "listed", json!([{"title": "dune", "tags": ["sf", "desert"]}, {"title": "emma"}]));
    assert_eq!(c.docs["Book:a:dune"].props["shelf"], json!("Shelf:a"));
    assert_eq!(c.docs["Book:a:emma"].props["title"], json!("emma"));
    assert_eq!(c.docs["Tag:sf"].props["seen"], json!(true));
    assert!(c.docs.contains_key("Tag:desert"));
    assert_eq!(c.docs["Shelf:a"].props["count"], json!(2));         // the write after the block runs once
    put(&mut c, &mut f, "Shelf:a", "listed", J::Null);                // None: zero iterations
    assert_eq!(c.docs["Shelf:a"].props["count"], json!(0));
}

#[test]
fn a_for_over_a_non_list_stops_the_setter_with_a_receipt() {
    let (mut c, mut f) = world(SHELF);
    put(&mut c, &mut f, "Shelf:a", "listed", json!("not a list"));
    let r = c.docs["Shelf:a"].props.get("refused").cloned().unwrap_or(J::Null);
    assert!(r.as_str().unwrap_or("").contains("not a list"), "{r}");
    assert!(c.docs["Shelf:a"].props.get("count").is_none(), "the setter wrote nothing");
}

#[test]
fn for_is_a_list_in_getters_and_in_setter_values() {
    let pack = "PACK t 1
Shelf.titles getter: FOR b IN (this.listed or []): prop(b, 'title')
Shelf.listed on-setter copy:
  ALWAYS:
    this.upper = FOR b IN this.listed: prop(b, 'title') + '!'
";
    let (mut c, mut f) = world(pack);
    put(&mut c, &mut f, "Shelf:a", "listed", json!([{"title": "dune"}, {"title": "emma"}]));
    assert_eq!(c.docs["Shelf:a"].props["titles"], json!(["dune", "emma"]));
    assert_eq!(c.docs["Shelf:a"].props["upper"], json!(["dune!", "emma!"]));
}

#[test]
fn a_getter_takes_the_setters_block_form() {
    let pack = "PACK t 1
Light.word getter:
  WHEN this.level > 5: 'bright'
  ELSE WHEN this.level > 0: 'on'
  ELSE: 'off'
";
    assert_eq!(run(pack, &[("level", json!(9))]).docs["Light:a"].props["word"], json!("bright"));
    assert_eq!(run(pack, &[("level", json!(1))]).docs["Light:a"].props["word"], json!("on"));
    assert_eq!(run(pack, &[("level", json!(0))]).docs["Light:a"].props["word"], json!("off"));
    for (bad, why) in [("PACK t 1\nLight.w getter:\n  ELSE: 1\n", "must follow a WHEN"),
                       ("PACK t 1\nLight.w getter:\n  WHEN 1: 1\n  WHEN 2: 2\n", "second WHEN"),
                       ("PACK t 1\nLight.w getter: 3\n  WHEN 1: 1\n", "header line"),
                       ("PACK t 1\nShelf.l on-setter s:\n  ALWAYS:\n    FOR b IN this.l:\n    this.x = 1\n", "no writes under it"),
                       ("PACK t 1\nShelf.l on-setter s:\n  ALWAYS:\n    FOR 2b IN this.l: this.x = 1\n", "one word")] {
        let e = parse_full(bad).unwrap_err();
        assert!(e.contains(why), "{bad:?}: {e}");
    }
}

#[test]
fn a_for_block_prints_and_parses_back_the_same() {
    use membrane_vm::syntax::{parse, print};
    let sets = parse_full(SHELF).unwrap().setters;
    assert_eq!(parse(&print(&sets)).unwrap(), sets);
}

#[test]
fn a_for_over_entities_binds_each_entity() {
    let pack = format!("{}Book.title getter: this.title\nTag.kind getter: 'tag'\nShelf.sweep on-setter retire:\n  ALWAYS:\n    FOR b IN referencing(this, 'shelf'):\n      b.kept = prop(b, 'title') in (this.sweep or [])\n", SHELF);
    let (mut c, mut f) = world(&pack);
    put(&mut c, &mut f, "Shelf:a", "listed", json!([{"title": "dune"}, {"title": "emma"}]));
    put(&mut c, &mut f, "Shelf:a", "sweep", json!(["emma"]));
    assert_eq!(c.docs["Book:a:dune"].props["kept"], json!(false));
    assert_eq!(c.docs["Book:a:emma"].props["kept"], json!(true));
}

#[test]
fn prop_reads_a_field_through_an_entitys_id_and_follows_it() {
    let pack = "PACK t 1\nBook.shelf_name getter: prop(this.shelf, 'name')\nShelf.kind getter: 'shelf'\n";
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Shelf:a".into(), "Shelf".into(), json!({"name": "attic"}).as_object().unwrap().clone(), None, 0.0);
    c.load("Book:x".into(), "Book".into(), json!({"shelf": "Shelf:a"}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    assert_eq!(c.docs["Book:x"].props["shelf_name"], json!("attic"));
    put(&mut c, &mut f, "Shelf:a", "name", json!("cellar"));          // the read is tracked: it follows
    assert_eq!(c.docs["Book:x"].props["shelf_name"], json!("cellar"));
}

#[test]
fn instances_lists_a_class_and_follows_births_and_writes() {
    let pack = "PACK t 1\nShelf.books getter: [prop(b, 'title') for b in instances('Book')]\nBook.kind getter: 'book'\n";
    let (mut c, mut f) = world(pack);
    c.load("Book:1".into(), "Book".into(), json!({"title": "dune"}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "Shelf:a", "poke", json!(1));
    assert_eq!(c.docs["Shelf:a"].props["books"], json!(["dune"]));
    c.load("Book:2".into(), "Book".into(), json!({"title": "emma"}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "Book:2", "title", json!("emma"));                 // a birth (the next round registers it)
    assert_eq!(c.docs["Shelf:a"].props["books"], json!(["dune", "emma"]));
    put(&mut c, &mut f, "Book:1", "title", json!("dune messiah"));          // a write to one it read
    assert_eq!(c.docs["Shelf:a"].props["books"], json!(["dune messiah", "emma"]));
}

#[test]
fn long_arms_fold_and_run_and_no_size_cap_refuses_them() {
    let arm = "this.label + ' ' + this.label + ' ' + this.label + ' ' + this.label + ' ' + this.label + ' ' + this.label";
    let pack = format!("PACK t 1\nLight.long getter:\n  WHEN this.level > 5: {arm}\n  ELSE WHEN this.level > 0: {arm}\n  ELSE: {arm}\n");
    let c = run(&pack, &[("level", json!(1))]);
    assert!(c.docs["Light:a"].props["long"].as_str().unwrap().starts_with("start start"));
    // there is no size cap any more: a long arm loads (the evaluation has a step budget instead)
    let big = format!("PACK t 1\nLight.big getter: {}\n", vec!["this.label"; 40].join(" + "));
    assert!(parse_full(&big).is_ok());
}

#[test]
fn a_getter_applies_to_an_entity_of_its_kind_by_isa() {
    // the scripts migration: a Component keeps its class and gains a kind; the kind's getters apply to it
    let pack = "PACK t 1\nChatThing.heard getter: this.heard_n or 0\n";
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("cls:chatthing".into(), "Class".into(), Default::default(), None, 0.0);
    c.load("Component:chat:a".into(), "Component".into(), json!({"heard_n": 4, "parents": ["cls:chatthing"]}).as_object().unwrap().clone(), None, 0.0);
    c.load("Component:other".into(), "Component".into(), json!({"heard_n": 9}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    assert_eq!(c.docs["Component:chat:a"].props["heard"], json!(4));
    assert!(c.docs["Component:other"].props.get("heard").is_none(), "only its kind");
    // the kind's own entity need not be in the field: its id names it
    let mut f2 = Fields::goalgraph();
    f2.set_pack("t", pack).unwrap();
    let mut c2 = Core::new();
    c2.load("Component:chat:b".into(), "Component".into(), json!({"heard_n": 2, "parents": ["membrane::cls:chatthing"]}).as_object().unwrap().clone(), None, 0.0);
    let mut seq2 = 0;
    f2.rebuild(&mut c2, 1.0, &mut seq2);
    assert_eq!(c2.docs["Component:chat:b"].props["heard"], json!(2));
}
