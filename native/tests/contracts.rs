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

// ---- the surface snapshot: a pack as a black box ------------------------------------------------------------------------------
// What a pack touches from outside, computed from its text: the classes it declares, the members it declares on them, the other
// packs it imports, the properties it WRITES on other entities (`('Class:key').prop = ...`, `this.<ref>.prop = ...` is local), the
// paths it claims, the Boot facts it reads. Compared to the committed `membrane/packs/surface.json`: any change is a failing test
// until someone reviews it and regenerates the file (`SURFACE_UPDATE=1 cargo test --test contracts surface`).
/// The unquoted entity literal a line starts with (`Goal:board`, `membrane::Chat:message[id]:response`): its surface target (a keyed
/// one is dynamic, shown as `<first segment>:*` like a quoted `'x:' + ..`) and the text after it.
fn entity_literal(t: &str) -> Option<(String, &str)> {
    let b = t.as_bytes();
    let nc = |i: usize| b.get(i).map_or(false, |c| c.is_ascii_alphanumeric() || *c == b'_');
    if !nc(0) { return None; }
    let (mut i, mut colon, mut keyed) = (0, false, false);
    loop {
        if nc(i) { i += 1; }
        else if b.get(i) == Some(&b':') && b.get(i + 1) == Some(&b':') && nc(i + 2) && colon == false { i += 2; colon = true; }
        else if b.get(i) == Some(&b':') && nc(i + 1) { i += 1; colon = true; }
        else if colon && b.get(i) == Some(&b'[') {
            let mut d = 0;
            while i < b.len() { if b[i] == b'[' { d += 1; } if b[i] == b']' { d -= 1; if d == 0 { break; } } i += 1; }
            if i >= b.len() { return None; }
            i += 1; keyed = true;
            if b.get(i) == Some(&b':') && nc(i + 1) { i += 1; }
        } else { break; }
    }
    if !colon { return None; }
    let path = &t[..i];
    let target = if keyed { path.split(':').next().unwrap_or("").to_string() + ":*" } else { path.to_string() };
    Some((target, &t[i..]))
}

fn surface_of(text: &str) -> serde_json::Value {
    use serde_json::json;
    let decl = declared(text);
    let mut classes: BTreeSet<String> = BTreeSet::new();
    let mut members: BTreeSet<String> = BTreeSet::new();
    for (c, p) in &decl { classes.insert(c.clone()); members.insert(format!("{c}.{p}")); }
    let mut writes: BTreeSet<String> = BTreeSet::new();
    let mut claims: BTreeSet<String> = BTreeSet::new();
    for l in text.lines() {
        let t = l.trim_start();
        if t.starts_with("--") { continue; }
        // a write to ANOTHER entity: ('Class:key').prop = ..., ('Class:' + expr).prop = ..., (expr).prop = ... (a dynamic target,
        // shown as `*`), or this.<ref>.prop = ... (through a reference property). `this.prop = ...` is the entity's own: not listed.
        let mut note = |target: String, rest: &str| {
            let prop: String = rest.chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
            if !prop.is_empty() && rest[prop.len()..].trim_start().starts_with('=') && !rest[prop.len()..].trim_start().starts_with("==") { writes.insert(format!("{target}.{prop}")); }
        };
        if t.starts_with('(') {
            // find the matching close paren of the target expression
            let mut depth = 0; let mut end = None;
            for (i, c) in t.char_indices() { if c == '(' { depth += 1; } if c == ')' { depth -= 1; if depth == 0 { end = Some(i); break; } } }
            if let Some(e) = end {
                if t[e + 1..].starts_with('.') {
                    let inner = &t[1..e];
                    let target = if inner.starts_with('\'') {
                        let lit: String = inner[1..].chars().take_while(|c| *c != '\'').collect();
                        if inner[1 + lit.len()..].trim_start_matches('\'').trim_start().starts_with('+') { lit.split(':').next().unwrap_or("").to_string() + ":*" } else { lit }
                    } else { "*".to_string() };
                    note(target, &t[e + 2..]);
                }
            }
        } else if let Some((target, rest)) = entity_literal(t) {
            // an unquoted entity: `Goal:board.status = ..`, `membrane::Chat:message[id].x = ..` (a keyed one is dynamic: `membrane:*`),
            // and a constructor `<entity> = Class(name: value)`, listed as `<target>=Class()`
            if let Some(p) = rest.strip_prefix('.') { note(target, p); }
            else if let Some(r) = rest.trim_start().strip_prefix('=') {
                let cls: String = r.trim_start().chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
                if !cls.is_empty() && r.trim_start()[cls.len()..].starts_with('(') && !r.trim_start().starts_with('=') { writes.insert(format!("{target}={cls}()")); }
            }
        } else if let Some(rest) = t.strip_prefix("this.") {
            if let Some((r, after)) = rest.split_once('.') {
                if r.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') { note(format!("ref({r})"), after); }
            }
        }
        // paths a family claims: startswith(this.path, '/x'), this.path == '/x', this.path in ['/x', ...]
        for pat in ["this.path == '", "startswith(this.path, '", "this.path in ["] {
            for (i, _) in l.match_indices(pat) {
                let rest = &l[i + pat.len()..];
                let seg = rest.split(|c| c == ']' || c == ')').next().unwrap_or("");
                for s in seg.split('\'').filter(|s| s.starts_with('/')) { claims.insert(s.to_string()); }
            }
        }
    }
    let mut facts: BTreeSet<String> = BTreeSet::new();
    let mut cur = String::new();
    for l in text.lines() {
        if !l.starts_with(' ') && !l.starts_with("--") && !l.is_empty() { cur = l.split(['.', ' ']).next().unwrap_or("").to_string(); }
        if l.contains("for b in instances('Boot')") || l.contains("for b in this.boots") { for id in ident_after(l, "b.") { facts.insert(id); } }
        if cur == "Boot" { for id in ident_after(l, "this.") { facts.insert(id); } }
    }
    let boot_decl: BTreeSet<String> = decl.iter().filter(|(c, _)| c == "Boot").map(|(_, p)| p.clone()).collect();
    facts.retain(|f| !boot_decl.contains(f) && !["eid", "name", "summary", "cls", "state", "facts"].contains(&f.as_str()));
    json!({"imports": imports(text), "classes": classes, "members": members.len(), "writes": writes, "claims": claims, "boot_facts": facts})
}

