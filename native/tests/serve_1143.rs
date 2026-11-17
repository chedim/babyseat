//! `cliffvm serve` (#1143, docs/packaging-plan.md): the one-command launcher. It finds the dist folder from its own binary (bin/cliffvm next to packs/ apps/ ui/),
//! starts the barest seat there, and holds NO app default of its own: the minds, the models, the local backend and the default port live in the pack `launcher.c1f`.
//! A fake `claude` stands in for the CLI: no chat turn runs here.
#![cfg(feature = "bin")]
mod common;

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, lines: Receiver<String>, port: u16, said: Vec<String> }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn tmp(tag: &str) -> PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("serve-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn repo_membrane() -> PathBuf { Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane") }
fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for e in std::fs::read_dir(from).unwrap() {
        let p = e.unwrap().path();
        let t = to.join(p.file_name().unwrap());
        if p.is_dir() { copy_dir(&p, &t); } else { std::fs::copy(&p, &t).unwrap(); }
    }
}
/// a dist folder in a temp dir: bin/cliffvm (a copy of the built binary), packs/ and apps/ copied from the repo; nothing of the repo is named to the binary
fn dist(tag: &str) -> PathBuf {
    let d = tmp(tag).join("dist");
    std::fs::create_dir_all(d.join("bin")).unwrap();
    std::fs::copy(BIN, d.join("bin/cliffvm")).unwrap();
    copy_dir(&repo_membrane().join("packs"), &d.join("packs"));
    copy_dir(&repo_membrane().join("apps"), &d.join("apps"));
    d
}
fn fake_claude(dir: &Path) -> PathBuf {
    let p = dir.join("claude");
    std::fs::write(&p, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    p
}
fn spawn(bin: &Path, args: &[&str], env: &[(&str, &str)], env_rm: &[&str]) -> Child {
    let mut c = Command::new(bin);
    c.arg("serve").args(args).current_dir(std::env::temp_dir()).stdout(Stdio::piped()).stderr(Stdio::piped());
    for k in env_rm { c.env_remove(k); }
    for (k, v) in env { c.env(k, v); }
    busy_retry(|| c.spawn())
}
/// a binary that was just copied can answer "Text file busy" (ETXTBSY) to an exec on Linux while another test thread is between fork and exec holding the copy's write descriptor:
/// the second try works, so the tests try again for a moment (a real failure to start is another error and is not retried)
fn busy_retry<T>(mut f: impl FnMut() -> std::io::Result<T>) -> T {
    for _ in 0..100 {
        match f() { Err(e) if e.raw_os_error() == Some(26) => std::thread::sleep(Duration::from_millis(50)), r => return r.unwrap() }
    }
    f().unwrap()
}
/// start `serve` and read stdout until the door is up and the lines after it are told (the curl example is the last)
fn start(bin: &Path, args: &[&str], env: &[(&str, &str)], env_rm: &[&str]) -> Run {
    let mut child = spawn(bin, args, env, env_rm);
    let out = child.stdout.take().unwrap();
    let err = child.stderr.take().unwrap();
    let (tx, rx) = channel();
    let tx2 = tx.clone();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    std::thread::spawn(move || for l in BufReader::new(err).lines().map_while(Result::ok) { if tx2.send(format!("[stderr] {l}")).is_err() { break; } });
    let mut port = 0u16;
    let mut said = vec![];
    while let Ok(l) = rx.recv_timeout(Duration::from_secs(30)) {
        said.push(l.clone());
        if l.starts_with("cliffvm: door listening on") { port = l.rsplit(':').next().unwrap().trim().parse().unwrap(); }
        if port != 0 && l.contains("curl") { break; }
    }
    assert!(port != 0, "the door came up: {said:?}");
    Run { child, lines: rx, port, said }
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
fn bearer(data: &Path) -> String { format!("Authorization: Bearer {}\r\n", std::fs::read_to_string(data.join("admin.token")).unwrap().trim()) }

#[test]
fn help_names_every_option_and_where_the_defaults_live() {
    let o = Command::new(BIN).args(["serve", "--help"]).output().unwrap();
    assert!(o.status.success());
    let t = String::from_utf8_lossy(&o.stdout);
    for w in ["cliffvm serve", "--port", "--data-dir", "--bind", "--fact", "--store", "--root", "127.0.0.1", "ANTHROPIC_API_KEY", "chat_auth=login", "CLAUDE_BIN", "launcher.c1f"] { assert!(t.contains(w), "help names {w}: {t}"); }
    let top = Command::new(BIN).arg("--help").output().unwrap();
    assert!(String::from_utf8_lossy(&top.stdout).contains("serve"), "the top usage lists serve");
}

#[test]
fn it_finds_packs_apps_and_ui_next_to_its_own_binary_and_serves_the_page_and_the_door_with_the_admin_token() {
    let d = dist("a");
    let data = d.parent().unwrap().join("data");
    let claude = fake_claude(d.parent().unwrap());
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap()], &[("CLAUDE_BIN", claude.to_str().unwrap())], &[]);
    // the first run made the door's secret (private) and a token file; the lines say where
    let secret = data.join("door.secret");
    assert_eq!(std::fs::metadata(&secret).unwrap().permissions().mode() & 0o777, 0o600);
    let said = run.said.join("\n");
    assert!(said.contains(&data.join("admin.token").display().to_string()), "the token file's path is told: {said}");
    assert!(said.contains(&format!("http://127.0.0.1:{}/", run.port)), "the URL is told: {said}");
    assert!(said.contains("Authorization: Bearer $(cat ") && said.contains("/global/health"), "a ready curl example: {said}");
    assert!(said.contains(&claude.display().to_string()), "the claude CLI that was found is told: {said}");
    // the page, with the admin cookie for a browser on this machine; the door refuses a bare caller and serves the token's
    let (head, body) = get(run.port, "/", "");
    assert!(head.starts_with("HTTP/1.1 200"), "{head}");
    assert!(body.to_lowercase().contains("<html") || body.to_lowercase().contains("<!doctype"), "the barest page");
    assert!(get(run.port, "/global/health", "").0.starts_with("HTTP/1.1 401"));
    let (h, b) = get(run.port, "/global/health", &bearer(&data));
    assert!(h.starts_with("HTTP/1.1 200") && b.contains("healthy"), "{h} {b}");
    // the defaults of the c1f launcher pack: the models route answers (503 without `minds`) and NO model is chosen (the local Ollama is the backend; the claude CLI is the developer mode), the router is off
    let (h, b) = get(run.port, "/membrane/models", &bearer(&data));
    assert!(h.starts_with("HTTP/1.1 200") && b.contains("\"seat_default\":\"\""), "the minds are the launcher pack's: {h} {b}");
    let (_, l) = get(run.port, "/v1/models", &bearer(&data));
    assert!(!l.contains("\"claude\""), "no claude rung by default: {l}");
    let (_, o) = get(run.port, "/membrane/options", &bearer(&data));
    assert!(o.contains("\"chat_auth\":\"api_key\""), "the key is the auth by default: {o}");
    let _ = std::fs::remove_dir_all(d.parent().unwrap());
}

