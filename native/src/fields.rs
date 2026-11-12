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

/// writer prefix of an own-name store (rule 6): it fires getters but not setters
const DIRECT: &str = "field:direct:";

pub fn on_setters() -> Vec<OnSet> { crate::translate::goalgraph_setters() }

/// A getter declared in cliffhanger (`<Class>.<field> getter: <expr>`, #994 rule 2): the field's value is the
/// expression, recalculated when something it read changes. `this` is the entity; `clock` the time.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Getter {
    pub cls: String, pub prop: String, pub expr: String,
    /// how many written arms were folded into `expr` (the size caps hold per arm: the fold may be that many times longer)
    pub arms: usize,
}

/// The input key of a getter that reads `clock`.
const CLOCK: &str = "\u{0}clock";

/// Getter levels: a getter that reads another getter's field (by name) sits above it, so a round computes
/// lower levels first. A cycle keeps the level it reached (its closing edge reads the previous value, rule 8).
fn levels(gs: &[Getter]) -> Vec<usize> {
    let mut lv = vec![0usize; gs.len()];
    for _ in 0..gs.len() {
        let mut moved = false;
        for i in 0..gs.len() {
            for j in 0..gs.len() {
                if gs[j].prop != gs[i].prop && reads_name(&gs[i].expr, &gs[j].prop) && lv[i] <= lv[j] && lv[j] + 1 <= gs.len() {
                    lv[i] = lv[j] + 1; moved = true;
                }
            }
        }
        if !moved { break; }
    }
    lv
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

pub struct Fields {
    pub setters: Vec<OnSet>,
    /// the llm calls the plan fields asked for (the effect that is not a prop write)
    pub calls: Vec<J>,
    pub writes: u64,
    /// row 3: the Chat entities' `banner` getter (banner.rs) is on
    pub banner: bool,
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
    /// getters declared in cliffhanger, their levels, and their inputs: (eid, field) read -> instances (eid, getter)
    pub getters: Vec<Getter>,
    glevel: Vec<usize>,
    deps: std::collections::HashMap<(String, String), std::collections::BTreeSet<(String, usize)>>,
    reads: std::collections::HashMap<(String, usize), Vec<(String, String)>>,
    dirty: std::collections::BTreeSet<(usize, String, usize)>,
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
        let mut f = Fields { setters: on_setters(), calls: vec![], writes: 0, banner: false, recall: false, adapters: false, method_calls: vec![], learn: None, accepted: Default::default(), visited: Default::default(), refusals: vec![], out: vec![],
            getters: vec![], glevel: vec![], deps: Default::default(), reads: Default::default(), dirty: Default::default(),
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
        let asker = format!("{}\u{0}{}.{}", eid, g.cls, g.prop);
        let (r, reads) = core.eval_traced(&g.expr, &me(eid), &[("clock", json!(now))], host, &asker, g.arms.max(1));
        let key = (eid.to_string(), gi);
        if let Some(old) = self.reads.remove(&key) { for k in old { if let Some(s) = self.deps.get_mut(&k) { s.remove(&key); } } }
        let mut ins: Vec<(String, String)> = reads.into_iter().filter(|(e, p)| !(e == eid && *p == g.prop)).collect();
        if reads_name(&g.expr, "clock") { ins.push((CLOCK.into(), String::new())); }
        ins.sort(); ins.dedup();
        for k in &ins { self.deps.entry(k.clone()).or_default().insert(key.clone()); }
        self.reads.insert(key, ins);
        match r {
            Ok(v) => Some(v),
            Err(e) => {
                let reason = format!("error in getter {}.{}: {}; the field kept its value", g.cls, g.prop, e);
                self.receipt(core, eid, &format!("getter:{}", g.prop), reason, J::Null, seq, now);
                None
            }
        }
    }

    /// A write changed (eid, prop): every getter instance that read it, or that asked `referencing(_, prop)`, is dirty.
    fn mark(&mut self, eid: &str, prop: &str) {
        self.mark_key(&(eid.to_string(), prop.to_string()));
    }

    fn mark_key(&mut self, k: &(String, String)) {
        if let Some(s) = self.deps.get(k) {
            let hit: Vec<(String, usize)> = s.iter().cloned().collect();
            for (e, gi) in hit { self.dirty.insert((self.glevel[gi], e, gi)); }
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
        for gi in order {
            let cls = self.getters[gi].cls.clone();
            let mut es: Vec<String> = of_kind(core, &cls);
            es.sort();
            for e in es {
                let Some(v) = self.compute(core, None, &e, gi, now, seq) else { continue };
                let prop = self.getters[gi].prop.clone();
                if core.docs.get(&e).and_then(|d| d.props.get(&prop)) == Some(&v) { continue; }
                *seq += 1;
                let w = Write { eid: e.clone(), prop: prop.clone(), new: v, old: J::Null, writer: "field:rebuild".into(), chain: json!({}), seq: *seq };
                self.out.push(json!({"eid": w.eid, "prop": w.prop, "new": w.new, "writer": w.writer}));
                core.hear(w, None, now);
            }
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
        self.visited.clear();
        self.run(core, host, VecDeque::new(), now, seq);
    }

    /// Row 3: a banner input (Message/Register/Edge/BannerRule) was written: recompute each Chat's `banner`.
    fn banner_on(&mut self, core: &mut Core, work: &mut VecDeque<Write>, seq: &mut i64, now: f64) {
        let mut chats: Vec<(String, String)> = core.docs.iter().filter(|(_, e)| e.cls == crate::banner::CLASS_CHAT)
            .map(|(k, e)| (k.clone(), e.props.get("sid").and_then(|s| s.as_str()).unwrap_or(k).to_string())).collect();
        chats.sort();
        for (eid, sid) in chats {
            let lines = crate::banner::walk(core, &sid);
            self.emit_unchecked(core, work, &eid, "banner", json!(lines), "banner", seq, now);
        }
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

    fn step(&mut self, core: &mut Core, host: &dyn Host, work: &mut VecDeque<Write>, w: Write, now: f64, seq: &mut i64) {
        self.driver_effect(core, work, &w, now, seq);
        self.mark(&w.eid, &w.prop);
        self.mark_refs(&w.prop, &w.old, &w.new);
        {
            let cls = core.docs.get(&w.eid).map(|e| e.cls.clone()).unwrap_or_default();
            if w.prop == "status" && BLOCKER_CLASSES.contains(&cls.as_str()) && !self.has_getter("Task", "open_blockers") {
                self.recount(core, work, &w.eid, seq, now);
            }
        }
            if self.banner && w.prop != "banner" {   // any write can move a line (a card's status, a name): the getter reads the whole field
                self.banner_on(core, work, seq, now);
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
        let mut k = 0;
        while k < ws.len() {
            let (target, prop, value) = &ws[k];
            if target == crate::syntax::FOR {
                let mut depth = 0; let mut j = k + 1;
                while j < ws.len() {
                    if ws[j].0 == crate::syntax::FOR { depth += 1; }
                    if ws[j].0 == crate::syntax::END { if depth == 0 { break; } depth -= 1; }
                    j += 1;
                }
                let items = {
                    let (vars, extra) = scope(eid, binds, now);
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
            let (vars, extra) = scope(eid, binds, now);
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

    /// A write of a driver verb (File.write, Command.run, ...: drivers.rs) runs its effect off the engine; the verb's
    /// slot is consumed. Entity scripts and peers never trigger one (a refusal receipt says so).
    fn driver_effect(&mut self, core: &mut Core, work: &mut VecDeque<Write>, w: &Write, now: f64, seq: &mut i64) {
        #[cfg(feature = "python")]
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
        #[cfg(not(feature = "python"))]
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
