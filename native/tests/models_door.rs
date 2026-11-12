//! The models' routes end to end (membrane/packs/router.c1f + door_models.c1f): a request for /v1/models makes the pack ask the
//! backend for its tags (the Fetch driver, a real socket), the rows become Model entities and the reply is built from them.
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host};
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
    for p in ["boot", "door", "fetch", "router", "door_models"] {
        let text = std::fs::read_to_string(root().join("membrane/packs").join(format!("{p}.c1f"))).unwrap();
        f.set_pack(p, &text).unwrap();
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Boot:seat".into(), "Boot".into(), json!({"ui_dir": "/x", "seat_backend": backend_url, "router_on": true,
        "cloud_models": [{"name": "claude-sonnet", "backend": "claude-agent"}]}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    (c, f)
}

/// the request arrives as the door's inbound entity does (born in the engine's next round): the pack's setters see it
fn arrive(c: &mut Core) {
    c.store.as_ref().unwrap().push_inbound("Request:1".into(), "Request".into(), json!({"method": "GET", "query": "", "headers": {}, "body": "",
        "peer": "127.0.0.1:5", "authed": false, "auth_error": "no token", "path": "/v1/models"}).as_object().unwrap().clone());
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

fn ids(r: &J) -> Vec<String> { r["body"]["data"].as_array().unwrap().iter().map(|m| m["id"].as_str().unwrap().to_string()).collect() }

#[test]
fn the_list_comes_from_the_backends_tags_screened_with_the_lanes_and_rungs() {
    let (port, rx) = backend(r#"{"models":[{"name":"glm-4:9b","size":5000000000,"capabilities":["completion","tools"]},{"name":"nomic-embed-text:latest","size":270000000},{"name":"huge:70b","size":40000000000},{"name":"kimi:cloud","size":0,"remote_host":"https://x"}]}"#);
    let (mut c, mut f) = world(&format!("http://127.0.0.1:{port}"));
    let mut s = 5;
    let boot = membrane_vm::vm::Write { eid: "Boot:seat".into(), prop: "started".into(), new: json!(5), old: J::Null, writer: "person".into(), chain: json!({}), seq: 4 };
    c.hear(boot.clone(), None, 2.0);
    f.react(&mut c, &H, boot, 2.0, &mut s);
    arrive(&mut c);
    let r = until_reply(&mut c, &mut f);
    assert_eq!(r["status"], json!(200), "{r}");
    let got = ids(&r);
    assert!(got.contains(&"glm-4:9b".to_string()) && got.contains(&"kimi:cloud".to_string()), "{got:?}");
    assert!(!got.contains(&"nomic-embed-text:latest".to_string()) && !got.contains(&"huge:70b".to_string()), "{got:?}");
    assert_eq!(&got[got.len() - 3..], &["autoselect", "autoselect-local", "claude-sonnet"], "{got:?}");
    assert!(got.contains(&"@explore".to_string()));
    let seen = rx.recv().unwrap();
    assert!(seen.starts_with("GET /api/tags HTTP/1.1"), "{seen}");
}

#[test]
fn an_unreachable_backend_lists_no_tags_but_the_request_still_gets_its_answer() {
    let (mut c, mut f) = world("http://127.0.0.1:1");
    arrive(&mut c);
    let r = until_reply(&mut c, &mut f);
    assert_eq!(r["status"], json!(200), "{r}");
    assert_eq!(ids(&r), vec!["@explore", "autoselect", "autoselect-local"]);
}

#[test]
fn every_shipped_pack_loads_together_in_file_order_with_no_pack_left_waiting() {
    let mut names: Vec<_> = std::fs::read_dir(root().join("membrane/packs")).unwrap().filter_map(|e| e.ok())
        .filter_map(|e| e.path().file_stem().map(|s| (s.to_string_lossy().to_string(), e.path()))).filter(|(_, p)| p.extension().map(|x| x == "c1f").unwrap_or(false)).collect();
    names.sort();
    let mut f = Fields::goalgraph();
    for (n, p) in &names { let _ = f.set_pack(n, &std::fs::read_to_string(p).unwrap()); }
    for want in ["fetch", "router", "door_models"] { assert!(names.iter().any(|(n, _)| n == want), "{want} ships"); }
    let mine: Vec<_> = f.pack_errors.iter().filter(|(n, _)| ["fetch", "router", "door_models"].contains(&n.as_str())).collect();
    assert!(mine.is_empty(), "{mine:?}");
}
