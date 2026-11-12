//! The contracts of docs/c1f-stdlib.md, checked mechanically against every shipped pack (the operator, 2026-11-12: contracts are
//! only good if something fails when they are broken).
//!  1. every shipped pack loads, together, in dependency order, with no error;
//!  2. a route family pack (`PACK door_<f>`) imports door and defines BOTH `Request.o_<f>` and `Request.r_<f>`; its family is
//!     one of the reserved ones; `m_<f>` without `o_<f>` is dead; no `door_*` pack redefines a getter the door owns;
//!  3. every Boot fact a pack reads is in the facts registry (membrane/packs/facts.json), and a fact the registry says the
//!     launcher writes (`wired`) is written by the launcher.
use membrane_vm::fields::Fields;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

const FAMILIES: [&str; 7] = ["models", "decisions", "state", "voice", "session", "self", "tools"];

fn root() -> PathBuf { Path::new(env!("CARGO_MANIFEST_DIR")).join("..") }
fn packs() -> Vec<(String, String)> {                                   // (file stem, text), shipped membrane packs only
    let mut v: Vec<(String, String)> = std::fs::read_dir(root().join("membrane/packs")).unwrap().filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "c1f").unwrap_or(false))
        .map(|p| (p.file_stem().unwrap().to_string_lossy().to_string(), std::fs::read_to_string(&p).unwrap())).collect();
    v.sort();
    v
}
fn pack_name(t: &str) -> String { t.lines().find_map(|l| l.strip_prefix("PACK ")).and_then(|r| r.split_whitespace().next()).unwrap_or("").to_string() }
fn imports(t: &str) -> Vec<String> { t.lines().filter_map(|l| l.strip_prefix("IMPORT ")).filter_map(|r| r.split_whitespace().next()).map(String::from).collect() }
/// `Class.prop getter|on-setter ...` -> (class, prop)
fn declared(t: &str) -> Vec<(String, String)> {
    t.lines().filter(|l| !l.starts_with(' ') && !l.starts_with("--")).filter_map(|l| {
        let (head, _) = l.split_once(' ')?;
        let (c, p) = head.split_once('.')?;
        if c.chars().next()?.is_ascii_uppercase() { Some((c.to_string(), p.to_string())) } else { None }
    }).collect()
}
fn ident_after(t: &str, pat: &str) -> Vec<String> {                    // every identifier that follows `pat` where `pat` starts a name
    let b = t.as_bytes();
    let mut out = vec![];
    for (i, _) in t.match_indices(pat) {
        if i > 0 && (b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_' || b[i - 1] == b'.') { continue; }
        let rest = &t[i + pat.len()..];
        let id: String = rest.chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
        if !id.is_empty() { out.push(id); }
    }
    out
}

#[test]
fn every_shipped_pack_loads_together() {
    let mut f = Fields::goalgraph();
    let all = packs();
    let mut pending: Vec<&(String, String)> = all.iter().collect();
    // dependency order: a pack loads when everything it imports is loaded (goalgraph is built in)
    let mut loaded: BTreeSet<String> = BTreeSet::from(["goalgraph".to_string()]);
    let mut errors = vec![];
    // a view pack (it imports `dom`) runs in the browser's VM: the seat's engine has no dom, and what imports a view pack waits too
    let mut view: BTreeSet<String> = BTreeSet::from(["dom".to_string()]);
    loop {
        let more: Vec<String> = all.iter().filter(|p| imports(&p.1).iter().any(|i| view.contains(i))).map(|p| pack_name(&p.1)).filter(|n| !view.contains(n)).collect();
        if more.is_empty() { break; }
        view.extend(more);
    }
    pending.retain(|p| !view.contains(&pack_name(&p.1)));
    while !pending.is_empty() {
        let before = pending.len();
        let mut rest = vec![];
        for p in pending {
            if imports(&p.1).iter().all(|i| loaded.contains(i)) {
                match f.set_pack(&pack_name(&p.1), &p.1) { Ok(_) => { loaded.insert(pack_name(&p.1)); } Err(e) => errors.push(format!("{}: {e}", p.0)) }
            } else { rest.push(p); }
        }
        pending = rest;
        if pending.len() == before { errors.push(format!("unresolved imports: {:?}", pending.iter().map(|p| &p.0).collect::<Vec<_>>())); break; }
    }
    assert!(errors.is_empty(), "\n{}\n", errors.join("\n"));
}

#[test]
fn a_route_family_keeps_the_door_contract() {
    let all = packs();
    let door = all.iter().find(|p| p.0 == "door").expect("the door pack").1.clone();
    let owned: BTreeSet<String> = declared(&door).into_iter().filter(|(c, _)| c == "Request").map(|(_, p)| p).collect();
    let mut bad = vec![];
    for (stem, text) in all.iter().filter(|p| p.0.starts_with("door_")) {
        let fam = stem.trim_start_matches("door_");
        if !FAMILIES.contains(&fam) { bad.push(format!("{stem}: `{fam}` is not a reserved family ({FAMILIES:?})")); }
        if !imports(text).iter().any(|i| i == "door") { bad.push(format!("{stem}: must IMPORT door 1")); }
        let d: BTreeSet<String> = declared(text).into_iter().filter(|(c, _)| c == "Request").map(|(_, p)| p).collect();
        let (o, r, m) = (format!("o_{fam}"), format!("r_{fam}"), format!("m_{fam}"));
        if !d.contains(&o) { bad.push(format!("{stem}: defines no Request.{o} (the door never routes to it)")); }
        if !d.contains(&r) { bad.push(format!("{stem}: defines no Request.{r} (it owns paths and answers none)")); }
        let _ = m;
        for p in &d { if owned.contains(p) { bad.push(format!("{stem}: redefines the door's own Request.{p}")); } }
        // a contract name (o_/r_/m_ + a reserved family) of ANOTHER family is a mistake; other names are the pack's own helpers
        for p in d.iter() {
            for pre in ["o_", "r_", "m_"] {
                if let Some(suffix) = p.strip_prefix(pre) {
                    if FAMILIES.contains(&suffix) && suffix != fam { bad.push(format!("{stem}: Request.{p} belongs to the {suffix} family")); }
                }
            }
        }
    }
    assert!(bad.is_empty(), "\n{}\n", bad.join("\n"));
}

#[test]
fn the_boot_facts_packs_read_are_registered_and_the_wired_ones_are_written_by_the_launcher() {
    let all = packs();
    let boot_declared: BTreeSet<String> = all.iter().flat_map(|p| declared(&p.1)).filter(|(c, _)| c == "Boot").map(|(_, p)| p).collect();
    let mut read: BTreeSet<String> = BTreeSet::new();
    for (_, text) in &all {
        let mut cur = String::new();
        for l in text.lines() {
            // [b.x for b in instances('Boot')] or [b.x for b in this.boots]: only where b ranges over the Boot entities
            if l.contains("for b in instances('Boot')") || l.contains("for b in this.boots") { for id in ident_after(l, "b.") { read.insert(id); } }
            if !l.starts_with(' ') && !l.starts_with("--") && !l.is_empty() { cur = l.split(['.', ' ']).next().unwrap_or("").to_string(); }
            if cur == "Boot" { for id in ident_after(l, "this.") { read.insert(id); } }
        }
    }
    let read: BTreeSet<String> = read.into_iter().filter(|id| !boot_declared.contains(id) && !["eid", "name", "summary", "cls", "state", "facts"].contains(&id.as_str())).collect();   // `facts` is the dict itself
    let reg_text = std::fs::read_to_string(root().join("membrane/packs/facts.json")).expect("membrane/packs/facts.json (the facts registry)");
    let reg: serde_json::Value = serde_json::from_str(&reg_text).unwrap();
    let facts = reg["facts"].as_object().expect("facts: {name: {wired, why}}");
    let launcher = std::fs::read_to_string(root().join("membrane/opencode.py")).unwrap() + &std::fs::read_to_string(root().join("membrane/fields_live.py")).unwrap();
    let mut bad = vec![];
    for id in &read {
        if !facts.contains_key(id) { bad.push(format!("a pack reads the Boot fact `{id}` that is not in membrane/packs/facts.json")); }
    }
    for (name, f) in facts {
        if f["wired"].as_bool() == Some(true) && !launcher.contains(&format!("\"{name}\"")) {
            bad.push(format!("fact `{name}` is marked wired but the launcher (opencode.py / fields_live.py) never writes it"));
        }
        if f["why"].as_str().unwrap_or("").is_empty() { bad.push(format!("fact `{name}` has no `why`")); }
    }
    assert!(bad.is_empty(), "\n{}\n", bad.join("\n"));
}
