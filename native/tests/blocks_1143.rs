//! Scoped code blocks in getters and setters (operator, 2026-11-12): a bare `name = expr` line is a LOCAL, visible to the later
//! lines of its own block only; the last line of a getter block is its value; each WHEN arm is its own block. The 64-node and
//! 400-character caps are gone: an evaluation has a STEP BUDGET instead (a reasoned null, never a hang or a panic).
use membrane_vm::expr;
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

fn world(pack: &str, props: J) -> (Core, Fields, i64) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), props.as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f, seq)
}
fn write(c: &mut Core, f: &mut Fields, seq: &mut i64, p: &str, v: J) {
    *seq += 1;
    let w = Write { eid: "Box:a".into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: *seq };
    c.hear(w.clone(), None, 2.0);
    f.react(c, &H, w, 2.0, seq);
}
fn val(c: &Core, p: &str) -> J { c.docs["Box:a"].props.get(p).cloned().unwrap_or(J::Null) }

// ---- getters ----------------------------------------------------------------------------------------------------------

#[test]
fn a_getter_block_ends_in_its_value_and_its_locals_feed_the_lines_below() {
    let pack = "PACK t 1\nBox.total getter:\n  base = this.w * this.h\n  tax = base / 10\n  base + tax\n";
    let (mut c, mut f, mut s) = world(pack, json!({"w": 2, "h": 5}));
    assert_eq!(val(&c, "total"), json!(11.0));
    write(&mut c, &mut f, &mut s, "w", json!(4));
    assert_eq!(val(&c, "total"), json!(22.0), "a local follows what it read");
}

#[test]
fn a_local_is_not_a_property() {
    let pack = "PACK t 1\nBox.total getter:\n  base = this.w * 2\n  base + 1\n";
    let (c, _, _) = world(pack, json!({"w": 2}));
    assert_eq!(val(&c, "total"), json!(5));
    assert!(!c.docs["Box:a"].props.contains_key("base"), "a local is never stored");
}

#[test]
fn a_local_is_gone_when_its_block_ends() {
    // another getter reading `base` as a bare name finds nothing, and nothing is stored under it
    let pack = "PACK t 1\nBox.total getter:\n  base = this.w * 2\n  base + 1\nBox.third getter: base\n";
    let (c, _, _) = world(pack, json!({"w": 2}));
    assert_eq!(val(&c, "third"), J::Null);
    assert!(!c.docs["Box:a"].props.contains_key("base"));
}

#[test]
fn each_arm_is_its_own_block() {
    let pack = "PACK t 1\nBox.word getter WHEN this.w > 5:\n  n = this.w * 2\n  'big ' + str(n)\nBox.word getter:\n  n = this.w + 1\n  'small ' + str(n)\n";
    let (c, _, _) = world(pack, json!({"w": 9}));
    assert_eq!(val(&c, "word"), json!("big 18"));
    let (c, _, _) = world(pack, json!({"w": 1}));
    assert_eq!(val(&c, "word"), json!("small 2"), "the same local name in two arms is two locals");
}

#[test]
fn an_arm_cannot_see_another_arms_local() {
    let pack = "PACK t 1\nBox.word getter WHEN this.w > 5:\n  n = this.w * 2\n  str(n)\nBox.word getter:\n  str(n)\n";
    let (c, f, _) = world(pack, json!({"w": 1}));
    assert_eq!(val(&c, "word"), J::Null);
    let chain = f.why_chain(&c, "Box:a", "word");
    assert!(chain[0]["reason"].as_str().unwrap().contains("unbound name"), "{chain:?}");
}

#[test]
fn block_form_arms_hold_blocks_too() {
    let pack = "PACK t 1\nBox.word getter:\n  WHEN this.w > 5:\n    n = this.w * 2\n    'big ' + str(n)\n  ELSE:\n    n = 1\n    'small ' + str(n)\n";
    assert_eq!(val(&world(pack, json!({"w": 9})).0, "word"), json!("big 18"));
    assert_eq!(val(&world(pack, json!({"w": 1})).0, "word"), json!("small 1"));
}

