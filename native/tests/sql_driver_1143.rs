//! The `Sql` driver (#1143, the operator 2026-11-14): a second SQLite file as ENTITIES and a driver effect, no builtin.
//!   SqlSource:<name> {path, mode: ro (default) | rw}      SqlQuery:<name> {source, sql}      SqlQuery.run = {id, args, max_rows?}
//! A read-only source is opened with the immutable URI; a run is a write, its receipt `{id, status, rc, rows, changes, ms, started, finished, head, ...}`
//! is the `run_result` of the query and is journalled; `rows` reads the latest receipt, an Error with the reason when the run failed.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn pack(n: &str) -> String { std::fs::read_to_string(repo().join(format!("membrane/packs/{n}.c1f"))).unwrap_or_else(|e| panic!("{n}.c1f: {e}")) }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("sqldrv-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(d.join(".membrane")).unwrap();
    d.canonicalize().unwrap()
}
fn store_path(d: &std::path::Path) -> String { d.join(".membrane/field.sqlite3").to_string_lossy().to_string() }
fn ticking() -> Box<dyn Fn() -> f64 + Send + Sync> {
    let n = Arc::new(AtomicU64::new(0));
    Box::new(move || 1000.0 + n.fetch_add(1, Ordering::SeqCst) as f64)
}
fn host_at(d: &std::path::Path) -> Host {
    let h = Host::open({ let mut o = Options::new(Some(store_path(d))); o.clock = ticking(); o }).unwrap();
    for n in ["entity", "boot", "sql"] { h.load_pack(n, &pack(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.load_stored_entities();                      // what the launcher does after the packs: the entities of an earlier run come back
    h.rebuild();
    h
}
fn pump_until(h: &Host, mut done: impl FnMut(&Host) -> bool) {
    let wt = h.waiter();
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(20) {
        h.pump();
        if done(h) { return; }
        if let Some(w) = &wt { w.wait(Some(Duration::from_millis(30))); }
    }
    panic!("timed out: {:?}", h.snapshot());
}

/// a small SQLite file: table t(id, name, n) with three rows
fn make_db(path: &std::path::Path) {
    let cx = rusqlite::Connection::open(path).unwrap();
    cx.execute_batch("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, n REAL); INSERT INTO t VALUES (1,'ada',1.5),(2,'bob',2.5),(3,'cy',NULL);").unwrap();
}
/// (the host-level stand-in for the model's `entity_write`: the door's tools pack births the entity on its first write, sql_door_1143.rs goes through the door)
fn put(h: &Host, eid: &str, cls: &str, p: J) {
    if h.get(eid, "cls").is_null() { h.born(eid, cls, props(p), "person"); } else { for (k, v) in p.as_object().unwrap() { h.write(eid, k, v.clone(), "person"); } }
    h.pump();
}
fn source(h: &Host, name: &str, path: &std::path::Path, mode: Option<&str>) {
    let mut p = json!({"path": path.to_string_lossy()});
    if let Some(m) = mode { p["mode"] = json!(m); }
    put(h, &format!("SqlSource:{name}"), "SqlSource", p);
}
fn query(h: &Host, name: &str, source: &str, sql: &str) {
    put(h, &format!("SqlQuery:{name}"), "SqlQuery", json!({"source": source, "sql": sql}));
}
/// write `run` and wait for the receipt of THIS id
fn run(h: &Host, q: &str, id: &str, args: J) -> J { run_with(h, q, json!({"id": id, "args": args})) }
fn run_with(h: &Host, q: &str, req: J) -> J {
    let id = req["id"].clone();
    h.write(&format!("SqlQuery:{q}"), "run", req, "person");
    pump_until(h, |h| h.get(&format!("SqlQuery:{q}"), "run_result")["id"] == id);
    h.get(&format!("SqlQuery:{q}"), "run_result")
}

#[test]
fn a_read_through_a_prepared_query_gives_the_receipt_and_the_rows_getter() {
    let d = tmp("read");
    make_db(&d.join("src.db"));
    let h = host_at(&d);
    source(&h, "lex", &d.join("src.db"), None);
    query(&h, "all", "lex", "SELECT id, name, n FROM t ORDER BY id");
    let r = run(&h, "all", "r1", json!([]));
    assert_eq!(r["status"], json!("ok"), "{r}");
    assert_eq!(r["rc"], json!(0));
    assert_eq!(r["rows"], json!([{"id": 1, "name": "ada", "n": 1.5}, {"id": 2, "name": "bob", "n": 2.5}, {"id": 3, "name": "cy", "n": null}]));
    assert_eq!(r["changes"], json!(0));
    for k in ["ms", "started", "finished", "head"] { assert!(r.get(k).is_some(), "the receipt has {k}: {r}"); }
    assert_eq!(h.get("SqlQuery:all", "rows"), r["rows"], "the rows getter reads the latest receipt");
    assert_eq!(h.get("SqlQuery:all", "status"), json!("ok"));
    assert_eq!(h.get("SqlSource:lex", "opened"), json!(true));
}

#[test]
fn bound_args_are_values_never_text_of_the_statement() {
    let d = tmp("bind");
    make_db(&d.join("src.db"));
    let h = host_at(&d);
    source(&h, "lex", &d.join("src.db"), None);
    query(&h, "by", "lex", "SELECT id FROM t WHERE name = ?");
    let r = run(&h, "by", "a", json!(["bob"]));
    assert_eq!(r["rows"], json!([{"id": 2}]));
    let r = run(&h, "by", "b", json!(["x' OR '1'='1"]));
    assert_eq!(r["status"], json!("ok"), "{r}");
    assert_eq!(r["rows"], json!([]), "a classic injection string is just a value that matches nothing");
    let r = run(&h, "by", "c", json!(["'; DROP TABLE t; --"]));
    assert_eq!(r["rows"], json!([]));
    let r = run(&h, "by", "d", json!(["ada"]));
    assert_eq!(r["rows"], json!([{"id": 1}]), "the table is still there");
    let r = run(&h, "by", "e", json!([]));
    assert_eq!(r["status"], json!("error"), "too few arguments: {r}");
    assert!(r["head"].as_str().unwrap().contains("argument"), "{r}");
}

#[test]
fn the_same_id_is_idempotent_a_new_id_reruns_and_the_statement_cache_counter_proves_reuse() {
    let d = tmp("idem");
    make_db(&d.join("src.db"));
    let h = host_at(&d);
    source(&h, "lex", &d.join("src.db"), None);
    query(&h, "all", "lex", "SELECT count(*) AS c FROM t WHERE id > ?");
    let r1 = run(&h, "all", "same", json!([0]));
    assert_eq!(r1["rows"], json!([{"c": 3}]));
    assert_eq!(r1["prepared"], json!(1), "the first run parses once: {r1}");
    assert_eq!(r1["reused"], json!(0));
    // the same id again, with other args: no second run
    h.write("SqlQuery:all", "run", json!({"id": "same", "args": [1]}), "person");
    for _ in 0..10 { h.pump(); std::thread::sleep(Duration::from_millis(20)); }
    assert_eq!(h.get("SqlQuery:all", "run_result"), r1, "the same id runs no second time");
    // a new id runs again, through the cached statement
    let r2 = run(&h, "all", "other", json!([1]));
    assert_eq!(r2["rows"], json!([{"c": 2}]));
    assert_eq!(r2["prepared"], json!(1), "no second parse: {r2}");
    assert_eq!(r2["reused"], json!(1), "{r2}");
    let r3 = run(&h, "all", "third", json!([2]));
    assert_eq!((r3["prepared"].clone(), r3["reused"].clone()), (json!(1), json!(2)));
    assert_eq!(h.get("SqlQuery:all", "reused"), json!(2), "the entity shows the counter");
    // a second query text is parsed once more
    query(&h, "two", "lex", "SELECT 1 AS one");
    let r4 = run(&h, "two", "x", json!([]));
    assert_eq!(r4["prepared"], json!(2), "{r4}");
}

#[test]
fn a_read_only_source_refuses_a_write_with_sqlites_own_words_and_leaves_the_file_untouched() {
    let d = tmp("ro");
    let f = d.join("src.db");
    make_db(&f);
    let before = std::fs::read(&f).unwrap();
    let h = host_at(&d);
    source(&h, "lex", &f, Some("ro"));
    query(&h, "ins", "lex", "INSERT INTO t VALUES (9, 'z', 0)");
    let r = run(&h, "ins", "w1", json!([]));
    assert_eq!(r["status"], json!("error"), "{r}");
    assert!(r["head"].as_str().unwrap().to_lowercase().contains("readonly") || r["head"].as_str().unwrap().to_lowercase().contains("read-only") || r["head"].as_str().unwrap().to_lowercase().contains("read only"), "SQLite's own words are the reason: {r}");
    query(&h, "all", "lex", "SELECT count(*) AS c FROM t");
    assert_eq!(run(&h, "all", "r", json!([]))["rows"], json!([{"c": 3}]));
    assert_eq!(std::fs::read(&f).unwrap(), before, "the file is byte-identical");
    let names: Vec<String> = std::fs::read_dir(&d).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().to_string()).collect();
    assert!(!names.iter().any(|n| n.starts_with("src.db-")), "no journal or wal beside a read-only source: {names:?}");
}

#[test]
fn a_read_write_source_changes_rows_and_reports_changes() {
    let d = tmp("rw");
    let f = d.join("src.db");
    make_db(&f);
    let h = host_at(&d);
    source(&h, "scratch", &f, Some("rw"));
    query(&h, "ins", "scratch", "INSERT INTO t (name, n) VALUES (?, ?)");
    let r = run(&h, "ins", "i1", json!(["dee", 4.0]));
    assert_eq!(r["status"], json!("ok"), "{r}");
    assert_eq!(r["changes"], json!(1));
    assert_eq!(r["rows"], json!([]));
    query(&h, "upd", "scratch", "UPDATE t SET n = 0 WHERE id <= ?");
    assert_eq!(run(&h, "upd", "u1", json!([2]))["changes"], json!(2));
    query(&h, "del", "scratch", "DELETE FROM t WHERE name = ?");
    assert_eq!(run(&h, "del", "d1", json!(["dee"]))["changes"], json!(1));
    query(&h, "all", "scratch", "SELECT count(*) AS c, sum(n) AS s FROM t");
    let r = run(&h, "all", "q", json!([]));
    assert_eq!(r["rows"], json!([{"c": 3, "s": 0.0}]), "{r}");
    assert_eq!(r["changes"], json!(0), "a read reports no changes, whatever the connection did before");
    let cx = rusqlite::Connection::open(&f).unwrap();
    let c: i64 = cx.query_row("SELECT count(*) FROM t", [], |r| r.get(0)).unwrap();
    assert_eq!(c, 3, "the write reached the file");
}

#[test]
fn a_missing_or_corrupt_source_is_an_error_with_a_reason_and_the_vm_keeps_running() {
    let d = tmp("bad");
    std::fs::write(d.join("junk.db"), "this is not a database, not even close, not even 100 bytes of header").unwrap();
    let h = host_at(&d);
    source(&h, "gone", &d.join("nope.db"), None);
    source(&h, "junk", &d.join("junk.db"), None);
    query(&h, "g", "gone", "SELECT 1");
    query(&h, "j", "junk", "SELECT 1");
    let r = run(&h, "g", "a", json!([]));
    assert_eq!(r["status"], json!("error"), "{r}");
    assert!(r["head"].as_str().unwrap().to_lowercase().contains("unable to open") || r["head"].as_str().unwrap().contains("nope.db"), "{r}");
    let r = run(&h, "j", "a", json!([]));
    assert_eq!(r["status"], json!("error"), "{r}");
    assert!(r["head"].as_str().unwrap().to_lowercase().contains("not a database"), "{r}");
    // the failure is an Error value with the reason, on the source and on the query's data
    assert_eq!(h.get("SqlSource:gone", "failed")["opened"].as_str().map(|s| s.to_lowercase().contains("unable to open")), Some(true), "{:?}", h.get("SqlSource:gone", "failed"));
    assert!(h.get("SqlQuery:g", "failed").get("rows").is_some());
    // no crash, no retry loop: the VM goes on and a good source still answers; nothing re-runs by itself
    let n0 = h.journal().len();
    for _ in 0..10 { h.pump(); std::thread::sleep(Duration::from_millis(10)); }
    assert_eq!(h.journal().len(), n0, "a failed source does not retry by itself");
    make_db(&d.join("ok.db"));
    source(&h, "ok", &d.join("ok.db"), None);
    query(&h, "o", "ok", "SELECT count(*) AS c FROM t");
    assert_eq!(run(&h, "o", "a", json!([]))["rows"], json!([{"c": 3}]));
}

#[test]
fn the_seats_own_store_is_never_a_source_even_through_a_symlink_or_a_hard_link() {
    let d = tmp("own");
    let h = host_at(&d);
    h.born("Probe:x", "Probe", props(json!({"v": 1})), "person");   // make sure the store file exists on disk
    h.pump();
    let own = std::path::PathBuf::from(store_path(&d));
    assert!(own.exists());
    std::os::unix::fs::symlink(&own, d.join("alias.db")).unwrap();
    std::fs::hard_link(&own, d.join("hard.db")).unwrap();
    for (name, p) in [("direct", own.clone()), ("sym", d.join("alias.db")), ("hard", d.join("hard.db"))] {
        source(&h, name, &p, Some("rw"));
        query(&h, name, name, "SELECT count(*) AS c FROM sqlite_master");
        let r = run(&h, name, "a", json!([]));
        assert_eq!(r["status"], json!("error"), "{name}: {r}");
        assert!(r["head"].as_str().unwrap().contains("seat's own store"), "{name}: a visible reason: {r}");
        assert!(r.get("rows").map_or(true, |x| x.is_null() || x == &json!([])), "{name}: nothing was read: {r}");
    }
}

#[test]
fn a_restart_on_the_same_store_and_a_replay_of_the_journal_use_the_receipt_and_never_ask_again() {
    let d = tmp("replay");
    let f = d.join("src.db");
    make_db(&f);
    let (journal, want);
    {
        let h = host_at(&d);
        source(&h, "lex", &f, None);
        query(&h, "all", "lex", "SELECT id, name FROM t ORDER BY id");
        run(&h, "all", "r1", json!([]));
        want = h.get("SqlQuery:all", "rows");
        assert_eq!(want.as_array().unwrap().len(), 3);
        journal = h.journal();
    }
    std::fs::remove_file(&f).unwrap();                       // the source is gone
    // (a) a restart on the same store: the entity still reads the rows, and a getter pass queries nothing
    let h2 = host_at(&d);
    h2.pump();
    assert_eq!(h2.get("SqlQuery:all", "rows"), want);
    assert_eq!(h2.get("SqlQuery:all", "run_result")["status"], json!("ok"));
    // (b) a replay of the journal on a fresh host (no effects, no store): the recorded receipt is in the journal
    let mut o = Options::new(None); o.effects = false; o.clock = ticking();
    let r = Host::open(o).unwrap();
    for n in ["entity", "boot", "sql"] { r.load_pack(n, &pack(n)).unwrap(); }
    r.rebuild();
    r.replay(&journal);
    r.pump();
    assert_eq!(r.get("SqlQuery:all", "rows"), want, "the replay reads the journalled receipt, rows included");
}

#[test]
fn two_sources_and_two_queries_work_independently() {
    let d = tmp("two");
    make_db(&d.join("a.db"));
    let cx = rusqlite::Connection::open(d.join("b.db")).unwrap();
    cx.execute_batch("CREATE TABLE w (word TEXT); INSERT INTO w VALUES ('x'),('y');").unwrap();
    drop(cx);
    let h = host_at(&d);
    source(&h, "a", &d.join("a.db"), None);
    source(&h, "b", &d.join("b.db"), None);
    query(&h, "qa", "a", "SELECT count(*) AS c FROM t");
    query(&h, "qb", "b", "SELECT word FROM w ORDER BY word");
    query(&h, "qa2", "a", "SELECT name FROM t WHERE id = ?");
    h.write("SqlQuery:qa", "run", json!({"id": "1", "args": []}), "person");
    h.write("SqlQuery:qb", "run", json!({"id": "1", "args": []}), "person");
    h.write("SqlQuery:qa2", "run", json!({"id": "1", "args": [2]}), "person");
    pump_until(&h, |h| ["qa", "qb", "qa2"].iter().all(|q| !h.get(&format!("SqlQuery:{q}"), "run_result").is_null()));
    assert_eq!(h.get("SqlQuery:qa", "rows"), json!([{"c": 3}]));
    assert_eq!(h.get("SqlQuery:qb", "rows"), json!([{"word": "x"}, {"word": "y"}]));
    assert_eq!(h.get("SqlQuery:qa2", "rows"), json!([{"name": "bob"}]));
    // a query on a source that has no table of that name fails alone
    query(&h, "bad", "b", "SELECT * FROM t");
    assert_eq!(run(&h, "bad", "1", json!([]))["status"], json!("error"));
    assert_eq!(h.get("SqlQuery:qb", "rows"), json!([{"word": "x"}, {"word": "y"}]), "the others are untouched");
}

#[test]
fn max_rows_truncates_visibly_and_is_not_on_by_default() {
    let d = tmp("max");
    let cx = rusqlite::Connection::open(d.join("m.db")).unwrap();
    cx.execute_batch("CREATE TABLE n (i INTEGER); WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 500) INSERT INTO n SELECT x FROM c;").unwrap();
    drop(cx);
    let h = host_at(&d);
    source(&h, "m", &d.join("m.db"), None);
    query(&h, "all", "m", "SELECT i FROM n ORDER BY i");
    let r = run(&h, "all", "full", json!([]));
    assert_eq!(r["rows"].as_array().unwrap().len(), 500, "no default cap");
    assert!(r.get("truncated").map_or(true, |t| t == &json!(false)), "{}", r["head"]);
    let r = run_with(&h, "all", json!({"id": "cut", "args": [], "max_rows": 10}));
    assert_eq!(r["rows"].as_array().unwrap().len(), 10);
    assert_eq!(r["truncated"], json!(true));
    assert_eq!(h.get("SqlQuery:all", "truncated"), json!(true));
    let r = run_with(&h, "all", json!({"id": "exact", "args": [], "max_rows": 500}));
    assert_eq!(r["rows"].as_array().unwrap().len(), 500);
    assert_ne!(r["truncated"], json!(true), "exactly max_rows rows is not truncated");
    let r = run_with(&h, "all", json!({"id": "bad", "args": [], "max_rows": "ten"}));
    assert_eq!(r["status"], json!("error"), "{r}");
}

#[test]
fn no_source_or_query_is_lost_across_a_restart() {
    let d = tmp("restart");
    make_db(&d.join("src.db"));
    {
        let h = host_at(&d);
        source(&h, "lex", &d.join("src.db"), Some("rw"));
        query(&h, "all", "lex", "SELECT count(*) AS c FROM t");
        run(&h, "all", "1", json!([]));
    }
    let h = host_at(&d);
    h.pump();
    assert_eq!(h.get("SqlSource:lex", "mode"), json!("rw"));
    assert_eq!(h.get("SqlSource:lex", "path"), json!(d.join("src.db").to_string_lossy()));
    assert_eq!(h.get("SqlQuery:all", "source"), json!("lex"));
    assert_eq!(h.get("SqlQuery:all", "sql"), json!("SELECT count(*) AS c FROM t"));
    // and it still runs: the connection is opened again lazily
    assert_eq!(run(&h, "all", "2", json!([]))["rows"], json!([{"c": 3}]));
}

#[test]
fn a_failed_run_makes_the_data_props_errors_and_the_error_class_follows() {
    let d = tmp("err");
    make_db(&d.join("src.db"));
    let h = host_at(&d);
    source(&h, "lex", &d.join("src.db"), None);
    query(&h, "q", "lex", "SELECT * FROM nothere");
    assert_eq!(h.get("SqlQuery:q", "rows"), J::Null, "never run: no value");
    run(&h, "q", "1", json!([]));
    let failed = h.get("SqlQuery:q", "failed");
    assert!(failed["rows"].as_str().unwrap().contains("nothere"), "the reason is in failed: {failed}");
    for p in ["rows", "ok", "rc", "changes"] { assert!(failed.get(p).is_some(), "{p} reads as an Error: {failed}"); }
    assert!(h.get("SqlQuery:q", "parents").as_array().unwrap().contains(&json!("cls:error")));
    // the query is fixed, a new id runs: the Errors clear
    h.write("SqlQuery:q", "sql", json!("SELECT count(*) AS c FROM t"), "person");
    run(&h, "q", "2", json!([]));
    assert_eq!(h.get("SqlQuery:q", "rows"), json!([{"c": 3}]));
    assert_eq!(h.get("SqlQuery:q", "failed"), J::Null);
}

#[test]
fn the_boot_fact_sql_sources_births_the_same_entities_and_a_model_write_may_make_more() {
    let d = tmp("boot");
    make_db(&d.join("a.db"));
    make_db(&d.join("b.db"));
    let h = host_at(&d);
    let facts = props(json!({"sql_sources": [{"name": "a", "path": d.join("a.db").to_string_lossy(), "mode": "rw"}, {"name": "b", "path": d.join("b.db").to_string_lossy()}]}));
    membrane_vm::host::boot_started(&h, &facts, true);
    h.pump();
    assert_eq!(h.get("SqlSource:a", "path"), json!(d.join("a.db").to_string_lossy()));
    assert_eq!(h.get("SqlSource:a", "mode"), json!("rw"));
    assert_eq!(h.get("SqlSource:b", "mode_eff"), json!("ro"), "ro is the default when mode is absent");
    query(&h, "q", "b", "SELECT count(*) AS c FROM t");
    assert_eq!(run(&h, "q", "1", json!([]))["rows"], json!([{"c": 3}]));
    // a source the model writes by hand needs no fact
    make_db(&d.join("c.db"));
    source(&h, "c", &d.join("c.db"), None);
    query(&h, "qc", "c", "SELECT count(*) AS c FROM t");
    assert_eq!(run(&h, "qc", "1", json!([]))["rows"], json!([{"c": 3}]));
}

#[test]
fn a_source_written_by_hand_recovers_when_its_path_is_rewritten() {
    let d = tmp("recover");
    make_db(&d.join("good.db"));
    let h = host_at(&d);
    source(&h, "lex", &d.join("typo.db"), None);
    query(&h, "q", "lex", "SELECT count(*) AS c FROM t");
    let r = run(&h, "q", "1", json!([]));
    assert_eq!(r["status"], json!("error"), "{r}");
    assert!(h.get("SqlSource:lex", "failed").get("opened").is_some(), "the source reads as an Error with its reason");
    // fix the path: the Error clears at once (the stale failure is not the new path's), the next run opens the new file
    h.write("SqlSource:lex", "path", json!(d.join("good.db").to_string_lossy()), "person");
    h.pump();
    assert_eq!(h.get("SqlSource:lex", "failed"), J::Null, "rewriting the path recovers the source");
    assert_eq!(run(&h, "q", "2", json!([]))["rows"], json!([{"c": 3}]));
    assert_eq!(h.get("SqlSource:lex", "opened"), json!(true));
    // rewriting the mode re-opens too: ro -> rw lets a write through
    query(&h, "ins", "lex", "INSERT INTO t (name) VALUES ('new')");
    assert_eq!(run(&h, "ins", "a", json!([]))["status"], json!("error"));
    h.write("SqlSource:lex", "mode", json!("rw"), "person");
    h.pump();
    let r = run(&h, "ins", "b", json!([]));
    assert_eq!(r["changes"], json!(1), "{r}");
}

#[test]
fn a_model_write_of_the_seats_own_store_path_is_refused_with_a_visible_reason() {
    let d = tmp("modelown");
    let h = host_at(&d);
    h.born("Probe:x", "Probe", props(json!({"v": 1})), "person");
    h.pump();
    // the model writes through the tools pack's writer; any writer gets the same refusal
    source(&h, "mine", &std::path::PathBuf::from(store_path(&d)), Some("rw"));
    query(&h, "q", "mine", "DELETE FROM c_field");
    let r = run(&h, "q", "1", json!([]));
    assert_eq!(r["status"], json!("error"));
    assert!(r["head"].as_str().unwrap().contains("seat's own store"), "{r}");
    assert!(h.get("SqlSource:mine", "failed")["opened"].as_str().unwrap().contains("seat's own store"));
}

#[test]
fn a_long_query_does_not_hang_the_vm() {
    let d = tmp("long");
    make_db(&d.join("src.db"));
    let h = host_at(&d);
    source(&h, "lex", &d.join("src.db"), None);
    query(&h, "slow", "lex", "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 12000000) SELECT count(*) AS n FROM c");
    query(&h, "fast", "lex", "SELECT 1 AS one");
    h.write("SqlQuery:slow", "run", json!({"id": "s"}), "person");
    let t = Instant::now();
    h.pump();
    assert!(t.elapsed() < Duration::from_secs(2), "the write returns at once");
    assert_eq!(h.get("SqlQuery:slow", "status"), json!("running"));
    // the VM answers other writes while it runs (one connection per source, queries on it are serialized, so another source/entity is free)
    h.born("Probe:y", "Probe", props(json!({"v": 5})), "person");
    h.pump();
    assert_eq!(h.get("Probe:y", "v"), json!(5));
    pump_until(&h, |h| h.get("SqlQuery:slow", "run_result")["id"] == json!("s"));
    assert_eq!(h.get("SqlQuery:slow", "rows"), json!([{"n": 12000000}]));
}

#[test]
fn the_launch_fact_only_seeds_a_source_that_does_not_exist_a_model_write_wins_over_a_restart() {
    // (#1143, the migration: the previous store is declared `legacy` by the launch fact; the model may point it elsewhere and a restart keeps its choice)
    let d = tmp("seed");
    make_db(&d.join("fact.db"));
    make_db(&d.join("mine.db"));
    let fact = |p: &str| props(json!({"sql_sources": [{"name": "legacy", "path": d.join(p).to_string_lossy(), "mode": "ro"}]}));
    {
        let h = host_at(&d);
        membrane_vm::host::boot_started(&h, &fact("fact.db"), true);
        h.pump();
        assert_eq!(h.get("SqlSource:legacy", "path"), json!(d.join("fact.db").to_string_lossy()), "the fact seeds a source that is not there");
        source(&h, "legacy", &d.join("mine.db"), None);                     // the model points it elsewhere
        assert_eq!(h.get("SqlSource:legacy", "path"), json!(d.join("mine.db").to_string_lossy()));
    }
    let h = host_at(&d);                                                      // a restart with the same fact
    membrane_vm::host::boot_started(&h, &fact("fact.db"), true);
    h.pump();
    assert_eq!(h.get("SqlSource:legacy", "path"), json!(d.join("mine.db").to_string_lossy()), "the model's choice survives the next launch");
    assert_eq!(h.get("SqlSource:legacy", "mode_eff"), json!("ro"));
}
