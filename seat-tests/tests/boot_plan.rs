//! The boot plan, native: order and policy come from boot.json / boot.c1f; the runner only walks them.
use std::sync::Mutex;

use membrane_vm::fields::Fields;
use membrane_vm::plan::{self, Engine, Outcome};
use membrane_vm::vm::{Core, Host};

struct H;
impl Host for H {
    fn resolve(&self, _: &str) -> Option<String> { None }
    fn resolve_exact(&self, _: &str) -> Option<String> { None }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

const PACK: &str = include_str!("../../membrane/packs/boot.c1f");
const SHIPPED: &str = include_str!("../../membrane/packs/boot.json");

fn engine(steps_json: &str) -> (Mutex<Engine>, Vec<plan::Step>) {
    let steps = plan::parse(steps_json).unwrap();
    let mut e: Engine = (Core::new(), Fields::goalgraph(), 0);
    plan::load(&mut e, &H, PACK, &steps).unwrap();
    (Mutex::new(e), steps)
}

#[test]
fn steps_run_in_the_order_the_data_gives() {
    let (e, steps) = engine(r#"[{"name":"c"},{"name":"a","blocks":["b"]},{"name":"b","blocks":["c"]}]"#);
    let mut ran = vec![];
    plan::run(&e, &H, &steps, false, &mut |n| { ran.push(n.to_string()); Outcome::Done }).unwrap();
    assert_eq!(ran, ["a", "b", "c"]);                       // the edges decide, not the list's order
    assert_eq!(plan::state(&e.lock().unwrap().0, "c"), "done");
}

#[test]
fn a_failed_optional_step_is_recorded_and_the_boot_goes_on() {
    let (e, steps) = engine(r#"[{"name":"a","blocks":["b"]},{"name":"b"}]"#);
    let mut ran = vec![];
    plan::run(&e, &H, &steps, false, &mut |n| { ran.push(n.to_string()); if n == "a" { Outcome::Failed("no embedder".into()) } else { Outcome::Done } }).unwrap();
    let g = e.lock().unwrap();
    assert_eq!(ran, ["a", "b"]);
    assert_eq!((plan::state(&g.0, "a").as_str(), plan::error(&g.0, "a").as_str()), ("failed", "no embedder"));
}

#[test]
fn a_failed_essential_step_stops_the_boot() {
    let (e, steps) = engine(r#"[{"name":"a","essential":true,"blocks":["b"]},{"name":"b"}]"#);
    let mut ran = vec![];
    let r = plan::run(&e, &H, &steps, false, &mut |n| { ran.push(n.to_string()); Outcome::Failed("no store".into()) });
    assert!(r.unwrap_err().contains("no store"));
    assert_eq!(ran, ["a"]);
}

#[test]
fn a_fatal_outcome_stops_the_boot_whatever_the_step() {
    let (e, steps) = engine(r#"[{"name":"a","blocks":["b"]},{"name":"b"}]"#);
    let r = plan::run(&e, &H, &steps, false, &mut |_| Outcome::Fatal("exit".into()));
    assert!(r.is_err());
}

#[test]
fn background_steps_wait_for_their_own_run_and_fail_alone() {
    let (e, steps) = engine(r#"[{"name":"serve"},{"name":"warm","background":true},{"name":"fore","background":true}]"#);
    let mut ran = vec![];
    plan::run(&e, &H, &steps, false, &mut |n| { ran.push(n.to_string()); Outcome::Done }).unwrap();
    assert_eq!(ran, ["serve"]);
    assert_eq!(plan::state(&e.lock().unwrap().0, "warm"), "pending");
    plan::run(&e, &H, &steps, true, &mut |n| { ran.push(n.to_string()); if n == "warm" { Outcome::Failed("x".into()) } else { Outcome::Skipped } }).unwrap();
    let g = e.lock().unwrap();
    assert_eq!(ran, ["serve", "warm", "fore"]);
    assert_eq!((plan::state(&g.0, "warm").as_str(), plan::state(&g.0, "fore").as_str()), ("failed", "skipped"));
}

#[test]
fn the_shipped_plan_loads_and_runs_through_every_step() {
    let (e, steps) = engine(SHIPPED);
    let mut ran = vec![];
    plan::run(&e, &H, &steps, false, &mut |n| { ran.push(n.to_string()); Outcome::Done }).unwrap();
    let want: Vec<&str> = steps.iter().filter(|s| !s.background).map(|s| s.name.as_str()).collect();
    assert_eq!(ran, want);
    assert_eq!(ran.first().map(|s| s.as_str()), Some("setup"));
    assert_eq!(ran.last().map(|s| s.as_str()), Some("serve"));
}
