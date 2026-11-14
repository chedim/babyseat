//! The standalone binary as a process (a bare process that hangs is killed: every wait has a timeout, the child is always reaped).
#![cfg(feature = "bin")]
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

/// start the binary on `packs` (name, text) with a ui dir holding `ui` files; -> the run and the door's port
fn start(tag: &str, packs: &[(&str, &str)], ui: &[(&str, &str)], extra: &[&str]) -> (Run, u16) {
    let dir = tmp(tag);
    let (pd, ud) = (dir.join("packs"), dir.join("ui"));
    std::fs::create_dir_all(&pd).unwrap();
    std::fs::create_dir_all(&ud).unwrap();
    for (n, t) in packs { std::fs::write(pd.join(format!("{n}.c1f")), t).unwrap(); }
    for (n, t) in ui { std::fs::write(ud.join(n), t).unwrap(); }
    let mut child = Command::new(BIN)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", ud.to_str().unwrap()])
        .args(extra).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port)
}

fn get(port: u16, raw: &str) -> (String, Vec<u8>) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(15))).unwrap();
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    (String::from_utf8_lossy(&out[..i]).to_string(), out[i + 4..].to_vec())
}

/// SIGTERM, then wait for the exit (a hard timeout, then kill)
fn terminate(run: &mut Run) -> Option<std::process::ExitStatus> {
    unsafe { libc::kill(run.child.id() as i32, libc::SIGTERM); }
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(15) {
        if let Some(s) = run.child.try_wait().unwrap() { return Some(s); }
        std::thread::sleep(Duration::from_millis(50));
    }
    let _ = run.child.kill();
    None
}

const BOOT: &str = include_str!("../../membrane/packs/boot.c1f");
const DOOR: &str = include_str!("../../membrane/packs/door.c1f");

#[test]
fn version_prints_and_exits() {
    let o = Command::new(BIN).arg("--version").output().unwrap();
    assert!(o.status.success());
    assert!(String::from_utf8_lossy(&o.stdout).starts_with("cliffvm "));
}

#[test]
fn a_bad_command_is_refused_with_usage() {
    let o = Command::new(BIN).args(["run"]).stderr(Stdio::piped()).stdout(Stdio::null()).output().unwrap();
    assert_eq!(o.status.code(), Some(64));
    assert!(String::from_utf8_lossy(&o.stderr).contains("usage"));
}

#[test]
fn the_process_serves_a_static_file_through_the_door_and_stops_on_sigterm() {
    let (mut run, port) = start("static", &[("boot", BOOT), ("door", DOOR)], &[("index.html", "<h1>hi</h1>"), ("home.js", "var a=1;")], &[]);
    // the barest seat: no token, no secret, and the door answers
    let (head, body) = get(port, "GET /ui/www/home.js HTTP/1.1\r\nHost: t\r\n\r\n");
    assert!(head.starts_with("HTTP/1.1 200 OK"), "{head}");
    assert!(head.to_lowercase().contains("content-type: application/javascript"), "{head}");
    assert_eq!(body, b"var a=1;");
    let (head, body) = get(port, "GET / HTTP/1.1\r\nHost: t\r\n\r\n");
    assert!(head.starts_with("HTTP/1.1 200 OK"), "{head}");
    assert_eq!(body, b"<h1>hi</h1>");
    let (head, _) = get(port, "GET /ui/www/nothing.js HTTP/1.1\r\nHost: t\r\n\r\n");
    assert!(head.starts_with("HTTP/1.1 404"), "{head}");
    let st = terminate(&mut run).expect("it exits on SIGTERM");
    assert!(st.success(), "{st:?}");
}

#[test]
fn the_door_binds_loopback_when_told_so() {
    let (_run, port) = start("bind", &[("boot", BOOT), ("door", DOOR)], &[("index.html", "x")], &["--bind", "127.0.0.1"]);
    assert!(get(port, "GET / HTTP/1.1\r\nHost: t\r\n\r\n").0.starts_with("HTTP/1.1 200"));
}

/// the shipped packs the decisions route and the not-ported routes need (tests/specs/door_decisions.json, door_self.json), and the boot plan
fn shipped(names: &[&str]) -> Vec<(String, String)> {
    let d = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    names.iter().map(|n| (n.to_string(), std::fs::read_to_string(format!("{d}/{n}")).unwrap())).collect()
}

#[test]
fn the_binary_boots_through_the_plan_and_answers_the_shipped_routes() {
    let files = shipped(&["boot.c1f", "boot.json", "fs.c1f", "banner.c1f", "intents.c1f", "decisions.c1f", "door.c1f", "door_decisions.c1f", "door_self.c1f"]);
    let dir = tmp("plan");
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    for (n, t) in &files { std::fs::write(pd.join(n), t).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let mut run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up through Boot.started");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    // a decisions route: the board of an empty field is a JSON answer
    let (head, body) = get(port, "GET /membrane/decisions HTTP/1.1\r\nHost: t\r\n\r\n");
    assert!(head.starts_with("HTTP/1.1 200 OK"), "{head}");
    assert!(serde_json::from_slice::<serde_json::Value>(&body).is_ok(), "{}", String::from_utf8_lossy(&body));
    // a not-ported route says so (door_self.c1f)
    let (head, body) = get(port, "GET /membrane/self/sheet HTTP/1.1\r\nHost: t\r\n\r\n");
    assert!(head.starts_with("HTTP/1.1 501"), "{head}");
    assert!(String::from_utf8_lossy(&body).contains("not ported yet"), "{}", String::from_utf8_lossy(&body));
    // the write route is a person's; the barest seat has no persons: the door answers it, and a GET of it is a 405
    let (head, _) = get(port, "GET /membrane/decision/answer HTTP/1.1\r\nHost: t\r\n\r\n");
    assert!(head.starts_with("HTTP/1.1 405"), "{head}");
    assert!(terminate(&mut run).expect("exits on SIGTERM").success());
}

#[test]
fn apps_are_layers_the_upper_apps_file_wins_and_the_first_argument_is_the_root() {
    let dir = tmp("layers");
    let (root, a, b) = (dir.join("root"), dir.join("a.c1f"), dir.join("b.c1f"));
    for (d, f, t) in [(&root, "boot.c1f", BOOT), (&root, "door.c1f", DOOR), (&a, "ui/x.js", "from a"), (&a, "ui/only_a.js", "a"), (&b, "ui/x.js", "from b")] {
        let p = d.join(f);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, t).unwrap();
    }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", root.join("boot.c1f").to_str().unwrap(), root.to_str().unwrap(), a.to_str().unwrap(), b.to_str().unwrap(),
               "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0"])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    for (path, want) in [("/ui/www/x.js", "from b"), ("/ui/www/only_a.js", "a")] {
        let (head, body) = get(port, &format!("GET {path} HTTP/1.1\r\nHost: t\r\n\r\n"));
        assert!(head.starts_with("HTTP/1.1 200"), "{path}: {head}");
        assert_eq!(String::from_utf8_lossy(&body), want, "{path}");
    }
    assert!(get(port, "GET /ui/www/none.js HTTP/1.1\r\nHost: t\r\n\r\n").0.starts_with("HTTP/1.1 404"));
    drop(run);
}
