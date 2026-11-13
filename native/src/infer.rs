//! Static type inference over c1f getters (#1143; operator's design, 2026-11-12). A REPORT: it enforces nothing.
//!
//! c1f is dynamically typed at run time, but a getter's result type follows from its expression plus the types of what it reads:
//! literals, the builtins' signatures, other getters, the right-hand sides of setters. Plain properties written from outside
//! (Boot facts, Request fields, driver receipts, JSON) are untyped EDGES: `Any`. Nulls propagate like SQL's: an operation on a
//! nullable value is nullable, `x or d` strips the null, `Int(x)` is a checked cast (a value, or a null with a reason), and a
//! comparison with `None` is a null test. This module only MODELS those rules; it never evaluates anything.
//!
//! A type is a set of kinds (`Bool Int Float Text List(T) Dict Entity(class)`) plus a nullable flag, or `Any`. `Num` is the
//! display name of `Int|Float`. `Never` is the empty type (the element of `[]`).
//!
//! Getters are resolved lazily, depth first, each once; a getter that reads itself (through any chain) is a cycle: its type is
//! `Any` and the cycle is a finding. That is the fixpoint of an acyclic system.
use crate::expr::{parse, parse_block, Cmp, EntPart, Node, Op, V};
use crate::syntax::{parse_full, END, FOR, LET};
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap};

// ---- types ----------------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Kind { Bool, Int, Float, Text, List(Box<Ty>), Dict, Entity(String) }

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Ty { pub any: bool, pub null: bool, pub kinds: BTreeSet<Kind> }

impl Ty {
    pub fn any() -> Ty { Ty { any: true, null: true, kinds: BTreeSet::new() } }
    pub fn never() -> Ty { Ty { any: false, null: false, kinds: BTreeSet::new() } }
    pub fn none() -> Ty { Ty { any: false, null: true, kinds: BTreeSet::new() } }
    pub fn of(k: Kind) -> Ty { Ty { any: false, null: false, kinds: BTreeSet::from([k]) } }
    pub fn bool() -> Ty { Ty::of(Kind::Bool) }
    pub fn int() -> Ty { Ty::of(Kind::Int) }
    pub fn float() -> Ty { Ty::of(Kind::Float) }
    pub fn num() -> Ty { Ty { any: false, null: false, kinds: BTreeSet::from([Kind::Int, Kind::Float]) } }
    pub fn text() -> Ty { Ty::of(Kind::Text) }
    pub fn dict() -> Ty { Ty::of(Kind::Dict) }
    pub fn list(t: Ty) -> Ty { Ty::of(Kind::List(Box::new(t))) }
    pub fn entity(c: &str) -> Ty { Ty::of(Kind::Entity(c.to_string())) }
    pub fn nullable(mut self) -> Ty { self.null = true; self }
    pub fn non_null(mut self) -> Ty { if !self.any { self.null = false; } self }
    pub fn null_if(self, c: bool) -> Ty { if c { self.nullable() } else { self } }
    /// true when it is not `Any` (some knowledge about it)
    pub fn is_concrete(&self) -> bool { !self.any }
    /// may be null (Any may be)
    pub fn maybe_null(&self) -> bool { self.any || self.null }
    pub fn join(&self, o: &Ty) -> Ty {
        if self.any || o.any { return Ty::any(); }
        let mut r = Ty { any: false, null: self.null || o.null, kinds: BTreeSet::new() };
        for k in self.kinds.iter().chain(o.kinds.iter()) { r.add(k.clone()); }
        r
    }
    fn add(&mut self, k: Kind) {
        if let Kind::List(e) = &k {
            if let Some(Kind::List(old)) = self.kinds.iter().find(|x| matches!(x, Kind::List(_))).cloned() {
                self.kinds.remove(&Kind::List(old.clone()));
                self.kinds.insert(Kind::List(Box::new(old.join(e))));
                return;
            }
        }
        self.kinds.insert(k);
    }
    fn is_num(k: &Kind) -> bool { matches!(k, Kind::Int | Kind::Float | Kind::Bool) }
    /// what a loop over it binds: the element type of its lists
    fn elem(&self) -> Ty {
        if self.any { return Ty::any(); }
        let mut r = Ty::never();
        for k in &self.kinds { if let Kind::List(e) = k { r = r.join(e); } }
        r
    }
    /// A short text form (`Int?`, `(Int|Text)?`, `List(Text)`, `Any`, `None`) and its reader, for tests and the snapshot.
    pub fn parse(s: &str) -> Result<Ty, String> {
        let s: Vec<char> = s.chars().filter(|c| !c.is_whitespace()).collect();
        let mut i = 0;
        let t = Self::p_union(&s, &mut i)?;
        if i != s.len() { return Err(format!("type: trailing input in {:?}", s.iter().collect::<String>())); }
        Ok(t)
    }
    fn p_union(s: &[char], i: &mut usize) -> Result<Ty, String> {
        let mut t = Self::p_term(s, i)?;
        while *i < s.len() && s[*i] == '|' { *i += 1; let o = Self::p_term(s, i)?; t = t.join(&o); }
        Ok(t)
    }
    fn p_term(s: &[char], i: &mut usize) -> Result<Ty, String> {
        let mut t = if *i < s.len() && s[*i] == '(' {
            *i += 1;
            let t = Self::p_union(s, i)?;
            if s.get(*i) != Some(&')') { return Err("type: expected )".into()); }
            *i += 1;
            t
        } else {
            let st = *i;
            while *i < s.len() && (s[*i].is_alphanumeric() || s[*i] == '_') { *i += 1; }
            let name: String = s[st..*i].iter().collect();
            let arg = |i: &mut usize| -> Result<Ty, String> {
                if s.get(*i) != Some(&'(') { return Err(format!("type: {name} needs (..)")); }
                *i += 1;
                let t = Self::p_union(s, i)?;
                if s.get(*i) != Some(&')') { return Err("type: expected )".into()); }
                *i += 1;
                Ok(t)
            };
            match name.as_str() {
                "Any" => Ty::any(), "None" => Ty::none(), "Never" => Ty::never(), "Bool" => Ty::bool(), "Int" => Ty::int(), "Float" => Ty::float(),
                "Num" => Ty::num(), "Text" => Ty::text(), "Dict" => Ty::dict(),
                "List" => Ty::list(arg(i)?),
                "Entity" => { let c = arg(i)?; Ty::entity(&c_of(&c)) }
                _ => return Err(format!("type: unknown {name:?}")),
            }
        };
        if *i < s.len() && s[*i] == '?' { *i += 1; t.null = true; }
        Ok(t)
    }
}

