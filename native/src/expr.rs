//! The cliffhanger expression language (ch.py `_parse` / `_ev`), native.
//!
//! A small safe subset of Python expressions: constants, names, lists/tuples, dicts with literal
//! string keys, `x.attr` on entities, + - * /, unary - and not, and/or, comparisons (== != < <= > >=
//! in, not in), and calls of the built-ins. Semantics follow Python's (truthiness, int vs float, a
//! comparison that would raise TypeError is false, and/or return an operand). Node counts follow
//! `ast.walk` (a Name counts its Load context, a BinOp its operator), so EXPR_MAX / NODES_MAX refuse
//! exactly what ch.py refuses.

use std::collections::HashMap;
use std::sync::Arc as Rc;

pub const EXPR_MAX: usize = 400;
pub const NODES_MAX: usize = 64;
pub const VEC_MAX: usize = 8192;

#[derive(Clone, Debug)]
pub enum V {
    None,
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
}

pub type Err = String;

// ---- tokens -------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
enum Tok { Int(i64), Float(f64), Str(String), Name(String), P(&'static str), End }

fn lex(s: &str) -> Result<Vec<Tok>, Err> {
    let c: Vec<char> = s.chars().collect();
    let mut i = 0;
    let mut out = Vec::new();
    while i < c.len() {
        let ch = c[i];
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
            out.push(Tok::Name(c[st..i].iter().collect()));
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

struct P { t: Vec<Tok>, i: usize, nodes: usize }

const KEYWORDS: &[&str] = &["and", "or", "not", "in", "is", "if", "else", "lambda", "for", "await", "yield"];

impl P {
    fn peek(&self) -> &Tok { &self.t[self.i] }
    fn next(&mut self) -> Tok { let t = self.t[self.i].clone(); if self.i + 1 < self.t.len() { self.i += 1; } t }
    fn is_p(&self, p: &str) -> bool { matches!(self.peek(), Tok::P(x) if *x == p) }
    fn is_kw(&self, k: &str) -> bool { matches!(self.peek(), Tok::Name(x) if x == k) }
    fn expect(&mut self, p: &str) -> Result<(), Err> {
        if self.is_p(p) { self.next(); Ok(()) } else { Err(format!("not an expression: expected {:?}", p)) }
    }
    fn n(&mut self, k: usize) { self.nodes += k; }

    fn expr(&mut self) -> Result<Node, Err> {
        if self.is_kw("lambda") || self.is_kw("await") || self.is_kw("yield") { return Err("not allowed in an expression: Lambda".into()); }
        let e = self.or_test()?;
        if !self.is_kw("if") { return Ok(e); }
        self.next();
        let cond = self.or_test()?;
        if !self.is_kw("else") { return Err("not an expression: `if` needs its `else`".into()); }
        self.next();
        let other = self.expr()?;
        self.n(3);
        Ok(Node::If(Box::new(e), Box::new(cond), Box::new(other)))
    }
    fn or_test(&mut self) -> Result<Node, Err> {
        let a = self.and_test()?;
        if !self.is_kw("or") { return Ok(a); }
        let mut v = vec![a];
        while self.is_kw("or") { self.next(); v.push(self.and_test()?); }
        self.n(2);
        Ok(Node::Or(v))
    }
    fn and_test(&mut self) -> Result<Node, Err> {
        let a = self.not_test()?;
        if !self.is_kw("and") { return Ok(a); }
        let mut v = vec![a];
        while self.is_kw("and") { self.next(); v.push(self.not_test()?); }
        self.n(2);
        Ok(Node::And(v))
    }
    fn not_test(&mut self) -> Result<Node, Err> {
        if self.is_kw("not") { self.next(); let o = self.not_test()?; self.n(2); return Ok(Node::Not(Box::new(o))); }
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
            Tok::Name(x) if x == "is" => return Err("not allowed in an expression: Is".into()),
            _ => return Ok(None),
        };
        self.next();
        Ok(Some(c))
    }
    fn comparison(&mut self) -> Result<Node, Err> {
        let a = self.arith()?;
        let mut ops = Vec::new();
        while let Some(c) = self.comp_op()? { let b = self.arith()?; self.n(1); ops.push((c, b)); }
        if ops.is_empty() { return Ok(a); }
        self.n(1);
        Ok(Node::Compare(Box::new(a), ops))
    }
    fn arith(&mut self) -> Result<Node, Err> {
        let mut a = self.term()?;
        loop {
            let op = if self.is_p("+") { Op::Add } else if self.is_p("-") { Op::Sub } else { break };
            self.next();
            let b = self.term()?;
            self.n(2);
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
            self.n(2);
            a = Node::Bin(op, Box::new(a), Box::new(b));
        }
        Ok(a)
    }
    fn factor(&mut self) -> Result<Node, Err> {
        if self.is_p("-") { self.next(); let o = self.factor()?; self.n(2); return Ok(Node::Neg(Box::new(o))); }
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
                    Tok::Name(n) => { self.n(2); a = Node::Attr(Box::new(a), n); }
                    _ => return Err("not an expression: attribute".into()),
                }
            } else if self.is_p("(") {
                let f = match a { Node::Name(ref n) => n.clone(), _ => return Err("only the built-ins may be called".into()) };
                self.next();
                let mut args = Vec::new();
                while !self.is_p(")") {
                    if self.is_p("*") || self.is_p("**") { return Err("not allowed in an expression: Starred".into()); }
                    if matches!(self.peek(), Tok::Name(_)) && matches!(self.t.get(self.i + 1), Some(Tok::P("="))) {
                        return Err("only the built-ins may be called".into());
                    }
                    args.push(self.expr()?);
                    if !self.is_p(")") { self.expect(",")?; }
                }
                self.next();
                self.n(1);
                a = Node::Call(f, args);
            } else if self.is_p("[") {
                return Err("not allowed in an expression: Subscript".into());
            } else {
                break;
            }
        }
        Ok(a)
    }
    fn atom(&mut self) -> Result<Node, Err> {
        match self.next() {
            Tok::Int(v) => { self.n(1); Ok(Node::Const(V::Int(v))) }
            Tok::Float(v) => { self.n(1); Ok(Node::Const(V::Float(v))) }
            Tok::Str(s) => {
                let mut s = s;
                while let Tok::Str(more) = self.peek().clone() { self.next(); s.push_str(&more); }
                self.n(1);
                Ok(Node::Const(V::Str(s)))
            }
            Tok::Name(n) => {
                match n.as_str() {
                    "True" => { self.n(1); Ok(Node::Const(V::Bool(true))) }
                    "False" => { self.n(1); Ok(Node::Const(V::Bool(false))) }
                    "None" => { self.n(1); Ok(Node::Const(V::None)) }
                    k if KEYWORDS.contains(&k) => Err(format!("not an expression: {:?}", k)),
                    _ => { self.n(2); Ok(Node::Name(n)) }
                }
            }
            Tok::P("(") => {
                if self.is_p(")") { self.next(); self.n(2); return Ok(Node::List(vec![])); }
                let first = self.expr()?;
                if self.is_p(")") { self.next(); return Ok(first); }
                let mut v = vec![first];
                while self.is_p(",") {
                    self.next();
                    if self.is_p(")") { break; }
                    v.push(self.expr()?);
                }
                self.expect(")")?;
                self.n(2);
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
                        self.n(4);
                        let elt = v.pop().unwrap();
                        return Ok(Node::Comp(Box::new(elt), var, Box::new(iter), cond));
                    }
                    if !self.is_p("]") { self.expect(",")?; }
                }
                self.next();
                self.n(2);
                Ok(Node::List(v))
            }
            Tok::P("{") => {
                let mut v = Vec::new();
                while !self.is_p("}") {
                    let k = self.expr()?;
                    if !self.is_p(":") { return Err("not allowed in an expression: Set".into()); }
                    self.next();
                    let val = self.expr()?;
                    let key = match k { Node::Const(V::Str(s)) => s, _ => return Err("a dict's keys are literal strings".into()) };
                    v.push((key, val));
                    if !self.is_p("}") { self.expect(",")?; }
                }
                self.next();
                self.n(1);
                Ok(Node::Dict(v))
            }
            t => Err(format!("not an expression: unexpected {:?}", t)),
        }
    }
}

