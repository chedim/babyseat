//! The VM core (ch.py `VM`), native: a mirror of the field kept from its writes, the triggers'
//! specs (validated by Python's `_validate`), the changed-trigger index, WHERE bindings, WHEN
//! watchers with their per-binding state, and the loop rule.
//!
//! The drain is RESUMABLE: `step` runs until the next firing and returns it; the host applies it
//! (a store write, which comes back here through `hear`) and calls `step` again. So a later trigger
//! on the same write reads what an earlier one just wrote, exactly as in ch.py.
//!
//! Lookups that follow the store's own spelling rules stay the host's (callbacks): resolving a spec
//! (`resolve` / `resolve_exact`), `dispatches` (night.resolve_method), `vel` and `align`.

use std::collections::{HashMap, HashSet, VecDeque};

use serde_json::{json, Map, Value as J};

use crate::expr::{self, Node, V};

pub const CLASS: &str = "Trigger";
pub const QUEUE_MAX: usize = 4096;
pub const ANCESTOR_HOPS: usize = 16 * 4; // entities.MultiParentOps.MAX_CHAIN * 4, as night.ancestors

/// An item of a FOR's list: an entity (the loop name is that entity) or a value.
#[derive(Clone, Debug, PartialEq)]
pub enum Item { Ent(String), Val(J) }

pub trait Host {
    fn resolve(&self, spec: &str) -> Option<String>;
    fn resolve_exact(&self, spec: &str) -> Option<String>;
    fn dispatch(&self, eid: &str, method: &str) -> Option<String>;
    fn vel(&self, eid: &str, now: f64, thr: f64) -> bool;
    fn align(&self, eid: &str, thr: f64) -> bool;
    /// The live store's entity as JSON [cls, props, position | null]; the position only when asked.
    fn live(&self, eid: &str, position: bool) -> Option<String>;
    /// A function a getter calls (`recall(...)`): args and result as JSON. None: no such function here.
    fn call(&self, _f: &str, _args_json: &str) -> Option<Result<String, String>> { None }
    /// A slow function the host runs OFF the engine (`recall`, `rerank`): the job that computes it, or None to call
    /// it in the round (`call`). The getter reads None until the answer lands, like `sql(...)`.
    fn call_later(&self, _f: &str, _args_json: &str, _asker: &str) -> Option<HostJob> { None }
}

/// A host function's answer (JSON), computed on another thread.
pub type HostJob = Box<dyn FnOnce() -> Result<String, String> + Send>;

pub struct Ent {
    pub cls: String,
    pub props: Map<String, J>,
    pub pos: Option<Vec<f64>>,
}

#[derive(Clone)]
pub struct Spec {
    pub when: Vec<Vec<J>>,
    pub wh: Vec<Vec<J>>,
    pub target: String,
    pub expr: String,
}

pub struct Trig {
    pub eid: String,
    pub spec: Option<Spec>,
    pub mine: bool,
}

#[derive(Clone)]
pub struct Write {
    pub eid: String,
    pub prop: String,
    pub new: J,
    pub old: J,
    pub writer: String,
    pub chain: J,
    pub seq: i64,
}

#[derive(Default, Clone)]
struct St {
    lv: Option<bool>,
    every: Option<f64>,
    stamp: Option<Vec<f64>>,
    within: Option<Vec<String>>,
}

type Binding = Vec<(String, String)>; // var -> eid, sorted by var

#[derive(Default)]
struct Memo {
    isa: HashMap<(String, String), bool>,
    cands: HashMap<String, Vec<String>>,
    disp: HashMap<(String, String), Option<String>>,
}

enum Unit {
    All,               // every binding (a changed trigger on a literal eid)
    Fixed(String),     // the written entity bound to this variable
}

struct Cur {
    w: Option<Write>,  // None: a sweep
    chain: J,
    now: f64,
    trs: Vec<usize>,
    ti: usize,
    units: VecDeque<Unit>,
    binds: VecDeque<Binding>,
    event: bool,
    started: bool,     // the current trigger's units are computed
    memo: Memo,
}

pub struct Core {
    pub docs: HashMap<String, Ent>,
    order: Vec<Option<String>>,
    at: HashMap<String, usize>,
    pub triggers: Vec<Trig>,
    by_prop: HashMap<String, HashSet<String>>,
    always: HashSet<String>,
    seen: HashMap<String, f64>,
    state: HashMap<String, HashMap<String, St>>,
    q: VecDeque<Write>,
    pub dropped: u64,
    pub fired: HashMap<String, u64>,
    pub refused: HashMap<String, u64>,
    /// the store driver (`sql(...)`): the seat's SQLite store, read-only, this engine's own connection
    #[cfg(feature = "python")]
    pub store: Option<crate::store::Store>,
    pub errors: HashMap<String, String>,
    /// `referencing(x, prop)`'s reverse index: prop -> target eid -> the entities whose prop holds it. Built per prop
    /// the first time it is asked for, then kept current on every write, load and removal.
    ref_ix: std::cell::RefCell<HashMap<String, HashMap<String, std::collections::BTreeSet<String>>>>,
    seq: u64,
    pub learner: bool,
    pub trace: Option<Vec<String>>,
    cur: Option<Cur>,
    cache: expr::Cache,
}

fn s(j: &J) -> String {
    match j { J::String(x) => x.clone(), J::Null => "None".into(), other => other.to_string() }
}

fn f(j: &J) -> f64 {
    match j { J::Number(n) => n.as_f64().unwrap_or(0.0), J::String(x) => x.trim().parse().unwrap_or(0.0), J::Bool(b) => if *b { 1.0 } else { 0.0 }, _ => 0.0 }
}

fn var(x: &J) -> Option<String> {
    match x { J::String(v) if v.starts_with('?') => Some(v[1..].to_string()), _ => None }
}

fn rpart(x: &str) -> (&str, &str) {
    match x.rfind('.') { Some(i) => (&x[..i], &x[i + 1..]), None => ("", x) }
}

fn bkey(b: &Binding) -> String {
    b.iter().map(|(k, v)| format!("{}\u{1}{}", k, v)).collect::<Vec<_>>().join("\u{2}")
}

fn bind_set(b: &Binding, v: &str, eid: &str) -> Binding {
    let mut o = b.clone();
    o.push((v.to_string(), eid.to_string()));
    o.sort();
    o
}

