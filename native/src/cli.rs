//! cliffvm's command line (docs/cliffvm.md). NO app logic: the arguments become Boot facts (the ones fields_live.seat_facts tells,
//! membrane/packs/facts.json), and the apps are the program: the FIRST argument is the root of trust, later ones are layers above it. The barest seat has no security (operator, 2026-11-12): there is no
//! secret and no token unless the launcher names `door_secret_file`; `--bind` only says which address the door opens on.
use std::sync::atomic::AtomicBool;

use serde_json::{json, Map, Value as J};

use crate::host::{self, Host, Options};
use crate::plan::{self, Outcome};

pub const USAGE: &str = "usage: cliffvm --version | cliffvm serve [--port <n>] [--data-dir <dir>] [--bind <addr>] [--fact <name>=<value>]... (cliffvm serve --help) | cliffvm run <app> [<app>...] [--store <sqlite>] [--port <n>] [--bind <addr>] [--ui-dir <dir>] [--fact <name>=<value>]... | cliffvm token mint --sub <id> --ttl <30d|12h|15m|45s|seconds> --secret-file <path> [--name <name>]";

#[derive(Debug, PartialEq)]
pub struct RunArgs { pub apps: Vec<String>, pub store: String, pub port: Option<u16>, pub bind: Option<String>, pub ui_dir: Option<String>, pub facts: Vec<(String, J)> }

/// `token mint`: a signed token for a person (the released `membrane token mint`, jwtauth.py `cli_main`). The secret file is the door's own and must exist (it is never made here);
/// the ttl is required and has no default; no name = the sub is the name.
#[derive(Debug, PartialEq)]
pub struct MintArgs { pub sub: String, pub name: Option<String>, pub ttl_s: f64, pub secret_file: String }

#[derive(Debug, PartialEq)]
pub enum Cmd { Version, Help, ServeHelp, Serve(ServeArgs), Run(RunArgs), Mint(MintArgs) }

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
        Some("serve") => {
            let mut s = ServeArgs::default();
            while let Some(a) = it.next() {
                let mut val = |n: &str| it.next().cloned().ok_or_else(|| format!("{n} needs a value"));
                match a.as_str() {
                    "--help" | "-h" => return Ok(Cmd::ServeHelp),
                    "--port" => s.port = Some(val("--port")?.parse().map_err(|_| "--port: a number 0-65535 (0 = any free port)".to_string())?),
                    "--bind" => s.bind = Some(val("--bind")?),
                    "--data-dir" => s.data_dir = Some(val("--data-dir")?),
                    "--store" => s.store = Some(val("--store")?),
                    "--root" => s.root = Some(val("--root")?),
                    "--legacy" => s.legacy = Some(val("--legacy")?),
                    "--fact" => s.facts.push(fact_arg(&val("--fact")?)?),
                    x if x.starts_with("--") => return Err(format!("serve: unknown option {x}")),
                    x => return Err(format!("serve: unexpected argument {x} (serve takes options only)")),
                }
            }
            Ok(Cmd::Serve(s))
        }
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
    for (k, v) in &r.facts { m.insert(k.clone(), v.clone()); }   // the facts the host itself owns (below) win; the seat defaults give way
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
    // the seat's own defaults: a fact the launch names (`--fact seat_name=<name>`) wins over them
    for (k, v) in [("seat_name", json!("Bob")), ("seat_named", json!(false)), ("seat_aliases", json!([])), ("seat_voice_out", json!(false)), ("seat_language", json!("en")), ("seat_blurp", json!(false))] { m.entry(k.to_string()).or_insert(v); }
    m
}

fn absolute(p: &str) -> String {
    std::fs::canonicalize(p).map(|x| x.to_string_lossy().to_string()).unwrap_or_else(|_| p.to_string())
}

/// `run`: the app is the folder. Load its packs, compute the getters once, tell the facts and `started`, then loop until `stop`.
/// Prints `cliffvm: door listening on <host>:<port>` once the door is up. -> the process's exit code.
pub fn run(r: &RunArgs, stop: &AtomicBool) -> i32 { run_with(r, stop, &|_, _| {}) }

