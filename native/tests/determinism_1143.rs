//! Evaluation limits are DETERMINISTIC (#1143, operator 2026-11-13): the wall clock never changes a VALUE. A getter's result depends
//! only on its inputs and the STEP budget. Time stays measured: `stats` counts a `slow` evaluation (self time over the former
//! allowance, the dial `expr::eval_time_ms`, default 10 ms) and nothing else changes. The clock is injected, so nothing sleeps.
use membrane_vm::expr;
use membrane_vm::fields::Fields;
use membrane_vm::stats;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::cell::Cell;

const MS: u64 = 1_000_000;
thread_local! { static NOW: Cell<u64> = const { Cell::new(0) }; static STEP: Cell<u64> = const { Cell::new(0) }; }
/// every read of the clock moves it STEP ms on
fn fake() -> u64 { NOW.with(|n| { let v = n.get(); n.set(v + STEP.with(|s| s.get())); v }) }
fn use_clock(step_ms: u64) { NOW.with(|n| n.set(0)); STEP.with(|s| s.set(step_ms * MS)); expr::set_clock(Some(fake)); }
fn unuse_clock() { expr::set_clock(None); }

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn long(cls: &str) -> String {
    let items = (0..600).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
    format!("PACK d{cls} 1\n{cls}.slow getter: this.w + len([x for x in [{items}]])\n{cls}.dep getter: str(this.slow)\n{cls}.fine getter: this.w + 1\n\
             {cls}.w on-setter s:\n  WHEN this.w > 1:\n    this.out = 1\n")
}
fn world(cls: &str) -> (Core, Fields, i64) {
    let mut f = Fields::goalgraph();
    f.set_pack(&format!("d{cls}"), &long(cls)).unwrap();
    let mut c = Core::new();
    c.load(format!("{cls}:a"), cls.into(), json!({"w": 1}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f, seq)
}
fn val(c: &Core, cls: &str, p: &str) -> J { c.docs[&format!("{cls}:a")].props.get(p).cloned().unwrap_or(J::Null) }
fn poke(c: &mut Core, f: &mut Fields, seq: &mut i64, cls: &str, v: i64) {
    *seq += 1;
    let w = Write { eid: format!("{cls}:a"), prop: "w".into(), new: json!(v), old: J::Null, writer: "person".into(), chain: json!({}), seq: *seq };
    c.hear(w.clone(), None, 2.0);
    f.react(c, &H, w, 2.0, seq);
}
/// the values of the world after a rebuild and a poke, under a clock and a former allowance
fn run(cls: &str, step_ms: u64, allowance_ms: u64) -> (Vec<J>, Vec<String>) {
    use_clock(step_ms);
    expr::set_eval_time_ms(Some(allowance_ms));
    let (mut c, mut f, mut seq) = world(cls);
    poke(&mut c, &mut f, &mut seq, cls, 5);
    expr::set_eval_time_ms(None);
    unuse_clock();
    let vals = ["slow", "dep", "fine", "out", "refused"].iter().map(|p| val(&c, cls, p)).collect();
    let trips = c.docs.keys().filter(|k| k.starts_with("Trip:")).cloned().collect();
    (vals, trips)
}

#[test]
fn the_same_getter_gives_the_same_values_under_a_tiny_and_a_huge_allowance_and_any_clock() {
    let (tiny, trips) = run("DetA", 12, 1);
    let (huge, _) = run("DetB", 0, 1_000_000);
    let (slow_clock, _) = run("DetC", 700, 10);
    assert_eq!(tiny, huge, "the clock changed a value");
    assert_eq!(tiny, slow_clock);
    assert_eq!(tiny[0], json!(605), "slow evaluated: {tiny:?}");
    assert_eq!(tiny[3], json!(1), "the setter wrote");
    assert!(trips.is_empty(), "no Trip entity any more: {trips:?}");
}

#[test]
fn a_getter_over_the_former_allowance_is_flagged_slow_not_killed() {
    stats::set_on(true);
    let slow_of = |cls: &str| stats::global_lifetime().into_iter().find(|r| r.cls == cls && r.prop == "fine").map_or(0, |r| r.slow);
    run("DetD", 12, 10);   // each evaluation reads the clock at start and end: 12 ms self time, over 10
    assert!(slow_of("DetD") >= 1, "flagged slow");
    run("DetE", 12, 1_000_000);
    assert_eq!(slow_of("DetE"), 0, "under the threshold: not slow");
}

#[test]
fn a_runaway_loop_still_ends_on_the_step_budget() {
    expr::set_eval_budget(Some(100));
    let (c, f, _) = world("DetF");
    expr::set_eval_budget(None);
    assert_eq!(val(&c, "DetF", "slow"), J::Null);
    assert_eq!(f.why_chain(&c, "DetF:a", "slow")[0]["reason"], json!("budget exceeded (100 steps)"));
}
