//! Eval accounting (#1143): the VM counts, per (kind, class, property, pack), the getter and setter evaluations, their self time and their steps, in a
//! fixed table; the host publishes it on `Stats:eval` (shipped pack `stats`) every window, and it changes no value and writes no journal line.
//! The overhead of the counting is REPORTED by `overhead_report` (run it with `--nocapture`), never asserted.
use membrane_vm::fields::Fields;
use membrane_vm::stats::{self, Table, KIND_GETTER, KIND_SETTER};
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn world(cls: &str, pack: &str) -> (Core, Fields, i64) {
    let mut f = Fields::goalgraph();
    f.set_pack(pack.split_whitespace().nth(1).unwrap(), pack).unwrap();
    let mut c = Core::new();
    c.load(format!("{cls}:a"), cls.into(), json!({"w": 1}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f, seq)
}
fn poke(c: &mut Core, f: &mut Fields, seq: &mut i64, eid: &str, v: i64, now: f64) {
    *seq += 1;
    let w = Write { eid: eid.into(), prop: "w".into(), new: json!(v), old: J::Null, writer: "person".into(), chain: json!({}), seq: *seq };
    c.hear(w.clone(), None, now);
    f.react(c, &H, w, now, seq);
}
fn row(cls: &str, prop: &str, kind: &str) -> Option<stats::Row> {
    stats::global_lifetime().into_iter().find(|r| r.cls == cls && r.prop == prop && r.kind == kind)
}

#[test]
fn n_evaluations_of_a_known_getter_are_n_counts() {
    stats::set_on(true);
    let (mut c, mut f, mut seq) = world("StatsA", "PACK sa 1\nStatsA.dbl getter: this.w * 2\n");
    let before = row("StatsA", "dbl", KIND_GETTER).map_or(0, |r| r.calls);
    for i in 0..25 { poke(&mut c, &mut f, &mut seq, "StatsA:a", 10 + i, 2.0 + i as f64); }
    let r = row("StatsA", "dbl", KIND_GETTER).expect("the getter has a row");
    assert_eq!(r.calls - before, 25, "one evaluation per input change");
    assert_eq!(r.pack, "sa");
    assert!(r.steps >= 25 * 3, "`this.w * 2` is at least three steps: {}", r.steps);
    assert_eq!(c.docs["StatsA:a"].props["dbl"], json!(2 * 34), "counting changes no value");
}

#[test]
fn a_setter_run_is_counted_under_its_class_and_property() {
    stats::set_on(true);
    let (mut c, mut f, mut seq) = world("StatsB", "PACK sb 1\nStatsB.w on-setter copy:\n  ALWAYS:\n    this.z = this.w + 1\n");
    let before = row("StatsB", "w", KIND_SETTER).map_or(0, |r| r.calls);
    for i in 0..7 { poke(&mut c, &mut f, &mut seq, "StatsB:a", 5 + i, 2.0 + i as f64); }
    let r = row("StatsB", "w", KIND_SETTER).expect("the setter has a row");
    assert_eq!(r.calls - before, 7);
    assert_eq!(r.pack, "sb");
    assert_eq!(c.docs["StatsB:a"].props["z"], json!(12));
}

#[test]
fn the_window_resets_and_the_lifetime_keeps() {
    let mut t = Table::new();
    for _ in 0..4 { t.record(KIND_GETTER, "X", "p", "k", 1000, 10, 7); }
    t.record(KIND_SETTER, "X", "p", "k", 500, 0, 3);
    let w = t.take_window();
    assert_eq!(w.len(), 2, "a getter and a setter of the same name are two rows");
    let g = w.iter().find(|r| r.kind == KIND_GETTER).unwrap();
    assert_eq!((g.calls, g.ns, g.wait_ns, g.steps), (4, 4000, 40, 28));
    assert!(t.take_window().is_empty(), "the window starts over");
    t.record(KIND_GETTER, "X", "p", "k", 1000, 0, 1);
    assert_eq!(t.take_window()[0].calls, 1);
    let life = t.lifetime();
    assert_eq!(life.iter().find(|r| r.kind == KIND_GETTER).unwrap().calls, 5, "the lifetime keeps what the windows took");
    t.clear();
    assert!(t.lifetime().is_empty());
}

#[test]
fn the_table_is_fixed_size_and_overflow_is_counted_not_lost() {
    let mut t = Table::new();
    for i in 0..5000 { t.record(KIND_GETTER, "C", &format!("p{i}"), "k", 1, 0, 1); }
    let total: u64 = t.lifetime().iter().map(|r| r.calls).sum();
    assert_eq!(total, 5000, "every evaluation is counted somewhere");
    assert!(t.lifetime().len() <= 1100, "the table does not grow: {}", t.lifetime().len());
    assert!(t.lifetime().iter().any(|r| r.cls == "(other)"), "what does not fit is one `(other)` row");
}

#[test]
fn the_top_is_sorted_by_self_time_and_cut() {
    let mut t = Table::new();
    for i in 0..30u64 { t.record(KIND_GETTER, "C", &format!("p{i}"), "k", (i + 1) * 1_000_000, 0, 1); }
    let j = stats::rows_json(&t.take_window(), 20);
    let a = j.as_array().unwrap();
    assert_eq!(a.len(), 20);
    assert_eq!(a[0]["prop"], json!("p29"));
    assert_eq!(a[0]["ms"], json!(30.0));
    assert_eq!(a[0]["calls"], json!(1));
    for k in ["class", "prop", "pack", "kind", "calls", "ms", "wait_ms", "steps"] { assert!(a[0].get(k).is_some(), "{k}"); }
}

#[test]
#[ignore]
fn overhead_report() {
    let (mut c, mut f, mut seq) = world("StatsO", "PACK so 1\nStatsO.dbl getter: this.w * 2\n");
    let n = 200_000;
    for on in [false, true, false, true] {
        stats::set_on(on);
        let t = std::time::Instant::now();
        for i in 0..n { poke(&mut c, &mut f, &mut seq, "StatsO:a", i, 2.0 + i as f64); }
        println!("eval_stats {} : {:.0} ns per poke (one getter evaluation each)", if on { "ON " } else { "OFF" }, t.elapsed().as_nanos() as f64 / n as f64);
    }
}

// #1143 (w36): a rebuild settles a chain of getters that read each other across entities over several passes. A getter whose inputs did
// not change in a pass is NOT evaluated again: `Command.timeout_default` was evaluated 16 times per Command at every boot (the passes
// ran to their cap, every getter of every entity each time).
#[test]
fn a_rebuild_does_not_evaluate_a_getter_whose_inputs_did_not_change() {
    stats::set_on(true);
    let pack = "PACK sr 1\nStatsR.dbl getter: this.w * 2\nStatsR.d getter: (prop(this.up, 'd') + 1) if this.up != None else 0\n";
    let mut f = Fields::goalgraph();
    f.set_pack("sr", pack).unwrap();
    let mut c = Core::new();
    let n = 12;
    for i in 0..n {
        let mut p = json!({"w": 1}).as_object().unwrap().clone();
        if i + 1 < n { p.insert("up".into(), json!({"$ref": format!("StatsR:e{:02}", i + 1)})); }
        c.load(format!("StatsR:e{:02}", i), "StatsR".into(), p, None, 0.0);
    }
    let before = row("StatsR", "dbl", KIND_GETTER).map_or(0, |r| r.calls);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    assert_eq!(c.docs["StatsR:e00"].props["d"], json!(n - 1), "the chain settles");
    let calls = row("StatsR", "dbl", KIND_GETTER).unwrap().calls - before;
    assert_eq!(calls, n as u64, "`dbl` reads only `w`: once per entity, not once per pass");
}

// #1143 (w36): a comprehension (or a `let`) opens a scope over the block's locals; it must not COPY them. `SemText.related` held ~1000 rows in a local
// and ran ~1400 inner comprehensions: each copied the local (a 10x cost, 60 ms against a 10 ms allowance). The self time of one evaluation here is
// well under a second when nothing is copied and many seconds when every iteration copies the 3000-row local.
#[test]
fn a_comprehension_inside_a_block_does_not_copy_the_blocks_locals() {
    stats::set_on(true);
    let pack = "PACK sl 1\nStatsL.go getter:\n  big = [{'a': i, 'b': 'xxxxxxxx'} for i in this.nums]\n  len([len([1 for y in [1, 2]]) for x in this.nums])\n";
    let mut f = Fields::goalgraph();
    f.set_pack("sl", pack).unwrap();
    let mut c = Core::new();
    let nums: Vec<i64> = (0..3000).collect();
    c.load("StatsL:a".into(), "StatsL".into(), json!({"nums": nums}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    let before = row("StatsL", "go", KIND_GETTER).map_or(0, |r| r.ns);
    f.rebuild(&mut c, 1.0, &mut seq);
    let ns = row("StatsL", "go", KIND_GETTER).expect("evaluated").ns - before;
    assert_eq!(c.docs["StatsL:a"].props["go"], json!(3000));
    assert!(ns < 500_000_000, "{} ms for one evaluation: the locals were copied per iteration", ns / 1_000_000);
}
