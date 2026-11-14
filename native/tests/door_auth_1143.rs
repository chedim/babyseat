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
const AUTH: &str = include_str!("../../membrane/packs/door_auth.c1f");
const SECRET: &str = "0123456789abcdef0123456789abcdef";

fn ui_dir() -> String { format!("{}/../membrane/apps/barest.c1f/ui", env!("CARGO_MANIFEST_DIR")) }

fn world() -> (Core, Fields, u64) {
    let mut f = Fields::goalgraph();
    f.set_pack("boot", include_str!("../../membrane/packs/boot.c1f")).unwrap();
    f.set_pack("door", DOOR).unwrap();
    f.set_pack("door_auth", AUTH).unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Http:door".into(), "Http".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    c.load("Boot:seat".into(), "Boot".into(), json!({"ui_dir": ui_dir()}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    std::env::set_var("DOOR_AUTH_SECRET", SECRET);
    put(&mut c, &mut f, "Http:door", "listen", json!({"id": "l1", "port": 0, "secret_env": "DOOR_AUTH_SECRET"}));
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


fn token(sub: &str) -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    membrane_vm::jwt::mint(SECRET.as_bytes(), sub, sub, 3600.0, now).unwrap()
}
fn status(r: &[u8]) -> String { split(r).0.lines().next().unwrap().to_string() }
fn header(r: &[u8], name: &str) -> Option<String> {
    split(r).0.lines().skip(1).find_map(|l| l.split_once(':').filter(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.trim().to_string()))
}
fn req(path: &str, extra: &str) -> String { format!("GET {path} HTTP/1.1\r\nHost: t\r\n{extra}\r\n") }

