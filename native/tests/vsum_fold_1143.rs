//! Two builtins the language could not say (#1143, operator 2026-11-15):
//! `vsum(vectors, weights?)`, the (weighted) sum of a LIST of vectors (`+` adds two; nothing added up a list), and
//! `fold(init, [step for x in xs if keep])`, a comprehension that carries a value across the list: `acc` is `init`, then each step's value
//! (a greedy pass keeps state, e.g. "keep a hit only if it is not a near duplicate of a hit ALREADY KEPT").
use membrane_vm::expr;
use serde_json::{json, Value as J};

fn ev(src: &str) -> Result<J, String> { membrane_vm::evaluate_json(src, "{}").map(|s| serde_json::from_str(&s).unwrap()) }
fn ev_env(src: &str, env: J) -> Result<J, String> { membrane_vm::evaluate_json(src, &env.to_string()).map(|s| serde_json::from_str(&s).unwrap()) }

// ---- vsum ----------------------------------------------------------------------------------------------------------

#[test]
fn vsum_adds_up_a_list_of_vectors() {
    assert_eq!(ev("vsum([[1, 2], [3, 4], [5, 6]])").unwrap(), json!([9.0, 12.0]));
    assert_eq!(ev("vsum([[1, 2]])").unwrap(), json!([1.0, 2.0]), "one vector is itself");
}

#[test]
fn vsum_weighs_each_vector_by_its_weight() {
    assert_eq!(ev("vsum([[1, 0], [0, 1]], [2, 0.5])").unwrap(), json!([2.0, 0.5]));
    assert_eq!(ev("vsum([[1, 1], [1, 1]], [1, -1])").unwrap(), json!([0.0, 0.0]));
}

#[test]
fn vsum_is_f32_math_in_list_order() {
    // 0.1 as an f32 is 0.100000001490116...; the result is that f32, read back as a float
    assert_eq!(ev("vsum([[0.1]])").unwrap(), json!([0.1f32 as f64]));
    // in list order: (1e8 + 1) - 1e8 in f32 is 0 (1e8 + 1 rounds to 1e8), not 1
    assert_eq!(ev("vsum([[100000000.0], [1.0], [-100000000.0]])").unwrap(), json!([0.0]));
}

#[test]
fn vsum_says_why_on_a_wrong_shape() {
    let e = ev("vsum([[1, 2], [1]])").unwrap_err();
    assert!(e.contains("vsum") && e.contains("dimension"), "{e}");
    let e = ev("vsum([[1, 2], [3, 4]], [1])").unwrap_err();
    assert!(e.contains("vsum") && e.contains("weight"), "{e}");
    let e = ev("vsum([[1, 'a']])").unwrap_err();
    assert!(e.contains("vsum"), "{e}");
    let e = ev("vsum([[1, 2]], ['x'])").unwrap_err();
    assert!(e.contains("vsum") && e.contains("weight"), "{e}");
    let e = ev("vsum([1, 2])").unwrap_err();
    assert!(e.contains("vsum") && e.contains("list of vectors"), "{e}");
    let e = ev("vsum([])").unwrap_err();
    assert!(e.contains("vsum") && e.contains("no vector"), "{e}");
    assert!(ev("vsum()").unwrap_err().contains("vsum"));
}

#[test]
fn vsum_is_one_step_however_many_vectors() {
    let vs: Vec<J> = (0..2000).map(|i| json!([i, 1])).collect();
    let n = expr::parse("vsum(vs)").unwrap();
    let env = [("vs".to_string(), expr::from_json(&J::Array(vs)))].into_iter().collect();
    struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    expr::set_eval_budget(Some(5));
    let r = expr::eval(&n, &env, &W);
    expr::set_eval_budget(None);
    assert!(r.is_ok(), "{r:?}");
}

// ---- fold ----------------------------------------------------------------------------------------------------------

#[test]
fn fold_carries_acc_across_the_list() {
    assert_eq!(ev("fold(0, [acc + x for x in [1, 2, 3]])").unwrap(), json!(6));
    assert_eq!(ev("fold('', [acc + x for x in ['a', 'b']])").unwrap(), json!("ab"));
    assert_eq!(ev("fold(7, [acc + x for x in []])").unwrap(), json!(7), "an empty list is the start value");
    assert_eq!(ev("fold(7, [acc + x for x in None])").unwrap(), json!(7), "a null list is empty, like a comprehension's");
}

