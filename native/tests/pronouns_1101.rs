//! #1101: a bare it / that / they in a c1f expression, with no variable of that name, resolves through the host's
//! context stack at the moment it runs; unbound, it fails naming the pronoun (never a silent wrong target).
use membrane_vm::expr::{eval, parse, py_str, World, V};
use std::collections::HashMap;

struct W(Option<V>);
impl World for W {
    fn attr(&self, eid: &str, attr: &str) -> V { if attr == "name" { V::Str(format!("name of {eid}")) } else { V::None } }
    fn host(&self, f: &str, a: &[V]) -> Option<Result<V, String>> {
        if f == "pronoun" && matches!(a.first(), Some(V::Str(_))) { return self.0.clone().map(Ok); }
        None
    }
}

fn run(src: &str, env: &HashMap<String, V>, w: &W) -> Result<V, String> { eval(&parse(src)?, env, w) }

#[test]
fn it_resolves_to_the_entity_the_context_stack_names() {
    let w = W(Some(V::Str("membrane::goal:x".into())));
    assert_eq!(py_str(&run("it.name", &HashMap::new(), &w).unwrap()), "name of membrane::goal:x");
}

#[test]
fn they_resolves_to_a_list_of_entities() {
    let w = W(Some(V::List(vec![V::Str("a".into()), V::Str("b".into())])));
    match run("they", &HashMap::new(), &w).unwrap() { V::List(l) => assert!(l.len() == 2 && matches!(&l[0], V::Ent(e) if e == "a") && matches!(&l[1], V::Ent(e) if e == "b")), _ => panic!("a list") }
}

#[test]
fn a_variable_of_that_name_wins() {
    let w = W(Some(V::Str("membrane::goal:x".into())));
    let env = HashMap::from([("it".to_string(), V::Int(5))]);
    assert!(matches!(run("it", &env, &w).unwrap(), V::Int(5)));
}

#[test]
fn an_unbound_pronoun_fails_naming_it() {
    let e = run("it.name", &HashMap::new(), &W(None)).unwrap_err();
    assert!(e.contains("\"it\"") && e.contains("not bound"), "{e}");
    let e = run("them", &HashMap::new(), &W(Some(V::None))).unwrap_err();
    assert!(e.contains("\"them\""), "{e}");
}

#[test]
fn any_other_unknown_name_stays_unbound() {
    let e = run("banana", &HashMap::new(), &W(Some(V::Str("e".into())))).unwrap_err();
    assert!(e.contains("unbound name"), "{e}");
}
