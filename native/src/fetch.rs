//! The outbound HTTP driver (the model list, the model call and its streamed reply): `Fetch:<x>.request = {id, url, method?,
//! headers?, body?, stream?, lines?, keep?, bearer_env?, timeout_s?}`. The receipt is data. With `stream` the body is pushed into the engine as entities
//! `Chunk:<fetch>:<n>` ({fetch, id, n, data, done}) while it is read (`lines`: each chunk holds whole lines only; `keep`: the receipt ALSO carries the whole body).
//! `bearer_env` names an environment variable whose value is sent as `Authorization: Bearer <value>` (a model endpoint's key, #1143): it is read HERE, so it is on no entity, row,
//! journal line or receipt, and where the server prints it back it is masked (`***`), across the cut of two pieces too. http and https: https is rustls with the webpki roots (feature `tls`, on with `python`; wasm
//! has none and refuses https). `ca_pem` (PEM text) adds trusted roots for a private CA or a test; a failed verification is a
//! receipt (rc 1) whose head names the certificate problem.
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

use serde_json::{json, Map, Value as J};

use crate::store::Store;

const MAX_BODY: usize = 64 * 1024 * 1024;

/// The connection, plain or TLS; a TLS peer that drops the socket without close_notify reads as a plain end of stream.
trait Conn: Read + Write {}
impl<T: Read + Write> Conn for T {}
#[cfg(feature = "tls")]
struct Tls(rustls::StreamOwned<rustls::ClientConnection, TcpStream>);
#[cfg(feature = "tls")]
impl Read for Tls {
    fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
        match self.0.read(b) { Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(0), r => r }
    }
}
#[cfg(feature = "tls")]
impl Write for Tls {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> { self.0.write(b) }
    fn flush(&mut self) -> std::io::Result<()> { self.0.flush() }
}

/// The client config: the webpki roots plus `ca_pem`.
#[cfg(feature = "tls")]
fn tls_config(ca_pem: Option<&str>) -> Result<std::sync::Arc<rustls::ClientConfig>, String> {
    use rustls::pki_types::{pem::PemObject, CertificateDer};
    let mut roots = rustls::RootCertStore::empty();
    roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    if let Some(pem) = ca_pem {
        let mut n = 0;
        for der in CertificateDer::pem_slice_iter(pem.as_bytes()) {
            roots.add(der.map_err(|e| format!("ca_pem: {e}"))?).map_err(|e| format!("ca_pem: {e}"))?;
            n += 1;
        }
        if n == 0 { return Err("ca_pem holds no certificate".into()); }
    }
    let cfg = rustls::ClientConfig::builder_with_provider(std::sync::Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions().map_err(|e| format!("tls: {e}"))?
        .with_root_certificates(roots).with_no_client_auth();
    Ok(std::sync::Arc::new(cfg))
}

/// TLS over `tcp`; the handshake is finished here so its failure is named.
#[cfg(feature = "tls")]
fn handshake(tcp: TcpStream, host: &str, cfg: std::sync::Arc<rustls::ClientConfig>) -> Result<Box<dyn Conn>, String> {
    use rustls::pki_types::ServerName;
    let name = ServerName::try_from(host.to_string()).map_err(|e| format!("tls server name {host:?}: {e}"))?;
    let conn = rustls::ClientConnection::new(cfg, name).map_err(|e| format!("tls: {e}"))?;
    let mut st = rustls::StreamOwned::new(conn, tcp);
    while st.conn.is_handshaking() {
        st.conn.complete_io(&mut st.sock).map_err(|e| format!("tls handshake {host}: {e}"))?;
    }
    Ok(Box::new(Tls(st)))
}

fn refuse(id: &J, rc: i64, why: String) -> J { json!({"id": id, "status": "error", "rc": rc, "head": why}) }

/// `http(s)://host[:port]/path?query` -> (tls, host, port, path+query)
fn parse(url: &str) -> Result<(bool, String, u16, String), String> {
    let (tls, rest) = match (url.strip_prefix("https://"), url.strip_prefix("http://")) {
        (Some(r), _) => (true, r), (_, Some(r)) => (false, r), _ => return Err(format!("not an http url: {url:?}")),
    };
    #[cfg(not(feature = "tls"))]
    if tls { return Err("https is not available in this build (no tls feature)".into()); }
    let (hostport, path) = match rest.find('/') { Some(i) => (&rest[..i], &rest[i..]), None => (rest, "/") };
    if hostport.is_empty() || hostport.contains(' ') { return Err(format!("not a url: {url:?}")); }
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) if !h.ends_with(']') || h.starts_with('[') => (h.trim_matches(|c| c == '[' || c == ']').to_string(), p.parse::<u16>().map_err(|_| format!("bad port in {url:?}"))?),
        _ => (hostport.to_string(), if tls { 443 } else { 80 }),
    };
    Ok((tls, host, port, path.to_string()))
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

