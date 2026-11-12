//! The spec harness (operator 2026-11-12: a c1f test framework; JSON data now, c1f `TEST` blocks later): a spec is
//! {packs, given, writes, expect}; the harness loads the packs, creates the given entities, performs the writes, settles the
//! engine until it is quiet and checks every expectation, reporting each failure with the actual value. No sleeps, no polling.
use membrane_vm::spec;
use serde_json::json;

fn pack_file(dir: &std::path::Path, name: &str, text: &str) -> String {
    std::fs::create_dir_all(dir).unwrap();
    let p = dir.join(format!("{name}.c1f"));
    std::fs::write(&p, text).unwrap();
    p.to_str().unwrap().to_string()
}
fn tmp(tag: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("spec-harness-{}-{}", std::process::id(), tag))
}

const LAMP: &str = "PACK lamp 1\nLamp.lit getter: this.power == 'on'\nLamp.power on-setter g:\n  WHEN this.power == 'on':\n    this.seen = 'yes'\n";

#[test]
fn a_passing_spec_passes() {
    let d = tmp("pass");
    let p = pack_file(&d, "lamp", LAMP);
    let s = json!({"name": "a lamp lights", "packs": [p],
        "given": [{"eid": "Lamp:desk", "class": "Lamp", "props": {"power": "off"}}],
        "writes": [{"eid": "Lamp:desk", "prop": "power", "value": "on"}],
        "expect": [{"eid": "Lamp:desk", "prop": "lit", "equals": true}, {"eid": "Lamp:desk", "prop": "seen", "equals": "yes"},
                   {"eid": "Lamp:desk", "prop": "nothing", "none": true}, {"eid": "Lamp:desk", "prop": "power", "contains": "o"}]});
    assert_eq!(spec::run(&s), Vec::<String>::new());
}

#[test]
fn a_failing_expectation_says_what_it_got() {
    let d = tmp("fail");
    let p = pack_file(&d, "lamp", LAMP);
    let s = json!({"name": "x", "packs": [p],
        "given": [{"eid": "Lamp:desk", "class": "Lamp", "props": {"power": "off"}}],
        "writes": [],
        "expect": [{"eid": "Lamp:desk", "prop": "lit", "equals": true}, {"eid": "Lamp:nope", "prop": "lit", "equals": true},
                   {"eid": "Lamp:desk", "prop": "power", "none": true}]});
    let f = spec::run(&s);
    assert_eq!(f.len(), 3, "{f:?}");
    assert!(f[0].contains("Lamp:desk.lit") && f[0].contains("false") && f[0].contains("true"), "{}", f[0]);
    assert!(f[1].contains("Lamp:nope") && f[1].contains("no such entity"), "{}", f[1]);
    assert!(f[2].contains("Lamp:desk.power") && f[2].contains("off"), "{}", f[2]);
}

#[test]
fn a_pack_that_does_not_load_fails_the_spec_with_its_error() {
    let d = tmp("bad");
    let p = pack_file(&d, "bad", "PACK bad 1\nLamp.x getter: this.y +\n");
    let f = spec::run(&json!({"name": "x", "packs": [p], "given": [], "writes": [],
        "expect": [{"eid": "Lamp:d", "prop": "x", "none": true}]}));
    assert_eq!(f.len(), 1);
    assert!(f[0].contains("bad"), "{}", f[0]);
}

#[test]
fn a_spec_without_expectations_is_refused() {
    let f = spec::run(&json!({"name": "empty", "packs": [], "given": [], "writes": []}));
    assert_eq!(f.len(), 1);
    assert!(f[0].contains("expect"), "{}", f[0]);
}

#[test]
fn a_chain_of_getters_settles_from_the_loaded_state() {
    let d = tmp("chain");
    // names sort against the dependency order on purpose: `a` reads `b` reads `c` reads the loaded `x`
    let p = pack_file(&d, "chain", "PACK chain 1\nT.a getter: this.b\nT.b getter: this.c\nT.c getter: this.x\nT.it getter: this.p_it\nT.p_it getter: (concat(this.xs, this.ys) or [None])[0]\n");
    let s = json!({"name": "chain", "packs": [p],
        "given": [{"eid": "T:1", "class": "T", "props": {"x": 5, "xs": ["m"], "ys": []}}],
        "writes": [], "expect": [{"eid": "T:1", "prop": "a", "equals": 5}, {"eid": "T:1", "prop": "it", "equals": "m"}]});
    assert_eq!(spec::run(&s), Vec::<String>::new());
}

#[test]
fn a_getter_reading_instances_of_another_class_settles_from_the_loaded_state() {
    let d = tmp("inst");
    let p = pack_file(&d, "inst", "PACK inst 1\nC.v2 getter: this.v\nT.rows getter: [c.v for c in instances('C')]\nT.first getter: (concat(this.rows, []) or [None])[0]\nT.it getter: this.first\n");
    let s = json!({"name": "inst", "packs": [p],
        "given": [{"eid": "C:1", "class": "C", "props": {"v": 7}}, {"eid": "T:1", "class": "T", "props": {}}],
        "writes": [], "expect": [{"eid": "T:1", "prop": "rows", "equals": [7]}, {"eid": "T:1", "prop": "first", "equals": 7}, {"eid": "T:1", "prop": "it", "equals": 7}]});
    assert_eq!(spec::run(&s), Vec::<String>::new());
}

#[test]
fn an_expectation_can_name_a_path_into_a_property() {
    let d = tmp("path");
    let p = pack_file(&d, "path", "PACK path 1\nR.reply getter: {'status': 200, 'body': {'name': this.n, 'who': None, 'tags': ['a', 'b']}}\n");
    let ok = json!({"name": "path", "packs": [p.clone()],
        "given": [{"eid": "R:1", "class": "R", "props": {"n": "seat"}}], "writes": [],
        "expect": [{"eid": "R:1", "prop": "reply.status", "equals": 200}, {"eid": "R:1", "prop": "reply.body.name", "equals": "seat"},
                   {"eid": "R:1", "prop": "reply.body.who", "none": true}, {"eid": "R:1", "prop": "reply.body.tags.1", "equals": "b"},
                   {"eid": "R:1", "prop": "reply.body.missing.deeper", "none": true}]});
    assert_eq!(spec::run(&ok), Vec::<String>::new());
    let bad = json!({"name": "path", "packs": [p], "given": [{"eid": "R:1", "class": "R", "props": {"n": "seat"}}], "writes": [],
        "expect": [{"eid": "R:1", "prop": "reply.body.name", "equals": "other"}]});
    let f = spec::run(&bad);
    assert_eq!(f.len(), 1);
    assert!(f[0].contains("reply.body.name") && f[0].contains("seat") && f[0].contains("other"), "{}", f[0]);
}
