//! THE MOVE-LOG VOTER (#1143, step 4 of the field on SemText; membrane/packs/movelog.c1f): the old `movelog.settle` over the move log the field writes (`field_moves`).
//! An event (a text) HOLDS, for each thing, its own move of it or the move of the previous turn in its chat, whichever is larger (a text that is not a chat's has its own
//! moves only). Each thing the QUESTION (the ask SemText) mentions weighs log(N / the events holding it), N the texts the field applied; an event scores the sum over those
//! things of that weight x the move it holds; scores are divided by the top one; ties by key; the first DEPTH (20) as rows `{key, of, text, score}`
//! (`SemText.movelog_votes`, computed while `recall_on`, NOT fused into recall yet). Each thing keeps its posting list (`ml_held_by`: the events holding it, with the move
//! held) so the voter reads only the question's things, never every text.
//! The seat runs in process with its store (chat order is the store's key order) and its drivers; the model server is a tiny one in the test (loopback), whose vectors are
//! the table `model` below. The question is a text like the others: its vectors land too, so it holds what it mentions (it is never a row of its own vote).
use membrane_vm::host::{boot_started, Host, Options};
use serde_json::{json, Map, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::time::{Duration, Instant};

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// `Box.go` makes a chat message with its body (as the chat does) and `Box.ans` its answer's body (as chat_turn does); `Leaf.go` a text that is not the chat's.
/// Every body is mentioned; the embed queue asks each one by itself (the seat's backend is a fact).
const MAKER: &str = "PACK mlmaker 1\nIMPORT semtext 1\nIMPORT mentions 1\nIMPORT chat 1\n\
Box.go on-setter make:\n  WHEN this.go != None:\n    membrane::Chat:main:message[this.k] = ChatMessage(text: this.go, at: this.at)\n    membrane::Chat:main:message[this.k]:body = SemText(text: this.go)\n    membrane::Chat:main:message[this.k]:body.mention = True\n\
Box.ans on-setter answer:\n  WHEN this.ans != None:\n    membrane::Chat:main:message[this.k]:turn:response:body = SemText(text: this.ans)\n    membrane::Chat:main:message[this.k]:turn:response:body.mention = True\n\
Leaf.go on-setter leaf:\n  WHEN this.go != None:\n    Leaf:x[this.k]:body = SemText(text: this.go)\n    Leaf:x[this.k]:body.mention = True\n";

/// the test's model: a term (the seed of a thing: no space, no full stop) is [1, 0, 0]; a sentence is [0, 1, 0]
fn model(t: &str) -> Vec<f64> { if !t.contains(' ') && !t.contains('.') && !t.contains('?') { vec![1.0, 0.0, 0.0] } else { vec![0.0, 1.0, 0.0] } }

/// a tiny model server on loopback: answers every `/api/embed` with `model` of each input
fn server() -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for s in l.incoming().flatten() {
            let mut s = s;
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut first = String::new();
            r.read_line(&mut first).unwrap_or(0);
            let mut len = 0usize;
            loop {
                let mut l = String::new();
                if r.read_line(&mut l).unwrap_or(0) == 0 || l == "\r\n" { break; }
                if let Some(v) = l.to_ascii_lowercase().strip_prefix("content-length:") { len = v.trim().parse().unwrap_or(0); }
            }
            let mut b = vec![0u8; len];
            let _ = r.read_exact(&mut b);
            let body: J = serde_json::from_slice(&b).unwrap_or(J::Null);
            let embeddings: Vec<Vec<f64>> = body["input"].as_array().map(|a| a.iter().map(|t| model(t.as_str().unwrap_or(""))).collect()).unwrap_or_default();
            let text = json!({"model": "m", "embeddings": embeddings}).to_string();
            let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}", text.len());
            let _ = s.flush();
        }
    });
    port
}

static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
fn tmpfile() -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let s = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    std::env::temp_dir().join(format!("movelog-{}-{n}-{s}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn seat_with(layer: Option<&str>) -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(10_000));
    let o = Options::new(Some(tmpfile()));
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        let l = h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
        assert!(n != "movelog" || l.msgs.is_empty(), "the movelog pack loads clean: {:?}", l.msgs);
    }
    h.load_app(&r.join("membrane/packs/launcher.c1f"), false).unwrap();
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_pack("mlmaker", MAKER).unwrap();
    if let Some(l) = layer { h.load_pack("mllayer", l).unwrap(); }
    h.rebuild();
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!(format!("http://127.0.0.1:{}", server())));
    f.insert("embed_model".into(), json!("m"));
    boot_started(&h, &f, true);
    h.pump();
    h
}
fn seat() -> Host { seat_with(None) }
const Q: &str = "membrane::Embed:queue";
const M: &str = "membrane::Chat:main:message:";
fn body(id: &str) -> String { format!("{M}{id}:body") }
fn answer(id: &str) -> String { format!("{M}{id}:turn:response:body") }
fn leaf_eid(k: &str) -> String { format!("Leaf:x:{k}:body") }

