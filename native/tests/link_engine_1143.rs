//! GRAPH LINK slice 2, the engine hooks (#1143; docs/graph-link.md, 15b): foreign reads (`pending`, `absent`, `refused`), subscriptions by dependency with the grace, inert replicas,
//! the notices, the namespace retire, and two engines over a frame pipe. Tests count work (subs, frames, evaluations), never wall-clock: the rounds' `now` is a number the test passes.
use membrane_vm::fields::{Fields, LinkVal};
use membrane_vm::host::{Host, Options};
use membrane_vm::vm::{Core, Host as VmHost, Write};
use serde_json::{json, Map, Value as J};

struct H;
impl VmHost for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

const SET: &str = "membrane::Setting:t";

struct Vm { c: Core, f: Fields, seq: i64 }
impl Vm {
    fn new(ns: &str, pack: &str) -> Vm {
        let mut f = Fields::goalgraph();
        f.set_pack("t", pack).unwrap();
        let mut c = Core::new();
        c.queue_writes = false;
        c.set_ns(ns);
        Vm { c, f, seq: 0 }
    }
    fn born(&mut self, eid: &str, props: J, now: f64) {
        let cls = membrane_vm::vm::class_of_eid(eid);
        self.f.arrive(&mut self.c, eid.into(), cls, props.as_object().unwrap().clone(), now);
        self.f.settle(&mut self.c, &H, now, &mut self.seq);
    }
    fn write(&mut self, eid: &str, prop: &str, v: J, now: f64) {
        self.seq += 1;
        let old = self.c.docs.get(eid).and_then(|e| e.props.get(prop).cloned()).unwrap_or(J::Null);
        let w = Write { eid: eid.into(), prop: prop.into(), new: v, old, writer: "person".into(), chain: json!({}), seq: self.seq };
        self.c.hear(w.clone(), None, now);
        let mut s = self.seq;
        self.f.react(&mut self.c, &H, w, now, &mut s);
        self.seq = s;
    }
    fn settle(&mut self, now: f64) { let mut s = self.seq; self.f.settle(&mut self.c, &H, now, &mut s); self.seq = s; }
    fn val(&mut self, eid: &str, props: J, rev: f64, now: f64) {
        let mut s = self.seq;
        self.f.apply_val(&mut self.c, &H, eid, rev, LinkVal::Props(props.as_object().unwrap().clone()), now, &mut s);
        self.seq = s;
    }
    fn prop(&self, eid: &str, p: &str) -> J { self.c.docs.get(eid).and_then(|e| e.props.get(p).cloned()).unwrap_or(J::Null) }
    fn why(&self, eid: &str, p: &str) -> String { self.c.why.get(&(eid.to_string(), p.to_string())).cloned().unwrap_or_default() }
}

const PACK_ONE: &str = "PACK t 1\nLight.title getter: membrane::Setting:t.val\n";

#[test]
fn a_foreign_read_is_pending_and_makes_one_subscription() {
    let mut v = Vm::new("p1", PACK_ONE);
    v.born("Light:a", json!({"x": 1}), 1.0);
    assert_eq!(v.prop("Light:a", "title"), J::Null);
    assert_eq!(v.why("Light:a", "title"), "pending: membrane::Setting:t.val");
    let (subs, unsubs, _) = v.f.link_drain();
    assert_eq!(subs, vec![json!({"eid": SET, "props": ["val"]})]);
    assert!(unsubs.is_empty());
    assert!(v.f.link_drain().0.is_empty(), "the notice is a delta: said once");
}

#[test]
fn two_readers_make_one_subscription() {
    let mut v = Vm::new("p1", "PACK t 1\nLight.title getter: membrane::Setting:t.val\nLight.again getter: membrane::Setting:t.val\n");
    v.born("Light:a", json!({"x": 1}), 1.0);
    v.born("Light:b", json!({"x": 1}), 1.0);
    let (subs, _, _) = v.f.link_drain();
    assert_eq!(subs, vec![json!({"eid": SET, "props": ["val"]})], "four getters, one key");
    assert_eq!(v.f.link_keys().len(), 1);
}

