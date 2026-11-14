//! An idle seat evaluates no getter (#1143, finding of Throwy on the live seat: `Command.timeout_default` was evaluated ~90 times a second).
//! The cause: any pack load or removal (every `run_cliff` loads and removes a cliff pack) re-registered ALL getters and recomputed every getter of every
//! entity, though no input of theirs moved. Now a load recomputes only the getters it changed or added. One test: the eval table is the process's.
#![cfg(feature = "bin")]
use membrane_vm::host::{Host, Options};
use membrane_vm::stats;
use serde_json::json;

fn calls(prop: &str) -> u64 { stats::global_lifetime().iter().filter(|r| r.prop == prop).map(|r| r.calls).sum() }

const BASE: &str = "PACK base 1\nRESIDENT Boot\nRESIDENT Cmd\nCmd.timeout_default getter: ([b.command_timeout_s for b in instances('Boot') if b.command_timeout_s != None] or [120])[0]\n";

#[test]
fn idle_rounds_and_an_unrelated_pack_load_or_removal_evaluate_no_getter_again() {
    let mut o = Options::new(None); o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("base", BASE).unwrap();
    h.born("Boot:seat", "Boot", json!({"a": 1}).as_object().unwrap().clone(), "person");
    for i in 0..20 { h.born(&format!("Cmd:c{i}"), "Cmd", json!({"x": i}).as_object().unwrap().clone(), "person"); }
    h.rebuild();
    stats::set_on(true);
    stats::global_clear();
    h.tick();
    let first = calls("timeout_default");
    for _ in 0..30 { h.tick(); }
    assert_eq!(calls("timeout_default"), first, "idle rounds evaluate nothing");
    // an unrelated pack: a getter of another class; the 20 commands' getter reads nothing it touches
    h.load_pack("other", "PACK other 1\nOther.y getter: 1\n").unwrap();
    for _ in 0..3 { h.tick(); }
    assert_eq!(calls("timeout_default"), first, "loading an unrelated pack evaluated Cmd.timeout_default again");
    // its removal
    h.load_pack("other", "PACK other 1\n").unwrap();
    for _ in 0..3 { h.tick(); }
    assert_eq!(calls("timeout_default"), first, "removing an unrelated pack's getter evaluated Cmd.timeout_default again");
    // a real input change still wakes it
    h.write("Boot:seat", "command_timeout_s", json!(7), "person");
    for _ in 0..3 { h.tick(); }
    assert_eq!(calls("timeout_default"), first + 20, "a Boot fact it reads moved: every command recomputes once");
    assert_eq!(h.get("Cmd:c3", "timeout_default"), json!(7));
    // a changed getter of the same property recomputes (the pack's new text wins)
    h.load_pack("base", &BASE.replace("[120]", "[99]")).unwrap();
    h.write("Boot:seat", "command_timeout_s", json!(null), "person");
    for _ in 0..3 { h.tick(); }
    assert_eq!(h.get("Cmd:c3", "timeout_default"), json!(99), "an edited getter is evaluated with its new text");
    // a getter ADDED by a later pack is computed for the entities that already exist
    h.load_pack("more", "PACK more 1\nIMPORT base 1\nCmd.twice getter: this.x * 2\n").unwrap();
    for _ in 0..3 { h.tick(); }
    assert_eq!(h.get("Cmd:c3", "twice"), json!(6));
}
