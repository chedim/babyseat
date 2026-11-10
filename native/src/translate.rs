//! #994 row 9: the WHEN/WHERE/WRITE TRANSLATION LAYER, native VM only. A trigger spec (the shape goalgraph, ch_learn and
//! the seed packs write today) is translated into a FIELD: an on-setter (WHEN the prop is written and a condition holds,
//! write another prop) or a counting getter. The layer exists only for the switch: fields.rs no longer carries a
//! hand-written setter table -- `goalgraph_fields` reads goalgraph's triggers as data (packs/goalgraph.json) and
//! translates them. A spec the layer cannot express as a field is an Err (it stays a trigger until its row moves it).
use serde_json::Value as J;

use crate::fields::{OnSet, LLM_CALL};
use crate::vm::Spec;

pub const GOALGRAPH: &str = include_str!("../packs/goalgraph.json");

#[derive(Debug, Clone, PartialEq)]
pub enum Field {
    Set(OnSet),
    /// `open_blockers` counting: `blocker` (class) being `finished` unblocks a `target` (class) that lists it in `blocks`
    Count { blocker: String, finished: String, target: String },
}

fn s(j: &J) -> String { j.as_str().unwrap_or("").to_string() }
fn var(j: &J) -> String { s(j).trim_start_matches('?').to_string() }

/// `v.` -> `x.` outside string literals, only at a name boundary; `drop.eid` -> `x.goal` (the goal the task names).
fn rename(src: &str, v: &str, other: Option<&str>) -> String {
    let cs: Vec<char> = src.chars().collect();
    let mut out = String::new();
    let (mut i, mut q) = (0usize, false);
    let at = |i: usize, pat: &str| -> bool {
        let p: Vec<char> = pat.chars().collect();
        i + p.len() <= cs.len() && cs[i..i + p.len()] == p[..] && (i == 0 || !(cs[i - 1].is_alphanumeric() || "_.".contains(cs[i - 1])))
    };
    while i < cs.len() {
        if cs[i] == '\'' { q = !q; }
        if !q {
            if let Some(o) = other { let p = format!("{}.eid", o); if at(i, &p) { out.push_str("this.goal"); i += p.chars().count(); continue; } }
            let p = format!("{}.", v);
            if at(i, &p) { out.push_str("this."); i += p.chars().count(); continue; }
        }
        out.push(cs[i]); i += 1;
    }
    out
}

fn num(j: &J) -> String { match j.as_f64() { Some(f) if f.fract() == 0.0 => format!("{}", f as i64), Some(f) => format!("{}", f), None => s(j) } }

pub fn translate(name: &str, sp: &Spec) -> Result<Field, String> {
    let kind = |a: &Vec<J>| s(&a[0]);
    let mut isa: Vec<(String, String)> = vec![];
    let mut filters: Vec<String> = vec![];
    for a in &sp.wh {
        match kind(a).as_str() {
            "isa" => isa.push((var(&a[1]), s(&a[2]))),
            "filter" => filters.push(s(&a[1])),
            k => return Err(format!("{}: WHERE {} has no field form", name, k)),
        }
    }
    let cls_of = |v: &str| isa.iter().find(|(x, _)| x == v).map(|(_, c)| c.clone());
    let (tv, tp) = match sp.target.trim_start_matches('?').split_once('.') {
        Some((a, b)) if sp.target.starts_with('?') => (a.to_string(), b.to_string()),
        _ => (String::new(), sp.target.clone()),
    };
    // WHEN every N: the due getter's setter
    if sp.when.len() == 1 && kind(&sp.when[0]) == "every" {
        let cls = cls_of(&tv).ok_or(format!("{}: no class for ?{}", name, tv))?;
        return Ok(Field::Set(OnSet { name: name.into(), cls, prop: "due".into(), when: "this.due".into(),
            writes: vec![("this".into(), tp, rename(&sp.expr, &tv, None))], els: vec![], pack: String::new() }));
    }
    let mut ch: Option<(String, String)> = None;
    let mut conds: Vec<String> = vec![];
    let mut pend: Vec<(String, String, String)> = vec![];
    for a in &sp.when {
        match kind(a).as_str() {
            "changed" => {
                let (v, p) = s(&a[1]).trim_start_matches('?').split_once('.').map(|(a, b)| (a.to_string(), b.to_string())).ok_or(format!("{}: changed needs ?v.prop", name))?;
                if ch.is_some() { return Err(format!("{}: two changed atoms", name)); }
                ch = Some((v, p));
            }
            "below" => pend.push((s(&a[1]), "<".into(), num(&a[2]))),
            k => return Err(format!("{}: WHEN {} has no field form", name, k)),
        }
    }
    let (v, prop) = ch.ok_or(format!("{}: no changed atom", name))?;
    let cls = cls_of(&v).ok_or(format!("{}: no class for ?{}", name, v))?;
    // the counting shape: b.status == 'S' and t.status == 'open' and t.eid in (b.blocks or [])
    if tp == "open_blockers" && sp.expr == format!("{}.open_blockers - 1", tv) && prop == "status" {
        let fin = filters.first().and_then(|f| f.split('\'').nth(1)).unwrap_or("").to_string();
        let want = format!("{b}.status == '{f}' and {t}.status == 'open' and {t}.eid in ({b}.blocks or [])", b = v, f = fin, t = tv);
        if filters.len() == 1 && filters[0] == want {
            return Ok(Field::Count { blocker: cls, finished: fin, target: cls_of(&tv).ok_or(format!("{}: no class for ?{}", name, tv))? });
        }
        return Err(format!("{}: a counting trigger of another shape", name));
    }
    let sec = isa.iter().map(|(x, _)| x.clone()).find(|x| *x != v);
    for (e, op, n) in pend { conds.push(format!("{} {} {}", rename(&e, &v, None), op, n)); }
    for f in &filters {
        let f = match &sec {
            Some(g) => match f.strip_prefix(&format!("{g}.eid == {v}.goal and ", g = g, v = v)) {
                Some(rest) => rest.to_string(),
                None => return Err(format!("{}: the join to ?{} is not on the goal", name, g)),
            },
            None => f.clone(),
        };
        conds.push(rename(&f, &v, None));
    }
    let target = if tv == v { "this".to_string() } else if Some(&tv) == sec.as_ref() { "this.goal".to_string() } else if tv.is_empty() { if tp == LLM_CALL { LLM_CALL.to_string() } else { return Err(format!("{}: target {}", name, tp)) } } else { return Err(format!("{}: target ?{}", name, tv)) };
    let prop_w = if target == LLM_CALL { String::new() } else { tp };
    Ok(Field::Set(OnSet { name: name.into(), cls, prop, when: if conds.is_empty() { "True".into() } else { conds.join(" and ") },
        writes: vec![(target, prop_w, rename(&sp.expr, &v, sec.as_deref()))], els: vec![], pack: String::new() }))
}

