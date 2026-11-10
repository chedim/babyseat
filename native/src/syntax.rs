//! Cliffhanger's text form for field setters (#994, the syntax pass). It's membrane's syntax, made consistent:
//!
//! ```text
//! -- a comment
//! Task.status on-setter replan-task:
//!   WHEN this.status == 'done' or this.status == 'failed':
//!     this.goal.replan = 'task ' + this.eid + ' ' + this.status
//!   ELSE WHEN this.status == 'stuck':
//!     this.goal.replan = 'stuck: ' + this.eid
//!   ELSE:
//!     this.note = ''
//! Goal.replan on-setter plan:
//!   ALWAYS: membrane::llm.call = {'about': this.eid, 'prompt': 'plan: ' + this.replan}
//! ```
//!
//! - A setter starts at column 0: `<Class>.<field> on-setter <name>:`. It runs after a write to that field
//!   (the `on` phase).
//! - Its body is indented: `WHEN <expr>:`, then any `ELSE WHEN <expr>:`, then an optional `ELSE:`. The first
//!   true branch runs, and only it. `ALWAYS:` or bare statements under the header mean a branch with no condition.
//! - A statement is `<target>.<field> = <expr>`, on the branch line after the colon or on lines indented under it.
//!   `this` is the entity written. A target other than `this` names another entity by an expression
//!   (`this.goal`). `membrane::llm.call = <expr>` is the LLM call effect.
//! - Expressions are the VM's expression language (`==`, `and`/`or`/`not`, `in`, `x.prop`, `'a' + b`, `{k: v}`).
//! - `--` starts a comment outside quotes.
use crate::fields::{Getter, OnSet, LLM_CALL};

type W = (String, String, String);

/// Cut a `--` comment, outside quotes.
fn strip_comment(line: &str) -> &str {
    let (mut q, b) = (None::<char>, line.as_bytes());
    for (i, c) in line.char_indices() {
        match q {
            Some(o) if c == o => q = None,
            Some(_) => {}
            None if c == '\'' || c == '"' => q = Some(c),
            None if c == '-' && b.get(i + 1) == Some(&b'-') => return &line[..i],
            None => {}
        }
    }
    line
}

/// Byte index of the first character at bracket depth 0, outside quotes, for which `hit` is true.
fn top_level(s: &str, hit: impl Fn(&str, usize) -> bool) -> Option<usize> {
    let (mut q, mut depth) = (None::<char>, 0i32);
    for (i, c) in s.char_indices() {
        match q {
            Some(o) => { if c == o { q = None; } continue; }
            None if c == '\'' || c == '"' => { q = Some(c); continue; }
            _ => {}
        }
        match c { '(' | '[' | '{' => depth += 1, ')' | ']' | '}' => depth -= 1, _ => {} }
        if depth == 0 && hit(s, i) { return Some(i); }
    }
    None
}

/// The colon that ends a branch's condition: the first one at depth 0 outside quotes.
fn branch_colon(s: &str) -> Option<usize> { top_level(s, |s, i| s.as_bytes()[i] == b':') }

/// The `=` of an assignment: not part of `==`, `!=`, `<=`, `>=`.
fn assign_eq(s: &str) -> Option<usize> {
    top_level(s, |s, i| {
        let b = s.as_bytes();
        b[i] == b'=' && b.get(i + 1) != Some(&b'=') && (i == 0 || !b"=!<>".contains(&b[i - 1]))
    })
}

/// The markers of a FOR block in a setter's writes: `(FOR, var, iterable)` ... `(END, "", "")`.
pub const FOR: &str = "FOR";
pub const END: &str = "END";

/// `FOR v IN it: e` is the list `[e for v in it]` (in getters and in a setter's values alike); nested FORs too.
pub fn for_expr(s: &str, at: usize) -> Result<String, String> {
    let t = s.trim();
    let Some(rest) = t.strip_prefix("FOR ") else { return Ok(t.to_string()) };
    let (var, it, body) = for_head(rest, at)?;
    let body = body.ok_or(format!("line {}: `FOR {} IN ...:` needs a value after its `:`", at, var))?;
    Ok(format!("[{} for {} in {}]", for_expr(&body, at)?, var, it))
}

