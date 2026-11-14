//! The seat's use of Home Assistant (#1143, slice 2, pack `membrane/packs/ha.c1f`): the model writes `HaCall:<id>.request`, the COMPONENT inside Home Assistant (which holds no
//! secret of the seat's and the seat holds none of Home Assistant's) pulls the pending calls over `GET /membrane/ha/calls`, claims one and finishes it with `POST /membrane/ha/calls`;
//! the receipt is read on the entity like any driver's. `HaCatalog:ha` is the seat's copy of what Home Assistant exposes (`POST /membrane/ha/catalog`), `HaAllow:<name>` is the
//! allow-list (default deny). Every route is the OWNER's. Real seat process, real door, real tokens; nothing outside loopback. Every wait has a timeout; the child is always reaped.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
const PACKS: &[&str] = &["boot.c1f", "entity.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f",
    "door_session.c1f", "door_auth.c1f", "router.c1f", "door_models.c1f", "door_state.c1f", "ha.c1f"];
const FACTS: [&str; 4] = [
    r#"cloud_models=[{"name":"claude","backend":"claude-agent"},{"name":"claude-agent:sonnet","backend":"claude-agent"}]"#,
    "router_on=false", "seat_backend=http://127.0.0.1:1",
    r#"minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude"}"#];

struct Run { child: Child, _lines: Receiver<String> }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }
struct Dir(std::path::PathBuf);
impl Drop for Dir { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); } }

const FAKE: &str = r#"#!/usr/bin/env python3
import json, sys
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
prompt = sys.argv[-1]
out({"type": "system", "subtype": "init", "session_id": "q-1"})
if "this will fail" in prompt:
    sys.stderr.write("fake: not logged in\n"); sys.exit(3)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.0, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}, "session_id": "q-1"})
"#;

fn setup(tag: &str) -> Dir {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    let pd = d.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let bin = d.join("fake-claude");
    std::fs::write(&bin, FAKE).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    Dir(d)
}

/// a strict-door seat on the folder's store (a second launch on the same folder is a restart on the same store)
fn launch(dir: &Dir) -> (Run, u16) {
    let d = &dir.0;
    let pd = d.join("packs");
    let mut child = Command::new(BIN).current_dir(d).env("ANTHROPIC_API_KEY", "sk-ant-harness-default-0000")
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", d.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", d.to_str().unwrap(),
               "--fact", &format!("chat_claude_bin={}", d.join("fake-claude").display()), "--fact", "seat_cwd=/srv/seat", "--fact", "seat_project=membrane", "--fact", "seat_version=1.0",
               "--fact", &format!("door_secret_file={}", d.join("door.secret").display()), "--fact", &format!("seat_data_dir={}", d.display())])
        .args(FACTS.iter().flat_map(|f| ["--fact".to_string(), f.to_string()]))
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    (run, line.rsplit(':').next().unwrap().trim().parse().unwrap())
}

/// a clean stop (SIGTERM, then reap): the store is flushed, so a restart reads every write; a kill (the Drop) can lose the last moment
fn stop(mut r: Run) {
    let _ = Command::new("kill").arg("-TERM").arg(r.child.id().to_string()).status();
    let _ = r.child.wait();
}

fn http(port: u16, method: &str, path: &str, tok: Option<&str>, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let auth = tok.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{auth}\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn j(port: u16, method: &str, path: &str, tok: &str, body: J) -> (u16, J) {
    let (st, b) = http(port, method, path, Some(tok), &if body.is_null() { String::new() } else { body.to_string() });
    (st, serde_json::from_str(&b).unwrap_or(J::String(b)))
}
fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(50));
    }
}
fn owner_token(dir: &Dir) -> String { wait("the owner token", || std::fs::read_to_string(dir.0.join("owner.token")).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty())) }
fn now() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() }
fn person_token(dir: &Dir, sub: &str) -> String {
    let secret = std::fs::read_to_string(dir.0.join("door.secret")).unwrap().trim().as_bytes().to_vec();
    membrane_vm::jwt::mint(&secret, sub, sub, 3600.0, now()).unwrap()
}

