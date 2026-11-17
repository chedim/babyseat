//! THE FIELD on SemText (#1143, step 3; membrane/packs/field.c1f): every Word / Identifier / Path entity that has a state moves halfway toward each sentence vector that
//! mentions it as the vector lands; the move is logged on the text; identifiers and paths live at their first mention, words wait in a nursery until 3 sentences, words of
//! 1-3 letters never live; seeds are the term's own embedding (through the embed queue, kept out of `Vector`); one background is the mean of every sentence vector;
//! mature identifiers keep episodes. The states are stored rows, restored at a restart, never journalled.
//! There is NEVER a real model server here: the receipts are written by the test (the Fetch driver's own writes).
mod common;

use membrane_vm::host::{boot_started, Host, Line, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// scratch pack: `Box.say` makes a chat body with the text, mentions it and asks for its vectors (what the chat does); `Probe` reads numbers back
const MAKER: &str = "PACK fieldmaker 1\nIMPORT semtext 1\nIMPORT mentions 1\nIMPORT chat 1\nIMPORT vector 1\n\
Probe.back getter: [prop(e, 'v') for e in this.eids]\n\
Probe.near getter: near(this.q, 'Vector', 50)\n\
Box.say on-setter say:\n  WHEN this.say != None:\n    membrane::Chat:main:message[prop(this.say, 'id')]:body = SemText(text: prop(this.say, 'text'))\n    membrane::Chat:main:message[prop(this.say, 'id')]:body.mention = True\n\
Box.ask on-setter ask:\n  WHEN this.ask != None:\n    membrane::Chat:main:message[this.ask]:body.embed = True\n";

fn load(h: &Host, layer: Option<&str>) {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let names: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(String::from).collect();
    for (i, n) in names.iter().enumerate() {
        let l = h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
        assert!(n != "field" || l.msgs.is_empty(), "the field pack loads: {:?}", l.msgs);
    }
    h.load_app(&r.join("membrane/packs/launcher.c1f"), false).unwrap();
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_pack("fieldmaker", MAKER).unwrap();
    if let Some(l) = layer { h.load_pack("fieldlayer", l).unwrap(); }
    h.rebuild();
}
fn open(file: Option<&str>) -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(10_000));
    let mut o = Options::new(file.map(String::from));
    o.effects = false;
    Host::open(o).unwrap()
}
fn start(h: &Host) {
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!("http://127.0.0.1:9"));
    boot_started(h, &f, true);
    h.pump();
}
fn seat_at(file: Option<&str>, layer: Option<&str>) -> Host { let h = open(file); load(&h, layer); start(&h); h.born("Box:a", "Box", props(json!({})), "person"); h.pump(); h }
fn seat() -> Host { seat_at(None, None) }
const Q: &str = "membrane::Embed:queue";
fn body(id: &str) -> String { format!("membrane::Chat:main:message:{id}:body") }

