//! The store driver (`sql(...)`, store.rs): `.c1f` reads the seat's SQLite store directly. Read-only, bound
//! arguments, rows as dicts, a runaway query interrupted on its budget, and a reader that never waits for a writer.
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
    let dir = std::env::temp_dir().join(format!("store-driver-{}-{}", std::process::id(), rand_tag()));
    std::fs::create_dir_all(&dir).unwrap();
    let p = dir.join("field.sqlite3");
    let c = rusqlite::Connection::open(&p).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    c.execute_batch("CREATE TABLE c_events (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
    for (k, sess, text, o) in [("e1", "s1", "the boat is blue", 1), ("e2", "s1", "the sail is red", 2), ("e3", "s2", "far away", 3)] {
        c.execute("INSERT INTO c_events VALUES (?1, ?2)", (k, json!({"o": o, "d": {"session": sess, "text": text}}).to_string())).unwrap();
    }
    (p, c)
}
fn open_store(p: &std::path::Path) -> Store {
    let path = p.to_str().unwrap().to_string();
    Store::with_exec(&path, std::sync::Arc::new(membrane_vm::sqlexec::NativeSqlExec::new(&path).unwrap()))
}
fn rand_tag() -> usize {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
}

const Q: &str = "SELECT k, json_extract(body, '$.d.text') AS text FROM c_events WHERE json_extract(body, '$.d.session') = ? ORDER BY json_extract(body, '$.o')";

#[test]
fn a_read_binds_its_arguments_and_returns_rows_as_dicts() {
    let (p, _w) = db();
    let st = open_store(&p);
    let rows = st.query(Q, &[json!("s1")]).unwrap();
    assert_eq!(rows, vec![json!({"k": "e1", "text": "the boat is blue"}), json!({"k": "e2", "text": "the sail is red"})]);
    assert!(st.query(Q, &[]).unwrap_err().contains("takes 1 argument"));
}

#[test]
fn only_reads_run() {
    let (p, _w) = db();
    let st = open_store(&p);
    for q in ["DELETE FROM c_events", "INSERT INTO c_events VALUES ('x', '{}')", "UPDATE c_events SET body = '{}'", "DROP TABLE c_events"] {
        let e = st.query(q, &[]).unwrap_err();
        assert!(e.contains("only reads") || e.contains("readonly") || e.contains("read-only"), "{q}: {e}");
    }
    assert_eq!(st.query("SELECT count(*) AS n FROM c_events", &[]).unwrap(), vec![json!({"n": 3})]);
}

#[test]
fn a_long_query_runs_to_its_end_and_many_rows_come_back() {
    // as the official drivers: no time limit, no row cap
    let (p, _w) = db();
    let st = open_store(&p);
    let rows = st.query("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 50000) SELECT i FROM n", &[]).unwrap();
    assert_eq!(rows.len(), 50000);
}

#[test]
fn a_reader_never_waits_for_a_writer_holding_a_transaction() {
    let (p, w) = db();
    let st = open_store(&p);
    w.execute_batch("BEGIN IMMEDIATE; INSERT INTO c_events VALUES ('e4', '{\"o\": 4, \"d\": {\"session\": \"s1\", \"text\": \"new\"}}');").unwrap();
    let t = std::time::Instant::now();
    let rows = st.query(Q, &[json!("s1")]).unwrap();                      // the committed state, at once
    assert_eq!(rows.len(), 2);
    assert!(t.elapsed().as_millis() < 100, "waited {:?}", t.elapsed());
    w.execute_batch("COMMIT;").unwrap();
    assert_eq!(st.query(Q, &[json!("s1")]).unwrap().len(), 3);
}