const PACK_TOGGLE: &str = "PACK t 1\nLight.title getter: membrane::Setting:t.val if this.on else 'off'\n";

#[test]
fn dropping_the_last_reader_unsubscribes_once_after_the_grace() {
    let mut v = Vm::new("p1", PACK_TOGGLE);
    v.born("Light:a", json!({"on": true}), 1.0);
    assert_eq!(v.f.link_drain().0.len(), 1);
    v.write("Light:a", "on", json!(false), 2.0);
    assert_eq!(v.f.link_drain(), (vec![], vec![], Some(2.5)), "dropped, in the grace: no unsub yet, and the engine says when it can be decided");
    v.settle(2.3);
    assert!(v.f.link_drain().1.is_empty(), "0.3 s into the grace");
    v.settle(2.6);
    let (s, u, due) = v.f.link_drain();
    assert!(s.is_empty());
    assert_eq!(u, vec![json!({"eid": SET, "props": ["val"]})]);
    assert_eq!(due, None);
    v.settle(9.0);
    assert!(v.f.link_drain().1.is_empty(), "once");
}

#[test]
fn a_getter_that_flickers_registers_no_unsubscribe() {
    let mut v = Vm::new("p1", PACK_TOGGLE);
    v.born("Light:a", json!({"on": true}), 1.0);
    v.f.link_drain();
    v.write("Light:a", "on", json!(false), 2.0);
    v.write("Light:a", "on", json!(true), 2.2);   // back inside the grace
    v.settle(3.0);
    v.settle(9.0);
    assert_eq!(v.f.link_drain(), (vec![], vec![], None), "neither a sub nor an unsub");
    // inside one drain period a drop that went through is cancelled by a new read: sub and unsub cancel
    v.write("Light:a", "on", json!(false), 10.0);
    v.settle(10.6);
    v.write("Light:a", "on", json!(true), 10.7);
    let (s, u, _) = v.f.link_drain();
    assert_eq!((s.len(), u.len()), (0, 0), "dropped and re-read before the host drained: the host never heard of the drop, and it still holds the subscription");
}

#[test]
fn pending_becomes_the_value_and_the_reader_recomputes() {
    let mut v = Vm::new("p1", PACK_ONE);
    v.born("Light:a", json!({"x": 1}), 1.0);
    v.val(SET, json!({"val": "hello"}), 7.0, 2.0);
    assert_eq!(v.prop("Light:a", "title"), json!("hello"));
    assert_eq!(v.why("Light:a", "title"), "");
    assert_eq!(v.c.replica_revs(), vec![(SET.to_string(), 7.0)]);
    v.val(SET, json!({"val": "bye"}), 8.0, 3.0);
    assert_eq!(v.prop("Light:a", "title"), json!("bye"));
    // the owner retires it: absent; refuses: refused; a later value: live again
    let mut s = v.seq;
    v.f.apply_val(&mut v.c, &H, SET, 9.0, LinkVal::Gone, 4.0, &mut s);
    assert_eq!(v.why("Light:a", "title"), format!("absent: {SET} does not exist"));
    v.f.apply_val(&mut v.c, &H, SET, 10.0, LinkVal::Refused("too large (70000 bytes)".into()), 5.0, &mut s);
    assert_eq!(v.why("Light:a", "title"), "refused: too large (70000 bytes)");
    v.seq = s;
    v.val(SET, json!({"val": "again"}), 11.0, 6.0);
    assert_eq!(v.prop("Light:a", "title"), json!("again"));
}

