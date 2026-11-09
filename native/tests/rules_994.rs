//! #994 section 2 rules 1 and 2: self-read exclusion, one round per outside write.
use membrane_vm::getters::Engine;
use serde_json::{json, Value as J};

fn num(e: &Engine, p: &str) -> i64 { e.store.get(p).and_then(|v| v.as_i64()).unwrap_or(0) }

#[test]
fn counter_is_plus_one_per_tick_and_only_one() {
    let mut e = Engine::new();
    e.add("x", "/x", |c| json!(c.get("/x").and_then(|v| v.as_i64()).unwrap_or(0) + 1 + 0 * c.get("/clock/tick").map(|_| 1).unwrap_or(0)));
    for n in 1..=5 {
        e.write("/clock/tick", json!(n));
        assert_eq!(num(&e, "/x"), n, "+1 per tick");
        assert_eq!(e.recalcs["x"], n as u64, "recalculated once per tick");
    }
}

fn russell() -> Engine {
    let mut e = Engine::new();
    // sets/A/members/B, sets/B/members/B (empty files); R.members starts {} (no initial evaluation)
    e.seed("/sets/A/members/B", J::Null);
    e.seed("/sets/B/members/B", J::Null);
    e.seed("/sets/R/members", json!([]));
    e.add("R", "/sets/R/members", |c| {
        let mut out = vec![];
        for s in c.children("/sets") {
            let m = c.children(&format!("/sets/{}/members", s));
            if !m.contains(&s) { out.push(json!(s)); }
        }
        json!(out)
    });
    e
}
fn members(e: &Engine) -> Vec<String> {
    e.store["/sets/R/members"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect()
}

#[test]
fn russell_flips_once_per_change_and_never_otherwise() {
    let mut e = russell();
    assert!(members(&e).is_empty());
    assert_eq!(e.recalcs.get("R"), None);
    e.write("/sets/C/members/A", J::Null);
    assert_eq!(e.recalcs["R"], 1);
    assert_eq!(members(&e), vec!["A", "C", "R"]);
    e.write("/sets/D/members/D", J::Null);
    assert_eq!(e.recalcs["R"], 2);
    assert_eq!(members(&e), vec!["A", "C"]);
    // no outside writes: no round, no recalculation (R is not its own input)
    let (r, f) = (e.rounds, e.flips.len());
    assert_eq!((e.rounds, e.recalcs["R"], e.flips.len()), (r, 2, f));
    // a write elsewhere does not touch R
    e.write("/other/thing", json!(1));
    assert_eq!(e.recalcs["R"], 2);
    assert_eq!(members(&e), vec!["A", "C"]);
}

#[test]
fn russell_checksum_replay_same_journal_same_flips() {
    let mut a = russell();
    a.write("/sets/C/members/A", J::Null);
    a.write("/sets/D/members/D", J::Null);
    a.write("/other/thing", json!(1));
    let mut b = russell();
    for (p, v) in a.journal.clone() { b.write(&p, v); }
    assert_eq!(a.flips, b.flips);
    assert_eq!(a.checksum(), b.checksum());
    // a different journal gives a different checksum
    let mut c = russell();
    c.write("/sets/C/members/A", J::Null);
    assert_ne!(a.checksum(), c.checksum());
}

#[test]
fn mutual_getters_recalculate_once_per_outside_write_in_fixed_order() {
    let mut e = Engine::new();
    e.add("a", "/a", |c| json!(c.get("/b").and_then(|v| v.as_i64()).unwrap_or(0) + 1 + c.get("/in").and_then(|v| v.as_i64()).unwrap_or(0)));
    e.add("b", "/b", |c| json!(c.get("/a").and_then(|v| v.as_i64()).unwrap_or(0) + 1));
    e.write("/in", json!(0));
    // order a, b: a sees b's previous (0): a=1; b sees the new a: b=2; the closing edge (b -> a) reads previous, no rerun
    assert_eq!((num(&e, "/a"), num(&e, "/b")), (1, 2));
    assert_eq!((e.recalcs["a"], e.recalcs["b"]), (1, 1));
    e.write("/in", json!(10));
    // a = b(2) + 1 + 10 = 13; b = 14
    assert_eq!((num(&e, "/a"), num(&e, "/b")), (13, 14));
    assert_eq!((e.recalcs["a"], e.recalcs["b"]), (2, 2));
    // replay gives the same field
    let mut r = Engine::new();
    r.add("a", "/a", |c| json!(c.get("/b").and_then(|v| v.as_i64()).unwrap_or(0) + 1 + c.get("/in").and_then(|v| v.as_i64()).unwrap_or(0)));
    r.add("b", "/b", |c| json!(c.get("/a").and_then(|v| v.as_i64()).unwrap_or(0) + 1));
    for (p, v) in e.journal.clone() { r.write(&p, v); }
    assert_eq!(e.checksum(), r.checksum());
}
