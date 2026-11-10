//! #994 row 5: calls as writes (#959/#956) as on-setters vs a hand-coded reference of adapters.py `_written_call`.
//! Replay one journal of `invoke` writes; after EVERY write the object's receipt props, its call path, the Method's
//! consumed slot and the asked effects must equal the reference; the whole-field checksum (FNV-1a) is stable and sensitive.
use membrane_vm::adapters::CLASS_METHOD;
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

const M: &str = "method:poke";
const OBJ: &str = "toy:tower";
const CALL: &str = "call:poke";

fn inv(req: J) -> J { json!({"object": OBJ, "call": CALL, "request": req}) }

fn journal() -> Vec<(String, String, J)> {
    let mut j = vec![];
    let mut w = |e: &str, p: &str, v: J| j.push((e.to_string(), p.to_string(), v));
    w(OBJ, CALL, json!({"id": "r1", "args": {"n": 3}}));               // the call path holds a request
    w(M, "invoke", inv(json!({"id": "r1", "args": {"n": 3}})));         // good: runs, effect asked
    w(M, "invoke", inv(json!("")));                                      // empty: nothing, keeps receipt
    w(OBJ, CALL, json!({"id": "r2", "args": {"conf": {"password": "x"}}}));
    w(M, "invoke", inv(json!({"id": "r2", "args": {"conf": {"password": "x"}}})));   // secret: refused, path cleared
    w(OBJ, CALL, json!({"id": "r3", "args": 5}));
    w(M, "invoke", inv(json!({"id": "r3", "args": 5})));                // malformed: refused
    w(M, "invoke", inv(json!({"id": " ", "args": {}})));                 // blank id
    w(M, "invoke", inv(json!({"id": "r4", "refused": "too deep"})));    // unwritable
    w(M, "invoke", json!({"object": OBJ, "call": "poke", "request": {"id": "r5"}}));   // not a call: path
    w(M, "invoke", inv(json!({"id": "r6"})));                           // no args: runs with {}
    w(M, "invoke", json!({"object": "tool:executor", "call": CALL, "request": {"id": "r7", "args": {}}}));   // the executor
    j
}

/// the reference: written from adapters.py's contract, not from adapters.rs
fn secret(v: &J, path: &str) -> Vec<String> {
    let bad = |k: &str| { let l = k.to_lowercase(); ["token", "key", "passw", "secret", "auth", "cookie", "credential"].iter().any(|s| l.contains(s)) };
    match v {
        J::Object(m) => m.iter().flat_map(|(k, x)| { let p = if path.is_empty() { k.clone() } else { format!("{}.{}", path, k) }; if bad(k) { vec![p] } else { secret(x, &p) } }).collect(),
        J::Array(a) => a.iter().flat_map(|x| secret(x, path)).collect(),
        _ => vec![],
    }
}

/// -> (receipt head, clears the path) for a refusal, None for a run, Err for nothing
fn reference(new: &J) -> Result<Option<(String, bool)>, ()> {
    let (obj, call, req) = (new["object"].as_str().unwrap_or(""), new["call"].as_str().unwrap_or(""), &new["request"]);
    if obj.is_empty() || !call.starts_with("call:") || req.is_null() || req == &json!("") { return Err(()); }
    if let Some(r) = req.get("refused").and_then(|r| r.as_str()).filter(|r| !r.is_empty()) { return Ok(Some((format!("refused: {}: nothing ran", r), true))); }
    let id = req.get("id").and_then(|i| i.as_str()).unwrap_or("");
    if !req.is_object() || id.trim().is_empty() || req.get("args").map(|a| !a.is_object()).unwrap_or(false) { return Ok(Some(("a call request is {id, args}: nothing ran".into(), true))); }
    let s = secret(req.get("args").unwrap_or(&json!({})), "");
    if !s.is_empty() { return Ok(Some((format!("refused: a written call can't carry secret-named args ({}); pass secrets through the model's own call instead", s.join(", ")), true))); }
    if obj == "tool:executor" { return Ok(Some((format!("refused: {} is the executor: run code instead", obj), false))); }
    Ok(None)
}

