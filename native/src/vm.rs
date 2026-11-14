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
    /// the touch clock: the value of `Core::clock` at the entity's last read, write or load (RAM only, never stored; `.touched`)
    pub touched: std::cell::Cell<u64>,
}

/// How a class is stored (docs/cliffvm.md, "Storage modes"): its OWN class (the eid's), declared by the pack that owns it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Storage { Lazy, Virtual, Resident }
/// class -> storage mode, ONE table: `Fields::compose` writes it, the core reads it (`.pinned`).
pub type StorageMap = std::sync::Arc<std::sync::RwLock<HashMap<String, Storage>>>;

/// The class names of a `parents` value (`cls:<name>` entries, the name after the last colon): a list, or one entry.
pub fn parent_names(parents: &J) -> Vec<String> {
    let name = |p: &J| p.as_str().filter(|s| !s.is_empty()).map(|s| s.rsplit(':').next().unwrap_or("").to_string());
    match parents { J::Array(a) => a.iter().filter_map(name).collect(), p => name(p).into_iter().collect() }
}

/// A BIRTH with its own `parents`: the eid class is the birth default, so it goes first unless the list names it (#1143). No `parents`: nothing
/// is stored, the list is [the eid class]. After the birth the eid class has no standing: a `parents` write is the whole list.
pub fn birth_props(cls: &str, mut props: Map<String, J>) -> Map<String, J> {
    if let Some(ps) = props.get("parents") {
        let names = parent_names(ps);
        if !names.is_empty() && !cls.is_empty() && !names.iter().any(|n| n.eq_ignore_ascii_case(cls)) {
            let mut l = vec![J::String(format!("cls:{}", cls.to_lowercase()))];
            match ps { J::Array(a) => l.extend(a.iter().cloned()), p => l.push(p.clone()) }
            props.insert("parents".into(), J::Array(l));
        }
    }
    props
}

