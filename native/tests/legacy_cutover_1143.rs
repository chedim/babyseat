//! The AUTOMATIC history cutover (#1143, operator 2026-11-14): a user who upgrades from the Python seat starts the new seat once and finds all the history in the one chat, doing nothing.
//! `membrane/packs/legacy_cutover.c1f` finds the old store (the search order below), declares it as the read-only `SqlSource:legacy`, and imports every session in the BACKGROUND, newest
//! first, in small batches that leave the imported entities out of memory; a ledger (`Legacy:cutover` and the per-session `Legacy:session:<sid>`) makes it done once and resumable.
//! Host-level tests on small synthetic stores (native/tests/legacy_fixture); the real-store numbers are measured on a private seat (see the report), never here. No clock: counts and contents only.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
mod legacy_fixture;
use legacy_fixture::*;

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn pack(n: &str) -> String { std::fs::read_to_string(repo().join(format!("membrane/packs/{n}.c1f"))).unwrap_or_else(|e| panic!("{n}.c1f: {e}")) }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cutover-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(d.join(".membrane")).unwrap();
    std::fs::create_dir_all(d.join("cwd")).unwrap();
    std::fs::create_dir_all(d.join("home")).unwrap();
    d.canonicalize().unwrap()
}
/// the NEW seat's store lives in its data folder `<d>/.membrane` (the same folder the Python seat kept `field.sqlite/` in)
fn store_path(d: &std::path::Path) -> String { d.join(".membrane/seat.sqlite").to_string_lossy().to_string() }
/// a counter clock; every host starts one step above where the last one ended (a seat's start time differs at every start, as a real clock does)
static CLOCK: AtomicU64 = AtomicU64::new(0);
fn ticking() -> Box<dyn Fn() -> f64 + Send + Sync> {
    let n = Arc::new(AtomicU64::new(CLOCK.fetch_add(100_000, Ordering::SeqCst)));
    Box::new(move || 1_800_000_000.0 + n.fetch_add(1, Ordering::SeqCst) as f64)
}
const PACKS: &[&str] = &["entity", "boot", "fs", "proc", "fetch", "sql", "store", "door", "semtext", "mentions", "chat", "chat_turn", "door_session", "door_auth", "legacy_import", "legacy_persons", "legacy_cutover", "settings"];
fn facts_of(d: &std::path::Path, extra: &[(&str, J)]) -> Map<String, J> {
    let mut m = Map::new();
    m.insert("seat_data_dir".into(), json!(d.join(".membrane").to_string_lossy()));
    m.insert("seat_cwd".into(), json!(d.join("cwd").to_string_lossy()));
    m.insert("seat_home".into(), json!(d.join("home").to_string_lossy()));
    for (k, v) in extra { m.insert((*k).into(), v.clone()); }
    m
}
fn host_with(d: &std::path::Path, extra: &[(&str, J)]) -> Host {
    let h = Host::open({ let mut o = Options::new(Some(store_path(d))); o.clock = ticking(); o }).unwrap();
    for n in PACKS { h.load_pack(n, &pack(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.load_stored_entities();
    h.rebuild();
    membrane_vm::host::boot_started(&h, &facts_of(d, extra), true);
    h.pump();
    h
}
fn pump_until(h: &Host, what: &str, mut done: impl FnMut(&Host) -> bool) {
    let wt = h.waiter();
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(std::env::var("CUTOVER_WAIT_S").ok().and_then(|s| s.parse().ok()).unwrap_or(90)) {
        h.pump();
        h.tick();
        if done(h) { return; }
        if let Some(w) = &wt { w.wait(Some(Duration::from_millis(20))); }
    }
    panic!("timed out waiting for {what}: {}", h.snapshot().iter().filter(|(k, _)| k.contains("Legacy:cutover") || k.contains("Legacy:seat") || k.contains("SqlSource")).map(|(k, v)| format!("{k}={v}")).collect::<Vec<_>>().join("\n"));
}
const CUT: &str = "membrane::Legacy:cutover";
fn cut(h: &Host, p: &str) -> J { h.get(CUT, p) }
fn ended(h: &Host) -> bool { matches!(cut(h, "state").as_str(), Some("done") | Some("absent") | Some("error")) }
/// the cutover of THIS start is over: it decided (the ledger of this start's boot) and the ledger says an end
fn finish(h: &Host) { pump_until(h, "the cutover to end", |h| ended(h) && !h.get("membrane::Legacy:seat", "cx_launched").is_null() && h.get("membrane::Legacy:seat", "cx_launched") == h.get("membrane::Legacy:seat", "cx_boot") && h.get("membrane::Legacy:seat", "running") == json!(false) && h.get("membrane::Legacy:seat", "cur").is_null()); }
/// the old store at `rel` under `d` (the Python seat's layout: <.membrane>/field.sqlite/field.sqlite3)
fn old_store(d: &std::path::Path, rel: &str) -> std::path::PathBuf {
    let p = d.join(rel);
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    make_legacy(&p);
    p
}
fn sha(path: &std::path::Path) -> String {
    let out = std::process::Command::new("shasum").args(["-a", "256"]).arg(path).output().unwrap();
    String::from_utf8_lossy(&out.stdout).split_whitespace().next().unwrap().to_string()
}
fn loaded_message_entities(h: &Host) -> usize { h.snapshot().keys().filter(|k| k.contains(":message:") || k.starts_with("membrane::Chat:main:time:")).count() }

// ---- the search order ----------------------------------------------------------------------------------------------------------------------------------

#[test]
fn the_old_store_is_found_in_the_conventional_places_in_the_listed_order_and_declared_read_only() {
    // the order: 1 the seat's data folder (the new seat keeps its store beside the old `field.sqlite/`), 2 `.membrane` under it, 3 `.membrane` under the working folder,
    // 4 `~/membrane/.membrane`; each holds `field.sqlite/field.sqlite3`. For each place: every LATER place has a store too, and the earlier one wins.
    let places = [".membrane/field.sqlite/field.sqlite3", ".membrane/.membrane/field.sqlite/field.sqlite3", "cwd/.membrane/field.sqlite/field.sqlite3", "home/membrane/.membrane/field.sqlite/field.sqlite3"];
    for k in 0..places.len() {
        let d = tmp("order");
        for p in &places[k..] { old_store(&d, p); }
        let h = host_with(&d, &[]);
        pump_until(&h, "the source", |h| !h.get("SqlSource:legacy", "path").is_null());
        assert_eq!(h.get("SqlSource:legacy", "path"), json!(d.join(places[k]).to_string_lossy()), "place {k} wins when it and every later place hold a store");
        assert_eq!(h.get("SqlSource:legacy", "mode"), json!("ro"), "the old store is only ever opened read-only");
        finish(&h);
        assert_eq!(cut(&h, "state"), json!("done"), "place {k}: {}", cut(&h, "reason"));
        assert_eq!(cut(&h, "path"), json!(d.join(places[k]).to_string_lossy()));
    }
}

#[test]
fn an_explicit_path_wins_over_every_conventional_place_and_a_folder_is_read_as_its_store() {
    let d = tmp("explicit");
    old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let given = old_store(&d, "elsewhere/mine.sqlite3");
    let h = host_with(&d, &[("legacy_path", json!(given.to_string_lossy()))]);
    finish(&h);
    assert_eq!(h.get("SqlSource:legacy", "path"), json!(given.to_string_lossy()));
    assert_eq!(cut(&h, "state"), json!("done"));
    // a folder: the Python seat's `.membrane` folder (it holds field.sqlite/field.sqlite3) and the `field.sqlite` folder (it holds field.sqlite3), and a folder holding field.sqlite3
    for (rel, fact) in [("a/.membrane/field.sqlite/field.sqlite3", "a/.membrane"), ("b/field.sqlite/field.sqlite3", "b/field.sqlite"), ("c/field.sqlite3", "c")] {
        let d = tmp("folder");
        let f = old_store(&d, rel);
        let h = host_with(&d, &[("legacy_path", json!(d.join(fact).to_string_lossy()))]);
        finish(&h);
        assert_eq!(h.get("SqlSource:legacy", "path"), json!(f.to_string_lossy()), "{fact}");
        assert_eq!(cut(&h, "state"), json!("done"), "{fact}");
    }
}

#[test]
fn a_source_the_launch_already_declared_as_legacy_is_the_one_imported_and_nothing_is_searched() {
    let d = tmp("declared");
    old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let given = old_store(&d, "declared/old.sqlite");
    let h = host_with(&d, &[("sql_sources", json!([{"name": "legacy", "path": given.to_string_lossy(), "mode": "ro"}]))]);
    finish(&h);
    assert_eq!(h.get("SqlSource:legacy", "path"), json!(given.to_string_lossy()));
    assert_eq!(cut(&h, "state"), json!("done"));
}

#[test]
fn no_old_store_means_nothing_is_touched_no_error_no_notice_and_a_store_that_appears_later_is_not_picked_up_without_its_path() {
    let d = tmp("absent");
    let h = host_with(&d, &[]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("absent"));
    assert!(cut(&h, "reason").is_null() && cut(&h, "line").is_null(), "a fresh user sees no notice and no error");
    assert!(h.get("SqlSource:legacy", "path").is_null(), "no source is declared");
    assert!(h.get("membrane::Legacy:seat", "refused").is_null());
    let snap = h.snapshot();
    assert!(!snap.keys().any(|k| k.starts_with("membrane::Legacy:session:") || k.starts_with("membrane::Chat:session:") || k.starts_with("File:legacy")), "nothing imported, nothing probed in the field: {:?}", snap.keys().filter(|k| k.contains("Legacy") || k.contains("File")).collect::<Vec<_>>());
    drop(h);
    // the store appears AFTER the first start: a later start does nothing (the ledger says absent) ...
    old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let h = host_with(&d, &[]);
    for _ in 0..30 { h.pump(); h.tick(); std::thread::sleep(Duration::from_millis(5)); }
    assert_eq!(cut(&h, "state"), json!("absent"));
    assert!(h.get("SqlSource:legacy", "path").is_null());
    drop(h);
    // ... unless the user passes the path
    let h = host_with(&d, &[("legacy_path", json!(d.join(".membrane/field.sqlite/field.sqlite3").to_string_lossy()))]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("done"));
    assert_eq!(h.get("membrane::Legacy:session:ses_a", "state"), json!("done"));
}

// ---- the import: automatic, newest first, visible, once ----------------------------------------------------------------------------------------------

#[test]
fn the_import_runs_by_itself_every_session_is_whole_and_the_old_file_is_untouched() {
    let d = tmp("auto");
    let old = old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let before = sha(&old);
    let h = host_with(&d, &[]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("done"), "{}", cut(&h, "reason"));
    for (sid, rows) in [("ses_a", 5), ("ses_b", 2), ("ses_c", 1), ("xcl_d", 0)] {
        let l = format!("membrane::Legacy:session:{sid}");
        assert_eq!(h.get(&l, "state"), json!("done"), "{sid}");
        assert_eq!(h.get(&l, "rows"), json!(rows), "{sid}");
    }
    assert_eq!(h.get("membrane::Chat:session:ses_a:message:1790000010000-00000010", "text"), json!("where do we start?"), "a message that left memory reads back from the store");
    assert_eq!(h.get("membrane::Chat:main:time:1790000020000-00000013:session:ses_a", "chat"), json!("session:ses_a"));
    assert_eq!(sha(&old), before, "the old store is only ever read");
}

#[test]
fn sessions_come_newest_first_and_inside_a_session_the_newest_questions_come_first() {
    let d = tmp("order-in");
    old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let h = host_with(&d, &[("legacy_batch", json!(1))]);
    // inside a session: after the first page of ses_a (batch 1) the NEWEST question is there and the older one is not yet
    let mut seen_first_page = false;
    pump_until(&h, "ses_a to start", |h| {
        let rows = h.get("membrane::Legacy:session:ses_a", "rows");
        if rows.as_i64().unwrap_or(0) >= 1 && !seen_first_page {
            seen_first_page = true;
            assert!(!h.get("membrane::Chat:session:ses_a:message:1790000020000-00000013", "text").is_null(), "the newest question of the session is imported first");
            if rows.as_i64() == Some(2) { assert!(h.get("membrane::Chat:session:ses_a:message:1790000010000-00000010", "text").is_null(), "the older question waits for the next page"); }
        }
        ended(h)
    });
    assert!(seen_first_page);
    assert_eq!(cut(&h, "state"), json!("done"));
    // across sessions: the one updated last is imported first (the ledgers' import times, a counter clock, run down the `updated` of the sessions)
    let at = |s: &str| h.get(&format!("membrane::Legacy:session:{s}"), "imported_at").as_f64().unwrap();
    assert!(at("xcl_d") < at("ses_c") && at("ses_c") < at("ses_b") && at("ses_b") < at("ses_a"), "xcl_d {} ses_c {} ses_b {} ses_a {}", at("xcl_d"), at("ses_c"), at("ses_b"), at("ses_a"));
}

#[test]
fn the_progress_is_a_plain_line_that_counts_the_sessions_and_goes_away_when_the_import_is_done() {
    let d = tmp("line");
    old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let h = host_with(&d, &[("legacy_batch", json!(1))]);
    let mut lines: Vec<String> = vec![];
    let mut dones: Vec<i64> = vec![];
    pump_until(&h, "the end", |h| {
        if let Some(l) = cut(h, "line").as_str() { if lines.last().map(|x| x.as_str()) != Some(l) { lines.push(l.to_string()); } }
        if let Some(n) = cut(h, "done").as_i64() { dones.push(n); }
        ended(h)
    });
    assert!(!lines.is_empty(), "a line was shown while importing");
    assert!(lines.iter().all(|l| l.starts_with("Importing your earlier conversations: ") && (l.contains(" of 4. ") || l.contains("reading the list")) && l.ends_with("Older turns appear as this proceeds.")), "{lines:?}");
    assert!(dones.windows(2).all(|w| w[0] <= w[1]), "the count only goes up: {dones:?}");
    assert_eq!(cut(&h, "state"), json!("done"));
    assert_eq!((cut(&h, "done"), cut(&h, "total")), (json!(4), json!(4)));
    assert!(cut(&h, "line").is_null(), "the line is gone when the import is done");
    assert_eq!(h.get("Setting:import.history", "current"), json!("4 earlier conversations imported"), "the Settings row says it too, read-only");
}

#[test]
fn a_restart_in_the_middle_resumes_from_the_ledger_with_no_duplicate_and_without_redoing_a_done_session() {
    let d = tmp("resume");
    let old = d.join(".membrane/field.sqlite/field.sqlite3");
    std::fs::create_dir_all(old.parent().unwrap()).unwrap();
    make_big_legacy(&old, 400, 3);   // 3 small sessions (newer) then ses_big: 400 questions, 800 rows (+3 strays)
    let facts = [("legacy_batch", json!(10))];
    let h = host_with(&d, &facts);
    pump_until(&h, "ses_big to be half imported", |h| h.get("membrane::Legacy:session:ses_big", "state") == json!("importing") && h.get("membrane::Legacy:session:ses_big", "rows").as_i64().unwrap_or(0) >= 20);
    // (a pump runs up to 16 rounds, so the poll sees the ledger every few pages)
    let small_at = h.get("membrane::Legacy:session:ses_s0000", "imported_at");
    let half = h.get("membrane::Legacy:session:ses_big", "rows").as_i64().unwrap();
    assert!(small_at.is_number() && half < 800, "a newer session was done and the big one is partly imported ({half} of 800 rows)");
    assert_eq!(cut(&h, "state"), json!("importing"));
    drop(h);   // the seat is stopped in the middle of the import
    let h = host_with(&d, &facts);
    assert_eq!(cut(&h, "state"), json!("importing"), "the ledger remembers where it was");
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("done"), "{}", cut(&h, "reason"));
    assert_eq!(h.get("membrane::Legacy:session:ses_s0000", "imported_at"), small_at, "a session that was done is not imported again");
    assert_eq!(h.get("membrane::Legacy:session:ses_big", "rows"), json!(800), "rows counted once: nothing is read twice after the restart");
    assert_eq!(h.get("membrane::Legacy:session:ses_big", "unmapped"), json!(3));
    // the second start after done does nothing: no new run of the headers query
    let rid = h.get("SqlQuery:legacy:sessions", "run_result")["id"].clone();
    drop(h);
    let h = host_with(&d, &facts);
    for _ in 0..30 { h.pump(); h.tick(); std::thread::sleep(Duration::from_millis(5)); }
    assert_eq!(cut(&h, "state"), json!("done"));
    assert_eq!(h.get("SqlQuery:legacy:sessions", "run_result")["id"], rid, "a later start only checks the ledger");
}

// ---- errors are text, the seat goes on ----------------------------------------------------------------------------------------------------------------

#[test]
fn an_explicit_path_that_holds_no_store_is_an_error_with_the_path_and_the_seat_goes_on() {
    let d = tmp("missing");
    old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    let nowhere = d.join("nowhere/field.sqlite3");
    let h = host_with(&d, &[("legacy_path", json!(nowhere.to_string_lossy()))]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("error"));
    let why = cut(&h, "reason");
    assert!(why.as_str().unwrap().contains(nowhere.to_str().unwrap()), "{why}");
    assert!(h.get("SqlSource:legacy", "path").is_null(), "no fallback to another place when the user named one");
    assert_eq!(cut(&h, "line"), why, "the page shows the reason as text");
}

#[test]
fn a_store_that_is_not_an_old_seats_or_cannot_be_read_is_an_error_with_the_reason_and_not_final() {
    let d = tmp("bad");
    let p = d.join(".membrane/field.sqlite/field.sqlite3");
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    { let cx = rusqlite::Connection::open(&p).unwrap(); cx.execute_batch("CREATE TABLE other (a TEXT);").unwrap(); }
    let h = host_with(&d, &[]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("error"));
    assert!(cut(&h, "reason").as_str().unwrap().contains("c_sessions"), "the reason names what is missing: {}", cut(&h, "reason"));
    assert_eq!(h.get("membrane::Legacy:seat", "running"), json!(false), "the seat itself is fine");
    drop(h);
    // not a database at all
    std::fs::write(&p, b"this is not a sqlite file at all, just text").unwrap();
    let h = host_with(&d, &[]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("error"));
    assert!(!cut(&h, "reason").as_str().unwrap().is_empty());
    drop(h);
    // an error is not final: the next start tries again, and a good store imports
    std::fs::remove_file(&p).unwrap();
    make_legacy(&p);
    let h = host_with(&d, &[]);
    finish(&h);
    assert_eq!(cut(&h, "state"), json!("done"), "{}", cut(&h, "reason"));
}

// ---- bounded memory, the cost of a batch --------------------------------------------------------------------------------------------------------------

#[test]
fn the_import_leaves_what_it_wrote_out_of_memory_so_the_loaded_entities_stay_a_few_batches_whatever_the_store_size() {
    let d = tmp("bound");
    let old = d.join(".membrane/field.sqlite/field.sqlite3");
    std::fs::create_dir_all(old.parent().unwrap()).unwrap();
    make_big_legacy(&old, 120, 40);   // 160 questions in 41 sessions
    let batch = 5;
    let h = host_with(&d, &[("legacy_batch", json!(batch))]);
    let mut peak = 0usize;
    pump_until(&h, "the end", |h| { peak = peak.max(loaded_message_entities(h)); ended(h) });
    assert_eq!(cut(&h, "state"), json!("done"), "{}", cut(&h, "reason"));
    let imported_questions = 160usize;
    // one question is at most 8 entities (message, body, turn, response, response body, tools, time row); a batch is `batch` questions; a TEST bound of four batches in memory at once
    let bound = 4 * batch as usize * 8;
    assert!(peak <= bound, "peak loaded message entities {peak} > {bound} ({imported_questions} questions imported)");
    assert!(imported_questions * 5 > 4 * bound, "the store is big enough for the bound to mean something");
    assert!(h.unloaded_count() > imported_questions * 4, "the imported entities left the engine ({} unloaded)", h.unloaded_count());
    // and they are all still there: a read loads one back
    assert_eq!(h.get("membrane::Chat:session:ses_big:message:1790000000000-00000001", "text"), json!("a question"));
    assert_eq!(h.get("membrane::Legacy:session:ses_big", "rows"), json!(240));
}

#[test]
fn the_page_and_header_queries_search_the_key_range_and_never_scan_the_messages() {
    let d = tmp("plan");
    let old = d.join(".membrane/field.sqlite/field.sqlite3");
    std::fs::create_dir_all(old.parent().unwrap()).unwrap();
    make_big_legacy(&old, 50, 5);
    let h = host_with(&d, &[]);
    finish(&h);
    let cx = rusqlite::Connection::open(&old).unwrap();
    for (q, args) in [("SqlQuery:legacy:msgs", 3usize), ("SqlQuery:legacy:sessions", 0)] {
        let sql = h.get(q, "sql").as_str().unwrap().to_string();
        let mut st = cx.prepare(&format!("EXPLAIN QUERY PLAN {sql}")).unwrap();
        let params: Vec<rusqlite::types::Value> = if args == 3 { vec!["ses_big".to_string().into(), 0i64.into(), 10i64.into()] } else { vec![] };
        let plan: Vec<String> = st.query_map(rusqlite::params_from_iter(params), |r| r.get::<_, String>(3)).unwrap().map(|r| r.unwrap()).collect();
        eprintln!("{q}: {plan:?}");
        assert!(plan.iter().any(|l| l.contains("SEARCH c_messages") || l.contains("SEARCH m") || l.contains("SEARCH a") || l.contains("SEARCH")), "{q}: {plan:?}");
        assert!(!plan.iter().any(|l| l.contains("SCAN c_messages") || l.contains("SCAN m") || l.contains("SCAN a")), "{q} scans the messages: {plan:?}");
    }
}

#[test]
fn the_batch_has_a_default_and_a_fact_changes_it() {
    let d = tmp("batch");
    let h = host_with(&d, &[]);
    pump_until(&h, "absent", ended);
    assert_eq!(cut(&h, "batch"), json!(5), "the default batch: 5 questions a page (a threshold flagged for decision)");
    let d2 = tmp("batch2");
    let h2 = host_with(&d2, &[("legacy_batch", json!(7))]);
    pump_until(&h2, "absent", ended);
    assert_eq!(cut(&h2, "batch"), json!(7));
}

#[test]
fn only_person_chats_enter_the_thread_background_and_absorbed_sessions_are_skipped_with_their_kind_and_not_counted() {
    // chat = agent `build` or `speaker` whose title is not "absorbed claude session"; everything else keeps its header in the ledger as `skipped` and imports nothing
    let d = tmp("kinds");
    let old = old_store(&d, ".membrane/field.sqlite/field.sqlite3");
    {
        let cx = rusqlite::Connection::open(&old).unwrap();
        let add = |sid: &str, agent: &str, title: &str, created: i64, n: i64| {
            let (k, b) = session(sid, title, None, created, created + 5);
            let b = b.replace("\"agent\":\"build\"", &format!("\"agent\":\"{agent}\""));
            cx.execute("INSERT INTO c_sessions VALUES (?1, ?2)", [&k, &b]).unwrap();
            let (mk, mb) = user_row(sid, &format!("msg_{sid}"), n, created + 1, "hello");
            cx.execute("INSERT INTO c_messages VALUES (?1, ?2)", [&mk, &mb]).unwrap();
        };
        add("ses_w", "worker", "a background job", 1_790_100_000_000, 40);
        add("ses_x", "build", "absorbed claude session", 1_790_100_100_000, 41);
        add("ses_sp", "speaker", "spoken", 1_790_100_200_000, 42);
        add("ses_p", "planner", "plan", 1_790_100_300_000, 43);
    }
    let h = host_with(&d, &[("legacy_batch", json!(1))]);
    let mut lines: Vec<String> = vec![];
    pump_until(&h, "the end", |h| { if let Some(l) = cut(h, "line").as_str() { lines.push(l.to_string()); } ended(h) && h.get("membrane::Legacy:seat", "running") == json!(false) });
    assert_eq!(cut(&h, "state"), json!("done"), "{}", cut(&h, "reason"));
    for (sid, kind) in [("ses_w", "background"), ("ses_x", "absorbed"), ("ses_p", "background"), ("ses_a", "chat"), ("ses_sp", "chat")] {
        assert_eq!(h.get(&format!("membrane::Legacy:session:{sid}"), "session_kind"), json!(kind), "{sid}");
    }
    for sid in ["ses_w", "ses_x", "ses_p"] {
        assert_eq!(h.get(&format!("membrane::Legacy:session:{sid}"), "state"), json!("skipped"), "{sid}");
        let rows = h.rows();
        assert!(!rows.keys().any(|k| k.contains(&format!("session:{sid}:")) && k.starts_with("membrane::Chat:") || k.starts_with("membrane::Chat:main:time:") && k.ends_with(&format!(":session:{sid}")) || k == &format!("membrane::Chat:session:{sid}")), "{sid} left rows in the chat");
    }
    assert_eq!(h.get("membrane::Legacy:session:ses_sp", "state"), json!("done"));
    assert_eq!((cut(&h, "done"), cut(&h, "total")), (json!(5), json!(5)), "4 fixture chats + the speaker: skipped sessions are not counted");
    assert!(lines.iter().all(|l| !l.contains(" of 9") && !l.contains(" of 8")), "{lines:?}");
    assert_eq!(h.get("Setting:import.history", "current"), json!("5 earlier conversations imported"));
}
