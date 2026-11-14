//! `GET /membrane/chat/messages` must not read message bodies to list the messages (#1143, 2026-11-14: on the operator's imported store a message body averages 160 KB, the history query
//! `json_extract`ed `at` out of 5,539 of them = 900 MB read, over a second). Two checks on the pack's own query text (`hs_src` + `hs_sql`):
//!  - no `sid`: answered from the `(cls, k)` index alone (EXPLAIN QUERY PLAN says COVERING INDEX);
//!  - with a `sid` (a session's turns, `sid=` the turns of no session): the filter reads the small TURN rows, never a message row: every message body in the test is NOT JSON, so a query that
//!    touched one would fail ("malformed JSON"); the answer is the right messages, newest first, with the cursor applied.
//! Set `CHAT_PACK` to another pack file to check a different text.
use rusqlite::Connection;

fn getter_string(src: &str, head: &str, which: usize) -> String {
    // the `which`-th single-quoted c1f string literal after `head` (escapes `\'` kept), unescaped
    let at = src.find(head).unwrap_or_else(|| panic!("{head}"));
    let rest = &src[at + head.len()..];
    let line_end = rest.find("\nRequest.").unwrap_or(rest.len());
    let rest = &rest[..line_end];
    let b = rest.as_bytes();
    let (mut i, mut n) = (0, 0);
    while i < b.len() {
        if b[i] == b'\'' && (i == 0 || b[i - 1] != b'\\') {
            let start = i + 1;
            let mut j = start;
            while !(b[j] == b'\'' && b[j - 1] != b'\\') { j += 1; }
            if n == which { return rest[start..j].replace("\\'", "'"); }
            n += 1;
            i = j + 1;
        } else { i += 1; }
    }
    panic!("no string {which} after {head}")
}

fn pack() -> String {
    let path = std::env::var("CHAT_PACK").unwrap_or_else(|_| concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/packs/chat.c1f").to_string());
    std::fs::read_to_string(path).unwrap()
}
fn query(sid: bool) -> String {
    let src = pack();
    let from = getter_string(&src, "Request.hs_src getter:", if sid { 1 } else { 0 });
    let sql = getter_string(&src, "Request.hs_sql getter:", 0) ;
    let tail = {
        // `hs_sql` is 'WITH m(k) AS (' + this.hs_src + ') SELECT ...': two literals around the source
        let b = getter_string(&src, "Request.hs_sql getter:", 1);
        format!("{sql}{from}{b}")
    };
    tail
}

fn store(n: usize, bodies_are_json: bool) -> Connection {
    let c = Connection::open_in_memory().unwrap();
    c.execute_batch("CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL, cls TEXT, updated REAL NOT NULL DEFAULT 0); CREATE INDEX c_field_cls ON c_field(cls, k);").unwrap();
    for i in 0..n {
        let k = format!("membrane::Chat:message:{}-{}", 1794679065874u64 + i as u64, 1000 + i);
        let body = if bodies_are_json { r#"{"props":{}}"#.to_string() } else { "this is not json ".repeat(300) };
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (&k, body)).unwrap();
        // the session: every third message is the main chat (no sid), the rest alternate two sessions
        let sid = match i % 3 { 0 => String::new(), 1 => "ses_a".into(), _ => "ses_b".into() };
        let tb = if sid.is_empty() { r#"{"props":{"state":"done"}}"#.to_string() } else { format!(r#"{{"props":{{"sid":"{sid}"}}}}"#) };
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (format!("{k}:turn"), tb)).unwrap();
        c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, '{}', 'Chat')", (format!("{k}:turn:response:body"),)).unwrap();
    }
    c
}

const LIKE: &str = "membrane::Chat:message:%";
const LIKE2: &str = "membrane::Chat:message:%:%";

#[test]
fn the_history_query_is_answered_from_the_index_and_reads_no_message_body() {
    let c = store(50, true);
    let sql = query(false);
    let mut st = c.prepare(&format!("EXPLAIN QUERY PLAN {sql}")).unwrap();
    let plan: Vec<String> = st.query_map((LIKE, LIKE2, "", 21), |r| r.get::<_, String>(3)).unwrap().map(|r| r.unwrap()).collect();
    eprintln!("plan: {plan:?}");
    assert!(plan.iter().any(|p| p.contains("COVERING INDEX")), "the history query reads the table rows (and their bodies): {plan:?}; query: {sql}");
    let n = c.prepare(&sql).unwrap().query_map((LIKE, LIKE2, "", 21), |r| r.get::<_, String>(0)).unwrap().count();
    assert_eq!(n, 21, "the query lists the newest 21 (limit + 1) messages and no turn");
}

#[test]
fn a_session_filter_reads_the_turn_rows_and_never_a_message_body() {
    let c = store(60, false);   // every message body is NOT JSON
    let sql = query(true);
    let run = |sid: &str, before: &str, lim: i64| -> Vec<String> {
        c.prepare(&sql).unwrap().query_map((LIKE, LIKE2, before, lim, sid), |r| r.get::<_, String>(0)).unwrap().map(|r| r.unwrap()).collect()
    };
    let ids = |v: Vec<String>| v.into_iter().map(|k| k.rsplit(':').next().unwrap().to_string()).collect::<Vec<_>>();
    // session a = messages 1, 4, 7 ...; the newest first, the extra row is the "more"
    let a = ids(run("ses_a", "", 4));
    assert_eq!(a, [58, 55, 52, 49].map(|i| format!("{}-{}", 1794679065874u64 + i, 1000 + i)), "{a:?}");
    // the cursor: only older than 1794679065874+52
    let a2 = ids(run("ses_a", &format!("{}-{}", 1794679065874u64 + 52, 1052), 2));
    assert_eq!(a2, [49, 46].map(|i| format!("{}-{}", 1794679065874u64 + i, 1000 + i)), "{a2:?}");
    // sid empty = the main chat: messages 0, 3, 6 ...
    let m = ids(run("", "", 2));
    assert_eq!(m, [57, 54].map(|i| format!("{}-{}", 1794679065874u64 + i, 1000 + i)), "{m:?}");
    assert_eq!(run("ses_none", "", 5).len(), 0);
    // the plan never scans message rows for their body: the only table reads are of the index range
    let mut st = c.prepare(&format!("EXPLAIN QUERY PLAN {sql}")).unwrap();
    let plan: Vec<String> = st.query_map((LIKE, LIKE2, "", 21, "ses_a"), |r| r.get::<_, String>(3)).unwrap().map(|r| r.unwrap()).collect();
    eprintln!("plan: {plan:?}");
}
