//! The seat's memory (#1143, w28): the trigger queue of the core (`Core::q`, read only by the Python-era trigger engine) must not keep the writes of a
//! field host. Before: every write (its `old` and its `new` value, whole) was kept in a ring of QUEUE_MAX writes that nothing read: a burst of 3000 chunk
//! births held about 1.6 GB live (measured with the counting allocator, `--features alloc-stats`).
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map};

fn host() -> Host { Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap() }

#[test]
fn a_field_host_keeps_no_writes_in_the_trigger_queue() {
    let h = host();
    let mut p = Map::new();
    p.insert("name".into(), json!("a big value"));
    h.born("Blob:one", "Blob", p, "test");
    for i in 0..200 { h.write("Blob:one", "v", json!("x".repeat(10_000) + &i.to_string()), "test"); }
    h.pump();
    let queued = h.engine().lock().unwrap().0.queued();
    assert_eq!(queued, 0, "the writes of a field host are not queued for a trigger engine nobody runs");
}

#[test]
fn a_core_made_for_triggers_still_queues_its_writes() {
    let mut c = membrane_vm::vm::Core::new();
    c.hear(membrane_vm::vm::Write { eid: "A:1".into(), prop: "p".into(), new: json!(1), old: json!(null), writer: "t".into(), chain: json!({}), seq: 1 }, None, 0.0);
    assert_eq!(c.queued(), 1, "the trigger engine (the Python binding) reads the queue");
}
