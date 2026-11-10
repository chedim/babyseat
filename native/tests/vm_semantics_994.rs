//! #994 review gaps 1-5 (doc sections 3-4): a read never fires a setter; a looping setter chain is bounded;
//! cold start of a new world runs effects once; warm restart runs none (caches saved with the store, loaded back);
//! the counting getter does not drift when an input write is dropped or doubled. Rust only.
use membrane_vm::fields::{Fields, OnSet};
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

fn mk(c: &mut Core, eid: &str, cls: &str, props: J) {
    let mut p = props.as_object().unwrap().clone();
    p.insert("parents".into(), json!([format!("cls:{}", cls.to_lowercase())]));
    c.load(eid.into(), cls.into(), p, None, 0.0);
}
fn classes(c: &mut Core) {
    for k in ["task", "decision", "wait", "goal"] { c.load(format!("cls:{}", k), "Class".into(), Default::default(), None, 0.0); }
    c.load("membrane::llm".into(), "Other".into(), Default::default(), None, 0.0);
}
fn births(c: &mut Core) {
    classes(c);
    mk(c, "g1", "Goal", json!({"status": "open"}));
    mk(c, "t1", "Task", json!({"goal": "g1", "status": "owed", "open_blockers": 0, "blocks": ["t2", "w1"]}));
    mk(c, "t2", "Task", json!({"goal": "g1", "status": "open", "open_blockers": 2}));
    mk(c, "d1", "Decision", json!({"goal": "g1", "status": "open", "blocks": ["t2"]}));
    mk(c, "w1", "Wait", json!({"goal": "g1", "status": "open", "open_blockers": 1, "every_s": 10, "blocks": ["t3"]}));
    mk(c, "t3", "Task", json!({"goal": "g1", "status": "open", "open_blockers": 1}));
}
fn journal() -> Vec<(&'static str, &'static str, J)> {
    vec![
        ("t1", "status", json!("running")),
        ("t1", "status", json!("done")),
        ("d1", "status", json!("closed")),
        ("g1", "planned_ts", json!(5.0)),
        ("g1", "planned_replan", json!("card d1 closed")),
        ("g1", "status", json!("closed")),
        ("t2", "status", json!("running")),
        ("t2", "status", json!("done")),
        ("g1", "status", json!("open")),
        ("w1", "status", json!("done")),
    ]
}

struct World { c: Core, fs: Fields, seq: i64, now: f64 }
impl World {
    fn new(fs: Fields) -> Self { World { c: Core::new(), fs, seq: 0, now: 1.0 } }
    fn write(&mut self, eid: &str, prop: &str, new: J) {
        self.seq += 1; self.now += 1.0;
        let w = Write { eid: eid.into(), prop: prop.into(), new, old: J::Null, writer: "person:ann".into(), chain: json!({}), seq: self.seq };
        self.c.hear(w.clone(), None, self.now);
        self.fs.react(&mut self.c, &H, w, self.now, &mut self.seq);
    }
    fn prop(&self, eid: &str, p: &str) -> J { self.c.docs[eid].props.get(p).cloned().unwrap_or(J::Null) }
}
fn cold() -> World {
    let mut w = World::new(Fields::goalgraph());
    births(&mut w.c);
    for (e, p, v) in journal() { w.write(e, p, v); }
    w
}
fn snapshot(c: &Core) -> Vec<String> {
    let mut rows: Vec<String> = c.docs.iter().map(|(eid, e)| {
        let mut ps: Vec<String> = e.props.iter().map(|(k, v)| format!("{}={}", k, v)).collect();
        ps.sort();
        format!("{}|{}|{}|{:?}", eid, e.cls, ps.join(","), e.pos)
    }).collect();
    rows.sort();
    rows
}

// gap 2: a read never fires a setter
#[test]
fn a_read_never_fires_a_setter() {
    let mut w = World::new(Fields::goalgraph());
    births(&mut w.c);
    w.write("t1", "status", json!("done"));   // t2 1 left, w1 armed
    let (rows, writes, seq, calls) = (snapshot(&w.c), w.fs.writes, w.seq, w.fs.calls.len());
    for _ in 0..3 {
        assert_eq!(w.c.eval_json("x.open_blockers", &[("x", "t2")], &[]).unwrap(), json!(1));
        assert_eq!(w.c.eval_json("x.open_blockers < 0.5 and x.status == 'open'", &[("x", "t3")], &[]).unwrap(), json!(false));
        let _ = w.c.eval_json("x.status == 'armed' and clock - x.armed_since >= x.every_s", &[("x", "w1")], &[("clock", json!(1e9))]);
        let _ = w.c.is_a(&H, "t2", "Task");
        let _ = w.prop("w1", "status");
    }
    assert_eq!(snapshot(&w.c), rows, "reading changed the field");
    assert_eq!((w.fs.writes, w.seq, w.fs.calls.len()), (writes, seq, calls), "a read wrote or called");
}

