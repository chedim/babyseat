//! The chain behind a null (operator, 2026-11-12: "they go through the definition chain and decide where it is better to handle
//! that null"). A getter whose value is null remembers why; `why_chain(eid, prop)` walks the inputs it read, back to the first
//! null that has a reason of its own: [{eid, prop, reason}] from the property asked about down to the origin. A getter that
//! errors is a null with the error as its reason (it no longer keeps a stale value).
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host};
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

fn world(pack: &str, ents: &[(&str, &str, J)], dir: Option<&str>) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("w", pack).unwrap();
    let mut c = Core::new();
    if let Some(d) = dir { c.store = Some(Store::io_only(d)); }
    for (e, cls, p) in ents { c.load(e.to_string(), cls.to_string(), p.as_object().unwrap().clone(), None, 0.0); }
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    (c, f)
}
fn origin(chain: &[J]) -> String { chain.last().and_then(|n| n["reason"].as_str()).unwrap_or("").to_string() }
fn names(chain: &[J]) -> Vec<String> { chain.iter().map(|n| n["prop"].as_str().unwrap().to_string()).collect() }

#[test]
fn a_null_traces_back_through_the_getters_that_passed_it_on_to_where_it_began() {
    let (c, f) = world("PACK w 1\nT.a getter: this.b + 1\nT.b getter: this.c * 2\nT.c getter: None('boom')\n", &[("T:1", "T", json!({}))], None);
    let chain = f.why_chain(&c, "T:1", "a");
    assert_eq!(names(&chain), vec!["a", "b", "c"], "{chain:?}");
    assert_eq!(origin(&chain), "boom");
    assert_eq!(chain[0]["eid"], json!("T:1"));
}

#[test]
fn an_absent_property_is_where_a_chain_begins_when_nothing_wrote_it() {
    let (c, f) = world("PACK w 1\nT.q getter: this.nope + 1\nT.r getter: this.q * 2\n", &[("T:1", "T", json!({"x": 1}))], None);
    let chain = f.why_chain(&c, "T:1", "r");
    assert_eq!(names(&chain), vec!["r", "q"], "{chain:?}");
    assert!(origin(&chain).contains("absent"), "{}", origin(&chain));
}

#[test]
fn a_value_that_is_not_null_has_no_chain() {
    let (c, f) = world("PACK w 1\nT.a getter: this.x + 1\n", &[("T:1", "T", json!({"x": 1}))], None);
    assert!(f.why_chain(&c, "T:1", "a").is_empty());
    assert!(f.why_chain(&c, "T:1", "missing").is_empty(), "an unknown property is not a chain");
}

#[test]
fn a_getter_that_errors_is_a_null_with_the_error_as_its_reason_not_a_stale_value() {
    let (mut c, mut f) = world("PACK w 1\nT.e getter: len(this.n)\nT.f getter: this.e + 1\n", &[("T:1", "T", json!({"n": [1, 2]}))], None);
    assert_eq!(c.docs["T:1"].props["e"], json!(2));
    // n becomes a number: len(3) is an error. The old value (2) must not stay.
    let w = membrane_vm::vm::Write { eid: "T:1".into(), prop: "n".into(), new: json!(3), old: json!([1, 2]), writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(&mut c, &H, w, 2.0, &mut seq);
    f.settle(&mut c, &H, 3.0, &mut seq);
    assert!(c.docs["T:1"].props.get("e").map(|v| v.is_null()).unwrap_or(true), "the stale 2 must not stay: {:?}", c.docs["T:1"].props);   // a null is an absent key
    let chain = f.why_chain(&c, "T:1", "f");
    assert_eq!(names(&chain), vec!["f", "e"], "{chain:?}");
    assert!(origin(&chain).contains("len"), "{}", origin(&chain));
}

#[test]
fn a_read_that_has_not_landed_is_pending_and_the_chain_says_what_is_awaited() {
    let d = std::env::temp_dir().join(format!("why-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    std::fs::write(d.join("a.txt"), "hello").unwrap();
    let (mut c, mut f) = world("PACK w 1\nFile.text getter: read(this.path)\n",
        &[("File:a", "File", json!({"path": "a.txt"}))], Some(d.to_str().unwrap()));
    let chain = f.why_chain(&c, "File:a", "text");
    assert_eq!(names(&chain), vec!["text"], "{chain:?}");
    assert!(origin(&chain).starts_with("pending: read("), "{}", origin(&chain));
    // it lands: no longer null, no chain
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    let mut s = 30;
    while wt.pending() > 0 && t.elapsed().as_secs() < 10 { wt.wait(Some(std::time::Duration::from_millis(50))); f.settle(&mut c, &H, 3.0, &mut s); }
    f.settle(&mut c, &H, 3.0, &mut s);
    assert_eq!(c.docs["File:a"].props["text"], json!("hello"));
    assert!(f.why_chain(&c, "File:a", "text").is_empty());
}
