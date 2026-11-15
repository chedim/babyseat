//! THE VECTOR STORE (#1143, operator 2026-11-14): the native half of the semantic field. A class a pack declares `MAPPED <Class> f16` keeps its entities' `v` (a list of
//! numbers) here, as one flat f16 matrix per class, not in the entity's row. Plain Rust (the browser engine compiles it; there `v` and `near` say they are not available).
//!
//! CPU only: brute force, a fixed summation order, so one device gives the same answer for the same inputs (stable, which early cutoff needs); another device may differ in the
//! last bits (no promise), and ties break by eid.
use std::collections::{BTreeMap, HashMap};

/// The compact form of a `v` in flight (a write, a journal line): `{"$f16": "<base64 of the f16 bytes, little endian>"}`.
pub const COMPACT_KEY: &str = "$f16";
/// The only dtype so far (a header change later: `MAPPED Vector <dtype>`).
pub const DTYPE: &str = "f16";

/// f32 -> f16 bits, round to nearest even. None when the value does not fit (infinite, NaN, or beyond 65504).
pub fn f32_to_f16(x: f32) -> Option<u16> {
    if !x.is_finite() { return None; }
    let b = x.to_bits();
    let s = ((b >> 16) & 0x8000) as u16;
    let e = ((b >> 23) & 0xff) as i32;
    let m = b & 0x7f_ffff;
    if e == 0 { return Some(s); }                       // an f32 subnormal is far below the f16 range: zero
    let ne = e - 127 + 15;
    if ne >= 31 { return None; }
    if ne <= 0 {
        if ne < -10 { return Some(s); }
        let m = m | 0x80_0000;
        let sh = (14 - ne) as u32;
        let mut h = (m >> sh) as u16;
        let rem = m & ((1 << sh) - 1);
        let half = 1u32 << (sh - 1);
        if rem > half || (rem == half && h & 1 == 1) { h += 1; }
        return Some(s | h);
    }
    let mut h = ((ne as u32) << 10) | (m >> 13);
    let rem = m & 0x1fff;
    if rem > 0x1000 || (rem == 0x1000 && h & 1 == 1) { h += 1; }
    if h >= 0x7c00 { return None; }                     // rounded up to infinity
    Some(s | h as u16)
}

pub fn f16_to_f32(h: u16) -> f32 {
    let s = ((h >> 15) & 1) as u32;
    let e = ((h >> 10) & 0x1f) as i32;
    let m = (h & 0x3ff) as u32;
    let bits = if e == 0 {
        if m == 0 { s << 31 } else {
            let (mut e2, mut m2) = (127 - 15 + 1, m);
            while m2 & 0x400 == 0 { m2 <<= 1; e2 -= 1; }
            (s << 31) | ((e2 as u32) << 23) | ((m2 & 0x3ff) << 13)
        }
    } else if e == 31 { (s << 31) | (0xff << 23) | (m << 13) } else { (s << 31) | (((e + 127 - 15) as u32) << 23) | (m << 13) };
    f32::from_bits(bits)
}

/// A list of numbers -> f16 words. Refuses (with the reason) a non-number, an infinite or too large one, an empty list.
pub fn to_f16(list: &[serde_json::Value]) -> Result<Vec<u16>, String> {
    if list.is_empty() { return Err("an empty list is no vector".into()); }
    let mut out = Vec::with_capacity(list.len());
    for (i, x) in list.iter().enumerate() {
        let f = x.as_f64().ok_or_else(|| format!("item {i} is not a number ({x}): a vector is a flat list of numbers"))?;
        out.push(f32_to_f16(f as f32).ok_or_else(|| format!("item {i} ({f}) does not fit f16 (the range is +-65504)"))?);
    }
    Ok(out)
}

pub fn words_to_bytes(w: &[u16]) -> Vec<u8> { w.iter().flat_map(|x| x.to_le_bytes()).collect() }
pub fn bytes_to_words(b: &[u8]) -> Result<Vec<u16>, String> {
    if b.len() % 2 != 0 || b.is_empty() { return Err("f16 bytes: an even, non-zero length".into()); }
    Ok(b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect())
}

/// The compact object of a vector (what the engine carries and the journal keeps).
pub fn compact_of(words: &[u16]) -> serde_json::Value { serde_json::json!({ COMPACT_KEY: crate::jwt::base64_encode(&words_to_bytes(words)) }) }
/// The f16 words of a compact object (None when the value is not one).
pub fn words_of_compact(v: &serde_json::Value) -> Option<Result<Vec<u16>, String>> {
    let s = v.as_object().filter(|o| o.len() == 1)?.get(COMPACT_KEY)?.as_str()?;
    Some(crate::jwt::base64_decode(s, false).ok_or_else(|| "base64: not valid base64".to_string()).and_then(|b| bytes_to_words(&b)))
}
/// What a stored row and a door read of `v` show instead of the numbers.
pub fn summary(n: usize) -> serde_json::Value { serde_json::json!(format!("[{n} numbers, {DTYPE}]")) }

/// One class's vectors: the f16 matrix (row i = eid i), each row's norm, and the way from an eid to its row. A removal moves the last row into the hole (the matrix stays dense;
/// the order of the rows means nothing: every answer is ordered by score, then eid).
#[derive(Default)]
pub struct ClassVecs {
    pub dim: usize,
    eids: Vec<String>,
    data: Vec<u16>,
    norms: Vec<f32>,
    at: HashMap<String, usize>,
}