/// A pack of triggers (JSON: [{name, when, where, write:[target, expr]}]) -> specs.
pub fn specs_of(pack: &str) -> Vec<(String, Spec)> {
    let v: J = serde_json::from_str(pack).unwrap_or(J::Null);
    let atoms = |j: Option<&J>| j.and_then(|x| x.as_array()).map(|a| a.iter().filter_map(|x| x.as_array().cloned()).collect::<Vec<_>>()).unwrap_or_default();
    v.as_array().cloned().unwrap_or_default().iter().map(|t| {
        let wr = t.get("write").and_then(|x| x.as_array()).cloned().unwrap_or_default();
        (s(&t["name"]), Spec { when: atoms(t.get("when")), wh: atoms(t.get("where")), target: s(wr.first().unwrap_or(&J::Null)), expr: s(wr.get(1).unwrap_or(&J::Null)) })
    }).collect()
}

/// goalgraph's setters: its triggers translated. What the field form adds to a translated setter (doc section 5): a
/// condition that reads two props is set on both (`-on-status`), and a Wait that arms stamps `armed_since` for the due getter.
pub const GOALGRAPH_CH: &str = include_str!("../packs/goalgraph.c1f");

/// The goal graph's setters, read from its cliffhanger text (packs/goalgraph.c1f).
pub fn goalgraph_setters() -> Vec<OnSet> {
    crate::syntax::parse_full(GOALGRAPH_CH).expect("packs/goalgraph.c1f parses (tests/syntax_994.rs)").setters
}

/// The goal graph's getters, read from its cliffhanger text (packs/goalgraph.c1f).
pub fn goalgraph_getters() -> Vec<crate::fields::Getter> {
    crate::syntax::parse_full(GOALGRAPH_CH).expect("packs/goalgraph.c1f parses").getters
}

/// The same setters translated from the old WHEN/WHERE/WRITE triggers (packs/goalgraph.json): the migration path,
/// held equal to the text pack by tests/syntax_994.rs.
pub fn translated_goalgraph_setters() -> Vec<OnSet> {
    let mut out = vec![];
    for (name, sp) in specs_of(GOALGRAPH) {
        let Ok(Field::Set(mut st)) = translate(&name, &sp) else { continue };
        if name == "wait-armed" { st.writes.push(("this".into(), "armed_since".into(), "clock".into())); }
        let twin = if name == "ready" || name == "wait-armed" { let mut t = st.clone(); t.name = format!("{}-on-status", name); t.prop = "status".into(); Some(t) } else { None };
        out.push(st);
        if let Some(t) = twin { out.push(t); }
    }
    out
}

/// goalgraph's counting getters, as (blocker, finished, target)
pub fn goalgraph_counts() -> Vec<(String, String, String)> {
    specs_of(GOALGRAPH).iter().filter_map(|(n, sp)| match translate(n, sp) { Ok(Field::Count { blocker, finished, target }) => Some((blocker, finished, target)), _ => None }).collect()
}
