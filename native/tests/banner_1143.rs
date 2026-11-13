//! The barest seat's banner (#1143, operator 2026-11-13): the banner is FULLY the model's. `Banner:owner:main` is born once by the app's
//! `default_banner` pack, whose `text` is a live getter (header, the previous turn's error); the model REPLACES it by loading a pack that
//! imports `default_banner` and defines `Banner.text` (the upper layer's arm wins). The seat never segments, orders or joins what the model puts there.
use membrane_vm::host::{self, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }

fn seat() -> Host { seat_with(|_| {}) }
/// `before` runs after the packs are loaded and before the seat starts (the banner is born at start)
fn seat_with(before: impl Fn(&Host)) -> Host {
    let h = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
    }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.rebuild();
    before(&h);
    host::boot_started(&h, &Map::new(), true);
    h.pump();
    h
}
const B: &str = "membrane::Banner:owner:main";
fn text(h: &Host) -> String { h.get(B, "text").as_str().unwrap_or_else(|| panic!("the banner text is a string: {}", h.get(B, "text"))).to_string() }

#[test]
fn a_fresh_banner_is_born_once_and_its_text_is_the_default_banner() {
    let h = seat();
    let t = text(&h);
    assert!(t.contains("CONVERSATION: main") && t.contains("NOW (for reference, not an event):"), "{t}");
    assert!(!t.contains("BannerLine") && !t.contains("placeholder"), "{t}");
    assert!(!h.snapshot().keys().any(|k| k.starts_with("BannerLine")), "no BannerLine entity exists");
    assert_eq!(h.get(B, "seeded"), json!(true));
}

fn turn(h: &Host, n: u32, state: &str, ended: f64, said: Option<&str>) {
    // as the chat makes them: the class of the eid's namespace (Chat), the kind in `parents`
    let mut p = Map::new(); p.insert("state".into(), json!(state)); p.insert("ended".into(), json!(ended)); p.insert("parents".into(), json!(["cls:chatturn"]));
    h.born(&format!("membrane::Chat:message:{n}:turn"), "Chat", p, "person");
    if let Some(t) = said {
        let mut r = Map::new(); r.insert("text".into(), json!(t)); r.insert("error".into(), json!(state == "error")); r.insert("parents".into(), json!(["cls:chatresponse"]));
        h.born(&format!("membrane::Chat:message:{n}:turn:response"), "Chat", r, "person");
    }
    h.pump();
}

#[test]
fn the_previous_turns_error_shows_in_the_default_banner_and_a_done_turn_adds_nothing() {
    let quiet = text(&seat());
    let done = text(&seat_with(|h| turn(h, 1, "done", 1.0, Some("all done"))));
    assert_eq!(done.lines().count(), quiet.lines().count(), "a done turn adds nothing: {done}");
    let t = text(&seat_with(|h| { turn(h, 1, "done", 1.0, Some("fine")); turn(h, 2, "error", 2.0, Some("claude failed (rc 1): boom")); }));
    assert!(t.contains("THE PREVIOUS TURN ENDED WITH AN ERROR: membrane::Chat:message:2:turn:response - claude failed (rc 1): boom"), "{t}");
    // the error is the PREVIOUS turn's: a later done turn takes it away
    let t = text(&seat_with(|h| { turn(h, 1, "error", 1.0, Some("boom")); turn(h, 2, "done", 2.0, Some("fine")); }));
    assert!(!t.contains("ERROR"), "{t}");
}

#[test]
fn a_model_pack_that_defines_banner_text_replaces_the_default_completely() {
    let h = seat_with(|h| { h.born("Stop:a", "Stop", json!({"done": true}).as_object().unwrap().clone(), "person"); });
    let pack = "PACK mine 1\nIMPORT default_banner 1\nBanner.text getter: 'stops done: ' + str(len([s for s in instances('Stop') if s.done == True]))\n";
    h.load_pack("mine", pack).unwrap(); h.rebuild(); h.pump();
    assert_eq!(text(&h), "stops done: 1", "nothing of the default is left, nothing is added");
}

/// FINDING (reported to the operator): the VM has no "nearest class wins". Writing `parents` on the banner to a class of the model's own
/// binds that class's `text` getter in addition to the `Banner` one; both are getters of the one property, and the order of the two decides.
/// Observed: the `Banner` getter keeps the value (the pack loaded before or after the parents write, same result), so the model's own class
/// `text` never shows. The way that works is the pack that defines `Banner.text` itself (the test above).
#[test]
fn reassigning_parents_to_a_class_with_its_own_text_getter_leaves_the_default_text_standing() {
    let own = "PACK own 1\nIMPORT default_banner 1\nOwnBanner.text getter: 'own text'\n";
    let h = seat(); h.load_pack("own", own).unwrap(); h.rebuild(); h.pump();
    h.write(B, "parents", json!(["cls:ownbanner"]), "person"); h.pump();
    assert!(text(&h).contains("CONVERSATION: main") && !text(&h).contains("own text"), "{}", text(&h));
    let h = seat(); h.write(B, "parents", json!(["cls:ownbanner"]), "person"); h.pump();
    h.load_pack("own", own).unwrap(); h.rebuild(); h.pump();
    assert!(text(&h).contains("CONVERSATION: main") && !text(&h).contains("own text"), "{}", text(&h));
}
