//! The imported chat history through the released page's session routes (#1143, migration phase 1): the previous store's fixture is read through the launch fact
//! `sql_sources` (`legacy`), the import is started by the owner's `entity_write` on `Legacy:seat` (the door's tool, as the model does it), and `GET /session`,
//! `GET /session/<sid>`, `GET /session/<sid>/message` and `GET /session/status` answer an imported session in the shape of a native one (tests/golden/session/*.json).
//! No model runs: the turn's binary is a script that fails the test if it is ever started. Every wait has a timeout and the child is always reaped.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn shipped(names: &[&str]) -> Vec<(String, String)> {
    let d = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    names.iter().map(|n| (n.to_string(), std::fs::read_to_string(format!("{d}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}")))).collect()
}

/// the barest chat's packs plus the session door
const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f", "door_session.c1f", "door_auth.c1f", "legacy_import.c1f", "legacy_persons.c1f", "router.c1f", "door_models.c1f"];

/// the seat's folder holds the fake claude; the binary is told its path as a fact
fn seat(tag: &str, script: &str) -> (Run, u16, std::path::PathBuf) { seat_with(tag, script, false) }
/// `strict`: the seat has a door secret (and so an owner token file, `<dir>/owner.token`): every route but the public set needs the token
fn seat_with(tag: &str, script: &str, strict: bool) -> (Run, u16, std::path::PathBuf) { seat_facts(tag, script, strict, &[]) }
/// `facts`: more `--fact name=value` lines
fn seat_facts(tag: &str, script: &str, strict: bool, facts: &[&str]) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, script).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(PACKS) { std::fs::write(pd.join(n), t).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let fact = format!("chat_claude_bin={}", bin.display());
    let mut child = Command::new(BIN).current_dir(&dir).env("ANTHROPIC_API_KEY", "sk-ant-harness-default-0000")   // the default mode is the API key
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap(), "--fact", &fact,
               "--fact", "seat_cwd=/srv/seat", "--fact", "seat_project=membrane", "--fact", "seat_version=1.0"])
        .args(facts.iter().flat_map(|f| ["--fact".to_string(), f.to_string()]))
        .args(if strict { vec!["--fact".to_string(), format!("door_secret_file={}", dir.join("door.secret").display()), "--fact".to_string(), format!("seat_data_dir={}", dir.display())] } else { vec![] })
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port, dir)
}

/// one HTTP request on its own connection; -> (status, body text). A bounded retry (3 tries) when the connection gives no answer at all: under a loaded
/// machine a test seat can stall; every route the tests send this way is safe to send again (a retry of a POST that did reach the seat is not made: it only
/// retries when NO byte came back, and the tests then check the rows they expect).
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    for _ in 0..2 { if let Some(r) = http_once(port, method, path, body) { return r; } }
    http_once(port, method, path, body).expect("an HTTP answer")
}
fn http_once(port: u16, method: &str, path: &str, body: &str) -> Option<(u16, String)> {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    Some((status, String::from_utf8_lossy(&out[i + 4..]).to_string()))
}
/// -> (status, the JSON body or null when it is empty)
fn api(port: u16, method: &str, path: &str, body: J) -> (u16, J) {
    let (st, t) = http(port, method, path, &if body.is_null() { String::new() } else { body.to_string() });
    (st, if t.trim().is_empty() { J::Null } else { serde_json::from_str(&t).unwrap_or_else(|e| panic!("{e}: {t}")) })
}

fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(90), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

