//! THE VECTOR (#1143, operator 2026-11-14): the native half of the semantic field. A class a pack declares `MAPPED Vector f16` keeps the list of numbers `v` of its entities in a
//! compact native store (not in the entity's row), and the builtin `near(list, 'Vector', k)` finds the k nearest by cosine. docs/cliffvm.md ("Storage modes"), docs/c1f-stdlib.md.
use membrane_vm::host::{Host, Line, MemoryConfig, Options};
use membrane_vm::syntax::parse_full;
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn vector_pack() -> String { std::fs::read_to_string(repo().join("membrane/packs/vector.c1f")).unwrap() }
fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("vector-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn open(file: Option<&str>) -> Host { let mut o = Options::new(file.map(String::from)); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// The probe asks the questions a rule asks: what `v` reads as, the nearest, and the failures as values.
const PROBE: &str = "PACK probe 1
Probe.back getter: Vector:a.v
Probe.top getter: near(this.q, 'Vector', this.k)
Probe.wrongdim getter: near(this.short, 'Vector', 2)
Probe.nok getter: near(this.q, 'Vector', 0)
Probe.nodir getter: near(this.zero, 'Vector', 2)
Probe.nope getter: why(near(this.q, 'Nothing', 2))
Probe.maths getter: dot(unit(Vector:a.v), unit(Vector:a.v))
Probe.dims getter: Vector:a.dim
";

fn host_with(file: Option<&str>) -> Host {
    let h = open(file);
    h.load_pack("vector", &vector_pack()).unwrap();
    h.load_pack("probe", PROBE).unwrap();
    h.rebuild();
    h
}
fn vec_born(h: &Host, id: &str) { h.born(&format!("Vector:{id}"), "Vector", Map::new(), "test"); }
fn put(h: &Host, id: &str, v: &[f64]) { vec_born(h, id); h.write(&format!("Vector:{id}"), "v", json!(v), "test"); }
fn probe(h: &Host, q: &[f64], k: i64) { h.born("Probe:p", "Probe", props(json!({"q": q, "k": k, "short": [1.0, 2.0], "zero": [0.0, 0.0, 0.0, 0.0]})), "test"); }
fn top(h: &Host) -> Vec<(String, f64)> {
    h.get("Probe:p", "top").as_array().unwrap().iter().map(|d| (d["eid"].as_str().unwrap().to_string(), d["score"].as_f64().unwrap())).collect()
}
fn rng(seed: &mut u64) -> f64 { *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407); ((*seed >> 33) as f64 / (1u64 << 31) as f64) * 2.0 - 1.0 }

#[test]
fn the_header_declares_a_mapped_class_with_its_dtype_and_refuses_another_dtype() {
    let p = parse_full("PACK p 1\nMAPPED Vector f16\nVector.dim getter: 1\n").unwrap();
    assert_eq!(p.mapped, vec![("Vector".to_string(), "f16".to_string())]);
    let e = parse_full("PACK p 1\nMAPPED Vector f32\n").unwrap_err();
    assert!(e.contains("f32") && e.contains("f16"), "{e}");
    assert!(parse_full("PACK p 1\nMAPPED\n").unwrap_err().contains("MAPPED needs a class"));
    assert!(parse_full("PACK p 1\nMAPPED Vector\n").unwrap_err().contains("dtype"), "the dtype is part of the line");
    let both = parse_full("PACK p 1\nMAPPED Vector f16\nRESIDENT Vector\n").unwrap_err();
    assert!(both.contains("Vector") && both.contains("one storage mode"), "{both}");
}

#[test]
fn only_the_owner_declares_a_mapped_class() {
    let h = open(None);
    h.load_pack("vector", &vector_pack()).unwrap();
    let e = h.load_pack("other", "PACK other 1\nIMPORT vector 1\nMAPPED Vector f16\n").unwrap_err();
    assert!(e.contains("only the owner"), "{e}");
}

