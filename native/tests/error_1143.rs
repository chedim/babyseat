//! The c1f `Error` value kind and the `is` operator (#1143, docs/c1f-error.md; operator decisions 2026-11-13).
//! null = not initialised / absent / not applicable (keeps its `why`); Error = a failure, carries its reason (and its remedy, as text);
//! `x == None` is False for an Error; an Error PROPAGATES; old routes see it as a plain null; the check is `x is Error`.
//! Round 1: the `is` grammar, the value (`V::Error`), `kind`, `why`, `to_json`, `py_eq` against None.
use membrane_vm::expr::{eval, parse, parse_block, py_eq, to_json, truthy, World, V};
use std::collections::HashMap;

/// a world with entities `Trip:a` (classes Trip, Place), `Place:b` (class Place) and `Box:c` (no Trip)
struct W;
impl World for W {
    fn attr(&self, _: &str, _: &str) -> V { V::None }
    fn is_class(&self, eid: &str, cls: &str) -> bool {
        let has: &[&str] = match eid { "Trip:a" => &["Trip", "Place"], "Place:b" => &["Place"], "Box:c" => &["Box"], _ => &[] };
        cls == "Entity" && !has.is_empty() || has.iter().any(|c| c.eq_ignore_ascii_case(cls))
    }
}

fn run(src: &str, env: &[(&str, V)]) -> Result<V, String> {
    let env: HashMap<String, V> = env.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
    eval(&parse(src)?, &env, &W)
}
fn err() -> V { V::Error("timed out after 5 s; start it again with a bigger timeout_s".into()) }
fn truth(src: &str, env: &[(&str, V)]) -> bool { match run(src, env).unwrap_or_else(|e| panic!("{src}: {e}")) { V::Bool(b) => b, v => panic!("{src} -> {v:?}") } }

#[test]
fn is_error_tells_a_failure_from_everything_else() {
    let e = [("e", err())];
    assert!(truth("e is Error", &e));
    for v in [V::None, V::Nil("absent: A:x.p".into()), V::Int(1), V::Str("x".into()), V::Bool(false), V::List(vec![])] {
        assert!(!truth("v is Error", &[("v", v)]));
    }
}

#[test]
fn is_names_the_value_kinds_by_the_casts_spellings() {
    for (src, want) in [("1 is Int", true), ("1 is Float", false), ("1.5 is Float", true), ("'a' is Text", true), ("1 is Text", false), ("True is Bool", true),
                        ("1 is Bool", false), ("[1] is List", true), ("{'a': 1} is Dict", true), ("[1] is Dict", false), ("None is Int", false)] {
        assert_eq!(truth(src, &[]), want, "{src}");
    }
}

#[test]
fn is_follows_the_class_list_of_an_entity() {
    let v = [("t", V::Ent("Trip:a".into())), ("p", V::Ent("Place:b".into()))];
    assert!(truth("t is Trip", &v));
    assert!(truth("t is Place", &v), "the second class of the list counts");
    assert!(truth("p is Place", &v));
    assert!(!truth("p is Trip", &v));
    assert!(truth("p is Entity", &v), "the root class is every entity's last class");
    assert!(!truth("1 is Trip", &v), "a value is not of a class");
    assert!(!truth("None is Trip", &v));
}

#[test]
fn is_with_a_list_of_names_needs_all_of_them() {
    let v = [("t", V::Ent("Trip:a".into())), ("p", V::Ent("Place:b".into())), ("b", V::Ent("Box:c".into()))];
    assert!(truth("t is (Trip, Place)", &v));
    assert!(!truth("p is (Trip, Place)", &v));
    assert!(!truth("b is (Trip, Place)", &v));
    assert!(truth("p is (Place,)", &v));
    assert!(truth("t is Trip or t is Box", &v), "`or` is the any form");
}

#[test]
fn is_not_negates() {
    let v = [("e", err()), ("p", V::Ent("Place:b".into()))];
    assert!(truth("1 is not Error", &[]));
    assert!(!truth("e is not Error", &v));
    assert!(truth("p is not Trip", &v));
    assert!(!truth("p is not Place", &v));
    assert!(!truth("not e is Error", &v), "`not x is Error` reads as `not (x is Error)`");
    assert!(truth("not 1 is Error", &[]));
}

#[test]
fn is_sits_at_comparison_precedence() {
    assert!(truth("1 + 1 is Int", &[]));
    assert!(truth("1 is Int and 'a' is Text", &[]));
    assert!(truth("(1 is Int) == True", &[]));
}

