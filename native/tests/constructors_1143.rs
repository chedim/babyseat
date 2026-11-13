//! Constructors (operator, 2026-11-12): `<entity> = Class(name: value, ...)`. The TARGET on the left is the entity's identity (no
//! `key` argument, no declared identity). It creates the entity at that eid by the creation path of a write (the class must be one
//! the pack owns or imports; refused otherwise, as a receipt, never a crash), sets the named arguments as its first properties, and
//! is idempotent: an entity that exists is not created again and a property that already has a value is not overwritten. A target
//! whose eid prefix is not the class (`membrane::Config:...` built as `Voice(...)`) is stamped `parents: ['cls:voice']`, the ISA
//! kind-mirror, so the engine mirrors it by kind.
use membrane_vm::fields::Fields;
use membrane_vm::infer::infer_src;
use membrane_vm::syntax::{parse, parse_full, print};
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

/// The classes the pack owns: Thing, Reply (a getter each). Box:a has `props`; `pre` entities are loaded first; then Box.go is written.
const OWN: &str = "Thing.shown getter: this.eid\nReply.shown getter: this.eid\n";
fn run_with(pack: &str, props: J, pre: &[(&str, &str, J)]) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap_or_else(|e| panic!("{e}"));
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), props.as_object().unwrap().clone(), None, 0.0);
    for (e, cls, p) in pre { c.load(e.to_string(), cls.to_string(), p.as_object().unwrap().clone(), None, 0.0); }
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    for _ in 0..2 {   // twice: the second write of the same value changes nothing, a second `go` is a fresh trigger
        seq += 1;
        let w = Write { eid: "Box:a".into(), prop: "go".into(), new: json!(seq), old: J::Null, writer: "person".into(), chain: json!({}), seq };
        c.hear(w.clone(), None, 2.0);
        f.react(&mut c, &H, w, 2.0, &mut seq);
    }
    (c, f)
}
fn run(body: &str, props: J) -> (Core, Fields) { run_with(&format!("PACK t 1\n{OWN}Box.go on-setter t:\n  ALWAYS:\n{body}"), props, &[]) }
fn born(f: &Fields) -> Vec<String> { f.out.iter().filter_map(|o| o.get("born").map(|_| o["eid"].as_str().unwrap().to_string())).collect() }
fn p<'a>(c: &'a Core, e: &str) -> &'a serde_json::Map<String, J> { &c.docs.get(e).unwrap_or_else(|| panic!("no entity {e}: {:?}", c.docs.keys().collect::<Vec<_>>())).props }

#[test]
fn a_constructor_creates_the_target_with_its_arguments_as_properties() {
    let (c, f) = run("    Thing:t1 = Thing(kind: 'in', n: this.n + 1)\n", json!({"n": 4}));
    assert_eq!(c.docs["Thing:t1"].cls, "Thing");
    assert_eq!(p(&c, "Thing:t1")["kind"], json!("in"));
    assert_eq!(p(&c, "Thing:t1")["n"], json!(5));
    assert!(!p(&c, "Thing:t1").contains_key("parents"), "the prefix is the class: no kind stamp");
    assert_eq!(born(&f), ["Thing:t1"], "born once, though the setter ran twice");
}

#[test]
fn the_target_is_the_identity_a_key_in_brackets_names_it() {
    let (c, _) = run("    Thing:slot[this.slug] = Thing(kind: 'x')\n    Reply:r[this.n]:part = Reply(text: 'hi')\n", json!({"slug": "ann-b", "n": 3}));
    assert_eq!(p(&c, "Thing:slot:ann-b")["kind"], json!("x"));
    assert_eq!(p(&c, "Reply:r:3:part")["text"], json!("hi"));
}

#[test]
fn a_foreign_prefix_is_stamped_with_the_kind_so_the_engine_mirrors_it() {
    let (c, _) = run("    membrane::Config:voice:in[this.slug] = Thing(kind: 'in')\n    membrane::Chat:message[this.n]:response = Reply(text: 'hi')\n", json!({"slug": "ann-b", "n": 9}));
    let v = p(&c, "membrane::Config:voice:in:ann-b");
    assert_eq!(v["kind"], json!("in"));
    assert_eq!(v["parents"], json!(["cls:thing"]));
    assert_eq!(c.docs["membrane::Config:voice:in:ann-b"].cls, "Config", "it stays the class its eid names (the Python seat's)");
    let r = p(&c, "membrane::Chat:message:9:response");
    assert_eq!(r["parents"], json!(["cls:reply"]));
    assert_eq!(r["text"], json!("hi"));
}

#[test]
fn a_constructor_is_idempotent_and_never_overwrites_a_value() {
    let pack = format!("PACK t 1\n{OWN}Box.go on-setter t:\n  ALWAYS:\n    Thing:t1 = Thing(kind: 'new', n: 1, note: None)\n");
    let pre = [("Thing:t1", "Thing", json!({"kind": "old", "n": null}))];
    let (c, f) = run_with(&pack, json!({}), &pre);
    assert_eq!(p(&c, "Thing:t1")["kind"], json!("old"), "a property that has a value stays");
    assert_eq!(p(&c, "Thing:t1")["n"], json!(1), "an unset one is filled");
    assert!(born(&f).is_empty(), "an entity that exists is not created again");
    assert!(!p(&c, "Thing:t1").contains_key("note") || p(&c, "Thing:t1")["note"].is_null(), "a null argument sets nothing");
}

