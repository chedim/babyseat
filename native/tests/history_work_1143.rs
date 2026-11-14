//! `GET /membrane/chat/messages` must not read message bodies to list the messages (#1143, 2026-11-14: on the operator's imported store a message body averages 160 KB, the history query
//! `json_extract`ed `at` out of 5,539 of them = 900 MB read, over a second, the route gave up and answered `{"messages": null}`, and the chat view stayed on "waiting for a turn").
//! The test runs the pack's own query and asks sqlite for its plan: it must be answered from the `(cls, k)` index alone (a COVERING INDEX), which never touches a body.
//! Set `CHAT_PACK` to another pack file to check a different text.
use rusqlite::Connection;

fn query() -> String {
    let path = std::env::var("CHAT_PACK").unwrap_or_else(|_| concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/packs/chat.c1f").to_string());
    let src = std::fs::read_to_string(path).unwrap();
    let line = src.lines().find(|l| l.starts_with("Request.hs_rows getter: sql(")).expect("hs_rows");
    let a = line.find("sql('").unwrap() + 5;
    let b = line.find("', 'membrane::Chat:message:%'").unwrap();
    line[a..b].replace("\\'", "'")
}

#[test]
fn the_history_query_is_answered_from_the_index_and_reads_no_message_body() {
    let c = Connection::open_in_memory().unwrap();
    c.execute_batch("CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL, cls TEXT, updated REAL NOT NULL DEFAULT 0); CREATE INDEX c_field_cls ON c_field(cls, k);").unwrap();
    let pad = "x".repeat(5000);
    for i in 0..50 {
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (format!("membrane::Chat:message:{}-{}", 1794679065874u64 + i, 1000 + i), format!(r#"{{"props":{{"at":1794679065.874,"pad":"{pad}"}}}}"#))).unwrap();
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (format!("membrane::Chat:message:{}-{}:turn", 1794679065874u64 + i, 1000 + i), "{}")).unwrap();
    }
    let sql = query();
    let mut st = c.prepare(&format!("EXPLAIN QUERY PLAN {sql}")).unwrap();
    let plan: Vec<String> = st.query_map(("membrane::Chat:message:%", "membrane::Chat:message:%:%", "", 21), |r| r.get::<_, String>(3)).unwrap().map(|r| r.unwrap()).collect();
    eprintln!("plan: {plan:?}");
    assert!(plan.iter().any(|p| p.contains("COVERING INDEX")), "the history query reads the table rows (and their bodies): {plan:?}; query: {sql}");
    let n: i64 = c.prepare(&sql).unwrap().query_map(("membrane::Chat:message:%", "membrane::Chat:message:%:%", "", 21), |r| r.get::<_, String>(0)).unwrap().count() as i64;
    assert_eq!(n, 21, "the query lists the newest 21 (limit + 1) messages and no turn");
}
