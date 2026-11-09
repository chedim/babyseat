//! #994 row 3: the banner's graph-walk rules (membrane/graphbanner.py) as COMPUTED FIELDS, native VM.
//! A rule is an entity (class BannerRule: order, where, show). The relations the walk reads are
//! entities too: class Message (sid, ts), Register (person), Edge (subj, rel, obj, first_ts,
//! superseded). A Chat entity's `banner` prop is a getter: the walk's lines, a pure function of
//! those entities, recomputed when one of them is written (fields.rs `Fields::banner_on`) and
//! materialised only when it changes. Atoms: recent, mine, edge, filter. `isa`/`is` are NOT implemented (no seed rule uses them): a rule using one yields no lines.
//! Differences, stated: a name with no `name` prop falls back to the eid's last segment (python asks
//! convgraph._label); no per-person banneruse measuring (that is a write the host does).
use std::collections::HashMap;

use serde_json::{json, Value as J};

use crate::vm::Core;

pub const WINDOW: usize = 8;
pub const LINES: usize = 12;
pub const CLASS_RULE: &str = "BannerRule";
pub const CLASS_CHAT: &str = "Chat";

/// graphbanner.SEED, verbatim.
pub fn seed_rules() -> Vec<(&'static str, i64, J, &'static str)> {
    vec![
        ("open decisions", 10, json!([["recent", "?m"], ["edge", "?m", "asks:*", "?d"], ["filter", "d.status == 'open'"]]), "open decision: {d.name}"),
        ("pronouns", 20, json!([["mine", "?r"], ["edge", "?r", "holds", "?x"]]), "\"{r.pronoun}\" = {x.name}"),
        ("named", 30, json!([["recent", "?m"], ["edge", "?m", "refers-to", "?x"]]), "named: {x.name} ({x.status})"),
        ("ran", 40, json!([["recent", "?m"], ["edge", "?m", "led-to", "?p"], ["edge", "?p", "result", "?res"]]), "ran: {p.name} (rc {res.rc})"),
    ]
}

pub fn load_seed(core: &mut Core) {
    for (name, order, wh, show) in seed_rules() {
        let mut p = serde_json::Map::new();
        p.insert("name".into(), json!(name));
        p.insert("order".into(), json!(order));
        p.insert("where".into(), wh);
        p.insert("show".into(), json!(show));
        core.load(format!("rule:{}", name.replace(' ', "-")), CLASS_RULE.into(), p, None, 0.0);
    }
}

type B = HashMap<String, String>;

fn var(s: &J) -> Option<String> { s.as_str().and_then(|s| s.strip_prefix('?')).map(|s| s.to_string()) }
fn s(e: &crate::vm::Ent, k: &str) -> String { e.props.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string() }
fn f(e: &crate::vm::Ent, k: &str) -> f64 { e.props.get(k).and_then(|v| v.as_f64()).unwrap_or(0.0) }

fn bind_list(v: &str, out: Vec<B>, eids: &[String]) -> Vec<B> {
    let mut nxt = vec![];
    for b in out {
        if let Some(x) = b.get(v) { if eids.contains(x) { nxt.push(b.clone()); } }
        else { for e in eids { let mut c = b.clone(); c.insert(v.into(), e.clone()); nxt.push(c); } }
    }
    nxt
}

fn rule_ok(rel: &str, want: &str) -> bool {
    match want.strip_suffix('*') { Some(p) => rel.starts_with(p), None => rel == want }
}

fn fill(core: &Core, show: &str, b: &B) -> String {
    let mut line = String::new();
    let cs: Vec<char> = show.chars().collect();
    let mut i = 0;
    while i < cs.len() {
        if cs[i] == '{' {
            if let Some(end) = cs[i..].iter().position(|&c| c == '}') {
                let inner: String = cs[i + 1..i + end].iter().collect();
                if let Some((v, p)) = inner.split_once('.') {
                    if !v.is_empty() && !p.is_empty() && v.chars().chain(p.chars()).all(|c| c.is_alphanumeric() || c == '_') {
                        let val = b.get(v).and_then(|eid| {
                            let got = core.docs.get(eid).and_then(|d| d.props.get(p)).filter(|x| !x.is_null()).cloned();
                            match got { Some(x) => Some(x), None if p == "name" => Some(json!(eid.rsplit(':').next().unwrap_or(eid))), None => None }
                        });
                        if let Some(x) = val {
                            let t = match &x { J::String(t) => t.clone(), o => o.to_string() };
                            let t: String = t.split_whitespace().collect::<Vec<_>>().join(" ");
                            line.extend(t.chars().take(160));
                        }
                        i += end + 1;
                        continue;
                    }
                }
            }
        }
        line.push(cs[i]);
        i += 1;
    }
    // an empty "()" / "(rc )" goes, with the space before it
    let cs: Vec<char> = line.chars().collect();
    let mut out: Vec<char> = vec![];
    let mut i = 0;
    while i < cs.len() {
        if cs[i] == '(' {
            let mut j = i + 1;
            if cs[j..].starts_with(&['r', 'c']) { j += 2; while j < cs.len() && cs[j].is_whitespace() { j += 1; } }
            if j < cs.len() && cs[j] == ')' {
                while out.last().map(|c| c.is_whitespace()).unwrap_or(false) { out.pop(); }
                i = j + 1;
                continue;
            }
        }
        out.push(cs[i]);
        i += 1;
    }
    out.into_iter().collect::<String>().split_whitespace().collect::<Vec<_>>().join(" ")
}

