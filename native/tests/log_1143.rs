//! `log(x)` (#1143, operator 2026-11-15): the natural log, the one builtin the move-log voter needs (its rarity is log(N / the events holding a thing)).
//! The f64 log rounded to f32, so two devices whose math libraries differ in the last bits of an f64 give the same answer; an Error with its reason on a
//! number that is not positive or on anything that is not a number; one step.
use membrane_vm::expr;
use serde_json::{json, Value as J};

fn ev(src: &str) -> Result<J, String> { membrane_vm::evaluate_json(src, "{}").map(|s| serde_json::from_str(&s).unwrap()) }

#[test]
fn log_is_the_natural_log_rounded_to_f32() {
    assert_eq!(ev("log(1)").unwrap(), json!(0.0));
    assert_eq!(ev("log(4000 / 3)").unwrap(), json!((4000.0f64 / 3.0).ln() as f32 as f64));
    assert_eq!(ev("log(2.0)").unwrap(), json!(2.0f64.ln() as f32 as f64));
    assert_eq!(ev("log(0.5)").unwrap(), json!(0.5f64.ln() as f32 as f64), "below one is negative");
}

#[test]
fn log_says_why_on_a_number_that_is_not_positive_or_on_a_non_number() {
    for (src, want) in [("log(0)", "positive"), ("log(-1)", "positive"), ("log(-0.5)", "positive"), ("log('e')", "number"), ("log([1])", "number"), ("log(True)", "number")] {
        let e = ev(src).unwrap_err();
        assert!(e.contains("log") && e.contains(want), "{src}: {e}");
    }
    assert!(ev("log()").unwrap_err().contains("log"));
    assert!(ev("log(1, 2)").unwrap_err().contains("log"));
}

#[test]
fn log_of_a_null_is_a_null_and_log_is_reserved() {
    assert_eq!(ev("log(None) == None").unwrap(), json!(true));
    assert!(expr::RESERVED.contains(&"log"));
}

#[test]
fn log_is_one_step() {
    let n = expr::parse("log(x)").unwrap();
    let env = [("x".to_string(), expr::V::Int(7))].into_iter().collect();
    struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    expr::set_eval_budget(Some(3));
    let r = expr::eval(&n, &env, &W);
    expr::set_eval_budget(None);
    assert!(r.is_ok(), "{r:?}");
}

#[test]
fn log_is_typed_a_float() {
    use membrane_vm::infer::infer_src;
    assert_eq!(infer_src("log(n)", &[("n", "Int")]).unwrap().0, "Float");
    assert!(!infer_src("log(t)", &[("t", "Text")]).unwrap().1.is_empty(), "a text is reported");
    assert!(!infer_src("log(n, n)", &[("n", "Int")]).unwrap().1.is_empty(), "two arguments are reported");
}