impl Ent {
    /// THE CLASS LIST (#1143): the entity's `parents`, nearest first; the eid class (`cls`) only when it has none (the birth default).
    /// The root `Entity` is implicitly last and is not in the list.
    pub fn class_names(&self) -> Vec<String> {
        let l = self.props.get("parents").map(parent_names).unwrap_or_default();
        if l.is_empty() { vec![self.cls.clone()] } else { l }
    }
    pub fn in_classes(&self, cls: &str) -> bool { self.class_names().iter().any(|c| c.eq_ignore_ascii_case(cls)) }
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

/// The VM-provided, read-only property of every entity: the epoch time of its last write (the host stamps it with the write, in the same row; `Core::updated`).
pub const UPDATED: &str = "updated";
/// The VM-provided, read-only property of every loaded entity: its place in the TOUCH CLOCK (a counter the core bumps at every read, write and load; RAM only,
/// never stored). The smaller it is, the colder the entity. Reading it (or `updated`, `eid`, `cls`, `pinned`) is no touch. EXCEPTION: an entity that STORES a property
/// named `touched` (the Python-era `Turn.touched`) reads its own: the clock does not shadow a stored value.
pub const TOUCHED: &str = "touched";
/// The VM-provided, read-only property that says why an entity cannot be unloaded: False when it can, else the reason (text, truthy).
pub const PINNED: &str = "pinned";
/// What a read of a collected virtual entity is: a null with this reason.
pub const COLLECTED: &str = "collected: nothing held it";
/// How many collected ids are remembered for that null (a threshold, flagged): older ones read as a plain missing entity.
pub const COLLECTED_KEPT: usize = 4096;

/// A stored row as the restore brings it back: its own class, the properties the engine takes back (a getter's value is computed again), its `updated`.
/// How many times one evaluation is done again after it loaded stored entities it looked for (a chain of references loads one link a time).
const FAULT_RETRIES: usize = 8;

pub struct StoredRow { pub cls: String, pub props: Map<String, J>, pub updated: f64 }
/// Reads one stored row by eid (the host's, from the store): how a LAZY entity loads when a setter writes it (docs/cliffvm.md, "Lazy load").
pub type Loader = Box<dyn Fn(&str) -> Option<StoredRow> + Send>;

pub struct Core {
    pub docs: HashMap<String, Ent>,
    /// the stored entities that are NOT loaded (a lazy class): eid -> its class. Loaded on first reference: a write loads the row at once (`Fields::ensure`), a read that
    /// finds nothing notes the eid in `wanted` and the host loads it between rounds (`Host::fault_wanted`); `instances` lists only `docs`.
    pub unloaded: HashMap<String, String>,
    pub wanted: std::cell::RefCell<std::collections::BTreeSet<String>>,
    pub loader: Option<Loader>,
    /// stored entities a read loaded in the middle of an evaluation (`fault_now`): in `docs` already, still to be registered with the field (`Fields::complete_arrivals`)
    pub arrived: Vec<String>,
    /// the time of the engine's last round: what a lazy load stamps as its `seen`
    pub last_now: f64,
    /// eid -> the time of its last write: what `<entity>.updated` reads (0 when it has none). Set by the host with each write; writing it is no write (no reaction, no recompute)
    pub updated: HashMap<String, f64>,
    /// the touch clock's counter (see `Ent::touched`) and the classes' storage modes (shared with `Fields`)
    pub clock: std::cell::Cell<u64>,
    pub storage: StorageMap,
    /// GARBAGE COLLECTION of VIRTUAL entities (docs/cliffvm.md): the reverse index of `{"$ref": id}` values (target -> holder -> how many), what each (holder, prop)
    /// holds, the live virtual entities, the ones to look at when the round ends (born, or just released), the owners whose in-flight work ended, the ones the host holds
    /// itself, and the recently collected (a read of one is a null with a reason)
    ref_in: HashMap<String, HashMap<String, u32>>,
    ref_of: HashMap<(String, String), Vec<String>>,
    ref_holders: HashMap<String, u32>,
    virt_live: std::collections::BTreeSet<String>,
    pub gc_candidates: std::cell::RefCell<std::collections::BTreeSet<String>>,
    pub gc_owners: std::cell::RefCell<std::collections::BTreeSet<String>>,
    pub host_held: HashSet<String>,
    collected: VecDeque<String>,
    collected_set: HashSet<String>,
    /// why a null getter value is null: (eid, prop) -> reason. The value itself is stored as a plain JSON null; reading it back through
    /// an expression gives `V::Nil(reason)` so the reason travels (fields.rs writes it, `why_chain` reads it)
    pub why: std::collections::BTreeMap<(String, String), String>,
    /// the (entity, property) whose `why` is an ERROR (a failure), not a null's note: reading it back gives `V::Error` (docs/c1f-error.md)
    pub failed: std::collections::BTreeSet<(String, String)>,
    /// (entity, property) a getter computed as a plain null (no reason): that null is held, not absent (`View::attr`)
    pub nulled: std::collections::BTreeSet<(String, String)>,
    /// the reasoned nulls inside the list or dict the last `eval_traced` returned (`expr::nested_nulls`), for the getter that stores it
    pub nested_last: Vec<(String, String)>,
    /// the locals a getter's block bound in its last evaluation: (eid, prop) -> name -> value (a null shows its reason). `Why` prints them.
    pub locals: std::collections::BTreeMap<(String, String), serde_json::Map<String, J>>,
    order: Vec<Option<String>>,
    at: HashMap<String, usize>,
    pub triggers: Vec<Trig>,
    by_prop: HashMap<String, HashSet<String>>,
    always: HashSet<String>,
    seen: HashMap<String, f64>,
    state: HashMap<String, HashMap<String, St>>,
    q: VecDeque<Write>,
    /// whether `hear` keeps each write in `q` for the trigger engine (`step`, read by the Python binding). A field host (host.rs) never reads it: it sets this false, or the ring
    /// would hold the last QUEUE_MAX writes with their whole `old` and `new` values for nothing (w28: ~1.6 GB live in a chunk burst).
    pub queue_writes: bool,
    pub dropped: u64,
    pub fired: HashMap<String, u64>,
    pub refused: HashMap<String, u64>,
    /// the store driver (`sql(...)`): the seat's SQLite store, read-only, this engine's own connection
    #[cfg(feature = "store")]
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

/// The ids a value holds as `{"$ref": id}` (anywhere in it): the reference form of an entity stored as a value (`V::Ent` is written that way).
pub fn collect_refs(v: &J, out: &mut Vec<String>) {
    match v {
        J::Object(m) => { if let Some(J::String(r)) = m.get("$ref") { out.push(r.clone()); } for x in m.values() { collect_refs(x, out); } }
        J::Array(a) => for x in a { collect_refs(x, out); },
        _ => {}
    }
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

/// What a getter's null value says: a null's note (`absent`, `pending`, `None('why')`) or a FAILURE (`Error`, docs/c1f-error.md). Both are stored as a plain
/// JSON null; the reason lives in `Core.why`, and an Error's kind in `Core.failed`.
#[derive(Clone, Debug, PartialEq)]
pub enum Note { Null(String), Error(String) }
impl Note { pub fn reason(&self) -> &str { match self { Note::Null(r) | Note::Error(r) => r } } pub fn is_error(&self) -> bool { matches!(self, Note::Error(_)) } }

struct View<'a> { core: &'a Core }

impl<'a> expr::World for View<'a> {
    fn attr(&self, eid: &str, attr: &str) -> V {
        let Some(e) = self.core.docs.get(eid) else {
            if self.core.unloaded.contains_key(eid) { self.core.wanted.borrow_mut().insert(eid.to_string()); }   // stored, not loaded: asked for (the host loads it between rounds)
            if self.core.collected_set.contains(eid) { return V::Nil(COLLECTED.to_string()); }   // a virtual entity nothing held any more: a null that says so
            return V::None;
        };
        match attr {
            "eid" => V::Str(eid.to_string()),
            // the first class of the list (the eid class when it is the first, as written there otherwise)
            "cls" => V::Str(match e.class_names().into_iter().next() { Some(c) if !c.eq_ignore_ascii_case(&e.cls) => c, _ => e.cls.clone() }),
            "position" => e.pos.as_ref().map(|p| V::List(p.iter().map(|x| V::Float(*x)).collect())).unwrap_or(V::None),
            UPDATED => V::Float(self.core.updated.get(eid).copied().unwrap_or(0.0)),   // provided by the VM: shadows a stored property of that name
            TOUCHED if !e.props.contains_key(TOUCHED) => V::Int(e.touched.get() as i64),   // likewise (reading it is no touch), unless the entity stores a property of that name (the Python-era `Turn.touched`): its own wins
            PINNED => match self.core.pinned_reason(eid) { Some(r) => V::Str(r), None => V::Bool(false) },
            _ => { e.touched.set(self.core.bump()); match e.props.get(attr) {
                Some(j) if !j.is_null() => { let mut v = expr::from_json(j); if matches!(v, V::List(_) | V::Dict(_)) { self.core.overlay_nested(eid, attr, &mut v); } v }
                // a getter that computed None (`nulled`) is a plain null; a property the entity never had says `absent`, naming both
                _ => self.core.why.get(&(eid.to_string(), attr.to_string())).map(|r| if self.core.failed.contains(&(eid.to_string(), attr.to_string())) { V::Error(r.clone()) } else { V::Nil(r.clone()) }).unwrap_or_else(|| if self.core.nulled.contains(&(eid.to_string(), attr.to_string())) { V::None } else { V::Nil(format!("absent: {eid}.{attr}")) }),
            } },
        }
    }
    fn refs(&self, eid: &str, prop: &str) -> Vec<String> { self.core.refs(eid, prop) }
    fn host(&self, f: &str, a: &[V]) -> Option<Result<V, String>> { self.core.native_call(f, a) }
    fn is_class(&self, eid: &str, cls: &str) -> bool {
        if !self.core.docs.contains_key(eid) && self.core.unloaded.contains_key(eid) { self.core.wanted.borrow_mut().insert(eid.to_string()); }
        crate::fields::entity_is(self.core, eid, cls)
    }
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
    /// `x is Class` reads the entity's `parents`: the getter that asked is dirty when they change
    fn is_class(&self, eid: &str, cls: &str) -> bool {
        self.reads.borrow_mut().push((eid.to_string(), "parents".to_string()));
        self.inner.is_class(eid, cls)
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
        match expr::waiting(|| h.call(f, &args_s))? {
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
            why: Default::default(), failed: Default::default(), nulled: Default::default(), nested_last: vec![], locals: Default::default(),
            docs: HashMap::new(), unloaded: HashMap::new(), wanted: Default::default(), loader: None, arrived: vec![], last_now: 0.0, updated: HashMap::new(), clock: Default::default(), storage: Default::default(), ref_in: Default::default(), ref_of: Default::default(), ref_holders: Default::default(), virt_live: Default::default(), gc_candidates: Default::default(), gc_owners: Default::default(), host_held: Default::default(), collected: Default::default(), collected_set: Default::default(), order: Vec::new(), at: HashMap::new(), triggers: Vec::new(),
            by_prop: HashMap::new(), always: HashSet::new(), seen: HashMap::new(), state: HashMap::new(),
            q: VecDeque::new(), queue_writes: true, dropped: 0, fired: HashMap::new(), refused: HashMap::new(), errors: HashMap::new(),
            seq: 0, learner: false, trace: None, cur: None, cache: expr::Cache::new(), ref_ix: Default::default(),
            #[cfg(feature = "store")]
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
        if let Some(o) = &old { for k in o.keys() { self.set_refs(&eid, k, None); } }
        for (k, v) in &props { self.set_refs(&eid, k, Some(v)); }
        if self.is_virtual_class(&cls) { self.virt_live.insert(eid.clone()); self.gc_candidates.borrow_mut().insert(eid.clone()); self.forget_collected(&eid); }
        let touched = std::cell::Cell::new(self.bump());
        self.docs.insert(eid, Ent { cls, props, pos, touched });
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
            (_, props) => { let touched = std::cell::Cell::new(self.bump());
                self.docs.insert(eid, Ent { cls, props: props.unwrap_or_default(), pos: if keep_pos { None } else { pos }, touched }); }
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
        self.updated.remove(eid);
        if let Some(e) = self.docs.remove(eid) {
            self.reindex_entity(eid, Some(&e.props), None);
            for k in e.props.keys() { self.set_refs(eid, k, None); }
        }
        self.virt_live.remove(eid);
        // the load order keeps a hole per removed entity: closed when they are more than the live ones, so load/unload cycles do not grow it (amortised O(1))
        if self.order.len() > 64 && self.order.len() > 2 * self.at.len() {
            self.order.retain(|o| o.is_some());
            for (i, o) in self.order.iter().enumerate() { if let Some(e) = o { self.at.insert(e.clone(), i); } }
        }
    }

    /// The next place in the touch clock.
    pub fn bump(&self) -> u64 { let t = self.clock.get() + 1; self.clock.set(t); t }

    // ---- garbage collection of virtual entities (docs/cliffvm.md, "Garbage collection") --------------------------------------------------------------------
    fn is_virtual_class(&self, cls: &str) -> bool { self.storage.read().unwrap_or_else(|x| x.into_inner()).get(cls).copied() == Some(Storage::Virtual) }

    /// Keep the reverse index of `$ref` values current for one (holder, prop): what it held is dropped (a virtual target that lost a holder is a candidate), what it
    /// holds now is added. Cheap when neither side has a reference (one lookup and a walk of the new value).
    fn set_refs(&mut self, holder: &str, prop: &str, new: Option<&J>) {
        let mut ts: Vec<String> = vec![];
        if let Some(n) = new { collect_refs(n, &mut ts); }
        if ts.is_empty() && !self.ref_holders.contains_key(holder) { return; }
        if let Some(old) = self.ref_of.remove(&(holder.to_string(), prop.to_string())) {
            if let Some(c) = self.ref_holders.get_mut(holder) { *c -= 1; if *c == 0 { self.ref_holders.remove(holder); } }
            for t in old {
                let Some(m) = self.ref_in.get_mut(&t) else { continue };
                if let Some(c) = m.get_mut(holder) { *c -= 1; if *c == 0 { m.remove(holder); } }
                if m.is_empty() { self.ref_in.remove(&t); }
                self.gc_candidates.borrow_mut().insert(t);   // whoever still holds it may be a loop nothing else supports: look again
            }
        }
        if ts.is_empty() { return; }
        for t in &ts { *self.ref_in.entry(t.clone()).or_default().entry(holder.to_string()).or_insert(0) += 1; }
        *self.ref_holders.entry(holder.to_string()).or_insert(0) += 1;
        self.ref_of.insert((holder.to_string(), prop.to_string()), ts);
    }

    fn forget_collected(&mut self, eid: &str) { self.collected_set.remove(eid); }

    /// Remember a collected entity: a read of it is a null with a reason (`COLLECTED`).
    pub fn note_collected(&mut self, eid: &str) {
        if self.collected_set.insert(eid.to_string()) { self.collected.push_back(eid.to_string()); }
        while self.collected.len() > COLLECTED_KEPT { if let Some(o) = self.collected.pop_front() { self.collected_set.remove(&o); } }
    }

    /// The owner an entity's id names when it is `<Class>:<owner id>:<n>` (a command's chunk: `Chunk:<command>:<n>`): what an in-flight effect on the owner holds.
    fn embedded_owner<'a>(&self, eid: &'a str, cls: &str) -> Option<&'a str> {
        let rest = eid.strip_prefix(cls)?.strip_prefix(':')?;
        rest.rsplit_once(':').map(|(o, _)| o).filter(|o| !o.is_empty())
    }