#[test]
fn a_written_list_lives_in_the_native_store_and_the_row_holds_a_summary_only() {
    let file = tmpfile("row");
    let h = host_with(Some(&file));
    let v: Vec<f64> = { let mut s = 7u64; (0..1024).map(|_| rng(&mut s)).collect() };
    put(&h, "a", &v);
    let rows = h.rows();
    let row = rows["Vector:a"].to_string();
    assert!(row.contains("[1024 numbers, f16]"), "the row says what it holds: {row}");
    assert!(row.len() < 400, "no 1024 numbers in the row ({} bytes): {row}", row.len());
    assert_eq!(h.get("Vector:a", "v"), json!("[1024 numbers, f16]"));
    assert_eq!(h.get("Vector:a", "dim"), json!(1024), "the dim getter");
}

#[test]
fn a_rule_reads_v_as_a_list_of_the_decoded_f16_floats() {
    let h = host_with(None);
    put(&h, "a", &[1.0, 0.1, -2.5, 3.0]);
    probe(&h, &[1.0, 0.0, 0.0, 0.0], 1);
    let back = h.get("Probe:p", "back");
    let back = back.as_array().unwrap();
    assert_eq!(back.len(), 4);
    assert_eq!(back[0], json!(1.0));
    assert_eq!(back[1], json!(0.0999755859375), "f16 rounds 0.1");
    assert_eq!(back[2], json!(-2.5));
    let m = h.get("Probe:p", "maths").as_f64().unwrap();
    assert!((m - 1.0).abs() < 1e-9, "the builtins take it: dot(unit(v), unit(v)) = {m}");
}

#[test]
fn the_dimension_is_fixed_by_the_first_vector_and_another_length_is_refused_with_a_receipt() {
    let h = host_with(None);
    put(&h, "a", &[1.0, 2.0, 3.0, 4.0]);
    put(&h, "b", &[1.0, 2.0, 3.0]);
    assert_eq!(h.get("Vector:b", "v"), J::Null, "nothing was stored");
    let why = h.get("Vector:b", "refused");
    let why = why.as_str().unwrap_or("");
    assert!(why.contains("3 numbers") && why.contains("4") && why.contains("dimension"), "a receipt says why: {why:?}");
    assert_eq!(h.get("Vector:a", "dim"), json!(4), "the first vector stands");
}

#[test]
fn a_value_that_is_not_a_list_of_numbers_is_refused_loudly() {
    let h = host_with(None);
    put(&h, "a", &[1.0, 2.0]);
    vec_born(&h, "t");
    h.write("Vector:t", "v", json!("hello"), "test");
    assert!(h.get("Vector:t", "refused").as_str().unwrap_or("").contains("list of numbers"), "text");
    vec_born(&h, "u");
    h.write("Vector:u", "v", json!([1.0, "x"]), "test");
    assert!(h.get("Vector:u", "refused").as_str().unwrap_or("").contains("not a number"), "a list with a word");
    vec_born(&h, "w");
    h.write("Vector:w", "v", json!([1.0, 1.0e9]), "test");
    assert!(h.get("Vector:w", "refused").as_str().unwrap_or("").contains("f16"), "out of range");
}

#[test]
fn near_gives_the_k_nearest_by_cosine_best_first_as_eid_score_dicts() {
    let h = host_with(None);
    put(&h, "x", &[1.0, 0.0, 0.0, 0.0]);
    put(&h, "y", &[1.0, 1.0, 0.0, 0.0]);
    put(&h, "z", &[0.0, 0.0, 1.0, 0.0]);
    put(&h, "w", &[-1.0, 0.0, 0.0, 0.0]);
    probe(&h, &[2.0, 0.0, 0.0, 0.0], 3);   // a query's length does not matter, only its direction
    let t = top(&h);
    assert_eq!(t.iter().map(|(e, _)| e.as_str()).collect::<Vec<_>>(), ["Vector:x", "Vector:y", "Vector:z"]);
    assert!((t[0].1 - 1.0).abs() < 1e-6, "{t:?}");
    assert!((t[1].1 - 0.70710678).abs() < 1e-3, "{t:?}");
    assert!(t[2].1.abs() < 1e-6, "{t:?}");
    h.write("Probe:p", "k", json!(10), "test");
    let t = top(&h);
    assert_eq!(t.len(), 4, "k beyond the class gives all of it");
    assert_eq!(t[3].0, "Vector:w");
    assert!((t[3].1 + 1.0).abs() < 1e-6);
}

