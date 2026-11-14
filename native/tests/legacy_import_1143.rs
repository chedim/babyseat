//! The chat history import of the migration (#1143, operator 2026-11-14, phase 1): the previous store's sessions and messages become the new seat's
//! `ChatSession` / `ChatMessage` / `ChatTurn` / `ChatResponse` / `ChatTool` entities, written by `membrane/packs/legacy_import.c1f` through the read-only
//! `SqlSource:legacy` (the Sql driver). The fixtures are SMALL SQLite files with the previous store's real schema (`c_sessions`, `c_messages`: `k TEXT PRIMARY KEY, body TEXT`)
//! and synthetic text; the real copy is used only by the `#[ignore]`d measuring test at the end (read-only). No model runs and no `claude` exists here.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
mod legacy_fixture;
use legacy_fixture::*;

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn pack(n: &str) -> String { std::fs::read_to_string(repo().join(format!("membrane/packs/{n}.c1f"))).unwrap_or_else(|e| panic!("{n}.c1f: {e}")) }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("legacy-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(d.join(".membrane")).unwrap();
    d.canonicalize().unwrap()
}
fn store_path(d: &std::path::Path) -> String { d.join(".membrane/field.sqlite3").to_string_lossy().to_string() }
fn ticking() -> Box<dyn Fn() -> f64 + Send + Sync> {
    let n = Arc::new(AtomicU64::new(0));
    Box::new(move || 1_800_000_000.0 + n.fetch_add(1, Ordering::SeqCst) as f64)
}
const PACKS: &[&str] = &["entity", "boot", "fs", "proc", "fetch", "sql", "store", "door", "semtext", "mentions", "chat", "chat_turn", "door_session", "door_auth", "legacy_import", "legacy_persons"];
fn host_at(d: &std::path::Path) -> Host {
    let h = Host::open({ let mut o = Options::new(Some(store_path(d))); o.clock = ticking(); o }).unwrap();
    for n in PACKS { h.load_pack(n, &pack(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.load_stored_entities();
    h.rebuild();
    membrane_vm::host::boot_started(&h, &Map::new(), true);
    h.pump();
    h
}
fn pump_until(h: &Host, what: &str, mut done: impl FnMut(&Host) -> bool) {
    let wt = h.waiter();
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(60) {
        h.pump();
        if done(h) { return; }
        if let Some(w) = &wt { w.wait(Some(Duration::from_millis(20))); }
    }
    panic!("timed out waiting for {what}: seat = {}", h.snapshot().iter().filter(|(k, _)| k.contains("Legacy") || k.contains("SqlQuery")).map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join("\n"));
}

fn sha(path: &std::path::Path) -> String {
    let out = std::process::Command::new("shasum").args(["-a", "256"]).arg(path).output().unwrap();
    String::from_utf8_lossy(&out.stdout).split_whitespace().next().unwrap().to_string()
}
fn legacy_source(h: &Host, path: &std::path::Path) {
    h.born("SqlSource:legacy", "SqlSource", props(json!({"path": path.to_string_lossy()})), "person");
    h.pump();
}
fn import(h: &Host, req: J) {
    h.write("membrane::Legacy:seat", "import", req, "person");
    h.pump();
}
fn settled(h: &Host) { pump_until(h, "the import to end", |h| h.get("membrane::Legacy:seat", "running") == json!(false) && h.get("membrane::Legacy:seat", "cur").is_null()); }

#[test]
fn the_headers_import_makes_every_session_a_chat_session_with_a_pending_ledger_and_the_owner_mapped() {
    let d = tmp("headers");
    make_legacy(&d.join("old.sqlite"));
    let h = host_at(&d);
    legacy_source(&h, &d.join("old.sqlite"));
    h.write("membrane::Legacy:seat", "list", json!({"id": "1"}), "person");
    pump_until(&h, "the headers", |h| !h.get("membrane::Chat:session:xcl_d", "stitle").is_null());
    for (sid, title, born, upd) in [("ses_a", "Plan the trip", 1_790_000_000_000i64, 1_790_000_100_000i64), ("ses_b", "Ada's notes", 1_790_000_200_000, 1_790_000_300_000), ("ses_c", "Left open", 1_790_000_400_000, 1_790_000_400_500), ("xcl_d", "Empty one", 1_790_000_500_000, 1_790_000_500_000)] {
        let e = format!("membrane::Chat:session:{sid}");
        assert_eq!(h.get(&e, "stitle"), json!(title));
        assert_eq!(h.get(&e, "born"), json!(born));
        assert_eq!(h.get(&e, "upd"), json!(upd));
        assert_eq!(h.get(&format!("membrane::Legacy:session:{sid}"), "state"), json!("pending"), "{sid}");
        assert_eq!(h.get(&format!("membrane::Legacy:session:{sid}"), "converter"), json!(1));
    }
    // the owner mapping: no owner -> the operator person (the fact `operator`, else `owner`); a named owner -> Person:<slug>, made now; the owner called "owner" is the operator's sub too
    assert_eq!(h.get("membrane::Chat:session:ses_a", "owner"), json!("Person:owner"));
    assert_eq!(h.get("membrane::Chat:session:ses_b", "owner"), json!("Person:ada-lovelace"));
    assert_eq!(h.get("Person:ada-lovelace", "name"), json!("Ada Lovelace"));
    assert_eq!(h.get("Person:ada-lovelace", "sub"), json!("ada-lovelace"));
    assert_eq!(h.get("membrane::Chat:session:ses_c", "owner"), json!("Person:owner"));
    assert_eq!(h.get("Person:owner", "imported"), json!(true), "a session that NAMES the owner `owner` makes that Person; ownerless ones (ses_a, xcl_d) make none by themselves");
    // the unmapped assistant row of ses_b is a visible note on its ledger, with the reason
    assert_eq!(h.get("membrane::Legacy:session:ses_b", "unmapped"), json!(1));
    assert_eq!(h.get("membrane::Legacy:session:ses_b", "unmapped_rows"), json!([{"mid": "msg_a5", "n": 22}]));
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "old_rows"), json!(5));
    // idempotent: a second list changes nothing
    let before = h.get("membrane::Legacy:session:ses_a", "imported_at");
    h.write("membrane::Legacy:seat", "list", json!({"id": "2"}), "person");
    pump_until(&h, "the second list", |h| h.get("SqlQuery:legacy:sessions", "run_result")["id"] == json!("list:2"));
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "imported_at"), before);
    assert_eq!(h.get("membrane::Chat:session:ses_b", "owner"), json!("Person:ada-lovelace"));
}

