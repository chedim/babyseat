//! The previous store's fixture shared by the legacy import tests: the real schema (`c_sessions`, `c_messages`), synthetic rows.
#![allow(dead_code)]
use serde_json::{json, Value as J};

// ---- the previous store's fixture: the real schema, synthetic rows ---------------------------------------------------------------------------------------
pub fn session(sid: &str, title: &str, owner: Option<&str>, created: i64, updated: i64) -> (String, String) {
    let mut s = json!({"model": {"id": "", "providerID": "membrane", "variant": "default"}, "path": "srv/seat", "summary": {"files": 0, "additions": 0, "deletions": 0}, "id": sid, "version": "1.0", "agent": "build", "cost": 0, "directory": "/srv/seat", "projectID": "membrane", "slug": "calm-cedar", "time": {"updated": updated, "created": created}, "title": title, "tokens": {"reasoning": 0, "input": 0, "output": 0, "cache": {"write": 0, "read": 0}, "total": 0}});
    if let Some(o) = owner { s["owner"] = json!(o); }
    (sid.to_string(), json!({"s": s}).to_string())
}
pub fn user_row(sid: &str, mid: &str, n: i64, at: i64, text: &str) -> (String, String) {
    (format!("{sid}|{mid}"), json!({"n": n, "sid": sid, "mid": mid, "row": {"info": {"role": "user", "model": {"providerID": "membrane", "modelID": "claude-agent:sonnet"}, "summary": {"diffs": []}, "id": mid, "agent": "build", "time": {"created": at}, "sessionID": sid, "_announced": true},
        "parts": [{"text": text, "id": format!("prt_{mid}"), "type": "text", "time": {"start": at}, "sessionID": sid, "messageID": mid}]}}).to_string())
}
/// an assistant row answering `parent`; `parts` as the old store holds them
pub fn asst_row(sid: &str, mid: &str, n: i64, parent: Option<&str>, at: i64, done: i64, parts: J, err: Option<J>) -> (String, String) {
    let mut info = json!({"role": "assistant", "path": {"root": "/srv/seat", "cwd": "/srv/seat"}, "id": mid, "mode": "build", "agent": "build", "cost": 0.25, "providerID": "membrane", "time": {"created": at, "completed": done},
        "tokens": {"reasoning": 0, "input": 100, "output": 20, "cache": {"write": 5, "read": 7}, "total": 132}, "modelID": "claude-agent:sonnet", "sessionID": sid, "finish": "stop", "madeBy": "claude-agent"});
    if let Some(p) = parent { info["parentID"] = json!(p); }
    if let Some(e) = err { info["error"] = e; }
    (format!("{sid}|{mid}"), json!({"n": n, "sid": sid, "mid": mid, "row": {"info": info, "parts": parts}}).to_string())
}
pub fn text_part(t: &str) -> J { json!({"id": "prt_t", "type": "text", "text": t, "time": {"start": 1, "end": 2}}) }
pub fn tool_part(id: &str, name: &str, input: J, output: &str) -> J { json!({"id": format!("prt_{id}"), "type": "tool", "callID": id, "tool": name, "state": {"status": "completed", "input": input, "output": output, "title": name, "time": {"start": 1, "end": 2}}}) }

