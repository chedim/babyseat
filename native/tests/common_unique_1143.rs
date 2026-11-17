//! `common::unique()` names a temp directory or file: no two calls in a process ever give the same text, whatever the clock's resolution and however many threads ask at once
//! (macOS's clock ticks in microseconds, and two tests of one process that took their names in the same tick shared a directory: #1143).
mod common;

use std::collections::BTreeSet;

#[test]
fn a_thousand_threads_asking_at_once_never_get_the_same_name() {
    let handles: Vec<_> = (0..64).map(|_| std::thread::spawn(|| (0..500).map(|_| common::unique()).collect::<Vec<_>>())).collect();
    let mut all = BTreeSet::new();
    let mut total = 0;
    for h in handles { for n in h.join().unwrap() { total += 1; all.insert(n); } }
    assert_eq!(all.len(), total, "a name was given twice");
}

#[test]
fn a_name_is_safe_in_a_path() {
    let n = common::unique();
    assert!(!n.is_empty() && n.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'), "{n}");
}
