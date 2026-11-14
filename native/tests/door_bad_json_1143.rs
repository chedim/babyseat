//! A malformed JSON body to the door is handled exactly as before `json(bad text)` became an Error (#1143, operator decision 2026-11-13):
//! every pack line that read "not JSON is fine" treats the Error as the plain null it used to be.
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

fn dir() -> std::path::PathBuf {
    static N: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
    let d = std::env::temp_dir().join(format!("door_bad_json_{}_{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// One POST through the door packs: -> (the request's props after the engine settled, the data dir)
fn ask(path: &str, body: &str) -> (serde_json::Map<String, J>, std::path::PathBuf) {
    let d = dir();
    let mut f = Fields::goalgraph();
    // every shipped pack, in dependency order (a pack whose import is not loaded yet waits for the next pass)
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane/packs");
    let mut todo: Vec<(String, String)> = std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.extension().map_or(false, |x| x == "c1f"))
        .map(|p| (p.file_stem().unwrap().to_string_lossy().to_string(), std::fs::read_to_string(&p).unwrap())).collect();
    todo.sort();
    while !todo.is_empty() {
        let before = todo.len();
        todo.retain(|(n, t)| f.set_pack(n, t).is_err());
        if todo.len() == before { break; /* a pack that needs more than the packs (about) is left out */ }
    }
    let mut c = Core::new();
    let p = d.join("field.sqlite3").to_str().unwrap().to_string();
    { let c = rusqlite::Connection::open(&p).unwrap(); c.pragma_update(None, "journal_mode", "WAL").unwrap();
      c.execute_batch("CREATE TABLE IF NOT EXISTS c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL, cls TEXT, updated REAL NOT NULL DEFAULT 0);").unwrap(); }
    c.store = Some(Store::with_exec(&p, std::sync::Arc::new(membrane_vm::sqlexec::NativeSqlExec::new(&p).unwrap())));
    c.load("Boot:seat".into(), "Boot".into(), json!({"ui_dir": "/x", "facts": {"seat_data_dir": d.to_str().unwrap()}}).as_object().unwrap().clone(), None, 0.0);
    let req = json!({"method": "POST", "query": "", "headers": {}, "peer": "127.0.0.1:5", "path": path, "conn": 7, "body": body, "authed": true, "sub": "owner", "claims": {"sub": "owner"}});
    let mut seq = 1;
    f.rebuild(&mut c, 1.0, &mut seq);
    c.store.as_ref().unwrap().push_inbound("Request:1".into(), "Request".into(), req.as_object().unwrap().clone());
    let wt = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    loop {
        f.settle(&mut c, &H, 3.0, &mut seq);
        if c.docs["Request:1"].props.get("reply").map_or(false, |r| !r.is_null()) || t.elapsed().as_secs() >= 5 { break; }
        wt.wait(Some(std::time::Duration::from_millis(20)));
    }
    f.settle(&mut c, &H, 4.0, &mut seq);
    (c.docs["Request:1"].props.clone(), d)
}

const BAD: [&str; 3] = ["{not json", "nope", "{\"a\": "];

#[test]
fn a_body_that_is_not_json_reads_as_an_empty_object_for_every_route_that_wants_one() {
    for b in BAD {
        let (p, _) = ask("/membrane/seat/pause", b);
        assert_eq!(p["jbody"], json!({}), "{b}: {:?}", p.get("jbody"));
    }
}

#[test]
fn an_empty_body_reads_as_an_empty_object_too() {
    let (p, _) = ask("/membrane/seat/pause", "");
    assert_eq!(p["jbody"], json!({}));
}

#[test]
fn a_login_whose_body_is_not_json_has_no_token() {
    for b in BAD.iter().chain(&[""]) {
        let (p, _) = ask("/ui/login", b);
        assert!(p.get("login_token").map_or(true, |t| t.is_null()), "{b}: {:?}", p.get("login_token"));
        assert!(p.get("login_has").map_or(true, |t| t == &json!(false)), "{b}: {:?}", p.get("login_has"));
    }
}

#[test]
fn an_mcp_post_that_is_not_json_is_a_parse_error() {
    for b in BAD.iter().chain(&[""]) {
        let (p, _) = ask("/mcp", b);
        assert_eq!(p["reply"], json!({"status": 400, "body": {"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "parse error"}}}), "{b}");
    }
}

#[test]
fn a_page_log_line_that_is_not_json_is_not_logged() {
    for b in BAD {
        let (p, d) = ask("/ui/log", b);
        assert_eq!(p["reply"]["status"], json!(204), "{b}: {:?}", p.get("reply"));
        assert!(!d.join("page-voice.log").exists(), "{b}");
    }
}

#[test]
fn a_decision_post_that_is_not_json_has_no_body() {
    for b in BAD {
        let (p, _) = ask("/membrane/decisions/answer", b);
        assert!(p.get("dm_body").map_or(true, |t| t.is_null()), "{b}: {:?}", p.get("dm_body"));
        assert!(p.get("dm_obj").map_or(true, |t| t == &json!(false)), "{b}: {:?}", p.get("dm_obj"));
    }
}
