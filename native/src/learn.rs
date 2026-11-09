//! #994 row 6: self's learned triggers (membrane/ch_learn.py, #576 part C) as FIELD RULES, native VM only.
//! A candidate is no free-floating WHEN/WHERE/WRITE entity: it is an entity of class `Learned` whose fields are
//! the rule (`cause`, `on_cls`, `target`, `to`, `copy`/`lit`) and whose account is computed from the write stream
//! (`balance`, `hits`, `state`, `predicted`). The rule fires as an on-setter on its cause prop. OFF by default
//! (Fields.learn is None); the parity gate builds it.
//! GUESS: a write W is explained by a cause among the last LOOK_BACK writes of its chain. SCORE: a later write in the
//! chain to the predicted path with the predicted value is a hit; only hits are refunded. ENERGY: every own write
//! costs WRITE_COST (one awake second); asleep nothing is spent, so nothing is learned or fired.
//! STAY POSITIVE: balance > 0 is a proposal (live on a granted path), else shadow; prune drops balance < -WRITE_COST.
use std::collections::{HashMap, VecDeque};

use serde_json::{json, Map, Value as J};

use crate::vm::{Core, Host, Write};

pub const CLASS: &str = "Learned";
pub const WRITE_COST: f64 = 1.0 / (16.0 * 3600.0);
pub const REFUND: f64 = 2.0;
pub const LOOK_BACK: usize = 3;
pub const GUESS_PER_CHAIN: usize = 3;
pub const CANDIDATES_MAX: usize = 256;
pub const CHAINS_KEPT: usize = 512;

fn scalar(v: &J) -> bool { matches!(v, J::Bool(_) | J::Number(_) | J::String(_) | J::Null) }
fn ident(s: &str) -> bool { !s.is_empty() && !s.starts_with(|c: char| c.is_ascii_digit()) && s.chars().all(|c| c.is_alphanumeric() || c == '_') }
/// a copy, not a coincidence: equal and the same type (true is not 1)
fn same(a: &J, b: &J) -> bool { scalar(a) && std::mem::discriminant(a) == std::mem::discriminant(b) && a == b }
fn chain_id(w: &Write) -> String { w.chain.get("id").map(|i| match i { J::String(s) => s.clone(), J::Null => String::new(), x => x.to_string() }).unwrap_or_default() }
fn fnv(s: &str) -> u64 { let mut h: u64 = 0xcbf29ce484222325; for b in s.bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); } h }

pub struct Cand {
    pub eid: String,
    pub cls: String,
    pub cause: String,
    /// the entity it writes: None = the cause's own entity (`?x`), else a fixed one (`?y`)
    pub target: Option<String>,
    pub to: String,
    pub copy: bool,
    pub lit: J,
    pub bal: f64,
    pub hits: u64,
}

#[derive(Default)]
pub struct Learner {
    pub cands: Vec<Cand>,
    pub awake: bool,
    pub spent: f64,
    pub refunded: f64,
    /// paths (`eid.prop`) the operator accepted: a proposal there fires live
    pub granted: Vec<String>,
    recent: HashMap<String, VecDeque<Write>>,
    order: VecDeque<String>,
    pending: HashMap<String, Vec<(String, String, J)>>,
    guessed: HashMap<String, usize>,
}

impl Learner {
    pub fn new() -> Self { Learner { awake: true, ..Default::default() } }
    fn charge(&mut self, i: usize) { self.spent += WRITE_COST; self.cands[i].bal -= WRITE_COST; }
    pub fn balance(&self, eid: &str) -> f64 { self.cands.iter().find(|c| c.eid == eid).map(|c| c.bal).unwrap_or(0.0) }
    pub fn state(&self, eid: &str) -> &'static str { if self.balance(eid) > 0.0 { "proposal" } else { "shadow" } }

    /// the candidate's fields on its entity, so the whole-field checksum sees the rule and its account
    fn show(&self, core: &mut Core, i: usize, predicted: Option<J>, now: f64) {
        let c = &self.cands[i];
        let mut p = Map::new();
        p.insert("cause".into(), json!(c.cause)); p.insert("on_cls".into(), json!(c.cls));
        p.insert("target".into(), c.target.as_ref().map(|t| json!(t)).unwrap_or(J::Null));
        p.insert("to".into(), json!(c.to)); p.insert("copy".into(), json!(c.copy)); p.insert("lit".into(), c.lit.clone());
        p.insert("balance".into(), json!(c.bal)); p.insert("hits".into(), json!(c.hits)); p.insert("state".into(), json!(self.state(&c.eid)));
        p.insert("owner".into(), json!("self"));
        let keep = core.docs.get(&c.eid).and_then(|e| e.props.get("predicted").cloned());
        if let Some(k) = predicted.or(keep) { p.insert("predicted".into(), k); }
        core.load(c.eid.clone(), CLASS.into(), p, None, now);
    }

    fn remove(&mut self, core: &mut Core, eid: &str) {
        self.cands.retain(|c| c.eid != eid);
        core.docs.remove(eid);
    }