fn c_of(t: &Ty) -> String { t.kinds.iter().next().map(|k| format!("{:?}", k)).unwrap_or_default() }

impl std::fmt::Display for Ty {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.any { return write!(f, "Any"); }
        if self.kinds.is_empty() { return write!(f, "{}", if self.null { "None" } else { "Never" }); }
        let mut names: Vec<String> = vec![];
        if self.kinds.contains(&Kind::Bool) { names.push("Bool".into()); }
        match (self.kinds.contains(&Kind::Int), self.kinds.contains(&Kind::Float)) {
            (true, true) => names.push("Num".into()), (true, false) => names.push("Int".into()), (false, true) => names.push("Float".into()), _ => {}
        }
        if self.kinds.contains(&Kind::Text) { names.push("Text".into()); }
        for k in &self.kinds { if let Kind::List(e) = k { names.push(format!("List({})", e)); } }
        if self.kinds.contains(&Kind::Dict) { names.push("Dict".into()); }
        for k in &self.kinds { if let Kind::Entity(c) = k { names.push(format!("Entity({})", c)); } }
        let body = names.join("|");
        if !self.null { write!(f, "{}", body) } else if names.len() > 1 { write!(f, "({})?", body) } else { write!(f, "{}?", body) }
    }
}

// ---- the environment ---------------------------------------------------------------------------------------------

/// What an expression reads: the types of its variables, the getters' types (None: not a getter, a plain property), and a
/// sink the findings go to.
pub struct Env<'a> {
    pub vars: HashMap<String, Ty>,
    pub lookup: &'a dyn Fn(&str, &str) -> Option<Ty>,
    pub sink: &'a RefCell<Vec<String>>,
}

impl<'a> Env<'a> {
    pub fn new(lookup: &'a dyn Fn(&str, &str) -> Option<Ty>, sink: &'a RefCell<Vec<String>>) -> Env<'a> { Env { vars: HashMap::new(), lookup, sink } }
    pub fn with(mut self, name: &str, t: Ty) -> Env<'a> { self.vars.insert(name.to_string(), t); self }
    fn bind(&self, name: &str, t: Ty) -> Env<'a> { let mut vars = self.vars.clone(); vars.insert(name.to_string(), t); Env { vars, lookup: self.lookup, sink: self.sink } }
    fn find(&self, msg: String) { self.sink.borrow_mut().push(msg); }
}

fn no_getter(_: &str, _: &str) -> Option<Ty> { None }

/// Infer one expression's type from source, with variables given as `(name, type text)`; for tests and tools.
/// Returns the type as text and the findings.
pub fn infer_src(src: &str, vars: &[(&str, &str)]) -> Result<(String, Vec<String>), String> {
    let node = parse(src)?;
    let sink = RefCell::new(vec![]);
    let mut env = Env::new(&no_getter, &sink);
    for (n, t) in vars { env.vars.insert(n.to_string(), Ty::parse(t)?); }
    let t = infer_expr(&node, &env);
    let f = sink.borrow().clone();
    Ok((t.to_string(), f))
}

// ---- the rules -------------------------------------------------------------------------------------------------------

fn op_sym(op: Op) -> &'static str { match op { Op::Add => "+", Op::Sub => "-", Op::Mul => "*", Op::Div => "/" } }

/// The result kinds of `a op b` for one pair of kinds; None: it raises.
fn pair(op: Op, a: &Kind, b: &Kind) -> Option<Ty> {
    let (an, bn) = (Ty::is_num(a), Ty::is_num(b));
    if an && bn {
        return Some(if op == Op::Div || *a == Kind::Float || *b == Kind::Float { Ty::float() } else { Ty::int() });
    }
    match (a, b) {
        (Kind::Text, Kind::Text) if op == Op::Add => Some(Ty::text()),
        (Kind::List(_), Kind::List(_)) | (Kind::List(_), _) if matches!(a, Kind::List(_)) && (matches!(b, Kind::List(_)) || bn) => Some(Ty::list(Ty::float())),
        (_, Kind::List(_)) if an => Some(Ty::list(Ty::float())),
        _ => None,
    }
}

