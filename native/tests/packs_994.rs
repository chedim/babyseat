//! #994 row 7: seed packs as field declarations. The whole-field checksum (FNV-1a over every entity) after loading the
//! shipped JSON packs equals the checksum after the old hand-coded loads (kept here as the oracle); a malformed pack
//! declares nothing; loading twice is idempotent.
use membrane_vm::vm::Core;
use membrane_vm::{packs, recall};

/// canonical JSON text: object keys sorted at every depth (a parsed pack and a json! literal order keys differently)
fn canon(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::Object(o) => { let mut ks: Vec<&String> = o.keys().collect(); ks.sort();
            format!("{{{}}}", ks.iter().map(|k| format!("{:?}:{}", k, canon(&o[*k]))).collect::<Vec<_>>().join(",")) }
        serde_json::Value::Array(a) => format!("[{}]", a.iter().map(canon).collect::<Vec<_>>().join(",")),
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

/// the old path, verbatim: Rust literals loaded by hand
fn legacy(c: &mut Core) {
    c.load(recall::DIALS.into(), recall::CLASS_DIALS.into(), recall::defaults(), None, 0.0);
}

#[test]
fn pack_load_matches_the_hand_coded_load() {
    let (mut a, mut b) = (Core::new(), Core::new());
    legacy(&mut a);
    let n: usize = packs::shipped().iter().map(|t| packs::load_pack(&mut b, t).unwrap()).sum();
    assert_eq!(n, 1);
    assert_eq!(checksum(&a), checksum(&b));
    println!("checksum {:016x}", checksum(&b));
}

#[test]
fn seed_functions_now_read_the_packs() {
    let (mut a, mut b) = (Core::new(), Core::new());
    legacy(&mut a);
    recall::load_seed(&mut b);
    assert_eq!(checksum(&a), checksum(&b));
}

#[test]
fn loading_twice_is_idempotent() {
    let mut c = Core::new();
    for t in packs::shipped() { packs::load_pack(&mut c, t).unwrap(); }
    let once = checksum(&c);
    for t in packs::shipped() { packs::load_pack(&mut c, t).unwrap(); }
    assert_eq!(once, checksum(&c));
}

#[test]
fn a_malformed_pack_declares_nothing() {
    let mut c = Core::new();
    let bad = r#"{"name":"x","version":1,"entities":[{"eid":"a:1","cls":"T","properties":{}},{"cls":"T"}]}"#;
    assert!(packs::load_pack(&mut c, bad).is_err());
    assert!(c.docs.is_empty());
    assert!(packs::load_pack(&mut c, "not json").is_err());
}
