//! The decision family's two store-backed routes (packs/door_decisions.c1f): /membrane/entity-history and /membrane/item-chat read the
//! store's delta and relation rows with `sql()`. The spec harness has no store, so these two are tested here, against a real SQLite file.
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

fn db() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("door-decisions-{}-{}", std::process::id(), tag()));
    std::fs::create_dir_all(&dir).unwrap();
    let p = dir.join("field.sqlite3");
    let c = rusqlite::Connection::open(&p).unwrap();
    c.pragma_update(None, "journal_mode", "WAL").unwrap();
    c.execute_batch("CREATE TABLE c_entity_deltas (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_relations (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
    let put = |t: &str, k: &str, o: i64, d: J| { c.execute(&format!("INSERT INTO {t} VALUES (?1, ?2)"), (k, json!({"o": o, "d": d}).to_string())).unwrap(); };
    put("c_entity_deltas", "d1", 1, json!({"eid": "Goal:g", "seq": 2, "sidx": 0, "event": "e2", "what": "second"}));
    put("c_entity_deltas", "d2", 2, json!({"eid": "Goal:g", "seq": 1, "sidx": 0, "event": "e1", "what": "first"}));
    put("c_entity_deltas", "d3", 3, json!({"eid": "Goal:other", "seq": 1, "sidx": 0, "what": "not this one"}));
    put("c_relations", "r1", 1, json!({"subj": "Message:m2", "rel": "refers-to", "obj": "Goal:g", "superseded": false}));
    put("c_relations", "r2", 2, json!({"subj": "Message:m1", "rel": "refers-to", "obj": "Goal:g", "superseded": false}));
    put("c_relations", "r3", 3, json!({"subj": "Message:old", "rel": "refers-to", "obj": "Goal:g", "superseded": true}));
    put("c_relations", "r4", 4, json!({"subj": "Goal:g", "rel": "watches", "obj": "Goal:other", "superseded": false}));
    put("c_relations", "r6", 6, json!({"subj": "Message:w", "rel": "mentions", "obj": "Goal:g", "superseded": false}));
    put("c_relations", "r5", 5, json!({"subj": "Message:x", "rel": "refers-to", "obj": "Goal:other", "superseded": false}));
    p
}

fn world(p: &std::path::Path) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for (n, t) in [("boot", include_str!("../../membrane/packs/boot.c1f")), ("fs", include_str!("../../membrane/packs/fs.c1f")),
                   ("banner", include_str!("../../membrane/packs/banner.c1f")), ("intents", include_str!("../../membrane/packs/intents.c1f")),
                   ("decisions", include_str!("../../membrane/packs/decisions.c1f")), ("door", include_str!("../../membrane/packs/door.c1f")),
                   ("door_decisions", include_str!("../../membrane/packs/door_decisions.c1f"))] {
        f.set_pack(n, t).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    let mut c = Core::new();
    let path = p.to_str().unwrap().to_string();
    c.store = Some(Store::with_exec(&path, std::sync::Arc::new(membrane_vm::sqlexec::NativeSqlExec::new(&path).unwrap())));
    let o = |v: J| v.as_object().unwrap().clone();
    c.load("Boot:seat".into(), "Boot".into(), o(json!({"ui_dir": "/x"})), None, 0.0);
    c.load("Goal:g".into(), "Goal".into(), o(json!({"name": "g", "status": "open"})), None, 0.0);
    c.load("Message:m1".into(), "Message".into(), o(json!({"speaker": "owner", "text": "hello", "ts": 20.0})), None, 0.0);
    c.load("Message:m2".into(), "Message".into(), o(json!({"speaker": "model", "text": "later", "ts": 30.0})), None, 0.0);
    c.load("Message:w".into(), "Message".into(), o(json!({"text": "mentions only", "ts": 5.0})), None, 0.0);
    c.load("Message:old".into(), "Message".into(), o(json!({"text": "superseded", "ts": 1.0})), None, 0.0);
    (c, f)
}

fn ask(path: &str, query: &str) -> J {
    let p = db();
    let (mut c, mut f) = world(&p);
    let req = json!({"method": "GET", "query": query, "headers": {}, "peer": "127.0.0.1:5", "authed": true, "sub": "owner", "auth_error": null, "path": path});
    c.load("Request:1".into(), "Request".into(), req.as_object().unwrap().clone(), None, 0.0);
    let mut seq = 1;
    f.rebuild(&mut c, 1.0, &mut seq);
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
fn an_entitys_history_is_the_stores_deltas_oldest_first_and_every_relation_it_is_in() {
    let r = ask("/membrane/entity-history", "spec=Goal%3Ag");
    assert_eq!(r["status"], 200, "{r}");
    let b = &r["body"];
    assert_eq!(b["ok"], true);
    assert_eq!(b["eid"], "Goal:g");
    assert_eq!(b["cls"], "Goal");
    assert_eq!(b["changes"].as_array().unwrap().iter().map(|x| x["what"].as_str().unwrap()).collect::<Vec<_>>(), ["first", "second"]);
    let rels: Vec<&str> = b["relations"].as_array().unwrap().iter().map(|x| x["subj"].as_str().unwrap()).collect();
    assert_eq!(rels, ["Message:m2", "Message:m1", "Message:old", "Goal:g", "Message:w"]);          // as the subject or the object, live and superseded
}

#[test]
fn an_unknown_entity_is_not_found() {
    let r = ask("/membrane/entity-history", "spec=Goal%3Anope");
    assert_eq!(r["status"], 200);
    assert_eq!(r["body"], json!({"ok": false, "error": "no entity matches 'Goal:nope'"}));
    let r = ask("/membrane/entity-history", "");
    assert_eq!(r["body"]["ok"], false);
}

#[test]
fn an_items_chat_is_the_messages_that_refer_to_it_oldest_first_live_ones_only() {
    let r = ask("/membrane/item-chat", "spec=Goal%3Ag");
    assert_eq!(r["status"], 200, "{r}");
    let b = &r["body"];
    assert_eq!(b["ok"], true);
    assert_eq!(b["eid"], "Goal:g");
    assert_eq!(b["messages"], json!([{"id": "Message:m1", "who": "owner", "text": "hello", "ts": 20.0}, {"id": "Message:m2", "who": "model", "text": "later", "ts": 30.0}]));
}

#[test]
fn a_post_to_a_read_route_is_a_405() {
    let p = db();
    let (mut c, mut f) = world(&p);
    let req = json!({"method": "POST", "query": "", "headers": {}, "peer": "127.0.0.1:5", "authed": true, "sub": "owner", "auth_error": null, "path": "/membrane/item-chat"});
    c.load("Request:1".into(), "Request".into(), req.as_object().unwrap().clone(), None, 0.0);
    let mut seq = 1;
    f.rebuild(&mut c, 1.0, &mut seq);
    f.settle(&mut c, &H, 3.0, &mut seq);
    assert_eq!(c.docs["Request:1"].props["reply"]["status"], 405);
}
