//! The seat's default model backend is the local Ollama (#1143, operator 2026-11-16): with no model facts the chat's model is UNSET until the person picks one from what the backend lists;
//! the `claude` CLI is a developer mode told with `--fact seat_default=claude`. In-process (no seat process, no real model): the launcher's defaults, the settings feed (`GET /ui/settings`:
//! the model row's choices, `chosen` for the first-run window), the model row's refusals and the turn that has no model to run.
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::io::{Read, Write as IoWrite};
use std::net::TcpListener;

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn backend(body: &'static str) -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for _ in 0..8 {
            let Ok((mut s, _)) = l.accept() else { return };
            let mut buf = [0u8; 4096];
            let _ = s.read(&mut buf);
            let r = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
            let _ = s.write_all(r.as_bytes());
        }
    });
    port
}

fn root() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf() }

/// the seat's packs the touched area needs, with the launcher: the facts a test names are the ONLY facts (the launcher fills the rest)
fn world(facts: J) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for p in ["boot", "door", "fetch", "router", "launcher", "settings"] {
        let text = std::fs::read_to_string(root().join("membrane/packs").join(format!("{p}.c1f"))).unwrap();
        f.set_pack(p, &text).unwrap_or_else(|e| panic!("{p}: {e:?}"));
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    let mut props = json!({"ui_dir": "/x"}).as_object().unwrap().clone();
    for (k, v) in facts.as_object().unwrap() { props.insert(k.clone(), v.clone()); }
    c.load("Boot:seat".into(), "Boot".into(), props, None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    let boot = Write { eid: "Boot:seat".into(), prop: "started".into(), new: json!(5), old: J::Null, writer: "person".into(), chain: json!({}), seq: 4 };
    c.hear(boot.clone(), None, 2.0);
    f.react(&mut c, &H, boot, 2.0, &mut s);
    f.settle(&mut c, &H, 3.0, &mut s);
    (c, f)
}

fn put(c: &mut Core, f: &mut Fields, eid: &str, prop: &str, v: J) {
    let w = Write { eid: eid.into(), prop: prop.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 4.0);
    let mut s = 30;
    f.react(c, &H, w, 4.0, &mut s);
    f.settle(c, &H, 5.0, &mut s);
}
fn get(c: &Core, eid: &str, prop: &str) -> J { c.docs.get(eid).and_then(|d| d.props.get(prop)).cloned().unwrap_or(J::Null) }
fn rungs(c: &Core) -> Vec<String> { c.docs.iter().filter(|(k, d)| k.starts_with("Model:") && d.props.get("rung") == Some(&json!(true))).map(|(k, _)| k.clone()).collect() }

fn arrive(c: &mut Core, n: u32) {
    c.store.as_ref().unwrap().push_inbound(format!("Request:{n}"), "Request".into(), json!({"method": "GET", "query": "", "headers": {}, "body": "",
        "peer": "127.0.0.1:5", "authed": true, "sub": "admin", "claims": {"sub": "admin"}, "path": "/ui/settings"}).as_object().unwrap().clone());
}
fn until_reply(c: &mut Core, f: &mut Fields, n: u32) -> J {
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    let mut s = 30;
    while t.elapsed().as_secs() < 10 {
        f.settle(c, &H, 6.0, &mut s);
        if let Some(r) = c.docs[&format!("Request:{n}")].props.get("reply").filter(|r| !r.is_null()) { return r.clone(); }
        wt.wait(Some(std::time::Duration::from_millis(50)));
    }
    panic!("no reply");
}
fn feed(c: &mut Core, f: &mut Fields, n: u32) -> J { arrive(c, n); until_reply(c, f, n) }
fn row(r: &J, id: &str) -> J { r["body"]["settings"].as_array().unwrap().iter().find(|x| x["id"] == json!(id)).unwrap_or_else(|| panic!("no row {id}")).clone() }

const TAGS: &str = r#"{"models":[{"name":"glm-4:9b","size":5000000000,"capabilities":["completion","tools"]},{"name":"mxbai-embed-large:latest","size":670000000}]}"#;

#[test]
fn with_no_model_facts_the_chat_model_is_unset_and_no_claude_rung_exists() {
    let (c, _f) = world(json!({"seat_backend": "http://127.0.0.1:1"}));
    assert_eq!(get(&c, "Minds:seat", "loops"), json!(""), "no model picked");
    assert_eq!(get(&c, "Minds:seat", "seat_default"), json!(""));
    assert!(rungs(&c).is_empty(), "no claude rung: {:?}", rungs(&c));
}

#[test]
fn the_developer_mode_fact_seat_default_claude_picks_the_claude_cli_and_registers_its_rung() {
    let (c, _f) = world(json!({"seat_backend": "http://127.0.0.1:1", "seat_default": "claude"}));
    assert_eq!(get(&c, "Minds:seat", "loops"), json!("claude"));
    assert_eq!(get(&c, "Minds:seat", "seat_default"), json!("claude"));
    assert_eq!(rungs(&c), vec!["Model:claude".to_string()]);
}

#[test]
fn a_minds_fact_still_wins_over_the_defaults_as_home_assistant_passes_it() {
    let (c, _f) = world(json!({"seat_backend": "http://127.0.0.1:1", "minds": {"loops": "qwen3:8b", "cortex": "qwen3:8b", "workers": "qwen3:8b", "seat_default": "qwen3:8b"}}));
    assert_eq!(get(&c, "Minds:seat", "loops"), json!("qwen3:8b"));
    assert!(rungs(&c).is_empty());
}

#[test]
fn the_feed_lists_only_the_backends_chat_models_and_says_what_is_chosen() {
    let port = backend(TAGS);
    let (mut c, mut f) = world(json!({"seat_backend": format!("http://127.0.0.1:{port}")}));
    let r = feed(&mut c, &mut f, 1);
    let m = row(&r, "model.chat");
    assert_eq!(m["kind"], json!("model"), "a dropdown row");
    assert_eq!(m["choices"], json!(["glm-4:9b"]), "the embedding model and the claude rung are not listed: {m}");
    assert_eq!(m["chosen"], json!(false));
    assert_eq!(row(&r, "chat.name")["chosen"], json!(false), "the default Bob is not a chosen name");
    // a pick through the settings door
    put(&mut c, &mut f, "Setting:model.chat", "set", json!("glm-4:9b"));
    put(&mut c, &mut f, "Setting:chat.name", "set", json!("Rosie"));
    let r = feed(&mut c, &mut f, 2);
    assert_eq!(row(&r, "model.chat")["chosen"], json!(true));
    assert_eq!(row(&r, "model.chat")["current"], json!("glm-4:9b"));
    assert_eq!(row(&r, "chat.name")["chosen"], json!(true));
}

#[test]
fn a_seat_name_fact_counts_as_a_chosen_name() {
    let (mut c, mut f) = world(json!({"seat_backend": "http://127.0.0.1:1", "seat_name": "Blurp"}));
    let r = feed(&mut c, &mut f, 1);
    assert_eq!(row(&r, "chat.name")["chosen"], json!(true));
    assert_eq!(row(&r, "chat.name")["current"], json!("Blurp"), "the window prefills it");
}

#[test]
fn an_unreachable_backend_gives_an_empty_list_and_the_feed_still_answers() {
    let (mut c, mut f) = world(json!({"seat_backend": "http://127.0.0.1:1"}));
    let r = feed(&mut c, &mut f, 1);
    assert_eq!(r["status"], json!(200));
    assert_eq!(row(&r, "model.chat")["choices"], json!([]));
    assert_eq!(row(&r, "model.chat")["chosen"], json!(false));
}

#[test]
fn the_model_row_refuses_a_bad_name_with_the_seats_reason_and_keeps_the_old_pick() {
    let (mut c, mut f) = world(json!({"seat_backend": "http://127.0.0.1:1"}));
    put(&mut c, &mut f, "Setting:model.chat", "set", json!("glm-4:9b"));
    put(&mut c, &mut f, "Setting:model.chat", "set", json!("has a space"));
    assert!(get(&c, "Setting:model.chat", "refused").as_str().unwrap().contains("not a model name"), "{:?}", get(&c, "Setting:model.chat", "refused"));
    assert_eq!(get(&c, "Setting:model.chat", "current"), json!("glm-4:9b"));
}

// ---- the turn with no model: nothing starts, the answer says so ----
fn full_world(facts: J) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    let list = std::fs::read_to_string(root().join("membrane/apps/barest.packs")).unwrap();
    for p in list.lines().map(|l| l.trim()).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        let text = std::fs::read_to_string(root().join("membrane/packs").join(format!("{p}.c1f"))).unwrap();
        f.set_pack(p, &text).unwrap_or_else(|e| panic!("{p}: {e:?}"));
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    let mut props = json!({"ui_dir": "/x", "chat_claude_bin": "/nonexistent/claude", "seat_port": 1}).as_object().unwrap().clone();
    for (k, v) in facts.as_object().unwrap() { props.insert(k.clone(), v.clone()); }
    c.load("Boot:seat".into(), "Boot".into(), props, None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    let boot = Write { eid: "Boot:seat".into(), prop: "started".into(), new: json!(5), old: J::Null, writer: "person".into(), chain: json!({}), seq: 4 };
    c.hear(boot.clone(), None, 2.0);
    f.react(&mut c, &H, boot, 2.0, &mut s);
    f.settle(&mut c, &H, 3.0, &mut s);
    (c, f)
}
fn ask_chat(c: &mut Core, f: &mut Fields) -> J {
    c.store.as_ref().unwrap().push_inbound("Request:1".into(), "Request".into(), json!({"method": "POST", "query": "", "headers": {"content-type": "application/json"}, "body": "{\"text\":\"hello\"}",
        "peer": "127.0.0.1:5", "authed": true, "sub": "admin", "claims": {"sub": "admin"}, "path": "/membrane/chat/message"}).as_object().unwrap().clone());
    let mut s = 30;
    let t = std::time::Instant::now();
    while t.elapsed().as_secs() < 10 {
        f.settle(c, &H, 8.0, &mut s);
        if let Some((_, d)) = c.docs.iter().find(|(k, _)| k.ends_with(":turn:response")) { return J::Object(d.props.clone().into_iter().collect()); }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    J::Null
}

#[test]
fn a_message_with_no_model_chosen_starts_no_process_and_says_how_to_pick_one() {
    let (mut c, mut f) = full_world(json!({"seat_backend": "http://127.0.0.1:1"}));
    let r = ask_chat(&mut c, &mut f);
    let t = r["text"].as_str().unwrap_or_else(|| panic!("no answer: {r}"));
    assert!(t.contains("No model is chosen") && t.contains("picker"), "{t}");
    assert!(t.contains("ollama pull"), "an empty list says how to get a model: {t}");
    assert_eq!(r["error"], json!(true));
    assert!(!c.docs.keys().any(|k| k.starts_with("Command:chat") || k.contains(":Command:chat")), "no process was asked for");
}

#[test]
fn the_launch_fact_seat_default_alone_counts_as_a_chosen_model_on_a_seat_with_no_launcher_pack() {
    let mut f = Fields::goalgraph();
    for p in ["boot", "door", "fetch", "router", "settings"] {
        f.set_pack(p, &std::fs::read_to_string(root().join("membrane/packs").join(format!("{p}.c1f"))).unwrap()).unwrap();
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Boot:seat".into(), "Boot".into(), json!({"ui_dir": "/x", "seat_backend": "http://127.0.0.1:1", "seat_default": "claude", "seat_name": "Rosie"}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    let boot = Write { eid: "Boot:seat".into(), prop: "started".into(), new: json!(5), old: J::Null, writer: "person".into(), chain: json!({}), seq: 4 };
    c.hear(boot.clone(), None, 2.0);
    f.react(&mut c, &H, boot, 2.0, &mut s);
    let r = feed(&mut c, &mut f, 1);
    assert_eq!(row(&r, "model.chat")["current"], json!("claude"));
    assert_eq!(row(&r, "model.chat")["chosen"], json!(true), "the first-run window stays closed");
}

// ---- the rung named `claude` is the CLI's own default: it runs with NO --model flag, whatever the seat's default is ----
fn chat_argv(c: &Core) -> Vec<String> {
    let cmd = c.docs.iter().find(|(k, _)| k.starts_with("membrane::Command:chat:")).unwrap_or_else(|| panic!("no process was asked for"));
    cmd.1.props["cmd"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}
fn picked_world(pick: &str) -> Vec<String> {
    // a seat whose store has the `claude` rung and the claude-agent:sonnet rung, with the launcher's empty minds (seat_default NOT set); the pick is the Settings row's own write
    let (mut c, mut f) = full_world(json!({"seat_backend": "http://127.0.0.1:1", "chat_auth": "login",
        "cloud_models": [{"name": "claude", "backend": "claude-agent"}, {"name": "claude-agent:sonnet", "backend": "claude-agent"}],
        "minds": {"loops": "", "cortex": "", "workers": "", "seat_default": "", "loops_auth": "chat", "cortex_auth": "chat", "workers_auth": "chat", "think": "auto"}}));
    c.load("Http:door".into(), "Http".into(), json!({"listen_result": {"port": 4321, "rc": 0, "host": "127.0.0.1"}}).as_object().unwrap().clone(), None, 3.0);
    put(&mut c, &mut f, "Setting:model.chat", "set", json!(pick));
    assert_eq!(get(&c, "Minds:seat", "loops"), json!(pick));
    ask_chat(&mut c, &mut f);
    chat_argv(&c)
}

#[test]
fn a_pick_of_the_claude_rung_runs_the_cli_with_no_model_flag_even_when_the_seat_default_is_not_set() {
    let argv = picked_world("claude");
    assert!(argv.iter().any(|a| a == "-p"), "the CLI was run: {argv:?}");
    assert!(!argv.iter().any(|a| a == "--model"), "no --model flag for `claude`: {argv:?}");
}

#[test]
fn a_claude_agent_alias_still_runs_with_its_model_flag() {
    let argv = picked_world("claude-agent:sonnet");
    let i = argv.iter().position(|a| a == "--model").unwrap_or_else(|| panic!("no --model: {argv:?}"));
    assert_eq!(argv[i + 1], "sonnet");
}