    /// Does an in-flight driver hold this virtual entity? A connection still open (a request not answered, a stream not closed), an effect on it whose receipt the
    /// engine has not taken, the listener it opened, an effect on its embedded owner (a running command's chunks), or the host itself (`host_held`).
    fn in_flight(&self, eid: &str, e: &Ent) -> bool {
        if self.host_held.contains(eid) { return true; }
        #[cfg(feature = "store")]
        if let Some(st) = &self.store {
            if st.holds(eid) { return true; }
            if let Some(n) = e.props.get("conn").and_then(|c| c.as_u64()) { if st.conn_held(n) { return true; } }
            if let Some(o) = self.embedded_owner(eid, &e.cls) { if st.holds(o) { return true; } }
        }
        #[cfg(not(feature = "store"))]
        { let _ = e; }
        false
    }

    /// Is this entity held? A stored (non-virtual) entity is a root. A virtual one is held by an in-flight driver, by its parent (a colon-path child lives while the
    /// parent entity does), or by a holder of its id that is held itself (`seen` ends a cycle: a loop of virtual entities that nothing else holds supports nobody).
    fn held(&self, v: &str, dead: &HashSet<String>, seen: &mut HashSet<String>) -> bool {
        if !seen.insert(v.to_string()) { return false; }
        let Some(e) = self.docs.get(v) else { return false };
        if !self.virt_live.contains(v) { return true; }
        if self.in_flight(v, e) { return true; }
        if let Some((p, _)) = v.rsplit_once(':') { if self.docs.contains_key(p) && !dead.contains(p) { return true; } }
        if let Some(m) = self.ref_in.get(v) {
            for h in m.keys() { if h != v && !dead.contains(h) && self.held(h, dead, seen) { return true; } }
        }
        false
    }

