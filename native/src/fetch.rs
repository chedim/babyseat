//! The outbound HTTP driver (the model list, the model call and its streamed reply): `Fetch:<x>.request = {id, url, method?,
//! headers?, body?, stream?, timeout_s?}`. The receipt is data. With `stream` the body is pushed into the engine as entities
//! `Chunk:<fetch>:<n>` ({fetch, id, n, data, done}) while it is read. Plain http for now; https says so (TLS is a later piece).
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

use serde_json::{json, Map, Value as J};

use crate::store::Store;

const MAX_BODY: usize = 64 * 1024 * 1024;

fn refuse(id: &J, rc: i64, why: String) -> J { json!({"id": id, "status": "error", "rc": rc, "head": why}) }

/// `http://host[:port]/path?query` -> (host, port, path+query)
fn parse(url: &str) -> Result<(String, u16, String), String> {
    if url.starts_with("https://") { return Err("https is not supported yet (TLS is a later piece)".into()); }
    let rest = url.strip_prefix("http://").ok_or_else(|| format!("not an http url: {url:?}"))?;
    let (hostport, path) = match rest.find('/') { Some(i) => (&rest[..i], &rest[i..]), None => (rest, "/") };
    if hostport.is_empty() || hostport.contains(' ') { return Err(format!("not a url: {url:?}")); }
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) if !h.ends_with(']') || h.starts_with('[') => (h.trim_matches(|c| c == '[' || c == ']').to_string(), p.parse::<u16>().map_err(|_| format!("bad port in {url:?}"))?),
        _ => (hostport.to_string(), 80),
    };
    Ok((host, port, path.to_string()))
}

/// Complete UTF-8 text from a byte buffer, keeping an incomplete trailing sequence for the next piece.
fn take_text(pending: &mut Vec<u8>) -> String {
    match std::str::from_utf8(pending) {
        Ok(s) => { let s = s.to_string(); pending.clear(); s }
        Err(e) => {
            let n = e.valid_up_to();
            let s = String::from_utf8_lossy(&pending[..n]).to_string();
            if e.error_len().is_some() {                       // an invalid byte, not a cut sequence: replace it and go on
                let bad = e.error_len().unwrap();
                let out = format!("{s}\u{fffd}");
                pending.drain(..n + bad);
                return out;
            }
            pending.drain(..n);
            s
        }
    }
}

