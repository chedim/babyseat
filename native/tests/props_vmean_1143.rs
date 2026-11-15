//! Two builtins that let a getter's work over a long list cost one step, not steps per item (#1143, operator 2026-11-15: the field's recall must scale):
//! `props(ids, 'name')` reads one property of every entity of a list (`props(ids, at, 'name')` takes the `at[i]`-th element of each value: a list index or a dict key),
//! `vmean(ids)` is the mean of the unit vectors (`v`) of a list of entities, read straight from the store; and `group_by(rows, key, sums)` adds, to each group,
//! the sum of each named column (no per-group comprehension to total it).
use membrane_vm::expr::{self, V};
use serde_json::{json, Value as J};
use std::collections::HashMap;

/// a field of a few entities: `v` vectors, a list `ids`, a dict `d`, a plain `n`
struct W(HashMap<(String, String), V>);
impl expr::World for W {
    fn attr(&self, eid: &str, attr: &str) -> V { self.0.get(&(eid.to_string(), attr.to_string())).cloned().unwrap_or_else(|| V::Nil(format!("absent: {eid}.{attr}"))) }
}
fn world() -> W {
    let mut m = HashMap::new();
    let mut put = |e: &str, p: &str, j: J| { m.insert((e.to_string(), p.to_string()), expr::from_json(&j)); };
    put("A:1", "v", json!([3.0, 4.0]));
    put("A:2", "v", json!([0.0, 2.0]));
    put("A:3", "v", json!([1.0, 1.0, 1.0]));
    put("A:4", "v", json!("not a vector"));
    put("A:5", "v", json!([0.1, 0.7]));
    put("A:1", "n", json!(10));
    put("A:2", "n", json!(20));
    put("A:1", "ids", json!(["x0", "x1", "x2"]));
    put("A:2", "ids", json!(["y0"]));
    put("A:1", "d", json!({"k": "one"}));
    W(m)
}
fn run(src: &str) -> Result<V, String> {
    let n = expr::parse(src).map_err(|e| e.to_string())?;
    expr::eval(&n, &HashMap::new(), &world())
}
fn ok(src: &str) -> J { expr::to_json(&run(src).unwrap_or_else(|e| panic!("{src}: {e}"))).unwrap() }
fn err(src: &str) -> String { match run(src) { Err(e) => e, Ok(V::Error(e)) => e, Ok(v) => panic!("{src}: not an error: {v:?}") } }
/// `src` evaluated under a budget of `b` steps
fn within(src: &str, env: Vec<(&str, J)>, b: u64) -> Result<V, String> {
    let n = expr::parse(src).unwrap();
    let env: HashMap<String, V> = env.into_iter().map(|(k, v)| (k.to_string(), expr::from_json(&v))).collect();
    expr::set_eval_budget(Some(b));
    let r = expr::eval(&n, &env, &world());
    expr::set_eval_budget(None);
    r
}

// ---- props ---------------------------------------------------------------------------------------------------------

#[test]
fn props_reads_one_property_of_every_entity() {
    assert_eq!(ok("props(['A:1', 'A:2'], 'n')"), json!([10, 20]));
    assert_eq!(ok("props([A:1, A:2], 'n')"), json!([10, 20]), "entities or their ids");
    assert_eq!(ok("props([], 'n')"), json!([]));
}

#[test]
fn props_keeps_a_null_where_an_entity_has_no_such_property_or_the_item_is_null() {
    let v = run("props(['A:1', 'A:9', None], 'n')").unwrap();
    let V::List(l) = v else { panic!("{v:?}") };
    assert!(matches!(&l[0], V::Int(10)));
    assert!(matches!(&l[1], V::Nil(r) if r.contains("absent")), "the read's own reason: {:?}", l[1]);
    assert!(matches!(&l[2], V::None | V::Nil(_)));
    assert_eq!(ok("props([{'n': 5}], 'n')"), json!([5]), "a dict item reads its key, as prop does");
}

#[test]
fn props_with_at_takes_one_element_of_each_value() {
    assert_eq!(ok("props(['A:1', 'A:2', 'A:1'], [2, 0, 7], 'ids')"), json!(["x2", "y0", null]), "a list by its index; past the end is a null");
    assert_eq!(ok("props(['A:1'], ['k'], 'd')"), json!(["one"]), "a dict by its key");
    assert_eq!(ok("props(['A:9', None], [0, 0], 'ids')"), json!([null, null]), "a null value stays null");
    assert_eq!(ok("props(['A:1'], [None], 'ids')"), json!([null]), "a null index is a null");
}

#[test]
fn props_says_why_on_a_wrong_shape() {
    assert!(err("props('A:1', 'n')").contains("list"));
    assert!(err("props(['A:1'], 3)").contains("name"));
    assert!(err("props(['A:1', 'A:2'], [0], 'ids')").contains("one index per"));
    assert!(err("props([3], 'n')").contains("item 0"));
    assert!(err("props(['A:1'], '_hidden')").contains("_hidden"));
    assert!(err("props(['A:1'])").contains("props"));
    assert!(err("props(['A:1'], [[1]], 'ids')").contains("index"));
}

#[test]
fn props_is_one_step_however_many_ids() {
    let ids: Vec<J> = (0..3000).map(|i| json!(format!("A:{}", i % 3 + 1))).collect();
    let at: Vec<J> = (0..3000).map(|_| json!(0)).collect();
    let r = within("props(ids, at, 'ids')", vec![("ids", J::Array(ids)), ("at", J::Array(at))], 5);
    assert!(matches!(&r, Ok(V::List(l)) if l.len() == 3000), "{:?}", r.map(|_| ()));
}