#[test]
fn ties_break_by_eid_never_by_position() {
    let h = host_with(None);
    // born in the reverse of the eid order, with the same direction
    for id in ["e", "d", "c", "b", "a"] { put(&h, id, &[0.0, 3.0, 0.0, 0.0]); }
    probe(&h, &[0.0, 1.0, 0.0, 0.0], 3);
    assert_eq!(top(&h).iter().map(|(e, _)| e.clone()).collect::<Vec<_>>(), ["Vector:a", "Vector:b", "Vector:c"]);
    // a removal moves a row: the answer does not depend on where rows sit
    h.write("Vector:a", "retire", json!(true), "test");
    assert_eq!(top(&h).iter().map(|(e, _)| e.clone()).collect::<Vec<_>>(), ["Vector:b", "Vector:c", "Vector:d"]);
}

#[test]
fn a_recompute_gives_the_identical_answer_on_one_device() {
    let h = host_with(None);
    let mut s = 99u64;
    for i in 0..40 { let v: Vec<f64> = (0..64).map(|_| rng(&mut s)).collect(); put(&h, &format!("{i:02}"), &v); }
    let q: Vec<f64> = (0..64).map(|_| rng(&mut s)).collect();
    probe(&h, &q, 7);
    let first = h.get("Probe:p", "top");
    h.write("Probe:p", "k", json!(8), "test");
    h.write("Probe:p", "k", json!(7), "test");
    assert_eq!(h.get("Probe:p", "top"), first, "same inputs, same bits");
}

#[test]
fn the_failures_of_near_are_reasoned_values_never_a_crash() {
    let h = host_with(None);
    probe(&h, &[1.0, 0.0, 0.0, 0.0], 2);
    // an empty class is a reasoned null (a value a rule can test with `why`)
    assert!(h.get("Probe:p", "nope").as_str().unwrap_or("").contains("no vectors"), "an empty class: {:?}", h.get("Probe:p", "nope"));
    put(&h, "a", &[1.0, 0.0, 0.0, 0.0]);
    // the rest are errors of the getter, as `dot: shapes not aligned` is: the property says why in `failed`
    let failed = h.get("Probe:p", "failed");
    let f = |k: &str| failed[k].as_str().unwrap_or("").to_string();
    assert!(f("wrongdim").contains("2 numbers") && f("wrongdim").contains("4") && f("wrongdim").contains("dimension"), "wrong dimension: {failed}");
    assert!(f("nok").contains("k is 0"), "k < 1: {failed}");
    assert!(f("nodir").contains("all zero"), "a zero query: {failed}");
    assert_eq!(h.get("Probe:p", "wrongdim"), J::Null);
}

#[test]
fn a_getter_that_calls_near_recomputes_when_a_vector_comes_goes_or_changes() {
    let h = host_with(None);
    put(&h, "a", &[1.0, 0.0, 0.0, 0.0]);
    probe(&h, &[1.0, 1.0, 0.0, 0.0], 5);
    assert_eq!(top(&h).len(), 1);
    put(&h, "b", &[0.0, 1.0, 0.0, 0.0]);
    assert_eq!(top(&h).len(), 2, "a new vector");
    h.write("Vector:a", "v", json!([0.0, 1.0, 1.0, 0.0]), "test");
    assert_eq!(top(&h)[0].0, "Vector:b", "a changed vector: {:?}", top(&h));
    h.write("Vector:b", "v", J::Null, "test");
    assert_eq!(top(&h).len(), 1, "a cleared v leaves the store");
}

#[test]
fn an_outside_write_is_journalled_compact_and_a_replay_restores_the_same_f16_values() {
    let h = host_with(None);
    let mut s = 5u64;
    let v: Vec<f64> = (0..1024).map(|_| rng(&mut s)).collect();
    vec_born(&h, "a");
    h.write("Vector:a", "v", json!(v), "test");
    let lines = h.journal();
    let line = lines.iter().find_map(|l| match l { Line::Write { eid, prop, new, .. } if eid == "Vector:a" && prop == "v" => Some(new.clone()), _ => None }).expect("a journal line");
    let b64 = line["$f16"].as_str().expect("compact form {\"$f16\": base64}");
    assert_eq!(b64.len(), 2732, "1024 f16 values = 2048 bytes = 2732 base64 characters");
    assert!(line.to_string().len() < 2800, "no JSON floats in the journal line");
    // a replay on a fresh host
    let h2 = host_with(None);
    h2.replay(&lines);
    h2.born("Probe:p", "Probe", props(json!({"q": v, "k": 1})), "test");
    assert_eq!(h2.get("Vector:a", "v"), json!("[1024 numbers, f16]"));
    h.born("Probe:p", "Probe", props(json!({"q": v, "k": 1})), "test");
    assert_eq!(h2.get("Probe:p", "top"), h.get("Probe:p", "top"), "the same values, the same answer");
    assert!(h2.get("Probe:p", "top")[0]["score"].as_f64().unwrap() > 0.9999);
    // an old journal (no vectors) replays as it did
    let old = vec![Line::Born { ts: 1.0, writer: "test".into(), eid: "Probe:q".into(), cls: "Probe".into(), props: props(json!({"k": 2})), by: None }];
    let h3 = host_with(None);
    h3.replay(&old);
    assert_eq!(h3.get("Probe:q", "k"), json!(2));
}