/// the model's tool: one entity_write on /mcp (the owner's token, as the turn's CLI carries it) -> the reply text
fn write(port: u16, tok: &str, spec: &str, prop: &str, value: J) -> String {
    let (st, b) = j(port, "POST", "/mcp", tok, json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_write", "arguments": {"spec": spec, "prop": prop, "value": value}}}));
    assert_eq!(st, 200, "{b}");
    b["result"]["content"][0]["text"].as_str().unwrap_or("").to_string()
}
/// one property as the model reads it (entity_read): the text of the reply
fn read(port: u16, tok: &str, spec: &str, prop: &str) -> String {
    let (st, b) = j(port, "POST", "/mcp", tok, json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": {"spec": spec, "prop": prop}}}));
    assert_eq!(st, 200, "{b}");
    b["result"]["content"][0]["text"].as_str().unwrap_or("").to_string()
}
fn state(port: u16, tok: &str, id: &str) -> String { read(port, tok, &format!("HaCall:{id}"), "state").trim_matches('"').to_string() }
fn allow(port: u16, tok: &str) {
    write(port, tok, "HaAllow:lights", "services", json!(["light.*", "states.read"]));
    write(port, tok, "HaAllow:lights", "entities", json!(["light.*", "sensor.temp"]));
}
fn light_call(port: u16, tok: &str, id: &str) {
    write(port, tok, &format!("HaCall:{id}"), "request", json!({"domain": "light", "service": "turn_on", "data": {"brightness_pct": 40}, "target": {"entity_id": "light.kitchen"}}));
}
fn calls(port: u16, tok: &str) -> J { let (st, b) = j(port, "GET", "/membrane/ha/calls", tok, J::Null); assert_eq!(st, 200, "{b}"); b["calls"].clone() }

// ---- the lifecycle -----------------------------------------------------------------------------------------------------------------------

#[test]
fn a_call_is_pending_then_running_then_done_and_the_model_reads_the_receipt_on_the_entity() {
    let dir = setup("ha-life");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    allow(port, &own);
    assert_eq!(calls(port, &own), json!([]), "nothing asked yet");
    light_call(port, &own, "k1");
    assert_eq!(state(port, &own, "k1"), "pending");
    assert_eq!(calls(port, &own), json!([{"id": "k1", "request": {"domain": "light", "service": "turn_on", "data": {"brightness_pct": 40}, "target": {"entity_id": "light.kitchen"}}}]));
    // the component claims it: one answer, the call is running and is no longer offered
    let (st, b) = j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "k1", "claim": true}));
    assert_eq!((st, &b["ok"]), (200, &json!(true)), "{b}");
    assert_eq!(state(port, &own, "k1"), "running");
    assert_eq!(calls(port, &own), json!([]));
    // a second claim (another component, a retry) is a 409 and changes nothing
    let (st, b) = j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "k1", "claim": true}));
    assert_eq!(st, 409, "{b}");
    assert_eq!(state(port, &own, "k1"), "running");
    // the result: done, readable where the model reads receipts
    let (st, b) = j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "k1", "result": {"ok": true, "response": {"changed": ["light.kitchen"]}}}));
    assert_eq!((st, &b["ok"]), (200, &json!(true)), "{b}");
    assert_eq!(state(port, &own, "k1"), "done");
    assert_eq!(read(port, &own, "HaCall:k1", "ok").to_lowercase(), "true");
    assert_eq!(serde_json::from_str::<J>(&read(port, &own, "HaCall:k1", "response")).unwrap(), json!({"changed": ["light.kitchen"]}));
    // a second result is a 409 too: the receipt stays the first
    let (st, _) = j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "k1", "result": {"ok": false, "error": "late"}}));
    assert_eq!(st, 409);
    assert_eq!(state(port, &own, "k1"), "done");
}

#[test]
fn a_failed_call_reads_as_an_error_with_the_reason_and_the_entity_is_of_the_class_error() {
    let dir = setup("ha-fail");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    allow(port, &own);
    light_call(port, &own, "f1");
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "f1", "claim": true})).0, 200);
    let (st, b) = j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "f1", "result": {"ok": false, "error": "Service light.turn_on not found"}}));
    assert_eq!(st, 200, "{b}");
    assert_eq!(state(port, &own, "f1"), "error");
    for p in ["response", "ok"] {
        let t = read(port, &own, "HaCall:f1", p);
        assert!(t.contains("Error") && t.contains("Service light.turn_on not found"), "{p}: {t}");
    }
    let (_, q) = j(port, "POST", "/mcp", &own, json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_query", "arguments": {"q": "kind:Error"}}}));
    assert!(q["result"]["content"][0]["text"].as_str().unwrap().contains("HaCall:f1"), "{q}");
}