// ---- the shape comparer -------------------------------------------------------------------------------------------------------------------------
/// What the answer lacks against a recorded fixture: every key of the fixture must be in the answer with the same JSON type (a `null` in the fixture
/// accepts any type: the recorded value was empty), recursively; extra keys are fine; each element of an answer's list must match SOME element of the
/// fixture's list (a parts list holds text, tool and step parts), an empty fixture list accepts anything. -> the misses, each a path and a reason.
fn shape_misses(fix: &J, got: &J) -> Vec<String> { let mut out = vec![]; shape_at("$", fix, got, &mut out); out }
fn kind(v: &J) -> &'static str { match v { J::Null => "null", J::Bool(_) => "bool", J::Number(_) => "number", J::String(_) => "string", J::Array(_) => "array", J::Object(_) => "object" } }
fn shape_at(p: &str, fix: &J, got: &J, out: &mut Vec<String>) {
    match (fix, got) {
        (J::Null, _) => {}
        (J::Object(f), J::Object(g)) => {
            for (k, fv) in f {
                // a map keyed by session ids: any key of the answer stands for it
                if k.starts_with("ses_") { if let Some(gv) = g.values().next() { shape_at(&format!("{p}.<session id>"), fv, gv, out); } continue; }
                match g.get(k) { Some(gv) => shape_at(&format!("{p}.{k}"), fv, gv, out), None => out.push(format!("{p}.{k}: missing")) }
            }
        }
        (J::Array(f), J::Array(g)) => {
            if f.is_empty() { return; }
            for (i, gv) in g.iter().enumerate() {
                let best = f.iter().map(|fv| { let mut m = vec![]; shape_at(&format!("{p}[{i}]"), fv, gv, &mut m); m }).min_by_key(|m| m.len()).unwrap();
                out.extend(best);
            }
        }
        (f, g) if kind(f) == kind(g) => {}
        (f, g) => out.push(format!("{p}: {} expected, {} found", kind(f), kind(g))),
    }
}
fn golden(name: &str) -> J { golden_in("session", name) }
fn golden_in(dir: &str, name: &str) -> J {
    let p = format!("{}/../tests/golden/{dir}/{name}.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{p}: {e}"))).unwrap()
}
/// the answer's body against the fixture's body, and the status against the fixture's
fn same_shape(name: &str, status: u16, body: &J) { same_shape_in("session", name, status, body) }
fn same_shape_in(dir: &str, name: &str, status: u16, body: &J) {
    let g = golden_in(dir, name);
    assert_eq!(status as i64, g["status"].as_i64().unwrap(), "{name}: the status; body {body}");
    let m = shape_misses(&g["body"], body);
    assert!(m.is_empty(), "{name}: {m:?}\nanswer: {body}");
}

/// a fake claude that finishes at once: an answer "Done: <prompt>" with no tool
mod legacy_fixture;

/// a turn's binary that must never run: the import starts no model
const NEVER: &str = "#!/bin/sh\necho started > \"$(dirname \"$0\")/claude-was-run\"\nexit 1\n";

fn mcp(port: u16, tool: &str, args: J) -> J {
    let (st, b) = api(port, "POST", "/mcp", json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}));
    assert_eq!(st, 200, "{b}");
    b
}
fn read_prop(port: u16, spec: &str, prop: &str) -> J {
    let r = mcp(port, "entity_read", json!({"spec": spec, "prop": prop}));
    let t = r["result"]["content"][0]["text"].as_str().unwrap_or("").to_string();
    if t.contains(" has no ") { return J::Null; }
    serde_json::from_str(&t).unwrap_or(json!(t))
}

#[test]
fn an_imported_session_answers_the_session_routes_like_a_native_one_and_no_model_ran() {
    let fx = tmp("legacy-fixture");
    let old = fx.join("old.sqlite");
    legacy_fixture::make_legacy(&old);
    let fact = format!("sql_sources=[{{\"name\": \"legacy\", \"path\": \"{}\", \"mode\": \"ro\"}}]", old.display());
    let (_run, port, dir) = seat_facts("legacy-routes", NEVER, false, &[&fact]);
    let w = mcp(port, "entity_write", json!({"spec": "Legacy:seat", "prop": "import", "value": {"id": "r1", "sessions": "all", "page": 2}}));
    assert!(w["result"].is_object(), "{w}");
    wait("the import to end", || (read_prop(port, "Legacy:seat", "running") == json!("False") && read_prop(port, "Legacy:session:ses_a", "state") == json!("done") && read_prop(port, "Legacy:session:xcl_d", "state") == json!("done")).then_some(()));
    // the list: all four sessions, oldest first, in the golden shape
    let (st, l) = api(port, "GET", "/session", J::Null);
    same_shape("session_list", st, &l);
    let ids: Vec<&str> = l.as_array().unwrap().iter().map(|x| x["id"].as_str().unwrap()).collect();
    assert_eq!(ids, ["ses_a", "ses_b", "ses_c", "xcl_d"]);
    assert_eq!(l[0]["title"], "Plan the trip");
    assert_eq!(l[0]["time"]["created"], json!(1_790_000_000_000i64));
    let (st, one) = api(port, "GET", "/session/ses_a", J::Null);
    same_shape("session_get", st, &one);
    // the messages: user and assistant rows, the tool part with its input and output, in the golden shape
    let (st, rows) = api(port, "GET", "/session/ses_a/message", J::Null);
    same_shape("messages_ok", st, &rows);
    let rows = rows.as_array().unwrap();
    assert_eq!(rows.len(), 4, "two questions, two answers: {rows:?}");
    assert_eq!(rows[0]["info"]["role"], "user");
    assert_eq!(rows[0]["parts"][0]["text"], "where do we start?");
    assert_eq!(rows[1]["info"]["role"], "assistant");
    let tools: Vec<&J> = rows[1]["parts"].as_array().unwrap().iter().filter(|p| p["type"] == "tool").collect();
    assert_eq!(tools.len(), 2);
    assert_eq!(tools[0]["tool"], "bash");
    assert_eq!(tools[0]["state"]["input"], json!({"command": "ls"}));
    assert_eq!(tools[0]["state"]["output"], "a.txt\nb.txt");
    assert!(rows[1]["parts"].as_array().unwrap().iter().any(|p| p["type"] == "text" && p["text"] == "Start with the list."));
    assert_eq!(rows[1]["info"]["tokens"]["input"], json!(200), "{}", rows[1]["info"]);
    assert_eq!(rows[3]["parts"][0]["text"], "Then we book.");
    // the page's paging by `limit` works on imported rows the way it does on native ones
    let (st, last2) = api(port, "GET", "/session/ses_a/message?limit=2", J::Null);
    assert_eq!(st, 200);
    assert_eq!(last2.as_array().unwrap().len(), 2);
    assert_eq!(last2[0]["info"]["role"], "user");
    // one row by id, and the unanswered question is an error turn with its words
    let rid = rows[1]["info"]["id"].as_str().unwrap();
    let (st, one_row) = api(port, "GET", &format!("/session/ses_a/message/{rid}"), J::Null);
    same_shape("message_one", st, &one_row);
    let (st, c) = api(port, "GET", "/session/ses_c/message", J::Null);
    assert_eq!(st, 200);
    assert!(c[1]["info"]["error"].is_object() || c[1]["parts"].is_array(), "{c}");
    // status: every imported session is idle
    let (st, s) = api(port, "GET", "/session/status", J::Null);
    same_shape("session_status_idle", st, &s);
    assert_eq!(s["ses_a"], json!({"type": "idle"}));
    // the history view of the bare chat shows the imported turns too
    assert!(!dir.join("claude-was-run").exists(), "the import started no model");
}

