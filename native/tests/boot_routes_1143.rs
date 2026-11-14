//! The page's boot-time routes on the release seat (#1143, swap step 3): every route the page calls when it boots, on the REAL shipped barest pack set started the way `scripts/seat` starts it (a door secret, so the strict door: each call carries the owner
//! token the seat writes). The answers are compared in SHAPE with the recorded answers of the released Python seat (tests/golden/boot/*.json,
//! scripts/golden_boot.py). Every wait has a timeout: a request that never answers FAILS the test instead of hanging it. NEVER a real `claude`.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, port: u16, token: String, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("boot-routes-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// scripts/seat on a fresh data folder, a free port, the fake claude `/usr/bin/true`; -> the seat and its owner token
fn seat(tag: &str) -> Run { let dir = tmp(tag); start_in(dir) }
/// the same on the folder `dir` (a second start on it is a restart: the store, the secret and the page's cookie are kept)
fn start_in(dir: std::path::PathBuf) -> Run { start_with(dir, &[]) }
/// the same with `--fact name=value` settings on the launch line
fn start_with(dir: std::path::PathBuf, facts: &[&str]) -> Run { launch(dir, facts, false) }
/// the routes of the packs the release seat's list (membrane/apps/barest.packs) does not carry (voice, self, decisions): the WHOLE pack folder, boot first, on the same strict door
/// (what the removed second launcher started; the test starts it itself, no script)
fn seat_full(tag: &str) -> Run { launch(tmp(tag), &[], true) }
fn launch(dir: std::path::PathBuf, facts: &[&str], full: bool) -> Run {
    let _ = std::fs::remove_file(dir.join("d/owner.token"));
    let root = format!("{}/..", env!("CARGO_MANIFEST_DIR"));
    let mut child = if full {
        let d = dir.join("d");
        std::fs::create_dir_all(&d).unwrap();
        Command::new(BIN).current_dir(&root).args(["run", "membrane/packs/boot.c1f", "membrane/packs", "--store", d.join("seat.sqlite").to_str().unwrap(), "--port", "0", "--ui-dir", "membrane/ui",
                "--fact", &format!("door_secret_file={}", d.join("door.secret").display()), "--fact", &format!("seat_data_dir={}", d.display()),
                "--fact", r#"minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude","loops_auth":"chat","cortex_auth":"chat","workers_auth":"chat","think":"auto"}"#,
                "--fact", "seat_backend=http://127.0.0.1:11434", "--fact", "router_on=false", "--fact", r#"cloud_models=[{"name":"claude","backend":"claude-agent"}]"#, "--fact", "chat_claude_bin=/usr/bin/true"])
            .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap()
    } else {
        Command::new("sh").arg(format!("{root}/scripts/seat")).args(["--data-dir", dir.join("d").to_str().unwrap(), "--port", "0"])
            .args(facts.iter().flat_map(|f| ["--fact", *f]))
            .env("CLIFFVM", BIN).env("CLAUDE_BIN", "/usr/bin/true").stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap()
    };
    let out = child.stdout.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let mut port = 0u16;
    while let Ok(l) = rx.recv_timeout(Duration::from_secs(60)) {
        if l.starts_with("cliffvm: door listening on") { port = l.rsplit(':').next().unwrap().trim().parse().unwrap(); break; }
    }
    assert!(port != 0, "the door came up");
    std::mem::forget(rx);
    let token = wait("the owner token", || std::fs::read_to_string(dir.join("d/owner.token")).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty()));
    Run { child, port, token, dir }
}

fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = std::time::Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// one request on its own connection, with the owner token; -> (status, body text). No answer in `secs` seconds is a FAILURE, never a wait.
fn http(run: &Run, method: &str, path: &str, body: &str, secs: u64) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(secs))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", run.token, body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").unwrap_or_else(|| panic!("{method} {path}: no answer in {secs} s"));
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
/// -> (status, the JSON body or null when it is empty)
fn api(run: &Run, method: &str, path: &str, body: J) -> (u16, J) {
    let (st, t) = http(run, method, path, &if body.is_null() { String::new() } else { body.to_string() }, 20);
    (st, if t.trim().is_empty() { J::Null } else { serde_json::from_str(&t).unwrap_or_else(|e| panic!("{path}: {e}: {t}")) })
}