/// the fixture: 4 sessions. ses_a: operator's (no owner), 2 questions (the first answered by two rows: a tool and a text), ses_b: owner "Ada Lovelace", one question + an orphan assistant row,
/// ses_c: owner "owner", a question nobody answered, ses_d: empty (no messages)
pub fn make_legacy(path: &std::path::Path) -> Vec<(String, String)> {
    let cx = rusqlite::Connection::open(path).unwrap();
    cx.execute_batch("CREATE TABLE c_sessions (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_messages (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_entities (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
    let sessions = vec![
        session("ses_a", "Plan the trip", None, 1_790_000_000_000, 1_790_000_100_000),
        session("ses_b", "Ada's notes", Some("Ada Lovelace"), 1_790_000_200_000, 1_790_000_300_000),
        session("ses_c", "Left open", Some("owner"), 1_790_000_400_000, 1_790_000_400_500),
        session("xcl_d", "Empty one", None, 1_790_000_500_000, 1_790_000_500_000),
    ];
    let msgs = vec![
        user_row("ses_a", "msg_u1", 10, 1_790_000_010_000, "where do we start?"),
        asst_row("ses_a", "msg_a1", 11, Some("msg_u1"), 1_790_000_010_050, 1_790_000_012_000, json!([{"id": "prt_s0", "type": "step-start"}, tool_part("toolu_1", "bash", json!({"command": "ls"}), "a.txt\nb.txt"), {"id": "prt_r", "type": "reasoning", "text": "hmm"}, {"id": "prt_s1", "type": "step-finish"}]), None),
        asst_row("ses_a", "msg_a2", 12, Some("msg_u1"), 1_790_000_012_100, 1_790_000_014_000, json!([text_part("Start with the list."), tool_part("toolu_2", "mcp__membrane__entity_read", json!({"spec": "Goal:x"}), "")]), None),
        user_row("ses_a", "msg_u2", 13, 1_790_000_020_000, "and then?"),
        asst_row("ses_a", "msg_a3", 14, Some("msg_u2"), 1_790_000_020_040, 1_790_000_021_000, json!([text_part("Then we book.")]), None),
        user_row("ses_b", "msg_u3", 20, 1_790_000_210_000, "remember this"),
        asst_row("ses_b", "msg_a4", 21, Some("msg_u3"), 1_790_000_210_030, 1_790_000_211_000, json!([text_part("Noted.")]), None),
        asst_row("ses_b", "msg_a5", 22, None, 1_790_000_250_000, 1_790_000_251_000, json!([text_part("a stray answer")]), None),
        user_row("ses_c", "msg_u4", 30, 1_790_000_410_000, "is anyone there"),
    ];
    for (k, b) in sessions.iter() { cx.execute("INSERT INTO c_sessions VALUES (?1, ?2)", [k, b]).unwrap(); }
    for (k, b) in msgs.iter() { cx.execute("INSERT INTO c_messages VALUES (?1, ?2)", [k, b]).unwrap(); }
    cx.execute("INSERT INTO c_entities VALUES ('membrane::node:x', '{}')", []).unwrap();
    sessions
}

/// a store with one BIG session `ses_big` of `users` questions (each answered by one assistant row, so 2 x `users` rows, plus 3 stray assistant rows with no user row) and `small` sessions
/// of one question and its answer each: the shape of the real store at its worst (#1143: its biggest session has 8,024 rows)
pub fn make_big_legacy(path: &std::path::Path, users: i64, small: i64) {
    let cx = rusqlite::Connection::open(path).unwrap();
    cx.execute_batch("CREATE TABLE c_sessions (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_messages (k TEXT PRIMARY KEY, body TEXT NOT NULL); BEGIN;").unwrap();
    let put_s = |s: (String, String)| { cx.execute("INSERT INTO c_sessions VALUES (?1, ?2)", [&s.0, &s.1]).unwrap(); };
    put_s(session("ses_big", "The long one", None, 1_790_000_000_000, 1_790_000_900_000));
    for i in 0..small { put_s(session(&format!("ses_s{i:04}"), "small", None, 1_790_001_000_000 + i, 1_790_001_000_000 + i)); }
    let put_m = |m: (String, String)| { cx.execute("INSERT INTO c_messages VALUES (?1, ?2)", [&m.0, &m.1]).unwrap(); };
    for i in 0..users {
        let (u, a) = (format!("msg_u{i:06}"), format!("msg_a{i:06}"));
        put_m(user_row("ses_big", &u, 2 * i + 1, 1_790_000_000_000 + 10 * i, "a question"));
        put_m(asst_row("ses_big", &a, 2 * i + 2, Some(&u), 1_790_000_000_005 + 10 * i, 1_790_000_000_008 + 10 * i, json!([text_part("an answer")]), None));
    }
    for j in 0..3 { put_m(asst_row("ses_big", &format!("msg_z{j}"), 2 * users + 1 + j, None, 1_790_000_800_000 + j, 1_790_000_800_001 + j, json!([text_part("stray")]), None)); }
    for i in 0..small {
        let sid = format!("ses_s{i:04}");
        put_m(user_row(&sid, &format!("msg_u{i}"), 1, 1_790_001_000_000 + i, "hi"));
        put_m(asst_row(&sid, &format!("msg_a{i}"), 2, Some(&format!("msg_u{i}")), 1_790_001_000_001 + i, 1_790_001_000_002 + i, json!([text_part("hello")]), None));
    }
    cx.execute_batch("COMMIT;").unwrap();
}