    /// THE COLLECTOR, at the end of a round: the virtual entities that nothing holds, found from the CANDIDATES only (born this round, or released: a holder gave
    /// the reference up, a holder was collected, an effect's receipt was taken). A dead entity releases what it held, so the pass runs on until nothing new dies.
    /// No scan of the field: the cost is the candidates times the length of the chains of holders they walk (usually one), plus one pass over the LIVE virtual
    /// entities when an effect ended (to find the entities that name it as their owner). -> the dead, in the order found; the caller removes them.
    pub fn gc_dead(&self) -> Vec<String> {
        let mut work: Vec<String> = std::mem::take(&mut *self.gc_candidates.borrow_mut()).into_iter().collect();
        let owners = std::mem::take(&mut *self.gc_owners.borrow_mut());
        if !owners.is_empty() {
            for v in &self.virt_live { if let Some(e) = self.docs.get(v) { if self.embedded_owner(v, &e.cls).map_or(false, |o| owners.contains(o)) { work.push(v.clone()); } } }
        }
        let (mut dead, mut order): (HashSet<String>, Vec<String>) = (HashSet::new(), vec![]);
        while let Some(v) = work.pop() {
            if dead.contains(&v) || !self.virt_live.contains(&v) { continue; }
            if self.held(&v, &dead, &mut HashSet::new()) { continue; }
            dead.insert(v.clone());
            order.push(v.clone());
            let mut ts = vec![];
            if let Some(e) = self.docs.get(&v) { for x in e.props.values() { collect_refs(x, &mut ts); } }
            work.extend(ts);                                                                         // what it held may be free now
            let below = format!("{v}:");
            work.extend(self.virt_live.range(below.clone()..).take_while(|c| c.starts_with(&below)).cloned());   // and so may its children
        }
        order
    }