/// `v IN it: rest` -> (v, it, rest or None when nothing follows the `:`).
fn for_head(rest: &str, at: usize) -> Result<(String, String, Option<String>), String> {
    let (var, tail) = rest.split_once(" IN ").ok_or(format!("line {}: expected `FOR <name> IN <list>:`", at))?;
    let var = var.trim();
    if var.is_empty() || !var.chars().all(|c| c.is_alphanumeric() || c == '_') || var.chars().next().map_or(true, |c| c.is_ascii_digit()) {
        return Err(format!("line {}: `FOR {}`: the loop name is one word", at, var));
    }
    let c = branch_colon(tail).ok_or(format!("line {}: `FOR {} IN ...` needs a `:` after its list", at, var))?;
    let (it, body) = (tail[..c].trim(), tail[c + 1..].trim());
    if it.is_empty() { return Err(format!("line {}: `FOR {} IN` has no list", at, var)); }
    Ok((var.to_string(), it.to_string(), if body.is_empty() { None } else { Some(body.to_string()) }))
}

fn statement(s: &str, at: usize) -> Result<W, String> {
    let eq = assign_eq(s).ok_or(format!("line {}: expected `<target>.<field> = <expr>`, got `{}`", at, s))?;
    let (lhs, rhs) = (s[..eq].trim(), s[eq + 1..].trim());
    if rhs.is_empty() { return Err(format!("line {}: `{}` has no value", at, lhs)); }
    let rhs = for_expr(rhs, at)?;
    let rhs = rhs.as_str();
    if lhs == LLM_CALL { return Ok((LLM_CALL.into(), String::new(), rhs.into())); }
    let (tgt, field) = lhs.rsplit_once('.').ok_or(format!("line {}: `{}` needs a target: write `this.{}`", at, lhs, lhs))?;
    if tgt.is_empty() || field.is_empty() { return Err(format!("line {}: bad target `{}`", at, lhs)); }
    Ok((tgt.into(), field.into(), rhs.into()))
}

/// Parse setters in cliffhanger's text form (getters are skipped: `parse_pack` returns both).
pub fn parse(src: &str) -> Result<Vec<OnSet>, String> { parse_full(src).map(|p| p.setters) }

/// A pack: its header (`PACK <name> <version>`, `IMPORT <name> <major>` lines) and its setters and getters.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Pack {
    pub name: Option<String>,
    pub version: String,
    pub imports: Vec<(String, u32)>,
    pub setters: Vec<OnSet>,
    pub getters: Vec<Getter>,
}

impl Pack {
    /// The major version (`2` of `2.1`); a pack without a `PACK` line is version 1.
    pub fn major(&self) -> u32 { self.version.split('.').next().and_then(|m| m.parse().ok()).unwrap_or(1) }
    /// The classes this pack declares setters or getters on.
    pub fn classes(&self) -> Vec<String> {
        let mut v: Vec<String> = self.setters.iter().map(|s| s.cls.clone()).chain(self.getters.iter().map(|g| g.cls.clone())).collect();
        v.sort(); v.dedup(); v
    }
}

/// Parse a whole pack: header lines, then setters and getters.
pub fn parse_full(src: &str) -> Result<Pack, String> {
    let mut pack = Pack { version: "1".into(), ..Default::default() };
    let mut body = String::new();
    for (n, raw) in src.lines().enumerate() {
        let line = strip_comment(raw).trim_end();
        if let Some(rest) = line.strip_prefix("PACK ") {
            let mut it = rest.split_whitespace();
            let name = it.next().ok_or(format!("line {}: PACK needs a name", n + 1))?;
            pack.name = Some(name.to_string());
            if let Some(v) = it.next() {
                if v.split('.').any(|p| p.parse::<u32>().is_err()) { return Err(format!("line {}: a version is numbers and dots: `{}`", n + 1, v)); }
                pack.version = v.to_string();
            }
            body.push('\n');
        } else if let Some(rest) = line.strip_prefix("IMPORT ") {
            let mut it = rest.split_whitespace();
            let name = it.next().ok_or(format!("line {}: IMPORT needs a pack name", n + 1))?;
            let major = match it.next() { Some(m) => m.parse::<u32>().map_err(|_| format!("line {}: IMPORT {} needs a major version number", n + 1, name))?, None => 1 };
            pack.imports.push((name.to_string(), major));
            body.push('\n');
        } else {
            body.push_str(raw);
            body.push('\n');
        }
    }
    let (setters, getters) = parse_pack(&body)?;
    pack.setters = setters;
    pack.getters = getters;
    Ok(pack)
}

