//! cliffvm's command line (docs/cliffvm.md). NO app logic: the arguments become Boot facts (the ones fields_live.seat_facts tells,
//! membrane/packs/facts.json), and the apps are the program: the FIRST argument is the root of trust, later ones are layers above it. The barest seat has no security (operator, 2026-11-12): there is no
//! secret and no token unless the launcher names `door_secret_file`; `--bind` only says which address the door opens on.
use std::sync::atomic::AtomicBool;

use serde_json::{json, Map, Value as J};

use crate::host::{self, Host, Options};
use crate::plan::{self, Outcome};

pub const USAGE: &str = "usage: cliffvm --version | cliffvm run <app> [<app>...] [--store <sqlite>] [--port <n>] [--bind <addr>] [--ui-dir <dir>] [--fact <name>=<value>]... | cliffvm token mint --sub <id> --ttl <30d|12h|15m|45s|seconds> --secret-file <path> [--name <name>]";

#[derive(Debug, PartialEq)]
pub struct RunArgs { pub apps: Vec<String>, pub store: String, pub port: Option<u16>, pub bind: Option<String>, pub ui_dir: Option<String>, pub facts: Vec<(String, J)> }

/// `token mint`: a signed token for a person (the released `membrane token mint`, jwtauth.py `cli_main`). The secret file is the door's own and must exist (it is never made here);
/// the ttl is required and has no default; no name = the sub is the name.
#[derive(Debug, PartialEq)]
pub struct MintArgs { pub sub: String, pub name: Option<String>, pub ttl_s: f64, pub secret_file: String }

#[derive(Debug, PartialEq)]
pub enum Cmd { Version, Help, Run(RunArgs), Mint(MintArgs) }

/// The released ttl spelling: `30d`, `12h`, `15m`, `45s` or plain seconds, a positive whole number.
pub fn parse_ttl(s: &str) -> Result<f64, String> {
    let t = s.trim();
    let (digits, unit) = match t.chars().last() { Some(c) if "smhd".contains(c) => (&t[..t.len() - 1], c), _ => (t, 's') };
    let n: u64 = digits.trim().parse().ok().filter(|n| *n > 0).ok_or_else(|| format!("bad ttl {s:?}: use e.g. 30d, 12h, 15m, 45s or seconds"))?;
    Ok((n * match unit { 'm' => 60, 'h' => 3600, 'd' => 86400, _ => 1 }) as f64)
}

/// `token mint` at `now`: -> the token to print.
pub fn mint(m: &MintArgs, now: f64) -> Result<String, String> {
    if m.sub.starts_with("seat:") { return Err("'seat:' ids are the seat's own (reserved)".into()); }
    let raw = std::fs::read_to_string(&m.secret_file).map_err(|e| format!("{}: {e}", m.secret_file))?;
    crate::jwt::mint(raw.trim().as_bytes(), &m.sub, m.name.as_deref().unwrap_or(&m.sub), m.ttl_s, now)
}

pub fn parse(args: &[String]) -> Result<Cmd, String> {
    let mut it = args.iter();
    match it.next().map(|s| s.as_str()) {
        None | Some("--help") | Some("-h") | Some("help") => Ok(Cmd::Help),
        Some("--version") | Some("-V") | Some("version") => Ok(Cmd::Version),
        Some("run") => {
            let mut r = RunArgs { apps: vec![], store: "cliffvm.sqlite3".into(), port: None, bind: None, ui_dir: None, facts: vec![] };
            while let Some(a) = it.next() {
                let mut val = |n: &str| it.next().cloned().ok_or_else(|| format!("{n} needs a value"));
                match a.as_str() {
                    "--help" | "-h" => return Ok(Cmd::Help),
                    "--store" => r.store = val("--store")?,
                    "--port" => r.port = Some(val("--port")?.parse().map_err(|_| "--port: a number 0-65535 (0 = any free port)".to_string())?),
                    "--bind" => r.bind = Some(val("--bind")?),
                    "--ui-dir" => r.ui_dir = Some(val("--ui-dir")?),
                    // any Boot fact a pack reads: JSON when the value parses as JSON, else the text (the pack decides what it means)
                    "--fact" => {
                        let v = val("--fact")?;
                        let (k, x) = v.split_once('=').filter(|(k, _)| !k.is_empty()).ok_or_else(|| "--fact: name=value".to_string())?;
                        r.facts.push((k.to_string(), serde_json::from_str(x).unwrap_or_else(|_| json!(x))));
                    }
                    x if x.starts_with("--") => return Err(format!("unknown option {x}")),
                    x => r.apps.push(x.to_string()),
                }
            }
            if r.apps.is_empty() { return Err("run needs an app (a folder <name>.c1f or a pack file)".into()); }
            Ok(Cmd::Run(r))
        }
        Some("token") => {
            if it.next().map(|s| s.as_str()) != Some("mint") { return Err("token: mint is the one subcommand".into()); }
            let (mut sub, mut name, mut ttl, mut file) = (None, None, None, None);
            while let Some(a) = it.next() {
                let mut val = |n: &str| it.next().cloned().ok_or_else(|| format!("{n} needs a value"));
                match a.as_str() {
                    "--help" | "-h" => return Ok(Cmd::Help),
                    "--sub" => sub = Some(val("--sub")?),
                    "--name" => name = Some(val("--name")?),
                    "--ttl" => ttl = Some(parse_ttl(&val("--ttl")?)?),
                    "--secret-file" => file = Some(val("--secret-file")?),
                    x => return Err(format!("token mint: unknown option {x}")),
                }
            }
            Ok(Cmd::Mint(MintArgs {
                sub: sub.ok_or("token mint: --sub is required")?, name,
                ttl_s: ttl.ok_or("token mint: --ttl is required (no default)")?,
                secret_file: file.ok_or("token mint: --secret-file is required (the door's secret)")?,
            }))
        }
        Some(x) => Err(format!("unknown command {x}")),
    }
}

