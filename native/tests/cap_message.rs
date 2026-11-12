//! The language has no local variables: a named part of an expression is a getter. An expression over the caps says so.
use membrane_vm::expr::parse;

#[test]
fn a_too_long_expression_says_to_name_the_part_as_a_getter() {
    let long = (0..40).map(|i| format!("a{i}")).collect::<Vec<_>>().join(" + ");
    let e = parse(&long).unwrap_err().to_string();
    assert!(e.contains("at most 64 nodes"), "{e}");
    assert!(e.contains("getter"), "{e}");
}

#[test]
fn a_too_wide_expression_says_the_same() {
    let wide = (0..60).map(|i| format!("this.name{i}")).collect::<Vec<_>>().join(" and ");
    let e = parse(&wide).unwrap_err().to_string();
    assert!(e.contains("getter"), "{e}");
}
