//! Eval accounting (#1143): how much work each getter and setter does, always on and cheap. Per (kind, class, property, pack) the table counts the
//! evaluations, their SELF time (the evaluation's own clock time, minus the time it waited in host calls, which is counted apart as `wait`, minus
//! the evaluations nested inside it, which count themselves) and their steps, and how many of them were SLOW (self time over the former allowance: measured, never a limit). Nothing here changes a value, writes a row or a journal line, or
//! reacts: the host reads the table on a tick and tells it to `Stats:eval` (`host.rs`, shipped pack `stats`).
//!
//! The table is a fixed `Vec` of `SLOTS` entries, open addressing on an FNV hash of the key: a key's strings are copied once, when its slot is first taken;
//! an evaluation of a known key allocates nothing. When `FULL` slots are taken, a new key is counted in the one `(other)` row (nothing is lost, nothing grows).
//! Each entry has a WINDOW count (what the host takes and clears every window) and a LIFETIME count (kept until `clear`).
use crate::expr;
use serde_json::{json, Value as J};
use std::cell::Cell;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};

pub const KIND_GETTER: &str = "getter";
pub const KIND_SETTER: &str = "setter";
/// Slots in the table, and how many keys may take one (the rest share `(other)`): keys are (kind, class, property, pack): the shipped packs have a few hundred.
pub const SLOTS: usize = 2048;
pub const FULL: usize = 1024;
/// The class of the row that counts every key that did not fit.
pub const OTHER: &str = "(other)";

/// One key's counts as the host reads them. `ns` is self time without the waits, `wait_ns` the time in host calls (a `sql(...)`, a file read).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Row { pub kind: String, pub cls: String, pub prop: String, pub pack: String, pub calls: u64, pub ns: u64, pub wait_ns: u64, pub steps: u64, pub slow: u64 }

#[derive(Clone, Copy, Default)]
struct Acc { calls: u64, ns: u64, wait_ns: u64, steps: u64, slow: u64 }
impl Acc {
    fn add(&mut self, ns: u64, wait_ns: u64, steps: u64) {
        self.calls += 1; self.ns = self.ns.saturating_add(ns); self.wait_ns = self.wait_ns.saturating_add(wait_ns); self.steps = self.steps.saturating_add(steps);
        // SLOW: this ONE evaluation's self time passed the former allowance (`expr::eval_time_ms`, default 10 ms). A flag for the reader, never a limit.
        if ns > expr::eval_time_ms().saturating_mul(1_000_000) { self.slow += 1; }
    }
}
struct Entry { kind: u8, cls: Box<str>, prop: Box<str>, pack: Box<str>, win: Acc, life: Acc }
impl Entry {
    fn row(&self, a: Acc) -> Row {
        Row { kind: (if self.kind == 0 { KIND_GETTER } else { KIND_SETTER }).into(), cls: self.cls.to_string(), prop: self.prop.to_string(), pack: self.pack.to_string(),
              calls: a.calls, ns: a.ns, wait_ns: a.wait_ns, steps: a.steps, slow: a.slow }
    }
}

pub struct Table { slots: Vec<Option<Entry>>, taken: usize, other: Entry }
/// A key's place in the table: a slot, or the `(other)` row.
#[derive(Clone, Copy, Debug)]
pub struct Slot(usize);
const OTHER_SLOT: usize = usize::MAX;

fn kind_code(kind: &str) -> u8 { if kind == KIND_GETTER { 0 } else { 1 } }
fn hash(kind: u8, cls: &str, prop: &str, pack: &str) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    let mut eat = |b: u8| { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); };
    eat(kind);
    for s in [cls, prop, pack] { for &b in s.as_bytes() { eat(b); } eat(0xff); }
    h
}

