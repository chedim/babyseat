//! What the model had said is not lost when its turn ends in an error (#1143, operator 2026-11-15): the text streamed so far becomes the turn's response body (a SemText, mentioned and
//! embedded like any answer) and the response is marked `interrupted` with the error's reason beside it. The turn still ends as an error and its answer text still says so. No text
//! streamed: nothing is written (today's behaviour). In process on the SHIPPED packs of the barest seat; nothing runs (effects off): the streamed words and the receipt are stubbed.
use membrane_vm::host::{self, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// a chat message as the chat makes it (its body mentioned, its turn born), queued when `q` is true
const MAKER: &str = "PACK recallmaker 1\nIMPORT semtext 1\nIMPORT chat 1\nBox.go on-setter make:\n  ALWAYS:\n    membrane::Chat:main:message[this.k] = ChatMessage(text: this.t, at: this.at)\n    membrane::Chat:main:message[this.k]:body = SemText(text: this.t)\n    membrane::Chat:main:message[this.k]:body.mention = True\n    membrane::Chat:main:message[this.k]:turn = ChatTurn(message: 'membrane::Chat:main:message:' + this.k, queued_at: this.at)\n    FOR a IN ([1] if this.q == True else []):\n      membrane::Chat:main:message[this.k].queued = True\n";

fn seat(extra: J) -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(10_000));
    let mut f = props(json!({"chat_claude_bin": "/usr/bin/true", "chat_auth": "login"}));
    for (k, v) in props(extra) { f.insert(k, v); }
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    if f.contains_key("seat_backend") { h.load_app(&r.join("membrane/packs/launcher.c1f"), false).unwrap(); }
    let l = h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    assert!(l.msgs.is_empty(), "the app loads clean: {:?}", l.msgs);
    h.load_pack("recallmaker", MAKER).unwrap();
    h.rebuild();
    host::boot_started(&h, &f, true);
    h.pump();
    h.hold("Http:fake");
    h.born("Http:fake", "Http", props(json!({"listen_result": {"port": 4097}})), "person"); h.pump();
    h
}
const M: &str = "membrane::Chat:main:message:";
fn post(h: &Host, id: &str, text: &str) {
    h.born(&format!("Box:{id}"), "Box", props(json!({"k": id, "t": text, "at": 1.0, "q": true})), "person"); h.pump();
    h.write(&format!("Box:{id}"), "go", json!(true), "person"); h.pump();
}
fn running(h: &Host, id: &str) { assert_eq!(h.get(&format!("{M}{id}"), "tstate"), json!("running"), "the turn has started"); }
/// what the page showed while the turn ran: the accumulator of the command's streamed words (a virtual `Chunk`, packs/chat_turn.c1f)
fn streamed(h: &Host, id: &str, text: &str) {
    let acc = format!("Chunk:membrane::Command:chat:main:message:{id}:acc");
    h.hold(&acc);   // the running command's effect holds its chunks; a virtual entity nothing holds is collected
    h.born(&acc, "Chunk", props(json!({"text": text})), "person"); h.pump();
}
fn receipt(h: &Host, id: &str, rc: i64, stdout: &str, stderr: &str) {
    h.write(&format!("membrane::Command:chat:main:message:{id}"), "run_result", json!({"id": format!("main:message:{id}"), "status": "ok", "rc": rc, "stdout": stdout, "stderr": stderr}), "person");
    h.pump();
}
fn resp(h: &Host, id: &str, p: &str) -> J { h.get(&format!("{M}{id}:turn:response"), p) }
fn body(h: &Host, id: &str) -> J { h.get(&format!("{M}{id}:turn:response:body"), "text") }

#[test]
fn a_failed_run_keeps_what_the_model_had_streamed_as_the_response_body_marked_interrupted() {
    let h = seat(json!({}));
    post(&h, "m1", "Plan the trip?");
    running(&h, "m1");
    streamed(&h, "m1", "Where do you want to go? When? With whom?");
    receipt(&h, "m1", 1, "", "budget exceeded (50 steps)");
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("error"));
    assert!(resp(&h, "m1", "text").as_str().unwrap().contains("claude failed"), "the answer still says it ended with an error: {}", resp(&h, "m1", "text"));
    assert_eq!(resp(&h, "m1", "error"), json!(true));
    assert_eq!(resp(&h, "m1", "interrupted"), json!(true));
    assert!(resp(&h, "m1", "reason").as_str().unwrap().contains("budget exceeded"), "{}", resp(&h, "m1", "reason"));
    assert_eq!(body(&h, "m1"), json!("Where do you want to go? When? With whom?"));
    assert_eq!(h.get(&format!("{M}m1:turn:response:body"), "mention"), json!(true), "mentioned like any answer");
}

