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
