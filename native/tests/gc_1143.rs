//! GARBAGE COLLECTION OF VIRTUAL ENTITIES (#1143, operator 2026-11-13; docs/cliffvm.md, "Garbage collection"): a virtual entity is collected at the end of the round in which nothing
//! holds it any more. Holders: ownership (a colon-path child lives with its parent), a reference (a `{"$ref": id}` value in a property of a live entity), in-flight drivers (a
//! connection, an effect, a running command's chunks). A plain read holds nothing. Roots: stored entities, packs, the drivers' in-flight work.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("gc-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }
fn mem() -> Host { let mut o = Options::new(None); o.effects = false; Host::open(o).unwrap() }
fn alive(h: &Host, eid: &str) -> bool { h.snapshot().contains_key(eid) }

const PACK: &str = "PACK gc 1
VIRTUAL Req
RESIDENT Keep
Maker.make on-setter make:
  ALWAYS:
    Req:made = Req(x: 1)
Maker.hold on-setter hold:
  ALWAYS:
    Req:held = Req(x: 2)
    Keep:k.keep = Req:held
Maker.nest on-setter nest:
  ALWAYS:
    Req:nested = Req(x: 5)
    Keep:k.keep = {'a': [Req:nested]}
Maker.child on-setter child:
  ALWAYS:
    Req:held:body = Req(x: 3)
Maker.cycle on-setter cycle:
  ALWAYS:
    Req:c1 = Req(x: 1)
    Req:c2 = Req(x: 2)
    Req:c1.other = Req:c2
    Req:c2.other = Req:c1
Maker.cycle_held on-setter cycle-held:
  ALWAYS:
    Req:c1 = Req(x: 1)
    Req:c2 = Req(x: 2)
    Req:c1.other = Req:c2
    Req:c2.other = Req:c1
    Keep:k.keep2 = Req:c1
Maker.release on-setter release:
  ALWAYS:
    Keep:k.keep = None
    Keep:k.keep2 = None
Probe.look on-setter look:
  ALWAYS:
    this.w = why(Req:made.x)
    this.v = Req:held.x
";

fn host() -> Host {
    let h = mem();
    h.load_pack("gc", PACK).unwrap();
    h.rebuild();
    h.born("Keep:k", "Keep", props(json!({})), "person");
    h.born("Maker:m", "Maker", props(json!({})), "person");
    h.born("Probe:p", "Probe", props(json!({})), "person");
    h
}

#[test]
fn an_entity_nothing_holds_is_collected_at_the_end_of_the_round_that_made_it_and_a_read_says_why() {
    let h = host();
    h.write("Maker:m", "make", json!(1), "person");
    assert!(!alive(&h, "Req:made"), "nothing held it: gone when the round ended");
    h.write("Probe:p", "look", json!(1), "person");
    assert_eq!(h.get("Probe:p", "w"), json!("collected: nothing held it"), "a read of a collected entity is a null with its reason");
    assert_eq!(h.get("Probe:p", "v"), J::Null);
}

#[test]
fn a_ref_value_holds_even_nested_and_releasing_it_collects() {
    let h = host();
    h.write("Maker:m", "hold", json!(1), "person");
    assert!(alive(&h, "Req:held"), "held by a $ref in a property of a stored entity: {:?}", h.get("Keep:k", "keep"));
    assert_eq!(h.get("Keep:k", "keep"), json!({"$ref": "Req:held"}), "the explicit reference form: a `$ref` value");
    h.write("Probe:p", "look", json!(1), "person");
    assert_eq!(h.get("Probe:p", "v"), json!(2), "a read of a held entity is the value");
    h.write("Maker:m", "release", json!(1), "person");
    assert!(!alive(&h, "Req:held"), "released: collected");
    h.write("Maker:m", "nest", json!(1), "person");
    assert!(alive(&h, "Req:nested"), "a $ref nested in a dict and a list holds too");
    h.write("Maker:m", "release", json!(2), "person");
    assert!(!alive(&h, "Req:nested"));
}

#[test]
fn a_plain_read_holds_nothing() {
    let h = host();
    h.write("Maker:m", "make", json!(1), "person");
    h.born("Req:outside", "Req", props(json!({"x": 9})), "host:door");
    h.write("Probe:p", "look", json!(1), "person");   // reads Req:made.x and Req:held.x: no hold
    assert!(!alive(&h, "Req:outside") && !alive(&h, "Req:made"));
}

#[test]
fn a_colon_path_child_lives_with_its_parent_and_goes_with_it() {
    let h = host();
    h.write("Maker:m", "hold", json!(1), "person");
    h.write("Maker:m", "child", json!(1), "person");
    assert!(alive(&h, "Req:held") && alive(&h, "Req:held:body"), "the child is held by its parent");
    h.write("Maker:m", "release", json!(1), "person");
    assert!(!alive(&h, "Req:held") && !alive(&h, "Req:held:body"), "the parent goes and takes the child in the same pass");
}

#[test]
fn a_cycle_nothing_outside_holds_is_collected_and_one_a_stored_entity_holds_is_not() {
    let h = host();
    h.write("Maker:m", "cycle", json!(1), "person");
    assert!(!alive(&h, "Req:c1") && !alive(&h, "Req:c2"), "two entities holding each other, held by nothing else: both go");
    h.write("Maker:m", "cycle_held", json!(1), "person");
    assert!(alive(&h, "Req:c1") && alive(&h, "Req:c2"), "reachable from a stored entity: both stay");
    h.write("Maker:m", "release", json!(1), "person");
    assert!(!alive(&h, "Req:c1") && !alive(&h, "Req:c2"), "released: the cycle goes");
}