fn bget<'a>(b: &'a Binding, v: &str) -> Option<&'a String> {
    b.iter().find(|(k, _)| k == v).map(|(_, e)| e)
}

struct View<'a> { core: &'a Core }

impl<'a> expr::World for View<'a> {
    fn attr(&self, eid: &str, attr: &str) -> V {
        let Some(e) = self.core.docs.get(eid) else { return V::None };
        match attr {
            "eid" => V::Str(eid.to_string()),
            "cls" => V::Str(e.cls.clone()),
            "position" => e.pos.as_ref().map(|p| V::List(p.iter().map(|x| V::Float(*x)).collect())).unwrap_or(V::None),
            _ => e.props.get(attr).map(expr::from_json).unwrap_or(V::None),
        }
    }
    fn refs(&self, eid: &str, prop: &str) -> Vec<String> { self.core.refs(eid, prop) }
    fn host(&self, f: &str, a: &[V]) -> Option<Result<V, String>> { self.core.native_call(f, a) }
}

/// A view that records what an evaluation read: (eid, field) for every attribute, and (REFS, prop) for every
/// `referencing(_, prop)`. #994 getters: these are the getter's inputs.
pub const REFS: &str = "\u{0}refs";
struct Traced<'a> { inner: View<'a>, reads: std::cell::RefCell<Vec<(String, String)>>, host: Option<&'a dyn Host>, asker: &'a str }

/// The read key a getter's query is tracked under: when its rows arrive, the getter is dirty (fields.rs).
pub const SQL: &str = "\u{0}sql";
/// The read key of `instances(cls)`: a birth or a retirement of that class makes the reader dirty (fields.rs).
pub const CLASS_READ: &str = "\u{0}class";

impl<'a> expr::World for Traced<'a> {
    fn attr(&self, eid: &str, attr: &str) -> V {
        self.reads.borrow_mut().push((eid.to_string(), attr.to_string()));
        self.inner.attr(eid, attr)
    }
    fn refs(&self, eid: &str, prop: &str) -> Vec<String> {
        self.reads.borrow_mut().push((REFS.to_string(), format!("{}\u{0}{}", prop, eid)));   // per (prop, target)
        self.inner.refs(eid, prop)
    }
    /// The host's functions run on live rounds only: a silent compute (rebuild, birth) never calls out, its value
    /// is None until the next live round reads its inputs again.
    fn host(&self, f: &str, a: &[V]) -> Option<Result<V, String>> {
        if f == "instances" {
            if let Some(V::Str(c)) = a.first() { self.reads.borrow_mut().push((CLASS_READ.to_string(), c.clone())); }
            return self.inner.core.native_call(f, a);
        }
        if f == "read" || f == "listdir" || f == "stat" {   // the fs driver: asked like a query (drivers.rs)
            let r = self.inner.core.fs_request(self.asker, f, a);
            if let Some(k) = r.1 { self.reads.borrow_mut().push((SQL.to_string(), k)); }
            return Some(r.0);
        }
        if f == "sql" || f == "jsonl" {       // the store driver: asked, never waited for (store.rs)
            let r = if f == "sql" { self.inner.core.sql_request(self.asker, a) } else { self.inner.core.jsonl_request(self.asker, a) };
            if let Some(k) = r.1 { self.reads.borrow_mut().push((SQL.to_string(), k)); }
            return Some(r.0);
        }
        if let Some(r) = self.inner.core.native_call(f, a) { return Some(r); }
        let Some(h) = self.host else { return Some(Ok(V::None)) };
        let args = match a.iter().map(expr::to_json).collect::<Result<Vec<_>, _>>() { Ok(v) => v, Err(e) => return Some(Err(e)) };
        let args_s = serde_json::Value::Array(args).to_string();
        if let Some(job) = h.call_later(f, &args_s, self.asker) {
            let r = self.inner.core.host_request(self.asker, f, &args_s, job);
            if let Some(k) = r.1 { self.reads.borrow_mut().push((SQL.to_string(), k)); }
            return Some(r.0);
        }
        match h.call(f, &args_s)? {
            Ok(s) => Some(serde_json::from_str::<serde_json::Value>(&s).map(|j| expr::from_json(&j)).map_err(|e| format!("{}: {}", f, e))),
            Err(e) => Some(Err(format!("{}: {}", f, e))),
        }
    }
}

/// ch.py's LEVEL_VERBS: the conditions that read the store when they are evaluated.
const LEVEL_VERBS: [&str; 9] = ["near", "away", "above", "below", "pos", "vel", "align", "around", "quiet"];

pub enum Step {
    Idle,
    Done(i64),
    Fire(J),
}

impl Core {
    pub fn new() -> Self {
        Core {
            docs: HashMap::new(), order: Vec::new(), at: HashMap::new(), triggers: Vec::new(),
            by_prop: HashMap::new(), always: HashSet::new(), seen: HashMap::new(), state: HashMap::new(),
            q: VecDeque::new(), dropped: 0, fired: HashMap::new(), refused: HashMap::new(), errors: HashMap::new(),
            seq: 0, learner: false, trace: None, cur: None, cache: expr::Cache::new(), ref_ix: Default::default(),
            #[cfg(feature = "python")]
            store: None,
        }
    }

    /// The eids a value points at: a string, or the strings in a list.
    pub fn targets(v: &J) -> Vec<String> {
        match v { J::String(s) => vec![s.clone()], J::Array(a) => a.iter().filter_map(|x| x.as_str().map(String::from)).collect(), _ => vec![] }
    }

    /// The entities whose `prop` holds `eid` (sorted): an index lookup after the prop's first use.
    pub fn refs(&self, eid: &str, prop: &str) -> Vec<String> {
        let mut ix = self.ref_ix.borrow_mut();
        let m = ix.entry(prop.to_string()).or_insert_with(|| {
            let mut m: HashMap<String, std::collections::BTreeSet<String>> = HashMap::new();
            for (k, e) in &self.docs {
                if let Some(v) = e.props.get(prop) { for t in Self::targets(v) { m.entry(t).or_default().insert(k.clone()); } }
            }
            m
        });
        m.get(eid).map(|s| s.iter().cloned().collect()).unwrap_or_default()
    }

