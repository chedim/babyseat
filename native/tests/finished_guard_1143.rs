//! A finished message stops depending on the banner (#1143): the per-message prompt getters (`banner_text`, `banner_slot`, `sys`, `argv`, `mcp_json`, `bin`, `port`,
//! `max_turns`, `note`) apply only while the turn is unfinished; once it is `done` or `error` they are none (derived), so a banner change recomputes no finished message,
//! the store rows stay slim and a restart derives nothing for them. Also the store's durability: WAL with `synchronous=NORMAL`.
use membrane_vm::host::{self, Host, Options};
use rusqlite::Connection;
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("guard-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn seat(store: Option<&str>, before: impl Fn(&Host)) -> Host {
    let mut o = Options::new(store.map(String::from)); o.effects = false;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
    }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_stored_entities();
    h.rebuild();
    before(&h);
    host::boot_started(&h, &Map::new(), true);
    h.pump();
    h
}
const DERIVED: [&str; 9] = ["banner_text", "banner_slot", "sys", "argv", "mcp_json", "bin", "port", "max_turns", "note"];

/// the live shapes: the class of the eid's namespace (Chat), the kind in `parents`; the turn's state lives on the `:turn` entity
fn message(h: &Host, n: u32, state: Option<&str>) {
    let mut p = Map::new(); p.insert("text".into(), json!(format!("question {n}"))); p.insert("parents".into(), json!(["cls:chatmessage"]));
    h.born(&format!("membrane::Chat:message:{n}"), "Chat", p, "person");
    let mut t = Map::new(); t.insert("parents".into(), json!(["cls:chatturn"]));
    if let Some(s) = state { t.insert("state".into(), json!(s)); }
    h.born(&format!("membrane::Chat:message:{n}:turn"), "Chat", t, "person");
    h.pump();
}
fn derived_present(h: &Host, n: u32) -> Vec<&'static str> {
    DERIVED.iter().copied().filter(|p| !h.get(&format!("membrane::Chat:message:{n}"), p).is_null()).collect()
}

#[test]
fn a_finished_message_holds_none_of_the_prompt_props_and_an_unfinished_one_still_has_them() {
    let h = seat(None, |h| { message(h, 1, Some("done")); message(h, 2, Some("error")); message(h, 3, None); message(h, 4, Some("running")); });
    assert_eq!(derived_present(&h, 1), Vec::<&str>::new(), "done");
    assert_eq!(derived_present(&h, 2), Vec::<&str>::new(), "error");
    assert!(derived_present(&h, 3).contains(&"sys") && derived_present(&h, 3).contains(&"banner_text"), "queued keeps them: {:?}", derived_present(&h, 3));
    assert!(derived_present(&h, 4).contains(&"sys"), "running keeps them");
    // a turn that finishes drops them
    h.write("membrane::Chat:message:3:turn", "state", json!("done"), "person"); h.pump();
    assert_eq!(derived_present(&h, 3), Vec::<&str>::new(), "finishing clears them");
    // what a finished message must still show
    assert_eq!(h.get("membrane::Chat:message:1", "text"), json!("question 1"));
    assert_eq!(h.get("membrane::Chat:message:1", "view")["state"], json!("done"));
}

#[test]
fn a_banner_change_recomputes_no_finished_message() {
    let file = tmpfile("banner");
    let pack = "PACK mine 1\nIMPORT default_banner 1\nBanner.text getter: 'n=' + str(prop('Note:x', 'n'))\n";
    let h = seat(Some(&file), |h| { for n in 0..50 { message(h, n, Some("done")); } message(h, 50, None); h.born("Note:x", "Note", json!({"n": 0}).as_object().unwrap().clone(), "person"); });
    h.load_pack("mine", pack).unwrap(); h.rebuild(); h.pump();
    let db = Connection::open(&file).unwrap();
    let rows = |db: &Connection| -> Vec<(String, String)> {
        let mut st = db.prepare("SELECT k, body FROM c_field WHERE k LIKE 'membrane::Chat:message:%' AND k NOT LIKE 'membrane::Chat:message:50%' ORDER BY k").unwrap();
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).unwrap().map(|x| x.unwrap()).collect()
    };
    let before = rows(&db);
    h.write("Note:x", "n", json!(1), "person"); h.pump();
    assert_eq!(rows(&db), before, "no finished message row was rewritten");
    assert!(h.get("membrane::Chat:message:50", "sys").as_str().unwrap().contains("n=1"), "the queued one follows the banner");
}

#[test]
fn a_restart_derives_nothing_for_finished_messages_and_their_rows_stay_slim() {
    let file = tmpfile("restart");
    { let _h = seat(Some(&file), |h| { for n in 0..20 { message(h, n, Some("done")); } }); }
    let db = Connection::open(&file).unwrap();
    let rows = |db: &Connection| -> Vec<(String, String)> {
        let mut st = db.prepare("SELECT k, body FROM c_field WHERE k LIKE 'membrane::Chat:message:%' ORDER BY k").unwrap();
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).unwrap().map(|x| x.unwrap()).collect()
    };
    let before = rows(&db);
    for (k, b) in &before {
        let v: J = serde_json::from_str(b).unwrap();
        for p in DERIVED { assert!(v["props"].get(p).map_or(true, |x| x.is_null()), "{k} stores {p}: {}", &b[..b.len().min(300)]); }
        assert!(!b.contains("CONVERSATION: main") && !b.contains("Modify your banner"), "{k} holds a copy of the system prompt");
    }
    let h = seat(Some(&file), |_| {});
    let c0 = h.store_commits();
    std::thread::sleep(std::time::Duration::from_millis(1100));   // a later boot: the default banner's NOW line moved
    h.rebuild(); h.pump();
    assert_eq!(rows(&db), before, "the restart rewrote no finished message's row");
    let _ = c0;
}

#[test]
fn the_store_runs_wal_with_synchronous_normal_and_commits_survive_a_clean_restart() {
    let file = tmpfile("sync");
    {
        let h = seat(Some(&file), |_| {});
        assert_eq!(h.store_pragma("journal_mode"), "wal");
        assert_eq!(h.store_pragma("synchronous"), "1", "NORMAL");
        h.born("Note:keep", "Note", json!({"n": 7}).as_object().unwrap().clone(), "person"); h.pump();
    }
    let h = seat(Some(&file), |_| {});
    assert_eq!(h.get("Note:keep", "n"), json!(7), "the commit persisted");
}