/// pump until `text` has all its sentences applied to the field and the queue is idle (the store and the model server answer on their own threads)
fn landed(h: &Host, text: &str) {
    let t = Instant::now();
    let mut quiet = 0;
    loop {
        h.pump();
        let n = h.get(text, "sentences").as_array().map(|a| a.len()).unwrap_or(0) as i64;
        let done = n > 0 && h.get(text, "field_n").as_i64() == Some(n) && h.get(Q, "state") == json!("idle") && !h.snapshot().keys().any(|k| k.starts_with("membrane::Embed:ask:"));
        quiet = if done { quiet + 1 } else { 0 };
        if quiet >= 5 { break; }
        assert!(t.elapsed() < Duration::from_secs(60), "{text} never landed: field_n {} of {n}, queue {} {}", h.get(text, "field_n"), h.get(Q, "state"), h.get(Q, "reason"));
        std::thread::sleep(Duration::from_millis(5));
    }
}
fn chat(h: &Host, id: &str, text: &str, ans: Option<&str>, at: f64) {
    h.born(&format!("Box:{id}"), "Box", props(json!({"k": id, "at": at})), "person"); h.pump();
    h.write(&format!("Box:{id}"), "go", json!(text), "person"); h.pump();
    landed(h, &body(id));
    if let Some(a) = ans { h.write(&format!("Box:{id}"), "ans", json!(a), "person"); h.pump(); landed(h, &answer(id)); }
}
fn leaf(h: &Host, k: &str, text: &str) {
    h.born(&format!("Leaf:{k}"), "Leaf", props(json!({"k": k})), "person"); h.pump();
    h.write(&format!("Leaf:{k}"), "go", json!(text), "person"); h.pump();
    landed(h, &leaf_eid(k));
}
/// the question: a text (it lands like any), asked with `recall_on` as the banner asks
fn votes(h: &Host, k: &str, text: &str) -> Vec<J> {
    leaf(h, k, text);
    ask(h, k)
}
fn ask(h: &Host, k: &str) -> Vec<J> {
    let e = leaf_eid(k);
    h.write(&e, "recall_on", json!(true), "person");
    for _ in 0..50 { h.pump(); if h.get(&e, "movelog_votes").is_array() { break; } std::thread::sleep(Duration::from_millis(5)); }
    let v = h.get(&e, "movelog_votes");
    // (`recall_on` turns the shared recall on too: only this voter's own getters are asserted)
    let failed = h.get(&e, "failed");
    let mine: Vec<&String> = failed.as_object().map(|o| o.keys().filter(|k| k.starts_with("ml_") || k.starts_with("movelog")).collect()).unwrap_or_default();
    assert!(mine.is_empty(), "no evaluation of the voter failed: {failed}");
    v.as_array().unwrap_or_else(|| panic!("no votes on {e}: {v}")).clone()
}
fn of(rows: &[J]) -> Vec<String> { rows.iter().map(|r| r["of"].as_str().unwrap().to_string()).collect() }
fn score(rows: &[J], of: &str) -> f64 { rows.iter().find(|r| r["of"] == json!(of)).unwrap_or_else(|| panic!("{of} not in {rows:?}"))["score"].as_f64().unwrap() }
fn near(a: f64, b: f64, what: &str) { assert!((a - b).abs() < 2e-3, "{what}: {a} vs {b}"); }
fn held(h: &Host, thing: &str) -> Vec<(String, f64)> {
    let v = h.get(thing, "ml_held_by");
    v.as_array().unwrap_or_else(|| panic!("no posting list on {thing}: {v}")).iter().map(|x| (x["t"].as_str().unwrap().to_string(), x["h"].as_f64().unwrap())).collect()
}
fn held_of(h: &Host, thing: &str, t: &str) -> f64 { held(h, thing).into_iter().find(|x| x.0 == t).unwrap_or_else(|| panic!("{t} does not hold {thing}: {:?}", held(h, thing))).1 }

