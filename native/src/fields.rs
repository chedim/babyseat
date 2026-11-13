//! #994 row 2: goalgraph's 12 triggers (+ the wait tick) as FIELDS, native VM only (doc section 5).
//! A field is a getter (a value that is a pure function of the field) or an on-setter (WHEN the
//! prop is written and a condition holds, write other props). Counting is a computed getter
//! (`open_blockers`), not decrement-on-event: idempotent and replay-safe. The getter's value is
//! materialised onto the entity (written when it changes), so the whole-field checksum sees it.
//! OFF by default: nothing in lib.rs or the seat wires this in; the parity gate builds it.
use std::collections::VecDeque;

use serde_json::{json, Value as J};

use crate::vm::{Core, Host, Write};

const BLOCKER_CLASSES: [&str; 3] = ["Task", "Decision", "Wait"];

/// finished(b): Task/Wait -> status 'done'; Decision -> answered: decided, in_flight, closed or dropped (#1139; doc section 5).
const OPEN_BLOCKERS_FILTER: &str = "t.eid in (b.blocks or []) and not ((b.cls == 'Decision' and b.status in ['decided', 'in_flight', 'closed', 'dropped']) or (b.cls != 'Decision' and b.status == 'done'))";

/// `this` is the entity written (`x` is the old name, still bound for migrated scripts); `clock` the time.
/// Each write: (target entity expr, prop, value expr). The first branch is `WHEN when: writes`; `els` are the
/// `ELSE WHEN` branches in order, a plain `ELSE` being the condition `True`. The first true branch runs, only it.
/// Written in cliffhanger's text form (`syntax.rs`, packs/goalgraph.c1f).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct OnSet {
    pub name: String,
    pub cls: String,
    pub prop: String,
    pub when: String,
    pub writes: Vec<(String, String, String)>,
    pub els: Vec<(String, Vec<(String, String, String)>)>,
    /// the pack it came from ("" for one built outside a pack: no class checks)
    pub pack: String,
}

/// The names a setter's expressions use for the entity written: `this`, and `x` for scripts written before the rename.
fn me(eid: &str) -> [(&'static str, &str); 2] { [("this", eid), ("x", eid)] }
fn is_me(target: &str) -> bool { target == "this" || target == "x" }

/// The names a write sees: `this` and the loop names bound to entities; `clock` and the loop names bound to values.
fn scope<'a>(eid: &'a str, binds: &'a [(String, crate::vm::Item)], now: f64) -> (Vec<(&'a str, &'a str)>, Vec<(&'a str, J)>) {
    let mut vars: Vec<(&str, &str)> = me(eid).to_vec();
    let mut extra: Vec<(&str, J)> = vec![("clock", json!(now))];
    for (k, it) in binds {
        match it { crate::vm::Item::Ent(e) => vars.push((k.as_str(), e.as_str())), crate::vm::Item::Val(v) => extra.push((k.as_str(), v.clone())) }
    }
    (vars, extra)
}


pub const LLM_CALL: &str = "membrane::llm.call";

/// The entities a getter of `cls` applies to: that class, or an entity with it among its ISA parents.
fn of_kind(core: &Core, cls: &str) -> Vec<String> {
    core.docs.iter().filter(|(k, e)| e.cls == cls || has_kind(core, k, &e.props, cls)).map(|(k, _)| k.clone()).collect()
}

/// A kind among an entity's ISA parents: named by the parent's id (`cls:chatthing` is ChatThing, mirrored or not),
/// or found up the chain (core.is_a).
fn has_kind(core: &Core, eid: &str, props: &serde_json::Map<String, J>, cls: &str) -> bool {
    let Some(ps) = props.get("parents").filter(|p| !p.is_null()) else { return false };
    let named = |p: &J| p.as_str().map(|s| s.rsplit(':').next().unwrap_or("").eq_ignore_ascii_case(cls)).unwrap_or(false);
    let direct = match ps { J::Array(a) => a.iter().any(named), p => named(p) };
    direct || core.is_a(&NoHost, eid, cls)
}

/// No lookups: what ISA needs is on the entities (getters_of runs where no host is at hand).
struct NoHost;
impl Host for NoHost {
    fn resolve(&self, _: &str) -> Option<String> { None }
    fn resolve_exact(&self, _: &str) -> Option<String> { None }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

/// The recorded kills (a getter evaluation that the self-time allowance stopped): `Trip:<eid>#<prop>` entities.
const TRIP_CLASS: &str = "Trip";
const TRIP_PREFIX: &str = "Trip:";
fn trip_eid(eid: &str, prop: &str) -> String { format!("{TRIP_PREFIX}{eid}#{prop}") }
/// The null's reason a Trip entity stands for, in the words of the live trip.
fn trip_reason(core: &Core, trip: &str) -> String {
    let n = |p: &str| core.docs.get(trip).and_then(|t| t.props.get(p)).and_then(|v| v.as_u64()).unwrap_or(0);
    format!("error: {}", crate::expr::timeout_reason(n("self_ms"), n("allowance_ms")))
}

/// writer prefix of an own-name store (rule 6): it fires getters but not setters
const DIRECT: &str = "field:direct:";

pub fn on_setters() -> Vec<OnSet> { crate::translate::goalgraph_setters() }

/// A getter declared in cliffhanger (`<Class>.<field> getter: <expr>`, #994 rule 2): the field's value is the
/// expression, recalculated when something it read changes. `this` is the entity; `clock` the time.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Getter {
    pub cls: String, pub prop: String, pub expr: String,
    /// how many written arms were folded into `expr` (informational)
    pub arms: usize,
}

/// The input key of a getter that reads `clock`.
const CLOCK: &str = "\u{0}clock";

/// Getter levels: a getter that reads another getter's field sits above it, so a round computes lower levels first. A cycle
/// keeps the level it reached (its closing edge reads the previous value, rule 8).
///
/// Which getters a read lands on is class-aware (the same expression types `infer.rs` uses): `this.n` reads `n` of THIS getter's
/// class; `x.n` / `g.n` / `prop(x, 'n')` read `n` of x's class when it is known (`this`, a loop variable over `instances('X')`
/// or over a getter of known element type, a constructor, an entity literal). Where the class is unknown the read is
/// conservative (every getter of that name), but such an edge is dropped when it would close a cycle that the known edges do
/// not: a name collision between unrelated classes can never order a getter after what depends on it. Strings in dict
/// literals and list elements are data, not reads.
pub fn levels(gs: &[Getter]) -> Vec<usize> {
    let n = gs.len();
    let mut by_key: std::collections::HashMap<(&str, &str), Vec<usize>> = Default::default();
    let mut by_name: std::collections::HashMap<&str, Vec<usize>> = Default::default();
    for (i, g) in gs.iter().enumerate() { by_key.entry((&g.cls, &g.prop)).or_default().push(i); by_name.entry(&g.prop).or_default().push(i); }
    let triples: Vec<(&str, &str, &str)> = gs.iter().map(|g| (g.cls.as_str(), g.prop.as_str(), g.expr.as_str())).collect();
    let reads = crate::infer::getter_reads(&triples);
    // known edges first, then the conservative ones that close no cycle
    let mut adj: Vec<Vec<usize>> = vec![vec![]; n];
    let mut loose: Vec<(usize, usize)> = vec![];
    for i in 0..n {
        let rs = reads[i].clone().unwrap_or_else(|| text_reads(&gs[i].expr));
        for r in rs {
            match &r.cls {
                Some(c) => for &j in by_key.get(&(c.as_str(), r.name.as_str())).into_iter().flatten() {
                    if !(gs[j].cls == gs[i].cls && gs[j].prop == gs[i].prop) { adj[i].push(j); }
                },
                None => for &j in by_name.get(r.name.as_str()).into_iter().flatten() {
                    if gs[j].prop != gs[i].prop { loose.push((i, j)); }
                },
            }
        }
    }
    for l in adj.iter_mut() { l.sort_unstable(); l.dedup(); }
    loose.sort_unstable(); loose.dedup();
    let mut stamp = vec![0usize; n];
    let mut tick = 0usize;
    for (i, j) in loose {
        if adj[i].contains(&j) { continue; }
        tick += 1;
        let mut todo = vec![j];
        let mut closes = false;
        while let Some(x) = todo.pop() {
            if x == i { closes = true; break; }
            if stamp[x] == tick { continue; }
            stamp[x] = tick;
            todo.extend(adj[x].iter().copied());
        }
        if !closes { adj[i].push(j); }
    }
    for l in adj.iter_mut() { l.sort_unstable(); l.dedup(); }
    let mut lv = vec![0usize; n];
    for _ in 0..n {
        let mut moved = false;
        for i in 0..n {
            for &j in &adj[i] {
                if lv[i] <= lv[j] && lv[j] + 1 <= n { lv[i] = lv[j] + 1; moved = true; }
            }
        }
        if !moved { break; }
    }
    lv
}

