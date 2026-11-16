//! `Error` is a REAL CLASS (#1143, operator 2026-11-13): the root pack `entity` declares it with ONE typed property `failed` (a dict {property: reason}). When a
//! getter fails the VM writes (derived, not journalled) the class `Error` on top of the entity's class list (`parents`) and the reason into `failed[prop]`; when
//! every failure clears, `Error` leaves `parents` and `failed` empties. The stored value of the failed property stays null; a read of it gives the Error (the overlay),
//! the getter keeps recomputing. Both rows persist, so a restart brings the Errors back.
use membrane_vm::host::{self, Host, Line, Options};
use serde_json::{json, Map, Value as J};
use std::time::{Duration, Instant};

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn entity_text() -> String { std::fs::read_to_string(repo().join("membrane/packs/entity.c1f")).unwrap() }
fn open(o: Options) -> Host { let mut o = o; o.effects = false; Host::open(o).unwrap() }
fn host(pack: &str) -> Host {
    let h = open(Options::new(None));
    h.load_pack("entity", &entity_text()).unwrap_or_else(|e| panic!("entity pack: {e}"));
    h.load_pack("er", pack).unwrap_or_else(|e| panic!("pack refused: {e}"));
    h.rebuild();
    h
}
fn born(h: &Host, eid: &str, props: J) { h.born(eid, eid.split(':').next().unwrap(), props.as_object().unwrap().clone(), "person"); h.pump(); }
fn set(h: &Host, eid: &str, p: &str, v: J) { h.write(eid, p, v, "person"); h.pump(); }
const Q: &str = "PACK er 1\nBox.q getter: Error('no price; set price') if this.price == None else this.price\n";

#[test]
fn the_root_pack_declares_the_class_error_and_no_other_pack_may() {
    let h = open(Options::new(None));
    h.load_pack("entity", &entity_text()).unwrap_or_else(|e| panic!("entity pack: {e}"));
    assert!(entity_text().contains("Error.failed"), "the root pack declares the class Error with its one property `failed`");
    let e = h.load_pack("other", "PACK other 1\nIMPORT entity 1\nError.x getter: 1\n").unwrap_err();
    assert!(e.contains("Error") && e.contains("reserved"), "{e}");
    let e = h.load_pack("other2", "PACK other2 1\nIMPORT entity 1\nBox.q on-setter s:\n  ALWAYS: Box:a.v = 1\nError.y on-setter t:\n  ALWAYS: Box:a.v = 2\n").unwrap_err();
    assert!(e.contains("reserved"), "{e}");
}

#[test]
fn a_failed_getter_puts_error_on_top_of_the_class_list_and_the_reason_in_failed() {
    let h = host(&format!("{Q}Box.isq getter: this.q is Error\nBox.both getter: this is (Box, Error)\nBox.cls_now getter: this.cls\n"));
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "q"), J::Null, "the property's own value stays null");
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "no price; set price"}));
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:box"]), "Error nearest, the eid class kept explicitly");
    assert_eq!(h.get("Box:a", "isq"), json!(true), "the read is the overlay");
    assert_eq!(h.get("Box:a", "both"), json!(true), "`x is (Box, Error)` follows the class list");
    assert_eq!(h.get("Box:a", "cls_now"), json!("Box"), "`cls` is the first class that is not the VM's Error");
}

#[test]
fn the_getter_recomputes_under_the_overlay_and_the_value_comes_back() {
    let h = host(&format!("{Q}Box.isq getter: this.q is Error\n"));
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "isq"), json!(true));
    set(&h, "Box:a", "price", json!(5));
    assert_eq!(h.get("Box:a", "q"), json!(5), "the value comes back");
    assert_eq!(h.get("Box:a", "isq"), json!(false));
    assert_eq!(h.get("Box:a", "failed"), J::Null, "the entry left and failed is empty");
    assert_eq!(h.get("Box:a", "parents"), J::Null, "Error left the list: back to the birth default (no parents stored)");
    set(&h, "Box:a", "price", J::Null);
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "no price; set price"}), "fails again");
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:box"]));
}

#[test]
fn another_reason_replaces_the_old_text_and_other_failures_keep_error_in_the_list() {
    let h = host("PACK er 1\nBox.q getter: Error('first') if this.k == 1 else (Error('second') if this.k == 2 else 1)\nBox.r getter: Error('r down') if this.k != 9 else 1\n");
    born(&h, "Box:a", json!({"k": 1}));
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "first", "r": "r down"}));
    set(&h, "Box:a", "k", json!(2));
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "second", "r": "r down"}), "no stale text");
    set(&h, "Box:a", "k", json!(3));
    assert_eq!(h.get("Box:a", "failed"), json!({"r": "r down"}));
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:box"]), "one failure left: Error stays");
    set(&h, "Box:a", "k", json!(9));
    assert_eq!(h.get("Box:a", "failed"), J::Null);
    assert_eq!(h.get("Box:a", "parents"), J::Null);
}

