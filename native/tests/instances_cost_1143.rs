//! `instances('<Class>')` costs one pass over the loaded entities (#1143, 2026-11-14 measurement of the session route): the pass must not allocate a class list per entity.
//! Found on an imported store: a trivial request evaluated eight getters of `instances('Boot')` and each took about 5 ms with 30,000 loaded entities (the messages and their
//! children), so a request for ONE row cost 50-60 ms and a session fetched row by row 45-64 ms a row. The bound below is a TEST bound between "a compare per entity" and
//! "a Vec<String> built per entity" (not a product constant).
use membrane_vm::host::{Host, Options};
use serde_json::json;
use std::time::Instant;

fn host(pack: &str) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("sem", pack).unwrap_or_else(|e| panic!("pack refused: {e}"));
    h.rebuild();
    h
}

#[test]
fn instances_of_a_class_does_not_build_a_class_list_per_loaded_entity() {
    let h = host("PACK sem 1\nProbe.n getter: len(instances('Boot'))\nProbe.m getter: len(instances('Chat'))\n");
    h.born("Probe:a", "Probe", Default::default(), "person");
    for i in 0..20_000 {
        // the shape of a stored message's children: a parents list of two kinds, never the class asked for in the first place
        h.born(&format!("Chat:message:{i}:turn:tool:{i}"), "Chat", json!({"parents": ["cls:chat", "cls:chatmessage"], "x": i}).as_object().unwrap().clone(), "person");
    }
    h.pump();
    h.born("Boot:one", "Boot", json!({}).as_object().unwrap().clone(), "person");
    h.pump();
    assert_eq!(h.get("Probe:a", "n"), json!(1));
    // each new Boot changes the class list read by `Probe.n`: one more pass over the loaded entities
    let t = Instant::now();
    for i in 0..30 {
        h.born(&format!("Boot:b{i}"), "Boot", json!({}).as_object().unwrap().clone(), "person");
        h.pump();
    }
    let per_pass_ms = t.elapsed().as_secs_f64() * 1000.0 / 30.0;
    eprintln!("per pass over 20,000 entities: {per_pass_ms:.2} ms");
    assert_eq!(h.get("Probe:a", "n"), json!(31));
    assert!(per_pass_ms < 6.0, "one instances() pass over 20,000 entities took {per_pass_ms:.2} ms (a class list was built per entity)");
}

/// The allocation-free class test keeps the old answers: the eid class when there is no `parents`, a kind by its last colon segment (any case), a single string, an entry with no colon.
#[test]
fn the_class_test_keeps_its_answers() {
    let h = host("PACK sem 1\nProbe.a getter: len(instances('Thing'))\nProbe.b getter: len(instances('Kind'))\nProbe.c getter: len(instances('Ind'))\n");
    h.born("Thing:plain", "Thing", Default::default(), "person");
    h.born("Other:listed", "Other", json!({"parents": ["cls:other", "cls:KIND"]}).as_object().unwrap().clone(), "person");
    h.born("Other:one", "Other", json!({"parents": "ns:cls:kind"}).as_object().unwrap().clone(), "person");
    h.born("Other:bare", "Other", json!({"parents": ["kind", "x:thing"]}).as_object().unwrap().clone(), "person");
    h.born("Other:empty", "Other", json!({"parents": []}).as_object().unwrap().clone(), "person");
    h.born("Probe:a", "Probe", Default::default(), "person");   // last: a getter hears of a new entity by its eid class, so it is born after the others
    h.pump();
    assert_eq!(h.get("Probe:a", "a"), json!(2), "Thing:plain by its eid class, Other:bare by `x:thing`");
    assert_eq!(h.get("Probe:a", "b"), json!(3), "listed (any case), a single string, a bare entry");
    assert_eq!(h.get("Probe:a", "c"), json!(0), "a suffix of a name is not the name");
}

/// The per-class answers are kept between asks: a `parents` write makes the next ask see the change (the getter also reads the entity's `parents`, so it is asked again).
#[test]
fn a_kept_class_answer_follows_a_parents_write() {
    let h = host("PACK sem 1\nProbe.k getter: len(instances('Kind')) * 10 + len(instances('Other')) + len(prop('Other:x', 'parents') or [])\n");
    h.born("Other:x", "Other", json!({"parents": ["cls:other"]}).as_object().unwrap().clone(), "person");
    h.born("Probe:a", "Probe", Default::default(), "person");
    h.pump();
    let base = h.get("Probe:a", "k").as_i64().unwrap();
    h.write("Other:x", "parents", json!(["cls:other", "cls:kind"]), "person");
    h.pump();
    let after = h.get("Probe:a", "k").as_i64().unwrap();
    assert_eq!(after - base, 10 + 1, "Other:x is a Kind now (10) and its parents list is one longer (1): {base} -> {after}");
}