fn unit(v: &[f64]) -> Vec<f64> { let n = v.iter().map(|x| x * x).sum::<f64>().sqrt(); v.iter().map(|x| x / n).collect() }
/// the field's walk: (state after, move)
fn step(s: &[f64], x: &[f64]) -> (Vec<f64>, f64) {
    let x = unit(x);
    let after = unit(&s.iter().zip(&x).map(|(a, b)| a + 0.5 * (b - a)).collect::<Vec<_>>());
    let m = 0.5 * s.iter().zip(&x).map(|(a, b)| (b - a) * (b - a)).sum::<f64>().sqrt();
    (after, m)
}
/// the moves of a thing whose seed is [1, 0, 0] through n sentences at [0, 1, 0]
fn walk(n: usize) -> Vec<f64> {
    let mut s = vec![1.0, 0.0, 0.0];
    (0..n).map(|_| { let (a, m) = step(&s, &[0.0, 1.0, 0.0]); s = a; m }).collect()
}

/// the old test `test_settle_finds_the_event_that_moved_the_rare_thing`, and the rarity: log(N / the events holding the thing)
#[test]
fn the_event_that_moved_the_rare_thing_wins_and_rarity_is_log_n_over_the_holders() {
    let h = seat();
    leaf(&h, "a", "The weather mentions common_id here.");
    leaf(&h, "b", "I started pottery_class on Tuesday.");
    leaf(&h, "c", "The weather was cold and common_id too.");
    leaf(&h, "d", "Again common_id shows up.");
    assert_eq!(h.get("MoveLog:all", "n"), json!(4), "N: the texts the field applied");
    let r = votes(&h, "q", "Is pottery_class like common_id?");
    assert_eq!(h.get("MoveLog:all", "n"), json!(5), "the question is a text too");
    // pottery_class: held by b and the question, 2 of 5 (log 5/2); common_id: a, c, d and the question, 4 of 5 (log 5/4), each move smaller than the one before
    let pc = walk(2);
    let ci = walk(4);
    let top = (5.0f64 / 2.0).ln() * pc[0];
    assert_eq!(of(&r), vec![leaf_eid("b"), leaf_eid("a"), leaf_eid("c"), leaf_eid("d")], "{r:?}");
    near(score(&r, &leaf_eid("b")), 1.0, "normalised by the top score");
    for (k, i) in [("a", 0), ("c", 1), ("d", 2)] { near(score(&r, &leaf_eid(k)), (5.0f64 / 4.0).ln() * ci[i] / top, k); }
    assert!(!of(&r).contains(&leaf_eid("q")), "never the question itself");
}

#[test]
fn a_row_is_key_of_text_score_and_the_key_names_the_sentence_that_moved_the_thing() {
    let h = seat();
    leaf(&h, "a", "Nothing here. But rare_id moves now.");
    leaf(&h, "b", "Something else entirely.");
    let r = votes(&h, "q", "What about rare_id?");
    assert_eq!(r.len(), 1, "{r:?}");
    let s2 = h.get(&leaf_eid("a"), "sentences")[1]["start"].as_i64().unwrap();
    assert_eq!(r[0], json!({"key": format!("{}#{s2}", leaf_eid("a")), "of": leaf_eid("a"), "text": "But rare_id moves now.", "score": 1.0}), "{r:?}");
}

#[test]
fn ties_go_by_key() {
    let h = seat();
    leaf(&h, "b", "Here is id_bee.");
    leaf(&h, "a", "Here is id_ay.");
    let r = votes(&h, "q", "Both id_bee and id_ay?");
    assert_eq!(of(&r), vec![leaf_eid("a"), leaf_eid("b")], "the same score: the key decides, not the order they came in: {r:?}");
    near(score(&r, &leaf_eid("a")), 1.0, "a");
    near(score(&r, &leaf_eid("b")), 1.0, "b");
}

#[test]
fn a_question_that_names_nothing_moved_votes_for_nothing() {
    let h = seat();
    leaf(&h, "a", "Here is rare_id.");
    assert_eq!(votes(&h, "q1", "Hi."), Vec::<J>::new(), "a word of two letters never moves");
    assert_eq!(votes(&h, "q2", "Nobody said tomato."), Vec::<J>::new(), "words nobody else moved (the question's own move is not a vote)");
    // a text with no word, identifier or path: nothing to apply to the field (it never lands), and no thing to weigh
    h.born("Leaf:q3", "Leaf", props(json!({"k": "q3"})), "person"); h.pump();
    h.write("Leaf:q3", "go", json!("!!!"), "person"); h.pump();
    assert_eq!(ask(&h, "q3"), Vec::<J>::new(), "no entity at all");
}