/// What may leave `held` now: every secret in it is masked; the tail that could be the start of a secret the next piece completes stays held (`all` releases it: the end).
fn release(held: &mut String, secrets: &[String], all: bool) -> String {
    if secrets.is_empty() { return std::mem::take(held); }
    let masked = secrets.iter().fold(std::mem::take(held), |t, s| t.replace(s.as_str(), crate::drivers::MASK));
    if all { return masked; }
    let max = secrets.iter().map(|s| s.len()).max().unwrap_or(0);
    let keep = (1..max.min(masked.len() + 1)).rev().find(|k| masked.is_char_boundary(masked.len() - k) && secrets.iter().any(|s| s.as_bytes().starts_with(&masked.as_bytes()[masked.len() - k..]))).unwrap_or(0);
    let cut = masked.len() - keep;
    *held = masked[cut..].to_string();
    masked[..cut].to_string()
}

/// A failed stream still ends with a `done` chunk carrying the reason (the consumer waits on `done`).
fn end_failed_stream(st: &Store, eid: &str, id: &J, n: i64, why: &str) {
    let mut p = Map::new();
    p.insert("fetch".into(), json!(eid)); p.insert("id".into(), id.clone()); p.insert("n".into(), json!(n + 1));
    p.insert("data".into(), json!("")); p.insert("done".into(), json!(true)); p.insert("error".into(), json!(why));
    st.push_inbound(format!("Chunk:{eid}:{}", n + 1), "Chunk".into(), p);
}

