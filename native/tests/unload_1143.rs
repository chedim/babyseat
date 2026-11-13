//! EAGER UNLOAD UNDER MEMORY PRESSURE (#1143, operator 2026-11-13; docs/cliffvm.md, "Unload"): the opposite of lazy load. An entity not touched for a while leaves the
//! engine (its store row stays) and loads again on the next reference. The primitive is one write, `unload = true`; the policy is a c1f pack (`unload.c1f` of the
//! barest app) driven by the `Mem:host` sensor; the engine keeps a touch clock per entity (`.touched`, not persisted).
use membrane_vm::host::{boot_started, Host, MemoryConfig, Options};
use serde_json::{json, Map, Value as J};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("unload-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn open(file: &str) -> Host { let mut o = Options::new(Some(file.to_string())); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn barest(h: &Host) {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() { h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap(); }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
}

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
    this.read = [Note:0.n, Note:1.n, Note:2.n, Note:3.n, Note:4.n]
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

// ---- the primitive: `unload = true` ---------------------------------------------------------------------------------------------------------------------
#[test]
fn unload_takes_an_entity_out_of_the_engine_keeps_its_row_and_a_reference_loads_it_with_every_value_equal() {
    let file = tmpfile("prim");
    let h = box_host(&file);
    h.born("Box:1", "Box", props(json!({"n": 3, "note": "x"})), "person");
    h.born("Box:2", "Box", props(json!({"n": 4})), "person");
    let before = h.snapshot()["Box:1"].clone();
    let row_before = h.rows()["Box:1"].clone();
    assert_eq!(before["props"]["double"], json!(6), "a getter value");
    let commits = h.store_commits();
    h.write("Box:1", "unload", json!(true), "person");
    assert!(!h.snapshot().contains_key("Box:1"), "it left the engine");
    assert!(h.snapshot().contains_key("Box:2"), "and only it");
    assert_eq!(h.rows()["Box:1"], row_before, "its row is exactly as it was: nothing was written back, `unload` is not stored");
    assert_eq!(h.unloaded_count(), 1);
    assert!(h.store_commits() - commits <= 1, "no churn: the write is journalled (an outside write), the entity's row is not rewritten");
    // a read loads it, every property equal (the getters' values too)
    assert_eq!(h.get("Box:1", "double"), json!(6));
    assert_eq!(h.snapshot()["Box:1"], before, "every property equal after the reload");
    assert_eq!(h.unloaded_count(), 0);
}

#[test]
fn an_unloaded_entity_loads_again_on_a_write_and_on_a_getter_that_reads_it() {
    let h = box_host(&tmpfile("reload"));
    h.born("Box:1", "Box", props(json!({"n": 3})), "person");
    h.born("Box:2", "Box", props(json!({"n": 4})), "person");
    h.born("Dep:d", "Dep", props(json!({})), "person");
    assert_eq!(h.get("Dep:d", "total"), json!(7));
    h.write("Box:1", "unload", json!(true), "person");
    assert!(!h.snapshot().contains_key("Box:1"));
    // a getter that read the unloaded entity keeps its value, and recomputes on a change of what it reads: the read loads Box:1 again
    assert_eq!(h.get("Dep:d", "total"), json!(7), "its cached value stands: an unload changes no value");
    h.write("Box:2", "n", json!(10), "person");
    assert_eq!(h.get("Dep:d", "total"), json!(13), "the recompute read Box:1, which loaded");
    assert!(h.snapshot().contains_key("Box:1"), "reloaded by the dependency");
    // a write to an unloaded one is made on what is stored
    h.write("Box:2", "unload", json!(true), "person");
    h.write("Box:2", "n", json!(11), "person");
    assert_eq!(h.get("Box:2", "double"), json!(22));
    assert_eq!(h.get("Box:2", "label"), json!("box 11"));
}