#[test]
fn a_thing_every_event_holds_weighs_nothing_so_nothing_wins() {
    let h = seat();
    leaf(&h, "a", "Here is every_id.");
    leaf(&h, "b", "And every_id again.");
    assert_eq!(votes(&h, "q", "What is every_id?"), Vec::<J>::new(), "log(3 / 3) = 0: the top score is 0, no vote");
}

/// the old test `test_a_reply_holds_the_turn_it_answers`, and the message after it holds the answer's
#[test]
fn a_reply_holds_the_turn_it_answers_and_the_next_message_holds_the_reply() {
    let h = seat();
    chat(&h, "m1", "Tell me about pottery_class.", Some("It meets every week."), 1.0);
    chat(&h, "m2", "Sounds good to me.", None, 2.0);
    let m = walk(1)[0];
    let ts: Vec<String> = held(&h, "Identifier:name:pottery_class").into_iter().map(|x| x.0).collect();
    assert_eq!(ts.len(), 2, "the message and its answer hold it, the next message does not (its previous turn is the answer, which moved nothing): {ts:?}");
    near(held_of(&h, "Identifier:name:pottery_class", &body("m1")), m, "own");
    near(held_of(&h, "Identifier:name:pottery_class", &answer("m1")), m, "the previous turn's");
    let r = votes(&h, "q", "pottery_class?");
    assert_eq!(of(&r), vec![body("m1"), answer("m1")], "the same move held: ties by key: {r:?}");
    near(score(&r, &answer("m1")), 1.0, "the answer holds its message's move");
    assert_eq!(r[1]["key"], json!(format!("{}#0", answer("m1"))), "held from the previous turn: the key names the first sentence: {r:?}");
    // the next message holds the answer's own move, and only what it MOVED (not what it held)
    chat(&h, "m3", "Fine, but gateway_x is down.", Some("Restart gateway_y please."), 3.0);
    chat(&h, "m4", "Done.", None, 4.0);
    let r = votes(&h, "q2", "gateway_x or gateway_y?");
    assert_eq!(of(&r), vec![answer("m3"), body("m3"), body("m4")], "m3's answer moves y and holds x (two things); m3 and m4 hold one each (ties by key); m4 holds y (its previous turn's move), not x: {r:?}");
    assert!(held(&h, "Identifier:name:gateway_x").iter().all(|x| x.0 != body("m4")), "{:?}", held(&h, "Identifier:name:gateway_x"));
}

#[test]
fn an_event_holds_the_larger_of_its_own_move_and_its_previous_turns() {
    let h = seat();
    // m1 moves pottery_class far; its answer moves it again from there: a smaller move of its own, so the answer holds m1's larger one
    chat(&h, "m1", "Tell me about pottery_class.", Some("pottery_class meets weekly."), 1.0);
    let w = walk(2);
    assert!(w[1] < w[0]);
    near(held_of(&h, "Identifier:name:pottery_class", &body("m1")), w[0], "own");
    near(held_of(&h, "Identifier:name:pottery_class", &answer("m1")), w[0], "the previous turn's, the larger");
}

#[test]
fn a_message_with_no_answer_before_it_holds_the_message_before() {
    let h = seat();
    leaf(&h, "x", "Nothing to see.");
    chat(&h, "m1", "Our pottery_class starts.", None, 1.0);
    chat(&h, "m2", "Anything else?", None, 2.0);
    let r = votes(&h, "q", "pottery_class?");
    assert_eq!(of(&r), vec![body("m1"), body("m2")], "{r:?}");
}

/// the store's answer (which message came before) can come after the text's first landing: the link is made then, by its own setter
const GATE: &str = "PACK mllayer 1\nIMPORT semtext 1\nIMPORT movelog 1\nSemText.ml_prev_msg getter WHEN prop('Box:gate', 'shut') == True:\n  None\n";