fn arith_ty(op: Op, a: &Ty, b: &Ty, env: &Env) -> Ty {
    if a.any && b.any { return Ty::any(); }
    // an operation on a null is a null; two nulls and nothing else: None
    if (!a.any && a.kinds.is_empty() && a.null) || (!b.any && b.kinds.is_empty() && b.null) { return Ty::none(); }
    if a.kinds.is_empty() && !a.any || b.kinds.is_empty() && !b.any { return Ty::never(); }
    let null = a.maybe_null() || b.maybe_null();
    if a.any || b.any {
        let o = if a.any { b } else { a };
        // Any is guessed to be the kind that works with what it meets (a number with a number, text with text, a vector with a list)
        let mut r = Ty::never();
        let mut ok = false;
        for k in &o.kinds {
            let guesses: Vec<Kind> = match k { Kind::Text => vec![Kind::Text], Kind::List(_) => vec![Kind::Float], _ => vec![Kind::Int, Kind::Float] };
            for g in guesses {
                if let Some(t) = if a.any { pair(op, &g, k) } else { pair(op, k, &g) } { ok = true; r = r.join(&t); }
            }
            if matches!(k, Kind::Int | Kind::Float | Kind::Bool) { r = r.join(&Ty::num()); }
        }
        if !ok { env.find(format!("cannot apply {} to {} and {}", op_sym(op), a, b)); return Ty::any(); }
        // number against Any: Int or Float; that is all the model knows
        return r.nullable();
    }
    let mut r = Ty::never();
    let mut ok = false;
    for ka in &a.kinds { for kb in &b.kinds { if let Some(t) = pair(op, ka, kb) { ok = true; r = r.join(&t); } } }
    if !ok { env.find(format!("cannot apply {} to {} and {}", op_sym(op), a, b)); return Ty::any(); }
    r.null_if(null)
}

fn cmp_ok(a: &Kind, b: &Kind) -> bool {
    (Ty::is_num(a) && Ty::is_num(b)) || matches!((a, b), (Kind::Text, Kind::Text) | (Kind::List(_), Kind::List(_)))
}

/// what an argument position accepts
#[derive(Clone, Copy)]
enum Acc { Num, Text, List, Len }
impl Acc {
    fn takes(self, k: &Kind) -> bool {
        match self {
            Acc::Num => Ty::is_num(k),
            Acc::Text => matches!(k, Kind::Text | Kind::Int | Kind::Float | Kind::Bool | Kind::Entity(_)),
            Acc::List => matches!(k, Kind::List(_)),
            Acc::Len => matches!(k, Kind::List(_) | Kind::Text | Kind::Dict)
        }
    }
    fn what(self) -> &'static str { match self { Acc::Num => "a number", Acc::Text => "text", Acc::List => "a list", Acc::Len => "a list, text or dict" } }
}

fn check(f: &str, i: usize, t: &Ty, acc: Acc, env: &Env) {
    if t.any || t.kinds.is_empty() { return; }
    if !t.kinds.iter().any(|k| acc.takes(k)) { env.find(format!("{f}: argument {} is {}, expected {}", i + 1, t, acc.what())); }
}

fn arity(f: &str, n: usize, lo: usize, hi: usize, env: &Env) -> bool {
    if n >= lo && n <= hi { return true; }
    let want = if lo == hi { format!("{lo}") } else if hi == usize::MAX { format!("at least {lo}") } else { format!("{lo} to {hi}") };
    env.find(format!("{f}: takes {want} arguments, got {n}"));
    false
}

fn const_str(n: &Node) -> Option<&str> { if let Node::Const(V::Str(s)) = n { Some(s) } else { None } }

fn cast_target(f: &str) -> Option<Ty> {
    Some(match f {
        "Int" => Ty::int(), "Float" => Ty::float(), "Num" => Ty::num(), "Text" => Ty::text(), "Bool" => Ty::bool(),
        "List" => Ty::list(Ty::any()), "Dict" => Ty::dict(), _ => return None,
    })
}

fn is_instance_of(k: &Kind, target: &Kind) -> bool { std::mem::discriminant(k) == std::mem::discriminant(target) }

/// A getter or a plain property read off an entity type.
fn attr_ty(base: &Ty, name: &str, env: &Env) -> Ty {
    if base.any { return Ty::any(); }
    if base.kinds.is_empty() { return if base.null { Ty::none() } else { Ty::never() }; }
    let mut r = Ty::never();
    let mut ok = false;
    for k in &base.kinds {
        if let Kind::Entity(c) = k {
            ok = true;
            r = r.join(&if c == "*" { Ty::any() } else { (env.lookup)(c, name).unwrap_or_else(Ty::any) });
        }
    }
    if !ok { env.find(format!("attribute .{name} read on {base}: attributes are read on entities only")); return Ty::any(); }
    r.null_if(base.null)
}

