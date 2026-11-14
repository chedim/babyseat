//! `Error` in the static inference (#1143, docs/c1f-error.md section (d)): Error is a KIND (not a null flag, never stripped by `or`); an operation
//! on a value that may be an Error may be an Error; `x is Error` and `why(x)` read it without; the failure sites type as `T | Error`.
use membrane_vm::infer::{infer_packs, infer_src};

fn ty(src: &str, vars: &[(&str, &str)]) -> String { infer_src(src, vars).unwrap().0 }
fn found(src: &str, vars: &[(&str, &str)]) -> Vec<String> { infer_src(src, vars).unwrap().1 }

#[test]
fn an_error_constructor_is_the_error_kind_and_the_type_text_reads_back() {
    assert_eq!(ty("Error('x')", &[]), "Error");
    assert_eq!(ty("x", &[("x", "Int? | Error")]), "(Int|Error)?");
    assert_eq!(ty("x", &[("x", "Error")]), "Error");
    assert_eq!(ty("1 if c else Error('x')", &[("c", "Bool")]), "Int|Error");
}

#[test]
fn a_cast_that_may_fail_is_its_type_or_error() {
    assert_eq!(ty("Int(x)", &[("x", "Text")]), "Int|Error");
    assert_eq!(ty("Int(x)", &[("x", "Int")]), "Int");
    assert_eq!(ty("Int(x)", &[("x", "Int?")]), "Int?");
    assert_eq!(ty("Int(x)", &[("x", "Text?")]), "(Int|Error)?");
    assert_eq!(ty("Int(x)", &[("x", "Any")]), "(Int|Error)?");
}

#[test]
fn an_operation_on_a_value_that_may_be_an_error_may_be_an_error() {
    let e = [("x", "Int | Error"), ("l", "List(Int) | Error"), ("t", "Text | Error"), ("c", "Bool | Error")];
    assert_eq!(ty("x + 1", &e), "Int|Error");
    assert_eq!(ty("-x", &e), "Int|Error");
    assert_eq!(ty("l[0]", &e), "(Int|Error)?");
    assert_eq!(ty("len(t)", &e), "Int|Error");
    assert_eq!(ty("lower(t)", &e), "Text|Error");
    assert_eq!(ty("1 if c else 2", &e), "Int|Error");
    assert_eq!(ty("x == 1", &e), "Bool|Error");
    assert_eq!(ty("not c", &e), "Bool|Error");
    assert_eq!(ty("[y for y in l]", &e), "List(Int)|Error");
    assert!(found("x + 1", &e).is_empty(), "an Error operand is no finding: {:?}", found("x + 1", &e));
}

#[test]
fn is_and_why_and_a_null_test_read_an_error_without_propagating() {
    let e = [("x", "Int | Error")];
    assert_eq!(ty("x is Error", &e), "Bool");
    assert_eq!(ty("x is not Error", &e), "Bool");
    assert_eq!(ty("x == None", &e), "Bool");
    assert_eq!(ty("why(x)", &e), "Text?");
}

#[test]
fn or_is_a_default_that_never_hides_an_error() {
    assert_eq!(ty("x or 0", &[("x", "Int? | Error")]), "Int|Error");
    assert_eq!(ty("x or 0", &[("x", "Int?")]), "Int");
}

#[test]
fn a_getter_that_can_fail_types_as_t_or_error() {
    let r = infer_packs(&["PACK p 1\nBox.o getter:\n  WHEN this.bad: Error('it failed; fix it')\n  ELSE: 'fine'\nBox.n getter: Int(this.raw)\nBox.plus getter: this.o + '!'\nBox.chk getter: this.o is Error\n"]).unwrap();
    let g = &r[0].1.getters;
    assert_eq!(g["Box.o"], "Text|Error");
    assert_eq!(g["Box.plus"], "Text|Error");
    assert_eq!(g["Box.chk"], "Bool");
    assert_eq!(g["Box.n"], "(Int|Error)?");
}