#[test]
fn a_setter_reading_an_unloaded_entity_loads_it_and_instances_lists_only_what_is_loaded() {
    let h = box_host(&tmpfile("inst"));
    for i in 1..=3 { h.born(&format!("Box:{i}"), "Box", props(json!({"n": i})), "person"); }
    h.born("Box:a", "Box", props(json!({"n": 1})), "person");
    h.born("Box:b", "Box", props(json!({"n": 2})), "person");
    h.born("Probe:p", "Probe", props(json!({})), "person");
    h.write("Probe:p", "count", json!(1), "person");
    assert_eq!(h.get("Probe:p", "boxes"), json!(5));
    h.write("Box:3", "unload", json!(true), "person");
    h.write("Box:b", "unload", json!(true), "person");
    h.write("Probe:p", "count", json!(2), "person");
    assert_eq!(h.get("Probe:p", "boxes"), json!(3), "instances('<lazy class>') sees only the loaded ones (documented)");
    h.write("Probe:p", "peek", json!(1), "person");
    assert_eq!(h.get("Probe:p", "seen"), json!(2), "a setter's read of Box:b loaded it");
    h.write("Probe:p", "count", json!(3), "person");
    assert_eq!(h.get("Probe:p", "boxes"), json!(4));
}

#[test]
fn a_pinned_entity_is_refused_with_a_reasoned_receipt_and_stays() {
    let h = box_host(&tmpfile("pinned"));
    h.born("Keep:k", "Keep", props(json!({"n": 1})), "person");           // RESIDENT class
    h.born("Gone:g", "Gone", props(json!({"n": 1})), "host:door");         // VIRTUAL class
    h.born("Box:run", "Box", props(json!({"n": 1, "running": {"id": "r1", "started": 1}})), "person");   // a running effect
    h.born("Box:ok", "Box", props(json!({"n": 1})), "person");
    for e in ["Keep:k", "Gone:g", "Box:run"] { h.write(e, "unload", json!(true), "person"); }
    h.write("Box:ok", "unload", json!(true), "person");
    assert!(h.snapshot().contains_key("Keep:k") && h.snapshot().contains_key("Gone:g") && h.snapshot().contains_key("Box:run"), "the pinned ones stay");
    assert!(!h.snapshot().contains_key("Box:ok"), "the control went");
    let reason = |e: &str| h.get(e, "unload_result");
    assert_eq!(reason("Keep:k")["status"], json!("refused"));
    assert!(reason("Keep:k")["reason"].as_str().unwrap().contains("resident"), "{}", reason("Keep:k"));
    assert!(reason("Gone:g")["reason"].as_str().unwrap().contains("virtual"), "{}", reason("Gone:g"));
    assert!(reason("Box:run")["reason"].as_str().unwrap().contains("running"), "{}", reason("Box:run"));
    assert_eq!(h.get("Keep:k", "unload"), J::Null, "the slot is consumed: a later `unload = true` fires again");
}

#[test]
fn an_entity_a_live_virtual_entity_holds_by_reference_is_pinned_while_it_is_held() {
    let h = box_host(&tmpfile("held"));
    h.born("Box:h", "Box", props(json!({"n": 1})), "person");
    h.born("Gone:g", "Gone", props(json!({"ref": {"$ref": "Box:h"}})), "host:door");
    h.write("Box:h", "unload", json!(true), "person");
    assert!(h.snapshot().contains_key("Box:h"), "held by a virtual entity's $ref");
    assert!(h.get("Box:h", "unload_result")["reason"].as_str().unwrap().contains("held"), "{}", h.get("Box:h", "unload_result"));
    h.write("Gone:g", "ref", json!(null), "host:door");
    h.write("Box:h", "unload", json!(true), "person");
    assert!(!h.snapshot().contains_key("Box:h"), "released: it goes");
}

// ---- the sensor and the policy --------------------------------------------------------------------------------------------------------------------------
const MB: u64 = 1 << 20;