pub fn infer_expr(n: &Node, env: &Env) -> Ty {
    match n {
        Node::Const(v) => match v {
            V::None | V::Nil(_) => Ty::none(), V::Bool(_) => Ty::bool(), V::Int(_) => Ty::int(), V::Float(_) => Ty::float(), V::Str(_) => Ty::text(),
            V::List(_) => Ty::list(Ty::any()), V::Dict(_) => Ty::dict(), V::Ent(_) => Ty::entity("*"),
        },
        Node::Name(s) => match env.vars.get(s) { Some(t) => t.clone(), None if s == "clock" => Ty::num(), None => Ty::any() },
        Node::List(v) => Ty::list(v.iter().fold(Ty::never(), |a, x| a.join(&infer_expr(x, env)))),
        Node::Dict(v) => { for (_, x) in v { infer_expr(x, env); } Ty::dict() }
        Node::Attr(b, a) => { let t = infer_expr(b, env); attr_ty(&t, a, env) }
        Node::Index(b, i) => {
            let (bt, it) = (infer_expr(b, env), infer_expr(i, env));
            if bt.any { return Ty::any(); }
            if bt.kinds.is_empty() { return if bt.null { Ty::none() } else { Ty::never() }; }
            let mut r = Ty::never();
            let mut ok = false;
            for k in &bt.kinds {
                let idx_ok = |want: fn(&Kind) -> bool| it.any || it.kinds.is_empty() || it.kinds.iter().any(want);
                match k {
                    Kind::List(e) if idx_ok(|x| *x == Kind::Int) => { ok = true; r = r.join(e); }
                    Kind::Dict if idx_ok(|x| *x == Kind::Text) => { ok = true; r = Ty::any(); }
                    Kind::Entity(c) if idx_ok(|x| *x == Kind::Text) => {
                        ok = true;
                        r = r.join(&match const_str(i) { Some(p) if c != "*" => (env.lookup)(c, p).unwrap_or_else(Ty::any), _ => Ty::any() });
                    }
                    _ => {}
                }
            }
            if !ok { env.find(format!("cannot index a {} by a {}", bt, it)); return Ty::any(); }
            r.nullable()   // a miss is a null
        }
        Node::Bin(op, a, b) => { let (x, y) = (infer_expr(a, env), infer_expr(b, env)); arith_ty(*op, &x, &y, env) }
        Node::Neg(a) => {
            let t = infer_expr(a, env);
            if t.any { return Ty::num().nullable(); }
            if t.kinds.is_empty() { return t; }
            let mut r = Ty::never();
            let mut ok = false;
            for k in &t.kinds {
                match k {
                    Kind::Int | Kind::Bool => { ok = true; r = r.join(&Ty::int()); }
                    Kind::Float => { ok = true; r = r.join(&Ty::float()); }
                    Kind::List(_) => { ok = true; r = r.join(&Ty::list(Ty::float())); }
                    _ => {}
                }
            }
            if !ok { env.find(format!("cannot negate {}", t)); return Ty::any(); }
            r.null_if(t.null)
        }
        Node::Not(a) => { infer_expr(a, env); Ty::bool() }
        Node::And(v) => {
            let ts: Vec<Ty> = v.iter().map(|x| infer_expr(x, env)).collect();
            let mut r = ts.last().cloned().unwrap_or_else(Ty::never);
            for t in &ts[..ts.len() - 1] {   // the first falsy operand is the value: an entity is never falsy
                let mut f = t.clone();
                f.kinds.retain(|k| !matches!(k, Kind::Entity(_)));
                r = r.join(&f);
            }
            r
        }
        Node::Or(v) => {
            let ts: Vec<Ty> = v.iter().map(|x| infer_expr(x, env)).collect();
            let mut r = ts.last().cloned().unwrap_or_else(Ty::never);
            for t in &ts[..ts.len() - 1] { r = r.join(&t.clone().non_null()); }
            // `Int(x) or Text(x)`: casts of one value whose kinds the casts together cover: the chain cannot end in a null
            if let Some(args) = cast_chain(v) {
                let (arg, targets) = args;
                let at = infer_expr(arg, env);
                if !at.any && !at.null && !at.kinds.is_empty() && at.kinds.iter().all(|k| targets.iter().any(|t| is_instance_of(k, t))) { r = r.non_null(); }
            }
            r
        }
        Node::Compare(a, ops) => {
            let mut left = infer_expr(a, env);
            for (op, rn) in ops {
                let right = infer_expr(rn, env);
                if matches!(op, Cmp::Lt | Cmp::Le | Cmp::Gt | Cmp::Ge)
                    && !left.any && !right.any && !left.kinds.is_empty() && !right.kinds.is_empty()
                    && !left.kinds.iter().any(|x| right.kinds.iter().any(|y| cmp_ok(x, y))) {
                    env.find(format!("cannot compare {} with {}", left, right));
                }
                left = right;
            }
            Ty::bool()
        }
        Node::Let(name, v, rest) => { let t = infer_expr(v, env); infer_expr(rest, &env.bind(name, t)) }
        Node::If(a, c, b) => { infer_expr(c, env); infer_expr(a, env).join(&infer_expr(b, env)) }
        Node::Comp(elt, var, iter, cond) => {
            let it = infer_expr(iter, env);
            if !it.any && !it.kinds.is_empty() && !it.kinds.iter().any(|k| matches!(k, Kind::List(_))) { env.find(format!("cannot iterate over {}", it)); }
            let inner = env.bind(var, it.elem());
            if let Some(c) = cond { infer_expr(c, &inner); }
            Ty::list(infer_expr(elt, &inner))
        }
        Node::Call(f, args) => call_ty(f, args, env),
        Node::Ctor(c, args) => { for (_, a) in args { infer_expr(a, env); } Ty::entity(c) }
        Node::Ent(parts) => {
            // the class is the first segment after a namespace; a key that may be null (or is not an int or text) makes the id null
            let mut nullable = false;
            for p in parts { if let EntPart::Key(k) = p { let t = infer_expr(k, env); if t.maybe_null() || t.kinds.iter().any(|x| !matches!(x, Kind::Int | Kind::Text)) { nullable = true; } } }
            let first = match parts.first() { Some(EntPart::Lit(t)) => t.as_str(), _ => "" };
            let cls = first.rsplit_once("::").map_or(first, |(_, r)| r).split(':').next().unwrap_or("");
            Ty::entity(cls).null_if(nullable)
        }
    }
}