/// The reads of an expression that does not parse: every word of its code (the old, conservative scan).
fn text_reads(expr: &str) -> Vec<crate::infer::Read> {
    let c = code_only(expr);
    let mut out = vec![];
    let mut w = String::new();
    for ch in c.chars().chain(std::iter::once(' ')) {
        if ch.is_ascii_alphanumeric() || ch == '_' { w.push(ch); }
        else if !w.is_empty() { out.push(crate::infer::Read { name: std::mem::take(&mut w), cls: None }); }
    }
    out.sort(); out.dedup();
    out
}

/// The old ordering, by NAME across all classes: the reference the class-aware `levels` is tested against.
pub fn levels_by_name(gs: &[Getter]) -> Vec<usize> {
    let exprs: Vec<String> = gs.iter().map(|g| code_only(&g.expr)).collect();
    let mut lv = vec![0usize; gs.len()];
    for _ in 0..gs.len() {
        let mut moved = false;
        for i in 0..gs.len() {
            for j in 0..gs.len() {
                if gs[j].prop != gs[i].prop && reads_name(&exprs[i], &gs[j].prop) && lv[i] <= lv[j] && lv[j] + 1 <= gs.len() {
                    lv[i] = lv[j] + 1; moved = true;
                }
            }
        }
        if !moved { break; }
    }
    lv
}

/// The expression with its data strings blanked: a dict key or a list element that happens to be a getter's name is not a read of
/// it. A string that is the LAST argument of a call (`prop(row, 'name')`) names a field: it stays.
fn code_only(expr: &str) -> String {
    let c: Vec<char> = expr.chars().collect();
    let mut out = String::with_capacity(expr.len());
    let mut i = 0;
    while i < c.len() {
        if c[i] == '\'' || c[i] == '"' {
            let q = c[i];
            let mut j = i + 1;
            while j < c.len() && c[j] != q { if c[j] == '\\' { j += 1; } j += 1; }
            let end = j.min(c.len() - 1);
            let prev = out.chars().rev().find(|x| !x.is_whitespace());
            let next = c[end + 1..].iter().find(|x| !x.is_whitespace());
            if prev == Some(',') && next == Some(&')') { out.extend(&c[i..=end]); }     // prop(x, 'name'): a read by name
            else { out.push(' '); for _ in i..=end { out.push(' '); } }
            i = end + 1;
        } else { out.push(c[i]); i += 1; }
    }
    out
}

/// `this.<name>` read in an expression (a whole name: `this.level` is not `this.levels`).
fn reads_this(expr: &str, name: &str) -> bool { reads_name(&expr.replace("this.", " "), name) && expr.contains(&format!("this.{name}")) }

/// A setter sees the getters as they were BEFORE the write that fired it, so one that reads a getter depending (through
/// this class's other getters) on its own trigger property reads a stale value. The loader refuses it and names the fix.
fn stale_setter_read(setters: &[OnSet], getters: &[Getter]) -> Option<String> {
    for st in setters {
        let mut exprs: Vec<&str> = vec![&st.when];
        exprs.extend(st.writes.iter().map(|w| w.2.as_str()));
        for (c, ws) in &st.els { exprs.push(c); exprs.extend(ws.iter().map(|w| w.2.as_str())); }
        let same: Vec<&Getter> = getters.iter().filter(|g| g.cls == st.cls && g.prop != st.prop).collect();
        let depends = |g: &Getter| -> bool {
            let mut seen = vec![g.prop.as_str()];
            let mut todo = vec![g];
            while let Some(x) = todo.pop() {
                if reads_this(&x.expr, &st.prop) { return true; }
                for y in &same { if !seen.contains(&y.prop.as_str()) && reads_this(&x.expr, &y.prop) { seen.push(&y.prop); todo.push(y); } }
            }
            false
        };
        for g in &same {
            if exprs.iter().any(|e| reads_this(e, &g.prop)) && depends(g) {
                return Some(format!(
                    "{c}.{p} on-setter {n} reads the getter {c}.{g}, which depends on {c}.{p}: a setter sees the getters as they were before its write, so {g} is stale there. Act from an on-setter on {c}.{g} instead",
                    c = st.cls, p = st.prop, n = st.name, g = g.prop));
            }
        }
    }
    None
}