#[test]
fn a_replicas_getters_and_setters_never_run() {
    let pack = "PACK t 1\nSetting.double getter: this.val * 2\nSetting.val on-setter hit:\n  ALWAYS:\n    Seen:x.hit = this.val\n";
    let mut v = Vm::new("p1", pack);
    v.born("Seen:x", json!({"hit": 0}), 1.0);
    v.born("Setting:local", json!({"val": 4}), 1.0);
    assert_eq!(v.prop("Setting:local", "double"), json!(8), "control: the same class runs its getter on a local entity");
    v.write("Setting:local", "val", json!(5), 1.5);
    assert_eq!(v.prop("Seen:x", "hit"), json!(5), "control: and its setter");
    v.val(SET, json!({"val": 21}), 1.0, 2.0);
    assert_eq!(v.prop(SET, "val"), json!(21), "the value arrived");
    assert_eq!(v.prop(SET, "double"), J::Null, "no getter computed on the replica");
    assert_eq!(v.prop("Seen:x", "hit"), json!(5), "no setter fired on it");
}

#[test]
fn a_local_entity_with_a_foreign_looking_id_is_not_a_replica() {
    // the slice-1 case: the seat holds `<ns>::Page:main` written through the door; its setters run and it is read as it is
    let mut v = Vm::new("membrane", "PACK t 1\nPage.shout getter: this.report\n");
    v.born("p7a::Page:main", json!({"report": "hi"}), 1.0);
    assert_eq!(v.prop("p7a::Page:main", "shout"), json!("hi"));
    assert!(v.f.link_drain().0.is_empty(), "no subscription for what the VM holds itself");
}

#[test]
fn without_a_namespace_nothing_is_foreign() {
    let mut f = Fields::goalgraph();
    f.set_pack("t", PACK_ONE).unwrap();
    let mut c = Core::new();
    let mut seq = 0;
    f.arrive(&mut c, "Light:a".into(), "Light".into(), Map::new(), 1.0);
    f.settle(&mut c, &H, 1.0, &mut seq);
    assert!(c.why.get(&("Light:a".to_string(), "title".to_string())).map_or(true, |r| r.starts_with("absent")));
    assert!(f.link_drain().0.is_empty());
}

#[test]
fn a_reset_announces_every_subscription_again() {
    let mut v = Vm::new("p1", PACK_ONE);
    v.born("Light:a", json!({"x": 1}), 1.0);
    assert_eq!(v.f.link_drain().0.len(), 1);
    v.f.link_reset();
    assert_eq!(v.f.link_drain().0, vec![json!({"eid": SET, "props": ["val"]})]);
}

/// A frame pipe between two engines: the subscriber's notices go to the owner, the owner answers with the values of what is subscribed. Counts the frames.
#[test]
fn two_engines_over_a_frame_pipe() {
    let mut sub = Vm::new("p1", "PACK t 1\nLight.title getter: membrane::Setting:t.val\n");
    let mut owner = Vm::new("membrane", "PACK t 1\n");
    owner.born(SET, json!({"val": "one", "other": "never read"}), 1.0);
    let (mut frames_up, mut frames_down, mut rev) = (0, 0, 0.0);
    let mut subscribed: Vec<(String, String)> = vec![];
    // pump: drain the subscriber's notices (a frame up), the owner answers with the current values (a frame down)
    let mut pump = |sub: &mut Vm, owner: &Vm, now: f64, subscribed: &mut Vec<(String, String)>| {
        let (s, u, _) = sub.f.link_drain();
        if s.is_empty() && u.is_empty() { return; }
        frames_up += 1;
        for n in &s { for p in n["props"].as_array().unwrap() { subscribed.push((n["eid"].as_str().unwrap().into(), p.as_str().unwrap().into())); } }
        for n in &u { for p in n["props"].as_array().unwrap() { subscribed.retain(|k| !(k.0 == n["eid"].as_str().unwrap() && k.1 == p.as_str().unwrap())); } }
        let mut by: std::collections::BTreeMap<String, Map<String, J>> = Default::default();
        for (e, p) in s.iter().flat_map(|n| n["props"].as_array().unwrap().iter().map(move |p| (n["eid"].as_str().unwrap().to_string(), p.as_str().unwrap().to_string()))) {
            by.entry(e.clone()).or_default().insert(p.clone(), owner.prop(&e, &p));
        }
        if by.is_empty() { return; }
        frames_down += 1;
        for (e, props) in by { rev += 1.0; sub.val(&e, J::Object(props), rev, now); }
    };
    sub.born("Light:a", json!({"x": 1}), 2.0);
    assert_eq!(sub.prop("Light:a", "title"), J::Null, "pending until the frame comes back");
    pump(&mut sub, &owner, 2.0, &mut subscribed);
    assert_eq!(sub.prop("Light:a", "title"), json!("one"));
    assert_eq!((frames_up, frames_down), (1, 1));
    assert!(!sub.c.docs[SET].props.contains_key("other"), "an unread prop of the owner's node is never asked for, never sent");
    // the owner changes the subscribed prop: ONE delta frame down, the getter recomputes
    owner.write(SET, "val", json!("two"), 3.0);
    let changed: Vec<_> = subscribed.iter().filter(|k| k.0 == SET).map(|k| (k.1.clone(), owner.prop(SET, &k.1))).collect();
    let mut m = Map::new(); for (p, v) in changed { m.insert(p, v); }
    frames_down += 1; rev += 1.0;
    sub.val(SET, J::Object(m), rev, 3.0);
    assert_eq!(sub.prop("Light:a", "title"), json!("two"));
    assert_eq!((frames_up, frames_down), (1, 2), "no extra frame up: nothing new was read");
}