pub fn request(st: &Store, eid: &str, req: &J) -> J {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let url = req.get("url").and_then(|u| u.as_str()).unwrap_or("");
    let (tls, host, port, path) = match parse(url) { Ok(p) => p, Err(e) => return refuse(&id, 2, e) };
    let timeout = Duration::from_secs(req.get("timeout_s").and_then(|t| t.as_u64()).unwrap_or(60));
    let stream = req.get("stream").and_then(|s| s.as_bool()).unwrap_or(false);
    #[cfg(feature = "tls")]
    let tls_cfg = if tls {
        let ca = match req.get("ca_pem") { None | Some(J::Null) => None, Some(J::String(p)) => Some(p.as_str()), Some(_) => return refuse(&id, 2, "ca_pem must be PEM text".into()) };
        match tls_config(ca) { Ok(c) => Some(c), Err(e) => return refuse(&id, 2, e) }
    } else { None };
    // the bearer, from the environment (nothing is sent without it); a value of MIN_SECRET_CHARS or more is masked where the server prints it back
    let mut secrets: Vec<String> = Vec::new();
    let bearer: Option<String> = match req.get("bearer_env") {
        None | Some(J::Null) => None,
        Some(J::String(var)) => match std::env::var(var) {
            Ok(v) if !v.is_empty() => { if v.chars().count() >= crate::drivers::MIN_SECRET_CHARS { secrets.push(v.clone()); } Some(v) }
            _ => return refuse(&id, 2, format!("environment variable {var} is not set (for Authorization)")),
        },
        Some(_) => return refuse(&id, 2, "bearer_env must name an environment variable".into()),
    };
    let addr = match (host.as_str(), port).to_socket_addrs().ok().and_then(|mut a| a.next()) {
        Some(a) => a, None => return refuse(&id, 1, format!("connect {host}:{port}: cannot resolve")),
    };
    let tcp = match TcpStream::connect_timeout(&addr, timeout) { Ok(s) => s, Err(e) => return refuse(&id, 1, format!("connect {host}:{port}: {e}")) };
    let _ = tcp.set_read_timeout(Some(timeout));
    let _ = tcp.set_write_timeout(Some(timeout));
    let mut s: Box<dyn Conn> = if tls {
        #[cfg(feature = "tls")]
        {
            match handshake(tcp, &host, tls_cfg.unwrap()) { Ok(c) => c, Err(e) => return refuse(&id, 1, e) }
        }
        #[cfg(not(feature = "tls"))]
        unreachable!()
    } else { Box::new(tcp) };
    let method = req.get("method").and_then(|m| m.as_str()).unwrap_or("GET").to_ascii_uppercase();
    let body = req.get("body").map(|b| match b { J::String(t) => t.clone(), other => other.to_string() }).unwrap_or_default();
    let hostport = if port == if tls { 443 } else { 80 } { host.clone() } else { format!("{host}:{port}") };
    let mut head = format!("{method} {path} HTTP/1.1\r\nHost: {hostport}\r\nUser-Agent: membrane\r\nConnection: close\r\nAccept: */*\r\n");
    if let Some(h) = req.get("headers").and_then(|h| h.as_object()) {
        for (k, v) in h {
            if k.eq_ignore_ascii_case("host") || k.eq_ignore_ascii_case("content-length") || k.eq_ignore_ascii_case("connection") { continue; }
            if bearer.is_some() && k.eq_ignore_ascii_case("authorization") { continue; }
            let v = v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
            if k.contains(['\r', '\n']) || v.contains(['\r', '\n']) { continue; }
            head.push_str(&format!("{k}: {v}\r\n"));
        }
    }
    if let Some(b) = &bearer { head.push_str(&format!("Authorization: Bearer {b}\r\n")); }
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
    let status_line = secrets.iter().fold(lines.next().unwrap_or("").to_string(), |t, s| t.replace(s.as_str(), crate::drivers::MASK));
    let status_line = status_line.as_str();
    let http_status: i64 = status_line.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
    let mut headers = Map::new();
    for l in lines { if let Some((k, v)) = l.split_once(':') { headers.insert(k.trim().to_ascii_lowercase(), json!(secrets.iter().fold(v.trim().to_string(), |t, s| t.replace(s.as_str(), crate::drivers::MASK)))); } }
    let mut rest: Vec<u8> = buf[head_end + 4..].to_vec();
    let chunked = headers.get("transfer-encoding").and_then(|v| v.as_str()).map(|v| v.to_ascii_lowercase().contains("chunked")).unwrap_or(false);
    let want: Option<usize> = headers.get("content-length").and_then(|v| v.as_str()).and_then(|v| v.parse().ok());

    let mut body_out = String::new();
    // `keep`: a streamed request's receipt ALSO carries the whole body (a consumer that reads the finished answer from the receipt, as a non-stream request does)
    let want_body = stream && req.get("keep").and_then(|s| s.as_bool()).unwrap_or(false);
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
        }
        if (!stream || want_body) && body_out.len() < MAX_BODY { body_out.push_str(&text); }
    };
    let mut total = 0usize;
    // data bytes -> text pieces, whatever the framing
    // `lines`: a chunk holds whole lines only (the unfinished last line waits for the next piece, the end flushes it): a consumer parses each chunk alone
    let by_lines = stream && req.get("lines").and_then(|s| s.as_bool()).unwrap_or(false);
    let mut held = String::new();
    let mut whole = String::new();
    let mut feed = |bytes: &[u8], pending: &mut Vec<u8>, body_out: &mut String, n: &mut i64, held: &mut String, whole: &mut String| {
        pending.extend_from_slice(bytes);
        held.push_str(&take_text(pending));
        let t = release(held, &secrets, false);
        if by_lines {
            whole.push_str(&t);
            if let Some(i) = whole.rfind('\n') { let out: String = whole.drain(..=i).collect(); emit(out, body_out, n); }
        } else { emit(t, body_out, n); }
    };
    let mut terminated = !chunked;                  // a chunked body must end with its zero-size chunk
    if chunked {
        // size line, data, CRLF; repeated; a zero size ends it
        let mut inbuf = rest;
        'outer: loop {
            loop {
                let Some(i) = inbuf.windows(2).position(|w| w == b"\r\n") else { break };
                let size = usize::from_str_radix(String::from_utf8_lossy(&inbuf[..i]).split(';').next().unwrap_or("").trim(), 16).unwrap_or(0);
                if size == 0 { terminated = true; break 'outer; }
                if inbuf.len() < i + 2 + size + 2 { break }
                let data = inbuf[i + 2..i + 2 + size].to_vec();
                inbuf.drain(..i + 2 + size + 2);
                total += data.len();
                feed(&data, &mut pending, &mut body_out, &mut n_chunk, &mut held, &mut whole);
            }
            match s.read(&mut chunk) { Ok(0) => break, Ok(n) => inbuf.extend_from_slice(&chunk[..n]), Err(e) => { let why = format!("read: {e}"); if stream { end_failed_stream(st, eid, &id, n_chunk, &why); } return refuse(&id, 1, why) } }
        }
    } else {
        if !rest.is_empty() { total += rest.len(); let r = std::mem::take(&mut rest); feed(&r, &mut pending, &mut body_out, &mut n_chunk, &mut held, &mut whole); }
        loop {
            if let Some(w) = want { if total >= w { break; } }
            match s.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => { total += n; feed(&chunk[..n], &mut pending, &mut body_out, &mut n_chunk, &mut held, &mut whole); if total > MAX_BODY { break; } }
                Err(e) => { if stream || total == 0 { let why = format!("read: {e}"); if stream { end_failed_stream(st, eid, &id, n_chunk, &why); } return refuse(&id, 1, why); } break; }
            }
        }
    }
    // a body cut short is a failure, never a quiet end (a truncation an attacker on the path can cause must not read as complete)
    let cut: Option<String> = if !terminated { Some("response truncated: the chunked body ended without its last chunk".into()) }
        else { want.filter(|w| total < *w).map(|w| format!("response truncated: got {total} of {w} bytes")) };
    if let Some(why) = cut {
        if stream { end_failed_stream(st, eid, &id, n_chunk, &why); }
        return refuse(&id, 1, why);
    }
    if !pending.is_empty() { held.push_str(&String::from_utf8_lossy(&pending)); }
    let t = release(&mut held, &secrets, true);
    whole.push_str(&t);
    emit(std::mem::take(&mut whole), &mut body_out, &mut n_chunk);
    if stream {
        let mut p = Map::new();
        p.insert("fetch".into(), json!(eid)); p.insert("id".into(), id.clone()); p.insert("n".into(), json!(n_chunk + 1));
        p.insert("data".into(), json!("")); p.insert("done".into(), json!(true));
        st.push_inbound(format!("Chunk:{eid}:{}", n_chunk + 1), "Chunk".into(), p);
    }
    let mut r = json!({"id": id, "status": "ok", "rc": 0, "head": status_line, "http_status": http_status, "headers": headers});
    if !stream || want_body { r["body"] = json!(body_out); }
    r
}
