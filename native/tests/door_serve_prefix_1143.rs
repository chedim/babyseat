//! The UI shell and its files through the door, DECIDED IN c1f (packs/door.c1f: the route table, the content types, the path
//! rules); rust only sends the file the pack names (`Request:<n>.respond = {status, headers, file}`).
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

const DOOR: &str = include_str!("../../membrane/packs/door.c1f");
const SECRET: &str = "0123456789abcdef0123456789abcdef";

fn ui_dir() -> String { format!("{}/../membrane/ui", env!("CARGO_MANIFEST_DIR")) }

fn world() -> (Core, Fields, u64) {
    let mut f = Fields::goalgraph();
    f.set_pack("boot", include_str!("../../membrane/packs/boot.c1f")).unwrap();
    f.set_pack("door", DOOR).unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Http:door".into(), "Http".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    c.load("Boot:seat".into(), "Boot".into(), json!({"ui_dir": ui_dir()}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    std::env::set_var("DOOR_PREFIX_SECRET", SECRET);
    put(&mut c, &mut f, "Http:door", "listen", json!({"id": "l1", "port": 0, "secret_env": "DOOR_PREFIX_SECRET"}));
    pump(&mut c, &mut f, |c| c.docs["Http:door"].props.get("listen_result").is_some());
    let port = c.docs["Http:door"].props["listen_result"]["port"].as_u64().unwrap();
    (c, f, port)
}
fn put(c: &mut Core, f: &mut Fields, eid: &str, p: &str, v: J) {
    let w = Write { eid: eid.into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
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
fn get(c: &mut Core, f: &mut Fields, port: u64, raw: &str) -> Vec<u8> {
    let (tx, rx) = std::sync::mpsc::channel();
    let raw = raw.to_string();
    std::thread::spawn(move || {
        let mut s = TcpStream::connect(("127.0.0.1", port as u16)).unwrap();
        s.write_all(raw.as_bytes()).unwrap();
        let mut out = Vec::new();
        s.read_to_end(&mut out).unwrap();
        tx.send(out).unwrap();
    });
    let mut got = None;
    pump(c, f, |_| { if got.is_none() { got = rx.try_recv().ok(); } got.is_some() });
    got.expect("an answer")
}
fn split(r: &[u8]) -> (String, Vec<u8>) {
    let i = r.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
    (String::from_utf8_lossy(&r[..i]).to_string(), r[i + 4..].to_vec())
}


fn status(r: &[u8]) -> String { split(r).0.lines().next().unwrap().to_string() }
fn req(path: &str, extra: &str) -> String { format!("GET {path} HTTP/1.1\r\nHost: t\r\n{extra}\r\n") }

#[test]
fn the_prefix_is_stripped_and_a_route_answers_on_both_paths() {
    let (mut c, mut f, port) = world();
    for path in ["/global/health", "/command", "/path", "/project/current", "/vcs"] {
        let bare = get(&mut c, &mut f, port, &req(path, ""));
        let pre = get(&mut c, &mut f, port, &req(&format!("/ui/serve{path}"), ""));
        assert!(status(&bare).starts_with("HTTP/1.1 200"), "{path}: {}", status(&bare));
        assert_eq!(status(&pre), status(&bare), "{path}");
        assert_eq!(split(&pre).1, split(&bare).1, "{path}: the same body");
    }
    // a path no route owns is a 404 (not a hang), and the prefix alone is no page
    for path in ["/ui/serve/session", "/ui/serve/nothing/here"] {
        let r = get(&mut c, &mut f, port, &req(path, ""));
        assert!(status(&r).starts_with("HTTP/1.1 404"), "{path}: {}", status(&r));
    }
    // the prefix is stripped ONCE, and only at the front: a path that holds it later is its own path
    let r = get(&mut c, &mut f, port, &req("/global/ui/serve/health", ""));
    assert!(status(&r).starts_with("HTTP/1.1 404"), "{}", status(&r));
    // a method and a body ride through (the page's POSTs): the family's own 405 answers, so the route is the family's
    let (mut c2, mut f2, port2) = world();
    let r = get(&mut c2, &mut f2, port2, "POST /ui/serve/global/health HTTP/1.1\r\nHost: t\r\nContent-Length: 0\r\n\r\n");
    assert!(status(&r).starts_with("HTTP/1.1 405"), "{}", status(&r));
}
