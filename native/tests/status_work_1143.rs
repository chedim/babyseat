//! `GET /session/status` is one pass over the sessions, not one pass over the turns per session (#1143, 2026-11-14: 14 s with 1,600 sessions and 1,600 turns, 51 s on the
//! operator's imported store, the sql thread busy for the whole request). The test runs the pack's own query on a store of N sessions and N turns and COUNTS the sqlite
//! virtual-machine instructions (a progress handler, no clock): doubling the store must not quadruple the work.
use rusqlite::Connection;
use rusqlite::StatementStatus;

fn query() -> String {
    let src = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/packs/door_session.c1f")).unwrap();
    let line = src.lines().find(|l| l.starts_with("Request.ss_stat getter: sql(")).expect("ss_stat");
    let a = line.find("sql('").unwrap() + 5;
    let b = line.find("') if this.ss_route").unwrap();
    line[a..b].replace("\\'", "'")
}

/// (the answer, the instructions run) on a store of `n` sessions with one turn each; every 10th turn is running.
fn run(n: usize) -> (serde_json::Value, u64) {
    let c = Connection::open_in_memory().unwrap();
    c.execute_batch("CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL, cls TEXT, updated REAL NOT NULL DEFAULT 0); CREATE INDEX c_field_cls ON c_field(cls, k);").unwrap();
    let pad = "x".repeat(2000);
    for i in 0..n {
        let sid = format!("ses_{i:06}");
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (format!("membrane::Chat:session:{sid}"), r#"{"props":{"stitle":"t"}}"#)).unwrap();
        let state = if i % 10 == 0 { "running" } else { "done" };
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')",
            (format!("membrane::Chat:session:{sid}:message:{i:013}-00000001:turn"), format!(r#"{{"props":{{"state":"{state}","pad":"{pad}"}}}}"#))).unwrap();
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (format!("membrane::Chat:session:{sid}:message:{i:013}-00000001"), r#"{"props":{}}"#)).unwrap();
    }
    let mut st = c.prepare(&query()).unwrap();
    let out: String = st.query_row([], |r| r.get(0)).unwrap();
    let steps = st.get_status(StatementStatus::VmStep) as u64;
    (serde_json::from_str(&out).unwrap(), steps)
}

#[test]
fn the_status_query_does_not_scan_the_turns_once_per_session() {
    let (a, w1) = run(300);
    let (b, w2) = run(600);
    assert_eq!(a.as_object().unwrap().len(), 300);
    assert_eq!(b.as_object().unwrap().len(), 600);
    assert_eq!(b["ses_000000"], serde_json::json!({"type": "busy"}));
    assert_eq!(b["ses_000001"], serde_json::json!({"type": "idle"}));
    assert_eq!(b.as_object().unwrap().values().filter(|v| v["type"] == "busy").count(), 60);
    eprintln!("work at 300 sessions: {w1}, at 600: {w2}");
    assert!(w2 < w1 * 5 / 2, "doubling the sessions took {w2} against {w1}: more than twice (a pass over the turns per session)");
}
