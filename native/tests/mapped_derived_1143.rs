//! `MAPPED <Class> f16 derived` (#1143, step 3 of the field on SemText; a language-surface change awaiting the operator): a MAPPED class whose entities a RULE writes keeps
//! those writes DERIVED, like every other class: stored (the row, the vector table) and restored at a restart, but NOT journalled. The plain `MAPPED <Class> f16` keeps
//! journalling them whole (docs/cliffvm.md, "MAPPED"). The field's states, seeds, episodes and background are such classes: stored rows, never replayed.
mod common;

use membrane_vm::host::{Host, Line, Options};
use membrane_vm::syntax::parse_full;
use serde_json::{json, Map, Value as J};

fn tmpfile(tag: &str) -> String {
    let n = common::unique();
    std::env::temp_dir().join(format!("mapped-derived-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn open(file: Option<&str>) -> Host { let mut o = Options::new(file.map(String::from)); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

const STATES: &str = "PACK states 1\nMAPPED St f16 derived\nSt.dim getter: len(this.v)\n";
const RULE: &str = "PACK rule 1\nIMPORT states 1\nSrc.go on-setter put:\n  WHEN this.go != None:\n    ('St:' + this.go) = St(of: this.go, v: this.nums)\nProbe.top getter: near(this.q, 'St', 2)\n";

fn host(file: Option<&str>) -> Host {
    let h = open(file);
    h.load_pack("states", STATES).unwrap();
    h.load_pack("rule", RULE).unwrap();
    h.rebuild();
    h
}

#[test]
fn the_header_takes_derived_after_the_dtype_and_refuses_another_word() {
    let p = parse_full("PACK p 1\nMAPPED St f16 derived\nSt.dim getter: 1\n").unwrap();
    assert_eq!(p.mapped.len(), 1);
    assert_eq!(p.mapped[0].0, "St");
    let e = parse_full("PACK p 1\nMAPPED St f16 sometimes\n").unwrap_err();
    assert!(e.contains("derived"), "the refusal names the one option: {e}");
}

#[test]
fn what_a_rule_writes_to_a_derived_mapped_class_is_stored_not_journalled_and_a_restart_restores_it() {
    let file = tmpfile("restart");
    let before;
    {
        let h = host(Some(&file));
        h.born("Src:1", "Src", props(json!({"nums": [1.0, 2.0, 3.0]})), "test");
        h.write("Src:1", "go", json!("a"), "test");
        h.write("Src:1", "nums", json!([3.0, 2.0, 1.0]), "test");
        h.write("Src:1", "go", json!("b"), "test");
        assert_eq!(h.get("St:a", "v"), json!("[3 numbers, f16]"));
        assert_eq!(h.get("St:a", "of"), json!("a"));
        let lines = h.journal_stored().unwrap();
        assert!(!lines.iter().any(|l| matches!(l, Line::Write { eid, .. } | Line::Born { eid, .. } if eid.starts_with("St:"))), "a rule's writes stay derived: {:?}",
            lines.iter().filter(|l| matches!(l, Line::Write { eid, .. } | Line::Born { eid, .. } if eid.starts_with("St:"))).collect::<Vec<_>>());
        h.born("Probe:p", "Probe", props(json!({"q": [1.0, 2.0, 3.0]})), "test");
        before = h.get("Probe:p", "top");
        assert_eq!(before[0]["eid"], json!("St:a"), "{before}");
    }
    // the restart reads the rows and the vector table: nothing is replayed, nothing recomputed
    let h = open(Some(&file));
    h.load_pack("states", STATES).unwrap();
    h.load_pack("rule", RULE).unwrap();
    h.load_stored_entities();
    h.rebuild();
    h.born("Probe:p", "Probe", props(json!({"q": [1.0, 2.0, 3.0]})), "test");
    assert_eq!(h.get("Probe:p", "top"), before, "the same nearest after the restart");
    assert_eq!(h.get("St:b", "of"), json!("b"));
    assert_eq!(h.get("St:b", "dim"), json!(3));
}

#[test]
fn an_outside_write_to_a_derived_mapped_class_is_still_journalled_compact() {
    let h = host(None);
    h.born("St:o", "St", Map::new(), "person");
    h.write("St:o", "v", json!([1.0, 0.0, 0.0]), "person");
    let line = h.journal_stored().unwrap().into_iter().find_map(|l| match l { Line::Write { eid, prop, new, .. } if eid == "St:o" && prop == "v" => Some(new), _ => None }).expect("an outside write is journalled");
    assert!(line["$f16"].is_string(), "compact: {line}");
}

#[test]
fn the_plain_mapped_class_still_journals_what_a_rule_writes() {
    let h = open(None);
    h.load_pack("states", "PACK states 1\nMAPPED St f16\nSt.dim getter: len(this.v)\n").unwrap();
    h.load_pack("rule", RULE).unwrap();
    h.rebuild();
    h.born("Src:1", "Src", props(json!({"nums": [1.0, 2.0, 3.0]})), "test");
    h.write("Src:1", "go", json!("a"), "test");
    assert!(h.journal_stored().unwrap().iter().any(|l| matches!(l, Line::Write { eid, prop, .. } if eid == "St:a" && prop == "v")), "journalled whole, as before");
}
