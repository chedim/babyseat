//! A time trip is recorded as a TRIP ENTITY (operator, 2026-11-12: record only the kills, never timings). When a node is killed
//! by the self-time allowance the engine writes `Trip:<eid>#<prop>` {node, self_ms, allowance_ms, at, active} the way an effect
//! receipt arrives (writer `field:driver:trip`), so a journal that keeps the outside writes keeps the kills. A node whose own Trip
//! is active is the reasoned null; the Trip applies until one of the node's inputs changes (then the node gets a fresh chance and
//! a fresh evaluation that does not trip sets `active` false). Replay: the node first evaluates (fast), the journalled Trip
//! arrives, the node recomputes to the same null.
use membrane_vm::expr;
use membrane_vm::fields::Fields;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::cell::Cell;

const MS: u64 = 1_000_000;
const TRIP: &str = "Trip:Box:a#slow";
thread_local! { static NOW: Cell<u64> = const { Cell::new(0) }; static STEP: Cell<u64> = const { Cell::new(0) }; static READS: Cell<u32> = const { Cell::new(0) }; }
/// every read of the clock moves it STEP ms on, and is counted: a getter that runs away reads it at its start and at each 256th step
fn fake() -> u64 { READS.with(|r| r.set(r.get() + 1)); NOW.with(|n| { let v = n.get(); n.set(v + STEP.with(|s| s.get())); v }) }
fn use_clock(step_ms: u64) { NOW.with(|n| n.set(0)); READS.with(|r| r.set(0)); STEP.with(|s| s.set(step_ms * MS)); expr::set_clock(Some(fake)); }
fn real_clock() { expr::set_clock(None); }
fn reads() -> u32 { READS.with(|r| r.get()) }

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

/// `slow` reads `this.w` first, then runs away (600 elements: it reads the clock at its start and at steps 256 and 512)
fn long() -> String {
    let items = (0..600).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
    format!("PACK t 1\nBox.slow getter: this.w + len([x for x in [{items}]])\n")
}
fn long_dep() -> String { format!("{}Box.dep getter: str(this.slow)\n", long()) }
fn world(pack: &str) -> (Core, Fields, i64) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), json!({"w": 1}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f, seq)
}
fn val(c: &Core, e: &str, p: &str) -> J { c.docs.get(e).and_then(|d| d.props.get(p).cloned()).unwrap_or(J::Null) }
fn poke(c: &mut Core, f: &mut Fields, seq: &mut i64, v: i64, now: f64) {
    *seq += 1;
    let w = Write { eid: "Box:a".into(), prop: "w".into(), new: json!(v), old: J::Null, writer: "person".into(), chain: json!({}), seq: *seq };
    c.hear(w.clone(), None, now);
    f.react(c, &H, w, now, seq);
}
fn trip_writes(f: &Fields) -> Vec<J> { f.out.iter().filter(|o| o["writer"] == json!("field:driver:trip") || o.get("born").is_some()).cloned().collect() }
fn reason(f: &Fields, c: &Core) -> J { f.why_chain(c, "Box:a", "slow")[0]["reason"].clone() }

/// The journal's outside writes: what the host would keep of the live run (the person's pokes are kept by the test itself)
enum Line { Poke(i64, f64), Trip(Vec<J>) }
fn replay(lines: &[Line]) -> (Core, Fields) { replay_in(&long(), lines) }
fn replay_in(pack: &str, lines: &[Line]) -> (Core, Fields) {
    let (mut c, mut f, mut seq) = world(pack);
    for l in lines {
        match l {
            Line::Poke(v, now) => poke(&mut c, &mut f, &mut seq, *v, *now),
            Line::Trip(ws) => for o in ws {
                let eid = o["eid"].as_str().unwrap().to_string();
                if let Some(cls) = o.get("born") { c.load(eid.clone(), cls.as_str().unwrap().into(), Default::default(), None, 2.0); f.born(&mut c, &H, &eid, 2.0, &mut seq); continue; }
                seq += 1;
                let w = Write { eid, prop: o["prop"].as_str().unwrap().into(), new: o["new"].clone(), old: J::Null, writer: "driver:trip".into(), chain: json!({}), seq };
                c.hear(w.clone(), None, 2.0);
                f.react(&mut c, &H, w, 2.0, &mut seq);
            },
        }
    }
    (c, f)
}