#[test]
fn is_that_cannot_be_read_is_a_parse_error() {
    for (src, want) in [
        ("1 is None", "== None"),                       // the null test stays `x == None`
        ("1 is 3", "is"), ("1 is 'a'", "is"), ("1 is x.y", "is"), ("1 is", "is"), ("1 is (", "is"), ("1 is ()", "is"),
        ("1 is (Int, Text)", "never"),                  // two value kinds that can never both match
        ("1 is (Int, Error)", "never"), ("p is (Trip, Int)", "never"), ("1 is (Float, Int)", "never"),
        ("a == b is Error", "chain"), ("a is Error == b", "chain"), ("a is Int is Int", "chain"), ("a < b is Int", "chain"),
    ] {
        let e = parse(src).err().unwrap_or_else(|| panic!("{src} parsed"));
        assert!(e.contains(want), "{src}: {e}");
    }
    assert!(parse("1 is (Int, Int)").is_ok(), "the same kind twice can match");
    assert!(parse("e is (Trip, Place)").is_ok());
    assert!(parse_block("x = 1 is Int\nx").is_ok());
}

#[test]
fn the_name_error_is_reserved_as_a_kind() {
    assert!(membrane_vm::expr::RESERVED.contains(&"Error"));
    let local = membrane_vm::syntax::parse_full("PACK p 1\nA.x getter:\n  Error = 1\n  Error\n").err().unwrap_or_default();
    assert!(local.contains("reserved"), "a local may not be called Error: {local}");
    for pack in ["PACK p 1\nError.x getter: 1\n", "PACK p 1\nError.x on-setter s:\n  ALWAYS:\n    this.y = 1\n", "PACK p 1\nRESIDENT Error\n", "PACK p 1\nVIRTUAL error\n"] {
        let e = membrane_vm::syntax::parse_full(pack).err().unwrap_or_else(|| panic!("{pack:?} loaded"));
        assert!(e.contains("Error") && e.contains("reserved"), "{pack:?}: {e}");
    }
}

#[test]
fn an_error_is_not_null_and_not_a_bool() {
    let e = [("e", err())];
    assert!(!py_eq(&err(), &V::None), "`x == None` is False for an Error");
    assert!(!py_eq(&V::None, &err()));
    assert!(!py_eq(&err(), &V::Nil("absent: x".into())));
    assert!(truth("e == None", &[("e", V::None)]), "a plain null still is None");
    assert!(!truth("e == None", &e));
    assert!(truth("e != None", &e));
    assert!(!truth("e != None", &[("e", V::None)]));
    assert!(!truth("None == e", &e));
}

#[test]
fn an_error_serialises_as_null_for_the_old_routes() {
    assert_eq!(to_json(&err()).unwrap(), serde_json::Value::Null);
    assert_eq!(to_json(&V::List(vec![V::Int(1), err()])).unwrap(), serde_json::json!([1, null]));
}

#[test]
fn why_reads_an_errors_text() {
    let txt = |v: V| if let V::Str(t) = v { Some(t) } else { None };
    assert_eq!(txt(run("why(e)", &[("e", err())]).unwrap()).as_deref(), Some("timed out after 5 s; start it again with a bigger timeout_s"));
    assert_eq!(txt(run("why(n)", &[("n", V::Nil("absent: A:x.p".into()))]).unwrap()).as_deref(), Some("absent: A:x.p"), "a null keeps its why");
}

#[test]
fn an_error_made_in_a_pack_is_error_of_a_reason() {
    let r = run("Error('no such file; make it first')", &[]).unwrap();
    assert!(matches!(&r, V::Error(t) if t == "no such file; make it first"), "{r:?}");
    assert!(truth("Error('x') is Error", &[]));
    assert!(!truth("Error('x') == None", &[]));
    assert!(run("Error()", &[]).is_err(), "an Error says why: one reason");
    assert!(run("Error('a', 'b')", &[]).is_err(), "the remedy is part of the text: one argument");
    let r = run("Error('no ' + 'file')", &[]).unwrap();
    assert!(matches!(&r, V::Error(t) if t == "no file"));
}

#[test]
fn truthy_of_an_error_is_never_asked_by_a_getter() {
    // `truthy` is the plain-Rust helper other code calls; an Error is not true (the evaluator never reaches it: it propagates first)
    assert!(!truthy(&err()));
}
