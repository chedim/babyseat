//! The store's churn (#1143, the seat that burned CPU idle): what the engine writes is stored ONCE per round, in ONE transaction, and a write
//! that changes nothing never touches the store. `Host::store_commits` counts the commits of the host's connection.
use membrane_vm::host::{Host, Options};
use rusqlite::Connection;
use serde_json::{json, Map, Value as J};

const PACK: &str = "PACK churn 1\nBox.double getter: this.n * 2\nBox.label getter: 'box ' + str(this.n)\n";

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("churn-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn open(file: &str) -> Host { let mut o = Options::new(Some(file.to_string())); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// a store written by an earlier run: `n` boxes, each with a big note (the real store's 800 KB chat rows, scaled down), the getters' values stored or not
fn earlier_run(file: &str, n: usize, with_derived: bool) {
    let h = open(file);
    h.load_pack("churn", PACK).unwrap();
    h.rebuild();
    for i in 0..n { h.born(&format!("Box:{i}"), "Box", props(json!({"n": i, "note": "x".repeat(800_000)})), "person"); }
    if !with_derived {
        let db = Connection::open(file).unwrap();
        db.execute("UPDATE c_field SET body = json_remove(body, '$.props.double', '$.props.label', '$.writers.double', '$.writers.label')", []).unwrap();
    }
}

#[test]
fn a_restore_that_computes_what_is_already_stored_writes_nothing() {
    let file = tmpfile("same");
    earlier_run(&file, 3, true);
    let watch = Connection::open(&file).unwrap();
    let h = open(&file);
    h.load_pack("churn", PACK).unwrap();
    assert_eq!(h.load_stored_entities(), 3);
    let before = h.store_commits();
    h.rebuild();
    assert_eq!(h.get("Box:2", "double"), json!(4), "the getters ran");
    assert_eq!(h.store_commits(), before, "the rebuild derived the very values the rows hold: no store write");
}

#[test]
fn the_writes_of_one_round_are_one_transaction() {
    let file = tmpfile("one");
    earlier_run(&file, 4, false);
    let watch = Connection::open(&file).unwrap();
    let h = open(&file);
    h.load_pack("churn", PACK).unwrap();
    assert_eq!(h.load_stored_entities(), 4);
    let before = h.store_commits();
    h.rebuild();
    assert_eq!(h.get("Box:3", "label"), json!("box 3"));
    let stored: J = serde_json::from_str(&watch.query_row("SELECT body FROM c_field WHERE k = 'Box:3'", [], |r| r.get::<_, String>(0)).unwrap()).unwrap();
    assert_eq!(stored["props"]["double"], json!(6), "the derived value is stored (the door's entity_read reads the store)");
    assert_eq!(h.store_commits() - before, 1, "8 derived writes on 4 rows: ONE commit (one fsync), not one per prop");
}

#[test]
fn a_quiet_host_with_big_rows_stays_quiet() {
    let file = tmpfile("quiet");
    earlier_run(&file, 3, true);
    let watch = Connection::open(&file).unwrap();
    let h = open(&file);
    h.load_pack("churn", PACK).unwrap();
    h.load_stored_entities();
    h.rebuild();
    let before = h.store_commits();
    for _ in 0..20 { h.pump(); h.tick(); }
    assert_eq!(h.store_commits(), before, "nothing arrived: nothing is written");
}
