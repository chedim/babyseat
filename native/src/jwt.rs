//! JWT verification for the door (operator, 2026-11-12: cliff talks to rust directly). The rules of membrane/jwtauth.py: HS256
//! only, a shared secret of at least 32 bytes, `exp` and `sub` required, `nbf`/`iat` checked with a 30 s leeway, a token of at
//! most 8192 bytes, a refusal is only ever "invalid token". std only (SHA-256, HMAC, base64url written here).
use serde_json::Value as J;

const MIN_SECRET: usize = 32;
const LEEWAY: f64 = 30.0;
const MAX_TOKEN: usize = 8192;
pub const INVALID: &str = "invalid token";

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

pub fn sha256(data: &[u8]) -> [u8; 32] {
    let mut h: [u32; 8] = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 { msg.push(0); }
    msg.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
    for block in msg.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 { w[i] = u32::from_be_bytes([block[4 * i], block[4 * i + 1], block[4 * i + 2], block[4 * i + 3]]); }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g; g = f; f = e; e = d.wrapping_add(t1); d = c; c = b; b = a; a = t1.wrapping_add(t2);
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) { *x = x.wrapping_add(y); }
    }
    let mut out = [0u8; 32];
    for (i, x) in h.iter().enumerate() { out[4 * i..4 * i + 4].copy_from_slice(&x.to_be_bytes()); }
    out
}

pub fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut k = if key.len() > 64 { sha256(key).to_vec() } else { key.to_vec() };
    k.resize(64, 0);
    let mut inner: Vec<u8> = k.iter().map(|b| b ^ 0x36).collect();
    inner.extend_from_slice(msg);
    let mut outer: Vec<u8> = k.iter().map(|b| b ^ 0x5c).collect();
    outer.extend_from_slice(&sha256(&inner));
    sha256(&outer)
}

pub fn hex(b: &[u8]) -> String { b.iter().map(|x| format!("{:02x}", x)).collect() }

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn base64_encode(data: &[u8]) -> String {
    let mut out = String::new();
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        for i in 0..4 {
            if i <= c.len() { out.push(B64[((n >> (18 - 6 * i)) & 63) as usize] as char); } else { out.push('='); }
        }
    }
    out
}

/// Standard or url-safe base64, padding tolerated for the standard form only.
pub fn base64_decode(s: &str, url: bool) -> Option<Vec<u8>> {
    let mut vals = Vec::new();
    let t = if url { s } else { s.trim_end_matches('=') };
    for ch in t.chars() {
        let v = match ch {
            'A'..='Z' => ch as u32 - 'A' as u32, 'a'..='z' => ch as u32 - 'a' as u32 + 26, '0'..='9' => ch as u32 - '0' as u32 + 52,
            '+' if !url => 62, '/' if !url => 63, '-' if url => 62, '_' if url => 63,
            _ => return None,
        };
        vals.push(v);
    }
    if vals.len() % 4 == 1 { return None; }
    let mut out = Vec::new();
    for c in vals.chunks(4) {
        let n = c.iter().enumerate().fold(0u32, |a, (i, v)| a | v << (18 - 6 * i));
        out.push((n >> 16) as u8);
        if c.len() > 2 { out.push((n >> 8) as u8); }
        if c.len() > 3 { out.push(n as u8); }
    }
    Some(out)
}

fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

/// Verify a token at `now` (unix seconds): -> its claims, or an error (log it; tell the client only `INVALID`).
pub fn verify(token: &str, secret: &[u8], now: f64) -> Result<J, String> {
    if secret.len() < MIN_SECRET { return Err("the secret is shorter than 32 bytes".into()); }
    if token.is_empty() || token.len() > MAX_TOKEN { return Err("token size".into()); }
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() != 3 { return Err("not three parts".into()); }
    let hdr_raw = base64_decode(parts[0], true).ok_or("header is not base64url")?;
    let hdr: J = serde_json::from_slice(&hdr_raw).map_err(|_| "header is not json")?;
    if hdr.get("alg").and_then(|a| a.as_str()) != Some("HS256") { return Err("alg is not HS256".into()); }
    let sig = base64_decode(parts[2], true).ok_or("signature is not base64url")?;
    let want = hmac_sha256(secret, format!("{}.{}", parts[0], parts[1]).as_bytes());
    if !same(&sig, &want) { return Err("signature".into()); }
    let claims: J = serde_json::from_slice(&base64_decode(parts[1], true).ok_or("payload is not base64url")?).map_err(|_| "payload is not json")?;
    let num = |k: &str| claims.get(k).and_then(|v| v.as_f64());
    let exp = num("exp").ok_or("exp is required")?;
    if now > exp + LEEWAY { return Err("expired".into()); }
    if let Some(nbf) = num("nbf") { if now + LEEWAY < nbf { return Err("not yet valid".into()); } }
    if let Some(iat) = num("iat") { if now + LEEWAY < iat { return Err("issued in the future".into()); } }
    match claims.get("sub").and_then(|s| s.as_str()) {
        Some(s) if !s.is_empty() && s.chars().count() <= 256 && !s.chars().any(|c| c.is_control()) => {}
        _ => return Err("sub is required".into()),
    }
    Ok(claims)
}