#[test]
fn an_arm_that_loses_resolution_is_never_evaluated_so_it_cannot_fail() {
    let h = open(Options::new(None));
    h.load_pack("entity", &entity_text()).unwrap();
    h.load_pack("er", "PACK er 1\nAlpha.v getter: Error('alpha broke')\nBeta.v getter: 1\n").unwrap();
    h.rebuild();
    born(&h, "Box:a", json!({"parents": ["cls:beta", "cls:alpha"]}));
    assert_eq!(h.get("Box:a", "v"), json!(1));
    assert_eq!(h.get("Box:a", "failed"), J::Null, "failed names only a property whose current result is the failure");
    set(&h, "Box:a", "parents", json!(["cls:alpha", "cls:beta"]));
    assert_eq!(h.get("Box:a", "failed"), json!({"v": "alpha broke"}));
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:alpha", "cls:beta"]));
    set(&h, "Box:a", "parents", json!(["cls:beta", "cls:alpha"]));    // the person writes the whole list: Error is the VM's, it follows `failed`
    assert_eq!(h.get("Box:a", "failed"), J::Null);
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:beta", "cls:alpha"]));
    set(&h, "Box:a", "parents", json!(["cls:alpha"]));
    set(&h, "Box:a", "parents", json!(["cls:alpha", "cls:beta"]));
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:alpha", "cls:beta"]), "a person's list that drops Error while a failure stands gets it back");
}

#[test]
fn a_reader_of_the_class_list_and_instances_of_error_follow_the_change() {
    let h = host(&format!("{Q}Dash.n getter: len(instances('Error'))\nDash.w getter: prop('Box:a', 'x') is Error\nBox.k getter: 1\nSeat.f getter: instances('Box')[0] is Error\n"));
    born(&h, "Dash:d", json!({}));
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Dash:d", "n"), json!(1), "instances('Error') lists every failed entity");
    set(&h, "Box:a", "price", json!(3));
    assert_eq!(h.get("Dash:d", "n"), json!(0));
}

fn journal_len(h: &Host) -> usize { h.journal().len() }

#[test]
fn fail_recover_fail_settles_with_no_per_round_growth() {
    let h = host(Q);
    born(&h, "Box:a", json!({}));
    let mut last = None;
    for i in 0..4 {
        set(&h, "Box:a", "price", json!(i + 1));
        set(&h, "Box:a", "price", J::Null);
        let j = journal_len(&h);
        let snap = h.snapshot();
        h.pump(); h.pump();
        assert_eq!(h.snapshot(), snap, "quiet: another round changes nothing");
        assert_eq!(journal_len(&h), j, "a quiet round journals nothing");
        if let Some(prev) = last { assert_eq!(j, prev + 2, "each cycle journals its two outside writes and nothing else"); }
        last = Some(j);
    }
}

#[test]
fn a_getter_whose_failure_depends_on_the_class_list_does_not_loop() {
    let h = host("PACK er 1\nBox.p getter: Error('x') if not (this is Error) else 5\nBox.s getter: join(this.parents or [], ',')\n");
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let h = h;
        born(&h, "Box:a", json!({}));
        for _ in 0..3 { h.pump(); }
        let j = h.journal().len();
        h.pump();
        tx.send((j, h.journal().len(), h.get("Box:a", "failed"), h.get("Box:a", "parents"))).unwrap();
    });
    let (j0, j1, f, p) = rx.recv_timeout(Duration::from_secs(30)).expect("the round ends");
    assert_eq!(j0, j1, "nothing journalled by the settling");
    // whatever it settles to, `failed` and the class list agree
    assert_eq!(f.is_object(), p.as_array().map_or(false, |l| l.contains(&json!("cls:error"))), "failed {f} vs parents {p}");
}

