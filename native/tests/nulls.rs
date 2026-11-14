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

// ---- casts: a checked narrowing, spelled as the type's capitalized name -------------------------------------------------------
// Int(x) is x when x is an int; otherwise an Error('not-a-type: int expected, got text') (a failure, #1143). A null stays the null it was (SQL's
// CAST(NULL AS int) is NULL, and its reason survives). `Int(v) or Text(v)` is the union of two casts.
fn is_nil_why(r: &V, want: &str) -> bool { why(r).map(|w| w == want).unwrap_or(false) }
fn is_err_why(r: &V, want: &str) -> bool { matches!(r, V::Error(w) if w == want) }

#[test]
fn a_cast_gives_the_value_back_when_it_has_the_type() {
    for (src, want) in [("Int(3)", V::Int(3)), ("Float(2.5)", V::Float(2.5)), ("Text('a')", V::Str("a".into())), ("Bool(True)", V::Bool(true)),
                        ("Num(3)", V::Int(3)), ("Num(2.5)", V::Float(2.5)), ("List([1, 2])", V::List(vec![V::Int(1), V::Int(2)]))] {
        let r = run(src, &[]).unwrap();
        assert!(py_eq(&r, &want), "{src} -> {r:?}");
    }
    assert!(matches!(run("Dict({'a': 1})", &[]).unwrap(), V::Dict(_)));
}

#[test]
fn a_cast_of_a_value_that_does_not_have_the_type_is_an_error_that_says_so() {
    for (src, want) in [("Int('a')", "not-a-type: int expected, got text"), ("Int(3.0)", "not-a-type: int expected, got float"),
                        ("Float(3)", "not-a-type: float expected, got int"), ("Text(3)", "not-a-type: text expected, got int"),
                        ("Bool(1)", "not-a-type: bool expected, got int"), ("Num('3')", "not-a-type: num expected, got text"),
                        ("List({})", "not-a-type: list expected, got dict"), ("Dict([])", "not-a-type: dict expected, got list")] {
        let r = run(src, &[]).unwrap();
        assert!(is_err_why(&r, want), "{src} -> {r:?}");
    }
}

#[test]
fn a_cast_of_a_null_is_that_null() {
    assert!(is_nil_why(&run("Int(p)", &[("p", pending())]).unwrap(), "pending: read(/a)"));
    let r = run("Text(n)", &[("n", V::None)]).unwrap();
    assert!(matches!(r, V::None | V::Nil(_)), "{r:?}");
}

#[test]
fn a_failed_cast_is_an_error_that_or_does_not_hide_and_a_union_is_written_with_is() {
    // `Int(v) or Text(v)` used to be the union of two casts; a failed cast is an Error now and `or` never hides an Error: the union is `is`
    assert!(is_err_why(&run("Int(v) or Text(v)", &[("v", V::Str("x".into()))]).unwrap(), "not-a-type: int expected, got text"));
    assert!(py_eq(&run("v if v is Int or v is Text else None", &[("v", V::Str("x".into()))]).unwrap(), &V::Str("x".into())));
    assert!(py_eq(&run("Int(v) or Text(v)", &[("v", V::Int(4))]).unwrap(), &V::Int(4)));
    assert!(is_err_why(&run("Int('a') + 1", &[]).unwrap(), "not-a-type: int expected, got text"));
    assert!(py_eq(&run("Int('a') == None", &[]).unwrap(), &V::Bool(false)), "an Error is not null");
}

#[test]
fn why_reads_the_reason_of_a_null_as_text_and_is_null_for_anything_else() {
    let txt = |v: V| if let V::Str(t) = v { Some(t) } else { None };
    assert_eq!(txt(run("why(None('no file'))", &[]).unwrap()).as_deref(), Some("no file"));
    assert_eq!(txt(run("why(x)", &[("x", V::Nil("pending: read(/a)".into()))]).unwrap()).as_deref(), Some("pending: read(/a)"));
    assert!(matches!(run("why(1)", &[]).unwrap(), V::None));
    assert_eq!(txt(run("why('t') or 'none'", &[]).unwrap()).as_deref(), Some("none"));
}