    /// Does this entity hold a live VIRTUAL entity by a `$ref` value? (Unloading it would drop the reference, and the virtual entity would be collected.)
    pub fn holds_virtual(&self, eid: &str) -> bool {
        if !self.ref_holders.contains_key(eid) { return false; }
        let Some(e) = self.docs.get(eid) else { return false };
        e.props.keys().any(|k| self.ref_of.get(&(eid.to_string(), k.clone())).map_or(false, |ts| ts.iter().any(|t| self.virt_live.contains(t))))
    }

    /// Why this loaded entity cannot be unloaded, or None when it can: a VIRTUAL class (RAM only: no row to come back to), a RESIDENT class (always loaded: packs,
    /// drivers, whatever a pack lists whole), a running effect (`running` is set while a Command is in flight). What a LIVE virtual entity holds by `$ref` is the host's
    /// check (`Host::unload_entity`): it reads the virtual entities, which the core's table does not index.
    pub fn pinned_reason(&self, eid: &str) -> Option<String> {
        let e = self.docs.get(eid)?;
        match self.storage.read().unwrap_or_else(|x| x.into_inner()).get(&e.cls).copied().unwrap_or(Storage::Lazy) {
            Storage::Virtual => return Some(format!("pinned: class {} is virtual (RAM only, no stored row to load it back from)", e.cls)),
            Storage::Resident => return Some(format!("pinned: class {} is resident (always loaded)", e.cls)),
            Storage::Lazy => {}
        }
        if e.props.get("running").map_or(false, |v| !v.is_null()) { return Some("pinned: running (an effect is in flight)".into()); }
        if self.holds_virtual(eid) { return Some("pinned: holds a live virtual entity by a $ref".into()); }
        None
    }

    /// UNLOAD a loaded entity: it leaves the engine (its properties, its place in the load order, what the engine remembers about its last evaluations) and is noted in
    /// `unloaded` with its class, so the next reference loads it from its stored row. No value changes and nothing is written (the host's store is written through, so
    /// the row is already what the entity was). -> whether it was loaded.
    pub fn unload(&mut self, eid: &str) -> bool {
        let Some(cls) = self.docs.get(eid).map(|e| e.cls.clone()) else { return false };
        self.remove(eid);
        self.seen.remove(eid);
        self.state.remove(eid);
        let lo = (eid.to_string(), String::new());
        let keys: Vec<(String, String)> = self.why.range(lo.clone()..).take_while(|(k, _)| k.0 == eid).map(|(k, _)| k.clone()).collect();
        for k in keys { self.why.remove(&k); self.failed.remove(&k); }
        let keys: Vec<(String, String)> = self.nulled.range(lo.clone()..).take_while(|(e, _)| e == eid).cloned().collect();
        for k in keys { self.nulled.remove(&k); }
        let keys: Vec<(String, String)> = self.locals.range(lo..).take_while(|(k, _)| k.0 == eid).map(|(k, _)| k.clone()).collect();
        for k in keys { self.locals.remove(&k); }
        self.unloaded.insert(eid.to_string(), cls);
        true
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
            if let Some(e) = self.docs.get(&w.eid) { e.touched.set(self.bump()); }   // a write is a touch
            if w.prop != "position" { self.set_refs(&w.eid, &w.prop, if w.new.is_null() { None } else { Some(&w.new) }); }
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
        if !self.queue_writes { return; }
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
        let mut ok = e.in_classes(&c);   // the class list: a kind named there binds with no ISA parent entity
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
        match self.eval(src, &env)? { V::Error(r) => Err(r), v => expr::to_json(&v) }
    }

    /// The value of an expression as the evaluator holds it: an Error stays one (the caller decides: a write of it is refused).
    pub fn eval_value(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<V, String> {
        let env = Self::env_of(vars, extra);
        self.eval(src, &env)
    }

    /// Record why a getter's value is null (or that it is not): `why` the reason, `failed` whether it is a failure. Nested notes are the caller's.
    pub fn note_why(&mut self, key: &(String, String), note: Option<&Note>) {
        match note {
            Some(n) => { self.why.insert(key.clone(), n.reason().to_string()); if n.is_error() { self.failed.insert(key.clone()); } else { self.failed.remove(key); } }
            None => { self.why.remove(key); self.failed.remove(key); }
        }
    }

    /// The engine's own built-ins that need the Core: `sql(query, args...)` (the store driver, store.rs). None: not one.
    pub fn native_call(&self, f: &str, a: &[V]) -> Option<Result<V, String>> {
        if f == "instances" {
            // every entity of a class (its eid class, or a kind in its `parents` list; not its ancestors'), in id order, as entities; an entity with `scratch = true` (the scaffolding of
            // one door call, e.g. the clifflet's own packs) is not listed: a block sees the field as it was, not its own scaffolding
            let Some(V::Str(c)) = a.first() else { return Some(Err("instances(class): a class name".into())) };
            // `Entity` is the root every entity has: every LOADED entity (what the unload policy picks its coldest from)
            let mut ids: Vec<&String> = self.docs.iter().filter(|(_, e)| (c.eq_ignore_ascii_case("Entity") || e.cls == *c || e.in_classes(c)) && e.props.get("scratch") != Some(&serde_json::Value::Bool(true))).map(|(k, _)| k).collect();
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
        #[cfg(feature = "store")]
        {
            let Some(st) = &self.store else { return (Err("sql: no store attached to this field".into()), None) };
            let k = crate::store::scoped(asker, &crate::store::key(q, &args));
            match st.request(asker, q, &args) {
                crate::store::Poll::Ready(r) => (r.map(|rows| V::List(rows.iter().map(expr::from_json).collect())), Some(k)),
                crate::store::Poll::Pending => (Ok(V::Nil(format!("pending: sql({})", q.chars().take(48).collect::<String>()))), Some(k)),
            }
        }
        #[cfg(not(feature = "store"))]
        { let _ = (asker, q, args); (Err("sql: this build has no store driver".into()), None) }
    }

    /// A slow host function (Host::call_later): asked like a query, on its own thread, never under the engine's lock.
    /// Each function has its own slot per getter (a getter may chain recall -> rerank); a newer call of the same
    /// function by the same getter drops the older one's answer. Without a store attached it runs in the round.
    pub fn host_request(&self, asker: &str, f: &str, args_json: &str, job: HostJob) -> (Result<V, String>, Option<String>) {
        let fname = f.to_string();
        let parse = move |s: String| serde_json::from_str::<J>(&s).map_err(|e| format!("{}: {}", fname, e));
        #[cfg(feature = "store")]
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
                    crate::store::Poll::Pending => (Ok(V::Nil(format!("pending: {}(...)", f))), Some(k)),
                };
            }
        }
        let _ = asker;
        (job().map_err(|e| format!("{}: {}", f, e)).and_then(|s| parse(s)).map(|j| expr::from_json(&j)), None)
    }

