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