#[test]
fn only_virtual_classes_are_collected_and_a_stored_entity_nobody_holds_stays() {
    let h = host();
    h.born("Box:b", "Box", props(json!({"n": 1})), "person");
    h.write("Maker:m", "make", json!(1), "person");
    assert!(alive(&h, "Box:b") && alive(&h, "Keep:k") && alive(&h, "Maker:m"), "stored entities are never collected");
}

#[test]
fn a_replay_of_the_journal_ends_in_the_same_stored_state() {
    let file = tmpfile("replay");
    let mut o = Options::new(Some(file)); o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("gc", PACK).unwrap();
    h.rebuild();
    h.born("Keep:k", "Keep", props(json!({})), "person");
    h.born("Maker:m", "Maker", props(json!({})), "person");
    for (i, w) in ["make", "hold", "child", "release", "cycle"].iter().enumerate() { h.write("Maker:m", w, json!(i + 1), "person"); }
    let r = mem();
    r.load_pack("gc", PACK).unwrap();
    r.rebuild();
    r.replay(&h.journal());
    assert_eq!(r.rows(), h.rows(), "replay-equal stored state");
    assert!(r.snapshot().keys().all(|k| !k.starts_with("Req:")), "and no virtual entity is left in the replay");
}

// ---- the drivers' in-flight work holds: a running command's chunks ------------------------------------------------------------------------------
const CHUNKS: &str = "PACK gcc 1
IMPORT proc 1
VIRTUAL Chunk
Chunk.size getter: len(str(this.data or ''))
";

#[test]
fn a_running_commands_chunks_are_held_while_it_runs_and_collected_when_it_is_over() {
    let file = tmpfile("chunks");
    let h = Host::open(Options::new(Some(file))).unwrap();
    let r = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane/packs");
    h.load_app(&r.join("proc.c1f"), true).unwrap();
    h.load_pack("gcc", CHUNKS).unwrap();
    h.rebuild();
    let dir = std::env::temp_dir().join(format!("gc-chunks-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    h.born("Command:c", "Command", props(json!({})), "person");
    h.write("Command:c", "run", json!({"id": "r1", "cmd": ["sh", "-c", "echo one; sleep 0.6; echo two; sleep 0.3"], "partial": true}), "person");
    let (mut most, mut done) = (0, false);
    let t = std::time::Instant::now();
    while t.elapsed() < std::time::Duration::from_secs(20) {
        h.pump();
        let n = h.snapshot().keys().filter(|k| k.starts_with("Chunk:")).count();
        most = most.max(n);
        if h.get("Command:c", "run_result") != J::Null { done = true; h.pump(); h.pump(); break; }
        std::thread::sleep(std::time::Duration::from_millis(40));
    }
    assert!(done, "the command finished: {} / {}", h.get("Command:c", "run_result"), h.get("Command:c", "running"));
    assert!(most >= 1, "its chunks were held while it ran (most at once: {most})");
    assert_eq!(h.snapshot().keys().filter(|k| k.starts_with("Chunk:")).count(), 0, "and collected once it is over: nothing holds them");
}

#[test]
fn a_killed_commands_chunks_are_collected_after_its_timeout_receipt_and_the_command_stays_pinned_with_its_reasoned_nulls() {
    let file = tmpfile("timeout");
    let h = Host::open(Options::new(Some(file))).unwrap();
    let r = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../membrane/packs");
    h.load_app(&r.join("proc.c1f"), true).unwrap();
    h.load_pack("gcc", CHUNKS).unwrap();
    h.rebuild();
    h.born("Command:t", "Command", props(json!({})), "person");
    h.write("Command:t", "run", json!({"id": "t1", "cmd": ["sh", "-c", "echo one; sleep 30"], "partial": true, "timeout_s": 1}), "person");
    let t = std::time::Instant::now();
    let mut most = 0;
    while t.elapsed() < std::time::Duration::from_secs(20) {
        h.pump();
        most = most.max(h.snapshot().keys().filter(|k| k.starts_with("Chunk:")).count());
        if h.get("Command:t", "run_result") != J::Null { h.pump(); h.pump(); break; }
        std::thread::sleep(std::time::Duration::from_millis(40));
    }
    assert_eq!(h.get("Command:t", "run_result")["status"], json!("timeout"));
    assert!(most >= 1, "its chunk was held while it ran");
    assert_eq!(h.snapshot().keys().filter(|k| k.starts_with("Chunk:")).count(), 0, "the killed command's chunks are collected after its receipt");
    // a Command is resident: pinned, refused with a receipt, and its reasoned nulls still read
    h.write("Command:t", "unload", json!(true), "person");
    assert!(h.snapshot().contains_key("Command:t"));
    assert!(h.get("Command:t", "unload_result")["reason"].as_str().unwrap().contains("resident"));
    assert_eq!(h.get("Command:t", "output"), J::Null);
    assert_eq!(h.get("Command:t", "partial_output"), json!("one\n"));
}
