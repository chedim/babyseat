//! #994 row 7: the seed packs (membrane/seedpack.py, #982) as FIELD DECLARATIONS, native VM only. DATA MOVES ONLY:
//! a pack is a JSON file ({name, version, entities:[{eid, cls, properties}]}) shipped in native/packs/, and loading it
//! is one generic step that declares each entity into the core. The recall dials no longer live
//! as Rust literals; no new WHEN/WHERE/WRITE is added. The pack ledger, adoption and overlays stay in seedpack.py.
//! Parity: tests/packs_994.rs checks the whole-field checksum of a pack load against the old hand-coded load.
use serde_json::{Map, Value as J};

use crate::vm::Core;

pub const RECALL_POLICY: &str = include_str!("../packs/recall-policy.json");

/// every shipped pack, in load order
pub fn shipped() -> Vec<&'static str> { vec![RECALL_POLICY] }

/// declare a pack's entities into the core; returns how many. A malformed pack declares nothing (Err).
pub fn load_pack(core: &mut Core, text: &str) -> Result<usize, String> {
    let p: J = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let ents = p.get("entities").and_then(|e| e.as_array()).ok_or("pack has no entities")?;
    let mut decl: Vec<(String, String, Map<String, J>)> = vec![];
    for e in ents {
        let eid = e.get("eid").and_then(|x| x.as_str()).ok_or("entity without eid")?;
        let cls = e.get("cls").and_then(|x| x.as_str()).ok_or("entity without cls")?;
        let mut props = e.get("properties").and_then(|x| x.as_object()).cloned().unwrap_or_default();
        // pack-ledger metadata (seedpack.py's adoption flags) is not core data; the dials' label is the pack's name
        for k in ["silent_birth", "version"] { props.remove(k); }
        if cls == crate::recall::CLASS_DIALS { props.remove("name"); }
        decl.push((eid.into(), cls.into(), props));
    }
    let n = decl.len();
    for (eid, cls, props) in decl { core.load(eid, cls, props, None, 0.0); }
    Ok(n)
}

pub fn load_named(core: &mut Core, text: &str) -> usize { load_pack(core, text).expect("shipped pack parses") }

// ---- #994 row 8: entity scripts (night.py clock tick, autonode.py filetree watch, chat_io.py / concept-drift getters) ----
// The script TEXT is data in native/packs/scripts.json: sensors (tick_script, watch_script) and read-through getters live
// on the entities that own them, or on a ScriptTemplate entity the planter copies onto the target. Loading is the same
// generic pack step; nothing new is a Rust literal. Python's seed_* functions stay the idempotent boot path (an edited
// script survives); tests/scripts_994_r8.rs checks the shipped text byte-for-byte against the Python constants.
pub const SCRIPTS: &str = include_str!("../packs/scripts.json");

/// every shipped pack including the entity-script pack
pub fn shipped_all() -> Vec<&'static str> { vec![RECALL_POLICY, SCRIPTS] }

/// an entity's script of one kind ("tick_script" | "watch_script" | "getter_script"), as the field holds it
pub fn script_of(core: &Core, eid: &str, kind: &str) -> Option<String> {
    core.docs.get(eid)?.props.get(kind)?.as_str().map(|s| s.to_string())
}
