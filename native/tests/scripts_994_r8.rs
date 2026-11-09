//! #994 row 8: entity scripts as field data. The script text shipped in native/packs/scripts.json equals the Python
//! constants byte-for-byte (fixture generated from night.py / autonode.py / chat_io.py), lands on the owning entities as
//! tick/watch/getter properties, and the whole-field checksum of the load is pinned.
use membrane_vm::packs;
use membrane_vm::vm::Core;
use serde_json::Value;

const FIX: &str = include_str!("fixtures/py_scripts_r8.json");

fn canon(v: &Value) -> String {
    match v {
        Value::Object(o) => { let mut ks: Vec<&String> = o.keys().collect(); ks.sort();
            format!("{{{}}}", ks.iter().map(|k| format!("{:?}:{}", k, canon(&o[*k]))).collect::<Vec<_>>().join(",")) }
        Value::Array(a) => format!("[{}]", a.iter().map(canon).collect::<Vec<_>>().join(",")),
        _ => v.to_string(),
    }
}
fn checksum(c: &Core) -> u64 {
    let mut rows: Vec<String> = c.docs.iter().map(|(k, e)| {
        let mut ps: Vec<String> = e.props.iter().map(|(p, v)| format!("{}={}", p, canon(v))).collect(); ps.sort();
        format!("{}|{}|{}", k, e.cls, ps.join(";"))
    }).collect();
    rows.sort();
    let mut h: u64 = 0xcbf29ce484222325;
    for b in rows.join("\n").bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
    h
}
fn loaded() -> Core {
    let mut c = Core::new();
    for t in packs::shipped_all() { packs::load_pack(&mut c, t).unwrap(); }
    c
}

#[test]
fn shipped_scripts_equal_the_python_constants() {
    let f: Value = serde_json::from_str(FIX).unwrap();
    let c = loaded();
    for (eid, kind, key) in [("tool:clock", "tick_script", "clock"), ("tool:filetree", "watch_script", "ft"),
                             ("script:chat-heard", "getter_script", "chat"), ("script:concept-drift", "getter_script", "drift")] {
        assert_eq!(packs::script_of(&c, eid, kind).as_deref(), f[key].as_str(), "{eid}.{kind}");
    }
}

#[test]
fn sensors_and_seeds_land_on_their_entities() {
    let c = loaded();
    assert_eq!(c.docs["tool:clock"].props["tick_every"], 1);
    assert_eq!(c.docs["tool:filetree"].props["watch"]["kind"], "file");
    assert_eq!(c.docs["tool:executor"].props["lang"], "sh");
    assert!(c.docs["remote_executor"].props["summary"].as_str().unwrap().starts_with("INTERFACE:"));
    assert!(!c.docs["tool:clock"].props.contains_key("silent_birth"));
}

#[test]
fn loading_twice_is_idempotent_and_malformed_declares_nothing() {
    let mut c = loaded();
    let once = checksum(&c);
    for t in packs::shipped_all() { packs::load_pack(&mut c, t).unwrap(); }
    assert_eq!(once, checksum(&c));
    let mut d = Core::new();
    assert!(packs::load_pack(&mut d, r#"{"entities":[{"eid":"a","cls":"T"},{"cls":"T"}]}"#).is_err());
    assert!(d.docs.is_empty());
}

#[test]
fn field_checksum_is_pinned() {
    let h = checksum(&loaded());
    println!("checksum {:016x}", h);
    assert_eq!(format!("{:016x}", h), "ca6ffcc09fb086df");
}