    /// One heard write: score the predictions on its path, then guess.
    pub fn observe(&mut self, core: &mut Core, w: &Write, now: f64) {
        if ["trigger:", "adapter:", "field:"].iter().any(|p| w.writer.starts_with(p)) || w.prop.is_empty() { return }   // its own acts never confirm or teach
        let ch = chain_id(w);
        if ch.is_empty() { return }
        let path = format!("{}.{}", w.eid, w.prop);
        if let Some(p) = self.pending.get_mut(&ch) {
            let (hit, keep): (Vec<_>, Vec<_>) = std::mem::take(p).into_iter().partition(|(_, pp, v)| *pp == path && *v == w.new);
            *p = keep;
            for (cand, _, _) in hit {
                if let Some(i) = self.cands.iter().position(|c| c.eid == cand) {
                    self.refunded += REFUND * WRITE_COST;
                    self.cands[i].bal += REFUND * WRITE_COST;
                    self.cands[i].hits += 1;
                    self.show(core, i, None, now);
                }
            }
        }
        if !self.recent.contains_key(&ch) {
            self.order.push_back(ch.clone());
            while self.order.len() > CHAINS_KEPT { if let Some(o) = self.order.pop_front() { self.recent.remove(&o); self.pending.remove(&o); self.guessed.remove(&o); } }
        }
        let causes: Vec<Write> = self.recent.get(&ch).map(|d| d.iter().cloned().collect()).unwrap_or_default();
        let d = self.recent.entry(ch.clone()).or_default();
        d.push_back(w.clone());
        while d.len() > LOOK_BACK { d.pop_front(); }
        for c in causes { self.guess(core, &c, w, &ch, now); }
    }

    fn guess(&mut self, core: &mut Core, c: &Write, w: &Write, ch: &str, now: f64) {
        if !self.awake || self.guessed.get(ch).copied().unwrap_or(0) >= GUESS_PER_CHAIN { return }
        let (ce, we) = match (core.docs.get(&c.eid), core.docs.get(&w.eid)) { (Some(a), Some(b)) => (a.cls.clone(), b.cls.clone()), _ => return };
        if ce == CLASS || we == CLASS || c.prop == "position" || w.prop == "position" || !ident(&c.prop) || !ident(&w.prop) { return }
        let same_e = c.eid == w.eid;
        let mut values: Vec<(bool, J)> = vec![];
        if same(&w.new, &c.new) { values.push((true, J::Null)); }
        if scalar(&w.new) { values.push((false, w.new.clone())); }
        for (copy, lit) in values {
            let spec = json!([c.prop, ce, if same_e { J::Null } else { json!(w.eid) }, w.prop, copy, lit]).to_string();
            let eid = format!("membrane::{}:cand-{:012x}", CLASS, fnv(&spec) & 0xffff_ffff_ffff);
            if core.docs.contains_key(&eid) { continue }
            if self.guessed.get(ch).copied().unwrap_or(0) >= GUESS_PER_CHAIN || !self.awake { return }
            if self.cands.len() >= CANDIDATES_MAX {
                let worst = self.cands.iter().min_by(|a, b| a.bal.partial_cmp(&b.bal).unwrap()).map(|c| (c.eid.clone(), c.bal));
                match worst { Some((e, b)) if b < 0.0 => self.remove(core, &e), _ => return }
            }
            self.cands.push(Cand { eid: eid.clone(), cls: ce.clone(), cause: c.prop.clone(), target: if same_e { None } else { Some(w.eid.clone()) }, to: w.prop.clone(), copy, lit, bal: 0.0, hits: 0 });
            let i = self.cands.len() - 1;
            self.charge(i);                                   // charged only for what was defined
            *self.guessed.entry(ch.to_string()).or_insert(0) += 1;
            self.show(core, i, None, now);
        }
    }

    /// A write moved a cause prop: each matching candidate fires. Shadow: its prediction is written onto ITSELF;
    /// live (a proposal on a granted path): the real write is returned for the caller to emit.
    pub fn fire(&mut self, core: &mut Core, host: &dyn Host, w: &Write, now: f64) -> Vec<(String, String, J, String)> {
        let mut live = vec![];
        if w.writer.starts_with("field:learn") || !self.awake { return live }
        let ch = chain_id(w);
        for i in 0..self.cands.len() {
            let (cause, cls) = (self.cands[i].cause.clone(), self.cands[i].cls.clone());
            if cause != w.prop || !core.is_a(host, &w.eid, &cls) { continue }
            let c = &self.cands[i];
            let tgt = c.target.clone().unwrap_or_else(|| w.eid.clone());
            let val = if c.copy { w.new.clone() } else { c.lit.clone() };
            let path = format!("{}.{}", tgt, c.to);
            let go = self.state(&c.eid) == "proposal" && self.granted.contains(&path);
            let (eid, to) = (c.eid.clone(), c.to.clone());
            self.charge(i);
            if !ch.is_empty() { self.pending.entry(ch.clone()).or_default().push((eid.clone(), path.clone(), val.clone())); }
            if go { live.push((tgt, to, val.clone(), eid)); }
            self.show(core, i, if go { None } else { Some(json!({"path": path, "value": val, "chain": ch})) }, now);
        }
        live
    }

    /// Sleep: a candidate whose balance is below -1 x WRITE_COST goes (untested ones stay).
    pub fn prune(&mut self, core: &mut Core) -> Vec<String> {
        let mut bad: Vec<(f64, String)> = self.cands.iter().filter(|c| c.bal < -WRITE_COST).map(|c| (c.bal, c.eid.clone())).collect();
        bad.sort_by(|a, b| a.partial_cmp(b).unwrap());
        for (_, e) in &bad { self.remove(core, e); }
        bad.into_iter().map(|(_, e)| e).collect()
    }
}