fn reads_name(expr: &str, name: &str) -> bool {
    expr.match_indices(name).any(|(i, _)| {
        let b = expr.as_bytes();
        let before = i == 0 || !(b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_');
        let j = i + name.len();
        let after = j >= b.len() || !(b[j].is_ascii_alphanumeric() || b[j] == b'_');
        before && after
    })
}

/// How many times the start-up pass may repeat while values still change (see `rebuild`).
const REBUILD_PASSES: usize = 16;

pub struct Fields {
    pub setters: Vec<OnSet>,
    /// the llm calls the plan fields asked for (the effect that is not a prop write)
    pub calls: Vec<J>,
    pub writes: u64,
    /// row 4: the recall policy's guard setters and RecallAsk getters (recall.rs) are on
    pub recall: bool,
    /// row 5: written calls (adapters.rs) are on; the calls they asked the host to run
    pub adapters: bool,
    pub method_calls: Vec<J>,
    /// row 6: self's learned triggers (learn.rs) are on
    pub learn: Option<crate::learn::Learner>,
    /// last value each guarded dial accepted
    pub accepted: std::collections::HashMap<String, J>,
    /// (entity, prop) pairs written by the field in the current round (one outside write's react)
    pub visited: std::collections::HashSet<(String, String)>,
    /// loop refusals: the write that was refused at a revisit, and why
    pub refusals: Vec<J>,
    /// every write the field made (getters, setters, receipts), in order, until the host drains it:
    /// {eid, prop, new, writer}. The host applies them to its store (fields_live.py).
    pub out: Vec<J>,
    /// the host takes the door's requests itself (to journal them, then `arrive`): a round leaves them in the store
    pub inbound_by_host: bool,
    /// getters declared in cliffhanger, their levels, and their inputs: (eid, field) read -> instances (eid, getter)
    pub getters: Vec<Getter>,
    glevel: Vec<usize>,
    deps: std::collections::HashMap<(String, String), std::collections::BTreeSet<(String, usize)>>,
    reads: std::collections::HashMap<(String, usize), Vec<(String, String)>>,
    dirty: std::collections::BTreeSet<(usize, String, usize)>,
    /// getter instances dirtied ONLY by their own Trip entity arriving (not by an input): the Trip applies to these
    trip_only: std::collections::BTreeSet<(String, usize)>,
    /// entities whose getters are registered; `primed` once the start-up rebuild ran
    known: std::collections::HashSet<String>,
    primed: bool,
    /// cliffhanglet packs loaded at runtime (name -> its setters and getters), after the built-in goal graph
    packs: std::collections::BTreeMap<String, crate::syntax::Pack>,
    /// class -> the pack that owns it; pack -> the packs it imports; packs that aren't active, and why
    owner: std::collections::HashMap<String, String>,
    imports: std::collections::HashMap<String, Vec<String>>,
    pub pack_errors: std::collections::BTreeMap<String, String>,
    /// the order packs were first loaded in: earlier packs keep the classes they own (a pack can't take a class
    /// by its name sorting first)
    load_order: Vec<String>,
}

impl Fields {
    pub fn goalgraph() -> Self {
        let mut f = Fields { setters: on_setters(), calls: vec![], writes: 0, recall: false, adapters: false, method_calls: vec![], learn: None, accepted: Default::default(), visited: Default::default(), refusals: vec![], out: vec![], inbound_by_host: false,
            getters: vec![], glevel: vec![], deps: Default::default(), reads: Default::default(), dirty: Default::default(), trip_only: Default::default(),
            known: Default::default(), primed: false, packs: Default::default(),
            owner: Default::default(), imports: Default::default(), pack_errors: Default::default(), load_order: vec![] };
        let gg = crate::syntax::parse_full(crate::translate::GOALGRAPH_CH).expect("packs/goalgraph.c1f parses");
        f.packs.insert("goalgraph".into(), gg);
        f.load_order.push("goalgraph".into());
        f.compose();
        f
    }

    /// Load (or replace) a named `.c1f` pack. A pack that doesn't parse is refused and nothing changes. The setters
    /// and getters are the built-in goal graph's, then each pack's in name order.
    pub fn set_pack(&mut self, name: &str, src: &str) -> Result<(), String> {
        let p = crate::syntax::parse_full(src).map_err(|e| format!("pack {}: {}", name, e))?;
        if let Some(n) = &p.name { if n != name { return Err(format!("pack {}: its PACK line says {}", name, n)); } }
        if let Some(e) = stale_setter_read(&p.setters, &p.getters) { return Err(format!("pack {}: {}", name, e)); }
        let old = self.packs.insert(name.to_string(), p);
        if !self.load_order.iter().any(|n| n == name) { self.load_order.push(name.to_string()); }
        self.compose();
        if let Some(e) = self.pack_errors.get(name).cloned() {
            // an import problem: a pack that was active stays as it was; a new one waits for its imports
            match old {
                Some(o) => { self.packs.insert(name.to_string(), o); self.compose(); }
                None if e.contains("belongs to pack") => { self.packs.remove(name); self.load_order.retain(|n| n != name); self.compose(); }
                None => {}                           // a new pack waiting for an import stays, and activates when it arrives
            }
            return Err(e);
        }
        Ok(())
    }

    pub fn remove_pack(&mut self, name: &str) -> bool {
        let had = self.packs.remove(name).is_some();
        self.load_order.retain(|n| n != name);
        if had { self.compose(); }
        had
    }

    /// Activate packs in import order: a pack is active once every pack it imports is active with the same major
    /// version, and it doesn't declare on a class another pack owns without importing that pack. A pack owns the
    /// classes it is first to declare on. The rest wait, each with its reason.
    fn compose(&mut self) {
        use std::collections::{HashMap, HashSet};
        self.pack_errors.clear();
        let mut owner: HashMap<String, String> = HashMap::new();
        let mut active: Vec<String> = vec![];
        let mut done: HashSet<String> = HashSet::new();
        let names: Vec<String> = self.load_order.iter().filter(|n| self.packs.contains_key(*n)).cloned().collect();
        loop {
            let mut moved = false;
            for n in &names {
                if done.contains(n) { continue; }
                let p = &self.packs[n];
                let ready = p.imports.iter().all(|(i, maj)| active.contains(i) && self.packs.get(i).map(|q| q.major()) == Some(*maj));
                if !ready { continue; }
                done.insert(n.clone());
                moved = true;
                let foreign: Vec<String> = p.classes().into_iter().filter(|c| owner.get(c).map(|o| o != n && !p.imports.iter().any(|(i, _)| i == o)).unwrap_or(false)).collect();
                if let Some(c) = foreign.first() {
                    let o = &owner[c];
                    let maj = self.packs.get(o).map(|q| q.major()).unwrap_or(1);
                    self.pack_errors.insert(n.clone(), format!("pack {}: declares on {}, which belongs to pack {}: add IMPORT {} {}", n, c, o, o, maj));
                    continue;
                }
                for c in p.classes() { owner.entry(c).or_insert_with(|| n.clone()); }
                active.push(n.clone());
            }
            if !moved { break; }
        }
        for n in &names {
            if done.contains(n) { continue; }
            let p = &self.packs[n];
            let why = p.imports.iter().find_map(|(i, maj)| match self.packs.get(i) {
                None => Some(format!("imports {} {}, which isn't loaded", i, maj)),
                Some(q) if q.major() != *maj => Some(format!("imports {} {}, but {} {} is loaded", i, maj, i, q.version)),
                Some(_) if !active.contains(i) => Some(format!("imports {}, which isn't active", i)),
                _ => None,
            }).unwrap_or_else(|| "its imports form a cycle".into());
            self.pack_errors.insert(n.clone(), format!("pack {}: {}", n, why));
        }
        let mut setters = vec![];
        let mut getters = vec![];
        for n in &active {
            let p = &self.packs[n];
            setters.extend(p.setters.iter().cloned().map(|mut s| { s.pack = n.clone(); s }));
            getters.extend(p.getters.iter().cloned());
        }
        self.imports = active.iter().map(|n| (n.clone(), self.packs[n].imports.iter().map(|(i, _)| i.clone()).collect())).collect();
        self.owner = owner;
        self.setters = setters;
        self.set_getters(getters);
    }

    /// May a setter from `pack` write an entity of class `cls`? Its own classes, an imported pack's, or a class no
    /// pack owns. A setter built outside a pack ("") is not checked.
    fn may_write(&self, pack: &str, cls: &str) -> Result<(), String> {
        if pack.is_empty() { return Ok(()); }
        match self.owner.get(cls) {
            None => Ok(()),
            Some(o) if o == pack || self.imports.get(pack).map(|v| v.contains(o)).unwrap_or(false) => Ok(()),
            Some(o) => Err(format!("pack {} can't write {} (pack {} owns it): add IMPORT {}", pack, cls, o, o)),
        }
    }

    /// Every class a loaded setter or getter is declared on.
    pub fn classes(&self) -> Vec<String> {
        let mut v: Vec<String> = self.setters.iter().map(|s| s.cls.clone()).chain(self.getters.iter().map(|g| g.cls.clone())).collect();
        v.sort(); v.dedup();
        v
    }

    pub fn set_getters(&mut self, gs: Vec<Getter>) {
        self.glevel = levels(&gs);
        self.getters = gs;
        self.deps.clear(); self.reads.clear(); self.dirty.clear(); self.known.clear(); self.primed = false;
    }

    /// Register entities the getters haven't seen: the first time, a silent rebuild (no setters run); after that,
    /// a new entity is a birth (its getters compute, and what references it recalculates, live).
    fn sync(&mut self, core: &mut Core, now: f64, seq: &mut i64) {
        if self.getters.is_empty() { return; }
        if !self.primed { self.prime(core, now, seq); return; }
        if core.docs.len() == self.known.len() { return; }   // nothing new (a removal is rare: its next birth registers at the scan after)
        let mut new: Vec<String> = core.docs.keys().filter(|k| !self.known.contains(*k)).cloned().collect();
        if new.is_empty() { return; }
        new.sort();
        for e in new { self.birth(core, &e, now, seq); }
    }

    fn has_getter(&self, cls: &str, prop: &str) -> bool { self.getters.iter().any(|g| g.cls == cls && g.prop == prop) }

    fn getters_of(&self, core: &Core, eid: &str) -> Vec<usize> {
        let Some(e) = core.docs.get(eid) else { return vec![] };
        // its own class, or a kind among its ISA parents (`parents: ["cls:chatthing"]`), as setters bind (core.is_a)
        let mut v: Vec<usize> = (0..self.getters.len()).filter(|&i| self.getters[i].cls == e.cls
            || has_kind(core, eid, &e.props, &self.getters[i].cls)).collect();
        v.sort_by_key(|&i| self.glevel[i]);
        v
    }

    /// Evaluate one getter instance, record what it read as its inputs, and return its value (None on an error,
    /// with a receipt). Its own field is never an input (rule 3: own name = previous value).
    fn compute(&mut self, core: &mut Core, host: Option<&dyn Host>, eid: &str, gi: usize, now: f64, seq: &mut i64) -> Option<J> {
        let g = self.getters[gi].clone();
        let key = (eid.to_string(), gi);
        let trip = trip_eid(eid, &g.prop);
        // The Trip (a recorded kill) of this node applies until an input changes: the node is that null, nothing is evaluated
        if self.trip_only.remove(&key) {
            match core.docs.get(&trip).map(|t| t.props.get("active") == Some(&json!(true))) {
                Some(true) => {
                    let reason = trip_reason(core, &trip);
                    core.why.insert((eid.to_string(), g.prop.clone()), reason);
                    core.locals.remove(&(eid.to_string(), g.prop.clone()));
                    return Some(J::Null);
                }
                Some(false) => return None,   // its half-written or cleared Trip: nothing changed for the node
                None => {}
            }
        }
        let asker = format!("{}\u{0}{}.{}", eid, g.cls, g.prop);
        let (r, reads, nil, locals) = core.eval_traced(&g.expr, &me(eid), &[("clock", json!(now))], host, &asker);
        let key2 = (eid.to_string(), g.prop.clone());
        if locals.is_empty() { core.locals.remove(&key2); } else {
            core.locals.insert(key2.clone(), locals.iter().map(|(k, v)| (k.clone(), match v {
                crate::expr::V::Nil(why) => J::String(format!("null ({why})")),
                v => crate::expr::to_json(v).unwrap_or(J::Null),
            })).collect());
        }
        if let Some(old) = self.reads.remove(&key) { for k in old { if let Some(s) = self.deps.get_mut(&k) { s.remove(&key); } } }
        let mut ins: Vec<(String, String)> = reads.into_iter().filter(|(e, p)| !(e == eid && *p == g.prop)).collect();
        if reads_name(&g.expr, "clock") { ins.push((CLOCK.into(), String::new())); }
        ins.sort(); ins.dedup();
        for k in &ins { self.deps.entry(k.clone()).or_default().insert(key.clone()); }
        self.reads.insert(key, ins);
        match r {
            Ok(v) => {
                self.clear_trip(core, &trip, seq, now);   // a fresh evaluation in time: the kill no longer applies
                match &nil { Some(why) if v.is_null() => { core.why.insert(key2, why.clone()); } _ => { core.why.remove(&key2); } }   // a null remembers why
                Some(v)
            }
            Err(e) if crate::expr::parse_timeout(&e).is_some() => {
                // killed by the time allowance: the one outcome that is not deterministic, so it is the one recorded (as a Trip entity)
                let (n, m) = crate::expr::parse_timeout(&e).unwrap_or((0, 0));
                self.write_trip(core, &trip, eid, &g.prop, n, m, now);
                core.why.insert(key2, format!("error: {}", e));
                Some(J::Null)
            }
            Err(e) => {
                // an error is a null with the error as its reason (it no longer keeps a stale value); the receipt still says where
                let reason = format!("error in getter {}.{}: {}; the value is null", g.cls, g.prop, e);
                self.receipt(core, eid, &format!("getter:{}", g.prop), reason, J::Null, seq, now);
                core.why.insert(key2, format!("error: {}", e));
                Some(J::Null)
            }
        }
    }

    /// A Trip entity became active (the journal's write, or a restore): its node is dirty, as "the kill, nothing else" (so the node
    /// recomputes to the reasoned null without being evaluated). Any other dirtying of the node (an input) ends the kill.
    fn trip_arrived(&mut self, core: &Core, trip: &str) {
        let Some((eid, prop)) = trip.strip_prefix(TRIP_PREFIX).and_then(|r| r.rsplit_once('#')) else { return };
        if core.docs.get(trip).map(|t| t.props.get("active") != Some(&json!(true))).unwrap_or(true) { return; }
        for gi in self.getters_of(core, eid) {
            if self.getters[gi].prop == prop { self.trip_only.insert((eid.to_string(), gi)); self.dirty.insert((self.glevel[gi], eid.to_string(), gi)); }
        }
    }

    /// Record a kill: `Trip:<eid>#<prop>` {node, self_ms, allowance_ms, at, active}, written like an effect receipt (writer
    /// `field:driver:trip`, so a journal of outside writes keeps it). The engine's own write is known to it at once (no echo round).
    fn write_trip(&mut self, core: &mut Core, trip: &str, eid: &str, prop: &str, self_ms: u64, allowance_ms: u64, now: f64) {
        if !core.docs.contains_key(trip) {
            core.load(trip.to_string(), TRIP_CLASS.to_string(), Default::default(), None, now);
            self.known.insert(trip.to_string());
            self.out.push(json!({"eid": trip, "born": TRIP_CLASS}));
        }
        let props = [("node", json!(format!("{eid}.{prop}"))), ("self_ms", json!(self_ms)), ("allowance_ms", json!(allowance_ms)), ("at", json!(now)), ("active", json!(true))];
        // `active` is written every time, even when it was already true: a replay may have cleared it on the way, and this is the write that re-arms it
        for (p, v) in props { self.trip_prop(core, trip, p, v, "field:driver:trip", now, p == "active"); }
    }

    /// A fresh evaluation that did not trip: an active Trip stops applying (the kill stays as history). Derived, so not journalled.
    fn clear_trip(&mut self, core: &mut Core, trip: &str, _seq: &mut i64, now: f64) {
        if core.docs.get(trip).map(|t| t.props.get("active") == Some(&json!(true))).unwrap_or(false) {
            self.trip_prop(core, trip, "active", json!(false), "field:trip-clear", now, false);
        }
    }

    fn trip_prop(&mut self, core: &mut Core, trip: &str, prop: &str, new: J, writer: &str, now: f64, force: bool) {
        let old = core.docs.get(trip).and_then(|e| e.props.get(prop).cloned()).unwrap_or(J::Null);
        if old == new && !force { return; }
        let w = Write { eid: trip.into(), prop: prop.into(), new: new.clone(), old, writer: writer.into(), chain: json!({}), seq: -1 };
        self.out.push(json!({"eid": trip, "prop": prop, "new": new, "writer": writer}));
        core.hear(w, None, now);
    }

    /// The chain behind a null: [{eid, prop, reason}] from the property asked about back to the first null that has a reason of
    /// its own (each getter on the way read the next one, and its reason is the same). Empty when the value is not null, or the
    /// property is not a getter of the entity.
    pub fn why_chain(&self, core: &Core, eid: &str, prop: &str) -> Vec<J> {
        let mut out: Vec<J> = vec![];
        let mut cur = (eid.to_string(), prop.to_string());
        let mut seen: std::collections::BTreeSet<(String, String)> = Default::default();
        loop {
            let null = core.docs.get(&cur.0).map(|e| e.props.get(&cur.1).map(|v| v.is_null()).unwrap_or(true)).unwrap_or(false);
            let gi = self.getters_of(core, &cur.0).into_iter().find(|&i| self.getters[i].prop == cur.1);
            if !null || gi.is_none() || !seen.insert(cur.clone()) { break; }
            let reason = core.why.get(&cur).cloned();
            let mut node = json!({"eid": cur.0, "prop": cur.1, "reason": reason});
            if let Some(l) = core.locals.get(&cur) { node["locals"] = J::Object(l.clone()); }
            let trip = trip_eid(&cur.0, &cur.1);
            if core.docs.get(&trip).map(|t| t.props.get("active") == Some(&json!(true))).unwrap_or(false) { node["trip"] = json!(trip); }
            out.push(node);
            let Some(r) = reason else { break };
            // the input that is itself a null getter with the same reason is where this one came from
            let next = self.reads.get(&(cur.0.clone(), gi.unwrap())).and_then(|ins| ins.iter().find(|k| {
                core.why.get(*k) == Some(&r) && core.docs.get(&k.0).map(|e| e.props.get(&k.1).map(|v| v.is_null()).unwrap_or(true)).unwrap_or(false)
            }).cloned());
            match next { Some(n) => cur = n, None => break }
        }
        out
    }

    /// A write changed (eid, prop): every getter instance that read it, or that asked `referencing(_, prop)`, is dirty.
    fn mark(&mut self, eid: &str, prop: &str) {
        self.mark_key(&(eid.to_string(), prop.to_string()));
    }

    fn mark_key(&mut self, k: &(String, String)) {
        if let Some(s) = self.deps.get(k) {
            let hit: Vec<(String, usize)> = s.iter().cloned().collect();
            for (e, gi) in hit {
                // an input changed: that ends the kill (the node gets a fresh chance)
                self.trip_only.remove(&(e.clone(), gi));
                self.dirty.insert((self.glevel[gi], e, gi));
            }
        }
    }

    /// `prop` went from `old` to `new` somewhere: the getters that asked `referencing(t, prop)` for a target t
    /// that was added or removed recalculate (only those: not every getter that ever referenced `prop`).
    fn mark_refs(&mut self, prop: &str, old: &J, new: &J) {
        let (o, n) = (crate::vm::Core::targets(old), crate::vm::Core::targets(new));
        let mut ts: Vec<String> = o.iter().filter(|t| !n.contains(t)).chain(n.iter().filter(|t| !o.contains(t))).cloned().collect();
        ts.sort(); ts.dedup();
        for t in ts { self.mark_key(&(crate::vm::REFS.to_string(), format!("{}\u{0}{}", prop, t))); }
    }

    /// A birth: its own getters compute silently (stored, no setter runs), as at a rebuild. An entity made in parts (a
    /// plan written entity by entity) must not fire on a half-made state. What references it recalculates live.
    fn birth(&mut self, core: &mut Core, e: &str, now: f64, seq: &mut i64) {
        self.known.insert(e.to_string());
        if let Some(c) = core.docs.get(e).map(|d| d.cls.clone()) { self.mark_key(&(crate::vm::CLASS_READ.to_string(), c)); }
        for gi in self.getters_of(core, e) {
            let Some(v) = self.compute(core, None, e, gi, now, seq) else { continue };
            let prop = self.getters[gi].prop.clone();
            if core.docs.get(e).and_then(|d| d.props.get(&prop)) == Some(&v) { continue; }
            *seq += 1;
            let w = Write { eid: e.to_string(), prop: prop.clone(), new: v, old: J::Null, writer: "field:rebuild".into(), chain: json!({}), seq: *seq };
            self.out.push(json!({"eid": w.eid, "prop": w.prop, "new": w.new, "writer": w.writer}));
            core.hear(w, None, now);
        }
        let props: Vec<(String, J)> = core.docs.get(e).map(|d| d.props.iter().map(|(k, v)| (k.clone(), v.clone())).collect()).unwrap_or_default();
        for (p, v) in props { self.mark(e, &p); self.mark_refs(&p, &J::Null, &v); }
    }

    /// First contact without a rebuild (an engine that started on a live field): every getter computes once to
    /// learn its inputs, and each whose value differs from the stored one is dirty, so this round writes it live.
    fn prime(&mut self, core: &mut Core, now: f64, seq: &mut i64) {
        self.primed = true;
        self.known = core.docs.keys().cloned().collect();
        let mut order: Vec<usize> = (0..self.getters.len()).collect();
        order.sort_by_key(|&i| self.glevel[i]);
        for gi in order {
            let cls = self.getters[gi].cls.clone();
            let prop = self.getters[gi].prop.clone();
            let mut es: Vec<String> = of_kind(core, &cls);
            es.sort();
            for e in es {
                let Some(v) = self.compute(core, None, &e, gi, now, seq) else { continue };
                if core.docs.get(&e).and_then(|d| d.props.get(&prop)) != Some(&v) { self.dirty.insert((self.glevel[gi], e, gi)); }
            }
        }
    }

    /// Start-up (a rebuild, #994 decisions): compute every getter of every entity, lower levels first, and keep the
    /// values. They are written (the host stores them), but no setter runs: a rebuild runs no effects.
    pub fn rebuild(&mut self, core: &mut Core, now: f64, seq: &mut i64) {
        self.primed = true;
        self.known = core.docs.keys().cloned().collect();
        let mut order: Vec<usize> = (0..self.getters.len()).collect();
        order.sort_by_key(|&i| self.glevel[i]);
        // A getter that reads the same getter on ANOTHER entity (a tree's depth through its children) cannot be ordered by class level:
        // the parent may be computed before its children. So the pass repeats until nothing changes (bounded: a chain of references
        // longer than REBUILD_PASSES settles in the live rounds that follow, since each change marks its readers dirty).
        for pass in 0..REBUILD_PASSES {
            let mut changed = false;
            for &gi in &order {
                let cls = self.getters[gi].cls.clone();
                let mut es: Vec<String> = of_kind(core, &cls);
                es.sort();
                for e in es {
                    let Some(v) = self.compute(core, None, &e, gi, now, seq) else { continue };
                    let prop = self.getters[gi].prop.clone();
                    let was = core.docs.get(&e).and_then(|d| d.props.get(&prop)).cloned();
                    if was.as_ref() == Some(&v) || (v.is_null() && was.is_none()) { continue; }
                    changed = true;
                    *seq += 1;
                    let w = Write { eid: e.clone(), prop: prop.clone(), new: v, old: J::Null, writer: "field:rebuild".into(), chain: json!({}), seq: *seq };
                    if pass == 0 || !self.out.iter().rev().take(64).any(|o| o["eid"] == json!(w.eid) && o["prop"] == json!(w.prop) && o["new"] == w.new) {
                        self.out.push(json!({"eid": w.eid, "prop": w.prop, "new": w.new, "writer": w.writer}));
                    }
                    core.hear(w, None, now);
                }
            }
            if !changed { break; }
        }
        self.dirty.clear();
    }

    /// The host replaced a known entity's state without writes (a reload): every field that changed counts as
    /// written for the getters, so a missed write is still picked up at the next round.
    pub fn reloaded(&mut self, eid: &str, old: &serde_json::Map<String, J>, new: &serde_json::Map<String, J>) {
        if !self.known.contains(eid) { return; }
        let keys: std::collections::BTreeSet<&String> = old.keys().chain(new.keys()).collect();
        for k in keys {
            if old.get(k) != new.get(k) {
                self.mark(eid, k);
                self.mark_refs(k, old.get(k).unwrap_or(&J::Null), new.get(k).unwrap_or(&J::Null));
            }
        }
    }

    /// Recompute every getter of this entity in the next round (a banner build asks its store reads again: a getter over
    /// constants is never dirty on its own).
    pub fn refresh(&mut self, core: &mut Core, host: &dyn Host, eid: &str, now: f64, seq: &mut i64) {
        for gi in self.getters_of(core, eid) { self.dirty.insert((self.glevel[gi], eid.to_string(), gi)); }
        self.settle(core, host, now, seq);
    }
    /// A round with no outside write (new entities registered, dirty getters settled).
    pub fn settle(&mut self, core: &mut Core, host: &dyn Host, now: f64, seq: &mut i64) {
        self.visited.clear();
        self.run(core, host, VecDeque::new(), now, seq);
    }

    /// A new entity (born while live): its getters compute, and whatever counts it by reference recalculates.
    pub fn born(&mut self, core: &mut Core, host: &dyn Host, eid: &str, now: f64, seq: &mut i64) {
        for gi in self.getters_of(core, eid) { self.dirty.insert((self.glevel[gi], eid.to_string(), gi)); }
        let props: Vec<String> = core.docs.get(eid).map(|e| e.props.keys().cloned().collect()).unwrap_or_default();
        for p in props { self.mark(eid, &p); }
        if eid.starts_with(TRIP_PREFIX) { self.trip_arrived(core, eid); }
        self.visited.clear();
        self.run(core, host, VecDeque::new(), now, seq);
    }

    /// Row 4: a dial was written -> its guard setter (a bad value goes back to the last accepted one); a dial or an ask
    /// moved -> recompute every RecallAsk's getters.
    fn recall_on(&mut self, core: &mut Core, work: &mut VecDeque<Write>, w: &Write, seq: &mut i64, now: f64) {
        let is_dial = w.eid == crate::recall::DIALS;
        let is_ask = core.docs.get(&w.eid).map(|e| e.cls == crate::recall::CLASS_ASK).unwrap_or(false);
        if !is_dial && !is_ask { return; }
        if is_dial && !crate::recall::guard(&w.prop, &w.new) {
            let back = self.accepted.get(&w.prop).cloned().or_else(|| crate::recall::defaults().get(&w.prop).cloned());
            if let Some(b) = back { self.emit_unchecked(core, work, &w.eid, &w.prop, b, "recall-guard", seq, now); }
            return;
        }
        if is_dial { self.accepted.insert(w.prop.clone(), w.new.clone()); }
        if is_ask && ["lanes", "rank_set", "rerank_top"].contains(&w.prop.as_str()) { return; }   // the ask getters themselves (the dial rerank_top is not one)
        let mut asks: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == crate::recall::CLASS_ASK).map(|(k, _)| k.clone()).collect();
        asks.sort();
        for a in asks { for (p, v) in crate::recall::ask_values(core, &a) { self.emit_unchecked(core, work, &a, p, v, "recall", seq, now); } }
    }

    /// Row 5: a Method's `invoke` was written -> the verdict (refusal receipt / effect), then the slot is consumed.
    fn adapters_on(&mut self, core: &mut Core, work: &mut VecDeque<Write>, w: &Write, seq: &mut i64, now: f64) {
        use crate::adapters::{decide, receipt, Verdict};
        if w.prop != "invoke" || !crate::adapters::is_method(core, &w.eid) || !w.new.is_object() || w.new.get("request").is_none() { return; }
        let name = crate::adapters::method_name(core, &w.eid);
        let (obj, call) = (w.new["object"].as_str().unwrap_or("").to_string(), w.new["call"].as_str().unwrap_or("").to_string());
        match decide(name.as_deref(), &w.new) {
            Verdict::Nothing => {}
            Verdict::Refuse { clear, rid, head } => {
                if clear && core.docs.get(&obj).and_then(|e| e.props.get(&call)).map(|v| *v != json!("") && !v.is_null()).unwrap_or(false) {
                    self.emit(core, work, &obj, &call, json!(""), "call-clear", seq, now);
                }
                if core.docs.contains_key(&obj) { for (p, v) in receipt(&call, &rid, 2, &head, None) { self.emit(core, work, &obj, &p, v, "call-receipt", seq, now); } }
            }
            Verdict::Run { object, method, call, rid, args } => {
                self.method_calls.push(json!({"eid": w.eid, "object": object, "method": method, "call": call, "id": rid, "args": args}));
            }
        }
        if core.docs.get(&w.eid).and_then(|e| e.props.get("invoke")) == Some(&w.new) {
            self.emit_unchecked(core, work, &w.eid, "invoke", json!(""), "call-consumed", seq, now);   // own name: the invoke setter consumes its slot (rule 6)
        }
    }

    /// Row 6: a write teaches and scores the learner, then fires the candidates on its prop; a live one writes the real path.
    fn learn_on(&mut self, core: &mut Core, host: &dyn Host, work: &mut VecDeque<Write>, w: &Write, seq: &mut i64, now: f64) {
        let Some(mut l) = self.learn.take() else { return };
        l.observe(core, w, now);
        let live = l.fire(core, host, w, now);
        self.learn = Some(l);
        for (e, p, v, c) in live { if core.docs.contains_key(&e) { self.emit(core, work, &e, &p, v, &format!("learn:{}", c), seq, now); } }
    }

    /// Row 5: the host's receipt for a run call (its rc, head, result), written on the object with the request's id.
    pub fn deliver(&mut self, core: &mut Core, object: &str, call: &str, rid: &str, rc: i64, head: &str, result: Option<J>, now: f64, seq: &mut i64) {
        if !core.docs.contains_key(object) { return; }
        let mut work = VecDeque::new();
        for (p, v) in crate::adapters::receipt(call, rid, rc, head, result) { self.emit(core, &mut work, object, &p, v, "call-receipt", seq, now); }
    }

    /// A setter-chain write: refused at a revisit of (entity, prop) in this round (rule 8).
    fn emit(&mut self, core: &mut Core, work: &mut VecDeque<Write>, eid: &str, prop: &str, new: J, writer: &str, seq: &mut i64, now: f64) {
        self.emit_as(core, work, eid, prop, new, writer, seq, now, true)
    }

    /// A getter's recalculated value, the recall guard's put-back, or an own-name store: not a setter chain, so no
    /// loop check (rule 8: getters recalculate, they are never refused).
    fn emit_unchecked(&mut self, core: &mut Core, work: &mut VecDeque<Write>, eid: &str, prop: &str, new: J, writer: &str, seq: &mut i64, now: f64) {
        self.emit_as(core, work, eid, prop, new, writer, seq, now, false)
    }

    /// A receipt that a setter wrote nothing (an error in a condition): recorded, and written as `refused` on the
    /// entity. Not queued: it triggers nothing.
    fn receipt(&mut self, core: &mut Core, eid: &str, writer: &str, reason: String, refused: J, seq: &mut i64, now: f64) {
        self.refusals.push(json!({"eid": eid, "prop": "", "refused": refused, "writer": writer, "reason": reason}));
        *seq += 1;
        let r = Write { eid: eid.into(), prop: "refused".into(), new: json!(reason), old: J::Null, writer: "field:setter-error".into(), chain: json!({}), seq: *seq };
        self.out.push(json!({"eid": r.eid, "prop": r.prop, "new": r.new, "writer": r.writer}));
        core.hear(r, None, now);
    }

    fn emit_as(&mut self, core: &mut Core, work: &mut VecDeque<Write>, eid: &str, prop: &str, new: J, writer: &str, seq: &mut i64, now: f64, check: bool) {
        if core.docs.get(eid).map(|e| e.props.get(prop) == Some(&new)).unwrap_or(false) { return; }   // idempotent
        if check && !self.visited.insert((eid.to_string(), prop.to_string())) {
            // revisit in this round: refuse BEFORE applying
            let reason = format!("loop: ({}, {}) already written this round by the field; refused {} by {}", eid, prop, new, writer);
            self.refusals.push(json!({"eid": eid, "prop": prop, "refused": new, "writer": writer, "reason": reason}));
            *seq += 1;
            let r = Write { eid: eid.into(), prop: "refused".into(), new: json!(reason), old: J::Null, writer: "field:loop-refused".into(), chain: json!({}), seq: *seq };
            self.out.push(json!({"eid": r.eid, "prop": r.prop, "new": r.new, "writer": r.writer}));
            core.hear(r, None, now);   // a receipt, not queued: it triggers nothing
            return;
        }
        *seq += 1;
        self.writes += 1;
        let old = core.docs.get(eid).and_then(|e| e.props.get(prop).cloned()).unwrap_or(J::Null);
        let w = Write { eid: eid.into(), prop: prop.into(), new, old, writer: format!("field:{}", writer), chain: json!({}), seq: *seq };
        self.out.push(json!({"eid": w.eid, "prop": w.prop, "new": w.new, "writer": w.writer}));
        core.hear(w.clone(), None, now);
        work.push_back(w);
    }

    /// The getter: every Task/Wait's open_blockers, recomputed when a blocker's status moved.
    /// The open-blockers getter, recalculated for what a blocker's status write can change: the tasks and waits it
    /// blocks (its `blocks`). Each is counted from the whole field, so a missed write is still picked up.
    fn recount(&mut self, core: &mut Core, work: &mut VecDeque<Write>, blocker: &str, seq: &mut i64, now: f64) {
        let mut targets: Vec<String> = core.docs.get(blocker).and_then(|e| e.props.get("blocks")).and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
        targets.retain(|t| core.docs.get(t).map(|e| e.cls == "Task" || e.cls == "Wait").unwrap_or(false));
        targets.sort(); targets.dedup();
        let mut blockers: Vec<String> = core.docs.iter().filter(|(_, e)| BLOCKER_CLASSES.contains(&e.cls.as_str())).map(|(k, _)| k.clone()).collect();
        blockers.sort();
        for t in targets {
            let mut n = 0i64;
            for b in &blockers {
                if let Ok(J::Bool(true)) = core.eval_json(OPEN_BLOCKERS_FILTER, &[("t", &t), ("b", b)], &[]) { n += 1; }
            }
            // an entity with no stored count and no open blocker stays as it is
            if core.docs.get(&t).map(|e| e.props.get("open_blockers").is_none()).unwrap_or(false) && n == 0 { continue; }
            self.emit_unchecked(core, work, &t, "open_blockers", json!(n), "open_blockers", seq, now);
        }
    }

    /// React to one write (already applied to the mirror): getters, then the on-setters, until quiet.
    /// An entity leaves (a page element taken out of the DOM): it is gone from the mirror, and what read it or
    /// referenced it recalculates, live, as after a write of each of its fields to nothing.
    pub fn remove(&mut self, core: &mut Core, host: &dyn Host, eid: &str, now: f64, seq: &mut i64) {
        let Some(old) = core.docs.get(eid).map(|e| e.props.clone()) else { return };
        if let Some(c) = core.docs.get(eid).map(|d| d.cls.clone()) { self.mark_key(&(crate::vm::CLASS_READ.to_string(), c)); }
        *seq += 1;
        core.hear(Write { eid: eid.into(), prop: String::new(), new: J::Null, old: J::Null, writer: "remove".into(),
                          chain: json!({}), seq: *seq }, None, now);
        self.known.remove(eid);
        self.dirty.retain(|(_, e, _)| e != eid);
        // what its getters read is forgotten with it: a reload under another class must not run the old class's getters
        self.reads.retain(|(e, _), _| e != eid);
        for set in self.deps.values_mut() { set.retain(|(e, _)| e != eid); }
        for (p, v) in &old { self.mark(eid, p); self.mark_refs(p, v, &J::Null); }
        self.dirty.retain(|(_, e, _)| e != eid);
        self.visited.clear();
        self.run(core, host, VecDeque::new(), now, seq);
    }

    pub fn react(&mut self, core: &mut Core, host: &dyn Host, w: Write, now: f64, seq: &mut i64) {
        self.visited.clear();
        self.visited.insert((w.eid.clone(), w.prop.clone()));   // the outside write starts the chain: returning to it is a loop
        self.run(core, host, VecDeque::from([w]), now, seq);
    }

    /// A round: the queued writes run (getters marked, setters fired); when the queue is empty, the dirty getters
    /// recalculate, lowest level first, and a changed value is written (which can queue more). Until both are empty.
    fn run(&mut self, core: &mut Core, host: &dyn Host, mut work: VecDeque<Write>, now: f64, seq: &mut i64) {
        self.sync(core, now, seq);
        for k in core.sql_ready() { self.mark_key(&(crate::vm::SQL.to_string(), k)); }   // a query's rows are in
        if !self.inbound_by_host { for (e, cls, props) in core.inbound() { self.arrive(core, e, cls, props, now); } }   // the door pushed an entity (a request): born
        for (e, verb, rec) in core.effect_receipts() {                                       // a driver effect finished
            if !core.docs.contains_key(&e) { continue; }
            let rc = rec.get("rc").cloned().unwrap_or(J::Null);
            self.emit_unchecked(core, &mut work, &e, &format!("{}_result", verb), rec, &format!("driver:{}", verb), seq, now);
            self.emit_unchecked(core, &mut work, &e, &format!("{}_rc", verb), rc, &format!("driver:{}", verb), seq, now);
        }
        loop {
            if let Some(w) = work.pop_front() {
                self.step(core, host, &mut work, w, now, seq);
                continue;
            }
            let Some((_, e, gi)) = self.dirty.pop_first() else { break };
            let Some(v) = self.compute(core, Some(host), &e, gi, now, seq) else { continue };
            let prop = self.getters[gi].prop.clone();
            self.emit_unchecked(core, &mut work, &e, &prop, v, &format!("getter:{}", prop), seq, now);
        }
    }

    /// An entity arrives from outside (the door's request, the host's birth): loaded and known, its getters and what read its
    /// fields dirty for the next round, which also fires the setters on its writes (a birth by `sync` runs none: that is a rebuild).
    pub fn arrive(&mut self, core: &mut Core, e: String, cls: String, props: serde_json::Map<String, J>, now: f64) {
        let keys: Vec<String> = props.keys().cloned().collect();
        core.load(e.clone(), cls, props, None, now);
        self.known.insert(e.clone());
        for gi in self.getters_of(core, &e) { self.dirty.insert((self.glevel[gi], e.clone(), gi)); }
        for p in keys { self.mark(&e, &p); }
    }

    fn step(&mut self, core: &mut Core, host: &dyn Host, work: &mut VecDeque<Write>, w: Write, now: f64, seq: &mut i64) {
        self.driver_effect(core, work, &w, now, seq);
        self.mark(&w.eid, &w.prop);
        if w.prop == "active" && w.eid.starts_with(TRIP_PREFIX) { self.trip_arrived(core, &w.eid); }
        self.mark_refs(&w.prop, &w.old, &w.new);
        {
            let cls = core.docs.get(&w.eid).map(|e| e.cls.clone()).unwrap_or_default();
            if w.prop == "status" && BLOCKER_CLASSES.contains(&cls.as_str()) && !self.has_getter("Task", "open_blockers") {
                self.recount(core, work, &w.eid, seq, now);
            }
        }
            if self.learn.is_some() { self.learn_on(core, host, work, &w, seq, now); }
            if self.adapters { self.adapters_on(core, work, &w, seq, now); }
            if self.recall { self.recall_on(core, work, &w, seq, now); }
            if w.writer.starts_with(DIRECT) { return; }   // rule 6: an own-name store bypasses the setters (getters above still ran)
            let hits: Vec<usize> = (0..self.setters.len())
                .filter(|&i| self.setters[i].prop == w.prop && core.is_a(host, &w.eid, &self.setters[i].cls)).collect();
            for i in hits {
                let name = self.setters[i].name.clone();
                let truthy = |v: &J| match v {
                    J::Bool(b) => *b, J::String(s) => !s.is_empty(), J::Number(n) => n.as_f64().map_or(false, |f| f != 0.0), _ => false };
                // WHEN / ELSE WHEN / ELSE: the first true branch runs, only it. A condition that errors stops the
                // setter: nothing is written and a receipt says why (it never falls through to ELSE).
                let branches: Vec<(String, Vec<(String, String, String)>)> =
                    std::iter::once((self.setters[i].when.clone(), self.setters[i].writes.clone())).chain(self.setters[i].els.clone()).collect();
                let mut chosen = None;
                for (c, wr) in branches {
                    match core.eval_json(&c, &me(&w.eid), &[("clock", json!(now))]) {
                        Ok(v) if truthy(&v) => { chosen = Some(wr); break; }
                        Ok(_) => {}
                        Err(e) => {
                            let reason = format!("error in setter {}: WHEN {}: {}; the setter wrote nothing", name, c, e);
                            self.receipt(core, &w.eid, &name, reason, J::Null, seq, now);
                            break;
                        }
                    }
                }
                let Some(writes) = chosen else { continue };
                let pack = self.setters[i].pack.clone();
                // in written order; a FOR's list is read when the FOR is reached (it sees what the writes above made)
                if let Err(e) = self.run_writes(core, work, &w.eid, &w.prop, &name, &pack, &writes, &[], now, seq) {
                    let reason = format!("error in setter {}: {}; the setter stopped there", name, e);
                    self.receipt(core, &w.eid, &name, reason, J::Null, seq, now);
                }
            }
    }

    /// A chosen branch's writes, in order: FOR blocks run their body once per item of their list, read when the
    /// FOR is reached, the loop name bound to the item (an entity, or a value); nested FORs too. An error in a FOR's
    /// list stops here (the writes above it stand).
    fn run_writes(&mut self, core: &mut Core, work: &mut VecDeque<Write>, eid: &str, wprop: &str, name: &str, pack: &str,
                  ws: &[(String, String, String)], binds: &[(String, crate::vm::Item)], now: f64, seq: &mut i64) -> Result<(), String> {
        let mut binds = binds.to_vec();      // a local joins them for the lines below it, in this block only
        let mut k = 0;
        while k < ws.len() {
            let (target, prop, value) = &ws[k];
            if target == crate::syntax::LET {
                let item = { let (vars, extra) = scope(eid, &binds, now); core.eval_item(value, &vars, &extra).map_err(|e| format!("{} = {}: {}", prop, value, e))? };
                binds.retain(|(n, _)| n != prop);
                binds.push((prop.clone(), item));
                k += 1;
                continue;
            }
            if target == crate::syntax::FOR {
                let mut depth = 0; let mut j = k + 1;
                while j < ws.len() {
                    if ws[j].0 == crate::syntax::FOR { depth += 1; }
                    if ws[j].0 == crate::syntax::END { if depth == 0 { break; } depth -= 1; }
                    j += 1;
                }
                let items = {
                    let (vars, extra) = scope(eid, &binds, now);
                    core.eval_items(value, &vars, &extra).map_err(|e| format!("FOR {} IN {}: {}", prop, value, e))?
                };
                for it in items {
                    let mut b = binds.to_vec();
                    b.retain(|(n, _)| n != prop);
                    b.push((prop.clone(), it));
                    self.run_writes(core, work, eid, wprop, name, pack, &ws[k + 1..j.min(ws.len())], &b, now, seq)?;
                }
                k = j + 1;
                continue;
            }
            k += 1;
            if target == crate::syntax::END { continue; }
            if prop == crate::syntax::CTOR {
                self.construct(core, work, eid, name, pack, target, value, &binds, now, seq);
                continue;
            }
            let (vars, extra) = scope(eid, &binds, now);
            // `<target>.[<expr>] = v`: the property's name is computed (non-empty text; anything else writes nothing)
            let dynamic: String;
            let prop: &String = if let Some(inner) = prop.strip_prefix('[').and_then(|p| p.strip_suffix(']')) {
                match core.eval_json(inner, &vars, &extra) {
                    Ok(J::String(s)) if !s.is_empty() => { dynamic = s; &dynamic }
                    _ => continue,
                }
            } else { prop };
            let Ok(v) = core.eval_json(value, &vars, &extra) else { continue };
            if target == LLM_CALL { self.calls.push(v); continue; }
            let tgt = if is_me(target) { Some(eid.to_string()) } else { core.eval_ref(target, &vars, &extra) };
            let Some(tgt) = tgt else { continue };
            if !core.docs.contains_key(&tgt) {
                // creation: `<Class>:<key>` of a class the pack owns or imports
                let Some((cls, _)) = tgt.split_once(':') else { continue };
                if self.owner.get(cls).is_none() || self.may_write(pack, cls).is_err() { continue; }
                core.load(tgt.clone(), cls.to_string(), Default::default(), None, now);
                self.out.push(json!({"eid": tgt, "born": cls}));
                self.birth(core, &tgt, now, seq);
            }
            let cls = core.docs.get(&tgt).map(|e| e.cls.clone()).unwrap_or_default();
            if let Err(why) = self.may_write(pack, &cls) {
                self.receipt(core, eid, name, why, J::Null, seq, now);
                continue;
            }
            if tgt == eid && prop == wprop {   // rule 6: own name inside its setter stores directly
                self.emit_unchecked(core, work, &tgt, prop, v, &format!("direct:{}", name), seq, now);
            } else {
                self.emit(core, work, &tgt, prop, v, name, seq, now);
            }
        }
        Ok(())
    }

    /// `<target> = Class(name: value, ...)`: the target is the identity. Created by the creation path of a write (the class must be one
    /// the pack owns or imports: refused as a receipt otherwise), its arguments set as its first properties; idempotent (an entity that
    /// exists is not created again, a property that has a value keeps it). A target whose eid prefix is not the class (the class cannot
    /// be read from the eid, e.g. `membrane::Config:...` built as `Voice(...)`) is stamped `parents: ['cls:<class>']`, the ISA
    /// kind-mirror; the entity is the class its eid names (`Config`), as the Python seat made it.
    fn construct(&mut self, core: &mut Core, work: &mut VecDeque<Write>, eid: &str, name: &str, pack: &str, target: &str, value: &str,
                 binds: &[(String, crate::vm::Item)], now: f64, seq: &mut i64) {
        let (vars, extra) = scope(eid, binds, now);
        let tgt = match core.eval_target(target, &vars, &extra) {
            Ok(t) => t,
            Err(why) => { self.receipt(core, eid, name, format!("error: constructor {} = ...: {}; nothing was created", target, why.strip_prefix("error: ").unwrap_or(&why)), J::Null, seq, now); return; }
        };
        let (cls, args) = match core.eval_ctor(value, &vars, &extra) {
            Ok(x) => x,
            Err(why) => { self.receipt(core, eid, name, format!("error: constructor {}: {}; nothing was created", tgt, why), J::Null, seq, now); return; }
        };
        if self.owner.get(&cls).is_none() {
            self.receipt(core, eid, name, format!("error: constructor {}({}): no pack owns the class {} (a pack owns a class it declares a getter or a setter on)", cls, tgt, cls), J::Null, seq, now);
            return;
        }
        if let Err(why) = self.may_write(pack, &cls) { self.receipt(core, eid, name, why, J::Null, seq, now); return; }
        let mut args = args;
        if !core.docs.contains_key(&tgt) {
            // the class the eid names: after a namespace, up to the next colon
            let tail = tgt.rsplit_once("::").map_or(tgt.as_str(), |(_, r)| r);
            let own = tail.split(':').next().unwrap_or("").to_string();
            if let Err(why) = self.may_write(pack, &own) { self.receipt(core, eid, name, why, J::Null, seq, now); return; }
            if own != cls && !args.iter().any(|(k, _)| k == "parents") { args.push(("parents".into(), json!([format!("cls:{}", cls.to_lowercase())]))); }
            core.load(tgt.clone(), own.clone(), Default::default(), None, now);
            self.out.push(json!({"eid": tgt, "born": own}));
            self.birth(core, &tgt, now, seq);
        } else {
            let own = core.docs.get(&tgt).map(|e| e.cls.clone()).unwrap_or_default();
            if let Err(why) = self.may_write(pack, &own) { self.receipt(core, eid, name, why, J::Null, seq, now); return; }
        }
        for (prop, v) in args {
            if v.is_null() { continue; }
            if core.docs.get(&tgt).and_then(|e| e.props.get(&prop)).map_or(false, |old| !old.is_null()) { continue; }   // only what is unset
            self.emit(core, work, &tgt, &prop, v, name, seq, now);
        }
    }

    /// A write of a driver verb (File.write, Command.run, ...: drivers.rs) runs its effect off the engine; the verb's
    /// slot is consumed. Entity scripts and peers never trigger one (a refusal receipt says so).
    fn driver_effect(&mut self, core: &mut Core, work: &mut VecDeque<Write>, w: &Write, now: f64, seq: &mut i64) {
        #[cfg(feature = "store")]
        {
            if w.new.is_null() || w.new == json!("") { return; }
            let Some(cls) = core.docs.get(&w.eid).map(|e| e.cls.clone()) else { return };
            if !crate::drivers::VERBS.iter().any(|(c, v)| *c == cls && *v == w.prop) { return; }
            let props = core.docs.get(&w.eid).map(|e| e.props.clone()).unwrap_or_default();
            self.emit_unchecked(core, work, &w.eid, &w.prop.clone(), json!(""), "direct:consumed", seq, now);
            if w.writer.starts_with("script:") || w.writer.starts_with("peer") {
                let id = w.new.get("id").cloned().unwrap_or(J::Null);
                let rec = json!({"id": id, "status": "error", "rc": 2, "head": "refused: entity scripts and peers don't run effects"});
                self.emit_unchecked(core, work, &w.eid, &format!("{}_result", w.prop), rec, "driver:refused", seq, now);
                return;
            }
            if let Some(st) = &core.store { crate::drivers::effect(st, &w.eid, &cls, &w.prop, &w.new, &props); }
        }
        #[cfg(not(feature = "store"))]
        { let _ = (core, work, w, now, seq); }
    }

    /// The clock input: Wait.due is a getter on the clock (status armed and clock - armed_since >= every_s).
    pub fn tick(&mut self, core: &mut Core, host: &dyn Host, now: f64, seq: &mut i64) {
        if self.has_getter("Wait", "due") {
            self.sync(core, now, seq);
            self.mark(CLOCK, "");
            self.visited.clear();
            self.run(core, host, VecDeque::new(), now, seq);
            return;
        }
        let mut ws: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == "Wait").map(|(k, _)| k.clone()).collect();
        ws.sort();
        for w in ws {
            let due = matches!(core.eval_json("this.status == 'armed' and clock - (this.last_check_ts or this.armed_since or this.armed_ts or clock) >= (this.every_s or 10)", &me(&w), &[("clock", json!(now))]), Ok(J::Bool(true)));
            let was = core.docs.get(&w).and_then(|e| e.props.get("due")) == Some(&json!(true));
            if due != was {
                *seq += 1;
                let wr = Write { eid: w.clone(), prop: "due".into(), new: json!(due), old: J::Null, writer: "field:due".into(), chain: json!({}), seq: *seq };
                self.out.push(json!({"eid": wr.eid, "prop": wr.prop, "new": wr.new, "writer": wr.writer}));
                core.hear(wr.clone(), None, now);
                self.react(core, host, wr, now, seq);
            }
        }
    }
}