/// every operand is a cast `T(e)` of the same `e`: (e, the target kinds)
fn cast_chain(v: &[Node]) -> Option<(&Node, Vec<Kind>)> {
    let mut arg: Option<&Node> = None;
    let mut targets = vec![];
    for x in v {
        let Node::Call(f, a) = x else { return None };
        let t = cast_target(f)?;
        if a.len() != 1 { return None; }
        match arg { Some(p) if format!("{:?}", p) != format!("{:?}", a[0]) => return None, _ => arg = Some(&a[0]) }
        targets.extend(t.kinds.iter().cloned());
    }
    Some((arg?, targets))
}

fn call_ty(f: &str, args: &[Node], env: &Env) -> Ty {
    let tys: Vec<Ty> = args.iter().map(|x| infer_expr(x, env)).collect();
    let n = tys.len();
    let any_null = tys.iter().any(|t| t.maybe_null());     // an error caused by a null is a null
    let ar = |lo: usize, hi: usize| arity(f, n, lo, hi, env);
    let chk = |i: usize, acc: Acc| if let Some(t) = tys.get(i) { check(f, i, t, acc, env) };
    if let Some(target) = cast_target(f) {
        if !ar(1, 1) { return Ty::any(); }
        let t = &tys[0];
        let inside = !t.any && !t.null && !t.kinds.is_empty() && t.kinds.iter().all(|k| target.kinds.iter().any(|x| is_instance_of(k, x)));
        let mut r = target;
        if f == "List" { if let Some(Kind::List(e)) = t.kinds.iter().find(|k| matches!(k, Kind::List(_))) { r = Ty::list((**e).clone()); } }
        return r.null_if(!inside);
    }
    match f {
        "None" => { ar(0, 1); Ty::none() }
        "dot" => { if ar(2, 2) { chk(0, Acc::List); chk(1, Acc::List); } Ty::float().null_if(any_null) }
        "norm" => { if ar(1, 1) { chk(0, Acc::List); } Ty::float().null_if(any_null) }
        "distance" => { if ar(2, 2) { chk(0, Acc::List); chk(1, Acc::List); } Ty::float().null_if(any_null) }
        "unit" => { if ar(1, 1) { chk(0, Acc::List); } Ty::list(Ty::float()).null_if(any_null) }
        "abs" => {
            if !ar(1, 1) { return Ty::any(); }
            chk(0, Acc::Num);
            let t = &tys[0];
            if t.any { return Ty::num().nullable(); }
            let mut r = Ty::never();
            for k in &t.kinds { match k { Kind::Float => r = r.join(&Ty::float()), Kind::Int | Kind::Bool => r = r.join(&Ty::int()), _ => {} } }
            r.null_if(t.null)
        }
        "min" | "max" => {
            if !ar(1, usize::MAX) { return Ty::any(); }
            if n == 1 {
                let t = &tys[0];
                if t.any { return Ty::any(); }
                let mut r = Ty::never();
                for k in &t.kinds { match k { Kind::List(e) => r = r.join(e), Kind::Text | Kind::Dict => r = r.join(&Ty::text()), _ => {} } }
                if !r.any && r.kinds.is_empty() && !t.kinds.is_empty() { env.find(format!("{f}: argument 1 is {}, expected a list", t)); return Ty::any(); }
                r.null_if(t.null)
            } else {
                let mut r = Ty::never();
                for t in &tys { r = r.join(t); }
                r
            }
        }
        "len" => { if ar(1, 1) { chk(0, Acc::Len); } Ty::int() }
        "why" => { ar(1, 1); Ty::text().null_if(true) }
        "lower" | "strip" | "plain" | "unspeak" => { if ar(1, 1) { chk(0, Acc::Text); } Ty::text() }
        "replace" => { if ar(3, 3) { for i in 0..3 { chk(i, Acc::Text); } } Ty::text() }
        "startswith" => { if ar(2, 2) { chk(0, Acc::Text); chk(1, Acc::Text); } Ty::bool() }
        "lines" | "words" => { if ar(1, 1) { chk(0, Acc::Text); } Ty::list(Ty::text()) }
        "join" => { if ar(2, 2) { chk(0, Acc::List); chk(1, Acc::Text); } Ty::text() }
        "head" | "tail" => {
            if !ar(2, 2) { return Ty::any(); }
            chk(1, Acc::Num);
            let t = &tys[0];
            if t.any { return Ty::any(); }
            let mut r = Ty::never();
            for k in &t.kinds { match k { Kind::List(_) => r = r.join(&Ty::of(k.clone())), _ => r = r.join(&Ty::text()) } }
            if t.kinds.iter().any(|k| matches!(k, Kind::Dict)) { env.find(format!("{}: argument 1 is {}, expected text or a list", f, t)); }
            if r.kinds.is_empty() { Ty::text() } else { r }
        }
        "sort_by" | "distinct" => {
            let lo = if f == "sort_by" { 2 } else { 1 };
            if !ar(lo, if f == "sort_by" { 3 } else { 1 }) { return Ty::any(); }
            chk(0, Acc::List);
            if f == "sort_by" { chk(1, Acc::Text); }
            let e = tys[0].elem();
            Ty::list(if tys[0].any { Ty::any() } else { e })
        }
        "pluck" => { if ar(2, 2) { chk(0, Acc::List); chk(1, Acc::Text); } Ty::list(Ty::any()) }
        "group_by" => { if ar(2, 2) { chk(0, Acc::List); chk(1, Acc::Text); } Ty::list(Ty::dict()) }
        "concat" => {
            let mut e = Ty::never();
            for (i, t) in tys.iter().enumerate() { chk(i, Acc::List); e = e.join(&t.elem()); }
            Ty::list(e)
        }
        "fit" => { if ar(2, 3) { chk(0, Acc::List); chk(1, Acc::Num); } Ty::list(Ty::text()) }
        "clip" => { if ar(2, 2) { chk(0, Acc::Text); chk(1, Acc::Num); } Ty::text() }
        "ago" => { ar(2, 2); Ty::text() }
        "num" => { ar(1, 1); Ty::num().nullable() }
        "epoch" => { ar(1, 1); Ty::float().nullable() }
        "localtime" => { if ar(1, 1) { chk(0, Acc::Num); } Ty::dict().null_if(any_null) }
        "json" => { ar(1, 1); Ty::any() }
        "str" => { ar(1, 1); Ty::text() }
        "before" | "after" => { if ar(2, 2) { chk(0, Acc::Text); chk(1, Acc::Text); } Ty::text() }
        "referencing" => { ar(2, 2); Ty::list(Ty::entity("*")) }
        "prop" => {
            if !ar(2, 2) { return Ty::any(); }
            let t = &tys[0];
            if t.any || t.kinds.is_empty() { return Ty::any(); }
            let mut r = Ty::never();
            for k in &t.kinds {
                r = r.join(&match (k, const_str(&args[1])) {
                    (Kind::Entity(c), Some(p)) if c != "*" => (env.lookup)(c, p).unwrap_or_else(Ty::any),
                    _ => Ty::any(),
                });
            }
            r.nullable()
        }
        // the host's own functions (drivers, the seat's recall): untyped edges, except `instances`
        "instances" => match args.first().and_then(const_str) { Some(c) => Ty::list(Ty::entity(c)), None => Ty::list(Ty::entity("*")) },
        _ => Ty::any(),
    }
}