    /// Keep the index current for one entity's prop going from `old` to `new`.
    fn reindex(&self, eid: &str, prop: &str, old: Option<&J>, new: Option<&J>) {
        let mut ix = self.ref_ix.borrow_mut();
        let Some(m) = ix.get_mut(prop) else { return };
        if let Some(o) = old { for t in Self::targets(o) { if let Some(s) = m.get_mut(&t) { s.remove(eid); } } }
        if let Some(n) = new { for t in Self::targets(n) { m.entry(t).or_default().insert(eid.to_string()); } }
    }

    fn reindex_entity(&self, eid: &str, old: Option<&Map<String, J>>, new: Option<&Map<String, J>>) {
        let props: Vec<String> = self.ref_ix.borrow().keys().cloned().collect();
        for p in props { self.reindex(eid, &p, old.and_then(|o| o.get(&p)), new.and_then(|n| n.get(&p))); }
    }

    // ---- the mirror ----------------------------------------------------------------------------
    pub fn load(&mut self, eid: String, cls: String, props: Map<String, J>, pos: Option<Vec<f64>>, now: f64) {
        if !self.at.contains_key(&eid) {
            self.at.insert(eid.clone(), self.order.len());
            self.order.push(Some(eid.clone()));
        }
        self.seen.insert(eid.clone(), now);
        let old = self.docs.get(&eid).map(|e| e.props.clone());
        self.reindex_entity(&eid, old.as_ref(), Some(&props));
        self.docs.insert(eid, Ent { cls, props, pos });
    }

    /// The live store's content for an entity, with no write behind it (a sweep re-reads what was
    /// changed in place); its `seen` time is untouched.
    pub fn refresh(&mut self, eid: String, cls: String, props: Option<Map<String, J>>, pos: Option<Vec<f64>>) {
        if !self.at.contains_key(&eid) {
            self.at.insert(eid.clone(), self.order.len());
            self.order.push(Some(eid.clone()));
        }
        // a position of one NaN: keep the mirror's (a sweep with no geometric trigger doesn't copy them)
        let keep_pos = matches!(&pos, Some(v) if v.len() == 1 && v[0].is_nan());
        match (self.docs.get_mut(&eid), props) {
            (Some(e), None) => { e.cls = cls; if !keep_pos { e.pos = pos; } }   // positions only (an `around` sweep)
            (Some(e), Some(props)) if keep_pos => { e.cls = cls; e.props = props; }
            (_, props) => { self.docs.insert(eid, Ent { cls, props: props.unwrap_or_default(), pos: if keep_pos { None } else { pos } }); }
        }
    }

    /// What a sweep can read: (every entity's position is needed -- an `around` trigger --, the entities
    /// the sweep's triggers can bind or name). Empty when no trigger is the sweep's.
    pub fn sweep_eids(&mut self, host: &dyn Host) -> (bool, bool, Vec<String>) {
        let mut memo = Memo::default();
        let mut out: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        let mut all = false;
        let mut geo = false;
        let specs: Vec<Spec> = self.triggers.iter().filter_map(|t| t.spec.clone())
            .filter(|sp| !sp.when.iter().any(|a| s(&a[0]) == "changed")
                // a `changed` trigger is not swept, but a condition beside it reads the store when its
                // write arrives; only a bare one has nothing here that a sweep must keep fresh
                || sp.when.iter().any(|a| LEVEL_VERBS.contains(&s(&a[0]).as_str()))
                || sp.wh.iter().any(|a| s(&a[0]) == "filter")).collect();
        for sp in &specs {
            if sp.when.iter().any(|a| s(&a[0]) == "around") { all = true; }
            if sp.when.iter().any(|a| matches!(s(&a[0]).as_str(), "near" | "away" | "pos" | "around")) { geo = true; }
            for a in &sp.wh {
                let kind = s(&a[0]);
                if kind == "isa" || kind == "is" || kind == "dispatches" {
                    for e in self.candidates(host, a, &mut memo) { if seen.insert(e.clone()) { out.push(e); } }
                }
            }
            for a in &sp.when {
                for x in a.iter().skip(1) {
                    if let J::String(r) = x {
                        if !r.starts_with('?') && s(&a[0]) != "above" && s(&a[0]) != "below" && s(&a[0]) != "every" {
                            if let Some(e) = self.lookup(host, r) { if seen.insert(e.clone()) { out.push(e); } }
                        }
                    }
                }
            }
        }
        (all, geo, out)
    }

    fn remove(&mut self, eid: &str) {
        if let Some(i) = self.at.remove(eid) { self.order[i] = None; }
        if let Some(e) = self.docs.remove(eid) { self.reindex_entity(eid, Some(&e.props), None); }
    }

    fn eids(&self) -> Vec<String> {
        self.order.iter().flatten().cloned().collect()
    }

    /// A write heard (the host's listener): the mirror follows it, and it is queued.
    /// `born`: the entity's content on a birth (cls, props, position).
    pub fn hear(&mut self, w: Write, born: Option<(String, Map<String, J>, Option<Vec<f64>>)>, now: f64) {
        if w.prop.is_empty() {
            if w.new.is_null() {
                self.remove(&w.eid);
                self.seen.remove(&w.eid);
                self.state.remove(&w.eid);
            } else if let Some((cls, props, pos)) = born {
                self.load(w.eid.clone(), cls, props, pos, now);
            }
        } else if self.docs.contains_key(&w.eid) {
            if w.prop != "position" {
                let old = self.docs.get(&w.eid).and_then(|e| e.props.get(&w.prop).cloned());
                self.reindex(&w.eid, &w.prop, old.as_ref(), if w.new.is_null() { None } else { Some(&w.new) });
            }
            let e = self.docs.get_mut(&w.eid).unwrap();
            if w.prop == "position" {
                e.pos = match &w.new { J::Array(a) => Some(a.iter().map(f).collect()), _ => None };
            } else if w.new.is_null() {
                e.props.remove(&w.prop);
            } else {
                e.props.insert(w.prop.clone(), w.new.clone());
            }
        }
        if !(w.prop.is_empty() && w.new.is_null()) && !w.writer.starts_with("trigger:") {
            self.seen.insert(w.eid.clone(), now);
        }
        if self.q.len() >= QUEUE_MAX {
            self.q.pop_front();
            self.dropped += 1;
        }
        self.q.push_back(w);
    }