/// Parse a pack: its setters and its getters (`<Class>.<field> getter: <expr>`, the expression on the header line
/// or on indented lines under it). Errors name the line and say what was expected.
pub fn parse_pack(src: &str) -> Result<(Vec<OnSet>, Vec<Getter>), String> {
    let mut getters: Vec<Getter> = vec![];
    // each getter definition's arm: its WHEN condition (None: the default) and the line it starts on
    let mut arms: Vec<(Option<String>, usize)> = vec![];
    let mut in_getter = false;
    let mut out: Vec<OnSet> = vec![];
    // the branches of the setter being read: (condition, writes); `None` before its first line
    let mut branches: Vec<(String, Vec<W>)> = vec![];
    let mut state = 0u8; // 0: before any branch; 1: in WHEN/ELSE WHEN; 2: after ELSE or ALWAYS (no more branches)
    // the open FOR blocks of the setter being read: each FOR line's indent (a line indented no deeper ends it)
    let mut fors: Vec<usize> = vec![];
    fn end_fors(fors: &mut Vec<usize>, branches: &mut [(String, Vec<W>)], upto: Option<usize>) {
        while let Some(&d) = fors.last() {
            if upto.map_or(false, |i| i > d) { break; }
            fors.pop();
            if let Some(b) = branches.last_mut() { b.1.push((END.into(), String::new(), String::new())); }
        }
    }
    // a getter in block form (`X.f getter:` then WHEN / ELSE WHEN / ELSE lines): 0 none yet, 1 after WHEN, 2 after ELSE
    let mut gstate = 0u8;
    fn close(out: &mut Vec<OnSet>, branches: &mut Vec<(String, Vec<W>)>) -> Result<(), String> {
        let Some(last) = out.last_mut() else { return Ok(()) };
        if branches.is_empty() { return Err(format!("setter {} has no body", last.name)); }
        let mut it = std::mem::take(branches).into_iter();
        let (when, writes) = it.next().unwrap();
        last.when = when; last.writes = writes; last.els = it.collect();
        Ok(())
    }
    for (n, raw) in src.lines().enumerate() {
        let at = n + 1;
        let line = strip_comment(raw).trim_end();
        if line.trim().is_empty() { continue; }
        if !line.starts_with(char::is_whitespace) {
            // `<Class>.<field> getter: <expr>`, or an arm: `<Class>.<field> getter WHEN <cond>: <expr>`
            let head = line.split_once(" getter:").map(|(p, e)| (p, None, e)).or_else(|| {
                line.split_once(" getter WHEN").map(|(p, rest)| match branch_colon(rest) {
                    Some(c) => (p, Some(rest[..c].trim()), &rest[c + 1..]),
                    None => (p, Some(""), ""),
                })
            });
            if let Some((path, when, expr)) = head {
                end_fors(&mut fors, &mut branches, None);
                if !in_getter { close(&mut out, &mut branches)?; }
                if let Some(g) = getters.last() { if g.expr.trim().is_empty() { return Err(format!("getter {}.{} has no expression", g.cls, g.prop)); } }
                let (cls, prop) = path.trim().split_once('.').ok_or(format!("line {}: expected `<Class>.<field> getter:`", at))?;
                if when == Some("") { return Err(format!("line {}: the getter arm `WHEN` has no condition (or no `:` after it)", at)); }
                getters.push(Getter { cls: cls.into(), prop: prop.into(), expr: expr.trim().into(), arms: 1 });
                arms.push((when.map(|w| w.to_string()), at));
                in_getter = true;
                gstate = 0;
                continue;
            }
            end_fors(&mut fors, &mut branches, None);
            if in_getter { in_getter = false; } else { close(&mut out, &mut branches)?; }
            let head = line.strip_suffix(':').ok_or(format!("line {}: a setter header ends with `:`", at))?;
            let (path, name) = head.split_once(" on-setter ").ok_or(format!("line {}: expected `<Class>.<field> on-setter <name>:`", at))?;
            let (cls, prop) = path.trim().split_once('.').ok_or(format!("line {}: expected `<Class>.<field>`", at))?;
            let name = name.trim();
            if name.is_empty() || name.contains(char::is_whitespace) { return Err(format!("line {}: the setter needs a one-word name", at)); }
            out.push(OnSet { name: name.into(), cls: cls.into(), prop: prop.into(), ..Default::default() });
            state = 0;
            continue;
        }
        if in_getter {
            // the block form, as a setter's: WHEN c: e / ELSE WHEN c: e / ELSE: e under `X.f getter:`
            let b = line.trim();
            let kw = if b.starts_with("ELSE WHEN ") { Some("ELSE WHEN") } else if b.starts_with("WHEN ") { Some("WHEN") }
                else if b.starts_with("ELSE:") { Some("ELSE") } else { None };
            if let Some(k) = kw {
                let (cls, prop) = { let g = getters.last().unwrap(); (g.cls.clone(), g.prop.clone()) };
                let rest = &b[k.len()..];
                let (when, e) = if k == "ELSE" { (None, rest[1..].trim().to_string()) } else {
                    let c = branch_colon(rest).ok_or(format!("line {}: `{}` needs a `:` after its condition", at, k))?;
                    let cond = rest[..c].trim();
                    if cond.is_empty() { return Err(format!("line {}: `{}` has no condition", at, k)); }
                    (Some(cond.to_string()), rest[c + 1..].trim().to_string())
                };
                if e.is_empty() { return Err(format!("line {}: `{}` has no value after its `:`", at, k)); }
                match (k, gstate) {
                    ("WHEN", 0) => {
                        let g = getters.last_mut().unwrap();
                        if !g.expr.trim().is_empty() { return Err(format!("line {}: getter {}.{} has a value on its header line: write its arms under an empty `getter:`", at, cls, prop)); }
                        g.expr = e; *arms.last_mut().unwrap() = (when, at); gstate = 1;
                    }
                    ("WHEN", _) => return Err(format!("line {}: a second WHEN in one getter: use ELSE WHEN", at)),
                    (_, 1) => { getters.push(Getter { cls, prop, expr: e, arms: 1 }); arms.push((when, at)); if k == "ELSE" { gstate = 2; } }
                    _ => return Err(format!("line {}: {} must follow a WHEN", at, k)),
                }
                continue;
            }
            if gstate != 0 { return Err(format!("line {}: after a getter's WHEN, only ELSE WHEN / ELSE lines", at)); }
            let g = getters.last_mut().unwrap();            // a getter's expression continues on indented lines
            g.expr = format!("{} {}", g.expr, line.trim()).trim().to_string();
            continue;
        }
        if out.is_empty() { return Err(format!("line {}: indented line before any setter header", at)); }
        let body = line.trim();
        let indent = line.len() - line.trim_start().len();
        end_fors(&mut fors, &mut branches, Some(indent));
        if let Some(rest) = body.strip_prefix("FOR ") {           // a FOR block: its writes run once per item
            let (var, it, inline) = for_head(rest, at)?;
            if state == 0 { branches.push(("True".into(), vec![])); state = 2; }
            let b = branches.last_mut().unwrap();
            b.1.push((FOR.into(), var, it));
            match inline {
                Some(st) => { b.1.push(statement(&st, at)?); b.1.push((END.into(), String::new(), String::new())); }
                None => fors.push(indent),
            }
            continue;
        }
        let (kw, rest): (Option<&str>, &str) = if let Some(r) = body.strip_prefix("ELSE WHEN ") { (Some("ELSE WHEN"), r) }
            else if let Some(r) = body.strip_prefix("WHEN ") { (Some("WHEN"), r) }
            else if let Some(r) = body.strip_prefix("ELSE:") { (Some("ELSE"), r) }
            else if let Some(r) = body.strip_prefix("ALWAYS:") { (Some("ALWAYS"), r) }
            else { (None, body) };
        match kw {
            Some(k @ ("WHEN" | "ELSE WHEN")) => {
                if k == "WHEN" && state != 0 { return Err(format!("line {}: a second WHEN in one setter: use ELSE WHEN, or a new setter", at)); }
                if k == "ELSE WHEN" && state != 1 { return Err(format!("line {}: ELSE WHEN must follow a WHEN", at)); }
                let c = branch_colon(rest).ok_or(format!("line {}: `{}` needs a `:` after its condition", at, k))?;
                let (cond, tail) = (rest[..c].trim(), rest[c + 1..].trim());
                if cond.is_empty() { return Err(format!("line {}: `{}` has no condition", at, k)); }
                branches.push((cond.into(), vec![]));
                if !tail.is_empty() { branches.last_mut().unwrap().1.push(statement(tail, at)?); }
                state = 1;
            }
            Some(k) => { // ELSE or ALWAYS
                if k == "ELSE" && state != 1 { return Err(format!("line {}: ELSE must follow a WHEN", at)); }
                if k == "ALWAYS" && state != 0 { return Err(format!("line {}: ALWAYS is a setter's only branch", at)); }
                branches.push(("True".into(), vec![]));
                let tail = rest.trim();
                if !tail.is_empty() { branches.last_mut().unwrap().1.push(statement(tail, at)?); }
                state = 2;
            }
            None => {
                if state == 0 { branches.push(("True".into(), vec![])); state = 2; }   // bare statements: ALWAYS
                branches.last_mut().unwrap().1.push(statement(body, at)?);
            }
        }
    }
    end_fors(&mut fors, &mut branches, None);
    if !in_getter { close(&mut out, &mut branches)?; }
    for s in &out {
        for ws in std::iter::once(&s.writes).chain(s.els.iter().map(|(_, w)| w)) {
            for (i, (t, v, _)) in ws.iter().enumerate() {
                if t == FOR && ws.get(i + 1).map_or(true, |n| n.0 == END) {
                    return Err(format!("setter {}: `FOR {} IN ...:` has no writes under it", s.name, v));
                }
            }
        }
    }
    if let Some(s) = out.iter().find(|s| s.writes.is_empty() && s.els.iter().all(|(_, w)| w.is_empty())) {
        return Err(format!("setter {} writes nothing", s.name));
    }
    if let Some(g) = getters.iter().find(|g| g.expr.is_empty()) { return Err(format!("getter {}.{} has no expression", g.cls, g.prop)); }
    for g in getters.iter_mut() { g.expr = for_expr(&g.expr, 0).map_err(|e| format!("getter {}.{}: {}", g.cls, g.prop, e))?; }
    for g in &getters { crate::expr::parse(&g.expr).map_err(|e| format!("getter {}.{}: {}", g.cls, g.prop, e))?; }
    for (g, (when, at)) in getters.iter().zip(&arms) {          // each arm's condition: the same caps, checked now
        if let Some(c) = when { crate::expr::parse(c).map_err(|e| format!("line {}: getter {}.{} WHEN: {}", at, g.cls, g.prop, e))?; }
    }
    let folded = fold_arms(getters, arms)?;
    for g in &folded { crate::expr::parse_scaled(&g.expr, g.arms.max(1)).map_err(|e| format!("getter {}.{}: {}", g.cls, g.prop, e))?; }
    Ok((out, folded))
}

