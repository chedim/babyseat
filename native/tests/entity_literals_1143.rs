//! Unquoted entity literals with `[key]` (operator, 2026-11-12): `membrane::Config:voice:in`, `Goal:board`, `Chat:message[id]:response`.
//! `::` after a namespace, `:` between segments (directly followed by a name character: the end-of-line colon of `WHEN c:` is
//! untouched); `[expr]` appends `:<value>` (an int or text; anything else is a reasoned null and creates nothing); `.prop` reads a
//! property, `:segment` after a `]` is a child entity. The quoted spellings keep working.
use membrane_vm::expr::{self, V};
use membrane_vm::fields::Fields;
use membrane_vm::infer::infer_src;
use membrane_vm::syntax::{parse, parse_full, print};
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::collections::HashMap;

struct W;
impl expr::World for W {
    fn attr(&self, eid: &str, attr: &str) -> V { V::Str(format!("{eid}#{attr}")) }
}

fn ev(src: &str, env: &[(&str, V)]) -> V {
    let env: HashMap<String, V> = env.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
    expr::eval(&expr::parse(src).unwrap_or_else(|e| panic!("{src}: {e}")), &env, &W).unwrap_or_else(|e| panic!("{src}: {e}"))
}
fn eqv(a: &V, b: &V) { assert!(expr::py_eq(a, b), "{a:?} != {b:?}"); }
fn ent(v: V) -> String { match v { V::Ent(e) => e, o => panic!("not an entity: {o:?}") } }
fn nil(v: V) -> String { match v { V::Nil(r) => r, o => panic!("not a reasoned null: {o:?}") } }

#[test]
fn an_unquoted_name_is_an_entity_in_an_expression() {
    assert_eq!(ent(ev("Goal:board", &[])), "Goal:board");
    assert_eq!(ent(ev("membrane::Config:voice:in", &[])), "membrane::Config:voice:in");
    assert_eq!(ent(ev("Chat:message:123", &[])), "Chat:message:123");
    eqv(&ev("Goal:board.status", &[]), &V::Str("Goal:board#status".into()));
}


#[test]
fn a_segment_after_a_colon_may_be_a_word_that_is_an_operator_elsewhere() {
    assert_eq!(ent(ev("membrane::Config:voice:in", &[])), "membrane::Config:voice:in");
    assert_eq!(ent(ev("Config:not:and:or:if:else:for", &[])), "Config:not:and:or:if:else:for");
    // and the operator is still the operator when it stands alone
    eqv(&ev("'a' in ['a']", &[]), &V::Bool(true));
    eqv(&ev("not 1 in [2]", &[]), &V::Bool(true));
}

#[test]
fn a_key_in_brackets_is_the_last_segment() {
    assert_eq!(ent(ev("membrane::Chat:message[123]", &[])), "membrane::Chat:message:123");
    assert_eq!(ent(ev("membrane::Chat:message[i]", &[("i", V::Int(7))])), "membrane::Chat:message:7");
    assert_eq!(ent(ev("membrane::Config:voice:in[slug]", &[("slug", V::Str("ann-b".into()))])), "membrane::Config:voice:in:ann-b");
    assert_eq!(ent(ev("Goal:g['x y']", &[])), "Goal:g:x y");
    assert_eq!(ent(ev("Goal:g[1 + 2]", &[])), "Goal:g:3");
}

#[test]
fn a_bare_name_in_brackets_is_a_variable_never_a_literal_segment() {
    let e = expr::parse("Chat:message[slug]").unwrap();
    let r = expr::eval(&e, &HashMap::new(), &W);
    assert!(r.is_err(), "an unbound `slug` is an unbound name, not the text 'slug': {r:?}");
}