fn norm_of(row: &[u16]) -> f32 { let mut s = 0f32; for h in row { let v = f16_to_f32(*h); s += v * v; } s.sqrt() }

impl ClassVecs {
    pub fn len(&self) -> usize { self.eids.len() }
    pub fn bytes(&self) -> usize { self.data.len() * 2 + self.norms.len() * 4 + self.eids.iter().map(|e| e.len() + 48).sum::<usize>() }
    pub fn row(&self, eid: &str) -> Option<&[u16]> { self.at.get(eid).map(|i| &self.data[i * self.dim..(i + 1) * self.dim]) }
    fn put(&mut self, eid: &str, words: &[u16]) {
        let n = norm_of(words);
        match self.at.get(eid) {
            Some(&i) => { self.data[i * self.dim..(i + 1) * self.dim].copy_from_slice(words); self.norms[i] = n; }
            None => { self.at.insert(eid.to_string(), self.eids.len()); self.eids.push(eid.to_string()); self.data.extend_from_slice(words); self.norms.push(n); }
        }
    }
    fn remove(&mut self, eid: &str) -> bool {
        let Some(i) = self.at.remove(eid) else { return false };
        let last = self.eids.len() - 1;
        if i != last {
            let (a, b) = (i * self.dim, last * self.dim);
            self.data.copy_within(b..b + self.dim, a);
            self.eids.swap(i, last);
            self.norms.swap(i, last);
            self.at.insert(self.eids[i].clone(), i);
        }
        self.eids.pop(); self.norms.pop(); self.data.truncate(last * self.dim);
        if self.eids.is_empty() { self.dim = 0; }   // the last vector left: the class forgets its dimension (the next first vector fixes it again)
        true
    }
    /// The `k` nearest by cosine, best first, ties by eid. `q` has this class's dimension and a non-zero norm (the caller checks).
    pub fn near(&self, q: &[f32], k: usize) -> Vec<(String, f32)> {
        let qn = q.iter().map(|x| x * x).sum::<f32>().sqrt();
        let mut scored: Vec<(f32, usize)> = Vec::with_capacity(self.eids.len());
        for (i, row) in self.data.chunks_exact(self.dim).enumerate() {
            // four lanes in a fixed order: the same inputs give the same bits on one device
            let mut lane = [0f32; 4];
            for (c, h) in row.chunks(4).zip(q.chunks(4)) { for j in 0..c.len() { lane[j] += f16_to_f32(c[j]) * h[j]; } }
            let d = (lane[0] + lane[1]) + (lane[2] + lane[3]);
            scored.push((d / (self.norms[i].max(1e-12) * qn), i));
        }
        let by = |a: &(f32, usize), b: &(f32, usize)| b.0.total_cmp(&a.0).then_with(|| self.eids[a.1].cmp(&self.eids[b.1]));
        let k = k.min(scored.len());
        if k < scored.len() { scored.select_nth_unstable_by(k - 1, by); scored.truncate(k); }
        scored.sort_by(by);
        scored.into_iter().map(|(s, i)| (self.eids[i].clone(), s)).collect()
    }
}

#[derive(Default)]
pub struct VecStores { pub classes: BTreeMap<String, ClassVecs> }

impl VecStores {
    pub fn class(&self, cls: &str) -> Option<&ClassVecs> { self.classes.get(cls) }
    pub fn dim(&self, cls: &str) -> Option<usize> { self.classes.get(cls).map(|c| c.dim).filter(|d| *d > 0) }
    pub fn get(&self, cls: &str, eid: &str) -> Option<&[u16]> { self.classes.get(cls)?.row(eid) }
    /// Store `words` as `eid`'s vector. The first vector of a class fixes its dimension; another length is refused.
    pub fn put(&mut self, cls: &str, eid: &str, words: &[u16]) -> Result<(), String> {
        let c = self.classes.entry(cls.to_string()).or_default();
        if c.dim == 0 { c.dim = words.len(); }
        if words.len() != c.dim { return Err(format!("{eid}.v has {} numbers but the vectors of {cls} have {} (the first vector written fixes the dimension)", words.len(), c.dim)); }
        c.put(eid, words);
        Ok(())
    }
    /// Remove an entity's vector wherever it is (its class is the eid's; a removal of an unknown eid does nothing). -> whether one was removed.
    pub fn remove(&mut self, cls: &str, eid: &str) -> bool { self.classes.get_mut(cls).map_or(false, |c| c.remove(eid)) }
    /// A retired entity and its colon-path children (`A:1` and `A:1:x`, not `A:10`): their vectors leave, loaded or not. -> how many.
    pub fn remove_tree(&mut self, cls: &str, eid: &str) -> usize {
        let Some(c) = self.classes.get_mut(cls) else { return 0 };
        let below = format!("{eid}:");
        let gone: Vec<String> = c.at.keys().filter(|k| *k == eid || k.starts_with(&below)).cloned().collect();
        for g in &gone { c.remove(g); }
        gone.len()
    }
    pub fn bytes(&self) -> usize { self.classes.values().map(|c| c.bytes()).sum() }
}