#[test]
fn the_banners_previous_error_line_still_names_the_failed_turn() {
    let h = seat(json!({}));
    post(&h, "m1", "Plan the trip?");
    streamed(&h, "m1", "Where to?");
    receipt(&h, "m1", 1, "", "boom");
    assert_eq!(h.get("membrane::Chat:main", "last_turn"), json!(format!("{M}m1:turn")));
    let t = h.get("membrane::Banner:admin:main", "text");
    assert!(t.as_str().unwrap().contains("claude failed"), "{t}");
}

#[test]
fn a_run_that_streamed_nothing_writes_no_body_and_no_mark() {
    let h = seat(json!({}));
    post(&h, "m1", "hello");
    receipt(&h, "m1", 1, "", "boom");
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("error"));
    assert!(resp(&h, "m1", "text").as_str().unwrap().contains("claude failed"));
    assert_eq!(resp(&h, "m1", "interrupted"), J::Null);
    assert!(!h.snapshot().contains_key(&format!("{M}m1:turn:response:body")), "no body without words");
}

#[test]
fn the_finished_messages_text_is_kept_when_the_stream_is_gone() {
    let h = seat(json!({}));
    post(&h, "m1", "hello");
    let line = json!({"type": "assistant", "message": {"content": [{"type": "text", "text": "One question: which day?"}]}}).to_string();
    receipt(&h, "m1", 1, &format!("{line}\n"), "killed");
    assert_eq!(resp(&h, "m1", "interrupted"), json!(true));
    assert_eq!(body(&h, "m1"), json!("One question: which day?"));
}

#[test]
fn a_turn_that_ends_well_is_not_marked_and_its_answer_is_the_body() {
    let h = seat(json!({}));
    post(&h, "m1", "hello");
    streamed(&h, "m1", "Hi");
    let line = json!({"type": "result", "subtype": "success", "is_error": false, "result": "Hi there."}).to_string();
    receipt(&h, "m1", 0, &format!("{line}\n"), "");
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("done"));
    assert_eq!(resp(&h, "m1", "interrupted"), J::Null);
    assert_eq!(body(&h, "m1"), json!("Hi there."));
}

#[test]
fn the_page_view_names_the_interruption_and_carries_the_kept_words() {
    let h = seat(json!({}));
    post(&h, "m1", "Plan the trip?");
    streamed(&h, "m1", "Where to?");
    receipt(&h, "m1", 1, "", "boom");
    let v = h.get(&format!("{M}m1"), "page_view");
    assert_eq!(v["kept"], json!("Where to?"), "{v}");
    assert!(v["interrupted"].as_str().unwrap().contains("claude failed"), "{v}");
    post(&h, "m2", "again");
    let l = json!({"type": "result", "subtype": "success", "is_error": false, "result": "Fine."}).to_string();
    receipt(&h, "m2", 0, &format!("{l}\n"), "");
    assert_eq!(h.get(&format!("{M}m2"), "page_view")["interrupted"], J::Null);
}

// ---- the OpenAI-compatible rung (packs/chat_oai.c1f) ----------------------------------------------------------------------------------------
fn oai_seat() -> Host {
    let h = seat(json!({"cloud_models": [{"name": "gpt-fake", "backend": "openai", "base_url": "http://127.0.0.1:1"}]}));
    h.born("Minds:seat", "Minds", props(json!({"loops": "gpt-fake"})), "person"); h.pump();
    h
}
/// the door's tool list arrives (round 1 starts)
fn oai_tools(h: &Host, id: &str) {
    let body = json!({"result": {"tools": [{"name": "entity_read", "description": "d", "inputSchema": {"type": "object"}}]}}).to_string();
    h.write(&format!("Fetch:oai:main:message:{id}:tools"), "request_result", json!({"rc": 0, "http_status": 200, "body": body}), "person"); h.pump();
}
fn sse(content: &str) -> String { format!("data: {}\n\n", json!({"choices": [{"delta": {"content": content}}]})) }
fn oai_round(h: &Host, id: &str, n: u32, rc: i64, status: i64, body: &str) {
    h.write(&format!("Fetch:oai:main:message:{id}:{n}"), "request_result", json!({"rc": rc, "http_status": status, "head": "HTTP/1.1", "body": body}), "person"); h.pump();
}

