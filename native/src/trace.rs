//! Phase trace (#1143), a diagnostic behind the cargo feature `phase-trace`: OFF by default, and then this module is not compiled and the `span!`/`gauge!` marks in the
//! engine are empty. It answers "what is the engine doing right now" when the seat does not answer: each MARK counts its calls and its clock time (a `span!`) or holds the
//! last size it saw (a `gauge!`), in relaxed atomics, and a reporter thread (`report`, started by the standalone app) prints one line every 2 s to stderr, so it still
//! speaks while the engine thread is stuck:
//!   `phase-trace t=<epoch s> remove=<calls>/<ms> link_round=<calls>/<ms> ... deps=<n> reads=<n>`
//! The numbers between two lines are the rate; a mark whose ms grows while the rest stand still is where the time goes. Nothing here changes a value or a write.
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::time::Instant;

/// The spans, in the order the line prints them.
pub const SPANS: &[&str] = &["drain_apply", "run", "remove", "link_round", "link_scan", "gc_dead", "retire_scan", "evict"];
/// The gauges: the last size seen.
pub const GAUGES: &[&str] = &["deps", "reads", "dirty", "docs", "dead_batch", "retired_batch"];
const N: usize = 16;
static CALLS: [AtomicU64; N] = [const { AtomicU64::new(0) }; N];
static NS: [AtomicU64; N] = [const { AtomicU64::new(0) }; N];
static GAUGE: [AtomicU64; N] = [const { AtomicU64::new(0) }; N];

fn idx(list: &[&str], name: &str) -> usize { list.iter().position(|n| *n == name).unwrap_or_else(|| panic!("phase-trace: unknown mark {name}")) }

/// Time a scope: counts a call now, adds its clock time at drop.
pub struct Span { i: usize, t: Instant }
pub fn span(name: &str) -> Span { let i = idx(SPANS, name); CALLS[i].fetch_add(1, Relaxed); Span { i, t: Instant::now() } }
impl Drop for Span { fn drop(&mut self) { NS[self.i].fetch_add(self.t.elapsed().as_nanos() as u64, Relaxed); } }
pub fn gauge(name: &str, v: usize) { GAUGE[idx(GAUGES, name)].store(v as u64, Relaxed); }

/// The line as it is now.
pub fn line() -> String {
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let mut s = format!("phase-trace t={t}");
    for (i, n) in SPANS.iter().enumerate() { s += &format!(" {n}={}/{}", CALLS[i].load(Relaxed), NS[i].load(Relaxed) / 1_000_000); }
    for (i, n) in GAUGES.iter().enumerate() { s += &format!(" {n}={}", GAUGE[i].load(Relaxed)); }
    s
}
/// Print the line every 2 s on its own thread (the engine thread may never come back to a tick).
pub fn report() { std::thread::spawn(|| loop { std::thread::sleep(std::time::Duration::from_secs(2)); eprintln!("{}", line()); }); }
