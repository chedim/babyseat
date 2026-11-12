//! Nulls propagate (the operator, 2026-11-12: "like in postgres"). An operation on a null gives a null, not an error, and the null
//! carries WHY it is one (`pending: read(/a)`, `error: ...`, `absent`). Steps 1 and 2: errors on a null operand become a null with a
//! reason; the reason travels through arithmetic, attributes, indexes and calls. The silent coercions (len -> 0, iteration -> [],
//! plain -> '') are unchanged for now (step 3) and pinned here so the day they change is a visible, reviewed day.
use membrane_vm::expr::{eval, parse, py_eq, to_json, World, V};
use std::collections::HashMap;

struct W;
impl World for W { fn attr(&self, _: &str, _: &str) -> V { V::None } }

fn run(src: &str, env: &[(&str, V)]) -> Result<V, String> {
    let env: HashMap<String, V> = env.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
    eval(&parse(src)?, &env, &W)
}
fn why(v: &V) -> Option<String> { if let V::Nil(r) = v { Some(r.clone()) } else { None } }
fn pending() -> V { V::Nil("pending: read(/a)".into()) }

#[test]
fn a_null_operand_gives_a_null_not_an_error() {
    for src in ["n + 1", "1 + n", "n * 2", "n - n", "-n", "'x' + n", "n / 2"] {
        let r = run(src, &[("n", V::None)]).unwrap_or_else(|e| panic!("{src} errored: {e}"));
        assert!(matches!(r, V::Nil(_)), "{src} -> {r:?}");
        assert!(why(&r).unwrap().contains("absent"), "{src}: {:?}", why(&r));
    }
}

#[test]
fn the_reason_of_a_null_travels_through_everything_it_touches() {
    let p = [("p", pending())];
    for src in ["p + 1", "1 + p * 2", "-p", "p['k']", "p.x", "max(1, p)", "min(p, 2)", "[p][0] + 1", "abs(p)", "(p + 1) + 2", "{'a': p}['a'] + 1"] {
        let r = run(src, &p).unwrap_or_else(|e| panic!("{src} errored: {e}"));
        assert_eq!(why(&r).as_deref(), Some("pending: read(/a)"), "{src} -> {r:?}");
    }
}

#[test]
fn the_first_reason_wins_when_two_nulls_meet() {
    let r = run("a + b", &[("a", V::Nil("pending: sql(x)".into())), ("b", V::Nil("error: boom".into()))]).unwrap();
    assert_eq!(why(&r).as_deref(), Some("pending: sql(x)"));
}

#[test]
fn a_null_with_a_reason_is_still_null_the_old_ways() {
    let p = [("p", pending())];
    assert!(py_eq(&run("p == None", &p).unwrap(), &V::Bool(true)));
    assert!(py_eq(&run("not p", &p).unwrap(), &V::Bool(true)));
    assert!(py_eq(&run("p or 'd'", &p).unwrap(), &V::Str("d".into())));
    assert!(py_eq(&run("p if p else 'x'", &p).unwrap(), &V::Str("x".into())));
    assert_eq!(to_json(&pending()).unwrap(), serde_json::Value::Null);
}

#[test]
fn an_error_that_is_not_about_a_null_is_still_an_error() {
    assert!(run("len(3)", &[]).is_err());
    assert!(run("'a' + 1", &[]).is_err());
    assert!(run("n.x", &[("n", V::Int(3))]).is_err());
}

#[test]
fn the_silent_coercions_are_unchanged_until_step_three() {
    let p = [("l", pending()), ("n", V::None)];
    assert!(py_eq(&run("len(l)", &p).unwrap(), &V::Int(0)));
    assert!(py_eq(&run("[x for x in l]", &p).unwrap(), &V::List(vec![])));
    assert!(py_eq(&run("plain(n)", &p).unwrap(), &V::Str(String::new())));
    assert!(py_eq(&run("n > 0", &p).unwrap(), &V::Bool(false)));
}

#[test]
fn none_called_with_a_reason_is_a_null_that_says_why() {
    // None is a class like any other: None('why') constructs a null that carries its reason (a setter can write one)
    let r = run("None('sheeeeet')", &[]).unwrap();
    assert_eq!(why(&r).as_deref(), Some("sheeeeet"));
    assert_eq!(why(&run("None()", &[]).unwrap()).as_deref(), Some("null"));
    assert_eq!(why(&run("None('a') + 1", &[]).unwrap()).as_deref(), Some("a"));
    assert!(py_eq(&run("None('a') == None", &[]).unwrap(), &V::Bool(true)));
    assert!(py_eq(&run("None('a') or 'd'", &[]).unwrap(), &V::Str("d".into())));
    assert!(py_eq(&run("not None('a')", &[]).unwrap(), &V::Bool(true)));
    assert_eq!(to_json(&r).unwrap(), serde_json::Value::Null, "stored, it is a plain null");
    // a reason may be built from values
    assert_eq!(why(&run("None('no ' + 'file')", &[]).unwrap()).as_deref(), Some("no file"));
}
