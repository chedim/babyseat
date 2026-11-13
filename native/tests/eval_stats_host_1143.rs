//! The host publishes the eval accounting on `Stats:eval` (#1143): every window, as an outside-style write that is NOT journalled (like an unload: not part of a replay);
//! `Stats:eval.reset = true` clears the table; with the accounting off there is no entity. One test: the table is the process's, and a reset clears all of it.
#![cfg(feature = "bin")]
use membrane_vm::host::{Host, Options};
use membrane_vm::stats;
use serde_json::json;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

#[test]
fn the_host_publishes_the_window_and_the_reset_clears_it_and_nothing_is_journalled() {
    let now = Arc::new(AtomicU64::new(1000));
    let n2 = now.clone();
    let mut o = Options::new(None); o.effects = false;
    o.clock = Box::new(move || n2.load(Ordering::SeqCst) as f64);
    let h = Host::open(o).unwrap();
    let r = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    h.load_pack("stats", &std::fs::read_to_string(r.join("membrane/packs/stats.c1f")).unwrap()).unwrap();
    h.load_pack("hs", "PACK hs 1\nHsBox.dbl getter: this.w * 2\n").unwrap();
    assert!(h.snapshot().get("Stats:eval").is_none(), "nothing is counted until the host asks for it");
    h.enable_stats(10.0);
    let before = h.journal().len();
    assert_eq!(h.get("Stats:eval", "window_s"), json!(0.0), "born at 0 s");
    h.born("HsBox:a", "HsBox", json!({"w": 1}).as_object().unwrap().clone(), "person");
    for i in 0..9 { h.write("HsBox:a", "w", json!(10 + i), "person"); }
    let journalled = h.journal().len();
    now.store(1004, Ordering::SeqCst);
    h.tick();
    assert_eq!(h.get("Stats:eval", "total_calls"), json!(0), "the window is not over at 4 s: nothing is told yet");
    now.store(1012, Ordering::SeqCst);
    h.tick();
    assert_eq!(h.journal().len(), journalled, "the publishing is not journalled");
    assert!(journalled > before);
    assert_eq!(h.get("Stats:eval", "window_s"), json!(12.0));
    let top = h.get("Stats:eval", "top");
    let hit = top.as_array().unwrap().iter().find(|r| r["class"] == json!("HsBox") && r["prop"] == json!("dbl")).unwrap_or_else(|| panic!("{top}"));
    assert_eq!(hit["calls"], json!(10), "a birth and nine writes: ten evaluations");
    assert_eq!(hit["pack"], json!("hs"));
    assert!(h.get("Stats:eval", "total_calls").as_u64().unwrap() >= 10);
    assert!(h.get("Stats:eval", "total_ms").is_number());
    let life = h.get("Stats:eval", "top_total");
    assert!(life.as_array().unwrap().iter().any(|r| r["prop"] == json!("dbl") && r["calls"].as_u64().unwrap() >= 10), "{life}");
    assert_eq!(h.get("HsBox:a", "dbl"), json!(36), "no value changed");
    // the next window starts at zero
    h.write("HsBox:a", "w", json!(1), "person");
    now.store(1023, Ordering::SeqCst);
    h.tick();
    let top = h.get("Stats:eval", "top");
    assert_eq!(top.as_array().unwrap().iter().find(|r| r["prop"] == json!("dbl")).unwrap()["calls"], json!(1));
    let life = h.get("Stats:eval", "top_total");
    assert!(life.as_array().unwrap().iter().find(|r| r["prop"] == json!("dbl")).unwrap()["calls"].as_u64().unwrap() >= 11);
    // the explicit reset
    h.write("Stats:eval", "reset", json!(true), "person");
    now.store(1024, Ordering::SeqCst);
    h.tick();
    assert_eq!(h.get("Stats:eval", "reset"), json!(false), "the reset is done: the flag is down");
    let life = h.get("Stats:eval", "top_total");
    assert!(life.as_array().unwrap().iter().all(|r| r["prop"] != json!("dbl")), "the lifetime is cleared: {life}");
    assert_eq!(h.get("HsBox:a", "dbl"), json!(2));
}
