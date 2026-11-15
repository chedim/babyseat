//! ONE recall on SemText (#1143, operator 2026-11-15): `SemText.recall` is what the banner part (`Banner:recall`) and a `Memory` note both read. The ask is the SemText
//! itself; the recall finds the rest in the graph: for a chat text, a WINDOW of the chat's last texts counting back from the ask (its message bodies and answer bodies in
//! chat order), each step back counting half as much as the step before it; for any other text (a Memory note) the window is the text alone. The edge lane weighs the
//! shared Word/Identifier/Path entities by that scale; the ask itself is never a hit (recent chat texts are: the model gets no chat history); near duplicates merge; no score
//! cut-off. Every test runs on the SHIPPED packs of the barest seat, in process, with no model server (receipts are written by the test).
use membrane_vm::host::{self, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// `Box.go` makes a chat message as the chat does (message, body, mention, turn); `Box.ans` makes its answer's body as chat_turn does; `Leaf.go` makes a text that is not the chat's
const MAKER: &str = "PACK srmaker 1\nIMPORT semtext 1\nIMPORT chat 1\n\
Box.go on-setter make:\n  ALWAYS:\n    membrane::Chat:main:message[this.k] = ChatMessage(text: this.t, at: this.at)\n    membrane::Chat:main:message[this.k]:body = SemText(text: this.t)\n    membrane::Chat:main:message[this.k]:body.mention = True\n    membrane::Chat:main:message[this.k]:turn = ChatTurn(message: 'membrane::Chat:main:message:' + this.k, queued_at: this.at)\n\
Box.ans on-setter answer:\n  WHEN this.ans != None:\n    membrane::Chat:main:message[this.k]:turn:response:body = SemText(text: this.ans)\n    membrane::Chat:main:message[this.k]:turn:response:body.mention = True\n\
Leaf.go on-setter leaf:\n  WHEN this.go != None:\n    Leaf:x[this.k]:body = SemText(text: this.go)\n    Leaf:x[this.k]:body.mention = True\n";

/// a seat with a store (the window is read from the store's key order) and its drivers on; no model server
fn seat() -> Host { seat_opts(&props(json!({"chat_claude_bin": "/usr/bin/true", "chat_auth": "login"})), true) }
/// a seat with no store and no drivers: the receipts are the test's
fn seat_with(facts: &Map<String, J>) -> Host { seat_opts(facts, false) }
fn seat_opts(facts: &Map<String, J>, store: bool) -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(10_000));
    let file = store.then(|| {
        let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        std::env::temp_dir().join(format!("semrecall-{}-{n}.sqlite", std::process::id())).to_str().unwrap().to_string()
    });
    let mut o = Options::new(file);
    o.effects = store;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    let l = h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    assert!(l.msgs.is_empty(), "the app loads clean: {:?}", l.msgs);
    h.load_pack("srmaker", MAKER).unwrap();
    h.rebuild();
    host::boot_started(&h, facts, true);
    h.pump();
    h
}
const M: &str = "membrane::Chat:main:message:";
fn body(id: &str) -> String { format!("{M}{id}:body") }
/// a message (with no answer yet) and, when `answer` is some, its answer
fn turn(h: &Host, id: &str, text: &str, answer: Option<&str>, at: f64) {
    h.born(&format!("Box:{id}"), "Box", props(json!({"k": id, "t": text, "at": at})), "person"); h.pump();
    h.write(&format!("Box:{id}"), "go", json!(true), "person"); h.pump();
    if let Some(a) = answer { h.write(&format!("Box:{id}"), "ans", json!(a), "person"); h.pump(); }
}
/// a text that is not the chat's (`Leaf:x:<k>:body`)
fn leaf(h: &Host, k: &str, text: &str) {
    h.born(&format!("Leaf:{k}"), "Leaf", props(json!({"k": k})), "person"); h.pump();
    h.write(&format!("Leaf:{k}"), "go", json!(text), "person"); h.pump();
}
/// the recall of a text, asked the way the banner part asks it (`recall_on`), after the store has answered
fn recall(h: &Host, eid: &str) -> Vec<J> {
    h.write(eid, "recall_on", json!(true), "person");
    for _ in 0..50 { h.pump(); if h.get(eid, "recall").is_array() { break; } std::thread::sleep(std::time::Duration::from_millis(20)); }
    h.get(eid, "recall").as_array().unwrap_or_else(|| panic!("no recall on {eid}: {}", h.get(eid, "recall"))).clone()
}
fn texts(rows: &[J]) -> Vec<String> { rows.iter().map(|r| r["text"].as_str().unwrap_or("").to_string()).collect() }
fn pos(rows: &[J], needle: &str) -> Option<usize> { texts(rows).iter().position(|t| t.contains(needle)) }