    /// A getter's `jsonl(name)`: the seat's JSON-lines file, asked like a query.
    pub fn jsonl_request(&self, asker: &str, a: &[V]) -> (Result<V, String>, Option<String>) {
        let Some(V::Str(name)) = a.first() else { return (Err("jsonl(name): a file name".into()), None) };
        #[cfg(feature = "store")]
        {
            let Some(st) = &self.store else { return (Err("jsonl: no store attached to this field".into()), None) };
            let k = crate::store::scoped(asker, &crate::store::jsonl_key(name));
            match st.request_jsonl(asker, name) {
                crate::store::Poll::Ready(r) => (r.map(|rows| V::List(rows.iter().map(expr::from_json).collect())), Some(k)),
                crate::store::Poll::Pending => (Ok(V::Nil(format!("pending: jsonl({name})"))), Some(k)),
            }
        }
        #[cfg(not(feature = "store"))]
        { let _ = (asker, name); (Err("jsonl: this build has no store driver".into()), None) }
    }

    /// A getter's `read(path)` / `listdir(path)` / `stat(path)` (drivers.rs): the answer when it's in, None while it's
    /// read.
    pub fn fs_request(&self, asker: &str, f: &str, a: &[V]) -> (Result<V, String>, Option<String>) {
        let Some(V::Str(path)) = a.first() else { return (Err(format!("{}(path): a path", f)), None) };
        #[cfg(feature = "store")]
        {
            let Some(st) = &self.store else { return (Err(format!("{}: no drivers attached to this field", f)), None) };
            let k = crate::store::scoped(asker, &crate::drivers::key(st, f, path));
            match crate::drivers::request(st, asker, f, path) {
                crate::store::Poll::Ready(r) => (r.map(|mut rows| rows.pop().map(|j| expr::from_json(&j)).unwrap_or(V::None)), Some(k)),
                crate::store::Poll::Pending => (Ok(V::Nil(format!("pending: {}({})", f, path))), Some(k)),
            }
        }
        #[cfg(not(feature = "store"))]
        { let _ = (asker, path); (Err(format!("{}: this build has no drivers", f)), None) }
    }

    /// The drivers' effects' receipts since the last round: (entity, verb, receipt).
    pub fn effect_receipts(&self) -> Vec<(String, String, J)> {
        #[cfg(feature = "store")]
        { if let Some(st) = &self.store {
            let r = st.take_receipts();
            // an effect's work on an entity is over: it may be garbage now, and so may what only it held (a command's chunks)
            for (eid, _, _) in &r { self.gc_candidates.borrow_mut().insert(eid.clone()); self.gc_owners.borrow_mut().insert(eid.clone()); }
            return r;
        } }
        vec![]
    }

    /// The entities the HTTP door pushed since the last round: (eid, class, properties).
    pub fn inbound(&self) -> Vec<(String, String, Map<String, J>)> {
        #[cfg(feature = "store")]
        { if let Some(st) = &self.store { return st.take_inbound(); } }
        vec![]
    }

    /// The queries whose rows arrived since the last round (their getters are dirty).
    pub fn sql_ready(&self) -> Vec<String> {
        #[cfg(feature = "store")]
        { if let Some(st) = &self.store { return st.take_ready(); } }
        vec![]
    }

    fn env_of(vars: &[(&str, &str)], extra: &[(&str, J)]) -> HashMap<String, V> {
        let mut env: HashMap<String, V> = vars.iter().map(|(k, e)| (k.to_string(), V::Ent(e.to_string()))).collect();
        for (k, v) in extra { env.insert(k.to_string(), expr::from_json(v)); }
        env
    }