pub fn request(st: &Store, eid: &str, req: &J) -> J {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let url = req.get("url").and_then(|u| u.as_str()).unwrap_or("");
    let (host, port, path) = match parse(url) { Ok(p) => p, Err(e) => return refuse(&id, 2, e) };
    let timeout = Duration::from_secs(req.get("timeout_s").and_then(|t| t.as_u64()).unwrap_or(60));
    let stream = req.get("stream").and_then(|s| s.as_bool()).unwrap_or(false);
    let addr = match (host.as_str(), port).to_socket_addrs().ok().and_then(|mut a| a.next()) {
        Some(a) => a, None => return refuse(&id, 1, format!("connect {host}:{port}: cannot resolve")),
    };
    let mut s = match TcpStream::connect_timeout(&addr, timeout) { Ok(s) => s, Err(e) => return refuse(&id, 1, format!("connect {host}:{port}: {e}")) };
    let _ = s.set_read_timeout(Some(timeout));
    let method = req.get("method").and_then(|m| m.as_str()).unwrap_or("GET").to_ascii_uppercase();
    let body = req.get("body").map(|b| match b { J::String(t) => t.clone(), other => other.to_string() }).unwrap_or_default();
    let mut head = format!("{method} {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUser-Agent: membrane\r\nConnection: close\r\nAccept: */*\r\n");
    if let Some(h) = req.get("headers").and_then(|h| h.as_object()) {
        for (k, v) in h {
            if k.eq_ignore_ascii_case("host") || k.eq_ignore_ascii_case("content-length") || k.eq_ignore_ascii_case("connection") { continue; }
            let v = v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
            if k.contains(['\r', '\n']) || v.contains(['\r', '\n']) { continue; }
            head.push_str(&format!("{k}: {v}\r\n"));
        }
    }
    if !body.is_empty() || method == "POST" || method == "PUT" || method == "PATCH" { head.push_str(&format!("Content-Length: {}\r\n", body.len())); }
    head.push_str("\r\n");
    if let Err(e) = s.write_all(head.as_bytes()).and_then(|_| s.write_all(body.as_bytes())).and_then(|_| s.flush()) { return refuse(&id, 1, format!("send: {e}")); }

    // the response head
    let mut buf: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 16384];
    let head_end = loop {
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") { break i; }
        match s.read(&mut chunk) { Ok(0) => return refuse(&id, 1, "connection closed before a response".into()), Ok(n) => buf.extend_from_slice(&chunk[..n]), Err(e) => return refuse(&id, 1, format!("read: {e}")) }
    };
    let head_text = String::from_utf8_lossy(&buf[..head_end]).to_string();
    let mut lines = head_text.split("\r\n");
    let status_line = lines.next().unwrap_or("");
    let http_status: i64 = status_line.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
    let mut headers = Map::new();
    for l in lines { if let Some((k, v)) = l.split_once(':') { headers.insert(k.trim().to_ascii_lowercase(), json!(v.trim())); } }
    let mut rest: Vec<u8> = buf[head_end + 4..].to_vec();
    let chunked = headers.get("transfer-encoding").and_then(|v| v.as_str()).map(|v| v.to_ascii_lowercase().contains("chunked")).unwrap_or(false);
    let want: Option<usize> = headers.get("content-length").and_then(|v| v.as_str()).and_then(|v| v.parse().ok());

    let mut body_out = String::new();
    let mut pending: Vec<u8> = Vec::new();
    let mut n_chunk: i64 = 0;
    let mut emit = |text: String, body_out: &mut String, n: &mut i64| {
        if text.is_empty() { return; }
        if stream {
            *n += 1;
            let mut p = Map::new();
            p.insert("fetch".into(), json!(eid)); p.insert("id".into(), id.clone()); p.insert("n".into(), json!(*n));
            p.insert("data".into(), json!(text)); p.insert("done".into(), json!(false));
            st.push_inbound(format!("Chunk:{eid}:{n}"), "Chunk".into(), p);
        } else if body_out.len() < MAX_BODY { body_out.push_str(&text); }
    };
    let mut total = 0usize;
    // data bytes -> text pieces, whatever the framing
    let mut feed = |bytes: &[u8], pending: &mut Vec<u8>, body_out: &mut String, n: &mut i64| {
        pending.extend_from_slice(bytes);
        let t = take_text(pending);
        emit(t, body_out, n);
    };
    if chunked {
        // size line, data, CRLF; repeated; a zero size ends it
        let mut inbuf = rest;
        'outer: loop {
            loop {
                let Some(i) = inbuf.windows(2).position(|w| w == b"\r\n") else { break };
                let size = usize::from_str_radix(String::from_utf8_lossy(&inbuf[..i]).split(';').next().unwrap_or("").trim(), 16).unwrap_or(0);
                if size == 0 { break 'outer; }
                if inbuf.len() < i + 2 + size + 2 { break }
                let data = inbuf[i + 2..i + 2 + size].to_vec();
                inbuf.drain(..i + 2 + size + 2);
                total += data.len();
                feed(&data, &mut pending, &mut body_out, &mut n_chunk);
            }
            match s.read(&mut chunk) { Ok(0) => break, Ok(n) => inbuf.extend_from_slice(&chunk[..n]), Err(e) => { return refuse(&id, 1, format!("read: {e}")) } }
        }
    } else {
        if !rest.is_empty() { total += rest.len(); let r = std::mem::take(&mut rest); feed(&r, &mut pending, &mut body_out, &mut n_chunk); }
        loop {
            if let Some(w) = want { if total >= w { break; } }
            match s.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => { total += n; feed(&chunk[..n], &mut pending, &mut body_out, &mut n_chunk); if total > MAX_BODY { break; } }
                Err(e) => { if stream || total == 0 { return refuse(&id, 1, format!("read: {e}")); } break; }
            }
        }
    }
    if !pending.is_empty() { let t = String::from_utf8_lossy(&pending).to_string(); emit(t, &mut body_out, &mut n_chunk); }
    if stream {
        let mut p = Map::new();
        p.insert("fetch".into(), json!(eid)); p.insert("id".into(), id.clone()); p.insert("n".into(), json!(n_chunk + 1));
        p.insert("data".into(), json!("")); p.insert("done".into(), json!(true));
        st.push_inbound(format!("Chunk:{eid}:{}", n_chunk + 1), "Chunk".into(), p);
    }
    let mut r = json!({"id": id, "status": "ok", "rc": 0, "head": status_line, "http_status": http_status, "headers": headers});
    if !stream { r["body"] = json!(body_out); }
    r
}
