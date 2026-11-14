//! The cliffhanger expression language (ch.py `_parse` / `_ev`), native.
//!
//! A small safe subset of Python expressions: constants, names, lists/tuples, dicts with literal
//! string keys, `x.attr` on entities, + - * /, unary - and not, and/or, comparisons (== != < <= > >=
//! in, not in), and calls of the built-ins. Semantics follow Python's (truthiness, int vs float, a
//! comparison that would raise TypeError is false, and/or return an operand).
//!
//! Blocks (operator, 2026-11-12): `parse_block` reads a getter's body, lines separated by `\n`: `name = expr` lines define
//! LOCALS (visible to the later lines of the block only, never stored), the last line is the value; a parenthesised block
//! `(a = 1\n a + 1)` is one arm of a folded getter. ch.py's 64-node / 400-character caps are intentionally NOT kept: an
//! evaluation has a step budget instead (`Budget`), a reasoned null when it runs out.

use std::collections::HashMap;
use std::sync::Arc as Rc;

pub const VEC_MAX: usize = 8192;
/// The parser's nesting depth and the evaluator's (stack safety, not a size cap: a chain of `a + b + c ...` is flat).
const NEST_MAX: usize = 64;
const EVAL_DEPTH_MAX: usize = 256;

// ---- the evaluation budget -------------------------------------------------------------------------------------------

/// Steps an evaluation may take before it is a reasoned null (`error: budget exceeded (N steps)`). A THRESHOLD the operator
/// still has to approve. Overridden by the env MEMBRANE_EVAL_BUDGET. All accounting lives in `Budget`.
pub const DEFAULT_EVAL_BUDGET: u64 = 100_000;

thread_local! { static BUDGET_OVERRIDE: std::cell::Cell<Option<u64>> = const { std::cell::Cell::new(None) }; }

/// Pin the budget on this thread (tests); None goes back to the env / default.
pub fn set_eval_budget(n: Option<u64>) { BUDGET_OVERRIDE.with(|o| o.set(n)); }

/// The budget an evaluation starts with: the thread's override, else MEMBRANE_EVAL_BUDGET, else the default.
pub fn eval_budget() -> u64 {
    if let Some(n) = BUDGET_OVERRIDE.with(|o| o.get()) { return n; }
    static ENV: std::sync::OnceLock<u64> = std::sync::OnceLock::new();
    *ENV.get_or_init(|| std::env::var("MEMBRANE_EVAL_BUDGET").ok().and_then(|v| v.trim().parse::<u64>().ok()).filter(|n| *n > 0).unwrap_or(DEFAULT_EVAL_BUDGET))
}

/// The self-time allowance of ONE getter or setter evaluation, in ms (operator-approved 2026-11-12; a dial, tuned later).
/// Overridden by the env MEMBRANE_EVAL_TIME_MS.
pub const DEFAULT_EVAL_TIME_MS: u64 = 10;
/// The step counter doubles as the in-evaluation backstop: every this many steps the clock is read, so a runaway loop inside
/// ONE evaluation trips the allowance before it ends. A power of two.
pub const TIME_CHECK_EVERY: u64 = 256;

/// A monotonic clock in nanoseconds. Injectable (`set_clock`), so a test needs no sleep.
pub type ClockFn = fn() -> u64;

thread_local! {
    static TIME_OVERRIDE: std::cell::Cell<Option<u64>> = const { std::cell::Cell::new(None) };
    static CLOCK_OVERRIDE: std::cell::Cell<Option<ClockFn>> = const { std::cell::Cell::new(None) };
    /// nanoseconds this thread has spent WAITING in host calls (`waiting`), summed; an evaluation subtracts what grew during it
    static WAITED: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
}

/// Pin the allowance on this thread (tests); None goes back to the env / default.
pub fn set_eval_time_ms(n: Option<u64>) { TIME_OVERRIDE.with(|o| o.set(n)); }
/// Inject the clock on this thread (tests); None goes back to the real one.
pub fn set_clock(c: Option<ClockFn>) { CLOCK_OVERRIDE.with(|o| o.set(c)); }

/// The allowance an evaluation starts with: the thread's override, else MEMBRANE_EVAL_TIME_MS, else the default.
pub fn eval_time_ms() -> u64 {
    if let Some(n) = TIME_OVERRIDE.with(|o| o.get()) { return n; }
    static ENV: std::sync::OnceLock<u64> = std::sync::OnceLock::new();
    *ENV.get_or_init(|| std::env::var("MEMBRANE_EVAL_TIME_MS").ok().and_then(|v| v.trim().parse::<u64>().ok()).filter(|n| *n > 0).unwrap_or(DEFAULT_EVAL_TIME_MS))
}

/// The browser build: std's Instant panics on wasm32-unknown-unknown, so the clock is the host's `Date.now` (milliseconds,
/// which is all a 10 ms allowance needs; the same import works in a page, a worker and Node).
#[cfg(all(target_arch = "wasm32", feature = "wasm"))]
fn real_clock() -> u64 {
    #[wasm_bindgen::prelude::wasm_bindgen]
    extern "C" {
        #[wasm_bindgen::prelude::wasm_bindgen(js_namespace = Date, js_name = now)]
        fn date_now() -> f64;
    }
    (date_now() * 1_000_000.0) as u64
}
#[cfg(not(all(target_arch = "wasm32", feature = "wasm")))]
fn real_clock() -> u64 {
    static T0: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    T0.get_or_init(std::time::Instant::now).elapsed().as_nanos() as u64
}
fn now_ns() -> u64 { match CLOCK_OVERRIDE.with(|o| o.get()) { Some(c) => c(), None => real_clock() } }

/// The evaluation clock in ns (the override a test installed, else the real one): `stats.rs` times with it.
pub fn clock_ns() -> u64 { now_ns() }
/// The ns this thread has spent waiting in host calls so far (`waiting`).
pub fn waited_ns() -> u64 { WAITED.with(|w| w.get()) }

/// Run a host call that may WAIT on I/O: the time it takes is not the evaluation's self-time.
pub fn waiting<T>(f: impl FnOnce() -> T) -> T {
    let t0 = now_ns();
    let r = f();
    let d = now_ns().saturating_sub(t0);
    WAITED.with(|w| w.set(w.get().saturating_add(d)));
    r
}

/// The one place an evaluation's cost is counted: `charge` a step, ask if it is `exceeded`, say the `reason`; and its self-time
/// (`check_time`: two clock reads per evaluation, plus one every `TIME_CHECK_EVERY` steps).
#[derive(Debug)]
pub struct Budget { used: u64, limit: u64, t0: u64, waited0: u64, allow_ns: u64 }

impl Budget {
    pub fn new() -> Budget {
        Budget { used: 0, limit: eval_budget(), t0: now_ns(), waited0: WAITED.with(|w| w.get()), allow_ns: eval_time_ms().saturating_mul(1_000_000) }
    }
    pub fn charge(&mut self, n: u64) -> Result<(), Err> {
        let before = self.used;
        self.used = self.used.saturating_add(n);
        if self.exceeded() { return Err(self.reason()); }
        if self.used / TIME_CHECK_EVERY != before / TIME_CHECK_EVERY { self.check_time()?; }
        Ok(())
    }
    pub fn used(&self) -> u64 { self.used }
    pub fn exceeded(&self) -> bool { self.used > self.limit }
    pub fn reason(&self) -> Err { format!("budget exceeded ({} steps)", self.limit) }
    /// Self-time so far: elapsed minus the time spent waiting in host calls.
    fn self_ns(&self) -> u64 {
        let waited = WAITED.with(|w| w.get()).saturating_sub(self.waited0);
        now_ns().saturating_sub(self.t0).saturating_sub(waited)
    }
    /// Over the allowance: `timeout N ms (allowance M ms)` (N rounded up).
    pub fn check_time(&self) -> Result<(), Err> {
        let ns = self.self_ns();
        if ns > self.allow_ns { Err(timeout_reason(ns.div_ceil(1_000_000), self.allow_ns / 1_000_000)) } else { Ok(()) }
    }
}

/// The words of a time trip.
pub fn timeout_reason(self_ms: u64, allowance_ms: u64) -> String { format!("timeout {self_ms} ms (allowance {allowance_ms} ms)") }
/// `(N, M)` out of a `timeout N ms (allowance M ms)` error; None for any other error.
pub fn parse_timeout(e: &str) -> Option<(u64, u64)> {
    let r = e.strip_prefix("timeout ")?;
    let (n, r) = r.split_once(" ms (allowance ")?;
    let m = r.strip_suffix(" ms)")?;
    Some((n.parse().ok()?, m.parse().ok()?))
}

/// What one evaluation carries: its budget, its depth, and (when asked) the locals it bound, in order.
struct Meter { b: Budget, depth: usize, locals: Option<Vec<(String, V)>> }

#[derive(Clone, Debug)]
pub enum V {
    None,
    /// A null that says why: `pending: read(/a)`, `error: ...`, `absent ...` (nulls propagate like SQL's; operator 2026-11-12)
    Nil(String),
    /// A FAILURE (#1143, docs/c1f-error.md): carries its reason, the remedy being part of the text. Not a null: `x == None` is False for it, it
    /// propagates through every operation that uses it, and `x is Error` is the test. Stored, it is a plain null (the kind lives in `Core.why`).
    Error(String),
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<V>),
    Dict(Vec<(String, V)>),
    Ent(String),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Op { Add, Sub, Mul, Div }

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Cmp { Eq, Ne, Lt, Le, Gt, Ge, In, NotIn }

#[derive(Debug)]
pub enum Node {
    Const(V),
    Name(String),
    List(Vec<Node>),
    Dict(Vec<(String, Node)>),
    Attr(Box<Node>, String),
    /// `base[index]`: a list by position, a dict by key, an entity by property name (a miss is None)
    Index(Box<Node>, Box<Node>),
    Bin(Op, Box<Node>, Box<Node>),
    Neg(Box<Node>),
    Not(Box<Node>),
    And(Vec<Node>),
    Or(Vec<Node>),
    Compare(Box<Node>, Vec<(Cmp, Node)>),
    Call(String, Vec<Node>),
    /// `[elt for var in iter if cond]` (#994 getters: a computed value over other entities)
    Comp(Box<Node>, String, Box<Node>, Option<Box<Node>>),
    /// `a if cond else b` (then, cond, else)
    If(Box<Node>, Box<Node>, Box<Node>),
    /// `x is Kind`, `x is (A, B)` (every name must match), `x is not Kind`: the kind names are not evaluated (names, negated)
    Is(Box<Node>, Vec<String>, bool),
    /// a block's local: `name = value` then the rest of the block, where `name` is bound
    Let(String, Box<Node>, Box<Node>),
    /// an unquoted entity literal: `Goal:board`, `membrane::Chat:message[id]:response` (its text, with each `[key]` evaluated and
    /// appended as `:<key>`)
    Ent(Vec<EntPart>),
    /// `Class(name: value, ...)`: a constructor (named arguments only). It means something only as the right of `<entity> = ...`,
    /// where the entity on the left is its identity (fields.rs); anywhere else it is a reasoned error.
    Ctor(String, Vec<(String, Node)>),
}

