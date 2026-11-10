//! A getter may call the host (`recall(...)` and the other banner built-ins run by the seat), on live rounds only:
//! a silent rebuild or birth never calls out (it would run every chat's recall at boot). A host error is the
//! getter's error: a `refused` receipt on the entity, the field keeps its value.
use std::cell::RefCell;
use membrane_vm::fields::Fields;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};

struct H { calls: RefCell<Vec<(String, String)>>, fail: bool }
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
    fn call(&self, f: &str, args: &str) -> Option<Result<String, String>> {
        self.calls.borrow_mut().push((f.to_string(), args.to_string()));
        if self.fail { return Some(Err("the store is away".into())); }
        match f {
            "recall" => Some(Ok(json!([{"n": 1, "fact": "cats chase mice"}, {"n": 2, "fact": "dogs bark"}]).to_string())),
            _ => None,
        }
    }
}

const PACK: &str = "PACK banner 1\nBanner.rows getter: recall(this.ask, this.k)\nBanner.lines getter: [prop(r, 'fact') for r in (this.rows or [])]\n";

fn world(fail: bool) -> (Core, Fields, H) {
    let mut f = Fields::goalgraph();
    f.set_pack("banner", PACK).unwrap();
    let mut c = Core::new();
    c.load("Banner:owner:s1".into(), "Banner".into(), json!({"k": 2}).as_object().unwrap().clone(), None, 0.0);
    (c, f, H { calls: RefCell::new(vec![]), fail })
}

fn ask(c: &mut Core, f: &mut Fields, h: &H, text: &str, seq: &mut i64) {
    *seq += 1;
    let w = Write { eid: "Banner:owner:s1".into(), prop: "ask".into(), new: json!(text), old: J::Null, writer: "person".into(), chain: json!({}), seq: *seq };
    c.hear(w.clone(), None, 5.0);
    f.react(c, h, w, 5.0, seq);
}

#[test]
fn a_getter_calls_the_host_on_a_live_round_and_never_at_a_rebuild() {
    let (mut c, mut f, h) = world(false);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    assert!(h.calls.borrow().is_empty(), "a silent rebuild never calls out");
    ask(&mut c, &mut f, &h, "what do cats do?", &mut seq);
    assert_eq!(h.calls.borrow().len(), 1);
    assert_eq!(h.calls.borrow()[0], ("recall".to_string(), json!(["what do cats do?", 2]).to_string()));
    assert_eq!(c.docs["Banner:owner:s1"].props["lines"], json!(["cats chase mice", "dogs bark"]));
}

#[test]
fn a_host_error_is_the_getters_error_shown_on_the_entity() {
    let (mut c, mut f, h) = world(true);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    ask(&mut c, &mut f, &h, "what do cats do?", &mut seq);
    let refused = c.docs["Banner:owner:s1"].props.get("refused").cloned().unwrap_or(J::Null);
    assert!(refused.as_str().unwrap_or("").contains("the store is away"), "{refused}");
}
