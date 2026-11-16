//! The graph link's engine side (docs/graph-link.md, "Slice 2: engine interface"): which foreign `(eid, prop)` the VM reads (SUBSCRIPTIONS BY DEPENDENCY), the notices
//! the host takes at the end of a round (`subs`, `unsubs`, `due`), and the replica notes (`Replica`). The engine never talks to a network.
use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value as J};

/// How long a subscription nobody reads any more lives, in ENGINE time (the `now` of the rounds, seconds): decision 5, end of round plus 500 ms.
pub const LINK_GRACE_S: f64 = 0.5;

/// What the owner said about a replicated entity.
#[derive(Clone, Debug, PartialEq)]
pub enum ReplicaState { Live, Gone, Refused(String) }

/// A foreign entity's local copy: the last revision applied (opaque to the engine), the props a value arrived for, and the owner's verdict.
#[derive(Clone, Debug)]
pub struct Replica { pub rev: f64, pub have: BTreeSet<String>, pub state: ReplicaState }

/// The VM-provided attributes: never replicated, never subscribed.
pub fn is_special(attr: &str) -> bool { matches!(attr, "eid" | "cls" | "position" | "updated" | "touched" | "pinned") }

/// The subscription table: the keys engaged (with the engine time since which nobody reads them, None while someone does) and the keys the host was told about.
#[derive(Default)]
pub struct LinkSubs {
    pub engaged: BTreeMap<(String, String), Option<f64>>,
    pub announced: BTreeSet<(String, String)>,
    /// a getter's read list changed (or the table was cleared): the foreign keys of `deps` are collected again at the next round end
    pub dirty: bool,
    pub dep_keys: BTreeSet<(String, String)>,
}

impl LinkSubs {
    /// One round end. `held`: the keys a getter's read list holds (already filtered to what is subscribable); `read`: the keys any evaluation read in this round.
    pub fn update(&mut self, held: &BTreeSet<(String, String)>, read: &BTreeSet<(String, String)>, now: f64) {
        for k in held { self.engaged.insert(k.clone(), None); }
        for k in read { if !held.contains(k) { self.engaged.insert(k.clone(), Some(now)); } }
        let mut drop = vec![];
        for (k, idle) in self.engaged.iter_mut() {
            if held.contains(k) || read.contains(k) { continue; }
            match idle { None => *idle = Some(now), Some(t) => if now - *t >= LINK_GRACE_S { drop.push(k.clone()); } }
        }
        for k in drop { self.engaged.remove(&k); }
    }
    /// The engine time at which the next unsubscribe can be decided.
    pub fn due(&self) -> Option<f64> {
        self.engaged.values().flatten().map(|t| t + LINK_GRACE_S).fold(None, |a: Option<f64>, x| Some(a.map_or(x, |a| a.min(x))))
    }
    /// The deltas since the last call, per entity with the props named; the table of announced keys follows.
    pub fn drain(&mut self) -> (Vec<J>, Vec<J>) {
        let now_keys: BTreeSet<(String, String)> = self.engaged.keys().cloned().collect();
        let subs: Vec<_> = now_keys.difference(&self.announced).cloned().collect();
        let unsubs: Vec<_> = self.announced.difference(&now_keys).cloned().collect();
        self.announced = now_keys;
        (group(subs), group(unsubs))
    }
    /// Forget every key of entities starting with `prefix` (a retired namespace): no notice, the owner is gone.
    pub fn forget_prefix(&mut self, prefix: &str) {
        self.engaged.retain(|k, _| !k.0.starts_with(prefix));
        self.announced.retain(|k| !k.0.starts_with(prefix));
        self.dep_keys.retain(|k| !k.0.starts_with(prefix));
    }
    /// The props engaged for one entity.
    pub fn props_of(&self, eid: &str) -> Vec<String> {
        self.engaged.range((eid.to_string(), String::new())..).take_while(|(k, _)| k.0 == eid).map(|(k, _)| k.1.clone()).collect()
    }
}

fn group(keys: Vec<(String, String)>) -> Vec<J> {
    let mut out: Vec<(String, Vec<String>)> = vec![];
    for (e, p) in keys {
        match out.last_mut() { Some((le, ps)) if *le == e => ps.push(p), _ => out.push((e, vec![p])) }
    }
    out.into_iter().map(|(e, p)| json!({"eid": e, "props": p})).collect()
}
