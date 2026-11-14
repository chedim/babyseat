//! Where an `Error` is born (#1143, docs/c1f-error.md section (a), operator decisions 2026-11-13): a failed cast, a bad entity-literal key, a
//! failed parse (`json`), a getter body that raised, the step budget (time never makes one: the clock decides no value), and a pack's own `Error('reason')`.
//! What stays null: a missing property, `.x` of an absent value, `pending: ...`, a collected virtual entity, `f of a null`.
//! Old routes are unchanged: an Error is a plain null there (`Core.docs`), and it reads back as an Error through `why`/`failed`.
use membrane_vm::expr::{self, eval, parse, parse_block, World, V};
use membrane_vm::fields::Fields;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::collections::HashMap;

struct W;
impl World for W { fn attr(&self, _: &str, _: &str) -> V { V::None } }
fn run(src: &str, env: &[(&str, V)]) -> V {
    let env: HashMap<String, V> = env.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
    eval(&parse_block(src).or_else(|_| parse(src)).unwrap(), &env, &W).unwrap_or_else(|e| panic!("{src}: {e}"))
}
fn reason(v: &V) -> Option<String> { if let V::Error(r) = v { Some(r.clone()) } else { None } }

#[test]
fn a_failed_cast_is_an_error_a_cast_of_a_null_is_that_null() {
    for (src, want) in [("Int('a')", "not-a-type: int expected, got text"), ("Int(3.0)", "not-a-type: int expected, got float"), ("Text(3)", "not-a-type: text expected, got int"),
                        ("List({})", "not-a-type: list expected, got dict"), ("Num('3')", "not-a-type: num expected, got text")] {
        assert_eq!(reason(&run(src, &[])).as_deref(), Some(want), "{src}");
    }
    assert_eq!(reason(&run("Int('a') + 1", &[])).as_deref(), Some("not-a-type: int expected, got text"));
    assert!(matches!(run("Int('a') == None", &[]), V::Bool(false)));
    assert!(matches!(run("Int(n) == None", &[("n", V::None)]), V::Bool(true)), "a null stays the null it was");
    assert!(matches!(run("Int(p)", &[("p", V::Nil("pending: read(/a)".into()))]), V::Nil(_)));
    assert!(matches!(run("Int(3)", &[]), V::Int(3)));
}

#[test]
fn a_bad_entity_literal_key_is_an_error_a_null_key_keeps_its_own_null() {
    let r = run("Goal:g[None]", &[]);
    assert!(reason(&r).is_some_and(|t| t.contains("the key of Goal:g is null")), "{r:?}");
    let r = run("Goal:g['']", &[]);
    assert!(reason(&r).is_some_and(|t| t.contains("empty text")), "{r:?}");
    let r = run("Goal:g[[1]]", &[]);
    assert!(reason(&r).is_some_and(|t| t.contains("integer or text")), "{r:?}");
    let r = run("Goal:g[k]", &[("k", V::Nil("pending: read(/a)".into()))]);
    assert!(matches!(r, V::Nil(_)), "a key that is a null with a reason is that null: {r:?}");
}

#[test]
fn json_of_text_that_is_not_json_is_an_error_with_a_reason() {
    // operator decision 2026-11-13: bad text is a failed parse, an Error (the packs that meant "not JSON is fine" test it with `is Error`)
    for bad in ["json('{not json')", "json('')", "json('nope')"] {
        assert!(matches!(run(bad, &[]), V::Error(ref r) if r.starts_with("not-json")), "{bad}: {:?}", run(bad, &[]));
    }
    assert!(matches!(run("json('{\"a\": 1}')", &[]), V::Dict(_)));
    assert!(matches!(run("json(n)", &[("n", V::None)]), V::None), "a null stays a null");
    assert!(matches!(run("json(e)", &[("e", V::Error("x".into()))]), V::Error(ref r) if r == "x"), "an Error propagates");
    assert!(matches!(run("json('{bad') == None", &[]), V::Bool(false)), "an Error is not a null");
}

