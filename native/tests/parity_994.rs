//! #994 row 1: the parity gate. Replay one journal on the native VM under (a) today's triggers
//! (WHEN/WHERE/WRITE) and (b) the fields form, then checksum the WHOLE field (every entity's class,
//! stored props and position, sorted) and compare. Rust only: no Python, no membrane/ch.py.
//! The fields side does not exist in the VM yet (migration row 2 builds it); until then it is the
//! `Runner::Fields` arm, and the comparison test is #[ignore]d with that reason. Today-vs-today,
//! and the checksum's own sensitivity, run now so the gate itself is proven.
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

enum Runner { Today, Fields }

fn run(r: Runner) -> Core {
    let mut c = Core::new();
    match r {
        Runner::Today => c.set_triggers(today()),
        Runner::Fields => unimplemented!("fields on the native VM: migration row 2 (#994)"),
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
                Step::Idle | Step::Done(_) => break,
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
    let mut rows: Vec<String> = c.docs.iter().map(|(eid, e)| {
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

#[test]
#[ignore = "fields do not exist on the native VM yet (#994 migration row 2)"]
fn fields_match_today_on_the_971_journal() {
    assert_eq!(checksum(&run(Runner::Today)), checksum(&run(Runner::Fields)));
}