#[test]
fn the_surface_of_every_pack_matches_the_reviewed_snapshot() {
    let mut now = serde_json::Map::new();
    for (stem, text) in packs() { now.insert(stem, surface_of(&text)); }
    let path = root().join("membrane/packs/surface.json");
    if std::env::var("SURFACE_UPDATE").is_ok() {
        std::fs::write(&path, serde_json::to_string_pretty(&serde_json::Value::Object(now)).unwrap() + "\n").unwrap();
        return;
    }
    let want: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&path).expect("membrane/packs/surface.json (SURFACE_UPDATE=1 writes it)")).unwrap();
    let mut diffs = vec![];
    let want = want.as_object().unwrap();
    for (k, v) in &now { match want.get(k) { None => diffs.push(format!("+ pack {k} is new: {v}")), Some(w) if w != v => diffs.push(format!("~ pack {k} changed:\n    was {w}\n    now {v}")), _ => {} } }
    for k in want.keys() { if !now.contains_key(k) { diffs.push(format!("- pack {k} is gone")); } }
    assert!(diffs.is_empty(), "\nthe surface of a pack changed; review it and run SURFACE_UPDATE=1 cargo test --test contracts surface\n{}\n", diffs.join("\n"));
}

// ---- the types snapshot: what the static inference says about every getter -------------------------------------------------
// `infer_pack` over every shipped pack, together (a class a pack imports is typed from the pack that defines it), compared to the
// committed `membrane/packs/types.json`: `pack -> {getters: Class.prop -> type, writes: ..., findings: [...]}`. A REPORT, not a gate on
// quality: a change is a diff to read, then `TYPES_UPDATE=1 cargo test --test contracts types` regenerates it.
#[test]
fn the_inferred_types_of_every_pack_match_the_reviewed_snapshot() {
    use serde_json::json;
    let all = packs();
    let texts: Vec<&str> = all.iter().map(|p| p.1.as_str()).collect();
    let reports = membrane_vm::infer::infer_packs(&texts).expect("every shipped pack parses");
    let mut now = serde_json::Map::new();
    let (mut total, mut concrete) = (0, 0);
    for ((stem, _), (_, r)) in all.iter().zip(&reports) {
        total += r.getters.len();
        concrete += r.getters.values().filter(|t| *t != "Any").count();
        now.insert(stem.clone(), json!({"getters": r.getters, "writes": r.writes, "findings": r.findings}));
    }
    eprintln!("inferred types: {concrete} of {total} getters resolve to a type other than Any");
    let path = root().join("membrane/packs/types.json");
    if std::env::var("TYPES_UPDATE").is_ok() {
        std::fs::write(&path, serde_json::to_string_pretty(&serde_json::Value::Object(now)).unwrap() + "\n").unwrap();
        return;
    }
    let want: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&path).expect("membrane/packs/types.json (TYPES_UPDATE=1 writes it)")).unwrap();
    let want = want.as_object().unwrap();
    let mut diffs = vec![];
    for (k, v) in &now { match want.get(k) { None => diffs.push(format!("+ pack {k} is new")), Some(w) if w != v => diffs.push(format!("~ pack {k} changed:\n    was {w}\n    now {v}")), _ => {} } }
    for k in want.keys() { if !now.contains_key(k) { diffs.push(format!("- pack {k} is gone")); } }
    assert!(diffs.is_empty(), "\nthe inferred types changed; review them and run TYPES_UPDATE=1 cargo test --test contracts types\n{}\n", diffs.join("\n"));
}
