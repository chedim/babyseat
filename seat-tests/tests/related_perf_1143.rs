//! `SemText.related` on a store like the live one (#1143, w36): a text of ~170 distinct words, 20 of them in every other text (the, a, and ...).
use membrane_vm::host::{Host, Options};
use membrane_vm::stats::{self, KIND_GETTER};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

/// the eval counters are process-wide: the tests of this file take turns
static ONE_AT_A_TIME: std::sync::Mutex<()> = std::sync::Mutex::new(());
fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn seat() -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(60_000));
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.rebuild();
    h
}
const DOCS: &str = "PACK semdocs 1\nIMPORT semtext 1\nDoc.go on-setter make:\n  ALWAYS:\n    Doc:body[this.k] = SemText(text: this.t)\n    Doc:body[this.k].mention = True\n";

/// `docs` texts; each holds `common` words every text holds, and `own` words of its own plus a few shared with a neighbour. Text 0 is the asked one.
fn world(docs: usize, common: usize, own: usize) -> Host {
    let h = seat();
    h.load_pack("semdocs", DOCS).unwrap();
    h.rebuild();
    for d in 0..docs {
        let mut ws: Vec<String> = (0..common).map(|i| format!("common{}", "x".repeat(i % 3) + &i.to_string())).collect();
        ws.extend((0..own).map(|i| format!("own{}w{}", if d == 0 { (i % 5) + 1 } else { d }, i)));
        let t = ws.join(" ") + ".";
        h.born(&format!("Doc:{d}"), "Doc", props(json!({"k": d.to_string(), "t": t})), "person");
        h.pump();
        h.write(&format!("Doc:{d}"), "go", json!(true), "person");
        h.pump();
        if std::env::var("W36V").is_ok() { eprintln!("doc {d} at {:?}", std::time::Instant::now()); }
    }
    h
}
fn ms_of(prop: &str, cls: &str) -> (u64, f64) {
    stats::global_lifetime().into_iter().find(|r| r.cls == cls && r.prop == prop && r.kind == KIND_GETTER).map_or((0, 0.0), |r| (r.calls, r.ns as f64 / 1e6))
}
fn steps_of(prop: &str, cls: &str) -> u64 {
    stats::global_lifetime().into_iter().find(|r| r.cls == cls && r.prop == prop && r.kind == KIND_GETTER).map_or(0, |r| r.steps)
}

#[test]
#[ignore]
fn related_report() {
    stats::set_on(true);
    for (docs, common, own) in [(6, 20, 40), (12, 20, 40), (24, 20, 40)] {
        let h = world(docs, common, own);
        let (c0, m0) = ms_of("related", "SemText"); let s0 = steps_of("related", "SemText");
        let t = std::time::Instant::now();
        h.write("Doc:body:0", "related_n", json!(5), "person");
        h.pump();
        let wall = t.elapsed().as_secs_f64() * 1e3;
        let (c1, m1) = ms_of("related", "SemText");
        let r = h.get("Doc:body:0", "related");
        for p in ["mention_done", "mention_table", "words"] { let (c, m) = ms_of(p, "SemText"); println!("    {p}: {c} calls {m:.2} ms {} steps", steps_of(p, "SemText")); }
        println!("related steps {}", steps_of("related", "SemText") - s0);
        println!("docs {docs:3} common {common} own {own}: related {} calls {:.1} ms (wall {wall:.1} ms), rows {}", c1 - c0, m1 - m0, r.as_array().map_or(0, |a| a.len()));
    }
}

