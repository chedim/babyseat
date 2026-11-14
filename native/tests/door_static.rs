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

fn ui_dir() -> String { format!("{}/../membrane/apps/barest.c1f/ui", env!("CARGO_MANIFEST_DIR")) }

fn world() -> (Core, Fields, u64) { world_with(json!({"ui_dir": ui_dir()})) }

fn world_with(boot: J) -> (Core, Fields, u64) {
    let mut f = Fields::goalgraph();
    f.set_pack("boot", include_str!("../../membrane/packs/boot.c1f")).unwrap();
    f.set_pack("door", DOOR).unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Http:door".into(), "Http".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    c.load("Boot:seat".into(), "Boot".into(), boot.as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    std::env::set_var("DOOR_STATIC_SECRET", "0123456789abcdef0123456789abcdef");        // a door with a secret: only the public shell answers a caller with no token
    put(&mut c, &mut f, "Http:door", "listen", json!({"id": "l1", "port": 0, "secret_env": "DOOR_STATIC_SECRET"}));
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

#[test]
fn the_page_and_its_files_are_served_with_their_types() {
    let (mut c, mut f, port) = world();
    for (path, ctype, file) in [("/", "text/html; charset=utf-8", "index.html"), ("/index.html", "text/html; charset=utf-8", "index.html"),
                                ("/membrane", "text/html; charset=utf-8", "index.html"),
                                ("/ui/www/boot.js", "application/javascript; charset=utf-8", "boot.js"),
                                ("/ui/www/fieldvm_core_bg.wasm", "application/wasm", "fieldvm_core_bg.wasm"),
                                ("/favicon.ico", "image/svg+xml", "favicon.svg")] {
        let (head, body) = split(&get(&mut c, &mut f, port, &format!("GET {path} HTTP/1.1\r\nHost: t\r\n\r\n")));
        assert!(head.starts_with("HTTP/1.1 200 OK"), "{path}: {head}");
        assert!(head.to_lowercase().contains(&format!("content-type: {}", ctype.to_lowercase())), "{path}: {head}");
        assert_eq!(body, std::fs::read(format!("{}/{}", ui_dir(), file)).unwrap(), "{path}: the bytes");
    }
}

#[test]
fn a_missing_file_a_traversal_and_a_post_are_refused() {
    let (mut c, mut f, port) = world();
    for (raw, status) in [("GET /ui/www/nothing.js HTTP/1.1\r\nHost: t\r\n\r\n", "404"),
                          ("GET /ui/www/../index.html HTTP/1.1\r\nHost: t\r\n\r\n", "404"),
                          ("GET /ui/www/%2e%2e/index.html HTTP/1.1\r\nHost: t\r\n\r\n", "404"),
                          ("GET /ui/www//etc/passwd HTTP/1.1\r\nHost: t\r\n\r\n", "404"),
                          ("POST /ui/www/boot.js HTTP/1.1\r\nHost: t\r\nContent-Length: 0\r\n\r\n", "405"),
                          ("GET /nowhere HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer TOKEN\r\n\r\n", "404"),
                          ("GET /nowhere HTTP/1.1\r\nHost: t\r\n\r\n", "401")] {
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
        let tok = membrane_vm::jwt::mint(b"0123456789abcdef0123456789abcdef", "owner", "owner", 3600.0, now).unwrap();
        let (head, _) = split(&get(&mut c, &mut f, port, &raw.replace("TOKEN", &tok)));
        assert!(head.starts_with(&format!("HTTP/1.1 {status}")), "{raw}: {head}");
    }
}

fn layer(tag: &str, files: &[(&str, &str)]) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("door-layers-{tag}-{}-{n}", std::process::id()));
    for (f, t) in files { let p = d.join("ui").join(f); std::fs::create_dir_all(p.parent().unwrap()).unwrap(); std::fs::write(p, t).unwrap(); }
    d.to_string_lossy().to_string()
}

#[test]
fn an_upper_layers_file_shadows_the_lower_and_a_file_only_below_falls_through() {
    // app_dirs is the folders of the apps, the UPPER layer first (each app's files are under its `ui/`); --ui-dir is the lowest layer
    let upper = layer("up", &[("x.js", "upper x"), ("only_up.js", "up")]);
    let lower = layer("low", &[("x.js", "lower x"), ("only_low.js", "low"), ("index.html", "<h1>low</h1>")]);
    let flat = format!("{}/ui", layer("flat", &[("x.js", "flat x"), ("only_flat.js", "flat"), ("index.html", "<h1>flat</h1>")]));
    let (mut c, mut f, port) = world_with(json!({"app_dirs": [upper, lower], "ui_dir": flat}));
    for (path, want) in [("/ui/www/x.js", "upper x"), ("/ui/www/only_up.js", "up"), ("/ui/www/only_low.js", "low"), ("/ui/www/only_flat.js", "flat"), ("/", "<h1>low</h1>")] {
        let (head, body) = split(&get(&mut c, &mut f, port, &format!("GET {path} HTTP/1.1\r\nHost: t\r\n\r\n")));
        assert!(head.starts_with("HTTP/1.1 200 OK"), "{path}: {head}");
        assert_eq!(String::from_utf8_lossy(&body), want, "{path}");
    }
    let (head, _) = split(&get(&mut c, &mut f, port, "GET /ui/www/nowhere.js HTTP/1.1\r\nHost: t\r\n\r\n"));
    assert!(head.starts_with("HTTP/1.1 404"), "{head}");
}
