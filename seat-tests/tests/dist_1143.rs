//! The dist folder (#1143, docs/packaging-plan.md): `scripts/dist/build.sh` assembles `dist/<version>/{bin/cliffvm, packs/, apps/, VERSION, MANIFEST.sha256}` from a release
//! build, and `cliffvm serve` run from THAT folder (the repo is not named to it) comes up. Built here from the test binary (a release build is CI's step).
#![cfg(feature = "bin")]
mod common;

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

const BIN: &str = common::bin("cliffvm");
const VERSION: &str = env!("CARGO_PKG_VERSION");

fn repo() -> PathBuf { Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("dist-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn build(out: &Path, extra: &[&str]) -> std::process::Output {
    Command::new("sh").arg(repo().join("scripts/dist/build.sh")).args(["--bin", BIN, "--out", out.to_str().unwrap()]).args(extra).output().unwrap()
}
fn files(dir: &Path, base: &Path, out: &mut Vec<String>) {
    for e in std::fs::read_dir(dir).unwrap() {
        let p = e.unwrap().path();
        if p.is_dir() { files(&p, base, out); } else { out.push(p.strip_prefix(base).unwrap().to_string_lossy().to_string()); }
    }
}
fn get(port: u16, path: &str, extra: &str) -> (String, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(15))).unwrap();
    s.write_all(format!("GET {path} HTTP/1.1\r\nHost: t\r\n{extra}\r\n").as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    (String::from_utf8_lossy(&out[..i]).to_string(), String::from_utf8_lossy(&out[i + 4..]).to_string())
}

#[test]
fn the_build_makes_the_version_stamped_tree_with_a_manifest_that_lists_every_file() {
    let t = tmp("a");
    let out = t.join("dist").join(VERSION);
    let o = build(&out, &[]);
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    assert!(out.join("bin/cliffvm").is_file());
    assert_eq!(std::fs::read_to_string(out.join("VERSION")).unwrap().trim(), VERSION, "the stamp is the crate's version");
    // the packs of the barest seat, the barest app, the page, the help
    let list = std::fs::read_to_string(repo().join("membrane/apps/barest.packs")).unwrap();
    for name in list.lines().map(|l| l.trim()).filter(|l| !l.is_empty() && !l.starts_with('#')) { assert!(out.join("packs").join(format!("{name}.c1f")).is_file(), "pack {name}"); }
    assert!(out.join("packs/boot.json").is_file() && out.join("apps/barest.packs").is_file() && out.join("apps/barest.c1f/help.c1f").is_file());
    assert!(out.join("apps/barest.c1f/ui/index.html").is_file() && out.join("apps/barest.c1f/ui/fieldvm_core_bg.wasm").is_file(), "the page (with its wasm)");
    // ONLY what the barest seat loads: no Python-era pack, no help folder, and exactly the listed packs + launcher + boot.json
    assert!(!out.join("help").exists() && !out.join("packs/banner.c1f").is_file() && !out.join("packs/door_voice.c1f").is_file() && !out.join("packs/facts.json").is_file());
    let mut want: Vec<String> = list.lines().map(|l| l.trim()).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| format!("{n}.c1f")).collect();
    want.push("launcher.c1f".into()); want.push("boot.json".into()); want.sort(); want.dedup();
    let mut have: Vec<String> = std::fs::read_dir(out.join("packs")).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().to_string()).collect(); have.sort();
    assert_eq!(have, want, "the packs folder holds exactly what the seat loads");
    assert!(!out.join("ui").exists(), "the old shared ui folder is gone");
    assert!(!out.join("apps/field.c1f").exists(), "the full seat's app is not in the barest dist");
    // the manifest: every file but itself, a sha256 each, sorted; the stamp's file is in it
    let m = std::fs::read_to_string(out.join("MANIFEST.sha256")).unwrap();
    let mut listed: Vec<String> = m.lines().map(|l| l.split_once("  ").expect("`<sha256>  <path>`").1.to_string()).collect();
    for l in m.lines() { let h = l.split_once("  ").unwrap().0; assert!(h.len() == 64 && h.chars().all(|c| c.is_ascii_hexdigit()), "{l}"); }
    let mut sorted = listed.clone(); sorted.sort();
    assert_eq!(listed, sorted, "sorted");
    let mut real = vec![];
    files(&out, &out, &mut real);
    real.retain(|f| f != "MANIFEST.sha256");
    real.sort();
    listed.sort();
    assert_eq!(listed, real, "the manifest names exactly the files there");
    // verify passes, and catches a changed file and a file that is not listed
    let v = |d: &Path| Command::new("sh").arg(repo().join("scripts/dist/verify.sh")).arg(d).output().unwrap();
    assert!(v(&out).status.success(), "{}", String::from_utf8_lossy(&v(&out).stderr));
    std::fs::OpenOptions::new().append(true).open(out.join("packs/boot.c1f")).unwrap().write_all(b"-- tampered\n").unwrap();
    let bad = v(&out);
    assert!(!bad.status.success() && String::from_utf8_lossy(&bad.stderr).contains("packs/boot.c1f"), "{}", String::from_utf8_lossy(&bad.stderr));
    let _ = std::fs::remove_dir_all(&t);
}