#[test]
fn a_killed_node_writes_a_trip_entity_with_the_numbers() {
    use_clock(6);
    let (c, f, _) = world(&long());
    real_clock();
    assert_eq!(val(&c, "Box:a", "slow"), J::Null);
    let t = &c.docs[TRIP];
    assert_eq!(t.cls, "Trip");
    assert_eq!(t.props["node"], json!("Box:a.slow"));
    assert_eq!(t.props["self_ms"], json!(12));
    assert_eq!(t.props["allowance_ms"], json!(10));
    assert_eq!(t.props["at"], json!(1.0));
    assert_eq!(t.props["active"], json!(true));
    let w = trip_writes(&f);
    assert!(w.iter().any(|o| o.get("born") == Some(&json!("Trip")) && o["eid"] == json!(TRIP)), "{w:?}");
    assert!(w.iter().filter(|o| o.get("prop").is_some()).all(|o| o["writer"] == json!("field:driver:trip")), "{w:?}");
}

#[test]
fn why_says_the_reason_and_points_at_the_trip() {
    use_clock(6);
    let (c, f, _) = world(&long_dep());
    real_clock();
    let chain = f.why_chain(&c, "Box:a", "slow");
    assert_eq!(chain[0]["reason"], json!("timeout 12 ms (allowance 10 ms)"), "{chain:?}");
    assert_eq!(chain[0]["trip"], json!(TRIP), "{chain:?}");
    assert_eq!(val(&c, "Box:a", "dep"), J::Null, "a reader of the kill is that Error (stored null), the rest of the engine runs");
    assert_eq!(f.why_chain(&c, "Box:a", "dep")[0]["reason"], json!("timeout 12 ms (allowance 10 ms)"));
}

#[test]
fn an_evaluation_that_finishes_in_time_records_nothing() {
    use_clock(0);
    let (mut c, mut f, mut s) = world(&long());
    poke(&mut c, &mut f, &mut s, 2, 2.0);
    real_clock();
    assert_eq!(val(&c, "Box:a", "slow"), json!(602));
    assert!(!c.docs.keys().any(|k| k.starts_with("Trip:")), "no timing log");
    assert!(trip_writes(&f).is_empty());
}

#[test]
fn the_trip_write_does_not_rerun_the_node_or_count_as_a_person() {
    use_clock(6);
    let (c, f, _) = world(&long());
    real_clock();
    assert_eq!(reads(), 3, "ONE evaluation (start + two backstops): the engine's own Trip write is not an input change");
    assert!(f.refusals.is_empty(), "no loop refusal: {:?}", f.refusals);
    assert!(trip_writes(&f).iter().all(|o| o["writer"] != json!("person")));
}

#[test]
fn the_trip_applies_until_an_input_changes_then_the_node_gets_a_fresh_chance() {
    use_clock(6);
    let (mut c, mut f, mut s) = world(&long());
    assert_eq!(val(&c, TRIP, "active"), json!(true));
    use_clock(0);
    poke(&mut c, &mut f, &mut s, 7, 2.0);
    real_clock();
    assert_eq!(val(&c, "Box:a", "slow"), json!(607), "fresh evaluation, in time");
    assert_eq!(val(&c, TRIP, "active"), json!(false), "the kill stays as history, but no longer applies");
    assert!(f.why_chain(&c, "Box:a", "slow").is_empty());
}

