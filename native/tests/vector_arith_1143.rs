//! Lists are vectors (#1143): `+ - * /` on a list is element-wise vector arithmetic, never list repetition or joining.
//! Pins what Throwy found odd: `[1] * 200000` is the vector `[200000.0]` (a length-1 operand broadcasts), so
//! `[x + 1 for x in [1] * N]` is `[N + 1.0]`. Elements come out as floats.
use serde_json::{json, Value as J};

fn ev(src: &str) -> Result<J, String> {
    membrane_vm::evaluate_json(src, "{}").map(|s| serde_json::from_str(&s).unwrap())
}

#[test]
fn list_times_number_scales_the_vector_and_does_not_repeat_it() {
    assert_eq!(ev("[1] * 5").unwrap(), json!([5.0]));
    assert_eq!(ev("[1, 2] * 3").unwrap(), json!([3.0, 6.0]));
    assert_eq!(ev("[x + 1 for x in [1] * 200000]").unwrap(), json!([200001.0]));
}

#[test]
fn number_times_list_is_the_same_scaling() {
    assert_eq!(ev("3 * [1, 2]").unwrap(), json!([3.0, 6.0]));
    assert_eq!(ev("5 * [1]").unwrap(), json!([5.0]));
}

#[test]
fn list_plus_list_adds_element_wise_and_does_not_join() {
    assert_eq!(ev("[1, 2] + [3, 4]").unwrap(), json!([4.0, 6.0]));
    assert_eq!(ev("[1] + [3, 4]").unwrap(), json!([4.0, 5.0]), "a length-1 operand broadcasts");
    assert!(ev("[1, 2] + [1, 2, 3]").is_err(), "unequal lengths do not broadcast");
}

#[test]
fn concat_is_the_way_to_join_lists() {
    assert_eq!(ev("concat([1, 2], [3])").unwrap(), json!([1, 2, 3]));
}
