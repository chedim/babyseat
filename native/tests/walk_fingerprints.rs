//! The autonode file-tree fingerprint walk, native: one call, no Python per file (each Python stat waited on the GIL).
use membrane_vm::walk::{fingerprints, Skip};
use std::fs;

#[test]
fn walks_skips_and_fingerprints() {
    let d = std::env::temp_dir().join(format!("walkfp-{}", std::process::id()));
    let _ = fs::remove_dir_all(&d);
    for p in ["a", "a/b", "node_modules", ".git", "c"] { fs::create_dir_all(d.join(p)).unwrap(); }
    for (p, t) in [("top.txt", "x"), ("a/one.py", "yy"), ("a/b/two.py", "zzz"), ("node_modules/x.js", "n"),
                   (".git/HEAD", "h"), ("c/serve-1.log", "s"), ("c/k.seed.json", "{}"), ("c/own.json", "o")] {
        fs::write(d.join(p), t).unwrap();
    }
    let skip = Skip { dirs: vec!["node_modules".into()], names: vec!["own.json".into()],
                      prefixes: vec!["serve-".into()], suffixes: vec![".seed.json".into()], hidden_dirs: true };
    let mut got = fingerprints(&d, &skip, 100000);
    got.sort();
    let names: Vec<_> = got.iter().map(|(r, _, _)| r.as_str()).collect();
    assert_eq!(names, vec!["a/b/two.py", "a/one.py", "top.txt"]);
    let sizes: Vec<_> = got.iter().map(|(_, _, s)| *s).collect();
    assert_eq!(sizes, vec![3, 2, 1]);
    assert!(got.iter().all(|(_, m, _)| *m > 0));
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn stops_at_the_cap_on_a_directory_boundary() {
    let d = std::env::temp_dir().join(format!("walkcap-{}", std::process::id()));
    let _ = fs::remove_dir_all(&d);
    for i in 0..3 { fs::create_dir_all(d.join(format!("d{}", i))).unwrap(); for j in 0..4 { fs::write(d.join(format!("d{}/f{}", i, j)), "x").unwrap(); } }
    let got = fingerprints(&d, &Skip::default(), 4);
    assert!(got.len() >= 4 && got.len() < 12, "{}", got.len());
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn a_missing_root_is_empty() {
    assert!(fingerprints(std::path::Path::new("/nonexistent/walkfp"), &Skip::default(), 10).is_empty());
}
