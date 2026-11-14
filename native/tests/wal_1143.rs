//! The store's WAL stays small (#1143): a reader that holds a snapshot while rows are written keeps SQLite from resetting the log, so it grows;
//! the host's tick folds it back into the file and truncates it once it is over WAL_LIMIT_BYTES (a threshold, flagged for the operator).
#![cfg(feature = "bin")]
use membrane_vm::host::{Host, Options};
use serde_json::json;

fn size(p: &std::path::Path) -> u64 { std::fs::metadata(p).map(|m| m.len()).unwrap_or(0) }

#[test]
fn a_wal_that_grew_behind_a_reader_is_truncated_by_the_next_tick() {
    let dir = std::env::temp_dir().join(format!("wal-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
    std::fs::create_dir_all(&dir).unwrap();
    let db = dir.join("s.sqlite3");
    let wal = dir.join("s.sqlite3-wal");
    let mut o = Options::new(Some(db.to_string_lossy().to_string()));
    o.effects = false;
    let h = Host::open(o).unwrap();
    let reader = rusqlite::Connection::open(&db).unwrap();
    reader.execute_batch("BEGIN; SELECT count(*) FROM c_field;").unwrap();      // a read snapshot, held
    let big = "x".repeat(1 << 20);
    for i in 0..14 { h.born(&format!("Blob:{i}"), "Blob", serde_json::Map::new(), "person"); h.write(&format!("Blob:{i}"), "data", json!(big), "person"); }
    let grown = size(&wal);
    assert!(grown > 8 << 20, "the log grew behind the reader: {grown}");
    reader.execute_batch("COMMIT").unwrap();
    h.tick();
    let after = size(&wal);
    assert!(after < 1 << 20, "the tick truncated the log: {after} (was {grown})");
    drop(h);
    let _ = std::fs::remove_dir_all(&dir);
}