/// 50 Notes in a seat on the shipped packs; the injected resident size is 40 MB + 1 MB per Note loaded.
fn pressured(tag: &str, cfg: MemoryConfig, batch: Option<u64>) -> (Host, String, Arc<AtomicU64>) {
    let file = tmpfile(tag);
    let h = open(&file);
    h.load_pack("probe", PACK).unwrap();
    barest(&h);
    h.rebuild();
    let rss = Arc::new(AtomicU64::new(0));
    let r2 = rss.clone();
    h.set_rss_reading(Box::new(move || Some(r2.load(Ordering::SeqCst))));
    h.set_memory(cfg);
    let mut facts = Map::new();
    if let Some(b) = batch { facts.insert("unload_batch".into(), json!(b)); }
    boot_started(&h, &facts, true);
    h.born("Probe:p", "Probe", props(json!({})), "person");
    for i in 0..50 { h.born(&format!("Note:{i}"), "Note", props(json!({"n": i, "text": format!("note {i}")})), "person"); }
    (h, file, rss)
}
fn loaded_notes(h: &Host) -> usize { h.snapshot().keys().filter(|k| k.starts_with("Note:")).count() }
fn set_rss(h: &Host, rss: &AtomicU64) { rss.store((40 + loaded_notes(h) as u64) * MB, Ordering::SeqCst); }
fn cfg() -> MemoryConfig { MemoryConfig { budget_mb: Some(100), soft: Some(0.5), hard: Some(0.9) } }

#[test]
fn under_pressure_the_coldest_are_unloaded_until_rss_is_under_the_soft_line_and_the_hot_ones_stay() {
    let (h, file, rss) = pressured("pressure", cfg(), Some(4));
    h.write("Probe:p", "hot", json!(1), "person");                       // Note:0..4 touched last
    assert_eq!(loaded_notes(&h), 50);
    set_rss(&h, &rss);
    h.tick();
    assert_eq!(h.get("Mem:host", "level"), json!("hard"), "90 MB of a 100 MB budget with hard at 0.9");
    assert_eq!(h.get("Mem:host", "budget"), json!(100 * MB));
    let mut passes = 0;
    while h.get("Mem:host", "level") != json!("ok") && passes < 40 {
        set_rss(&h, &rss);
        h.tick(); h.pump();
        passes += 1;
    }
    set_rss(&h, &rss);
    h.tick();
    assert_eq!(h.get("Mem:host", "level"), json!("ok"), "under the soft line (50 MB) after {passes} passes");
    let left = loaded_notes(&h);
    assert!(left < 10 && left >= 5, "unloaded in batches of 4 until under the line: {left} loaded");
    for i in 0..5 { assert!(h.snapshot().contains_key(&format!("Note:{i}")), "the hot Note:{i} stays"); }
    let refused: Vec<_> = h.snapshot().into_iter().filter(|(_, v)| v["props"].get("unload_result").is_some()).map(|(k, _)| k).collect();
    assert!(refused.is_empty(), "the policy asks only for entities `.pinned` lets go, so nothing was refused: {refused:?}");
    assert_eq!(h.rows().keys().filter(|k| k.starts_with("Note:")).count(), 50, "every row is kept");
    assert_eq!(h.unloaded_count(), 50 - left, "the unloaded are noted for a reload");
    // nothing more is unloaded once the level is ok
    let n = loaded_notes(&h);
    for _ in 0..3 { set_rss(&h, &rss); h.tick(); h.pump(); }
    assert_eq!(loaded_notes(&h), n);
    // an unloaded Note reads as it always did
    let cold = (5..50).map(|i| format!("Note:{i}")).find(|k| !h.snapshot().contains_key(k)).unwrap();
    let i: u64 = cold.trim_start_matches("Note:").parse().unwrap();
    assert_eq!(h.get(&cold, "text"), json!(format!("note {i}")));
    drop(file);
}

