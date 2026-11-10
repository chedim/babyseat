//! #994 row 1: the parity gate. Replay one journal on the native VM under (a) today's triggers
//! (WHEN/WHERE/WRITE) and (b) the fields form, then checksum the WHOLE field (every entity's class,
//! stored props and position, sorted) and compare. Rust only: no Python, no membrane/ch.py.
//! Row 2 built the fields side (native/src/fields.rs); the gg_* tests below replay a goalgraph journal
//! (tasks, a card, a wait, a goal) through today's 12 triggers and through the fields, and compare.
//! Today-vs-today and the checksum's own sensitivity prove the gate itself.
use membrane_vm::fields::Fields;
use membrane_vm::vm::{Core, Host, Step, Trig, Write, Spec};
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

/// The journal: (eid, prop, new, writer). Births come first as loads.
fn journal() -> Vec<(&'static str, &'static str, J, &'static str)> {
    vec![
        ("t:a", "status", json!("open"), "person:ann"),
        ("t:b", "status", json!("open"), "person:ann"),
        ("t:a", "status", json!("done"), "person:ann"),
        ("t:b", "n", json!(3), "person:ann"),
        ("t:a", "status", json!("open"), "person:ann"),
        ("t:a", "status", json!("done"), "person:ann"),
    ]
}

fn today() -> Vec<Trig> {
    let spec = Spec {
        when: vec![vec![json!("changed"), json!("?x.status")]],
        wh: vec![vec![json!("isa"), json!("?x"), json!("Task")]],
        target: "?x.last".into(),
        expr: "x.status".into(),
    };
    vec![Trig { eid: "trig:seen".into(), spec: Some(spec), mine: false }]
}

enum Runner { Today }

fn run(r: Runner) -> Core {
    let mut c = Core::new();
    match r {
        Runner::Today => c.set_triggers(today()),
    }
    c.load("cls:task".into(), "Class".into(), Default::default(), None, 0.0);
    for e in ["t:a", "t:b"] { let mut p = serde_json::Map::new(); p.insert("parents".into(), json!(["cls:task"])); c.load(e.into(), "Task".into(), p, None, 0.0); }
    let mut now = 1.0;
    let mut seq = 0i64;
    for (eid, prop, new, who) in journal() {
        seq += 1; now += 1.0;
        let w = Write { eid: eid.into(), prop: prop.into(), new, old: J::Null, writer: who.into(), chain: json!({}), seq };
        c.hear(w, None, now);
        loop {
            match c.step(&H, now, &json!({"id": "x", "root": "x"})) {
                Step::Idle => break,
                Step::Done(_) => continue,
                Step::Fire(f) => {
                    seq += 1;
                    let w = Write { eid: f["eid"].as_str().unwrap().trim_start_matches('?').into(),
                        prop: f["prop"].as_str().unwrap().into(), new: f["value"].clone(), old: J::Null,
                        writer: format!("trigger:{}", f["trigger"].as_str().unwrap_or("")), chain: f["chain"].clone(), seq };
                    c.hear(w, None, now);
                    c.fired_ok(f["trigger"].as_str().unwrap_or(""));
                }
            }
        }
    }
    c
}

/// FNV-1a over the sorted whole field: eid, class, each prop sorted, position.
fn checksum(c: &Core) -> (u64, Vec<String>) {
    let mut rows: Vec<String> = c.docs.iter().filter(|(eid, _)| *eid != "membrane::llm").map(|(eid, e)| {
        let mut ps: Vec<String> = e.props.iter().map(|(k, v)| format!("{}={}", k, v)).collect();
        ps.sort();
        format!("{}|{}|{}|{:?}", eid, e.cls, ps.join(","), e.pos)
    }).collect();
    rows.sort();
    let mut h: u64 = 0xcbf29ce484222325;
    for b in rows.join("\n").bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
    (h, rows)
}

#[test]
fn today_is_deterministic_and_the_trigger_fired() {
    let c0 = run(Runner::Today); let (a, rows) = checksum(&c0);
    let (b, _) = checksum(&run(Runner::Today));
    assert_eq!(a, b);
    println!("fired {:?} errors {:?} refused {:?}", c0.fired, c0.errors, c0.refused);
    println!("field rows:\n{}\nchecksum {:016x}", rows.join("\n"), a);
    assert!(rows.iter().any(|r| r.starts_with("t:a|Task|") && r.contains("last=\"done\"")), "{:?}", rows);
}