/// the texts of the request now out (none: empty)
fn inputs(h: &Host) -> Vec<String> {
    let j = h.get(Q, "inflight");
    if j.is_null() || h.get(Q, "flying") != json!(true) { return vec![]; }
    j["unc"].as_array().unwrap().iter().map(|x| x["text"].as_str().unwrap().to_string()).collect()
}
/// answer every request until the queue is idle; `f` is the model: a text's vector
fn drain(h: &Host, f: &dyn Fn(&str) -> Vec<f64>) -> Vec<Vec<String>> {
    let mut seen = vec![];
    for _ in 0..50 {
        let ins = inputs(h);
        if ins.is_empty() { break; }
        let id = h.get(Q, "inflight")["id"].clone();
        let embeddings: Vec<Vec<f64>> = ins.iter().map(|t| f(t)).collect();
        h.write("Fetch:embed", "request_result", json!({"id": id, "status": "ok", "rc": 0, "head": "HTTP/1.1 200 OK", "http_status": 200, "headers": {}, "body": json!({"model": "m", "embeddings": embeddings}).to_string()}), "driver:request");
        h.pump();
        seen.push(ins);
    }
    seen
}
fn say(h: &Host, id: &str, text: &str) {
    h.write("Box:a", "say", json!({"id": id, "text": text}), "person");
    h.pump();
    h.write("Box:a", "ask", json!(id), "person");
    h.pump();
}
fn back(h: &Host, eids: &[&str]) -> Vec<J> {
    let n = h.snapshot().keys().filter(|k| k.starts_with("Probe:")).count();
    let p = format!("Probe:p{n}");
    h.born(&p, "Probe", props(json!({"eids": eids})), "person");
    h.pump();
    h.get(&p, "back").as_array().unwrap().clone()
}
fn nums(v: &J) -> Vec<f64> { v.as_array().unwrap_or_else(|| panic!("not a list: {v}")).iter().map(|x| x.as_f64().unwrap()).collect() }
fn unit(v: &[f64]) -> Vec<f64> { let n = v.iter().map(|x| x * x).sum::<f64>().sqrt(); v.iter().map(|x| x / n).collect() }
/// the old field's update: unit(s + 0.5 (x - s)), and its move |0.5 (x - s)|
fn step(s: &[f64], x: &[f64]) -> (Vec<f64>, f64) {
    let x = unit(x);
    let after = unit(&s.iter().zip(&x).map(|(a, b)| a + 0.5 * (b - a)).collect::<Vec<_>>());
    let m = 0.5 * s.iter().zip(&x).map(|(a, b)| (b - a) * (b - a)).sum::<f64>().sqrt();
    (after, m)
}
fn close(a: &[f64], b: &[f64], what: &str) {
    assert_eq!(a.len(), b.len(), "{what}: {a:?} vs {b:?}");
    for (x, y) in a.iter().zip(b) { assert!((x - y).abs() < 2e-3, "{what}: {a:?} vs {b:?}"); }
}
fn moves(h: &Host, text: &str) -> Vec<J> { h.get(text, "field_moves").as_array().cloned().unwrap_or_default() }
fn move_of(h: &Host, text: &str, e: &str, s: i64) -> f64 {
    let m = moves(h, text);
    m.iter().find(|r| r["e"] == json!(e) && r["s"] == json!(s)).unwrap_or_else(|| panic!("no move of {e} in sentence {s} of {text}: {m:?}"))["m"].as_f64().unwrap()
}

const FN: &str = "Identifier:name:my_fn";
/// the test's model: a table, else a vector from the text's length
fn model(t: &str) -> Vec<f64> {
    match t {
        "my_fn" => vec![1.0, 0.0, 0.0],
        "gateway" => vec![0.0, 0.0, 1.0],
        "Use my_fn here." => vec![0.0, 1.0, 0.0],
        "The gateway is up." => vec![1.0, 0.0, 0.0],
        "A gateway again." => vec![0.0, 1.0, 0.0],
        "Third gateway now." => vec![1.0, 1.0, 0.0],
        "First my_fn." => vec![0.0, 1.0, 0.0],
        "Then my_fn again." => vec![0.0, 0.0, 1.0],
        _ => vec![t.len() as f64, 1.0, 2.0],
    }
}

#[test]
fn an_identifier_gets_a_state_at_its_first_mention_its_seed_is_asked_and_the_move_is_logged_on_the_text() {
    let h = seat();
    say(&h, "m1", "Use my_fn here.");
    let seen = drain(&h, &model);
    assert_eq!(seen, vec![vec!["Use my_fn here.".to_string()], vec!["my_fn".to_string()]], "the sentence, then the seed of the identifier (one call)");
    let seed = unit(&model("my_fn"));
    let (want, m) = step(&seed, &model("Use my_fn here."));
    close(&nums(&back(&h, &[&format!("FieldState:{FN}")])[0]), &want, "the state: the seed moved halfway to the sentence");
    assert_eq!(h.get(&format!("FieldState:{FN}"), "n"), json!(1));
    assert!((move_of(&h, &body("m1"), FN, 0) - m).abs() < 1e-3, "the move |0.5 (x - seed)| = {m}");
    assert_eq!(h.get(&body("m1"), "field_n"), json!(1), "the text says how many of its sentences moved the field");
}

