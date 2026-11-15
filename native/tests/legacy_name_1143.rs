//! The ONE-SHOT NAME CARRY-OVER at the history cutover (#1143, operator 2026-11-15): when the cutover has found the old store and the new seat has no name, ONE read-only query reads the old
//! name (`membrane::name` in `c_entities`) and the seat sends the model one message in the main chat that asks it to store the name in `Setting:chat.name`. Once (the ledger `Legacy:name`),
//! never when the name is already set (the row or the launch fact `seat_name`), nothing when the old store holds no name. Host-level tests on a small synthetic store.
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
const PACKS: &[&str] = &["entity", "boot", "fs", "proc", "fetch", "sql", "store", "door", "semtext", "mentions", "chat", "chat_turn", "door_session", "door_auth", "legacy_import", "legacy_persons", "legacy_cutover", "settings", "environment"];
fn facts_of(d: &std::path::Path, extra: &[(&str, J)]) -> Map<String, J> {
    let mut m = Map::new();
    m.insert("seat_data_dir".into(), json!(d.join(".membrane").to_string_lossy()));
    m.insert("seat_cwd".into(), json!(d.join("cwd").to_string_lossy()));
    m.insert("seat_home".into(), json!(d.join("home").to_string_lossy()));
    for (k, v) in extra { m.insert((*k).into(), v.clone()); }
    m
}
fn host_with(d: &std::path::Path, extra: &[(&str, J)]) -> Host { host_pre(d, extra, |_| {}) }
/// the same, the seat's environment told as the host tells it at the start (`Environment:host`, before `started`)
fn host_pre(d: &std::path::Path, extra: &[(&str, J)], pre: impl Fn(&Host)) -> Host {
    let env: Vec<(String, String)> = vec![];
    let h = Host::open({ let mut o = Options::new(Some(store_path(d))); o.clock = ticking(); o }).unwrap();
    for n in PACKS { h.load_pack(n, &pack(n)).unwrap_or_else(|e| panic!("pack {n}: {e}")); }
    h.load_stored_entities();
    h.rebuild();
    pre(&h);
    membrane_vm::host::boot_environment(&h, &facts_of(d, extra), &env);
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

const LED: &str = "membrane::Legacy:name";
const WANT: &str = "The old store has this stored for your name: YOUR NAME: Memo, agreed with the operator. Extract your name from it and store it in the Setting:chat.name entity on the graph, with entity_write {spec: \"Setting:chat.name\", prop: \"set\", value: \"<your name>\"}.";
/// the old store with the Python seat's name entity in `c_entities` (the key `membrane::name`; the body is the entity's JSON as that seat wrote it)
fn old_with_name(d: &std::path::Path, props: J) -> std::path::PathBuf {
    let p = d.join(".membrane/field.sqlite/field.sqlite3");
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    make_legacy(&p);
    if !props.is_null() {
        let cx = rusqlite::Connection::open(&p).unwrap();
        let body = json!({"e": "membrane::name", "d": {"data": {"class": "Config", "properties": props}}}).to_string();
        cx.execute("INSERT INTO c_entities VALUES ('membrane::name', ?1)", [&body]).unwrap();
    }
    p
}
const MAIN: &str = "membrane::Chat:main:message:";
fn main_messages(h: &Host) -> Vec<String> {
    h.rows().into_keys().filter(|k| k.starts_with(MAIN) && !k[MAIN.len()..].contains(':')).collect()
}
fn texts(h: &Host) -> Vec<String> { main_messages(h).into_iter().filter_map(|k| h.get(&k, "text").as_str().map(|s| s.to_string())).collect() }
/// the cutover ended and the name ledger decided (it is written once, in the round the answer of the query lands)
fn settle(h: &Host) {
    pump_until(h, "the name ledger", |h| !h.get(LED, "state").is_null() && matches!(h.get("membrane::Legacy:cutover", "state").as_str(), Some("done") | Some("error")));
    for _ in 0..30 { h.pump(); h.tick(); }
}

#[test]
fn the_old_name_is_sent_to_the_model_once_as_a_message_in_the_main_chat() {
    let d = tmp("name-sent");
    old_with_name(&d, json!({"name": "Memo", "summary": "YOUR NAME: Memo, agreed with the operator", "status": "x"}));
    let h = host_with(&d, &[]);
    settle(&h);
    assert_eq!(h.get(LED, "state"), json!("sent"));
    // `name` first when present: the stored value is that one
    assert_eq!(texts(&h), vec!["The old store has this stored for your name: Memo. Extract your name from it and store it in the Setting:chat.name entity on the graph, with entity_write {spec: \"Setting:chat.name\", prop: \"set\", value: \"<your name>\"}.".to_string()]);
    let k = main_messages(&h)[0].clone();
    assert_eq!(h.get(&k, "queued"), json!(true), "it starts a normal turn like a person's message");
    assert_eq!(h.get(&format!("{k}:turn"), "message"), json!(k), "its turn is born");
    assert_eq!(h.get(&k, "sent_by"), json!("seat"), "marked as sent by the seat, not typed by a person");
    // the page's row carries the mark, so the chat view can label it
    assert_eq!(h.get(&k, "page_view")["sent_by"], json!("seat"));
    assert!(h.snapshot().keys().any(|x| x.starts_with("membrane::Chat:main:time:")), "the time row, so the history lists it");
    drop(h);
    // a later start does nothing: the ledger says sent
    let h = host_with(&d, &[]);
    settle(&h);
    assert_eq!(texts(&h).len(), 1, "once: {:?}", texts(&h));
    assert_eq!(h.get(LED, "state"), json!("sent"));
}

#[test]
fn the_first_present_of_name_summary_status_is_the_stored_value() {
    let d = tmp("name-summary");
    old_with_name(&d, json!({"summary": "YOUR NAME: Memo, agreed with the operator", "status": "x"}));
    let h = host_with(&d, &[]);
    settle(&h);
    assert_eq!(texts(&h), vec![WANT.to_string()]);
    let d = tmp("name-status");
    old_with_name(&d, json!({"name": "", "status": "Rosie"}));
    let h = host_with(&d, &[]);
    settle(&h);
    assert!(texts(&h)[0].starts_with("The old store has this stored for your name: Rosie. "), "{:?}", texts(&h));
}

#[test]
fn nothing_is_sent_when_the_new_seat_already_has_a_name() {
    // the launch fact seat_name
    let d = tmp("name-fact");
    old_with_name(&d, json!({"name": "Memo"}));
    let h = host_with(&d, &[("seat_name", json!("Ada"))]);
    settle(&h);
    assert_eq!(h.get(LED, "state"), json!("skipped"));
    assert!(texts(&h).is_empty(), "{:?}", texts(&h));
    // the settings row's own value
    let d = tmp("name-row");
    // an earlier start of this seat had no old store; the person named it through the settings row (`set`), which is kept in the store
    let h0 = host_with(&d, &[]);
    pump_until(&h0, "absent", |h| h.get("membrane::Legacy:cutover", "state") == json!("absent"));
    h0.write("Setting:chat.name", "set", json!("Ada"), "person");
    for _ in 0..20 { h0.pump(); h0.tick(); }
    assert_eq!(h0.get("Setting:chat.name", "value"), json!("Ada"));
    drop(h0);
    let old = old_with_name(&d, json!({"name": "Memo"}));
    let h = host_with(&d, &[("legacy_path", json!(old.to_string_lossy()))]);
    settle(&h);
    assert_eq!(h.get(LED, "state"), json!("skipped"));
    assert!(texts(&h).is_empty(), "{:?}", texts(&h));
}

#[test]
fn nothing_is_sent_when_the_old_store_holds_no_name() {
    for props in [json!(null), json!({"status": ""})] {
        let d = tmp("name-none");
        old_with_name(&d, props.clone());
        let h = host_with(&d, &[]);
        settle(&h);
        assert_eq!(h.get(LED, "state"), json!("none"), "{props}");
        assert!(texts(&h).is_empty(), "{:?}", texts(&h));
    }
    // no old store at all: no ledger, no message
    let d = tmp("name-absent");
    let h = host_with(&d, &[]);
    pump_until(&h, "absent", |h| h.get("membrane::Legacy:cutover", "state") == json!("absent"));
    for _ in 0..30 { h.pump(); h.tick(); }
    assert!(h.get(LED, "state").is_null() && texts(&h).is_empty());
}

#[test]
fn a_store_without_the_entity_table_never_fails_the_cutover_and_the_error_is_in_the_ledger() {
    let d = tmp("name-notable");
    let p = d.join(".membrane/field.sqlite/field.sqlite3");
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    make_legacy(&p);
    rusqlite::Connection::open(&p).unwrap().execute_batch("DROP TABLE c_entities;").unwrap();
    let h = host_with(&d, &[]);
    settle(&h);
    assert_eq!(h.get("membrane::Legacy:cutover", "state"), json!("done"), "the history import is untouched");
    assert_eq!(h.get(LED, "state"), json!("error"));
    assert!(h.get(LED, "reason").as_str().map_or(false, |r| !r.is_empty()));
    assert!(texts(&h).is_empty());
}

#[test]
fn a_seat_whose_cutover_was_done_before_this_start_never_sends_the_name() {
    let d = tmp("name-olddone");
    let old = old_with_name(&d, json!({"name": "Memo"}));
    // an earlier start (before the carry-over existed) finished the cutover: its ledger says done and the source is declared; there is no `Legacy:name`
    let h0 = host_with(&d, &[("legacy_path", json!(d.join("nowhere").to_string_lossy()))]);
    pump_until(&h0, "error", |h| h.get("membrane::Legacy:cutover", "state") == json!("error"));
    h0.write("membrane::Legacy:cutover", "state", json!("done"), "person");
    h0.write("membrane::Legacy:cutover", "path", json!(old.to_string_lossy()), "person");
    h0.write("SqlSource:legacy", "path", json!(old.to_string_lossy()), "person");
    for _ in 0..20 { h0.pump(); h0.tick(); }
    drop(h0);
    let h = host_with(&d, &[("sql_sources", json!([{"name": "legacy", "path": old.to_string_lossy(), "mode": "ro"}]))]);
    for _ in 0..60 { h.pump(); h.tick(); std::thread::sleep(Duration::from_millis(20)); }
    assert_eq!(h.get("membrane::Legacy:cutover", "state"), json!("done"));
    assert!(h.get(LED, "state").is_null(), "no ledger change: {}", h.get(LED, "state"));
    assert!(texts(&h).is_empty(), "{:?}", texts(&h));
}