/// `run`, and `on_up(host, port)` once the door is listening (after its line is printed).
pub fn run_with(r: &RunArgs, stop: &AtomicBool, on_up: &dyn Fn(&str, u64)) -> i32 {
    // the admin's token (admin.rs): with a door secret, the secret is made if missing and a fresh admin token is written at every boot; before any thread starts
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    let admin_facts = match crate::admin::boot(&r.facts, now) { Ok(m) => m, Err(e) => { eprintln!("cliffvm: admin token: {e}"); return 2; } };
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
    let fact = |n: &str| r.facts.iter().find(|(k, _)| k == n).map(|(_, v)| v);
    if let Some(n) = fact("effect_workers").and_then(|v| v.as_u64()) { h.set_effect_workers(n.max(1) as usize); }   // the cap on Command / File / Fetch effects running at once (default 4)
    if let Some(n) = fact("command_timeout_s").and_then(|v| v.as_f64()) { h.set_command_timeout(n); }   // the limit of a Command run that states no timeout_s (default 120 s)
    if let Some(n) = fact("warm_rows").and_then(|v| v.as_u64()) { h.set_warm_rows(n as usize); }   // how many lazy rows the warm boot loads in the background (default host::DEFAULT_WARM_ROWS; 0 = none)
    // the memory policy (docs/cliffvm.md, "Unload"): the budget, the fractions and the batch are the operator's boot facts; a fact left unnamed gets the launcher's default (10% of the machine's memory, a batch of 200, a hard line at 90%), see host.rs `MemoryConfig::with_defaults`
    let mem = host::MemoryConfig { budget_mb: fact("memory_budget_mb").and_then(|v| v.as_u64()), soft: fact("unload_soft").and_then(|v| v.as_f64()), hard: fact("unload_hard").and_then(|v| v.as_f64()), batch: fact("unload_batch").and_then(|v| v.as_u64()) }.with_defaults(host::physical_memory_bytes());
    h.set_memory(mem.clone());
    // TEST ONLY: MEMBRANE_TEST_RSS names a file that holds the resident size in bytes; the policy reads it at every drain instead of the process's own (unreadable: the real one)
    if let Ok(path) = std::env::var("MEMBRANE_TEST_RSS") { h.set_rss_reading(Box::new(move || std::fs::read_to_string(&path).ok().and_then(|t| t.trim().parse().ok()).or_else(host::process_rss))); }
    h.load_stored_packs();                       // the model's own packs, written in an earlier run (Pack:<name>.text)
    h.load_stored_entities();                    // then every other entity the model wrote (banner lines, trips, messages), minus what a getter computed
    h.rebuild();
    // the eval accounting (stats.rs): on unless `--fact eval_stats=false`; `Stats:eval` is told every `eval_stats_window_s` (default stats::DEFAULT_WINDOW_S); needs the shipped pack `stats`
    let has_pack = h.engine().lock().unwrap_or_else(|e| e.into_inner()).1.has_pack("stats");   // the pack that declares the class
    if has_pack && fact("eval_stats") != Some(&json!(false)) { h.enable_stats(fact("eval_stats_window_s").and_then(|v| v.as_f64()).filter(|w| *w > 0.0).unwrap_or(crate::stats::DEFAULT_WINDOW_S)); }
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
    told.extend(admin_facts);
    // the memory policy the seat runs with (the launcher's default for what the operator left unnamed) is told too, so the settings window shows the real values
    for (k, v) in [("memory_budget_mb", mem.budget_mb.map(|n| json!(n))), ("unload_soft", mem.soft.map(|n| json!(n))), ("unload_hard", mem.hard.map(|n| json!(n))), ("unload_batch", mem.batch.map(|n| json!(n)))] {
        if let Some(v) = v { told.entry(k.to_string()).or_insert(v); }
    }
    for m in host::boot_environment(&h, &told, &std::env::vars().collect::<Vec<_>>()) { eprintln!("cliffvm: {m}"); }   // the environment as an entity (host.rs), before `started` so a setter on it finds it
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
            on_up(res["host"].as_str().unwrap_or("127.0.0.1"), res["port"].as_u64().unwrap_or(0));
        } else { eprintln!("cliffvm: door: {}", res["head"]); code = 2; }
    });
    code
}

// ---- `serve`: the one-command launcher (docs/packaging-plan.md, decided 2026-11-14) ----
// It only FINDS things (the dist folder, the data folder, the claude CLI) and runs the barest seat there. Every app default (the minds, the models, the local backend, the
// default port) is in the pack `packs/launcher.c1f`, not here; what is here is where files are.
pub const SERVE_USAGE: &str = "\
usage: cliffvm serve [--port <n>] [--data-dir <dir>] [--bind <addr>] [--store <sqlite>] [--root <dist folder>] [--legacy <path>] [--fact <name>=<value>]...