// ---- the shape comparer (the session API's, tests/golden/session) -----------------------------------------------------------------------------
fn shape_misses(fix: &J, got: &J) -> Vec<String> { let mut out = vec![]; shape_at("$", fix, got, &mut out); out }
fn kind(v: &J) -> &'static str { match v { J::Null => "null", J::Bool(_) => "bool", J::Number(_) => "number", J::String(_) => "string", J::Array(_) => "array", J::Object(_) => "object" } }
fn shape_at(p: &str, fix: &J, got: &J, out: &mut Vec<String>) {
    match (fix, got) {
        (J::Null, _) => {}
        (J::Object(f), J::Object(g)) => for (k, fv) in f { match g.get(k) { Some(gv) => shape_at(&format!("{p}.{k}"), fv, gv, out), None => out.push(format!("{p}.{k}: missing")) } },
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
fn golden(name: &str) -> J {
    let p = format!("{}/../tests/golden/boot/{name}.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{p}: {e}"))).unwrap()
}
/// the answer's status and body against the fixture's
fn same_shape(name: &str, status: u16, body: &J) {
    let g = golden(name);
    assert_eq!(status as i64, g["status"].as_i64().unwrap(), "{name}: the status; body {body}");
    let m = shape_misses(&g["body"], body);
    assert!(m.is_empty(), "{name}: {m:?}\nanswer: {body}");
}

// ---- voice: the state write on a fresh store ----------------------------------------------------------------------------------------------------
#[test]
fn the_voice_state_write_and_read_answer_on_a_fresh_store_in_the_released_shape() {
    let run = seat_full("voice");
    // a store made by this seat has no Python tables: the read of a person with no voice entities used to wait for ever
    let (st, v) = api(&run, "POST", "/ui/serve/membrane/voice", json!({}));
    assert_eq!(st, 200, "{v}");
    assert_eq!(v["ok"], json!(true), "{v}");
    assert_eq!(v["in"]["log"], json!([]), "{v}");
    let (st, s) = api(&run, "POST", "/ui/serve/membrane/voice/state", json!({"in_on": true, "in_mode": "ptt", "out_on": false, "device": "phone"}));
    same_shape("voice_state", st, &s);
    assert_eq!(s["state"], json!({"in": {"on": true, "mode": "ptt", "device": "phone"}, "out": {"on": false, "device": "phone"}}), "{s}");
    // the write is what a read finds, and a second write keeps what the body leaves out
    let (_, v) = api(&run, "POST", "/ui/serve/membrane/voice", json!({}));
    assert_eq!((v["in"]["on"].clone(), v["in"]["mode"].clone(), v["in"]["device"].clone()), (json!(true), json!("ptt"), json!("phone")), "{v}");
    let (st, s) = api(&run, "POST", "/ui/serve/membrane/voice/state", json!({"in_on": false}));
    assert_eq!(st, 200, "{s}");
    assert_eq!(s["state"]["in"], json!({"on": false, "mode": "ptt", "device": "phone"}), "{s}");
}

