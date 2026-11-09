//! #994 row 3: banner rules as computed fields vs a hand-coded reference of graphbanner.walk's seed
//! rules (the Python lane is not importable here; the reference mirrors it line for line).
//! Replay one journal; after EVERY write the Chat's materialised `banner` must equal the reference.
use membrane_vm::banner;
use membrane_vm::fields::Fields;
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

fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn st(c: &Core, e: &str, p: &str) -> String { c.docs.get(e).and_then(|d| d.props.get(p)).map(|v| match v { J::String(s) => s.clone(), o => o.to_string() }).unwrap_or_default() }
fn name(c: &Core, e: &str) -> String { let n = st(c, e, "name"); if n.is_empty() { e.rsplit(':').next().unwrap().to_string() } else { n } }

/// graphbanner.walk's four seed rules, by hand.
fn reference(c: &Core, sid: &str) -> Vec<String> {
    let mut ms: Vec<(f64, String)> = c.docs.iter().filter(|(_, e)| e.cls == "Message" && e.props.get("sid") == Some(&json!(sid)))
        .map(|(k, e)| (e.props.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0), k.clone())).collect();
    ms.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap().then(a.1.cmp(&b.1)));
    let recent: Vec<String> = ms.into_iter().take(8).map(|m| m.1).collect();
    let edges = |subj: &str, rel: &str| -> Vec<String> {
        let mut v: Vec<(f64, String, String)> = c.docs.iter().filter(|(_, e)| e.cls == "Edge"
            && e.props.get("superseded") != Some(&json!(true)) && e.props.get("subj") == Some(&json!(subj))
            && (match rel.strip_suffix('*') { Some(p) => e.props.get("rel").and_then(|r| r.as_str()).unwrap_or("").starts_with(p), None => e.props.get("rel") == Some(&json!(rel)) }))
            .map(|(k, e)| (e.props.get("first_ts").and_then(|v| v.as_f64()).unwrap_or(0.0), k.clone(), e.props.get("obj").and_then(|v| v.as_str()).unwrap_or("").to_string())).collect();
        v.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
        v.into_iter().map(|x| x.2).collect()
    };
    let mut out: Vec<String> = vec![];
    let mut push = |l: String| { let l = l.split_whitespace().collect::<Vec<_>>().join(" "); if !l.is_empty() && !out.contains(&l) && out.len() < 12 { out.push(l); } };
    for m in &recent { for d in edges(m, "asks:*") { if st(c, &d, "status") == "open" { push(format!("open decision: {}", name(c, &d))); } } }
    let mut regs: Vec<&String> = c.docs.iter().filter(|(_, e)| e.cls == "Register" && e.props.get("person") == Some(&json!(sid))).map(|(k, _)| k).collect();
    regs.sort();
    for r in regs { for x in edges(r, "holds") { push(format!("\"{}\" = {}", st(c, r, "pronoun"), name(c, &x))); } }
    for m in &recent { for x in edges(m, "refers-to") { push(format!("named: {} ({})", name(c, &x), st(c, &x, "status")).replace(" ()", "")); } }
    let ran_on = c.docs.get("rule:ran").map(|e| e.props.get("retired") != Some(&json!(true))).unwrap_or(false);
    if ran_on { for m in &recent { for p in edges(m, "led-to") { for r in edges(&p, "result") { push(format!("ran: {} (rc {})", name(c, &p), st(c, &r, "rc")).replace(" (rc )", "")); } } } }
    out
}

fn journal() -> Vec<(String, String, J)> {
    let mut j: Vec<(String, String, J)> = vec![];
    let mut w = |e: &str, p: &str, v: J| j.push((e.into(), p.into(), v));
    w("m:1", "sid", json!("c1")); w("m:1", "ts", json!(1.0));
    w("m:2", "sid", json!("c1")); w("m:2", "ts", json!(2.0));
    w("m:3", "sid", json!("c1")); w("m:3", "ts", json!(3.0));
    w("m:9", "sid", json!("other")); w("m:9", "ts", json!(9.0));
    w("card:q", "name", json!("which runner")); w("card:q", "status", json!("open"));
    w("edge:1", "subj", json!("m:1")); w("edge:1", "rel", json!("asks:why")); w("edge:1", "obj", json!("card:q")); w("edge:1", "first_ts", json!(1.5));
    w("edge:9", "subj", json!("m:9")); w("edge:9", "rel", json!("asks:why")); w("edge:9", "obj", json!("card:q")); w("edge:9", "first_ts", json!(9.5));
    w("card:q", "status", json!("closed"));
    w("card:q", "status", json!("open"));
    w("reg:1", "person", json!("c1")); w("reg:1", "pronoun", json!("it"));
    w("task:x", "name", json!("the build")); w("task:x", "status", json!("open"));
    w("edge:2", "subj", json!("reg:1")); w("edge:2", "rel", json!("holds")); w("edge:2", "obj", json!("task:x")); w("edge:2", "first_ts", json!(2.5));
    w("edge:3", "subj", json!("m:2")); w("edge:3", "rel", json!("refers-to")); w("edge:3", "obj", json!("task:x")); w("edge:3", "first_ts", json!(2.6));
    w("task:x", "status", json!("done"));
    w("proc:p", "name", json!("cargo test"));
    w("res:1", "rc", json!(0));
    w("edge:4", "subj", json!("m:3")); w("edge:4", "rel", json!("led-to")); w("edge:4", "obj", json!("proc:p")); w("edge:4", "first_ts", json!(3.1));
    w("edge:5", "subj", json!("proc:p")); w("edge:5", "rel", json!("result")); w("edge:5", "obj", json!("res:1")); w("edge:5", "first_ts", json!(3.2));
    w("edge:3", "superseded", json!(true));
    w("rule:ran", "retired", json!(true));
    w("rule:ran", "retired", json!(false));
    for i in 10..22 { let e = format!("m:{}", i); w(&e, "sid", json!("c1")); w(&e, "ts", json!(10.0 + i as f64)); }   // pushes m:1..m:3 out of the window
    j
}

