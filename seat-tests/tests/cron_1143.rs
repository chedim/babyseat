//! The `Cron` entity (#1143, operator 2026-11-16): `Cron:<name> {schedule, enabled}`. The pack (`membrane/packs/cron.c1f`) parses the schedule, computes the
//! next time and arms ONE timer with the driver verb `Cron.arm = {id, at}`; the driver (store.rs `arm_timer` / `fire_due`) only waits and answers with the receipt
//! `arm_result`, which the pack turns into `fired = {id, at}`. Every test runs on a FAKE CLOCK (a settable time handed to the host: nothing sleeps) in a fixed
//! zone with no daylight saving (`TZ=Etc/GMT-2`, UTC+2), so the expected epochs are plain arithmetic.
mod common;

use cliffvm::host::{Host, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn pack(n: &str) -> String { std::fs::read_to_string(repo().join(format!("membrane/packs/{n}.c1f"))).unwrap_or_else(|e| panic!("{n}.c1f: {e}")) }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

extern "C" { fn tzset(); }
/// UTC+2 all year (POSIX `Etc/GMT-2`), set before any local-time call of this process
fn zone() { static ONCE: std::sync::Once = std::sync::Once::new(); ONCE.call_once(|| { std::env::set_var("TZ", "Etc/GMT-2"); unsafe { tzset(); } }); }
const TZ_OFF: f64 = 7200.0;
/// the epoch second of a LOCAL wall time (UTC+2): 2026-11-16 is day 20773 since 1970 (a Monday)
fn at(day: i64, h: i64, m: i64) -> f64 { (day * 86400 + h * 3600 + m * 60) as f64 - TZ_OFF }
const MON: i64 = 20773;          // 2026-11-16, a Monday

struct Clock(Arc<AtomicU64>);
impl Clock {
    fn set(&self, t: f64) { self.0.store(t.to_bits(), Ordering::SeqCst); }
    fn get(&self) -> f64 { f64::from_bits(self.0.load(Ordering::SeqCst)) }
    fn add(&self, s: f64) { self.set(self.get() + s); }
}
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("cron-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(d.join(".membrane")).unwrap();
    d.canonicalize().unwrap()
}
fn store_path(d: &std::path::Path) -> String { d.join(".membrane/field.sqlite3").to_string_lossy().to_string() }
/// a host on `d`'s store with the packs the Cron needs, on the fake clock `c`; `boot` tells it has started (a restart: the timers are re-armed)
fn host_at(d: &std::path::Path, c: &Clock, boot: bool) -> Host {
    zone();
    let cc = c.0.clone();
    let h = Host::open({ let mut o = Options::new(Some(store_path(d))); o.clock = Box::new(move || f64::from_bits(cc.load(Ordering::SeqCst))); o }).unwrap();
    for n in ["entity", "boot", "cron", "cron_probe"] { h.load_pack(n, &pack_or_probe(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.load_stored_entities();
    h.rebuild();
    if boot { cliffvm::host::boot_started(&h, &Map::new(), true); }
    h.pump();
    h
}
/// a test pack of its own: a counter that reacts to `Cron.fired` with an ordinary setter (what a user's pack does)
fn pack_or_probe(n: &str) -> String {
    if n != "cron_probe" { return pack(n); }
    "PACK cron_probe 1\nIMPORT cron 1\nCron.fired on-setter probe-count:\n  ALWAYS:\n    this.seen = (this.seen or 0) + 1\n".to_string()
}
fn put(h: &Host, eid: &str, cls: &str, p: J) {
    // like the door's `entity_write`: the entity is made, then each property is a write (a setter on it fires)
    if !h.snapshot().contains_key(eid) { h.born(eid, cls, Map::new(), "person"); }
    for (k, v) in p.as_object().unwrap() { h.write(eid, k, v.clone(), "person"); }
    h.pump();
}
/// move the fake clock to `t`, let the host fire what is due and settle
fn go(h: &Host, c: &Clock, t: f64) { c.set(t); for _ in 0..4 { h.pump(); } }

fn s(h: &Host, name: &str, p: &str) -> J { h.get(&format!("Cron:{name}"), p) }
const T0: (i64, i64, i64) = (MON, 10, 30);      // Monday 2026-11-16 10:30 local; the tests start 30 s later
fn t0() -> f64 { at(T0.0, T0.1, T0.2) + 30.0 }
fn start(tag: &str) -> (std::path::PathBuf, Clock, Host) {
    let d = tmp(tag);
    let c = Clock(Arc::new(AtomicU64::new(0)));
    c.set(t0());
    let h = host_at(&d, &c, true);
    (d, c, h)
}
/// a Cron with this schedule, born at the start time
fn cron(h: &Host, name: &str, sched: &str) { put(h, &format!("Cron:{name}"), "Cron", json!({"schedule": sched})); }

#[test]
fn a_bad_schedule_is_refused_with_a_reason_and_never_guessed() {
    let (_d, _c, h) = start("bad");
    for (i, (sched, why)) in [
        ("", "needs a schedule"), ("   ", "needs a schedule"), ("* * * *", "5 fields"), ("* * * * * *", "5 fields"), ("61 * * * *", "bad cron field minute: 61"),
        ("*/0 * * * *", "bad cron field minute: */0"), ("5-1 * * * *", "bad cron field minute: 5-1"), ("a * * * *", "bad cron field minute: a"),
        ("* 24 * * *", "bad cron field hour: 24"), ("* * 0 * *", "bad cron field day-of-month: 0"), ("* * * 13 *", "bad cron field month: 13"), ("* * * * 8", "bad cron field day-of-week: 8"),
        ("* * * jan *", "bad cron field month: jan"), ("1,,2 * * * *", "bad cron field minute"), ("1, * * * *", "bad cron field minute"), ("1.5 * * * *", "bad cron field minute: 1.5"),
        ("@daily", "the only @-form"), ("@every", "unknown schedule"), ("@every 0s", "bad @every"), ("@every 5x", "bad @every"), ("@every s", "bad @every"), ("@every 1.5s", "bad @every"),
        ("0 0 31 2 *", "matches no day"),
    ].iter().enumerate() {
        let n = format!("b{i}");
        cron(&h, &n, sched);
        let p = s(&h, &n, "problem");
        assert!(p.as_str().map_or(false, |t| t.contains(why)), "{sched:?}: wanted a problem with {why:?}, got {p}");
        assert!(s(&h, &n, "next").is_null() && s(&h, &n, "fired").is_null(), "{sched:?}: a refused schedule is never armed");
    }
}

#[test]
fn a_good_schedule_has_no_problem() {
    let (_d, _c, h) = start("good");
    for (i, sched) in ["* * * * *", "*/5 * * * *", "0 9 * * 1-5", "5,10,15 */2 1-15 1,6 0", "10-40/10 * * * *", "0 0 29 2 *", "0 8 * * 7", "@every 1s", "@every 30s", "@every 5m", "@every 2h", "@every 1d"].iter().enumerate() {
        let n = format!("g{i}");
        cron(&h, &n, sched);
        assert!(s(&h, &n, "problem").is_null(), "{sched:?}: {}", s(&h, &n, "problem"));
        assert!(s(&h, &n, "next").is_number(), "{sched:?} is armed: {}", s(&h, &n, "next"));
    }
}

#[test]
fn next_is_the_first_matching_minute_strictly_after_now_in_the_local_zone() {
    let (_d, _c, h) = start("next");
    let w = |d: i64, hh: i64, m: i64| at(d, hh, m);
    // (schedule, the expected next): now is Monday 10:30:30 (local, UTC+2)
    for (i, (sched, want)) in [
        ("* * * * *", w(MON, 10, 31)), ("*/15 * * * *", w(MON, 10, 45)), ("31 10 * * *", w(MON, 10, 31)),
        ("30 10 * * *", w(MON + 1, 10, 30)),                       // 10:30 is not after 10:30:30
        ("0 9 * * *", w(MON + 1, 9, 0)), ("0 9 * * 1-5", w(MON + 1, 9, 0)), ("0 9 * * 6", w(MON + 5, 9, 0)),
        ("0 8 * * 7", w(MON + 6, 8, 0)), ("0 8 * * 0", w(MON + 6, 8, 0)),             // Sunday is 0 and 7
        ("0 0 1 * *", w(MON + 15, 0, 0)),                          // 1 December
        ("0 12 31 * *", w(MON + 45, 12, 0)),                       // November has 30 days: 31 December
        ("0 0 13 * 5", w(MON + 4, 0, 0)),                          // the 13th OR a Friday: Friday 20 November comes first
        ("0 0 13 * *", w(MON + 27, 0, 0)),                         // the 13th alone: 13 December
        ("0 0 * * 5", w(MON + 4, 0, 0)),                           // a Friday alone
        ("0 0 29 2 *", w(MON + 470, 0, 0)),                        // 29 February 2028: the far search
        ("10-40/10 11 * * *", w(MON, 11, 10)), ("0 */6 * * *", w(MON, 12, 0)),
        ("0 0 1 1,7 *", w(MON + 46, 0, 0)),                        // 1 January 2027
        ("@every 90s", t0() + 90.0), ("@every 2h", t0() + 7200.0), ("@every 1d", t0() + 86400.0), ("@every 1s", t0() + 1.0),
    ].iter().enumerate() {
        let n = format!("n{i}");
        cron(&h, &n, sched);
        assert!(s(&h, &n, "problem").is_null(), "{sched:?}: {}", s(&h, &n, "problem"));
        assert_eq!(s(&h, &n, "next").as_f64(), Some(*want), "{sched:?}");
    }
}

#[test]
fn a_fire_writes_fired_once_and_an_ordinary_setter_reacts() {
    let (_d, c, h) = start("fire");
    cron(&h, "tick", "@every 10s");
    assert_eq!(s(&h, "tick", "next").as_f64(), Some(t0() + 10.0));
    go(&h, &c, t0() + 9.0);
    assert!(s(&h, "tick", "fired").is_null() && s(&h, "tick", "seen").is_null(), "not yet: nothing fires before its time");
    go(&h, &c, t0() + 10.0);
    assert_eq!(s(&h, "tick", "fired")["at"].as_f64(), Some(t0() + 10.0), "{}", s(&h, "tick", "fired"));
    assert!(s(&h, "tick", "fired")["id"].is_string());
    assert_eq!(s(&h, "tick", "seen"), json!(1), "the setter on Cron.fired ran once");
    assert_eq!(s(&h, "tick", "last").as_f64(), Some(t0() + 10.0));
    assert_eq!(s(&h, "tick", "next").as_f64(), Some(t0() + 20.0), "armed again for the slot after");
    go(&h, &c, t0() + 10.5);
    go(&h, &c, t0() + 19.9);
    assert_eq!(s(&h, "tick", "seen"), json!(1), "the same fire is not delivered twice");
    go(&h, &c, t0() + 20.0);
    go(&h, &c, t0() + 30.0);
    assert_eq!(s(&h, "tick", "seen"), json!(3));
    assert_eq!(s(&h, "tick", "dropped"), J::Null, "nothing was late");
}

#[test]
fn a_cron_field_schedule_fires_on_its_minute() {
    let (_d, c, h) = start("field");
    cron(&h, "five", "*/5 * * * *");
    go(&h, &c, at(MON, 10, 34) + 59.0);
    assert!(s(&h, "five", "fired").is_null());
    go(&h, &c, at(MON, 10, 35));
    assert_eq!(s(&h, "five", "fired")["at"].as_f64(), Some(at(MON, 10, 35)));
    assert_eq!(s(&h, "five", "next").as_f64(), Some(at(MON, 10, 40)));
}

#[test]
fn disabling_disarms_and_writing_the_schedule_arms_again() {
    let (_d, c, h) = start("rearm");
    cron(&h, "r", "@every 10s");
    put(&h, "Cron:r", "Cron", json!({"enabled": false}));
    assert!(s(&h, "r", "next").is_null(), "a disabled Cron has no next");
    go(&h, &c, t0() + 100.0);
    assert!(s(&h, "r", "fired").is_null() && s(&h, "r", "seen").is_null(), "a disarmed timer never fires");
    put(&h, "Cron:r", "Cron", json!({"enabled": true}));
    assert_eq!(s(&h, "r", "next").as_f64(), Some(t0() + 110.0), "armed again from now");
    // a new schedule replaces the armed timer: the old time must not fire
    put(&h, "Cron:r", "Cron", json!({"schedule": "@every 1h"}));
    assert_eq!(s(&h, "r", "next").as_f64(), Some(t0() + 100.0 + 3600.0));
    go(&h, &c, t0() + 115.0);
    assert!(s(&h, "r", "fired").is_null(), "the replaced timer is gone");
    // a schedule that is refused disarms too
    put(&h, "Cron:r", "Cron", json!({"schedule": "nonsense"}));
    assert!(s(&h, "r", "problem").is_string() && s(&h, "r", "next").is_null());
    go(&h, &c, t0() + 9000.0);
    assert!(s(&h, "r", "fired").is_null());
}

#[test]
fn after_a_restart_the_missed_runs_are_skipped_and_the_timer_is_armed_for_the_next_one() {
    let (d, c, h) = start("restart");
    cron(&h, "t", "@every 10s");
    go(&h, &c, t0() + 10.0);
    assert_eq!(s(&h, "t", "seen"), json!(1));
    drop(h);                                              // the seat stops; ten fires' worth of time passes
    c.set(t0() + 105.0);
    let h = host_at(&d, &c, true);                        // the restart: rows restored, the boot re-arms every Cron
    assert_eq!(s(&h, "t", "seen"), json!(1), "no catch-up: the missed runs did not run");
    assert_eq!(s(&h, "t", "next").as_f64(), Some(t0() + 115.0), "armed from the restart time, for the next slot");
    go(&h, &c, t0() + 114.0);
    assert_eq!(s(&h, "t", "seen"), json!(1));
    go(&h, &c, t0() + 115.0);
    assert_eq!(s(&h, "t", "seen"), json!(2), "and it fires once there");
    assert_eq!(s(&h, "t", "dropped"), J::Null, "skipped missed runs are not counted as dropped: they were never due while the seat ran");
}

#[test]
fn a_second_fire_that_comes_while_the_first_is_still_being_handled_is_dropped_and_counted() {
    let (_d, c, h) = start("drop");
    cron(&h, "d", "@every 1s");
    // the engine was busy for three and a half seconds: slots at +1, +2, +3 are all past when it looks again
    go(&h, &c, t0() + 3.5);
    assert_eq!(s(&h, "d", "seen"), json!(1), "one fire is delivered, the others are not queued");
    assert_eq!(s(&h, "d", "fired")["at"].as_f64(), Some(t0() + 1.0));
    assert_eq!(s(&h, "d", "dropped"), json!(2), "the slots at +2 and +3 are counted");
    assert_eq!(s(&h, "d", "next").as_f64(), Some(t0() + 4.0), "armed for the first slot still ahead");
    go(&h, &c, t0() + 4.0);
    assert_eq!(s(&h, "d", "seen"), json!(2));
}

#[test]
fn the_fire_is_journalled_once_and_a_replay_ends_in_the_same_state_without_firing() {
    let (_d, c, h) = start("replay");
    cron(&h, "j", "@every 10s");
    go(&h, &c, t0() + 10.0);
    go(&h, &c, t0() + 20.0);
    assert_eq!(s(&h, "j", "seen"), json!(2));
    let j = h.journal();
    let receipts: Vec<_> = j.iter().filter(|l| matches!(l, cliffvm::host::Line::Write { eid, prop, .. } if eid == "Cron:j" && prop == "arm_result")).collect();
    assert_eq!(receipts.len(), 2, "each fire is ONE outside write: {receipts:?}");
    assert!(receipts.iter().all(|l| l.writer() == "host:record:arm"), "stamped as a recorded receipt");
    // replay: no drivers (no timer to arm, nothing to wait for), the clock far ahead: nothing may fire
    zone();
    let mut o = Options::new(None);
    o.effects = false;
    let late = c.0.clone();
    c.set(t0() + 10_000.0);
    o.clock = Box::new(move || f64::from_bits(late.load(Ordering::SeqCst)));
    let r = Host::open(o).unwrap();
    for n in ["entity", "boot", "cron", "cron_probe"] { r.load_pack(n, &pack_or_probe(n)).unwrap(); }
    r.rebuild();
    r.replay(&j);
    for p in ["fired", "seen", "last", "dropped", "due", "next", "schedule"] {
        assert_eq!(r.get("Cron:j", p), h.get("Cron:j", p), "{p}: a replay equals the live run");
    }
    r.pump();
    assert_eq!(r.get("Cron:j", "seen"), json!(2), "a replay does not fire again");
}