#[test]
fn a_result_for_a_call_nobody_claimed_or_that_does_not_exist_changes_nothing() {
    let dir = setup("ha-order");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    allow(port, &own);
    light_call(port, &own, "o1");
    let (st, _) = j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "o1", "result": {"ok": true, "response": 1}}));
    assert_eq!(st, 409, "a result before a claim");
    assert_eq!(state(port, &own, "o1"), "pending");
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "nope", "claim": true})).0, 404);
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"claim": true})).0, 400);
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "o1"})).0, 400);
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "o1", "result": {"response": 1}})).0, 400, "ok is required");
    assert_eq!(state(port, &own, "o1"), "pending");
}

// ---- the owner's door -----------------------------------------------------------------------------------------------------------------------

#[test]
fn every_ha_route_is_the_owners_a_person_gets_a_403_and_no_token_a_401_and_nothing_changes() {
    let dir = setup("ha-gate");
    let (_run, port) = launch(&dir);
    let (own, bob) = (owner_token(&dir), person_token(&dir, "bob"));
    allow(port, &own);
    light_call(port, &own, "g1");
    for (m, p, body) in [("GET", "/membrane/ha/calls", J::Null), ("POST", "/membrane/ha/calls", json!({"id": "g1", "claim": true})),
                         ("POST", "/membrane/ha/calls", json!({"id": "g1", "result": {"ok": true}})),
                         ("POST", "/membrane/ha/catalog", json!({"entities": [{"entity_id": "light.a"}], "services": []})), ("GET", "/membrane/ha/metrics", J::Null)] {
        let (st, b) = j(port, m, p, &bob, body.clone());
        assert_eq!((st, &b), (403, &json!({"type": "error", "error": {"message": "for the owner"}})), "{m} {p}");
        let (st, _) = http(port, m, p, None, &body.to_string());
        assert_eq!(st, 401, "{m} {p}: no token");
    }
    assert_eq!(state(port, &own, "g1"), "pending", "a refused claim claimed nothing");
    assert!(read(port, &own, "HaCatalog:ha", "entities").contains("has no"), "a refused catalog wrote nothing");
    // wrong methods answer 405 with the allowed ones
    assert_eq!(j(port, "PUT", "/membrane/ha/calls", &own, json!({})).0, 405);
    assert_eq!(j(port, "POST", "/membrane/ha/metrics", &own, json!({})).0, 405);
}

// ---- the allow-list -------------------------------------------------------------------------------------------------------------------

#[test]
fn with_no_allow_entry_every_call_is_an_error_at_once_and_is_never_offered_to_the_component() {
    let dir = setup("ha-deny");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    light_call(port, &own, "d1");
    assert_eq!(state(port, &own, "d1"), "error");
    let t = read(port, &own, "HaCall:d1", "response");
    assert!(t.contains("not allowed") && t.contains("HaAllow"), "the reason names the remedy: {t}");
    assert_eq!(calls(port, &own), json!([]));
    // a claim of it is refused (a call that is not pending cannot be claimed)
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "d1", "claim": true})).0, 409);
}

#[test]
fn the_allow_list_matches_service_and_every_entity_by_exact_name_or_domain_star_inside_one_entry() {
    let dir = setup("ha-matrix");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    allow(port, &own);
    let call = |id: &str, req: J| { write(port, &own, &format!("HaCall:{id}"), "request", req); state(port, &own, id) };
    assert_eq!(call("a1", json!({"domain": "light", "service": "turn_off", "target": {"entity_id": ["light.a", "light.b"]}})), "pending", "domain star on both sides, a list of entities");
    assert_eq!(call("a2", json!({"domain": "light", "service": "turn_on", "data": {"entity_id": "light.c"}})), "pending", "an entity in data counts the same");
    assert_eq!(call("a3", json!({"domain": "switch", "service": "turn_on", "target": {"entity_id": "switch.fan"}})), "error", "service not on the entry");
    assert_eq!(call("a4", json!({"domain": "light", "service": "turn_on", "target": {"entity_id": ["light.a", "lock.front"]}})), "error", "ONE entity off the entry denies the whole call");
    assert_eq!(call("a5", json!({"domain": "light", "service": "turn_on", "target": {"area_id": "kitchen"}})), "error", "an area expands to entities the list cannot see");
    assert_eq!(call("a6", json!({"domain": "light", "service": "turn_on", "data": {"device_id": "abc"}})), "error", "so does a device in the data");
    assert_eq!(call("a7", json!({"domain": "light", "service": "turn_on", "target": {"entity_id": 5}})), "error", "an entity that is not text");
    assert_eq!(call("a8", json!({"domain": "light"})), "error", "no service");
    assert_eq!(call("a9", "just text".into()), "error", "not an object");
    assert_eq!(call("r1", json!({"read": "sensor.temp"})), "pending", "a read needs `states.read` and the entity");
    assert_eq!(call("r2", json!({"read": "sensor.secret"})), "error", "an entity off the list cannot be read");
    assert_eq!(call("r3", json!({"read": "catalog"})), "pending", "the catalog is what Home Assistant already exposes: no entry needed");
    // the services of one entry and the entities of another do not combine
    write(port, &own, "HaAllow:notify", "services", json!(["notify.mobile"]));
    assert_eq!(call("m1", json!({"domain": "notify", "service": "mobile", "data": {"message": "hi"}})), "pending", "an entity-less service needs only the service");
    assert_eq!(call("m2", json!({"domain": "notify", "service": "mobile", "target": {"entity_id": "light.a"}})), "error", "this entry lists no entities");
    assert_eq!(call("m3", json!({"domain": "light", "service": "turn_on", "target": {"entity_id": "light.a"}})), "pending");
}

