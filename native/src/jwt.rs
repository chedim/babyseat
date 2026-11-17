//! JWT for the door (operator, 2026-11-12: cliff talks to rust directly). The code is the `graphlink-auth` crate (github.com/chedim/graphlink), shared with the seat's peers: HS256 only, a shared
//! secret of at least 32 bytes, `exp` and `sub` required, `nbf`/`iat` checked with a 30 s leeway, a token of at most 8192 bytes, a refusal is only ever "invalid token". What stays here is the door's own: the cookie name.
pub use graphlink_auth::*;

/// This seat's cookie: `membrane_jwt_<8 hex of a hash of its secret>` (cookies are per host, not per port: two seats on one host
/// under one name overwrite each other's token). The old shared name is read when this seat's is absent.
pub fn cookie_name(secret: &[u8]) -> String {
    let mut m = b"membrane cookie\0".to_vec();
    m.extend_from_slice(secret);
    format!("membrane_jwt_{}", &hex(&sha256(&m))[..8])
}