/// A piece of an entity literal: text as written (`membrane::Chat:message`, `:response`), or a `[key]` to evaluate.
#[derive(Debug)]
pub enum EntPart { Lit(String), Key(Node) }

impl Node {
    /// The children, taken out (what `Drop` walks with a stack, so a deep expression never overflows it).
    fn take_children(&mut self, out: &mut Vec<Node>) {
        let mut b = |x: &mut Box<Node>| out.push(std::mem::replace(&mut **x, Node::Const(V::None)));
        match self {
            Node::Const(_) | Node::Name(_) => {}
            Node::List(v) | Node::And(v) | Node::Or(v) | Node::Call(_, v) => out.extend(v.drain(..)),
            Node::Dict(v) => out.extend(v.drain(..).map(|(_, n)| n)),
            Node::Attr(a, _) | Node::Neg(a) | Node::Not(a) | Node::Is(a, _, _) => b(a),
            Node::Index(a, c) | Node::Bin(_, a, c) | Node::Let(_, a, c) => { b(a); b(c); }
            Node::Compare(a, ops) => { b(a); out.extend(ops.drain(..).map(|(_, n)| n)); }
            Node::Comp(e, _, i, c) => { b(e); b(i); if let Some(c) = c { b(c); } }
            Node::If(a, c, d) => { b(a); b(c); b(d); }
            Node::Ent(parts) => for p in parts.drain(..) { if let EntPart::Key(n) = p { out.push(n); } },
            Node::Ctor(_, args) => out.extend(args.drain(..).map(|(_, n)| n)),
        }
    }
}

impl Drop for Node {
    fn drop(&mut self) {
        let mut stack: Vec<Node> = vec![];
        self.take_children(&mut stack);
        while let Some(mut n) = stack.pop() { n.take_children(&mut stack); }
    }
}

pub type Err = String;

// ---- tokens -------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
enum Tok {
    Int(i64), Float(f64), Str(String), Name(String), P(&'static str), End,
    /// an unquoted entity literal's text: `Goal:board`, `membrane::Config:voice:in` (a name, then `:segment`s, directly joined)
    Ent(String),
    /// `:name` directly after a `]`: the path of an entity literal continuing after a `[key]`
    Seg(String),
}

fn name_char(c: Option<&char>) -> bool { c.map_or(false, |c| c.is_alphanumeric() || *c == '_') }

fn lex(s: &str, block: bool) -> Result<Vec<Tok>, Err> {
    let c: Vec<char> = s.chars().collect();
    let mut i = 0;
    let mut out = Vec::new();
    while i < c.len() {
        let ch = c[i];
        if ch == '\n' && block { out.push(Tok::P(";")); i += 1; continue; }
        if ch.is_whitespace() { i += 1; continue; }
        if ch.is_ascii_digit() || (ch == '.' && i + 1 < c.len() && c[i + 1].is_ascii_digit()) {
            let st = i;
            let mut float = false;
            while i < c.len() && (c[i].is_ascii_digit() || c[i] == '_') { i += 1; }
            if i < c.len() && c[i] == '.' { float = true; i += 1; while i < c.len() && c[i].is_ascii_digit() { i += 1; } }
            if i < c.len() && (c[i] == 'e' || c[i] == 'E') {
                let save = i;
                i += 1;
                if i < c.len() && (c[i] == '+' || c[i] == '-') { i += 1; }
                if i < c.len() && c[i].is_ascii_digit() { float = true; while i < c.len() && c[i].is_ascii_digit() { i += 1; } } else { i = save; }
            }
            let txt: String = c[st..i].iter().filter(|x| **x != '_').collect();
            if i < c.len() && (c[i].is_alphanumeric() || c[i] == '_') { return Err(format!("not an expression: bad number near {:?}", txt)); }
            if float {
                out.push(Tok::Float(txt.parse::<f64>().map_err(|e| e.to_string())?));
            } else {
                match txt.parse::<i64>() { Ok(v) => out.push(Tok::Int(v)), Err(_) => return Err("an integer too large".into()) }
            }
            continue;
        }
        if ch == '\'' || ch == '"' {
            let q = ch;
            i += 1;
            let mut sb = String::new();
            loop {
                if i >= c.len() { return Err("not an expression: unterminated string".into()); }
                let d = c[i];
                if d == q { i += 1; break; }
                if d == '\\' && i + 1 < c.len() {
                    let e = c[i + 1];
                    let esc = match e { 'n' => Some('\n'), 't' => Some('\t'), 'r' => Some('\r'), '0' => Some('\0'), '\\' => Some('\\'), '\'' => Some('\''), '"' => Some('"'), _ => None };
                    match esc { Some(x) => sb.push(x), None => { sb.push('\\'); sb.push(e); } }
                    i += 2;
                    continue;
                }
                if d == '\n' { return Err("not an expression: newline in a string".into()); }
                sb.push(d);
                i += 1;
            }
            out.push(Tok::Str(sb));
            continue;
        }
        if ch.is_alphabetic() || ch == '_' {
            let st = i;
            while i < c.len() && (c[i].is_alphanumeric() || c[i] == '_') { i += 1; }
            let first: String = c[st..i].iter().collect();
            // an entity literal: `name::ns...` / `name:seg:seg`, a colon DIRECTLY followed by a name character (a segment may be a word
            // that is an operator elsewhere). Not after a dot (`this.x`), and never starting with a keyword or constant.
            let starts = !(st > 0 && c[st - 1] == '.') && !KEYWORDS.contains(&first.as_str()) && !["True", "False", "None"].contains(&first.as_str());
            if starts && i < c.len() && c[i] == ':' && (name_char(c.get(i + 1)) || (c.get(i + 1) == Some(&':') && name_char(c.get(i + 2)))) {
                let mut text = first;
                let mut single = false;
                loop {
                    if !single && c.get(i) == Some(&':') && c.get(i + 1) == Some(&':') && name_char(c.get(i + 2)) && !text.contains(':') { text.push_str("::"); i += 2; }
                    else if c.get(i) == Some(&':') && name_char(c.get(i + 1)) { text.push(':'); i += 1; single = true; }
                    else { break; }
                    while i < c.len() && name_char(Some(&c[i])) { text.push(c[i]); i += 1; }
                }
                out.push(Tok::Ent(text));
                continue;
            }
            out.push(Tok::Name(first));
            continue;
        }
        if ch == ':' && i > 0 && c[i - 1] == ']' && name_char(c.get(i + 1)) {
            let mut text = String::new();
            while c.get(i) == Some(&':') && name_char(c.get(i + 1)) {
                text.push(':'); i += 1;
                while i < c.len() && name_char(Some(&c[i])) { text.push(c[i]); i += 1; }
            }
            out.push(Tok::Seg(text));
            continue;
        }
        let two: String = c[i..(i + 2).min(c.len())].iter().collect();
        let p: Option<&'static str> = match two.as_str() {
            "==" => Some("=="), "!=" => Some("!="), "<=" => Some("<="), ">=" => Some(">="), "**" => Some("**"), "//" => Some("//"),
            _ => None,
        };
        if let Some(p) = p { out.push(Tok::P(p)); i += 2; continue; }
        let p: &'static str = match ch {
            '+' => "+", '-' => "-", '*' => "*", '/' => "/", '(' => "(", ')' => ")", '[' => "[", ']' => "]",
            '{' => "{", '}' => "}", ',' => ",", ':' => ":", '.' => ".", '<' => "<", '>' => ">", '%' => "%",
            '=' => "=", '~' => "~", '&' => "&", '|' => "|", '^' => "^", '@' => "@",
            _ => return Err(format!("not an expression: unexpected {:?}", ch)),
        };
        out.push(Tok::P(p));
        i += 1;
    }
    out.push(Tok::End);
    Ok(out)
}

// ---- parser (Python's precedence) -------------------------------------------------------------

struct P { t: Vec<Tok>, i: usize, depth: usize, block: bool }

const IS_NAMES: &str = "`is` takes a kind or class name (Error, Int, Float, Text, Bool, List, Dict, or a class like Trip), or a list of them: `x is (Trip, Place)`";
const IS_CHAIN: &str = "not allowed in an expression: `is` cannot chain with another comparison (write `(a == b) is Error`, or use `and`)";
/// The kinds a VALUE can be, as `is` spells them (the casts' spellings, and `Error`); every other name on the right of `is` is a class.
const VALUE_KINDS: &[&str] = &["Int", "Float", "Text", "Bool", "List", "Dict", "Error"];

/// Names a local may not take: the builtins and host functions, `this`/`x`/`clock`, the constants and the pronouns.
/// (keep in step with `call`: a test reads its arms)
pub const RESERVED: &[&str] = &[
    "Int", "Float", "Num", "Text", "Bool", "List", "Dict", "Error", "None", "True", "False", "this", "x", "clock",
    "dot", "norm", "distance", "unit", "abs", "min", "max", "len", "lower", "strip", "replace", "startswith", "lines", "words", "spans",
    "join", "head", "tail", "sort_by", "unspeak", "before", "after", "pluck", "distinct", "group_by", "concat", "fit", "clip", "ago",
    "plain", "num", "epoch", "localtime", "json", "str", "referencing", "prop", "why",
    "instances", "jsonl", "listdir", "read", "sql", "stat", "recall", "rerank", "pronoun",
    "it", "that", "they", "them", "these", "those", "he", "him", "she", "her",
    "and", "or", "not", "in", "is", "if", "else", "lambda", "for", "await", "yield",
];

const KEYWORDS: &[&str] = &["and", "or", "not", "in", "is", "if", "else", "lambda", "for", "await", "yield"];

impl P {
    fn peek(&self) -> &Tok { &self.t[self.i] }
    fn next(&mut self) -> Tok { let t = self.t[self.i].clone(); if self.i + 1 < self.t.len() { self.i += 1; } t }
    fn is_p(&self, p: &str) -> bool { matches!(self.peek(), Tok::P(x) if *x == p) }
    fn is_kw(&self, k: &str) -> bool { matches!(self.peek(), Tok::Name(x) if x == k) }
    fn expect(&mut self, p: &str) -> Result<(), Err> {
        if self.is_p(p) { self.next(); Ok(()) } else { Err(format!("not an expression: expected {:?}", p)) }
    }
    fn nest(&mut self) -> Result<(), Err> {
        self.depth += 1;
        if self.depth > NEST_MAX { Err(format!("not an expression: nested more than {} deep", NEST_MAX)) } else { Ok(()) }
    }

