//! THE STDLIB DRIVERS (#1044, the operator 2026-11-10): `fs` and `proc` for `.c1f`, native, no Python.
//!
//! - Reads are getter built-ins, asked like `sql(...)` (store.rs `request_job`): off the engine's thread, the getter
//!   reads None until the answer lands, so they can't hang the VM. `read(path)` -> text, `listdir(path)` ->
//!   [{name, kind, size, mtime}], `stat(path)` -> {exists, kind, size, mtime}.
//! - Effects are writes the engine runs itself (fields.rs `effect`), off its lock, with a receipt on the entity as
//!   `<verb>_result` = {id, status, rc, head, ...} and `<verb>_rc` at the next round:
//!     File:<...>.write  = {id, content, append}     File.move = {id, to}     File.delete = {id}
//!     Command:<...>.run = {id, args}  (the entity's cmd, or the `cmd` in the run value (also stored on the entity): an argument list, or a string run by `sh -c`; cwd;
//!                                     env_from {VAR: ENV_VAR}: secrets named, read here, never stored)
//!     Command.stop      = {id}        (kills the running child; its run's receipt says so)
//! - Reach (the operator): everything the VM's OS user can do. Entity scripts and peers never trigger an effect
//!   (fields.rs checks the writer). Per-token scopes are a later release.
//! - A Command run has a time limit, `timeout_s` (the model's estimate of the longest it may run; default the boot fact `command_timeout_s`, 120 s): past it
//!   the child's process group gets SIGTERM, after `KILL_GRACE_MS` SIGKILL, and the receipt is `status: timeout`, `rc: 124`, `killed: true`, `timeout_s`,
//!   with the partial output. `timeout_s` of 0 or less is refused.
//! - As the official libraries: no output cap (a command's whole stdout and stderr are kept;
//!   `head` is the receipt's first lines for a glance).
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use serde_json::{json, Map, Value as J};

use crate::store::{Poll, Store};

/// a Command run that states no `timeout_s` is limited to this many seconds (the boot fact `command_timeout_s` changes it): the operator-delegated
/// sensible default (2026-11-13), the time a model-run command may take unless the model says more
pub const DEFAULT_COMMAND_TIMEOUT_S: f64 = 120.0;
/// what a handed-over secret (`env_from`) becomes where the child printed it
pub const MASK: &str = "***";
/// A handed-over value shorter than this many characters is NOT masked: it is no credential, and replacing every occurrence of a short word
/// would corrupt the child's stream-json. The operator approved 16 (2026-11-13): real keys and tokens are far longer.
pub const MIN_SECRET_CHARS: usize = 16;
/// after the SIGTERM of a timeout the group gets this long to end; then it is killed with SIGKILL (the operator, 2026-11-13)
pub const KILL_GRACE_MS: u64 = 2000;
/// the exit code of a receipt whose run was killed by its timeout (the convention of GNU `timeout`)
pub const TIMEOUT_RC: i64 = 124;

/// Where a chunk may end: the last newline of `held` that no handed-over value spans. A value is one line (an API key), so the last newline is
/// safe; a value that holds a newline is cut only at a newline at least its length before the end of what was read, where it would be whole in `held`
/// (and not at one inside an occurrence of it), so a chunk never ends in the middle of a secret.
fn cut_at(held: &[u8], secrets: &[String]) -> Option<usize> {
    let guard = secrets.iter().filter(|s| s.contains('\n')).map(|s| s.len()).max().unwrap_or(0);
    let mut end = if guard == 0 { held.len() } else { (held.len() + 1).saturating_sub(guard) };
    let inside = |i: usize| secrets.iter().any(|s| { let n = s.as_bytes(); !n.is_empty() && held.windows(n.len()).enumerate().any(|(a, w)| w == n && a <= i && i + 1 < a + n.len()) });
    while let Some(i) = held[..end].iter().rposition(|b| *b == b'\n') {
        if guard == 0 || !inside(i) { return Some(i); }
        end = i;
    }
    None
}

