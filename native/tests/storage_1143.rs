//! STORAGE MODES (#1143, operator 2026-11-13): a class is VIRTUAL (RAM only: never stored, restored or journalled), RESIDENT (stored and always loaded)
//! or, by default, LAZY (stored, loaded on demand). The owner pack of a class declares it in its header (`VIRTUAL <Class>` / `RESIDENT <Class>`),
//! as it declares its imports; docs/cliffvm.md, "Storage modes".
use membrane_vm::fields::Storage;
use membrane_vm::host::{Host, Options};
use membrane_vm::syntax::parse_full;
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn mem() -> Host { let mut o = Options::new(None); o.effects = false; Host::open(o).unwrap() }

#[test]
fn the_header_declares_virtual_and_resident_classes() {
    let p = parse_full("PACK p 1\nVIRTUAL Foo\nRESIDENT Bar\nFoo.x getter: 1\n").unwrap();
    assert_eq!(p.virtuals, vec!["Foo".to_string()]);
    assert_eq!(p.residents, vec!["Bar".to_string()]);
    assert!(parse_full("PACK p 1\nVIRTUAL\n").unwrap_err().contains("VIRTUAL needs a class"), "a declaration names a class");
    assert!(parse_full("PACK p 1\nRESIDENT A B\n").unwrap_err().contains("one class"), "one class per line");
    let both = parse_full("PACK p 1\nVIRTUAL Foo\nRESIDENT Foo\n").unwrap_err();
    assert!(both.contains("Foo") && both.contains("VIRTUAL") && both.contains("RESIDENT") && both.contains("one storage mode"), "{both}");
}

#[test]
fn only_the_owner_declares_a_storage_mode_and_a_conflict_names_both_packs() {
    let h = mem();
    h.load_pack("a", "PACK a 1\nVIRTUAL X\nX.one getter: 1\n").unwrap();
    assert_eq!(h.storage_of("X"), Storage::Virtual);
    assert_eq!(h.storage_of("Y"), Storage::Lazy, "the default");
    // a pack that imports the owner and declares another mode
    let e = h.load_pack("b", "PACK b 1\nIMPORT a 1\nRESIDENT X\n").unwrap_err();
    assert!(e.contains("pack b") && e.contains("pack a") && e.contains("X") && e.contains("only the owner"), "{e}");
    assert_eq!(h.storage_of("X"), Storage::Virtual, "the refused pack changed nothing");
    // a pack that does not import the owner
    let e = h.load_pack("c", "PACK c 1\nVIRTUAL X\n").unwrap_err();
    assert!(e.contains("pack c") && e.contains("pack a") && e.contains("only the owner"), "{e}");
    // the same mode from a non-owner is refused too (it is not the owner's line)
    let e = h.load_pack("d", "PACK d 1\nIMPORT a 1\nVIRTUAL X\n").unwrap_err();
    assert!(e.contains("pack d") && e.contains("only the owner"), "{e}");
}

#[test]
fn a_class_declared_by_a_pack_that_owns_it_through_a_storage_line_alone_is_owned() {
    // no getter or setter on the class: the storage line itself claims the class, so a later pack cannot declare another mode
    let h = mem();
    h.load_pack("a", "PACK a 1\nRESIDENT Z\n").unwrap();
    assert_eq!(h.storage_of("Z"), Storage::Resident);
    assert!(h.load_pack("b", "PACK b 1\nVIRTUAL Z\n").unwrap_err().contains("only the owner"));
}

#[test]
fn removing_the_owner_pack_returns_its_classes_to_lazy() {
    let h = mem();
    h.load_pack("a", "PACK a 1\nVIRTUAL X\n").unwrap();
    assert_eq!(h.storage_of("X"), Storage::Virtual);
    h.load_pack("a", "PACK a 1\nX.one getter: 1\n").unwrap();
    assert_eq!(h.storage_of("X"), Storage::Lazy, "the new text does not declare it");
}

