//! ENTITY RECALL by worldline on SemText (#1143, step 5 of the field; membrane/packs/entityrecall.c1f): for the ask (the shared recall's query, `r_query`), the entities nearest it
//! by their WORLDLINE (a young entity's mean, an old one's episodes), centred on the field's background, then the texts that moved those entities, each scored by the sum over
//! the entities of (similarity x the move the text made of it). One getter, `SemText.entity_votes`, rows `{key, of, text, score}`; the vote of `recall` reads it (vote_1143.rs).
//! Every test runs the shipped packs in process, with no model server: the receipts are written by the test (the field_1143 harness).
use cliffvm::host::{boot_started, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// `Box.say` makes a mentioned chat body (what the chat does); `Box.q` makes a text that is NOT mentioned (the ask: it moves nothing); `Probe` reads vectors back
const MAKER: &str = "PACK ermaker 1\nIMPORT semtext 1\nIMPORT mentions 1\nIMPORT chat 1\nIMPORT vector 1\n\
Probe.back getter: [${e}['v'] for e in this.eids]\n\
Box.say on-setter say:\n  WHEN this.say != None:\n    membrane::Chat:main:message[${this.say}['id']]:body = SemText(text: ${this.say}['text'])\n    membrane::Chat:main:message[${this.say}['id']]:body.mention = True\n\
Box.q on-setter q:\n  WHEN this.q != None:\n    Leaf:x[${this.q}['id']]:body = SemText(text: ${this.q}['text'])\n";

fn load(h: &Host, layer: Option<&str>) {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        let l = h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
        assert!(l.msgs.is_empty() || !n.starts_with("entityrecall"), "the entity recall pack loads: {:?}", l.msgs);
    }
    h.load_app(&r.join("membrane/packs/launcher.c1f"), false).unwrap();
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_pack("ermaker", MAKER).unwrap();
    if let Some(l) = layer { h.load_pack("erlayer", l).unwrap(); }
    h.rebuild();
}
fn seat_with(layer: Option<&str>) -> Host {
    cliffvm::expr::set_eval_time_ms(Some(10_000));
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    load(&h, layer);
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!("http://127.0.0.1:9"));
    f.insert("embed_model".into(), json!("m"));
    boot_started(&h, &f, true);
    h.pump();
    h.born("Box:a", "Box", props(json!({})), "person");
    h.pump();
    h
}
fn seat() -> Host { seat_with(None) }
const Q: &str = "membrane::Embed:queue";
fn body(id: &str) -> String { format!("membrane::Chat:main:message:{id}:body") }
fn leaf(id: &str) -> String { format!("Leaf:x:{id}:body") }

