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

/// `x` is the entity written; `clock` the time. Each write: (target entity expr, prop, value expr).
pub struct OnSet {
    pub name: &'static str,
    pub cls: &'static str,
    pub prop: &'static str,
    pub when: &'static str,
    pub writes: &'static [(&'static str, &'static str, &'static str)],
}

pub const LLM_CALL: &str = "membrane::llm.call";
const PLAN: &str = "{'about': x.eid, 'prompt': 'plan the goal: ' + x.replan, 'lane': 'planner'}";

pub fn on_setters() -> Vec<OnSet> {
    vec![
        // 3 ready (on the original trigger input, open_blockers, and on status)
        OnSet { name: "ready", cls: "Task", prop: "open_blockers", when: "x.open_blockers < 0.5 and x.status == 'open'", writes: &[("x", "status", "'owed'")] },
        OnSet { name: "ready-on-status", cls: "Task", prop: "status", when: "x.open_blockers < 0.5 and x.status == 'open'", writes: &[("x", "status", "'owed'")] },
        // 4 replan on task end
        OnSet { name: "replan-task", cls: "Task", prop: "status", when: "x.status in ('done', 'failed', 'stuck')", writes: &[("x.goal", "replan", "'task ' + x.eid + ' ' + x.status")] },
        // 5 replan on card
        OnSet { name: "replan-card", cls: "Decision", prop: "status", when: "x.status in ('closed', 'dropped')", writes: &[("x.goal", "replan", "'card ' + x.eid + ' ' + x.status")] },
        // 9 wait armed
        OnSet { name: "wait-armed", cls: "Wait", prop: "open_blockers", when: "x.open_blockers < 0.5 and x.status == 'open'", writes: &[("x", "status", "'armed'"), ("x", "armed_since", "clock")] },
        OnSet { name: "wait-armed-on-status", cls: "Wait", prop: "status", when: "x.open_blockers < 0.5 and x.status == 'open'", writes: &[("x", "status", "'armed'"), ("x", "armed_since", "clock")] },
        // 10 wait check: the due getter's on-setter (adapters' check branch)
        OnSet { name: "wait-check", cls: "Wait", prop: "due", when: "x.due", writes: &[("x", "check", "'due'")] },
        // 11 plan
        OnSet { name: "plan", cls: "Goal", prop: "replan", when: "(x.status or 'open') == 'open' and (x.replan or '') != ''", writes: &[(LLM_CALL, "", PLAN)] },
        // 12 plan after reopen
        OnSet { name: "plan-after-reopen", cls: "Goal", prop: "status", when: "x.status == 'open' and x.replan and x.planned_ts and x.replan != x.planned_replan", writes: &[(LLM_CALL, "", PLAN)] },
    ]
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
    /// last value each guarded dial accepted
    pub accepted: std::collections::HashMap<String, J>,
}

impl Fields {
    pub fn goalgraph() -> Self { Fields { setters: on_setters(), calls: vec![], writes: 0, banner: false, recall: false, adapters: false, method_calls: vec![], accepted: Default::default() } }