#[test]
fn a_text_that_landed_before_its_previous_turn_was_known_is_linked_when_it_is() {
    let h = seat_with(Some(GATE));
    h.born("Box:gate", "Box", props(json!({"shut": true})), "person"); h.pump();
    leaf(&h, "x", "Nothing to see.");
    chat(&h, "m1", "Our pottery_class starts.", None, 1.0);
    chat(&h, "m2", "Anything else?", None, 2.0);
    assert!(held(&h, "Identifier:name:pottery_class").iter().all(|x| x.0 != body("m2")), "not linked yet: {:?}", held(&h, "Identifier:name:pottery_class"));
    h.write("Box:gate", "shut", json!(false), "person");
    for _ in 0..40 { h.pump(); std::thread::sleep(Duration::from_millis(5)); }
    near(held_of(&h, "Identifier:name:pottery_class", &body("m2")), walk(1)[0], "linked: m2 holds m1's move");
    assert_eq!(h.get(&body("m1"), "ml_next"), json!(body("m2")));
    let r = votes(&h, "q", "pottery_class?");
    assert_eq!(of(&r), vec![body("m1"), body("m2")], "{r:?}");
}

/// a message whose previous turn's vectors land AFTER its own (the backlog lands older history after a live message): the moves reach it when they land
#[test]
fn moves_that_land_on_the_previous_turn_later_reach_the_turn_after_it() {
    let h = seat();
    chat(&h, "m1", "The gateway is up.", None, 1.0);
    chat(&h, "m2", "Fine.", None, 2.0);
    leaf(&h, "y", "A gateway again.");
    assert!(h.get("Word:lemma:gateway", "ml_held_by").is_null(), "the word waits in the nursery: no move yet");
    // the third sentence that says `gateway` materialises it: its moves are written on m1 now, and m2 (m1's follower) holds them
    leaf(&h, "z", "Third gateway now.");
    let w = walk(3);
    near(held_of(&h, "Word:lemma:gateway", &body("m1")), w[0], "m1's own, written at the materialisation");
    near(held_of(&h, "Word:lemma:gateway", &body("m2")), w[0], "m2 holds its previous turn's move");
}

#[test]
fn a_text_that_is_not_a_chats_has_its_own_moves_only() {
    let h = seat();
    leaf(&h, "a", "Here is rare_id.");
    leaf(&h, "b", "Nothing to see.");
    assert_eq!(held(&h, "Identifier:name:rare_id").len(), 1, "the next leaf holds nothing of the one before");
    let r = votes(&h, "q", "rare_id?");
    assert_eq!(of(&r), vec![leaf_eid("a")], "{r:?}");
}

#[test]
fn a_word_that_materialises_later_is_posted_on_the_texts_that_moved_it() {
    let h = seat();
    leaf(&h, "a", "The gateway is up.");
    leaf(&h, "b", "A gateway again.");
    assert_eq!(h.get("Word:lemma:gateway", "ml_held_by"), J::Null, "in the nursery: no move yet");
    leaf(&h, "c", "Third gateway now.");
    let w = walk(3);
    for (k, m) in [("a", w[0]), ("b", w[1]), ("c", w[2])] { near(held_of(&h, "Word:lemma:gateway", &leaf_eid(k)), m, k); }
    assert_eq!(held(&h, "Word:lemma:gateway").len(), 3);
}

const DEPTH1: &str = "PACK mllayer 1\nIMPORT semtext 1\nIMPORT movelog 1\nSemText.ml_depth getter: 1\n";

#[test]
fn the_depth_and_the_weight_are_getters_a_layer_overrides() {
    let h = seat();
    leaf(&h, "a", "Here is id_ay.");
    leaf(&h, "b", "Here is id_bee.");
    assert_eq!(votes(&h, "q", "id_ay and id_bee?").len(), 2);
    assert_eq!(h.get(&leaf_eid("q"), "ml_depth"), json!(20));
    assert_eq!(h.get(&leaf_eid("q"), "ml_weight"), json!(2));
    let h = seat_with(Some(DEPTH1));
    leaf(&h, "a", "Here is id_ay.");
    leaf(&h, "b", "Here is id_bee.");
    assert_eq!(votes(&h, "q", "id_ay and id_bee?").len(), 1, "a layer's depth");
}

#[test]
fn only_the_first_depth_rows_are_given() {
    let h = seat();
    for i in 0..23 { leaf(&h, &format!("n{i:02}"), &format!("Here is id_{i}x now.")); }
    let ids: Vec<String> = (0..23).map(|i| format!("id_{i}x")).collect();
    let r = votes(&h, "q", &format!("Are {} here?", ids.join(" ")));
    assert_eq!(r.len(), 20, "DEPTH: {r:?}");
    assert_eq!(of(&r), (0..20).map(|i| leaf_eid(&format!("n{i:02}"))).collect::<Vec<_>>(), "equal scores: by key");
}