// ---- the opencode stubs the chat reads on boot: /agent and /config -----------------------------------------------------------------------------------
#[test]
fn the_agent_list_carries_the_primary_agents_the_chat_picker_needs_in_the_released_shape() {
    let run = seat("agent");
    for path in ["/ui/serve/agent", "/agent"] {
        let (st, a) = api(&run, "GET", path, J::Null);
        same_shape("agent", st, &a);
        let rows = a.as_array().unwrap();
        let names: Vec<&str> = rows.iter().map(|r| r["name"].as_str().unwrap()).collect();
        assert_eq!(names, ["build", "plan", "general", "explore", "watch"], "{path}: the released rows, in order");
        let primary: Vec<&str> = rows.iter().filter(|r| r["mode"] == "primary").map(|r| r["name"].as_str().unwrap()).collect();
        assert_eq!(primary, ["build", "plan"], "{path}: with NO primary agent the attached TUI's Enter does nothing");
        assert_eq!(api(&run, "POST", path, json!({})).0, 405, "{path}: read only");
    }
}

#[test]
fn the_config_names_the_model_the_page_shows_and_the_provider_that_serves_it() {
    let run = seat("config");
    for path in ["/ui/serve/config", "/config"] {
        let (st, c) = api(&run, "GET", path, J::Null);
        assert_eq!(st, 200, "{c}");
        // the page's `_loadModel` reads `model`; the launcher's default model is claude
        assert_eq!(c["model"], json!("membrane/claude"), "{c}");
        assert_eq!(c["small_model"], json!("membrane/claude"), "{c}");
        assert_eq!(c["$schema"], json!("https://opencode.ai/config.json"), "{c}");
        assert_eq!(c["provider"]["membrane"]["npm"], json!("@ai-sdk/openai-compatible"), "{c}");
        assert_eq!(c["provider"]["membrane"]["options"]["baseURL"], json!(format!("http://127.0.0.1:{}/v1", run.port)), "{c}");
        assert_eq!(c["provider"]["membrane"]["models"], json!({"claude": {"name": "claude"}}), "{c}");
        assert_eq!(c["mcp"], json!({}), "{c}");
    }
}

// ---- the seat-wide options (the page's boot reads them, the gear window writes them) --------------------------------------------------------------
#[test]
fn the_options_read_has_the_released_shape_and_the_background_minds_the_page_needs() {
    let run = seat("options");
    for path in ["/ui/serve/membrane/options", "/membrane/options"] {
        let (st, o) = api(&run, "GET", path, J::Null);
        same_shape("options", st, &o);
        assert_eq!((o["self_cpu_share"].clone(), o["code_runs_keep"].clone(), o["current_tau_s"].clone()), (json!(0.5), json!(30), json!(180.0)), "{path}: {o}");
        // the `background` object: the page asks "Which model?" at boot only when `picked` is false, and the seat has claude picked
        let bg = &o["background"];
        let m = shape_misses(&golden("options_bg")["body"], bg);
        assert!(m.is_empty(), "{path}: background {m:?}: {bg}");
        assert_eq!((bg["loops"].clone(), bg["cortex"].clone(), bg["picked"].clone()), (json!("claude"), json!("claude"), json!(true)), "{bg}");
        assert_eq!(bg["claude"], json!(["claude"]), "{bg}");
        assert!(bg["models"].as_array().unwrap().contains(&json!("claude")), "{bg}");
        assert_eq!(bg["chat_auth"], json!("api_key"), "the chat runs on an API key unless the login is switched on (chat_auth=login): {bg}");
        let w = o["writable"].as_array().unwrap();
        for k in ["self_cpu_share", "code_runs_keep", "loops", "cortex", "loops_auth", "cortex_auth"] { assert!(w.contains(&json!(k)), "{k} is writable: {w:?}"); }
    }
}