    /// The key a nested reasoned null of (eid, prop) is remembered under in `why`: the property, a NUL, the path.
    fn nested_key(prop: &str, path: &str) -> String { format!("{prop}\u{0}{path}") }
    /// A getter stored a list or a dict: remember the reasoned nulls inside it (JSON keeps them plain), replacing what the last value had.
    pub fn set_nested(&mut self, eid: &str, prop: &str, nulls: Vec<(String, String)>) {
        let lo = (eid.to_string(), Self::nested_key(prop, ""));
        let old: Vec<(String, String)> = self.why.range(lo..).take_while(|(k, _)| k.0 == eid && k.1.starts_with(&format!("{prop}\u{0}"))).map(|(k, _)| k.clone()).collect();
        for k in old { self.why.remove(&k); }
        for (path, r) in nulls { self.why.insert((eid.to_string(), Self::nested_key(prop, &path)), r); }
    }
    /// A read of a stored list or dict: the reasoned nulls inside it come back.
    fn overlay_nested(&self, eid: &str, prop: &str, v: &mut V) {
        let pre = format!("{prop}\u{0}");
        let lo = (eid.to_string(), pre.clone());
        let nulls: Vec<(String, String)> = self.why.range(lo..).take_while(|(k, _)| k.0 == eid && k.1.starts_with(&pre)).map(|(k, r)| (k.1[pre.len()..].to_string(), r.clone())).collect();
        if !nulls.is_empty() { expr::overlay_nulls(v, &nulls); }
    }

