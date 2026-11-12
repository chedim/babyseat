//! Every spec in tests/specs/*.json runs (the harness is src/spec.rs); a failure prints each wrong value.
use membrane_vm::spec;

#[test]
fn every_spec_passes() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    std::env::set_current_dir(&root).unwrap();                       // pack paths in a spec are relative to the repository
    let mut files: Vec<_> = std::fs::read_dir(root.join("tests/specs")).unwrap().filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "json").unwrap_or(false)).collect();
    files.sort();
    assert!(!files.is_empty(), "no specs in tests/specs");
    let mut fails = Vec::new();
    for f in &files {
        let s: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(f).unwrap()).unwrap_or_else(|e| panic!("{}: {e}", f.display()));
        // a file is one spec or a list of them
        let specs = match s { serde_json::Value::Array(a) => a, one => vec![one] };
        for sp in specs { fails.extend(spec::run(&sp)); }
    }
    assert!(fails.is_empty(), "\n{}\n", fails.join("\n"));
}