#[test]
fn the_checksum_sees_a_changed_field() {
    let mut c = run(Runner::Today);
    let (a, _) = checksum(&c);
    c.hear(Write { eid: "t:b".into(), prop: "n".into(), new: json!(4), old: J::Null, writer: "person:ann".into(), chain: json!({}), seq: 99 }, None, 99.0);
    assert_ne!(a, checksum(&c).0);
}


// ---- row 2: goalgraph's 12 triggers, today vs fields -------------------------------------------
fn spec(when: J, wh: J, target: &str, expr: &str) -> Spec {
    let v = |j: J| j.as_array().unwrap().iter().map(|a| a.as_array().unwrap().clone()).collect::<Vec<_>>();
    Spec { when: v(when), wh: v(wh), target: target.into(), expr: expr.into() }
}

/// goalgraph.TRIGGERS, verbatim (membrane/goalgraph.py), as the VM's specs.
fn gg_today() -> Vec<Trig> {
    let blk = |b: &str, bs: &str, t: &str, ts: &str, tgt: &str, e: &str, n: &str| (n.to_string(), spec(
        json!([["changed", format!("?b.status")]]),
        json!([["isa", "?b", b], ["isa", format!("?{}", t), ts],
               ["filter", format!("b.status == '{}' and {}.status == 'open' and {}.eid in (b.blocks or [])", bs, t, t)]]), tgt, e));
    let mut v = vec![
        blk("Task", "done", "t", "Task", "?t.open_blockers", "t.open_blockers - 1", "unblock-task"),
        blk("Decision", "closed", "t", "Task", "?t.open_blockers", "t.open_blockers - 1", "unblock-card"),
        ("ready".to_string(), spec(json!([["changed", "?t.open_blockers"], ["below", "t.open_blockers", 0.5]]),
            json!([["isa", "?t", "Task"], ["filter", "t.status == 'open'"]]), "?t.status", "'owed'")),
        ("replan-task".to_string(), spec(json!([["changed", "?t.status"]]),
            json!([["isa", "?t", "Task"], ["isa", "?g", "Goal"], ["filter", "g.eid == t.goal and (t.status == 'done' or t.status == 'failed' or t.status == 'stuck')"]]),
            "?g.replan", "'task ' + t.eid + ' ' + t.status")),
        ("replan-card".to_string(), spec(json!([["changed", "?d.status"]]),
            json!([["isa", "?d", "Decision"], ["isa", "?g", "Goal"], ["filter", "g.eid == d.goal and (d.status == 'closed' or d.status == 'dropped')"]]),
            "?g.replan", "'card ' + d.eid + ' ' + d.status")),
        blk("Wait", "done", "t", "Task", "?t.open_blockers", "t.open_blockers - 1", "unblock-wait"),
        blk("Task", "done", "w", "Wait", "?w.open_blockers", "w.open_blockers - 1", "wait-follows-task"),
        blk("Decision", "closed", "w", "Wait", "?w.open_blockers", "w.open_blockers - 1", "wait-follows-card"),
        ("wait-armed".to_string(), spec(json!([["changed", "?w.open_blockers"], ["below", "w.open_blockers", 0.5]]),
            json!([["isa", "?w", "Wait"], ["filter", "w.status == 'open'"]]), "?w.status", "'armed'")),
        ("wait-check".to_string(), spec(json!([["every", 10]]),
            json!([["isa", "?w", "Wait"], ["filter", "w.status == 'armed'"]]), "?w.check", "'due'")),
        ("plan".to_string(), spec(json!([["changed", "?g.replan"]]),
            json!([["isa", "?g", "Goal"], ["filter", "(g.status or 'open') == 'open' and g.replan"]]),
            "membrane::llm.call", "{'about': g.eid, 'prompt': 'plan the goal: ' + g.replan, 'lane': 'planner'}")),
        ("plan-after-reopen".to_string(), spec(json!([["changed", "?g.status"]]),
            json!([["isa", "?g", "Goal"], ["filter", "g.status == 'open' and g.replan and g.planned_ts and g.replan != g.planned_replan"]]),
            "membrane::llm.call", "{'about': g.eid, 'prompt': 'plan the goal: ' + g.replan, 'lane': 'planner'}")),
    ];
    v.drain(..).map(|(n, sp)| Trig { eid: format!("trig:{}", n), spec: Some(sp), mine: false }).collect()
}