pub fn parse(src: &str) -> Result<Node, Err> { parse_scaled(src, 1) }

/// `parse` with the caps times `scale`: a getter's arms are each held to the caps, and folded into one expression
/// (syntax.rs fold_arms) that may be that many times as long.
pub fn parse_scaled(src: &str, scale: usize) -> Result<Node, Err> {
    let (chars, nodes) = (EXPR_MAX * scale + 40 * (scale - 1), NODES_MAX * scale + 4 * (scale - 1));
    let toks = lex(src)?;
    // the character cap is for logic: a string literal's text (a SQL query, a header) is one node, not counted here
    let quoted: usize = toks.iter().map(|t| if let Tok::Str(x) = t { x.chars().count() } else { 0 }).sum();
    if src.chars().count().saturating_sub(quoted) > chars { return Err(format!("an expression is at most {} characters (string literals not counted)", EXPR_MAX)); }
    let mut p = P { t: toks, i: 0, nodes: 0 };
    let e = p.expr()?;
    if *p.peek() != Tok::End { return Err("not an expression: trailing input".into()); }
    if p.nodes > nodes { return Err(format!("an expression is at most {} nodes", NODES_MAX)); }
    Ok(e)
}

pub struct Cache { m: HashMap<String, Rc<Result<Node, Err>>> }