    /// Row 3: a banner input (Message/Register/Edge/BannerRule) was written: recompute each Chat's `banner`.
    fn banner_on(&mut self, core: &mut Core, work: &mut VecDeque<Write>, seq: &mut i64, now: f64) {
        let mut chats: Vec<(String, String)> = core.docs.iter().filter(|(_, e)| e.cls == crate::banner::CLASS_CHAT)
            .map(|(k, e)| (k.clone(), e.props.get("sid").and_then(|s| s.as_str()).unwrap_or(k).to_string())).collect();
        chats.sort();
        for (eid, sid) in chats {
            let lines = crate::banner::walk(core, &sid);
            self.emit(core, work, &eid, "banner", json!(lines), "banner", seq, now);
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
            if let Some(b) = back { self.emit(core, work, &w.eid, &w.prop, b, "recall-guard", seq, now); }
            return;
        }
        if is_dial { self.accepted.insert(w.prop.clone(), w.new.clone()); }
        if is_ask && ["lanes", "rank_set", "rerank_top"].contains(&w.prop.as_str()) { return; }   // the ask getters themselves (the dial rerank_top is not one)
        let mut asks: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == crate::recall::CLASS_ASK).map(|(k, _)| k.clone()).collect();
        asks.sort();
        for a in asks { for (p, v) in crate::recall::ask_values(core, &a) { self.emit(core, work, &a, p, v, "recall", seq, now); } }
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
            self.emit(core, work, &w.eid, "invoke", json!(""), "call-consumed", seq, now);
        }
    }

    /// Row 5: the host's receipt for a run call (its rc, head, result), written on the object with the request's id.
    pub fn deliver(&mut self, core: &mut Core, object: &str, call: &str, rid: &str, rc: i64, head: &str, result: Option<J>, now: f64, seq: &mut i64) {
        if !core.docs.contains_key(object) { return; }
        let mut work = VecDeque::new();
        for (p, v) in crate::adapters::receipt(call, rid, rc, head, result) { self.emit(core, &mut work, object, &p, v, "call-receipt", seq, now); }
    }

    fn emit(&mut self, core: &mut Core, work: &mut VecDeque<Write>, eid: &str, prop: &str, new: J, writer: &str, seq: &mut i64, now: f64) {
        if core.docs.get(eid).map(|e| e.props.get(prop) == Some(&new)).unwrap_or(false) { return; }   // idempotent
        *seq += 1;
        self.writes += 1;
        let w = Write { eid: eid.into(), prop: prop.into(), new, old: J::Null, writer: format!("field:{}", writer), chain: json!({}), seq: *seq };
        core.hear(w.clone(), None, now);
        work.push_back(w);
    }

    /// The getter: every Task/Wait's open_blockers, recomputed when a blocker's status moved.
    fn recount(&mut self, core: &mut Core, work: &mut VecDeque<Write>, seq: &mut i64, now: f64) {
        let mut targets: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == "Task" || e.cls == "Wait").map(|(k, _)| k.clone()).collect();
        targets.sort();
        let mut blockers: Vec<String> = core.docs.iter().filter(|(_, e)| BLOCKER_CLASSES.contains(&e.cls.as_str())).map(|(k, _)| k.clone()).collect();
        blockers.sort();
        for t in targets {
            let mut n = 0i64;
            for b in &blockers {
                if let Ok(J::Bool(true)) = core.eval_json(OPEN_BLOCKERS_FILTER, &[("t", &t), ("b", b)], &[]) { n += 1; }
            }
            // an entity with no stored count and no open blocker stays as it is
            if core.docs.get(&t).map(|e| e.props.get("open_blockers").is_none()).unwrap_or(false) && n == 0 { continue; }
            self.emit(core, work, &t, "open_blockers", json!(n), "open_blockers", seq, now);
        }
    }

    /// React to one write (already applied to the mirror): getters, then the on-setters, until quiet.
    pub fn react(&mut self, core: &mut Core, host: &dyn Host, w: Write, now: f64, seq: &mut i64) {
        let mut work: VecDeque<Write> = VecDeque::from([w]);
        let mut guard = 0;
        while let Some(w) = work.pop_front() {
            guard += 1;
            if guard > 10_000 { break; }
            if w.prop == "status" && core.docs.get(&w.eid).map(|e| BLOCKER_CLASSES.contains(&e.cls.as_str())).unwrap_or(false) {
                self.recount(core, &mut work, seq, now);
            }
            if self.banner && w.prop != "banner" {   // any write can move a line (a card's status, a name): the getter reads the whole field
                self.banner_on(core, &mut work, seq, now);
            }
            if self.adapters { self.adapters_on(core, &mut work, &w, seq, now); }
            if self.recall { self.recall_on(core, &mut work, &w, seq, now); }
            let hits: Vec<usize> = (0..self.setters.len())
                .filter(|&i| self.setters[i].prop == w.prop && core.is_a(host, &w.eid, self.setters[i].cls)).collect();
            for i in hits {
                let (when, name) = (self.setters[i].when, self.setters[i].name);
                if !matches!(core.eval_json(when, &[("x", &w.eid)], &[("clock", json!(now))]), Ok(J::Bool(true))) { continue; }
                for (target, prop, value) in self.setters[i].writes.iter().copied() {
                    let Ok(v) = core.eval_json(value, &[("x", &w.eid)], &[("clock", json!(now))]) else { continue };
                    if target == LLM_CALL { self.calls.push(v); continue; }
                    let tgt = if target == "x" { Some(w.eid.clone()) } else {
                        match core.eval_json(target, &[("x", &w.eid)], &[]) { Ok(J::String(g)) => Some(g), _ => None } };
                    let Some(tgt) = tgt else { continue };
                    // a replan lands on a Goal only (the trigger's `isa ?g Goal`)
                    if target != "x" && core.docs.get(&tgt).map(|e| e.cls.as_str()) != Some("Goal") { continue; }
                    if core.docs.contains_key(&tgt) { self.emit(core, &mut work, &tgt, prop, v, name, seq, now); }
                }
            }
        }
    }

    /// The clock input: Wait.due is a getter on the clock (status armed and clock - armed_since >= every_s).
    pub fn tick(&mut self, core: &mut Core, host: &dyn Host, now: f64, seq: &mut i64) {
        let mut ws: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == "Wait").map(|(k, _)| k.clone()).collect();
        ws.sort();
        for w in ws {
            let due = matches!(core.eval_json("x.status == 'armed' and clock - x.armed_since >= x.every_s", &[("x", &w)], &[("clock", json!(now))]), Ok(J::Bool(true)));
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