impl Default for Table { fn default() -> Table { Table::new() } }
impl Table {
    pub fn new() -> Table {
        Table { slots: (0..SLOTS).map(|_| None).collect(), taken: 0,
                other: Entry { kind: 0, cls: OTHER.into(), prop: "".into(), pack: "".into(), win: Acc::default(), life: Acc::default() } }
    }
    /// The slot of a key (taking one the first time). No allocation for a key already in.
    pub fn intern(&mut self, kind: &str, cls: &str, prop: &str, pack: &str) -> Slot {
        let k = kind_code(kind);
        let mut i = (hash(k, cls, prop, pack) as usize) % SLOTS;
        loop {
            match &self.slots[i] {
                Some(e) if e.kind == k && &*e.cls == cls && &*e.prop == prop && &*e.pack == pack => return Slot(i),
                Some(_) => i = (i + 1) % SLOTS,
                None => {
                    if self.taken >= FULL { return Slot(OTHER_SLOT); }
                    self.slots[i] = Some(Entry { kind: k, cls: cls.into(), prop: prop.into(), pack: pack.into(), win: Acc::default(), life: Acc::default() });
                    self.taken += 1;
                    return Slot(i);
                }
            }
        }
    }
    pub fn add(&mut self, s: Slot, ns: u64, wait_ns: u64, steps: u64) {
        let e = if s.0 == OTHER_SLOT { &mut self.other } else { match self.slots[s.0].as_mut() { Some(e) => e, None => return } };
        e.win.add(ns, wait_ns, steps);
        e.life.add(ns, wait_ns, steps);
    }
    pub fn record(&mut self, kind: &str, cls: &str, prop: &str, pack: &str, ns: u64, wait_ns: u64, steps: u64) {
        let s = self.intern(kind, cls, prop, pack);
        self.add(s, ns, wait_ns, steps);
    }
    /// What the window counted, cleared: the next window starts at zero. The lifetime keeps it.
    pub fn take_window(&mut self) -> Vec<Row> {
        let mut out = vec![];
        for e in self.slots.iter_mut().flatten().chain(std::iter::once(&mut self.other)) {
            if e.win.calls > 0 { out.push(e.row(e.win)); }
            e.win = Acc::default();
        }
        out
    }
    /// Everything counted since the start (or the last `clear`).
    pub fn lifetime(&self) -> Vec<Row> {
        self.slots.iter().flatten().chain(std::iter::once(&self.other)).filter(|e| e.life.calls > 0).map(|e| e.row(e.life)).collect()
    }
    /// Forget everything: the explicit reset.
    pub fn clear(&mut self) { *self = Table::new(); }
}

/// The top `n` rows by self time, as the list `Stats:eval.top` holds: {class, prop, pack, kind, calls, ms, wait_ms, steps, slow}.
pub fn rows_json(rows: &[Row], n: usize) -> J {
    let mut v: Vec<&Row> = rows.iter().collect();
    v.sort_by(|a, b| b.ns.cmp(&a.ns).then(b.calls.cmp(&a.calls)).then(a.cls.cmp(&b.cls)).then(a.prop.cmp(&b.prop)));
    let ms = |ns: u64| (ns as f64 / 1e6 * 1000.0).round() / 1000.0;
    J::Array(v.into_iter().take(n).map(|r| json!({"class": r.cls, "prop": r.prop, "pack": r.pack, "kind": r.kind, "calls": r.calls, "ms": ms(r.ns), "wait_ms": ms(r.wait_ns), "steps": r.steps, "slow": r.slow})).collect())
}

// ---- the process's one table, and the guard the field's evaluations take --------------------------------------------------------
static ON: AtomicBool = AtomicBool::new(false);
static GLOBAL: OnceLock<Mutex<Table>> = OnceLock::new();
fn table() -> std::sync::MutexGuard<'static, Table> { GLOBAL.get_or_init(|| Mutex::new(Table::new())).lock().unwrap_or_else(|e| e.into_inner()) }
/// Count (or stop counting). Off by default for a library user; the host turns it on unless the boot fact `eval_stats` is false.
pub fn set_on(on: bool) { ON.store(on, Ordering::Relaxed); }
pub fn is_on() -> bool { ON.load(Ordering::Relaxed) }
pub fn global_lifetime() -> Vec<Row> { table().lifetime() }
pub fn global_take_window() -> Vec<Row> { table().take_window() }
pub fn global_clear() { table().clear(); }

thread_local! {
    /// the steps every evaluation on this thread has taken (`expr::eval` adds its own when it ends)
    static STEPS: Cell<u64> = const { Cell::new(0) };
    /// what the scopes nested in the current one took in all: (clock ns, waited ns, steps), so the outer one counts only its own
    static CHILD: Cell<(u64, u64, u64)> = const { Cell::new((0, 0, 0)) };
}
/// `expr::eval` tells the steps one evaluation took.
pub fn add_steps(n: u64) { STEPS.with(|s| s.set(s.get().wrapping_add(n))); }