/// The walk for one chat: every rule's lines in (order, eid) order, unique, at most LINES.
pub fn walk(core: &mut Core, sid: &str) -> Vec<String> {
    let mut msgs: Vec<(f64, String)> = core.docs.iter().filter(|(_, e)| e.cls == "Message" && s(e, "sid") == sid).map(|(k, e)| (f(e, "ts"), k.clone())).collect();
    msgs.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1)));
    let recent: Vec<String> = msgs.into_iter().take(WINDOW).map(|m| m.1).collect();
    let mut mine: Vec<String> = core.docs.iter().filter(|(_, e)| e.cls == "Register" && s(e, "person") == sid).map(|(k, _)| k.clone()).collect();
    mine.sort();
    // live edges, each (subj, rel, obj, first_ts), sorted so ties are stable
    let mut edges: Vec<(String, String, String, f64)> = core.docs.iter()
        .filter(|(_, e)| e.cls == "Edge" && e.props.get("superseded") != Some(&json!(true)))
        .map(|(_, e)| (s(e, "subj"), s(e, "rel"), s(e, "obj"), f(e, "first_ts"))).collect();
    edges.sort_by(|a, b| a.3.partial_cmp(&b.3).unwrap_or(std::cmp::Ordering::Equal).then(a.0.cmp(&b.0)).then(a.1.cmp(&b.1)).then(a.2.cmp(&b.2)));
    let mut rules: Vec<(i64, String, Vec<Vec<J>>, String)> = core.docs.iter()
        .filter(|(_, e)| e.cls == CLASS_RULE && e.props.get("retired") != Some(&json!(true)) && e.props.get("kind").and_then(|k| k.as_str()) != Some("recall"))
        .map(|(k, e)| (e.props.get("order").and_then(|o| o.as_i64()).unwrap_or(100), k.clone(),
            e.props.get("where").and_then(|w| w.as_array()).map(|a| a.iter().map(|x| x.as_array().cloned().unwrap_or_default()).collect()).unwrap_or_default(),
            s(e, "show"))).collect();
    rules.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
    let (mut out, mut seen): (Vec<String>, Vec<String>) = (vec![], vec![]);
    for (_, _, wh, show) in rules {
        let mut bs: Vec<B> = vec![B::new()];
        let mut broke = false;
        for a in &wh {
            let kind = a.first().and_then(|k| k.as_str()).unwrap_or("");
            match kind {
                "recent" | "mine" => { let Some(v) = a.get(1).and_then(var) else { broke = true; break }; bs = bind_list(&v, bs, if kind == "recent" { &recent } else { &mine }); }
                "edge" => {
                    let (Some(lv), Some(rv)) = (a.get(1).and_then(var), a.get(3).and_then(var)) else { broke = true; break };
                    let rel = a.get(2).and_then(|r| r.as_str()).unwrap_or("");
                    let mut nxt = vec![];
                    for b in &bs {
                        if let Some(l) = b.get(&lv) {
                            for (su, r, ob, _) in &edges {
                                if su == l && rule_ok(r, rel) {
                                    if let Some(rb) = b.get(&rv) { if rb != ob { continue; } }
                                    let mut c = b.clone(); c.insert(rv.clone(), ob.clone()); nxt.push(c);
                                }
                            }
                        } else if let Some(r_) = b.get(&rv) {
                            let mut subs: Vec<&(String, String, String, f64)> = edges.iter().filter(|e| &e.2 == r_ && rule_ok(&e.1, rel)).collect();
                            subs.sort_by(|x, y| x.0.cmp(&y.0));
                            for e in subs { let mut c = b.clone(); c.insert(lv.clone(), e.0.clone()); nxt.push(c); }
                        }
                    }
                    bs = nxt;
                }
                "isa" | "is" => { broke = true; break }   // not used by the banner's seed rules
                "filter" => {
                    let src = a.get(1).and_then(|x| x.as_str()).unwrap_or("");
                    let mut nxt = vec![];
                    for b in bs {
                        let vars: Vec<(&str, &str)> = b.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
                        if matches!(core.eval_json(src, &vars, &[("chat", json!(sid))]), Ok(J::Bool(true))) { nxt.push(b); }
                    }
                    bs = nxt;
                }
                _ => { broke = true; break }
            }
        }
        if broke { continue; }          // one broken rule never stops the others
        for b in &bs {
            let ln = fill(core, &show, b);
            if !ln.is_empty() && !seen.contains(&ln) { seen.push(ln.clone()); out.push(ln); }
            if out.len() >= LINES { return out; }
        }
    }
    out
}
