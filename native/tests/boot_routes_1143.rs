//! The page's boot-time routes on the full-set seat (#1143, swap step 3): every route the page calls when it boots, and the field views' empty
//! answers, on the REAL shipped pack set started the way `scripts/seat-full` starts it (a door secret, so the strict door: each call carries the owner
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

/// scripts/seat-full on a fresh data folder, a free port, the fake claude `/usr/bin/true`; -> the seat and its owner token
fn seat(tag: &str) -> Run {
    let dir = tmp(tag);
    let script = format!("{}/../scripts/seat-full", env!("CARGO_MANIFEST_DIR"));
    let mut child = Command::new("sh").arg(script).args(["--data-dir", dir.join("d").to_str().unwrap(), "--port", "0"])
        .env("CLIFFVM", BIN).env("CLAUDE_BIN", "/usr/bin/true").stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
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
    let run = seat("voice");
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
        // the page's `_loadModel` reads `model`; seat-full's default model is claude
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
        assert_eq!(bg["chat_auth"], json!("login"), "the chat runs on the claude CLI's own login: {bg}");
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