#[test]
fn nothing_moves_before_the_sentence_vector_lands() {
    let h = seat();
    say(&h, "m1", "Use my_fn here.");
    assert_eq!(h.get(&format!("FieldState:{FN}"), "of"), J::Null, "no state before the vector");
    assert!(moves(&h, &body("m1")).is_empty());
}

#[test]
fn a_seed_lives_in_its_own_class_and_the_cosine_lane_over_vector_never_returns_it() {
    let h = seat();
    say(&h, "m1", "Use my_fn here.");
    drain(&h, &model);
    close(&nums(&back(&h, &[&format!("FieldSeed:{FN}")])[0]), &[1.0, 0.0, 0.0], "the seed is the term's embedding");
    h.born("Probe:n", "Probe", props(json!({"q": [1.0, 0.0, 0.0]})), "person");
    h.pump();
    let near = h.get("Probe:n", "near");
    let eids: Vec<&str> = near.as_array().unwrap_or_else(|| panic!("{near}")).iter().map(|r| r["eid"].as_str().unwrap()).collect();
    assert!(!eids.is_empty() && eids.iter().all(|e| e.starts_with("Vector:of:membrane/Chat:")), "only sentence vectors: {eids:?}");
}

#[test]
fn a_word_waits_in_the_nursery_until_three_sentences_then_its_seed_walks_through_them_and_each_move_is_logged_on_its_own_text() {
    let h = seat();
    let w = "Word:lemma:gateway";
    say(&h, "m1", "The gateway is up.");
    drain(&h, &model);
    say(&h, "m2", "A gateway again.");
    let seen = drain(&h, &model);
    assert_eq!(seen, vec![vec!["A gateway again.".to_string()]], "no seed is asked while the word waits");
    assert_eq!(h.get(w, "field_pend").as_array().map(|a| a.len()), Some(2), "two sentences wait: {}", h.get(w, "field_pend"));
    assert_eq!(h.get(&format!("FieldState:{w}"), "of"), J::Null, "no state yet");
    assert!(moves(&h, &body("m1")).iter().all(|r| r["e"] != json!(w)), "no move logged yet");
    say(&h, "m3", "Third gateway now.");
    let seen = drain(&h, &model);
    assert_eq!(seen, vec![vec!["Third gateway now.".to_string()], vec!["gateway".to_string()]], "the third sentence materialises it: its seed is asked once");
    let s0 = unit(&model("gateway"));
    let (s1, m1) = step(&s0, &model("The gateway is up."));
    let (s2, m2) = step(&s1, &model("A gateway again."));
    let (s3, m3) = step(&s2, &model("Third gateway now."));
    close(&nums(&back(&h, &[&format!("FieldState:{w}")])[0]), &s3, "the seed walked through the three sentences in order");
    assert_eq!(h.get(&format!("FieldState:{w}"), "n"), json!(3));
    for (id, m) in [("m1", m1), ("m2", m2), ("m3", m3)] { assert!((move_of(&h, &body(id), w, 0) - m).abs() < 1e-3, "{id}: {m}"); }
    assert_eq!(h.get(w, "field_pend"), json!([]), "the nursery is empty");
}

#[test]
fn a_waiting_moves_written_later_joins_the_moves_the_text_already_has() {
    let h = seat();
    say(&h, "m1", "The gateway runs my_fn.");
    drain(&h, &model);
    assert_eq!(moves(&h, &body("m1")).len(), 1, "the identifier's move, at once: {:?}", moves(&h, &body("m1")));
    say(&h, "m2", "A gateway again.");
    drain(&h, &model);
    say(&h, "m3", "Third gateway now.");
    drain(&h, &model);
    let m = moves(&h, &body("m1"));
    assert!(m.iter().any(|r| r["e"] == json!(FN)) && m.iter().any(|r| r["e"] == json!("Word:lemma:gateway")), "both moves are on the first text: {m:?}");
}