#[test]
fn a_foreign_entity_that_exists_keeps_what_it_has_and_gains_only_what_is_unset() {
    let pack = format!("PACK t 1\n{OWN}Box.go on-setter t:\n  ALWAYS:\n    membrane::Config:voice:in:z = Thing(kind: 'in', person: 'Z')\n");
    let pre = [("membrane::Config:voice:in:z", "Config", json!({"person": "Zed", "parents": ["cls:thing"]}))];
    let (c, f) = run_with(&pack, json!({}), &pre);
    assert_eq!(p(&c, "membrane::Config:voice:in:z")["person"], json!("Zed"));
    assert_eq!(p(&c, "membrane::Config:voice:in:z")["kind"], json!("in"));
    assert_eq!(p(&c, "membrane::Config:voice:in:z")["parents"], json!(["cls:thing"]));
    assert!(born(&f).is_empty());
}

#[test]
fn a_class_the_pack_does_not_own_or_import_is_refused_with_a_reason() {
    let (c, f) = run("    Thing:t1 = Nope(kind: 'in')\n    Thing:t2 = Goal(kind: 'in')\n    Thing:t3 = Thing(kind: 'ok')\n", json!({}));
    assert!(!c.docs.contains_key("Thing:t1"), "no pack owns Nope");
    assert!(!c.docs.contains_key("Thing:t2"), "goalgraph owns Goal and this pack does not import it");
    assert_eq!(p(&c, "Thing:t3")["kind"], json!("ok"), "the line below still runs");
    let why: Vec<String> = f.refusals.iter().map(|r| r["reason"].as_str().unwrap().to_string()).collect();
    assert!(why.iter().any(|w| w.starts_with("error:") && w.contains("Nope")), "{why:?}");
    assert!(why.iter().any(|w| w.contains("Goal") && w.contains("IMPORT")), "{why:?}");
}

#[test]
fn a_null_or_bad_key_creates_nothing_and_says_why() {
    let (c, f) = run("    Thing:k[this.missing] = Thing(kind: 'x')\n    Thing:k[this.f] = Thing(kind: 'x')\n", json!({"f": 1.5}));
    assert!(!c.docs.keys().any(|k| k.starts_with("Thing:k")));
    assert!(f.refusals.iter().any(|r| r["reason"].as_str().unwrap().starts_with("error:")), "{:?}", f.refusals);
}

#[test]
fn only_named_arguments_make_a_constructor_and_a_plain_value_has_none() {
    for (body, want) in [("Thing:x = 5", "constructor"), ("Thing:x = Thing(1, 2)", "named"), ("Thing:x = Thing(a: 1, 2)", "named"), ("Thing:x = Thing(a: 1, a: 2)", "twice")] {
        let src = format!("PACK t 1\n{OWN}Box.go on-setter t:\n  ALWAYS:\n    {body}\n");
        let e = parse_full(&src).err().unwrap_or_else(|| panic!("{body} loaded"));
        assert!(e.contains(want), "{body}: {e}");
    }
}

#[test]
fn a_constructor_outside_a_write_target_is_a_reasoned_error_not_a_crash() {
    let pack = format!("PACK t 1\n{OWN}Box.g getter: Thing(kind: 'x')\n");
    let mut f = Fields::goalgraph();
    f.set_pack("t", &pack).unwrap();
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), Default::default(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    assert_eq!(c.docs["Box:a"].props.get("g").cloned().unwrap_or(J::Null), J::Null);
    let chain = f.why_chain(&c, "Box:a", "g");
    assert!(chain[0]["reason"].as_str().unwrap().contains("constructor"), "{chain:?}");
}

#[test]
fn print_and_parse_round_trip_a_constructor() {
    let src = format!("PACK t 1\n{OWN}Box.go on-setter t:\n  ALWAYS:\n    membrane::Chat:message[this.id]:response = Reply(text: this.t, n: 1)\n");
    let a = parse(&src).unwrap();
    assert_eq!(a, parse(&print(&a)).unwrap());
    assert!(print(&a).contains("membrane::Chat:message[this.id]:response = Reply(text: this.t, n: 1)"), "{}", print(&a));
}

#[test]
fn a_constructor_is_typed_as_its_class() {
    assert_eq!(infer_src("Voice(kind: 'in')", &[]).unwrap().0, "Entity(Voice)");
    assert_eq!(infer_src("Voice(kind: 1 + 'a')", &[]).unwrap().1.len(), 1, "its arguments are typed for findings");
}

#[test]
fn a_foreign_prefix_entity_gets_its_kinds_getters_at_once() {
    // the kind stamp (`parents`) is set after the birth: the kind's getters must still be computed for what was just made
    let pack = "PACK t 1\nVoice.shown getter: 'v:' + this.eid\nVoice.twice getter: this.n * 2\nBox.go on-setter t:\n  ALWAYS:\n    Config:x = Voice(n: 4)\n";
    let (c, _) = run_with(pack, json!({}), &[]);
    assert_eq!(p(&c, "Config:x")["parents"], json!(["cls:voice"]));
    assert_eq!(p(&c, "Config:x")["shown"], json!("v:Config:x"), "{:?}", p(&c, "Config:x"));
    assert_eq!(p(&c, "Config:x")["twice"], json!(8));
}