#[test]
fn a_binary_with_no_packs_beside_it_says_where_it_looked_and_how_to_name_the_folder() {
    let d = tmp("b");
    std::fs::create_dir_all(d.join("bin")).unwrap();
    std::fs::copy(BIN, d.join("bin/cliffvm")).unwrap();
    let o = busy_retry(|| Command::new(d.join("bin/cliffvm")).args(["serve", "--port", "0", "--data-dir", d.join("data").to_str().unwrap()]).current_dir(&d).output());
    assert_eq!(o.status.code(), Some(2), "refused");
    let e = String::from_utf8_lossy(&o.stderr);
    assert!(e.contains(&d.join("packs").display().to_string()) && e.contains("--root"), "{e}");
    assert!(!d.join("data").join("door.secret").exists(), "nothing was made before the folder was found");
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn root_names_a_folder_that_is_not_the_binarys_own_and_the_repo_tree_is_one() {
    let d = tmp("c");
    let data = d.join("data");
    let claude = fake_claude(&d);
    // the repo's membrane/ has the dist's shape (packs/ apps/): the dev wrapper names it with --root
    let run = start(Path::new(BIN), &["--port", "0", "--data-dir", data.to_str().unwrap(), "--root", repo_membrane().to_str().unwrap()], &[("CLAUDE_BIN", claude.to_str().unwrap())], &[]);
    assert!(get(run.port, "/global/health", &bearer(&data)).0.starts_with("HTTP/1.1 200"));
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn a_fact_wins_over_the_launcher_pack_defaults_and_chat_auth_login_is_an_opt_in() {
    let d = dist("d");
    let data = d.parent().unwrap().join("data");
    let claude = fake_claude(d.parent().unwrap());
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap(), "--fact", "chat_auth=login",
        "--fact", r#"cloud_models=[{"name":"zed","backend":"claude-agent"}]"#], &[("CLAUDE_BIN", claude.to_str().unwrap())], &["ANTHROPIC_API_KEY"]);
    let (_, b) = get(run.port, "/v1/models", &bearer(&data));
    assert!(b.contains("zed") && !b.contains("\"claude\""), "the fact's model replaces the default: {b}");
    let (_, o) = get(run.port, "/membrane/options", &bearer(&data));
    assert!(o.contains("\"chat_auth\":\"login\""), "the opt-in is told: {o}");
    let said = run.said.join("\n");
    assert!(!said.contains("ANTHROPIC_API_KEY"), "no key warning when login was chosen: {said}");
    let _ = std::fs::remove_dir_all(d.parent().unwrap());
}

#[test]
fn without_a_key_or_login_it_says_so_at_start_and_without_the_cli_it_says_how_to_name_it() {
    let d = dist("e");
    let data = d.parent().unwrap().join("data");
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap()], &[("PATH", "/nonexistent")], &["ANTHROPIC_API_KEY", "CLAUDE_BIN"]);
    let said = run.said.join("\n");
    assert!(said.contains("ANTHROPIC_API_KEY") && said.contains("chat_auth=login"), "{said}");
    assert!(said.contains("CLAUDE_BIN"), "{said}");
    let _ = std::fs::remove_dir_all(d.parent().unwrap());
}