Starts the barest seat: the packs listed in <root>/apps/barest.packs, and the app <root>/apps/barest.c1f (the page is its ui/ folder), with the claude CLI as the only model.
  --port <n>         the door's port (0 = any free port). Default: the one launcher.c1f says (4096).
  --bind <addr>      the address the door opens on. Default 127.0.0.1 (this machine only); 0.0.0.0 opens it to the network.
  --data-dir <dir>   the seat's data: its store (seat.sqlite), the door's secret (door.secret, made at the first run, mode 0600) and the admin token (admin.token, new at every start, mode 0600).
                     Default: $XDG_DATA_HOME/membrane, else ~/Library/Application Support/membrane (macOS) or ~/.local/share/membrane.
  --store <sqlite>   the store file, instead of <data-dir>/seat.sqlite.
  --root <dir>       the dist folder (has packs/ apps/). Default: the folder above the one this binary is in (bin/cliffvm); the repo's membrane/ folder has the same shape.
  --legacy <path>     where the old Python seat's store is (a file, or a folder that holds it): told as the fact legacy_path, which the cutover pack (packs/legacy_cutover.c1f) imports from.
                     Default: the old variable MEMBRANE_DATA_DIR when set (that seat's data dir), else the pack searches its own places.
  --fact <name>=<value>   a Boot fact, after the defaults so it wins (repeatable): JSON when the value parses, else text. Defaults: packs/launcher.c1f.

The chat answers through the claude CLI: CLAUDE_BIN names it, else `claude` on PATH. It authenticates with ANTHROPIC_API_KEY from this environment (the default);
the CLI's own login is an opt-in: --fact chat_auth=login. A local tool presents the admin token: curl -H \"Authorization: Bearer $(cat <data-dir>/admin.token)\" <url>/global/health";

#[derive(Debug, PartialEq, Default)]
pub struct ServeArgs { pub port: Option<u16>, pub bind: Option<String>, pub data_dir: Option<String>, pub store: Option<String>, pub root: Option<String>, pub legacy: Option<String>, pub facts: Vec<(String, J)> }

fn fact_arg(v: &str) -> Result<(String, J), String> {
    let (k, x) = v.split_once('=').filter(|(k, _)| !k.is_empty()).ok_or_else(|| "--fact: name=value".to_string())?;
    Ok((k.to_string(), serde_json::from_str(x).unwrap_or_else(|_| json!(x))))
}

/// The user's data folder for the seat: `$XDG_DATA_HOME/membrane` when set, else `~/Library/Application Support/membrane` on macOS, else `~/.local/share/membrane`.
pub fn default_data_dir(home: &str, xdg: Option<&str>, macos: bool) -> String {
    match xdg.filter(|x| !x.is_empty()) {
        Some(x) => format!("{x}/membrane"),
        None if macos => format!("{home}/Library/Application Support/membrane"),
        None => format!("{home}/.local/share/membrane"),
    }
}

/// The dist folder: `--root`, else the folder above the one this binary is in (`<root>/bin/cliffvm`). It must hold `packs/` and `apps/barest.packs`.
pub fn find_root(root: Option<&str>, exe: &std::path::Path) -> Result<std::path::PathBuf, String> {
    let dir = match root {
        Some(r) => std::path::PathBuf::from(r),
        None => std::fs::canonicalize(exe).unwrap_or_else(|_| exe.to_path_buf()).parent().and_then(|b| b.parent()).map(|p| p.to_path_buf()).unwrap_or_default(),
    };
    if dir.join("packs").is_dir() && dir.join("apps/barest.packs").is_file() { return Ok(std::fs::canonicalize(&dir).unwrap_or(dir)); }
    Err(format!("no packs folder at {} (looked for {} and {}). Install the whole dist folder (bin/ packs/ apps/ together) or name it with --root <dir>.",
        dir.display(), dir.join("packs").display(), dir.join("apps/barest.packs").display()))
}

/// What `serve` made of its arguments and where it looked: the `run` arguments, and the lines to tell.
pub struct ServePlan { pub run: RunArgs, pub data: String, pub notes: Vec<String> }

/// `serve` -> the `run` it stands for. `env` reads an environment variable (a test passes its own);
pub fn serve_plan(s: &ServeArgs, exe: &std::path::Path, env: &dyn Fn(&str) -> Option<String>) -> Result<ServePlan, String> {
    let root = find_root(s.root.as_deref(), exe)?;
    let data = match &s.data_dir {
        Some(d) => d.clone(),
        None => default_data_dir(&env("HOME").ok_or("no HOME in the environment: name the data folder with --data-dir")?, env("XDG_DATA_HOME").as_deref(), cfg!(target_os = "macos")),
    };
    std::fs::create_dir_all(&data).map_err(|e| format!("{data}: {e}"))?;
    let data = absolute(&data);
    let store = s.store.clone().unwrap_or_else(|| format!("{data}/seat.sqlite"));
    if let Some(d) = std::path::Path::new(&store).parent().filter(|d| !d.as_os_str().is_empty()) { std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?; }
    // the packs are the barest seat's own, one name per line in apps/barest.packs (load order; the first is the root of trust), then the app folder
    let list = std::fs::read_to_string(root.join("apps/barest.packs")).map_err(|e| format!("apps/barest.packs: {e}"))?;
    let mut apps: Vec<String> = vec![];
    for name in list.lines().map(|l| l.trim()).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        let p = root.join("packs").join(format!("{name}.c1f"));
        if !p.is_file() { return Err(format!("apps/barest.packs names the pack {name}, and {} is not there", p.display())); }
        apps.push(p.to_string_lossy().to_string());
    }
    // the defaults pack (launcher.c1f): after the list, so `cliffvm run` on barest.packs alone stays a seat with no defaults
    let launcher = root.join("packs/launcher.c1f");
    if !launcher.is_file() { return Err(format!("{} is not there (the pack that holds the defaults)", launcher.display())); }
    apps.push(launcher.to_string_lossy().to_string());
    let app = root.join("apps/barest.c1f");
    if !app.exists() { return Err(format!("{} is not there", app.display())); }
    apps.push(app.to_string_lossy().to_string());
    let mut notes = vec![];
    // the places and the claude CLI, told as facts BEFORE the operator's own (which win)
    let mut facts: Vec<(String, J)> = vec![("door_secret_file".into(), json!(format!("{data}/door.secret"))), ("seat_data_dir".into(), json!(data))];
    let claude = env("CLAUDE_BIN").filter(|c| !c.is_empty()).or_else(|| env("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join("claude")).find(|c| c.is_file()).map(|c| c.to_string_lossy().to_string())));
    match &claude {
        Some(c) => { facts.push(("chat_claude_bin".into(), json!(c))); notes.push(format!("cliffvm serve: claude CLI: {c}")); }
        None => notes.push("cliffvm serve: no claude CLI found: the chat answers through it. Name it with CLAUDE_BIN=/path/to/claude, or put `claude` on PATH, then restart.".into()),
    }
    // the old Python seat's store: `--legacy <path>`, else the old variable MEMBRANE_DATA_DIR (that seat's data dir); c1f cannot read the environment, so it is told as the fact `legacy_path`
    // (packs/legacy_cutover.c1f searches the place; a named path that holds no store is its error)
    if let Some(l) = s.legacy.clone().or_else(|| env("MEMBRANE_DATA_DIR").filter(|v| !v.is_empty())) { facts.push(("legacy_path".into(), json!(l))); }
    facts.extend(s.facts.iter().cloned());
    let login = facts.iter().rev().find(|(k, _)| k == "chat_auth").map(|(_, v)| v == &json!("login")).unwrap_or(false);
    if !login && !env("ANTHROPIC_API_KEY").map_or(false, |k| !k.is_empty()) {
        notes.push("cliffvm serve: no ANTHROPIC_API_KEY in the environment: the chat refuses a message until you set it and restart (or opt into the claude CLI's login: --fact chat_auth=login).".into());
    }
    Ok(ServePlan { run: RunArgs { apps, store, port: s.port, bind: s.bind.clone(), ui_dir: None, facts }, data, notes })
}

/// `serve`: tell what was found, run the seat, and once the door is up tell the URL and how a local tool uses the admin token.
pub fn serve(s: &ServeArgs, exe: &std::path::Path, stop: &AtomicBool) -> i32 {
    let plan = match serve_plan(s, exe, &|k| std::env::var(k).ok()) { Ok(p) => p, Err(e) => { eprintln!("cliffvm serve: {e}"); return 2; } };
    for n in &plan.notes { println!("{n}"); }
    println!("cliffvm serve: data {} (the door's secret and the admin token are made here)", plan.data);
    let token = format!("{}/admin.token", plan.data);
    run_with(&plan.run, stop, &|host, port| {
        let url = format!("http://{host}:{port}/");
        println!("cliffvm serve: open {url}");
        println!("cliffvm serve: a localhost peer is not the admin: a local tool sends the admin token, written (mode 600, new at every start) to {token}");
        println!("cliffvm serve: curl -H \"Authorization: Bearer $(cat {token})\" {url}global/health");
        use std::io::Write; let _ = std::io::stdout().flush();
    })
}
