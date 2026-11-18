//! Deadlines shared by the seat-spawning test files (#1143). A deadline here only bounds a HANG: every wait polls its condition and returns the moment it holds, so a
//! passing test costs the same whatever number is chosen here. The numbers are sized for a machine under heavy load (all cores busy plus a second heavy job: the boot of
//! a seat took 30+ s there, a turn of a fake CLI more than 60 s), not for the idle case. They are TEST deadlines, not product constants.
#![allow(dead_code)]
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

/// from spawning a seat to the first line of its stdout (the door's address): the boot reads, loads and starts every pack
pub const DOOR_UP: Duration = Duration::from_secs(180);
/// the default deadline of a poll for a condition (a turn done, a row written, a receipt landed)
pub const WAIT: Duration = Duration::from_secs(180);
/// a socket read: one HTTP answer from the seat's door (a route that runs a turn's setup, a command, a query)
pub const IO: Duration = Duration::from_secs(120);

/// poll `f` until it gives Some; `what` names the condition in the panic of a hang
pub fn wait_for<T>(what: &str, deadline: Duration, mut f: impl FnMut() -> Option<T>) -> T {
    let t = std::time::Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < deadline, "timed out after {deadline:?} waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// A name for a temp directory or file: the clock plus a counter of this process, so two callers never share one, however coarse the clock (macOS ticks in microseconds) and
/// however many threads ask at the same instant. Letters, digits and `-` only.
pub fn unique() -> String {
    static COUNT: AtomicU64 = AtomicU64::new(0);
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    format!("{nanos}-{}", COUNT.fetch_add(1, Ordering::SeqCst))
}

/// A program of the pinned cliffvm release (bin/<name> of the folder CLIFFVM_DIST names; `scripts/dist/fetch_cliffvm.sh <version> <target> <folder>` makes it). The tests spawn the
/// engine as a process: babyseat does not build it any more (CLIFFVM_VERSION).
pub fn bin(name: &str) -> &'static str {
    let dist = std::env::var("CLIFFVM_DIST").unwrap_or_else(|_| panic!("CLIFFVM_DIST must name the folder of the pinned cliffvm release (scripts/dist/fetch_cliffvm.sh \"$(cat CLIFFVM_VERSION)\" <target> <folder>)"));
    let p = std::path::Path::new(&dist).join("bin").join(name);
    assert!(p.is_file(), "{} is not a file: CLIFFVM_DIST is not a cliffvm release folder", p.display());
    Box::leak(p.to_string_lossy().into_owned().into_boxed_str())
}

/// The folder of the pinned cliffvm crate's source (its `src/`, `packs/`, `tests/`), from the dependency itself: `cargo metadata` names where cargo unpacked it.
pub fn engine_dir() -> &'static std::path::Path {
    static DIR: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();
    DIR.get_or_init(|| {
        let out = std::process::Command::new(std::env::var("CARGO").unwrap_or_else(|_| "cargo".into()))
            .args(["metadata", "--format-version", "1", "--manifest-path"]).arg(concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml")).output().expect("cargo metadata");
        assert!(out.status.success(), "cargo metadata failed: {}", String::from_utf8_lossy(&out.stderr));
        let v: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
        let p = v["packages"].as_array().unwrap().iter().find(|p| p["name"] == "cliffvm").expect("the cliffvm dependency");
        std::path::Path::new(p["manifest_path"].as_str().unwrap()).parent().unwrap().to_path_buf()
    })
}

/// The product's version (babyseat's own: `pyproject.toml`, which `scripts/dist/build.sh` stamps into the dist). Not this package's (`CARGO_PKG_VERSION` is 0.0.0 here: it was the product's when the
/// tests lived in the seat's own crate).
pub fn product_version() -> &'static str {
    static V: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    V.get_or_init(|| {
        let text = std::fs::read_to_string(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../pyproject.toml")).expect("pyproject.toml");
        text.lines().find_map(|l| l.strip_prefix("version = \"").and_then(|v| v.strip_suffix('"'))).expect("a version in pyproject.toml").to_string()
    })
}

/// The engine's version the product is pinned to (`CLIFFVM_VERSION`): what the program reports.
pub fn engine_version() -> &'static str {
    static V: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    V.get_or_init(|| std::fs::read_to_string(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../CLIFFVM_VERSION")).expect("CLIFFVM_VERSION").trim().to_string())
}