#[test]
fn the_recall_of_a_chat_ask_reads_the_window_of_the_chat_texts_before_it() {
    let h = seat();
    leaf(&h, "x", "Wombats dig burrows.");
    turn(&h, "m1", "Hello there.", Some("Hi."), 1.0);
    turn(&h, "m2", "Nice day.", Some("Indeed it is."), 2.0);
    turn(&h, "m3", "Tell me about wombats.", Some("They are marsupials."), 3.0);
    turn(&h, "m4", "And their homes?", None, 4.0);
    let r = recall(&h, &body("m4"));
    assert!(pos(&r, "Wombats dig burrows.").is_some(), "the ask shares no word with the leaf, the message before it does: {r:?}");
    assert!(pos(&r, "And their homes?").is_none(), "never the ask itself: {r:?}");
}

#[test]
fn a_text_further_back_than_the_window_does_not_feed_the_recall() {
    let h = seat();
    leaf(&h, "x", "Wombats dig burrows.");
    // the window of m5 is: m5, m4's answer, m4, m3's answer, m3, m2's answer: m2's own body is the seventh text back
    turn(&h, "m2", "Tell me about wombats.", Some("Later."), 2.0);
    turn(&h, "m3", "Nice day.", Some("Indeed."), 3.0);
    turn(&h, "m4", "Rain soon?", Some("Maybe."), 4.0);
    turn(&h, "m5", "And now?", None, 5.0);
    let r = recall(&h, &body("m5"));
    assert!(pos(&r, "Wombats dig burrows.").is_none() && pos(&r, "Tell me about wombats.").is_none(), "nothing shares a word with the window: {r:?}");
    // one step nearer: m2's answer is in the window of m4's... (control: the same leaf IS found from a window that holds the word)
    let r = recall(&h, &body("m3"));
    assert!(pos(&r, "Wombats dig burrows.").is_some(), "{r:?}");
}

#[test]
fn each_step_back_counts_half_as_much_as_the_step_before_it() {
    let h = seat();
    leaf(&h, "k", "Koalas sleep a lot.");
    leaf(&h, "e", "Emus run fast.");
    // m1's body (two steps back: a quarter) says emus, its answer (one step back: a half) says koalas; both words are said by two texts
    turn(&h, "m1", "Emus are birds.", Some("Koalas are not."), 1.0);
    turn(&h, "m2", "Which is lazier?", None, 2.0);
    let r = recall(&h, &body("m2"));
    let (k, e) = (pos(&r, "Koalas sleep a lot.").unwrap_or_else(|| panic!("{r:?}")), pos(&r, "Emus run fast.").unwrap_or_else(|| panic!("{r:?}")));
    assert!(k < e, "the half outranks the quarter: {r:?}");
    assert_eq!(h.get(&body("m2"), "r_ratio"), json!(0.5), "the ratio is a getter a layer can override");
    let w = h.get(&body("m2"), "r_win");
    assert_eq!(w, json!([
        {"t": body("m2"), "w": 1}, {"t": format!("{M}m1:turn:response:body"), "w": 0.5}, {"t": body("m1"), "w": 0.25}]), "the window, newest first");
}

#[test]
fn recent_chat_texts_are_hits_only_the_ask_is_left_out() {
    let h = seat();
    turn(&h, "m1", "Quokkas live on islands.", Some("Yes, on Rottnest."), 1.0);
    turn(&h, "m2", "Are quokkas cute?", None, 2.0);
    let r = recall(&h, &body("m2"));
    assert!(pos(&r, "Quokkas live on islands.").is_some(), "the message just before is a hit: the model gets no chat history: {r:?}");
    assert!(pos(&r, "Are quokkas cute?").is_none(), "{r:?}");
}

#[test]
fn near_duplicates_merge_and_every_row_has_its_score_with_no_cut_off() {
    let h = seat();
    leaf(&h, "a", "Quokkas eat grass at dawn.");
    leaf(&h, "b", "Quokkas eat grass at dawn daily.");
    leaf(&h, "c", "Quokkas sleep.");
    for (i, t) in ["One quokka rests.", "Two quokkas jump far away.", "Three quokkas swim.", "A quokka hides under rocks.", "Every quokka smiles."].iter().enumerate() { leaf(&h, &format!("n{i}"), t); }
    leaf(&h, "q", "quokka");
    let r = recall(&h, "Leaf:x:q:body");
    let dup = texts(&r).iter().filter(|t| t.starts_with("Quokkas eat grass at dawn")).count();
    assert_eq!(dup, 1, "five of the shorter's five words are in the longer: one hit: {r:?}");
    assert!(pos(&r, "Quokkas sleep.").is_some(), "{r:?}");
    assert!(r.iter().all(|x| x["score"].is_number()), "{r:?}");
    assert_eq!(r.len(), 7, "every hit, however weak, is there (no score cut-off): {r:?}");
}

