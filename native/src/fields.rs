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
}

impl Fields {
    pub fn goalgraph() -> Self { Fields { setters: on_setters(), calls: vec![], writes: 0, banner: false } }

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
