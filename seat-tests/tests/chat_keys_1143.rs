//! The session id is in the message's key (#1143, operator 2026-11-14; docs/chat-keys.md): the messages of a session are a KEY RANGE of the `(cls, k)` index, for both routes:
//! `GET /membrane/chat/messages` (`hs_sql_msg` per session, `hs_sql_time` for every turn) and `GET /session/<sid>/message` (`ml_sql`). Each query text is read out of its pack and run on an in-memory store
//! with the real table and index: the plan must be a SEARCH of the covering index with a `k` range (no table access, so no message body is read, and no sort), and every message body in the test is
//! NOT JSON, so a query that touched one would fail ("malformed JSON"). The answers are checked against the keys: newest first, one session only, children skipped, the cursor applied, the page bounded.
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


fn pack(name: &str) -> String { std::fs::read_to_string(format!("{}/../membrane/packs/{name}.c1f", env!("CARGO_MANIFEST_DIR"))).unwrap() }
fn q(pack_name: &str, head: &str) -> String { getter_string(&pack(pack_name), head, 0) }

const MSG: &str = "membrane::Chat:session:";
fn id(i: u64) -> String { format!("{}-{:08}", 1794679065874u64 + i, 1000 + i) }

/// 3 sessions (`ses_a`, `ses_ab` whose name starts with the first's, `ses_c`) and the main chat, messages round-robin, each with children (body, turn, answer, two tools) and a time row
fn store(n: u64) -> Connection {
    let c = Connection::open_in_memory().unwrap();
    c.execute_batch("CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL, cls TEXT, updated REAL NOT NULL DEFAULT 0); CREATE INDEX c_field_cls ON c_field(cls, k);").unwrap();
    let junk = "this is not json ".repeat(300);
    let put = |k: &str| { c.execute("INSERT INTO c_field(k, body, cls) VALUES (?1, ?2, 'Chat')", (k, &junk)).unwrap(); };
    for s in ["ses_a", "ses_ab", "ses_c"] { put(&format!("{MSG}{s}")); }
    put("membrane::Chat:main");
    for i in 0..n {
        let (chat, wh) = match i % 4 { 0 => ("membrane::Chat:main".to_string(), "main".to_string()), 1 => (format!("{MSG}ses_a"), "session:ses_a".into()), 2 => (format!("{MSG}ses_ab"), "session:ses_ab".into()), _ => (format!("{MSG}ses_c"), "session:ses_c".into()) };
        let m = format!("{chat}:message:{}", id(i));
        for suf in ["", ":body", ":turn", ":turn:response", ":turn:response:body", ":turn:tool:t1", ":turn:tool:t2"] { put(&format!("{m}{suf}")); }
        put(&format!("membrane::Chat:main:time:{}:{wh}", id(i)));
    }
    c
}
fn plan(c: &Connection, sql: &str, args: impl rusqlite::Params) -> Vec<String> {
    let mut st = c.prepare(&format!("EXPLAIN QUERY PLAN {sql}")).unwrap();
    st.query_map(args, |r| r.get::<_, String>(3)).unwrap().map(|r| r.unwrap()).collect()
}
fn keys(c: &Connection, sql: &str, args: impl rusqlite::Params) -> Vec<String> {
    c.prepare(sql).unwrap().query_map(args, |r| r.get::<_, String>(0)).unwrap().map(|r| r.unwrap()).collect()
}
fn end(prefix: &str) -> String { format!("{};", &prefix[..prefix.len() - 1]) }
fn assert_range_plan(p: &[String], sql: &str) {
    eprintln!("plan: {p:?}");
    assert!(p.iter().any(|l| l.contains("SEARCH") && l.contains("COVERING INDEX c_field_cls") && l.contains("cls=?") && l.contains("k>?") && l.contains("k<?")), "not a covering-index key range: {p:?}; {sql}");
    assert!(!p.iter().any(|l| l.contains("TEMP B-TREE")), "the query sorts: {p:?}");
    assert!(!sql.contains("json_extract") && !sql.contains("body"), "the query names a body: {sql}");
}
fn ids(v: Vec<String>) -> Vec<String> { v.into_iter().map(|k| k.rsplit(':').next().unwrap().to_string()).collect() }