#[test]
fn the_manifest_is_the_same_for_the_same_inputs() {
    let t = tmp("b");
    assert!(build(&t.join("one"), &[]).status.success() && build(&t.join("two"), &[]).status.success());
    assert_eq!(std::fs::read_to_string(t.join("one/MANIFEST.sha256")).unwrap(), std::fs::read_to_string(t.join("two/MANIFEST.sha256")).unwrap());
    let _ = std::fs::remove_dir_all(&t);
}

#[test]
fn serve_from_the_built_folder_finds_its_packs_there_and_not_in_the_repo() {
    let t = tmp("c");
    let out = t.join("dist").join(VERSION);
    assert!(build(&out, &[]).status.success());
    // a pack of the dist is edited: the seat must read THIS copy (a mark in boot's neighbour that makes the launcher default port 0), proving the repo's packs are not the ones read
    let pack = out.join("packs/launcher.c1f");
    let text = std::fs::read_to_string(&pack).unwrap();
    std::fs::write(&pack, text.replace("default_port getter: 4096", "default_port getter: 0")).unwrap();
    let data = t.join("data");
    let fake = t.join("claude");
    std::fs::write(&fake, "#!/bin/sh\nexit 0\n").unwrap();
    let mut child = Command::new(out.join("bin/cliffvm")).args(["serve", "--data-dir", data.to_str().unwrap()]).current_dir(&t)
        .env("CLAUDE_BIN", &fake).env_remove("ANTHROPIC_API_KEY").stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
    let mut port = 0u16;
    while let Some(Ok(l)) = lines.next() { if l.starts_with("cliffvm: door listening on") { port = l.rsplit(':').next().unwrap().trim().parse().unwrap(); break; } }
    assert!(port != 0 && port != 4096, "the dist's own launcher.c1f was read: {port}");
    let tok = std::fs::read_to_string(data.join("admin.token")).unwrap();
    let (h, b) = get(port, "/global/health", &format!("Authorization: Bearer {}\r\n", tok.trim()));
    assert!(h.starts_with("HTTP/1.1 200") && b.contains("healthy"), "{h} {b}");
    let (h, b) = get(port, "/", "");
    assert!(h.starts_with("HTTP/1.1 200") && b.to_lowercase().contains("<html"), "the page comes from the dist's apps/: {h}");
    let _ = child.kill(); let _ = child.wait();
    let _ = std::fs::remove_dir_all(&t);
}

#[test]
fn the_wheel_payload_is_the_dist_without_a_version_folder_and_names_its_interface() {
    let t = tmp("d");
    let out = t.join("dist").join(VERSION);
    assert!(build(&out, &[]).status.success());
    let payload = t.join("payload");
    let o = Command::new("sh").arg(repo().join("scripts/dist/assemble_wheel_payload.sh")).arg(&out).arg(&payload).output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    // the wrapper packages `payload/` as `membrane/dist/` (package-data `dist/**`): bin/cliffvm, packs, apps, help at the top, executable bit kept, the stamp and manifest beside them
    use std::os::unix::fs::PermissionsExt;
    assert!(std::fs::metadata(payload.join("dist/bin/cliffvm")).unwrap().permissions().mode() & 0o111 != 0);
    for d in ["packs", "apps"] { assert!(payload.join("dist").join(d).is_dir(), "{d}"); }
    assert_eq!(std::fs::read_to_string(payload.join("dist/VERSION")).unwrap().trim(), VERSION);
    let v = Command::new("sh").arg(repo().join("scripts/dist/verify.sh")).arg(payload.join("dist")).output().unwrap();
    assert!(v.status.success(), "{}", String::from_utf8_lossy(&v.stderr));
    // `cliffvm --version` from the payload
    let r = Command::new(payload.join("dist/bin/cliffvm")).arg("--version").output().unwrap();
    assert!(String::from_utf8_lossy(&r.stdout).contains(VERSION));
    let _ = std::fs::remove_dir_all(&t);
}

#[test]
fn a_missing_binary_is_refused_before_anything_is_made() {
    let t = tmp("e");
    let o = Command::new("sh").arg(repo().join("scripts/dist/build.sh")).args(["--bin", t.join("nope").to_str().unwrap(), "--out", t.join("o").to_str().unwrap()]).output().unwrap();
    assert!(!o.status.success() && String::from_utf8_lossy(&o.stderr).contains("nope"));
    assert!(!t.join("o").exists(), "nothing half-made");
    let _ = std::fs::remove_dir_all(&t);
}