// ---- packs ------------------------------------------------------------------------------------------------------------

#[derive(Debug, Default, Clone, PartialEq)]
pub struct PackReport {
    /// `Class.prop` -> the getter's inferred type
    pub getters: BTreeMap<String, String>,
    /// `Class.prop` -> the union of the types setters write to it (and, when the property also has a getter, its type)
    pub writes: BTreeMap<String, String>,
    /// what is certainly wrong, each prefixed with where (`Class.prop: ...` or `setter name: ...`)
    pub findings: Vec<String>,
}

type Key = (String, String);

struct Session {
    defs: HashMap<Key, (String, usize)>,     // (cls, prop) -> (folded expr, arms)
    done: RefCell<HashMap<Key, Ty>>,
    stack: RefCell<Vec<Key>>,
    cyclic: RefCell<BTreeSet<Key>>,
    findings: RefCell<BTreeMap<Key, Vec<String>>>,   // by getter
    cycles: RefCell<Vec<(Key, String)>>,
}

impl Session {
    fn resolve(&self, key: &Key) -> Option<Ty> {
        let (expr, _arms) = self.defs.get(key)?;
        if let Some(t) = self.done.borrow().get(key) { return Some(t.clone()); }
        // a getter that reads its own name reads the property's WRITTEN value (`X.f getter: this.f` is the fallback arm): an untyped edge
        if self.stack.borrow().last() == Some(key) { return Some(Ty::any()); }
        let pos = self.stack.borrow().iter().position(|k| k == key);
        if let Some(p) = pos {
            let st = self.stack.borrow();
            let path: Vec<String> = st[p..].iter().chain(std::iter::once(key)).map(|k| format!("{}.{}", k.0, k.1)).collect();
            self.cyclic.borrow_mut().extend(st[p..].iter().cloned());
            self.cycles.borrow_mut().push((key.clone(), format!("getter cycle: {}", path.join(" -> "))));
            return Some(Ty::any());
        }
        self.stack.borrow_mut().push(key.clone());
        let sink = RefCell::new(vec![]);
        let lookup = |c: &str, p: &str| self.resolve(&(c.to_string(), p.to_string()));
        let t = match parse_block(expr) {
            Ok(node) => { let env = Env::new(&lookup, &sink).with("this", Ty::entity(&key.0)); infer_expr(&node, &env) }
            Err(e) => { sink.borrow_mut().push(format!("not an expression: {e}")); Ty::any() }
        };
        self.stack.borrow_mut().pop();
        let t = if self.cyclic.borrow().contains(key) { Ty::any() } else { t };
        self.findings.borrow_mut().insert(key.clone(), sink.into_inner());
        self.done.borrow_mut().insert(key.clone(), t.clone());
        Some(t)
    }
}

