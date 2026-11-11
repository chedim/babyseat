//! The SQLite field store, native (#1111): the one SQLite connection of the process lives here, not in Python's
//! `sqlite3`. Same contract and same file layout as `membrane/sqlitedb.py` had: `<directory>/<name>.sqlite3`, WAL, one
//! table per collection (`c_<coll>`: `k TEXT PRIMARY KEY, body TEXT`), value indexes on json_extract(body, '$.<path>'),
//! nested transactions that commit only at the outermost `end`, and the SQL++ subset the callers use.
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use pyo3::IntoPyObjectExt;
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{Map, Value as J};
use std::collections::HashSet;
use std::sync::Mutex;

type R<T> = Result<T, String>;

fn dbe(e: rusqlite::Error) -> String { e.to_string() }

fn ident(s: &str) -> bool {
    let mut c = s.chars();
    match c.next() {
        Some(f) if f.is_ascii_alphabetic() || f == '_' => c.all(|x| x.is_ascii_alphanumeric() || x == '_'),
        _ => false,
    }
}

fn table(coll: &str) -> R<String> {
    if !ident(coll) { return Err(format!("a collection name is a plain identifier: {coll:?}")); }
    Ok(format!("c_{coll}"))
}

fn path(expr: &str) -> R<Vec<String>> {
    let parts: Vec<String> = expr.split('.').map(|p| p.trim().to_string()).collect();
    if parts.is_empty() || !parts.iter().all(|p| ident(p)) { return Err(format!("not a field path: {expr:?}")); }
    Ok(parts)
}

fn get<'a>(doc: &'a J, parts: &[String]) -> Option<&'a J> {
    let mut d = doc;
    for p in parts { d = d.as_object()?.get(p)?; }
    Some(d)
}

/// `SELECT <fields> FROM <scope>.<coll> [WHERE <path> IS VALUED] [ORDER BY <path> [ASC|DESC]]`, parsed.
struct Select { table: String, projs: Vec<(String, Option<Vec<String>>)>, order: Option<Vec<String>>, desc: bool, valued: Option<Vec<String>> }

fn parse_select(sql: &str) -> R<Select> {
    let refuse = || format!("query not supported by the SQLite store: {:?}", sql.chars().take(120).collect::<String>());
    let toks: Vec<&str> = sql.split_whitespace().collect();
    if toks.len() < 4 || !toks[0].eq_ignore_ascii_case("select") { return Err(refuse()); }
    let from = toks.iter().position(|t| t.eq_ignore_ascii_case("from")).ok_or_else(refuse)?;
    if from < 2 || from + 1 >= toks.len() { return Err(refuse()); }
    let proj = toks[1..from].join(" ");
    let scope = toks[from + 1];
    let (sc, coll) = scope.split_once('.').ok_or_else(refuse)?;
    let word = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_');
    if !word(sc) || !word(coll) { return Err(refuse()); }
    let mut projs = Vec::new();
    for item in proj.split(',') {
        let t: Vec<&str> = item.split_whitespace().collect();
        if t.is_empty() { return Err(format!("not a projection: {:?}", item.trim())); }
        let (e, alias) = if t.len() >= 3 && t[t.len() - 2].eq_ignore_ascii_case("as") && word(t[t.len() - 1]) {
            (t[..t.len() - 2].join(" "), Some(t[t.len() - 1].to_string()))
        } else { (t.join(" "), None) };
        if e.eq_ignore_ascii_case("META().id") { projs.push((alias.unwrap_or_else(|| "id".into()), None)); }
        else { let p = path(&e)?; projs.push((alias.unwrap_or_else(|| p[p.len() - 1].clone()), Some(p))); }
    }
    let mut i = from + 2;
    let mut valued = None;
    if i < toks.len() && toks[i].eq_ignore_ascii_case("where") {
        if i + 3 < toks.len() && toks[i + 2].eq_ignore_ascii_case("is") && toks[i + 3].eq_ignore_ascii_case("valued") {
            valued = Some(path(toks[i + 1])?);
            i += 4;
        } else { return Err(refuse()); }
    }
    let (mut order, mut desc) = (None, false);
    if i < toks.len() && toks[i].eq_ignore_ascii_case("order") {
        if i + 2 < toks.len() && toks[i + 1].eq_ignore_ascii_case("by") {
            order = Some(path(toks[i + 2])?);
            i += 3;
            if i < toks.len() && (toks[i].eq_ignore_ascii_case("asc") || toks[i].eq_ignore_ascii_case("desc")) {
                desc = toks[i].eq_ignore_ascii_case("desc");
                i += 1;
            }
        } else { return Err(refuse()); }
    }
    if i != toks.len() { return Err(refuse()); }
    Ok(Select { table: table(coll)?, projs, order, desc, valued })
}