// gap 3: a setter chain that loops is cut off, not run forever
#[test]
fn a_looping_setter_chain_is_refused_at_the_revisit() {
    let mut fs = Fields::goalgraph();
    fs.setters = vec![
        OnSet { name: "side".into(), cls: "Task".into(), prop: "ping".into(), when: "x.ping >= 0".into(), writes: vec![("x".into(), "side".into(), "1".into())], ..Default::default() },
        OnSet { name: "ping".into(), cls: "Task".into(), prop: "ping".into(), when: "x.ping >= 0".into(), writes: vec![("x".into(), "pong".into(), "x.ping + 1".into())], ..Default::default() },
        OnSet { name: "pong".into(), cls: "Task".into(), prop: "pong".into(), when: "x.pong >= 0".into(), writes: vec![("x".into(), "ping".into(), "x.pong + 1".into())], ..Default::default() },
    ];
    let mut w = World::new(fs);
    classes(&mut w.c);
    mk(&mut w.c, "t9", "Task", json!({"status": "open"}));
    w.write("t9", "ping", json!(0));   // must return
    // refused at the revisit of (t9, ping), before applying: no numeric cap, a handful of writes
    assert!(w.fs.writes <= 6, "the loop was cut at the revisit ({} writes)", w.fs.writes);
    assert_eq!(w.prop("t9", "pong"), json!(1), "pong was written once");
    assert_eq!(w.prop("t9", "ping"), json!(0), "the revisit of the outside-written ping (ping=2) was refused, the person's write stands");
    assert_eq!(w.fs.refusals.len(), 1, "one refusal recorded: {:?}", w.fs.refusals);
    assert!(w.fs.refusals[0].to_string().contains("loop"), "with a reason: {:?}", w.fs.refusals);
    assert_eq!(w.fs.refusals[0]["prop"], json!("ping"), "refused at the revisit of (t9, ping): {:?}", w.fs.refusals);
    assert_eq!(w.prop("t9", "refused").to_string().contains("loop"), true, "recorded as a write on the entity");
    assert_eq!(w.prop("t9", "side"), json!(1), "unrelated work in the same round still ran");
    w.write("t9", "status", json!("done"));
    assert_eq!(w.prop("t9", "status"), json!("done"));
}

// gap 4: cold start of a new world runs effects once
#[test]
fn cold_start_runs_effects_once() {
    let mut w = cold();
    assert_eq!(w.fs.calls.len(), 3, "{:?}", w.fs.calls);
    let mut seen: Vec<String> = w.fs.calls.iter().map(|v| v.to_string()).collect();
    seen.sort(); seen.dedup();
    assert_eq!(seen.len(), 3, "no effect twice: {:?}", w.fs.calls);
    assert_eq!(w.prop("t3", "status"), json!("owed"));
    assert_eq!(w.prop("t2", "status"), json!("done"));
    let (rows, n) = (snapshot(&w.c), w.fs.calls.len());
    w.write("w1", "status", json!("done"));
    assert_eq!((snapshot(&w.c), w.fs.calls.len()), (rows, n));
}

// gap 5: warm restart runs none; the caches saved with the store load back
#[test]
fn warm_restart_runs_no_effects_and_loads_the_saved_caches() {
    let a = cold();
    let saved: Vec<(String, String, Map<String, J>)> = a.c.docs.iter().map(|(k, e)| (k.clone(), e.cls.clone(), e.props.clone())).collect();
    assert!(saved.iter().any(|(k, _, p)| k == "t3" && p.get("open_blockers") == Some(&json!(0))), "the cache is stored");
    let mut b = World::new(Fields::goalgraph());
    for (k, cls, p) in saved { b.c.load(k, cls, p, None, 0.0); }
    assert_eq!(snapshot(&b.c), snapshot(&a.c), "caches came back as saved");
    for t in [100.0, 200.0] { b.now = t; b.fs.tick(&mut b.c, &H, t, &mut b.seq); }
    assert_eq!(b.fs.writes, 0, "a warm restart wrote");
    assert!(b.fs.calls.is_empty() && b.fs.method_calls.is_empty(), "a warm restart ran an effect");
    assert_eq!(snapshot(&b.c), snapshot(&a.c));
    // and it carries on from the loaded caches: a real input moves only what it should
    b.write("t3", "status", json!("running"));
    assert_eq!(b.prop("t3", "open_blockers"), json!(0));
    assert_eq!(b.prop("t3", "status"), json!("running"));
    assert!(b.fs.calls.is_empty(), "{:?}", b.fs.calls);
}

