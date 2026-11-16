//! The engine's `sql()` reads, on the process's one SQLite (#1111, review of #1131): the same bundled SQLite the native
//! store (`db.rs`) writes through, never CPython's `sqlite3`. Same rules the Python reader (`membrane/sqlread.py`) had:
//! read-only connections (`mode=ro` + `query_only`), a statement that is not a pure read is refused before it runs
//! (`sqlite3_stmt_readonly`), arguments are bound and never spliced, a locked database is waited for up to 5 s, no row
//! cap and no time limit; `interrupt(token)` stops a running query (a getter asked another question, or the seat stops).
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rusqlite::types::{Value as SqlValue, ValueRef};
use rusqlite::{Connection, InterruptHandle, OpenFlags};
use serde_json::{Map, Value as J};

use crate::store::{SqlExec, BUSY_TIMEOUT_MS};

const ONLY_READS: &str = "sql: only reads (SELECT / WITH); the field writes through the engine";
const INTERRUPTED: &str = "sql: interrupted (the getter asked another question, or the seat is stopping)";

struct Pooled { cx: Connection, handle: Arc<InterruptHandle> }

#[derive(Default)]
struct State {
    idle: Vec<Pooled>,
    running: HashMap<u64, Arc<InterruptHandle>>,
    cancelled: HashSet<u64>,
}

/// One field database, read-only, on this process's SQLite. Thread-safe: each query takes a connection of its own from
/// a small pool; `interrupt` reaches a running one.
pub struct NativeSqlExec { path: String, st: Arc<Mutex<State>> }

fn open(path: &str) -> Result<Pooled, String> {
    let cx = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI
        | OpenFlags::SQLITE_OPEN_NO_MUTEX).map_err(|e| format!("store {path}: {e}"))?;
    cx.busy_timeout(Duration::from_millis(BUSY_TIMEOUT_MS)).map_err(|e| format!("store {path}: {e}"))?;
    cx.execute_batch("PRAGMA query_only = 1").map_err(|e| format!("store {path}: {e}"))?;
    let handle = Arc::new(cx.get_interrupt_handle());
    Ok(Pooled { cx, handle })
}

fn word(e: &rusqlite::Error) -> String {
    match e {
        rusqlite::Error::InvalidParameterCount(got, want) =>
            format!("sql: the query takes {want} argument(s), got {got}"),
        rusqlite::Error::SqliteFailure(f, msg) => {
            let m = msg.clone().unwrap_or_else(|| f.to_string());
            if f.code == rusqlite::ErrorCode::OperationInterrupted || m.contains("interrupted") { INTERRUPTED.to_string() }
            else if f.code == rusqlite::ErrorCode::ReadOnly || m.contains("not authorized") || m.contains("readonly")
                || m.contains("read-only") { ONLY_READS.to_string() }
            else { format!("sql: {m}") }
        }
        other => format!("sql: {other}"),
    }
}

pub(crate) fn bind(v: &J) -> SqlValue {
    match v {
        J::Null => SqlValue::Null,
        J::Bool(b) => SqlValue::Integer(*b as i64),
        J::Number(n) => n.as_i64().map(SqlValue::Integer).unwrap_or_else(|| SqlValue::Real(n.as_f64().unwrap_or(f64::NAN))),
        J::String(s) => SqlValue::Text(s.clone()),
        other => SqlValue::Text(other.to_string()),      // a list or a dict goes in as JSON text (json_each() reads it)
    }
}

pub(crate) fn cell(v: ValueRef<'_>) -> J {
    match v {
        ValueRef::Null | ValueRef::Blob(_) => J::Null,
        ValueRef::Integer(i) => J::from(i),
        ValueRef::Real(f) => if f.is_finite() { J::from(f) } else { J::Null },
        ValueRef::Text(t) => J::String(String::from_utf8_lossy(t).into_owned()),
    }
}

impl NativeSqlExec {
    pub fn new(path: &str) -> Result<Self, String> {
        let first = open(path)?;                          // fail now when the store can't be opened
        let mut st = State::default();
        st.idle.push(first);
        Ok(NativeSqlExec { path: path.to_string(), st: Arc::new(Mutex::new(st)) })
    }

    fn take(&self) -> Result<Pooled, String> {
        let got = self.st.lock().unwrap_or_else(|e| e.into_inner()).idle.pop();
        match got { Some(p) => Ok(p), None => open(&self.path) }
    }

    fn give(&self, p: Pooled) { self.st.lock().unwrap_or_else(|e| e.into_inner()).idle.push(p); }
}

impl SqlExec for NativeSqlExec {
    fn run(&self, token: u64, sql: &str, args: &[J]) -> Result<Vec<J>, String> {
        let p = self.take()?;
        let cancelled = {
            let mut g = self.st.lock().unwrap_or_else(|e| e.into_inner());
            g.running.insert(token, p.handle.clone());
            g.cancelled.contains(&token)
        };
        // an interrupt can come before the query starts: it is remembered, and seen here
        let out = if cancelled { Err(INTERRUPTED.to_string()) } else { query(&p.cx, sql, args) };
        {
            let mut g = self.st.lock().unwrap_or_else(|e| e.into_inner());
            g.running.remove(&token);
            g.cancelled.remove(&token);
        }
        self.give(p);
        out
    }