#[test]
fn a_key_that_is_not_an_int_or_text_is_a_reasoned_null() {
    for (src, kind) in [("Goal:g[1.5]", "float"), ("Goal:g[[1]]", "list"), ("Goal:g[{'a': 1}]", "dict"), ("Goal:g[True]", "bool")] {
        let r = nil(ev(src, &[]));
        assert!(r.starts_with("error:") && r.contains(kind), "{src}: {r}");
    }
    let r = nil(ev("Goal:g[None]", &[]));
    assert!(r.starts_with("error:") && r.contains("null"), "{r}");
    assert!(nil(ev("Goal:g['']", &[])).starts_with("error:"));
}

#[test]
fn a_null_key_propagates_its_reason() {
    assert_eq!(nil(ev("Goal:g[None('no slug yet')]", &[])), "no slug yet");
    assert_eq!(nil(ev("Goal:g[k]", &[("k", V::Nil("pending: read(/a)".into()))])), "pending: read(/a)");
    // a property read off a null entity is the same null
    assert_eq!(nil(ev("Goal:g[None('why')].status", &[])), "why");
}

#[test]
fn a_property_reads_after_the_key() {
    eqv(&ev("membrane::Config:voice:in[slug].on", &[("slug", V::Str("a".into()))]), &V::Str("membrane::Config:voice:in:a#on".into()));
}

#[test]
fn the_path_continues_after_a_bracket_a_colon_is_a_child_and_a_dot_a_property() {
    let id = [("id", V::Int(5))];
    assert_eq!(ent(ev("membrane::Chat:message[id]:response", &id)), "membrane::Chat:message:5:response");
    assert_eq!(ent(ev("Chat:message[id]:response:text", &id)), "Chat:message:5:response:text");
    assert_eq!(ent(ev("Chat:message[id]:part[2]", &id)), "Chat:message:5:part:2");
    assert_eq!(ent(ev("Chat:a[1]:b[2]:c", &[])), "Chat:a:1:b:2:c");
    eqv(&ev("Chat:message[id].response", &id), &V::Str("Chat:message:5#response".into()));
    eqv(&ev("Chat:message[id]:response.text", &id), &V::Str("Chat:message:5:response#text".into()));
}

#[test]
fn on_a_list_or_dict_a_bracket_is_still_an_index() {
    eqv(&ev("xs[1]", &[("xs", V::List(vec![V::Int(4), V::Int(5)]))]), &V::Int(5));
    eqv(&ev("d['k']", &[("d", V::Dict(vec![("k".into(), V::Int(9))]))]), &V::Int(9));
}

#[test]
fn a_dotted_name_is_not_taken_for_a_literal() {
    // `this.x:y` would have to be `this.x` then a colon: a name after a dot never starts a literal
    assert!(expr::parse("this.x:y").is_err());
    assert_eq!(ent(ev("e", &[("e", V::Ent("Goal:board".into()))])), "Goal:board");
}

// ---- in packs -----------------------------------------------------------------------------------------------------------------

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

/// Box:a with `props`, the pack loaded; then Box.go is written.
fn run(pack: &str, props: J) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap_or_else(|e| panic!("{e}"));
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), props.as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    seq += 1;
    let w = Write { eid: "Box:a".into(), prop: "go".into(), new: json!(true), old: J::Null, writer: "person".into(), chain: json!({}), seq };
    c.hear(w.clone(), None, 2.0);
    f.react(&mut c, &H, w, 2.0, &mut seq);
    (c, f)
}
const CLS: &str = "Item.shown getter: this.eid\n";   // makes `Item` a class the pack owns

#[test]
fn an_entity_literal_on_the_left_of_a_write_creates_and_writes_it() {
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  ALWAYS:\n    Item:board.status = 'open'\n");
    let (c, _) = run(&pack, json!({}));
    assert_eq!(c.docs["Item:board"].props["status"], json!("open"));
    assert_eq!(c.docs["Item:board"].cls, "Item");
}