#[test]
fn a_rule_that_writes_v_is_a_derived_write_nothing_is_journalled_for_it() {
    let h = host_with(None);
    h.load_pack("copy", "PACK copy 1\nIMPORT vector 1\nSrc.go on-setter copy:\n  ALWAYS:\n    Vector:d.v = this.nums\n").unwrap();
    h.born("Src:1", "Src", Map::new(), "test");
    h.write("Src:1", "nums", json!([1.0, 2.0, 3.0]), "test");
    h.write("Src:1", "go", json!(1), "test");
    assert_eq!(h.get("Vector:d", "v"), json!("[3 numbers, f16]"));
    assert!(!h.journal().iter().any(|l| matches!(l, Line::Write { eid, prop, .. } if eid == "Vector:d" && prop == "v")), "derived: a replay of the cause writes it again");
}

#[test]
fn the_vectors_survive_a_restart() {
    let file = tmpfile("restart");
    let mut s = 3u64;
    let vs: Vec<Vec<f64>> = (0..6).map(|_| (0..32).map(|_| rng(&mut s)).collect()).collect();
    let before;
    {
        let h = host_with(Some(&file));
        for (i, v) in vs.iter().enumerate() { put(&h, &format!("{i}"), v); }
        probe(&h, &vs[2], 3);
        before = h.get("Probe:p", "top");
    }
    let h = open(Some(&file));
    h.load_pack("vector", &vector_pack()).unwrap();
    h.load_pack("probe", PROBE).unwrap();
    h.load_stored_entities();
    h.rebuild();
    h.born("Probe:p", "Probe", props(json!({"q": vs[2], "k": 3})), "test");
    assert_eq!(h.get("Probe:p", "top"), before, "the same nearest, the same scores");
    assert_eq!(h.get("Vector:3", "v"), json!("[32 numbers, f16]"));
    // the dimension is still fixed after the restart
    put(&h, "late", &[1.0, 2.0]);
    assert!(h.get("Vector:late", "refused").as_str().unwrap_or("").contains("32"), "{:?}", h.get("Vector:late", "refused"));
}

#[test]
fn retiring_an_entity_removes_its_vector_from_the_store_and_from_the_file() {
    let file = tmpfile("retire");
    {
        let h = host_with(Some(&file));
        put(&h, "a", &[1.0, 0.0]);
        put(&h, "b", &[0.0, 1.0]);
        h.write("Vector:a", "retire", json!(true), "test");
    }
    let db = rusqlite::Connection::open(&file).unwrap();
    let n: i64 = db.query_row("SELECT count(*) FROM c_vectors", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 1, "the retired vector is deleted from the table");
    let h = open(Some(&file));
    h.load_pack("vector", &vector_pack()).unwrap();
    h.load_pack("probe", PROBE).unwrap();
    h.load_stored_entities();
    h.born("Probe:p", "Probe", props(json!({"q": [1.0, 1.0], "k": 5})), "test");
    assert_eq!(top(&h).iter().map(|(e, _)| e.clone()).collect::<Vec<_>>(), ["Vector:b"]);
}

