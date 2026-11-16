//! Time decides no value (operator 2026-11-13; before it, a getter or setter whose SELF time passed a 10 ms allowance was a reasoned null).
//! The former allowance is now only the `slow` threshold of `stats` (a dial, default 10 ms). The clock is injected, so nothing sleeps.
use membrane_vm::expr;
use membrane_vm::fields::Fields;
use membrane_vm::syntax::parse_full;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::cell::Cell;

const MS: u64 = 1_000_000;
thread_local! { static NOW: Cell<u64> = const { Cell::new(0) }; static STEP: Cell<u64> = const { Cell::new(0) }; }
/// every read of the clock moves it STEP ms on: an evaluation's cost is its number of clock reads
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
    /// a host function that waits 100 ms (on the fake clock)
    fn call(&self, f: &str, _: &str) -> Option<Result<String, String>> {
        if f == "waits" { NOW.with(|n| n.set(n.get() + 100 * MS)); Some(Ok("1".into())) } else { None }
    }
}

fn world(pack: &str) -> (Core, Fields, i64) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), json!({"w": 1}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f, seq)
}
fn val(c: &Core, p: &str) -> J { c.docs["Box:a"].props.get(p).cloned().unwrap_or(J::Null) }
fn poke(c: &mut Core, f: &mut Fields, seq: &mut i64, v: i64) {
    *seq += 1;
    let w = Write { eid: "Box:a".into(), prop: "w".into(), new: json!(v), old: J::Null, writer: "person".into(), chain: json!({}), seq: *seq };
    c.hear(w.clone(), None, 2.0);
    f.react(c, &H, w, 2.0, seq);
}

fn long() -> String {
    let items = (0..600).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
    format!("PACK t 1\nBox.slow getter: len([x + this.w for x in [{items}]])\nBox.fine getter: this.w + 1\n")
}

#[test]
fn the_default_step_budget_is_one_hundred_thousand() {
    assert_eq!(expr::DEFAULT_EVAL_BUDGET, 100_000);
    assert_eq!(expr::eval_budget(), 100_000);
}

#[test]
fn the_former_allowance_is_still_ten_ms_as_the_slow_threshold() {
    assert_eq!(expr::DEFAULT_EVAL_TIME_MS, 10);
    assert_eq!(expr::eval_time_ms(), 10);
}

#[test]
fn a_runaway_evaluation_is_not_killed_by_time_even_over_many_clock_reads() {
    use_clock(6);   // a clock that jumps 6 ms at every read: far over 10 ms by the end of a 600-element comprehension
    let (c, f, _) = world(&long());
    unuse_clock();
    assert_eq!(val(&c, "slow"), json!(600));
    assert_eq!(val(&c, "fine"), json!(2));
    assert!(f.why_chain(&c, "Box:a", "slow").is_empty(), "not a null: nothing to explain");
}

#[test]
fn a_short_getter_over_the_former_allowance_keeps_its_value() {
    use_clock(12);
    let (c, _, _) = world("PACK t 1\nBox.fine getter: this.w + 1\n");
    unuse_clock();
    assert_eq!(val(&c, "fine"), json!(2));
}

#[test]
fn a_setter_evaluation_over_the_former_allowance_writes() {
    use_clock(12);
    let (mut c, mut f, mut s) = world("PACK t 1\nBox.w on-setter s:\n  WHEN this.w > 1:\n    this.out = 1\n");
    poke(&mut c, &mut f, &mut s, 5);
    unuse_clock();
    assert_eq!(val(&c, "out"), json!(1), "the setter wrote");
    assert_eq!(val(&c, "refused"), J::Null);
}

#[test]
fn time_spent_waiting_in_a_host_call_changes_nothing() {
    use_clock(3);
    let (mut c, mut f, mut s) = world("PACK t 1\nBox.h getter: waits(this.w)\n");
    poke(&mut c, &mut f, &mut s, 2);
    unuse_clock();
    assert_eq!(val(&c, "h"), json!(1));
}

#[test]
fn a_fast_evaluation_on_the_real_clock_is_untouched() {
    let (c, _, _) = world("PACK t 1\nBox.fine getter: this.w + 1\n");
    assert_eq!(val(&c, "fine"), json!(2));
    let _ = parse_full;
}
