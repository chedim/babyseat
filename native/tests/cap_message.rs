//! The caps are gone (operator, 2026-11-12: scoped blocks replace "name a part as a getter"; an evaluation has a step budget).
//! ch.py's 64-node / 400-character caps are intentionally NOT kept in parity.
use membrane_vm::expr::{self, parse};

#[test]
fn a_long_expression_parses() {
    let long = (0..40).map(|i| format!("a{i}")).collect::<Vec<_>>().join(" + ");
    assert!(parse(&long).is_ok());
}

#[test]
fn a_wide_expression_parses() {
    let wide = (0..60).map(|i| format!("this.name{i}")).collect::<Vec<_>>().join(" and ");
    assert!(parse(&wide).is_ok());
}

#[test]
fn no_message_tells_you_to_name_a_part_as_a_getter() {
    let long = (0..900).map(|i| format!("a{i}")).collect::<Vec<_>>().join(" + ");
    if let Err(e) = parse(&long) { assert!(!e.contains("name a part"), "{e}"); }
}

#[test]
fn the_budget_message_names_the_steps() {
    expr::set_eval_budget(Some(10));
    struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    let n = parse(&(0..40).map(|i| i.to_string()).collect::<Vec<_>>().join(" + ")).unwrap();
    let e = expr::eval(&n, &Default::default(), &W).unwrap_err();
    expr::set_eval_budget(None);
    assert_eq!(e, "budget exceeded (10 steps)");
}