/// The class a write's target names: `this`, an expression of one entity type, or a literal id `'Class:key'` / `'Class:' + x`.
fn target_class(tgt: &str, cls: &str, env: &Env) -> String {
    if tgt == "this" || tgt == "x" { return cls.to_string(); }
    let Ok(node) = parse(tgt) else { return "*".into() };
    let from_id = |s: &str| s.split_once(':').map(|(c, _)| c.to_string());
    let lit = match &node {
        Node::Const(V::Str(s)) => from_id(s),
        Node::Bin(Op::Add, a, _) => if let Node::Const(V::Str(s)) = &**a { from_id(s) } else { None },
        _ => None,
    };
    if let Some(c) = lit { return c; }
    let t = infer_expr(&node, env);
    match t.kinds.iter().collect::<Vec<_>>().as_slice() { [Kind::Entity(c)] if !t.any => c.clone(), _ => "*".into() }
}

/// Infer every pack of a set together (a class a pack imports is typed from the pack that defines it). One report per pack, in
/// the order given, named by its `PACK` line.
pub fn infer_packs(texts: &[&str]) -> Result<Vec<(String, PackReport)>, String> {
    let mut packs = vec![];
    for (i, t) in texts.iter().enumerate() {
        let p = parse_full(t).map_err(|e| format!("pack {}: {e}", i + 1))?;
        packs.push(p);
    }
    let mut s = Session { defs: HashMap::new(), done: RefCell::new(HashMap::new()), stack: RefCell::new(vec![]), cyclic: RefCell::new(BTreeSet::new()),
        findings: RefCell::new(BTreeMap::new()), cycles: RefCell::new(vec![]) };
    for p in &packs { for g in &p.getters { s.defs.entry((g.cls.clone(), g.prop.clone())).or_insert((g.expr.clone(), g.arms)); } }
    let keys: Vec<Key> = packs.iter().flat_map(|p| p.getters.iter().map(|g| (g.cls.clone(), g.prop.clone()))).collect();
    for k in &keys { s.resolve(k); }
    s.defs.shrink_to_fit();
    let mut out = vec![];
    for p in &packs {
        let mut r = PackReport::default();
        let mut seen: BTreeSet<Key> = BTreeSet::new();
        for g in &p.getters {
            let k = (g.cls.clone(), g.prop.clone());
            let t = s.done.borrow().get(&k).cloned().unwrap_or_else(Ty::any);
            r.getters.insert(format!("{}.{}", k.0, k.1), t.to_string());
            if seen.insert(k.clone()) {
                for f in s.findings.borrow().get(&k).cloned().unwrap_or_default() { r.findings.push(format!("{}.{}: {}", k.0, k.1, f)); }
            }
        }
        for (k, msg) in s.cycles.borrow().iter() { if p.getters.iter().any(|g| (g.cls.as_str(), g.prop.as_str()) == (k.0.as_str(), k.1.as_str())) { r.findings.push(format!("{}.{}: {}", k.0, k.1, msg)); } }
        // setters: their conditions and right-hand sides are typed for findings, and each write is collected
        let mut writes: BTreeMap<String, Ty> = BTreeMap::new();
        for st in &p.setters {
            let sink = RefCell::new(vec![]);
            let lookup = |c: &str, pr: &str| s.resolve(&(c.to_string(), pr.to_string()));
            let base = Env::new(&lookup, &sink).with("this", Ty::entity(&st.cls));
            let branches = std::iter::once((&st.when, &st.writes)).chain(st.els.iter().map(|(c, w)| (c, w)));
            for (cond, ws) in branches {
                let mut env = Env { vars: base.vars.clone(), lookup: base.lookup, sink: base.sink };
                let mut scopes: Vec<HashMap<String, Ty>> = vec![];
                if cond != "True" { match parse(cond) { Ok(n) => { infer_expr(&n, &env); } Err(e) => sink.borrow_mut().push(format!("not an expression: {e}")) } }
                for (tgt, field, rhs) in ws {
                    if tgt == FOR {
                        let it = match parse(rhs) { Ok(n) => infer_expr(&n, &env), Err(_) => Ty::any() };
                        scopes.push(env.vars.clone());
                        env.vars.insert(field.clone(), it.elem());
                        continue;
                    }
                    if tgt == END { if let Some(v) = scopes.pop() { env.vars = v; } continue; }
                    if tgt == LET {
                        let t = match parse(rhs) { Ok(n) => infer_expr(&n, &env), Err(_) => Ty::any() };
                        env.vars.insert(field.clone(), t);
                        continue;
                    }
                    let t = match parse(rhs) { Ok(n) => infer_expr(&n, &env), Err(e) => { sink.borrow_mut().push(format!("not an expression: {e}")); Ty::any() } };
                    if tgt == crate::fields::LLM_CALL { continue; }
                    if field == crate::syntax::CTOR { continue; }   // a constructor writes no property: its arguments are typed above
                    let c = target_class(tgt, &st.cls, &env);
                    let e = writes.entry(format!("{}.{}", c, field)).or_insert_with(Ty::never);
                    *e = e.join(&t);
                }
            }
            for f in sink.into_inner() { r.findings.push(format!("{}.{} on-setter {}: {}", st.cls, st.prop, st.name, f)); }
        }
        for (k, t) in writes {
            let gt = k.split_once('.').and_then(|(c, pr)| s.done.borrow().get(&(c.to_string(), pr.to_string())).cloned());
            r.writes.insert(k, match gt { Some(g) => format!("{} (getter: {})", t, g), None => t.to_string() });
        }
        r.findings.dedup();
        out.push((p.name.clone().unwrap_or_default(), r));
    }
    Ok(out)
}

