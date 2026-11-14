//! A write that creates an entity of a class the writing pack may not create is REFUSED WITH A RECEIPT (#1143, operator 2026-11-14):
//! it used to be dropped silently (a view pack drawing `DomNode:...` with no loaded pack declaring DomNode did nothing, no message).
//! The cases: a write to an EXISTING entity of a class no pack owns stays legal; creating one as an unprivileged pack is the silent case.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};

fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn host() -> Host { let mut o = Options::new(None); o.effects = false; Host::open(o).unwrap() }

const OWN: &str = "PACK own 1\nCls.tag getter: 'tag'\n";
const DRAW: &str = "PACK draw 1\nTrig.go on-setter w:\n  ALWAYS: Ghost:made.val = 1\nTrig.mk on-setter c:\n  ALWAYS: Cls:made.val = 1\nTrig.ex on-setter e:\n  ALWAYS: Ghost:old.val = 1\n";

fn refused(h: &Host) -> String { h.get("Trig:t", "refused").as_str().unwrap_or_default().to_string() }

#[test]
fn creating_an_entity_of_a_class_no_pack_declares_is_refused_with_a_receipt_naming_entity_class_and_pack() {
    let h = host();
    h.load_pack("draw", DRAW).unwrap();
    h.born("Trig:t", "Trig", props(json!({})), "person");
    h.write("Trig:t", "go", json!(1), "person"); h.pump();
    assert!(!h.snapshot().contains_key("Ghost:made"));
    let r = refused(&h);
    assert!(r.contains("Ghost:made") && r.contains("Ghost") && r.contains("draw") && r.contains("no loaded pack declares"), "receipt: {r:?}");
}

#[test]
fn creating_an_entity_of_another_packs_class_without_the_import_is_refused_with_a_receipt() {
    let h = host();
    h.load_pack("own", OWN).unwrap();
    h.load_pack("draw", DRAW).unwrap();
    h.born("Trig:t", "Trig", props(json!({})), "person");
    h.write("Trig:t", "mk", json!(1), "person"); h.pump();
    assert!(!h.snapshot().contains_key("Cls:made"));
    let r = refused(&h);
    assert!(r.contains("Cls:made") && r.contains("draw") && r.contains("own") && r.contains("IMPORT own"), "receipt: {r:?}");
}

#[test]
fn legal_writes_are_unchanged() {
    let h = host();
    h.load_pack("draw", DRAW).unwrap();
    h.born("Trig:t", "Trig", props(json!({})), "person");
    h.born("Ghost:old", "Ghost", props(json!({})), "person");
    // an existing entity of a class no pack owns: written, no receipt
    h.write("Trig:t", "ex", json!(1), "person"); h.pump();
    assert_eq!(h.get("Ghost:old", "val"), json!(1));
    assert_eq!(refused(&h), "");
    // the person (outside writer) creates and writes any class
    h.born("Ghost:person", "Ghost", props(json!({})), "person");
    h.write("Ghost:person", "val", json!(2), "person"); h.pump();
    assert_eq!(h.get("Ghost:person", "val"), json!(2));
    // a privileged pack creates a class no pack owns
    h.write("Pack:draw", "privileged", json!(true), "person"); h.pump();
    h.write("Trig:t", "go", json!(2), "person"); h.pump();
    assert_eq!(h.get("Ghost:made", "val"), json!(1));
}
