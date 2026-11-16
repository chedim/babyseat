//! THE Sql DRIVER (#1143, the operator 2026-11-14): a second SQLite file as ENTITIES and a driver effect; no builtin.
//!   SqlSource:<name> {path, mode: ro (default) | rw}      SqlQuery:<name> {source, sql}      SqlQuery:<name>.run = {id, args: [...], max_rows?}
//! - One connection per source, opened lazily by the first run and opened again when `path` or `mode` was rewritten since. `ro` opens with the
//!   immutable URI (SQLite takes no lock and writes nothing: the file must not change while the seat runs); `rw` opens read-write, an existing file.
//! - The seat's OWN store file is never a source: a path that resolves to it (symlinks and hard links included) is refused with a visible reason.
//! - Queries on a source are serialized (its connection is locked for the run); a run is an effect of the slow-driver pool (`effect_limited`), so a long
//!   query never holds the VM. The statement is prepared on first use and kept in the connection's statement cache: `prepared` and `reused` in the
//!   receipt count the parses and the reuses (the cache holds `CACHE_STATEMENTS` texts; one more flushes it and the counters stay true).
//! - The receipt {id, status, rc, rows, changes, ms, started, finished, head, truncated?, prepared, reused}: rc 0 ok, 1 the statement failed (SQLite's
//!   words are `head`), 2 the run was refused (no source, own store, a bad request, the file would not open). Rows are dicts, a BLOB reads as null.
//!   The receipt is journalled by the host (writer `record:`), so a replay never asks the file again.
//! - A second receipt goes to the SOURCE entity (`open_result` {status, rc, head, path, mode}) whenever an open was tried: the source reads as an Error
//!   with the reason when it failed.
use std::collections::HashSet;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Map, Value as J};

use crate::store::{Store, BUSY_TIMEOUT_MS};

/// how many statement texts a connection keeps prepared before the cache is flushed whole (a count of statement texts, flagged as a decision)
pub const CACHE_STATEMENTS: usize = 256;

#[derive(Default)]
pub struct Source {
    path: String,
    mode: String,
    cx: Option<Connection>,
    seen: HashSet<String>,
    prepared: u64,
    reused: u64,
}

fn epoch() -> f64 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0) }
fn round3(x: f64) -> f64 { (x * 1000.0).round() / 1000.0 }

fn receipt(id: &J, rc: i64, head: String, mut extra: Map<String, J>, started: f64, clock: std::time::Instant) -> J {
    let mut m = Map::new();
    m.insert("id".into(), id.clone());
    m.insert("status".into(), json!(if rc == 0 { "ok" } else { "error" }));
    m.insert("rc".into(), json!(rc));
    m.insert("head".into(), json!(head.chars().take(crate::drivers::HEAD_CHARS).collect::<String>()));
    m.insert("started".into(), json!(round3(started)));
    m.insert("finished".into(), json!(round3(epoch())));
    m.insert("ms".into(), json!(clock.elapsed().as_millis() as u64));
    m.append(&mut extra);
    J::Object(m)
}

fn uri_encode(p: &str) -> String {
    let mut s = String::from("file:");
    for b in p.bytes() {
        if b.is_ascii_alphanumeric() || b"/-_.~".contains(&b) { s.push(b as char); } else { s.push_str(&format!("%{b:02X}")); }
    }
    s.push_str("?immutable=1");
    s
}

fn same_file(a: &Path, b: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    if let (Ok(x), Ok(y)) = (a.canonicalize(), b.canonicalize()) { if x == y { return true; } }
    matches!((std::fs::metadata(a), std::fs::metadata(b)), (Ok(x), Ok(y)) if x.dev() == y.dev() && x.ino() == y.ino())
}

/// Open `path` as `mode`; the seat's own store is refused first.
fn open(st: &Store, path: &str, mode: &str) -> Result<Connection, String> {
    if mode != "ro" && mode != "rw" { return Err(format!("refused: mode is ro or rw (it was {mode:?})")); }
    let p = crate::drivers::resolve(&st.dir, path);
    if !st.path.is_empty() && same_file(&p, Path::new(&st.path)) {
        return Err(format!("refused: {} is the seat's own store, never a source (the field is written by the engine only); copy the file and open the copy", p.display()));
    }
    let name = p.to_string_lossy().to_string();
    let cx = if mode == "ro" {
        Connection::open_with_flags(uri_encode(&name), OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI | OpenFlags::SQLITE_OPEN_NO_MUTEX)
    } else {
        Connection::open_with_flags(&name, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX)
    }.map_err(|e| format!("{}: {e}", p.display()))?;
    cx.busy_timeout(Duration::from_millis(BUSY_TIMEOUT_MS)).map_err(|e| format!("{}: {e}", p.display()))?;
    if mode == "ro" { cx.execute_batch("PRAGMA query_only = 1").map_err(|e| format!("{}: {e}", p.display()))?; }
    cx.query_row("SELECT count(*) FROM sqlite_master", [], |_| Ok(())).map_err(|e| format!("{}: {e}", p.display()))?;   // a file that is no database fails here
    cx.set_prepared_statement_cache_capacity(CACHE_STATEMENTS + 1);
    Ok(cx)
}