#[test]
fn without_a_budget_the_level_is_ok_and_nothing_is_unloaded_and_without_a_batch_too() {
    let (h, _f, rss) = pressured("nobudget", MemoryConfig::default(), Some(4));
    rss.store(10_000 * MB, Ordering::SeqCst);
    h.tick(); h.pump();
    assert_eq!(h.get("Mem:host", "level"), json!("ok"), "no budget: ok whatever the reading");
    assert_eq!(h.get("Mem:host", "rss"), json!(10_000 * MB));
    assert_eq!(h.get("Mem:host", "budget"), J::Null);
    assert_eq!(loaded_notes(&h), 50);
    let (h, _f, rss) = pressured("nobatch", cfg(), None);
    rss.store(95 * MB, Ordering::SeqCst);
    for _ in 0..3 { h.tick(); h.pump(); }
    assert_eq!(h.get("Mem:host", "level"), json!("hard"));
    assert_eq!(loaded_notes(&h), 50, "no unload_batch: no unloading");
}

#[test]
fn with_no_soft_fraction_the_levels_are_ok_and_hard_only() {
    let (h, _f, rss) = pressured("nosoft", MemoryConfig { budget_mb: Some(100), soft: None, hard: Some(0.9) }, Some(4));
    rss.store(80 * MB, Ordering::SeqCst);
    h.tick(); h.pump();
    assert_eq!(h.get("Mem:host", "level"), json!("ok"), "no soft line: below hard is ok");
    assert_eq!(loaded_notes(&h), 50);
    rss.store(91 * MB, Ordering::SeqCst);
    h.tick(); h.pump();
    assert_eq!(h.get("Mem:host", "level"), json!("hard"));
    assert_eq!(h.unloaded_count(), 4, "one pass: the batch of the four coldest (whatever class they are of)");
}

#[test]
fn a_restart_after_unloading_restores_the_same_data() {
    let (h, file, rss) = pressured("restart", cfg(), Some(10));
    let want: Vec<J> = (0..50).map(|i| h.get(&format!("Note:{i}"), "text")).collect();
    for _ in 0..3 { set_rss(&h, &rss); h.tick(); h.pump(); }
    assert!(h.unloaded_count() >= 20, "some were unloaded: {}", h.unloaded_count());
    let rows = h.rows();
    drop(h);
    let h2 = open(&file);
    h2.load_pack("probe", PACK).unwrap();
    barest(&h2);
    h2.load_stored_entities();
    h2.rebuild();
    for i in 0..50 { assert_eq!(h2.get(&format!("Note:{i}"), "text"), want[i], "Note:{i}"); }
    let rows2 = h2.rows();
    for k in rows.keys().filter(|k| k.starts_with("Note:")) { assert_eq!(rows2[k]["props"], rows[k]["props"], "{k}"); }
}

#[test]
fn a_replay_of_the_journal_ends_with_the_same_stored_state_and_the_sensor_is_not_in_it() {
    let (h, _file, rss) = pressured("replay", cfg(), Some(10));
    for _ in 0..3 { set_rss(&h, &rss); h.tick(); h.pump(); }
    assert!(h.unloaded_count() >= 20);
    h.write("Note:7", "text", json!("changed"), "person");     // a write to a (possibly unloaded) Note is made on what is stored
    let j = h.journal_stored().unwrap();
    assert!(j.iter().all(|l| !format!("{l:?}").contains("Mem:host")), "a sensor reading is a measurement of this process, not history: never journalled");
    assert!(j.iter().all(|l| !format!("{l:?}").contains("\"unload\"")), "an unload the policy made is not journalled");
    let r = open(&tmpfile("replay2"));
    r.load_pack("probe", PACK).unwrap();
    barest(&r);
    r.rebuild();
    r.replay(&j);
    let (a, b) = (h.rows(), r.rows());
    for k in a.keys().filter(|k| k.starts_with("Note:")) { assert_eq!(a[k]["props"], b[k]["props"], "{k}: replay-equal"); }
    assert_eq!(b["Note:7"]["props"]["text"], json!("changed"));
}
