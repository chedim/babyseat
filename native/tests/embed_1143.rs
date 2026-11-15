//! Sentence vectors (#1143, stage 1 of the semantic field; membrane/packs/embed.c1f): every sentence of EVERY SemText (asked when its text is written, no flag: the `ask` of the scratch pack below writes
//! the old `embed` flag, which nothing reads now) or found in the store (the backlog: the bodies of the imported history) gets a vector, made by the local model server through the Fetch driver
//! (`POST <seat_backend>/api/embed {model, input}`), stored as `Vector:of:<key>:s:<start>`, by a gentle one-request-at-a-time queue that waits while a chat turn runs.
//! There is NEVER a real model server here: the receipts are written by the test (the Fetch driver's own writes), or, in the two end-to-end tests, a tiny HTTP server in this file answers.
use membrane_vm::host::{boot_started, Host, Options};
use serde_json::{json, Map, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// scratch packs on top of the seat: `Box.say` makes a chat body (`Chat:main:message:<id>:body`) with the text, `Box.ask` asks for its vectors (what the chat does), `Box.turn` makes a turn
const MAKER: &str = "PACK embmaker 1\nIMPORT semtext 1\nIMPORT chat 1\nIMPORT vector 1\nProbe.back getter: [prop(e, 'v') for e in this.eids]\n\
Box.say on-setter say:\n  WHEN this.say != None:\n    membrane::Chat:main:message[prop(this.say, 'id')]:body = SemText(text: prop(this.say, 'text'))\n\
Box.ask on-setter ask:\n  WHEN this.ask != None:\n    membrane::Chat:main:message[this.ask]:body.embed = True\n\
Box.sturn on-setter sturn:\n  WHEN this.sturn != None:\n    membrane::Chat:session[\'s1\']:message[this.sturn]:turn = ChatTurn(message: 1, queued_at: 1)\n\
Box.turn on-setter turn:\n  WHEN this.turn != None:\n    membrane::Chat:main:message[this.turn]:turn = ChatTurn(message: 'membrane::Chat:main:message:' + this.turn, queued_at: 1)\n";

fn load(h: &Host, extra: &str) { load_skip(h, extra, &[]) }
/// the seat's packs but those named (`embed`: a store from before the sentence vectors, whose texts no ask ever named)
fn load_skip(h: &Host, extra: &str, skip: &[&str]) {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let names: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#') && !skip.contains(l)).map(String::from).collect();
    for (i, n) in names.iter().enumerate() { h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}")); }
    h.load_app(&r.join("membrane/packs/launcher.c1f"), false).unwrap();
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_pack("embmaker", MAKER).unwrap();
    let _ = extra;
    h.rebuild();
}
fn open(file: Option<&str>, effects: bool) -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(10_000));
    let mut o = Options::new(file.map(String::from));
    o.effects = effects;
    Host::open(o).unwrap()
}
const BACKEND: &str = "http://127.0.0.1:9";
/// a seat whose model server is nowhere (the receipts are the test's)
fn seat() -> Host { seat_with(&[]) }
fn seat_with(facts: &[(&str, &str)]) -> Host {
    let h = open(None, false);
    load(&h, "");
    start(&h, facts);
    h
}
fn start(h: &Host, facts: &[(&str, &str)]) {
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!(BACKEND));
    for (k, v) in facts { f.insert((*k).into(), json!(v)); }
    boot_started(h, &f, true);
    h.pump();
}
fn box_(h: &Host) { h.born("Box:a", "Box", props(json!({})), "person"); }
fn say(h: &Host, id: &str, text: &str, ask: bool) {
    h.write("Box:a", "say", json!({"id": id, "text": text}), "person");
    h.pump();
    if ask { h.write("Box:a", "ask", json!(id), "person"); h.pump(); }
}
fn body(id: &str) -> String { format!("membrane::Chat:main:message:{id}:body") }
fn vec_eid(id: &str, start: usize) -> String { format!("Vector:of:membrane/Chat:main:message:{id}:body:s:{start}") }
const Q: &str = "membrane::Embed:queue";
/// the request that is out: the engine consumes a driver write (`Fetch:embed.request` reads empty once it is sent), so the queue's own `inflight` is what is read here; the request itself is checked end to end below
fn request(h: &Host) -> J {
    let j = h.get(Q, "inflight");
    if j.is_null() || h.get(Q, "flying") != json!(true) { return J::Null; }
    json!({"id": j["id"], "url": j["url"], "method": "POST", "body": {"model": j["model"], "input": j["unc"].as_array().unwrap().iter().map(|x| x["text"].clone()).collect::<Vec<_>>()}})
}
fn seq(h: &Host) -> J { h.get(Q, "seq") }
fn inputs(h: &Host) -> Vec<String> { request(h)["body"]["input"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect() }
/// the deterministic vector of a sentence (dimension 3)
fn vector(t: &str) -> Vec<f64> { vec![t.len() as f64, (t.bytes().map(|b| b as u64).sum::<u64>() % 7) as f64, 1.0] }
/// the model server's receipt for the request now out
fn answer(h: &Host) {
    let r = request(h);
    let embeddings: Vec<Vec<f64>> = inputs(h).iter().map(|t| vector(t)).collect();
    receipt(h, &r["id"], json!({"id": r["id"], "status": "ok", "rc": 0, "head": "HTTP/1.1 200 OK", "http_status": 200, "headers": {}, "body": json!({"model": "m", "embeddings": embeddings}).to_string()}));
}
fn receipt(h: &Host, _id: &J, rec: J) { h.write("Fetch:embed", "request_result", rec, "driver:request"); h.pump(); }
fn sentences(n: usize) -> String { (1..=n).map(|i| format!("Sentence number {i} is here.")).collect::<Vec<_>>().join(" ") }
fn state(h: &Host) -> J { h.get(Q, "state") }
/// the numbers of some vectors as a rule reads them (the row holds a summary only: the vector pack keeps them in its own store)
fn back(h: &Host, eids: &[String]) -> Vec<J> {
    let n = h.snapshot().keys().filter(|k| k.starts_with("Probe:")).count();
    let p = format!("Probe:p{n}");
    h.born(&p, "Probe", props(json!({"eids": eids})), "person");
    h.pump();
    h.get(&p, "back").as_array().unwrap().clone()
}

#[test]
fn an_ask_makes_one_request_for_the_sentences_with_the_default_model() {
    let h = seat();
    box_(&h);
    say(&h, "m1", "Dogs chase cats. Cats climb trees.", true);
    let r = request(&h);
    assert_eq!(r["url"], json!(format!("{BACKEND}/api/embed")), "{r}");
    assert_eq!(r["method"], json!("POST"));
    assert_eq!(r["body"]["model"], json!("mxbai-embed-large"), "the launcher's default model");
    assert_eq!(inputs(&h), ["Dogs chase cats.", "Cats climb trees."]);
    assert_eq!(state(&h), json!("working"));
}

#[test]
fn the_embed_model_fact_wins_over_the_default() {
    let h = seat_with(&[("embed_model", "nomic-embed-text")]);
    box_(&h);
    say(&h, "m1", "One sentence.", true);
    assert_eq!(request(&h)["body"]["model"], json!("nomic-embed-text"));
}

#[test]
fn the_receipt_becomes_vector_entities_the_text_is_marked_and_the_ask_is_gone() {
    let h = seat();
    box_(&h);
    say(&h, "m1", "Dogs chase cats. Cats climb trees.", true);
    answer(&h);
    for (start, t) in [(0usize, "Dogs chase cats."), (17, "Cats climb trees.")] {
        let e = vec_eid("m1", start);
        assert_eq!(h.get(&e, "of"), json!(body("m1")), "{e}");
        assert_eq!(h.get(&e, "start"), json!(start));
        assert_eq!(back(&h, &[e.clone()]), [json!(vector(t))], "{e}");
    }
    assert_eq!(h.get(&body("m1"), "embedded"), json!(true));
    assert!(!h.snapshot().keys().any(|k| k.starts_with("membrane::Embed:ask:")), "the ask is retired: {:?}", h.snapshot().keys().filter(|k| k.contains("Embed")).collect::<Vec<_>>());
    assert_eq!(state(&h), json!("idle"));
}

#[test]
fn waiting_asks_go_oldest_first_so_the_vectors_land_in_text_order() {
    // the field (packs/field.c1f) moves its entities as the vectors land: a message's body must land before the answer asked after it
    let h = seat();
    box_(&h);
    say(&h, "m1", "First one is out.", true);
    say(&h, "z2", "Asked second.", true);
    std::thread::sleep(Duration::from_millis(5));
    say(&h, "a3", "Asked third.", true);
    answer(&h);
    assert_eq!(inputs(&h), ["Asked second."], "the older ask goes first (its id sorts later: the order is the ask's time, not the id)");
    answer(&h);
    assert_eq!(inputs(&h), ["Asked third."]);
}

#[test]
fn a_request_holds_at_most_64_sentences_and_the_rest_follows() {
    let h = seat();
    box_(&h);
    say(&h, "m1", &sentences(70), true);
    assert_eq!(inputs(&h).len(), 64);
    answer(&h);
    assert_eq!(inputs(&h).len(), 6, "the 6 left go in the next request");
    assert_eq!(inputs(&h)[0], "Sentence number 65 is here.");
    answer(&h);
    assert_eq!(h.get(&body("m1"), "embedded"), json!(true));
    assert_ne!(h.get(&vec_eid("m1", 0), "of"), J::Null);
}

#[test]
fn a_pass_is_256_sentences_the_cache_lives_for_a_pass_and_holds_at_most_64() {
    let h = seat();
    box_(&h);
    say(&h, "m1", &sentences(300), true);
    for _ in 0..3 { assert_eq!(inputs(&h).len(), 64); answer(&h); }
    assert_eq!(h.get(Q, "pass_n"), json!(192));
    assert_eq!(h.get(Q, "cache").as_array().unwrap().len(), 64, "never more than 64 entries");
    assert_eq!(inputs(&h).len(), 64);
    answer(&h);
    assert_eq!(h.get(Q, "pass_n"), json!(0), "256 sentences end the pass");
    assert_eq!(h.get(Q, "cache"), json!([]), "and the cache with it");
    assert_eq!(inputs(&h).len(), 44, "the next pass follows at once");
}

#[test]
fn a_sentence_said_twice_in_a_pass_makes_no_second_request() {
    let h = seat();
    box_(&h);
    say(&h, "m1", "The same words twice.", true);
    answer(&h);
    let first = seq(&h);
    say(&h, "m2", "The same words twice.", true);
    assert_eq!(seq(&h), first, "no new request: the cache had it");
    assert_eq!(request(&h), J::Null);
    assert_eq!(back(&h, &[vec_eid("m2", 0)]), [json!(vector("The same words twice."))]);
    assert_eq!(h.get(&body("m2"), "embedded"), json!(true));
}

#[test]
fn nothing_is_sent_while_a_turn_runs_a_request_in_flight_finishes_and_the_queue_resumes_after() {
    let h = seat();
    box_(&h);
    h.write("Box:a", "turn", json!("t1"), "person");
    h.pump();
    h.write("membrane::Chat:main:message:t1:turn", "state", json!("running"), "person");
    h.pump();
    say(&h, "m1", "Asked while a turn runs.", true);
    assert_eq!(request(&h), J::Null, "paused: no request while a turn runs");
    assert_eq!(state(&h), json!("paused"));
    h.write("membrane::Chat:main:message:t1:turn", "state", json!("done"), "person");
    h.pump();
    assert_eq!(inputs(&h), ["Asked while a turn runs."], "the turn is over: the queue went on by itself");
    // a turn starts while the request is out: the request finishes, the next waits
    h.write("membrane::Chat:main:message:t1:turn", "state", json!("running"), "person");
    h.pump();
    say(&h, "m2", "Second one.", true);
    let first = seq(&h);
    answer(&h);
    assert_ne!(h.get(&vec_eid("m1", 0), "v"), J::Null, "the request in flight was finished");
    assert_eq!(seq(&h), first, "the next request waits for the turn");
    assert_eq!(request(&h), J::Null);
    h.write("membrane::Chat:main:message:t1:turn", "state", json!("done"), "person");
    h.pump();
    assert_eq!(inputs(&h), ["Second one."]);
}

fn assert_stuck(h: &Host, needle: &str) -> String {
    assert_eq!(state(h), json!("stuck"));
    let why = h.get(Q, "reason").as_str().unwrap().to_string();
    assert!(why.contains(needle), "{why}");
    assert!(h.get(Q, "line").as_str().unwrap().contains(needle));
    assert_eq!(h.get(&vec_eid("m1", 0), "of"), J::Null, "the sentence stays pending");
    why
}

#[test]
fn a_model_server_that_is_not_running_is_a_visible_state_and_a_retry_waits_for_the_next_chat_event() {
    let h = seat();
    box_(&h);
    say(&h, "m1", "Nobody answers this.", true);
    let first = request(&h)["id"].clone();
    let n = seq(&h);
    receipt(&h, &first, json!({"id": first, "status": "error", "rc": 1, "head": "connect 127.0.0.1:9: Connection refused"}));
    assert_stuck(&h, "did not answer");
    h.pump();
    assert_eq!(seq(&h), n, "no hot loop: nothing is sent again by itself");
    assert_eq!(request(&h), J::Null);
    // the next chat event (a turn that starts and ends) is the next quiet moment: the queue tries again
    h.write("Box:a", "turn", json!("t1"), "person");
    h.pump();
    h.write("membrane::Chat:main:message:t1:turn", "state", json!("running"), "person");
    h.pump();
    assert_eq!(seq(&h), n, "not while the turn runs");
    h.write("membrane::Chat:main:message:t1:turn", "state", json!("done"), "person");
    h.pump();
    assert_ne!(request(&h)["id"], first, "tried again when idle");
    assert_eq!(seq(&h), json!(n.as_i64().unwrap() + 1));
    answer(&h);
    assert_eq!(state(&h), json!("idle"));
    assert_eq!(h.get(Q, "reason"), J::Null, "the reason is cleared with the success");
    assert_ne!(h.get(&vec_eid("m1", 0), "of"), J::Null);
}

#[test]
fn a_model_that_is_not_installed_says_so_and_how_to_install_it() {
    let h = seat();
    box_(&h);
    say(&h, "m1", "No such model here.", true);
    let id = request(&h)["id"].clone();
    receipt(&h, &id, json!({"id": id, "status": "ok", "rc": 0, "head": "HTTP/1.1 404 Not Found", "http_status": 404, "headers": {}, "body": "{\"error\":\"model \\\"mxbai-embed-large\\\" not found, try pulling it first\"}"}));
    let why = assert_stuck(&h, "not found");
    assert!(why.contains("ollama pull mxbai-embed-large"), "{why}");
}

#[test]
fn a_wrong_answer_and_a_timeout_are_visible_states_too() {
    let h = seat();
    box_(&h);
    say(&h, "m1", "One. Two.", true);
    let id = request(&h)["id"].clone();
    receipt(&h, &id, json!({"id": id, "status": "ok", "rc": 0, "head": "HTTP/1.1 200 OK", "http_status": 200, "headers": {}, "body": "{\"embeddings\":[[1,2,3]]}"}));
    assert_stuck(&h, "wrong answer");
    let h = seat();
    box_(&h);
    say(&h, "m1", "One.", true);
    let id = request(&h)["id"].clone();
    receipt(&h, &id, json!({"id": id, "status": "error", "rc": 1, "head": "read: timed out"}));
    assert_stuck(&h, "timed out");
}

#[test]
fn a_seat_with_no_embedding_model_is_stuck_in_plain_words_and_sends_nothing() {
    let h = open(None, false);
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() { h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap(); }
    h.load_pack("embmaker", MAKER).unwrap();
    h.rebuild();
    start(&h, &[]);
    box_(&h);
    say(&h, "m1", "Nobody names a model.", true);
    assert_eq!(request(&h), J::Null);
    assert_eq!(state(&h), json!("stuck"));
    assert!(h.get(Q, "reason").as_str().unwrap().contains("embed_model"));
}

#[test]
fn the_settings_row_shows_the_queue_line_and_is_read_only() {
    let h = seat();
    box_(&h);
    let row = "Setting:import.embeddings";
    assert_eq!(h.get(row, "group"), json!("import"));
    assert_eq!(h.get(row, "label"), json!("Sentence vectors"));
    assert_eq!(h.get(row, "kind"), json!("readonly"));
    say(&h, "m1", "Nobody answers this.", true);
    let id = request(&h)["id"].clone();
    receipt(&h, &id, json!({"id": id, "status": "error", "rc": 1, "head": "connect 127.0.0.1:9: Connection refused"}));
    assert_eq!(h.get(row, "current"), h.get(Q, "line"), "the row is the queue's own sentence");
    assert!(h.get(row, "current").as_str().unwrap().contains("did not answer"));
}

// ---- end to end: the Fetch driver against a tiny model server, the backlog of a store, the replay ---------------------------------------------------------
struct Fake { port: u16, seen: Arc<Mutex<Vec<J>>> }
fn fake() -> Fake {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    let seen: Arc<Mutex<Vec<J>>> = Arc::new(Mutex::new(vec![]));
    let s2 = seen.clone();
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
            let embeddings: Vec<Vec<f64>> = body["input"].as_array().map(|a| a.iter().map(|t| vector(t.as_str().unwrap_or(""))).collect()).unwrap_or_default();
            s2.lock().unwrap().push(json!({"path": first.split_whitespace().nth(1).unwrap_or(""), "body": body}));
            let text = json!({"model": "m", "embeddings": embeddings}).to_string();
            let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}", text.len());
            let _ = s.flush();
        }
    });
    Fake { port, seen }
}
fn until(what: &str, mut f: impl FnMut() -> bool, h: &Host) {
    let t = Instant::now();
    while !f() { assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}"); h.pump(); std::thread::sleep(Duration::from_millis(25)); }
}
fn tmp(tag: &str) -> PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("embed-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

#[test]
fn through_the_fetch_driver_the_request_is_the_ollama_call_and_the_vectors_arrive() {
    let srv = fake();
    let dir = tmp("e2e");
    let file = dir.join("seat.sqlite");
    let h = open(Some(file.to_str().unwrap()), true);
    load(&h, "");
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!(format!("http://127.0.0.1:{}", srv.port)));
    boot_started(&h, &f, true);
    h.pump();
    box_(&h);
    say(&h, "m1", "Dogs chase cats. Cats climb trees.", true);
    until("the vectors", || h.get(&vec_eid("m1", 17), "v") != J::Null, &h);
    assert_eq!(back(&h, &[vec_eid("m1", 0)]), [json!(vector("Dogs chase cats."))]);
    let seen = srv.seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 1);
    assert_eq!(seen[0]["path"], json!("/api/embed"));
    assert_eq!(seen[0]["body"]["model"], json!("mxbai-embed-large"));
    assert_eq!(seen[0]["body"]["input"], json!(["Dogs chase cats.", "Cats climb trees."]));
    assert_eq!(h.get(Q, "state"), json!("idle"));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn the_backlog_of_the_store_is_embedded_oldest_first_once_the_seat_starts() {
    let srv = fake();
    let dir = tmp("backlog");
    let file = dir.join("seat.sqlite");
    {
        // an earlier run (the imported history, a store from before the sentence vectors): bodies nobody asked for
        let h = open(Some(file.to_str().unwrap()), false);
        load_skip(&h, "", &["embed"]);
        start(&h, &[]);
        box_(&h);
        say(&h, "1000-00000001", "Oldest message.", false);
        say(&h, "2000-00000002", "Middle message.", false);
        say(&h, "3000-00000003", "Newest message.", false);
        h.pump();
    }
    let h = open(Some(file.to_str().unwrap()), true);
    load(&h, "");
    h.load_stored_entities();
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!(format!("http://127.0.0.1:{}", srv.port)));
    boot_started(&h, &f, true);
    until("the whole backlog", || h.get(&vec_eid("3000-00000003", 0), "v") != J::Null, &h);
    let seen = srv.seen.lock().unwrap().clone();
    let order: Vec<String> = seen.iter().map(|s| s["body"]["input"][0].as_str().unwrap().to_string()).collect();
    // oldest first (the field moves its entities in text order as the vectors land: packs/field.c1f)
    assert_eq!(order, ["Oldest message.", "Middle message.", "Newest message."], "oldest first, one text a request");
    for id in ["1000-00000001", "2000-00000002", "3000-00000003"] { assert_eq!(h.get(&body(id), "embedded"), json!(true), "{id}"); }
    until("the progress", || h.get(Q, "line").as_str().map_or(false, |l| l.starts_with("3 of 3 texts have sentence vectors")), &h);
    let _ = std::fs::remove_dir_all(dir);
}