#[test]
fn a_sessions_history_page_is_a_key_range_of_the_index_and_reads_no_body() {
    let sql = q("chat", "Request.hs_sql_msg getter:");
    let c = store(60);
    let p = format!("{MSG}ses_a:message:");
    assert_range_plan(&plan(&c, &sql, (&p, end(&p), 21)), &sql);
    // session a = messages 1, 5, 9 ...: the newest first (limit + 1 = the "more" probe), no child, no other session (`ses_ab` shares the name's start)
    let a = ids(keys(&c, &sql, (&p, end(&p), 4)));
    assert_eq!(a, [57, 53, 49, 45].map(id), "{a:?}");
    // the cursor: the key itself is the end of the range
    let cut = format!("{p}{}", id(49));
    let a2 = ids(keys(&c, &sql, (&p, &cut, 2)));
    assert_eq!(a2, [45, 41].map(id), "{a2:?}");
    // the main chat is its own range
    let m = format!("membrane::Chat:main:message:");
    assert_eq!(ids(keys(&c, &sql, (&m, end(&m), 2))), [56, 52].map(id));
    // an unknown session is empty, and the work is the page: 15 messages in the session, 3 asked, 3 returned
    let none = format!("{MSG}ses_none:message:");
    assert!(keys(&c, &sql, (&none, end(&none), 5)).is_empty());
    assert_eq!(keys(&c, &sql, (&p, end(&p), 3)).len(), 3);
}

#[test]
fn the_history_of_every_turn_is_a_key_range_of_the_time_rows_across_sessions() {
    let sql = q("chat", "Request.hs_sql_time getter:");
    let c = store(60);
    let p = "membrane::Chat:main:time:".to_string();
    assert_range_plan(&plan(&c, &sql, (&p, end(&p), 21)), &sql);
    let top = keys(&c, &sql, (&p, end(&p), 6));
    // time order across the chats: the newest message of ANY chat first
    assert_eq!(top.iter().map(|k| k[p.len()..p.len() + id(0).len()].to_string()).collect::<Vec<_>>(), [59, 58, 57, 56, 55, 54].map(id), "{top:?}");
    assert!(top[0].ends_with(":session:ses_c") && top[1].ends_with(":session:ses_ab") && top[3].ends_with(":main"), "{top:?}");
    // the cursor works across sessions: older than message 57
    let cut = format!("{p}{}", id(57));
    assert_eq!(top_ids(keys(&c, &sql, (&p, &cut, 3)), &p), [56, 55, 54].map(id));
}
fn top_ids(v: Vec<String>, p: &str) -> Vec<String> { v.into_iter().map(|k| k[p.len()..p.len() + id(0).len()].to_string()).collect() }

#[test]
fn the_session_api_message_list_is_a_key_range_of_the_index_and_reads_no_body() {
    let sql = q("door_session", "Request.ml_sql getter:");
    let c = store(60);
    let p = format!("{MSG}ses_c:message:");
    assert_range_plan(&plan(&c, &sql, (&p, end(&p), 5)), &sql);
    // no limit: -1 = the session's whole range, still only its own messages
    let all = ids(keys(&c, &sql, (&p, end(&p), -1)));
    assert_eq!(all.len(), 15, "{all:?}");
    assert_eq!(all[0], id(59));
    let page = ids(keys(&c, &sql, (&p, format!("{p}{}", id(51)), 2)));
    assert_eq!(page, [47, 43].map(id));
}

#[test]
fn the_plan_cost_is_the_page_not_the_store() {
    // the same query on a store ten times larger touches the same page: the rows it returns are the page and the plan is the same range search
    let sql = q("chat", "Request.hs_sql_msg getter:");
    let (small, big) = (store(40), store(400));
    let p = format!("{MSG}ses_a:message:");
    assert_eq!(plan(&small, &sql, (&p, end(&p), 21)), plan(&big, &sql, (&p, end(&p), 21)));
    assert_eq!(keys(&small, &sql, (&p, end(&p), 6)).len(), 6);
    assert_eq!(keys(&big, &sql, (&p, end(&p), 6)).len(), 6);
}

#[test]
fn a_message_id_sorts_as_time_because_its_request_number_is_padded() {
    // `...-9` must sort before `...-10`: the pack pads n to 8 digits wherever it makes an id (chat.c1f, door_session.c1f, legacy_import.c1f)
    for (name, n) in [("chat", 2), ("door_session", 1), ("legacy_import", 1)] {
        let t = pack(name);
        assert_eq!(t.matches("tail('00000000' + ").count() >= n, true, "{name} makes an unpadded message id");
    }
}