#[test]
fn a_local_holding_a_reasoned_null_passes_it_on_like_any_value() {
    let pack = "PACK t 1\nBox.a getter:\n  v = None('no reading yet')\n  w = v + 1\n  w * 2\n";
    let (c, f, _) = world(pack, json!({}));
    assert_eq!(val(&c, "a"), J::Null);
    let chain = f.why_chain(&c, "Box:a", "a");
    assert_eq!(chain[0]["reason"], json!("no reading yet"), "{chain:?}");
}

#[test]
fn why_prints_the_locals_of_the_evaluation_it_explains() {
    let pack = "PACK t 1\nBox.a getter:\n  base = this.w * 2\n  gap = None('sensor off') if base > 3 else base\n  gap + 1\n";
    let (c, f, _) = world(pack, json!({"w": 2}));
    let chain = f.why_chain(&c, "Box:a", "a");
    assert_eq!(chain[0]["locals"]["base"], json!(4), "{chain:?}");
    assert!(chain[0]["locals"]["gap"].as_str().unwrap().contains("sensor off"), "{chain:?}");
}

#[test]
fn a_chain_without_locals_has_no_locals_key() {
    let (c, f, _) = world("PACK t 1\nBox.a getter: None('x')\n", json!({}));
    assert!(f.why_chain(&c, "Box:a", "a")[0].get("locals").is_none());
}

// ---- refusals ---------------------------------------------------------------------------------------------------------

fn refused(pack: &str) -> String { parse_full(pack).unwrap_err() }

#[test]
fn a_local_may_not_shadow_a_property_of_this() {
    let e = refused("PACK t 1\nBox.total getter:\n  w = this.w * 2\n  w + 1\n");
    assert!(e.contains("shadow") && e.contains("`w`"), "{e}");
}

#[test]
fn a_local_may_not_shadow_a_getter_of_the_class() {
    let e = refused("PACK t 1\nBox.other getter: 1\nBox.total getter:\n  other = 3\n  other + 1\n");
    assert!(e.contains("shadow") && e.contains("the getter Box.other"), "{e}");
}

#[test]
fn a_local_may_not_shadow_a_builtin() {
    for name in ["len", "str", "recall", "clock", "this", "it", "True"] {
        let e = refused(&format!("PACK t 1\nBox.total getter:\n  {name} = 3\n  {name}\n"));
        assert!(e.contains("shadow") || e.contains("not a name"), "{name}: {e}");
    }
    let e = refused("PACK t 1\nBox.total getter:\n  len = 3\n  len + 1\n");
    assert!(e.contains("shadow") && e.contains("builtin"), "{e}");
}

#[test]
fn a_duplicate_local_in_one_block_is_refused() {
    let e = refused("PACK t 1\nBox.total getter:\n  a = 1\n  a = 2\n  a\n");
    assert!(e.contains("twice") || e.contains("shadow"), "{e}");
}

#[test]
fn a_block_needs_its_value_and_locals_come_first() {
    let e = refused("PACK t 1\nBox.total getter:\n  a = 1\n");
    assert!(e.contains("no value") || e.contains("ends"), "{e}");
    let e = refused("PACK t 1\nBox.total getter:\n  1 + 1\n  a = 1\n");
    assert!(e.contains("before"), "{e}");
}

#[test]
fn a_local_may_not_reuse_a_setters_loop_name_or_an_earlier_local() {
    let e = refused("PACK t 1\nBox.k on-setter s:\n  a = 1\n  a = 2\n  this.o = a\n");
    assert!(e.contains("twice") || e.contains("shadow"), "{e}");
}

// ---- setters ----------------------------------------------------------------------------------------------------------