    pub fn set_cls(&mut self, eid: &str, cls: String) {
        if let Some(e) = self.docs.get_mut(eid) { e.cls = cls; }
    }

    pub fn queued(&self) -> usize { self.q.len() }

    // ---- triggers ------------------------------------------------------------------------------
    pub fn set_triggers(&mut self, trs: Vec<Trig>) {
        self.by_prop.clear();
        self.always.clear();
        for t in &trs {
            let Some(sp) = &t.spec else { continue };
            let kinds: HashSet<String> = sp.when.iter().map(|a| s(&a[0])).collect();
            if kinds.contains("every") { continue; }
            if kinds.contains("changed") && !kinds.contains("pos") && !kinds.contains("around") {
                let a = sp.when.iter().find(|a| s(&a[0]) == "changed").unwrap();
                let p = rpart(&s(&a[1])).1.to_string();
                self.by_prop.entry(p).or_default().insert(t.eid.clone());
            } else {
                self.always.insert(t.eid.clone());
            }
        }
        self.triggers = trs;
    }

    pub fn forget_state(&mut self, eid: &str) { self.state.remove(eid); }

    fn relevant(&self, prop: &str, written: &str) -> Vec<usize> {
        let empty = HashSet::new();
        let want = self.by_prop.get(prop).unwrap_or(&empty);
        self.triggers.iter().enumerate()
            .filter(|(_, t)| (self.always.contains(&t.eid) || want.contains(&t.eid)) && t.eid != written)
            .map(|(i, _)| i).collect()
    }

    // ---- binding -------------------------------------------------------------------------------
    fn parents_of(props: &Map<String, J>) -> Vec<String> {
        match props.get("parents") {
            Some(J::Array(a)) => a.iter().filter(|x| !x.is_null() && x.as_str() != Some("")).map(s).collect(),
            _ => match props.get("parent") { Some(p) if !p.is_null() && p.as_str() != Some("") => vec![s(p)], _ => vec![] },
        }
    }

    fn isa(&self, host: &dyn Host, eid: &str, cls: &str, memo: &mut Memo) -> bool {
        let c = cls.to_lowercase();
        let key = (eid.to_string(), c.clone());
        if let Some(v) = memo.isa.get(&key) { return *v; }
        let Some(e) = self.docs.get(eid) else { return false };
        let mut ok = e.cls.to_lowercase() == c;
        if !ok {
            let suffix = format!(":{}", c);
            let mut seen: HashSet<String> = HashSet::from([eid.to_string()]);
            let mut queue: VecDeque<String> = Self::parents_of(&e.props).into();
            let mut hops = 0;
            while let Some(pid) = queue.pop_front() {
                if hops >= ANCESTOR_HOPS { break; }
                hops += 1;
                if seen.contains(&pid) { continue; }
                seen.insert(pid.clone());
                let aid = if self.docs.contains_key(&pid) { Some(pid.clone()) } else { host.resolve(&pid) };
                let Some(aid) = aid else { continue };
                let Some(anc) = self.docs.get(&aid) else { continue };
                let name = anc.props.get("name").map(|n| s(n).to_lowercase()).unwrap_or_default();
                if aid.to_lowercase().ends_with(&suffix) || name == c { ok = true; break; }
                queue.extend(Self::parents_of(&anc.props));
            }
        }
        memo.isa.insert(key, ok);
        ok
    }

    fn lookup(&self, host: &dyn Host, r: &str) -> Option<String> {
        if self.docs.contains_key(r) { return Some(r.to_string()); }
        host.resolve_exact(r)
    }

    fn dispatches(&self, host: &dyn Host, eid: &str, atom: &[J], memo: &mut Memo) -> bool {
        let key = (eid.to_string(), s(&atom[2]));
        let m = match memo.disp.get(&key) { Some(m) => m.clone(), None => { let m = host.dispatch(eid, &s(&atom[2])); memo.disp.insert(key, m.clone()); m } };
        m.as_deref() == Some(s(&atom[3]).as_str())
    }

    fn candidates(&self, host: &dyn Host, atom: &[J], memo: &mut Memo) -> Vec<String> {
        match s(&atom[0]).as_str() {
            "is" => self.lookup(host, &s(&atom[2])).into_iter().collect(),
            "dispatches" => self.eids().into_iter()
                .filter(|e| self.docs.get(e).map(|d| d.cls != CLASS).unwrap_or(false))
                .filter(|e| self.dispatches(host, e, atom, memo)).collect(),
            _ => {
                let k = s(&atom[2]).to_lowercase();
                if let Some(c) = memo.cands.get(&k) { return c.clone(); }
                let c: Vec<String> = self.eids().into_iter()
                    .filter(|e| self.docs.get(e).map(|d| d.cls != CLASS).unwrap_or(false))
                    .filter(|e| self.isa(host, e, &s(&atom[2]), memo)).collect();
                memo.cands.insert(k, c.clone());
                c
            }
        }
    }