#[test]
fn a_memory_note_reads_the_same_recall_with_its_own_vector_and_the_dials() {
    let h = seat_with(&props(json!({"chat_claude_bin": "/usr/bin/true", "chat_auth": "login", "seat_backend": "http://127.0.0.1:9", "embed_model": "m"})));
    turn(&h, "m1", "The marsupial slept all afternoon.", None, 1.0);
    // the sentence vector of the chat message (as the embedding pipeline names it)
    let k = body("m1").replace("::", "/");
    let ve = format!("Vector:of:{k}:s:0");
    h.born(&ve, "Vector", props(json!({"of": body("m1"), "start": 0})), "person");
    h.write(&ve, "v", json!([0.9, 0.1]), "person"); h.pump();
    h.born("Memory:b", "Memory", Map::new(), "person"); h.pump();
    h.write("Memory:b", "text", json!("Saw a quokka near some lake."), "person"); h.pump();
    assert_eq!(h.get("Memory:b", "in_flight"), json!(true), "the note's own embedding is asked, and the note waits for it");
    // the receipt of the note's own embedding call
    if !h.snapshot().contains_key("Fetch:recall:note") { h.born("Fetch:recall:note", "Fetch", Map::new(), "person"); }
    h.write("Fetch:recall:note", "request_result", json!({"id": {"ask": "Memory:b"}, "status": "ok", "rc": 0, "http_status": 200, "body": json!({"embeddings": [[1.0, 0.0]]}).to_string()}), "person");
    h.pump();
    assert_eq!(h.get("Memory:b", "in_flight"), json!(false));
    let a = h.get("Memory:b", "associations").as_str().unwrap_or("").to_string();
    assert!(a.contains("The marsupial slept all afternoon."), "the cosine lane: no shared word, near by meaning: {a}");
    assert_eq!(h.get("recall:dials", "assoc_n"), json!(5));
    assert_eq!(h.get("recall:dials", "assoc_clip"), json!(200));
    assert_eq!(h.get("Memory:b", "assoc_n"), json!(5), "read from the dials");
}

#[test]
fn the_memory_count_and_clip_are_the_dials() {
    let h = seat();
    for (i, t) in ["The quokka sleeps under a eucalyptus tree.", "A hungry quokka searches every bin nearby.", "Tourists photograph one smiling quokka daily.", "Rottnest island hosts many quokka families."].iter().enumerate() { leaf(&h, &format!("n{i}"), t); }
    h.write("recall:dials", "assoc_n", json!(2), "person");
    h.write("recall:dials", "assoc_clip", json!(20), "person"); h.pump();
    h.born("Memory:b", "Memory", Map::new(), "person"); h.pump();
    h.write("Memory:b", "text", json!("A quokka."), "person"); h.pump();
    let a = h.get("Memory:b", "associations").as_str().unwrap_or("").to_string();
    assert_eq!(a.lines().count(), 3, "the head and two lines: {a}");
    assert!(a.lines().skip(1).all(|l| l.starts_with("- \"") && l.contains("...\" (") && l.find("...\"").unwrap() <= 3 + 20), "clipped to 20: {a}");
    // the dials are guarded like the others: a bad value is put back
    h.write("recall:dials", "assoc_n", json!(0), "person"); h.pump();
    assert_eq!(h.get("recall:dials", "assoc_n"), json!(2));
}

#[test]
fn every_semtext_is_asked_for_its_sentence_vectors_without_a_flag() {
    let h = seat_with(&props(json!({"chat_claude_bin": "/usr/bin/true", "chat_auth": "login", "seat_backend": "http://127.0.0.1:9", "embed_model": "m"})));
    leaf(&h, "a", "A text of a pack. It has two sentences.");
    let k = "Leaf:x:a:body";
    assert!(h.snapshot().contains_key(&format!("membrane::Embed:ask:{k}")), "asked with no `embed` write: {:?}", h.snapshot().keys().filter(|x| x.contains("Embed")).collect::<Vec<_>>());
    h.born("Memory:n", "Memory", Map::new(), "person"); h.pump();
    h.write("Memory:n", "text", json!("A note."), "person"); h.pump();
    assert!(h.snapshot().contains_key("membrane::Embed:ask:Memory:n"), "a note is a SemText too");
}

#[test]
fn the_recall_pack_names_no_default_of_its_own() {
    let t = std::fs::read_to_string(repo().join("membrane/apps/barest.c1f/recall.c1f")).unwrap();
    assert!(!t.contains("mxbai"), "the embedding model's default lives in launcher.c1f only");
}
