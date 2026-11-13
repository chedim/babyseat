//! The browser build (wasm32, `--no-default-features --features wasm`) must keep compiling: the store, drivers and
//! sockets are not part of it. Skips (with a message) when the wasm32 target is not installed for the toolchain.
use std::process::Command;

fn rustup_bin() -> Option<String> {
    let home = std::env::var("HOME").ok()?;
    let p = format!("{home}/.rustup/toolchains/stable-aarch64-apple-darwin/bin");
    std::path::Path::new(&p).is_dir().then_some(p)
}

#[test]
fn wasm_build_checks() {
    let mut cmd = Command::new("cargo");
    if let Some(b) = rustup_bin() {
        // /opt/homebrew/bin/cargo shadows rustup's wasm32 target: use the rustup toolchain when it is there
        let path = format!("{b}:{}", std::env::var("PATH").unwrap_or_default());
        cmd.env("PATH", path).env("RUSTC", format!("{b}/rustc"));
    }
    let tdir = std::env::temp_dir().join("membrane-wasm-check-target");
    let out = cmd
        .args(["check", "--target", "wasm32-unknown-unknown", "--no-default-features", "--features", "wasm"])
        .env("CARGO_TARGET_DIR", tdir)
        .env_remove("RUSTFLAGS")
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()
        .expect("run cargo");
    let err = String::from_utf8_lossy(&out.stderr);
    if !out.status.success() && (err.contains("can't find crate for `core`") || err.contains("target may not be installed")) {
        eprintln!("SKIP wasm_build_checks: the wasm32-unknown-unknown target is not installed");
        return;
    }
    assert!(out.status.success(), "wasm32 check failed:\n{err}");
}