#[test]
fn the_vm_writes_are_derived_not_journalled_and_stamped_like_the_other_derived_writes() {
    let h = host(Q);
    born(&h, "Box:a", json!({}));
    assert!(h.journal().iter().all(|l| match l { Line::Write { prop, .. } => prop != "failed" && prop != "parents", Line::Born { props, .. } => !props.contains_key("failed") && !props.contains_key("parents") }), "{:?}", h.journal());
    let rows = h.rows();
    let w = &rows["Box:a"]["writers"];
    for p in ["failed", "parents"] {
        let s = w[p].as_str().unwrap_or_default();
        assert!(s.starts_with("trigger:field:") && !host::is_outside(s), "{p} is stamped {s}");
    }
    assert_eq!(rows["Box:a"]["props"]["failed"], json!({"q": "no price; set price"}));
    // a replay of the journal derives the same
    let r = host(Q);
    r.replay(&h.journal());
    assert_eq!(r.get("Box:a", "failed"), h.get("Box:a", "failed"));
    assert_eq!(r.get("Box:a", "parents"), h.get("Box:a", "parents"));
}

#[test]
fn failures_persist_across_a_restart_and_come_back_through_the_overlay() {
    let file = std::env::temp_dir().join(format!("errclass-{}-{}.sqlite3", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos())).to_string_lossy().to_string();
    {
        let h = open(Options::new(Some(file.clone())));
        h.load_pack("entity", &entity_text()).unwrap(); h.load_pack("er", Q).unwrap(); h.rebuild();
        born(&h, "Box:a", json!({}));
        born(&h, "Box:b", json!({"price": 2}));
        assert_eq!(h.get("Box:a", "failed"), json!({"q": "no price; set price"}));
    }
    // the stored row itself carries both (stamped derived, so a restore reads them back)
    {
        let h = open(Options::new(Some(file.clone())));
        let rows = h.rows();
        assert_eq!(rows["Box:a"]["props"]["failed"], json!({"q": "no price; set price"}));
        assert_eq!(rows["Box:a"]["props"]["parents"], json!(["cls:error", "cls:box"]));
        assert!(rows["Box:b"]["props"].get("failed").is_none() && rows["Box:b"]["props"].get("parents").is_none());
    }
    // a restart with the same getter: the failure is there and reads back as an Error
    {
        let h = open(Options::new(Some(file.clone())));
        h.load_pack("entity", &entity_text()).unwrap();
        h.load_pack("er", &format!("{Q}Box.isq getter: this.q is Error\nBox.why getter: why(this.q)\n")).unwrap();
        h.load_stored_entities();
        assert_eq!(h.load_everything(), 2);
        h.rebuild();
        assert_eq!(h.get("Box:a", "failed"), json!({"q": "no price; set price"}));
        assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:box"]));
        assert_eq!(h.get("Box:a", "isq"), json!(true), "the stored failure reads back as an Error");
        assert_eq!(h.get("Box:a", "why"), json!("no price; set price"));
        assert_eq!(h.get("Box:b", "isq"), json!(false));
    }
    // a restart whose pack no longer has the getter: the stored failure of a property nothing computes leaves, and Error with it
    {
        let h = open(Options::new(Some(file.clone())));
        h.load_pack("entity", &entity_text()).unwrap();
        h.load_pack("er", "PACK er 1\nBox.other getter: 1\n").unwrap();
        h.load_stored_entities();
        assert_eq!(h.load_everything(), 2);
        h.rebuild(); h.pump();
        assert_eq!(h.get("Box:a", "failed"), J::Null);
        assert_eq!(h.get("Box:a", "parents"), J::Null);
    }
    let _ = std::fs::remove_file(&file);
}

fn pack_entity(h: &Host, text: &str, first: bool) {
    if first { h.born("Pack:er", "Pack", Map::from_iter([("text".to_string(), json!(text))]), "person"); } else { h.write("Pack:er", "text", json!(text), "person"); }
    h.pump();
}

#[test]
fn a_redefinition_or_a_removal_of_the_pack_clears_the_failure_and_a_new_failure_has_a_fresh_reason() {
    let h = open(Options::new(None));
    h.load_pack("entity", &entity_text()).unwrap();
    pack_entity(&h, "PACK er 1\nBox.q getter: Error('old reason')\nBox.z getter: Error('z reason')\n", true);
    h.rebuild();
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "old reason", "z": "z reason"}));
    pack_entity(&h, "PACK er 1\nBox.q getter: 7\nBox.z getter: Error('z reason')\n", false);
    assert_eq!(h.get("Box:a", "q"), json!(7));
    assert_eq!(h.get("Box:a", "failed"), json!({"z": "z reason"}), "the redefined getter succeeds: its entry leaves");
    assert_eq!(h.get("Box:a", "parents"), json!(["cls:error", "cls:box"]));
    pack_entity(&h, "PACK er 1\nBox.q getter: Error('new reason')\nBox.z getter: Error('z reason')\n", false);
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "new reason", "z": "z reason"}), "failing again after a redefinition: a fresh reason");
    pack_entity(&h, "PACK er 1\nBox.q getter: Error('newer reason')\n", false);
    assert_eq!(h.get("Box:a", "failed"), json!({"q": "newer reason"}), "a getter the new text lacks leaves no entry, and a changed reason replaces the old one");
    pack_entity(&h, "", false);    // the pack is removed
    assert_eq!(h.get("Box:a", "failed"), J::Null, "a removed pack's getters leave no failure");
    assert_eq!(h.get("Box:a", "parents"), J::Null);
}