fn inputs(h: &Host) -> Vec<String> {
    let j = h.get(Q, "inflight");
    if j.is_null() || h.get(Q, "flying") != json!(true) { return vec![]; }
    j["unc"].as_array().unwrap().iter().map(|x| x["text"].as_str().unwrap().to_string()).collect()
}
/// answer every request until the queue is idle; `f` is the model
fn drain(h: &Host, f: &dyn Fn(&str) -> Vec<f64>) {
    for _ in 0..80 {
        let ins = inputs(h);
        if ins.is_empty() { break; }
        let id = h.get(Q, "inflight")["id"].clone();
        let embeddings: Vec<Vec<f64>> = ins.iter().map(|t| f(t)).collect();
        h.write("Fetch:embed", "request_result", json!({"id": id, "status": "ok", "rc": 0, "head": "HTTP/1.1 200 OK", "http_status": 200, "headers": {}, "body": json!({"model": "m", "embeddings": embeddings}).to_string()}), "driver:request");
        h.pump();
    }
}
fn say(h: &Host, id: &str, text: &str) { h.write("Box:a", "say", json!({"id": id, "text": text}), "person"); h.pump(); }
/// the ask: a text that is not mentioned, embedded by the queue like any text
fn ask(h: &Host, id: &str, text: &str) { h.write("Box:a", "q", json!({"id": id, "text": text}), "person"); h.pump(); }
fn on(h: &Host, eid: &str) { h.write(eid, "recall_on", json!(true), "person"); h.pump(); }
fn back(h: &Host, eids: &[&str]) -> Vec<J> {
    let n = h.snapshot().keys().filter(|k| k.starts_with("Probe:")).count();
    let p = format!("Probe:p{n}");
    h.born(&p, "Probe", props(json!({"eids": eids})), "person");
    h.pump();
    h.get(&p, "back").as_array().unwrap().clone()
}
fn nums(v: &J) -> Vec<f64> { v.as_array().unwrap_or_else(|| panic!("not a list: {v}")).iter().map(|x| x.as_f64().unwrap()).collect() }
fn unit(v: &[f64]) -> Vec<f64> { let n = v.iter().map(|x| x * x).sum::<f64>().sqrt(); v.iter().map(|x| x / n).collect() }
fn sub(a: &[f64], b: &[f64]) -> Vec<f64> { a.iter().zip(b).map(|(x, y)| x - y).collect() }
fn dot(a: &[f64], b: &[f64]) -> f64 { a.iter().zip(b).map(|(x, y)| x * y).sum() }
/// the centred cosine of a point and the query: dot(unit(p - bg), unit(q - bg))
fn cc(p: &[f64], q: &[f64], bg: &[f64]) -> f64 { dot(&unit(&sub(p, bg)), &unit(&sub(q, bg))) }
fn bg(h: &Host) -> Vec<f64> { nums(&back(h, &["FieldBg:all"])[0]) }
/// the ranked entities `[(entity, score)]`
fn hits(h: &Host, eid: &str) -> Vec<(String, f64)> {
    let v = h.get(eid, "entity_hits");
    v.as_array().unwrap_or_else(|| panic!("entity_hits of {eid}: {v} ({})", h.get(eid, "failed"))).iter().map(|r| (r["e"].as_str().unwrap().to_string(), r["s"].as_f64().unwrap())).collect()
}
fn votes(h: &Host, eid: &str) -> Vec<J> {
    let v = h.get(eid, "entity_votes");
    v.as_array().unwrap_or_else(|| panic!("entity_votes of {eid}: {v} ({})", h.get(eid, "failed"))).clone()
}

fn model(t: &str) -> Vec<f64> {
    match t {
        "Set aa_x up." => vec![0.9, 0.436, 0.0],
        "Set bb_x up." => vec![0.8, 0.0, 0.6],
        "It is so." | "So it is." => vec![1.0, 0.5, 0.0],
        "Set al_x up." => vec![1.0, 0.0, 0.0],
        "Run al_x now." => vec![0.0, 1.0, 0.0],
        "Hm." => vec![1.0, 0.0, 0.0],
        "Ah." => vec![1.0, 1.0, 0.0],
        "al_x" => vec![0.0, 0.0, 1.0],
        _ => vec![t.len() as f64, 1.0, 2.0],
    }
}
const AL: &str = "Identifier:name:al_x";

#[test]
fn a_young_entity_is_scored_by_its_mean_centred_on_the_background_not_by_its_state() {
    let h = seat();
    for (id, t) in [("m1", "Set al_x up."), ("m2", "Run al_x now."), ("m3", "It is so.")] { say(&h, id, t); drain(&h, &model); }
    assert_eq!(h.get(&format!("FieldState:{AL}"), "n"), json!(2));
    ask(&h, "q", "Ah.");
    drain(&h, &model);
    on(&h, &leaf("q"));
    let b = bg(&h);
    let q = unit(&model("Ah."));
    let mean: Vec<f64> = (0..3).map(|i| (unit(&model("Set al_x up."))[i] + unit(&model("Run al_x now."))[i]) / 2.0).collect();
    let state = nums(&back(&h, &[&format!("FieldState:{AL}")])[0]);
    let (want, by_state) = (cc(&mean, &q, &b), cc(&state, &q, &b));
    assert!((want - by_state).abs() > 0.05, "the test tells the mean from the state: {want} vs {by_state}");
    let got = hits(&h, &leaf("q"));
    let s = got.iter().find(|(e, _)| e == AL).unwrap_or_else(|| panic!("{got:?}")).1;
    assert!((s - want).abs() < 3e-3, "the centred cosine of the mean of its two sentences: {s} vs {want} (by the state: {by_state})");
}

/// a model's layer: every identifier is mature from its first mention, runs of 2, and ONE entity from ONE candidate of each kind
const OLD: &str = "PACK erlayer 1\nIMPORT field 1\nIMPORT entityrecall 1\nSemText.field_mature getter: 1\nSemText.field_ep_run getter: 2\nSemText.ent_k getter: 1\nSemText.ent_over getter: 1\n";