/// The facts the arguments tell (the names facts.json lists); a source that is missing leaves its fact out.
pub fn facts(r: &RunArgs) -> Map<String, J> {
    let mut m = Map::new();
    for (k, v) in &r.facts { m.insert(k.clone(), v.clone()); }   // the named facts below win
    if let Some(p) = r.port { m.insert("door_port".into(), json!(p)); }
    // the folders of the apps for the door's data files, the UPPER layer (the later argument) first; a pack file has no folder
    let dirs: Vec<String> = r.apps.iter().rev().filter(|a| std::path::Path::new(a).is_dir()).map(|a| absolute(a)).collect();
    if !dirs.is_empty() { m.insert("app_dirs".into(), json!(dirs)); }
    if let Some(u) = &r.ui_dir { m.insert("ui_dir".into(), json!(absolute(u))); }
    let ver = env!("CARGO_PKG_VERSION");
    m.insert("seat_version".into(), json!(ver));
    m.insert("build_id".into(), json!(format!("cliffvm-{}", ver)));
    if let Ok(d) = std::env::current_dir() { m.insert("seat_cwd".into(), json!(d.to_string_lossy())); }
    if let Ok(h) = std::env::var("HOME") { m.insert("seat_home".into(), json!(h)); }
    // whether the seat's environment holds a non-empty ANTHROPIC_API_KEY (the claude turn's key, membrane/packs/chat_turn.c1f): ONLY the yes or no is told, never the value
    m.insert("anthropic_key_set".into(), json!(std::env::var("ANTHROPIC_API_KEY").map(|k| !k.is_empty()).unwrap_or(false)));
    m.insert("seat_name".into(), json!("Bob"));
    m.insert("seat_named".into(), json!(false));
    m.insert("seat_aliases".into(), json!([]));
    m.insert("seat_voice_out".into(), json!(false));
    m.insert("seat_language".into(), json!("en"));
    m.insert("seat_blurp".into(), json!(false));
    m
}

fn absolute(p: &str) -> String {
    std::fs::canonicalize(p).map(|x| x.to_string_lossy().to_string()).unwrap_or_else(|_| p.to_string())
}

