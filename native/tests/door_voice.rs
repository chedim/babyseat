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
    put("membrane::Config:other", json!({"on": true}));
    p
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
    c.load("Boot:seat".into(), "Boot".into(), o(json!({"ui_dir": "/x", "facts": {"seat_data_dir": d.to_str().unwrap()}})), None, 0.0);
    (c, f)
}

/// One request through the door: `who` None = a caller with no token on this machine. -> the reply.
fn ask(d: &std::path::Path, path: &str, body: &str, who: Option<&str>) -> J {
    let (mut c, mut f) = world(d);
    let mut req = json!({"method": "POST", "query": "", "headers": {}, "peer": "127.0.0.1:5", "path": path, "conn": 7});
    match who {
        Some(s) => { req["authed"] = json!(true); req["sub"] = json!(s); req["claims"] = json!({"sub": s}); }
        None => { req["authed"] = json!(false); req["auth_error"] = json!("no token"); }
    }
    c.load("Request:1".into(), "Request".into(), req.as_object().unwrap().clone(), None, 0.0);
    let mut seq = 1;
    f.rebuild(&mut c, 1.0, &mut seq);
    f.settle(&mut c, &H, 2.0, &mut seq);
    // the body arrives as a write, as the door's requests do (a setter fires on a change)
    let wr = membrane_vm::vm::Write { eid: "Request:1".into(), prop: "body".into(), new: json!(body), old: J::Null, writer: "person".into(), chain: json!({}), seq: 2 };
    c.hear(wr.clone(), None, 2.0);
    f.react(&mut c, &H, wr, 2.0, &mut seq);
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    loop {
        f.settle(&mut c, &H, 3.0, &mut seq);
        let r = c.docs["Request:1"].props.get("reply").cloned().unwrap_or(J::Null);
        if !r.is_null() { return r; }
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
fn a_caller_with_no_token_is_the_person_the_page_stamped_in_the_body() {
    let d = dir();
    let r = ask(&d, "/membrane/voice", "{\"person\": \"owner\"}", None);
    assert_eq!(r["body"]["in"]["mode"], "ptt", "{r}");
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