#[test]
fn the_batch_import_makes_messages_turns_responses_and_tools_from_the_old_rows_and_a_done_session_is_never_imported_twice() {
    let d = tmp("batch");
    let old = d.join("old.sqlite");
    make_legacy(&old);
    let h = host_at(&d);
    legacy_source(&h, &old);
    import(&h, json!({"id": "i1", "sessions": "all", "page": 1}));
    settled(&h);
    // ses_a: two questions. The first (n 10, created 1790000010000) has two answering rows: one tool, then a tool and a text
    let m1 = "membrane::Chat:message:1790000010000-10";
    assert_eq!(h.get(m1, "text"), json!("where do we start?"));
    assert_eq!(h.get(m1, "sid"), json!("ses_a"));
    assert_eq!(h.get(m1, "pick"), json!("claude-agent:sonnet"));
    assert_eq!(h.get(m1, "legacy_src")["mid"], json!("msg_u1"));
    assert_eq!(h.get(m1, "legacy_src")["answers"], json!(2));
    assert_eq!(h.get(&format!("{m1}:body"), "text"), json!("where do we start?"));
    assert_eq!(h.get(&format!("{m1}:turn"), "status"), json!("done"));
    assert_eq!(h.get(&format!("{m1}:turn:response"), "text"), json!("Start with the list."));
    assert_eq!(h.get(&format!("{m1}:turn:tool:toolu_1"), "name"), json!("bash"));
    assert_eq!(h.get(&format!("{m1}:turn:tool:toolu_1"), "input"), json!({"command": "ls"}));
    assert_eq!(h.get(&format!("{m1}:turn:tool:toolu_1"), "result"), json!("a.txt\nb.txt"));
    assert_eq!(h.get(&format!("{m1}:turn:tool:toolu_2"), "name"), json!("mcp__membrane__entity_read"));
    assert_eq!(h.get(m1, "tool_ids"), json!("toolu_1 toolu_2"), "the tools in the order they were called");
    assert_eq!(h.get(&format!("{m1}:turn"), "usage"), json!({"input_tokens": 200, "output_tokens": 40, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 14}), "the two answering rows' numbers are summed");
    // the unanswered question of ses_c is a turn in error that says so; the orphan answer of ses_b is a note, not a message
    assert_eq!(h.get("membrane::Chat:message:1790000410000-30:turn", "status"), json!("error"));
    assert!(h.get("membrane::Chat:message:1790000410000-30:turn:response", "text").as_str().unwrap().contains("no answer"), "visible, never silently dropped");
    assert!(h.get("membrane::Chat:message:1790000250000-22", "text").is_null(), "the stray answer is not made a message");
    // the ledgers: done, the old rows are all accounted for (rows consumed + unmapped = old rows), the reasoning part is counted as dropped
    for (sid, old_rows, unm) in [("ses_a", 5, 0), ("ses_b", 3, 1), ("ses_c", 1, 0), ("xcl_d", 0, 0)] {
        let l = format!("membrane::Legacy:session:{sid}");
        assert_eq!(h.get(&l, "state"), json!("done"), "{sid}: {}", h.get(&l, "reason"));
        assert_eq!(h.get(&l, "rows").as_i64().unwrap() + h.get(&l, "unmapped").as_i64().unwrap(), old_rows, "{sid}: every old row is a message part or a noted one");
        assert_eq!(h.get(&l, "unmapped"), json!(unm));
        assert!(!h.get(&l, "imported_at").is_null());
    }
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "dropped_parts"), json!(1), "the reasoning part has no place in the new rows and is counted");
    assert_eq!(h.get("membrane::Legacy:seat", "run_done"), json!(4));
    // a done session is never imported twice: a second `all` makes nothing and runs no query
    let q = h.get("SqlQuery:legacy:msgs", "run_result")["id"].clone();
    import(&h, json!({"id": "i2", "sessions": "all", "page": 1}));
    settled(&h);
    assert_eq!(h.get("membrane::Legacy:seat", "run_total"), json!(0));
    assert_eq!(h.get("SqlQuery:legacy:msgs", "run_result")["id"], q, "no page query ran for a done session");
}