    fn expr(&mut self) -> Result<Node, Err> {
        self.nest()?;
        let r = self.expr_inner();
        self.depth -= 1;
        r
    }
    fn expr_inner(&mut self) -> Result<Node, Err> {
        if self.is_kw("lambda") || self.is_kw("await") || self.is_kw("yield") { return Err("not allowed in an expression: Lambda".into()); }
        let e = self.or_test()?;
        if !self.is_kw("if") { return Ok(e); }
        self.next();
        let cond = self.or_test()?;
        if !self.is_kw("else") { return Err("not an expression: `if` needs its `else`".into()); }
        self.next();
        let other = self.expr()?;
        Ok(Node::If(Box::new(e), Box::new(cond), Box::new(other)))
    }
    fn or_test(&mut self) -> Result<Node, Err> {
        let a = self.and_test()?;
        if !self.is_kw("or") { return Ok(a); }
        let mut v = vec![a];
        while self.is_kw("or") { self.next(); v.push(self.and_test()?); }
        Ok(Node::Or(v))
    }
    fn and_test(&mut self) -> Result<Node, Err> {
        let a = self.not_test()?;
        if !self.is_kw("and") { return Ok(a); }
        let mut v = vec![a];
        while self.is_kw("and") { self.next(); v.push(self.not_test()?); }
        Ok(Node::And(v))
    }
    fn not_test(&mut self) -> Result<Node, Err> {
        if self.is_kw("not") { self.next(); self.nest()?; let o = self.not_test(); self.depth -= 1; return Ok(Node::Not(Box::new(o?))); }
        self.comparison()
    }
    fn comp_op(&mut self) -> Result<Option<Cmp>, Err> {
        let c = match self.peek() {
            Tok::P("==") => Cmp::Eq, Tok::P("!=") => Cmp::Ne, Tok::P("<") => Cmp::Lt, Tok::P("<=") => Cmp::Le,
            Tok::P(">") => Cmp::Gt, Tok::P(">=") => Cmp::Ge,
            Tok::Name(x) if x == "in" => Cmp::In,
            Tok::Name(x) if x == "not" => {
                if matches!(self.t.get(self.i + 1), Some(Tok::Name(y)) if y == "in") { self.next(); Cmp::NotIn } else { return Ok(None); }
            }
            Tok::Name(x) if x == "is" => return Err(IS_CHAIN.into()),
            _ => return Ok(None),
        };
        self.next();
        Ok(Some(c))
    }
    fn comparison(&mut self) -> Result<Node, Err> {
        let a = self.arith()?;
        if self.is_kw("is") { return self.is_clause(a); }
        let mut ops = Vec::new();
        while let Some(c) = self.comp_op()? { let b = self.arith()?; ops.push((c, b)); }
        if ops.is_empty() { return Ok(a); }
        Ok(Node::Compare(Box::new(a), ops))
    }
    /// `is Kind`, `is not Kind`, `is (A, B)`: the right side is names, never evaluated. It does not chain with another comparison.
    fn is_clause(&mut self, a: Node) -> Result<Node, Err> {
        self.next();
        let neg = if self.is_kw("not") { self.next(); true } else { false };
        let mut names: Vec<String> = vec![];
        let mut name = |p: &mut P| -> Result<(), Err> {
            match p.next() {
                Tok::Name(n) if n == "None" => Err("`is None` is not a test: write `x == None` for the null test".into()),
                Tok::Name(n) if !KEYWORDS.contains(&n.as_str()) && n != "True" && n != "False" => { names.push(n); Ok(()) }
                _ => Err(IS_NAMES.into()),
            }
        };
        if self.is_p("(") {
            self.next();
            loop {
                name(self)?;
                if self.is_p(",") { self.next(); if self.is_p(")") { break; } continue; }
                break;
            }
            if !self.is_p(")") { return Err(IS_NAMES.into()); }
            self.next();
        } else { name(self)?; }
        if self.is_p(".") || self.is_p("(") || self.is_p("[") { return Err(IS_NAMES.into()); }
        let mut seen: Vec<&String> = vec![];
        for n in &names { if !seen.contains(&n) { seen.push(n); } }
        if seen.len() > 1 && seen.iter().any(|n| VALUE_KINDS.contains(&n.as_str())) {
            return Err(format!("`is ({})`: those kinds can never both match (a value is of one kind; only classes can be listed together)", names.join(", ")));
        }
        if matches!(self.peek(), Tok::P("==" | "!=" | "<" | "<=" | ">" | ">=")) || self.is_kw("in") || self.is_kw("is") || (self.is_kw("not") && matches!(self.t.get(self.i + 1), Some(Tok::Name(y)) if y == "in")) {
            return Err(IS_CHAIN.into());
        }
        Ok(Node::Is(Box::new(a), names, neg))
    }
    fn arith(&mut self) -> Result<Node, Err> {
        let mut a = self.term()?;
        loop {
            let op = if self.is_p("+") { Op::Add } else if self.is_p("-") { Op::Sub } else { break };
            self.next();
            let b = self.term()?;
            a = Node::Bin(op, Box::new(a), Box::new(b));
        }
        Ok(a)
    }
    fn term(&mut self) -> Result<Node, Err> {
        let mut a = self.factor()?;
        loop {
            let op = if self.is_p("*") { Op::Mul } else if self.is_p("/") { Op::Div }
                     else if self.is_p("//") || self.is_p("%") || self.is_p("@") { return Err("not allowed in an expression: BinOp".into()); }
                     else { break };
            self.next();
            let b = self.factor()?;
            a = Node::Bin(op, Box::new(a), Box::new(b));
        }
        Ok(a)
    }
    fn factor(&mut self) -> Result<Node, Err> {
        if self.is_p("-") { self.next(); self.nest()?; let o = self.factor(); self.depth -= 1; return Ok(Node::Neg(Box::new(o?))); }
        if self.is_p("+") || self.is_p("~") { return Err("not allowed in an expression: UnaryOp".into()); }
        let a = self.primary()?;
        if self.is_p("**") { return Err("not allowed in an expression: BinOp".into()); }
        Ok(a)
    }
    fn primary(&mut self) -> Result<Node, Err> {
        let mut a = self.atom()?;
        loop {
            if self.is_p(".") {
                self.next();
                match self.next() {
                    Tok::Name(n) => { a = Node::Attr(Box::new(a), n); }
                    _ => return Err("not an expression: attribute".into()),
                }
            } else if self.is_p("(") {
                let f = match a { Node::Name(ref n) => n.clone(), Node::Const(V::None) => "None".to_string(), _ => return Err("only the built-ins may be called".into()) };
                self.next();
                let mut args = Vec::new();
                let mut named: Vec<(String, Node)> = Vec::new();
                while !self.is_p(")") {
                    if self.is_p("*") || self.is_p("**") { return Err("not allowed in an expression: Starred".into()); }
                    if matches!(self.peek(), Tok::Name(_)) && matches!(self.t.get(self.i + 1), Some(Tok::P("="))) {
                        return Err("only the built-ins may be called".into());
                    }
                    // `name: value`: a constructor's argument
                    if let (Tok::Name(n), Some(Tok::P(":"))) = (self.peek().clone(), self.t.get(self.i + 1)) {
                        if KEYWORDS.contains(&n.as_str()) || ["True", "False", "None"].contains(&n.as_str()) { return Err(format!("not an argument name: {:?}", n)); }
                        self.next(); self.next();
                        if named.iter().any(|(k, _)| *k == n) { return Err(format!("the constructor argument `{}` is given twice", n)); }
                        named.push((n, self.expr()?));
                    } else {
                        args.push(self.expr()?);
                    }
                    if !self.is_p(")") { self.expect(",")?; }
                }
                self.next();
                if named.is_empty() { a = Node::Call(f, args); }
                else if !args.is_empty() { return Err(format!("a constructor's arguments are all named (name: value): {}(...)", f)); }
                else { a = Node::Ctor(f, named); }
            } else if self.is_p("[") {
                self.next();
                let idx = self.expr()?;
                if self.is_p(":") { return Err("slices are not allowed in an expression: use head / pluck".into()); }
                self.expect("]")?;
                a = Node::Index(Box::new(a), Box::new(idx));
            } else {
                break;
            }
        }
        Ok(a)
    }
    fn atom(&mut self) -> Result<Node, Err> {
        match self.next() {
            Tok::Int(v) => { Ok(Node::Const(V::Int(v))) }
            Tok::Float(v) => { Ok(Node::Const(V::Float(v))) }
            Tok::Str(s) => {
                let mut s = s;
                while let Tok::Str(more) = self.peek().clone() { self.next(); s.push_str(&more); }
                Ok(Node::Const(V::Str(s)))
            }
            Tok::Ent(text) => {
                let mut parts = vec![EntPart::Lit(text)];
                loop {
                    if self.is_p("[") {
                        self.next();
                        let k = self.expr()?;
                        self.expect("]")?;
                        parts.push(EntPart::Key(k));
                    } else if matches!(self.peek(), Tok::Seg(_)) && matches!(parts.last(), Some(EntPart::Key(_))) {
                        if let Tok::Seg(t) = self.next() { parts.push(EntPart::Lit(t)); }
                    } else { break; }
                }
                Ok(Node::Ent(parts))
            }
            Tok::Name(n) => {
                match n.as_str() {
                    "True" => { Ok(Node::Const(V::Bool(true))) }
                    "False" => { Ok(Node::Const(V::Bool(false))) }
                    "None" => { Ok(Node::Const(V::None)) }
                    k if KEYWORDS.contains(&k) => Err(format!("not an expression: {:?}", k)),
                    _ => { Ok(Node::Name(n)) }
                }
            }
            Tok::P("(") => {
                if self.block {
                    while self.is_p(";") { self.next(); }
                    if self.at_local() { let b = self.block_body()?; self.expect(")")?; return Ok(b); }
                }
                if self.is_p(")") { self.next(); return Ok(Node::List(vec![])); }
                let first = self.expr()?;
                if self.is_p(")") { self.next(); return Ok(first); }
                let mut v = vec![first];
                while self.is_p(",") {
                    self.next();
                    if self.is_p(")") { break; }
                    v.push(self.expr()?);
                }
                self.expect(")")?;
                Ok(Node::List(v))
            }
            Tok::P("[") => {
                let mut v = Vec::new();
                while !self.is_p("]") {
                    v.push(self.expr()?);
                    if self.is_kw("for") {
                        if v.len() != 1 { return Err("a comprehension starts the list: [x for x in xs]".into()); }
                        self.next();
                        let var = match self.next() { Tok::Name(n) if !KEYWORDS.contains(&n.as_str()) => n, _ => return Err("for needs a name".into()) };
                        if !self.is_kw("in") { return Err("expected `in` in a comprehension".into()); }
                        self.next();
                        let iter = self.or_test()?;
                        let cond = if self.is_kw("if") { self.next(); Some(Box::new(self.or_test()?)) } else { None };
                        self.expect("]")?;
                        let elt = v.pop().unwrap();
                        return Ok(Node::Comp(Box::new(elt), var, Box::new(iter), cond));
                    }
                    if !self.is_p("]") { self.expect(",")?; }
                }
                self.next();
                Ok(Node::List(v))
            }
            Tok::P("{") => {
                let mut v = Vec::new();
                while !self.is_p("}") {
                    let k = self.expr()?;
                    if !self.is_p(":") { return Err("not allowed in an expression: Set".into()); }
                    self.next();
                    let val = self.expr()?;
                    let key = match &k { Node::Const(V::Str(s)) => s.clone(), _ => return Err("a dict's keys are literal strings".into()) };
                    v.push((key, val));
                    if !self.is_p("}") { self.expect(",")?; }
                }
                self.next();
                Ok(Node::Dict(v))
            }
            t => Err(format!("not an expression: unexpected {:?}", t)),
        }
    }
}