#[test]
fn a_call_that_is_running_or_done_is_not_re_gated_when_the_allow_entry_goes_away() {
    let dir = setup("ha-regate");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    allow(port, &own);
    light_call(port, &own, "x1");
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "x1", "claim": true})).0, 200);
    write(port, &own, "HaAllow:lights", "services", json!([]));
    assert_eq!(state(port, &own, "x1"), "running");
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "x1", "result": {"ok": true, "response": "on"}})).0, 200);
    assert_eq!(state(port, &own, "x1"), "done");
}

// ---- the catalog ----------------------------------------------------------------------------------------------------------------------------

#[test]
fn the_component_pushes_the_catalog_and_the_model_reads_entities_domains_and_services_on_one_entity() {
    let dir = setup("ha-catalog");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    let entities: Vec<J> = (0..300).map(|i| json!({"entity_id": format!("light.l{i}"), "name": format!("Lamp {i}")})).chain([json!({"entity_id": "sensor.temp", "name": "Temp"})]).collect();
    let services: Vec<J> = (0..400).map(|i| json!({"id": format!("light.s{i}"), "target": true, "fields": ["brightness_pct", "color_name"]})).chain([json!({"id": "notify.mobile", "target": false, "fields": ["message"]})]).collect();
    let (st, b) = j(port, "POST", "/membrane/ha/catalog", &own, json!({"entities": entities, "services": services}));
    assert_eq!((st, &b["ok"], &b["entities"], &b["services"]), (200, &json!(true), &json!(301), &json!(401)), "{b}");
    assert_eq!(read(port, &own, "HaCatalog:ha", "domains"), r#"["light","sensor"]"#, "domains of the entities");
    assert!(read(port, &own, "HaCatalog:ha", "service_ids").contains("notify.mobile"));
    assert!(read(port, &own, "HaCatalog:ha", "entity_ids").contains("sensor.temp"));
    // refused shapes
    for body in [json!({}), json!({"entities": "x", "services": []}), json!({"entities": [{"name": "no id"}], "services": []}), json!({"entities": [], "services": [{"target": true}]})] {
        let (st, b) = j(port, "POST", "/membrane/ha/catalog", &own, body.clone());
        assert_eq!(st, 400, "{body}: {b}");
    }
    // the refused ones left the catalog as it was
    assert!(read(port, &own, "HaCatalog:ha", "entity_ids").contains("light.l299"));
}

// ---- the journal ----------------------------------------------------------------------------------------------------------------------------

#[test]
fn a_restart_replays_the_calls_as_they_were_nothing_runs_again_and_a_pending_call_is_still_offered() {
    let dir = setup("ha-replay");
    let own;
    {
        let (_run, port) = launch(&dir);
        own = owner_token(&dir);
        allow(port, &own);
        light_call(port, &own, "p1");                // stays pending
        light_call(port, &own, "p2");                // runs and stays running
        light_call(port, &own, "p3");                // done
        light_call(port, &own, "p4");                // failed
        for id in ["p2", "p3", "p4"] { assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": id, "claim": true})).0, 200); }
        assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "p3", "result": {"ok": true, "response": {"n": 1}}})).0, 200);
        assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "p4", "result": {"ok": false, "error": "boom"}})).0, 200);
        let (st, _) = j(port, "POST", "/membrane/ha/catalog", &own, json!({"entities": [{"entity_id": "light.a"}], "services": [{"id": "light.turn_on"}]}));
        assert_eq!(st, 200);
        stop(_run);
    }
    let (_run, port) = launch(&dir);
    let own = { let _ = own; owner_token(&dir) };
    assert_eq!(state(port, &own, "p1"), "pending");
    assert_eq!(state(port, &own, "p2"), "running", "a call the component took is never offered again: it may have run");
    assert_eq!(state(port, &own, "p3"), "done");
    assert_eq!(serde_json::from_str::<J>(&read(port, &own, "HaCall:p3", "response")).unwrap(), json!({"n": 1}));
    assert_eq!(state(port, &own, "p4"), "error");
    assert!(read(port, &own, "HaCall:p4", "response").contains("boom"));
    let ids: Vec<String> = calls(port, &own).as_array().unwrap().iter().map(|c| c["id"].as_str().unwrap().to_string()).collect();
    assert_eq!(ids, vec!["p1".to_string()], "only the pending one is offered, and the replay re-ran nothing");
    assert!(read(port, &own, "HaCatalog:ha", "entity_ids").contains("light.a"));
    // and the allow entry survived, so the pending one is claimable
    assert_eq!(j(port, "POST", "/membrane/ha/calls", &own, json!({"id": "p1", "claim": true})).0, 200);
}

