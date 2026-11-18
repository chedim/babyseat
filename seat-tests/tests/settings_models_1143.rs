//! The Settings window's model row (#1143, operator 2026-11-15: "why are the models gone from the dropdown?"): the feed `GET /ui/settings` asks the backend for its model list the way
//! `GET /v1/models` does, so the chat model row's `choices` (the page shows a select when it is not empty) lists the backend's chat models and the rungs without anything else asking first.
//! An embedding model is not a chat choice. An unreachable backend leaves the list as it was and the feed still answers.
use cliffvm::fields::Fields;
use cliffvm::store::Store;
use cliffvm::vm::{Core, Host};
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

fn backend(body: &'static str) -> (u16, std::sync::mpsc::Receiver<String>) {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let (mut s, _) = l.accept().unwrap();
        let mut buf = [0u8; 4096];
        let n = s.read(&mut buf).unwrap();
        tx.send(String::from_utf8_lossy(&buf[..n]).to_string()).unwrap();
        let r = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
        s.write_all(r.as_bytes()).unwrap();
    });
    (port, rx)
}

fn root() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf() }

fn world(backend_url: &str) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for p in ["boot", "door", "fetch", "router", "settings"] {
        let text = std::fs::read_to_string(root().join("membrane/packs").join(format!("{p}.c1f"))).unwrap();
        f.set_pack(p, &text).unwrap_or_else(|e| panic!("{p}: {e:?}"));
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Boot:seat".into(), "Boot".into(), json!({"ui_dir": "/x", "seat_backend": backend_url, "router_on": false,
        "cloud_models": [{"name": "claude", "backend": "claude-agent"}]}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    let boot = cliffvm::vm::Write { eid: "Boot:seat".into(), prop: "started".into(), new: json!(5), old: J::Null, writer: "person".into(), chain: json!({}), seq: 4 };
    c.hear(boot.clone(), None, 2.0);
    f.react(&mut c, &H, boot, 2.0, &mut s);
    (c, f)
}

fn arrive(c: &mut Core) {
    c.store.as_ref().unwrap().push_inbound("Request:1".into(), "Request".into(), json!({"method": "GET", "query": "", "headers": {}, "body": "",
        "peer": "127.0.0.1:5", "authed": true, "sub": "admin", "claims": {"sub": "admin"}, "path": "/ui/settings"}).as_object().unwrap().clone());
}

fn until_reply(c: &mut Core, f: &mut Fields) -> J {
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    let mut s = 30;
    while t.elapsed().as_secs() < 10 {
        f.settle(c, &H, 3.0, &mut s);
        if let Some(r) = c.docs["Request:1"].props.get("reply").filter(|r| !r.is_null()) { return r.clone(); }
        wt.wait(Some(std::time::Duration::from_millis(50)));
    }
    panic!("no reply: router {:?} fetch {:?}", c.docs.get("Router:seat").map(|d| &d.props), c.docs.get("Fetch:tags").map(|d| &d.props));
}

fn row(r: &J, id: &str) -> J { r["body"]["settings"].as_array().unwrap().iter().find(|x| x["id"] == json!(id)).unwrap_or_else(|| panic!("no row {id}")).clone() }

const TAGS: &str = r#"{"models":[{"name":"glm-4:9b","size":5000000000,"capabilities":["completion","tools"]},{"name":"nomic-embed-text:latest","size":270000000},{"name":"kimi:cloud","remote_host":"x"}]}"#;

#[test]
fn the_chat_model_row_lists_the_backends_chat_models_and_the_rungs_the_first_time_the_feed_is_read() {
    let (port, rx) = backend(TAGS);
    let (mut c, mut f) = world(&format!("http://127.0.0.1:{port}"));
    arrive(&mut c);
    let r = until_reply(&mut c, &mut f);
    assert_eq!(r["status"], json!(200), "{r}");
    let choices: Vec<String> = row(&r, "model.chat")["choices"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect();
    assert!(choices.contains(&"glm-4:9b".to_string()) && choices.contains(&"kimi:cloud".to_string()), "{choices:?}");
    assert!(choices.contains(&"claude".to_string()), "the rung the seat runs stays in the list: {choices:?}");
    assert!(rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap().starts_with("GET /api/tags HTTP/1.1"), "the feed asked the backend");
}

#[test]
fn an_embedding_model_is_not_a_chat_choice() {
    let (port, _rx) = backend(TAGS);
    let (mut c, mut f) = world(&format!("http://127.0.0.1:{port}"));
    arrive(&mut c);
    let r = until_reply(&mut c, &mut f);
    let choices = row(&r, "model.chat")["choices"].to_string();
    assert!(!choices.contains("nomic-embed-text"), "{choices}");
    assert!(!row(&r, "model.list")["current"].to_string().contains("nomic-embed-text"), "the same list");
}

#[test]
fn an_unreachable_backend_leaves_the_rungs_and_the_feed_still_answers() {
    let (mut c, mut f) = world("http://127.0.0.1:1");
    arrive(&mut c);
    let r = until_reply(&mut c, &mut f);
    assert_eq!(r["status"], json!(200), "{r}");
    assert_eq!(row(&r, "model.chat")["choices"], json!(["claude"]));
}
