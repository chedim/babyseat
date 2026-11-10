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

fn statement(s: &str, at: usize) -> Result<W, String> {
    let eq = assign_eq(s).ok_or(format!("line {}: expected `<target>.<field> = <expr>`, got `{}`", at, s))?;
    let (lhs, rhs) = (s[..eq].trim(), s[eq + 1..].trim());
    if rhs.is_empty() { return Err(format!("line {}: `{}` has no value", at, lhs)); }
    if lhs == LLM_CALL { return Ok((LLM_CALL.into(), String::new(), rhs.into())); }
    let (tgt, field) = lhs.rsplit_once('.').ok_or(format!("line {}: `{}` needs a target: write `this.{}`", at, lhs, lhs))?;
    if tgt.is_empty() || field.is_empty() { return Err(format!("line {}: bad target `{}`", at, lhs)); }
    Ok((tgt.into(), field.into(), rhs.into()))
}

/// Parse setters in cliffhanger's text form (getters are skipped: `parse_pack` returns both).
pub fn parse(src: &str) -> Result<Vec<OnSet>, String> { parse_pack(src).map(|p| p.0) }

/// Parse a pack: its setters and its getters (`<Class>.<field> getter: <expr>`, the expression on the header line
/// or on indented lines under it). Errors name the line and say what was expected.
pub fn parse_pack(src: &str) -> Result<(Vec<OnSet>, Vec<Getter>), String> {
    let mut getters: Vec<Getter> = vec![];
    let mut in_getter = false;
    let mut out: Vec<OnSet> = vec![];
    // the branches of the setter being read: (condition, writes); `None` before its first line
    let mut branches: Vec<(String, Vec<W>)> = vec![];
    let mut state = 0u8; // 0: before any branch; 1: in WHEN/ELSE WHEN; 2: after ELSE or ALWAYS (no more branches)
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
            if let Some((path, expr)) = line.split_once(" getter:") {
                if !in_getter { close(&mut out, &mut branches)?; }
                if let Some(g) = getters.last() { if g.expr.trim().is_empty() { return Err(format!("getter {}.{} has no expression", g.cls, g.prop)); } }
                let (cls, prop) = path.trim().split_once('.').ok_or(format!("line {}: expected `<Class>.<field> getter:`", at))?;
                getters.push(Getter { cls: cls.into(), prop: prop.into(), expr: expr.trim().into() });
                in_getter = true;
                continue;
            }
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
        if in_getter {                                  // a getter's expression continues on indented lines
            let g = getters.last_mut().unwrap();
            g.expr = format!("{} {}", g.expr, line.trim()).trim().to_string();
            continue;
        }
        if out.is_empty() { return Err(format!("line {}: indented line before any setter header", at)); }
        let body = line.trim();
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
    if !in_getter { close(&mut out, &mut branches)?; }
    if let Some(s) = out.iter().find(|s| s.writes.is_empty() && s.els.iter().all(|(_, w)| w.is_empty())) {
        return Err(format!("setter {} writes nothing", s.name));
    }
    if let Some(g) = getters.iter().find(|g| g.expr.is_empty()) { return Err(format!("getter {}.{} has no expression", g.cls, g.prop)); }
    for g in &getters { crate::expr::parse(&g.expr).map_err(|e| format!("getter {}.{}: {}", g.cls, g.prop, e))?; }
    Ok((out, getters))
}

fn print_writes(out: &mut String, ws: &[W], indent: &str) {
    for (t, p, v) in ws {
        if t == LLM_CALL { out.push_str(&format!("{}{} = {}\n", indent, LLM_CALL, v)); }
        else { out.push_str(&format!("{}{}.{} = {}\n", indent, t, p, v)); }
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