fn cls_of(e: &str) -> &'static str {
    match e.split(':').next().unwrap() { "m" => "Message", "edge" => "Edge", "reg" => "Register", "card" => "Decision", "task" => "Task", "proc" => "Proc", "res" => "Result", _ => "Other" }
}

/// replay; returns (the reference after every write, the field's banner after every write, the core)
fn run(fields_on: bool) -> (Vec<String>, Vec<Vec<String>>, Core) {
    let mut c = Core::new();
    c.load("chat:c1".into(), "Chat".into(), props(json!({"sid": "c1"})), None, 0.0);
    banner::load_seed(&mut c);
    let mut f = Fields::goalgraph();
    f.banner = fields_on;
    let (mut seq, mut now) = (0i64, 1.0);
    let (mut refs, mut got) = (vec![], vec![]);
    for (e, p, v) in journal() {
        seq += 1; now += 1.0;
        let w = Write { eid: e.clone(), prop: p, new: v, old: J::Null, writer: "person:ann".into(), chain: json!({}), seq };
        if !c.docs.contains_key(&e) { c.load(e.clone(), cls_of(&e).into(), Map::new(), None, now); }
        c.hear(w.clone(), None, now);
        f.react(&mut c, &H, w, now, &mut seq);
        refs.push(reference(&c, "c1").join(" | "));
        got.push(c.docs["chat:c1"].props.get("banner").and_then(|b| b.as_array()).map(|a| a.iter().map(|x| x.as_str().unwrap().to_string()).collect()).unwrap_or_default());
    }
    (refs, got, c)
}

#[test]
fn banner_field_equals_reference_after_every_write() {
    let (refs, got, _) = run(true);
    for (i, (r, g)) in refs.iter().zip(got.iter()).enumerate() {
        assert_eq!(r, &g.join(" | "), "write #{}", i);
    }
    // the journal is not vacuous: every seed rule showed a line at some point, and the cap held
    let all: String = got.iter().flatten().cloned().collect::<Vec<_>>().join("\n");
    for needle in ["open decision: which runner", "\"it\" = the build", "named: the build (open)", "ran: cargo test (rc 0)"] {
        assert!(all.contains(needle), "never saw {:?} in\n{}", needle, all);
    }
    assert!(got.iter().all(|g| g.len() <= 12));
    println!("banner after the last write: {:?}", got.last().unwrap());
}

#[test]
fn banner_off_writes_nothing() {
    let (_, got, c) = run(false);
    assert!(got.iter().all(|g| g.is_empty()));
    assert!(c.docs["chat:c1"].props.get("banner").is_none());
}

#[test]
fn banner_is_a_pure_function_of_the_field_and_idempotent() {
    let (_, _, mut c) = run(true);
    let a = banner::walk(&mut c, "c1");
    let b = banner::walk(&mut c, "c1");
    assert_eq!(a, b);
    assert_eq!(c.docs["chat:c1"].props.get("banner"), Some(&json!(a)));
    // a chat nothing talked in shows nothing
    assert!(banner::walk(&mut c, "nobody").is_empty());
}

#[test]
fn a_user_defined_rule_is_data() {
    let (_, _, mut c) = run(true);
    c.load("rule:cards".into(), "BannerRule".into(), props(json!({"name": "cards", "order": 5, "where": [["recent", "?m"], ["edge", "?m", "asks:*", "?d"]], "show": "card: {d.name} [{d.status}]"})), None, 0.0);
    let got = banner::walk(&mut c, "c1");
    let _ = &got;
    // a broken rule never stops the others
    c.load("rule:bad".into(), "BannerRule".into(), props(json!({"name": "bad", "order": 1, "where": [["nope", "?m"]], "show": "x"})), None, 0.0);
    assert_eq!(banner::walk(&mut c, "c1"), got);
}
