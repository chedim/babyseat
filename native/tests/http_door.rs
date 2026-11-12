//! The HTTP door (http.rs; operator 2026-11-12, cliff talks to rust directly): `Http:<name>.listen = {port}` binds, every
//! request is an entity `Request:<n>` the engine is told of, a pack's getter DECIDES the answer, and `Http:<name>.respond`
//! (written on the request by a setter) sends it. No Python anywhere in the path.
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};
use std::io::{Read, Write as IoWrite};
use std::net::TcpStream;

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

const PACK: &str = "PACK web 1
Request.reply getter:
  WHEN this.path == '/missing': {'status': 404, 'body': 'no ' + this.path}
  ELSE: {'status': 200, 'headers': {'x-seen': this.method}, 'body': 'hello ' + this.path + ' ' + this.query + ' ' + this.body}
Request.reply on-setter answer:
  ALWAYS:
    this.respond = this.reply
Request.who getter: ('owner ' + this.sub) if this.authed else ('nobody: ' + str(this.auth_error))
Http.kind getter: 'http'
";

fn world() -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("web", PACK).unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Http:door".into(), "Http".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, p: &str, v: J) {
    let w = Write { eid: "Http:door".into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
/// run engine rounds until `done` says so (10 s at most)
fn pump(c: &mut Core, f: &mut Fields, mut done: impl FnMut(&Core) -> bool) {
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    let mut s = 30;
    while t.elapsed().as_secs() < 10 {
        f.settle(c, &H, 3.0, &mut s);
        if done(c) { return; }
        wt.wait(Some(std::time::Duration::from_millis(50)));
    }
}
fn ask(port: u64, raw: String) -> std::sync::mpsc::Receiver<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut s = TcpStream::connect(("127.0.0.1", port as u16)).unwrap();
        s.write_all(raw.as_bytes()).unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        tx.send(out).unwrap();
    });
    rx
}

#[test]
fn a_request_is_an_entity_a_pack_answers_and_the_door_sends_it() {
    let (mut c, mut f) = world();
    put(&mut c, &mut f, "listen", json!({"id": "l1", "port": 0}));
    pump(&mut c, &mut f, |c| c.docs["Http:door"].props.get("listen_result").is_some());
    let port = c.docs["Http:door"].props["listen_result"]["port"].as_u64().expect("a port");
    assert!(port > 0);
    let rx = ask(port, "POST /x?y=1 HTTP/1.1\r\nHost: t\r\nContent-Length: 3\r\n\r\nabc".into());
    let mut got: Option<String> = None;
    pump(&mut c, &mut f, |_| { if got.is_none() { got = rx.try_recv().ok(); } got.is_some() });
    let r = got.expect("the client got an answer");
    assert!(r.starts_with("HTTP/1.1 200 OK"), "{r}");
    assert!(r.contains("x-seen: POST"), "{r}");
    assert!(r.ends_with("hello /x y=1 abc"), "{r}");
    let req = c.docs.iter().find(|(k, _)| k.starts_with("Request:")).map(|(_, e)| e.props.clone()).expect("the request is an entity");
    assert_eq!(req["method"], json!("POST"));
    assert_eq!(req["path"], json!("/x"));
    assert_eq!(req["headers"]["host"], json!("t"));
}

#[test]
fn the_pack_decides_the_status_and_a_second_request_is_its_own_entity() {
    let (mut c, mut f) = world();
    put(&mut c, &mut f, "listen", json!({"id": "l1", "port": 0}));
    pump(&mut c, &mut f, |c| c.docs["Http:door"].props.get("listen_result").is_some());
    let port = c.docs["Http:door"].props["listen_result"]["port"].as_u64().unwrap();
    let a = ask(port, "GET /missing HTTP/1.1\r\nHost: t\r\n\r\n".into());
    let b = ask(port, "GET /ok HTTP/1.1\r\nHost: t\r\n\r\n".into());
    let (mut ra, mut rb) = (None, None);
    pump(&mut c, &mut f, |_| { if ra.is_none() { ra = a.try_recv().ok(); } if rb.is_none() { rb = b.try_recv().ok(); } ra.is_some() && rb.is_some() });
    assert!(ra.unwrap().starts_with("HTTP/1.1 404 Not Found"));
    assert!(rb.unwrap().contains("hello /ok"));
    assert_eq!(c.docs.keys().filter(|k| k.starts_with("Request:")).count(), 2);
}

#[test]
fn a_request_without_a_connection_cannot_be_answered() {
    let (mut c, mut f) = world();
    c.load("Request:900".into(), "Request".into(), json!({"path": "/z", "method": "GET", "query": "", "body": ""}).as_object().unwrap().clone(), None, 0.0);
    let w = Write { eid: "Request:900".into(), prop: "respond".into(), new: json!({"id": "r9", "status": 200}), old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(&mut c, &H, w, 2.0, &mut seq);
    pump(&mut c, &mut f, |c| c.docs["Request:900"].props.get("respond_result").is_some());
    assert_eq!(c.docs["Request:900"].props["respond_rc"], json!(2));
}

const GOOD: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvd25lciIsImV4cCI6NDEwMjQ0NDgwMCwiaWF0IjoxNzAwMDAwMDAwLCJuYW1lIjoiT3duZXIifQ.AaVTM8jSb9mpYU5XQGs0zoUVw2_kMPZ-lSDhe6s9YTw";

#[test]
fn the_door_verifies_the_token_and_a_pack_reads_who() {
    std::env::set_var("DOOR_TEST_SECRET", "0123456789abcdef0123456789abcdef");
    let (mut c, mut f) = world();
    put(&mut c, &mut f, "listen", json!({"id": "l1", "port": 0, "secret_env": "DOOR_TEST_SECRET"}));
    pump(&mut c, &mut f, |c| c.docs["Http:door"].props.get("listen_result").is_some());
    let port = c.docs["Http:door"].props["listen_result"]["port"].as_u64().unwrap();
    let a = ask(port, format!("GET /a HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {GOOD}\r\n\r\n"));
    let b = ask(port, "GET /b HTTP/1.1\r\nHost: t\r\n\r\n".into());
    let c2 = ask(port, format!("GET /c?token={GOOD} HTTP/1.1\r\nHost: t\r\n\r\n"));
    let (mut ra, mut rb, mut rc) = (None, None, None);
    pump(&mut c, &mut f, |_| { if ra.is_none() { ra = a.try_recv().ok(); } if rb.is_none() { rb = b.try_recv().ok(); } if rc.is_none() { rc = c2.try_recv().ok(); } ra.is_some() && rb.is_some() && rc.is_some() });
    let who = |path: &str| c.docs.values().find(|e| e.props.get("path") == Some(&json!(path))).map(|e| e.props["who"].clone()).unwrap();
    assert_eq!(who("/a"), json!("owner owner"));
    assert_eq!(who("/b"), json!("nobody: no token"));
    assert_eq!(who("/c"), json!("nobody: no token"), "a query-string token is never read");
}