    /// A FOR's list (#994): each item an entity (bound as one) or a value; None is the empty list.
    pub fn eval_items(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<Vec<Item>, String> {
        let env = Self::env_of(vars, extra);
        match self.eval(src, &env)? {
            V::List(l) => l.into_iter().map(|v| match v { V::Ent(e) => Ok(Item::Ent(e)), v => expr::to_json(&v).map(Item::Val) }).collect(),
            V::None | V::Nil(_) => Ok(vec![]),
            V::Error(r) => Err(r),
            other => Err(format!("not a list ({})", expr::to_json(&other).map(|j| j.to_string()).unwrap_or_else(|_| "an entity".into()))),
        }
    }

    /// A setter local's value: an entity (bound as one) or a value (a null stays null).
    pub fn eval_item(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<Item, String> {
        let env = Self::env_of(vars, extra);
        match self.eval(src, &env)? { V::Ent(e) => Ok(Item::Ent(e)), V::Error(r) => Err(r), v => expr::to_json(&v).map(Item::Val) }
    }

    /// A write's target: an entity, or the id of one (a string).
    pub fn eval_ref(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Option<String> {
        self.eval_target(src, vars, extra).ok()
    }

    /// `eval_ref`, with the reason when there is no entity (a null key's own reason, an error).
    pub fn eval_target(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<String, String> {
        let env = Self::env_of(vars, extra);
        match self.eval(src, &env) {
            Ok(V::Ent(e)) | Ok(V::Str(e)) => Ok(e),
            Ok(V::Nil(r)) | Ok(V::Error(r)) => Err(r),
            Ok(V::None) => Err("error: the target is null".into()),
            Ok(_) => Err("error: the target of a write is an entity or the id of one".into()),
            Err(e) => Err(format!("error: {}", e)),
        }
    }

    /// A constructor `Class(name: value, ...)`: its class and its arguments' values (a null argument is `null`).
    pub fn eval_ctor(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, J)]) -> Result<(String, Vec<(String, J)>), String> {
        let env = Self::env_of(vars, extra);
        let (cls, args) = expr::parse_ctor(src)?;
        let mut out = vec![];
        for (name, node) in &args {
            let v = expr::eval(node, &env, &View { core: self }).map_err(|e| format!("{}({}: ...): {}", cls, name, e))?;
            if let V::Error(r) = &v { return Err(format!("refused: {}({}: ...) is an Error ({}); an Error is never stored", cls, name, r)); }
            out.push((name.clone(), expr::to_json(&v).map_err(|e| format!("{}({}: ...): {}", cls, name, e))?));
        }
        Ok((cls, out))
    }

    /// Is the entity of this class (its own, or an ancestor's)?
    pub fn is_a(&self, host: &dyn Host, eid: &str, cls: &str) -> bool {
        self.isa(host, eid, cls, &mut Memo::default())
    }

    fn env(b: &Binding) -> HashMap<String, V> {
        b.iter().map(|(k, e)| (k.clone(), V::Ent(e.clone()))).collect()
    }

    fn eval(&mut self, src: &str, env: &HashMap<String, V>) -> Result<V, String> {
        for _ in 0..FAULT_RETRIES {
            let r = {
                let parsed = self.cache.get(src);
                match &*parsed {
                    Ok(node) => { let n: &Node = node; expr::eval(n, env, &View { core: self }) }
                    Err(e) => Err(e.clone()),
                }
            };
            if !self.fault_now() { return r; }   // the read found nothing but the store has it: it is loaded now, and the evaluation is done again on it
        }
        Err("a read keeps asking for stored entities that are not loaded".into())
    }

    /// Entities an evaluation looked for and did not find, though the store has them (`wanted`): load them NOW (a lazy class loads on first reference), as the store holds
    /// them (a getter's stored value too: the read is right away, and the getters of the entity recompute once it is registered, `Fields::complete_arrivals`). -> whether any loaded.
    pub fn fault_now(&mut self) -> bool {
        if self.wanted.borrow().is_empty() { return false; }
        let wanted: Vec<String> = std::mem::take(&mut *self.wanted.borrow_mut()).into_iter().collect();
        let mut any = false;
        for e in wanted { any |= self.fault_one(&e); }
        any
    }

    /// Load one stored entity that is not loaded: its row into `docs`, noted in `arrived` for the field to register. -> whether it loaded.
    pub fn fault_one(&mut self, eid: &str) -> bool {
        if self.unloaded.remove(eid).is_none() { return false; }
        let Some(r) = self.loader.as_ref().and_then(|l| l(eid)) else { return false };
        self.updated.insert(eid.to_string(), r.updated);
        let now = self.last_now;
        self.load(eid.to_string(), r.cls, r.props, None, now);
        self.arrived.push(eid.to_string());
        true
    }

    /// Evaluate and return what it read (#994 getters). `vars` bind names to entities, `extra` to values.
    pub fn eval_traced(&mut self, src: &str, vars: &[(&str, &str)], extra: &[(&str, serde_json::Value)], host: Option<&dyn Host>, asker: &str)
        -> (Result<serde_json::Value, String>, Vec<(String, String)>, Option<Note>, Vec<(String, V)>) {
        let mut env: HashMap<String, V> = vars.iter().map(|(k, e)| (k.to_string(), V::Ent(e.to_string()))).collect();
        for (k, v) in extra { env.insert(k.to_string(), expr::from_json(v)); }
        let mut tries = 0;
        loop {
            let (r, reads, nil, locals) = {
                let parsed = self.cache.get(src);
                let tv = Traced { inner: View { core: self }, reads: std::cell::RefCell::new(vec![]), host, asker };
                let mut nil: Option<Note> = None;
                let mut nested = vec![];
                let mut locals = vec![];
                let r = match &*parsed {
                    Ok(node) => {
                        let n: &Node = node;
                        let (r, l) = expr::eval_locals(n, &env, &tv);
                        locals = l;
                        r.and_then(|v| { match &v { V::Nil(why) => nil = Some(Note::Null(why.clone())), V::Error(why) => nil = Some(Note::Error(why.clone())), _ => {} } nested = expr::nested_nulls(&v); expr::to_json(&v) })
                    }
                    Err(e) => Err(e.clone()),
                };
                let reads = tv.reads.into_inner();
                self.nested_last = nested;
                (r, reads, nil, locals)
            };
            tries += 1;
            if tries >= FAULT_RETRIES || !self.fault_now() { return (r, reads, nil, locals); }   // loaded what it looked for: once more, on it
        }
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

#[cfg(feature = "alloc-stats")]
impl Core {
    /// The memory breakdown (alloc-stats): the entities' properties in total and by class, and the sizes of the engine's side tables.
    pub fn mem_sizes(&self) -> Vec<crate::alloc_stats::Size> {
        use crate::alloc_stats::{jsize, Size};
        let mut v: Vec<Size> = vec![];
        let mut by: HashMap<String, (u64, u64)> = HashMap::new();
        let (mut n, mut bytes) = (0u64, 0u64);
        for e in self.docs.values() {
            let b: u64 = e.props.iter().map(|(k, x)| k.len() as u64 + 56 + jsize(x)).sum::<u64>() + e.cls.len() as u64 + 120;
            n += 1; bytes += b;
            let t = by.entry(e.cls.clone()).or_default(); t.0 += 1; t.1 += b;
        }
        v.push(("core.docs".into(), n, bytes));
        let mut top: Vec<_> = by.into_iter().collect();
        top.sort_by(|a, b| b.1 .1.cmp(&a.1 .1));
        for (c, (n, b)) in top.into_iter().take(6) { v.push((format!("core.docs[{c}]"), n, b)); }
        v.push(("core.unloaded".into(), self.unloaded.len() as u64, self.unloaded.iter().map(|(k, c)| (k.len() + c.len() + 64) as u64).sum()));
        v.push(("core.why".into(), self.why.len() as u64, self.why.iter().map(|((a, b), r)| (a.len() + b.len() + r.len() + 96) as u64).sum()));
        v.push(("core.locals".into(), self.locals.len() as u64, self.locals.iter().map(|((a, b), m)| (a.len() + b.len() + 96) as u64 + m.iter().map(|(k, x)| k.len() as u64 + 56 + jsize(x)).sum::<u64>()).sum()));
        v.push(("core.ref_in".into(), self.ref_in.len() as u64, self.ref_in.iter().map(|(k, m)| k.len() as u64 + 64 + m.iter().map(|(h, _)| h.len() as u64 + 40).sum::<u64>()).sum()));
        v.push(("core.ref_of".into(), self.ref_of.len() as u64, self.ref_of.iter().map(|((a, b), l)| (a.len() + b.len() + 64) as u64 + l.iter().map(|s| s.len() as u64 + 24).sum::<u64>()).sum()));
        v.push(("core.ref_ix(referencing)".into(), self.ref_ix.borrow().len() as u64, self.ref_ix.borrow().values().map(|m| m.iter().map(|(k, s)| k.len() as u64 + 64 + s.iter().map(|x| x.len() as u64 + 48).sum::<u64>()).sum::<u64>()).sum()));
        v.push(("core.virt_live".into(), self.virt_live.len() as u64, self.virt_live.iter().map(|s| s.len() as u64 + 48).sum()));
        v.push(("core.expr_cache".into(), self.cache.len() as u64, 0));
        v
    }
}
