//! The HTTP door (operator, 2026-11-12: cliff talks to rust directly, no Python in between): `Http:<name>.listen = {port,
//! host?}` binds a listener; every request arrives as an entity `Request:<n>` ({method, path, query, headers, body, conn, http,
//! peer, ts}) pushed into the engine like any write, so a pack DECIDES the answer; `Request:<n>.respond = {status, headers?,
//! body}` (the request holds its connection) writes it out. std only. One request per connection (Connection: close); a request body
//! is read by Content-Length (no chunked bodies: 411).
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Map, Value as J};

use crate::store::Store;

const MAX_HEAD: usize = 64 * 1024;
const MAX_BODY: usize = 16 * 1024 * 1024;
const READ_TIMEOUT_S: u64 = 10;

fn reason(status: i64) -> &'static str {
    match status {
        200 => "OK", 201 => "Created", 202 => "Accepted", 204 => "No Content", 301 => "Moved Permanently", 302 => "Found",
        304 => "Not Modified", 400 => "Bad Request", 401 => "Unauthorized", 403 => "Forbidden", 404 => "Not Found",
        405 => "Method Not Allowed", 409 => "Conflict", 411 => "Length Required", 413 => "Payload Too Large",
        423 => "Locked", 429 => "Too Many Requests", 500 => "Internal Server Error", 502 => "Bad Gateway",
        503 => "Service Unavailable", _ => "Status",
    }
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> { hay.windows(needle.len()).position(|w| w == needle) }

fn bad(mut s: TcpStream, status: i64, why: &str) {
    let body = why.as_bytes();
    let _ = write!(s, "HTTP/1.1 {} {}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", status, reason(status), body.len());
    let _ = s.write_all(body);
}

/// The token a request presents (never the query string): `Authorization: Bearer <jwt>`, HTTP Basic with the jwt as the
/// password, or the `membrane_jwt` cookie.
pub fn token_of(headers: &Map<String, J>, _query: &str) -> Option<String> {
    if let Some(a) = headers.get("authorization").and_then(|a| a.as_str()) {
        let a = a.trim();
        if let Some(t) = a.strip_prefix("Bearer ").or_else(|| a.strip_prefix("bearer ")) { let t = t.trim(); if !t.is_empty() { return Some(t.to_string()); } }
        if let Some(b) = a.strip_prefix("Basic ").or_else(|| a.strip_prefix("basic ")) {
            if let Some(raw) = crate::jwt::base64_decode(b.trim(), false) {
                let s = String::from_utf8_lossy(&raw).to_string();
                if let Some((_, pw)) = s.split_once(':') { if !pw.is_empty() { return Some(pw.to_string()); } }
            }
        }
    }
    if let Some(c) = headers.get("cookie").and_then(|c| c.as_str()) {
        for kv in c.split(';') {
            if let Some((k, v)) = kv.trim().split_once('=') { if k == "membrane_jwt" && !v.is_empty() { return Some(v.to_string()); } }
        }
    }
    None
}

/// Read one request off a connection: -> the entity's properties, or None (the connection was answered or dropped).
fn read_request(s: &mut TcpStream) -> Option<(Map<String, J>, Vec<u8>)> {
    s.set_read_timeout(Some(Duration::from_secs(READ_TIMEOUT_S))).ok()?;
    let mut buf: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 8192];
    let head_end = loop {
        if let Some(i) = find(&buf, b"\r\n\r\n") { break i; }
        if buf.len() > MAX_HEAD { return None; }
        match s.read(&mut chunk) { Ok(0) | Err(_) => return None, Ok(n) => buf.extend_from_slice(&chunk[..n]) }
    };
    let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
    let mut lines = head.split("\r\n");
    let first = lines.next()?;
    let mut parts = first.split_whitespace();
    let (method, target) = (parts.next()?.to_string(), parts.next()?.to_string());
    let (path, query) = match target.split_once('?') { Some((p, q)) => (p.to_string(), q.to_string()), None => (target.clone(), String::new()) };
    let mut headers = Map::new();
    for l in lines {
        if let Some((k, v)) = l.split_once(':') { headers.insert(k.trim().to_ascii_lowercase(), json!(v.trim())); }
    }
    if headers.get("transfer-encoding").and_then(|v| v.as_str()).map(|v| v.to_ascii_lowercase().contains("chunked")).unwrap_or(false) { return None; }
    let want: usize = headers.get("content-length").and_then(|v| v.as_str()).and_then(|v| v.parse().ok()).unwrap_or(0);
    if want > MAX_BODY { return None; }
    let mut body: Vec<u8> = buf[head_end + 4..].to_vec();
    while body.len() < want {
        match s.read(&mut chunk) { Ok(0) | Err(_) => return None, Ok(n) => body.extend_from_slice(&chunk[..n]) }
    }
    body.truncate(want);
    let mut p = Map::new();
    p.insert("method".into(), json!(method));
    p.insert("path".into(), json!(path));
    p.insert("query".into(), json!(query));
    p.insert("headers".into(), J::Object(headers));
    p.insert("body".into(), json!(String::from_utf8_lossy(&body)));
    Some((p, body))
}