#[test]
fn an_old_entity_whose_state_moved_away_is_found_and_scored_by_an_old_episode() {
    let h = seat_with(Some(OLD));
    let v = |t: &str| -> Vec<f64> {
        match t {
            "old_x" => vec![0.0, 0.0, 1.0],
            "new_x" => vec![1.0, 0.0, 0.0],
            "A old_x one. B old_x two. C old_x six. D old_x ten." => vec![9.0, 9.0, 9.0],
            "A old_x one." | "B old_x two." => vec![1.0, 0.0, 0.1],
            "C old_x six." | "D old_x ten." => vec![0.0, 1.0, 0.1],
            "Use new_x." => vec![0.8, 0.6, 0.0],
            "Hm." => vec![1.0, 0.0, 0.1],
            _ => model(t),
        }
    };
    say(&h, "m1", "A old_x one. B old_x two. C old_x six. D old_x ten.");
    say(&h, "m2", "Use new_x.");
    drain(&h, &v);
    let st = "FieldState:Identifier:name:old_x";
    assert_eq!(h.get(st, "eps").as_array().map(|a| a.len()), Some(2), "two episodes: {}", h.get(st, "eps"));
    ask(&h, "q", "Hm.");
    drain(&h, &v);
    on(&h, &leaf("q"));
    let state = nums(&back(&h, &[st])[0]);
    let q = unit(&v("Hm."));
    assert!(dot(&state, &q) < 0.5, "the state moved away from the first phase: {state:?}");
    let got = hits(&h, &leaf("q"));
    assert_eq!(got.len(), 1, "{got:?}");
    assert_eq!(got[0].0, "Identifier:name:old_x", "found through its first episode though the nearest state is new_x's: {got:?}");
    let ep = unit(&v("A old_x one."));
    assert!((got[0].1 - cc(&ep, &q, &bg(&h))).abs() < 3e-3, "scored by the first episode: {got:?}");
}

#[test]
fn centring_on_the_background_changes_the_order() {
    let h = seat();
    say(&h, "m1", "Set aa_x up.");
    say(&h, "m2", "Set bb_x up.");
    say(&h, "m3", "It is so.");
    say(&h, "m4", "So it is.");
    drain(&h, &model);
    ask(&h, "q", "Hm.");
    drain(&h, &model);
    on(&h, &leaf("q"));
    let (a, b, q) = (unit(&model("Set aa_x up.")), unit(&model("Set bb_x up.")), unit(&model("Hm.")));
    assert!(dot(&a, &q) > dot(&b, &q), "raw cosine puts aa_x first");
    let got = hits(&h, &leaf("q"));
    let order: Vec<&str> = got.iter().map(|(e, _)| e.as_str()).collect();
    assert_eq!(order, vec!["Identifier:name:bb_x", "Identifier:name:aa_x"], "centred, bb_x is first: {got:?}");
}

#[test]
fn with_no_states_the_entity_votes_are_empty() {
    let h = seat();
    ask(&h, "q", "Hm.");
    drain(&h, &model);
    on(&h, &leaf("q"));
    assert_eq!(h.get(&leaf("q"), "entity_votes"), json!([]), "no state: no row, no error");
    assert_eq!(h.get(&leaf("q"), "entity_hits"), json!([]));
}

#[test]
fn nothing_is_computed_while_the_recall_is_off() {
    let h = seat();
    say(&h, "m1", "Set al_x up.");
    drain(&h, &model);
    ask(&h, "q", "Hm.");
    drain(&h, &model);
    assert_eq!(h.get(&leaf("q"), "entity_votes"), J::Null);
}

#[test]
fn a_query_of_another_dimension_gives_empty_votes_not_an_error() {
    let h = seat();
    say(&h, "m1", "Set al_x up.");
    drain(&h, &model);
    ask(&h, "q", "Hm.");
    on(&h, &leaf("q"));
    // the ask's own call answers with a vector of 4 numbers: the states have 3
    let job = h.get(&leaf("q"), "ask_job");
    assert!(job.is_object(), "{job}");
    h.write("Fetch:recall:ask", "request_result", json!({"id": job["id"], "status": "ok", "rc": 0, "http_status": 200, "body": json!({"embeddings": [[1.0, 0.0, 0.0, 0.0]]}).to_string()}), "person");
    h.pump();
    assert_eq!(nums(&h.get(&leaf("q"), "r_query")).len(), 4, "the query is the call's vector");
    assert_eq!(h.get(&leaf("q"), "entity_votes"), json!([]), "{}", h.get(&leaf("q"), "failed"));
    let failed = h.get(&leaf("q"), "failed");
    assert!(failed.as_object().map(|m| m.keys().all(|k| !k.starts_with("ent") || k.starts_with("ent_near_"))).unwrap_or(true), "only the searches say why they are empty (as the cosine lane's own does): {failed}");
    assert!(failed["ent_near_states"].as_str().unwrap_or("").contains("dimension"), "{failed}");
}

