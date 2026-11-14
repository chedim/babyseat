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
    serve_tls_with(rcgen::SanType::IpAddress("127.0.0.1".parse().unwrap()), true, reply)
}

/// The same, with the certificate's one name chosen and whether the server says close_notify when it is done.
fn serve_tls_with(san: rcgen::SanType, notify: bool, reply: impl Fn(&str) -> Vec<Vec<u8>> + Send + 'static) -> (u16, String, std::sync::mpsc::Receiver<String>) {
    use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
    let mut params = rcgen::CertificateParams::new(Vec::<String>::new()).unwrap();
    params.subject_alt_names = vec![san];
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
        if notify { s.conn.send_close_notify(); }
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

#[test]
fn a_certificate_for_another_name_is_refused_even_when_its_ca_is_trusted() {
    let (port, pem, _rx) = serve_tls_with(rcgen::SanType::DnsName("wrong.test".try_into().unwrap()), true, |_| vec![b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "n1", "url": format!("https://127.0.0.1:{port}/"), "ca_pem": pem, "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "a trusted CA does not make a certificate valid for another name: {r}");
    assert!(r.get("body").is_none() && r.get("http_status").is_none(), "{r}");
}

#[test]
fn a_body_cut_short_is_a_failure_not_a_quiet_end_over_plain_http() {
    let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nhell".to_vec()]);        // promises 10, sends 4, closes
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "t1", "url": format!("http://127.0.0.1:{port}/"), "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    assert!(r["head"].as_str().unwrap().contains("truncated"), "{r}");
}

#[test]
fn a_chunked_body_with_no_terminator_is_a_failure_too() {
    let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n".to_vec()]);   // no 0-size chunk
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "t2", "url": format!("http://127.0.0.1:{port}/"), "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    assert!(r["head"].as_str().unwrap().contains("truncated"), "{r}");
}

#[test]
fn a_tls_peer_that_drops_the_socket_mid_body_is_a_failure() {
    // no close_notify and a body shorter than it promised: a truncation an attacker can cause is not an end of stream
    let (port, pem, _rx) = serve_tls_with(rcgen::SanType::IpAddress("127.0.0.1".parse().unwrap()), false, |_| vec![b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nhell".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "t3", "url": format!("https://127.0.0.1:{port}/"), "ca_pem": pem, "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    assert!(r["head"].as_str().unwrap().contains("truncated"), "{r}");
}

#[test]
fn a_response_with_no_length_ends_at_the_close_as_before() {
    let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nall of it".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "t4", "url": format!("http://127.0.0.1:{port}/"), "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(0), "{r}");
    assert_eq!(r["body"], json!("all of it"));
}

#[test]
fn a_stream_that_fails_still_ends_with_a_done_chunk_that_says_why() {
    // the consumer waits on `done`: a truncated stream must still end it, with the error on it
    let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\ndata: partial\n\n".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "s9", "url": format!("http://127.0.0.1:{port}/"), "stream": true, "timeout_s": 5}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(1), "{r}");
    pump(&mut c, &mut f, |c| c.docs.iter().any(|(k, e)| k.starts_with("Chunk:Fetch:m:") && e.props["done"] == json!(true)));
    let done: Vec<&serde_json::Value> = c.docs.iter().filter(|(k, e)| k.starts_with("Chunk:Fetch:m:") && e.props["done"] == json!(true)).map(|(_, e)| &e.props["error"]).collect();
    assert_eq!(done.len(), 1, "exactly one done chunk");
    assert!(done[0].as_str().unwrap_or("").contains("truncated"), "{:?}", done);
}

#[test]
fn a_stream_that_stalls_past_the_timeout_also_ends_with_a_done_chunk() {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    std::thread::spawn(move || {
        let (mut s, _) = l.accept().unwrap();
        let mut b = [0u8; 2048];
        let _ = s.read(&mut b);
        s.write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n").unwrap();
        s.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_secs(4));              // then says nothing (longer than the 1 s timeout)
    });
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "s8", "url": format!("http://127.0.0.1:{port}/"), "stream": true, "timeout_s": 1}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    assert_eq!(c.docs["Fetch:m"].props["request_result"]["rc"], json!(1));
    pump(&mut c, &mut f, |c| c.docs.iter().any(|(k, e)| k.starts_with("Chunk:Fetch:m:") && e.props["done"] == json!(true)));
    let done: Vec<&serde_json::Value> = c.docs.iter().filter(|(k, e)| k.starts_with("Chunk:Fetch:m:") && e.props["done"] == json!(true)).map(|(_, e)| &e.props["error"]).collect();
    assert_eq!(done.len(), 1);
    assert!(done[0].as_str().unwrap_or("").contains("read"), "{:?}", done);
}

// A Fetch receipt carries the same timing a Command's does (#1143, a live finding): started and finished (epoch seconds) and ms.
#[test]
fn a_fetch_receipt_carries_started_finished_and_ms_like_a_commands_and_a_refusal_too() {
    let now = || std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    let (port, _rx) = serve(|_| { std::thread::sleep(std::time::Duration::from_millis(300)); vec![b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok".to_vec()] });
    let (mut c, mut f) = world();
    let t0 = now();
    put(&mut c, &mut f, json!({"id": "t1", "url": format!("http://127.0.0.1:{port}/")}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let t1 = now();
    let r = c.docs["Fetch:m"].props["request_result"].clone();
    let (st, fin, ms) = (r["started"].as_f64().unwrap_or_else(|| panic!("{r}")), r["finished"].as_f64().unwrap(), r["ms"].as_f64().unwrap());
    assert!(st >= t0 - 0.01 && fin <= t1 + 0.01 && st < fin, "{st} {fin} within {t0}..{t1}");
    assert!(ms >= 250.0 && ((fin - st) * 1000.0 - ms).abs() < 5.0, "ms {ms} vs {st}..{fin}");
    // a refusal (a bad url) has them too
    put(&mut c, &mut f, json!({"id": "t2", "url": "nonsense"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").map(|r| r["id"] == json!("t2")).unwrap_or(false));
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert!(r["started"].is_f64() && r["finished"].is_f64() && r["ms"].is_u64(), "{r}");
}

// ---- THE BEARER FROM THE ENVIRONMENT (#1143, the OpenAI-compatible chat rung): `bearer_env` names a variable; its value is the request's `Authorization: Bearer`
// header, read by the driver, never stored in the entity, the journal or the receipt, and masked wherever the server prints it back.
#[test]
fn bearer_env_sends_the_value_of_the_named_variable_as_a_bearer_and_masks_it_in_whatever_comes_back() {
    let key = "sk-test-bearer-PLANTED-0123456789";
    std::env::set_var("FETCH_TEST_BEARER_A", key);
    let (port, rx) = serve(move |_| { let body = format!("bad key: {key} !"); vec![format!("HTTP/1.1 401 No\r\nContent-Length: {}\r\n\r\n", body.len()).into_bytes(), body.into_bytes()] });
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "b1", "url": format!("http://127.0.0.1:{port}/x"), "method": "POST", "body": "{}", "bearer_env": "FETCH_TEST_BEARER_A"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let seen = rx.recv().unwrap();
    assert!(seen.contains(&format!("Authorization: Bearer {key}")), "the server got the bearer: {seen}");
    let all = serde_json::to_string(&c.docs["Fetch:m"].props).unwrap();
    assert!(!all.contains(key), "the key is not on the entity: {all}");
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["http_status"], json!(401));
    assert!(r["body"].as_str().unwrap().contains("bad key: *** !"), "the echo is masked: {r}");
}

#[test]
fn a_masked_value_is_masked_in_a_stream_even_when_the_pieces_cut_it() {
    let key = "sk-test-bearer-STRADDLE-0123456789";
    std::env::set_var("FETCH_TEST_BEARER_B", key);
    let (a, b) = key.split_at(11);
    let (port, _rx) = serve({ let (a, b) = (a.to_string(), b.to_string()); move |_| vec![b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n".to_vec(), format!("data: one {a}").into_bytes(), format!("{b} two\n\n").into_bytes()] });
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "b2", "url": format!("http://127.0.0.1:{port}/event"), "stream": true, "bearer_env": "FETCH_TEST_BEARER_B"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let mut chunks: Vec<(i64, String)> = c.docs.iter().filter(|(k, _)| k.starts_with("Chunk:Fetch:m:")).map(|(_, e)| (e.props["n"].as_i64().unwrap(), e.props["data"].as_str().unwrap().to_string())).collect();
    chunks.sort();
    let text: String = chunks.iter().map(|c| c.1.clone()).collect();
    assert_eq!(text, "data: one *** two\n\n");
    assert!(!chunks.iter().any(|c| c.1.contains(&key[..11]) || c.1.contains(&key[11..])), "no piece holds a part of the key: {chunks:?}");
}

#[test]
fn a_bearer_variable_that_is_not_set_is_a_refusal_that_names_it_and_nothing_is_sent() {
    std::env::remove_var("FETCH_TEST_BEARER_C");
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "b3", "url": "http://127.0.0.1:1/x", "bearer_env": "FETCH_TEST_BEARER_C"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let r = &c.docs["Fetch:m"].props["request_result"];
    assert_eq!(r["rc"], json!(2), "{r}");
    assert!(r["head"].as_str().unwrap().contains("environment variable FETCH_TEST_BEARER_C is not set"), "{r}");
    std::env::set_var("FETCH_TEST_BEARER_C", "");
    put(&mut c, &mut f, json!({"id": "b4", "url": "http://127.0.0.1:1/x", "bearer_env": "FETCH_TEST_BEARER_C"}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props["request_result"]["id"] == json!("b4"));
    assert_eq!(c.docs["Fetch:m"].props["request_result"]["rc"], json!(2), "an empty variable is no key");
}

#[test]
fn with_lines_a_chunk_holds_whole_lines_only_and_the_end_flushes_the_last_one() {
    let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n".to_vec(), b"data: a\ndata: b".to_vec(), b"c\n\ndata: d".to_vec(), b"\n\nlast".to_vec()]);
    let (mut c, mut f) = world();
    put(&mut c, &mut f, json!({"id": "l1", "url": format!("http://127.0.0.1:{port}/event"), "stream": true, "lines": true}));
    pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
    let mut chunks: Vec<(i64, String, bool)> = c.docs.iter().filter(|(k, _)| k.starts_with("Chunk:Fetch:m:")).map(|(_, e)| (e.props["n"].as_i64().unwrap(), e.props["data"].as_str().unwrap().to_string(), e.props["done"].as_bool().unwrap())).collect();
    chunks.sort();
    let text: String = chunks.iter().map(|c| c.1.clone()).collect();
    assert_eq!(text, "data: a\ndata: bc\n\ndata: d\n\nlast");
    for (_, d, done) in &chunks[..chunks.len() - 2] { assert!(d.ends_with('\n') && !done, "a chunk ends at a line end: {d:?}"); }
    assert_eq!(chunks[chunks.len() - 2].1, "last", "the unfinished line comes at the end");
    assert!(chunks.last().unwrap().2);
}

#[test]
fn with_keep_a_streams_receipt_also_carries_the_whole_body_and_without_it_does_not() {
    for keep in [true, false] {
        let (port, _rx) = serve(|_| vec![b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n".to_vec(), b"data: one\n\n".to_vec(), b"data: two\n\n".to_vec()]);
        let (mut c, mut f) = world();
        put(&mut c, &mut f, json!({"id": "k1", "url": format!("http://127.0.0.1:{port}/event"), "stream": true, "keep": keep}));
        pump(&mut c, &mut f, |c| c.docs["Fetch:m"].props.get("request_result").is_some());
        let r = &c.docs["Fetch:m"].props["request_result"];
        assert_eq!(r.get("body").and_then(|b| b.as_str()), if keep { Some("data: one\n\ndata: two\n\n") } else { None }, "{r}");
        assert!(c.docs.keys().any(|k| k.starts_with("Chunk:Fetch:m:")), "the chunks come either way");
    }
}