    /// A field's read: one expression over named entities and extra values (fields.rs, #994 row 2).
    pub fn eval_json(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<J, String> {
        let mut env: HashMap<String, V> = vars.iter().map(|(k, e)| (k.to_string(), V::Ent(e.to_string()))).collect();
        for (k, v) in extra { env.insert(k.to_string(), expr::from_json(v)); }
        let v = self.eval(src, &env)?;
        expr::to_json(&v)
    }

    /// The engine's own built-ins that need the Core: `sql(query, args...)` (the store driver, store.rs). None: not one.
    pub fn native_call(&self, f: &str, a: &[V]) -> Option<Result<V, String>> {
        if f == "instances" {
            // every entity of a class (its own class, not its ancestors'), in id order, as entities
            let Some(V::Str(c)) = a.first() else { return Some(Err("instances(class): a class name".into())) };
            let mut ids: Vec<&String> = self.docs.iter().filter(|(_, e)| e.cls == *c).map(|(k, _)| k).collect();
            ids.sort();
            return Some(Ok(V::List(ids.into_iter().map(|k| V::Ent(k.clone())).collect())));
        }
        if f != "sql" && f != "jsonl" && f != "read" && f != "listdir" && f != "stat" { return None; }
        // a setter runs once, when something is written: it can't wait for a query's rows. A getter reads sql(...)
        // (it recomputes when the rows arrive) and the setter reads the getter's field.
        Some(Err("sql(...) is read in getters: a setter reads the getter's field".into()))
    }

    /// A getter's `sql(query, args...)`: the rows when they are in, None while the query runs (the getter is dirty
    /// again when they arrive: the read key returned). The query never runs under the engine's lock (store.rs).
    pub fn sql_request(&self, asker: &str, a: &[V]) -> (Result<V, String>, Option<String>) {
        let Some(V::Str(q)) = a.first() else { return (Err("sql(query, args...): the query is a string".into()), None) };
        let args = match a[1..].iter().map(expr::to_json).collect::<Result<Vec<_>, _>>() { Ok(v) => v, Err(e) => return (Err(e), None) };
        #[cfg(feature = "python")]
        {
            let Some(st) = &self.store else { return (Err("sql: no store attached to this field".into()), None) };
            let k = crate::store::scoped(asker, &crate::store::key(q, &args));
            match st.request(asker, q, &args) {
                crate::store::Poll::Ready(r) => (r.map(|rows| V::List(rows.iter().map(expr::from_json).collect())), Some(k)),
                crate::store::Poll::Pending => (Ok(V::None), Some(k)),
            }
        }
        #[cfg(not(feature = "python"))]
        { let _ = (asker, q, args); (Err("sql: this build has no store driver".into()), None) }
    }

    /// A slow host function (Host::call_later): asked like a query, on its own thread, never under the engine's lock.
    /// Each function has its own slot per getter (a getter may chain recall -> rerank); a newer call of the same
    /// function by the same getter drops the older one's answer. Without a store attached it runs in the round.
    pub fn host_request(&self, asker: &str, f: &str, args_json: &str, job: HostJob) -> (Result<V, String>, Option<String>) {
        let fname = f.to_string();
        let parse = move |s: String| serde_json::from_str::<J>(&s).map_err(|e| format!("{}: {}", fname, e));
        #[cfg(feature = "python")]
        {
            if let Some(st) = &self.store {
                let slot = format!("{}\u{0}{}", asker, f);
                let key = format!("\u{3}{}\u{0}{}", f, args_json);
                let k = crate::store::scoped(&slot, &key);
                let f2 = f.to_string();
                let run = Box::new(move || -> Result<Vec<J>, String> {
                    job().map_err(|e| format!("{}: {}", f2, e)).and_then(|s| parse(s).map(|j| vec![j]))
                });
                return match st.request_job(&slot, key, true, run) {
                    crate::store::Poll::Ready(r) => (r.map(|mut rows| rows.pop().map(|j| expr::from_json(&j)).unwrap_or(V::None)), Some(k)),
                    crate::store::Poll::Pending => (Ok(V::None), Some(k)),
                };
            }
        }
        let _ = asker;
        (job().map_err(|e| format!("{}: {}", f, e)).and_then(|s| parse(s)).map(|j| expr::from_json(&j)), None)
    }

    /// A getter's `jsonl(name)`: the seat's JSON-lines file, asked like a query.
    pub fn jsonl_request(&self, asker: &str, a: &[V]) -> (Result<V, String>, Option<String>) {
        let Some(V::Str(name)) = a.first() else { return (Err("jsonl(name): a file name".into()), None) };
        #[cfg(feature = "python")]
        {
            let Some(st) = &self.store else { return (Err("jsonl: no store attached to this field".into()), None) };
            let k = crate::store::scoped(asker, &crate::store::jsonl_key(name));
            match st.request_jsonl(asker, name) {
                crate::store::Poll::Ready(r) => (r.map(|rows| V::List(rows.iter().map(expr::from_json).collect())), Some(k)),
                crate::store::Poll::Pending => (Ok(V::None), Some(k)),
            }
        }
        #[cfg(not(feature = "python"))]
        { let _ = (asker, name); (Err("jsonl: this build has no store driver".into()), None) }
    }

    /// A getter's `read(path)` / `listdir(path)` / `stat(path)` (drivers.rs): the answer when it's in, None while it's
    /// read.
    pub fn fs_request(&self, asker: &str, f: &str, a: &[V]) -> (Result<V, String>, Option<String>) {
        let Some(V::Str(path)) = a.first() else { return (Err(format!("{}(path): a path", f)), None) };
        #[cfg(feature = "python")]
        {
            let Some(st) = &self.store else { return (Err(format!("{}: no drivers attached to this field", f)), None) };
            let k = crate::store::scoped(asker, &crate::drivers::key(st, f, path));
            match crate::drivers::request(st, asker, f, path) {
                crate::store::Poll::Ready(r) => (r.map(|mut rows| rows.pop().map(|j| expr::from_json(&j)).unwrap_or(V::None)), Some(k)),
                crate::store::Poll::Pending => (Ok(V::None), Some(k)),
            }
        }
        #[cfg(not(feature = "python"))]
        { let _ = (asker, path); (Err(format!("{}: this build has no drivers", f)), None) }
    }

    /// The drivers' effects' receipts since the last round: (entity, verb, receipt).
    pub fn effect_receipts(&self) -> Vec<(String, String, J)> {
        #[cfg(feature = "python")]
        { if let Some(st) = &self.store { return st.take_receipts(); } }
        vec![]
    }

    /// The entities the HTTP door pushed since the last round: (eid, class, properties).
    pub fn inbound(&self) -> Vec<(String, String, Map<String, J>)> {
        #[cfg(feature = "python")]
        { if let Some(st) = &self.store { return st.take_inbound(); } }
        vec![]
    }

    /// The queries whose rows arrived since the last round (their getters are dirty).
    pub fn sql_ready(&self) -> Vec<String> {
        #[cfg(feature = "python")]
        { if let Some(st) = &self.store { return st.take_ready(); } }
        vec![]
    }

    fn env_of(vars: &[(&str, &str)], extra: &[(&str, J)]) -> HashMap<String, V> {
        let mut env: HashMap<String, V> = vars.iter().map(|(k, e)| (k.to_string(), V::Ent(e.to_string()))).collect();
        for (k, v) in extra { env.insert(k.to_string(), expr::from_json(v)); }
        env
    }

    /// A FOR's list (#994): each item an entity (bound as one) or a value; None is the empty list.
    pub fn eval_items(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<Vec<Item>, String> {
        let env = Self::env_of(vars, extra);
        match self.eval(src, &env)? {
            V::List(l) => l.into_iter().map(|v| match v { V::Ent(e) => Ok(Item::Ent(e)), v => expr::to_json(&v).map(Item::Val) }).collect(),
            V::None => Ok(vec![]),
            other => Err(format!("not a list ({})", expr::to_json(&other).map(|j| j.to_string()).unwrap_or_else(|_| "an entity".into()))),
        }
    }

    /// A write's target: an entity, or the id of one (a string).
    pub fn eval_ref(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Option<String> {
        let env = Self::env_of(vars, extra);
        match self.eval(src, &env) { Ok(V::Ent(e)) | Ok(V::Str(e)) => Some(e), _ => None }
    }

    /// Is the entity of this class (its own, or an ancestor's)?
    pub fn is_a(&self, host: &dyn Host, eid: &str, cls: &str) -> bool {
        self.isa(host, eid, cls, &mut Memo::default())
    }

    fn env(b: &Binding) -> HashMap<String, V> {
        b.iter().map(|(k, e)| (k.clone(), V::Ent(e.clone()))).collect()
    }

    fn eval(&mut self, src: &str, env: &HashMap<String, V>) -> Result<V, String> {
        let parsed = self.cache.get(src);
        match &*parsed {
            Ok(node) => { let n: &Node = node; expr::eval(n, env, &View { core: self }) }
            Err(e) => Err(e.clone()),
        }
    }

    /// Evaluate and return what it read (#994 getters). `vars` bind names to entities, `extra` to values.
    pub fn eval_traced(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, serde_json::Value)], host: Option<&dyn Host>, asker: &str, scale: usize)
        -> (Result<serde_json::Value, String>, Vec<(String, String)>) {
        let mut env: HashMap<String, V> = vars.iter().map(|(k, e)| (k.to_string(), V::Ent(e.to_string()))).collect();
        for (k, v) in extra { env.insert(k.to_string(), expr::from_json(v)); }
        let parsed = self.cache.get_scaled(src, scale);
        let tv = Traced { inner: View { core: self }, reads: std::cell::RefCell::new(vec![]), host, asker };
        let r = match &*parsed {
            Ok(node) => { let n: &Node = node; expr::eval(n, &env, &tv).and_then(|v| expr::to_json(&v)) }
            Err(e) => Err(e.clone()),
        };
        let reads = tv.reads.into_inner();
        (r, reads)
    }

    fn bindings(&mut self, host: &dyn Host, sp: &Spec, memo: &mut Memo, fixed: Binding) -> Vec<Binding> {
        let mut out = vec![fixed];
        for a in &sp.wh {
            let kind = s(&a[0]);
            if kind != "isa" && kind != "is" && kind != "dispatches" { continue; }
            let Some(v) = var(&a[1]) else { continue };
            let mut nxt = Vec::new();
            for b in &out {
                if let Some(e) = bget(b, &v) {
                    if !self.docs.contains_key(e) { continue; }
                    let ok = match kind.as_str() {
                        "is" => self.candidates(host, a, memo).contains(e),
                        "dispatches" => self.dispatches(host, e, a, memo),
                        _ => self.isa(host, e, &s(&a[2]), memo),
                    };
                    if ok { nxt.push(b.clone()); }
                } else {
                    for e in self.candidates(host, a, memo) { nxt.push(bind_set(b, &v, &e)); }
                }
            }
            out = nxt;
        }
        let filters: Vec<String> = sp.wh.iter().filter(|a| s(&a[0]) == "filter").map(|a| s(&a[1])).collect();
        let mut res = Vec::new();
        'b: for b in out {
            let env = Self::env(&b);
            for fsrc in &filters {
                match self.eval(fsrc, &env) { Ok(v) if expr::truthy(&v) => {}, _ => continue 'b }
            }
            res.push(b);
        }
        res
    }

    fn vars_for(&self, host: &dyn Host, sp: &Spec, eid: &str, memo: &mut Memo) -> Vec<String> {
        let mut out = Vec::new();
        for a in &sp.wh {
            let kind = s(&a[0]);
            let hit = match kind.as_str() {
                "is" => self.lookup(host, &s(&a[2])).as_deref() == Some(eid),
                "isa" => self.isa(host, eid, &s(&a[2]), memo),
                "dispatches" => self.dispatches(host, eid, a, memo),
                _ => false,
            };
            if hit { if let Some(v) = var(&a[1]) { out.push(v); } }
        }
        out
    }

    fn rf(&self, host: &dyn Host, r: &J, b: &Binding) -> Option<String> {
        match var(r) { Some(v) => bget(b, &v).cloned(), None => self.lookup(host, &s(r)) }
    }

    fn pos(&self, eid: &Option<String>) -> Option<Vec<f64>> {
        eid.as_ref().and_then(|e| self.docs.get(e)).and_then(|d| d.pos.clone())
    }

    // ---- watchers ------------------------------------------------------------------------------
    fn level(&mut self, host: &dyn Host, a: &[J], b: &Binding, env: &HashMap<String, V>, st: &mut St, now: f64) -> Result<bool, String> {
        let verb = s(&a[0]);
        Ok(match verb.as_str() {
            "near" | "away" => {
                let (p, q) = (self.pos(&self.rf(host, &a[1], b)), self.pos(&self.rf(host, &a[2], b)));
                match (p, q) { (Some(p), Some(q)) => { let d = expr::dist(&p, &q); if verb == "near" { d <= f(&a[3]) } else { d >= f(&a[3]) } }, _ => false }
            }
            "above" | "below" => {
                let v = self.eval(&s(&a[1]), env)?;
                match v { V::Int(_) | V::Float(_) => { let x = match v { V::Int(i) => i as f64, V::Float(x) => x, _ => 0.0 }; if verb == "above" { x > f(&a[2]) } else { x < f(&a[2]) } }, _ => false }
            }
            "pos" => {
                let Some(p) = self.pos(&self.rf(host, &a[1], b)) else { return Ok(false) };
                match &st.stamp {
                    Some(stamp) if stamp.len() == p.len() => {
                        let d = p.iter().zip(stamp).map(|(x, y)| (x - y) * (x - y)).sum::<f64>().sqrt();
                        if d > f(&a[2]) { st.stamp = Some(p); true } else { false }
                    }
                    _ => { st.stamp = Some(p); false }
                }
            }
            "vel" => match self.rf(host, &a[1], b) { Some(e) if self.docs.contains_key(&e) => host.vel(&e, now, f(&a[2])), _ => false },
            "align" => match self.rf(host, &a[1], b) { Some(e) if self.docs.contains_key(&e) => host.align(&e, f(&a[2])), _ => false },
            "around" => {
                let me = self.rf(host, &a[1], b);
                let Some(p) = self.pos(&me) else { return Ok(false) };
                let z = f(&a[2]);
                let mut within: Vec<String> = self.eids().into_iter()
                    .filter(|e| Some(e) != me.as_ref())
                    .filter(|e| self.docs.get(e).and_then(|d| d.pos.as_ref()).map(|q| expr::dist(&p, q) <= z).unwrap_or(false))
                    .collect();
                within.sort();
                let changed = st.within.as_ref().map(|w| *w != within).unwrap_or(false);
                st.within = Some(within);
                changed
            }
            "quiet" => match self.rf(host, &a[1], b).and_then(|e| self.seen.get(&e).copied()) { Some(last) => now - last >= f(&a[2]), None => false },
            _ => false,
        })
    }

    /// _consider: Ok(Some(firing)) when the trigger fires on this binding.
    fn consider(&mut self, host: &dyn Host, ti: usize, b: &Binding, now: f64, event: Option<&Write>, chain: &J) -> Result<Option<J>, String> {
        let teid = self.triggers[ti].eid.clone();
        let sp = self.triggers[ti].spec.clone().unwrap();
        let key = bkey(b);
        let mut st = self.state.get(&teid).and_then(|m| m.get(&key)).cloned().unwrap_or_default();
        let mut env = Self::env(b);
        if let Some(w) = event {
            env.insert("new".into(), expr::from_json(&w.new));
            env.insert("old".into(), expr::from_json(&w.old));
        }
        let ev: Vec<&Vec<J>> = sp.when.iter().filter(|a| matches!(s(&a[0]).as_str(), "changed" | "every")).collect();
        let mut level = true;
        let mut failed = None;
        for a in sp.when.iter().filter(|a| !matches!(s(&a[0]).as_str(), "changed" | "every")) {
            match self.level(host, a, b, &env, &mut st, now) {
                Ok(x) => level = level && x,
                Err(e) => { failed = Some(e); break; }
            }
        }
        let save = |core: &mut Core, st: St| { core.state.entry(teid.clone()).or_default().insert(key.clone(), st); };
        if let Some(e) = failed { save(self, st); return Err(e); }
        if let Some(a) = ev.first() {
            if s(&a[0]) == "changed" {
                let Some(w) = event else { save(self, st); return Ok(None) };
                let (head, prop) = { let t = s(&a[1]); let (h, p) = rpart(&t); (h.to_string(), p.to_string()) };
                if Some(w.eid.clone()) != self.rf(host, &J::String(head), b) || w.prop != prop { save(self, st); return Ok(None); }
            } else {
                if event.is_some() { save(self, st); return Ok(None); }
                if let Some(last) = st.every { if now - last < f(&a[1]) { save(self, st); return Ok(None); } }
                st.every = Some(now);
            }
            save(self, st);
            return Ok(if level { self.fire(host, ti, &sp, b, env, now, chain) } else { None });
        }
        let seeded = st.lv.is_some();
        let was = st.lv.unwrap_or(false);
        st.lv = Some(level);
        save(self, st);
        Ok(if seeded && level && !was { self.fire(host, ti, &sp, b, env, now, chain) } else { None })
    }

    /// A `changed` trigger's firing: what its write expression names comes from the live store, as in
    /// ch.py. A sweep doesn't keep these fresh (that would cost every sweep), so the cost lands here, per
    /// firing, and only for the entities the expression names.
    fn read_live(&mut self, host: &dyn Host, sp: &Spec, b: &Binding) {
        let names: HashSet<&str> = sp.expr.split(|c: char| !(c.is_alphanumeric() || c == '_')).collect();
        let position = names.contains("position");
        for (v, e) in b {
            if !names.contains(v.trim_start_matches('?')) { continue; }
            let Some(row) = host.live(e, position).and_then(|t| serde_json::from_str::<J>(&t).ok()) else { continue };
            let (Some(cls), Some(props)) = (row[0].as_str(), row[1].as_object()) else { continue };
            let pos = if position { row[2].as_array().map(|a| a.iter().map(f).collect()) } else { Some(vec![f64::NAN]) };
            self.refresh(e.clone(), cls.to_string(), Some(props.clone()), pos);
        }
    }

    fn fire(&mut self, host: &dyn Host, ti: usize, sp: &Spec, b: &Binding, env: HashMap<String, V>, now: f64, chain: &J) -> Option<J> {
        let (teid, mine) = (self.triggers[ti].eid.clone(), self.triggers[ti].mine);
        if mine && !self.learner { return None; } // self's triggers run only with a learner (its energy)
        let via: Vec<String> = chain.get("via").and_then(|v| v.as_array()).map(|a| a.iter().map(s).collect()).unwrap_or_default();
        if let Some(last) = via.iter().rposition(|x| *x == teid) {
            if !via[last + 1..].iter().any(|x| x == "self") {
                *self.refused.entry(teid).or_insert(0) += 1;
                return None;
            }
        }
        let (head, prop) = { let (h, p) = rpart(&sp.target); (h.to_string(), p.to_string()) };
        let target = self.rf(host, &J::String(head), b);
        let ok = target.as_ref().and_then(|e| self.docs.get(e)).map(|d| d.cls != CLASS).unwrap_or(false);
        if !ok {
            self.errors.insert(teid, format!("no target {:?}", sp.target));
            return None;
        }
        if env.contains_key("new") { self.read_live(host, sp, b); }
        let value = match self.eval(&sp.expr, &env).and_then(|v| expr::to_json(&v)) {
            Ok(v) => v,
            Err(e) => { let m: String = format!("{:?}: {}", sp.expr, e).chars().take(200).collect(); self.errors.insert(teid, m); return None; }
        };
        let via2: Vec<String> = if mine {
            vec!["self".to_string()]          // before self nothing matters to the rule (ch.py keeps the last one)
        } else {
            let mut v = via;
            v.push(teid.clone());
            v
        };
        let base = if chain.is_object() { chain.clone() } else {
            self.seq += 1;
            json!({"id": format!("field-{}-{}-{}", teid.rsplit(':').next().unwrap_or(""), (now * 1000.0) as i64, self.seq - 1), "root": "field"})
        };
        let mut ch = base.as_object().cloned().unwrap_or_default();
        ch.insert("via".into(), J::Array(via2.into_iter().map(J::String).collect()));
        let src = bget(b, "x").cloned().or_else(|| b.first().map(|(_, e)| e.clone())).or_else(|| target.clone());
        Some(json!({"trigger": teid, "eid": target, "prop": prop, "value": value, "chain": J::Object(ch), "from": src, "expr": sp.expr, "mine": mine}))
    }

    // ---- running -------------------------------------------------------------------------------
    fn start_unit_list(&mut self, host: &dyn Host, cur: &mut Cur) -> bool {
        // the current trigger's units; false: nothing to do for it
        let ti = cur.trs[cur.ti];
        if cur.w.is_some() {
            if let Some(t) = self.trace.as_mut() { t.push(self.triggers[ti].eid.clone()); }   // ch.py: _on_write called
        }
        let Some(sp) = self.triggers[ti].spec.clone() else { return false };
        let kinds: HashSet<String> = sp.when.iter().map(|a| s(&a[0])).collect();
        cur.units.clear();
        cur.binds.clear();
        match &cur.w {
            None => {
                if kinds.contains("changed") { return false; }
                cur.event = false;
                cur.units.push_back(Unit::All);
            }
            Some(w) => {
                if kinds.contains("every") { return false; }
                cur.event = kinds.contains("changed");
                if cur.event {
                    let a = sp.when.iter().find(|a| s(&a[0]) == "changed").unwrap();
                    let t = s(&a[1]);
                    let head = rpart(&t).0.to_string();
                    if !head.starts_with('?') {
                        if self.lookup(host, &head).as_deref() == Some(w.eid.as_str()) { cur.units.push_back(Unit::All); }
                        return !cur.units.is_empty();
                    }
                }
                let eid = w.eid.clone();
                for v in self.vars_for(host, &sp, &eid, &mut cur.memo) { cur.units.push_back(Unit::Fixed(v)); }
            }
        }
        !cur.units.is_empty()
    }

    /// Run until the next firing (Fire), the end of a write (Done), or nothing queued (Idle).
    pub fn step(&mut self, host: &dyn Host, now: f64, fallback: &J) -> Step {
        loop {
            let mut cur = match self.cur.take() {
                Some(c) => c,
                None => {
                    let Some(w) = self.q.pop_front() else { return Step::Idle };
                    let ent_ok = self.docs.get(&w.eid).map(|d| d.cls != CLASS).unwrap_or(false);
                    let trs = if ent_ok { self.relevant(&w.prop, &w.eid) } else { vec![] };
                    // ch.py: W.resume(chain) -- a write without a chain runs in the caller's
                    let chain = if w.chain.get("id").map(|i| !i.is_null() && i.as_str() != Some("")).unwrap_or(false) { w.chain.clone() } else { fallback.clone() };
                    Cur { w: Some(w), chain, now, trs, ti: 0, units: VecDeque::new(), binds: VecDeque::new(), event: false, started: false, memo: Memo::default() }
                }
            };
            match self.advance(host, &mut cur) {
                Some(Ok(firing)) => { self.cur = Some(cur); return Step::Fire(firing); }
                Some(Err(())) => { self.cur = Some(cur); continue; }
                None => { if let Some(w) = &cur.w { return Step::Done(w.seq); } return Step::Idle; }
            }
        }
    }

    pub fn start_sweep(&mut self, now: f64, chain: J) {
        let trs: Vec<usize> = (0..self.triggers.len()).collect();
        self.cur = Some(Cur { w: None, chain, now, trs, ti: 0, units: VecDeque::new(), binds: VecDeque::new(), event: false, started: false, memo: Memo::default() });
    }

    pub fn busy(&self) -> bool { self.cur.is_some() }

    /// Some(Ok(firing)): stop here; Some(Err): keep going; None: this cursor is finished.
    fn advance(&mut self, host: &dyn Host, cur: &mut Cur) -> Option<Result<J, ()>> {
        loop {
            if cur.ti >= cur.trs.len() { return None; }
            if !cur.started {
                cur.started = true;
                if !self.start_unit_list(host, cur) { cur.ti += 1; cur.started = false; continue; }
            }
            if cur.binds.is_empty() {
                let Some(u) = cur.units.pop_front() else { cur.ti += 1; cur.started = false; continue };
                let ti = cur.trs[cur.ti];
                let sp = self.triggers[ti].spec.clone().unwrap();
                let fixed = match (&u, &cur.w) { (Unit::Fixed(v), Some(w)) => vec![(v.clone(), w.eid.clone())], _ => vec![] };
                let mut memo = std::mem::take(&mut cur.memo);
                let bs = self.bindings(host, &sp, &mut memo, fixed);
                cur.memo = memo;
                cur.binds = bs.into();
                continue;
            }
            let b = cur.binds.pop_front().unwrap();
            let ti = cur.trs[cur.ti];
            let w = if cur.event { cur.w.clone() } else { None };
            let chain = cur.chain.clone();
            match self.consider(host, ti, &b, cur.now, w.as_ref(), &chain) {
                Ok(Some(firing)) => return Some(Ok(firing)),
                Ok(None) => {}
                Err(e) => {
                    let teid = self.triggers[ti].eid.clone();
                    self.errors.insert(teid, e.chars().take(200).collect());
                    cur.units.clear();
                    cur.binds.clear();   // one trigger failing: its remaining bindings are skipped, as in ch.py
                }
            }
        }
    }

    /// The host failed applying a firing: like ch.py's `_one`, the trigger's remaining bindings for
    /// this write are skipped.
    pub fn skip_trigger(&mut self) {
        if let Some(c) = self.cur.as_mut() { c.units.clear(); c.binds.clear(); }
    }

    pub fn fired_ok(&mut self, teid: &str) { *self.fired.entry(teid.to_string()).or_insert(0) += 1; }
    pub fn error(&mut self, teid: &str, msg: String) { self.errors.insert(teid.to_string(), msg); }
}