#[test]
fn texts_that_land_while_the_seed_is_out_walk_from_the_seed_once_in_text_order() {
    // the two texts land before the seed of al_x: their mentions must be applied once each, in text order, exactly as if each had been drained alone
    let x = "Identifier:name:al_x";
    let v = |t: &str| -> Vec<f64> { match t { "al_x" => vec![1.0, 0.0, 0.0], "Set al_x up." => vec![0.0, 1.0, 0.0], "Run al_x now." => vec![0.0, 0.0, 1.0], "Go al_x on." => vec![1.0, 1.0, 0.0], _ => vec![0.3, 0.3, 1.0] } };
    let h = seat();
    say(&h, "m1", "Set al_x up.");
    say(&h, "m2", "It is so. Run al_x now.");
    drain(&h, &v);
    say(&h, "m3", "Go al_x on.");
    drain(&h, &v);
    let (s1, m1) = step(&unit(&v("al_x")), &v("Set al_x up."));
    let (s2, m2) = step(&s1, &v("Run al_x now."));
    let (s3, m3) = step(&s2, &v("Go al_x on."));
    assert_eq!(h.get(&format!("FieldState:{x}"), "n"), json!(3), "each mention once");
    close(&nums(&back(&h, &[&format!("FieldState:{x}")])[0]), &s3, "seed -> m1 -> m2 -> m3");
    assert!((move_of(&h, &body("m1"), x, 0) - m1).abs() < 2e-3, "m1: {m1}");
    assert!((move_of(&h, &body("m2"), x, 1) - m2).abs() < 2e-3, "m2 moves from where m1 left it: {m2}, {:?}", moves(&h, &body("m2")));
    assert!((move_of(&h, &body("m3"), x, 0) - m3).abs() < 2e-3, "m3: {m3}");
    for id in ["m1", "m2"] { assert_eq!(moves(&h, &body(id)).iter().filter(|r| r["e"] == json!(x)).count(), 1, "{id}: one move, not two: {:?}", moves(&h, &body(id))); }
}

#[test]
fn a_word_of_one_to_three_letters_never_gets_a_state_or_a_move() {
    let h = seat();
    for (i, t) in ["The cat sat.", "A cat ran.", "One cat hid."].iter().enumerate() { say(&h, &format!("m{i}"), t); drain(&h, &model); }
    let w = "Word:lemma:cat";
    assert_eq!(h.get(w, "texts"), json!(3), "the word is an entity (mentions.c1f)");
    assert_eq!(h.get(w, "field_pend"), J::Null, "not even in the nursery");
    assert_eq!(h.get(&format!("FieldState:{w}"), "of"), J::Null);
    for i in 0..3 { assert!(moves(&h, &body(&format!("m{i}"))).iter().all(|r| r["e"] != json!(w))); }
}

#[test]
fn two_mentions_in_one_text_move_the_state_twice_in_sentence_order() {
    let h = seat();
    say(&h, "m1", "First my_fn. Then my_fn again.");
    drain(&h, &model);
    let (s1, m1) = step(&unit(&model("my_fn")), &model("First my_fn."));
    let (s2, m2) = step(&s1, &model("Then my_fn again."));
    close(&nums(&back(&h, &[&format!("FieldState:{FN}")])[0]), &s2, "seed -> first -> second");
    assert_eq!(h.get(&format!("FieldState:{FN}"), "n"), json!(2));
    assert!((move_of(&h, &body("m1"), FN, 0) - m1).abs() < 1e-3);
    assert!((move_of(&h, &body("m1"), FN, 1) - m2).abs() < 1e-3);
}