fn gg_births(c: &mut Core) {
    let mk = |c: &mut Core, eid: &str, cls: &str, props: J| {
        let mut p = props.as_object().unwrap().clone();
        p.insert("parents".into(), json!([format!("cls:{}", cls.to_lowercase())]));
        c.load(eid.into(), cls.into(), p, None, 0.0);
    };
    for k in ["task", "decision", "wait", "goal"] { c.load(format!("cls:{}", k), "Class".into(), Default::default(), None, 0.0); }
    c.load("membrane::llm".into(), "Other".into(), Default::default(), None, 0.0);
    mk(c, "g1", "Goal", json!({"status": "open"}));
    mk(c, "t1", "Task", json!({"goal": "g1", "status": "owed", "open_blockers": 0, "blocks": ["t2", "w1"]}));
    mk(c, "t2", "Task", json!({"goal": "g1", "status": "open", "open_blockers": 2}));
    mk(c, "d1", "Decision", json!({"goal": "g1", "status": "open", "blocks": ["t2"]}));
    mk(c, "w1", "Wait", json!({"goal": "g1", "status": "open", "open_blockers": 1, "every_s": 10, "blocks": ["t3"]}));
    mk(c, "t3", "Task", json!({"goal": "g1", "status": "open", "open_blockers": 1}));
}

fn gg_journal() -> Vec<(&'static str, &'static str, J)> {
    vec![
        ("t1", "status", json!("running")),
        ("t1", "status", json!("done")),            // unblocks t2 (2->1) and w1 (1->0: armed); replans g1
        ("d1", "status", json!("closed")),          // unblocks t2 (1->0: owed); replans g1
        ("g1", "planned_ts", json!(5.0)),
        ("g1", "planned_replan", json!("card d1 closed")),
        ("g1", "status", json!("closed")),
        ("t2", "status", json!("running")),
        ("t2", "status", json!("done")),            // g1 is closed: the replan lands, no plan call
        ("g1", "status", json!("open")),            // reopened with an unplanned replan: plans
        ("w1", "status", json!("done")),            // unblocks t3 (1->0: owed)
    ]
}

struct Out { c: Core, calls: Vec<String> }

fn gg_run(fields: bool) -> Out {
    let mut c = Core::new();
    let mut fs = Fields::goalgraph();
    if !fields { c.set_triggers(gg_today()); }
    gg_births(&mut c);
    let mut now = 1.0;
    let mut seq = 0i64;
    let mut calls: Vec<String> = vec![];
    let mut drain = |c: &mut Core, fs: &mut Fields, now: f64, seq: &mut i64, calls: &mut Vec<String>| {
        if fields { return; }
        loop {
            match c.step(&H, now, &json!({"id": "x", "root": "x"})) {
                Step::Idle => break,
                Step::Done(_) => continue,
                Step::Fire(f) => {
                    *seq += 1;
                    let (eid, prop) = (f["eid"].as_str().unwrap().trim_start_matches('?').to_string(), f["prop"].as_str().unwrap().to_string());
                    if eid == "membrane::llm" { calls.push(f["value"].to_string()); }
                    let w = Write { eid, prop, new: f["value"].clone(), old: J::Null, writer: format!("trigger:{}", f["trigger"].as_str().unwrap_or("")), chain: f["chain"].clone(), seq: *seq };
                    c.hear(w, None, now);
                    c.fired_ok(f["trigger"].as_str().unwrap_or(""));
                }
            }
        }
        let _ = fs;
    };
    let jr = gg_journal();
    let last = jr.len() - 1;
    for (i, (eid, prop, new)) in jr.into_iter().enumerate() {
        if i == last {
            // the clock: w1 armed early on; long after, its check is due (every 10 s)
            for t in [20.0, 40.0] {
                now = t;
                if fields { fs.tick(&mut c, &H, now, &mut seq); } else { drain(&mut c, &mut fs, now, &mut seq, &mut calls); }
            }
        }
        seq += 1; now += 1.0;
        let w = Write { eid: eid.into(), prop: prop.into(), new, old: J::Null, writer: "person:ann".into(), chain: json!({}), seq };
        c.hear(w.clone(), None, now);
        if fields { fs.react(&mut c, &H, w, now, &mut seq); } else { drain(&mut c, &mut fs, now, &mut seq, &mut calls); }
    }
    if fields { calls = fs.calls.iter().map(|v| v.to_string()).collect(); }
    calls.sort();
    Out { c, calls }
}

