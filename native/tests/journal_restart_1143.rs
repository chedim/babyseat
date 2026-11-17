//! The journal survives restarts (#1143): `c_journal` is the image of ALL the outside writes, in order. The line key used to be the in-memory count, which starts at 0 at every open, so a
//! restarted run REPLACED the earlier run's first lines and a replay of the stored journal lost the births (the writes to them were dropped). The key now continues from the stored maximum.
//! Content only, no clock: three runs on one store write and birth entities; the stored journal holds every line in order, and a replay from it rebuilds every entity the rows hold.
mod common;

use membrane_vm::host::{Host, Line, Options};
use serde_json::{json, Map, Value as J};

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("journal-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn open(store: &std::path::Path) -> Host { Host::open(Options::new(Some(store.to_string_lossy().to_string()))).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn rows(store: &std::path::Path) -> std::collections::BTreeMap<String, J> {
    let db = rusqlite::Connection::open(store).unwrap();
    let mut st = db.prepare("SELECT k, body FROM c_field").unwrap();
    st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).unwrap().filter_map(|r| r.ok())
        .map(|(k, b)| { let j: J = serde_json::from_str(&b).unwrap(); (k, json!({"cls": j["cls"], "props": j["props"]})) }).collect()
}
fn summary(l: &Line) -> String {
    match l { Line::Born { eid, .. } => format!("born {eid}"), Line::Write { eid, prop, new, .. } => format!("write {eid}.{prop}={new}") }
}

#[test]
fn three_runs_leave_every_line_in_the_stored_journal_in_order_and_a_replay_rebuilds_every_entity() {
    let d = tmp("restart");
    let store = d.join("s.sqlite");
    {
        let h = open(&store);
        h.born("Note:a", "Note", props(json!({"n": 1})), "person");
        h.write("Note:a", "n", json!(2), "person");
        h.born("Note:b", "Note", props(json!({"n": 10})), "person");
    }
    {
        let h = open(&store);
        h.write("Note:a", "n", json!(3), "person");
        h.born("Note:c", "Note", props(json!({"n": 100})), "person");
    }
    {
        let h = open(&store);
        h.write("Note:b", "m", json!(9), "person");
        h.write("Note:c", "n", json!(101), "person");
    }
    let h = open(&store);
    let stored: Vec<String> = h.journal_stored().unwrap().iter().map(summary).collect();
    assert_eq!(stored, [
        "born Note:a", "write Note:a.n=2", "born Note:b",
        "write Note:a.n=3", "born Note:c",
        "write Note:b.m=9", "write Note:c.n=101",
    ], "every run's lines, in the order they happened");
    // a replay of the stored journal on a fresh host ends with the entities the rows hold
    let fresh = Host::open(Options::new(None)).unwrap();
    fresh.replay(&h.journal_stored().unwrap());
    fresh.pump();
    let snap = fresh.snapshot();
    let want = rows(&store);
    assert_eq!(want.len(), 3);
    for (k, row) in &want {
        let got = snap.get(k).unwrap_or_else(|| panic!("{k} is not rebuilt by the replay"));
        assert_eq!(got["cls"], row["cls"], "{k}");
        for (p, v) in row["props"].as_object().unwrap() { assert_eq!(&got["props"][p], v, "{k}.{p}"); }
    }
    assert_eq!(fresh.get("Note:a", "n"), json!(3));
    assert_eq!(fresh.get("Note:b", "m"), json!(9));
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn a_store_whose_journal_an_older_run_already_overwrote_just_continues_after_its_last_line() {
    // an older seat's journal (keys 000000000000..): the next line goes AFTER the highest key, never over it
    let d = tmp("older");
    let store = d.join("s.sqlite");
    { let h = open(&store); h.born("Note:a", "Note", props(json!({"n": 1})), "person"); h.write("Note:a", "n", json!(2), "person"); }
    let h = open(&store);
    h.write("Note:a", "n", json!(3), "person");
    let keys: Vec<String> = { let db = rusqlite::Connection::open(&store).unwrap(); let mut st = db.prepare("SELECT k FROM c_journal ORDER BY k").unwrap(); st.query_map([], |r| r.get(0)).unwrap().filter_map(|r| r.ok()).collect() };
    assert_eq!(keys, ["000000000000", "000000000001", "000000000002"]);
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn a_big_receipt_is_stored_whole_and_kept_after_a_restart() {
    // the RAM copy elides a huge value (JOURNAL_RAM_MAX_BYTES), the stored line is whole: and a restart no longer overwrites it
    let d = tmp("big");
    let store = d.join("s.sqlite");
    let big = "x".repeat(400_000);
    { let h = open(&store); h.born("Note:a", "Note", props(json!({})), "person"); h.write("Note:a", "blob", json!(big.clone()), "person"); }
    { let h = open(&store); h.write("Note:a", "n", json!(1), "person"); }
    let h = open(&store);
    let j = h.journal_stored().unwrap();
    assert_eq!(j.len(), 3);
    assert!(matches!(&j[1], Line::Write { prop, new, .. } if prop == "blob" && new == &json!(big)), "the whole value, still the second line");
    let _ = std::fs::remove_dir_all(d);
}