#[test]
fn a_key_on_the_left_of_a_write_names_the_entity_with_an_int_or_text() {
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  ALWAYS:\n    Item:board[this.n].status = 'open'\n    Item:board[this.slug].status = 'shut'\n");
    let (c, _) = run(&pack, json!({"n": 3, "slug": "ann-b"}));
    assert_eq!(c.docs["Item:board:3"].props["status"], json!("open"));
    assert_eq!(c.docs["Item:board:ann-b"].props["status"], json!("shut"));
}

#[test]
fn a_bad_or_null_key_creates_nothing() {
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  ALWAYS:\n    Item:board[this.f].status = 'x'\n    Item:board[this.missing].status = 'x'\n    Item:board[this.l].status = 'x'\n");
    let (c, _) = run(&pack, json!({"f": 1.5, "l": [1]}));
    assert!(!c.docs.keys().any(|k| k.starts_with("Item:")), "{:?}", c.docs.keys().collect::<Vec<_>>());
}

#[test]
fn a_continued_path_on_the_left_of_a_write() {
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  ALWAYS:\n    Item:message[this.n]:response.text = 'hi'\n");
    let (c, _) = run(&pack, json!({"n": 9}));
    assert_eq!(c.docs["Item:message:9:response"].props["text"], json!("hi"));
}

#[test]
fn the_end_of_line_colon_of_a_condition_still_ends_it() {
    // WHEN c: stmt on one line, WHEN c: then lines, a condition ending in `]` or in a name, an entity literal inside the condition
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  WHEN this.xs[0] == 1: Item:one.status = 'a'\n  ELSE WHEN Item:board[this.n].status == 'x':\n    Item:two.status = 'b'\n  ELSE:\n    Item:three.status = 'c'\n");
    let (c, _) = run(&pack, json!({"xs": [1], "n": 1}));
    assert_eq!(c.docs["Item:one"].props["status"], json!("a"));
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  WHEN this.ok:\n    Item:one.status = 'a'\n");
    let (c, _) = run(&pack, json!({"ok": true}));
    assert_eq!(c.docs["Item:one"].props["status"], json!("a"));
    let pack = format!("PACK t 1\n{CLS}Box.ok getter WHEN this.xs[0]: True\nBox.ok getter: False\n");
    let (c, _) = run(&pack, json!({"xs": [1]}));
    assert_eq!(c.docs["Box:a"].props["ok"], json!(true));
}

#[test]
fn a_for_loop_over_entity_literals() {
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  FOR k IN [Item:a, Item:b[1]]: Item:log.seen = k\n");
    let (c, _) = run(&pack, json!({}));
    assert!(c.docs.contains_key("Item:log"));
}

#[test]
fn the_quoted_spellings_still_work() {
    let pack = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  ALWAYS:\n    ('Item:' + this.slug).status = 'open'\n    ('Item:fixed').status = 'ok'\n");
    let (c, _) = run(&pack, json!({"slug": "q"}));
    assert_eq!(c.docs["Item:q"].props["status"], json!("open"));
    assert_eq!(c.docs["Item:fixed"].props["status"], json!("ok"));
}

#[test]
fn print_and_parse_round_trip_an_entity_target() {
    let src = format!("PACK t 1\n{CLS}Box.go on-setter t:\n  ALWAYS:\n    membrane::Chat:message[this.id]:response.text = 'x'\n    Item:board.status = this.s\n");
    let a = parse(&src).unwrap();
    let again = parse(&print(&a)).unwrap();
    assert_eq!(a, again);
    assert!(print(&a).contains("membrane::Chat:message[this.id]:response.text = 'x'"), "{}", print(&a));
    assert!(parse_full(&src).is_ok());
}

#[test]
fn an_entity_literal_is_typed_as_its_class() {
    assert_eq!(infer_src("Item:board", &[]).unwrap().0, "Entity(Item)");
    assert_eq!(infer_src("membrane::Config:voice:in[s]", &[("s", "Text")]).unwrap().0, "Entity(Config)");
    assert_eq!(infer_src("Chat:message[1]:response", &[]).unwrap().0, "Entity(Chat)");
}
