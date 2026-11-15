//! Silent foot-guns made loud (#1143, operator 2026-11-14): each is a refusal with a reason the writer can read (a receipt `refused` on the writing entity), never a silent "wrote".
//! The host's own writes and the engine's own stay open; every refusal below is for a setter (the door's `entity_write` is one) or for a pack's declaration.
use membrane_vm::host::{boot_environment, boot_started, Host, Options};
use serde_json::{json, Map, Value as J};

fn open() -> Host { let mut o = Options::new(None); o.effects = false; Host::open(o).unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn refused(h: &Host, eid: &str) -> String { h.get(eid, "refused").as_str().unwrap_or("").to_string() }

const PACK: &str = "PACK fg 1
Box.shown getter: this.eid
Thing.shown getter: this.eid
Box.setcls on-setter setcls:
  ALWAYS:
    Thing:t1.cls = 'Other'
Box.seteid on-setter seteid:
  ALWAYS:
    Thing:t1.eid = 'Thing:zzz'
Box.setdyn on-setter setdyn:
  ALWAYS:
    Thing:t1.[this.name] = 'x'
Box.mk on-setter mk:
  ALWAYS:
    membrane::Thing:n1.n = 1
Box.good on-setter good:
  ALWAYS:
    Thing:t1.n = this.good
Box.flaky on-setter flaky:
  WHEN this.bad == True:
    Thing:t1.updated = 1
  ELSE:
    Thing:t1.n = this.k
";

fn seat() -> Host {
    let h = open();
    h.load_pack("fg", PACK).unwrap_or_else(|e| panic!("{e}"));
    h.born("Box:a", "Box", props(json!({})), "person");
    h.born("Thing:t1", "Thing", props(json!({})), "person");
    h.rebuild();
    h
}

#[test]
fn a_setter_write_of_cls_or_eid_is_refused_with_a_receipt_and_the_value_stays() {
    let h = seat();
    h.write("Box:a", "setcls", json!(1), "person");
    let r = refused(&h, "Box:a");
    assert!(r.contains("cls") && r.contains("read-only"), "a receipt: {r:?}");
    assert_eq!(h.snapshot()["Thing:t1"]["cls"], json!("Thing"));
    assert!(h.snapshot()["Thing:t1"]["props"].get("cls").is_none(), "no property of that name is stored");
    h.write("Box:a", "seteid", json!(1), "person");
    let r = refused(&h, "Box:a");
    assert!(r.contains("eid") && r.contains("read-only"), "a receipt: {r:?}");
    assert!(h.snapshot()["Thing:t1"]["props"].get("eid").is_none(), "no property of that name is stored");
    // the door's way: a computed name
    h.write("Box:a", "name", json!("cls"), "person");
    h.write("Box:a", "setdyn", json!(1), "person");
    assert!(refused(&h, "Box:a").contains("cls"), "{}", refused(&h, "Box:a"));
    assert_eq!(h.snapshot()["Thing:t1"]["cls"], json!("Thing"));
    assert!(h.snapshot()["Thing:t1"]["props"].get("cls").is_none(), "no property of that name is stored");
}

#[test]
fn an_outside_write_of_a_name_the_vm_provides_is_dropped_unjournalled() {
    let h = seat();
    let before = h.journal().len();
    for p in ["cls", "eid", "updated", "pinned", "born_by"] { h.write("Thing:t1", p, json!("x"), "person"); }
    assert_eq!(h.journal().len(), before);
    assert_eq!(h.snapshot()["Thing:t1"]["cls"], json!("Thing"));
    assert!(h.snapshot()["Thing:t1"]["props"].get("cls").is_none(), "no property of that name is stored");
}

#[test]
fn a_pack_may_not_declare_a_getter_of_a_name_the_vm_provides() {
    for n in ["cls", "eid", "updated", "pinned", "born_by"] {
        let h = open();
        let e = h.load_pack("bad", &format!("PACK bad 1\nThing.{n} getter: 'x'\n")).unwrap_err();
        assert!(e.contains(n) && e.contains("VM"), "{n}: {e}");
    }
    // a setter on such a name is still just a setter, and any other getter loads
    open().load_pack("ok", "PACK ok 1\nThing.cls_name getter: this.cls\n").unwrap();
}

#[test]
fn a_plain_write_creating_a_namespaced_eid_takes_the_class_after_the_namespace() {
    let h = seat();
    h.write("Box:a", "mk", json!(1), "person");
    assert_eq!(refused(&h, "Box:a"), "", "no refusal");
    assert_eq!(h.snapshot()["membrane::Thing:n1"]["cls"], json!("Thing"));
}

#[test]
fn a_refused_receipt_is_cleared_by_the_next_good_write_of_the_same_setter() {
    let h = seat();
    h.write("Box:a", "bad", json!(true), "person");
    h.write("Box:a", "flaky", json!(1), "person");
    assert!(refused(&h, "Box:a").contains("updated"), "the first run was refused");
    h.write("Box:a", "bad", json!(false), "person");
    h.write("Box:a", "k", json!(7), "person");
    h.write("Box:a", "flaky", json!(2), "person");
    assert_eq!(refused(&h, "Box:a"), "", "the good run cleared it");
    assert_eq!(h.get("Thing:t1", "n"), json!(7));
}

#[test]
fn a_receipt_of_another_setter_or_one_a_pack_wrote_itself_is_not_cleared() {
    let h = seat();
    h.write("Box:a", "refused", json!("a pack's own words"), "person");
    h.write("Box:a", "good", json!(1), "person");
    assert_eq!(refused(&h, "Box:a"), "a pack's own words");
    h.write("Box:a", "setcls", json!(2), "person");
    let r = refused(&h, "Box:a");
    assert!(r.contains("cls"));
    h.write("Box:a", "good", json!(3), "person");
    assert_eq!(refused(&h, "Box:a"), r, "another setter's good write leaves it");
}

const GUARD: &str = "PACK gd 1
Boot.up getter: this.started != None
Request.entrant getter: this.authed == True
Box.shown getter: this.eid
Box.start on-setter start:
  ALWAYS:
    Boot:seat.started = 5
Box.fake on-setter fake:
  ALWAYS:
    Request:r1.authed = True
Box.fake2 on-setter fake2:
  ALWAYS:
    Request:r1.sub = 'root'
Box.fake3 on-setter fake3:
  ALWAYS:
    Request:r1.claims = {'sub': 'root'}
Box.other on-setter other:
  ALWAYS:
    Request:r1.note = 'fine'
";

#[test]
fn boot_started_and_request_identity_refuse_a_setter_and_stay_open_to_the_host() {
    let h = open();
    h.load_pack("gd", GUARD).unwrap_or_else(|e| panic!("{e}"));
    h.born("Box:a", "Box", props(json!({})), "person");
    // the host's own: the boot announcement and the door's request birth
    h.born("Request:r1", "Request", props(json!({"authed": false})), "host:door");
    h.rebuild();
    boot_started(&h, &Map::new(), false);
    h.write("Box:a", "start", json!(1), "person");
    assert!(refused(&h, "Box:a").contains("started"), "{}", refused(&h, "Box:a"));
    assert_eq!(h.get("Boot:seat", "started"), J::Null, "a setter did not fire the start");
    for (s, p) in [("fake", "authed"), ("fake2", "sub"), ("fake3", "claims")] {
        h.write("Box:a", s, json!(1), "person");
        assert!(refused(&h, "Box:a").contains(p) && refused(&h, "Box:a").contains("Request"), "{s}: {}", refused(&h, "Box:a"));
    }
    assert_eq!(h.get("Request:r1", "authed"), json!(false));
    h.write("Box:a", "other", json!(1), "person");
    assert_eq!(h.get("Request:r1", "note"), json!("fine"), "the rest of a Request stays writable");
    // the host's own writes still work
    h.write("Request:r1", "authed", json!(true), "host:door");
    assert_eq!(h.get("Request:r1", "authed"), json!(true));
    boot_started(&h, &Map::new(), true);
    assert!(h.get("Boot:seat", "started").as_f64().is_some(), "the host's announcement works");
}

#[test]
fn clearing_the_environment_leaves_a_getter_owned_property_and_a_variable_named_like_one_never_collides() {
    let h = open();
    h.load_pack("environment", &std::fs::read_to_string(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane/packs/environment.c1f")).unwrap()).unwrap();
    h.load_pack("envget", "PACK envget 1\nIMPORT environment 1\nEnvironment.summary getter: 'home is ' + str(this.HOME or 'unset')\n").unwrap();
    h.rebuild();
    let env = |v: &[(&str, &str)]| v.iter().map(|(k, x)| (k.to_string(), x.to_string())).collect::<Vec<_>>();
    boot_environment(&h, &Map::new(), &env(&[("HOME", "/h")]));
    h.pump();
    assert_eq!(h.get("Environment:host", "summary"), json!("home is /h"));
    // the next boot has no HOME: the old variable clears, the getter's value is its own and is not cleared by the host
    let before = h.journal().len();
    boot_environment(&h, &Map::new(), &env(&[("LANG", "en")]));
    h.pump();
    assert!(h.journal().iter().skip(before).all(|l| !l.to_json().to_string().contains("\"summary\"")), "no outside write touched `summary`");
    assert_eq!(h.get("Environment:host", "summary"), json!("home is unset"));
    // a variable named like a getter is not stored over it
    boot_environment(&h, &Map::new(), &env(&[("LANG", "en"), ("TMPDIR", "/t")]));
    let h2 = open();
    h2.load_pack("environment", &std::fs::read_to_string(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane/packs/environment.c1f")).unwrap()).unwrap();
    h2.load_pack("envget2", "PACK envget2 1\nIMPORT environment 1\nEnvironment.LANG getter: 'mine'\n").unwrap();
    h2.rebuild();
    boot_environment(&h2, &Map::new(), &env(&[("LANG", "en_US")]));
    h2.pump();
    assert_eq!(h2.get("Environment:host", "LANG"), json!("mine"));
}

#[test]
fn a_write_of_the_model_call_target_is_refused_on_the_seat_and_still_handed_on_by_the_browser_engine() {
    let h = open();
    h.load_pack("lc", "PACK lc 1\nBox.shown getter: this.eid\nBox.ask on-setter ask:\n  ALWAYS:\n    membrane::llm.call = {'prompt': 'hi'}\n").unwrap();
    h.born("Box:a", "Box", props(json!({})), "person");
    h.rebuild();
    h.write("Box:a", "ask", json!(1), "person");
    let r = refused(&h, "Box:a");
    assert!(r.contains("llm.call") && r.contains("this seat"), "a receipt: {r:?}");
    // the engine without a host (the browser's) keeps handing the call to its page
    use membrane_vm::fields::Fields;
    use membrane_vm::vm::{Core, Write};
    let mut f = Fields::goalgraph();
    f.set_pack("lc", "PACK lc 1\nBox.ask on-setter ask:\n  ALWAYS:\n    membrane::llm.call = {'prompt': 'hi'}\n").unwrap();
    let mut c = Core::new();
    c.load("Box:a".into(), "Box".into(), Default::default(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    seq += 1;
    let w = Write { eid: "Box:a".into(), prop: "ask".into(), new: json!(1), old: J::Null, writer: "person".into(), chain: json!({}), seq };
    struct H;
    impl membrane_vm::vm::Host for H {
        fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
        fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
        fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
        fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
        fn align(&self, _: &str, _: f64) -> bool { false }
        fn live(&self, _: &str, _: bool) -> Option<String> { None }
    }
    c.hear(w.clone(), None, 2.0);
    f.react(&mut c, &H, w, 2.0, &mut seq);
    assert_eq!(f.calls.len(), 1);
}

#[test]
fn a_computed_id_whose_key_holds_colons_and_a_namespace_lookalike_is_created_with_its_class_and_properties() {
    // the page's tool rows: `(base + ':tools:t:' + k).tag = 'li'`, k built from a tool's name and its input JSON
    let h = open();
    h.load_pack("pg", "PACK pg 1\nView.shown getter: this.eid\nView.go on-setter go:\n  ALWAYS:\n    (this.base + ':tools:t:' + this.k).tag = 'li'\n    (this.base + ':tools:t:' + this.k).text = 'x'\n").unwrap();
    h.born("View:a", "View", props(json!({"base": "View:a", "k": "entity_read|{\"spec\":\"membrane::Chat:main\"}"})), "person");
    h.rebuild();
    h.write("View:a", "go", json!(1), "person");
    assert_eq!(refused(&h, "View:a"), "");
    let eid = "View:a:tools:t:entity_read|{\"spec\":\"membrane::Chat:main\"}";
    assert_eq!(h.snapshot()[eid]["cls"], json!("View"), "the class is the eid's first segment");
    assert_eq!(h.get(eid, "tag"), json!("li"));
    assert_eq!(h.get(eid, "text"), json!("x"));
}
