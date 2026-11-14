//! The per-evaluation time allowance (operator-approved 2026-11-12): a getter or setter evaluation whose SELF time passes the
//! allowance (a dial, default 10 ms) is a reasoned null for that node only. The clock is injected, so nothing sleeps.
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
fn the_default_time_allowance_is_ten_ms() {
    assert_eq!(expr::DEFAULT_EVAL_TIME_MS, 10);
    assert_eq!(expr::eval_time_ms(), 10);
    assert_eq!(expr::TIME_CHECK_EVERY, 256);
}

#[test]
fn a_runaway_evaluation_trips_at_the_backstop_for_that_node_only() {
    use_clock(6);   // start 0, the backstop at step 256 reads 6, at step 512 reads 12: over 10
    let (c, f, _) = world(&long());
    unuse_clock();
    assert_eq!(val(&c, "slow"), J::Null);
    assert_eq!(val(&c, "fine"), json!(2), "the rest of the engine keeps running (two clock reads: 6 ms)");
    let chain = f.why_chain(&c, "Box:a", "slow");
    assert_eq!(chain[0]["reason"], json!("timeout 12 ms (allowance 10 ms)"), "{chain:?}");
}

#[test]
fn the_end_of_an_evaluation_checks_the_clock_too() {
    use_clock(12);   // a short getter: start 0, end 12
    let (c, f, _) = world("PACK t 1\nBox.fine getter: this.w + 1\n");
    unuse_clock();
    assert_eq!(val(&c, "fine"), J::Null);
    assert_eq!(f.why_chain(&c, "Box:a", "fine")[0]["reason"], json!("timeout 12 ms (allowance 10 ms)"));
}

#[test]
fn a_setter_evaluation_trips_the_same_way() {
    use_clock(12);
    let (mut c, mut f, mut s) = world("PACK t 1\nBox.w on-setter s:\n  WHEN this.w > 1:\n    this.out = 1\n");
    poke(&mut c, &mut f, &mut s, 5);
    unuse_clock();
    assert_eq!(val(&c, "out"), J::Null, "the setter wrote nothing");
    let r = val(&c, "refused").as_str().unwrap_or("").to_string();
    assert!(r.contains("timeout 12 ms (allowance 10 ms)"), "{r}");
}

#[test]
fn time_spent_waiting_in_a_host_call_is_not_self_time() {
    use_clock(3);   // start 0, wait start 3, +100 inside, wait end 106, end 109: 109 elapsed - 103 waited = 6
    let (mut c, mut f, mut s) = world("PACK t 1\nBox.h getter: waits(this.w)\n");
    poke(&mut c, &mut f, &mut s, 2);
    unuse_clock();
    assert_eq!(val(&c, "h"), json!(1), "no trip: it was waiting, not computing");
}

#[test]
fn the_allowance_is_a_dial() {
    use_clock(12);
    expr::set_eval_time_ms(Some(20));
    let (c, _, _) = world("PACK t 1\nBox.fine getter: this.w + 1\n");
    expr::set_eval_time_ms(None);
    unuse_clock();
    assert_eq!(val(&c, "fine"), json!(2));
}

#[test]
fn a_fast_evaluation_on_the_real_clock_is_untouched() {
    let (c, _, _) = world("PACK t 1\nBox.fine getter: this.w + 1\n");
    assert_eq!(val(&c, "fine"), json!(2));
    let _ = parse_full;
}
