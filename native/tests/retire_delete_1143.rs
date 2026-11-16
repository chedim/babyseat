//! A RETIRED ENTITY'S DELETE COSTS WHAT IT DELETES, NOT THE SIZE OF THE STORE (#1143, the seat that froze on a 1.4 GB store, 2026-11-15): the rows under `<eid>:` are a key
//! RANGE of the primary key, not a `substr` test of every row. The plan of each delete must search the key index, never scan the table.
use membrane_vm::host::{namespace_delete_sql, retire_delete_sql, Host, Options, ROWS, VECTORS};
use rusqlite::Connection;
use serde_json::{json, Map, Value as J};

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("retire-delete-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

fn plan(db: &Connection, sql: &str) -> String {
    let mut st = db.prepare(&format!("EXPLAIN QUERY PLAN {sql}")).unwrap();
    let rows: Vec<String> = st.query_map(rusqlite::params_from_iter(vec!["a"; st.parameter_count()]),|r| r.get::<_, String>(3)).unwrap().map(|r| r.unwrap()).collect();
    rows.join(" | ")
}

#[test]
fn the_retire_deletes_search_the_key_index_and_never_scan_the_table() {
    let db = Connection::open_in_memory().unwrap();
    db.execute_batch(&format!("CREATE TABLE {ROWS} (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE {VECTORS} (k TEXT PRIMARY KEY, cls TEXT NOT NULL, data BLOB NOT NULL)")).unwrap();
    for sql in [retire_delete_sql(ROWS), retire_delete_sql(VECTORS), namespace_delete_sql(ROWS)] {
        let p = plan(&db, &sql);
        assert!(p.contains("SEARCH") && !p.contains("SCAN"), "this delete must use the key index, not read every row: {sql} -> {p}");
    }
}

#[test]
fn retiring_a_parent_takes_its_children_and_not_its_lookalikes() {
    let file = tmpfile("tree");
    {
        let mut o = Options::new(Some(file.clone())); o.effects = false;
        let h = Host::open(o).unwrap();
        for e in ["A:1", "A:1:x", "A:1:x:y", "A:10", "A:10:x", "A:2", "A"] { h.born(e, "A", props(json!({"n": 1})), "person"); }
        h.write("A:1", "retire", json!(true), "person");
    }
    let db = Connection::open(&file).unwrap();
    let mut left: Vec<String> = db.prepare(&format!("SELECT k FROM {ROWS} WHERE k LIKE 'A%'")).unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
    left.sort();
    assert_eq!(left, ["A", "A:10", "A:10:x", "A:2"], "A:1 takes A:1:x and A:1:x:y with it, never A:10");
}

#[test]
fn many_retired_at_once_leave_what_read_them_right() {
    let mut o = Options::new(Some(tmpfile("many"))); o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("t", "PACK t 1\nRESIDENT Count\nCount.n getter: len(instances('Box'))\n").unwrap();
    h.rebuild();
    h.born("Count:c", "Count", props(json!({})), "person");
    for i in 0..60 { h.born(&format!("Box:{i}"), "Box", props(json!({"v": i})), "person"); h.born(&format!("Box:{i}:kid"), "Box", props(json!({"v": 1})), "person"); }
    assert_eq!(h.get("Count:c", "n"), json!(120));
    for i in 10..60 { h.write(&format!("Box:{i}"), "retire", json!(true), "person"); }
    assert_eq!(h.get("Count:c", "n"), json!(20), "the fifty parents took their fifty children with them");
}

/// The time to retire 40 entities among `m` that each hold a getter reading `reads` properties (the field remembers `m * reads` things nobody retired).
fn retire_time(m: usize, reads: usize) -> std::time::Duration {
    let mut o = Options::new(Some(tmpfile("cost"))); o.effects = false;
    let h = Host::open(o).unwrap();
    let expr = (0..reads).map(|j| format!("this.p{j}")).collect::<Vec<_>>().join(" + ");
    h.load_pack("t", &format!("PACK t 1\nBox.d getter: {expr}\n")).unwrap();
    h.rebuild();
    let mut p = Map::new();
    for j in 0..reads { p.insert(format!("p{j}"), json!(j)); }
    for i in 0..m + 40 { h.born(&format!("Box:{i}"), "Box", p.clone(), "person"); }
    let t = std::time::Instant::now();
    for i in m..m + 40 { h.write(&format!("Box:{i}"), "retire", json!(true), "person"); }
    t.elapsed()
}

#[test]
fn retiring_costs_what_is_retired_not_what_the_field_remembers() {
    let narrow = retire_time(4000, 1);
    let wide = retire_time(4000, 40);   // the same entities, forty times what the field remembers of them
    // the removal finds the readers through what they read, not by a pass over all that is remembered
    assert!(wide < narrow * 3 + std::time::Duration::from_millis(40), "retiring 40 took {narrow:?} among 4000 readers of 1 property, {wide:?} among 4000 readers of 40");
}

#[test]
fn a_reader_of_a_retired_entity_recomputes_and_the_others_keep_their_value() {
    let mut o = Options::new(Some(tmpfile("readers"))); o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("t", "PACK t 1\nRESIDENT Sum\nBox.d getter: this.v * 2\nSum.s getter: Box:1.v + Box:2.v\n").unwrap();
    h.rebuild();
    for i in 0..5 { h.born(&format!("Box:{i}"), "Box", props(json!({"v": i + 10})), "person"); }
    h.born("Sum:x", "Sum", props(json!({})), "person");
    assert_eq!(h.get("Sum:x", "s"), json!(23));
    h.write("Box:2", "retire", json!(true), "person");
    h.write("Box:3", "retire", json!(true), "person");
    assert_ne!(h.get("Sum:x", "s"), json!(23), "what read a retired entity recomputed");
    assert_eq!(h.get("Box:4", "d"), json!(28));
    h.write("Box:1", "v", json!(100), "person");
    assert_eq!(h.get("Box:1", "d"), json!(200), "a survivor's getter still follows its inputs");
}
