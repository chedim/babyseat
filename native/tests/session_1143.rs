//! The opencode session API on cliffvm (#1143, swap step 2): `/session*`, `/event`, `/global/event` (membrane/packs/door_session.c1f), over the
//! barest chat's machinery (a session is a chat, a message a ChatMessage and its turn). Every wait has a timeout and the child is always reaped.
//! There is NEVER a real `claude` here: the turn's binary is a fake script (a Boot fact names it). The answers are compared in SHAPE with the
//! recorded answers of the released Python seat (tests/golden/session/*.json, scripts/golden_session.py): `shape_misses`.
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
const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "chat.c1f", "chat_turn.c1f", "door_session.c1f"];

/// the seat's folder holds the fake claude; the binary is told its path as a fact
fn seat(tag: &str, script: &str) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let bin = dir.join("fake-claude");
    std::fs::write(&bin, script).unwrap();
    { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap(); }
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in shipped(PACKS) { std::fs::write(pd.join(n), t).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let fact = format!("chat_claude_bin={}", bin.display());
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap(), "--fact", &fact,
               "--fact", "seat_cwd=/srv/seat", "--fact", "seat_project=membrane", "--fact", "seat_version=1.0"])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port, dir)
}

/// one HTTP request on its own connection; -> (status, body text)
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    let status: u16 = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
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
fn golden(name: &str) -> J {
    let p = format!("{}/../tests/golden/session/{name}.json", env!("CARGO_MANIFEST_DIR"));
    serde_json::from_str(&std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{p}: {e}"))).unwrap()
}
/// the answer's body against the fixture's body, and the status against the fixture's
fn same_shape(name: &str, status: u16, body: &J) {
    let g = golden(name);
    assert_eq!(status as i64, g["status"].as_i64().unwrap(), "{name}: the status; body {body}");
    let m = shape_misses(&g["body"], body);
    assert!(m.is_empty(), "{name}: {m:?}\nanswer: {body}");
}

/// a fake claude that finishes at once: an answer "Done: <prompt>" with no tool
const QUICK: &str = r#"#!/usr/bin/env python3
import json, sys
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
prompt = sys.argv[-1]
out({"type": "system", "subtype": "init", "session_id": "q-1"})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 4}, "session_id": "q-1"})
"#;

#[test]
fn sessions_are_created_listed_read_renamed_and_deleted_in_the_shape_the_python_seat_answers() {
    let (_run, port, _dir) = seat("ses-crud", QUICK);
    let (st, s) = api(port, "POST", "/session", json!({"title": "T"}));
    same_shape("session_create", st, &s);
    let sid = s["id"].as_str().unwrap().to_string();
    assert!(sid.starts_with("ses_") && sid.len() == 28 && sid[4..].chars().all(|c| c.is_ascii_hexdigit()), "{sid}");
    assert_eq!(s["title"], "T");
    let (st, s2) = api(port, "POST", "/session", json!({}));
    assert_eq!(st, 200);
    assert!(s2["title"].as_str().unwrap().starts_with("New session"), "{s2}");
    let (st, l) = api(port, "GET", "/session", J::Null);
    same_shape("session_list", st, &l);
    let ids: Vec<&str> = l.as_array().unwrap().iter().map(|x| x["id"].as_str().unwrap()).collect();
    assert_eq!(ids, [sid.as_str(), s2["id"].as_str().unwrap()], "oldest first, like the Python seat");
    let (st, one) = api(port, "GET", &format!("/session/{sid}"), J::Null);
    same_shape("session_get", st, &one);
    assert_eq!(one["id"], json!(sid));
    let (st, miss) = api(port, "GET", "/session/ses_000000000000000000000000", J::Null);
    same_shape("session_missing", st, &miss);
    let (st, p) = api(port, "PATCH", &format!("/session/{sid}"), json!({"title": "Renamed"}));
    same_shape("session_patch", st, &p);
    assert_eq!(p["title"], "Renamed");
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).1["title"], "Renamed");
    let (st, status) = api(port, "GET", "/session/status", J::Null);
    same_shape("session_status_idle", st, &status);
    assert_eq!(status[&sid], json!({"type": "idle"}));
    assert_eq!(api(port, "POST", &format!("/session/{sid}/nonsense"), json!({})).0, 404);
    assert_eq!(api(port, "PUT", "/session", json!({})).0, 405);
    let (st, _) = api(port, "DELETE", &format!("/session/{sid}"), J::Null);
    assert_eq!(st, 200);
    assert_eq!(api(port, "GET", &format!("/session/{sid}"), J::Null).0, 404);
    let (_, l) = api(port, "GET", "/session", J::Null);
    assert_eq!(l.as_array().unwrap().len(), 1, "the deleted session is gone from the list");
}
