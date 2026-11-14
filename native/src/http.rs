//! The HTTP door (operator, 2026-11-12: cliff talks to rust directly, no Python in between): `Http:<name>.listen = {port,
//! host?}` binds a listener; every request arrives as an entity `Request:<n>` ({method, path, query, headers, body, conn, http,
//! peer, ts}) pushed into the engine like any write, so a pack DECIDES the answer; `Request:<n>.respond = {status, headers?,
//! body}` (the request holds its connection) writes it out. std only. One request per connection (Connection: close); a request body
//! is read by Content-Length (no chunked bodies: 411).
//! An EVENT STREAM is the other way to answer (docs/cliffvm.md, "The event stream"): `Request.stream = {id, status?, headers?}` writes the head
//! of a `text/event-stream` response and keeps the connection open; `Request.send = {id, event?, data}` appends one event
//! (`event: <name>` and `data: <json>`; `{id, frames: [{event?, data}, ...]}` appends several in one write); `Request.end = {id}` closes. The frames are queued in the order the writes were made (on the
//! engine's thread) and written by the effect threads, so two sends never swap.
use std::collections::VecDeque;
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
pub fn token_of(headers: &Map<String, J>, _query: &str, cookie: &str) -> Option<String> {
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
        let (mut ours, mut old) = (None, None);
        for kv in c.split(';') {
            if let Some((k, v)) = kv.trim().split_once('=') {
                if v.is_empty() { continue; }
                if !cookie.is_empty() && k == cookie { ours = Some(v.to_string()); }
                if k == "membrane_jwt" { old = Some(v.to_string()); }
            }
        }
        if ours.is_some() { return ours; }
        return old;
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
    let host = req.get("host").and_then(|h| h.as_str()).map(String::from).or_else(|| st.bind_default()).unwrap_or_else(|| "127.0.0.1".to_string());
    let port = req.get("port").and_then(|p| p.as_u64()).unwrap_or(0);
    let l = match TcpListener::bind((host.as_str(), port as u16)) {
        Ok(l) => l,
        Err(e) => return json!({"id": id, "status": "error", "rc": 2, "head": format!("cannot listen on {}:{}: {}", host, port, e)}),
    };
    let bound = l.local_addr().map(|a| a.port()).unwrap_or(0);
    if l.set_nonblocking(true).is_err() { return json!({"id": id, "status": "error", "rc": 2, "head": "cannot listen: nonblocking"}); }
    // the door verifies the token itself: the secret comes from the environment variable `secret_env` names (never a prop)
    let secret: Option<Vec<u8>> = req.get("secret_env").and_then(|e| e.as_str()).and_then(|e| std::env::var(e).ok()).map(|s| s.into_bytes())
        .or_else(|| req.get("secret_file").and_then(|f| f.as_str()).and_then(|f| std::fs::read_to_string(f).ok()).map(|s| s.trim().as_bytes().to_vec()));
    if let Some(k) = &secret { st.set_secret(eid, k.clone()); }
    let cookie = secret.as_ref().map(|k| crate::jwt::cookie_name(k)).unwrap_or_default();
    let stop = Arc::new(AtomicBool::new(false));
    if let Some(old) = st.set_listener(eid, stop.clone()) { old.store(true, Ordering::SeqCst); }
    let plain = st.plain() && secret.is_none();
    let (st2, eid2, secret, cookie2) = (st.clone_handle(), eid.to_string(), secret, cookie.clone());
    std::thread::Builder::new().name("membrane-http".into()).spawn(move || {
        while !stop.load(Ordering::SeqCst) {
            match l.accept() {
                Ok((mut s, peer)) => {
                    let plain = plain;
                    let (st3, eid3, secret, cookie) = (st2.clone_handle(), eid2.clone(), secret.clone(), cookie2.clone());
                    let _ = std::thread::Builder::new().name("membrane-http-conn".into()).spawn(move || {
                        let _ = s.set_nonblocking(false);
                        let Some((mut props, _)) = read_request(&mut s) else { return bad(s, 400, "bad request"); };
                        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                        // WHO: verified here, by the door, before any pack sees the request (a pack reads `authed` and `sub`; it cannot set them)
                        let headers = props.get("headers").and_then(|h| h.as_object()).cloned().unwrap_or_default();
                        let verdict = if plain { Ok(json!({"sub": "owner"})) } else { match (&secret, token_of(&headers, "", &cookie)) {
                            (None, _) => Err("this door has no secret".to_string()),
                            (Some(_), None) => Err("no token".to_string()),
                            (Some(k), Some(t)) => crate::jwt::verify(&t, k, now),
                        } };
                        match verdict {
                            Ok(claims) => { if plain { props.insert("open".into(), json!(true)); }   // no secret, no check: the caller is the owner by decision (docs/auth.md)
                                props.insert("authed".into(), json!(true)); props.insert("sub".into(), claims["sub"].clone()); props.insert("claims".into(), claims); }
                            Err(why) => { props.insert("authed".into(), json!(false)); props.insert("auth_error".into(), json!(why)); }
                        }
                        // the credentials were checked above and no pack reads them: they must not stay on the Request entity, which any verified person can read with `entity_read` while it is held
                        // (the page's open event stream is held for as long as the page is open)
                        if let Some(h) = props.get_mut("headers").and_then(|h| h.as_object_mut()) { for k in ["authorization", "cookie", "proxy-authorization"] { h.remove(k); } }
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
    json!({"id": id, "status": "ok", "rc": 0, "head": format!("listening on {}:{}", host, bound), "port": bound, "host": host, "cookie": cookie})
}

/// `Request.verify = {id, token}`: check a token (one the request carries in its body, say) with the door's own secret.
/// The receipt is data: rc 0 and the claims, or rc 1 and only "invalid token" (the reason stays in the log, never the answer).
pub fn verify(st: &Store, http_eid: &str, req: &J) -> J {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let tok = req.get("token").and_then(|t| t.as_str()).unwrap_or("");
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    match st.secret_of(http_eid).map(|k| crate::jwt::verify(tok, &k, now)) {
        Some(Ok(claims)) => json!({"id": id, "status": "ok", "rc": 0, "head": "ok", "claims": claims}),
        Some(Err(why)) => { eprintln!("[door] token refused: {why}"); json!({"id": id, "status": "error", "rc": 1, "head": crate::jwt::INVALID}) }
        None => json!({"id": id, "status": "error", "rc": 2, "head": "this door has no secret"}),
    }
}

/// `Request.mint = {id, sub, name?, ttl}`: sign a token with the door's own secret (the door never hands the secret to a pack).
/// The receipt is data: rc 0 and the `token`, or rc 2 and why (no secret, a bad subject, a ttl under a second).
pub fn mint(st: &Store, http_eid: &str, req: &J) -> J {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let sub = req.get("sub").and_then(|s| s.as_str()).unwrap_or("");
    let name = req.get("name").and_then(|s| s.as_str()).unwrap_or(sub);
    let ttl = req.get("ttl").and_then(|t| t.as_f64()).unwrap_or(0.0);
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    match st.secret_of(http_eid).ok_or("this door has no secret".to_string()).and_then(|k| crate::jwt::mint(&k, sub, name, ttl, now)) {
        Ok(token) => json!({"id": id, "status": "ok", "rc": 0, "head": "ok", "token": token}),
        Err(why) => json!({"id": id, "status": "error", "rc": 2, "head": why}),
    }
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
    let no_body = req.get("no_body").and_then(|b| b.as_bool()).unwrap_or(false);       // a HEAD: the headers, the length, no bytes
    let body: Vec<u8> = match req.get("body") {
        _ if req.get("file").is_some() => {
            // the bytes of a file a pack named (the pack decided which: its routes refuse a climbing path)
            std::fs::read(req.get("file").and_then(|f| f.as_str()).unwrap_or("")).unwrap_or_default()
        }
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
    let ok = s.write_all(head.as_bytes()).and_then(|_| if no_body { Ok(()) } else { s.write_all(&body) }).and_then(|_| s.flush()).is_ok();
    let _ = s.shutdown(std::net::Shutdown::Both);
    if ok { json!({"id": id, "status": "ok", "rc": 0, "head": format!("{} {}", status, body.len())}) }
    else { json!({"id": id, "status": "error", "rc": 1, "head": "the client went away"}) }
}

/// An open event stream: the socket and the frames not yet written, in the order the writes were made.
#[cfg(feature = "alloc-stats")]
impl Stream { pub fn queued(&self) -> (u64, u64) { (self.queue.len() as u64, self.queue.iter().map(|f| f.as_ref().map(|b| b.len() as u64).unwrap_or(0)).sum()) } }
pub struct Stream { sock: TcpStream, queue: VecDeque<Option<Vec<u8>>>, dead: bool, closed: bool }

/// `Request.stream` (called on the engine's thread, in write order): the held connection becomes a stream whose first frame is the head.
/// -> the stream's connection number, or the receipt that refuses.
pub fn stream_open(st: &Store, req: &J, conn: Option<u64>) -> Result<u64, J> {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let refuse = |why: String| json!({"id": id, "status": "error", "rc": 2, "head": why});
    let Some(conn) = conn else { return Err(refuse("stream is written on a Request entity (it holds its connection)".into())) };
    let Some(sock) = st.take_conn(conn) else { return Err(refuse(format!("no open connection {} (answered already, or it went away)", conn))) };
    let status = req.get("status").and_then(|v| v.as_i64()).unwrap_or(200);
    let mut head = format!("HTTP/1.1 {} {}\r\n", status, reason(status));
    let (mut ctype, mut cache) = (false, false);
    if let Some(h) = req.get("headers").and_then(|h| h.as_object()) {
        for (k, v) in h {
            if k.eq_ignore_ascii_case("content-length") || k.eq_ignore_ascii_case("connection") { continue; }
            let v = v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
            if k.contains(['\r', '\n']) || v.contains(['\r', '\n']) { continue; }          // never a header split
            if k.eq_ignore_ascii_case("content-type") { ctype = true; }
            if k.eq_ignore_ascii_case("cache-control") { cache = true; }
            head.push_str(&format!("{}: {}\r\n", k, v));
        }
    }
    if !ctype { head.push_str("Content-Type: text/event-stream; charset=utf-8\r\n"); }
    if !cache { head.push_str("Cache-Control: no-store\r\n"); }
    head.push_str("Connection: close\r\n\r\n");
    st.open_stream(conn, Stream { sock, queue: VecDeque::from([Some(head.into_bytes())]), dead: false, closed: false });
    Ok(conn)
}

/// `Request.send` / `Request.end` (on the engine's thread): queue the frame (`end` queues the close). -> the stream, or the receipt that refuses.
pub fn stream_push(st: &Store, req: &J, conn: Option<u64>, end: bool) -> Result<u64, J> {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let refuse = |why: &str| json!({"id": id, "status": "error", "rc": 2, "head": why});
    let Some(conn) = conn else { return Err(refuse("send and end are written on a Request entity")) };
    let Some(s) = st.stream_of(conn) else { return Err(refuse("no open stream (not started, closed already, or the client went away)")) };
    let mut g = s.lock().unwrap_or_else(|e| e.into_inner());
    if g.dead || g.closed { return Err(refuse("no open stream (closed already, or the client went away)")); }
    if end { g.closed = true; g.queue.push_back(None); return Ok(conn); }
    // one frame: {event?, data}; or SEVERAL in one write: {frames: [{event?, data}, ...]}, queued in that order (all or none: one bad name refuses the send)
    let one = [req.clone()];
    let list: &[J] = match req.get("frames").and_then(|f| f.as_array()) { Some(fs) => fs, None => &one };
    let mut queued = Vec::with_capacity(list.len());
    for f in list {
        let mut frame = String::new();
        if let Some(e) = f.get("event").and_then(|e| e.as_str()) {
            if e.contains(['\r', '\n']) { return Err(refuse("an event name is one line")); }
            frame.push_str(&format!("event: {}\n", e));
        }
        frame.push_str(&format!("data: {}\n\n", f.get("data").cloned().unwrap_or(J::Null)));
        queued.push(frame.into_bytes());
    }
    for b in queued { g.queue.push_back(Some(b)); }
    Ok(conn)
}

/// The effect thread's part: write what is queued, in order (whichever thread gets here first writes all of it). A close shuts the socket
/// and forgets the stream; a failed write (the client left) forgets it too and says so. -> the receipt.
pub fn stream_flush(st: &Store, id: &J, conn: u64) -> J {
    let Some(s) = st.stream_of(conn) else { return json!({"id": id, "status": "error", "rc": 1, "head": "the stream is closed or the client went away"}) };
    let mut g = s.lock().unwrap_or_else(|e| e.into_inner());
    if g.dead { return json!({"id": id, "status": "error", "rc": 1, "head": "the client went away"}); }
    let mut sent = 0usize;
    while let Some(f) = g.queue.pop_front() {
        match f {
            Some(b) => {
                if g.sock.write_all(&b).and_then(|_| g.sock.flush()).is_err() {
                    g.dead = true; g.queue.clear();
                    let _ = g.sock.shutdown(std::net::Shutdown::Both);
                    drop(g); st.drop_stream(conn);
                    return json!({"id": id, "status": "error", "rc": 1, "head": "the client went away"});
                }
                sent += b.len();
            }
            None => {
                let _ = g.sock.shutdown(std::net::Shutdown::Both);
                drop(g); st.drop_stream(conn);
                return json!({"id": id, "status": "ok", "rc": 0, "head": "closed"});
            }
        }
    }
    json!({"id": id, "status": "ok", "rc": 0, "head": format!("{} bytes", sent)})
}
