//! The spec harness: a spec is data -- {name, packs: [paths], given: [{eid, class, props}], writes: [{eid, prop, value}],
//! expect: [{eid, prop, equals | none | contains}]}. It loads the packs, creates the given entities, performs the writes in
//! order, settles the engine until it is quiet and checks every expectation. -> the failures (empty = the spec passes).
use crate::fields::Fields;
use crate::store::Store;
use crate::vm::{Core, Host, Write};
use serde_json::{json, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn pack_name(text: &str) -> String {
    text.lines().find_map(|l| l.strip_prefix("PACK ")).and_then(|r| r.split_whitespace().next()).unwrap_or("pack").to_string()
}

/// Settle until the engine is quiet: nothing running outside it and a round that changes nothing.
fn quiet(c: &mut Core, f: &mut Fields) {
    let wt = c.store.as_ref().map(|s| s.waiter());
    let t = std::time::Instant::now();
    let mut seq = 100;
    loop {
        f.settle(c, &H, 3.0, &mut seq);
        let busy = wt.as_ref().map(|w| w.pending()).unwrap_or(0);
        if busy == 0 || t.elapsed().as_secs() >= 10 { break; }
        if let Some(w) = &wt { w.wait(Some(std::time::Duration::from_millis(20))); }
    }
    f.settle(c, &H, 3.0, &mut seq);
}

/// A property, or a path into it: `reply.body.tags.1` (an object key, or an index into a list); anything missing is null.
fn at_path(props: &serde_json::Map<String, J>, path: &str) -> J {
    if let Some(v) = props.get(path) { return v.clone(); }            // a property whose own name has dots wins
    let mut parts = path.split('.');
    let Some(first) = parts.next() else { return J::Null };
    let mut cur = props.get(first).cloned().unwrap_or(J::Null);
    for p in parts {
        cur = match &cur {
            J::Object(m) => m.get(p).cloned().unwrap_or(J::Null),
            J::Array(a) => p.parse::<usize>().ok().and_then(|i| a.get(i).cloned()).unwrap_or(J::Null),
            _ => J::Null,
        };
    }
    cur
}

/// The entity's properties, compact, for a failure message.
fn dump(p: &serde_json::Map<String, J>) -> String {
    let t = J::Object(p.clone()).to_string();
    if t.len() > 900 { format!("{}...", &t[..900]) } else { t }
}

pub fn run(spec: &J) -> Vec<String> {
    let name = spec.get("name").and_then(|n| n.as_str()).unwrap_or("(unnamed)").to_string();
    let Some(expect) = spec.get("expect").and_then(|e| e.as_array()).filter(|e| !e.is_empty()) else {
        return vec![format!("{name}: a spec needs `expect` (what is true at the end)")];
    };
    let mut f = Fields::goalgraph();
    for p in spec.get("packs").and_then(|p| p.as_array()).cloned().unwrap_or_default() {
        let path = p.as_str().unwrap_or("");
        let text = match std::fs::read_to_string(path) { Ok(t) => t, Err(e) => return vec![format!("{name}: cannot read pack {path}: {e}")] };
        if let Err(e) = f.set_pack(&pack_name(&text), &text) { return vec![format!("{name}: pack {path}: {e}")]; }
    }
    let mut c = Core::new();
    c.store = Some(Store::io_only(std::env::temp_dir().to_str().unwrap_or("/")));
    for g in spec.get("given").and_then(|g| g.as_array()).cloned().unwrap_or_default() {
        let (eid, cls) = (g["eid"].as_str().unwrap_or("").to_string(), g["class"].as_str().unwrap_or("").to_string());
        let props = g.get("props").and_then(|p| p.as_object()).cloned().unwrap_or_default();
        c.load(eid, cls, props, None, 0.0);
    }
    let mut seq = 1;
    f.rebuild(&mut c, 1.0, &mut seq);
    quiet(&mut c, &mut f);
    for w in spec.get("writes").and_then(|w| w.as_array()).cloned().unwrap_or_default() {
        let (eid, prop) = (w["eid"].as_str().unwrap_or("").to_string(), w["prop"].as_str().unwrap_or("").to_string());
        let old = c.docs.get(&eid).and_then(|e| e.props.get(&prop).cloned()).unwrap_or(J::Null);
        seq += 1;
        let wr = Write { eid, prop, new: w.get("value").cloned().unwrap_or(J::Null), old, writer: "person".into(), chain: json!({}), seq };
        c.hear(wr.clone(), None, 2.0);
        f.react(&mut c, &H, wr, 2.0, &mut seq);
        quiet(&mut c, &mut f);
    }
    let mut fails = Vec::new();
    for e in expect {
        let (eid, prop) = (e["eid"].as_str().unwrap_or(""), e["prop"].as_str().unwrap_or(""));
        let Some(ent) = c.docs.get(eid) else { fails.push(format!("{name}: {eid}.{prop}: no such entity")); continue };
        let got = at_path(&ent.props, prop);
        if let Some(want) = e.get("equals") {
            if &got != want { fails.push(format!("{name}: {eid}.{prop}: expected {want}, got {got}   [{}]", dump(&ent.props))); }
        } else if e.get("none").and_then(|n| n.as_bool()).unwrap_or(false) {
            if !got.is_null() { fails.push(format!("{name}: {eid}.{prop}: expected none, got {got}")); }
        } else if let Some(sub) = e.get("contains") {
            let ok = match (&got, sub) {
                (J::String(s), J::String(x)) => s.contains(x.as_str()),
                (J::Array(a), x) => a.contains(x),
                _ => false,
            };
            if !ok { fails.push(format!("{name}: {eid}.{prop}: expected it to contain {sub}, got {got}")); }
        } else {
            fails.push(format!("{name}: {eid}.{prop}: the expectation needs equals, none or contains"));
        }
    }
    fails
}
