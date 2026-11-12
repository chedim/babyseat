//! `x[i]` in a c1f expression (the operator, 2026-11-12): a list by position (negative from the end), a dict by key, an
//! entity by property name. A miss is None, never an error; a base that is None stays None. No slices.
use membrane_vm::expr::{eval, parse, py_str, World, V};
use std::collections::HashMap;

struct W;
impl World for W {
    fn attr(&self, eid: &str, attr: &str) -> V { if attr == "name" { V::Str(format!("name of {eid}")) } else { V::None } }
    fn host(&self, _: &str, _: &[V]) -> Option<Result<V, String>> { None }
}

fn run(src: &str) -> Result<V, String> {
    let env = HashMap::from([
        ("xs".to_string(), V::List(vec![V::Int(10), V::Int(20), V::Int(30)])),
        ("d".to_string(), V::Dict(vec![("a".to_string(), V::Int(1)), ("b".to_string(), V::List(vec![V::Str("x".into())]))])),
        ("e".to_string(), V::Ent("Thing:a".into())),
        ("n".to_string(), V::None),
    ]);
    eval(&parse(src)?, &env, &W)
}

fn int(v: V) -> i64 { match v { V::Int(i) => i, o => panic!("not an int: {:?}", py_str(&o)) } }

#[test]
fn a_list_by_position_and_from_the_end() {
    assert_eq!(int(run("xs[0]").unwrap()), 10);
    assert_eq!(int(run("xs[2]").unwrap()), 30);
    assert_eq!(int(run("xs[-1]").unwrap()), 30);
    assert_eq!(int(run("xs[-3]").unwrap()), 10);
}

#[test]
fn a_miss_is_none_not_an_error() {
    assert!(matches!(run("xs[3]").unwrap(), V::None));
    assert!(matches!(run("xs[-4]").unwrap(), V::None));
    assert!(matches!(run("d['zz']").unwrap(), V::None));
    assert!(matches!(run("n[0]").unwrap(), V::None));
    assert!(matches!(run("[][0]").unwrap(), V::None));
}

#[test]
fn a_dict_by_key_and_it_chains() {
    assert_eq!(int(run("d['a']").unwrap()), 1);
    assert_eq!(py_str(&run("d['b'][0]").unwrap()), "x");
    assert_eq!(int(run("xs[xs[0] - 10]").unwrap()), 10);      // the index is an expression
}

#[test]
fn an_entity_by_property_name() {
    assert_eq!(py_str(&run("e['name']").unwrap()), "name of Thing:a");
    assert!(run("e['_private']").is_err());                   // the same rule as e._private
}

#[test]
fn slices_and_wrong_index_types_are_refused() {
    assert!(run("xs[0:2]").unwrap_err().contains("slice"));
    assert!(run("xs['a']").unwrap_err().contains("index"));
    assert!(run("d[0]").unwrap_err().contains("index"));
    assert!(run("5[0]").unwrap_err().contains("index"));
}

#[test]
fn it_counts_toward_the_node_cap_and_composes_with_the_rest() {
    assert_eq!(int(run("xs[0] + xs[1] + len(xs)").unwrap()), 33);
    assert_eq!(int(run("[x for x in xs][1]").unwrap()), 20);
}