#[test]
fn an_option_post_is_validated_first_then_kept_and_the_minds_follow_the_models_route() {
    let run = seat("options-post");
    let post = |b: J| api(&run, "POST", "/ui/serve/membrane/options", b);
    let (st, o) = post(json!({"self_cpu_share": 0.8, "code_runs_keep": 7}));
    assert_eq!(st, 200, "{o}");
    same_shape("options", st, &o);
    assert_eq!((o["self_cpu_share"].clone(), o["code_runs_keep"].clone()), (json!(0.8), json!(7)), "{o}");
    let (_, o) = api(&run, "GET", "/ui/serve/membrane/options", J::Null);
    assert_eq!((o["self_cpu_share"].clone(), o["code_runs_keep"].clone()), (json!(0.8), json!(7)), "kept: {o}");
    // a refusal is a 400 with the reason, and writes nothing (even the valid key beside it)
    for (b, why) in [
        (json!({"self_cpu_share": 2}), "self_cpu_share must be a number in (0, 1], got 2"),
        (json!({"code_runs_keep": 0}), "code_runs_keep must be a whole number >= 1, got 0"),
        (json!({"current_tau_s": 1}), "current_tau_s is read-only: changing the current's fade needs the operator's approval"),
        (json!({"energy": 1}), "energy is read-only: it is Blurp's to report (#345), not a setting"),
        (json!({}), "expected a JSON object of options, e.g. {\"self_cpu_share\": 0.5}"),
        (json!({"loops": "nope"}), "loops: membrane doesn't see 'nope' on its backends"),
    ] {
        let (st, e) = post(b.clone());
        assert_eq!(st, 400, "{b}: {e}");
        assert_eq!(e["error"], json!(why), "{b}");
    }
    // a value of the wrong kind is refused too: a text, a bool, a fraction where a whole number is wanted
    for b in [json!({"self_cpu_share": "x"}), json!({"self_cpu_share": true}), json!({"code_runs_keep": 2.5}), json!({"code_runs_keep": "3"}), json!({"code_runs_keep": false})] {
        let (st, e) = post(b.clone());
        assert_eq!(st, 400, "{b}: {e}");
        assert!(e["error"].as_str().unwrap().contains("must be a"), "{b}: {e}");
    }
    let (st, e) = post(json!({"bogus": 1, "self_cpu_share": 0.1}));
    assert_eq!(st, 400, "{e}");
    assert!(e["error"].as_str().unwrap().starts_with("unknown option"), "{e}");
    let (_, o) = api(&run, "GET", "/ui/serve/membrane/options", J::Null);
    assert_eq!(o["self_cpu_share"], json!(0.8), "a refused post wrote nothing: {o}");
    // the minds' keys go where /membrane/models puts them, and the answer is the options with the new values
    let (st, o) = post(json!({"cortex_auth": "login"}));
    assert_eq!(st, 200, "{o}");
    assert_eq!(o["background"]["cortex_auth"], json!("login"), "{o}");
    let (_, m) = api(&run, "GET", "/ui/serve/membrane/models", J::Null);
    assert_eq!(m["cortex_auth"], json!("login"), "the same state the models route reads: {m}");
    assert_eq!(api(&run, "DELETE", "/ui/serve/membrane/options", J::Null).0, 405);
}

// ---- the out-of-tokens popup: nothing here pauses a model, the released answer of a seat with nothing paused ------------------------------------------
#[test]
fn the_quota_read_and_resume_answer_a_seat_with_nothing_paused_in_the_released_shape() {
    let run = seat("quota");
    let (st, q) = api(&run, "GET", "/ui/serve/membrane/quota", J::Null);
    same_shape("quota", st, &q);
    assert_eq!((q["paused"].clone(), q["probe_s"].clone()), (json!([]), json!(600)), "{q}");
    assert!(q["now"].as_f64().unwrap() > 1.7e9, "{q}");
    let (st, e) = api(&run, "POST", "/ui/serve/membrane/quota/resume", json!({}));
    assert_eq!((st, e["error"].clone()), (400, json!("model required")), "{e}");
    let (st, r) = api(&run, "POST", "/ui/serve/membrane/quota/resume", json!({"model": "claude"}));
    same_shape("quota", st, &r);
    assert_eq!((r["resumed"].clone(), r["paused"].clone()), (json!(false), json!([])), "nothing was paused: {r}");
    assert_eq!(api(&run, "DELETE", "/ui/serve/membrane/quota", J::Null).0, 405);
}