// ---- the metrics: the seat's own sensors ------------------------------------------------------------------------------------------------

fn row(rows: &J, key: &str) -> J { rows.as_array().unwrap().iter().find(|r| r["key"] == key).unwrap_or_else(|| panic!("no row {key} in {rows}")).clone() }
fn turn(port: u16, tok: &str, sid: &str, text: &str) {
    let (st, b) = j(port, "POST", &format!("/session/{sid}/message"), tok, json!({"agent": "build", "parts": [{"type": "text", "text": text}]}));
    assert_eq!(st, 200, "{b}");
}

#[test]
fn the_metrics_rows_say_what_the_seat_really_knows_the_model_the_turns_the_last_answer_and_the_last_error() {
    let dir = setup("ha-metrics");
    let (_run, port) = launch(&dir);
    let own = owner_token(&dir);
    let rows = |port: u16| { let (st, b) = j(port, "GET", "/membrane/ha/metrics", &own, J::Null); assert_eq!(st, 200, "{b}"); b["rows"].clone() };
    let r = rows(port);
    assert_eq!(r.as_array().unwrap().iter().map(|x| x["key"].as_str().unwrap()).collect::<Vec<_>>(), ["busy", "model", "turns_today", "last_answer", "last_error"], "the whole honest set, in this order");
    assert_eq!((row(&r, "busy")["value"].clone(), row(&r, "busy")["platform"].clone()), (json!(false), json!("binary_sensor")));
    assert_eq!(row(&r, "model")["value"], "claude");
    assert_eq!(row(&r, "turns_today")["value"], 0);
    assert_eq!((row(&r, "last_answer")["value"].clone(), row(&r, "last_answer")["device_class"].clone()), (J::Null, json!("timestamp")));
    assert_eq!(row(&r, "last_error")["value"], J::Null);
    let (_, s) = j(port, "POST", "/session", &own, json!({"title": "m"}));
    let sid = s["id"].as_str().unwrap().to_string();
    turn(port, &own, &sid, "hello");
    let r = rows(port);
    assert_eq!(row(&r, "turns_today")["value"], 1);
    let at = row(&r, "last_answer")["value"].as_f64().unwrap();
    assert!((now() - at).abs() < 60.0, "{at}");
    turn(port, &own, &sid, "this will fail");
    let r = rows(port);
    assert_eq!(row(&r, "turns_today")["value"], 2, "a failed turn is a turn");
    assert!(row(&r, "last_error")["value"].as_str().unwrap().contains("fake: not logged in"), "{r}");
    assert!(row(&r, "last_error")["attributes"]["at"].as_f64().unwrap() > 1.0e9);
    assert_eq!(row(&r, "last_answer")["value"].as_f64().unwrap(), at, "the failed turn is not an answer");
    // the model the page's post set is the model the row names
    assert_eq!(j(port, "POST", "/membrane/options", &own, json!({"loops": "claude-agent:sonnet"})).0, 200);
    assert_eq!(row(&rows(port), "model")["value"], "claude-agent:sonnet");
}
