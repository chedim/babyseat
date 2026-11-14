//! A counting global allocator (#1143), a diagnostic behind the cargo feature `alloc-stats`: OFF by default, and then this module is not compiled at all.
//! It wraps the system allocator and counts, with three relaxed atomics, the bytes live now, the peak of live bytes and the total ever allocated.
//! Live bytes against the process's RSS tells retained data (live grows with RSS) from the allocator holding freed pages (live small, RSS large).
//! `bin/cliffvm.rs` installs it and starts `report`, one line to stderr per second: `alloc-stats t=<epoch s> live=<bytes> peak=<bytes> total=<bytes> allocs=<n>`.
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

pub struct Counting;
static LIVE: AtomicU64 = AtomicU64::new(0);
static PEAK: AtomicU64 = AtomicU64::new(0);
static TOTAL: AtomicU64 = AtomicU64::new(0);
static ALLOCS: AtomicU64 = AtomicU64::new(0);

fn grow(n: u64) {
    let live = LIVE.fetch_add(n, Relaxed) + n;
    PEAK.fetch_max(live, Relaxed);
    TOTAL.fetch_add(n, Relaxed);
    ALLOCS.fetch_add(1, Relaxed);
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 { let p = System.alloc(l); if !p.is_null() { grow(l.size() as u64); } p }
    unsafe fn alloc_zeroed(&self, l: Layout) -> *mut u8 { let p = System.alloc_zeroed(l); if !p.is_null() { grow(l.size() as u64); } p }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) { System.dealloc(p, l); LIVE.fetch_sub(l.size() as u64, Relaxed); }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, new: usize) -> *mut u8 {
        let q = System.realloc(p, l, new);
        if !q.is_null() { LIVE.fetch_sub(l.size() as u64, Relaxed); grow(new as u64); }
        q
    }
}

/// (live, peak, total, allocation count)
pub fn snapshot() -> (u64, u64, u64, u64) { (LIVE.load(Relaxed), PEAK.load(Relaxed), TOTAL.load(Relaxed), ALLOCS.load(Relaxed)) }

/// The one-line report `snapshot` prints.
pub fn line() -> String {
    let (live, peak, total, allocs) = snapshot();
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    format!("alloc-stats t={t:.1} live={live} peak={peak} total={total} allocs={allocs}")
}

/// A thread that prints `line()` to stderr every second.
pub fn report() { std::thread::spawn(|| loop { eprintln!("{}", line()); std::thread::sleep(std::time::Duration::from_secs(1)); }); }

/// The size of a JSON value in bytes, counted without allocating (a string's length, a number as 8, the keys of an object): an estimate of its heap.
pub fn jsize(v: &serde_json::Value) -> u64 {
    use serde_json::Value as J;
    match v {
        J::String(s) => s.len() as u64 + 24,
        J::Array(a) => 24 + a.iter().map(|x| jsize(x) + 32).sum::<u64>(),
        J::Object(m) => 48 + m.iter().map(|(k, x)| k.len() as u64 + 24 + jsize(x) + 32).sum::<u64>(),
        _ => 32,
    }
}

/// One named size of a structure: (name, entries, bytes).
pub type Size = (String, u64, u64);
