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

/// finished(b): Task/Wait -> status 'done'; Decision -> status 'closed' (doc section 5).
const OPEN_BLOCKERS_FILTER: &str = "t.eid in (b.blocks or []) and not ((b.cls == 'Decision' and b.status == 'closed') or (b.cls != 'Decision' and b.status == 'done'))";

/// `this` is the entity written (`x` is the old name, still bound for migrated scripts); `clock` the time.
/// Each write: (target entity expr, prop, value expr). The first branch is `WHEN when: writes`; `els` are the
/// `ELSE WHEN` branches in order, a plain `ELSE` being the condition `True`. The first true branch runs, only it.
/// Written in cliffhanger's text form (`syntax.rs`, packs/goalgraph.ch).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct OnSet {
    pub name: String,
    pub cls: String,
    pub prop: String,
    pub when: String,
    pub writes: Vec<(String, String, String)>,
    pub els: Vec<(String, Vec<(String, String, String)>)>,
}

/// The names a setter's expressions use for the entity written: `this`, and `x` for scripts written before the rename.
fn me(eid: &str) -> [(&'static str, &str); 2] { [("this", eid), ("x", eid)] }
fn is_me(target: &str) -> bool { target == "this" || target == "x" }

pub const LLM_CALL: &str = "membrane::llm.call";

/// writer prefix of an own-name store (rule 6): it fires getters but not setters
const DIRECT: &str = "field:direct:";

pub fn on_setters() -> Vec<OnSet> { crate::translate::goalgraph_setters() }

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
}

impl Fields {
    pub fn goalgraph() -> Self { Fields { setters: on_setters(), calls: vec![], writes: 0, banner: false, recall: false, adapters: false, method_calls: vec![], learn: None, accepted: Default::default(), visited: Default::default(), refusals: vec![] } }

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
            core.hear(r, None, now);   // a receipt, not queued: it triggers nothing
            return;
        }
        *seq += 1;
        self.writes += 1;
        let w = Write { eid: eid.into(), prop: prop.into(), new, old: J::Null, writer: format!("field:{}", writer), chain: json!({}), seq: *seq };
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
    pub fn react(&mut self, core: &mut Core, host: &dyn Host, w: Write, now: f64, seq: &mut i64) {
        self.visited.clear();
        self.visited.insert((w.eid.clone(), w.prop.clone()));   // the outside write starts the chain: returning to it is a loop
        let mut work: VecDeque<Write> = VecDeque::from([w]);
        while let Some(w) = work.pop_front() {
            if w.prop == "status" && core.docs.get(&w.eid).map(|e| BLOCKER_CLASSES.contains(&e.cls.as_str())).unwrap_or(false) {
                self.recount(core, &mut work, &w.eid, seq, now);
            }
            if self.banner && w.prop != "banner" {   // any write can move a line (a card's status, a name): the getter reads the whole field
                self.banner_on(core, &mut work, seq, now);
            }
            if self.learn.is_some() { self.learn_on(core, host, &mut work, &w, seq, now); }
            if self.adapters { self.adapters_on(core, &mut work, &w, seq, now); }
            if self.recall { self.recall_on(core, &mut work, &w, seq, now); }
            if w.writer.starts_with(DIRECT) { continue; }   // rule 6: an own-name store bypasses the setters (getters above still ran)
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
                for (target, prop, value) in writes {
                    let Ok(v) = core.eval_json(&value, &me(&w.eid), &[("clock", json!(now))]) else { continue };
                    if target == LLM_CALL { self.calls.push(v); continue; }
                    let tgt = if is_me(&target) { Some(w.eid.clone()) } else {
                        match core.eval_json(&target, &me(&w.eid), &[]) { Ok(J::String(g)) => Some(g), _ => None } };
                    let Some(tgt) = tgt else { continue };
                    // a replan lands on a Goal only (the trigger's `isa ?g Goal`)
                    if !is_me(&target) && core.docs.get(&tgt).map(|e| e.cls.as_str()) != Some("Goal") { continue; }
                    if !core.docs.contains_key(&tgt) { continue; }
                    if tgt == w.eid && prop == w.prop {   // rule 6: own name inside its setter stores directly
                        self.emit_unchecked(core, &mut work, &tgt, &prop, v, &format!("direct:{}", name), seq, now);
                    } else {
                        self.emit(core, &mut work, &tgt, &prop, v, &name, seq, now);
                    }
                }
            }
        }
    }

    /// The clock input: Wait.due is a getter on the clock (status armed and clock - armed_since >= every_s).
    pub fn tick(&mut self, core: &mut Core, host: &dyn Host, now: f64, seq: &mut i64) {
        let mut ws: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == "Wait").map(|(k, _)| k.clone()).collect();
        ws.sort();
        for w in ws {
            let due = matches!(core.eval_json("this.status == 'armed' and clock - this.armed_since >= this.every_s", &me(&w), &[("clock", json!(now))]), Ok(J::Bool(true)));
            let was = core.docs.get(&w).and_then(|e| e.props.get("due")) == Some(&json!(true));
            if due != was {
                *seq += 1;
                let wr = Write { eid: w.clone(), prop: "due".into(), new: json!(due), old: J::Null, writer: "field:due".into(), chain: json!({}), seq: *seq };
                core.hear(wr.clone(), None, now);
                self.react(core, host, wr, now, seq);
            }
        }
    }
}