// ---- vmean ---------------------------------------------------------------------------------------------------------

#[test]
fn vmean_is_the_mean_of_the_unit_vectors() {
    // unit([3, 4]) = [0.6, 0.8], unit([0, 2]) = [0, 1]: summed in f32, then halved
    let want: Vec<f64> = [(0.6f64 as f32 + 0.0f32), (0.8f64 as f32 + 1.0f32)].iter().map(|s| *s as f64 / 2.0).collect();
    assert_eq!(ok("vmean(['A:1', 'A:2'])"), json!(want));
    assert_eq!(ok("vmean([A:1])"), json!([0.6f64 as f32 as f64, 0.8f64 as f32 as f64]));
}

#[test]
fn vmean_gives_the_same_bits_as_the_c1f_it_replaces() {
    let a = ok("vmean(['A:1', 'A:2', 'A:5', 'A:1'])");
    let b = ok("vsum([unit(prop(e, 'v')) for e in ['A:1', 'A:2', 'A:5', 'A:1']]) / len(['A:1', 'A:2', 'A:5', 'A:1'])");
    assert_eq!(a, b);
}

#[test]
fn vmean_skips_the_ids_with_no_vector_and_the_nulls() {
    assert_eq!(ok("vmean(['A:9', 'A:1', None, 'A:8'])"), ok("vmean(['A:1'])"));
    let v = run("vmean(['A:9', None])").unwrap();
    assert!(matches!(&v, V::Nil(r) if r.contains("empty") && r.contains("2 ids")), "none has a vector: a null that says so: {v:?}");
    assert!(matches!(run("vmean([])").unwrap(), V::Nil(_)));
}

#[test]
fn vmean_says_why_on_a_wrong_shape() {
    assert!(err("vmean(['A:1', 'A:3'])").contains("dimension"));
    assert!(err("vmean(['A:1', 'A:4'])").contains("A:4"));
    assert!(err("vmean([3])").contains("item 0"));
    assert!(err("vmean('A:1')").contains("list"));
    assert!(err("vmean(['A:1'], 2)").contains("vmean"));
}

#[test]
fn vmean_is_one_step_however_many_ids() {
    let ids: Vec<J> = (0..3000).map(|i| json!(format!("A:{}", i % 2 + 1))).collect();
    let r = within("vmean(ids)", vec![("ids", J::Array(ids))], 5);
    assert!(matches!(&r, Ok(V::List(l)) if l.len() == 2), "{r:?}");
}

// ---- group_by with sums ----------------------------------------------------------------------------------------------

#[test]
fn group_by_sums_the_named_columns_of_each_group() {
    let rows = "[{'k': 'a', 'p': 0.1, 'd': 1}, {'k': 'b', 'p': 0.5, 'd': 0}, {'k': 'a', 'p': 0.2, 'd': 1}]";
    let g = ok(&format!("group_by({rows}, 'k', ['p', 'd'])"));
    assert_eq!(g[0]["key"], json!("a"));
    assert_eq!(g[0]["rows"].as_array().unwrap().len(), 2, "the rows stay");
    assert_eq!(g[0]["d"], json!(2), "whole numbers sum to a whole number");
    assert_eq!(g[1]["p"], json!(0.5));
    // the same float as the c1f total it replaces (in list order)
    assert_eq!(g[0]["p"], ok(&format!("dot(pluck([r for r in {rows} if r['k'] == 'a'], 'p'), [1, 1])")));
    assert_eq!(ok(&format!("group_by({rows}, 'k')")), ok(&format!("group_by({rows}, 'k', [])")), "no sums: the groups as before");
}

#[test]
fn group_by_sums_say_why_on_a_wrong_column() {
    assert!(err("group_by([{'k': 'a', 'p': 'x'}], 'k', ['p'])").contains("p"));
    assert!(err("group_by([{'k': 'a'}], 'k', ['p'])").contains("p"));
    assert!(err("group_by([{'k': 'a', 'rows': 1}], 'k', ['rows'])").contains("rows"));
    assert!(err("group_by([{'k': 'a'}], 'k', 'p')").contains("list"));
}

#[test]
fn group_by_with_sums_is_one_step_however_many_rows() {
    let rows: Vec<J> = (0..3000).map(|i| json!({"k": format!("k{}", i % 700), "p": 0.5, "d": 1})).collect();
    let r = within("group_by(rows, 'k', ['p', 'd'])", vec![("rows", J::Array(rows))], 6);
    assert!(matches!(&r, Ok(V::List(l)) if l.len() == 700), "{:?}", r.map(|_| ()));
}

// ---- the names and the inferencer ------------------------------------------------------------------------------------

#[test]
fn the_names_are_reserved_and_typed() {
    use membrane_vm::infer::infer_src;
    assert!(expr::RESERVED.contains(&"props") && expr::RESERVED.contains(&"vmean"));
    assert_eq!(infer_src("vmean(ids)", &[("ids", "List(Text)")]).unwrap().0, "List(Float)?");
    assert_eq!(infer_src("props(ids, 'n')", &[("ids", "List(Text)")]).unwrap().0, "List(Any)");
    assert!(!infer_src("props(ids)", &[("ids", "List(Text)")]).unwrap().1.is_empty(), "one argument is reported");
    assert!(!infer_src("vmean(ids, ids)", &[("ids", "List(Text)")]).unwrap().1.is_empty(), "two arguments are reported");
    assert!(infer_src("group_by(rows, 'k', ['p'])", &[("rows", "List(Dict)")]).unwrap().1.is_empty(), "sums are a third argument");
}