// gap 1: the getter does not drift under dropped or doubled input writes
#[test]
fn open_blockers_does_not_drift_under_dropped_or_doubled_writes() {
    let mut w = World::new(Fields::goalgraph());
    classes(&mut w.c);
    mk(&mut w.c, "a", "Task", json!({"status": "open", "open_blockers": 0, "blocks": ["x"]}));
    mk(&mut w.c, "b", "Task", json!({"status": "open", "open_blockers": 0, "blocks": ["x"]}));
    mk(&mut w.c, "x", "Task", json!({"status": "open", "open_blockers": 2}));
    w.write("a", "status", json!("done"));
    w.write("a", "status", json!("done"));
    assert_eq!(w.prop("x", "open_blockers"), json!(1), "doubled write drifted");
    assert_eq!(w.prop("x", "status"), json!("open"));
    w.write("a", "status", json!("done"));
    assert_eq!(w.prop("x", "open_blockers"), json!(1));
    w.write("b", "status", json!("done"));
    w.write("b", "status", json!("done"));
    assert_eq!(w.prop("x", "open_blockers"), json!(0));
    assert_eq!(w.prop("x", "status"), json!("owed"));
    w.write("a", "status", json!("open"));
    assert_eq!(w.prop("x", "open_blockers"), json!(1));
    w.write("a", "status", json!("done"));
    assert_eq!(w.prop("x", "open_blockers"), json!(0));
}

// rule 8: a getter whose input changes twice in one round ends on the last value; getters are never refused as a loop
#[test]
fn a_getter_recalculated_twice_in_a_round_is_not_refused() {
    let mut fs = Fields::goalgraph();
    // a task that is done gets archived in the same round (archived is not finished: t7 is blocked again)
    fs.setters.push(OnSet { name: "archive".into(), cls: "Task".into(), prop: "status".into(), when: "x.status == 'done'".into(), writes: vec![("x".into(), "status".into(), "'archived'".into())], ..Default::default() });
    let mut w = World::new(fs);
    classes(&mut w.c);
    mk(&mut w.c, "g1", "Goal", json!({"status": "open"}));
    mk(&mut w.c, "t8", "Task", json!({"goal": "g1", "status": "running", "open_blockers": 0, "blocks": ["t7"]}));
    mk(&mut w.c, "d9", "Decision", json!({"goal": "g1", "status": "open", "blocks": ["t7"]}));
    mk(&mut w.c, "t7", "Task", json!({"goal": "g1", "status": "open", "open_blockers": 2}));
    w.write("t8", "status", json!("done"));
    assert_eq!(w.prop("t8", "status"), json!("archived"));
    assert_eq!(w.prop("t7", "open_blockers"), json!(2), "t8 archived is not finished: both blockers open");
    assert!(w.fs.refusals.is_empty(), "no refusal: {:?}", w.fs.refusals);
}

