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
fn host_at(d: &std::path::Path) -> Host { host_facts(d, Map::new()) }
fn host_facts(d: &std::path::Path, facts: Map<String, J>) -> Host {
    let h = Host::open({ let mut o = Options::new(Some(store_path(d))); o.clock = ticking(); o }).unwrap();
    for n in PACKS { h.load_pack(n, &pack(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.load_stored_entities();
    h.rebuild();
    membrane_vm::host::boot_started(&h, &facts, true);
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
    let m1 = "membrane::Chat:session:ses_a:message:1790000010000-00000010";
    assert_eq!(h.get(m1, "text"), json!("where do we start?"));
    assert_eq!(h.get(m1, "sid"), json!("ses_a"), "the session id is read from the key");
    assert_eq!(h.get(m1, "mid"), json!("1790000010000-00000010"));
    assert_eq!(h.get("membrane::Chat:main:time:1790000010000-00000010:session:ses_a", "chat"), json!("session:ses_a"), "the time row of the message");
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
    assert_eq!(h.get("membrane::Chat:session:ses_c:message:1790000410000-00000030:turn", "status"), json!("error"));
    assert!(h.get("membrane::Chat:session:ses_c:message:1790000410000-00000030:turn:response", "text").as_str().unwrap().contains("no answer"), "visible, never silently dropped");
    assert!(h.get("membrane::Chat:session:ses_b:message:1790000250000-00000022", "text").is_null(), "the stray answer is not made a message");
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
    assert!(!h.get("membrane::Chat:session:ses_a:message:1790000010000-00000010", "text").is_null(), "what was written stays");
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
    let msgs = |sid: &str| { let p = format!("membrane::Chat:session:{sid}:message:"); h.snapshot().iter().filter(|(k, _)| k.starts_with(&p) && !k[p.len()..].contains(':')).count() };
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

/// the cost of the headers pass of a store whose one session has `users` questions: the `ms` the Sql driver's receipt says (a test bound, not a product constant)
fn headers_ms(users: i64) -> (f64, usize) {
    let d = tmp("perf");
    make_big_legacy(&d.join("old.sqlite"), users, 100);
    let h = host_at(&d);
    legacy_source(&h, &d.join("old.sqlite"));
    h.write("membrane::Legacy:seat", "list", json!({"id": "p"}), "person");
    pump_until(&h, "the headers of the big store", |h| h.get("SqlQuery:legacy:sessions", "run_result")["id"] == json!("list:p"));
    let r = h.get("SqlQuery:legacy:sessions", "run_result");
    assert_eq!(r["status"], json!("ok"), "{r}");
    (r["ms"].as_f64().unwrap(), r["rows"].as_array().unwrap().len())
}

#[test]
fn the_headers_pass_grows_linearly_with_the_biggest_session_not_with_its_square() {
    // 4x the messages of one session must cost far less than 16x (the correlated orphan subquery: quadratic, 11-14: an 8,024-row session ran for minutes). The bound 8 is a TEST bound between 4 (linear) and 16 (square).
    let (small, n1) = headers_ms(500);
    let (big, n2) = headers_ms(2000);
    assert_eq!((n1, n2), (101, 101));
    eprintln!("headers: 500 questions {small:.0} ms, 2000 questions {big:.0} ms, ratio {:.1}", big / small.max(1.0));
    assert!(big < 8.0 * small.max(5.0), "headers cost {small} ms at 500 questions but {big} ms at 2000: super-linear");
}

/// the wall time of importing the one big session (of `users` questions) page by page, and its ledger
fn import_big_s(users: i64, page: i64) -> f64 {
    let d = tmp("perfimp");
    make_big_legacy(&d.join("old.sqlite"), users, 0);
    let h = host_at(&d);
    legacy_source(&h, &d.join("old.sqlite"));
    let t = Instant::now();
    import(&h, json!({"id": "p", "sessions": ["ses_big"], "page": page}));
    settled(&h);
    let s = t.elapsed().as_secs_f64();
    let l = "membrane::Legacy:session:ses_big";
    assert_eq!(h.get(l, "state"), json!("done"), "{}", h.get(l, "reason"));
    assert_eq!(h.get(l, "rows"), json!(2 * users), "every question and its answer");
    assert_eq!(h.get(l, "old_rows"), json!(2 * users + 3));
    assert_eq!(h.get(l, "unmapped"), json!(3), "the 3 stray rows are noted, on the ledger, when the session is done");
    assert_eq!(h.get(l, "rows").as_i64().unwrap() + h.get(l, "unmapped").as_i64().unwrap(), 2 * users + 3, "rows + unmapped = old_rows");
    assert_eq!(h.get(l, "unmapped_rows").as_array().unwrap().len(), 3);
    s
}

#[test]
fn the_import_of_one_big_session_grows_linearly_with_its_size_at_a_fixed_page() {
    // at a fixed page size, 4x the questions must cost far less than 16x. A TEST bound between 4 (linear) and 16 (square).
    let a = import_big_s(150, 25);
    let b = import_big_s(600, 25);
    eprintln!("import: 150 questions {a:.1} s, 600 questions {b:.1} s, ratio {:.1}", b / a);
    assert!(b < 8.0 * a, "import cost {a} s at 150 questions but {b} s at 600: super-linear");
}

#[test]
fn a_page_query_costs_a_few_passes_over_the_session_not_a_pass_per_question() {
    // one page (25 questions) of a session of 3,000 questions: the page query may cost a few passes over the session's rows (the one-pass cost is measured here, on the same file, by a bare scan that reads the
    // same json field of every row), never a pass per question of the page. A TEST bound (20 passes), not a product constant.
    let d = tmp("perfpage");
    let old = d.join("old.sqlite");
    make_big_legacy(&old, 3000, 0);
    let cx = rusqlite::Connection::open(&old).unwrap();
    let pass = (0..3).map(|_| { let t = Instant::now(); let n: i64 = cx.query_row("SELECT count(*) FROM c_messages WHERE k >= 'ses_big|' AND k < 'ses_big}' AND json_extract(body,'$.row.info.parentID') IS NOT NULL", [], |r| r.get(0)).unwrap(); assert_eq!(n, 3000); t.elapsed().as_secs_f64() * 1000.0 }).fold(f64::MAX, f64::min);
    let h = host_at(&d);
    legacy_source(&h, &old);
    h.write("SqlQuery:legacy:msgs", "run", json!({"id": "pg", "args": ["ses_big", 0, 25]}), "person");
    pump_until(&h, "the page", |h| h.get("SqlQuery:legacy:msgs", "run_result")["id"] == json!("pg"));
    let r = h.get("SqlQuery:legacy:msgs", "run_result");
    assert_eq!(r["status"], json!("ok"), "{r}");
    assert_eq!(r["rows"].as_array().unwrap().len(), 25);
    let ms = r["ms"].as_f64().unwrap();
    eprintln!("page query {ms:.0} ms, one pass over the session {pass:.1} ms, ratio {:.0}", ms / pass.max(0.1));
    assert!(ms < 20.0 * pass.max(0.5), "a page of 25 questions cost {ms} ms; one pass over the session is {pass} ms");
}

fn rss_mb() -> f64 {
    let o = std::process::Command::new("ps").args(["-o", "rss=", "-p", &std::process::id().to_string()]).output().unwrap();
    String::from_utf8_lossy(&o.stdout).trim().parse::<f64>().unwrap_or(0.0) / 1024.0
}

/// THE REAL-STORE MEASUREMENT (ignored: it reads a real store, read-only and immutable through the `ro` SqlSource: never written, never copied).
/// `LEGACY_REAL=<path of the previous seat's store file>` `LEGACY_PAGE=<n>` (user rows per page; no default). Prints the headers pass for every session and the import of the BIGGEST
/// session page by page into a fresh private store: time, RSS and store growth. Run on a stopped seat's store (the file must not change while it is open).
#[test]
#[ignore]
fn measure_the_headers_and_the_biggest_session_of_a_real_store_read_only() {
    let real = std::path::PathBuf::from(std::env::var("LEGACY_REAL").expect("LEGACY_REAL=<path of the previous store file>"));
    let page: i64 = std::env::var("LEGACY_PAGE").expect("LEGACY_PAGE=<n>").parse().unwrap();
    let size = std::fs::metadata(&real).unwrap().len();
    let d = tmp("real");
    let h = host_at(&d);
    legacy_source(&h, &real);
    let rss0 = rss_mb();
    let t = Instant::now();
    h.write("membrane::Legacy:seat", "list", json!({"id": "real"}), "person");
    pump_until(&h, "the headers of the real store", |h| h.get("SqlQuery:legacy:sessions", "run_result")["id"] == json!("list:real"));
    let wall = t.elapsed().as_secs_f64();
    let r = h.get("SqlQuery:legacy:sessions", "run_result");
    assert_eq!(r["status"], json!("ok"), "{}", r["head"]);
    let rows = r["rows"].as_array().unwrap();
    pump_until(&h, "the headers written", |h| h.get(&format!("membrane::Legacy:session:{}", rows[0]["k"].as_str().unwrap()), "state") == json!("pending"));
    let wall_all = t.elapsed().as_secs_f64();
    let (mut big, mut total, mut unm) = (String::new(), 0i64, 0i64);
    let mut best = -1i64;
    for x in rows {
        let l = format!("membrane::Legacy:session:{}", x["k"].as_str().unwrap());
        let (n, u) = (h.get(&l, "old_rows").as_i64().unwrap(), h.get(&l, "unmapped").as_i64().unwrap());
        total += n; unm += u;
        if n > best { best = n; big = x["k"].as_str().unwrap().to_string(); }
    }
    eprintln!("MEASURE headers: {} sessions, {total} old rows, {unm} unmapped; query {:.2} s (driver receipt ms {}), headers run + entities written {wall_all:.2} s (query seen after {wall:.2} s); RSS {:.0} -> {:.0} MB", rows.len(), r["ms"].as_f64().unwrap() / 1000.0, r["ms"], rss0, rss_mb());
    let base = dir_bytes_of(&d);
    let t = Instant::now();
    let peak = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let (p2, s2) = (peak.clone(), stop.clone());
    let th = std::thread::spawn(move || { while !s2.load(Ordering::SeqCst) { p2.fetch_max(rss_mb() as u64, Ordering::SeqCst); std::thread::sleep(Duration::from_millis(100)); } });
    import(&h, json!({"id": "big", "sessions": [big.clone()], "page": page}));
    let tt = Instant::now();
    while tt.elapsed() < Duration::from_secs(3000) {
        h.pump();
        if h.get("membrane::Legacy:seat", "running") == json!(false) && h.get("membrane::Legacy:seat", "cur").is_null() { break; }
        if let Some(w) = h.waiter() { w.wait(Some(Duration::from_millis(20))); }
    }
    let secs = t.elapsed().as_secs_f64();
    stop.store(true, Ordering::SeqCst);
    th.join().unwrap();
    let l = format!("membrane::Legacy:session:{big}");
    eprintln!("MEASURE big session {big}: state {} old_rows {} rows {} unmapped {} dropped {}; import {secs:.1} s at page {page}; RSS peak {} MB; store growth {:.1} MB; source unchanged: {}", h.get(&l, "state"), h.get(&l, "old_rows"), h.get(&l, "rows"), h.get(&l, "unmapped"), h.get(&l, "dropped_parts"), peak.load(Ordering::SeqCst), (dir_bytes_of(&d) - base) as f64 / 1048576.0, std::fs::metadata(&real).unwrap().len() == size);
    assert_eq!(h.get(&l, "state"), json!("done"), "{}", h.get(&l, "reason"));
    assert_eq!(h.get(&l, "rows").as_i64().unwrap() + h.get(&l, "unmapped").as_i64().unwrap(), h.get(&l, "old_rows").as_i64().unwrap(), "rows + unmapped = old_rows");
}
fn dir_bytes_of(d: &std::path::Path) -> u64 { std::fs::read_dir(d.join(".membrane")).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with("field.sqlite3")).map(|e| e.metadata().unwrap().len()).sum() }

/// the wall time from the `list` write to the last session's header entity, for a store of `small` one-question sessions
fn list_wall_s(small: i64) -> f64 {
    let d = tmp("perflist");
    make_big_legacy(&d.join("old.sqlite"), 1, small);
    let h = host_at(&d);
    legacy_source(&h, &d.join("old.sqlite"));
    let t = Instant::now();
    h.write("membrane::Legacy:seat", "list", json!({"id": "l"}), "person");
    let last = format!("membrane::Legacy:session:ses_s{:04}", small - 1);
    pump_until(&h, "every header written", |h| h.get(&last, "state") == json!("pending"));
    t.elapsed().as_secs_f64()
}

#[test]
fn writing_the_headers_grows_linearly_with_the_number_of_sessions() {
    // 4x the sessions must cost far less than 16x (a TEST bound between 4 and 16)
    let a = list_wall_s(150);
    let b = list_wall_s(600);
    eprintln!("list: 150 sessions {a:.2} s, 600 sessions {b:.2} s, ratio {:.1}", b / a);
    assert!(b < 8.0 * a, "writing the headers cost {a} s for 150 sessions but {b} s for 600: super-linear");
}

#[test]
fn an_open_of_a_pending_session_imports_it_alone_when_the_page_fact_is_told_and_queues_behind_a_running_one() {
    let d = tmp("open");
    let old = d.join("old.sqlite");
    make_legacy(&old);
    let h = host_facts(&d, props(json!({"legacy_page": 2})));
    legacy_source(&h, &old);
    h.write("membrane::Legacy:seat", "list", json!({"id": "1"}), "person");
    pump_until(&h, "the headers", |h| !h.get("membrane::Chat:session:xcl_d", "stitle").is_null());
    h.write("membrane::Legacy:session:ses_a", "open", json!("r1"), "person");
    h.pump();
    settled(&h);
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "state"), json!("done"), "{}", h.get("membrane::Legacy:seat", "refused"));
    assert_eq!(h.get("membrane::Legacy:session:ses_b", "state"), json!("pending"));
    assert_eq!(h.get("membrane::Chat:session:ses_a:message:1790000010000-00000010", "sid"), json!("ses_a"));
}

#[test]
fn an_open_during_a_running_import_waits_for_it_and_is_imported_once_after_it() {
    let d = tmp("open-queue");
    let old = d.join("old.sqlite");
    make_legacy(&old);
    let h = host_facts(&d, props(json!({"legacy_page": 1})));
    legacy_source(&h, &old);
    h.write("membrane::Legacy:seat", "list", json!({"id": "0"}), "person");
    pump_until(&h, "the headers", |h| !h.get("membrane::Chat:session:xcl_d", "stitle").is_null());
    import(&h, json!({"id": "i1", "sessions": ["ses_a"], "page": 1}));
    h.write("membrane::Legacy:session:ses_b", "open", json!("r1"), "person");
    h.write("membrane::Legacy:session:ses_b", "open", json!("r2"), "person");
    pump_until(&h, "ses_b", |h| h.get("membrane::Legacy:session:ses_b", "state") == json!("done"));
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "state"), json!("done"));
    assert_eq!(h.get("membrane::Legacy:session:ses_c", "state"), json!("pending"));
    assert_eq!(h.get("membrane::Chat:session:ses_b:message:1790000210000-00000020", "sid"), json!("ses_b"));
}

#[test]
fn two_opens_at_once_are_both_imported_one_after_the_other() {
    let d = tmp("open-two");
    let old = d.join("old.sqlite");
    make_legacy(&old);
    let h = host_facts(&d, props(json!({"legacy_page": 1})));
    legacy_source(&h, &old);
    h.write("membrane::Legacy:seat", "list", json!({"id": "1"}), "person");
    pump_until(&h, "the headers", |h| !h.get("membrane::Chat:session:xcl_d", "stitle").is_null());
    h.write("membrane::Legacy:session:ses_b", "open", json!("r1"), "person");
    h.write("membrane::Legacy:session:ses_c", "open", json!("r2"), "person");
    pump_until(&h, "both done", |h| h.get("membrane::Legacy:session:ses_b", "state") == json!("done") && h.get("membrane::Legacy:session:ses_c", "state") == json!("done"));
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "state"), json!("pending"));
}