#[test]
fn an_import_without_a_page_is_refused_with_the_reason_and_names_no_default() {
    let d = tmp("nopage");
    make_legacy(&d.join("old.sqlite"));
    let h = host_at(&d);
    legacy_source(&h, &d.join("old.sqlite"));
    import(&h, json!({"id": "x", "sessions": "all"}));
    assert!(h.get("membrane::Legacy:seat", "refused").as_str().unwrap().contains("page"), "{}", h.get("membrane::Legacy:seat", "refused"));
    assert_ne!(h.get("membrane::Legacy:seat", "running"), json!(true));
}

#[test]
fn a_session_that_fails_midway_is_an_error_with_the_reason_keeps_what_was_written_and_a_new_request_resumes_from_its_cursor() {
    let d = tmp("resume");
    let (old, good) = (d.join("old.sqlite"), d.join("good.sqlite"));
    make_legacy(&old);
    std::fs::copy(&old, &good).unwrap();
    // the second answer of ses_a has parts that are not JSON objects: SQLite refuses the page that reads them (the first page, one user row, is fine)
    rusqlite::Connection::open(&old).unwrap().execute("UPDATE c_messages SET body = replace(body, '\"parts\":[{\"id\":\"prt_t\"', '\"parts\":[\"oops\",{\"id\":\"prt_t\"') WHERE k = 'ses_a|msg_a3'", []).unwrap();
    let h = host_at(&d);
    legacy_source(&h, &old);
    import(&h, json!({"id": "i1", "sessions": ["ses_a", "ses_b"], "page": 1}));
    settled(&h);
    let a = "membrane::Legacy:session:ses_a";
    assert_eq!(h.get(a, "state"), json!("error"), "{}", h.get(a, "reason"));
    assert!(h.get(a, "reason").as_str().unwrap().to_lowercase().contains("json"), "SQLite's own words: {}", h.get(a, "reason"));
    assert_eq!(h.get(a, "cursor"), json!(10), "the first page was written with its ledger step");
    assert_eq!(h.get(a, "rows"), json!(3));
    assert!(!h.get("membrane::Chat:message:1790000010000-10", "text").is_null(), "what was written stays");
    assert_eq!(h.get("membrane::Legacy:session:ses_b", "state"), json!("done"), "one session's failure does not stop the next");
    assert_eq!(h.get("membrane::Legacy:seat", "run_error"), json!(1));
    assert_eq!(h.get("membrane::Legacy:session:ses_c", "state"), json!("pending"), "a session not named is left as it was");
    // the source is pointed at the good copy (a model write; the next run opens it) and the session is named again: it resumes at the cursor
    h.write("SqlSource:legacy", "path", json!(good.to_string_lossy()), "person");
    h.pump();
    import(&h, json!({"id": "i2", "sessions": ["ses_a"], "page": 1}));
    settled(&h);
    assert_eq!(h.get(a, "state"), json!("done"));
    assert_eq!(h.get(a, "reason"), J::Null);
    assert_eq!(h.get(a, "rows"), json!(5), "no row counted twice");
    assert_eq!(h.get("membrane::Legacy:seat", "run_total"), json!(1));
    let msgs = |sid: &str| h.snapshot().iter().filter(|(k, v)| k.starts_with("membrane::Chat:message:") && !k[23..].contains(':') && v["props"]["sid"] == json!(sid)).count();
    assert_eq!((msgs("ses_a"), msgs("ses_b")), (2, 1), "none doubled");
}