/// A field's getter arms, in written order, as one getter: `(e1) if (c1) else ((e2) if (c2) else default)`; no default
/// arm: None. A default must be the last arm, and there is one at most.
fn fold_arms(getters: Vec<Getter>, arms: Vec<(Option<String>, usize)>) -> Result<Vec<Getter>, String> {
    let mut order: Vec<(String, String)> = vec![];
    let mut by: std::collections::HashMap<(String, String), Vec<(Option<String>, usize, String)>> = Default::default();
    for (g, (when, at)) in getters.into_iter().zip(arms) {
        let k = (g.cls.clone(), g.prop.clone());
        if !by.contains_key(&k) { order.push(k.clone()); }
        let v = by.entry(k).or_default();
        if let Some((None, first, _)) = v.iter().find(|a| a.0.is_none()) {
            return Err(if when.is_none() {
                format!("line {}: getter {}.{} has two defaults (lines {} and {}): give one a WHEN", at, g.cls, g.prop, first, at)
            } else {
                format!("line {}: a getter arm of {}.{} after its default (line {}) never runs: put the default last", at, g.cls, g.prop, first)
            });
        }
        v.push((when, at, g.expr));
    }
    Ok(order.into_iter().map(|k| {
        let v = by.remove(&k).unwrap();
        let expr = if v.len() == 1 && v[0].0.is_none() { v[0].2.clone() } else {
            v.iter().rev().fold(String::from("None"), |rest, (when, _, e)| match when {
                Some(c) => format!("({}) if ({}) else ({})", e, c, rest),
                None => e.clone(),
            })
        };
        Getter { cls: k.0, prop: k.1, expr, arms: v.len() }
    }).collect())
}

