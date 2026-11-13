//! APPS and TRUST BY POSITION (docs/c1f-layers.md): an app is a folder `<name>.c1f/` (or one pack file); `.c1f` files are packs loaded in
//! dependency order (a pack after the packs it imports), the rest alphabetical by path; every other file is DATA. The first app given
//! is the root of trust: its packs are privileged, nothing is trusted by name, a later app is a layer above with no privilege.
use membrane_vm::expr;
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::{Path, PathBuf};

fn tmp(tag: &str) -> PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("apps1143-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn put(dir: &Path, rel: &str, text: &str) {
    let p = dir.join(rel);
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    std::fs::write(p, text).unwrap();
}
fn host() -> Host {
    let h = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
    h.rebuild();
    h
}
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

#[test]
fn packs_load_in_dependency_order_then_alphabetical_and_data_files_are_not_packs() {
    let d = tmp("order");
    let app = d.join("shop.c1f");
    put(&app, "z_base.c1f", "PACK z_base 1\nBox.zb getter: 1\n");
    put(&app, "a_top.c1f", "PACK a_top 1\nIMPORT z_base 1\nBox.at getter: this.zb\n");
    put(&app, "c.c1f", "PACK c 1\nCrate.c getter: 1\n");
    put(&app, "m.c1f", "PACK m 1\nMug.m getter: 1\n");
    put(&app, "ui/index.html", "<h1>x</h1>");
    put(&app, "notes.txt", "not a pack");
    let h = host();
    let l = h.load_app(&app, false).unwrap();
    assert_eq!(l.names, ["c", "m", "z_base", "a_top"], "an import loads first; the rest alphabetical by path");
    assert!(l.msgs.is_empty(), "{:?}", l.msgs);
    assert!(!h.snapshot().keys().any(|k| k.contains("notes")), "a data file is not an entity of the engine");
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn a_single_pack_file_is_an_app_and_a_later_app_skips_a_pack_already_loaded_by_name() {
    let d = tmp("file");
    put(&d, "boot.c1f", "PACK boot 1\nBoot.up getter: 1\n");
    put(&d, "packs/boot.c1f", "PACK boot 1\nBoot.up getter: 2\n");
    put(&d, "packs/other.c1f", "PACK other 1\nIMPORT boot 1\nBoot.o getter: 1\n");
    let h = host();
    let a = h.load_app(&d.join("boot.c1f"), true).unwrap();
    assert_eq!(a.names, ["boot"]);
    let b = h.load_app(&d.join("packs"), false).unwrap();
    assert_eq!(b.names, ["other"], "boot is already loaded: skipped, the first one stays");
    h.born("Boot:s", "Boot", props(json!({})), "person");
    assert_eq!(h.get("Boot:s", "up"), json!(1));
    assert_eq!(h.get("Boot:s", "o"), json!(1));
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn trust_is_by_position_never_by_name() {
    let d = tmp("trust");
    put(&d, "root/rootpack.c1f", "PACK rootpack 1\nBoot.up getter: 1\n");
    put(&d, "root/second.c1f", "PACK second 1\nIMPORT rootpack 1\nBoot.s getter: 1\n");
    put(&d, "layer/boot.c1f", "PACK boot 1\nIMPORT rootpack 1\nBoot.b getter: 1\n");
    put(&d, "layer/plain.c1f", "PACK plain 1\n");
    let h = host();
    assert!(!h.pack_privileged("boot"), "a fresh host trusts nothing, not even the name `boot`");
    h.load_app(&d.join("root"), true).unwrap();
    h.load_app(&d.join("layer"), false).unwrap();
    assert!(h.pack_privileged("rootpack") && h.pack_privileged("second"), "the first argument is the root of trust, whatever it is called");
    assert!(!h.pack_privileged("boot"), "a pack named boot that is not first is not trusted");
    assert!(!h.pack_privileged("plain"), "a later app has no privilege");
    assert_eq!(h.get("Pack:rootpack", "granted_by"), json!("host"));
    // until a grant: the root's pack grants (a write by a privileged writer), and the layer pack then holds it
    h.write("Pack:plain", "privileged", json!(true), "person");
    assert!(h.pack_privileged("plain"));
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn replacing_a_packs_text_with_a_version_that_lacks_a_getter_clears_its_value() {
    expr::set_clock(None);
    let h = host();
    h.born("Pack:p", "Pack", props(json!({"text": "PACK p 1\nBox.keep getter: 'k'\nBox.drop getter: 'd'\n"})), "person");
    h.born("Box:a", "Box", props(json!({})), "person");
    assert_eq!(h.get("Box:a", "drop"), json!("d"));
    assert_eq!(h.get("Box:a", "keep"), json!("k"));
    h.write("Pack:p", "text", json!("PACK p 1\nBox.keep getter: 'k2'\n"), "person");
    assert_eq!(h.get("Box:a", "keep"), json!("k2"));
    assert_eq!(h.get("Box:a", "drop"), J::Null, "the getter is gone from the new text: the value it left is cleared");
}
