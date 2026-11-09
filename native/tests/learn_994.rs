//! #994 row 6: self's learned triggers (ch_learn.py #576 C) as field rules vs a hand-coded account of the contract.
//! A cause->effect pair seen in one chain defines a copy and a literal candidate (each charged one write); the cause
//! seen again fires them (charged); a later matching write in the chain refunds 2x; a proposal on a granted path writes
//! live; a loser is pruned; asleep nothing is learned. The whole-field checksum (FNV-1a) is stable and sensitive.
use membrane_vm::fields::Fields;
use membrane_vm::learn::{Learner, CLASS, WRITE_COST as C};
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Map, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

const T: &str = "toy:lamp";

struct W { c: Core, f: Fields, seq: i64, now: f64 }
impl W {
    fn new(learn: bool) -> W {
        let mut c = Core::new();
        c.load(T.into(), "Toy".into(), Map::new(), None, 0.0);
        let mut f = Fields::goalgraph();
        if learn { f.learn = Some(Learner::new()); }
        W { c, f, seq: 0, now: 1.0 }
    }
    fn set(&mut self, chain: &str, prop: &str, v: J) {
        self.seq += 1; self.now += 1.0;
        let w = Write { eid: T.into(), prop: prop.into(), new: v, old: J::Null, writer: "person:ann".into(), chain: json!({"id": chain}), seq: self.seq };
        self.c.hear(w.clone(), None, self.now);
        self.f.react(&mut self.c, &H, w, self.now, &mut self.seq);
    }
    fn bals(&self) -> Vec<(bool, f64, u64)> {
        let l = self.f.learn.as_ref().unwrap();
        let mut v: Vec<(bool, f64, u64)> = l.cands.iter().map(|c| (c.copy, c.bal, c.hits)).collect();
        v.sort_by(|a, b| b.0.cmp(&a.0)); v
    }
}
fn near(a: f64, b: f64) -> bool { (a - b).abs() < 1e-12 }

fn checksum(c: &Core) -> u64 {
    let mut rows: Vec<String> = c.docs.iter().map(|(k, e)| {
        let mut ps: Vec<String> = e.props.iter().map(|(p, v)| format!("{}={}", p, v)).collect(); ps.sort();
        format!("{}|{}|{}", k, e.cls, ps.join(";"))
    }).collect();
    rows.sort();
    let mut h: u64 = 0xcbf29ce484222325;
    for b in rows.join("\n").bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
    h
}

fn story() -> W {
    let mut w = W::new(true);
    w.set("c1", "mode", json!("on")); w.set("c1", "lamp", json!("on"));
    // defined: a copy and a literal, each charged one write, no hits yet
    let b = w.bals();
    assert_eq!(b.len(), 2);
    assert!(b[0].0 && near(b[0].1, -C) && near(b[1].1, -C) && b[0].2 == 0, "{:?}", b);
    w.set("c2", "mode", json!("x"));
    // both fired in shadow (charged), predictions sit on the Learned entities
    let b = w.bals();
    assert!(near(b[0].1, -2.0 * C) && near(b[1].1, -2.0 * C), "{:?}", b);
    for c in &w.f.learn.as_ref().unwrap().cands {
        let p = &w.c.docs[&c.eid].props;
        assert_eq!(p["state"], json!("shadow")); assert_eq!(p["cause"], json!("mode")); assert_eq!(p["to"], json!("lamp"));
        assert_eq!(p["predicted"]["value"], if c.copy { json!("x") } else { json!("on") });
        assert_eq!(w.c.docs[&c.eid].cls, CLASS);
    }
    assert!(!w.c.docs[T].props.contains_key("lamp") || w.c.docs[T].props["lamp"] == json!("on"), "shadow writes nothing");
    w.set("c2", "lamp", json!("x"));
    // only the copy hit: refunded 2x
    let b = w.bals();
    assert!(near(b[0].1, 0.0) && b[0].2 == 1 && near(b[1].1, -2.0 * C) && b[1].2 == 0, "{:?}", b);
    w.set("c3", "mode", json!("y")); w.set("c3", "lamp", json!("y"));
    let b = w.bals();
    assert!(near(b[0].1, C) && b[0].2 == 2, "{:?}", b);
    assert_eq!(w.c.docs[&w.f.learn.as_ref().unwrap().cands.iter().find(|c| c.copy).unwrap().eid].props["state"], json!("proposal"));
    w
}

#[test]
fn candidates_are_scored_by_the_account() { story(); }

#[test]
fn a_proposal_writes_live_only_on_a_granted_path_and_a_loser_is_pruned() {
    let mut w = story();
    w.set("c4", "mode", json!("z")); w.set("c4", "lamp", json!("z"));
    assert_eq!(w.c.docs[T].props["lamp"], json!("z"), "ungranted: shadow only (the person wrote z)");
    w.f.learn.as_mut().unwrap().granted.push(format!("{}.lamp", T));
    w.set("c5", "mode", json!("q"));
    assert_eq!(w.c.docs[T].props["lamp"], json!("q"), "granted proposal writes live");
    let mut l = w.f.learn.take().unwrap();
    let gone = l.prune(&mut w.c);
    assert!(gone.len() >= 1, "the literal losers go (each value a new literal)");
    assert!(l.cands.iter().filter(|c| c.copy).count() == 1 && l.cands.iter().all(|c| c.bal >= -C), "only the copy and the untested stay");
    assert!(gone.iter().all(|g| !w.c.docs.contains_key(g)));
}

#[test]
fn asleep_nothing_is_learned_or_fired_and_off_by_default() {
    let mut w = W::new(true);
    w.f.learn.as_mut().unwrap().awake = false;
    w.set("c1", "mode", json!("on")); w.set("c1", "lamp", json!("on"));
    assert!(w.f.learn.as_ref().unwrap().cands.is_empty());
    let mut o = W::new(false);
    o.set("c1", "mode", json!("on")); o.set("c1", "lamp", json!("on"));
    assert_eq!(o.c.docs.len(), 1, "learn off: no Learned entity");
}

#[test]
fn checksum_is_stable_and_sensitive() {
    let (a, b) = (story(), story());
    let (ca, cb) = (checksum(&a.c), checksum(&b.c));
    println!("checksum {:016x}", ca);
    assert_eq!(ca, cb);
    let mut c = story();
    let e = c.f.learn.as_ref().unwrap().cands[0].eid.clone();
    c.c.docs.get_mut(&e).unwrap().props.insert("hits".into(), json!(99));
    assert_ne!(ca, checksum(&c.c));
}