#[test]
fn with_no_data_dir_the_seat_lives_under_the_users_data_location() {
    let d = dist("f");
    let home = d.parent().unwrap().join("home");
    std::fs::create_dir_all(&home).unwrap();
    let want = if cfg!(target_os = "macos") { home.join("Library/Application Support/membrane") } else { home.join(".local/share/membrane") };
    let run = start(&d.join("bin/cliffvm"), &["--port", "0"], &[("HOME", home.to_str().unwrap()), ("CLAUDE_BIN", "/usr/bin/true")], &["XDG_DATA_HOME", "ANTHROPIC_API_KEY"]);
    assert!(want.join("door.secret").exists() && want.join("admin.token").exists() && want.join("seat.sqlite").exists(), "{:?}", want);
    assert!(get(run.port, "/global/health", &bearer(&want)).0.starts_with("HTTP/1.1 200"));
    drop(run);
    // XDG_DATA_HOME moves it on Linux and macOS alike (the freedesktop name is honored where it is set)
    let xdg = d.parent().unwrap().join("xdg");
    let run = start(&d.join("bin/cliffvm"), &["--port", "0"], &[("HOME", home.to_str().unwrap()), ("XDG_DATA_HOME", xdg.to_str().unwrap()), ("CLAUDE_BIN", "/usr/bin/true")], &["ANTHROPIC_API_KEY"]);
    assert!(xdg.join("membrane/door.secret").exists());
    drop(run);
    let _ = std::fs::remove_dir_all(d.parent().unwrap());
}

#[test]
fn bad_arguments_are_refused_with_the_serve_usage() {
    for args in [&["serve", "--port", "x"][..], &["serve", "--nope"], &["serve", "app"], &["serve", "--fact", "novalue"]] {
        let o = Command::new(BIN).args(args).output().unwrap();
        assert_eq!(o.status.code(), Some(64), "{args:?}");
        assert!(String::from_utf8_lossy(&o.stderr).contains("cliffvm serve"), "{args:?}");
    }
}

#[test]
fn sigterm_ends_the_seat_cleanly() {
    let d = dist("g");
    let data = d.parent().unwrap().join("data");
    let mut run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap()], &[("CLAUDE_BIN", "/usr/bin/true")], &[]);
    unsafe { libc::kill(run.child.id() as i32, libc::SIGTERM); }
    let t = std::time::Instant::now();
    let st = loop {
        if let Some(s) = run.child.try_wait().unwrap() { break s; }
        assert!(t.elapsed() < Duration::from_secs(20), "it ended");
        std::thread::sleep(Duration::from_millis(50));
    };
    assert!(st.success(), "{st:?}");
    let _ = run.lines.try_recv();
    let _ = std::fs::remove_dir_all(d.parent().unwrap());
}

#[test]
fn the_default_port_is_the_launcher_packs_not_the_binarys_and_editing_the_pack_changes_it() {
    // (4096 itself is not bound here: a real seat may hold it.) The dist's own pack says port 0, no `--port` is given, and the door comes up: the default was read from the pack.
    let d = dist("h");
    let data = d.parent().unwrap().join("data");
    let pack = d.join("packs/launcher.c1f");
    let text = std::fs::read_to_string(&pack).unwrap();
    assert!(text.contains("default_port getter: 4096"), "the shipped default is 4096");
    std::fs::write(&pack, text.replace("default_port getter: 4096", "default_port getter: 0")).unwrap();
    let run = start(&d.join("bin/cliffvm"), &["--data-dir", data.to_str().unwrap()], &[("CLAUDE_BIN", "/usr/bin/true")], &[]);
    assert!(run.port != 0 && run.port != 4096, "the door listens where the pack says: {}", run.port);
    assert!(get(run.port, "/global/health", &bearer(&data)).0.starts_with("HTTP/1.1 200"));
    let _ = std::fs::remove_dir_all(d.parent().unwrap());
}