fn shipped_texts() -> Vec<(String, String)> {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let mut v: Vec<(String, String)> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| (n.to_string(), std::fs::read_to_string(r.join(format!("membrane/packs/{n}.c1f"))).unwrap())).collect();
    for f in ["default_banner", "collector", "help"] { v.push((f.to_string(), std::fs::read_to_string(r.join(format!("membrane/apps/barest.c1f/{f}.c1f"))).unwrap())); }
    v
}

fn barest() -> Host {
    let h = mem();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() { h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap(); }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h
}

#[test]
fn the_shipped_packs_declare_the_per_run_classes_virtual_and_the_drivers_resident() {
    let h = barest();
    for c in ["Request", "Return", "Chunk", "Step", "Boot", "Http"] { assert_eq!(h.storage_of(c), Storage::Virtual, "{c}"); }
    for c in ["Pack", "Banner", "Help", "Command", "File", "Dir", "Fetch", "Store"] { assert_eq!(h.storage_of(c), Storage::Resident, "{c}"); }
    for c in ["Chat", "ChatMessage"] { assert_eq!(h.storage_of(c), Storage::Lazy, "{c}"); }
}

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("storage-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}

#[test]
fn a_restart_restores_no_row_of_a_virtual_class_what_the_old_per_run_list_did() {
    // the rows of an older run (before the class was declared VIRTUAL) stay text: the restore skips them, as `PER_RUN_CLASSES` did
    let file = tmpfile("legacy");
    {
        let mut o = Options::new(Some(file.clone())); o.effects = false;
        let h = Host::open(o).unwrap();
        h.load_pack("a", "PACK a 1\nRESIDENT Keep\nVIRTUAL Gone\nGone.x getter: 1\n").unwrap();
        let db = rusqlite::Connection::open(&file).unwrap();
        db.execute("INSERT INTO c_field (k, body) VALUES ('Gone:1', '{\"cls\":\"Gone\",\"props\":{\"n\":1}}'), ('Keep:1', '{\"cls\":\"Keep\",\"props\":{\"n\":2}}')", []).unwrap();
    }
    let mut o = Options::new(Some(file)); o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("a", "PACK a 1\nRESIDENT Keep\nVIRTUAL Gone\nGone.x getter: 1\n").unwrap();
    assert_eq!(h.load_stored_entities(), 1);
    assert!(h.snapshot().contains_key("Keep:1") && !h.snapshot().contains_key("Gone:1"));
}

// ---- VIRTUAL semantics: RAM only, never stored, never journalled, and what a virtual entity's setter makes is still re-derivable ---------------------
const VIRT: &str = "PACK v 1
VIRTUAL Req
Req.go on-setter make:
  WHEN this.go == True:
    ('Msg:' + this.eid).text = this.body
    ('Msg:' + this.eid).n = 1
Msg.shout getter: str(this.text) + '!'
Msg.n on-setter count:
  WHEN this.n == 1:
    ('Tally:main').seen = 1
Tally.label getter: 'seen ' + str(this.seen)
";

fn file_host(file: &str) -> Host { let mut o = Options::new(Some(file.to_string())); o.effects = false; Host::open(o).unwrap() }
fn count(file: &str, like: &str) -> i64 { rusqlite::Connection::open(file).unwrap().query_row("SELECT count(*) FROM c_field WHERE k LIKE ?1", [like], |r| r.get(0)).unwrap() }
fn jcount(file: &str, like: &str) -> i64 { rusqlite::Connection::open(file).unwrap().query_row("SELECT count(*) FROM c_journal WHERE body LIKE ?1", [like], |r| r.get(0)).unwrap() }
fn props(v: serde_json::Value) -> serde_json::Map<String, serde_json::Value> { v.as_object().unwrap().clone() }

#[test]
fn a_virtual_entity_is_never_stored_never_journalled_and_a_restart_does_not_bring_it_back() {
    let file = tmpfile("virt");
    {
        let h = file_host(&file);
        h.load_pack("v", VIRT).unwrap();
        h.rebuild();
        h.born("Req:1", "Req", props(serde_json::json!({"path": "/x"})), "host:door");
        h.write("Req:1", "body", serde_json::json!("hello"), "host:door");
        h.pump();
        assert_eq!(h.get("Req:1", "body"), serde_json::json!("hello"), "it lives in the engine");
    }
    assert_eq!(count(&file, "Req:%"), 0, "no row of a virtual class");
    assert_eq!(jcount(&file, "%Req:1%"), 0, "no journal line of a virtual class");
    let h = file_host(&file);
    h.load_pack("v", VIRT).unwrap();
    assert_eq!(h.load_stored_entities(), 0);
    assert!(!h.snapshot().contains_key("Req:1"), "a restart does not resurrect it");
}

#[test]
fn what_a_virtual_entitys_setter_makes_is_journalled_as_an_outside_write_and_a_replay_ends_equal() {
    let file = tmpfile("replay");
    let h = file_host(&file);
    h.load_pack("v", VIRT).unwrap();
    h.rebuild();
    h.born("Req:1", "Req", props(serde_json::json!({"path": "/x"})), "host:door");
    h.write("Req:1", "body", serde_json::json!("hello"), "host:door");
    h.write("Req:1", "go", serde_json::json!(true), "host:door");
    h.pump();
    assert_eq!(h.get("Msg:Req:1", "text"), serde_json::json!("hello"));
    assert_eq!(h.get("Msg:Req:1", "shout"), serde_json::json!("hello!"));
    assert_eq!(h.get("Tally:main", "label"), serde_json::json!("seen 1"));
    let j = h.journal();
    assert!(j.iter().all(|l| !format!("{l:?}").contains("Req:1\"") || format!("{l:?}").contains("Msg:")), "no line about the Request itself: {j:?}");
    let made: Vec<_> = j.iter().filter(|l| format!("{l:?}").contains("Msg:Req:1")).collect();
    assert!(made.iter().all(|l| l.writer().starts_with("host:virtual:")), "stamped as outside writes: {made:?}");
    assert!(!made.is_empty());
    assert!(j.iter().all(|l| !format!("{l:?}").contains("Tally:main")), "the second hop is derived again by the replay, not journalled: {j:?}");
    // replay on a fresh engine
    let r = mem();
    r.load_pack("v", VIRT).unwrap();
    r.rebuild();
    r.replay(&h.journal_stored().unwrap());
    assert!(!r.snapshot().contains_key("Req:1"), "the replay never saw the Request");
    for e in ["Msg:Req:1", "Tally:main"] { assert_eq!(r.snapshot().get(e), h.snapshot().get(e), "{e}: replay-equal state"); }
    assert_eq!(count(&file, "Msg:%"), 1, "the message is stored");
}

// ---- `.updated`: the VM-provided, read-only time of an entity's last write ---------------------------------------------------------------------------------
fn ticking(start: f64) -> Box<dyn Fn() -> f64 + Send + Sync> {
    let n = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
    Box::new(move || start + n.fetch_add(1, std::sync::atomic::Ordering::SeqCst) as f64)
}
fn timed(file: Option<&str>, start: f64) -> Host { let mut o = Options::new(file.map(String::from)); o.effects = false; o.clock = ticking(start); Host::open(o).unwrap() }
const UPD: &str = "PACK u 1\nBox.stamp getter: this.updated\nBox.double getter: this.w * 2\nBox.updated on-setter never:\n  ALWAYS:\n    this.hit = True\n";

#[test]
fn updated_is_the_time_of_the_last_write_stamped_with_it_and_it_causes_nothing() {
    let file = tmpfile("updated");
    let h = timed(Some(&file), 1000.0);
    h.load_pack("u", UPD).unwrap();
    h.rebuild();
    h.born("Box:a", "Box", props(serde_json::json!({"w": 1})), "person");
    let t0 = h.updated("Box:a");
    assert!(t0 >= 1000.0, "stamped at its birth: {t0}");
    assert_eq!(h.get("Box:a", "stamp"), serde_json::json!(t0), "a getter reads it");
    let before = h.store_commits();
    h.write("Box:a", "w", serde_json::json!(2), "person");
    let t1 = h.updated("Box:a");
    assert!(t1 > t0, "a write moves it: {t0} -> {t1}");
    assert_eq!(h.store_commits() - before, 2, "the journal line and the round (as before `updated` existed): the stamp is in the row of the write, no extra write");
    assert_eq!(h.get("Box:a", "double"), serde_json::json!(4), "the derived write is part of the round");
    assert_eq!(h.get("Box:a", "stamp"), serde_json::json!(t0), "NO getter recompute: a getter that read `updated` keeps what it read until something else changes");
    assert_eq!(h.get("Box:a", "hit"), serde_json::Value::Null, "NO reaction: a setter on `updated` never fires");
    assert_eq!(h.stored_updated("Box:a"), Some(t1), "the same time in the row");
    let db = rusqlite::Connection::open(&file).unwrap();
    let ix: Vec<String> = db.prepare("SELECT name FROM pragma_index_list('c_field')").unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
    assert!(ix.iter().any(|n| n == "c_field_updated"), "an indexed column: {ix:?}");
    // a write that changes nothing does not move it
    h.write("Box:a", "w", serde_json::json!(2), "person");
    assert_eq!(h.updated("Box:a"), t1);
    // read-only: an outside write of it is refused and leaves no trace
    let jlen = h.journal().len();
    h.write("Box:a", "updated", serde_json::json!(5), "person");
    assert_eq!(h.updated("Box:a"), t1);
    assert_eq!(h.journal().len(), jlen, "not even journalled");
}

#[test]
fn a_setter_cannot_write_updated_either() {
    let h = timed(None, 1000.0);
    h.load_pack("u", "PACK u 1\nBox.go on-setter s:\n  WHEN this.go == True:\n    this.updated = 1\n").unwrap();
    h.rebuild();
    h.born("Box:a", "Box", props(serde_json::json!({})), "person");
    h.write("Box:a", "go", serde_json::json!(true), "person");
    assert!(h.updated("Box:a") > 1000.0);
    assert!(h.get("Box:a", "refused").as_str().unwrap_or("").contains("updated"), "{:?}", h.get("Box:a", "refused"));
}

#[test]
fn a_replay_ends_with_the_same_updated_the_journals_time_not_the_replays_clock() {
    let h = timed(None, 1000.0);
    h.load_pack("u", UPD).unwrap();
    h.rebuild();
    h.born("Box:a", "Box", props(serde_json::json!({"w": 1})), "person");
    h.born("Box:b", "Box", props(serde_json::json!({"w": 5})), "person");
    h.write("Box:a", "w", serde_json::json!(2), "person");
    let r = timed(None, 900000.0);
    r.load_pack("u", UPD).unwrap();
    r.rebuild();
    r.replay(&h.journal());
    for e in ["Box:a", "Box:b"] { assert_eq!(r.updated(e), h.updated(e), "{e}"); assert!(r.updated(e) > 1000.0 && r.updated(e) < 2000.0, "the journal's time, not the replay clock's"); }
    assert_eq!(r.get("Box:a", "double"), serde_json::json!(4));
}

#[test]
fn an_old_store_gets_the_columns_and_a_backfill_from_the_journal_once() {
    let file = tmpfile("migrate");
    {
        let db = rusqlite::Connection::open(&file).unwrap();
        db.execute_batch("CREATE TABLE c_field (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_journal (k TEXT PRIMARY KEY, body TEXT NOT NULL);").unwrap();
        db.execute_batch("INSERT INTO c_field VALUES ('Box:a', '{\"cls\":\"Box\",\"props\":{\"w\":1}}'), ('Box:b', '{\"cls\":\"Box\",\"props\":{\"w\":2}}'), ('Note:c', '{\"cls\":\"Note\",\"props\":{}}');
            INSERT INTO c_journal VALUES ('000000000000', '{\"op\":\"born\",\"ts\":10.0,\"writer\":\"p\",\"eid\":\"Box:a\",\"cls\":\"Box\",\"props\":{\"w\":1}}'),
                                         ('000000000001', '{\"op\":\"write\",\"ts\":30.5,\"writer\":\"p\",\"eid\":\"Box:a\",\"prop\":\"w\",\"new\":1}'),
                                         ('000000000002', '{\"op\":\"born\",\"ts\":20.0,\"writer\":\"p\",\"eid\":\"Box:b\",\"cls\":\"Box\",\"props\":{\"w\":2}}');").unwrap();
    }
    let h = timed(Some(&file), 5000.0);
    assert_eq!(h.stored_updated("Box:a"), Some(30.5), "the last journalled write");
    assert_eq!(h.stored_updated("Box:b"), Some(20.0));
    assert_eq!(h.stored_updated("Note:c"), Some(0.0), "no journal line: 0");
    let db = rusqlite::Connection::open(&file).unwrap();
    let cls: String = db.query_row("SELECT cls FROM c_field WHERE k = 'Note:c'", [], |r| r.get(0)).unwrap();
    assert_eq!(cls, "Note", "the class column is backfilled");
    drop(h);
    // idempotent: open again; what a later run changed is not backfilled over
    db.execute("UPDATE c_field SET updated = 99 WHERE k = 'Box:a'", []).unwrap();
    let h = timed(Some(&file), 5000.0);
    assert_eq!(h.stored_updated("Box:a"), Some(99.0));
    drop(h);
    let n: i64 = db.query_row("SELECT count(*) FROM pragma_table_info('c_field') WHERE name IN ('cls', 'updated')", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 2, "each column once");
}

// ---- LAZY LOAD and the WARM BOOT: the packs and the RESIDENT rows at start, the rest on first reference and in the background, newest first --------------------------
const LAZY: &str = "PACK l 1
RESIDENT Peek
Peek.val getter: prop(this.target, 'n')
Box.double getter: this.n * 2
Box.go on-setter bump:
  WHEN this.go == True:
    this.n = this.n + 10
Box.made on-setter make:
  WHEN this.made == True:
    ('Box:new').n = 7
";

/// a store of an earlier run: `n` Box rows (n = i) and what a resident class holds
fn earlier(file: &str, n: usize) {
    let h = file_host(file);
    h.load_pack("l", LAZY).unwrap();
    h.rebuild();
    for i in 0..n { h.born(&format!("Box:{i}"), "Box", props(serde_json::json!({"n": i})), "person"); }
    h.born("Peek:p", "Peek", props(serde_json::json!({"target": "Box:3"})), "person");
    h.pump();
}
fn restarted(file: &str) -> Host {
    let h = file_host(file);
    h.load_pack("l", LAZY).unwrap();
    h.load_stored_entities();
    h.rebuild();
    h
}

#[test]
fn the_restore_loads_the_resident_rows_and_leaves_the_lazy_ones_in_the_store() {
    let file = tmpfile("lazy1");
    earlier(&file, 50);
    let h = restarted(&file);
    assert!(h.snapshot().contains_key("Peek:p"), "a RESIDENT class is loaded at start");
    assert!(!h.snapshot().keys().any(|k| k.starts_with("Box:")) || h.snapshot().keys().filter(|k| k.starts_with("Box:")).count() < 50, "the lazy rows are not all loaded");
    assert_eq!(h.unloaded_count(), 50 - h.snapshot().keys().filter(|k| k.starts_with("Box:")).count());
}

#[test]
fn a_getter_that_reads_an_unloaded_entity_recomputes_when_it_loads() {
    let file = tmpfile("lazy2");
    earlier(&file, 10);
    let h = restarted(&file);
    h.pump();
    assert_eq!(h.get("Peek:p", "val"), serde_json::json!(3), "the reference loaded Box:3 and the getter that read it recomputed");
    assert!(h.snapshot().contains_key("Box:3"));
    assert_eq!(h.get("Box:3", "double"), serde_json::json!(6), "and its own getters ran");
}

#[test]
fn a_lazy_entity_written_by_a_setter_after_a_restart_behaves_as_if_it_had_been_loaded() {
    let file = tmpfile("lazy3");
    earlier(&file, 10);
    let h = restarted(&file);
    assert!(!h.snapshot().contains_key("Box:5"));
    // an outside write
    h.write("Box:5", "go", serde_json::json!(true), "person");
    assert_eq!(h.get("Box:5", "n"), serde_json::json!(15), "the stored n (5) was there when the setter read it");
    // a setter's write to another entity: it exists in the store, the setter must not create it anew
    h.write("Box:6", "made", serde_json::json!(true), "person");
    assert_eq!(h.get("Box:6", "n"), serde_json::json!(6));
    let again = restarted(&file);
    again.write("Box:2", "made", serde_json::json!(true), "person");
    let db = rusqlite::Connection::open(&file).unwrap();
    let body: String = db.query_row("SELECT body FROM c_field WHERE k = 'Box:5'", [], |r| r.get(0)).unwrap();
    assert!(body.contains("\"n\":15"), "{body}");
}

#[test]
fn a_setter_that_writes_a_stored_but_unloaded_entity_changes_that_entity_not_a_new_one() {
    let file = tmpfile("lazy4");
    {
        let h = file_host(&file);
        h.load_pack("l", LAZY).unwrap();
        h.rebuild();
        h.born("Box:new", "Box", props(serde_json::json!({"n": 1, "keep": "me"})), "person");
        h.born("Box:t", "Box", props(serde_json::json!({"n": 0})), "person");
    }
    let h = restarted(&file);
    assert!(!h.snapshot().contains_key("Box:new"));
    h.write("Box:t", "made", serde_json::json!(true), "person");   // ('Box:new').n = 7
    assert_eq!(h.get("Box:new", "n"), serde_json::json!(7));
    assert_eq!(h.get("Box:new", "keep"), serde_json::json!("me"), "the stored properties came with it");
}

#[test]
fn instances_of_a_lazy_class_sees_only_the_loaded_ones_and_a_resident_class_sees_all() {
    let file = tmpfile("lazy5");
    {
        let h = file_host(&file);
        h.load_pack("l", "PACK l 1\nRESIDENT Res\nBox.n getter: 1\nSee.lazy getter: len(instances('Box'))\nSee.res getter: len(instances('Res'))\n").unwrap();
        h.rebuild();
        for i in 0..20 { h.born(&format!("Box:{i}"), "Box", props(serde_json::json!({})), "person"); h.born(&format!("Res:{i}"), "Res", props(serde_json::json!({})), "person"); }
    }
    let h = file_host(&file);
    h.load_pack("l", "PACK l 1\nRESIDENT Res\nBox.n getter: 1\nSee.lazy getter: len(instances('Box'))\nSee.res getter: len(instances('Res'))\n").unwrap();
    h.load_stored_entities();
    h.born("See:x", "See", props(serde_json::json!({})), "person");
    h.pump();
    assert_eq!(h.get("See:x", "res"), serde_json::json!(20), "a RESIDENT class is whole");
    assert_eq!(h.get("See:x", "lazy"), serde_json::json!(0), "a lazy class lists only what is loaded");
    h.write("Box:4", "touch", serde_json::json!(1), "person");
    h.pump();
    assert_eq!(h.get("See:x", "lazy"), serde_json::json!(1), "a reference loaded one, and the reader of the class recomputed");
}

#[test]
fn the_warm_boot_loads_the_newest_rows_first_up_to_the_budget_in_the_background() {
    let file = tmpfile("warm");
    {
        let h = timed(Some(&file), 1000.0);
        h.load_pack("l", LAZY).unwrap();
        h.rebuild();
        for i in 0..40 { h.born(&format!("Box:{i}"), "Box", props(serde_json::json!({"n": i})), "person"); }
        h.write("Box:7", "n", serde_json::json!(70), "person");   // the newest write
    }
    let h = timed(Some(&file), 9000.0);
    h.load_pack("l", LAZY).unwrap();
    h.set_warm_rows(10);
    h.load_stored_entities();
    assert_eq!(h.snapshot().keys().filter(|k| k.starts_with("Box:")).count(), 0, "start loads no lazy row");
    let mut steps = 0;
    while h.warm_step() { steps += 1; assert!(steps < 100); }
    let loaded: Vec<String> = h.snapshot().keys().filter(|k| k.starts_with("Box:")).cloned().collect();
    assert_eq!(loaded.len(), 10, "the budget: {loaded:?}");
    assert!(loaded.contains(&"Box:7".to_string()), "the last written first");
    assert!(loaded.contains(&"Box:39".to_string()) && !loaded.contains(&"Box:0".to_string()), "newest first: {loaded:?}");
    assert_eq!(h.get("Box:7", "n"), serde_json::json!(70));
    assert_eq!(h.get("Box:7", "double"), serde_json::json!(140), "warmed rows run their getters");
}

#[test]
fn a_retired_entity_takes_its_unloaded_children_with_it() {
    let file = tmpfile("lazy6");
    {
        let h = file_host(&file);
        h.load_pack("l", "PACK l 1\nBox.done on-setter bye:\n  WHEN this.done == True:\n    this.retire = True\n").unwrap();
        h.rebuild();
        h.born("Box:a", "Box", props(serde_json::json!({})), "person");
        h.born("Box:a:child", "Box", props(serde_json::json!({})), "person");
    }
    let h = file_host(&file);
    h.load_pack("l", "PACK l 1\nBox.done on-setter bye:\n  WHEN this.done == True:\n    this.retire = True\n").unwrap();
    h.load_stored_entities();
    h.write("Box:a", "done", serde_json::json!(true), "person");
    assert_eq!(count(&file, "Box:%"), 0, "both rows are gone");
    assert_eq!(h.unloaded_count(), 0, "and the child is not waiting to load");
}

#[test]
fn a_class_a_pack_lists_with_instances_is_loaded_whole_or_is_virtual() {
    // `instances('<Class>')` sees only the loaded entities of a lazy class: a shipped pack that lists a class must declare it RESIDENT (or VIRTUAL: all live ones are loaded)
    let mut all = shipped_texts();
    let r = repo();
    all.push(("goalgraph".into(), std::fs::read_to_string(r.join("native/packs/goalgraph.c1f")).unwrap()));   // the built-in pack owns Goal, Task, Wait and Decision
    for f in std::fs::read_dir(r.join("membrane/packs")).unwrap().flatten() {
        let p = f.path();
        if p.extension().map_or(false, |e| e == "c1f") { let n = p.file_stem().unwrap().to_string_lossy().to_string(); if !all.iter().any(|(m, _)| *m == n) { all.push((n, std::fs::read_to_string(&p).unwrap())); } }
    }
    let mut declared = std::collections::BTreeSet::new();
    for (_, t) in &all { let p = parse_full(t).unwrap(); for c in p.virtuals.iter().chain(p.residents.iter()) { declared.insert(c.clone()); } }
    // a KIND of a lazy class (`ChatMessage`, in the `parents` of an eid-class `Chat` entity) lists only the loaded ones as well: it is not declared, so a pack that lists it is flagged
    assert!(!declared.contains("Chat") && !declared.contains("ChatMessage"), "the chat's classes are lazy: {declared:?}");
    let mut bad = vec![];
    for (n, t) in &all {
        for l in t.lines().filter(|l| !l.trim_start().starts_with("--")) {
            for (i, _) in l.match_indices("instances('") {
                let c: String = l[i + 11..].chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
                if !declared.contains(&c) { bad.push(format!("{n}: instances('{c}')")); }
            }
        }
    }
    bad.sort(); bad.dedup();
    assert!(bad.is_empty(), "a class listed with instances must be declared RESIDENT or VIRTUAL by its owner pack:\n{}", bad.join("\n"));
}