#[test]
fn a_long_text_walks_every_mention_in_order_across_two_landings() {
    // 66 sentences: the queue sends 64, then 2 (two landings); every one mentions the identifier. The sentences are bare and all different (`my_fn!`, `my_fn?!`, ...):
    // a text of 70 identifiers and 140 words runs past the step budget in mentions.c1f `mention-spans` (a limit of that pack, not of the field), and a remainder made only of
    // sentences already sent in the same request stalls the embed queue (its cache-hit send writes `emb_n` twice in one round): both are reported, not the field's
    let h = seat();
    let sents: Vec<String> = (2..68).map(|i: u32| format!("my_fn{}", (0..(32 - i.leading_zeros() - 1)).map(|b| if i >> b & 1 == 1 { '!' } else { '?' }).collect::<String>())).collect();
    say(&h, "m1", &sents.join(" "));
    let v = |t: &str| -> Vec<f64> { if t == "my_fn" { vec![1.0, 0.0, 0.0] } else { let a = t.matches('!').count() as f64; let b = t.matches('?').count() as f64; vec![0.1, a, b + 0.5] } };
    drain(&h, &v);
    let mut s = unit(&v("my_fn"));
    let mut ms = vec![];
    for t in &sents { let (a, m) = step(&s, &v(t)); s = a; ms.push(m); }
    close(&nums(&back(&h, &[&format!("FieldState:{FN}")])[0]), &s, "sixty-six steps in order");
    assert_eq!(h.get(&format!("FieldState:{FN}"), "n"), json!(66));
    assert_eq!(h.get(&body("m1"), "field_n"), json!(66));
    for (i, m) in ms.iter().enumerate() { assert!((move_of(&h, &body("m1"), FN, i as i64) - m).abs() < 2e-3, "sentence {i}"); }
}

#[test]
fn the_background_is_the_running_mean_of_every_sentence_vector() {
    let h = seat();
    say(&h, "m1", "First my_fn. Then my_fn again.");
    drain(&h, &model);
    say(&h, "m2", "Use my_fn here.");
    drain(&h, &model);
    let xs = [unit(&model("First my_fn.")), unit(&model("Then my_fn again.")), unit(&model("Use my_fn here."))];
    let mean: Vec<f64> = (0..3).map(|i| xs.iter().map(|x| x[i]).sum::<f64>() / 3.0).collect();
    close(&nums(&back(&h, &["FieldBg:all"])[0]), &mean, "the mean of the three sentences (the seed's term is not a sentence of a text)");
    assert_eq!(h.get("FieldBg:all", "n"), json!(3));
}

