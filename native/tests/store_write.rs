//! The store driver writes (operator, 2026-11-12: the cliff rust driver fully featured): `Store:<name>.run = {id, sql, args}`
//! (or `{id, statements: [{sql, args}, ...]}`) is an effect with a receipt -- one transaction, bound arguments, INSERT /
//! UPDATE / DELETE only (a read is a `sql()` getter; DDL, ATTACH and PRAGMA are refused), RETURNING rows in the receipt.
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn db() -> (std::path::PathBuf, rusqlite::Connection) {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let dir = std::env::temp_dir().join(format!("store-write-{}-{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
    std::fs::create_dir_all(dir.join("store")).unwrap();
    let p = dir.join("store").join("field.sqlite3");
    let c = rusqlite::Connection::open(&p).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    c.execute_batch("CREATE TABLE c_events (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
    for (k, text) in [("e1", "the boat is blue"), ("e2", "the sail is red"), ("e3", "far away")] {
        c.execute("INSERT INTO c_events VALUES (?1, ?2)", (k, json!({"d": {"text": text}}).to_string())).unwrap();
    }
    (p, c)
}

fn world(p: &std::path::Path) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("std", "PACK std 1\nStore.kind getter: 'store'\n").unwrap();
    let mut c = Core::new();
    let path = p.to_str().unwrap().to_string();
    c.store = Some(Store::with_exec(&path, std::sync::Arc::new(membrane_vm::sqlexec::NativeSqlExec::new(&path).unwrap())));
    c.load("Store:events".into(), "Store".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, p: &str, v: J, writer: &str) {
    let w = Write { eid: "Store:events".into(), prop: p.into(), new: v, old: J::Null, writer: writer.into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    while wt.pending() > 0 && t.elapsed().as_secs() < 10 { wt.wait(Some(std::time::Duration::from_millis(100))); let mut s = 30; f.settle(c, &H, 3.0, &mut s); }
    let mut s = 40; f.settle(c, &H, 3.0, &mut s);
}
fn texts(r: &rusqlite::Connection) -> Vec<String> {
    let mut st = r.prepare("SELECT coalesce(json_extract(body,'$.d.text'),'~') FROM c_events ORDER BY k").unwrap();
    st.query_map([], |x| x.get::<_, String>(0)).unwrap().map(|x| x.unwrap()).collect()
}

#[test]
fn a_run_updates_rows_with_bound_arguments_and_leaves_a_receipt() {
    let (p, r) = db();
    let (mut c, mut f) = world(&p);
    put(&mut c, &mut f, "run", json!({"id": "u1", "sql": "UPDATE c_events SET body = json_set(body, '$.d.text', ?) WHERE k = ?", "args": ["cleared", "e2"]}), "person");
    let rc = &c.docs["Store:events"].props["run_result"];
    assert_eq!(rc["id"], json!("u1"));
    assert_eq!(c.docs["Store:events"].props["run_rc"], json!(0));
    assert_eq!(rc["changes"], json!(1));
    assert_eq!(texts(&r), vec!["the boat is blue", "cleared", "far away"]);
}

#[test]
fn statements_are_one_transaction_and_a_failure_rolls_all_back() {
    let (p, r) = db();
    let (mut c, mut f) = world(&p);
    put(&mut c, &mut f, "run", json!({"id": "b1", "statements": [
        {"sql": "DELETE FROM c_events WHERE k = ?", "args": ["e1"]},
        {"sql": "INSERT INTO c_events VALUES (?, ?)", "args": ["e3", "{}"]}]}), "person");       // e3 exists: the PRIMARY KEY refuses
    assert_ne!(c.docs["Store:events"].props["run_rc"], json!(0));
    assert_eq!(texts(&r).len(), 3, "the delete was rolled back");
    put(&mut c, &mut f, "run", json!({"id": "b2", "statements": [
        {"sql": "DELETE FROM c_events WHERE k = ?", "args": ["e1"]},
        {"sql": "DELETE FROM c_events WHERE k = ?", "args": ["e2"]}]}), "person");
    assert_eq!(c.docs["Store:events"].props["run_result"]["changes"], json!(2));
    assert_eq!(texts(&r), vec!["far away"]);
}

#[test]
fn returning_rows_come_back_in_the_receipt() {
    let (p, _r) = db();
    let (mut c, mut f) = world(&p);
    put(&mut c, &mut f, "run", json!({"id": "d1", "sql": "DELETE FROM c_events WHERE k = ? RETURNING k", "args": ["e1"]}), "person");
    assert_eq!(c.docs["Store:events"].props["run_result"]["rows"], json!([{"k": "e1"}]));
}

#[test]
fn reads_ddl_attach_and_pragma_are_refused_and_a_script_may_not_write() {
    let (p, r) = db();
    let (mut c, mut f) = world(&p);
    for (n, sql) in [("read", "SELECT * FROM c_events"), ("ddl", "DROP TABLE c_events"), ("create", "CREATE TABLE x (a)"),
                     ("attach", "ATTACH DATABASE ':memory:' AS m"), ("pragma", "PRAGMA journal_mode = DELETE"),
                     ("two", "DELETE FROM c_events WHERE k = 'e1'; DELETE FROM c_events WHERE k = 'e2'")] {
        put(&mut c, &mut f, "run", json!({"id": n, "sql": sql}), "person");
        assert_eq!(c.docs["Store:events"].props["run_rc"], json!(2), "{n}: {:?}", c.docs["Store:events"].props["run_result"]);
    }
    assert_eq!(texts(&r).len(), 3);
    put(&mut c, &mut f, "run", json!({"id": "s", "sql": "DELETE FROM c_events"}), "script:Component:x");
    assert_eq!(c.docs["Store:events"].props["run_result"]["rc"], json!(2));
    assert_eq!(texts(&r).len(), 3);
}

#[test]
fn the_wrong_number_of_arguments_is_refused_before_anything_runs() {
    let (p, r) = db();
    let (mut c, mut f) = world(&p);
    put(&mut c, &mut f, "run", json!({"id": "a", "sql": "DELETE FROM c_events WHERE k = ?", "args": []}), "person");
    assert_eq!(c.docs["Store:events"].props["run_rc"], json!(2));
    assert_eq!(texts(&r).len(), 3);
}
