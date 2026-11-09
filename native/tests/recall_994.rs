//! #994 row 4: the recall policy (#983) as guard setters + RecallAsk getters vs a hand-coded reference
//! (the Python lane is not importable here; the reference mirrors recall_policy.py line for line).
//! Replay one journal of dial and ask writes; after EVERY write the field's dials and ask getters must equal
//! the reference, and the whole-field checksum (FNV-1a) must be stable and sensitive.
use membrane_vm::fields::Fields;
use membrane_vm::recall;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Map, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

const ASK: &str = "ask:1";

/// the reference guard: written from recall_policy.py's contract, not from recall.rs
fn ok(p: &str, v: &J) -> bool {
    let int = |min: i64| v.as_i64().map(|n| n >= min).unwrap_or(false);
    let unit = || v.as_f64().map(|x| x >= 0.0 && x <= 1.0).unwrap_or(false);
    match p {
        "banner_n" | "pool" | "fact_chars" | "recent_turns" | "entity_pool_max" => int(1),
        "history_turns" | "rerank_top" => int(0),
        "no_topic_min" | "dedupe_jaccard" => unit(),
        "rrf_k" => v.as_f64().map(|x| x > 0.0).unwrap_or(false),
        "rerank" => v.is_boolean(),
        "header" => v.as_str().map(|s| !s.trim().is_empty()).unwrap_or(false),
        "reply_keys" => v.as_array().map(|a| a.iter().all(|x| x.is_string())).unwrap_or(false),
        "weights" => v.as_object().map(|o| ["default", "legacy", "rerank", "legacy_rerank"].iter().all(|k| o.get(*k).and_then(|s| s.as_object()).map(|s| s.contains_key("bias") && s.values().all(|x| x.is_number())).unwrap_or(false))).unwrap_or(false),
        "lane_rules" => v.as_object().map(|o| o.get("tool_needs_question").map(|x| x.is_boolean()).unwrap_or(false) && o.get("vector_lanes").is_some() && o.get("no_vector_lanes").is_some()).unwrap_or(false),
        _ => true,
    }
}

fn journal() -> Vec<(String, String, J)> {
    let mut j = vec![];
    let mut w = |e: &str, p: &str, v: J| j.push((e.to_string(), p.to_string(), v));
    w("recall:dials", "banner_n", json!(0));
    w("recall:dials", "banner_n", json!(5));
    w("recall:dials", "banner_n", json!(-3));
    w("recall:dials", "rrf_k", json!(-1));
    w("recall:dials", "rrf_k", json!(30));
    w("recall:dials", "no_topic_min", json!(1.5));
    w("recall:dials", "no_topic_min", json!(0.5));
    w("recall:dials", "rerank", json!("yes"));
    w("recall:dials", "header", json!("  "));
    w("recall:dials", "header", json!("RECALL:"));
    w("recall:dials", "reply_keys", json!(["a", 3]));
    w("recall:dials", "reply_keys", json!(["listener"]));
    w("recall:dials", "weights", json!({"default": {}}));
    w("recall:dials", "lane_rules", json!({"tool_needs_question": "no"}));
    w(ASK, "question", json!(true));
    w(ASK, "has_vec", json!(true));
    w(ASK, "replies", json!(false));
    w(ASK, "weights_fit", json!(true));
    w(ASK, "rerank", json!(true));
    w("recall:dials", "rerank_top", json!(7));
    w("recall:dials", "lane_rules", json!({"vector_lanes": ["ask", "direct", "tool"], "tool_needs_question": false, "no_vector_lanes": ["word"]}));
    w(ASK, "question", json!(false));
    w("recall:dials", "rerank", json!(false));
    w(ASK, "has_vec", json!(false));
    w(ASK, "replies", json!(true));
    w("recall:dials", "banner_n", json!(2));
    j
}

