//! A pack that writes a class it does not own must IMPORT the pack that owns it, or the engine refuses the write
//! (`pack X can't write SemText: add IMPORT semtext`). goalgraph_chat and goalgraph_lanes missed `IMPORT semtext`.
use std::path::Path;

/// (constructor written, pack that owns the class)
const OWNED: &[(&str, &str)] = &[("SemText(", "semtext")];

#[test]
fn a_pack_that_writes_a_class_imports_its_owner() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane/packs");
    let mut bad = Vec::new();
    for e in std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()) {
        let p = e.path();
        if p.extension().map(|x| x != "c1f").unwrap_or(true) { continue; }
        let text = std::fs::read_to_string(&p).unwrap();
        let name = text.lines().next().unwrap_or("").split_whitespace().nth(1).unwrap_or("").to_string();
        let imports: Vec<&str> = text.lines().filter(|l| l.starts_with("IMPORT ")).filter_map(|l| l.split_whitespace().nth(1)).collect();
        for (ctor, owner) in OWNED {
            if name == *owner { continue; }
            let writes = text.lines().filter(|l| !l.trim_start().starts_with("--")).any(|l| l.contains(&format!("= {ctor}")));
            if writes && !imports.contains(owner) { bad.push(format!("{}: writes {}) but does not IMPORT {owner}", p.file_name().unwrap().to_string_lossy(), ctor.trim_end_matches('('))); }
        }
    }
    assert!(bad.is_empty(), "\n{}\n", bad.join("\n"));
}