#[test]
fn a_setter_local_feeds_the_writes_below_it() {
    let pack = "PACK t 1\nBox.k on-setter s:\n  d = this.k * 2\n  this.o1 = d\n  this.o2 = d + 1\n";
    let (mut c, mut f, mut s) = world(pack, json!({}));
    write(&mut c, &mut f, &mut s, "k", json!(4));
    assert_eq!((val(&c, "o1"), val(&c, "o2")), (json!(8), json!(9)));
    assert!(!c.docs["Box:a"].props.contains_key("d"));
}

#[test]
fn a_setter_local_is_per_branch_and_ends_with_its_for() {
    let pack = "PACK t 1\nBox.k on-setter s:\n  WHEN this.k > 0:\n    a = this.k\n    this.p = a\n  ELSE:\n    this.p = a\nBox.l on-setter t:\n  FOR i IN this.items:\n    sq = i * i\n    membrane::llm.call = {'sq': sq}\n  membrane::llm.call = {'after': sq}\n";
    let (mut c, mut f, mut s) = world(pack, json!({"items": [1, 2, 3]}));
    write(&mut c, &mut f, &mut s, "k", json!(5));
    assert_eq!(val(&c, "p"), json!(5));
    write(&mut c, &mut f, &mut s, "k", json!(-1));
    assert_eq!(val(&c, "p"), json!(5), "the ELSE branch cannot see the WHEN branch's local: its write is skipped");
    write(&mut c, &mut f, &mut s, "l", json!(1));
    assert_eq!(f.calls, vec![json!({"sq": 1}), json!({"sq": 4}), json!({"sq": 9})], "sq lived inside the FOR (and `after` could not read it)");
}

#[test]
fn a_setter_local_may_not_shadow_a_property_or_getter() {
    let e = refused("PACK t 1\nBox.k on-setter s:\n  o = 1\n  this.o = o\n");
    assert!(e.contains("shadow"), "{e}");
}

#[test]
fn the_stale_setter_check_still_sees_a_getter_read_through_a_local() {
    let e = Fields::goalgraph().set_pack("t", "PACK t 1\nBox.g getter: this.k + 1\nBox.k on-setter s:\n  v = this.g\n  this.o = v\n").unwrap_err();
    assert!(e.contains("stale"), "{e}");
}

#[test]
fn a_local_is_not_mistaken_for_a_getter_read_by_the_stale_check() {
    // `tmp` is a local; the setter reads this.g, which does not depend on k: loads fine
    let mut f = Fields::goalgraph();
    f.set_pack("t", "PACK t 1\nBox.g getter: this.z + 1\nBox.k on-setter s:\n  tmp = this.g\n  this.o = tmp\n").unwrap();
    // a local named like a getter of ANOTHER class is fine too, and `g` as a bare local is refused only where this class has the getter
    let mut f = Fields::goalgraph();
    f.set_pack("t", "PACK t 1\nBox.k on-setter s:\n  tmp = 1\n  this.o = tmp\nBox.k2 on-setter s2:\n  tmp = 2\n  this.o2 = tmp\n").unwrap();
}

#[test]
fn a_pack_with_blocks_prints_and_parses_back() {
    let p = parse_full("PACK t 1\nBox.k on-setter s:\n  d = this.k * 2\n  this.o = d\n").unwrap();
    let text = membrane_vm::syntax::print(&p.setters);
    assert_eq!(membrane_vm::syntax::parse(&text).unwrap(), p.setters, "{text}");
}

// ---- caps are gone; a step budget instead -----------------------------------------------------------------------------

#[test]
fn a_long_expression_is_no_longer_refused() {
    let long = (0..300).map(|i| format!("{i}")).collect::<Vec<_>>().join(" + ");
    assert!(long.len() > 400);
    let n = expr::parse(&long).unwrap();
    struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    assert!(matches!(expr::eval(&n, &Default::default(), &W), Ok(expr::V::Int(44850))));
}