#[test]
fn the_memory_policy_evicts_rows_never_the_vector_store() {
    let file = tmpfile("evict");
    let h = host_with(Some(&file));
    for i in 0..8 { put(&h, &format!("v{i}"), &[1.0, i as f64]); }
    probe(&h, &[1.0, 1.0], 8);
    h.set_memory(MemoryConfig { budget_mb: Some(1), soft: None, hard: Some(1.0), batch: Some(100) });
    let rss = Arc::new(AtomicU64::new(10 << 20));
    let r2 = rss.clone();
    h.set_rss_reading(Box::new(move || Some(r2.load(Ordering::SeqCst))));
    h.write("Probe:p", "k", json!(7), "test");   // a round: the policy runs at its end
    rss.store(0, Ordering::SeqCst);
    assert_eq!(top(&h).len(), 7, "the rows may be unloaded; the vectors stand");
    h.write("Probe:p", "k", json!(8), "test");
    assert_eq!(top(&h).len(), 8);
}

#[test]
fn what_a_virtual_trigger_writes_to_v_is_an_outside_write_journalled_compact() {
    // the door's shape: a request (VIRTUAL, not journalled) whose setter writes the entity; the host stamps that write outside, and the line must not hold JSON floats
    let h = host_with(None);
    h.load_pack("door", "PACK door 1\nIMPORT vector 1\nVIRTUAL Req\nReq.go on-setter put:\n  ALWAYS:\n    Vector:d.v = this.nums\n").unwrap();
    h.hold("Req:1");   // nothing else holds it: the collector would take it at the end of its birth round
    h.born("Req:1", "Req", props(json!({"nums": [0.5, 0.25, 1.0, 2.0]})), "test");
    h.write("Req:1", "go", json!(1), "test");
    assert_eq!(h.get("Vector:d", "v"), json!("[4 numbers, f16]"));
    let line = h.journal().into_iter().find_map(|l| match l { Line::Write { eid, prop, new, .. } if eid == "Vector:d" && prop == "v" => Some(new), _ => None }).expect("journalled: the trigger is not");
    assert!(line["$f16"].is_string(), "compact: {line}");
}

#[test]
fn a_birth_that_names_v_stores_it_like_a_write() {
    let h = host_with(None);
    h.born("Vector:b", "Vector", props(json!({"v": [1.0, 2.0, 3.0]})), "test");
    assert_eq!(h.get("Vector:b", "v"), json!("[3 numbers, f16]"));
    assert!(!h.journal().iter().any(|l| matches!(l, Line::Born { props, .. } if props.contains_key("v"))), "the birth line holds no numbers");
}

#[test]
fn the_shipped_barest_packs_list_the_vector_pack_and_it_loads() {
    let list = std::fs::read_to_string(repo().join("membrane/apps/barest.packs")).unwrap();
    assert!(list.lines().any(|l| l.trim() == "vector"));
    let h = open(None);
    h.load_pack("vector", &vector_pack()).unwrap();
}

/// A measurement, not a check (`cargo test --release --test vector_1143 -- --ignored --nocapture`): `near` over 20,000 random 1024-d vectors, the engine's whole path.
#[test]
#[ignore]
fn measure_near_over_twenty_thousand_vectors() {
    let h = host_with(None);
    let mut s = 11u64;
    let t = std::time::Instant::now();
    for i in 0..20_000 { let v: Vec<f64> = (0..1024).map(|_| rng(&mut s)).collect(); put(&h, &format!("{i:05}"), &v); }
    eprintln!("MEASURE 20,000 writes: {:?}", t.elapsed());
    let q: Vec<f64> = (0..1024).map(|_| rng(&mut s)).collect();
    probe(&h, &q, 10);
    let mut best = std::time::Duration::MAX;
    for i in 0..5 {
        let q2: Vec<f64> = (0..1024).map(|_| rng(&mut s)).collect();
        let t = std::time::Instant::now();
        h.write("Probe:p", "q", json!(q2), "test");   // the getter `top` recomputes: one near
        let d = t.elapsed();
        eprintln!("MEASURE near #{i} (write + recompute through the engine): {d:?}");
        best = best.min(d);
    }
    eprintln!("MEASURE best {best:?}; top = {}", h.get("Probe:p", "top").to_string().chars().take(120).collect::<String>());
    let j = h.journal().into_iter().find_map(|l| match l { Line::Write { eid, prop, new, .. } if eid == "Vector:00000" && prop == "v" => Some(new.to_string().len()), _ => None });
    eprintln!("MEASURE journal bytes of a vector line (new only): {j:?}");
}