// ---- the old Python seat's store (the cutover pack, packs/legacy_cutover.c1f, reads the fact `legacy_path`; c1f cannot read the environment, so `serve` tells it) ----
fn post(port: u16, path: &str, extra: &str, body: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(15))).unwrap();
    s.write_all(format!("POST {path} HTTP/1.1\r\nHost: t\r\n{extra}Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let t = String::from_utf8_lossy(&out).to_string();
    t.split_once("\r\n\r\n").map(|x| x.1.to_string()).unwrap_or_default()
}
/// `Boot:seat.legacy_path` as the seat holds it (None = no such property)
fn legacy_fact(run: &Run, data: &Path) -> Option<String> {
    let body = serde_json::json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": {"spec": "Boot:seat", "prop": "legacy_path"}}}).to_string();
    let r: serde_json::Value = serde_json::from_str(&post(run.port, "/mcp", &bearer(data), &body)).unwrap();
    let t = r["result"]["content"][0]["text"].as_str().unwrap_or("").to_string();
    if t.contains(" has no ") || t.is_empty() { None } else { Some(serde_json::from_str::<String>(&t).unwrap_or(t)) }
}
/// a fake old data dir: `<dir>/field.sqlite/field.sqlite3` is a file (not a real store: the seat only has to be TOLD where it is)
fn fake_old_dir(parent: &Path, name: &str) -> PathBuf {
    let d = parent.join(name);
    std::fs::create_dir_all(d.join("field.sqlite")).unwrap();
    std::fs::write(d.join("field.sqlite/field.sqlite3"), b"not a database").unwrap();
    d
}

#[test]
fn legacy_names_the_old_store_and_the_fact_reaches_the_seat() {
    let d = dist("i");
    let t = d.parent().unwrap();
    let data = t.join("data");
    let old = fake_old_dir(t, "old");
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap(), "--legacy", old.to_str().unwrap()], &[("CLAUDE_BIN", "/usr/bin/true")], &["MEMBRANE_DATA_DIR"]);
    assert_eq!(legacy_fact(&run, &data), Some(old.display().to_string()), "--legacy becomes the fact legacy_path");
    drop(run);
    let _ = std::fs::remove_dir_all(t);
}

#[test]
fn the_old_membrane_data_dir_variable_is_passed_as_legacy_path_and_legacy_wins_over_it() {
    let d = dist("j");
    let t = d.parent().unwrap();
    let data = t.join("data");
    let from_env = fake_old_dir(t, "env-old");
    let named = fake_old_dir(t, "named-old");
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap()], &[("CLAUDE_BIN", "/usr/bin/true"), ("MEMBRANE_DATA_DIR", from_env.to_str().unwrap())], &[]);
    assert_eq!(legacy_fact(&run, &data), Some(from_env.display().to_string()), "MEMBRANE_DATA_DIR is the Python seat's data dir: passed as legacy_path");
    drop(run);
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap(), "--legacy", named.to_str().unwrap()], &[("CLAUDE_BIN", "/usr/bin/true"), ("MEMBRANE_DATA_DIR", from_env.to_str().unwrap())], &[]);
    assert_eq!(legacy_fact(&run, &data), Some(named.display().to_string()), "--legacy wins over the variable");
    drop(run);
    // an explicit `--fact legacy_path=` of the operator comes after both and wins
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap(), "--legacy", named.to_str().unwrap(), "--fact", "legacy_path=/somewhere/else"], &[("CLAUDE_BIN", "/usr/bin/true")], &["MEMBRANE_DATA_DIR"]);
    assert_eq!(legacy_fact(&run, &data), Some("/somewhere/else".to_string()));
    drop(run);
    let _ = std::fs::remove_dir_all(t);
}

#[test]
fn with_no_legacy_and_no_variable_no_legacy_path_is_told_and_help_names_both() {
    let d = dist("k");
    let t = d.parent().unwrap();
    let data = t.join("data");
    let run = start(&d.join("bin/cliffvm"), &["--port", "0", "--data-dir", data.to_str().unwrap()], &[("CLAUDE_BIN", "/usr/bin/true")], &["MEMBRANE_DATA_DIR"]);
    assert_eq!(legacy_fact(&run, &data), None, "nothing named: the cutover pack searches its own places");
    drop(run);
    let h = Command::new(BIN).args(["serve", "--help"]).output().unwrap();
    let h = String::from_utf8_lossy(&h.stdout);
    assert!(h.contains("--legacy") && h.contains("MEMBRANE_DATA_DIR") && h.contains("legacy_path"), "{h}");
    let _ = std::fs::remove_dir_all(t);
}
