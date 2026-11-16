//! `instances('<Class>')` costs one pass over the loaded entities, and only when something it can hold changed (#1143, 2026-11-14 measurement of the session route). The tests count walks, never milliseconds.
//! Found on an imported store: a trivial request evaluated eight getters of `instances('Boot')` and each took about 5 ms with 30,000 loaded entities (the messages and their
//! children), so a request for ONE row cost 50-60 ms and a session fetched row by row 45-64 ms a row. The bound below is a TEST bound between "a compare per entity" and
//! "a Vec<String> built per entity" (not a product constant).
use membrane_vm::host::{Host, Options};
use serde_json::json;

fn host(pack: &str) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("sem", pack).unwrap_or_else(|e| panic!("pack refused: {e}"));
    h.rebuild();
    h
}

/// Work, not clock: the walks over the loaded entities are counted (`Host::inst_scans`). A Boot arriving changes the answer of `instances('Boot')`: one walk. An entity of another class
/// arriving changes nothing it can be in: no walk, however many entities are loaded or arrive. (Before: any arrival dropped every kept answer, so a request that was born,
/// `Request`, made every `instances('Boot')` of the request walk the field again, 10 ms each at 27,000 entities: a 404 took 45 ms, 12 s on a seat with 400,000 entities and a poll queue.)
#[test]
fn an_arrival_of_another_class_does_not_walk_the_field_again() {
    let h = host("PACK sem 1\nProbe.n getter: len(instances('Boot')) + len(prop('Probe:a', 'tick') or [])\n");
    h.born("Probe:a", "Probe", Default::default(), "person");
    for i in 0..2_000 {
        h.born(&format!("Chat:message:{i}:turn:tool:{i}"), "Chat", json!({"parents": ["cls:chat", "cls:chatmessage"], "x": i}).as_object().unwrap().clone(), "person");
    }
    h.born("Boot:one", "Boot", json!({}).as_object().unwrap().clone(), "person");
    h.pump();
    assert_eq!(h.get("Probe:a", "n"), json!(1));
    let s0 = h.inst_scans();
    for i in 0..30 {
        // a request is born, then the getter is asked again (its input `tick` moved)
        h.born(&format!("Request:{i}"), "Request", json!({"route": "/x"}).as_object().unwrap().clone(), "person");
        h.write("Probe:a", "tick", json!(vec![0; i + 1]), "person");
        h.pump();
    }
    assert_eq!(h.get("Probe:a", "n"), json!(31), "the getter was asked again on every tick");
    assert_eq!(h.inst_scans() - s0, 0, "30 arrivals of a class `instances('Boot')` cannot hold walked the field");
    // a Boot arriving is the one thing that does change the answer: exactly one walk each
    let s1 = h.inst_scans();
    for i in 0..5 {
        h.born(&format!("Boot:b{i}"), "Boot", json!({}).as_object().unwrap().clone(), "person");
        h.pump();
    }
    assert_eq!(h.get("Probe:a", "n"), json!(36));
    assert_eq!(h.inst_scans() - s1, 5, "one walk per Boot that arrived");
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
