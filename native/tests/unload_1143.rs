//! EAGER UNLOAD UNDER MEMORY PRESSURE (#1143, operator 2026-11-13; docs/cliffvm.md, "Unload"): the opposite of lazy load. An entity not touched for a while leaves the
//! engine (its store row stays) and loads again on the next reference. The VM does it ITSELF, the host's policy (no pack, no write: operator 2026-11-14, "the model does
//! not maintain what is in memory, the VM does"): the memory-budget facts say when, the engine's touch clock (`.touched`, not persisted) says which.
use membrane_vm::host::{Host, MemoryConfig, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("unload-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn open(file: &str) -> Host { let mut o = Options::new(Some(file.to_string())); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

const PACK: &str = "PACK probe 1
RESIDENT Probe
RESIDENT Keep
VIRTUAL Gone
Box.double getter: this.n * 2
Box.label getter: 'box ' + str(this.n)
Probe.go on-setter snap:
  ALWAYS:
    this.snap = {'a': Box:a.touched, 'b': Box:b.touched}
Probe.peek on-setter peek-b:
  ALWAYS:
    this.seen = Box:b.n
Probe.count on-setter count-boxes:
  ALWAYS:
    this.boxes = len(instances('Box'))
Probe.upd on-setter try-touched:
  ALWAYS:
    Box:a.pinned = 5
Probe.hot on-setter hot:
  ALWAYS:
    this.read = [Box:0.n, Box:1.n, Box:2.n, Box:3.n, Box:4.n]
Dep.total getter: Box:1.n + Box:2.n
";

fn box_host(file: &str) -> Host {
    let h = open(file);
    h.load_pack("probe", PACK).unwrap();
    h.rebuild();
    h
}

// ---- the touch clock: `.touched` ------------------------------------------------------------------------------------------------------------------------
#[test]
fn touched_follows_reads_and_writes_is_read_only_and_reading_it_touches_nothing() {
    let h = box_host(&tmpfile("touched"));
    h.born("Box:a", "Box", props(json!({"n": 1})), "person");
    h.born("Box:b", "Box", props(json!({"n": 2})), "person");
    h.born("Probe:p", "Probe", props(json!({})), "person");
    h.write("Probe:p", "go", json!(1), "person");
    let s1 = h.get("Probe:p", "snap");
    assert!(s1["b"].as_u64().unwrap() > s1["a"].as_u64().unwrap(), "b was born after a: {s1}");
    // a READ of Box:b (a setter's) makes it hotter than a
    h.write("Probe:p", "peek", json!(1), "person");
    h.write("Probe:p", "go", json!(2), "person");
    let s2 = h.get("Probe:p", "snap");
    assert!(s2["b"].as_u64().unwrap() > s1["b"].as_u64().unwrap(), "a read bumps: {s1} -> {s2}");
    assert_eq!(s2["a"], s1["a"], "what was not touched keeps its clock");
    // a WRITE to a makes it hotter than b; reading `.touched` (the two snaps above) touched nothing
    h.write("Box:a", "n", json!(5), "person");
    h.write("Probe:p", "go", json!(3), "person");
    let s3 = h.get("Probe:p", "snap");
    assert!(s3["a"].as_u64().unwrap() > s2["b"].as_u64().unwrap(), "a write bumps: {s2} -> {s3}");
    assert_eq!(s3["b"], s2["b"], "reading `.touched` is no touch: b's clock did not move while the snaps read it");
    // a stored property of that name (the Python-era `Turn.touched`) is read as itself: the clock does not shadow a stored value
    h.write("Box:a", "touched", json!(["x"]), "person");
    h.write("Probe:p", "go", json!(4), "person");
    assert_eq!(h.get("Probe:p", "snap")["a"], json!(["x"]));
    assert_eq!(h.get("Probe:p", "snap")["b"], s3["b"], "while b still reads the clock");
    // `pinned` is the VM's, read-only: a setter that writes it is refused with a receipt, an outside write of it is dropped and never journalled
    h.write("Probe:p", "upd", json!(1), "person");
    let refused = h.get("Probe:p", "refused").as_str().unwrap_or("").to_string();
    assert!(refused.contains("pinned") && refused.contains("read-only"), "a receipt: {refused:?}");
    let before = h.journal().len();
    h.write("Box:a", "pinned", json!(true), "person");
    assert_eq!(h.journal().len(), before, "an outside write of `pinned` is dropped, not journalled");
    assert_eq!(h.get("Box:a", "pinned"), J::Null, "and no property of that name is stored");
}

// ---- no write evicts: `unload` is an ordinary property --------------------------------------------------------------------------------------------------
const PACK_WRITES_UNLOAD: &str = "PACK evictor 1
IMPORT probe 1
Probe.evict on-setter evict-box:
  ALWAYS:
    Box:1.unload = True
";

#[test]
fn a_write_of_unload_is_plain_data_by_a_person_and_by_a_pack_and_nothing_leaves_the_engine() {
    let h = box_host(&tmpfile("plain"));
    h.load_pack("evictor", PACK_WRITES_UNLOAD).unwrap();
    h.rebuild();
    h.born("Box:1", "Box", props(json!({"n": 3})), "person");
    h.born("Box:2", "Box", props(json!({"n": 4})), "person");
    h.born("Probe:p", "Probe", props(json!({})), "person");
    h.write("Box:2", "unload", json!(true), "person");
    assert!(h.snapshot().contains_key("Box:2"), "a person's write of `unload` evicts nothing");
    assert_eq!(h.get("Box:2", "unload"), json!(true), "it reads back as the data it is");
    assert_eq!(h.rows()["Box:2"]["props"]["unload"], json!(true), "and is stored like any property");
    assert_eq!(h.get("Box:2", "unload_result"), J::Null, "no receipt: there is no primitive to answer");
    h.write("Probe:p", "evict", json!(1), "person");
    assert!(h.snapshot().contains_key("Box:1"), "a pack's write of `unload` evicts nothing either");
    assert_eq!(h.get("Box:1", "unload"), json!(true));
    assert_eq!(h.unloaded_count(), 0, "nothing was taken out of the engine by name");
}

// ---- the policy: the VM's own ---------------------------------------------------------------------------------------------------------------------------
const MB: u64 = 1 << 20;

/// 50 Boxes in a seat with only the probe pack (no pack of the policy: there is none); the injected resident size is what a test sets.
fn pressured(tag: &str, cfg: MemoryConfig) -> (Host, String, Arc<AtomicU64>) {
    let file = tmpfile(tag);
    let h = box_host(&file);
    let rss = Arc::new(AtomicU64::new(0));
    let r2 = rss.clone();
    h.set_rss_reading(Box::new(move || Some(r2.load(Ordering::SeqCst))));
    h.set_memory(cfg);
    h.born("Probe:p", "Probe", props(json!({})), "person");
    for i in 0..50 { h.born(&format!("Box:{i}"), "Box", props(json!({"n": i, "text": format!("box {i}")})), "person"); }
    (h, file, rss)
}
fn loaded_boxes(h: &Host) -> usize { h.snapshot().keys().filter(|k| k.starts_with("Box:")).count() }
/// the resident size a test says: 40 MB + 1 MB per Box loaded
fn set_rss(h: &Host, rss: &AtomicU64) { rss.store((40 + loaded_boxes(h) as u64) * MB, Ordering::SeqCst); }
fn cfg() -> MemoryConfig { MemoryConfig { budget_mb: Some(100), soft: Some(0.6), hard: Some(0.9), batch: Some(4) } }

#[test]
fn under_pressure_the_coldest_leave_until_rss_is_under_the_soft_line_and_the_hot_ones_stay() {
    let (h, _file, rss) = pressured("pressure", cfg());
    h.write("Probe:p", "hot", json!(1), "person");                       // Box:0..4 read last
    assert_eq!(loaded_boxes(&h), 50, "nothing leaves while the reading is 0");
    let mut passes = 0;
    while { set_rss(&h, &rss); rss.load(Ordering::SeqCst) >= 60 * MB } && passes < 40 { h.tick(); h.pump(); passes += 1; }
    let left = loaded_boxes(&h);
    assert!(passes >= 1 && passes < 40, "it took passes, and ended: {passes}");
    assert!(left < 20 && left >= 5, "unloaded in batches until under the soft line (60 MB): {left} loaded");
    for i in 0..5 { assert!(h.snapshot().contains_key(&format!("Box:{i}")), "the hot Box:{i} stays"); }
    assert_eq!(h.rows().keys().filter(|k| k.starts_with("Box:")).count(), 50, "every row is kept");
    // the unloaded boxes are noted for a reload; at most one more entity left beside them: `recall:dials`, the policy entity every host loads at open (cold, like a box nobody reads)
    assert!(h.unloaded_count() >= 50 - left && h.unloaded_count() - (50 - left) <= 1, "the unloaded are noted for a reload: {} noted, {} boxes unloaded", h.unloaded_count(), 50 - left);
    let n = loaded_boxes(&h);
    for _ in 0..3 { set_rss(&h, &rss); h.tick(); h.pump(); }
    assert_eq!(loaded_boxes(&h), n, "nothing more is unloaded once under the line");
    // lazy reload: every value reads back, the getters too
    for i in 5..50 { assert_eq!(h.get(&format!("Box:{i}"), "text"), json!(format!("box {i}")), "Box:{i}"); assert_eq!(h.get(&format!("Box:{i}"), "double"), json!(i * 2)); }
    let _ = h.get("recall:dials", "banner_n");   // the policy entity every host loads at open may have left too: reading it loads it back
    assert_eq!(h.unloaded_count(), 0, "all loaded back by the reads");
}

#[test]
fn the_hard_line_alone_is_enough_and_without_a_budget_or_a_batch_nothing_leaves() {
    let (h, _f, rss) = pressured("nobudget", MemoryConfig { batch: Some(4), ..Default::default() });
    rss.store(10_000 * MB, Ordering::SeqCst);
    h.tick(); h.pump();
    assert_eq!(loaded_boxes(&h), 50, "no budget: no pressure whatever the reading");
    let (h, _f, rss) = pressured("nobatch", MemoryConfig { batch: None, ..cfg() });
    rss.store(95 * MB, Ordering::SeqCst);
    for _ in 0..3 { h.tick(); h.pump(); }
    assert_eq!(loaded_boxes(&h), 50, "no unload_batch: no unloading");
    let (h, _f, rss) = pressured("nosoft", MemoryConfig { soft: None, ..cfg() });
    rss.store(80 * MB, Ordering::SeqCst);
    h.tick(); h.pump();
    assert_eq!(loaded_boxes(&h), 50, "no soft line: below the hard line is fine");
    rss.store(91 * MB, Ordering::SeqCst);
    h.tick();
    assert!(loaded_boxes(&h) < 50 && h.unloaded_count() % 4 == 0 && h.unloaded_count() >= 4, "over the hard line: whole batches of the four coldest leave ({} unloaded)", h.unloaded_count());
}

#[test]
fn the_pins_are_never_candidates_a_resident_a_virtual_a_running_one_and_one_a_live_virtual_entity_holds() {
    let (h, _f, rss) = pressured("pins", cfg());
    h.born("Keep:k", "Keep", props(json!({"n": 1})), "person");
    h.hold("Gone:g");
    h.born("Box:held", "Box", props(json!({"n": 1})), "person");
    h.born("Gone:g", "Gone", props(json!({"ref": {"$ref": "Box:held"}})), "host:door");
    h.born("Box:run", "Box", props(json!({"n": 1, "running": {"id": "r1", "started": 1}})), "person");
    rss.store(99 * MB, Ordering::SeqCst);
    for _ in 0..40 { h.tick(); h.pump(); }
    for k in ["Keep:k", "Gone:g", "Box:run", "Box:held", "Probe:p"] { assert!(h.snapshot().contains_key(k), "{k} is pinned and stays"); }
    assert_eq!(loaded_boxes(&h), 2, "every other Box left: only the running and the held one remain");
    // released: the held one is a candidate again
    h.write("Gone:g", "ref", json!(null), "host:door");
    h.tick(); h.pump();
    assert!(!h.snapshot().contains_key("Box:held"), "released: it goes");
    assert!(h.snapshot().contains_key("Box:run"));
}

#[test]
fn a_restart_after_unloading_restores_the_same_data() {
    let (h, file, rss) = pressured("restart", cfg());
    let want: Vec<J> = (0..50).map(|i| h.get(&format!("Box:{i}"), "text")).collect();
    for _ in 0..6 { set_rss(&h, &rss); h.tick(); h.pump(); }
    assert!(h.unloaded_count() >= 20, "some were unloaded: {}", h.unloaded_count());
    let rows = h.rows();
    drop(h);
    let h2 = open(&file);
    h2.load_pack("probe", PACK).unwrap();
    h2.load_stored_entities();
    h2.rebuild();
    for i in 0..50 { assert_eq!(h2.get(&format!("Box:{i}"), "text"), want[i], "Box:{i}"); }
    let rows2 = h2.rows();
    for k in rows.keys().filter(|k| k.starts_with("Box:")) { assert_eq!(rows2[k]["props"], rows[k]["props"], "{k}"); }
}

#[test]
fn eviction_is_not_journalled_and_a_replay_ends_with_the_same_stored_state() {
    let (h, _file, rss) = pressured("replay", cfg());
    for _ in 0..6 { set_rss(&h, &rss); h.tick(); h.pump(); }
    assert!(h.unloaded_count() >= 20);
    h.write("Box:7", "text", json!("changed"), "person");     // a write to a (possibly unloaded) Box is made on what is stored
    let j = h.journal_stored().unwrap();
    assert!(j.iter().all(|l| !format!("{l:?}").contains("unload")), "an eviction the VM made is not history: never journalled");
    let r = box_host(&tmpfile("replay2"));
    r.replay(&j);
    let (a, b) = (h.rows(), r.rows());
    for k in a.keys().filter(|k| k.starts_with("Box:")) { assert_eq!(a[k]["props"], b[k]["props"], "{k}: replay-equal"); }
    assert_eq!(b["Box:7"]["props"]["text"], json!("changed"));
}


