//! An idle seat is idle (#1143): started on a store with big rows (the real store's 800 KB chat rows, scaled), with no client, the process
//! uses (almost) no CPU. The packs and the app are the SHIPPED folders; no `claude` is ever run; the port is ephemeral; the child is always reaped.
#![cfg(feature = "bin")]
use membrane_vm::host::{Host, Options};
use serde_json::json;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
/// the bound (FLAGGED for the operator): CPU seconds the idle process may use in IDLE_WINDOW_S seconds, 5 % of one core
const IDLE_CPU_BOUND_S: f64 = 0.25;
const IDLE_WINDOW_S: u64 = 5;

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }

/// the CPU time of a process in seconds (`ps -o time=`: [h:]m:ss.cc)
fn cpu_s(pid: u32) -> f64 {
    let o = Command::new("ps").args(["-o", "time=", "-p", &pid.to_string()]).output().unwrap();
    String::from_utf8_lossy(&o.stdout).trim().split(':').fold(0.0, |a, p| a * 60.0 + p.parse::<f64>().unwrap_or(0.0))
}

#[test]
fn a_seat_on_a_store_with_big_rows_is_idle_when_nobody_asks() {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let dir = std::env::temp_dir().join(format!("cliffvm-idle-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let store = dir.join("seat.sqlite");
    {   // an earlier run: chat messages whose rows hold big raw stream output
        let mut o = Options::new(Some(store.to_string_lossy().to_string())); o.effects = false;
        let h = Host::open(o).unwrap();
        for i in 0..8 {
            let p = json!({"text": format!("hello {i}"), "raw": "x".repeat(800_000)});
            h.born(&format!("membrane::Chat:message:1-{i}"), "Chat", p.as_object().unwrap().clone(), "person");
        }
    }
    let r = repo();
    let packs: Vec<String> = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut child = Command::new(BIN).current_dir(&dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f"))
        .args(["--store", store.to_str().unwrap(), "--port", "0", "--fact", "request_keep_s=0"]).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    rx.recv_timeout(Duration::from_secs(60)).expect("the door came up");
    let pid = child.id();
    // the start (restore, rebuild) may work; then it settles: wait for a quiet second, then measure
    let t = Instant::now();
    let mut last = cpu_s(pid);
    let settled = loop {
        std::thread::sleep(Duration::from_secs(1));
        let now = cpu_s(pid);
        if now - last < 0.05 { break true; }
        last = now;
        if t.elapsed() > Duration::from_secs(60) { break false; }
    };
    let a = cpu_s(pid);
    std::thread::sleep(Duration::from_secs(IDLE_WINDOW_S));
    let used = cpu_s(pid) - a;
    let _ = child.kill(); let _ = child.wait();
    let _ = std::fs::remove_dir_all(&dir);
    assert!(settled, "the seat never settled: it kept working for 60 s with no client");
    assert!(used < IDLE_CPU_BOUND_S, "an idle seat used {used:.2} s of CPU in {IDLE_WINDOW_S} s (bound {IDLE_CPU_BOUND_S} s)");
}
