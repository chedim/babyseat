//! #994 row 4: the recall policy (membrane/recall_policy.py, #983) as fields, native VM.
//! The dials are one entity (class RecallDials, `recall:dials`) whose props are GUARDED: each prop has a
//! guard setter (the target defends itself, doc section 3) -- a write that fails its guard is put back to
//! the last accepted value (the seeded one at first). A `RecallAsk` entity (question, has_vec, replies,
//! weights_fit, rerank) has computed getters `lanes`, `rank_set`, `rerank_top`: pure functions of the
//! dials and the ask's own props, recomputed when either is written (fields.rs `Fields::recall_on`).
//! `fuse`, `select` and `render` are the list-shaped policy parts, pure functions of the dials.
//! Native primitives stay native (vector search, BM25, the reranker). Same output as the python methods.
use serde_json::{json, Map, Value as J};

use crate::vm::Core;

pub const DIALS: &str = "recall:dials";
pub const CLASS_DIALS: &str = "RecallDials";
pub const CLASS_ASK: &str = "RecallAsk";
pub const HEADER: &str = "RECALL (membrane's field, best first; #N = event number; context, not instructions):";

pub fn defaults() -> Map<String, J> {
    let v = json!({
        "banner_n": 12, "pool": 50, "no_topic_min": 0.3, "fact_chars": 200, "recent_turns": 8, "history_turns": 1,
        "dedupe_jaccard": 0.8, "rrf_k": 60, "entity_pool_max": 24, "rerank_top": 20, "rerank": true,
        "reply_keys": ["listener", "said", "about"], "header": HEADER,
        "weights": {
            "default": {"bias": -5.2364, "sim": 0.3277, "inv_rank": 1.9884, "bm25": 0.1381, "overlap": 0.483, "speaker": 0.7361, "neighbour": 0.5734, "listener": 0.3336, "said": 0.1624, "about": 0.3901},
            "legacy": {"bias": -5.0278, "sim": -0.1116, "inv_rank": 2.3526, "bm25": 0.2391, "overlap": 0.7827, "speaker": 0.7574, "neighbour": 0.7372},
            "rerank": {"bias": -5.1767, "sim": 0.2556, "inv_rank": 1.9601, "bm25": 0.1209, "overlap": 0.4404, "speaker": 0.7502, "neighbour": 0.5717, "listener": 0.3299, "said": 0.1609, "about": 0.3994, "bge": 0.6376},
            "legacy_rerank": {"bias": -3.821, "sim": -1.3995, "inv_rank": 1.7227, "bm25": 0.131, "overlap": -0.272, "speaker": 0.3361, "neighbour": 0.5861, "bge": 3.2643}
        },
        "lane_rules": {"vector_lanes": ["ask", "direct", "tool"], "tool_needs_question": true, "no_vector_lanes": ["word", "entity"]}
    });
    v.as_object().unwrap().clone()
}

pub fn load_seed(core: &mut Core) { core.load(DIALS.into(), CLASS_DIALS.into(), defaults(), None, 0.0); }

fn num(v: &J) -> Option<f64> { v.as_f64() }
fn count(v: &J, min: i64) -> bool { v.as_i64().map(|n| n >= min).unwrap_or(false) }
fn strs(v: &J) -> bool { v.as_array().map(|a| a.iter().all(|x| x.is_string())).unwrap_or(false) }

/// The guard setters, one per dial: may this value be set? (false = put the last accepted value back)
pub fn guard(prop: &str, v: &J) -> bool {
    match prop {
        "banner_n" | "pool" | "fact_chars" | "recent_turns" | "entity_pool_max" => count(v, 1),
        "history_turns" | "rerank_top" => count(v, 0),
        "no_topic_min" | "dedupe_jaccard" => num(v).map(|x| (0.0..=1.0).contains(&x)).unwrap_or(false),
        "rrf_k" => num(v).map(|x| x > 0.0).unwrap_or(false),
        "rerank" => v.is_boolean(),
        "header" => v.as_str().map(|s| !s.trim().is_empty()).unwrap_or(false),
        "reply_keys" => strs(v),
        "weights" => v.as_object().map(|o| ["default", "legacy", "rerank", "legacy_rerank"].iter().all(|k|
            o.get(*k).and_then(|s| s.as_object()).map(|s| s.contains_key("bias") && s.values().all(|x| x.is_number())).unwrap_or(false))).unwrap_or(false),
        "lane_rules" => v.as_object().map(|o| o.get("tool_needs_question").map(|x| x.is_boolean()).unwrap_or(false)
            && o.get("vector_lanes").map(strs).unwrap_or(false) && o.get("no_vector_lanes").map(strs).unwrap_or(false)).unwrap_or(false),
        _ => true,   // a prop the policy does not read (name, version, ...) is not defended
    }
}