/// Infer one pack on its own (what it imports is untyped: `Any`).
pub fn infer_pack(text: &str) -> Result<PackReport, String> {
    infer_packs(&[text]).map(|mut v| v.remove(0).1)
}

// ---- reads (what orders getters) -----------------------------------------------------------------------------------------

/// One field read in a getter's expression: the field name and the class it is read on (None: the receiver's class is not known,
/// so the read may land on a getter of any class).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Read { pub name: String, pub cls: Option<String> }

fn read_on(base: &Ty, name: &str, out: &mut Vec<Read>) {
    let classes: Vec<&String> = base.kinds.iter().filter_map(|k| if let Kind::Entity(c) = k { Some(c) } else { None }).collect();
    if base.any || classes.is_empty() { out.push(Read { name: name.into(), cls: None }); return; }
    for c in classes { out.push(Read { name: name.into(), cls: if c == "*" { None } else { Some(c.clone()) } }); }
}

fn walk_reads(n: &Node, env: &Env, out: &mut Vec<Read>) {
    let mut each = |xs: &[Node], env: &Env, out: &mut Vec<Read>| for x in xs { walk_reads(x, env, out) };
    match n {
        Node::Const(_) | Node::Name(_) => {}
        Node::List(v) | Node::And(v) | Node::Or(v) => each(v, env, out),
        Node::Dict(v) => for (_, x) in v { walk_reads(x, env, out) },
        Node::Attr(b, a) => { walk_reads(b, env, out); read_on(&infer_expr(b, env), a, out); }
        Node::Index(b, i) => {
            walk_reads(b, env, out); walk_reads(i, env, out);
            // an entity indexed by a literal key reads that field; a dict key is data (and an unknown receiver was never a read)
            if let Some(k) = const_str(i) {
                let t = infer_expr(b, env);
                if !t.any && t.kinds.iter().any(|x| matches!(x, Kind::Entity(c) if c != "*")) { read_on(&t, k, out); }
            }
        }
        Node::Bin(_, a, b) => { walk_reads(a, env, out); walk_reads(b, env, out); }
        Node::Neg(a) | Node::Not(a) => walk_reads(a, env, out),
        Node::Compare(a, ops) => { walk_reads(a, env, out); for (_, x) in ops { walk_reads(x, env, out); } }
        Node::Let(name, v, rest) => { walk_reads(v, env, out); let t = infer_expr(v, env); walk_reads(rest, &env.bind(name, t), out); }
        Node::If(a, c, b) => { walk_reads(a, env, out); walk_reads(c, env, out); walk_reads(b, env, out); }
        Node::Comp(elt, var, iter, cond) => {
            walk_reads(iter, env, out);
            let inner = env.bind(var, infer_expr(iter, env).elem());
            if let Some(c) = cond { walk_reads(c, &inner, out); }
            walk_reads(elt, &inner, out);
        }
        Node::Call(f, args) => {
            each(args, env, out);
            // a string that is the last argument of a call names a field (`prop(row, 'name')`, `pluck(rows, 'name')`)
            if let Some(k) = args.last().and_then(const_str) {
                if f == "prop" && args.len() == 2 { read_on(&infer_expr(&args[0], env), k, out); }
                else { out.push(Read { name: k.into(), cls: None }); }
            }
        }
        Node::Ctor(_, args) => for (_, x) in args { walk_reads(x, env, out) },
        Node::Ent(parts) => for p in parts { if let EntPart::Key(k) = p { walk_reads(k, env, out) } },
    }
}

/// What each getter reads, by class where the receiver's class is known (`this`, a loop variable over `instances('X')` or over a
/// getter of known element type, a constructor, an entity literal). `getters` are `(class, prop, expr)`; the result is in the same
/// order. None for a getter whose expression does not parse (the caller falls back to scanning its text).
pub fn getter_reads(getters: &[(&str, &str, &str)]) -> Vec<Option<Vec<Read>>> {
    let mut s = Session { defs: HashMap::new(), done: RefCell::new(HashMap::new()), stack: RefCell::new(vec![]), cyclic: RefCell::new(BTreeSet::new()),
        findings: RefCell::new(BTreeMap::new()), cycles: RefCell::new(vec![]) };
    for (c, p, e) in getters { s.defs.entry((c.to_string(), p.to_string())).or_insert((e.to_string(), 1)); }
    for (c, p, _) in getters { s.resolve(&(c.to_string(), p.to_string())); }
    let lookup = |c: &str, p: &str| s.done.borrow().get(&(c.to_string(), p.to_string())).cloned();
    getters.iter().map(|(c, _, e)| {
        let node = parse_block(e).ok()?;
        let sink = RefCell::new(vec![]);
        let env = Env::new(&lookup, &sink).with("this", Ty::entity(c));
        let mut out = vec![];
        walk_reads(&node, &env, &mut out);
        out.sort(); out.dedup();
        Some(out)
    }).collect()
}