#[test]
fn fold_skips_what_its_if_refuses_and_the_if_sees_acc() {
    assert_eq!(ev("fold(0, [acc + x for x in [1, 2, 3, 4] if x > 2])").unwrap(), json!(7));
    assert_eq!(ev("fold([], [concat(acc, [x]) for x in [1, 2, 3, 2, 1] if not (x in acc)])").unwrap(), json!([1, 2, 3]));
}

#[test]
fn fold_is_greedy_a_chain_keeps_its_far_end() {
    // keep x unless it is within 1 of a KEPT one: 1 kept, 2 dropped (near 1), 3 kept (near 2, but 2 was not kept)
    assert_eq!(ev("fold([], [concat(acc, [x]) for x in [1, 2, 3] if len([k for k in acc if abs(k - x) <= 1]) == 0])").unwrap(), json!([1, 3]));
}

#[test]
fn fold_sees_the_locals_and_names_around_it() {
    assert_eq!(ev_env("fold(base, [acc * x for x in xs])", json!({"base": 2, "xs": [3, 4]})).unwrap(), json!(24));
}

#[test]
fn acc_is_bound_only_inside_a_fold_and_hides_a_name_of_its_own_there() {
    assert!(ev("acc + 1").is_err());
    assert!(expr::RESERVED.contains(&"fold") && expr::RESERVED.contains(&"vsum"));
    // `acc` is NOT reserved (a shipped pack has a local of that name): inside a step the fold's own `acc` is meant
    assert_eq!(ev_env("[acc, fold(0, [acc + x for x in [1, 2]])]", json!({"acc": "outer"})).unwrap(), json!(["outer", 3]));
    assert!(ev("fold(0, [acc for acc in [1]])").unwrap_err().contains("acc"), "the loop's item is not named acc");
}

#[test]
fn fold_takes_a_start_and_a_comprehension() {
    let e = ev("fold(0, [1, 2])").unwrap_err();
    assert!(e.contains("fold(init, [step for x in xs])"), "{e}");
    assert!(ev("fold([acc for x in [1]])").unwrap_err().contains("fold(init, [step for x in xs])"));
}

#[test]
fn an_error_in_a_step_is_the_answer() {
    // a failed step stops the fold: its Error is the value (as an Error passes through every operation)
    let n = expr::parse("fold(0, [acc + Error('bad step') for x in [1, 2]])").unwrap();
    struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    let r = expr::eval(&n, &Default::default(), &W).unwrap();
    assert!(matches!(&r, expr::V::Error(m) if m == "bad step"), "{r:?}");
}

#[test]
fn the_steps_of_a_fold_count_against_the_budget() {
    let n = expr::parse("fold(0, [acc + x for x in xs])").unwrap();
    let xs: Vec<J> = (0..1000).map(|i| json!(i)).collect();
    let env = [("xs".to_string(), expr::from_json(&J::Array(xs)))].into_iter().collect();
    struct W; impl expr::World for W { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    expr::set_eval_budget(Some(500));
    let r = expr::eval(&n, &env, &W);
    expr::set_eval_budget(None);
    assert_eq!(r.unwrap_err(), "budget exceeded (500 steps)");
    let r = expr::eval(&n, &env, &W);
    assert_eq!(expr::to_json(&r.unwrap()).unwrap(), json!(499500));
}

#[test]
fn the_inferencer_types_vsum_and_checks_the_arity_of_both() {
    use membrane_vm::infer::infer_src;
    assert_eq!(infer_src("vsum(v)", &[("v", "List(List(Float))")]).unwrap().0, "List(Float)");
    assert!(!infer_src("vsum(v, w, w)", &[("v", "List(List(Float))"), ("w", "List(Float)")]).unwrap().1.is_empty(), "three arguments are reported");
    assert!(!infer_src("fold(0)", &[]).unwrap().1.is_empty(), "one argument is reported");
    assert!(infer_src("fold(0, [acc + x for x in [1, 2]])", &[]).unwrap().1.is_empty());
}
