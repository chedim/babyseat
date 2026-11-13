//! The unload policy in the real binary (#1143): the shipped packs and the barest app on a store of synthetic entities, the memory budget and the fractions as boot facts, the
//! resident size read from the test's own file (`MEMBRANE_TEST_RSS`: the path of a file holding the size in bytes, consulted only by the sensor). No real `claude`; ephemeral port.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
const MB: u64 = 1 << 20;
struct Run { child: Child, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }
fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn http(port: u16, body: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("POST /mcp HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    String::from_utf8_lossy(&out).split_once("\r\n\r\n").map(|x| x.1.to_string()).unwrap_or_default()
}
fn clifflet(port: u16, code: &str) -> String {
    let r: J = serde_json::from_str(&http(port, &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_cliff", "arguments": {"code": code}}}).to_string())).unwrap();
    r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string()
}
fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop { if let Some(v) = f() { return v; } assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}"); std::thread::sleep(Duration::from_millis(200)); }
}

#[test]
fn a_seat_over_its_memory_budget_unloads_its_coldest_and_a_reference_loads_one_back() {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let dir = std::env::temp_dir().join(format!("cliffvm-unload-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    {
        let db = rusqlite::Connection::open(dir.join("seat.sqlite")).unwrap();
        db.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_journal (k TEXT PRIMARY KEY, body TEXT NOT NULL); BEGIN;").unwrap();
        for i in 0..300 { db.execute("INSERT INTO c_field (k, body) VALUES (?1, ?2)", rusqlite::params![format!("Note:{i}"), json!({"cls": "Note", "props": {"n": i}, "writers": {"n": "person"}}).to_string()]).unwrap(); }
        db.execute_batch("COMMIT").unwrap();
    }
    let rss_file = dir.join("rss");
    std::fs::write(&rss_file, (40 * MB).to_string()).unwrap();
    let r = repo();
    let packs: Vec<String> = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(&dir).env("MEMBRANE_TEST_RSS", &rss_file).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f"))
        .args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in ["warm_rows=300", "memory_budget_mb=100", "unload_soft=0.5", "unload_hard=0.9", "unload_batch=50"] { cmd.arg("--fact").arg(f); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, dir: dir.clone() };
    let line = rx.recv_timeout(Duration::from_secs(120)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    let loaded = |port| clifflet(port, "len(instances('Note'))").trim().parse::<usize>().unwrap();
    wait("the warm boot loads every Note", || (loaded(port) == 300).then_some(()));
    wait("the sensor reports", || (clifflet(port, "Mem:host.level") == "ok").then_some(()));
    assert_eq!(clifflet(port, "Mem:host.rss"), (40 * MB).to_string(), "the sensor reads the test's file");
    std::thread::sleep(Duration::from_millis(2500));
    assert_eq!(loaded(port), 300);
    // pressure: 95 MB of 100 (hard from 90): one batch of 50 a tick until it is under the soft line, which the reading never says: it unloads on
    std::fs::write(&rss_file, (95 * MB).to_string()).unwrap();
    wait("some Notes are unloaded", || (loaded(port) < 300).then_some(()));
    // the reading drops under the soft line: the passes stop
    std::fs::write(&rss_file, (30 * MB).to_string()).unwrap();
    wait("the level is ok again", || (clifflet(port, "Mem:host.level") == "ok").then_some(()));
    std::thread::sleep(Duration::from_millis(1500));
    let n = loaded(port);
    std::thread::sleep(Duration::from_millis(2500));
    assert_eq!(loaded(port), n, "no pass runs while the level is ok");
    assert!(n < 300, "{n} Notes loaded");
    // a reference loads one back, with its value
    let cold: usize = (0..300).find(|i| clifflet(port, &format!("len([x for x in instances('Note') if x.eid == 'Note:{i}'])")) == "0").expect("one is unloaded");
    assert_eq!(clifflet(port, &format!("Note:{cold}.n")), cold.to_string());
    assert_eq!(loaded(port), n + 1);
    drop(run);
}