fn run(on: bool) -> (Vec<(Map<String, J>, [J; 3])>, Core) {
    let mut c = Core::new();
    c.load(ASK.into(), recall::CLASS_ASK.into(), Map::new(), None, 0.0);
    recall::load_seed(&mut c);
    let mut f = Fields::goalgraph();
    f.recall = on;
    let (mut seq, mut now) = (0i64, 1.0);
    let mut dials = recall::defaults();
    let mut flags: std::collections::HashMap<String, bool> = Default::default();
    let mut out = vec![];
    for (e, p, v) in journal() {
        seq += 1; now += 1.0;
        let w = Write { eid: e.clone(), prop: p.clone(), new: v.clone(), old: J::Null, writer: "person:ann".into(), chain: json!({}), seq };
        c.hear(w.clone(), None, now);
        f.react(&mut c, &H, w, now, &mut seq);
        if e == ASK { flags.insert(p.clone(), v == json!(true)); } else if ok(&p, &v) { dials.insert(p.clone(), v.clone()); }
        let fl = |k: &str| *flags.get(k).unwrap_or(&false);
        let r = dials["lane_rules"].clone();
        let need_q = r["tool_needs_question"] != json!(false);
        let (q, hv) = (fl("question"), fl("has_vec"));
        let mut lanes = Map::new();
        for n in r["vector_lanes"].as_array().unwrap() { let n = n.as_str().unwrap(); lanes.insert(n.into(), json!(if n == "tool" { hv && (q || !need_q) } else { hv })); }
        for n in r["no_vector_lanes"].as_array().unwrap() { lanes.insert(n.as_str().unwrap().into(), json!(true)); }
        let rank = if !fl("replies") && fl("weights_fit") { "legacy" } else { "default" };
        let top = if fl("rerank") && dials["rerank"] != json!(false) { dials["rerank_top"].as_i64().unwrap() } else { 0 };
        out.push((dials.clone(), [J::Object(lanes), json!(rank), json!(top)]));
        if on {
            let d = &c.docs[recall::DIALS].props;
            let a = c.docs.get(ASK).map(|x| x.props.clone()).unwrap_or_default();
            let (rd, ra) = out.last().unwrap();
            let tag = (e.clone(), p.clone());
            for (k, v) in rd { assert_eq!(d.get(k), Some(v), "dial {} after write {:?}", k, tag); }
            if a.contains_key("lanes") {
                assert_eq!(a.get("lanes"), Some(&ra[0]), "lanes after {:?}", tag);
                assert_eq!(a.get("rank_set"), Some(&ra[1]), "rank_set after {:?}", tag);
                assert_eq!(a.get("rerank_top"), Some(&ra[2]), "rerank_top after {:?}", tag);
            }
        }
    }
    (out, c)
}

/// FNV-1a over the sorted whole field: eid, class, each prop sorted.
fn checksum(c: &Core) -> u64 {
    let mut rows: Vec<String> = c.docs.iter().map(|(k, e)| {
        let mut ps: Vec<String> = e.props.iter().map(|(p, v)| format!("{}={}", p, v)).collect(); ps.sort();
        format!("{}|{}|{}", k, e.cls, ps.join(";"))
    }).collect();
    rows.sort();
    let mut h: u64 = 0xcbf29ce484222325;
    for b in rows.join("\n").bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
    h
}

#[test]
fn guards_and_getters_equal_reference_after_every_write() {
    let (refs, c) = run(true);
    assert_eq!(c.docs[recall::DIALS].props["banner_n"], json!(2));
    assert_eq!(refs.last().unwrap().1[2], json!(0));
    assert!(c.docs[ASK].props.contains_key("lanes"), "the ask getters never ran");
    println!("checksum {:016x}", checksum(&c));
}

#[test]
fn checksum_is_stable_and_sees_a_changed_dial() {
    let (_, a) = run(true);
    let (_, b) = run(true);
    assert_eq!(checksum(&a), checksum(&b));
    let mut c = run(true).1;
    c.docs.get_mut(recall::DIALS).unwrap().props.insert("rrf_k".into(), json!(61));
    assert_ne!(checksum(&a), checksum(&c));
}

#[test]
fn recall_off_defends_nothing() {
    let (_, c) = run(false);
    assert_eq!(c.docs[recall::DIALS].props["banner_n"], json!(2));
    assert!(c.docs[ASK].props.get("lanes").is_none());
}

#[test]
fn fuse_select_render_are_the_policy() {
    let mut c = Core::new(); recall::load_seed(&mut c);
    let l = vec![vec!["a".to_string(), "b".to_string()], vec!["b".to_string(), "c".to_string()]];
    let f = recall::fuse(&c, &l, None);
    assert_eq!(f.iter().map(|x| x.0.as_str()).collect::<Vec<_>>(), ["b", "a", "c"]);
    assert!((f[0].1 - (1.0 / 62.0 + 1.0 / 61.0)).abs() < 1e-12);
    let rows = vec![("one".to_string(), vec![1]), ("one!".to_string(), vec![2]), ("two".to_string(), vec![3]), ("three".to_string(), vec![4])];
    let alike = |a: &str, b: &str| b.starts_with(a);
    assert_eq!(recall::select(&c, &rows, &[3], None, &alike), ["one", "three"]);
    assert_eq!(recall::select(&c, &rows, &[], Some(2), &alike), ["one", "two"]);
    assert_eq!(recall::render(&c, &[]), None);
    assert_eq!(recall::render(&c, &["x".into()]).unwrap(), format!("{}\nx", recall::HEADER));
}