#[test]
fn an_operation_that_failed_because_of_a_null_stays_a_null() {
    // `f of a null`: the failure belongs to the earlier absence
    let r = run("lower(n)", &[("n", V::None)]);
    assert!(!matches!(r, V::Error(_)), "{r:?}");
    let r = run("n.x", &[("n", V::None)]);
    assert!(matches!(r, V::Nil(_)), "{r:?}");
}

// ---- in the field ------------------------------------------------------------------------------------------------------------

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}
fn world(pack: &str, props: J) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap_or_else(|e| panic!("{e}"));
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), props.as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f)
}
fn val(c: &Core, p: &str) -> J { c.docs["Box:a"].props.get(p).cloned().unwrap_or(J::Null) }
fn failed(c: &Core, p: &str) -> bool { c.failed.contains(&("Box:a".to_string(), p.to_string())) }
fn why(c: &Core, p: &str) -> Option<String> { c.why.get(&("Box:a".to_string(), p.to_string())).cloned() }

#[test]
fn a_getter_body_that_raised_is_an_error_with_the_raised_text_and_a_receipt_still_names_the_getter() {
    let (c, f) = world("PACK t 1\nBox.bad getter: this.w + 'a'\nBox.is_bad getter: this.bad is Error\nBox.bad_none getter: this.bad == None\nBox.fine getter: this.w + 1\n", json!({"w": 1}));
    assert_eq!(val(&c, "bad"), J::Null);
    assert!(failed(&c, "bad"));
    assert!(why(&c, "bad").is_some_and(|r| !r.starts_with("error") && r.contains("a")), "{:?}", why(&c, "bad"));
    assert_eq!(val(&c, "is_bad"), json!(true));
    assert_eq!(val(&c, "bad_none"), json!(false));
    assert_eq!(val(&c, "fine"), json!(2), "the rest of the engine runs");
    assert!(f.refusals.iter().any(|r| r["reason"].as_str().is_some_and(|s| s.contains("error in getter Box.bad"))), "{:?}", f.refusals);
}

#[test]
fn over_the_step_budget_is_an_error() {
    expr::set_eval_budget(Some(50));
    let long = (0..100).map(|i| format!("this.w + {i}")).collect::<Vec<_>>().join(" + ");
    let (c, _f) = world(&format!("PACK t 1\nBox.total getter: {long}\nBox.is_err getter: this.total is Error\nBox.fine getter: this.w + 1\n"), json!({"w": 1}));
    expr::set_eval_budget(None);
    assert_eq!(val(&c, "total"), J::Null);
    assert!(failed(&c, "total"));
    assert_eq!(why(&c, "total").as_deref(), Some("budget exceeded (50 steps)"));
    assert_eq!(val(&c, "is_err"), json!(true));
    assert_eq!(val(&c, "fine"), json!(2));
}

mod time {
    use super::*;
    use std::cell::Cell;
    const MS: u64 = 1_000_000;
    thread_local! { static NOW: Cell<u64> = const { Cell::new(0) }; static STEP: Cell<u64> = const { Cell::new(0) }; }
    fn fake() -> u64 { NOW.with(|n| { let v = n.get(); n.set(v + STEP.with(|s| s.get())); v }) }
    fn use_clock(step_ms: u64) { NOW.with(|n| n.set(0)); STEP.with(|s| s.set(step_ms * MS)); expr::set_clock(Some(fake)); }
    fn long() -> String {
        let items = (0..600).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
        format!("PACK t 1\nBox.slow getter: this.w + len([x for x in [{items}]])\nBox.dep getter: this.slow + 1\nBox.dep_err getter: this.dep is Error\n")
    }

    #[test]
    fn a_slow_evaluation_is_not_an_error_and_so_is_nothing_that_reads_it() {
        use_clock(6);   // 12 ms of self time against the 10 ms former allowance: it used to be an Error
        let (c, _f) = world(&long(), json!({"w": 1}));
        expr::set_clock(None);
        assert_eq!(val(&c, "slow"), json!(601));
        assert!(!failed(&c, "slow"));
        assert_eq!(val(&c, "dep"), json!(602));
        assert_eq!(val(&c, "dep_err"), json!(false));
        assert!(!c.docs.keys().any(|k| k.starts_with("Trip:")), "no kill is recorded");
    }
}
