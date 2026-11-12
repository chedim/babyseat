//! The door verifies the token (jwt.rs), the same rules membrane/jwtauth.py had: HS256 only, a secret of at least 32 bytes,
//! `exp` and `sub` required, `nbf`/`iat` checked with a 30 s leeway, a refused token is only ever "invalid token". The token
//! comes from `Authorization: Bearer`, HTTP Basic (the token as the password) or the `membrane_jwt` cookie -- never the
//! query string. A verified request carries `authed`, `sub` and `claims`; a pack decides what that allows.
use membrane_vm::http::token_of;
use membrane_vm::jwt;

const SECRET: &[u8] = b"0123456789abcdef0123456789abcdef";
const NOW: f64 = 1_800_000_000.0;
const GOOD: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvd25lciIsImV4cCI6NDEwMjQ0NDgwMCwiaWF0IjoxNzAwMDAwMDAwLCJuYW1lIjoiT3duZXIifQ.AaVTM8jSb9mpYU5XQGs0zoUVw2_kMPZ-lSDhe6s9YTw";
const EXPIRED: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvd25lciIsImV4cCI6MTAwMH0.JOPKgVOUfjVm6y9c2npbHTfV6AZDQPDB14XXF6Gr35g";
const NOSUB: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjQxMDI0NDQ4MDB9.sWsXA_2jSLVj898-a4GCxtzBXdodHLuy769ojE2Ifl4";
const NOEXP: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvd25lciJ9.BKdGq1DtTSkvMyCxDg5IiSmkJW9OVfoopuUnoC_wLu0";
const WRONGKEY: &str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvd25lciIsImV4cCI6NDEwMjQ0NDgwMH0.cze59B6frHs0esv8M_JrNVTOR3iJoNrb2GycGUy3ZBA";
const NONE: &str = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJvd25lciIsImV4cCI6NDEwMjQ0NDgwMH0.";

#[test]
fn the_hash_and_the_mac_match_the_known_vectors() {
    assert_eq!(jwt::hex(&jwt::sha256(b"abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    assert_eq!(jwt::hex(&jwt::sha256(b"")), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    assert_eq!(jwt::hex(&jwt::hmac_sha256(b"key", b"The quick brown fox jumps over the lazy dog")),
               "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8");
}

#[test]
fn a_good_token_gives_its_subject_and_claims() {
    let c = jwt::verify(GOOD, SECRET, NOW).unwrap();
    assert_eq!(c["sub"], "owner");
    assert_eq!(c["name"], "Owner");
}

#[test]
fn every_refusal_is_a_refusal() {
    for (name, t) in [("expired", EXPIRED), ("no sub", NOSUB), ("no exp", NOEXP), ("wrong key", WRONGKEY), ("alg none", NONE),
                      ("garbage", "not.a.token"), ("empty", ""), ("two parts", "a.b"), ("padding", "eyJhbGciOiJIUzI1NiJ9=.e30=.x")] {
        assert!(jwt::verify(t, SECRET, NOW).is_err(), "{name} was accepted");
    }
    assert!(jwt::verify(GOOD, b"too short", NOW).is_err(), "a secret under 32 bytes must not verify");
    assert!(jwt::verify(&"a".repeat(9000), SECRET, NOW).is_err(), "a token over 8192 bytes");
    assert!(jwt::verify(GOOD, SECRET, 4_200_000_000.0).is_err(), "after exp");
}

#[test]
fn the_token_rides_the_bearer_header_basic_or_the_cookie_never_the_query() {
    let h = |k: &str, v: &str| { let mut m = serde_json::Map::new(); m.insert(k.into(), serde_json::json!(v)); m };
    assert_eq!(token_of(&h("authorization", &format!("Bearer {GOOD}")), "", ""), Some(GOOD.to_string()));
    let basic = format!("Basic {}", jwt::base64_encode(format!("anyuser:{GOOD}").as_bytes()));
    assert_eq!(token_of(&h("authorization", &basic), "", ""), Some(GOOD.to_string()));
    assert_eq!(token_of(&h("cookie", &format!("a=b; membrane_jwt={GOOD}; c=d")), "", ""), Some(GOOD.to_string()), "the old shared name");
    assert_eq!(token_of(&h("cookie", &format!("a=b; membrane_jwt_23a8086e={GOOD}; c=d")), "", "membrane_jwt_23a8086e"), Some(GOOD.to_string()), "this seat's name");
    assert_eq!(token_of(&h("cookie", &format!("membrane_jwt_deadbeef={GOOD}")), "", "membrane_jwt_23a8086e"), None, "another seat's cookie is not ours");
    assert_eq!(token_of(&h("cookie", &format!("membrane_jwt_deadbeef=OTHER; membrane_jwt={GOOD}")), "", "membrane_jwt_23a8086e"), Some(GOOD.to_string()), "the old name only when ours is absent");
    assert_eq!(token_of(&serde_json::Map::new(), &format!("token={GOOD}"), ""), None);
    assert_eq!(token_of(&h("authorization", "Bearer"), "", ""), None);
}

#[test]
fn the_cookie_name_is_per_seat_from_a_hash_of_the_secret() {
    assert_eq!(jwt::cookie_name(SECRET), "membrane_jwt_23a8086e");
    assert_ne!(jwt::cookie_name(SECRET), jwt::cookie_name(b"another secret of at least 32 bytes!!"));
}