fn print_writes(out: &mut String, ws: &[W], indent: &str) {
    let mut ind = indent.to_string();
    for (t, p, v) in ws {
        if t == FOR { out.push_str(&format!("{}FOR {} IN {}:\n", ind, p, v)); ind.push_str("  "); }
        else if t == END { ind.truncate(ind.len().saturating_sub(2)); }
        else if t == LLM_CALL { out.push_str(&format!("{}{} = {}\n", ind, LLM_CALL, v)); }
        else { out.push_str(&format!("{}{}.{} = {}\n", ind, t, p, v)); }
    }
}

/// Print setters in the text form (`parse(print(s)) == s`). Used to migrate translated scripts.
pub fn print(sets: &[OnSet]) -> String {
    let mut out = String::new();
    for s in sets {
        out.push_str(&format!("{}.{} on-setter {}:\n", s.cls, s.prop, s.name));
        if s.when == "True" && s.els.is_empty() {
            out.push_str("  ALWAYS:\n");
        } else {
            out.push_str(&format!("  WHEN {}:\n", s.when));
        }
        print_writes(&mut out, &s.writes, "    ");
        for (c, ws) in &s.els {
            if c == "True" { out.push_str("  ELSE:\n"); } else { out.push_str(&format!("  ELSE WHEN {}:\n", c)); }
            print_writes(&mut out, ws, "    ");
        }
    }
    out
}