#[test]
fn ties_go_by_key_for_the_entities_and_for_the_texts() {
    let h = seat();
    // two identifiers with the same seed (the test model: one vector per text length) in two texts with the same sentence vector: equal scores, equal moves
    say(&h, "m1", "Use zz_x.");
    say(&h, "m2", "Use aa_x.");
    drain(&h, &model);
    ask(&h, "q", "Hm.");
    drain(&h, &model);
    on(&h, &leaf("q"));
    let got = hits(&h, &leaf("q"));
    assert_eq!(got.len(), 2);
    assert_eq!(got[0].1, got[1].1, "a tie: {got:?}");
    assert_eq!((got[0].0.as_str(), got[1].0.as_str()), ("Identifier:name:aa_x", "Identifier:name:zz_x"));
    let rows = votes(&h, &leaf("q"));
    let ofs: Vec<&str> = rows.iter().map(|r| r["of"].as_str().unwrap()).collect();
    assert_eq!(rows[0]["score"], rows[1]["score"], "{rows:?}");
    assert_eq!(ofs, vec![body("m1").as_str(), body("m2").as_str()], "the texts tie: by key, m1 first (aa_x, ranked first, is m2's): {rows:?}");
}

#[test]
fn the_texts_that_moved_the_nearest_entities_rank_by_similarity_times_their_move() {
    let h = seat();
    // al_x: seed e3; its first text moves it far (toward e1), its second less (toward a sentence near the state it reached); m3 moves nothing near
    let v = |t: &str| -> Vec<f64> { match t { "Run al_x now." => vec![1.0, 0.2, 0.0], "Go al_x on." => vec![1.0, 0.25, 0.05], "Put al_x by." => vec![0.0, 0.0, 1.0], "Hm al_x." => vec![1.0, 0.1, 0.3], _ => model(t) } };
    // all four said, then one drain: texts that land while the seed is out wait on the entity and are walked once, in order, when it lands (field.c1f)
    for (id, t) in [("m1", "Set al_x up."), ("m2", "It is so. Run al_x now."), ("m3", "So it is."), ("m4", "Go al_x on. Put al_x by.")] { say(&h, id, t); }
    drain(&h, &v);
    // the ask is a chat body that mentions al_x too: it moves the field, and it is never a row
    say(&h, "m9", "Hm al_x.");
    drain(&h, &v);
    on(&h, &body("m9"));
    let got = hits(&h, &body("m9"));
    let s = got.iter().find(|(e, _)| e == AL).unwrap_or_else(|| panic!("{got:?}")).1;
    let rows = votes(&h, &body("m9"));
    assert!(rows.iter().all(|r| r["of"] != json!(body("m9"))), "never the ask: {rows:?}");
    assert!(rows.iter().all(|r| r["of"] != json!(body("m3"))), "a text that moved no near entity is no row: {rows:?}");
    let m1 = h.get(&body("m1"), "field_moves").as_array().unwrap().iter().find(|r| r["e"] == json!(AL)).unwrap()["m"].as_f64().unwrap();
    let m2 = h.get(&body("m2"), "field_moves").as_array().unwrap().iter().find(|r| r["e"] == json!(AL)).unwrap()["m"].as_f64().unwrap();
    let r1 = rows.iter().find(|r| r["of"] == json!(body("m1"))).unwrap_or_else(|| panic!("{rows:?}"));
    let r2 = rows.iter().find(|r| r["of"] == json!(body("m2"))).unwrap_or_else(|| panic!("{rows:?}"));
    // the score sums over the hit entities: only al_x moved in m1 and m2 (the short words have no state)
    assert!((r1["score"].as_f64().unwrap() - s * m1).abs() < 1e-3, "{r1} vs {s} x {m1}");
    assert!((r2["score"].as_f64().unwrap() - s * m2).abs() < 1e-3, "{r2} vs {s} x {m2}");
    // the row is the sentence that holds the move: m2's second sentence (it starts at 10)
    assert_eq!(r2["key"], json!(format!("{}#10", body("m2"))));
    assert_eq!(r2["text"], json!("Run al_x now."));
    assert_eq!(r1["key"], json!(format!("{}#0", body("m1"))));
    // a text with two moves of al_x: its row is the sentence whose part (score x move) is the larger, here the second
    let mv4: Vec<f64> = h.get(&body("m4"), "field_moves").as_array().unwrap().iter().filter(|r| r["e"] == json!(AL)).map(|r| r["m"].as_f64().unwrap()).collect();
    assert_eq!(mv4.len(), 2, "{mv4:?}");
    assert!(s * mv4[1] > s * mv4[0] + 0.05, "the test's second sentence holds the larger part: s {s}, moves {mv4:?}");
    let r4 = rows.iter().find(|r| r["of"] == json!(body("m4"))).unwrap_or_else(|| panic!("{rows:?}"));
    assert_eq!(r4["key"], json!(format!("{}#12", body("m4"))), "{r4}");
    assert!((r4["score"].as_f64().unwrap() - s * (mv4[0] + mv4[1])).abs() < 1e-3, "the text's score sums its moves: {r4}");
    let keys: Vec<&J> = rows.iter().map(|r| &r["key"]).collect();
    let mut sorted = rows.clone();
    sorted.sort_by(|a, b| b["score"].as_f64().unwrap().partial_cmp(&a["score"].as_f64().unwrap()).unwrap());
    assert_eq!(keys, sorted.iter().map(|r| &r["key"]).collect::<Vec<_>>(), "best first");
    assert!((m1 - m2).abs() > 0.1, "two different moves: {m1} {m2}");
    let mut want = vec![("m1", s * m1), ("m2", s * m2), ("m4", s * (mv4[0] + mv4[1]))];
    want.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    assert_eq!(rows.iter().map(|r| r["of"].as_str().unwrap().to_string()).collect::<Vec<_>>(), want.iter().map(|(id, _)| body(id)).collect::<Vec<_>>(), "{rows:?}");
}