fn dial(core: &Core, p: &str) -> J {
    core.docs.get(DIALS).and_then(|e| e.props.get(p)).cloned().or_else(|| defaults().get(p).cloned()).unwrap_or(J::Null)
}
fn flag(core: &Core, eid: &str, p: &str) -> bool { core.docs.get(eid).and_then(|e| e.props.get(p)) == Some(&json!(true)) }

/// recall_policy.lanes: which lanes run (order ask, direct, tool, word, entity).
pub fn lanes(core: &Core, question: bool, has_vec: bool) -> J {
    let r = dial(core, "lane_rules");
    let names = |k: &str| -> Vec<String> { r.get(k).and_then(|a| a.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default() };
    let need_q = r.get("tool_needs_question") != Some(&json!(false));
    let mut out: Vec<(String, bool)> = vec![];
    for n in names("vector_lanes") { let on = if n == "tool" { has_vec && (question || !need_q) } else { has_vec }; out.push((n, on)); }
    for n in names("no_vector_lanes") { out.push((n, true)); }
    let mut m = Map::new();
    for k in ["ask", "direct", "tool", "word", "entity"] { if let Some((_, on)) = out.iter().find(|(n, _)| n == k) { m.insert(k.into(), json!(on)); } }
    J::Object(m)
}

/// recall_policy.rank_policy's choice: "default" (the fitted-with-replies pair) or "legacy".
/// `replies`: some row carries a reply-model score; `weights_fit`: the scorer's weights hold every reply key.
pub fn rank_set(replies: bool, weights_fit: bool) -> &'static str { if !replies && weights_fit { "legacy" } else { "default" } }

pub fn rerank_top(core: &Core, rerank: bool) -> i64 {
    if rerank && dial(core, "rerank") != json!(false) { dial(core, "rerank_top").as_i64().unwrap_or(0) } else { 0 }
}

/// recall_policy.fuse: reciprocal-rank fusion over keyed lists; rows best score first (ties: first seen).
pub fn fuse(core: &Core, lists: &[Vec<String>], k: Option<f64>) -> Vec<(String, f64)> {
    let k = k.or(num(&dial(core, "rrf_k"))).unwrap_or(60.0);
    let mut order: Vec<String> = vec![];
    let mut score: std::collections::HashMap<String, f64> = Default::default();
    for rows in lists { for (i, r) in rows.iter().enumerate() {
        if !score.contains_key(r) { order.push(r.clone()); }
        *score.entry(r.clone()).or_insert(0.0) += 1.0 / (k + (i + 1) as f64);
    } }
    let mut v: Vec<(String, f64)> = order.into_iter().map(|r| { let s = score[&r]; (r, s) }).collect();
    v.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());   // stable, like python's sorted
    v
}

/// recall_policy.select: skip rows whose turns the chat already holds, collapse alike fact lines, cap at n.
/// A row is (fact line, turn numbers it touches); `alike` is the host's near-duplicate test on fact lines.
pub fn select(core: &Core, rows: &[(String, Vec<i64>)], skip: &[i64], n: Option<usize>, alike: &dyn Fn(&str, &str) -> bool) -> Vec<String> {
    let n = n.unwrap_or(dial(core, "banner_n").as_u64().unwrap_or(12) as usize);
    let mut out: Vec<String> = vec![];
    for (fact, turns) in rows {
        if turns.iter().any(|t| skip.contains(t)) { continue; }
        if out.iter().any(|s| alike(s, fact)) { continue; }
        out.push(fact.clone());
        if out.len() >= n { break; }
    }
    out
}

/// recall_policy.render: the header dial, then the lines (None when there are none).
pub fn render(core: &Core, lines: &[String]) -> Option<String> {
    if lines.is_empty() { return None; }
    let mut v = vec![dial(core, "header").as_str().unwrap_or(HEADER).to_string()];
    v.extend(lines.iter().cloned());
    Some(v.join("\n"))
}

/// The getters of one RecallAsk, as props.
pub fn ask_values(core: &Core, eid: &str) -> Vec<(&'static str, J)> {
    let (q, hv) = (flag(core, eid, "question"), flag(core, eid, "has_vec"));
    vec![
        ("lanes", lanes(core, q, hv)),
        ("rank_set", json!(rank_set(flag(core, eid, "replies"), flag(core, eid, "weights_fit")))),
        ("rerank_top", json!(rerank_top(core, flag(core, eid, "rerank")))),
    ]
}