const STURN: &str = "membrane::Chat:session:s1:message:t9:turn";

#[test]
fn a_turn_of_a_session_pauses_too_and_a_turn_that_ends_in_error_lets_the_queue_go_on() {
    let h = seat();
    box_(&h);
    h.write("Box:a", "sturn", json!("t9"), "person");
    h.pump();
    h.write(STURN, "state", json!("running"), "person");
    h.pump();
    say(&h, "m1", "Asked while a session turn runs.", true);
    assert_eq!(request(&h), J::Null);
    assert_eq!(state(&h), json!("paused"));
    h.write(STURN, "state", json!("error"), "person");
    h.pump();
    assert_eq!(inputs(&h), ["Asked while a session turn runs."], "an error ends the turn as done does");
}

#[test]
fn a_turn_the_last_run_left_running_does_not_pause_the_next_run_forever() {
    let dir = tmp("stale");
    let file = dir.join("seat.sqlite");
    {
        let h = open(Some(file.to_str().unwrap()), false);
        load(&h, "");
        start(&h, &[]);
        box_(&h);
        h.write("Box:a", "turn", json!("t1"), "person");
        h.pump();
        h.write("membrane::Chat:main:message:t1:turn", "state", json!("running"), "person");
        h.pump();
        assert_eq!(h.get(Q, "busy"), json!(true));
    }
    std::thread::sleep(Duration::from_millis(20));
    let h = open(Some(file.to_str().unwrap()), false);
    load(&h, "");
    h.load_stored_entities();
    start(&h, &[]);
    box_(&h);
    say(&h, "m1", "Asked after a restart.", true);
    assert_eq!(h.get(Q, "busy"), json!(false), "the dead run's pointer is not counted");
    assert_eq!(inputs(&h), ["Asked after a restart."]);
    let _ = std::fs::remove_dir_all(dir);
}
