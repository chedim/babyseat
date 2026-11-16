//! The write feed is gone (#1143, operator 2026-11-14): the host no longer notes applied writes and no longer writes a batch entity per round. A pack that owns the class `Wire` (the
//! feed's old marker) with a setter on `batch` gets NO `Wire` entity and no evaluation when a stored class is written; the eval accounting shows no `Wire` row.
#![cfg(feature = "bin")]
use membrane_vm::host::{Host, Options};
use serde_json::json;

#[test]
fn a_write_of_a_stored_class_makes_no_wire_entity_and_no_extra_evaluation() {
    let mut o = Options::new(None); o.effects = false;
    let h = Host::open(o).unwrap();
    let r = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    h.load_pack("stats", &std::fs::read_to_string(r.join("membrane/packs/stats.c1f")).unwrap()).unwrap();
    h.load_pack("wt", "PACK wt 1\nVIRTUAL Wire\nWire.batch on-setter fan:\n  WHEN this.batch != None:\n    this.seen = len(this.batch)\nWt.dbl getter: this.w * 2\n").unwrap();
    h.enable_stats(10.0);
    h.born("Wt:a", "Wt", json!({"w": 1}).as_object().unwrap().clone(), "person");
    for i in 0..5 { h.write("Wt:a", "w", json!(10 + i), "person"); h.tick(); h.pump(); }
    assert!(h.snapshot().get("Wire:host").is_none(), "no Wire entity is born");
    assert!(h.snapshot().keys().all(|k| !k.starts_with("Wire:")), "no entity of the class Wire at all");
    assert_eq!(h.get("Wt:a", "dbl"), json!(28));
    let top = h.get("Stats:eval", "top");
    let rows = top.as_array().cloned().unwrap_or_default();
    assert!(rows.iter().all(|r| r["class"] != json!("Wire")), "no Wire row in the eval accounting: {top}");
}