/// The fields form adds bookkeeping props today's triggers never write in this harness (`armed_since`, `due`, `check`: the `every` clock is not swept by the test drain);
/// they are masked, and nothing else is.
fn gg_checksum(c: &Core) -> (u64, Vec<String>) {
    let mut rows: Vec<String> = c.docs.iter().filter(|(eid, _)| *eid != "membrane::llm").map(|(eid, e)| {
        // `finished` is a getter the fields declare (packs/goalgraph.c1f) that the triggers never had
        let mut ps: Vec<String> = e.props.iter().filter(|(k, _)| *k != "armed_since" && *k != "due" && *k != "check" && *k != "finished").map(|(k, v)| format!("{}={}", k, v)).collect();
        ps.sort();
        format!("{}|{}|{}|{:?}", eid, e.cls, ps.join(","), e.pos)
    }).collect();
    rows.sort();
    let mut h: u64 = 0xcbf29ce484222325;
    for b in rows.join("\n").bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
    (h, rows)
}

#[test]
fn gg_fields_match_the_12_triggers_on_the_journal() {
    let t = gg_run(false);
    let f = gg_run(true);
    let (a, ra) = gg_checksum(&t.c);
    let (b, rb) = gg_checksum(&f.c);
    println!("today  {:016x} calls {:?} fired {:?} errors {:?}\n{}", a, t.calls, t.c.fired, t.c.errors, ra.join("\n"));
    println!("fields {:016x} calls {:?}\n{}", b, f.calls, rb.join("\n"));
    assert_eq!(ra, rb);
    assert_eq!(a, b, "the whole-field checksum");
    assert_eq!(t.calls, f.calls, "the planner calls");
    assert_eq!(t.calls.len(), 3, "{:?}", t.calls);
    // the journal exercised the rules (not a vacuous equality)
    for want in ["t2|Task|", "t3|Task|", "w1|Wait|"] { assert!(ra.iter().any(|r| r.starts_with(want)), "{}", want); }
    assert!(ra.iter().any(|r| r.starts_with("t3|") && r.contains("status=\"owed\"")));
    assert!(f.c.docs["w1"].props.get("check") == Some(&json!("due")), "the wait-check field fires on the clock");
}

#[test]
fn gg_checksum_sees_a_missing_rule() {
    // drop the ready field: t2 and t3 never become owed -> the checksums must differ
    let t = gg_run(false);
    let mut fs = Fields::goalgraph();
    fs.setters.retain(|s| !s.name.starts_with("ready"));
    let mut c = Core::new();
    gg_births(&mut c);
    let mut seq = 0i64; let mut now = 1.0;
    let jr = gg_journal();
    let last = jr.len() - 1;
    for (i, (eid, prop, new)) in jr.into_iter().enumerate() {
        if i == last {
            // the clock: w1 armed early on; long after, its check is due (every 10 s); then the last write
            for t in [20.0, 40.0] {
                now = t;
                fs.tick(&mut c, &H, now, &mut seq);
            }
        }
        seq += 1; now += 1.0;
        let w = Write { eid: eid.into(), prop: prop.into(), new, old: J::Null, writer: "person:ann".into(), chain: json!({}), seq };
        c.hear(w.clone(), None, now);
        fs.react(&mut c, &H, w, now, &mut seq);
    }
    assert_ne!(gg_checksum(&t.c).0, gg_checksum(&c).0);
}