#[test]
fn the_page_fetched_from_loopback_sets_the_owner_cookie() {
    let (mut c, mut f, port) = world();
    for path in ["/", "/index.html", "/membrane"] {
        let r = get(&mut c, &mut f, port, &req(path, ""));
        assert!(status(&r).starts_with("HTTP/1.1 200"), "{path}: {}", status(&r));
        let ck = header(&r, "set-cookie").unwrap_or_else(|| panic!("{path}: no cookie"));
        let name = membrane_vm::jwt::cookie_name(SECRET.as_bytes());
        assert!(ck.starts_with(&format!("{name}=")), "{ck}");
        assert!(ck.contains("; HttpOnly") && ck.contains("; SameSite=Strict") && ck.contains("; Path=/"), "{ck}");
        let tok = ck[name.len() + 1..].split(';').next().unwrap();
        let claims = membrane_vm::jwt::verify(tok, SECRET.as_bytes(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64()).unwrap();
        assert_eq!(claims["sub"], "owner", "the owner's token");
        assert_eq!(split(&r).1, std::fs::read(format!("{}/index.html", ui_dir())).unwrap(), "{path}: the page's bytes");
    }
}

#[test]
fn a_forwarded_page_request_gets_the_page_and_no_cookie() {
    let (mut c, mut f, port) = world();
    let r = get(&mut c, &mut f, port, &req("/", "X-Forwarded-For: 203.0.113.9\r\n"));
    assert!(status(&r).starts_with("HTTP/1.1 200"), "the shell stays public: {}", status(&r));
    assert_eq!(header(&r, "set-cookie"), None, "a proxied caller is not this machine");
}

#[test]
fn the_served_routes_need_the_cookie_or_a_bearer_and_answer_401_in_the_released_shape() {
    let (mut c, mut f, port) = world();
    let name = membrane_vm::jwt::cookie_name(SECRET.as_bytes());
    let page = get(&mut c, &mut f, port, &req("/", ""));
    let ck = header(&page, "set-cookie").unwrap();
    let cookie = ck.split(';').next().unwrap().to_string();
    // with the cookie the page was given: 200
    let r = get(&mut c, &mut f, port, &req("/ui/serve/global/health", &format!("Cookie: {cookie}\r\n")));
    assert!(status(&r).starts_with("HTTP/1.1 200"), "{}", status(&r));
    assert!(String::from_utf8_lossy(&split(&r).1).contains("healthy"), "{:?}", String::from_utf8_lossy(&split(&r).1));
    // a Bearer works too
    let r = get(&mut c, &mut f, port, &req("/ui/serve/global/health", &format!("Authorization: Bearer {}\r\n", token("ann"))));
    assert!(status(&r).starts_with("HTTP/1.1 200"), "{}", status(&r));
    // no token, even from loopback (the page is given its token: tokenless is denied): 401, the released body and header
    let r = get(&mut c, &mut f, port, &req("/ui/serve/global/health", ""));
    assert!(status(&r).starts_with("HTTP/1.1 401"), "{}", status(&r));
    assert_eq!(header(&r, "www-authenticate").as_deref(), Some("Bearer realm=\"membrane\""));
    let body: J = serde_json::from_slice(&split(&r).1).unwrap();
    assert_eq!(body, json!({"error": {"message": "missing or invalid token", "type": "unauthorized"}}));
    // a bad JWT (cookie and bearer): the same 401; a token signed with another key too
    let other = membrane_vm::jwt::mint(b"another secret of at least 32 bytes!!", "owner", "owner", 3600.0, std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64()).unwrap();
    for extra in [format!("Cookie: {name}=garbage\r\n"), "Authorization: Bearer not.a.jwt\r\n".to_string(), format!("Authorization: Bearer {other}\r\n")] {
        let r = get(&mut c, &mut f, port, &req("/ui/serve/global/health", &extra));
        assert!(status(&r).starts_with("HTTP/1.1 401"), "{extra}: {}", status(&r));
        assert_eq!(serde_json::from_slice::<J>(&split(&r).1).unwrap(), body, "{extra}");
    }
}

#[test]
fn a_forwarded_request_without_a_token_is_refused_and_with_one_is_served() {
    let (mut c, mut f, port) = world();
    let fwd = "X-Forwarded-For: 203.0.113.9\r\n";
    for path in ["/ui/serve/global/health", "/global/health", "/ui/conf"] {
        let r = get(&mut c, &mut f, port, &req(path, fwd));
        assert!(status(&r).starts_with("HTTP/1.1 401"), "{path}: {}", status(&r));
    }
    let r = get(&mut c, &mut f, port, &req("/ui/serve/global/health", &format!("{fwd}Authorization: Bearer {}\r\n", token("ann"))));
    assert!(status(&r).starts_with("HTTP/1.1 200"), "{}", status(&r));
}

#[test]
fn the_prefix_is_stripped_and_a_route_answers_on_both_paths() {
    let (mut c, mut f, port) = world();
    let auth = format!("Authorization: Bearer {}\r\n", token("ann"));
    for path in ["/global/health", "/command", "/path", "/project/current"] {
        let bare = get(&mut c, &mut f, port, &req(path, &auth));
        let pre = get(&mut c, &mut f, port, &req(&format!("/ui/serve{path}"), &auth));
        assert!(status(&bare).starts_with("HTTP/1.1 200"), "{path}: {}", status(&bare));
        assert_eq!(status(&pre), status(&bare), "{path}");
        assert_eq!(split(&pre).1, split(&bare).1, "{path}: the same body");
    }
    // a path no route owns is a 404 (with a token), not a 401 and not a hang
    let r = get(&mut c, &mut f, port, &req("/ui/serve/session", &auth));
    assert!(status(&r).starts_with("HTTP/1.1 404"), "{}", status(&r));
    // the prefix means the served path only: no climbing out of it into the page's files by the prefix
    let r = get(&mut c, &mut f, port, &req("/ui/serve", &auth));
    assert!(status(&r).starts_with("HTTP/1.1 404"), "{}", status(&r));
}

#[test]
fn a_bare_loopback_caller_is_not_the_owner_when_the_door_has_a_secret() {
    // the operator's rule (#842): a localhost peer is not the owner. Every route but the public shell needs a verified token, the unprefixed ones too
    let (mut c, mut f, port) = world();
    for path in ["/global/health", "/command", "/path", "/ui/conf", "/session", "/ui/serve/global/health"] {
        let r = get(&mut c, &mut f, port, &req(path, ""));
        assert!(status(&r).starts_with("HTTP/1.1 401"), "{path}: {}", status(&r));
        assert_eq!(header(&r, "www-authenticate").as_deref(), Some("Bearer realm=\"membrane\""), "{path}");
        assert_eq!(serde_json::from_slice::<J>(&split(&r).1).unwrap(), json!({"error": {"message": "missing or invalid token", "type": "unauthorized"}}), "{path}");
    }
    // the same call with a token is served
    let auth = format!("Authorization: Bearer {}\r\n", token("owner"));
    let r = get(&mut c, &mut f, port, &req("/global/health", &auth));
    assert!(status(&r).starts_with("HTTP/1.1 200"), "{}", status(&r));
    // a token past its life (beyond the 30 s leeway) is the same 401
    let old = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() - 7200.0;
    let expired = membrane_vm::jwt::mint(SECRET.as_bytes(), "owner", "owner", 60.0, old).unwrap();
    let r = get(&mut c, &mut f, port, &req("/global/health", &format!("Authorization: Bearer {expired}\r\n")));
    assert!(status(&r).starts_with("HTTP/1.1 401"), "{}", status(&r));
}

#[test]
fn the_public_shell_stays_open_to_a_caller_with_no_token() {
    let (mut c, mut f, port) = world();
    for path in ["/", "/index.html", "/membrane", "/ui/www/none.js", "/favicon.ico"] {
        let r = get(&mut c, &mut f, port, &req(path, ""));
        assert!(!status(&r).starts_with("HTTP/1.1 401"), "{path}: {}", status(&r));
    }
}