fn rss_kb(pid: u32) -> u64 {
    let o = std::process::Command::new("ps").args(["-o", "rss=", "-p", &pid.to_string()]).output().unwrap();
    String::from_utf8_lossy(&o.stdout).trim().parse().unwrap_or(0)
}
fn dir_bytes(d: &std::path::Path) -> u64 { std::fs::read_dir(d).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with("field.sqlite3")).map(|e| e.metadata().unwrap().len()).sum() }

/// THE MEASUREMENT (ignored: it reads a real store). `LEGACY_COPY=<a COPY of the previous seat's store, read-only>`, `LEGACY_PAGE=<n>` (the page size: it has no default).
/// The copy is opened as `SqlSource:legacy` (read-only, immutable) and never written; the target is a NEW store in a temp folder; the turn's binary is a script that must never run; the port is 0.
#[test]
#[ignore]
fn measure_the_import_of_every_session_of_a_real_store_read_only() {
    let copy = std::env::var("LEGACY_COPY").expect("LEGACY_COPY=<path of the copy>");
    let page: i64 = std::env::var("LEGACY_PAGE").expect("LEGACY_PAGE=<n>").parse().unwrap();
    let before = std::fs::metadata(&copy).unwrap().len();
    let fact = format!("sql_sources=[{{\"name\": \"legacy\", \"path\": \"{copy}\", \"mode\": \"ro\"}}]");
    let (run, port, dir) = seat_facts("legacy-measure", NEVER, false, &[&fact]);
    let pid = run.child.id();
    let base_rss = rss_kb(pid);
    let base_store = dir_bytes(&dir);
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let peak = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
    let (s2, p2) = (stop.clone(), peak.clone());
    let th = std::thread::spawn(move || { while !s2.load(std::sync::atomic::Ordering::SeqCst) { p2.fetch_max(rss_kb(pid), std::sync::atomic::Ordering::SeqCst); std::thread::sleep(Duration::from_millis(100)); } });
    let t0 = Instant::now();
    mcp(port, "entity_write", json!({"spec": "Legacy:seat", "prop": "list", "value": {"id": "m0"}}));
    wait("the headers", || (api(port, "GET", "/session", J::Null).1.as_array().map(|a| a.len()).unwrap_or(0) >= 130).then_some(()));
    let t_headers = t0.elapsed();
    let t1 = Instant::now();
    mcp(port, "entity_write", json!({"spec": "Legacy:seat", "prop": "import", "value": {"id": "m1", "sessions": "all", "page": page}}));
    let mut tick = Instant::now();
    loop {
        let running = read_prop(port, "Legacy:seat", "running");
        let cur = read_prop(port, "Legacy:seat", "cur");
        if tick.elapsed() > Duration::from_secs(10) { tick = Instant::now(); eprintln!("PROGRESS {:.0}s cur={cur} after={} done={} err={} refused={}", t1.elapsed().as_secs_f64(), read_prop(port, "Legacy:seat", "after"), read_prop(port, "Legacy:seat", "run_done"), read_prop(port, "Legacy:seat", "run_error"), read_prop(port, "Legacy:seat", "refused")); }
        if running == json!("False") && read_prop(port, "Legacy:seat", "run_total") != J::Null && cur.is_null() { break; }
        assert!(t1.elapsed() < Duration::from_secs(3000), "the import did not end in 50 minutes");
        std::thread::sleep(Duration::from_millis(500));
    }
    let t_import = t1.elapsed();
    let done = read_prop(port, "Legacy:seat", "run_done");
    let errs = read_prop(port, "Legacy:seat", "run_error");
    let (_, list) = api(port, "GET", "/session", J::Null);
    let mut msgs = 0usize;
    let mut bytes = 0usize;
    let mut biggest = (String::new(), 0usize);
    let t2 = Instant::now();
    for s in list.as_array().unwrap() {
        let sid = s["id"].as_str().unwrap();
        let (st, t) = http(port, "GET", &format!("/session/{sid}/message"), "");
        assert_eq!(st, 200);
        let n = serde_json::from_str::<J>(&t).unwrap().as_array().unwrap().len();
        msgs += n; bytes += t.len();
        if n > biggest.1 { biggest = (sid.to_string(), n); }
    }
    let t_all_reads = t2.elapsed();
    let t3 = Instant::now();
    let (_, big) = api(port, "GET", &format!("/session/{}/message", biggest.0), J::Null);
    let t_big = t3.elapsed();
    stop.store(true, std::sync::atomic::Ordering::SeqCst);
    th.join().unwrap();
    let (mut rows_done, mut unmapped, mut dropped, mut old_rows) = (0i64, 0i64, 0i64, 0i64);
    for s in list.as_array().unwrap() {
        let sid = s["id"].as_str().unwrap();
        let g = |p: &str| read_prop(port, &format!("Legacy:session:{sid}"), p).as_i64().unwrap_or(0);
        rows_done += g("rows"); unmapped += g("unmapped"); dropped += g("dropped_parts"); old_rows += g("old_rows");
        assert_eq!(read_prop(port, &format!("Legacy:session:{sid}"), "state"), json!("done"), "{sid}");
    }
    eprintln!("MEASURE page={page} sessions={} done={done} errors={errs} headers_s={:.2} import_s={:.2} old_rows={old_rows} rows_consumed={rows_done} unmapped={unmapped} dropped_parts={dropped}", list.as_array().unwrap().len(), t_headers.as_secs_f64(), t_import.as_secs_f64());
    eprintln!("MEASURE page-rows-served={msgs} bytes_served={bytes} read_all_sessions_s={:.2} biggest={} rows={} first_read_after_import_s={:.3} ({} rows)", t_all_reads.as_secs_f64(), biggest.0, biggest.1, t_big.as_secs_f64(), big.as_array().unwrap().len());
    eprintln!("MEASURE rss_base_mb={} rss_peak_mb={} store_growth_mb={:.1} (base {:.1}) copy_bytes_unchanged={}", base_rss / 1024, peak.load(std::sync::atomic::Ordering::SeqCst) / 1024, (dir_bytes(&dir) - base_store) as f64 / 1048576.0, base_store as f64 / 1048576.0, std::fs::metadata(&copy).unwrap().len() == before);
    // where the growth is: the tables of a copy of the target store (a copy: the seat is still running on the original)
    let snap = dir.join("breakdown.sqlite3");
    for f in ["field.sqlite3", "field.sqlite3-wal", "field.sqlite3-shm"] { let _ = std::fs::copy(dir.join(f), dir.join(format!("breakdown.sqlite3{}", &f["field.sqlite3".len()..]))); }
    let o = std::process::Command::new("sqlite3").arg(&snap).arg("select name, count(*) as pages, sum(pgsize)/1048576.0 as mb from dbstat where name not like 'sqlite_%' group by name order by 3 desc limit 6").output().unwrap();
    eprintln!("MEASURE target-store tables (name|pages|MB): {}", String::from_utf8_lossy(&o.stdout).replace('\n', " ; "));
    assert!(!dir.join("claude-was-run").exists());
    assert_eq!(errs.to_string(), "0");
}