/// A constructor's class and named arguments when `src` is exactly `Class(name: value, ...)`; the reason when it is not.
pub fn parse_ctor(src: &str) -> Result<(String, Vec<(String, Node)>), String> {
    let toks = lex(src, false)?;
    let mut p = P { t: toks, i: 0, depth: 0, block: false };
    let mut n = p.expr()?;
    if *p.peek() != Tok::End { return Err("not an expression: trailing input".into()); }
    match &mut n {
        Node::Ctor(c, a) => Ok((std::mem::take(c), std::mem::take(a))),
        Node::Call(f, _) => Err(format!("`{}(...)` takes named arguments to make an entity: `{}(name: value, ...)`", f, f)),
        _ => Err("an entity on the left of `=` takes a constructor: `Class(name: value, ...)`".into()),
    }
}

/// A plain expression (a setter's value or condition, a vector read): no blocks.
pub fn parse(src: &str) -> Result<Node, Err> { parse_with(src, false) }

/// A getter's body: `name = expr` lines (locals), then the value; lines are separated by `\n`.
pub fn parse_block(src: &str) -> Result<Node, Err> { parse_with(src, true) }

fn parse_with(src: &str, block: bool) -> Result<Node, Err> {
    let toks = lex(src, block)?;
    let mut p = P { t: toks, i: 0, depth: 0, block };
    let e = if block { p.block_body()? } else { p.expr()? };
    if *p.peek() != Tok::End { return Err("not an expression: trailing input".into()); }
    Ok(e)
}

impl P {
    /// at `name =` (an assignment, not `==`): the start of a local line
    fn at_local(&self) -> bool {
        matches!(self.peek(), Tok::Name(_)) && matches!(self.t.get(self.i + 1), Some(Tok::P("=")))
    }
    /// locals, then the value: `(name = expr ;)* expr`
    fn block_body(&mut self) -> Result<Node, Err> {
        let mut lets: Vec<(String, Node)> = vec![];
        loop {
            while self.is_p(";") { self.next(); }
            if !self.at_local() { break; }
            let name = match self.next() { Tok::Name(n) => n, _ => unreachable!() };
            if KEYWORDS.contains(&name.as_str()) || ["True", "False", "None"].contains(&name.as_str()) { return Err(format!("not a name for a local: {:?}", name)); }
            self.next();
            let e = self.expr()?;
            if !self.is_p(";") { return Err(format!("not an expression: the local `{}` is one line, then a new line", name)); }
            lets.push((name, e));
        }
        if matches!(self.peek(), Tok::End) || self.is_p(")") {
            return Err(if lets.is_empty() { "not an expression: empty".into() } else { "a block ends with its value: its last line is not a local".into() });
        }
        let mut node = self.expr()?;
        while self.is_p(";") { self.next(); }
        while let Some((name, v)) = lets.pop() { node = Node::Let(name, Box::new(v), Box::new(node)); }
        Ok(node)
    }
}

pub struct Cache { m: HashMap<String, Rc<Result<Node, Err>>> }

impl Cache {
    pub fn new() -> Self { Cache { m: HashMap::new() } }
    #[cfg(feature = "alloc-stats")]
    pub fn len(&self) -> usize { self.m.len() }
    /// Parsed as a block (a getter's body); an ordinary expression is a block of one line.
    pub fn get(&mut self, src: &str) -> Rc<Result<Node, Err>> {
        if let Some(h) = self.m.get(src) { return h.clone(); }
        if self.m.len() > 4096 { self.m.clear(); }
        let r = Rc::new(parse_block(src));
        self.m.insert(src.to_string(), r.clone());
        r
    }
}

// ---- evaluation ----------------------------------------------------------------------------------

/// What an expression reads off an entity.
pub trait World {
    fn attr(&self, eid: &str, attr: &str) -> V;
    /// The entities whose `prop` holds `eid` (a list containing it, or equal to it): `referencing(x, 'prop')`.
    fn refs(&self, _eid: &str, _prop: &str) -> Vec<String> { vec![] }
    /// A function the host runs (the seat's `recall(...)` and the other banner built-ins). None: not the host's.
    fn host(&self, _f: &str, _a: &[V]) -> Option<Result<V, Err>> { None }
    /// Is the entity of this class: its `parents` (nearest first), an ancestor's, or the root `Entity`? (`x is Trip`)
    fn is_class(&self, _eid: &str, _cls: &str) -> bool { false }
}

pub fn nullish(v: &V) -> bool { matches!(v, V::None | V::Nil(_)) }

/// The separator of one path's steps (a list index, a dict key) in the key a nested reasoned null is remembered under.
pub const PATH_SEP: char = '\u{1}';

/// The reasoned nulls INSIDE a list or a dict, each as (path, reason): the path is the list indexes and dict keys down to it, joined by `PATH_SEP`.
/// A stored value is JSON, where every null is plain; the engine remembers these beside it (`Core.why`) so that a read of the stored value gives them back.
pub fn nested_nulls(v: &V) -> Vec<(String, String)> {
    fn walk(v: &V, path: &mut Vec<String>, out: &mut Vec<(String, String)>) {
        match v {
            V::List(l) => for (i, x) in l.iter().enumerate() { path.push(i.to_string()); walk(x, path, out); path.pop(); },
            V::Dict(d) => for (k, x) in d { path.push(k.clone()); walk(x, path, out); path.pop(); },
            V::Nil(r) if !path.is_empty() => out.push((path.join(&PATH_SEP.to_string()), r.clone())),
            _ => {}
        }
    }
    let mut out = vec![];
    walk(v, &mut vec![], &mut out);
    out
}

/// `nested_nulls` read back: the null at each path becomes the reasoned null again (a path that no longer fits the value is skipped).
pub fn overlay_nulls(v: &mut V, nulls: &[(String, String)]) {
    fn put(v: &mut V, path: &[&str], reason: &str) {
        match path.split_first() {
            None => if matches!(v, V::None) { *v = V::Nil(reason.to_string()); },
            Some((step, rest)) => match v {
                V::List(l) => if let Some(x) = step.parse::<usize>().ok().and_then(|i| l.get_mut(i)) { put(x, rest, reason); },
                V::Dict(d) => if let Some((_, x)) = d.iter_mut().find(|(k, _)| k == step) { put(x, rest, reason); },
                _ => {}
            },
        }
    }
    for (path, reason) in nulls { put(v, &path.split(PATH_SEP).collect::<Vec<_>>(), reason); }
}

fn shown_marker(key: &str, reason: &str) -> serde_json::Value { serde_json::Value::Object(std::iter::once((key.to_string(), serde_json::Value::String(reason.to_string()))).collect()) }
/// A value as JSON text for a person or a model to read (`str` of a list or a dict, the clifflet's printout): like `to_json`, except that a reasoned null
/// INSIDE a container is written `{"$null": "<reason>"}` so the reason is on the page. (`to_json`, what is stored and sent, keeps every null plain.)
pub fn to_json_shown(v: &V) -> Result<serde_json::Value, Err> {
    use serde_json::Value as J;
    Ok(match v {
        V::List(l) => J::Array(l.iter().map(|x| match x { V::Nil(r) => Ok(shown_marker("$null", r)), V::Error(r) => Ok(shown_marker("$error", r)), x => to_json_shown(x) }).collect::<Result<_, Err>>()?),
        V::Dict(d) => J::Object(d.iter().map(|(k, x)| Ok((k.clone(), match x { V::Nil(r) => shown_marker("$null", r), V::Error(r) => shown_marker("$error", r), x => to_json_shown(x)? }))).collect::<Result<_, Err>>()?),
        v => to_json(v)?,
    })
}

/// The Error among operands, if any: it wins over a plain null, the first one wins among several. (An Error is contagious wherever a value is USED.)
fn first_error(vals: &[&V]) -> Option<V> { vals.iter().find(|v| matches!(v, V::Error(_))).map(|v| (*v).clone()) }

/// The null an operation on null operands gives: the first reason carried, else `absent`.
fn null_of(vals: &[&V], what: &str) -> Option<V> {
    for v in vals { if let V::Nil(r) = v { return Some(V::Nil(r.clone())); } }
    if vals.iter().any(|v| matches!(v, V::None)) { return Some(V::Nil(format!("absent operand of {what}"))); }
    None
}

pub fn truthy(v: &V) -> bool {
    match v {
        V::None | V::Nil(_) | V::Error(_) => false,
        V::Bool(b) => *b,
        V::Int(i) => *i != 0,
        V::Float(f) => *f != 0.0,
        V::Str(s) => !s.is_empty(),
        V::List(l) => !l.is_empty(),
        V::Dict(d) => !d.is_empty(),
        V::Ent(_) => true,
    }
}

fn numf(v: &V) -> Option<f64> {
    match v { V::Bool(b) => Some(if *b { 1.0 } else { 0.0 }), V::Int(i) => Some(*i as f64), V::Float(f) => Some(*f), _ => None }
}

pub fn py_eq(a: &V, b: &V) -> bool {
    match (a, b) {
        (V::Int(x), V::Int(y)) => x == y,
        (V::Bool(x), V::Bool(y)) => x == y,
        (V::None | V::Nil(_), V::None | V::Nil(_)) => true,
        (V::Str(x), V::Str(y)) => x == y,
        (V::List(x), V::List(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q)),
        (V::Dict(x), V::Dict(y)) => x.len() == y.len() && x.iter().all(|(k, v)| y.iter().any(|(k2, v2)| k == k2 && py_eq(v, v2))),
        (V::Ent(x), V::Ent(y)) => x == y,
        _ => match (numf(a), numf(b)) { (Some(x), Some(y)) => x == y, _ => false },
    }
}

/// Python's ordering: Some(ordering) or None where Python raises TypeError.
fn py_cmp(a: &V, b: &V) -> Option<std::cmp::Ordering> {
    match (a, b) {
        (V::Int(x), V::Int(y)) => Some(x.cmp(y)),
        (V::Str(x), V::Str(y)) => Some(x.cmp(y)),
        (V::List(x), V::List(y)) => {
            for (p, q) in x.iter().zip(y) {
                if !py_eq(p, q) { return py_cmp(p, q); }
            }
            Some(x.len().cmp(&y.len()))
        }
        _ => match (numf(a), numf(b)) { (Some(x), Some(y)) => x.partial_cmp(&y), _ => None },
    }
}