struct Inner { cx: Option<Connection>, depth: usize, colls: HashSet<String> }

impl Inner {
    fn cx(&self) -> R<&Connection> { self.cx.as_ref().ok_or_else(|| "the database is closed".to_string()) }

    fn coll(&mut self, name: &str) -> R<String> {
        if self.colls.contains(name) { return Ok(format!("c_{name}")); }
        let t = table(name)?;
        self.cx()?.execute_batch(&format!("CREATE TABLE IF NOT EXISTS {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")).map_err(dbe)?;
        self.colls.insert(name.to_string());
        Ok(t)
    }

    fn query(&self, sql: &str) -> R<Vec<J>> {
        let s = parse_select(sql)?;
        let cx = self.cx()?;
        let has: Option<i64> = cx.query_row("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", params![s.table], |r| r.get(0))
            .optional().map_err(dbe)?;
        // as Couchbase Lite: a query never creates its collection
        if has.is_none() { return Err(format!("no collection {:?}", &s.table[2..])); }
        let mut q = format!("SELECT k, body FROM {}", s.table);
        match &s.order {
            Some(o) => q.push_str(&format!(" ORDER BY json_extract(body, '$.{}') {}, rowid", o.join("."), if s.desc { "DESC" } else { "ASC" })),
            None => q.push_str(" ORDER BY rowid"),
        }
        let mut st = cx.prepare(&q).map_err(dbe)?;
        let mut rows = st.query([]).map_err(dbe)?;
        let mut out = Vec::new();
        while let Some(r) = rows.next().map_err(dbe)? {
            let k: String = r.get(0).map_err(dbe)?;
            let body: String = r.get(1).map_err(dbe)?;
            let doc: J = match serde_json::from_str(&body) { Ok(d) => d, Err(_) => continue };
            if let Some(v) = &s.valued { if matches!(get(&doc, v), None | Some(J::Null)) { continue; } }
            let mut row = Map::new();
            for (alias, parts) in &s.projs {
                match parts {
                    None => { row.insert(alias.clone(), J::String(k.clone())); }
                    Some(p) => if let Some(v) = get(&doc, p) { row.insert(alias.clone(), v.clone()); },
                }
            }
            out.push(J::Object(row));
        }
        Ok(out)
    }
}

fn to_py(py: Python<'_>, j: &J) -> PyResult<Py<PyAny>> {
    match j {
        J::Null => Ok(py.None()),
        J::Bool(b) => (*b).into_py_any(py),
        J::Number(n) => if let Some(i) = n.as_i64() { i.into_py_any(py) } else if let Some(u) = n.as_u64() { u.into_py_any(py) }
                        else { n.as_f64().unwrap_or(f64::NAN).into_py_any(py) },
        J::String(s) => s.as_str().into_py_any(py),
        J::Array(a) => { let l = PyList::empty(py); for x in a { l.append(to_py(py, x)?)?; } l.into_py_any(py) }
        J::Object(m) => { let d = PyDict::new(py); for (k, v) in m { d.set_item(k, to_py(py, v)?)?; } d.into_py_any(py) }
    }
}

fn err(e: String) -> PyErr { PyRuntimeError::new_err(e) }

#[pyclass]
pub struct Db { m: Mutex<Inner>, file: String }

impl Db {
    fn with<T: Send>(&self, py: Python<'_>, f: impl FnOnce(&mut Inner) -> R<T> + Send) -> PyResult<T> {
        py.detach(|| { let mut g = self.m.lock().unwrap_or_else(|e| e.into_inner()); f(&mut g) }).map_err(err)
    }
}

