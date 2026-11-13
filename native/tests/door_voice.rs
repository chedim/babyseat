//! The voice family's two routes that touch the outside (packs/door_voice.c1f): /membrane/voice reads the person's voice entities with
//! `sql()` over the store, /ui/log appends to a real file (and rotates it). The spec harness has no store and does not read files back,
//! so these are tested here, against a real SQLite file and a real folder.
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

fn tag() -> usize {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
}

fn dir() -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("door-voice-{}-{}", std::process::id(), tag()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// A store with the Python seat's voice entities as it writes them (c_entities: k = eid, body.d.data.properties).
fn db(d: &std::path::Path) -> std::path::PathBuf {
    let p = d.join("field.sqlite3");
    let c = rusqlite::Connection::open(&p).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    c.execute_batch("CREATE TABLE IF NOT EXISTS c_entities (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
    let put = |k: &str, props: J| {
        let body = json!({"e": k, "d": {"meta": {"type": "QuantumEntity"}, "type": "entities", "data": {"class": "Config", "properties": props}}});
        c.execute("INSERT OR REPLACE INTO c_entities VALUES (?1, ?2)", (k, body.to_string())).unwrap();
    };
    put("membrane::Config:voice:in:dmitrii-chechetkin", json!({"person": "Dmitrii Chechetkin", "on": true, "mode": "addressed", "device": "phone", "session": "ses_1",
        "heard": [{"ts": 1.0, "text": "hey memo", "session": "ses_1"}], "last_heard": "hey memo"}));
    put("membrane::Config:voice:out:dmitrii-chechetkin", json!({"person": "Dmitrii Chechetkin", "on": false, "device": "phone", "said": [{"ts": 2.0, "text": "hello"}]}));
    put("membrane::Config:voice:in:owner", json!({"on": true, "mode": "ptt"}));
    for (k, p) in voice_ents() { put(k, p); }
    put("membrane::Config:voice:in:old-one", json!({"person": "Old One", "on": false}));                      // made before the kind stamp
    put("membrane::Config:voice:out:old-one", json!({"person": "Old One", "on": false}));
    put("membrane::Config:other", json!({"on": true}));
    p
}

/// The voice entities the Python seat stamps with the kind (voice_io.entity): in the store and held by the engine.
fn voice_ents() -> Vec<(&'static str, J)> {
    vec![("membrane::Config:voice:in:alice-b", json!({"person": "Alice B", "on": false, "voice_io": "in", "mode": "ptt", "device": "tablet", "parents": ["cls:voice"]})),
         ("membrane::Config:voice:out:alice-b", json!({"person": "Alice B", "on": true, "voice_io": "out", "device": "tablet", "parents": ["cls:voice"]}))]
}

fn world(d: &std::path::Path) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for (n, t) in [("boot", include_str!("../../membrane/packs/boot.c1f")), ("fs", include_str!("../../membrane/packs/fs.c1f")),
                   ("door", include_str!("../../membrane/packs/door.c1f")), ("door_voice", include_str!("../../membrane/packs/door_voice.c1f"))] {
        f.set_pack(n, t).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    let mut c = Core::new();
    let path = db(d).to_str().unwrap().to_string();
    c.store = Some(Store::with_exec(&path, std::sync::Arc::new(membrane_vm::sqlexec::NativeSqlExec::new(&path).unwrap())));
    let o = |v: J| v.as_object().unwrap().clone();
    for (k, p) in voice_ents() { c.load(k.into(), "Config".into(), p.as_object().unwrap().clone(), None, 0.0); }
    c.load("Boot:seat".into(), "Boot".into(), o(json!({"ui_dir": "/x", "facts": {"seat_data_dir": d.to_str().unwrap()}})), None, 0.0);
    (c, f)
}

/// One request through the door: `who` None = a caller with no token on this machine. -> the reply.
fn ask(d: &std::path::Path, path: &str, body: &str, who: Option<&str>) -> J { ask_in(d, path, body, who).0 }

/// The same, and the field after it: the entities the request wrote. The body is born WITH the request, as the door's `Request:<n>`
/// entities are (http.rs pushes every prop at once).
fn ask_in(d: &std::path::Path, path: &str, body: &str, who: Option<&str>) -> (J, Core) { ask_with(d, path, body, who, false) }

/// `late`: the body arrives as a write only AFTER the store's rows have landed (the order the old harness raced into, driven
/// explicitly here: no sleeps, the engine is settled until the rows are in, then the body is written).
fn ask_with(d: &std::path::Path, path: &str, body: &str, who: Option<&str>, late: bool) -> (J, Core) {
    let (mut c, mut f) = world(d);
    let mut req = json!({"method": "POST", "query": "", "headers": {}, "peer": "127.0.0.1:5", "path": path, "conn": 7});
    if !late { req["body"] = json!(body); }
    match who {
        Some(s) => { req["authed"] = json!(true); req["sub"] = json!(s); req["claims"] = json!({"sub": s}); }
        None => { req["authed"] = json!(false); req["auth_error"] = json!("no token"); }
    }
    let mut seq = 1;
    f.rebuild(&mut c, 1.0, &mut seq);
    // born as the door's requests are: pushed into the engine with every prop at once (http.rs), so its setters fire
    c.store.as_ref().unwrap().push_inbound("Request:1".into(), "Request".into(), req.as_object().unwrap().clone());
    f.settle(&mut c, &H, 2.0, &mut seq);
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    let rows_in = |c: &Core| ["vs_in_q", "vs_out_q"].iter().all(|k| c.docs["Request:1"].props.get(*k).map_or(false, |v| !v.is_null()));
    if late {
        while !rows_in(&c) {
            if t.elapsed().as_secs() >= 10 { panic!("no rows: {:?}", c.docs["Request:1"].props); }
            wt.wait(Some(std::time::Duration::from_millis(20)));
            f.settle(&mut c, &H, 2.5, &mut seq);
        }
        let wr = membrane_vm::vm::Write { eid: "Request:1".into(), prop: "body".into(), new: json!(body), old: J::Null, writer: "person".into(), chain: json!({}), seq: 2 };
        c.hear(wr.clone(), None, 2.0);
        f.react(&mut c, &H, wr, 2.0, &mut seq);
    }
    loop {
        f.settle(&mut c, &H, 3.0, &mut seq);
        let r = c.docs["Request:1"].props.get("reply").cloned().unwrap_or(J::Null);
        if !r.is_null() { f.settle(&mut c, &H, 4.0, &mut seq); return (r, c); }
        if t.elapsed().as_secs() >= 10 { panic!("no reply: {:?}", c.docs["Request:1"].props); }
        wt.wait(Some(std::time::Duration::from_millis(20)));
    }
}

#[test]
fn the_voice_read_is_the_persons_two_entities_state_and_rings() {
    let d = dir();
    let r = ask(&d, "/membrane/voice", "{}", Some("Dmitrii Chechetkin"));
    assert_eq!(r["status"], 200, "{r}");
    assert_eq!(r["body"], json!({"ok": true,
        "in": {"on": true, "mode": "addressed", "device": "phone", "session": "ses_1", "log": [{"ts": 1.0, "text": "hey memo", "session": "ses_1"}]},
        "out": {"on": false, "mode": null, "device": "phone", "session": null, "log": [{"ts": 2.0, "text": "hello"}]}}));
}

#[test]
fn a_person_with_no_voice_entities_reads_empty_state_and_logs() {
    let d = dir();
    let r = ask(&d, "/membrane/voice", "{}", Some("Nobody"));
    assert_eq!(r["body"], json!({"ok": true,
        "in": {"on": null, "mode": null, "device": null, "session": null, "log": []},
        "out": {"on": null, "mode": null, "device": null, "session": null, "log": []}}));
}

#[test]
fn a_caller_with_no_token_is_refused_even_from_this_machine() {
    // a localhost peer is not the owner (#842): the body's `person` no longer names anyone for a caller with no token
    let d = dir();
    let r = ask(&d, "/membrane/voice", "{\"person\": \"owner\"}", None);
    assert_eq!(r["status"], 401, "{r}");
}

#[test]
fn the_verified_person_wins_over_a_name_in_the_body() {
    let d = dir();
    let r = ask(&d, "/membrane/voice", "{\"person\": \"owner\"}", Some("Nobody"));
    assert_eq!(r["body"]["in"]["mode"], J::Null, "{r}");
}

#[test]
fn the_page_log_appends_one_line_per_post_and_answers_204() {
    let d = dir();
    let r = ask(&d, "/ui/log", "{\"t\": 1,\n \"heard\": \"hey\"}", Some("owner"));
    assert_eq!(r["status"], 204, "{r}");
    let r = ask(&d, "/ui/log", "{\"t\": 2}", Some("owner"));
    assert_eq!(r["status"], 204);
    assert_eq!(std::fs::read_to_string(d.join("page-voice.log")).unwrap(), "{\"t\": 1,  \"heard\": \"hey\"}\n{\"t\": 2}\n");
}

#[test]
fn a_log_over_two_megabytes_is_moved_aside_before_the_next_line() {
    let d = dir();
    std::fs::write(d.join("page-voice.log"), vec![b'x'; 2_000_001]).unwrap();
    let r = ask(&d, "/ui/log", "{\"t\": 3}", Some("owner"));
    assert_eq!(r["status"], 204, "{r}");
    assert_eq!(std::fs::metadata(d.join("page-voice.log.old")).unwrap().len(), 2_000_001);
    assert_eq!(std::fs::read_to_string(d.join("page-voice.log")).unwrap(), "{\"t\": 3}\n");
}

#[test]
fn a_line_that_is_not_json_is_not_logged() {
    let d = dir();
    let r = ask(&d, "/ui/log", "not json", Some("owner"));
    assert_eq!(r["status"], 204);
    assert!(!d.join("page-voice.log").exists());
}

fn props<'a>(c: &'a Core, eid: &str) -> &'a serde_json::Map<String, J> { &c.docs[eid].props }
const I: &str = "membrane::Config:voice:in:alice-b";
const O: &str = "membrane::Config:voice:out:alice-b";

#[test]
fn the_state_route_writes_the_persons_two_voice_entities() {
    let d = dir();
    let (r, c) = ask_in(&d, "/membrane/voice/state", "{\"in_on\": true, \"in_mode\": \"addressed\", \"out_on\": false, \"device\": \"phone\"}", Some("Alice B"));
    assert_eq!(r["status"], 200, "{r}");
    assert_eq!(r["body"], json!({"ok": true, "state": {"in": {"on": true, "mode": "addressed", "device": "phone"}, "out": {"on": false, "device": "phone"}}}));
    assert_eq!((props(&c, I)["on"].clone(), props(&c, I)["mode"].clone(), props(&c, I)["device"].clone()), (json!(true), json!("addressed"), json!("phone")));
    assert_eq!((props(&c, O)["on"].clone(), props(&c, O)["device"].clone()), (json!(false), json!("phone")));
    assert!(props(&c, I)["state_ts"].is_number() && props(&c, O)["state_ts"].is_number());
    assert_eq!(props(&c, O)["person"], "Alice B");                                  // the rest of the entity is untouched
    assert_eq!(props(&c, I)["parents"], json!(["cls:voice"]), "a stored list is what it is (no prefix put back)");
}

#[test]
fn a_mode_outside_the_list_is_off_and_a_device_is_clipped_to_eighty() {
    let d = dir();
    let dev = "d".repeat(100);
    let (r, c) = ask_in(&d, "/membrane/voice/state", &format!("{{\"in_mode\": \"nonsense\", \"device\": \"{dev}\"}}"), Some("Alice B"));
    assert_eq!(r["status"], 200, "{r}");
    assert_eq!(props(&c, I)["mode"], "off");
    assert_eq!(props(&c, I)["device"], "d".repeat(80).as_str());
    assert_eq!(props(&c, O)["device"], "d".repeat(80).as_str());
}

#[test]
fn what_the_body_leaves_out_keeps_its_old_value() {
    let d = dir();
    let (r, c) = ask_in(&d, "/membrane/voice/state", "{\"in_on\": true}", Some("Alice B"));
    assert_eq!(r["body"], json!({"ok": true, "state": {"in": {"on": true, "mode": "ptt", "device": "tablet"}, "out": {"on": true, "device": "tablet"}}}), "{r}");
    assert_eq!((props(&c, I)["mode"].clone(), props(&c, I)["device"].clone(), props(&c, O)["on"].clone()), (json!("ptt"), json!("tablet"), json!(true)));
}

#[test]
fn a_person_with_no_voice_entities_gets_them_on_the_first_state_write() {
    let d = dir();
    let (r, c) = ask_in(&d, "/membrane/voice/state", "{\"in_on\": true, \"in_mode\": \"ptt\", \"device\": \"mic\"}", Some("Nobody"));
    assert_eq!(r["body"], json!({"ok": true, "state": {"in": {"on": true, "mode": "ptt", "device": "mic"}, "out": {"on": false, "device": "mic"}}}), "{r}");
    let (i, o) = ("membrane::Config:voice:in:nobody", "membrane::Config:voice:out:nobody");
    for (e, io) in [(i, "in"), (o, "out")] {
        assert_eq!(props(&c, e)["parents"], json!(["cls:config", "cls:voice"]), "{e}: the kind mirror");
        assert_eq!((props(&c, e)["person"].clone(), props(&c, e)["voice_io"].clone()), (json!("Nobody"), json!(io)));
        assert_eq!(c.docs[e].cls, "Config", "the class the Python seat gives it");
        assert!(props(&c, e)["state_ts"].is_number());
    }
    assert_eq!((props(&c, i)["on"].clone(), props(&c, i)["mode"].clone(), props(&c, i)["device"].clone()), (json!(true), json!("ptt"), json!("mic")));
    assert_eq!(props(&c, o)["on"], false);
}

#[test]
fn an_old_entity_without_the_kind_stamp_is_mirrored_and_keeps_its_values() {
    let d = dir();
    let (r, c) = ask_in(&d, "/membrane/voice/state", "{\"in_on\": true}", Some("Old One"));
    assert_eq!(r["status"], 200, "{r}");
    let i = "membrane::Config:voice:in:old-one";
    assert_eq!(props(&c, i)["parents"], json!(["cls:config", "cls:voice"]), "stamped now, as voice_io.entity stamps old ones");
    assert_eq!(props(&c, i)["person"], "Old One", "the person the store has is not overwritten");
    assert_eq!(props(&c, i)["on"], true);
}

#[test]
fn the_state_route_refuses_a_caller_with_no_token_and_names_the_person_from_the_token() {
    let d = dir();
    let (r, _) = ask_in(&d, "/membrane/voice/state", "{\"person\": \"Alice B\", \"out_on\": false}", None);
    assert_eq!(r["status"], 401, "{r}");
    let (r, c) = ask_in(&d, "/membrane/voice/state", "{\"out_on\": false}", Some("Alice B"));
    assert_eq!(r["status"], 200, "{r}");
    assert_eq!(props(&c, O)["on"], false);
}

#[test]
fn the_state_write_waits_for_the_body_whenever_it_arrives() {
    // the rows land first, the body after: the write must carry the body's values (it fired once, on the rows, with an empty body)
    let d = dir();
    let (r, c) = ask_with(&d, "/membrane/voice/state", "{\"in_on\": true}", Some("Old One"), true);
    assert_eq!(r["status"], 200, "{r}");
    assert_eq!(props(&c, "membrane::Config:voice:in:old-one")["on"], true, "the write used the body that arrived after the rows");
}