// ---- the browser VM's start: GET /ui/fieldvm -------------------------------------------------------------------------------------------------------------
fn bare(run: &Run, method: &str, path: &str) -> u16 {
    let mut s = TcpStream::connect(("127.0.0.1", run.port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    String::from_utf8_lossy(&out).split_whitespace().nth(1).unwrap().parse().unwrap()
}

#[test]
fn the_browser_vm_snapshot_carries_the_view_packs_and_needs_the_owner() {
    let run = seat("fieldvm");
    assert_eq!(bare(&run, "GET", "/ui/fieldvm"), 401, "the snapshot is the seat's field: not public");
    let (st, s) = api(&run, "GET", "/ui/fieldvm", J::Null);
    assert_eq!(st, 200, "{s}");
    // the packs that import `dom` (they run in the page; the page has `dom`), by name, with their text; none of the seat's own
    let packs = s["packs"].as_object().unwrap_or_else(|| panic!("{s}"));
    let mut names: Vec<String> = packs.keys().cloned().collect();
    names.sort();
    let folder = format!("{}/../membrane/apps/barest.c1f", env!("CARGO_MANIFEST_DIR"));
    let mut want: Vec<String> = std::fs::read_dir(&folder).unwrap().filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.extension().map(|x| x == "c1f").unwrap_or(false))
        .filter(|p| std::fs::read_to_string(p).unwrap().lines().any(|l| l.starts_with("IMPORT dom "))).map(|p| p.file_stem().unwrap().to_string_lossy().to_string()).collect();
    want.sort();
    assert!(!want.is_empty() && names == want, "only the app folder's packs that import dom: {names:?} vs {want:?}");
    let shell = packs["shell_view"].as_str().unwrap();
    assert_eq!(shell, std::fs::read_to_string(format!("{folder}/shell_view.c1f")).unwrap(), "the file's text, whole");
    // no entity of a view class is mirrored yet; the one entity is `Boot:seat`, the chat view's settings (null: the pack's own default stands)
    assert_eq!(s["entities"], json!([{"eid": "Boot:seat", "cls": "Boot", "props": {"chat_page_size": null, "chat_poll_size": null, "chat_list_poll_ms": null, "chat_active_poll_ms": null, "chat_stick_px": null, "chat_tool_chars": null, "field_window": 150, "field_poll_ms": 2000, "chat_poll_timeout_s": null, "shell_status_ms": null, "shell_panel_chars": null}}]), "{s}");
    assert!(s.get("views").is_none(), "no per-view switch any more: {s}");
    // the same through the page's prefix; read only
    let (st, s2) = api(&run, "GET", "/ui/serve/ui/fieldvm", J::Null);
    assert_eq!((st, s2["packs"].as_object().map(|m| m.len())), (200, Some(want.len())), "{s2}");
    assert_eq!(api(&run, "POST", "/ui/fieldvm", json!({})).0, 405);
}

#[test]
fn the_browser_vm_snapshot_reports_the_chat_settings_from_the_facts() {
    let run = start_with(tmp("fieldvm-facts"), &["chat_page_size=5", "chat_active_poll_ms=100", "field_window=40", "field_poll_ms=800"]);
    let (st, s) = api(&run, "GET", "/ui/fieldvm", J::Null);
    assert_eq!(st, 200, "{s}");
    let p = &s["entities"][0]["props"];
    assert_eq!((p["chat_page_size"].clone(), p["chat_active_poll_ms"].clone(), p["chat_poll_size"].clone()), (json!(5), json!(100), J::Null), "a fact given rides as it is; one not given is null");
    assert_eq!((p["field_window"].clone(), p["field_poll_ms"].clone()), (json!(40), json!(800)), "the field view's settings ride the same way");
}

// ---- the session's side reads the chat view asks beside the messages (tolerated when absent; answered now) -----------------------------------------------
#[test]
fn the_sessions_voice_and_predict_reads_are_the_empty_lists_of_a_seat_with_neither() {
    let run = seat("session-empty");
    let (st, s) = api(&run, "POST", "/ui/serve/session", json!({"title": "T"}));
    assert_eq!(st, 200, "{s}");
    let sid = s["id"].as_str().unwrap().to_string();
    // the released handler answers these for any session id: membrane's own voice lines (none: no voice state here), its guesses of the seat's next moves
    // (phantoms: none: no predictor here) and the TUI's stubs
    for what in ["voice", "predict", "todo", "children", "diff"] {
        for id in [sid.as_str(), "ses_unknown"] {
            let (st, l) = api(&run, "GET", &format!("/ui/serve/session/{id}/{what}"), J::Null);
            assert_eq!((st, l), (200, json!([])), "{what} of {id}");
        }
    }
    // only reads: the vote / rewrite teaching of the released POST /voice is not here
    assert_eq!(api(&run, "POST", &format!("/ui/serve/session/{sid}/predict"), json!({})).0, 405);
    assert_eq!(api(&run, "GET", &format!("/ui/serve/session/{sid}/nonsense"), J::Null).0, 404);
}

// ---- the work graph's drawer: history and item chat on a store with no Python tables -------------------------------------------------------------------------
#[test]
fn the_history_and_item_chat_reads_answer_on_a_fresh_store_instead_of_waiting_for_python_tables() {
    let run = seat_full("history");
    // the store this seat made has no `c_entity_deltas` / `c_relations` (the Python seat's tables): the reads used to wait for ever
    let (st, h) = api(&run, "GET", "/ui/serve/membrane/entity-history?spec=Goal%3Anone", J::Null);
    assert_eq!((st, h), (200, json!({"ok": false, "error": "no entity matches 'Goal:none'"})));
    let (st, c) = api(&run, "GET", "/ui/serve/membrane/item-chat?spec=Goal%3Anone", J::Null);
    assert_eq!((st, c), (200, json!({"ok": true, "eid": "Goal:none", "messages": []})));
}

#[test]
fn the_snapshot_is_the_same_after_a_restart_on_the_same_store() {
    let first = seat("fieldvm-restart");
    let (_, a) = api(&first, "GET", "/ui/fieldvm", J::Null);
    let dir = first.dir.clone();
    let mut first = first;
    let _ = first.child.kill();
    let _ = first.child.wait();
    std::mem::forget(first);            // the folder stays: the second seat owns it now
    let second = start_in(dir);
    let (st, b) = api(&second, "GET", "/ui/fieldvm", J::Null);
    assert_eq!((st, &b), (200, &a), "the same view packs after a restart");
}

// ---- w46: the degraded reads the page's windows make (stats, minds): the released answer of a seat with nothing counted -------------------------------------
#[test]
fn the_stats_window_reads_the_released_shape_of_a_seat_that_counted_nothing() {
    let run = seat("stats");
    let (st, s) = api(&run, "GET", "/ui/serve/membrane/stats", J::Null);
    same_shape("stats", st, &s);
    assert_eq!((s["today"]["total"].clone(), s["today"]["minds"].clone(), s["hour"]["models"].clone()), (json!({"calls": 0, "input": 0, "output": 0}), json!({}), json!({})), "{s}");
    assert_eq!((s["claude"]["answers"].clone(), s["paused"].clone()), (json!(0), json!([])), "{s}");
    assert!(s["now"].as_f64().unwrap() > 1.7e9 && s["midnight"].as_f64().unwrap() <= s["now"].as_f64().unwrap(), "{s}");
    assert_eq!(api(&run, "POST", "/ui/serve/membrane/stats", json!({})).0, 405);
}

#[test]
fn the_minds_read_is_the_released_empty_orb_lists() {
    let run = seat("minds");
    let (st, m) = api(&run, "GET", "/ui/serve/membrane/minds", J::Null);
    same_shape("minds", st, &m);
    assert_eq!(m, json!({"loops": {}, "thinker": [], "dreamer": []}));
}

// ---- w46: the 👶 panel's feed and the item card's say ------------------------------------------------------------------------------------------------------
#[test]
fn the_self_record_feed_is_the_empty_list_of_a_seat_with_no_record() {
    let run = seat_full("selfrecent");
    let (st, r) = api(&run, "GET", "/ui/serve/membrane/self/recent?n=15", J::Null);
    assert_eq!((st, r), (200, json!({"lines": []})), "the released answer with no self record (server.py:4577)");
}

#[test]
fn the_item_card_say_is_a_stated_not_built_and_writes_nothing() {
    let run = seat_full("itemsay");
    let (st, r) = api(&run, "POST", "/ui/serve/membrane/item-chat/say", json!({"spec": "Goal:none", "text": "hi"}));
    assert_eq!(st, 501, "{r}");
    assert!(r["error"].as_str().unwrap().starts_with("not ported yet"), "{r}");
    let (_, c) = api(&run, "GET", "/ui/serve/membrane/item-chat?spec=Goal%3Anone", J::Null);
    assert_eq!(c["messages"], json!([]), "nothing was written: {c}");
    assert_eq!(api(&run, "GET", "/ui/serve/membrane/item-chat/say", J::Null).0, 405);
}

// ---- w46: the constants carried over from the release are launch settings (`--fact`), with the released values as their defaults ---------------------------------
#[test]
fn the_released_constants_are_launch_facts_and_keep_the_released_values_without_them() {
    let run = seat("facts-default");
    let (_, q) = api(&run, "GET", "/ui/serve/membrane/quota", J::Null);
    let (_, o) = api(&run, "GET", "/ui/serve/membrane/options", J::Null);
    assert_eq!(q["probe_s"], json!(600), "{q}");
    assert_eq!((o["defaults"].clone(), o["current_tau_s"].clone(), o["background"]["chat_auth"].clone()), (json!({"self_cpu_share": 0.5, "code_runs_keep": 30}), json!(180.0), json!("api_key")), "{o}");
    let dir = tmp("facts-set");
    let run = start_with(dir, &["quota_probe_s=900", "field_tau_s=90.0", "self_cpu_share_default=0.25", "code_runs_keep_default=10", "chat_auth=login"]);
    let (_, q) = api(&run, "GET", "/ui/serve/membrane/quota", J::Null);
    let (_, o) = api(&run, "GET", "/ui/serve/membrane/options", J::Null);
    assert_eq!(q["probe_s"], json!(900), "{q}");
    assert_eq!((o["defaults"].clone(), o["current_tau_s"].clone(), o["background"]["chat_auth"].clone()), (json!({"self_cpu_share": 0.25, "code_runs_keep": 10}), json!(90.0), json!("login")), "{o}");
    assert_eq!((o["self_cpu_share"].clone(), o["code_runs_keep"].clone()), (json!(0.25), json!(10)), "an unset option reads its default: {o}");
}

#[test]
fn an_unknown_chat_auth_is_shown_as_the_fact_says_with_no_new_field_and_the_turns_refusal_is_where_it_shows() {
    let (_, d) = api(&seat("auth-default"), "GET", "/ui/serve/membrane/options", J::Null);
    let run = start_with(tmp("auth-unknown"), &["chat_auth=oauth"]);
    let (_, o) = api(&run, "GET", "/ui/serve/membrane/options", J::Null);
    assert_eq!(o["background"]["chat_auth"], json!("oauth"), "the fact as it is, not dressed up as a usable mode: {o}");
    let keys = |v: &J| -> Vec<String> { let mut k: Vec<String> = v["background"].as_object().unwrap().keys().cloned().collect(); k.sort(); k };
    assert_eq!(keys(&o), keys(&d), "the released options shape gained no field");
}