fn contains(a: &V, b: &V) -> Option<bool> {
    match b {
        V::List(l) => Some(l.iter().any(|x| py_eq(a, x))),
        V::Str(s) => match a { V::Str(x) => Some(s.contains(x.as_str())), _ => None },
        V::Dict(d) => match a { V::Str(x) => Some(d.iter().any(|(k, _)| k == x)), V::Int(_) | V::Float(_) | V::Bool(_) | V::None | V::Nil(_) => Some(false), _ => None },
        _ => None,
    }
}

fn vec_of(v: &V) -> Result<Vec<f64>, Err> {
    match v {
        V::List(l) => {
            if l.len() > VEC_MAX { return Err("a vector is a flat list of numbers".into()); }
            l.iter().map(|x| match x {
                V::List(_) | V::Dict(_) => Err("a vector is a flat list of numbers".to_string()),
                _ => numf(x).ok_or_else(|| "could not convert to float".to_string()),
            }).collect()
        }
        V::Int(_) | V::Float(_) | V::Bool(_) => Err("a vector is a flat list of numbers".into()),
        _ => Err("could not convert to float".into()),
    }
}

fn num(v: &V) -> Result<&V, Err> {
    match v { V::Int(_) | V::Float(_) => Ok(v), _ => Err(format!("not a number: {:?}", v)) }
}

fn floats(v: Vec<f64>) -> V { V::List(v.into_iter().map(V::Float).collect()) }

fn vec_arith(op: Op, a: &V, b: &V) -> Result<V, Err> {
    let x: Vec<f64> = if let V::List(_) = a { vec_of(a)? } else { vec![numf(num(a)?).unwrap()] };
    let y: Vec<f64> = if let V::List(_) = b { vec_of(b)? } else { vec![numf(num(b)?).unwrap()] };
    let xs = matches!(a, V::List(_));
    let ys = matches!(b, V::List(_));
    let n = if x.len() == y.len() { x.len() } else if x.len() == 1 { y.len() } else if y.len() == 1 { x.len() } else {
        return Err("vector arithmetic: operands could not be broadcast together".into());
    };
    let _ = (xs, ys);
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        let p = if x.len() == 1 { x[0] } else { x[i] };
        let q = if y.len() == 1 { y[0] } else { y[i] };
        let r = match op { Op::Add => p + q, Op::Sub => p - q, Op::Mul => p * q, Op::Div => p / q };
        if !r.is_finite() && p.is_finite() && q.is_finite() { return Err("vector arithmetic: floating point error".into()); }
        // numpy's errstate(all='raise') also raises on underflow: a non-zero result too small for a normal
        // float, or a product/quotient of non-zeros that rounds to zero (Memo's fuzz: `l / -1e308`)
        // (IEEE: tiny AND inexact; a sum of normals is exact, so only * and / of normal operands count)
        let normal = |x: f64| x.abs() >= f64::MIN_POSITIVE && x.is_finite();
        let muldiv = matches!(op, Op::Mul | Op::Div) && normal(p) && normal(q);
        let tiny = muldiv && r != 0.0 && r.abs() < f64::MIN_POSITIVE;
        let lost = muldiv && r == 0.0;
        if tiny || lost { return Err("vector arithmetic: underflow".into()); }
        out.push(r);
    }
    Ok(floats(out))
}

pub fn arith(op: Op, a: &V, b: &V) -> Result<V, Err> {
    if let Some(e) = first_error(&[a, b]) { return Ok(e); }
    if let Some(n) = null_of(&[a, b], "arithmetic") { return Ok(n); }
    if matches!(a, V::List(_)) || matches!(b, V::List(_)) { return vec_arith(op, a, b); }
    if let (V::Str(x), V::Str(y), Op::Add) = (a, b, op) { return Ok(V::Str(format!("{}{}", x, y))); }
    let (a, b) = (num(a)?, num(b)?);
    let r = match (a, b, op) {
        (V::Int(x), V::Int(y), Op::Add) => x.checked_add(*y).map(V::Int).ok_or("arithmetic: overflow")?,
        (V::Int(x), V::Int(y), Op::Sub) => x.checked_sub(*y).map(V::Int).ok_or("arithmetic: overflow")?,
        (V::Int(x), V::Int(y), Op::Mul) => x.checked_mul(*y).map(V::Int).ok_or("arithmetic: overflow")?,
        _ => {
            let (x, y) = (numf(a).unwrap(), numf(b).unwrap());
            if op == Op::Div && y == 0.0 { return Err("arithmetic: division by zero".into()); }
            V::Float(match op { Op::Add => x + y, Op::Sub => x - y, Op::Mul => x * y, Op::Div => x / y })
        }
    };
    if let V::Float(f) = r { if !f.is_finite() { return Err("not a finite number".into()); } }
    Ok(r)
}

fn norm(v: &[f64]) -> f64 { v.iter().map(|x| x * x).sum::<f64>().sqrt() }

pub fn dist(a: &[f64], b: &[f64]) -> f64 {
    let (na, nb) = (norm(a), norm(b));
    if a.len() != b.len() || na < 1e-12 || nb < 1e-12 { return 1.0; }
    1.0 - a.iter().zip(b).map(|(x, y)| x * y).sum::<f64>() / (na * nb)
}

fn extreme(args: &[V], want: std::cmp::Ordering) -> Result<V, Err> {
    let items: Vec<V> = if args.len() == 1 {
        match &args[0] { V::List(l) => l.clone(), V::Str(s) => s.chars().map(|c| V::Str(c.to_string())).collect(), V::Dict(d) => d.iter().map(|(k, _)| V::Str(k.clone())).collect(), _ => return Err("object is not iterable".into()) }
    } else { args.to_vec() };
    if items.is_empty() { return Err("arg is an empty sequence".into()); }
    let mut best = items[0].clone();
    for x in &items[1..] {
        match py_cmp(x, &best) { Some(o) if o == want => best = x.clone(), Some(_) => {}, None => return Err("'<' not supported".into()) }
    }
    Ok(best)
}