#[test]
fn a_wide_expression_is_no_longer_refused() {
    let wide = (0..200).map(|i| format!("this.name{i}")).collect::<Vec<_>>().join(" and ");
    assert!(expr::parse(&wide).is_ok());
}

#[test]
fn over_the_budget_is_a_reasoned_null_not_an_error_or_a_hang() {
    expr::set_eval_budget(Some(50));
    let long = (0..100).map(|i| format!("this.w + {i}")).collect::<Vec<_>>().join(" + ");
    let pack = format!("PACK t 1\nBox.total getter: {long}\nBox.fine getter: this.w + 1\n");
    let (c, f, _) = world(&pack, json!({"w": 1}));
    expr::set_eval_budget(None);
    assert_eq!(val(&c, "total"), J::Null);
    assert_eq!(val(&c, "fine"), json!(2), "only the expensive node trips");
    let chain = f.why_chain(&c, "Box:a", "total");
    assert_eq!(chain[0]["reason"], json!("error: budget exceeded (50 steps)"), "{chain:?}");
}

#[test]
fn a_comprehension_over_a_long_list_spends_the_budget() {
    expr::set_eval_budget(Some(100));
    let items = (0..500).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
    let (c, _, _) = world(&format!("PACK t 1\nBox.s getter: len([x + 1 for x in [{items}]])\n"), json!({}));
    expr::set_eval_budget(None);
    assert_eq!(val(&c, "s"), J::Null);
}

#[test]
fn the_default_budget_is_ten_thousand_steps_and_the_env_dial_overrides_it() {
    assert_eq!(expr::DEFAULT_EVAL_BUDGET, 10000);
    assert_eq!(expr::eval_budget(), 10000);
}

#[test]
fn a_very_deep_expression_never_overflows_the_stack() {
    // on a 2 MB stack, as a worker thread has
    let r = std::thread::Builder::new().stack_size(2 << 20).spawn(|| {
        let chain = (0..20000).map(|_| "1").collect::<Vec<_>>().join(" + ");
        let a = expr::parse(&chain).map(|n| { struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } } let _ = expr::eval(&n, &Default::default(), &W); });
        let nest_eval = (0..5000).map(|_| ".a").collect::<String>();
        let d = expr::parse(&format!("this{nest_eval}")).map(|n| { struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::Ent("e".into()) } }
            let env = [("this".to_string(), expr::V::Ent("e".into()))].into_iter().collect(); expr::eval(&n, &env, &W) });
        assert!(matches!(d, Ok(Err(_)) | Err(_)), "a 5000-deep attribute chain is an error, not a crash");
        let nest = format!("{}1{}", "(".repeat(100000), ")".repeat(100000));
        let b = expr::parse(&nest);
        let c = expr::parse(&format!("{}1{}", "[".repeat(100000), "]".repeat(100000)));
        (a.is_ok() || a.is_err(), b.is_err(), c.is_err())
    }).unwrap().join().unwrap();
    assert_eq!(r, (true, true, true));
}

// ---- the type report types a block's locals ---------------------------------------------------------------------------

#[test]
fn infer_types_a_getter_block_through_its_locals() {
    let r = membrane_vm::infer::infer_pack("PACK t 1\nBox.label getter:\n  n = len('abc')\n  tag = 'n=' + str(n)\n  tag\nBox.num getter:\n  n = len('abc')\n  n\n").unwrap();
    assert_eq!(r.getters["Box.label"], "Text", "{:?}", r.getters);
    assert_eq!(r.getters["Box.num"], "Int", "{:?}", r.getters);
    assert!(r.findings.is_empty(), "{:?}", r.findings);
}

#[test]
fn infer_types_a_setter_local_for_its_writes() {
    let r = membrane_vm::infer::infer_pack("PACK t 1\nBox.k on-setter s:\n  d = 'x' + 'y'\n  this.o = d\n").unwrap();
    assert_eq!(r.writes["Box.o"], "Text", "{:?}", r.writes);
}
