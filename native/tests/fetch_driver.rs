//! The outbound HTTP driver (fetch.rs; the model list, the model call and its streamed reply need it): `Fetch:<x>.request =
//! {id, url, method?, headers?, body?, stream?}` is an effect whose receipt is data -- {status, http_status, headers, body}.
//! With `stream: true` the body arrives as entities `Chunk:<fetch>:<n>` ({fetch, n, data, done}) pushed into the engine as it
//! is read, and the receipt comes at the end. http and https (rustls; `ca_pem` adds a trusted root).
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

/// A one-shot server: reads one request, answers `reply` (a closure over the request text), returns its port and the request seen.
fn serve(reply: impl Fn(&str) -> Vec<Vec<u8>> + Send + 'static) -> (u16, std::sync::mpsc::Receiver<String>) {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let (mut s, _) = l.accept().unwrap();
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];
        loop {
            let n = s.read(&mut chunk).unwrap();
            buf.extend_from_slice(&chunk[..n]);
            if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                let head = String::from_utf8_lossy(&buf[..i]).to_string();
                let want: usize = head.lines().find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse().unwrap_or(0))).unwrap_or(0);
                while buf.len() < i + 4 + want { let n = s.read(&mut chunk).unwrap(); buf.extend_from_slice(&chunk[..n]); }
                break;
            }
        }
        let text = String::from_utf8_lossy(&buf).to_string();
        tx.send(text.clone()).unwrap();
        for part in reply(&text) { s.write_all(&part).unwrap(); s.flush().unwrap(); std::thread::sleep(std::time::Duration::from_millis(30)); }
    });
    (port, rx)
}

fn world() -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("fx", "PACK fx 1\nFetch.kind getter: 'fetch'\nChunk.kind getter: 'chunk'\n").unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap()));
    c.load("Fetch:m".into(), "Fetch".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, v: J) {
    let w = Write { eid: "Fetch:m".into(), prop: "request".into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
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

#[test]
fn a_get_with_a_content_length_comes_back_as_data() {
    let (port, rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"ok\":true}\n".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "r1", "url": format!("http://127.0.0.1:{port}/api/tags?x=1"), "headers": {"x-key": "k"}}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["id"], json!("r1"));
    assert_eq!(r["rc"], json!(0), "{r}");
    assert_eq!(r["http_status"], json!(200));
    assert_eq!(r["headers"]["content-type"], json!("application/json"));
    assert_eq!(r["body"], json!("{\"ok\":true}\n"));
    let seen = rx.recv().unwrap();
    assert!(seen.starts_with("GET /api/tags?x=1 HTTP/1.1"), "{seen}");
    assert!(seen.to_lowercase().contains("x-key: k") && seen.to_lowercase().contains("host: 127.0.0.1"), "{seen}");
}

#[test]
fn a_post_sends_its_body_and_a_chunked_answer_is_decoded() {
    let (port, rx) = serve(|_| vec![b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n7\r\n, world\r\n0\r\n\r\n".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "p1", "url": format!("http://127.0.0.1:{port}/v1/chat"), "method": "POST", "headers": {"content-type": "application/json"}, "body": "{\"q\":1}"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["http_status"], json!(201));
    assert_eq!(r["body"], json!("hello, world"));
    let seen = rx.recv().unwrap();
    assert!(seen.starts_with("POST /v1/chat HTTP/1.1") && seen.to_lowercase().contains("content-length: 7") && seen.ends_with("{\"q\":1}"), "{seen}");
}

#[test]
fn a_stream_arrives_as_chunk_entities_and_then_the_receipt() {
    let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".to_vec(),
        b"data: one\n\n".to_vec(), b"data: two\n\n".to_vec(), b"data: three\n\n".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "s1", "url": format!("http://127.0.0.1:{port}/event"), "stream": true}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(0), "{r}");
    assert_eq!(r["http_status"], json!(200));
    let mut chunks: Vec<(i64, String, bool)> = c.docs.iter().filter(|(k, _)| k.starts_with("Chunk:Fetch:m:"))
        .map(|(_, e)| (e.props["n"].as_i64().unwrap(), e.props["data"].as_str().unwrap().to_string(), e.props["done"].as_bool().unwrap())).collect();
    chunks.sort();
    let text: String = chunks.iter().map(|c| c.1.clone()).collect();
    assert_eq!(text, "data: one\n\ndata: two\n\ndata: three\n\n");
    assert!(chunks.last().unwrap().2, "the last chunk says done");
    assert_eq!(chunks.iter().filter(|c| c.2).count(), 1);
}

#[test]
fn a_connection_that_fails_is_a_receipt_not_a_hang() {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    drop(l);                                                     // nothing listens there now
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "x1", "url": format!("http://127.0.0.1:{port}/"), "timeout_s": 2}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    assert!(r["head"].as_str().unwrap().contains("connect"), "{r}");
}

