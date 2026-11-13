//! cliffvm's command line (docs/cliffvm.md). NO app logic: the arguments become Boot facts (the ones fields_live.seat_facts tells,
//! membrane/packs/facts.json), and the apps are the program: the FIRST argument is the root of trust, later ones are layers above it. The barest seat has no security (operator, 2026-11-12): there is no
//! secret and no token; `--bind` only says which address the door opens on.
use std::sync::atomic::AtomicBool;

use serde_json::{json, Map, Value as J};

use crate::host::{self, Host, Options};
use crate::plan::{self, Outcome};

pub const USAGE: &str = "usage: cliffvm --version | cliffvm run <app> [<app>...] [--store <sqlite>] [--port <n>] [--bind <addr>] [--ui-dir <dir>] [--fact <name>=<value>]...";

#[derive(Debug, PartialEq)]
pub struct RunArgs { pub apps: Vec<String>, pub store: String, pub port: Option<u16>, pub bind: Option<String>, pub ui_dir: Option<String>, pub facts: Vec<(String, J)> }

#[derive(Debug, PartialEq)]
pub enum Cmd { Version, Help, Run(RunArgs) }

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
    h.load_stored_packs();                       // the model's own packs, written in an earlier run (Pack:<name>.text)
    h.rebuild();
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
    host::boot_started(&h, &facts(r), true);
    if let Some(steps) = &steps {
        let _ = plan::run(h.engine(), &host::NullHost, steps, true, &mut |_| Outcome::Skipped);   // the UI is up: the background steps
    }
    let mut up = false;
    let mut code = 0;
    h.run(stop, 1.0, &mut |h| {
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