/// the text of the i-th of many: three of five common things, two common words and one thing of its own
fn many(i: usize) -> String {
    let c = ["c_alpha", "c_beta", "c_gamma", "c_delta", "c_eps"];
    format!("Item {i} uses {} {} {} with u_{i}x.", c[i % 5], c[(i + 1) % 5], c[(i + 2) % 5])
}
const MANY_Q: &str = "Do item uses c_alpha c_beta c_gamma c_delta c_eps and u_7x work?";

/// every evaluation of the voter stays inside the step budget on a field of 150 texts, each holding three of the question's five common things and its two common words
/// (each posting list 90 to 150 long): the vote reads every holder of every thing the question names
#[test]
fn a_question_of_common_things_stays_inside_the_step_budget() {
    let h = seat();
    for i in 0..150 { leaf(&h, &format!("n{i:03}"), &many(i)); }
    let r = votes(&h, "q", MANY_Q);
    assert_eq!(of(&r)[0], leaf_eid("n007"), "the text of the rare thing first: {r:?}");
    assert_eq!(r.len(), 20);
}

/// the posting setter's per-thing write, as a getter the measurement can evaluate alone
const PROBE: &str = "PACK mllayer 1\nIMPORT movelog 1\nProbe.post getter:\n  old = prop(this.e, 'ml_held_by') or []\n  by = [{'key': this.t, 'rows': [{'t': this.t, 'e': this.e, 'm': 0.5}]}]\n  concat([x for x in old if len([b for b in by if b['key'] == x['t']]) == 0], [{'t': b['key'], 'h': max(concat([x['h'] for x in old if x['t'] == b['key']], pluck(b['rows'], 'm')))} for b in by])\n";

/// the measurement (run by hand: `cargo test --test movelog_1143 measure -- --ignored --nocapture`, MOVELOG_N texts): the smallest step budget that the vote and the
/// posting setter of one more landing each need, found by bisecting the thread's budget
#[test]
#[ignore]
fn measure_the_step_budget() {
    let n: usize = std::env::var("MOVELOG_N").ok().and_then(|v| v.parse().ok()).unwrap_or(300);
    let h = seat_with(Some(PROBE));
    for i in 0..n { leaf(&h, &format!("n{i:04}"), &many(i)); }
    leaf(&h, "q", MANY_Q);
    let e = leaf_eid("q");
    let vote_ok = |b: u64| -> bool {
        membrane_vm::expr::set_eval_budget(Some(b));
        h.write(&e, "recall_on", json!(false), "person"); h.pump();
        h.write(&e, "recall_on", json!(true), "person"); h.pump();
        let failed = h.get(&e, "failed");
        let mine = failed.as_object().map(|o| o.keys().any(|k| k.starts_with("ml_") || k.starts_with("movelog"))).unwrap_or(false);
        let ok = h.get(&e, "movelog_votes").is_array() && !mine;
        membrane_vm::expr::set_eval_budget(None);
        ok
    };
    let (mut lo, mut hi) = (1u64, 10_000_000u64);
    while lo + 1 < hi { let mid = (lo + hi) / 2; if vote_ok(mid) { hi = mid } else { lo = mid } }
    let vote = hi;
    // the largest expression of the posting setter is the write of one thing's list (its old list read twice): the probe evaluates that same expression for c_alpha
    let mut k = 0;
    let mut post_ok = |b: u64| -> bool {
        k += 1;
        let p = format!("Probe:p{k}");
        membrane_vm::expr::set_eval_budget(Some(b));
        h.born(&p, "Probe", props(json!({"e": "Identifier:name:c_alpha", "t": "Leaf:x:new:body"})), "person"); h.pump();
        let ok = h.get(&p, "post").is_array() && h.get(&p, "failed").is_null();
        membrane_vm::expr::set_eval_budget(None);
        ok
    };
    let (mut lo, mut hi) = (1u64, 2_000_000u64);
    while lo + 1 < hi { let mid = (lo + hi) / 2; if post_ok(mid) { hi = mid } else { lo = mid } }
    let held_len = held(&h, "Identifier:name:c_alpha").len();
    println!("MEASURE texts={n} vote_steps={vote} post_steps<={hi} (c_alpha holders {held_len})");
}
