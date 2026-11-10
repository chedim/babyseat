//! The autonode file-tree fingerprint walk (#1044 perf, the operator 2026-11-10): one native call returns
//! {relative path: (mtime_ns, size)} for a tree. In Python each of the ~1300 stats waited for the GIL behind whatever
//! else ran (a 0.01 s walk took 6-65 s in the seat); here the caller releases the GIL once for the whole walk.
//! Same rules as the Python walk it replaces (autonode.FileTreeScanner): skipped directory names, hidden directories,
//! skipped file names / prefixes / suffixes, and the file cap checked as each directory starts.
use std::path::Path;

#[derive(Default, Clone)]
pub struct Skip {
    pub dirs: Vec<String>,
    pub names: Vec<String>,
    pub prefixes: Vec<String>,
    pub suffixes: Vec<String>,
    pub hidden_dirs: bool,
}

/// -> [(relative path, mtime_ns, size)]; a missing or unreadable root is empty, an unreadable entry is skipped.
pub fn fingerprints(root: &Path, skip: &Skip, max_files: usize) -> Vec<(String, i64, u64)> {
    let mut out = vec![];
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        if out.len() >= max_files { break; }
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        let mut subdirs = vec![];
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            let Ok(ft) = e.file_type() else { continue };
            if ft.is_dir() {
                if skip.dirs.iter().any(|d| d == &name) || (skip.hidden_dirs && name.starts_with('.')) { continue; }
                subdirs.push(e.path());
                continue;
            }
            if skip.names.iter().any(|n| n == &name) || skip.prefixes.iter().any(|p| name.starts_with(p.as_str()))
                || skip.suffixes.iter().any(|s| name.ends_with(s.as_str())) { continue; }
            let p = e.path();
            let Ok(m) = std::fs::metadata(&p) else { continue };       // follows a link, as Path.stat() does
            if m.is_dir() { continue; }
            let mt = m.modified().ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_nanos() as i64).unwrap_or(0);
            let rel = p.strip_prefix(root).unwrap_or(&p).to_string_lossy().into_owned();
            out.push((rel, mt, m.len()));
        }
        subdirs.reverse();                     // depth-first in listing order, like os.walk top-down
        stack.extend(subdirs);
    }
    out
}