fn engine(p: &std::path::Path, pack: &str) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.store = Some(open_store(&p));
    c.load("Chat:a".into(), "Chat".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, p: &str, v: J) {
    let w = Write { eid: "Chat:a".into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
/// what a seat does: wait for a query outside the engine, then let the waiting getters recompute
fn arrive(c: &mut Core, f: &mut Fields) {
    let w = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    while w.pending() > 0 && t.elapsed().as_secs() < 10 { w.wait(Some(std::time::Duration::from_millis(200))); }
    let mut seq = 20;
    f.settle(c, &H, 3.0, &mut seq);
}

const TURNS: &str = "PACK t 1\n\
Chat.turns getter: FOR r IN (sql(\"SELECT body FROM c_events WHERE json_extract(body, '$.d.session') = ? ORDER BY k\", this.session) or []): prop(prop(json(prop(r, 'body')), 'd'), 'text')\n";
const RUNAWAY: &str = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) AS c FROM n";

#[test]
fn a_getter_reads_the_store_with_sql_and_json() {
    let (p, _w) = db();
    let (mut c, mut f) = engine(&p, TURNS);
    put(&mut c, &mut f, "session", json!("s1"));
    assert_eq!(c.docs["Chat:a"].props["turns"], json!([]));                 // asked: the rows are not in yet
    arrive(&mut c, &mut f);
    assert_eq!(c.docs["Chat:a"].props["turns"], json!(["the boat is blue", "the sail is red"]));
}

#[test]
fn a_runaway_query_never_hangs_the_engine_and_a_new_question_interrupts_it() {
    let (p, _w) = db();
    let pack = format!("{}Chat.spin getter: sql(this.q) if this.q else None\n", TURNS);
    let (mut c, mut f) = engine(&p, &pack);
    let t = std::time::Instant::now();
    put(&mut c, &mut f, "q", json!(RUNAWAY));                               // never ends on its own
    put(&mut c, &mut f, "session", json!("s1"));                            // the engine goes on at once
    let w = c.store.as_ref().unwrap().waiter();
    for _ in 0..50 { w.wait(Some(std::time::Duration::from_millis(50))); if w.pending() == 1 { break; } }
    std::thread::sleep(std::time::Duration::from_millis(100));
    let mut seq = 30;
    f.settle(&mut c, &H, 3.0, &mut seq);
    assert_eq!(c.docs["Chat:a"].props["turns"], json!(["the boat is blue", "the sail is red"]));
    assert!(t.elapsed().as_secs() < 5, "the engine waited {:?}", t.elapsed());
    assert_eq!(c.store.as_ref().unwrap().pending(), 1);                     // the runaway still runs, alone
    put(&mut c, &mut f, "q", json!("SELECT 7 AS c"));                       // the getter asks another question
    for _ in 0..3 { if c.docs["Chat:a"].props.get("spin") == Some(&json!([{"c": 7}])) { break; } arrive(&mut c, &mut f); }   // the runaway is interrupted, so all finish
    assert_eq!(c.docs["Chat:a"].props["spin"], json!([{"c": 7}]));
    let w = c.store.as_ref().unwrap().waiter();
    for _ in 0..50 { if w.pending() == 0 { break; } std::thread::sleep(std::time::Duration::from_millis(20)); }
    assert_eq!(w.pending(), 0, "the superseded runaway was interrupted");
}

#[test]
fn a_setter_reads_a_getters_field_not_sql() {
    let (p, _w) = db();
    let pack = "PACK t 1\nChat.go on-setter s:\n  ALWAYS:\n    this.n = sql('SELECT 1')\n";
    let (mut c, mut f) = engine(&p, pack);
    put(&mut c, &mut f, "go", json!(true));
    assert!(c.docs["Chat:a"].props.get("n").is_none());
}

#[test]
fn a_getter_reads_the_seats_jsonl_files_and_nothing_else() {
    let (p, _w) = db();
    let seat = p.parent().unwrap().parent().unwrap().join("notes.ledger.jsonl");
    std::fs::write(&seat, "{\"conv\": \"s1\", \"text\": \"ship it\", \"ts\": \"2026-11-10T10:00:00Z\"}\nnot json\n{\"conv\": \"s2\", \"text\": \"later\"}\n").unwrap();
    let pack = "PACK t 1\nChat.said getter: FOR e IN (jsonl(this.file) or []): prop(e, 'text')\n";
    let (mut c, mut f) = engine(&p, pack);
    put(&mut c, &mut f, "file", json!("notes.ledger.jsonl"));
    arrive(&mut c, &mut f);
    assert_eq!(c.docs["Chat:a"].props["said"], json!(["ship it", "later"]));
    for bad in ["../secrets.jsonl", "/etc/passwd", "notes.txt", "a/b.jsonl"] {
        put(&mut c, &mut f, "file", json!(bad));
        let r = c.docs["Chat:a"].props.get("refused").cloned().unwrap_or(J::Null);
        assert!(r.as_str().unwrap_or("").contains("not a .jsonl file of this seat"), "{bad}: {r}");
    }
}

#[test]
fn two_getters_reading_the_same_file_both_settle() {
    // the 2026-11-10 hang: two chats' banners read the same ledger; a shared answer ping-ponged between them forever
    let (p, _w) = db();
    std::fs::write(p.parent().unwrap().parent().unwrap().join("shared.jsonl"), "{\"t\": 1}\n").unwrap();
    let pack = "PACK t 1\nChat.rows getter: len(jsonl('shared.jsonl') or []) if this.go else 0\n";
    let (mut c, mut f) = engine(&p, pack);
    c.load("Chat:b".into(), "Chat".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 50;
    f.rebuild(&mut c, 1.0, &mut seq);
    for e in ["Chat:a", "Chat:b"] {
        let w = Write { eid: e.into(), prop: "go".into(), new: json!(true), old: J::Null, writer: "person".into(), chain: json!({}), seq: 51 };
        c.hear(w.clone(), None, 2.0);
        f.react(&mut c, &H, w, 2.0, &mut seq);
    }
    let w = c.store.as_ref().unwrap().waiter();
    let mut rounds = 0;
    while w.pending() > 0 && rounds < 50 { w.wait(Some(std::time::Duration::from_millis(200))); f.settle(&mut c, &H, 3.0, &mut seq); rounds += 1; }
    assert_eq!(w.pending(), 0, "the reads never settled ({} rounds)", rounds);
    assert!(rounds < 10, "{} rounds", rounds);
    assert_eq!(c.docs["Chat:a"].props["rows"], json!(1));
    assert_eq!(c.docs["Chat:b"].props["rows"], json!(1));
}