// syntax pass: WHEN / ELSE WHEN / ELSE run the first true branch, only it; a script still written with `x` runs
#[test]
fn else_branches_run_the_first_true_one_only() {
    let mut fs = Fields::goalgraph();
    fs.setters = membrane_vm::syntax::parse("
Task.level on-setter grade:
  WHEN this.level >= 10: this.grade = 'high'
  ELSE WHEN this.level >= 5: this.grade = 'mid'
  ELSE: this.grade = 'low'
Task.old on-setter legacy:
  WHEN x.old == 1: x.legacy = x.old + 1
").unwrap();
    let mut w = World::new(fs);
    classes(&mut w.c);
    mk(&mut w.c, "t9", "Task", json!({"status": "open"}));
    for (lvl, want) in [(12, "high"), (7, "mid"), (1, "low"), (10, "high")] {
        w.write("t9", "level", json!(lvl));
        assert_eq!(w.prop("t9", "grade"), json!(want), "level {}", lvl);
    }
    w.write("t9", "old", json!(1));
    assert_eq!(w.prop("t9", "legacy"), json!(2), "an `x` script still runs");
}

// Memo's review of #1024: a WHEN whose condition errors stops the setter (nothing written, a receipt left);
// it never falls through to ELSE
#[test]
fn an_erroring_condition_stops_the_setter_and_never_runs_else() {
    let mut fs = Fields::goalgraph();
    fs.setters = membrane_vm::syntax::parse("
Task.level on-setter typo:
  WHEN nope == 1: this.a = 1
  ELSE: this.note = 'x'
Task.level on-setter broken:
  WHEN this.level >>> 3: this.b = 1
  ELSE: this.note2 = 'x'
Task.level on-setter fine:
  WHEN this.level > 0: this.ok = 1
").unwrap();
    let mut w = World::new(fs);
    classes(&mut w.c);
    mk(&mut w.c, "t9", "Task", json!({"status": "open"}));
    w.write("t9", "level", json!(2));
    assert_eq!(w.prop("t9", "note"), J::Null, "ELSE did not run on an error");
    assert_eq!(w.prop("t9", "note2"), J::Null, "ELSE did not run on a parse error");
    assert_eq!(w.prop("t9", "a"), J::Null);
    assert_eq!(w.prop("t9", "ok"), json!(1), "a later, working setter still ran");
    let errs: Vec<_> = w.fs.refusals.iter().filter(|r| r["reason"].as_str().unwrap_or("").starts_with("error")).collect();
    assert_eq!(errs.len(), 2, "one receipt per broken setter: {:?}", w.fs.refusals);
    assert!(w.prop("t9", "refused").as_str().unwrap_or("").contains("error"), "recorded as a write on the entity");
}

// the replay of the live goal graph (2026-11-09): a status write recounted every task against every blocker
// (~400 ms per write on 1,073 entities). It recounts only what the blocker blocks.
#[test]
fn a_status_write_recounts_only_what_the_blocker_blocks() {
    let mut w = World::new(Fields::goalgraph());
    classes(&mut w.c);
    mk(&mut w.c, "g1", "Goal", json!({"status": "open"}));
    for i in 0..1500 {
        mk(&mut w.c, &format!("t{}", i), "Task", json!({"goal": "g1", "status": "open", "open_blockers": 1, "blocks": [format!("t{}", i + 1)]}));
    }
    w.write("t500", "status", json!("running"));     // first contact: the getters learn their inputs (not timed)
    let t = std::time::Instant::now();
    w.write("t10", "status", json!("done"));
    let took = t.elapsed();
    assert_eq!(w.prop("t11", "open_blockers"), json!(0));
    assert_eq!(w.prop("t11", "status"), json!("owed"));
    assert_eq!(w.prop("t12", "open_blockers"), json!(1), "untouched");
    assert!(took < std::time::Duration::from_millis(200), "one status write took {:?}", took);
}

// imports: a pack writes its own or imported classes (or ones no pack owns); it creates `<Class>:<key>` entities of
// those classes; writing another pack's class without importing it is refused with a receipt
#[test]
fn a_pack_writes_and_creates_only_in_classes_it_owns_or_imports() {
    let mut fs = Fields::goalgraph();
    fs.set_pack("dom", "PACK dom 1\nDomNode.html getter: this.text\n").unwrap();
    fs.set_pack("inflight", "PACK inflight 1\nIMPORT goalgraph 1\nIMPORT dom 1\nTask.status on-setter row:\n  ALWAYS: ('DomNode:inflight:' + this.eid).text = this.status\n").unwrap();
    fs.set_pack("lamps", "PACK lamps 1\nLamp.power on-setter meddle:\n  ALWAYS: this.target.note = 'touched'\n").unwrap();
    let mut w = World::new(fs);
    classes(&mut w.c);
    mk(&mut w.c, "t9", "Task", json!({"status": "open"}));
    mk(&mut w.c, "lamp1", "Lamp", json!({"power": "off", "target": "t9"}));
    w.write("t9", "status", json!("running"));
    assert_eq!(w.prop("DomNode:inflight:t9", "text"), json!("running"), "created as a DomNode, through the import");
    assert_eq!(w.c.docs["DomNode:inflight:t9"].cls, "DomNode");
    assert_eq!(w.prop("DomNode:inflight:t9", "html"), json!("running"), "its own getters run on it");
    assert!(w.fs.out.iter().any(|o| o["born"] == json!("DomNode")), "the host is told to create it");
    w.write("lamp1", "power", json!("on"));
    assert_eq!(w.prop("t9", "note"), J::Null, "lamps doesn't import goalgraph: no write to a Task");
    assert!(w.fs.refusals.iter().any(|r| r["reason"].as_str().unwrap_or("").contains("pack lamps can't write Task")), "{:?}", w.fs.refusals);
}
