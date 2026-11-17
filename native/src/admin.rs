//! THE ADMIN'S TOKEN (#1143, operator 2026-11-13 and 11-14; docs/auth.md). A localhost peer is not the admin (#842): once the door has a secret, a tool on this
//! machine presents a token like anyone else. At boot, when the fact `door_secret_file` is set, `cliffvm run`
//!   1. makes sure the secret exists (64 hex characters of /dev/urandom, mode 0600, created once and kept),
//!   2. mints an admin JWT (sub = `admin`; life = the fact `admin_token_ttl_s`, default 30 days) with `jwt::mint`, and
//!   3. replaces `<seat_data_dir>/admin.token` with it (mode 0600, a temp file renamed over it, so a reader never sees half a token).
//! The token value is held in this process's environment as `MEMBRANE_ADMIN_TOKEN` and nowhere else: a chat turn's `Command` names it in `env_from`
//! (docs/cliffvm.md), so it is in no property, no journal row and no argv. The seat without a secret (the barest) does none of this.
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value as J};

/// The environment variable that holds the admin's token for this process's children's `env_from`.
pub const TOKEN_ENV: &str = "MEMBRANE_ADMIN_TOKEN";
/// How long the admin token lives unless `admin_token_ttl_s` says: 30 days in seconds (a threshold flagged for the operator).
/// The one identity of this release: every verified request is the admin, and this is the `sub` the seat gives its own tokens.
pub const SUB: &str = "admin";
pub const DEFAULT_TTL_S: f64 = 2_592_000.0;

fn private_write(path: &Path, text: &str) -> Result<(), String> {
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(text.as_bytes()).map_err(|e| format!("{}: {e}", path.display()))
}

/// The secret at `path`: kept when it is there and not empty, else made (32 random bytes as 64 hex characters, mode 0600).
pub fn ensure_secret(path: &Path) -> Result<Vec<u8>, String> {
    // only a file that is NOT THERE (or empty) is made: a file that is there and cannot be read (permissions, a folder) is a refusal, never a silent new secret that logs everyone out
    match std::fs::read_to_string(path) {
        Ok(t) if !t.trim().is_empty() => return Ok(t.trim().as_bytes().to_vec()),
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("{}: cannot read the door secret: {e} (the seat will not replace a secret it cannot read)", path.display())),
    }
    if let Some(dir) = path.parent() { std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?; }
    let mut raw = [0u8; 32];
    std::io::Read::read_exact(&mut std::fs::File::open("/dev/urandom").map_err(|e| format!("/dev/urandom: {e}"))?, &mut raw).map_err(|e| format!("/dev/urandom: {e}"))?;
    let hex = crate::jwt::hex(&raw);
    let _ = std::fs::remove_file(path);   // an empty file left by an earlier run
    private_write(path, &hex)?;
    Ok(hex.into_bytes())
}

/// Replace `path` with `text` (mode 0600): a temp file in the same folder, renamed over it.
pub fn replace_private(path: &Path, text: &str) -> Result<(), String> {
    let tmp = path.with_extension(format!("tmp{}", std::process::id()));
    let _ = std::fs::remove_file(&tmp);
    private_write(&tmp, text)?;
    std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600)).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| { let _ = std::fs::remove_file(&tmp); format!("{}: {e}", path.display()) })
}

/// What the seat's peers need when the seat has a door secret: the secret (to mint each declared peer's token and to check every hello), the folder the tokens go in (`<data dir>/peers`) and their life
/// (the admin token's: the fact `admin_token_ttl_s`, 30 days when unset). A seat with no secret has none: its peers are not asked for a token.
pub struct PeerAuth { pub secret: Vec<u8>, pub dir: PathBuf, pub ttl: f64 }

pub fn peer_auth(facts: &[(String, J)]) -> Result<Option<PeerAuth>, String> {
    let fact = |k: &str| facts.iter().rev().find(|(n, _)| n == k).map(|(_, v)| v);
    let Some(secret_path) = fact("door_secret_file").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) else { return Ok(None) };
    let secret_path = PathBuf::from(secret_path);
    let secret = ensure_secret(&secret_path)?;
    let dir = fact("seat_data_dir").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(PathBuf::from)
        .or_else(|| secret_path.parent().map(|p| p.to_path_buf())).unwrap_or_else(|| PathBuf::from("."));
    let ttl = fact("admin_token_ttl_s").and_then(|v| v.as_f64()).unwrap_or(DEFAULT_TTL_S);
    Ok(Some(PeerAuth { secret, dir: dir.join("peers"), ttl }))
}

/// At boot: the facts say whether there is a door secret; -> the facts to add (`admin_token_file`), or none for a seat with no secret.
pub fn boot(facts: &[(String, J)], now: f64) -> Result<Map<String, J>, String> {
    let fact = |k: &str| facts.iter().rev().find(|(n, _)| n == k).map(|(_, v)| v);
    let Some(secret_path) = fact("door_secret_file").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) else { return Ok(Map::new()); };
    let secret_path = PathBuf::from(secret_path);
    let secret = ensure_secret(&secret_path)?;
    // the admin token sits in the seat's data folder; a seat that names none keeps it beside its secret
    let dir = fact("seat_data_dir").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(PathBuf::from)
        .or_else(|| secret_path.parent().map(|p| p.to_path_buf())).unwrap_or_else(|| PathBuf::from("."));
    let ttl = fact("admin_token_ttl_s").and_then(|v| v.as_f64()).unwrap_or(DEFAULT_TTL_S);
    let token = crate::jwt::mint(&secret, SUB, SUB, ttl, now)?;
    let file = dir.join("admin.token");
    replace_private(&file, &token)?;
    std::env::set_var(TOKEN_ENV, &token);
    let mut m = Map::new();
    m.insert("admin_token_file".into(), json!(file.to_string_lossy()));
    Ok(m)
}