/// a model's layer: identifiers keep episodes from their 3rd mention, runs of 2
const EARLY: &str = "PACK erlayer 1\nIMPORT field 1\nSemText.field_mature getter: 3\nSemText.field_ep_run getter: 2\n";
const A3: [f64; 3] = [1.0, 0.0, 0.1];
const B3: [f64; 3] = [0.0, 1.0, 0.1];
fn early(t: &str) -> Vec<f64> {
    if t.starts_with("A ") || t.starts_with("B ") || t.starts_with("E ") { A3.to_vec() } else if t.starts_with("C ") || t.starts_with("D ") { B3.to_vec() } else if t == "Hm." { A3.to_vec() } else { model(t) }
}
fn score_of(got: &[(String, f64)], e: &str) -> f64 { got.iter().find(|(x, _)| x == e).unwrap_or_else(|| panic!("{e}: {got:?}")).1 }

#[test]
fn an_old_entity_reads_its_mean_until_its_episodes_cover_the_cover_share_and_its_open_run_is_a_point() {
    for (layer, covered) in [(EARLY.to_string(), false), (format!("{EARLY}IMPORT entityrecall 1\nSemText.ent_cover getter: 0.4\n"), true)] {
        let h = seat_with(Some(&layer));
        // ka_x: A A | B B (the episode: the 3rd and 4th mentions), nothing open: the episode holds 2 of 4 mentions
        say(&h, "m1", "A ka_x one. B ka_x two. C ka_x six. D ka_x ten.");
        drain(&h, &early);
        // kb_x: A A | B B | A (open)
        say(&h, "m2", "A kb_x one. B kb_x two. C kb_x six. D kb_x ten. E kb_x yes.");
        drain(&h, &early);
        // a background off the line between A and B
        for (id, t) in [("m3", "It is so."), ("m4", "So it is.")] { say(&h, id, t); drain(&h, &|_t: &str| vec![0.0, 0.3, 1.0]); }
        assert_eq!(h.get("FieldState:Identifier:name:ka_x", "eps").as_array().map(|a| a.len()), Some(1));
        assert_eq!(h.get("FieldState:Identifier:name:kb_x", "run_k"), json!(1));
        ask(&h, "q", "Hm.");
        drain(&h, &early);
        on(&h, &leaf("q"));
        let (a, b, bgv) = (unit(&A3), unit(&B3), bg(&h));
        let mean: Vec<f64> = (0..3).map(|i| (a[i] + a[i] + b[i] + b[i]) / 4.0).collect();
        let got = hits(&h, &leaf("q"));
        let ka = score_of(&got, "Identifier:name:ka_x");
        let want = if covered { cc(&b, &a, &bgv) } else { cc(&mean, &a, &bgv).max(cc(&b, &a, &bgv)) };
        assert!((cc(&mean, &a, &bgv) - cc(&b, &a, &bgv)).abs() > 0.1, "the test tells the mean from the episode");
        assert!((ka - want).abs() < 3e-3, "covered {covered}: {ka} vs {want}");
        assert!((score_of(&got, "Identifier:name:kb_x") - 1.0).abs() < 3e-3, "the open run (A) is the ask's own direction: {got:?}");
    }
}