#[test]
fn a_model_that_fails_mid_stream_keeps_its_words_in_the_round_marked_interrupted() {
    let h = oai_seat();
    post(&h, "m1", "Plan the trip?");
    running(&h, "m1");
    oai_tools(&h, "m1");
    let stream = format!("{}{}data: {}\n\n", sse("Where to? "), sse("When?"), json!({"error": {"message": "the model is overloaded"}}));
    oai_round(&h, "m1", 1, 0, 200, &stream);
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("error"));
    assert!(resp(&h, "m1", "text").as_str().unwrap().contains("reported an error"), "{}", resp(&h, "m1", "text"));
    assert_eq!(resp(&h, "m1", "interrupted"), json!(true));
    assert!(resp(&h, "m1", "reason").as_str().unwrap().contains("overloaded"));
    assert_eq!(body(&h, "m1"), json!("Where to? When?"));
    assert_eq!(h.get(&format!("{M}m1:turn:response:body"), "mention"), json!(true));
}

#[test]
fn a_model_whose_call_is_cut_keeps_the_words_the_page_had_and_one_that_said_nothing_keeps_nothing() {
    let h = oai_seat();
    post(&h, "m1", "Plan the trip?");
    oai_tools(&h, "m1");
    let acc = "Chunk:Fetch:oai:main:message:m1:1:acc";
    h.hold(acc);
    h.born(acc, "Chunk", props(json!({"text": "Which day? "})), "person"); h.pump();
    oai_round(&h, "m1", 1, 28, 0, "");
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("error"));
    assert_eq!(resp(&h, "m1", "interrupted"), json!(true));
    assert_eq!(body(&h, "m1"), json!("Which day?"));
    let g = oai_seat();
    post(&g, "m2", "again");
    oai_tools(&g, "m2");
    oai_round(&g, "m2", 1, 28, 0, "");
    assert_eq!(g.get(&format!("{M}m2"), "tstate"), json!("error"));
    assert_eq!(resp(&g, "m2", "interrupted"), J::Null);
    assert!(!g.snapshot().contains_key(&format!("{M}m2:turn:response:body")));
}

#[test]
fn what_an_earlier_round_said_before_its_tool_calls_is_kept_too() {
    let h = oai_seat();
    post(&h, "m1", "Plan the trip?");
    oai_tools(&h, "m1");
    let call = json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "entity_read", "arguments": "{}"}}]}}]});
    oai_round(&h, "m1", 1, 0, 200, &format!("{}data: {}\n\n", sse("Let me look. "), call));
    h.write("Fetch:oai:main:message:m1:1:0", "request_result", json!({"rc": 0, "http_status": 200, "body": json!({"result": {"content": [{"type": "text", "text": "ok"}]}}).to_string()}), "person"); h.pump();
    oai_round(&h, "m1", 2, 0, 200, &format!("{}data: {}\n\n", sse("Which day?"), json!({"error": "overloaded"})));
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("error"));
    assert_eq!(body(&h, "m1"), json!("Let me look.\n\nWhich day?"));
}

/// the answer of an OpenAI-compatible model is a SemText body like the claude turn's (the pack imports `semtext`; without it the body was a bare entity with no text)
#[test]
fn an_openai_answer_is_a_mentioned_semtext_body() {
    let h = oai_seat();
    post(&h, "m1", "hello");
    oai_tools(&h, "m1");
    oai_round(&h, "m1", 1, 0, 200, &sse("Fine."));
    assert_eq!(h.get(&format!("{M}m1"), "tstate"), json!("done"));
    assert_eq!(body(&h, "m1"), json!("Fine."));
    assert_eq!(resp(&h, "m1", "interrupted"), J::Null);
}