fn tmpfile(tag: &str) -> String {
    let n = common::unique();
    std::env::temp_dir().join(format!("field-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}

#[test]
fn the_field_is_stored_not_journalled_and_a_restart_goes_on_from_the_stored_states() {
    let file = tmpfile("restart");
    let (state, mv);
    {
        let h = seat_at(Some(&file), None);
        say(&h, "m1", "Use my_fn here.");
        drain(&h, &model);
        state = nums(&back(&h, &[&format!("FieldState:{FN}")])[0]);
        mv = move_of(&h, &body("m1"), FN, 0);
        let lines = h.journal_stored().unwrap();
        let field: Vec<&Line> = lines.iter().filter(|l| match l {
            Line::Write { eid, prop, .. } => ["FieldState:", "FieldSeed:", "FieldEpisode:", "FieldBg:", "FieldTerm:"].iter().any(|p| eid.starts_with(p)) || prop.starts_with("field_"),
            Line::Born { eid, .. } => ["FieldState:", "FieldSeed:", "FieldEpisode:", "FieldBg:", "FieldTerm:"].iter().any(|p| eid.starts_with(p)),
        }).collect();
        assert!(field.is_empty(), "the field is never journalled: {field:?}");
    }
    let h = open(Some(&file));
    load(&h, None);
    h.load_stored_entities();
    h.rebuild();
    start(&h);
    h.born("Box:b", "Box", props(json!({})), "person");
    h.pump();
    close(&nums(&back(&h, &[&format!("FieldState:{FN}")])[0]), &state, "the state is the stored row");
    assert!((move_of(&h, &body("m1"), FN, 0) - mv).abs() < 1e-9, "the move log is the stored row");
    // a new mention goes on from the stored state, not from the seed
    h.write("Box:b", "say", json!({"id": "m2", "text": "First my_fn."}), "person");
    h.pump();
    h.write("Box:b", "ask", json!("m2"), "person");
    h.pump();
    drain(&h, &model);
    let (want, _) = step(&state, &model("First my_fn."));
    close(&nums(&back(&h, &[&format!("FieldState:{FN}")])[0]), &want, "moved from the restored state");
    assert_eq!(h.get(&format!("FieldState:{FN}"), "n"), json!(2));
}

/// the episode constants, made small by a model's layer (they are overridable getters)
const SMALL: &str = "PACK fieldlayer 1\nIMPORT field 1\nSemText.field_mature getter: 1\nSemText.field_ep_run getter: 2\nSemText.field_ep_max getter: 2\n";

#[test]
fn a_mature_identifier_keeps_episodes_of_runs_and_the_two_most_alike_neighbours_merge_past_the_cap() {
    let h = seat_at(None, Some(SMALL));
    let text = "A my_fn one. B my_fn two. C my_fn three. D my_fn four. E my_fn five. F my_fn six. G my_fn seven.";
    let v = |t: &str| -> Vec<f64> {
        match t { "A my_fn one." | "B my_fn two." => vec![1.0, 0.0, 0.0], "C my_fn three." | "D my_fn four." => vec![0.0, 1.0, 0.0],
                  "E my_fn five." | "F my_fn six." => vec![0.0, 1.0, 0.1], "G my_fn seven." => vec![0.0, 0.0, 1.0], _ => model(t) }
    };
    say(&h, "m1", text);
    drain(&h, &v);
    let st = format!("FieldState:{FN}");
    assert_eq!(h.get(&st, "n"), json!(7));
    // runs of 2 from the 1st mention: (A B) (C D) (E F) close, G stays open; three episodes past the cap of 2: (C D) and (E F) are the most alike, they merge
    let eps = h.get(&st, "eps");
    let list = eps.as_array().unwrap_or_else(|| panic!("eps: {eps}"));
    assert_eq!(list.len(), 2, "{eps}");
    assert_eq!(list[0]["n"], json!(2));
    assert_eq!(list[1]["n"], json!(4), "the merged episode holds four mentions: {eps}");
    let e0 = list[0]["e"].as_str().unwrap().to_string();
    let e1 = list[1]["e"].as_str().unwrap().to_string();
    let got = back(&h, &[&e0, &e1]);
    close(&nums(&got[0]), &[1.0, 0.0, 0.0], "the first run's centroid");
    let cd = [0.0, 1.0, 0.0];
    let ef = unit(&[0.0, 1.0, 0.1]);
    close(&nums(&got[1]), &(0..3).map(|i| (2.0 * cd[i] + 2.0 * ef[i]) / 4.0).collect::<Vec<_>>(), "the weighted mean of the merged pair");
    assert_eq!(h.get(&st, "run_k"), json!(1), "G is the open run");
    assert_eq!(h.snapshot().keys().filter(|k| k.starts_with("FieldEpisode:")).count(), 2, "the merged-away episode is not kept");
}

#[test]
fn an_identifier_younger_than_mature_keeps_no_episode() {
    let h = seat();
    say(&h, "m1", "First my_fn. Then my_fn again.");
    drain(&h, &model);
    assert_eq!(h.get(&format!("FieldState:{FN}"), "eps"), J::Null);
    assert_eq!(h.get(&format!("FieldState:{FN}"), "run_k"), json!(0));
    assert!(!h.snapshot().keys().any(|k| k.starts_with("FieldEpisode:")));
}