/// `Http.listen`: bind and accept on threads of its own; -> the receipt. A second listen on the same entity stops the first.
pub fn listen(st: &Store, eid: &str, req: &J) -> J {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let host = req.get("host").and_then(|h| h.as_str()).unwrap_or("127.0.0.1").to_string();
    let port = req.get("port").and_then(|p| p.as_u64()).unwrap_or(0);
    let l = match TcpListener::bind((host.as_str(), port as u16)) {
        Ok(l) => l,
        Err(e) => return json!({"id": id, "status": "error", "rc": 2, "head": format!("cannot listen on {}:{}: {}", host, port, e)}),
    };
    let bound = l.local_addr().map(|a| a.port()).unwrap_or(0);
    if l.set_nonblocking(true).is_err() { return json!({"id": id, "status": "error", "rc": 2, "head": "cannot listen: nonblocking"}); }
    // the door verifies the token itself: the secret comes from the environment variable `secret_env` names (never a prop)
    let secret: Option<Vec<u8>> = req.get("secret_env").and_then(|e| e.as_str()).and_then(|e| std::env::var(e).ok()).map(|s| s.into_bytes());
    let stop = Arc::new(AtomicBool::new(false));
    if let Some(old) = st.set_listener(eid, stop.clone()) { old.store(true, Ordering::SeqCst); }
    let (st2, eid2, secret) = (st.clone_handle(), eid.to_string(), secret);
    std::thread::Builder::new().name("membrane-http".into()).spawn(move || {
        while !stop.load(Ordering::SeqCst) {
            match l.accept() {
                Ok((mut s, peer)) => {
                    let (st3, eid3, secret) = (st2.clone_handle(), eid2.clone(), secret.clone());
                    let _ = std::thread::Builder::new().name("membrane-http-conn".into()).spawn(move || {
                        let _ = s.set_nonblocking(false);
                        let Some((mut props, _)) = read_request(&mut s) else { return bad(s, 400, "bad request"); };
                        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                        // WHO: verified here, by the door, before any pack sees the request (a pack reads `authed` and `sub`; it cannot set them)
                        let headers = props.get("headers").and_then(|h| h.as_object()).cloned().unwrap_or_default();
                        let verdict = match (&secret, token_of(&headers, "")) {
                            (None, _) => Err("this door has no secret".to_string()),
                            (Some(_), None) => Err("no token".to_string()),
                            (Some(k), Some(t)) => crate::jwt::verify(&t, k, now),
                        };
                        match verdict {
                            Ok(claims) => { props.insert("authed".into(), json!(true)); props.insert("sub".into(), claims["sub"].clone()); props.insert("claims".into(), claims); }
                            Err(why) => { props.insert("authed".into(), json!(false)); props.insert("auth_error".into(), json!(why)); }
                        }
                        let conn = st3.hold_conn(s);
                        props.insert("conn".into(), json!(conn));
                        props.insert("http".into(), json!(eid3));
                        props.insert("peer".into(), json!(peer.to_string()));
                        props.insert("ts".into(), json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)));
                        st3.push_inbound(format!("Request:{}", conn), "Request".into(), props);
                    });
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(20)),
                Err(_) => std::thread::sleep(Duration::from_millis(100)),
            }
        }
    }).ok();
    json!({"id": id, "status": "ok", "rc": 0, "head": format!("listening on {}:{}", host, bound), "port": bound, "host": host})
}

/// `Http.respond`: write the answer to the held connection and close it. body: text, or any JSON (sent as json).
pub fn respond(st: &Store, req: &J, conn: Option<u64>) -> J {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let Some(conn) = conn else {
        return json!({"id": id, "status": "error", "rc": 2, "head": "respond is written on a Request entity (it holds its connection)"});
    };
    let Some(mut s) = st.take_conn(conn) else {
        return json!({"id": id, "status": "error", "rc": 2, "head": format!("no open connection {} (answered already, or it went away)", conn)});
    };
    let status = req.get("status").and_then(|v| v.as_i64()).unwrap_or(200);
    let mut ctype = "text/plain; charset=utf-8".to_string();
    let body: Vec<u8> = match req.get("body") {
        None | Some(J::Null) => Vec::new(),
        Some(J::String(t)) => t.clone().into_bytes(),
        Some(other) => { ctype = "application/json".into(); other.to_string().into_bytes() }
    };
    let mut head = format!("HTTP/1.1 {} {}\r\n", status, reason(status));
    let mut have_ctype = false;
    if let Some(h) = req.get("headers").and_then(|h| h.as_object()) {
        for (k, v) in h {
            if k.eq_ignore_ascii_case("content-length") || k.eq_ignore_ascii_case("connection") { continue; }
            if k.eq_ignore_ascii_case("content-type") { have_ctype = true; }
            let v = v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
            if k.contains(['\r', '\n']) || v.contains(['\r', '\n']) { continue; }          // never a header split
            head.push_str(&format!("{}: {}\r\n", k, v));
        }
    }
    if !have_ctype && !body.is_empty() { head.push_str(&format!("Content-Type: {}\r\n", ctype)); }
    head.push_str(&format!("Content-Length: {}\r\nConnection: close\r\n\r\n", body.len()));
    let ok = s.write_all(head.as_bytes()).and_then(|_| s.write_all(&body)).and_then(|_| s.flush()).is_ok();
    let _ = s.shutdown(std::net::Shutdown::Both);
    if ok { json!({"id": id, "status": "ok", "rc": 0, "head": format!("{} {}", status, body.len())}) }
    else { json!({"id": id, "status": "error", "rc": 1, "head": "the client went away"}) }
}