#[test]
fn a_thousand_failing_and_recovering_entities_cost_what_a_plain_null_costs() {
    let run = |pack: &str| -> (Duration, Duration) {
        let h = host(pack);
        let t = Instant::now();
        for i in 0..1000 { h.born(&format!("Box:{i}"), "Box", Map::new(), "person"); }
        h.pump(); h.pump(); let birth = t.elapsed();
        let t = Instant::now();
        for i in 0..1000 { h.write(&format!("Box:{i}"), "price", json!(1), "person"); }
        h.pump();
        for i in 0..1000 { h.write(&format!("Box:{i}"), "price", J::Null, "person"); }
        h.pump();
        (birth, t.elapsed())
    };
    let (eb, ec) = run(Q);
    let (nb, nc) = run("PACK er 1\nBox.q getter: None('no price') if this.price == None else this.price\n");
    eprintln!("1000 entities: Error birth {eb:?} cycle {ec:?}; plain null birth {nb:?} cycle {nc:?}");
    assert!(ec < nc * 4 + Duration::from_millis(500), "the cycle with Errors {ec:?} vs plain nulls {nc:?}");
    assert!(eb < nb * 4 + Duration::from_millis(500), "the birth with Errors {eb:?} vs plain nulls {nb:?}");
}

/// Whole-entity `entity_read` (door_tools.c1f `mc_r_rows`): the stored row through the overlay, a failed property as `Error: <reason>`, every other field as it is. The test runs the
/// pack's own query text against the store.
#[test]
fn a_whole_entity_read_shows_failed_properties_as_error_and_the_rest_normally() {
    let tools = std::fs::read_to_string(repo().join("membrane/packs/door_tools.c1f")).unwrap();
    let line = tools.lines().find(|l| l.starts_with("Request.mc_r_rows getter: sql(")).expect("the whole-read query");
    let lit = &line["Request.mc_r_rows getter: sql(".len()..line.find(", this.mc_r_id)").unwrap()];
    let file = std::env::temp_dir().join(format!("errclass-read-{}-{}.sqlite3", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos())).to_string_lossy().to_string();
    let h = Host::open(Options::new(Some(file.clone()))).unwrap();
    h.load_pack("entity", &entity_text()).unwrap();
    h.load_pack("er", &format!("{Q}Dash.rows getter: sql({lit}, 'Box:a')\nDash.plain getter: sql({lit}, 'Box:b')\n")).unwrap();
    h.rebuild();
    born(&h, "Box:a", json!({"note": "kept"}));
    born(&h, "Box:b", json!({"price": 3}));
    born(&h, "Dash:d", json!({}));
    for _ in 0..4 { h.pump(); std::thread::sleep(Duration::from_millis(50)); }
    let rows = h.get("Dash:d", "rows");
    let parse = |r: &J| -> J { serde_json::from_str(r[0]["props"].as_str().unwrap()).unwrap() };
    let p = &parse(&rows);
    assert_eq!(p["q"], json!("Error: no price; set price"), "{rows}");
    assert_eq!(p["note"], json!("kept"));
    assert!(p.get("failed").is_none(), "the dict itself is not shown twice: {rows}");
    let plain = &parse(&h.get("Dash:d", "plain"));
    assert_eq!(plain["price"], json!(3));
    assert_eq!(plain["q"], json!(3));
    let _ = std::fs::remove_file(&file);
}

/// `failed` is the class Error's property: a shipped pack that declared a getter of that name on another class would fight the VM for the property (the first run of this
/// change found `ChatMessage.failed`, now `fail_why`, ping-ponging with the dict).
#[test]
fn no_shipped_pack_declares_a_property_called_failed_but_the_root_pack() {
    for e in std::fs::read_dir(repo().join("membrane/packs")).unwrap() {
        let p = e.unwrap().path();
        if p.extension().map_or(true, |x| x != "c1f") { continue; }
        let text = std::fs::read_to_string(&p).unwrap();
        for l in text.lines().filter(|l| !l.trim_start().starts_with("--")) {
            if let Some((path, _)) = l.split_once(" getter") { if path.ends_with(".failed") && !path.contains(' ') { assert!(path == "Error.failed", "{}: {l}", p.display()); } }
        }
    }
}