/// The cost of `related` follows the EDGES it walks, and each edge costs a few steps, not tens: an entity's rows are built once per entity (a getter that caches,
/// recomputed when its `mentioned_in` changes), not once per ask. The live seat's 950-edge ask took 8-15 ms against a 10 ms allowance (`Trip ... #related`).
#[test]
fn an_ask_for_related_costs_few_steps_per_edge_walked() {
    let _turn = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    stats::set_on(true);
    let h = world(16, 20, 30);
    let edges: usize = h.get("Doc:body:0", "mentions").as_array().unwrap().iter()
        .map(|m| h.get(m["$ref"].as_str().unwrap_or_else(|| m.as_str().unwrap()), "mentioned_in").as_array().map_or(0, |a| a.len())).sum();
    let s0 = steps_of("related", "SemText");
    h.write("Doc:body:0", "related_n", json!(5), "person");
    h.pump();
    let steps = steps_of("related", "SemText") - s0;
    let r = h.get("Doc:body:0", "related");
    assert_eq!(r.as_array().unwrap().len(), 5, "{r}");
    assert!(edges > 300, "the world has the edges: {edges}");
    assert!(steps < 8 * edges as u64, "{steps} steps for {edges} edges: {:.1} per edge", steps as f64 / edges as f64);
}

/// A mention of a text of W words evaluates `mention_done` a few times per word, not W times per word: it waits for every span's forms (a higher level than they)
/// instead of re-scanning the spans after each one classifies. (Live: 4162 ms lifetime for ~20 texts; 1100 evaluations of a 60-word text.)
#[test]
fn a_mention_evaluates_mention_done_about_once_per_span() {
    let _turn = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    stats::set_on(true);
    let words = 60usize;
    let h = seat();
    h.load_pack("semdocs", DOCS).unwrap();
    h.rebuild();
    let t = (0..words).map(|i| format!("w{i}x")).collect::<Vec<_>>().join(" ") + ".";
    h.born("Doc:z", "Doc", props(json!({"k": "z", "t": t})), "person");
    h.pump();
    let before = ms_of("mention_done", "SemText").0;
    h.write("Doc:z", "go", json!(true), "person");
    h.pump();
    let calls = ms_of("mention_done", "SemText").0 - before;
    assert_eq!(h.get("Doc:body:z", "mention_n"), json!(words));
    assert!(calls <= words as u64 + 5, "{calls} evaluations of mention_done for {words} words");
}

const PARTS: &str = "PACK xp 1
IMPORT mentions 1
IMPORT semtext 1
SemText.x_parts getter: [prop(m, 'edge_rows') for m in this.mentions] if this.related_n != None and this.mentions != None else None
SemText.x_hits getter: json('[' + join([p for p in this.x_parts if p != ''], ',') + ']') if this.x_parts != None else None
SemText.x_rows getter: [{'text': g['rows'][0]['text'], 'sent': g['rows'][0]['sent'], 'shared': len(g['rows']), 'score': dot([r['w'] for r in g['rows']], [1 for r in g['rows']])} for g in group_by(this.x_hits, 'key') if g['rows'][0]['text'] != this.eid] if this.x_hits != None else None
SemText.x_top getter: head(sort_by(this.x_rows, 'score', True), this.related_n) if this.x_rows != None else None
SemText.x_final getter: [{'semtext': r['text'], 'sentence_index': r['sent'], 'text': prop(prop(r['text'], 'sentences')[r['sent']], 'text'), 'shared': r['shared']} for r in this.x_top] if this.x_top != None else None
";

/// MEASURE on a store: `W36_STORE=<copy of a seat store> cargo test --release --features bin --test related_perf_1143 -- --ignored --nocapture real_store_report`
#[test]
#[ignore]
fn real_store_report() {
    let Ok(path) = std::env::var("W36_STORE") else { return };
    stats::set_on(true);
    membrane_vm::expr::set_eval_time_ms(Some(60_000));
    let mut o = Options::new(Some(path));
    o.effects = false;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_pack("xp", PARTS).unwrap();
    h.load_stored_entities();
    h.load_everything();
    h.rebuild();
    let e = std::env::var("W36_EID").unwrap();
    for p in ["related", "x_parts", "x_hits", "x_rows", "x_top", "x_final"] { let (c, m) = ms_of(p, "SemText"); println!("boot {p}: {c} calls {m:.2} ms"); }
    for n in [4, 6, 8] {
        h.write(&e, "related_n", json!(n), "person");
        h.pump();
    }
    for p in ["related", "x_parts", "x_hits", "x_rows", "x_top", "x_final"] { let (c, m) = ms_of(p, "SemText"); println!("after: {p}: {c} calls {m:.2} ms {} steps", steps_of(p, "SemText")); }
}
