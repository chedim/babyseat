//! Every entity remembers what birthed it (#1143, operator 2026-11-14): the reserved read-only property `born_by` is the eid of the CAUSE of the birth, the entity whose
//! setter was running (for a door `entity_write`, the Request of the call). A birth with no cause reads as the engine's plain "absent" null. It is a constant per entity,
//! stored with the row (survives eviction and restart) and, for a birth a replay does not recompute (a virtual trigger's), carried by the journal line's optional `by`.
use membrane_vm::host::{Host, Line, MemoryConfig, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("bornby-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn open(file: Option<&str>) -> Host { let mut o = Options::new(file.map(String::from)); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

const PACK: &str = "PACK bb 1
VIRTUAL Gone
Box.go on-setter make:
  ALWAYS:
    Thing:t1 = Thing(n: 1)
    Thing:t1.go = 1
Thing.go on-setter chain:
  ALWAYS:
    Other:o1 = Other(k: 1)
Gone.go on-setter vmake:
  ALWAYS:
    Thing:v1 = Thing(n: 2)
Box.ask on-setter ask:
  ALWAYS:
    this.by_t1 = Thing:t1.born_by
    this.by_o1 = Other:o1.born_by
    this.by_box = Box:a.born_by == None
Box.poke on-setter poke:
  ALWAYS:
    Thing:t1.born_by = 'Box:evil'
Box.poke2 on-setter poke2:
  ALWAYS:
    Thing:t1.n = 7
Other.shown getter: this.eid
Thing.who getter: this.born_by
";

fn seat(file: Option<&str>) -> Host {
    let h = open(file);
    h.load_pack("bb", PACK).unwrap();
    h.rebuild();
    h
}
fn made(h: &Host) {
    h.born("Box:a", "Box", props(json!({})), "person");
    h.write("Box:a", "go", json!(1), "person");
}

#[test]
fn a_setter_that_creates_an_entity_is_its_cause_and_a_cascade_names_the_setters_entity() {
    let h = seat(None);
    made(&h);
    assert_eq!(h.born_by("Thing:t1"), Some("Box:a".into()), "Box:a's setter made Thing:t1");
    assert_eq!(h.born_by("Other:o1"), Some("Thing:t1".into()), "the chain's step that did the birth: Thing:t1's setter, not the first trigger");
    assert_eq!(h.born_by("Box:a"), None, "a birth from outside has no cause");
    h.write("Box:a", "ask", json!(1), "person");
    assert_eq!(h.get("Box:a", "by_t1"), json!("Box:a"), "a plain read in a setter");
    assert_eq!(h.get("Box:a", "by_o1"), json!("Thing:t1"));
    assert_eq!(h.get("Box:a", "by_box"), json!(true), "no cause: the engine's null (== None)");
}

#[test]
fn it_is_a_constant_a_getter_reading_it_has_a_value_and_is_never_invalidated_by_it() {
    let h = seat(None);
    made(&h);
    assert_eq!(h.get("Thing:t1", "who"), json!("Box:a"));
    h.write("Thing:t1", "n", json!(5), "person");
    h.write("Box:a", "go", json!(2), "person");   // the creation is idempotent: the entity exists, the cause stays
    assert_eq!(h.born_by("Thing:t1"), Some("Box:a".into()));
    assert_eq!(h.get("Thing:t1", "who"), json!("Box:a"));
}

#[test]
fn a_write_to_it_is_refused_loudly_by_a_setter_and_dropped_unjournalled_from_outside() {
    let h = seat(None);
    made(&h);
    h.write("Box:a", "poke", json!(1), "person");
    let r = h.get("Box:a", "refused").as_str().unwrap_or("").to_string();
    assert!(r.contains("born_by") && r.contains("read-only"), "a receipt: {r:?}");
    assert_eq!(h.born_by("Thing:t1"), Some("Box:a".into()));
    let before = h.journal().len();
    h.write("Thing:t1", "born_by", json!("Box:evil"), "person");
    assert_eq!(h.journal().len(), before, "not journalled");
    assert_eq!(h.born_by("Thing:t1"), Some("Box:a".into()));
    assert_eq!(h.get("Thing:t1", "born_by"), J::Null, "and no property of that name is stored");
}

#[test]
fn it_is_in_the_row_and_survives_a_restart() {
    let f = tmpfile("restart");
    { let h = seat(Some(&f)); made(&h); assert_eq!(h.rows()["Thing:t1"]["born_by"], json!("Box:a"), "one string in the row"); assert!(h.rows()["Box:a"].get("born_by").is_none(), "no cause: no key"); }
    let h = seat(Some(&f));
    h.load_stored_entities();
    assert_eq!(h.born_by("Thing:t1"), Some("Box:a".into()));
    assert_eq!(h.born_by("Other:o1"), Some("Thing:t1".into()));
    assert_eq!(h.born_by("Box:a"), None);
}

#[test]
fn it_survives_the_vms_own_eviction() {
    let f = tmpfile("evict");
    let h = seat(Some(&f));
    let rss = Arc::new(AtomicU64::new(0));
    let r2 = rss.clone();
    h.set_rss_reading(Box::new(move || Some(r2.load(Ordering::SeqCst))));
    h.set_memory(MemoryConfig { budget_mb: Some(100), soft: Some(0.6), hard: Some(0.9), batch: Some(50) });
    made(&h);
    rss.store(200 << 20, Ordering::SeqCst);
    h.tick(); h.pump();
    assert!(h.unloaded_count() > 0, "the engine let entities go");
    assert!(!h.snapshot().contains_key("Thing:t1"), "Thing:t1 is out of the engine");
    rss.store(0, Ordering::SeqCst);   // the pressure is over: what loads now stays
    assert_eq!(h.born_by("Thing:t1"), Some("Box:a".into()), "it loads again with its cause");
}

#[test]
fn a_replay_gives_the_same_causes_engine_births_recomputed_and_outside_lines_by_their_by() {
    let a = seat(None);
    made(&a);
    a.hold("Gone:g");   // a virtual entity nothing holds is collected
    a.born("Gone:g", "Gone", props(json!({})), "person");
    a.write("Gone:g", "go", json!(1), "person");   // a virtual trigger's birth of a stored entity: journalled as an outside birth
    assert_eq!(a.born_by("Thing:v1"), Some("Gone:g".into()));
    let j = a.journal();
    let line = j.iter().find(|l| matches!(l, Line::Born { eid, .. } if eid == "Thing:v1")).expect("the virtual trigger's birth is journalled");
    assert!(matches!(line, Line::Born { by: Some(b), .. } if b == "Gone:g"), "the line says by: {line:?}");
    assert_eq!(line.to_json()["by"], json!("Gone:g"));
    let b = seat(None);
    b.replay(&j);
    b.pump();
    for e in ["Thing:t1", "Other:o1", "Box:a", "Thing:v1"] { assert_eq!(b.born_by(e), a.born_by(e), "{e}: {:?}", b.journal()); }
    // an OLD journal line (no `by`) still reads: no cause
    let old = Line::from_json(&json!({"op": "born", "ts": 1.0, "writer": "host:door", "eid": "Box:z", "cls": "Box", "props": {}})).unwrap();
    assert!(matches!(old, Line::Born { by: None, .. }));
    assert!(old.to_json().get("by").is_none(), "and writes no key when there is none");
}

#[test]
fn a_setter_entity_that_names_where_its_births_are_charged_is_not_the_cause_that_entity_is() {
    // the VM rule: when the entity whose setter runs has a `births_to` holding an entity id, what the setter creates says THAT entity in `born_by`; else the setter's own entity
    let f = tmpfile("chargeto");
    let a = seat(Some(&f));
    a.hold("Gone:g");
    a.born("Box:a", "Box", props(json!({"births_to": "Msg:1"})), "person");
    a.write("Box:a", "go", json!(1), "person");
    a.born("Gone:g", "Gone", props(json!({"births_to": "Msg:2"})), "person");
    a.write("Gone:g", "go", json!(1), "person");
    a.born("Box:b", "Box", props(json!({})), "person");
    a.write("Box:b", "go", json!(1), "person");   // no births_to: the old rule (Thing:t1 exists already, so b creates nothing new; the cascade's Other:o1 too)
    assert_eq!(a.born_by("Thing:t1"), Some("Msg:1".into()));
    assert_eq!(a.born_by("Other:o1"), Some("Thing:t1".into()), "the next step's setter entity has no births_to: itself");
    assert_eq!(a.born_by("Thing:v1"), Some("Msg:2".into()), "a virtual trigger's birth too");
    let line = a.journal().into_iter().find(|l| matches!(l, Line::Born { eid, .. } if eid == "Thing:v1")).unwrap();
    assert!(matches!(&line, Line::Born { by: Some(b), .. } if b == "Msg:2"), "{line:?}");
    // a replay gives the same causes
    let b = seat(None);
    b.replay(&a.journal());
    b.pump();
    for e in ["Thing:t1", "Other:o1", "Thing:v1"] { assert_eq!(b.born_by(e), a.born_by(e), "{e}"); }
    drop(a);
    // and a restart keeps them
    let r = seat(Some(&f));
    r.load_stored_entities();
    assert_eq!(r.born_by("Thing:t1"), Some("Msg:1".into()));
    assert_eq!(r.born_by("Thing:v1"), Some("Msg:2".into()), "a virtual trigger's birth is a stored row: kept too");
}
