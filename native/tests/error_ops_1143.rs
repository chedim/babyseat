//! `Error` in the operators (#1143, docs/c1f-error.md section (b), operator decisions 2026-11-13).
//! An Error is contagious everywhere a value is USED and is never hidden by a default: arithmetic, `-x`, `x[i]`, `x.a`, calls, string `+`,
//! comparisons, `not`, `and`, `or`, a condition (`if`, a comprehension's `if`) all give that Error. `is`, `why(x)` and `x == None` read it
//! without propagating it. A list or dict holding an Error is not an Error; the element stays one inside the evaluation.
use membrane_vm::expr::{eval, parse, parse_block, World, V};
use std::collections::HashMap;

struct W;
impl World for W { fn attr(&self, _: &str, _: &str) -> V { V::None } }

fn run(src: &str, env: &[(&str, V)]) -> Result<V, String> {
    let env: HashMap<String, V> = env.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
    eval(&parse_block(src).or_else(|_| parse(src))?, &env, &W)
}
fn e(t: &str) -> V { V::Error(t.into()) }
fn reason(v: &V) -> Option<String> { if let V::Error(r) = v { Some(r.clone()) } else { None } }
fn env() -> Vec<(&'static str, V)> { vec![("e", e("boom")), ("f", e("second")), ("n", V::None), ("p", V::Nil("pending: read(/a)".into())), ("ent", V::Ent("A:x".into()))] }
fn gives_error(src: &str, want: &str) { let r = run(src, &env()).unwrap_or_else(|x| panic!("{src} raised {x}")); assert_eq!(reason(&r).as_deref(), Some(want), "{src} -> {r:?}"); }
fn truth(src: &str) -> bool { match run(src, &env()).unwrap_or_else(|x| panic!("{src}: {x}")) { V::Bool(b) => b, v => panic!("{src} -> {v:?}") } }

#[test]
fn arithmetic_negation_index_attribute_and_calls_give_that_error() {
    for src in ["e + 1", "1 + e", "e * 2", "e - e", "e / 2", "-e", "'x' + e", "e + 'x'", "e[0]", "[1][e]", "e['k']", "e.name", "abs(e)", "len(e)", "str(e)", "max(1, e)",
                "join(e, ',')", "sort_by(e, 'k')", "Int(e)", "lower(e)", "prop(e, 'x')", "[1, 2] + e"] {
        gives_error(src, "boom");
    }
}

#[test]
fn an_error_wins_over_a_plain_null_and_the_first_error_wins() {
    gives_error("n + e", "boom");
    gives_error("p + e", "boom");
    gives_error("e + n", "boom");
    gives_error("e + f", "boom");
    gives_error("f + e", "second");
    gives_error("max(n, p, f, e)", "second");
}

#[test]
fn a_plain_null_still_propagates_as_a_null() {
    let r = run("n + 1", &env()).unwrap();
    assert!(matches!(r, V::Nil(_)), "{r:?}");
    assert!(matches!(run("len(n)", &env()).unwrap(), V::Int(0)), "the silent coercions of a plain null are unchanged");
}

#[test]
fn comparisons_over_an_error_give_the_error_not_a_bool() {
    for src in ["e == 3", "3 == e", "e != 3", "e < 1", "1 >= e", "e in [1]", "1 in e", "1 not in e", "e == e", "1 < e < 3", "e == f"] {
        gives_error(src, "boom");
    }
}

#[test]
fn x_equals_none_is_false_for_an_error_and_never_an_error() {
    assert!(!truth("e == None"));
    assert!(truth("e != None"));
    assert!(!truth("None == e"));
    assert!(truth("None != e"));
    assert!(!truth("e == p"), "a null that says why is still a null: an Error is not it");
    assert!(truth("n == None") && truth("p == None"));
}

#[test]
fn not_and_or_over_an_error_give_the_error_and_a_default_never_hides_it() {
    gives_error("not e", "boom");
    gives_error("e or 'default'", "boom");
    gives_error("n or e", "boom");
    gives_error("e and 1", "boom");
    gives_error("1 and e", "boom");
    gives_error("e or e", "boom");
    // an operand that is not reached is not evaluated
    assert!(matches!(run("'a' or e", &env()).unwrap(), V::Str(_)));
    assert!(matches!(run("False and e", &env()).unwrap(), V::Bool(false)));
    // a plain null still takes the default
    assert!(matches!(run("n or 'd'", &env()).unwrap(), V::Str(_)));
    assert!(matches!(run("p or 'd'", &env()).unwrap(), V::Str(_)));
}

#[test]
fn a_condition_over_an_error_is_that_error_not_the_else_arm() {
    gives_error("1 if e else 2", "boom");
    gives_error("[x for x in [1, 2] if e]", "boom");
    gives_error("[x for x in e]", "boom");
    assert!(truth("(1 if e is Error else 2) == 1"));
    assert!(truth("(1 if e is not Error else 2) == 2"));
}

#[test]
fn is_and_why_read_an_error_without_propagating() {
    assert!(truth("e is Error"));
    assert!(!truth("not (e is Error)"));
    assert!(truth("why(e) == 'boom'"));
    assert!(truth("[e][0] is Error"));
}

#[test]
fn a_list_or_dict_holding_an_error_is_not_an_error_and_the_element_stays_one() {
    assert!(truth("[1, e] is List"));
    assert!(truth("len([1, e]) == 2"));
    assert!(truth("[1, e][1] is Error"));
    assert!(truth("{'a': e}['a'] is Error"));
    assert!(truth("{'a': e} is Dict"));
    assert!(truth("why([1, e][1]) == 'boom'"));
    // the idiom to drop failed items
    let r = run("[x for x in [1, e, 3] if not (x is Error)]", &env()).unwrap();
    assert!(matches!(&r, V::List(l) if l.len() == 2), "{r:?}");
    // a comprehension's element may be an Error
    let r = run("[Error('bad ' + str(x)) for x in [1, 2]]", &env()).unwrap();
    assert!(matches!(&r, V::List(l) if l.len() == 2 && matches!(l[0], V::Error(_))), "{r:?}");
}

#[test]
fn a_local_may_hold_an_error_and_be_tested() {
    assert!(truth("x = Error('bad')\nx is Error"));
    gives_error("x = Error('bad')\nx + 1", "bad");
    gives_error("x = e\ny = 5\nx", "boom");
}

#[test]
fn an_entity_key_that_is_an_error_is_that_error() {
    gives_error("Goal:board[e]", "boom");
}