fn mem() -> Host { let mut o = Options::new(None); o.effects = false; Host::open(o).unwrap() }

#[test]
fn replicas_are_not_journalled_not_stored() {
    let h = mem();
    let before = h.journal().len();
    h.feed_val("p1::Setting:t", LinkVal::Props(json!({"val": 3}).as_object().unwrap().clone()), 4.0);
    assert_eq!(h.snapshot().get("p1::Setting:t").map(|e| e["props"]["val"].clone()), Some(json!(3)), "the replica is in the engine");
    assert_eq!(h.journal().len(), before, "and not in the journal");
    assert!(h.stored_updated("p1::Setting:t").is_none(), "and not in the store");
    assert_eq!(h.replica_revs(), vec![("p1::Setting:t".to_string(), 4.0)]);
}

#[test]
fn retiring_a_vm_retires_its_namespace() {
    let h = mem();
    h.born("Vm:p7a", "Vm", Map::new(), "test");
    h.born("p7a::Shell:main", "Shell", json!({"open": true}).as_object().unwrap().clone(), "test");
    h.born("p7a::Page:main", "Page", Map::new(), "test");
    h.born("p7b::Shell:main", "Shell", Map::new(), "test");
    h.feed_val("p7a::Setting:q", LinkVal::Props(json!({"val": 1}).as_object().unwrap().clone()), 1.0);
    h.write("Vm:p7a", "retire", json!(true), "test");
    let snap = h.snapshot();
    assert!(!snap.contains_key("Vm:p7a") && !snap.contains_key("p7a::Shell:main") && !snap.contains_key("p7a::Page:main") && !snap.contains_key("p7a::Setting:q"), "{:?}", snap.keys().collect::<Vec<_>>());
    assert!(snap.contains_key("p7b::Shell:main"), "another namespace stays");
    assert!(h.replica_revs().is_empty(), "the replica note went with it");
    assert!(h.stored_updated("p7a::Shell:main").is_none(), "its stored row too");
}

#[test]
fn the_host_has_the_same_notices() {
    let h = mem();
    let pack = "PACK t 1\nLight.title getter: p1::Setting:t.val\n";
    h.write("Pack:t", "text", json!(pack), "test");
    h.born("Light:a", "Light", Map::new(), "test");
    let (subs, unsubs, _) = h.link_notices();
    assert_eq!(subs, vec![json!({"eid": "p1::Setting:t", "props": ["val"]})]);
    assert!(unsubs.is_empty());
    h.feed_val("p1::Setting:t", LinkVal::Props(json!({"val": "page says"}).as_object().unwrap().clone()), 1.0);
    assert_eq!(h.get("Light:a", "title"), json!("page says"));
}
