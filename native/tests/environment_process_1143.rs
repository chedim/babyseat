//! `Environment:host` in the REAL binary (#1143): a seat started with secrets in its environment shows the allow-listed variables and the NAMES of the secret ones, and the secret VALUES appear nowhere a
//! value could go: every route's reply, the store, its WAL, the journal, stdout, stderr, the process listing. A restart with another environment shows the new boot value, the journal holds both writes.
//! Private seat: a data folder of its own, an ephemeral port, no chat turn, no `claude`.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
const SECRETS: [&str; 3] = ["sk-secret-w115", "xyz-w115", "pw-w115"];
struct Seat { child: Child, port: u16, stdout: Vec<String>, stderr: std::path::PathBuf }
impl Seat { fn stop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }
impl Drop for Seat { fn drop(&mut self) { self.stop(); } }
fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn http(port: u16, req: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(req.as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    String::from_utf8_lossy(&out).to_string()
}
fn get(port: u16, path: &str) -> String { http(port, &format!("GET {path} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")) }
fn mcp(port: u16, name: &str, args: J) -> String {
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}).to_string();
    http(port, &format!("POST /mcp HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()))
}
fn text(reply: &str) -> String {
    let b = reply.split_once("\r\n\r\n").map(|x| x.1).unwrap_or("");
    let r: J = serde_json::from_str(b).unwrap_or_else(|e| panic!("{e}: {b}"));
    r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string()
}
fn environment(port: u16) -> J {
    let t = text(&mcp(port, "entity_read", json!({"spec": "Environment:host"})));   // `Environment:host (Environment) {json}`
    serde_json::from_str(&t[t.find('{').expect("a property list")..]).unwrap_or_else(|e| panic!("{e}: {t}"))
}

fn start(dir: &std::path::Path, env: &[(&str, &str)], facts: &[&str]) -> Seat {
    let r = repo();
    let packs: Vec<String> = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let stderr = dir.join("stderr.log");
    let mut cmd = Command::new(BIN);
    cmd.current_dir(dir).env_remove("ANTHROPIC_API_KEY").arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f"))
        .args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0", "--fact", &format!("seat_data_dir={}", dir.display()), "--fact", "warm_rows=0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
    for (k, v) in env { cmd.env(k, v); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(std::fs::File::create(&stderr).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Seat { child, port, stdout: vec![line], stderr }
}
fn dir(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-environment-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d.canonicalize().unwrap()
}
fn journal(dir: &std::path::Path) -> Vec<String> {
    let db = rusqlite::Connection::open_with_flags(dir.join("seat.sqlite"), rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let mut st = db.prepare("SELECT body FROM c_journal ORDER BY k").unwrap();
    st.query_map([], |r| r.get::<_, String>(0)).unwrap().filter_map(|r| r.ok()).collect()
}

#[test]
fn a_seat_with_secrets_in_its_environment_shows_the_allowed_names_and_the_secret_names_and_no_secret_value_anywhere() {
    let d = dir("secrets");
    let env = [("ANTHROPIC_API_KEY", "sk-secret-w115"), ("FOO_TOKEN", "xyz-w115"), ("MEMBRANE_DATA_DIR", "/x"), ("DB_PASSWORD", "pw-w115"), ("FOO_BAR", "harmless")];
    let mut s = start(&d, &env, &["env_allow=FOO_BAR"]);
    let e = environment(s.port);
    assert_eq!(e["MEMBRANE_DATA_DIR"], json!("/x"));
    assert_eq!(e["FOO_BAR"], json!("harmless"), "`env_allow=FOO_BAR` adds a harmless name");
    for k in ["ANTHROPIC_API_KEY", "FOO_TOKEN", "DB_PASSWORD"] { assert_eq!(e[k], json!("set"), "{k}: only that it is set"); }
    assert!(e.get("PATH").is_none() && e.get("EDITOR").is_none(), "not on the list: absent");
    // every route's reply
    let mut replies = vec![
        mcp(s.port, "entity_read", json!({"spec": "Environment:host"})), mcp(s.port, "entity_read", json!({"spec": "Boot:seat"})), mcp(s.port, "entity_query", json!({"q": "", "k": 100, "full": true})),
        mcp(s.port, "entity_query", json!({"q": "kind:Environment", "full": true})), mcp(s.port, "run_cliff", json!({"code": "Environment:host.env_present"})),
        get(s.port, "/"), get(s.port, "/global/health"), get(s.port, "/ui/fieldvm"), get(s.port, "/membrane/state"),
    ];
    // the process listing (argv and, on this platform, whatever `ps` shows of it)
    replies.push(String::from_utf8_lossy(&Command::new("ps").args(["-o", "command=", "-p", &s.child.id().to_string()]).output().unwrap().stdout).to_string());
    s.stdout.push(String::new());
    s.stop();
    // stdout, stderr, the store, its WAL and shared memory, the journal
    let mut files = vec![std::fs::read_to_string(&s.stderr).unwrap_or_default(), s.stdout.join("\n")];
    for f in ["seat.sqlite", "seat.sqlite-wal", "seat.sqlite-shm"] { files.push(String::from_utf8_lossy(&std::fs::read(d.join(f)).unwrap_or_default()).to_string()); }
    files.extend(journal(&d));
    for v in SECRETS {
        for (i, r) in replies.iter().enumerate() { assert!(!r.contains(v), "{v} appears in reply {i}: {r}"); }
        for (i, f) in files.iter().enumerate() { assert!(!f.contains(v), "{v} appears in file/log {i}"); }
    }
    assert!(files[2..].iter().any(|f| f.contains("MEMBRANE_DATA_DIR")), "the allowed name IS in the store (the test looks in the right place)");
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn a_secret_name_asked_for_by_env_allow_is_refused_with_the_reason_and_its_value_stays_out() {
    let d = dir("refused");
    let mut s = start(&d, &[("FOO_TOKEN", "xyz-w115")], &["env_allow=FOO_TOKEN"]);
    assert_eq!(environment(s.port)["FOO_TOKEN"], json!("set"));
    assert!(!mcp(s.port, "entity_read", json!({"spec": "Environment:host"})).contains("xyz-w115"));
    s.stop();
    let err = std::fs::read_to_string(&s.stderr).unwrap();
    assert!(err.contains("FOO_TOKEN") && err.contains("refused") && err.contains("SECRET"), "the refusal is said with the reason: {err}");
    assert!(!err.contains("xyz-w115"), "...without the value");
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn a_restart_with_another_environment_shows_the_new_boot_value_and_the_journal_holds_both_boots_writes() {
    let d = dir("restart");
    let mut a = start(&d, &[("MEMBRANE_DATA_DIR", "/first-w115")], &[]);
    assert_eq!(environment(a.port)["MEMBRANE_DATA_DIR"], json!("/first-w115"));
    a.stop();
    let mut b = start(&d, &[("MEMBRANE_DATA_DIR", "/second-w115")], &[]);
    assert_eq!(environment(b.port)["MEMBRANE_DATA_DIR"], json!("/second-w115"), "the new boot's value");
    b.stop();
    let j = journal(&d).into_iter().filter(|l| l.contains("Environment:host")).collect::<Vec<_>>().join("\n");
    assert!(j.contains("/first-w115") && j.contains("/second-w115") && j.contains("host:environment"), "both boots are in the journal as outside writes: {j}");
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn each_restart_of_the_barest_seat_appends_its_boot_lines_to_the_journal_and_the_growth_is_small() {
    // measured: lines and bytes the stored journal gains per restart of an idle seat (the numbers are printed with --nocapture)
    let d = dir("growth");
    let mut sizes = vec![];
    for _ in 0..3 {
        let mut s = start(&d, &[], &[]);
        let _ = environment(s.port);
        s.stop();
        let j = journal(&d);
        sizes.push((j.len(), j.iter().map(|l| l.len()).sum::<usize>()));
    }
    eprintln!("journal after each start (lines, bytes): {sizes:?}");
    assert!(sizes[0].0 < sizes[1].0 && sizes[1].0 < sizes[2].0, "every restart ADDS lines: {sizes:?}");
    assert!(sizes[2].1 - sizes[1].1 < 64 * 1024, "an idle restart adds well under 64 KiB: {sizes:?}");
    let _ = std::fs::remove_dir_all(d);
}