#[pymethods]
impl Db {
    #[new]
    fn new(name: &str, directory: &str) -> PyResult<Self> {
        std::fs::create_dir_all(directory).map_err(|e| err(e.to_string()))?;
        let file = std::path::Path::new(directory).join(format!("{name}.sqlite3")).to_string_lossy().to_string();
        let cx = Connection::open(&file).map_err(|e| err(dbe(e)))?;
        cx.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
             CREATE TABLE IF NOT EXISTS _vector_indexes (coll TEXT, name TEXT, PRIMARY KEY (coll, name));").map_err(|e| err(dbe(e)))?;
        Ok(Db { m: Mutex::new(Inner { cx: Some(cx), depth: 0, colls: HashSet::new() }), file })
    }

    #[getter]
    fn file(&self) -> String { self.file.clone() }

    /// The collection's table (created when missing).
    fn coll(&self, py: Python<'_>, name: String) -> PyResult<String> { self.with(py, move |g| g.coll(&name)) }

    /// The collections' names present in the file.
    fn tables(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        self.with(py, |g| {
            let cx = g.cx()?;
            let mut st = cx.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'c_%'").map_err(dbe)?;
            let v = st.query_map([], |r| r.get::<_, String>(0)).map_err(dbe)?.collect::<Result<Vec<_>, _>>().map_err(dbe)?;
            Ok(v)
        })
    }

    fn begin(&self, py: Python<'_>) -> PyResult<()> {
        self.with(py, |g| {
            if g.depth == 0 { g.cx()?.execute_batch("BEGIN").map_err(dbe)?; }
            g.depth += 1;
            Ok(())
        })
    }

    #[pyo3(signature = (commit=true))]
    fn end(&self, py: Python<'_>, commit: bool) -> PyResult<()> {
        self.with(py, move |g| {
            let r = if g.depth == 1 { g.cx()?.execute_batch(if commit { "COMMIT" } else { "ROLLBACK" }).map_err(dbe) } else { Ok(()) };
            g.depth = g.depth.saturating_sub(1);
            r
        })
    }

    /// Another caller's write must not ride inside an open batch: commit it now; returns the depth to `resume`.
    fn suspend(&self, py: Python<'_>) -> PyResult<usize> {
        self.with(py, |g| {
            let d = g.depth;
            if d > 0 { g.cx()?.execute_batch("COMMIT").map_err(dbe)?; }
            g.depth = 0;
            Ok(d)
        })
    }

    /// Reopen the batch `suspend` committed.
    fn resume(&self, py: Python<'_>, depth: usize) -> PyResult<()> {
        self.with(py, move |g| {
            if depth > 0 { g.cx()?.execute_batch("BEGIN").map_err(dbe)?; }
            g.depth = depth;
            Ok(())
        })
    }

    fn put_json(&self, py: Python<'_>, coll: String, key: String, js: String) -> PyResult<()> {
        self.with(py, move |g| {
            if key.is_empty() { return Err("a document needs an id".into()); }
            let t = g.coll(&coll)?;
            g.cx()?.execute(&format!("INSERT INTO {t} (k, body) VALUES (?1, ?2) ON CONFLICT(k) DO UPDATE SET body = excluded.body"),
                            params![key, js]).map_err(dbe)?;
            Ok(())
        })
    }

    fn get_json<'py>(&self, py: Python<'py>, coll: String, key: String) -> PyResult<Option<Bound<'py, PyBytes>>> {
        let r = self.with(py, move |g| {
            if key.is_empty() { return Ok(None); }
            let t = g.coll(&coll)?;
            g.cx()?.query_row(&format!("SELECT body FROM {t} WHERE k = ?1"), params![key], |r| r.get::<_, String>(0)).optional().map_err(dbe)
        })?;
        Ok(r.map(|s| PyBytes::new(py, s.as_bytes())))
    }

    fn exists(&self, py: Python<'_>, coll: String, key: String) -> PyResult<bool> {
        self.with(py, move |g| {
            if key.is_empty() { return Ok(false); }
            let t = g.coll(&coll)?;
            Ok(g.cx()?.query_row(&format!("SELECT 1 FROM {t} WHERE k = ?1"), params![key], |r| r.get::<_, i64>(0)).optional().map_err(dbe)?.is_some())
        })
    }

    fn purge(&self, py: Python<'_>, coll: String, key: String) -> PyResult<()> {
        self.with(py, move |g| {
            let t = g.coll(&coll)?;
            g.cx()?.execute(&format!("DELETE FROM {t} WHERE k = ?1"), params![key]).map_err(dbe)?;
            Ok(())
        })
    }

    fn count(&self, py: Python<'_>, coll: String) -> PyResult<i64> {
        self.with(py, move |g| {
            let t = g.coll(&coll)?;
            g.cx()?.query_row(&format!("SELECT COUNT(*) FROM {t}"), [], |r| r.get::<_, i64>(0)).map_err(dbe)
        })
    }

    fn value_index(&self, py: Python<'_>, coll: String, name: String, exprs: String) -> PyResult<()> {
        self.with(py, move |g| {
            if !ident(&name) { return Err(format!("an index name is a plain identifier: {name:?}")); }
            let mut cols = Vec::new();
            for e in exprs.split(',') { cols.push(format!("json_extract(body, '$.{}')", path(e.trim())?.join("."))); }
            let t = g.coll(&coll)?;
            g.cx()?.execute_batch(&format!("CREATE INDEX IF NOT EXISTS {name} ON {t} ({})", cols.join(", "))).map_err(dbe)
        })
    }

    fn has_index(&self, py: Python<'_>, coll: String, name: String) -> PyResult<bool> {
        self.with(py, move |g| {
            g.coll(&coll)?;
            let cx = g.cx()?;
            if cx.query_row("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?1", params![name], |r| r.get::<_, i64>(0)).optional().map_err(dbe)?.is_some() {
                return Ok(true);
            }
            Ok(cx.query_row("SELECT 1 FROM _vector_indexes WHERE coll = ?1 AND name = ?2", params![coll, name], |r| r.get::<_, i64>(0)).optional().map_err(dbe)?.is_some())
        })
    }

    /// Recorded only: the backend searches its vectors itself.
    #[pyo3(signature = (coll, name, expr, dims, centroids, probes=0, sq=8, lazy=true))]
    #[allow(clippy::too_many_arguments)] // the Python contract's signature (sqlitedb.PyDatabase.vector_index)
    fn vector_index(&self, py: Python<'_>, coll: String, name: String, expr: String, dims: i64, centroids: i64,
                    probes: i64, sq: i64, lazy: bool) -> PyResult<()> {
        let _ = (expr, dims, centroids, probes, sq, lazy);
        self.with(py, move |g| {
            g.cx()?.execute("INSERT OR IGNORE INTO _vector_indexes (coll, name) VALUES (?1, ?2)", params![coll, name]).map_err(dbe)?;
            Ok(())
        })
    }

    /// Nothing to do: the vectors are not the database's.
    #[pyo3(signature = (coll, name, to_vec, limit=500))]
    fn update_lazy(&self, coll: String, name: String, to_vec: Py<PyAny>, limit: i64) -> i64 {
        let _ = (coll, name, to_vec, limit);
        0
    }

    #[pyo3(signature = (sql, params=None))]
    fn query(&self, py: Python<'_>, sql: String, params: Option<Py<PyAny>>) -> PyResult<Py<PyAny>> {
        let _ = params;
        let rows = self.with(py, move |g| g.query(&sql))?;
        let l = PyList::empty(py);
        for r in &rows { l.append(to_py(py, r)?)?; }
        l.into_py_any(py)
    }

    /// The rows, as `query` (the whole answer is read under the connection, then handed over).
    #[pyo3(signature = (sql, params=None))]
    fn iter_query(&self, py: Python<'_>, sql: String, params: Option<Py<PyAny>>) -> PyResult<Py<PyAny>> {
        self.query(py, sql, params)
    }

    /// Commit an open batch, fold the WAL into the file, close the connection (idempotent).
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        self.with(py, |g| {
            if let Some(cx) = g.cx.take() {
                if g.depth > 0 { let _ = cx.execute_batch("COMMIT"); }
                let _ = cx.execute_batch("PRAGMA wal_checkpoint(TRUNCATE)");
                drop(cx);
            }
            g.depth = 0;
            g.colls.clear();
            Ok(())
        })
    }
}