/// a vector of 16 numbers from the text (FNV-1a), the same text the same vector
fn hashed(t: &str) -> Vec<f64> {
    let mut x: u64 = 0xcbf29ce484222325;
    for b in t.bytes() { x ^= b as u64; x = x.wrapping_mul(0x100000001b3); }
    (0..16u32).map(|i| { let y = x.rotate_left(i * 4) ^ (i as u64).wrapping_mul(0x9e3779b97f4a7c15); ((y % 2000) as f64) / 1000.0 - 1.0 }).collect()
}

#[test]
fn a_realistic_field_stays_inside_the_step_budget() {
    // 300 texts, each one sentence naming 8 of 40 identifiers: every identifier is named by 60 texts (mature, with episodes, its mean still a point), so the 32
    // candidates' means read 60 sentence vectors each and the 8 hits' evidence walks 60 texts of 8 moves each. ER_TEXTS sets another count (to measure).
    let n = std::env::var("ER_TEXTS").ok().and_then(|v| v.parse::<usize>().ok()).unwrap_or(300);
    let h = seat();
    for i in 0..n {
        let ids: Vec<String> = (0..8).map(|j| format!("id{}_x", (i * 3 + j * 5) % 40)).collect();
        say(&h, &format!("m{i}"), &format!("Do {} now.", ids.join(" ")));
        drain(&h, &hashed);
    }
    assert_eq!(h.get("FieldState:Identifier:name:id0_x", "n"), json!(n * 8 / 40));
    ask(&h, "q", "Hm.");
    drain(&h, &hashed);
    on(&h, &leaf("q"));
    let failed = h.get(&leaf("q"), "failed");
    assert!(failed.as_object().map(|m| m.keys().all(|k| !k.starts_with("ent"))).unwrap_or(true), "no evaluation of the entity recall failed: {failed}");
    assert_eq!(hits(&h, &leaf("q")).len(), 8);
    assert_eq!(votes(&h, &leaf("q")).len(), 20);
    if std::env::var("ER_BISECT").is_ok() {
        // the smallest step budget under which no evaluation of the entity recall fails (the recall is asked again under each budget)
        let ok = |b: u64| -> bool {
            cliffvm::expr::set_eval_budget(Some(b));
            h.write(&leaf("q"), "recall_on", json!(false), "person"); h.pump();
            h.write(&leaf("q"), "recall_on", json!(true), "person"); h.pump();
            let f = h.get(&leaf("q"), "failed");
            let good = f.as_object().map(|m| m.keys().all(|k| !k.starts_with("ent"))).unwrap_or(true) && h.get(&leaf("q"), "entity_votes").as_array().map(|a| a.len()) == Some(20);
            cliffvm::expr::set_eval_budget(None);
            good
        };
        let (mut lo, mut hi) = (1u64, 100_000u64);
        assert!(ok(hi));
        while hi - lo > 100 { let mid = (lo + hi) / 2; if ok(mid) { hi = mid } else { lo = mid } }
        eprintln!("ER_BISECT texts {n}: the largest evaluation of the entity recall takes about {hi} steps");
        cliffvm::expr::set_eval_budget(Some(hi - 200));
        h.write(&leaf("q"), "recall_on", json!(false), "person"); h.pump();
        h.write(&leaf("q"), "recall_on", json!(true), "person"); h.pump();
        eprintln!("ER_BISECT failing first under it: {}", h.get(&leaf("q"), "failed"));
        cliffvm::expr::set_eval_budget(None);
    }
}
