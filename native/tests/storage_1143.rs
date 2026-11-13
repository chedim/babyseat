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
