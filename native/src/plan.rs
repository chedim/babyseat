//! THE BOOT PLAN, native (docs/python-to-c1f.md, piece 1): the boot's order and policy are data -- `packs/boot.json` (the
//! steps, their edges, which one stops the boot, which run in the background once the UI is up) and `packs/boot.c1f`
//! (when a step is ready) -- on the field engine. This file only walks it: ask the field which step is ready, run it
//! through the bridge, write its state back (running, done, failed + its error, skipped). No Python decides anything.
use std::sync::Mutex;

use serde_json::{json, Map, Value as J};

use crate::fields::Fields;
use crate::vm::{Core, Host, Write};

pub type Engine = (Core, Fields, i64);

pub fn eid(name: &str) -> String { format!("boot:{name}") }

#[derive(Clone, Debug)]
pub struct Step { pub name: String, pub essential: bool, pub background: bool, pub blocks: Vec<String> }

/// What running a step came to. `Fatal` ends the boot (an essential step failed, or the host says so).
pub enum Outcome { Done, Skipped, Failed(String), Fatal(String) }

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub fn parse(src: &str) -> Result<Vec<Step>, String> {
    let v: J = serde_json::from_str(src).map_err(|e| format!("boot plan: {e}"))?;
    let rows = v.as_array().ok_or("boot plan: a list of steps")?;
    rows.iter().map(|r| {
        let name = r.get("name").and_then(|n| n.as_str()).ok_or("boot plan: a step has no name")?.to_string();
        let flag = |k: &str| r.get(k).and_then(|b| b.as_bool()).unwrap_or(false);
        let blocks = r.get("blocks").and_then(|b| b.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
        Ok(Step { name, essential: flag("essential"), background: flag("background"), blocks })
    }).collect()
}

/// Load the pack and the steps into the engine; every step starts `pending`.
pub fn load(e: &mut Engine, host: &dyn Host, pack: &str, steps: &[Step]) -> Result<(), String> {
    let (core, f, seq) = e;
    f.set_pack("boot", pack)?;
    for s in steps {
        let mut p = Map::new();
        p.insert("name".into(), json!(s.name));
        p.insert("state".into(), json!("pending"));
        p.insert("essential".into(), json!(s.essential));
        p.insert("background".into(), json!(s.background));
        p.insert("blocks".into(), json!(s.blocks.iter().map(|b| eid(b)).collect::<Vec<_>>()));
        core.load(eid(&s.name), "Step".into(), p, None, 0.0);
    }
    let mut q = *seq;
    f.settle(core, host, now(), &mut q);
    *seq = q;
    Ok(())
}

fn prop(core: &Core, name: &str, key: &str) -> J {
    core.docs.get(&eid(name)).and_then(|d| d.props.get(key).cloned()).unwrap_or(J::Null)
}

pub fn state(core: &Core, name: &str) -> String { prop(core, name, "state").as_str().unwrap_or("").to_string() }
pub fn error(core: &Core, name: &str) -> String { prop(core, name, "error").as_str().unwrap_or("").to_string() }

/// The first step, in the plan's order, that the field says is ready (and is, or isn't, a background step).
pub fn next(core: &Core, steps: &[Step], background: bool) -> Option<String> {
    steps.iter().find(|s| s.background == background && prop(core, &s.name, "ready") == J::Bool(true)).map(|s| s.name.clone())
}

pub fn set(e: &mut Engine, host: &dyn Host, name: &str, key: &str, value: J) {
    let (core, f, seq) = e;
    let t = now();
    *seq += 1;
    let old = prop(core, name, key);
    let w = Write { eid: eid(name), prop: key.to_string(), new: value, old, writer: "boot".into(), chain: json!({}), seq: *seq };
    core.hear(w.clone(), None, t);
    let mut s = *seq;
    f.react(core, host, w, t, &mut s);
    f.settle(core, host, t, &mut s);
    *seq = s;
}

/// Run the plan's foreground steps (`background` false) or its background ones, in the field's order. The engine's lock
/// is never held while a step runs. `bridge(name)` runs one step. -> Err(message) when the boot must stop.
pub fn run(engine: &Mutex<Engine>, host: &dyn Host, steps: &[Step], background: bool,
           bridge: &mut dyn FnMut(&str) -> Outcome) -> Result<(), String> {
    let lock = || engine.lock().unwrap_or_else(|e| e.into_inner());
    loop {
        let name = match next(&lock().0, steps, background) { Some(n) => n, None => return Ok(()) };
        set(&mut lock(), host, &name, "state", json!("running"));
        let out = bridge(&name);
        let mut g = lock();
        match out {
            Outcome::Done => set(&mut g, host, &name, "state", json!("done")),
            Outcome::Skipped => set(&mut g, host, &name, "state", json!("skipped")),
            Outcome::Failed(m) => {
                set(&mut g, host, &name, "error", json!(m));
                set(&mut g, host, &name, "state", json!("failed"));
                if steps.iter().any(|s| s.name == name && s.essential && !background) { return Err(format!("{name}: {m}")); }
            }
            Outcome::Fatal(m) => {
                set(&mut g, host, &name, "error", json!(m));
                set(&mut g, host, &name, "state", json!("failed"));
                return Err(format!("{name}: {m}"));
            }
        }
    }
}

/// {name: {state, error}} for every step.
pub fn report(core: &Core, steps: &[Step]) -> J {
    let mut m = Map::new();
    for s in steps { m.insert(s.name.clone(), json!({"state": state(core, &s.name), "error": error(core, &s.name)})); }
    J::Object(m)
}