/// the limit of a run in seconds: the run value's `timeout_s`, else the entity's, else the boot fact; a number above 0, else the refusal's words
pub fn timeout_of(st: &Store, req: &J, props: &Map<String, J>) -> Result<f64, String> {
    match req.get("timeout_s").filter(|v| !v.is_null()).or(props.get("timeout_s").filter(|v| !v.is_null())) {
        None => Ok(st.command_timeout()),
        Some(v) => match v.as_f64() {
            Some(n) if n > 0.0 && n.is_finite() => Ok(n),
            _ => Err(format!("refused: timeout_s must be a number above 0 (it was {v}); state your estimate of the longest the command may run, in seconds, and a bigger number for a longer job, e.g. timeout_s: 600")),
        },
    }
}
/// a whole number of seconds is an integer in JSON
pub fn num_json(n: f64) -> J { if n.fract() == 0.0 && n.abs() < 1e15 { json!(n as i64) } else { json!(n) } }

pub const HEAD_CHARS: usize = 4000;     // a receipt's `head` (#956's receipt shape); the full output is kept beside it

/// `~` is the home folder; a relative path is the seat's folder.
pub fn resolve(dir: &Path, p: &str) -> PathBuf {
    if p == "~" { return home(); }
    if let Some(rest) = p.strip_prefix("~/") { return home().join(rest); }
    let q = Path::new(p);
    if q.is_absolute() { q.to_path_buf() } else { dir.join(q) }
}
fn home() -> PathBuf { std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default() }

fn mtime(m: &std::fs::Metadata) -> J {
    m.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| json!(d.as_secs_f64())).unwrap_or(J::Null)
}
fn kind(m: &std::fs::Metadata) -> &'static str { if m.is_dir() { "dir" } else if m.is_file() { "file" } else { "other" } }

/// A getter's `read` / `listdir` / `stat`.
pub fn request(st: &Store, asker: &str, f: &str, path: &str) -> Poll {
    let p = resolve(&st.dir, path);
    let key = format!("\u{2}{}\u{0}{}", f, p.display());
    let f = f.to_string();
    st.request_job(asker, key, false, Box::new(move || -> Result<Vec<J>, String> {
        match f.as_str() {
            "read" => std::fs::read_to_string(&p).map(|t| vec![J::String(t)]).map_err(|e| format!("read {}: {}", p.display(), e)),
            "listdir" => {
                let mut out = vec![];
                for e in std::fs::read_dir(&p).map_err(|e| format!("listdir {}: {}", p.display(), e))? {
                    let e = e.map_err(|e| e.to_string())?;
                    let m = e.metadata().map_err(|e| e.to_string())?;
                    out.push(json!({"name": e.file_name().to_string_lossy(), "kind": kind(&m), "size": m.len(), "mtime": mtime(&m)}));
                }
                out.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
                Ok(vec![J::Array(out)])
            }
            _ => Ok(vec![match std::fs::metadata(&p) {
                Ok(m) => json!({"exists": true, "kind": kind(&m), "size": m.len(), "mtime": mtime(&m)}),
                Err(_) => json!({"exists": false}),
            }]),
        }
    }))
}
pub fn key(st: &Store, f: &str, path: &str) -> String { format!("\u{2}{}\u{0}{}", f, resolve(&st.dir, path).display()) }

/// Folders the operating system wipes (a reboot, a battery restart): work done there is lost (the operator, 2026-11-12).
const EPHEMERAL: [&str; 5] = ["/tmp", "/private/tmp", "/var/tmp", "/var/folders", "/private/var/folders"];

/// Is this path one the seat refuses to write or run in? The seat's own data folder is never refused: a throwaway seat
/// (a test, a probe) lives under the OS temp folder.
fn ephemeral(p: &std::path::Path, seat: &std::path::Path) -> bool {
    !p.starts_with(seat) && EPHEMERAL.iter().any(|e| p.starts_with(e))
}

fn ephemeral_why(p: &std::path::Path) -> String {
    format!("refused: {} is under a temporary folder (a reboot or a battery restart wipes it) -- work under the project's \
             .worktrees/<name>, or another folder that survives a reboot", p.display())
}