#[test]
fn a_headers_run_that_fails_ends_the_import_with_the_reason() {
    let d = tmp("hfail");
    legacy_source(&host_at(&d), &d.join("missing.sqlite"));
    let h = host_at(&d);
    import(&h, json!({"id": "x", "sessions": "all", "page": 5}));
    pump_until(&h, "the refusal", |h| h.get("membrane::Legacy:seat", "running") == json!(false));
    let r = h.get("membrane::Legacy:seat", "refused");
    assert!(r.as_str().unwrap().contains("could not be read"), "{r}");
}

#[test]
fn the_previous_store_is_never_written_the_file_is_identical_after_an_import_and_a_write_through_the_source_is_refused() {
    let d = tmp("readonly");
    let old = d.join("old.sqlite");
    make_legacy(&old);
    let before = sha(&old);
    let h = host_at(&d);
    legacy_source(&h, &old);
    import(&h, json!({"id": "i1", "sessions": "all", "page": 3}));
    settled(&h);
    // a write through the `ro` source reads as SQLite's own refusal, the file is untouched
    h.born("SqlQuery:evil", "SqlQuery", props(json!({"source": "legacy", "sql": "DELETE FROM c_messages"})), "person");
    h.write("SqlQuery:evil", "run", json!({"id": "w"}), "person");
    pump_until(&h, "the refusal", |h| h.get("SqlQuery:evil", "run_result")["id"] == json!("w"));
    assert_eq!(h.get("SqlQuery:evil", "run_result")["status"], json!("error"));
    assert_eq!(sha(&old), before, "the previous store is byte-identical after the import and the refused write");
    assert!(!old.with_extension("sqlite-wal").exists() && !old.with_extension("sqlite-shm").exists(), "no WAL or lock file was made beside it");
}

#[test]
fn the_barest_seat_has_no_persons_and_still_imports_with_the_owner_named_on_the_session() {
    // the barest seat's packs (membrane/apps/barest.packs) have no door_auth: legacy_import loads without it, legacy_persons is the full seat's
    let d = tmp("barest");
    make_legacy(&d.join("old.sqlite"));
    let h = Host::open({ let mut o = Options::new(Some(store_path(&d))); o.clock = ticking(); o }).unwrap();
    for n in PACKS.iter().filter(|n| !["door_auth", "legacy_persons"].contains(n)) { h.load_pack(n, &pack(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.rebuild();
    membrane_vm::host::boot_started(&h, &Map::new(), true);
    h.pump();
    legacy_source(&h, &d.join("old.sqlite"));
    import(&h, json!({"id": "i1", "sessions": "all", "page": 10}));
    settled(&h);
    assert_eq!(h.get("membrane::Chat:session:ses_b", "owner"), json!("Person:ada-lovelace"));
    assert!(h.get("Person:ada-lovelace", "name").is_null(), "no persons here: the name stays on the old row");
    assert_eq!(h.get("membrane::Legacy:session:ses_b", "state"), json!("done"));
}
