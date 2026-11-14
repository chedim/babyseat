//! The release plumbing (#1143, operator 2026-11-14): ONE launcher (`scripts/seat`, the barest app) with the strict door, the version 0.7.60, `run --help`, the real
//! auth mode of `/ui/conf`, and the dead code gone (the `ui_views` fact, the degraded map shapes). Every seat here is private: a free port, its own data folder, a fake claude,
//! no chat turn. Every wait has a timeout and the child is always reaped.
#![cfg(feature = "bin")]
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
fn repo() -> std::path::PathBuf { std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..") }

struct Run { child: Child, _lines: Receiver<String>, port: u16, data: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.data); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("plumb-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn watch(mut child: Child, data: std::path::PathBuf) -> Run {
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let mut port = 0u16;
    while let Ok(l) = rx.recv_timeout(Duration::from_secs(60)) {
        if l.starts_with("cliffvm: door listening on") { port = l.rsplit(':').next().unwrap().trim().parse().unwrap(); break; }
    }
    assert!(port != 0, "the door came up");
    Run { child, _lines: rx, port, data }
}
/// the one launcher, scripts/seat, on its own data folder
fn launcher(tag: &str) -> Run {
    let data = tmp(tag);
    let child = Command::new("sh").arg(repo().join("scripts/seat")).args(["--data-dir", data.to_str().unwrap(), "--port", "0"])
        .env("CLIFFVM", BIN).env("CLAUDE_BIN", "/usr/bin/true").stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    watch(child, data)
}
/// the barest packs with NO door secret (the seat the strict door is opt-in on), straight on the binary
fn open_seat(tag: &str) -> Run {
    let data = tmp(tag);
    let mut args: Vec<String> = vec!["run".into()];
    for l in std::fs::read_to_string(repo().join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        args.push(repo().join(format!("membrane/packs/{l}.c1f")).display().to_string());
    }
    args.extend(["membrane/apps/barest.c1f".into(), "--store".into(), data.join("s.sqlite").display().to_string(), "--port".into(), "0".into()].map(String::from));
    let child = Command::new(BIN).current_dir(repo()).args(&args).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    watch(child, data)
}
fn http(port: u16, method: &str, path: &str, extra: &str, body: &str) -> (u16, String, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let head = String::from_utf8_lossy(&out[..i]).to_string();
    (head.split_whitespace().nth(1).unwrap().parse().unwrap(), head, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn bearer(t: &str) -> String { format!("Authorization: Bearer {t}\r\n") }
fn owner_token(r: &Run) -> String {
    let t = std::time::Instant::now();
    loop {
        if let Ok(s) = std::fs::read_to_string(r.data.join("owner.token")) { if !s.trim().is_empty() { return s.trim().to_string(); } }
        assert!(t.elapsed() < Duration::from_secs(30), "the owner token file");
        std::thread::sleep(Duration::from_millis(100));
    }
}
fn json(b: &str) -> serde_json::Value { serde_json::from_str(b).unwrap_or_else(|e| panic!("json {e}: {b}")) }

// ---- (1) the version -------------------------------------------------------------------------------------------------------------------

#[test]
fn the_binary_the_health_route_and_the_conf_report_the_version_0_7_60() {
    let o = Command::new(BIN).arg("--version").output().unwrap();
    assert_eq!(String::from_utf8_lossy(&o.stdout).trim(), "cliffvm 0.7.60");
    let run = launcher("version");
    let tok = owner_token(&run);
    let (st, _, b) = http(run.port, "GET", "/global/health", &bearer(&tok), "");
    assert_eq!((st, json(&b)["version"].as_str().map(String::from)), (200, Some("0.7.60".to_string())), "{b}");
    let (st, _, b) = http(run.port, "GET", "/ui/conf", &bearer(&tok), "");
    assert_eq!((st, json(&b)["build"].as_str().map(String::from)), (200, Some("cliffvm-0.7.60".to_string())), "{b}");
}

// ---- (2) the one launcher --------------------------------------------------------------------------------------------------------------

#[test]
fn scripts_seat_full_is_gone_and_no_script_names_it() {
    assert!(!repo().join("scripts/seat-full").exists(), "the second launcher is removed");
    for f in ["scripts/seat", "native/tests/seat_process.rs", "native/tests/boot_routes_1143.rs"] {
        let t = std::fs::read_to_string(repo().join(f)).unwrap();
        assert!(!t.contains("scripts/seat-full"), "{f} still names scripts/seat-full");
    }
}

#[test]
fn the_launcher_makes_a_door_secret_and_an_owner_token_and_the_strict_door_serves_the_page_and_the_house_routes() {
    let run = launcher("strict");
    let secret = run.data.join("door.secret");
    let tok = owner_token(&run);
    assert!(std::fs::read_to_string(&secret).unwrap().trim().len() >= 64, "a long secret");
    // the page shell loads with no token; a protected route answers 401
    let (st, head, body) = http(run.port, "GET", "/", "", "");
    assert_eq!(st, 200, "{head}");
    assert!(body.to_lowercase().contains("<html") || body.to_lowercase().contains("<!doctype"), "the page");
    assert_eq!(http(run.port, "GET", "/membrane/ha/calls", "", "").0, 401, "no token, no house route");
    // `cliffvm token mint` works on the launcher's secret, and so does the owner's own token
    let minted = Command::new(BIN).args(["token", "mint", "--sub", "owner", "--ttl", "15m", "--secret-file", secret.to_str().unwrap()]).output().unwrap();
    assert!(minted.status.success(), "{}", String::from_utf8_lossy(&minted.stderr));
    let minted = String::from_utf8_lossy(&minted.stdout).trim().to_string();
    for t in [tok.clone(), minted] {
        let (st, _, b) = http(run.port, "GET", "/membrane/ha/calls", &bearer(&t), "");
        assert_eq!(st, 200, "{b}");
        assert!(json(&b)["calls"].is_array(), "{b}");
    }
    // the models and options routes (the seat's use of the house, the page's model picker) exist on the release seat
    let (st, _, b) = http(run.port, "GET", "/membrane/models", &bearer(&tok), "");
    assert_eq!(st, 200, "{b}");
    assert!(b.contains("claude"), "claude is the model: {b}");
    assert_eq!(http(run.port, "GET", "/membrane/options", &bearer(&tok), "").0, 200);
    assert_eq!(http(run.port, "GET", "/v1/models", &bearer(&tok), "").0, 200);
    // the metrics Home Assistant's sensors read
    assert_eq!(http(run.port, "GET", "/membrane/ha/metrics", &bearer(&tok), "").0, 200);
    // a localhost peer is not the owner: the page's owner cookie comes from loopback, the bare route stays closed
    assert!(head.to_lowercase().contains("set-cookie"), "the page is given its cookie: {head}");
}

#[test]
fn the_launcher_binds_loopback_by_default() {
    let t = std::fs::read_to_string(repo().join("scripts/seat")).unwrap();
    assert!(!t.contains("0.0.0.0"), "no all-interfaces default");
    assert!(t.contains("127.0.0.1"));
}

// ---- (4) small fixes -------------------------------------------------------------------------------------------------------------------

#[test]
fn run_help_prints_the_usage_and_exits_zero() {
    for a in [&["run", "--help"][..], &["run", "-h"][..], &["token", "mint", "--help"][..]] {
        let o = Command::new(BIN).args(a).output().unwrap();
        assert_eq!(o.status.code(), Some(0), "{a:?}: {}", String::from_utf8_lossy(&o.stderr));
        assert!(String::from_utf8_lossy(&o.stdout).contains("usage: cliffvm"), "{a:?}");
    }
    assert_eq!(Command::new(BIN).args(["run", "--nope"]).output().unwrap().status.code(), Some(64), "an unknown option is still refused");
}

#[test]
fn conf_reports_the_auth_mode_the_door_really_has() {
    let strict = launcher("auth-jwt");
    let tok = owner_token(&strict);
    assert_eq!(json(&http(strict.port, "GET", "/ui/conf", &bearer(&tok), "").2)["auth"], "jwt");
    let open = open_seat("auth-none");
    let b = json(&http(open.port, "GET", "/ui/conf", "", "").2);
    assert_eq!(b["auth"], "none", "a door with no secret is not a jwt door: {b}");
}

// ---- (3) dead code ---------------------------------------------------------------------------------------------------------------------

#[test]
fn the_ui_views_fact_and_the_degraded_map_routes_are_gone() {
    let facts = std::fs::read_to_string(repo().join("membrane/packs/facts.json")).unwrap();
    assert!(!facts.contains("ui_views"), "the fact is not in the registry");
    for f in ["door_fieldvm.c1f", "door_map.c1f"] {
        let p = repo().join("membrane/packs").join(f);
        if let Ok(t) = std::fs::read_to_string(p) { assert!(!t.contains("ui_views"), "{f}"); }
    }
    assert!(!repo().join("membrane/packs/door_map.c1f").exists(), "the degraded map pack is deleted");
    let run = launcher("nomap");
    let tok = owner_token(&run);
    for p in ["/membrane/map/field", "/membrane/map/field3d", "/membrane/map/blurp", "/membrane/field/entity?eid=x"] {
        assert_eq!(http(run.port, "GET", p, &bearer(&tok), "").0, 404, "{p}");
    }
    let (st, _, b) = http(run.port, "GET", "/ui/fieldvm", &bearer(&tok), "");
    assert_eq!(st, 200, "{b}");
    assert!(json(&b).get("views").is_none(), "the snapshot no longer reports views: {b}");
}