impl Cache {
    pub fn new() -> Self { Cache { m: HashMap::new() } }
    pub fn get(&mut self, src: &str) -> Rc<Result<Node, Err>> { self.get_scaled(src, 1) }
    pub fn get_scaled(&mut self, src: &str, scale: usize) -> Rc<Result<Node, Err>> {
        if let Some(h) = self.m.get(src) { return h.clone(); }
        if self.m.len() > 4096 { self.m.clear(); }
        let r = Rc::new(parse_scaled(src, scale.max(1)));
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
}

pub fn truthy(v: &V) -> bool {
    match v {
        V::None => false,
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
        (V::None, V::None) => true,
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
        V::Dict(d) => match a { V::Str(x) => Some(d.iter().any(|(k, _)| k == x)), V::Int(_) | V::Float(_) | V::Bool(_) | V::None => Some(false), _ => None },
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
        "dot" => { argn(2)?; let (x, y) = (vec_of(&a[0])?, vec_of(&a[1])?); if x.len() != y.len() { return Err("dot: shapes not aligned".into()); } Ok(V::Float(x.iter().zip(&y).map(|(p, q)| p * q).sum())) }
        "norm" => { argn(1)?; Ok(V::Float(norm(&vec_of(&a[0])?))) }
        "distance" => { argn(2)?; Ok(V::Float(dist(&vec_of(&a[0])?, &vec_of(&a[1])?))) }
        "unit" => { argn(1)?; let x = vec_of(&a[0])?; let n = norm(&x); Ok(floats(if n > 1e-12 { x.iter().map(|v| v / n).collect() } else { x })) }
        "abs" => { argn(1)?; match &a[0] { V::Int(i) => Ok(V::Int(i.abs())), V::Float(x) => Ok(V::Float(x.abs())), V::Bool(b) => Ok(V::Int(*b as i64)), _ => Err("bad operand type for abs()".into()) } }
        "min" => { if a.is_empty() { return Err("min expected 1 argument".into()); } extreme(&a, std::cmp::Ordering::Less) }
        "max" => { if a.is_empty() { return Err("max expected 1 argument".into()); } extreme(&a, std::cmp::Ordering::Greater) }
        "len" => { argn(1)?; match &a[0] { V::List(l) => Ok(V::Int(l.len() as i64)), V::Str(t) => Ok(V::Int(t.chars().count() as i64)), V::Dict(d) => Ok(V::Int(d.len() as i64)), V::None => Ok(V::Int(0)), v => Err(format!("object of type {:?} has no len()", v)) } }
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
        "join" => {
            argn(2)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None => &none, _ => return Err("join(list, sep): a list".into()) };  // #1065: a pending read (None) is empty
            Ok(V::Str(l.iter().map(py_str).collect::<Vec<_>>().join(&text_of(&a[1])?)))
        }
        "head" => {
            argn(2)?;
            let n = match &a[1] { V::Int(n) => (*n).max(0) as usize, V::Float(f) => f.max(0.0) as usize, _ => return Err("head(text, n): n is a number".into()) };
            if let V::List(l) = &a[0] { return Ok(V::List(l.iter().take(n).cloned().collect())); }   // a list's first n
            Ok(V::Str(text_of(&a[0])?.chars().take(n).collect()))
        }
        // ---- lists ----
        "sort_by" => {
            if a.len() < 2 || a.len() > 3 { return Err("sort_by(rows, key[, desc]): 2 or 3 arguments".into()); }
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None => &none, _ => return Err("sort_by(rows, key, desc): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let key = text_of(&a[1])?;
            let desc = a.get(2).map(truthy).unwrap_or(false);
            let field = |v: &V| -> V { match v { V::Dict(d) => d.iter().find(|(k, _)| *k == key).map(|(_, x)| x.clone()).unwrap_or(V::None), _ => V::None } };
            let mut out = l.clone();
            // None first going up, last going down; ties keep their order
            out.sort_by(|x, y| {
                let (p, q) = (field(x), field(y));
                let o = match (&p, &q) {
                    (V::None, V::None) => std::cmp::Ordering::Equal,
                    (V::None, _) => std::cmp::Ordering::Less,
                    (_, V::None) => std::cmp::Ordering::Greater,
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
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None => &none, _ => return Err("pluck(rows, key): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let key = text_of(&a[1])?;
            Ok(V::List(l.iter().map(|r| match r { V::Dict(d) => d.iter().find(|(x, _)| *x == key).map(|(_, y)| y.clone()).unwrap_or(V::None), _ => V::None }).collect()))
        }
        "distinct" => {
            argn(1)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None => &none, _ => return Err("distinct(list): a list".into()) };  // #1065: a pending read (None) is empty
            let mut out: Vec<V> = vec![];
            for x in l { if !out.iter().any(|y| py_eq(y, x)) { out.push(x.clone()); } }
            Ok(V::List(out))
        }
        "group_by" => {
            // rows grouped by a key, in first-seen order: [{key, rows}]
            argn(2)?;
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None => &none, _ => return Err("group_by(rows, key): rows is a list".into()) };  // #1065: a pending read (None) is empty
            let key = text_of(&a[1])?;
            let mut groups: Vec<(V, Vec<V>)> = vec![];
            for r in l {
                let k = match r { V::Dict(d) => d.iter().find(|(x, _)| *x == key).map(|(_, y)| y.clone()).unwrap_or(V::None), _ => V::None };
                match groups.iter_mut().find(|(g, _)| py_eq(g, &k)) { Some(g) => g.1.push(r.clone()), None => groups.push((k, vec![r.clone()])) }
            }
            Ok(V::List(groups.into_iter().map(|(k, rows)| V::Dict(vec![("key".into(), k), ("rows".into(), V::List(rows))])).collect()))
        }
        "concat" => {
            // lists joined end to end (`+` on lists is vector addition: lists are vectors)
            let mut out = vec![];
            for x in a.iter() { match x { V::List(l) => out.extend(l.iter().cloned()), V::None => {}, _ => return Err("concat(list, ...): lists".into()) } }
            Ok(V::List(out))
        }
        "fit" => {
            // the banner's budget (conv_banner.render): rows {text, prio, slot}; lower prio kept first, each line
            // costs len(text) + 1, a line that does not fit is skipped (a later shorter one may); page order = slot
            if a.len() < 2 || a.len() > 3 { return Err("fit(rows, budget[, used]): 2 or 3 arguments".into()); }
            let none: Vec<V> = Vec::new(); let l: &Vec<V> = match &a[0] { V::List(l) => l, V::None => &none, _ => return Err("fit(rows, budget): rows is a list".into()) };  // #1065: a pending read (None) is empty
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
        "epoch" => { argn(1)?; Ok(match &a[0] { V::Int(n) => V::Float(*n as f64), V::Float(f) => V::Float(*f), v => epoch_of(v).map(V::Float).unwrap_or(V::None) }) }
        // ---- time ----
        "localtime" => { argn(1)?; let ts = match &a[0] { V::Int(n) => *n as f64, V::Float(f) => *f, _ => return Err("localtime(ts): a number".into()) }; Ok(crate::clock::local(ts)) }
        "json" => { argn(1)?; match &a[0] { V::Str(t) => serde_json::from_str::<serde_json::Value>(t).map(|j| from_json(&j)).map_err(|e| format!("json: {}", e)), V::None => Ok(V::None), _ => Err("json(text): a string".into()) } }
        "str" => { argn(1)?; Ok(V::Str(match &a[0] { V::Str(t) => t.clone(), V::List(_) | V::Dict(_) => to_json(&a[0])?.to_string(), v => py_str(v) })) }
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
    match v { V::Str(s) => Ok(s.clone()), V::None => Ok(String::new()), V::Ent(e) => Ok(e.clone()), V::List(_) | V::Dict(_) => Err("expected text".into()), v => Ok(py_str(v)) }
}

pub fn py_str(v: &V) -> String {
    match v {
        V::None => "None".into(), V::Bool(b) => if *b { "True".into() } else { "False".into() },
        V::Int(i) => i.to_string(), V::Float(f) => format!("{:?}", f), V::Str(s) => s.clone(),
        V::Ent(e) => e.clone(), _ => format!("{:?}", v),
    }
}

pub fn eval(n: &Node, env: &HashMap<String, V>, w: &dyn World) -> Result<V, Err> {
    match n {
        Node::Const(v) => Ok(v.clone()),
        Node::Name(s) => env.get(s).cloned().ok_or_else(|| format!("unbound name {:?}", s)),
        Node::List(v) => Ok(V::List(v.iter().map(|x| eval(x, env, w)).collect::<Result<_, _>>()?)),
        Node::Dict(v) => Ok(V::Dict(v.iter().map(|(k, x)| Ok((k.clone(), eval(x, env, w)?))).collect::<Result<_, Err>>()?)),
        Node::Attr(b, a) => match eval(b, env, w)? {
            V::Ent(e) => { if a.starts_with('_') { return Err(format!("no attribute {:?}", a)); } Ok(w.attr(&e, a)) }
            _ => Err("attributes are read on entities only".into()),
        },
        Node::Bin(op, a, b) => { let x = eval(a, env, w)?; let y = eval(b, env, w)?; arith(*op, &x, &y) }
        Node::Neg(a) => match eval(a, env, w)? {
            V::List(l) => Ok(floats(vec_of(&V::List(l))?.into_iter().map(|x| -x).collect())),
            V::Int(i) => Ok(V::Int(-i)),
            V::Float(f) => Ok(V::Float(-f)),
            v => Err(format!("not a number: {:?}", v)),
        },
        Node::Not(a) => Ok(V::Bool(!truthy(&eval(a, env, w)?))),
        Node::And(v) => { let mut last = V::Bool(true); for x in v { last = eval(x, env, w)?; if !truthy(&last) { return Ok(last); } } Ok(last) }
        Node::Or(v) => { let mut last = V::Bool(false); for x in v { last = eval(x, env, w)?; if truthy(&last) { return Ok(last); } } Ok(last) }
        Node::Compare(a, ops) => {
            let mut left = eval(a, env, w)?;
            for (op, rn) in ops {
                let right = eval(rn, env, w)?;
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
        Node::If(a, c, b) => if truthy(&eval(c, env, w)?) { eval(a, env, w) } else { eval(b, env, w) },
        Node::Comp(elt, var, iter, cond) => {
            let xs = match eval(iter, env, w)? { V::List(l) => l, V::None => vec![], v => return Err(format!("can't iterate over {:?}", v)) };
            let mut out = Vec::new();
            let mut env2 = env.clone();
            for x in xs {
                env2.insert(var.clone(), x);
                if let Some(c) = cond { if !truthy(&eval(c, &env2, w)?) { continue; } }
                out.push(eval(elt, &env2, w)?);
            }
            Ok(V::List(out))
        }
        Node::Call(f, args) => {
            let a = args.iter().map(|x| eval(x, env, w)).collect::<Result<Vec<_>, _>>()?;
            call(f, a, w).map_err(|e| if e.starts_with(f.as_str()) || e.contains("built-ins") { e } else { format!("{}: {}", f, e) })
        }
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
        J::Object(o) => V::Dict(o.iter().map(|(k, v)| (k.clone(), from_json(v))).collect()),
    }
}

/// None where Python's _writable would refuse an entity (it can't be written).
pub fn to_json(v: &V) -> Result<serde_json::Value, Err> {
    use serde_json::Value as J;
    Ok(match v {
        V::None => J::Null,
        V::Bool(b) => J::Bool(*b),
        V::Int(i) => J::from(*i),
        V::Float(f) => serde_json::Number::from_f64(*f).map(J::Number).ok_or("not a finite number")?,
        V::Str(s) => J::String(s.clone()),
        V::List(l) => J::Array(l.iter().map(to_json).collect::<Result<_, _>>()?),
        V::Dict(d) => J::Object(d.iter().map(|(k, x)| Ok((k.clone(), to_json(x)?))).collect::<Result<_, Err>>()?),
        V::Ent(_) => return Err("cannot write a EntityView".into()),
    })
}
