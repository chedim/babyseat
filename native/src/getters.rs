//! #994 rules 1 and 2 of section 2, native VM field engine (no Python).
//!  1. A getter's own field is never one of its inputs: reading it returns the PREVIOUS value and
//!     does not subscribe. A getter recalculates only when a field other than itself is written.
//!  2. One round per outside write: every downstream getter runs at most once, in the fixed
//!     evaluation order (registration order). The edge that closes a cycle reads the previous value.
//!     A getter's own result is not an outside write: it marks later getters dirty, never starts a round.
//! Inputs are discovered by what a getter reads (`Ctx::get`, `Ctx::children`); a read of the
//! getter's own output path is served from its previous value and recorded as nothing.
use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value as J;

/// What a getter sees: the field (paths -> values) and a record of what it read.
pub struct Ctx<'a> {
    store: &'a BTreeMap<String, J>,
    own: &'a str,
    pub reads: BTreeSet<String>,
}

impl<'a> Ctx<'a> {
    /// the value at `path`; the getter's own path answers its previous value, unsubscribed
    pub fn get(&mut self, path: &str) -> Option<J> {
        if path != self.own { self.reads.insert(path.to_string()); }
        self.store.get(path).cloned()
    }
    /// names directly under `dir`: stored child paths, plus the elements of an array stored AT `dir`
    pub fn children(&mut self, dir: &str) -> Vec<String> {
        if dir != self.own { self.reads.insert(format!("{}/", dir)); }
        let pre = format!("{}/", dir);
        let mut out: BTreeSet<String> = BTreeSet::new();
        for (k, _) in self.store.range(pre.clone()..) {
            if !k.starts_with(&pre) { break; }
            if let Some(n) = k[pre.len()..].split('/').next() { out.insert(n.to_string()); }
        }
        if let Some(J::Array(a)) = self.store.get(dir) {
            for e in a { if let Some(s) = e.as_str() { out.insert(s.to_string()); } }
        }
        out.into_iter().collect()
    }
}

pub type GetFn = Box<dyn Fn(&mut Ctx) -> J>;

pub struct Getter { pub name: String, pub out: String, f: GetFn, reads: BTreeSet<String> }

#[derive(Default)]
pub struct Engine {
    pub store: BTreeMap<String, J>,
    getters: Vec<Getter>,
    /// every outside write, in order: the replay input
    pub journal: Vec<(String, J)>,
    /// recalculations per getter
    pub recalcs: BTreeMap<String, u64>,
    /// (round, getter, new value) for every recalculation that changed the value
    pub flips: Vec<(u64, String, J)>,
    pub rounds: u64,
}

fn hit(read: &str, path: &str) -> bool {
    if let Some(dir) = read.strip_suffix('/') { path.starts_with(read) || path == dir } else { path == read }
}

impl Engine {
    pub fn new() -> Self { Self::default() }

    /// Register a getter (its output path is the field it is the value of). No initial evaluation:
    /// the engine starts from what the store holds (`seed`), and runs only on outside writes.
    pub fn add(&mut self, name: &str, out: &str, f: impl Fn(&mut Ctx) -> J + 'static) {
        self.getters.push(Getter { name: name.into(), out: out.into(), f: Box::new(f), reads: BTreeSet::new() });
    }
    /// a seeded value that is not an outside write: no round
    pub fn seed(&mut self, path: &str, v: J) { self.store.insert(path.into(), v); }

    /// An outside write (journalled). Starts exactly one round.
    pub fn write(&mut self, path: &str, v: J) {
        self.journal.push((path.to_string(), v.clone()));
        self.store.insert(path.to_string(), v);
        self.rounds += 1;
        let round = self.rounds;
        let mut dirty: BTreeSet<usize> = BTreeSet::new();
        for (i, g) in self.getters.iter().enumerate() {
            if g.out == path { continue; }   // a field is never its own getter's input
            // a getter that has never run has no recorded inputs yet: it runs on the first write
            if g.reads.is_empty() || g.reads.iter().any(|r| hit(r, path)) { dirty.insert(i); }
        }
        // one pass in the fixed order: each getter at most once; a change dirties LATER getters only
        for i in 0..self.getters.len() {
            if !dirty.contains(&i) { continue; }
            let mut ctx = Ctx { store: &self.store, own: &self.getters[i].out, reads: BTreeSet::new() };
            let v = (self.getters[i].f)(&mut ctx);
            let reads = ctx.reads;
            let (name, out) = (self.getters[i].name.clone(), self.getters[i].out.clone());
            self.getters[i].reads = reads;
            *self.recalcs.entry(name.clone()).or_insert(0) += 1;
            if self.store.get(&out) != Some(&v) {
                self.store.insert(out.clone(), v.clone());
                self.flips.push((round, name, v));
                for j in (i + 1)..self.getters.len() {
                    if self.getters[j].out != out && self.getters[j].reads.iter().any(|r| hit(r, &out)) { dirty.insert(j); }
                }
            }
        }
    }

    /// FNV-1a over the whole field and the flip log: same journal, same checksum.
    pub fn checksum(&self) -> u64 {
        let mut h: u64 = 0xcbf29ce484222325;
        let mut feed = |s: &str| { for b in s.bytes().chain([0u8]) { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); } };
        for (k, v) in &self.store { feed(k); feed(&v.to_string()); }
        for (r, n, v) in &self.flips { feed(&r.to_string()); feed(n); feed(&v.to_string()); }
        h
    }
}
