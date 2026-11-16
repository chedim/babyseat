//! A model's own layer can answer a message without starting any model (#1143, operator 2026-11-15: "can't a model override that routing for that?").
//! The layer is a pack that imports `chat_turn` and overrides what it defines on `ChatMessage`: `plan` (none for the messages the rule covers: no model starts) and `answer`
//! (the rule's text), so the turn's own finish writes the response the way it does for any answer. Every test runs on the SHIPPED packs of the barest seat; nothing is stubbed
//! but the model backend (a path that would fail loudly if a model were started).
#![cfg(feature = "store")]
use membrane_vm::host::{self, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

const MAKER: &str = "PACK answermaker 1\nIMPORT semtext 1\nIMPORT chat 1\nBox.go on-setter make:\n  ALWAYS:\n    membrane::Chat:main:message[this.k] = ChatMessage(text: this.t, at: this.at)\n    membrane::Chat:main:message[this.k]:body = SemText(text: this.t)\n    membrane::Chat:main:message[this.k]:body.mention = True\n    membrane::Chat:main:message[this.k]:turn = ChatTurn(message: 'membrane::Chat:main:message:' + this.k, queued_at: this.at)\n    membrane::Chat:main:message[this.k].queued = True\n";
/// the model's layer: a message that says exactly `ping` is answered `pong` by the graph, everything else goes to the model as before. The answer waits for `queued` (the turn exists then:
/// the turn's finish writes to it, and a finish that fires before the turn is born writes nothing)
const LAYER: &str = "PACK myanswers 1\nIMPORT chat_turn 1\nChatMessage.plan getter:\n  WHEN this.text == 'ping': None\nChatMessage.answer getter:\n  WHEN this.text == 'ping' and this.queued == True: 'pong'\n";

const M: &str = "membrane::Chat:main:message:";

fn seat(layer: Option<&str>) -> Host {
    membrane_vm::expr::set_eval_time_ms(Some(10_000));
    let mut o = Options::new(None);
    o.effects = true;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    let l = h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    assert!(l.msgs.is_empty(), "the app loads clean: {:?}", l.msgs);
    h.load_pack("answermaker", MAKER).unwrap();
    if let Some(src) = layer { h.load_pack("myanswers", src).unwrap_or_else(|e| panic!("the layer is refused: {e}")); }
    h.rebuild();
    // a claude path that does not exist: a model that is started would end the turn in an error (and the test would see it)
    host::boot_started(&h, &props(json!({"chat_claude_bin": "/nonexistent/claude", "seat_default": "claude", "chat_auth": "login"})), true);
    h.pump();
    h.hold("Http:fake");
    h.born("Http:fake", "Http", props(json!({"listen_result": {"port": 4097}})), "person"); h.pump();
    h
}
fn post(h: &Host, id: &str, text: &str, at: f64) {
    h.born(&format!("Box:{id}"), "Box", props(json!({"k": id, "t": text, "at": at})), "person"); h.pump();
    h.write(&format!("Box:{id}"), "go", json!(true), "person"); h.pump();
}
fn until(h: &Host, what: &str, f: impl Fn(&Host) -> bool) { for _ in 0..200 { h.pump(); if f(h) { return; } std::thread::sleep(std::time::Duration::from_millis(25)); } panic!("never: {what}"); }
fn st(h: &Host, id: &str) -> String { h.get(&format!("{M}{id}"), "tstate").as_str().unwrap_or("").to_string() }
fn response(h: &Host, id: &str) -> J { h.get(&format!("{M}{id}:turn:response"), "text") }

#[test]
fn a_layer_answers_its_message_without_starting_a_model() {
    let h = seat(Some(LAYER));
    post(&h, "m1", "ping", 1.0);
    until(&h, "the turn ends", |h| st(h, "m1") == "done" || st(h, "m1") == "error");
    assert_eq!(st(&h, "m1"), "done", "the layer's answer finishes the turn: {:?}", h.get(&format!("{M}m1:turn"), "error"));
    assert_eq!(response(&h, "m1"), json!("pong"));
    assert_eq!(h.get(&format!("{M}m1:turn:response:body"), "text"), json!("pong"), "the answer is a SemText like any other: mentioned and found by recall");
    assert_eq!(h.get("membrane::Command:chat:main:message:m1", "cmd"), J::Null, "no command, so no model, was started");
    assert!(!h.get("membrane::Banner:admin:main", "text").to_string().contains("ENDED WITH AN ERROR"), "the next banner does not call a layer's answer a failed turn");
}

#[test]
fn a_message_the_layer_does_not_cover_still_goes_to_the_model() {
    let h = seat(Some(LAYER));
    post(&h, "m1", "ping", 1.0);
    post(&h, "m2", "hello there", 2.0);
    until(&h, "the second turn ends", |h| st(h, "m2") == "done" || st(h, "m2") == "error");
    assert_eq!(st(&h, "m2"), "error", "the uncovered message went the normal way (the claude path does not exist here)");
    assert!(h.get(&format!("{M}m2:turn"), "error").as_str().unwrap_or("").contains("claude"), "{:?}", h.get(&format!("{M}m2:turn"), "error"));
    assert_eq!(st(&h, "m1"), "done");
}

#[test]
fn without_the_layer_the_same_message_goes_to_the_model() {
    let h = seat(None);
    post(&h, "m1", "ping", 1.0);
    until(&h, "the turn ends", |h| st(h, "m1") == "done" || st(h, "m1") == "error");
    assert_eq!(st(&h, "m1"), "error", "no layer: the model path is taken (and refused here)");
}