/// One run: -> the receipt. `props` hold the query's own props plus `source_eid`, `source_path`, `source_mode` (fields.rs, from the SqlSource entity).
pub fn run(st: &Store, eid: &str, req: &J, props: &Map<String, J>) -> J {
    let (started, clock) = (epoch(), std::time::Instant::now());
    let id = req.get("id").cloned().unwrap_or(J::Null);
    let refuse = |why: String| receipt(&id, 2, why, Map::new(), started, clock);
    let text = |k: &str| props.get(k).and_then(|v| v.as_str()).filter(|s| !s.trim().is_empty());
    let Some(sql) = text("sql") else { return refuse(format!("{eid} has no `sql`: write {eid}.sql = '<one statement, ? for each argument>' first")) };
    let Some(src_name) = text("source") else { return refuse(format!("{eid} has no `source`: write {eid}.source = '<name>' (the SqlSource:<name> to ask)")) };
    let Some(src_eid) = props.get("source_eid").and_then(|v| v.as_str()) else { return refuse(format!("the source SqlSource:{src_name} does not exist: write SqlSource:{src_name}.path = '<file>' first")) };
    let Some(path) = props.get("source_path").and_then(|v| v.as_str()).filter(|s| !s.trim().is_empty()) else { return refuse(format!("{src_eid} has no `path`: write {src_eid}.path = '<file>'")) };
    let mode = props.get("source_mode").and_then(|v| v.as_str()).unwrap_or("ro");
    let args: Vec<J> = match req.get("args") {
        None | Some(J::Null) => vec![],
        Some(J::Array(a)) => a.clone(),
        Some(other) => return refuse(format!("args must be a list of values bound to the ? of the statement (it was {other}); the statement text is never changed by them")),
    };
    let max_rows: Option<usize> = match req.get("max_rows").filter(|v| !v.is_null()) {
        None => None,
        Some(v) => match v.as_u64() { Some(n) => Some(n as usize), None => return refuse(format!("max_rows must be a whole number, 0 or more (it was {v}); leave it out to take every row")) },
    };
    let src = st.sql_source(src_eid);
    let mut g = src.lock().unwrap_or_else(|e| e.into_inner());       // the queries of one source go one after the other
    if g.cx.is_none() || g.path != path || g.mode != mode {
        g.cx = None; g.seen.clear(); g.prepared = 0; g.reused = 0;
        g.path = path.to_string(); g.mode = mode.to_string();
        let tried = open(st, path, mode);
        let open_rec = match &tried {
            Ok(_) => json!({"id": id, "status": "ok", "rc": 0, "head": format!("opened {path} ({mode})"), "path": path, "mode": mode}),
            Err(e) => json!({"id": id, "status": "error", "rc": 2, "head": e, "path": path, "mode": mode}),
        };
        st.note_receipt(src_eid, "open", open_rec);
        match tried { Ok(cx) => g.cx = Some(cx), Err(e) => return refuse(e) }
    }
    let g = &mut *g;
    let cx = g.cx.as_ref().unwrap();
    if !g.seen.contains(sql) {
        if g.seen.len() >= CACHE_STATEMENTS { cx.flush_prepared_statement_cache(); g.seen.clear(); }
        g.seen.insert(sql.to_string());
        g.prepared += 1;
    } else { g.reused += 1; }
    let (np, nr) = (g.prepared, g.reused);
    let counters = || { let mut m = Map::new(); m.insert("prepared".into(), json!(np)); m.insert("reused".into(), json!(nr)); m };
    let mut stmt = match cx.prepare_cached(sql) { Ok(s) => s, Err(e) => { g.seen.remove(sql); return receipt(&id, 1, e.to_string(), counters(), started, clock); } };
    if stmt.parameter_count() != args.len() {
        let why = format!("the statement takes {} argument(s), got {}", stmt.parameter_count(), args.len());
        return receipt(&id, 1, why, counters(), started, clock);
    }
    let names: Vec<String> = stmt.column_names().into_iter().map(String::from).collect();
    let writes = !stmt.readonly();
    let before = cx.total_changes();
    let bound: Vec<rusqlite::types::Value> = args.iter().map(crate::sqlexec::bind).collect();
    let mut rows: Vec<J> = vec![];
    let mut truncated = false;
    let outcome = (|| -> Result<(), rusqlite::Error> {
        let mut r = stmt.query(rusqlite::params_from_iter(bound.iter()))?;
        while let Some(row) = r.next()? {
            if let Some(n) = max_rows { if rows.len() >= n { truncated = true; break; } }
            let mut m = Map::new();
            for (i, n) in names.iter().enumerate() { m.insert(n.clone(), crate::sqlexec::cell(row.get_ref(i)?)); }
            rows.push(J::Object(m));
        }
        Ok(())
    })();
    let changes = if writes { cx.total_changes() - before } else { 0 };
    let mut extra = counters();
    match outcome {
        Err(e) => receipt(&id, 1, e.to_string(), extra, started, clock),
        Ok(()) => {
            extra.insert("rows".into(), J::Array(rows.clone()));
            extra.insert("changes".into(), json!(changes));
            if truncated { extra.insert("truncated".into(), json!(true)); }
            let head = format!("{} row(s){}{}", rows.len(), if writes { format!(", {changes} change(s)") } else { String::new() }, if truncated { " (truncated at max_rows)" } else { "" });
            receipt(&id, 0, head, extra, started, clock)
        }
    }
}

pub type SourceSlot = Arc<Mutex<Source>>;