#[test]
fn a_bad_url_says_so_and_https_needs_no_special_refusal() {
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "t2", "url": "not a url"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    assert_eq!(c.docs["Fetch:m"].props["request_result"]["rc"], json!(2));
    // https is a real attempt now: a closed port is a connect failure (rc 1), not a "not supported" refusal
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    drop(l);
    put(&mut c, &mut f, json!({"id": "t3", "url": format!("https://127.0.0.1:{port}/"), "timeout_s": 2}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props["request_result"]["id"] == json!("t3"));
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    assert!(r["head"].as_str().unwrap().contains("connect"), "{r}");
}

/// A one-shot TLS server with a fresh self-signed certificate for 127.0.0.1; returns (port, cert pem, request seen).
fn serve_tls(reply: impl Fn(&str) -> Vec<Vec<u8>> + Send + 'static) -> (u16, String, std::sync::mpsc::Receiver<String>) {
    use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
    let mut params = rcgen::CertificateParams::new(Vec::<String>::new()).unwrap();
    params.subject_alt_names = vec![rcgen::SanType::IpAddress("127.0.0.1".parse().unwrap())];
    let key = rcgen::KeyPair::generate().unwrap();
    let cert = params.self_signed(&key).unwrap();
    let pem = cert.pem();
    let provider = std::sync::Arc::new(rustls::crypto::ring::default_provider());
    let cfg = rustls::ServerConfig::builder_with_provider(provider).with_safe_default_protocol_versions().unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert.der().clone()], PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der()))).unwrap();
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let (mut tcp, _) = l.accept().unwrap();
        let mut conn = rustls::ServerConnection::new(std::sync::Arc::new(cfg)).unwrap();
        let mut s = rustls::Stream::new(&mut conn, &mut tcp);
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];
        loop {
            let n = match s.read(&mut chunk) { Ok(n) => n, Err(_) => return };   // a client that rejects our certificate ends here
            if n == 0 { return; }
            buf.extend_from_slice(&chunk[..n]);
            if buf.windows(4).any(|w| w == b"\r\n\r\n") { break; }
        }
        let text = String::from_utf8_lossy(&buf).to_string();
        tx.send(text.clone()).unwrap();
        for part in reply(&text) { s.write_all(&part).unwrap(); s.flush().unwrap(); std::thread::sleep(std::time::Duration::from_millis(30)); }
        s.conn.send_close_notify();
        let _ = s.flush();
    });
    (port, pem, rx)
}

#[test]
fn an_untrusted_certificate_is_a_failure_receipt_naming_the_certificate() {
    let (port, _pem, _rx) = serve_tls(|_| vec![b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "u1", "url": format!("https://127.0.0.1:{port}/"), "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    assert!(r["head"].as_str().unwrap().to_lowercase().contains("certificate"), "{r}");
    assert!(r.get("body").is_none() && r.get("http_status").is_none(), "{r}");
}

#[test]
fn a_certificate_trusted_through_ca_pem_gives_the_answer() {
    let (port, pem, rx) = serve_tls(|_| vec![b"HTTP/1.1 201 Created\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n7\r\n, world\r\n0\r\n\r\n".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "k1", "url": format!("https://127.0.0.1:{port}/v1/chat"), "method": "POST", "headers": {"x-key": "k"},
        "body": "{\"q\":1}", "ca_pem": pem, "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(0), "{r}");
    assert_eq!(r["http_status"], json!(201));
    assert_eq!(r["body"], json!("hello, world"));
    let seen = rx.recv().unwrap();
    assert!(seen.starts_with("POST /v1/chat HTTP/1.1") && seen.to_lowercase().contains("x-key: k"), "{seen}");
}

#[test]
fn a_tls_stream_arrives_as_chunk_entities() {
    let (port, pem, _rx) = serve_tls(|_| vec![b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".to_vec(),
        b"data: one\n\n".to_vec(), b"data: two\n\n".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "s2", "url": format!("https://127.0.0.1:{port}/event"), "stream": true, "ca_pem": pem, "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(0), "{r}");
    let mut chunks: Vec<(i64, String, bool)> = c.docs.iter().filter(|(k, _)| k.starts_with("Chunk:Fetch:m:"))
        .map(|(_, e)| (e.props["n"].as_i64().unwrap(), e.props["data"].as_str().unwrap().to_string(), e.props["done"].as_bool().unwrap())).collect();
    chunks.sort();
    let text: String = chunks.iter().map(|c| c.1.clone()).collect();
    assert_eq!(text, "data: one\n\ndata: two\n\n");
    assert!(chunks.last().unwrap().2);
}

#[test]
fn a_bad_ca_pem_is_a_refusal() {
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "b1", "url": "https://127.0.0.1:9/", "ca_pem": "not a pem"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(2), "{r}");
    assert!(r["head"].as_str().unwrap().contains("ca_pem"), "{r}");
}
