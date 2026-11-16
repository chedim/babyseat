//! APPS (docs/c1f-layers.md): which files of an app folder are packs and the order they load in. No engine logic: the engine composes.
use std::path::{Path, PathBuf};

/// Every `*.c1f` file in a folder's tree, sorted by path. (A subfolder named `x.c1f` is walked into, not loaded as a file.)
pub fn pack_files(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), std::io::Error> {
    let mut all: Vec<PathBuf> = std::fs::read_dir(dir)?.filter_map(|e| e.ok().map(|e| e.path())).collect();
    all.sort();
    for p in all {
        if p.is_dir() { pack_files(&p, out)?; }
        else if p.extension().map_or(false, |x| x == "c1f") { out.push(p); }
    }
    Ok(())
}

/// The names a pack's text IMPORTs (`IMPORT <name> <major>` lines).
fn imports(text: &str) -> Vec<String> {
    text.lines().filter_map(|l| { let mut w = l.split_whitespace(); (w.next() == Some("IMPORT")).then(|| w.next().map(String::from)).flatten() }).collect()
}

/// A pack after the packs of this app it imports; of the packs ready, the first in the given (path) order. A cycle's members stay in path order.
pub fn in_dependency_order(packs: Vec<(String, String)>) -> Vec<(String, String)> {
    let mut left = packs;
    let all: Vec<String> = left.iter().map(|(n, _)| n.clone()).collect();
    let mut out: Vec<(String, String)> = vec![];
    while !left.is_empty() {
        let placed: Vec<&String> = out.iter().map(|(n, _)| n).collect();
        let i = left.iter().position(|(n, t)| imports(t).iter().all(|i| i == n || !all.contains(i) || placed.contains(&i))).unwrap_or(0);
        out.push(left.remove(i));
    }
    out
}