/// `run`: the app is the folder. Load its packs, compute the getters once, tell the facts and `started`, then loop until `stop`.
/// Prints `cliffvm: door listening on <host>:<port>` once the door is up. -> the process's exit code.
pub fn run(r: &RunArgs, stop: &AtomicBool) -> i32 {
    // the owner's token (owner.rs): with a door secret, the secret is made if missing and a fresh owner token is written at every boot; before any thread starts
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    let owner_facts = match crate::owner::boot(&r.facts, now) { Ok(m) => m, Err(e) => { eprintln!("cliffvm: owner token: {e}"); return 2; } };
    let mut o = Options::new(Some(r.store.clone()));
    o.plain = true;
    o.bind = r.bind.clone();
    let h = match Host::open(o) { Ok(h) => h, Err(e) => { eprintln!("cliffvm: {e}"); return 2; } };
    for (i, app) in r.apps.iter().enumerate() {                 // the first app is the root of trust; each later one a layer above
        match h.load_app(std::path::Path::new(app), i == 0) {
            Ok(l) => for m in l.msgs { eprintln!("cliffvm: {m}"); },
            Err(e) => { eprintln!("cliffvm: {e}"); return 2; }
        }
    }
    if let Some(n) = r.facts.iter().find(|(k, _)| k == "effect_workers").and_then(|(_, v)| v.as_u64()) { h.set_effect_workers(n.max(1) as usize); }   // the cap on Command / File / Fetch effects running at once (default 4)
    if let Some(n) = r.facts.iter().find(|(k, _)| k == "command_timeout_s").and_then(|(_, v)| v.as_f64()) { h.set_command_timeout(n); }   // the limit of a Command run that states no timeout_s (default 120 s)
    if let Some(n) = r.facts.iter().find(|(k, _)| k == "warm_rows").and_then(|(_, v)| v.as_u64()) { h.set_warm_rows(n as usize); }   // how many lazy rows the warm boot loads in the background (default host::DEFAULT_WARM_ROWS; 0 = none)
    // the memory sensor (docs/cliffvm.md, "Unload"): the budget and the fractions are the operator's boot facts, none has a default; the policy pack reads `unload_batch` itself
    let fact = |n: &str| r.facts.iter().find(|(k, _)| k == n).map(|(_, v)| v);
    h.set_memory(host::MemoryConfig { budget_mb: fact("memory_budget_mb").and_then(|v| v.as_u64()), soft: fact("unload_soft").and_then(|v| v.as_f64()), hard: fact("unload_hard").and_then(|v| v.as_f64()) });
    // TEST ONLY: MEMBRANE_TEST_RSS names a file that holds the resident size in bytes; the sensor reads it at every tick instead of the process's own (unreadable: the real one)
    if let Ok(path) = std::env::var("MEMBRANE_TEST_RSS") { h.set_rss_reading(Box::new(move || std::fs::read_to_string(&path).ok().and_then(|t| t.trim().parse().ok()).or_else(host::process_rss))); }
    h.load_stored_packs();                       // the model's own packs, written in an earlier run (Pack:<name>.text)
    h.load_stored_entities();                    // then every other entity the model wrote (banner lines, trips, messages), minus what a getter computed
    h.rebuild();
    // the eval accounting (stats.rs): on unless `--fact eval_stats=false`; `Stats:eval` is told every `eval_stats_window_s` (default stats::DEFAULT_WINDOW_S); needs the shipped pack `stats`
    let fact = |k: &str| r.facts.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone());
    let has_pack = h.engine().lock().unwrap_or_else(|e| e.into_inner()).1.has_pack("stats");   // the pack that declares the class
    if has_pack && fact("eval_stats") != Some(json!(false)) { h.enable_stats(fact("eval_stats_window_s").and_then(|v| v.as_f64()).filter(|w| *w > 0.0).unwrap_or(crate::stats::DEFAULT_WINDOW_S)); }
    // THE BOOT PLAN (plan.rs): boot.json's steps in the order boot.c1f says. This host runs none of them itself (they are the Python
    // seat's steps: nothing here to run), so each is `skipped`; what the plan decides is when the boot is over. Then `started`.
    // the plan lives with the first app: its folder, or the folder a pack file is in
    let first = std::path::Path::new(&r.apps[0]);
    let dir = if first.is_dir() { first } else { first.parent().unwrap_or(std::path::Path::new(".")) };
    let plan_src = std::fs::read_to_string(dir.join("boot.json")).ok();
    let steps = match &plan_src {
        Some(src) => match (plan::parse(src), std::fs::read_to_string(dir.join("boot.c1f"))) {
            (Ok(steps), Ok(pack)) => {
                if let Err(e) = plan::load(&mut h.engine().lock().unwrap_or_else(|e| e.into_inner()), &host::NullHost, &pack, &steps) { eprintln!("cliffvm: boot plan: {e}"); return 2; }
                Some(steps)
            }
            (Err(e), _) => { eprintln!("cliffvm: {e}"); return 2; }
            (_, Err(e)) => { eprintln!("cliffvm: boot plan: boot.c1f: {e}"); return 2; }
        },
        None => None,
    };
    if let Some(steps) = &steps {
        if let Err(e) = plan::run(h.engine(), &host::NullHost, steps, false, &mut |_| Outcome::Skipped) { eprintln!("cliffvm: boot stopped: {e}"); return 2; }
    }
    let mut told = facts(r);
    told.extend(owner_facts);
    host::boot_started(&h, &told, true);
    if let Some(steps) = &steps {
        let _ = plan::run(h.engine(), &host::NullHost, steps, true, &mut |_| Outcome::Skipped);   // the UI is up: the background steps
    }
    let mut up = false;
    let mut code = 0;
    #[cfg(feature = "alloc-stats")]
    let mut last_mem = std::time::Instant::now();
    h.run(stop, 1.0, &mut |h| {
        #[cfg(feature = "alloc-stats")]
        if last_mem.elapsed().as_secs() >= 2 { last_mem = std::time::Instant::now(); eprintln!("{}", h.mem_line()); }
        if up { return; }
        let res = h.get("Http:door", "listen_result");
        if res.is_null() { return; }
        up = true;
        if res["rc"] == json!(0) {
            println!("cliffvm: door listening on {}:{}", res["host"].as_str().unwrap_or("127.0.0.1"), res["port"]);
            use std::io::Write; let _ = std::io::stdout().flush();
        } else { eprintln!("cliffvm: door: {}", res["head"]); code = 2; }
    });
    code
}