fn call(f: &str, a: Vec<V>, w: &dyn World) -> Result<V, Err> {
    let argn = |n: usize| -> Result<(), Err> { if a.len() == n { Ok(()) } else { Err(format!("{}: takes {} arguments", f, n)) } };
    match f {
        // a cast: Int(x) is x when x is an int, else None('not-a-type: int expected, got text'); a null stays the null it was
        "Int" | "Float" | "Num" | "Text" | "Bool" | "List" | "Dict" => {
            argn(1)?;
            let v = &a[0];
            if nullish(v) { return Ok(v.clone()); }
            let ok = match (f, v) {
                ("Int", V::Int(_)) | ("Float", V::Float(_)) | ("Num", V::Int(_) | V::Float(_)) | ("Text", V::Str(_)) | ("Bool", V::Bool(_))
                | ("List", V::List(_)) | ("Dict", V::Dict(_)) => true,
                _ => false,
            };
            Ok(if ok { v.clone() } else { V::Error(format!("not-a-type: {} expected, got {}", f.to_lowercase(), kind_of(v))) })
        }
        // Error('reason') makes a failure: the remedy is part of the text (docs/c1f-error.md)
        "Error" => { argn(1)?; Ok(V::Error(py_str(&a[0]))) }
        // None is a class like any other: None('why') constructs a null that carries its reason (None() says "null")
        "None" => Ok(V::Nil(match a.len() { 0 => "null".into(), 1 => py_str(&a[0]), _ => return Err("None: takes one reason".into()) })),
        // why(x): the reason a null carries as text (`absent ...`, `error: ...`, `pending: ...`, the text of None('...')); None when x is not a reasoned null
        "why" => { argn(1)?; Ok(match &a[0] { V::Nil(r) | V::Error(r) => V::Str(r.clone()), _ => V::None }) }
        "dot" => { argn(2)?; let (x, y) = (vec_of(&a[0])?, vec_of(&a[1])?); if x.len() != y.len() { return Err("dot: shapes not aligned".into()); } Ok(V::Float(x.iter().zip(&y).map(|(p, q)| p * q).sum())) }
        "norm" => { argn(1)?; Ok(V::Float(norm(&vec_of(&a[0])?))) }
        "distance" => { argn(2)?; Ok(V::Float(dist(&vec_of(&a[0])?, &vec_of(&a[1])?))) }
        "unit" => { argn(1)?; let x = vec_of(&a[0])?; let n = norm(&x); Ok(floats(if n > 1e-12 { x.iter().map(|v| v / n).collect() } else { x })) }
        "abs" => { argn(1)?; match &a[0] { V::Int(i) => Ok(V::Int(i.abs())), V::Float(x) => Ok(V::Float(x.abs())), V::Bool(b) => Ok(V::Int(*b as i64)), _ => Err("bad operand type for abs()".into()) } }
        "min" => { if a.is_empty() { return Err("min expected 1 argument".into()); } extreme(&a, std::cmp::Ordering::Less) }
        "max" => { if a.is_empty() { return Err("max expected 1 argument".into()); } extreme(&a, std::cmp::Ordering::Greater) }
        "len" => { argn(1)?; match &a[0] { V::List(l) => Ok(V::Int(l.len() as i64)), V::Str(t) => Ok(V::Int(t.chars().count() as i64)), V::Dict(d) => Ok(V::Int(d.len() as i64)), V::None | V::Nil(_) => Ok(V::Int(0)), v => Err(format!("object of type {:?} has no len()", v)) } }
        // ---- text (the banner port; plain functions, no new syntax) ----
        "lower" => { argn(1)?; Ok(V::Str(text_of(&a[0])?.to_lowercase())) }
        "strip" => { argn(1)?; Ok(V::Str(text_of(&a[0])?.trim().to_string())) }
        "replace" => {
            argn(3)?;
            let (t, from, to) = (text_of(&a[0])?, text_of(&a[1])?, text_of(&a[2])?);
            Ok(V::Str(if from.is_empty() { t } else { t.replace(&from, &to) }))
        }
        "startswith" => { argn(2)?; Ok(V::Bool(text_of(&a[0])?.starts_with(&text_of(&a[1])?))) }
        "lines" => { argn(1)?; Ok(V::List(text_of(&a[0])?.lines().map(|l| V::Str(l.to_string())).collect())) }
        "words" => { argn(1)?; Ok(V::List(text_of(&a[0])?.split_whitespace().map(|w| V::Str(w.to_string())).collect())) }
        "spans" => {      // the tokenizer (#1143): [{start, end, text}] of a layer, offsets in chars (docs/semtext-and-legs.md)
            argn(2)?;
            let layer = text_of(&a[1])?;
            let l = crate::text::Layer::parse(&layer).ok_or_else(|| format!("spans(text, layer): the layer is one of {} (not {:?})", crate::text::Layer::NAMES, layer))?;
            Ok(V::List(crate::text::surfaces(&text_of(&a[0])?, l).into_iter().map(|(s, t)| V::Dict(vec![
                ("start".to_string(), V::Int(s.start as i64)), ("end".to_string(), V::Int(s.end as i64)), ("text".to_string(), V::Str(t))])).collect()))
        }
        "join" => {
            argn(2)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None | V::Nil(_) => &none, _ => return Err("join(list, sep): a list".into()) };  // #1065: a pending read (None) is empty
            Ok(V::Str(l.iter().map(py_str).collect::<Vec<_>>().join(&text_of(&a[1])?)))
        }
        "head" => {
            argn(2)?;
            let n = match &a[1] { V::Int(n) => (*n).max(0) as usize, V::Float(f) => f.max(0.0) as usize, _ => return Err("head(text, n): n is a number".into()) };
            if let V::List(l) = &a[0] { return Ok(V::List(l.iter().take(n).cloned().collect())); }   // a list's first n
            Ok(V::Str(text_of(&a[0])?.chars().take(n).collect()))
        }
        "tail" => {      // the mirror of head: a text's last n characters, a list's last n
            argn(2)?;
            let n = match &a[1] { V::Int(n) => (*n).max(0) as usize, V::Float(f) => f.max(0.0) as usize, _ => return Err("tail(text, n): n is a number".into()) };
            if let V::List(l) = &a[0] { return Ok(V::List(l[l.len().saturating_sub(n)..].to_vec())); }
            let t: Vec<char> = text_of(&a[0])?.chars().collect();
            Ok(V::Str(t[t.len().saturating_sub(n)..].iter().collect()))
        }
        // ---- lists ----
        "sort_by" => {
            if a.len() < 2 || a.len() > 3 { return Err("sort_by(rows, key[, desc]): 2 or 3 arguments".into()); }
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None | V::Nil(_) => &none, _ => return Err("sort_by(rows, key, desc): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let key = text_of(&a[1])?;
            let desc = a.get(2).map(truthy).unwrap_or(false);
            let field = |v: &V| -> V { match v { V::Dict(d) => d.iter().find(|(k, _)| *k == key).map(|(_, x)| x.clone()).unwrap_or(V::None), _ => V::None } };
            let mut out = l.clone();
            // None first going up, last going down; ties keep their order
            out.sort_by(|x, y| {
                let (p, q) = (field(x), field(y));
                let o = match (&p, &q) {
                    (a, b) if nullish(a) && nullish(b) => std::cmp::Ordering::Equal,
                    (a, _) if nullish(a) => std::cmp::Ordering::Less,
                    (_, b) if nullish(b) => std::cmp::Ordering::Greater,
                    _ => py_cmp(&p, &q).unwrap_or(std::cmp::Ordering::Equal),
                };
                if desc { o.reverse() } else { o }
            });
            Ok(V::List(out))
        }
        "unspeak" => {
            // speakers.strip_speaker_lines, rule for rule: leading whitespace, a speaker tag -- "[user: X]",
            // "[HA user: X]" (X: 1..80 characters, no ']' or newline) or "[user#<id>: X]" (id: [a-z0-9][a-z0-9-]{0,79})
            // -- then spaces or tabs and one optional newline; again while one leads
            argn(1)?;
            let mut t = text_of(&a[0])?;
            loop {
                let s = t.trim_start();
                let Some(rest) = s.strip_prefix('[') else { return Ok(V::Str(t)) };
                let Some(close) = rest.find(']') else { return Ok(V::Str(t)) };
                let inner = &rest[..close];
                if inner.contains('\n') { return Ok(V::Str(t)); }
                let ok = match inner.split_once(": ") {
                    Some((who, name)) => !name.is_empty() && name.chars().count() <= 80 && (who == "user" || who == "HA user"
                        || who.strip_prefix("user#").map_or(false, |id| {
                            let mut cs = id.chars();
                            matches!(cs.next(), Some(c) if c.is_ascii_lowercase() || c.is_ascii_digit()) && id.chars().count() <= 80
                                && id.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
                        })),
                    None => false,
                };
                if !ok { return Ok(V::Str(t)); }
                let after = rest[close + 1..].trim_start_matches([' ', '\t']);
                t = after.strip_prefix('\n').unwrap_or(after).to_string();
            }
        }
        "before" => { argn(2)?; let (t, sep) = (text_of(&a[0])?, text_of(&a[1])?); Ok(V::Str(t.split_once(sep.as_str()).map(|(x, _)| x.to_string()).unwrap_or(t))) }
        "after" => { argn(2)?; let (t, sep) = (text_of(&a[0])?, text_of(&a[1])?); Ok(V::Str(t.split_once(sep.as_str()).map(|(_, y)| y.to_string()).unwrap_or_default())) }
        "pluck" => {
            // a column of rows: [row[key] for row in rows]
            argn(2)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None | V::Nil(_) => &none, _ => return Err("pluck(rows, key): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let key = text_of(&a[1])?;
            Ok(V::List(l.iter().map(|r| match r { V::Dict(d) => d.iter().find(|(x, _)| *x == key).map(|(_, y)| y.clone()).unwrap_or(V::None), _ => V::None }).collect()))
        }
        "distinct" => {
            argn(1)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None | V::Nil(_) => &none, _ => return Err("distinct(list): a list".into()) };  // #1065: a pending read (None) is empty
            let mut out: Vec<V> = vec![];
            for x in l { if !out.iter().any(|y| py_eq(y, x)) { out.push(x.clone()); } }
            Ok(V::List(out))
        }
        "group_by" => {
            // rows grouped by a key, in first-seen order: [{key, rows}]
            argn(2)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None | V::Nil(_) => &none, _ => return Err("group_by(rows, key): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let key = text_of(&a[1])?;
            let mut groups: Vec<(V, Vec<V>)> = vec![];
            let mut by_text: HashMap<String, usize> = HashMap::new();   // a text key finds its group at once (a text equals only a text); any other key scans the groups
            for r in l {
                let k = match r { V::Dict(d) => d.iter().find(|(x, _)| *x == key).map(|(_, y)| y.clone()).unwrap_or(V::None), _ => V::None };
                if let V::Str(t) = &k {
                    match by_text.get(t) { Some(&i) => groups[i].1.push(r.clone()), None => { by_text.insert(t.clone(), groups.len()); groups.push((k, vec![r.clone()])); } }
                    continue;
                }
                match groups.iter_mut().find(|(g, _)| !matches!(g, V::Str(_)) && py_eq(g, &k)) { Some(g) => g.1.push(r.clone()), None => groups.push((k, vec![r.clone()])) }
            }
            Ok(V::List(groups.into_iter().map(|(k, rows)| V::Dict(vec![("key".into(), k), ("rows".into(), V::List(rows))])).collect()))
        }
        "concat" => {
            // lists joined end to end (`+` on lists is vector addition: lists are vectors)
            let mut out = vec![];
            for x in a.iter() { match x { V::List(l) => out.extend(l.iter().cloned()), V::None | V::Nil(_) => {}, _ => return Err("concat(list, ...): lists".into()) } }
            Ok(V::List(out))
        }
        "fit" => {
            // the banner's budget (conv_banner.render): rows {text, prio, slot}; lower prio kept first, each line
            // costs len(text) + 1, a line that does not fit is skipped (a later shorter one may); page order = slot
            if a.len() < 2 || a.len() > 3 { return Err("fit(rows, budget[, used]): 2 or 3 arguments".into()); }
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None | V::Nil(_) => &none, _ => return Err("fit(rows, budget): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let budget = match &a[1] { V::Int(n) => *n as f64, V::Float(f) => *f, _ => return Err("fit(rows, budget): budget is a number".into()) };
            let get = |v: &V, k: &str| -> V { match v { V::Dict(d) => d.iter().find(|(x, _)| x == k).map(|(_, y)| y.clone()).unwrap_or(V::None), _ => V::None } };
            let num = |v: V| -> f64 { match v { V::Int(n) => n as f64, V::Float(f) => f, _ => 0.0 } };
            // optional {head, head_slot}: a group's heading (DECIDED:), costed and shown once, with its first kept line
            let mut rows: Vec<(f64, f64, String, String, f64)> = l.iter().map(|r| (num(get(r, "prio")), num(get(r, "slot")), py_str(&get(r, "text")),
                match get(r, "head") { V::Str(h) => h, _ => String::new() }, num(get(r, "head_slot")))).collect();
            rows.sort_by(|x, y| (x.0, x.1).partial_cmp(&(y.0, y.1)).unwrap_or(std::cmp::Ordering::Equal));
            let mut used = match a.get(2) { Some(V::Int(n)) => *n as f64, Some(V::Float(f)) => *f, _ => 0.0 };
            let mut kept: Vec<(f64, String)> = vec![];
            let mut heads: std::collections::HashSet<String> = Default::default();
            for (_, slot, t, head, hslot) in rows {
                let mut cost = t.chars().count() as f64 + 1.0;
                let new_head = !head.is_empty() && !heads.contains(&head);
                if new_head { cost += head.chars().count() as f64 + 1.0; }
                if used + cost > budget { continue; }
                used += cost;
                if new_head { heads.insert(head.clone()); kept.push((hslot, head)); }
                kept.push((slot, t));
            }
            kept.sort_by(|x, y| x.0.partial_cmp(&y.0).unwrap_or(std::cmp::Ordering::Equal));
            Ok(V::List(kept.into_iter().map(|(_, t)| V::Str(t)).collect()))
        }
        "clip" => {
            // conv_banner._clip: whitespace collapsed; longer than n: cut to n - 3, stripped, then "..."
            argn(2)?;
            let n = match &a[1] { V::Int(n) => (*n).max(1) as usize, V::Float(f) => f.max(1.0) as usize, _ => return Err("clip(text, n): n is a number".into()) };
            let t = text_of(&a[0])?.split_whitespace().collect::<Vec<_>>().join(" ");
            if t.chars().count() <= n { return Ok(V::Str(t)); }
            let cut: String = t.chars().take(n.saturating_sub(3).max(1)).collect();
            Ok(V::Str(format!("{}...", cut.trim_end())))
        }
        "ago" => {
            // conv_banner.age: a record's age in the operator's buckets ('' with no time)
            argn(2)?;
            let num = |v: &V| -> Option<f64> { match v { V::Int(n) => Some(*n as f64), V::Float(f) => Some(*f), V::Str(s) => s.parse().ok(), _ => None } };
            let (Some(ts), Some(now)) = (num(&a[0]).or_else(|| epoch_of(&a[0])), num(&a[1])) else { return Ok(V::Str(String::new())) };
            Ok(V::Str(crate::clock::ago(ts, now)))
        }
        // plain(text): the text a quote is compared in (convgraph._norm): lowercase; letters, digits and _ # . / : - stay,
        // everything else is a space; runs of spaces collapse
        "plain" => {
            argn(1)?;
            let t = match &a[0] { V::None | V::Nil(_) => String::new(), v => text_of(v)? };
            let mapped: String = t.to_lowercase().chars().map(|c| if c.is_alphanumeric() || "_#./:-".contains(c) { c } else { ' ' }).collect();
            Ok(V::Str(mapped.split_whitespace().collect::<Vec<_>>().join(" ")))
        }
        // num(x): a number from a number or a numeric text (whole -> int, else float); anything else, a bool, nan, inf: none
        "num" => {
            argn(1)?;
            Ok(match &a[0] {
                V::Int(i) => V::Int(*i),
                V::Float(f) if f.is_finite() => V::Float(*f),
                V::Str(t) => {
                    let t = t.trim();
                    if let Ok(i) = t.parse::<i64>() { V::Int(i) }
                    else { match t.parse::<f64>() { Ok(f) if f.is_finite() => V::Float(f), _ => V::None } }
                }
                _ => V::None,
            })
        }
        "epoch" => { argn(1)?; Ok(match &a[0] { V::Int(n) => V::Float(*n as f64), V::Float(f) => V::Float(*f), v => epoch_of(v).map(V::Float).unwrap_or(V::None) }) }
        // ---- time ----
        "localtime" => { argn(1)?; let ts = match &a[0] { V::Int(n) => *n as f64, V::Float(f) => *f, _ => return Err("localtime(ts): a number".into()) }; Ok(crate::clock::local(ts)) }
        "json" => { argn(1)?; match &a[0] { V::Str(t) => serde_json::from_str::<serde_json::Value>(t).map(|j| from_json(&j)).or_else(|_| Ok::<V, String>(V::None)), V::None | V::Nil(_) => Ok(V::None), _ => Err("json(text): a string".into()) } }
        "str" => { argn(1)?; Ok(V::Str(match &a[0] { V::Str(t) => t.clone(), V::List(_) | V::Dict(_) => to_json_shown(&a[0])?.to_string(), v => py_str(v) })) }
        "referencing" => {
            argn(2)?;
            let e = match &a[0] { V::Ent(e) | V::Str(e) => e.clone(), _ => return Ok(V::List(vec![])) };
            Ok(V::List(w.refs(&e, &py_str(&a[1])).into_iter().map(V::Ent).collect()))
        }
        "prop" => {
            argn(2)?;
            match &a[0] {
                V::Ent(e) => { let name = py_str(&a[1]); if name.starts_with('_') { return Err(format!("no attribute {:?}", name)); } Ok(w.attr(e, &name)) }
                V::Dict(d) => { let name = py_str(&a[1]); Ok(d.iter().find(|(k, _)| *k == name).map(|(_, v)| v.clone()).unwrap_or(V::None)) }
                // an entity's id (a reference stored as a string): its field, read like `x.name`
                V::Str(id) => { let name = py_str(&a[1]); if name.starts_with('_') { return Err(format!("no attribute {:?}", name)); } Ok(w.attr(id, &name)) }
                _ => Ok(V::None),
            }
        }
        _ => match w.host(f, &a) { Some(r) => r, None => Err("only the built-ins may be called".into()) },
    }
}

/// "YYYY-MM-DDTHH:MM:SSZ" (UTC, as the ledgers write it) -> seconds since 1970, or a number as it is.
fn epoch_of(v: &V) -> Option<f64> {
    let s = match v { V::Str(s) => s.trim().to_string(), V::Int(n) => return Some(*n as f64), V::Float(f) => return Some(*f), _ => return None };
    if let Ok(x) = s.parse::<f64>() { return Some(x); }
    let b = s.as_bytes();
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' || (b[10] != b'T' && b[10] != b' ') { return None; }
    let n = |r: std::ops::Range<usize>| s.get(r).and_then(|x| x.parse::<i64>().ok());
    let (y, mo, d, h, mi, se) = (n(0..4)?, n(5..7)?, n(8..10)?, n(11..13)?, n(14..16)?, n(17..19)?);
    let (y2, m2) = if mo <= 2 { (y - 1, mo + 12) } else { (y, mo) };
    let days = 365 * y2 + y2 / 4 - y2 / 100 + y2 / 400 + (153 * (m2 - 3) + 2) / 5 + d - 719469;
    Some((days * 86400 + h * 3600 + mi * 60 + se) as f64)
}

fn text_of(v: &V) -> Result<String, Err> {
    match v { V::Str(s) => Ok(s.clone()), V::None | V::Nil(_) => Ok(String::new()), V::Error(r) => Err(r.clone()), V::Ent(e) => Ok(e.clone()), V::List(_) | V::Dict(_) => Err("expected text".into()), v => Ok(py_str(v)) }
}

pub fn py_str(v: &V) -> String {
    match v {
        V::None | V::Nil(_) => "None".into(), V::Error(r) => format!("Error: {r}"), V::Bool(b) => if *b { "True".into() } else { "False".into() },
        V::Int(i) => i.to_string(), V::Float(f) => format!("{:?}", f), V::Str(s) => s.clone(),
        V::Ent(e) => e.clone(), _ => format!("{:?}", v),
    }
}

/// The pronouns a bare name can mean (#1101): with no variable of that name, one resolves through the host's context
/// stack (turn frame -> entered frames -> chat -> person) to the entity (or entities, for `they`) it stands for.
pub const PRONOUNS: [&str; 9] = ["it", "that", "they", "them", "these", "those", "he", "him", "she"];

fn pronoun(name: &str, w: &dyn World) -> Result<V, Err> {
    if !PRONOUNS.contains(&name) && name != "her" { return Err(format!("unbound name {:?}", name)); }
    let ent = |v: V| match v { V::Str(e) => V::Ent(e), o => o };
    match w.host("pronoun", &[V::Str(name.to_string())]) {
        Some(Ok(V::List(l))) if !l.is_empty() => Ok(V::List(l.into_iter().map(ent).collect())),
        Some(Ok(V::Str(e))) if !e.is_empty() => Ok(V::Ent(e)),
        Some(Err(e)) => Err(e),
        _ => Err(format!("the pronoun {:?} is not bound here (nothing in the turn, the entered frames, the chat or the person is it)", name)),
    }
}

/// `v is <name>`: a value kind by its spelling, else a class of an entity.
fn is_kind(v: &V, name: &str, w: &dyn World) -> bool {
    match (name, v) {
        ("Error", V::Error(_)) | ("Int", V::Int(_)) | ("Float", V::Float(_)) | ("Text", V::Str(_)) | ("Bool", V::Bool(_)) | ("List", V::List(_)) | ("Dict", V::Dict(_)) => true,
        (n, _) if VALUE_KINDS.contains(&n) => false,
        (cls, V::Ent(e)) => w.is_class(e, cls),
        _ => false,
    }
}

fn kind_of(v: &V) -> &'static str {
    match v { V::None | V::Nil(_) => "none", V::Error(_) => "error", V::Bool(_) => "bool", V::Int(_) => "int", V::Float(_) => "float", V::Str(_) => "text",
              V::List(_) => "list", V::Dict(_) => "dict", V::Ent(_) => "entity" }
}

/// Evaluate within the step budget (`Budget`): over it, the error is `budget exceeded (N steps)`; over the self-time allowance,
/// `timeout N ms (allowance M ms)`.
pub fn eval(n: &Node, env: &HashMap<String, V>, w: &dyn World) -> Result<V, Err> {
    let mut m = Meter { b: Budget::new(), depth: 0, locals: None };
    let r = ev(n, &Env::root(env), w, &mut m);
    crate::stats::add_steps(m.b.used());
    r.and_then(|v| m.b.check_time().map(|_| v))
}

/// `eval`, and the locals the block bound while it ran (in order): what `Why` prints.
pub fn eval_locals(n: &Node, env: &HashMap<String, V>, w: &dyn World) -> (Result<V, Err>, Vec<(String, V)>) {
    let mut m = Meter { b: Budget::new(), depth: 0, locals: Some(vec![]) };
    let r = ev(n, &Env::root(env), w, &mut m);
    crate::stats::add_steps(m.b.used());
    let r = r.and_then(|v| m.b.check_time().map(|_| v));
    (r, m.locals.unwrap_or_default())
}

/// The names an evaluation sees: the caller's map, then the scopes a `let` or a comprehension opens over it (innermost last). A scope holds only ITS OWN bindings:
/// opening one copies nothing of the enclosing ones.
pub struct Env<'a> { root: Option<&'a HashMap<String, V>>, parent: Option<&'a Env<'a>>, vars: Vec<(String, V)> }
impl<'a> Env<'a> {
    fn root(m: &'a HashMap<String, V>) -> Env<'a> { Env { root: Some(m), parent: None, vars: vec![] } }
    fn child(p: &'a Env<'a>) -> Env<'a> { Env { root: None, parent: Some(p), vars: vec![] } }
    fn get(&self, name: &str) -> Option<&V> {
        if let Some((_, v)) = self.vars.iter().rev().find(|(k, _)| k == name) { return Some(v); }
        match (self.parent, self.root) { (Some(p), _) => p.get(name), (None, Some(r)) => r.get(name), _ => None }
    }
}

fn ev(n: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    m.b.charge(1)?;
    if m.depth >= EVAL_DEPTH_MAX { return Err(format!("nested more than {} deep", EVAL_DEPTH_MAX)); }
    m.depth += 1;
    let r = ev_node(n, env, w, m);
    m.depth -= 1;
    r
}

fn ev_node(n: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    // each arm with real work is its own function: this frame stays small, so a deep expression costs little stack
    match n {
        Node::Const(v) => Ok(v.clone()),
        Node::Name(s) => match env.get(s) { Some(v) => Ok(v.clone()), None => pronoun(s, w) },
        Node::List(_) | Node::Dict(_) | Node::And(_) | Node::Or(_) | Node::Not(_) | Node::If(..) | Node::Is(..) => ev_seq(n, env, w, m),
        Node::Attr(b, a) => ev_attr(b, a, env, w, m),
        Node::Index(b, i) => ev_index(b, i, env, w, m),
        Node::Bin(..) => ev_bin(n, env, w, m),
        Node::Let(..) => ev_let(n, env, w, m),
        Node::Neg(a) => ev_neg(a, env, w, m),
        Node::Compare(a, ops) => ev_compare(a, ops, env, w, m),
        Node::Comp(elt, var, iter, cond) => ev_comp(elt, var, iter, cond, env, w, m),
        Node::Call(f, args) => ev_call(f, args, env, w, m),
        Node::Ent(parts) => ev_ent(parts, env, w, m),
        Node::Ctor(c, _) => Err(format!("a constructor creates the entity on the left of a write: write `<entity> = {}(name: value)`", c)),
    }
}

#[inline(never)]
fn ev_seq(n: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    match n {
        Node::List(v) => Ok(V::List(v.iter().map(|x| ev(x, env, w, m)).collect::<Result<_, _>>()?)),
        Node::Dict(v) => Ok(V::Dict(v.iter().map(|(k, x)| Ok((k.clone(), ev(x, env, w, m)?))).collect::<Result<_, Err>>()?)),
        Node::Not(a) => { let v = ev(a, env, w, m)?; if let V::Error(_) = v { return Ok(v); } Ok(V::Bool(!truthy(&v))) }
        // an Error operand is the answer (a default or a short-circuit must not hide a failure)
        Node::And(v) => { let mut last = V::Bool(true); for x in v { last = ev(x, env, w, m)?; if matches!(last, V::Error(_)) || !truthy(&last) { return Ok(last); } } Ok(last) }
        Node::Or(v) => { let mut last = V::Bool(false); for x in v { last = ev(x, env, w, m)?; if matches!(last, V::Error(_)) || truthy(&last) { return Ok(last); } } Ok(last) }
        // a condition over an Error is that Error: neither arm can be chosen (an `else` would silently hide it)
        Node::If(a, c, b) => { let cv = ev(c, env, w, m)?; if let V::Error(_) = cv { return Ok(cv); } if truthy(&cv) { ev(a, env, w, m) } else { ev(b, env, w, m) } }
        Node::Is(a, names, neg) => { let v = ev(a, env, w, m)?; Ok(V::Bool(names.iter().all(|n| is_kind(&v, n, w)) != *neg)) }
        _ => unreachable!(),
    }
}

#[inline(never)]
fn ev_attr(b: &Node, a: &String, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    match ev(b, env, w, m)? {
        V::Ent(e) => { if a.starts_with('_') { return Err(format!("no attribute {:?}", a)); } Ok(w.attr(&e, a)) }
        V::Nil(r) => Ok(V::Nil(r)),
        e @ V::Error(_) => Ok(e),
        V::None => Ok(V::Nil(format!("absent: .{} of an absent value", a))),
        _ => Err("attributes are read on entities only".into()),
    }
}

/// a long `a + b + c ...` is a deep left spine: walk it as a loop, not by recursion
#[inline(never)]
fn ev_bin(n: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    let mut ops: Vec<(Op, &Node)> = vec![];
    let mut cur = n;
    while let Node::Bin(op, a, b) = cur { ops.push((*op, &**b)); cur = &**a; }
    let mut acc = ev(cur, env, w, m)?;
    for (op, b) in ops.into_iter().rev() {
        m.b.charge(1)?;
        let y = ev(b, env, w, m)?;
        acc = arith(op, &acc, &y)?;
    }
    Ok(acc)
}

#[inline(never)]
fn ev_let(n: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    let mut env2 = Env::child(env);
    let mut cur = n;
    while let Node::Let(name, val, rest) = cur {
        let v = ev(val, &env2, w, m)?;
        if let Some(l) = m.locals.as_mut() { l.push((name.clone(), v.clone())); }
        env2.vars.push((name.clone(), v));
        cur = &**rest;
    }
    ev(cur, &env2, w, m)
}

/// An entity literal's id: its text, each `[key]` appended as `:<key>`. A key is an integer or text; a null key is the null it was
/// (its reason kept), any other kind is a reasoned null (`error: ...`): the entity is not named, so nothing is created.
#[inline(never)]
fn ev_ent(parts: &[EntPart], env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    let mut id = String::new();
    for p in parts {
        match p {
            EntPart::Lit(t) => id.push_str(t),
            EntPart::Key(n) => match ev(n, env, w, m)? {
                V::Int(i) => { id.push(':'); id.push_str(&i.to_string()); }
                V::Str(t) if !t.is_empty() => { id.push(':'); id.push_str(&t); }
                V::Nil(r) => return Ok(V::Nil(r)),
                e @ V::Error(_) => return Ok(e),
                V::None => return Ok(V::Error(format!("the key of {} is null", id))),
                V::Str(_) => return Ok(V::Error(format!("the key of {} is empty text", id))),
                v => return Ok(V::Error(format!("the key of {} is an integer or text, got a {}", id, kind_of(&v)))),
            },
        }
    }
    Ok(V::Ent(id))
}

#[inline(never)]
fn ev_neg(a: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
    match ev(a, env, w, m)? {
        V::List(l) => Ok(floats(vec_of(&V::List(l))?.into_iter().map(|x| -x).collect())),
        V::Int(i) => Ok(V::Int(-i)),
        V::Float(f) => Ok(V::Float(-f)),
        e @ V::Error(_) => Ok(e),
        v => if let Some(n) = null_of(&[&v], "negation") { Ok(n) } else { Err(format!("not a number: {:?}", v)) },
    }
}

#[inline(never)]
fn ev_index(b: &Node, i: &Node, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
            let base = ev(b, env, w, m)?;
            let idx = ev(i, env, w, m)?;
            match (base, idx) {
                (e @ V::Error(_), _) | (_, e @ V::Error(_)) => Ok(e),
                (V::Nil(r), _) => Ok(V::Nil(r)),
                (_, V::Nil(r)) => Ok(V::Nil(r)),
                (V::None, _) => Ok(V::None),
                (V::List(l), V::Int(k)) => {
                    let n = l.len() as i64;
                    let at = if k < 0 { k + n } else { k };
                    Ok(if at >= 0 && at < n { l[at as usize].clone() } else { V::None })
                }
                (V::Dict(d), V::Str(k)) => Ok(d.iter().find(|(x, _)| *x == k).map(|(_, v)| v.clone()).unwrap_or(V::None)),
                (V::Ent(e), V::Str(k)) => { if k.starts_with('_') { return Err(format!("no attribute {:?}", k)); } Ok(w.attr(&e, &k)) }
                (b, i) => Err(format!("cannot index a {} by a {}", kind_of(&b), kind_of(&i))),
            }
        }

#[inline(never)]
fn ev_compare(a: &Node, ops: &[(Cmp, Node)], env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
            let mut left = ev(a, env, w, m)?;
            for (op, rn) in ops {
                let right = ev(rn, env, w, m)?;
                // `x == None` / `x != None` is the one comparison that reads an Error: it is not a null (False / True)
                let null_test = matches!(op, Cmp::Eq | Cmp::Ne) && matches!((&left, &right), (V::Error(_), V::None | V::Nil(_)) | (V::None | V::Nil(_), V::Error(_)));
                if !null_test { if let Some(e) = first_error(&[&left, &right]) { return Ok(e); } }
                let ok = match op {
                    Cmp::Eq => Some(py_eq(&left, &right)),
                    Cmp::Ne => Some(!py_eq(&left, &right)),
                    Cmp::Lt => py_cmp(&left, &right).map(|o| o.is_lt()),
                    Cmp::Le => py_cmp(&left, &right).map(|o| o.is_le()),
                    Cmp::Gt => py_cmp(&left, &right).map(|o| o.is_gt()),
                    Cmp::Ge => py_cmp(&left, &right).map(|o| o.is_ge()),
                    Cmp::In => contains(&left, &right),
                    Cmp::NotIn => contains(&left, &right).map(|x| !x),
                };
                if ok != Some(true) { return Ok(V::Bool(false)); }
                left = right;
            }
            Ok(V::Bool(true))
        }

#[inline(never)]
fn ev_comp(elt: &Node, var: &String, iter: &Node, cond: &Option<Box<Node>>, env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
            let xs = match ev(iter, env, w, m)? { V::List(l) => l, V::None | V::Nil(_) => vec![], e @ V::Error(_) => return Ok(e), v => return Err(format!("can't iterate over {:?}", v)) };
            let mut out = Vec::new();
            let mut env2 = Env::child(env);   // a scope over the enclosing one: the enclosing bindings (a local holding a long list) are looked up, never copied per iteration
            env2.vars.push((var.clone(), V::None));
            for x in xs {
                env2.vars[0].1 = x;
                if let Some(c) = cond { let cv = ev(c, &env2, w, m)?; if let V::Error(_) = cv { return Ok(cv); } if !truthy(&cv) { continue; } }
                out.push(ev(elt, &env2, w, m)?);
            }
            Ok(V::List(out))
        }

#[inline(never)]
fn ev_call(f: &String, args: &[Node], env: &Env, w: &dyn World, m: &mut Meter) -> Result<V, Err> {
            let a = args.iter().map(|x| ev(x, env, w, m)).collect::<Result<Vec<_>, _>>()?;
            // a call over an Error is that Error (`why` reads it)
            if f != "why" { if let Some(e) = first_error(&a.iter().collect::<Vec<_>>()) { return Ok(e); } }
            let any_null = a.iter().any(nullish);
            let r = call(f, a.clone(), w);
            match r {
                Err(_) if any_null => Ok(null_of(&a.iter().collect::<Vec<_>>(), f).unwrap_or(V::Nil(format!("error: {f} of a null")))),   // an error caused by a null is a null
                other => other.map_err(|e| if e.starts_with(f.as_str()) || e.contains("built-ins") { e } else { format!("{}: {}", f, e) }),
            }
        }

// ---- JSON <-> values -----------------------------------------------------------------------------

pub fn from_json(j: &serde_json::Value) -> V {
    use serde_json::Value as J;
    match j {
        J::Null => V::None,
        J::Bool(b) => V::Bool(*b),
        J::Number(n) => if let Some(i) = n.as_i64() { V::Int(i) } else { V::Float(n.as_f64().unwrap_or(f64::NAN)) },
        J::String(s) => V::Str(s.clone()),
        J::Array(a) => V::List(a.iter().map(from_json).collect()),
        // an entity stored by a getter is a reference: {"$ref": "<eid>"} reads back as the entity
        J::Object(o) if o.len() == 1 && o.get("$ref").map(|r| r.is_string()).unwrap_or(false) =>
            V::Ent(o["$ref"].as_str().unwrap_or_default().to_string()),
        J::Object(o) => V::Dict(o.iter().map(|(k, v)| (k.clone(), from_json(v))).collect()),
    }
}

/// A value as JSON. An entity is kept as a reference, `{"$ref": "<eid>"}` (`from_json` reads it back as the entity).
pub fn to_json(v: &V) -> Result<serde_json::Value, Err> {
    use serde_json::Value as J;
    Ok(match v {
        V::None | V::Nil(_) | V::Error(_) => J::Null,
        V::Bool(b) => J::Bool(*b),
        V::Int(i) => J::from(*i),
        V::Float(f) => serde_json::Number::from_f64(*f).map(J::Number).ok_or("not a finite number")?,
        V::Str(s) => J::String(s.clone()),
        V::List(l) => J::Array(l.iter().map(to_json).collect::<Result<_, _>>()?),
        V::Dict(d) => J::Object(d.iter().map(|(k, x)| Ok((k.clone(), to_json(x)?))).collect::<Result<_, Err>>()?),
        V::Ent(e) => J::Object(std::iter::once(("$ref".to_string(), J::String(e.clone()))).collect()),
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn every_builtin_is_reserved() {
        let src = include_str!("expr.rs");
        let a = src.find("fn call(f: &str").unwrap();
        let body = &src[a..];
        for line in body.lines().take(300) {
            let t = line.trim_start();
            if !t.starts_with('"') || !t.contains("=>") { continue; }
            let head = &t[..t.find("=>").unwrap()];
            for name in head.split('|').map(|p| p.trim().trim_matches('"')) {
                if name.chars().all(|c| c.is_alphanumeric() || c == '_') && !name.is_empty() {
                    assert!(super::RESERVED.contains(&name), "builtin {name:?} is missing from RESERVED");
                }
            }
        }
    }
}