    fn check(&self, sql: &str, nargs: usize) -> Result<(), String> {
        if sql.trim_start().get(..7).map(|s| s.eq_ignore_ascii_case("explain")).unwrap_or(false) { return Ok(()); }
        let p = self.take()?;
        let r = (|| {
            let stmt = p.cx.prepare(sql).map_err(|e| word(&e))?;
            if !stmt.readonly() { return Err(ONLY_READS.to_string()); }
            if stmt.parameter_count() != nargs {
                return Err(format!("sql: the query takes {} argument(s), got {}", stmt.parameter_count(), nargs));
            }
            Ok(())
        })();
        self.give(p);
        r
    }

    fn write(&self, statements: &[(String, Vec<J>)]) -> Result<(i64, Vec<J>), String> {
        if statements.is_empty() { return Err("sql: nothing to run (a statement, or statements)".into()); }
        for (sql, _) in statements { allowed(sql)?; }
        let mut cx = Connection::open_with_flags(&self.path, OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI
            | OpenFlags::SQLITE_OPEN_NO_MUTEX).map_err(|e| format!("store {}: {e}", self.path))?;
        cx.busy_timeout(Duration::from_millis(BUSY_TIMEOUT_MS)).map_err(|e| word(&e))?;
        let tx = cx.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate).map_err(|e| word(&e))?;
        let (mut changed, mut returned) = (0i64, Vec::new());
        for (sql, args) in statements {
            let mut stmt = tx.prepare(sql).map_err(|e| word(&e))?;
            if stmt.readonly() { return Err("sql: a read is a getter: sql(...) (Store.run writes)".into()); }
            if stmt.parameter_count() != args.len() {
                return Err(format!("sql: the statement takes {} argument(s), got {}", stmt.parameter_count(), args.len()));
            }
            let cols: Vec<String> = stmt.column_names().into_iter().map(|c| c.to_string()).collect();
            let vals: Vec<SqlValue> = args.iter().map(bind).collect();
            if cols.is_empty() {
                changed += stmt.execute(rusqlite::params_from_iter(vals.iter())).map_err(|e| word(&e))? as i64;
            } else {
                let mut rows = stmt.query(rusqlite::params_from_iter(vals.iter())).map_err(|e| word(&e))?;
                while let Some(r) = rows.next().map_err(|e| word(&e))? {
                    let mut m = Map::new();
                    for (i, c) in cols.iter().enumerate() { m.insert(c.clone(), cell(r.get_ref(i).map_err(|e| word(&e))?)); }
                    returned.push(J::Object(m));
                    changed += 1;
                }
            }
        }
        tx.commit().map_err(|e| word(&e))?;
        Ok((changed, returned))
    }

    fn interrupt(&self, token: u64) {
        let handle = {
            let mut g = self.st.lock().unwrap_or_else(|e| e.into_inner());
            g.cancelled.insert(token);                    // kept until its run ends: a run not started yet sees it too
            g.running.get(&token).cloned()
        };
        let Some(h) = handle else { return };
        let st = self.st.clone();
        // SQLite drops an interrupt that comes while no statement runs: fire it again until the run ends
        std::thread::Builder::new().name("membrane-sql-interrupt".into()).spawn(move || loop {
            { if !st.lock().unwrap_or_else(|e| e.into_inner()).running.contains_key(&token) { return; } h.interrupt(); }
            std::thread::sleep(Duration::from_millis(20));
        }).ok();
    }
}

/// What `Store.run` may be: one INSERT / UPDATE / DELETE / REPLACE (or a WITH leading one), never DDL, ATTACH, PRAGMA or
/// VACUUM, never two statements in one string.
fn allowed(sql: &str) -> Result<(), String> {
    let t = sql.trim();
    let first = t.split(|c: char| !c.is_ascii_alphabetic()).next().unwrap_or("").to_ascii_lowercase();
    if !["insert", "update", "delete", "replace", "with"].contains(&first.as_str()) {
        return Err(format!("sql: Store.run takes INSERT, UPDATE or DELETE (got {:?}); reads are sql(...) getters, the schema is the store's own", first));
    }
    if t.trim_end_matches(';').contains(';') { return Err("sql: one statement per entry (use statements: [...] for several)".into()); }
    Ok(())
}

fn query(cx: &Connection, sql: &str, args: &[J]) -> Result<Vec<J>, String> {
    let mut stmt = cx.prepare(sql).map_err(|e| word(&e))?;
    if !stmt.readonly() { return Err(ONLY_READS.to_string()); }
    if stmt.parameter_count() != args.len() {
        return Err(format!("sql: the query takes {} argument(s), got {}", stmt.parameter_count(), args.len()));
    }
    let cols: Vec<String> = stmt.column_names().into_iter().map(|c| c.to_string()).collect();
    let vals: Vec<SqlValue> = args.iter().map(bind).collect();
    let mut rows = stmt.query(rusqlite::params_from_iter(vals.iter())).map_err(|e| word(&e))?;
    let mut out = Vec::new();
    while let Some(r) = rows.next().map_err(|e| word(&e))? {
        let mut m = Map::new();
        for (i, c) in cols.iter().enumerate() {
            m.insert(c.clone(), cell(r.get_ref(i).map_err(|e| word(&e))?));
        }
        out.push(J::Object(m));
    }
    Ok(out)
}