fn receipt(id: &J, rc: i64, head: String, extra: Map<String, J>) -> J {
    let mut m = Map::new();
    m.insert("id".into(), id.clone());
    m.insert("status".into(), json!(if rc == 0 { "ok" } else { "error" }));
    m.insert("rc".into(), json!(rc));
    m.insert("head".into(), json!(head.chars().take(HEAD_CHARS).collect::<String>()));
    // when the receipt was made (epoch seconds): a Command's `status` tells an old receipt from a refusal of the run in flight by it
    m.insert("finished".into(), json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)));
    for (k, v) in extra { m.insert(k, v); }
    J::Object(m)
}

/// The driver verbs the engine runs: (class, verb).
pub const VERBS: [(&str, &str); 14] = [("File", "write"), ("File", "move"), ("File", "delete"), ("Command", "run"), ("Command", "stop"), ("Store", "run"), ("Http", "listen"), ("Request", "respond"), ("Request", "verify"), ("Request", "mint"), ("Request", "stream"), ("Request", "send"), ("Request", "end"), ("Fetch", "request")];

/// Run one effect (the engine calls this for a write of a driver verb). `props` are the entity's.
pub fn effect(st: &Store, eid: &str, cls: &str, verb: &str, req: &J, props: &Map<String, J>) {
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let dir = st.dir.clone();
    let path = props.get("path").and_then(|v| v.as_str()).map(|p| resolve(&dir, p));
    match (cls, verb) {
        ("Http", "listen") | ("Request", "respond") => {   // the HTTP door (http.rs): a listener, and the answer to a held request
            let (req2, verb2) = (req.clone(), verb.to_string());
            let conn = props.get("conn").and_then(|c| c.as_u64());          // the request entity holds its own connection
            st.effect(eid, verb, Box::new(move |sh, eid| {
                let h = Store::from_shared(sh);
                if verb2 == "listen" { crate::http::listen(&h, eid, &req2) } else { crate::http::respond(&h, &req2, conn) }
            }));
        }
        ("Request", "stream") | ("Request", "send") | ("Request", "end") => {   // the event stream (http.rs): the frame is queued HERE (write order), written by the thread
            let conn = props.get("conn").and_then(|c| c.as_u64());
            let queued = match verb { "stream" => crate::http::stream_open(st, req, conn), v => crate::http::stream_push(st, req, conn, v == "end") };
            st.effect(eid, verb, Box::new(move |sh, _| match queued {
                Ok(n) => crate::http::stream_flush(&Store::from_shared(sh), &id, n),
                Err(rec) => rec,
            }));
        }
        ("Fetch", "request") => {                          // outbound http (fetch.rs): the receipt is data; a stream arrives as Chunk entities
            let req2 = req.clone();
            st.effect_limited(eid, verb, Box::new(move |sh, eid| {
                // the same timing a Command's receipt carries (started, finished in epoch seconds; ms), on a refusal too
                let epoch = || std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                let (t_start, t_clock) = (epoch(), std::time::Instant::now());
                let mut r = crate::fetch::request(&Store::from_shared(sh), eid, &req2);
                let round3 = |x: f64| (x * 1000.0).round() / 1000.0;
                if let Some(m) = r.as_object_mut() {
                    m.insert("started".into(), json!(round3(t_start)));
                    m.insert("finished".into(), json!(round3(epoch())));
                    m.insert("ms".into(), json!(t_clock.elapsed().as_millis() as u64));
                }
                r
            }));
        }
        ("Request", "verify") => {                         // a token the request carries: checked with its door's secret
            let (req2, http) = (req.clone(), props.get("http").and_then(|h| h.as_str()).unwrap_or("").to_string());
            st.effect(eid, verb, Box::new(move |sh, _| crate::http::verify(&Store::from_shared(sh), &http, &req2)));
        }
        ("Request", "mint") => {                           // a token for a person, signed with the door's own secret (the page's owner cookie)
            let (req2, http) = (req.clone(), props.get("http").and_then(|h| h.as_str()).unwrap_or("").to_string());
            st.effect(eid, verb, Box::new(move |sh, _| crate::http::mint(&Store::from_shared(sh), &http, &req2)));
        }
        ("Store", "run") => {                   // the store's writes: bound statements in one transaction, a receipt
            let stmts: Vec<(String, Vec<J>)> = match req.get("statements").and_then(|s| s.as_array()) {
                Some(a) => a.iter().map(|x| (x.get("sql").and_then(|s| s.as_str()).unwrap_or("").to_string(),
                    x.get("args").and_then(|a| a.as_array()).cloned().unwrap_or_default())).collect(),
                None => vec![(req.get("sql").and_then(|s| s.as_str()).unwrap_or("").to_string(),
                    req.get("args").and_then(|a| a.as_array()).cloned().unwrap_or_default())],
            };
            let exec = st.exec_handle();
            st.effect(eid, verb, Box::new(move |_, _| {
                let Some(exec) = exec else { return receipt(&id, 2, "this seat has no store attached".into(), Map::new()) };
                match exec.write(&stmts) {
                    Ok((changes, rows)) => {
                        let mut extra = Map::new();
                        extra.insert("changes".into(), json!(changes));
                        if !rows.is_empty() { extra.insert("rows".into(), J::Array(rows)); }
                        receipt(&id, 0, format!("{changes} row(s)"), extra)
                    }
                    Err(e) => receipt(&id, 2, e, Map::new()),
                }
            }));
        }
        ("Command", "stop") => {
            let killed = st.kill(eid);
            st.effect(eid, verb, Box::new(move |_, _| receipt(&id, if killed { 0 } else { 1 },
                if killed { "stopped".into() } else { "nothing was running".into() }, Map::new())));
        }
        ("Command", "run") => {
            let cmd = req.get("cmd").or(props.get("cmd")).cloned().unwrap_or(J::Null);        // `run = {id, cmd}` starts a command in ONE write; else the entity's cmd
            let cwd = props.get("cwd").and_then(|v| v.as_str()).map(|p| resolve(&dir, p)).unwrap_or(dir.clone());
            let env_from: Vec<(String, String)> = props.get("env_from").and_then(|v| v.as_object())
                .map(|o| o.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect()).unwrap_or_default();
            let args: Vec<String> = req.get("args").and_then(|a| a.as_array()).map(|a| a.iter().map(|x| x.as_str().map(|s| s.to_string()).unwrap_or_else(|| x.to_string())).collect()).unwrap_or_default();
            let strange = std::iter::once(cwd.clone()).chain(args.iter().filter(|a| a.starts_with('/')).map(|a| resolve(&dir, a)))
                .find(|p| ephemeral(p, &dir));
            if let Some(bad) = strange {
                let (id2, why) = (id.clone(), ephemeral_why(&bad));
                st.effect_limited(eid, verb, Box::new(move |_, _| receipt(&id2, 2, why.clone(), Map::new())));
                return;
            }
            let timeout_s = match timeout_of(st, req, props) {
                Ok(t) => t,
                Err(why) => { let id2 = id.clone(); st.effect_limited(eid, verb, Box::new(move |_, _| receipt(&id2, 2, why.clone(), Map::new()))); return; }
            };
            let req2 = req.clone();
            st.effect_limited(eid, verb, Box::new(move |sh, eid| {
                let mut c = match &cmd {
                    J::Array(a) if !a.is_empty() => { let mut c = std::process::Command::new(a[0].as_str().unwrap_or_default()); for x in &a[1..] { c.arg(x.as_str().map(|s| s.to_string()).unwrap_or_else(|| x.to_string())); } c }
                    J::String(s) if !s.trim().is_empty() => { let mut c = std::process::Command::new("sh"); c.arg("-c").arg(s).arg("sh"); c }
                    _ => return receipt(&id, 2, "a command needs `cmd` (an argument list, or a string for sh -c)".into(), Map::new()),
                };
                c.args(&args).current_dir(&cwd).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).stdin(std::process::Stdio::null());
                std::os::unix::process::CommandExt::process_group(&mut c, 0);   // the child leads its own group: a timeout or a stop ends the whole tree
                // the values handed over are SECRETS: whatever the child prints of them (a 401 page, a debug line) is masked before it reaches a chunk, a receipt or any row
                let mut secrets: Vec<String> = Vec::new();
                for (k, var) in &env_from {
                    match std::env::var(var) { Ok(v) => { if v.chars().count() >= MIN_SECRET_CHARS { secrets.push(v.clone()); } c.env(k, v); } Err(_) => return receipt(&id, 2, format!("environment variable {} is not set (for {})", var, k), Map::new()) }
                }
                let mask = |t: String| -> String { secrets.iter().fold(t, |t, s| t.replace(s.as_str(), MASK)) };
                let epoch = || std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
                let (t_start, t_clock) = (epoch(), std::time::Instant::now());
                let child = match c.spawn() { Ok(ch) => ch, Err(e) => return receipt(&id, 127, format!("{}", e), Map::new()) };
                let pid = child.id() as i32;
                // THE WATCHDOG: at the deadline SIGTERM to the group, after the grace SIGKILL; it ends when the run does (the channel closes)
                let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
                let timed_out = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
                let watchdog = { let flag = timed_out.clone(); std::thread::spawn(move || {
                    if let Err(std::sync::mpsc::RecvTimeoutError::Timeout) = done_rx.recv_timeout(std::time::Duration::from_secs_f64(timeout_s.min(3.0e8))) {
                        flag.store(true, std::sync::atomic::Ordering::SeqCst);
                        unsafe { libc::kill(-pid, libc::SIGTERM); }
                        if let Err(std::sync::mpsc::RecvTimeoutError::Timeout) = done_rx.recv_timeout(std::time::Duration::from_millis(KILL_GRACE_MS)) { unsafe { libc::kill(-pid, libc::SIGKILL); } }
                    }
                }) };
                let slot = crate::store::child_slot(sh, eid);
                let (mut out_r, mut err_r) = {
                    let mut g = slot.lock().unwrap_or_else(|e| e.into_inner());
                    *g = Some(child);
                    let ch = g.as_mut().unwrap();
                    (ch.stdout.take(), ch.stderr.take())
                };
                let err_t = std::thread::spawn(move || { let mut s = String::new(); if let Some(r) = err_r.as_mut() { let _ = std::io::Read::read_to_string(r, &mut s); } s });
                // `partial: true`: what each read brings, cut at its last newline, is pushed as an outside birth `Chunk:<command>:<n>` ({cmd, id, n,
                // data}) while the process runs; the unterminated tail goes at the end. The receipt below still carries the whole output.
                let partial = req2.get("partial").and_then(|p| p.as_bool()).unwrap_or(false);
                let h = Store::from_shared(sh);
                let (mut all, mut held, mut n): (Vec<u8>, Vec<u8>, i64) = (Vec::new(), Vec::new(), 0);
                let push = |bytes: &[u8], n: &mut i64| {
                    *n += 1;
                    let mut p = Map::new();
                    p.insert("cmd".into(), json!(eid)); p.insert("id".into(), id.clone()); p.insert("n".into(), json!(*n));
                    p.insert("data".into(), json!(mask(String::from_utf8_lossy(bytes).to_string())));
                    h.push_inbound(format!("Chunk:{eid}:{n}"), "Chunk".into(), p);
                };
                if let Some(r) = out_r.as_mut() {
                    let mut buf = [0u8; 16384];
                    loop {
                        match std::io::Read::read(r, &mut buf) {
                            Ok(0) | Err(_) => break,
                            Ok(k) => {
                                all.extend_from_slice(&buf[..k]);
                                if partial {
                                    held.extend_from_slice(&buf[..k]);
                                    if let Some(i) = cut_at(&held, &secrets) { let rest = held.split_off(i + 1); push(&held, &mut n); held = rest; }
                                }
                            }
                        }
                    }
                }
                if partial && !held.is_empty() { push(&held, &mut n); }
                let out = mask(String::from_utf8_lossy(&all).to_string());
                let err = mask(err_t.join().unwrap_or_default());
                let status = slot.lock().unwrap_or_else(|e| e.into_inner()).as_mut().map(|ch| ch.wait());
                crate::store::child_done(sh, eid);
                drop(done_tx);
                let _ = watchdog.join();
                let killed = timed_out.load(std::sync::atomic::Ordering::SeqCst);
                let rc = if killed { TIMEOUT_RC } else { match status { Some(Ok(s)) => s.code().map(|c| c as i64).unwrap_or(-1), _ => -1 } };
                let mut extra = Map::new();
                extra.insert("timeout_s".into(), num_json(timeout_s));
                if killed { extra.insert("killed".into(), json!(true)); }
                extra.insert("stdout".into(), json!(out));
                extra.insert("stderr".into(), json!(err));
                let round3 = |x: f64| (x * 1000.0).round() / 1000.0;
                extra.insert("started".into(), json!(round3(t_start)));       // epoch seconds: reruns with the same output are told apart by them
                extra.insert("finished".into(), json!(round3(epoch())));
                extra.insert("ms".into(), json!(t_clock.elapsed().as_millis() as u64));
                extra.insert("stdout_bytes".into(), json!(out.len()));
                extra.insert("stderr_bytes".into(), json!(err.len()));
                let head = if out.trim().is_empty() { err.clone() } else { out.clone() };
                let mut rec = receipt(&id, rc, head, extra);
                if killed { rec["status"] = json!("timeout"); }
                rec
            }));
        }
        ("File", v) => {
            let Some(path) = path else {
                let id2 = id.clone();
                st.effect_limited(eid, verb, Box::new(move |_, _| receipt(&id2, 2, "a File needs `path`".into(), Map::new())));
                return;
            };
            let to = req.get("to").and_then(|t| t.as_str()).map(|t| resolve(&dir, t));
            if let Some(bad) = [Some(path.clone()), to].into_iter().flatten().find(|p| ephemeral(p, &dir)) {
                let (id2, why) = (id.clone(), ephemeral_why(&bad));
                st.effect_limited(eid, verb, Box::new(move |_, _| receipt(&id2, 2, why.clone(), Map::new())));
                return;
            }
            let req = req.clone();
            let v = v.to_string();
            st.effect_limited(eid, verb, Box::new(move |_, _| {
                let r: Result<String, String> = match v.as_str() {
                    "write" => {
                        let content = req.get("content").map(|c| c.as_str().map(|s| s.to_string()).unwrap_or_else(|| c.to_string())).unwrap_or_default();
                        let append = req.get("append").and_then(|a| a.as_bool()).unwrap_or(false);
                        (|| {
                            if let Some(d) = path.parent() { std::fs::create_dir_all(d)?; }
                            if append { use std::io::Write; std::fs::OpenOptions::new().create(true).append(true).open(&path)?.write_all(content.as_bytes())?; }
                            else { std::fs::write(&path, content.as_bytes())?; }
                            Ok::<_, std::io::Error>(format!("wrote {} bytes to {}", content.len(), path.display()))
                        })().map_err(|e| format!("{}: {}", path.display(), e))
                    }
                    "move" => {
                        let to = req.get("to").and_then(|t| t.as_str()).map(|t| resolve(&dir, t));
                        match to { Some(to) => std::fs::rename(&path, &to).map(|_| format!("moved to {}", to.display())).map_err(|e| e.to_string()), None => Err("move needs `to`".into()) }
                    }
                    _ => {
                        let m = std::fs::metadata(&path);
                        match m { Ok(m) if m.is_dir() => std::fs::remove_dir(&path), _ => std::fs::remove_file(&path) }
                            .map(|_| format!("deleted {}", path.display())).map_err(|e| format!("{}: {}", path.display(), e))
                    }
                };
                match r { Ok(h) => receipt(&id, 0, h, Map::new()), Err(e) => receipt(&id, 1, e, Map::new()) }
            }));
        }
        _ => {}
    }
}