fn run(on: bool) -> (Core, Fields) {
    let mut c = Core::new();
    let mut mp = Map::new(); mp.insert("method_name".into(), json!("poke"));
    c.load(M.into(), CLASS_METHOD.into(), mp, None, 0.0);
    c.load(OBJ.into(), "Toy".into(), Map::new(), None, 0.0);
    c.load("tool:executor".into(), "Tool".into(), Map::new(), None, 0.0);
    let mut f = Fields::goalgraph();
    f.adapters = on;
    let (mut seq, mut now) = (0i64, 1.0);
    let mut runs = 0usize;
    for (e, p, v) in journal() {
        seq += 1; now += 1.0;
        let w = Write { eid: e.clone(), prop: p.clone(), new: v.clone(), old: J::Null, writer: "person:ann".into(), chain: json!({}), seq };
        c.hear(w.clone(), None, now);
        f.react(&mut c, &H, w, now, &mut seq);
        if !on || e != M { continue; }
        let obj = &c.docs[v["object"].as_str().unwrap()].props;
        let tag = format!("{} after {}", v, e);
        assert_eq!(c.docs[M].props.get("invoke"), Some(&json!("")), "the invoke slot is consumed: {}", tag);
        match reference(&v) {
            Err(()) => assert_eq!(f.method_calls.len(), runs, "nothing asked: {}", tag),
            Ok(None) => { runs += 1; assert_eq!(f.method_calls.len(), runs, "a run asked: {}", tag);
                let m = f.method_calls.last().unwrap();
                assert_eq!((m["object"].as_str(), m["call"].as_str(), m["method"].as_str()), (v["object"].as_str(), v["call"].as_str(), Some("poke"))); }
            Ok(Some((head, clear))) => {
                assert_eq!(f.method_calls.len(), runs, "a refusal runs nothing: {}", tag);
                let rec = &obj[&format!("{}_result", CALL)];
                assert_eq!((rec["status"].clone(), rec["rc"].clone(), rec["head"].clone()), (json!("error"), json!(2), json!(head)), "{}", tag);
                assert_eq!(obj[&format!("{}_rc", CALL)], json!(2));
                if clear && obj.contains_key(CALL) { assert_eq!(obj[CALL], json!(""), "path cleared: {}", tag); }
            }
        }
    }
    (c, f)
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
fn written_calls_equal_reference_after_every_write() {
    let (c, f) = run(true);
    assert_eq!(f.method_calls.len(), 2);   // r1 and r6
    println!("checksum {:016x}", checksum(&c));
}

#[test]
fn the_host_delivers_a_run_calls_receipt() {
    let (mut c, mut f) = run(true);
    let mut seq = 1000;
    f.deliver(&mut c, OBJ, CALL, "r1", 0, "ok", Some(json!({"n": 3})), 50.0, &mut seq);
    let rec = &c.docs[OBJ].props[&format!("{}_result", CALL)];
    assert_eq!((rec["id"].clone(), rec["status"].clone(), rec["result"].clone()), (json!("r1"), json!("ok"), json!({"n": 3})));
    assert_eq!(c.docs[OBJ].props[&format!("{}_rc", CALL)], json!(0));
}

#[test]
fn checksum_is_stable_and_sees_a_changed_receipt() {
    let (a, _) = run(true);
    let (b, _) = run(true);
    assert_eq!(checksum(&a), checksum(&b));
    let mut c = run(true).0;
    c.docs.get_mut(OBJ).unwrap().props.insert(format!("{}_rc", CALL), json!(0));
    assert_ne!(checksum(&a), checksum(&c));
}

#[test]
fn adapters_off_runs_nothing() {
    let (c, f) = run(false);
    assert!(f.method_calls.is_empty());
    assert!(c.docs[OBJ].props.get(&format!("{}_result", CALL)).is_none());
}