#[test]
fn a_fresh_evaluation_that_trips_again_rewrites_the_trip() {
    use_clock(6);
    let (mut c, mut f, mut s) = world(&long());
    use_clock(8);
    poke(&mut c, &mut f, &mut s, 7, 5.0);
    real_clock();
    assert_eq!(val(&c, "Box:a", "slow"), J::Null);
    assert_eq!(val(&c, TRIP, "self_ms"), json!(16));
    assert_eq!(val(&c, TRIP, "at"), json!(5.0));
    assert_eq!(val(&c, TRIP, "active"), json!(true));
    assert_eq!(reads(), 3, "the poke is one evaluation, not two");
}

#[test]
fn replay_with_a_clock_that_would_not_trip_reaches_the_same_state() {
    use_clock(6);
    let (c, f, _) = world(&long());
    real_clock();
    let live_null = reason(&f, &c);
    let journal = vec![Line::Trip(trip_writes(&f))];
    let (rc, rf) = replay(&journal);               // the real clock: nothing would trip
    assert_eq!(val(&rc, "Box:a", "slow"), J::Null, "the journalled kill applies");
    assert_eq!(reason(&rf, &rc), live_null);
    assert_eq!(rc.docs[TRIP].props, c.docs[TRIP].props);
    assert_eq!(rc.docs["Box:a"].props, c.docs["Box:a"].props, "the whole entity, dependants included");
}

#[test]
fn replay_follows_the_trip_through_a_later_input_change() {
    use_clock(6);
    let (mut c, mut f, mut s) = world(&long());
    let first = trip_writes(&f);
    f.out.clear();
    use_clock(0);
    poke(&mut c, &mut f, &mut s, 7, 2.0);
    real_clock();
    assert!(trip_writes(&f).is_empty(), "the clear is derived, not a journalled write");
    let (rc, _) = replay(&[Line::Trip(first), Line::Poke(7, 2.0)]);
    assert_eq!(rc.docs["Box:a"].props, c.docs["Box:a"].props);
    assert_eq!(rc.docs[TRIP].props, c.docs[TRIP].props);
    assert_eq!(val(&rc, TRIP, "active"), json!(false));
}

#[test]
fn replay_of_a_second_kill_on_the_same_node() {
    use_clock(6);
    let (mut c, mut f, mut s) = world(&long());
    let mut journal = vec![Line::Trip(trip_writes(&f))];
    f.out.clear();
    use_clock(8);
    poke(&mut c, &mut f, &mut s, 7, 5.0);
    real_clock();
    journal.push(Line::Poke(7, 5.0));
    journal.push(Line::Trip(trip_writes(&f)));
    let (rc, rf) = replay(&journal);
    assert_eq!(rc.docs["Box:a"].props, c.docs["Box:a"].props);
    assert_eq!(rc.docs[TRIP].props, c.docs[TRIP].props);
    assert_eq!(reason(&rf, &rc), json!("timeout 16 ms (allowance 10 ms)"));
}

#[test]
fn the_shipped_trip_pack_declares_the_class_so_the_host_mirrors_it() {
    let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/packs/trip.c1f")).unwrap();
    let mut f = Fields::goalgraph();
    f.set_pack("trip", &text).unwrap();
    assert!(f.classes().iter().any(|c| c == "Trip"), "{:?}", f.classes());
}

#[test]
fn an_input_that_changes_in_the_same_round_as_the_trip_wins() {
    // a setter on the Trip's arrival moves the node's input: the node is evaluated fresh, the kill no longer applies
    use_clock(6);
    let (live, f, _) = world(&long());
    real_clock();
    assert_eq!(val(&live, "Box:a", "slow"), J::Null);
    let pack = format!("{}Trip.active on-setter bump:\n  ALWAYS:\n    ('Box:a').w = 9\n", long());
    let (rc, _) = replay_in(&pack, &[Line::Trip(trip_writes(&f))]);
    assert_eq!(val(&rc, "Box:a", "slow"), json!(609));
    assert_eq!(val(&rc, TRIP, "active"), json!(false));
}