/// The guard of one getter or setter run: counts it when dropped.
pub struct Scope { slot: Slot, t0: u64, w0: u64, s0: u64, saved: (u64, u64, u64) }
pub fn scope(kind: &'static str, cls: &str, prop: &str, pack: &str) -> Option<Scope> {
    if !is_on() { return None; }
    let slot = table().intern(kind, cls, prop, pack);
    let saved = CHILD.with(|c| c.replace((0, 0, 0)));
    Some(Scope { slot, t0: expr::clock_ns(), w0: expr::waited_ns(), s0: STEPS.with(|s| s.get()), saved })
}
impl Drop for Scope {
    fn drop(&mut self) {
        let tot = (expr::clock_ns().saturating_sub(self.t0), expr::waited_ns().saturating_sub(self.w0), STEPS.with(|s| s.get()).wrapping_sub(self.s0));
        let kids = CHILD.with(|c| c.get());
        let own = (tot.0.saturating_sub(kids.0), tot.1.saturating_sub(kids.1), tot.2.saturating_sub(kids.2));
        CHILD.with(|c| c.set((self.saved.0 + tot.0, self.saved.1 + tot.1, self.saved.2 + tot.2)));
        table().add(self.slot, own.0.saturating_sub(own.1), own.1, own.2);
    }
}

// ---- the host: `Stats:eval` ----------------------------------------------------------------------------------------------
/// The window the host publishes at, in seconds (the boot fact `eval_stats_window_s`; a threshold, FLAGGED for the operator).
#[cfg(feature = "store")]
pub const DEFAULT_WINDOW_S: f64 = 10.0;
/// The entity the accounting is told to, and the writer of its writes (an outside-style stamp, never journalled: like an unload, not part of a replay).
#[cfg(feature = "store")]
pub const ENTITY: &str = "Stats:eval";
#[cfg(feature = "store")]
const WRITER: &str = "host:stats";
#[cfg(feature = "store")]
static STATE: Mutex<Option<(f64, f64)>> = Mutex::new(None);   // (window_s, when the window began)
#[cfg(feature = "store")]
const TOP: usize = 20;

#[cfg(feature = "store")]
impl crate::host::Host {
    /// Count evaluations and publish them on `Stats:eval` every `window_s` seconds (the boot fact `eval_stats`, default on, turns it off by never calling this).
    /// The pack `stats` must be loaded (it declares the class RESIDENT). The entity is born here, not journalled.
    pub fn enable_stats(&self, window_s: f64) {
        set_on(true);
        let now = self.now();
        *STATE.lock().unwrap_or_else(|e| e.into_inner()) = Some((window_s, now));
        let props = json!({"window_s": 0.0, "top": [], "total_calls": 0, "total_ms": 0.0, "top_total": [], "life_calls": 0, "life_ms": 0.0, "reset": false, "at": now});
        self.inject_born(now, ENTITY, "Stats", props.as_object().cloned().unwrap_or_default(), WRITER, WRITER);
    }

    /// On the host's tick: a reset asked for (`Stats:eval.reset = true`) clears the table and the flag; a window that is over is published and starts again.
    pub(crate) fn stats_tick(&self) {
        let Some((window_s, since)) = *STATE.lock().unwrap_or_else(|e| e.into_inner()) else { return };
        let now = self.now();
        let reset = self.get(ENTITY, "reset") == json!(true);
        if !reset && now - since < window_s { return; }
        if reset { global_clear(); }
        let win = global_take_window();
        let life = global_lifetime();
        let ms = |ns: u64| (ns as f64 / 1e6 * 1000.0).round() / 1000.0;
        let sum = |v: &[Row], f: fn(&Row) -> u64| v.iter().map(f).sum::<u64>();
        let put = |p: &str, v: J| self.inject_write(now, ENTITY, p, v, WRITER, WRITER);
        put("window_s", json!(now - since));
        put("top", rows_json(&win, TOP));
        put("total_calls", json!(sum(&win, |r| r.calls)));
        put("total_ms", json!(ms(sum(&win, |r| r.ns))));
        put("top_total", rows_json(&life, TOP));
        put("life_calls", json!(sum(&life, |r| r.calls)));
        put("life_ms", json!(ms(sum(&life, |r| r.ns))));
        put("at", json!(now));
        if reset { put("reset", json!(false)); }
        *STATE.lock().unwrap_or_else(|e| e.into_inner()) = Some((window_s, now));
    }
}
