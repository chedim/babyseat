//! THE HOST LOOP, in Rust (docs/cliffvm.md): the minimum of membrane/fields_live.py that a process with no Python needs.
//! It owns the engine (Core + Fields), one SQLite connection for the entity rows and the journal, and the door's inbound
//! requests. What it does, in the order fields_live does it:
//!   - hears OUTSIDE writes (the door's requests, the boot's facts, a person's write) and gives them to the engine;
//!   - drains the engine's output (`Fields::out`) and applies every write to the store with the writer stamped as
//!     fields_live.stamp_writer / stamp_born_writer do: `trigger:field:<setter>` for the engine's own writes (replay
//!     re-derives them);
//!   - runs the clock (`Fields::tick`) and settles what the drivers' threads finished (queries, files, commands);
//!   - keeps the JOURNAL of the outside writes only. Replay = apply the journal to a fresh engine: the engine writes are
//!     computed again, never read from the journal.
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value as J};

use crate::fields::Fields;
use crate::plan::Engine;
use crate::vm::{Core, Host as EngineHost, Write};

/// The size the write-ahead log may reach before a tick truncates it (a threshold, flagged for the operator: SQLite resets the log at about 4 MB but never shrinks the file, and a reader holding a snapshot stops the reset).
pub const WAL_LIMIT_BYTES: u64 = 8 << 20;
pub const WRITER: &str = "trigger:field:";
/// the collection (table `c_field`) of the entity rows: k = eid, body = {cls, props, writers}
pub const ROWS: &str = "c_field";
/// the journal's collection (table `c_journal`): k = sequence number, body = a line
pub const JOURNAL: &str = "c_journal";
/// a journalled line longer than this (bytes of its JSON) is kept in RAM without its value (see `journal_push`); a threshold flagged for the operator
pub const JOURNAL_RAM_MAX_BYTES: usize = 262_144;
/// the writer of what the door pushes in (a request) and of what the boot tells (facts, started)
pub const DOOR_WRITER: &str = "host:door";
pub const BOOT_WRITER: &str = "host:boot";
/// A Pack entity (docs/cliffvm.md, "Pack entities"): `Pack:<name>`, its c1f in `text`; the host mirrors the outcome back as derived writes
pub const PACK_PREFIX: &str = "Pack:";
pub const PACK_CLASS: &str = "Pack";
pub const PACK_WRITER: &str = "pack";
/// The pack the host itself marks privileged (packs/boot.c1f): the root of trust.

/// The store writer for an engine write (fields_live.stamp_writer): derived.
pub fn stamp_writer(engine_writer: &str) -> String {
    // a RECORDED receipt (the Sql driver's: the answer of a file the replay must not ask again) is OUTSIDE, so it is journalled
    let w = engine_writer.trim_start_matches("field:");
    if w.starts_with("record:") { return format!("host:{w}"); }
    format!("{WRITER}{w}")
}

/// The store stamp of what a setter wrote while triggered by a VIRTUAL entity: OUTSIDE (journalled), since the trigger is not in the journal and a replay could not
/// derive the write again (`host:` stamps).
pub fn stamp_virtual_writer(engine_writer: &str) -> String { format!("host:virtual:{}", engine_writer.trim_start_matches("field:")) }

/// The writer for an entity the engine created (fields_live.stamp_born_writer).
pub fn stamp_born_writer(_cls: &str) -> String { format!("{WRITER}create") }

/// The class an eid names: after a `namespace::`, up to the next colon (`membrane::Chat:main` is `Chat`).
pub fn class_of_eid(eid: &str) -> String { crate::vm::class_of_eid(eid) }

/// The seat's own namespace for the graph link.
pub const SEAT_NS: &str = "membrane";

/// How many lazy rows the warm boot loads in the background by default (the boot fact `warm_rows`; a threshold, flagged for the operator).
pub const DEFAULT_WARM_ROWS: usize = 1000;
/// One slice of the warm boot loads at most this many rows and stops after this long (flagged for the operator: the loop must answer the door between slices).
pub const WARM_SLICE_ROWS: usize = 64;
pub const WARM_SLICE_MS: u64 = 20;

/// What a restore takes back of a stored row: its properties without the ones a GETTER wrote (the rebuild computes them again from the restored inputs, so a getter that
/// changed since the row was stored shows its new value).
fn restore_props(row: &J) -> Map<String, J> {
    let writers = row["writers"].as_object().cloned().unwrap_or_default();
    row["props"].as_object().cloned().unwrap_or_default().into_iter().filter(|(k, _)| !writers.get(k).and_then(|w| w.as_str()).map_or(false, is_getter_write)).collect()
}

/// The engine's loader of one stored row (`Fields::ensure`): the store's row of `eid`, as a restore would bring it.
fn stored_loader(db: Arc<Mutex<Connection>>) -> crate::vm::Loader {
    Box::new(move |eid: &str| {
        let db = db.lock().unwrap_or_else(|e| e.into_inner());
        let (body, updated): (String, f64) = db.query_row(&format!("SELECT body, updated FROM {ROWS} WHERE k = ?"), params![eid], |r| Ok((r.get(0)?, r.get(1)?))).optional().ok().flatten()?;
        let row: J = serde_json::from_str(&body).ok()?;
        Some(crate::vm::StoredRow { cls: row["cls"].as_str().unwrap_or("").to_string(), props: row["props"].as_object().cloned().unwrap_or_default(), updated, born_by: row["born_by"].as_str().map(String::from) })   // a getter's stored value too: the read is right away
    })
}

/// A store stamp of a value the engine computes again from its inputs (a getter's, a rebuild's).
pub fn is_getter_write(stamp: &str) -> bool { stamp.starts_with("trigger:field:getter:") || stamp == "trigger:field:rebuild" }

/// An outside write is anything the engine can not re-derive.
pub fn is_outside(stamp: &str) -> bool { !stamp.starts_with(WRITER) }

/// One line of the journal.
#[derive(Clone, Debug, PartialEq)]
pub enum Line {
    /// `by`: the cause of the birth (`born_by`), written only for a birth the engine made but a replay cannot recompute (a virtual trigger's); an older journal has none: no cause.
    Born { ts: f64, writer: String, eid: String, cls: String, props: Map<String, J>, by: Option<String> },
    Write { ts: f64, writer: String, eid: String, prop: String, new: J },
}

impl Line {
    pub fn writer(&self) -> &str { match self { Line::Born { writer, .. } | Line::Write { writer, .. } => writer } }
    pub fn to_json(&self) -> J {
        match self {
            Line::Born { ts, writer, eid, cls, props, by } => { let mut o = json!({"op": "born", "ts": ts, "writer": writer, "eid": eid, "cls": cls, "props": props}); if let Some(b) = by { o["by"] = json!(b); } o }
            Line::Write { ts, writer, eid, prop, new } => json!({"op": "write", "ts": ts, "writer": writer, "eid": eid, "prop": prop, "new": new}),
        }
    }
    pub fn from_json(j: &J) -> Result<Line, String> {
        let s = |k: &str| j.get(k).and_then(|v| v.as_str()).map(String::from).ok_or_else(|| format!("journal line: no {k}"));
        let ts = j.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
        match j.get("op").and_then(|v| v.as_str()) {
            Some("born") => Ok(Line::Born { ts, writer: s("writer")?, eid: s("eid")?, cls: s("cls")?, props: j.get("props").and_then(|p| p.as_object()).cloned().unwrap_or_default(), by: j.get("by").and_then(|b| b.as_str()).map(String::from) }),
            Some("write") => Ok(Line::Write { ts, writer: s("writer")?, eid: s("eid")?, prop: s("prop")?, new: j.get("new").cloned().unwrap_or(J::Null) }),
            other => Err(format!("journal line: unknown op {other:?}")),
        }
    }
}

/// The engine's host callbacks: the pack classes bind by class, so none of the Python seat's lookups exist here.
pub struct NullHost;
impl EngineHost for NullHost {
    fn resolve(&self, _: &str) -> Option<String> { None }
    fn resolve_exact(&self, _: &str) -> Option<String> { None }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

pub fn system_now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub struct Options {
    /// the SQLite file of the rows and the journal; None = in memory (nothing for the drivers to read)
    pub store: Option<String>,
    /// attach the drivers (the door, files, commands, `sql(...)`): off for a replay, which must not redo effects
    pub effects: bool,
    pub clock: Box<dyn Fn() -> f64 + Send + Sync>,
    /// the barest seat: the door verifies nothing (operator decision); `bind` is the address a listen binds (default 127.0.0.1)
    pub plain: bool,
    pub bind: Option<String>,
}

impl Options {
    pub fn new(store: Option<String>) -> Options { Options { store, effects: true, clock: Box::new(system_now), plain: false, bind: None } }
}

/// What `load_app` loaded: the pack names in load order, and the messages for what could not load.
#[derive(Debug, Default)]
pub struct Loaded { pub names: Vec<String>, pub msgs: Vec<String> }

pub struct Host {
    eng: Mutex<Engine>,
    db: Arc<Mutex<Connection>>,
    journal: Mutex<Vec<Line>>,
    /// the key of the NEXT stored journal line: continues from the highest stored key at open (the in-memory `journal` holds this run's lines only), so a restart appends and never replaces
    journal_next: std::sync::atomic::AtomicU64,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
    waiter: Option<crate::store::Waiter>,
    /// the last refusal of a Pack entity's text (parse, stale setter, PACK line, import clash): name -> message
    refusals: Mutex<std::collections::BTreeMap<String, String>>,
    packs_dirty: AtomicBool,
    /// the store's write-ahead log file (None in memory): folded back and truncated by `tick` once it is over WAL_LIMIT_BYTES
    wal: Option<std::path::PathBuf>,
    /// how many transactions are open (a nested `txn` joins the outer one) and how many commits the store has had (see `store_commits`)
    depth: std::sync::atomic::AtomicUsize,
    commits: std::sync::atomic::AtomicUsize,
    /// the rows a transaction has read or changed (eid -> row), parsed once; written back, the changed ones, when the outermost transaction ends
    pending: Mutex<Option<std::collections::HashMap<String, Pending>>>,
    /// whether the open transaction has executed a statement (an empty one is rolled back, not counted)
    wrote: AtomicBool,
    /// the time of the OUTSIDE write whose round is being applied (f64 bits; 0 = none): what that round's derived writes are stamped with, so a replay (the journal's time) ends with the same `updated`
    round_ts: std::sync::atomic::AtomicU64,
    /// the warm boot: how many lazy rows to load in the background (newest `updated` first) and the ones still waiting (docs/cliffvm.md)
    warm_rows: std::sync::atomic::AtomicUsize,
    warm: Mutex<std::collections::VecDeque<String>>,
    /// the memory policy (`evict_under_pressure`): its config (the boot facts) and the injected reading (tests)
    memory: Mutex<MemoryConfig>,
    rss_reading: Mutex<Option<RssReading>>,
}

/// The memory policy's configuration: the boot facts `memory_budget_mb`, `unload_soft`, `unload_hard`, `unload_batch` (none of them has a default number).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct MemoryConfig { pub budget_mb: Option<u64>, pub soft: Option<f64>, pub hard: Option<f64>, pub batch: Option<u64> }
impl MemoryConfig {
    /// The launcher's default (operator 2026-11-14): a fact left unnamed gets the default, a named one is kept. The budget is 10% of `total_bytes` (MB, rounded down), the
    /// hard line 90% of the budget, the batch 200; no soft line. With the machine's memory unknown (`None`) nothing is added, so nothing is evicted, as before.
    pub fn with_defaults(mut self, total_bytes: Option<u64>) -> MemoryConfig {
        let Some(total) = total_bytes else { return self };
        if self.budget_mb.is_none() { self.budget_mb = Some(total / 10 / (1 << 20)); }
        if self.hard.is_none() { self.hard = Some(0.9); }
        if self.batch.is_none() { self.batch = Some(200); }
        self
    }
}
/// The machine's physical memory in bytes (the container's limit when a cgroup sets a lower one): `hw.memsize` on macOS, `sysconf` on Linux; None when unreadable.
#[cfg(target_os = "macos")]
pub fn physical_memory_bytes() -> Option<u64> {
    let mut v: u64 = 0; let mut n = std::mem::size_of::<u64>();
    let name = std::ffi::CString::new("hw.memsize").ok()?;
    let rc = unsafe { libc::sysctlbyname(name.as_ptr(), &mut v as *mut u64 as *mut libc::c_void, &mut n, std::ptr::null_mut(), 0) };
    (rc == 0 && v > 0).then_some(v)
}
#[cfg(target_os = "linux")]
pub fn physical_memory_bytes() -> Option<u64> {
    let (pages, page) = unsafe { (libc::sysconf(libc::_SC_PHYS_PAGES), libc::sysconf(libc::_SC_PAGESIZE)) };
    let total = (pages > 0 && page > 0).then(|| pages as u64 * page as u64)?;
    // a container's limit (cgroup v2, then v1) when it is lower than the machine's
    let limit = ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"].iter()
        .filter_map(|p| std::fs::read_to_string(p).ok()).filter_map(|t| t.trim().parse::<u64>().ok()).filter(|n| *n > 0).min();
    Some(limit.map_or(total, |l| l.min(total)))
}
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn physical_memory_bytes() -> Option<u64> { None }
/// A resident-size reading in bytes (None: unreadable): the real one is `process_rss`; a test injects its own.
pub type RssReading = Box<dyn Fn() -> Option<u64> + Send + Sync>;

struct Pending { row: Option<(String, Map<String, J>, Map<String, J>)>, dirty: bool, updated: f64, by: Option<String> }

/// Holds the round's time for the length of an outside write; the previous one comes back when it ends.
struct RoundTs<'a> { h: &'a Host, prev: u64 }
impl Drop for RoundTs<'_> { fn drop(&mut self) { self.h.round_ts.store(self.prev, Ordering::SeqCst); } }

/// An open transaction of the store: everything the host writes while it lives is ONE commit (one sync), the rows it changed written once each.
pub struct Txn<'a> { h: &'a Host }
impl Drop for Txn<'_> { fn drop(&mut self) { self.h.end_txn(); } }

fn dbe(e: rusqlite::Error) -> String { e.to_string() }

/// The ids a live VIRTUAL entity holds as `{"$ref": id}` values (anywhere in its properties).
fn held_by_virtual(core: &Core, f: &Fields) -> std::collections::HashSet<String> {
    fn walk(v: &J, out: &mut std::collections::HashSet<String>) {
        match v {
            J::Object(m) => { if let Some(J::String(r)) = m.get("$ref") { out.insert(r.clone()); } for x in m.values() { walk(x, out); } }
            J::Array(a) => for x in a { walk(x, out); },
            _ => {}
        }
    }
    let mut out = std::collections::HashSet::new();
    for e in core.docs.values().filter(|e| f.storage_of(&e.cls) == crate::fields::Storage::Virtual) { for v in e.props.values() { walk(v, &mut out); } }
    out
}

/// The resident set size of this process in bytes: `/proc/self/statm` on Linux, `task_info(MACH_TASK_BASIC_INFO)` on macOS; None elsewhere or when unreadable.
#[cfg(target_os = "linux")]
pub fn process_rss() -> Option<u64> {
    let t = std::fs::read_to_string("/proc/self/statm").ok()?;
    let pages: u64 = t.split_whitespace().nth(1)?.parse().ok()?;
    let page = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    (page > 0).then(|| pages * page as u64)
}
#[cfg(target_os = "macos")]
#[allow(deprecated)]
pub fn process_rss() -> Option<u64> {
    unsafe {
        let mut info: libc::mach_task_basic_info = std::mem::zeroed();
        let mut count = (std::mem::size_of::<libc::mach_task_basic_info>() / std::mem::size_of::<libc::natural_t>()) as libc::mach_msg_type_number_t;
        let rc = libc::task_info(libc::mach_task_self(), libc::MACH_TASK_BASIC_INFO as libc::task_flavor_t, &mut info as *mut _ as libc::task_info_t, &mut count);
        (rc == 0).then(|| info.resident_size)
    }
}
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn process_rss() -> Option<u64> { None }

/// The shape of `c_field` (docs/cliffvm.md, "Storage modes"): next to `k` and `body`, the entity's own class (`cls`, indexed: the restore loads the RESIDENT classes by it)
/// and `updated`, the epoch time of its last write (indexed: the warm boot loads the newest first). A store of an older shape gets the columns added; `cls` is filled
/// from the body, and `updated` from the journal's last line about each entity (0 for an entity the journal never names: only outside writes are journalled).
/// Idempotent: the journal backfill runs only when the `updated` column is added, never over what later runs stamped.
fn migrate(db: &Connection) -> Result<(), String> {
    let cols: Vec<String> = db.prepare("SELECT name FROM pragma_table_info('c_field')").map_err(dbe)?.query_map([], |r| r.get(0)).map_err(dbe)?.filter_map(|r| r.ok()).collect();
    let had_updated = cols.iter().any(|c| c == "updated");
    if !cols.iter().any(|c| c == "cls") { db.execute_batch("ALTER TABLE c_field ADD COLUMN cls TEXT").map_err(dbe)?; }
    if !had_updated { db.execute_batch("ALTER TABLE c_field ADD COLUMN updated REAL NOT NULL DEFAULT 0").map_err(dbe)?; }
    db.execute_batch("CREATE INDEX IF NOT EXISTS c_field_cls ON c_field(cls, k); CREATE INDEX IF NOT EXISTS c_field_updated ON c_field(updated, k)").map_err(dbe)?;
    db.execute_batch("UPDATE c_field SET cls = json_extract(body, '$.cls') WHERE cls IS NULL").map_err(dbe)?;
    if !had_updated {
        let mut last: std::collections::HashMap<String, f64> = Default::default();
        {
            let mut st = db.prepare(&format!("SELECT body FROM {JOURNAL} ORDER BY k")).map_err(dbe)?;
            for b in st.query_map([], |r| r.get::<_, String>(0)).map_err(dbe)?.filter_map(|r| r.ok()) {
                if let Ok(j) = serde_json::from_str::<J>(&b) {
                    if let (Some(e), Some(ts)) = (j.get("eid").and_then(|e| e.as_str()), j.get("ts").and_then(|t| t.as_f64())) { let m = last.entry(e.to_string()).or_insert(ts); if ts > *m { *m = ts; } }
                }
            }
        }
        db.execute_batch("BEGIN").map_err(dbe)?;
        for (e, ts) in last { db.execute("UPDATE c_field SET updated = ?1 WHERE k = ?2", params![ts, e]).map_err(dbe)?; }
        db.execute_batch("COMMIT").map_err(dbe)?;
    }
    Ok(())
}

impl Host {
    pub fn open(o: Options) -> Result<Host, String> {
        let db = match &o.store {
            Some(p) => Connection::open(p).map_err(|e| format!("store {p}: {e}"))?,
            None => Connection::open_in_memory().map_err(dbe)?,
        };
        db.busy_timeout(Duration::from_millis(5000)).map_err(dbe)?;
        if o.store.is_some() { let _: String = db.query_row("PRAGMA journal_mode = WAL", [], |r| r.get(0)).map_err(dbe)?; db.execute_batch("PRAGMA synchronous = NORMAL").map_err(dbe)?; }   // WAL + NORMAL: a power loss may lose the last commits, never corrupts (what the Python store does too)
        for t in [ROWS, JOURNAL] {
            db.execute_batch(&format!("CREATE TABLE IF NOT EXISTS {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")).map_err(dbe)?;
        }
        migrate(&db)?;
        let journal_next = db.query_row(&format!("SELECT MAX(k) FROM {JOURNAL}"), [], |r| r.get::<_, Option<String>>(0)).map_err(dbe)?.and_then(|k| k.parse::<u64>().ok()).map_or(0, |k| k + 1);   // the primary key's index: no scan
        let db = Arc::new(Mutex::new(db));
        let mut core = Core::new();
        core.set_ns(SEAT_NS);   // the graph link: ids under `membrane::` and bare ids are this VM's, any other `ns::` is foreign (docs/graph-link.md, 15b)
        core.queue_writes = false;   // a field host runs no trigger engine: the queue would only hold every write's values (vm.rs `queue_writes`)
        if o.store.is_some() { core.loader = Some(stored_loader(db.clone())); }   // a lazy entity a setter writes loads from the store
        let mut waiter = None;
        if o.effects {
            let st = match &o.store {
                Some(p) => crate::store::Store::with_exec(p, Arc::new(crate::sqlexec::NativeSqlExec::new(p)?)),
                None => crate::store::Store::io_only(&std::env::current_dir().map(|d| d.to_string_lossy().to_string()).unwrap_or_default()),
            };
            st.set_plain(o.plain, o.bind.clone());
            waiter = Some(st.waiter());
            core.store = Some(st);
        }
        let mut fields = Fields::goalgraph();
        core.storage = fields.storage_handle();   // the core reads the classes' modes too (`.pinned`)
        fields.inbound_by_host = true;
        fields.calls_refused = true;
        // TRUST BY POSITION: nothing is privileged at open (not even a pack named `boot`); `load_app(.., trusted)` marks the first app's packs
        Ok(Host { eng: Mutex::new((core, fields, 0)), db, journal: Mutex::new(vec![]), journal_next: std::sync::atomic::AtomicU64::new(journal_next), clock: o.clock, waiter, refusals: Mutex::new(Default::default()), packs_dirty: AtomicBool::new(false), wal: o.store.as_ref().map(|p| std::path::PathBuf::from(format!("{p}-wal"))), depth: Default::default(), commits: Default::default(), pending: Mutex::new(None), wrote: AtomicBool::new(false), round_ts: Default::default(), warm_rows: std::sync::atomic::AtomicUsize::new(DEFAULT_WARM_ROWS), warm: Default::default(), memory: Default::default(), rss_reading: Default::default() })
    }

    /// Whether the named pack holds the privilege (exempt from rule B): the packs of the first app (the root of trust, by the host), any other by a
    /// privileged writer's grant.
    pub fn pack_privileged(&self, name: &str) -> bool { self.eng().1.is_privileged(name) }
    pub fn now(&self) -> f64 { (self.clock)() }
    /// The VM-provided `updated` of an entity: the time of its last write (the engine's copy; 0 when it has none).
    pub fn updated(&self, eid: &str) -> f64 { self.eng().0.updated.get(eid).copied().unwrap_or(0.0) }
    /// The same as the store's `updated` column holds it (None: no row).
    pub fn stored_updated(&self, eid: &str) -> Option<f64> {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        self.flush(&db);
        db.query_row(&format!("SELECT updated FROM {ROWS} WHERE k = ?"), params![eid], |r| r.get(0)).optional().ok().flatten()
    }
    pub fn storage_of(&self, cls: &str) -> crate::fields::Storage { self.eng().1.storage_of(cls) }
    fn eng(&self) -> std::sync::MutexGuard<'_, Engine> { self.eng.lock().unwrap_or_else(|e| e.into_inner()) }
    /// The engine, for the boot plan's walker (plan.rs takes the lock itself, never held while a step runs).
    pub fn engine(&self) -> &Mutex<Engine> { &self.eng }

    // ---- packs -------------------------------------------------------------------------------------------------------
    pub fn load_pack(&self, name: &str, text: &str) -> Result<(), String> { self.eng().1.set_pack(name, text) }

    /// Load an APP (docs/c1f-layers.md): a folder `<name>.c1f/` or one pack file (the loader tells them apart). The `.c1f` files of a folder (its
    /// whole tree) are packs, loaded in dependency order (a pack after the packs of this app it imports; of those ready, the alphabetically first
    /// path next); every other file is data, not loaded here. A pack whose name is already loaded is skipped (the lower layer stays). `trusted`
    /// is the root of trust: the packs loaded here are marked privileged by the host (the FIRST app only; nothing is trusted by name).
    pub fn load_app(&self, path: &std::path::Path, trusted: bool) -> Result<Loaded, String> {
        let mut files = vec![];
        if path.is_dir() { crate::apps::pack_files(path, &mut files).map_err(|e| format!("{}: {e}", path.display()))?; } else { files.push(path.to_path_buf()); }
        let mut packs = vec![];
        for p in files {
            let name = p.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
            if self.eng().1.has_pack(&name) { continue; }
            let text = std::fs::read_to_string(&p).map_err(|e| format!("{}: {e}", p.display()))?;
            packs.push((name, text));
        }
        let mut l = Loaded { names: vec![], msgs: vec![] };
        for (name, text) in crate::apps::in_dependency_order(packs) {
            if trusted { self.eng().1.note_grant(&name, true, "host"); }
            if let Err(e) = self.load_pack(&name, &text) {
                if !(e.contains("isn't loaded") || e.contains("isn't active")) { l.msgs.push(e); }
            }
            l.names.push(name);
        }
        for (n, why) in self.eng().1.pack_errors.clone() {
            if !why.is_empty() && !(why.contains("dom")) { l.msgs.push(format!("pack {n}: {why}")); }
        }
        if trusted { self.packs_dirty.store(true, Ordering::SeqCst); }
        Ok(l)
    }

    /// Start-up: compute every getter once and keep the values, running no setter (a rebuild runs no effects).
    pub fn rebuild(&self) {
        let _t = self.txn();
        let now = self.now();
        { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.rebuild(core, now, seq); }
        self.drain_apply();
    }

    // ---- hearing -----------------------------------------------------------------------------------------------------
    /// An outside write: journalled (the engine can not re-derive it), stored, heard by the engine, and what the engine made of it applied.
    pub fn write(&self, eid: &str, prop: &str, new: J, writer: &str) {
        // an outside None written to an ENTITY (no property) is its death, the mirror of a birth: journalled as the `retire = true` it is, so a replay destroys it again
        let (prop, new) = if prop.is_empty() && new.is_null() { ("retire", json!(true)) } else { (prop, new) };
        if crate::vm::vm_provided(prop).is_some() { return; }   // the VM's own, read-only: refused, not journalled
        let ts = self.now();
        self.journal_push(Line::Write { ts, writer: writer.into(), eid: eid.into(), prop: prop.into(), new: new.clone() });
        self.inject_write(ts, eid, prop, new, writer, writer);
    }

    /// An outside birth (a request, the boot's entity): journalled, stored, loaded, its getters run.
    pub fn born(&self, eid: &str, cls: &str, props: Map<String, J>, writer: &str) {
        let ts = self.now();
        self.journal_push(Line::Born { ts, writer: writer.into(), eid: eid.into(), cls: cls.into(), props: props.clone(), by: None });
        self.inject_born(ts, eid, cls, props, writer, writer, None);
    }

    /// The cause of an entity's birth (`<entity>.born_by`): None for one that has none. A stored entity that is not loaded loads first.
    pub fn born_by(&self, eid: &str) -> Option<String> {
        if self.eng().0.unloaded.contains_key(eid) { self.ensure_loaded(eid, self.now()); self.settle_loaded(); }
        self.eng().0.born_by.get(eid).cloned()
    }

    fn round_at(&self, ts: f64) -> RoundTs<'_> { RoundTs { h: self, prev: self.round_ts.swap(ts.to_bits(), Ordering::SeqCst) } }
    /// The time a write applied now is stamped with: its round's (an outside write's own time), else the clock.
    fn stamp_ts(&self) -> f64 { match self.round_ts.load(Ordering::SeqCst) { 0 => self.now(), b => f64::from_bits(b) } }

    /// `engine_writer` is what the engine sees `stamp` what the store keeps.
    pub(crate) fn inject_write(&self, ts: f64, eid: &str, prop: &str, new: J, engine_writer: &str, stamp: &str) {
        let _t = self.txn();
        let _r = self.round_at(ts);
        self.ensure_loaded(eid, ts);
        self.store_prop(eid, None, prop, &new, stamp);
        if prop == "text" { self.pack_text(eid, &new); }
        if prop == "privileged" { self.note_outside_grant(eid, &new, stamp); }
        {
            let mut g = self.eng();
            let (core, f, seq) = &mut *g;
            let old = core.docs.get(eid).and_then(|e| e.props.get(prop).cloned()).unwrap_or(J::Null);
            *seq += 1;
            let w = Write { eid: eid.into(), prop: prop.into(), new, old, writer: engine_writer.into(), chain: json!({}), seq: *seq };
            core.hear(w.clone(), None, ts);
            let mut s = *seq;
            f.react(core, &NullHost, w, ts, &mut s);
            *seq = s;
        }
        self.drain_apply();
    }

    // ---- the graph link (docs/graph-link.md, 15b): the same notices and applies as the browser's `Field` -----------------------------------------------
    /// Tell the engine its own namespace (default `membrane`).
    pub fn set_ns(&self, ns: &str) { self.eng().0.set_ns(ns); }
    /// Apply what a foreign owner sent (a replica: RAM only, never stored, never journalled) and the round it causes.
    pub fn feed_val(&self, eid: &str, val: crate::fields::LinkVal, rev: f64) {
        let _t = self.txn();
        let ts = self.now();
        { let mut g = self.eng(); let (core, f, seq) = &mut *g; let mut s = *seq; f.apply_val(core, &NullHost, eid, rev, val, ts, &mut s); *seq = s; }
        self.drain_apply();
    }
    /// The notices since the last call: (subs, unsubs, due).
    pub fn link_notices(&self) -> (Vec<J>, Vec<J>, Option<f64>) { self.eng().1.link_drain() }
    /// A reconnect: the next notices announce every subscription again.
    pub fn link_reset(&self) { self.eng().1.link_reset(); }
    /// The revisions held per replica.
    pub fn replica_revs(&self) -> Vec<(String, f64)> { self.eng().0.replica_revs() }

    /// How many times `instances(<class>)` walked every loaded entity so far (the engine's cache misses): a count of work for the tests.
    pub fn inst_scans(&self) -> u64 { self.eng().0.inst_scans() }

    pub(crate) fn inject_born(&self, ts: f64, eid: &str, cls: &str, props: Map<String, J>, _engine_writer: &str, stamp: &str, by: Option<&str>) {
        let _t = self.txn();
        let _r = self.round_at(ts);
        self.ensure_loaded(eid, ts);
        let props = crate::vm::birth_props(cls, props);   // the eid class is the birth default of the class list (the journal keeps what was said)
        self.store_born(eid, cls, &props, stamp, by);
        if let Some(t) = props.get("text") { self.pack_text(eid, t); }
        if let Some(v) = props.get("privileged") { self.note_outside_grant(eid, v, stamp); }
        {
            let mut g = self.eng();
            let (core, f, seq) = &mut *g;
            f.arrive(core, eid.into(), cls.into(), props, ts);
            if let Some(b) = by { core.born_by.insert(eid.to_string(), b.to_string()); }
            f.settle(core, &NullHost, ts, seq);
        }
        self.drain_apply();
    }

    /// Replay: apply a journal to this (fresh) host, line by line. The lines are journalled again (the new journal equals the old one).
    pub fn replay(&self, lines: &[Line]) {
        for l in lines {
            self.journal_push(l.clone());
            match l {
                Line::Born { ts, writer, eid, cls, props, by } => self.inject_born(*ts, eid, cls, props.clone(), writer, writer, by.as_deref()),
                Line::Write { ts, writer, eid, prop, new } => {
                    let seen = writer.strip_prefix("host:").unwrap_or(writer);
                    self.inject_write(*ts, eid, prop, new.clone(), seen, writer)
                }
            }
        }
    }

    // ---- the loop ----------------------------------------------------------------------------------------------------
    /// One turn: the door's requests come in (outside births), the engine settles what the drivers' threads finished, its output is applied.
    pub fn pump(&self) {
        for _ in 0..16 {
            let now = self.now();
            let inbound = { let g = self.eng(); g.0.inbound() };
            let had = !inbound.is_empty();
            for (e, cls, props) in inbound { self.born_at(now, &e, &cls, props, DOOR_WRITER); }
            { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.settle(core, &NullHost, now, seq); }
            let applied = self.drain_apply();
            if !applied && !had { break; }
        }
    }

    fn born_at(&self, ts: f64, eid: &str, cls: &str, props: Map<String, J>, writer: &str) {
        self.journal_push(Line::Born { ts, writer: writer.into(), eid: eid.into(), cls: cls.into(), props: props.clone(), by: None });
        self.inject_born(ts, eid, cls, props, writer, writer, None);
    }

    /// The clock input: armed waits come due.
    pub fn tick(&self) {
        {
            let _t = self.txn();
            let now = self.now();
            { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.tick(core, &NullHost, now, seq); }
            self.drain_apply();
        }
        self.stats_tick();     // the eval accounting's window (stats.rs): publishes `Stats:eval` when due
        self.trim_wal();       // after the round's transaction has committed: a checkpoint cannot run inside one
    }

    /// A log over WAL_LIMIT_BYTES is folded into the file and truncated. The attempt waits at most 50 ms for readers (the engine's thread must not stall),
    /// and a reader that is still there leaves it for the next tick.
    fn trim_wal(&self) {
        let Some(w) = &self.wal else { return };
        if std::fs::metadata(w).map(|m| m.len()).unwrap_or(0) <= WAL_LIMIT_BYTES { return; }
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let _ = db.busy_timeout(Duration::from_millis(50));
        let _ = db.execute_batch("PRAGMA wal_checkpoint(TRUNCATE)");
        let _ = db.busy_timeout(Duration::from_millis(5000));
    }

    /// Run until `stop`: pump, tick every `tick_s`, wait for the drivers (50 ms at most). `each` runs after every turn.
    pub fn run(&self, stop: &AtomicBool, tick_s: f64, each: &mut dyn FnMut(&Host)) {
        let mut last = self.now();
        while !stop.load(Ordering::SeqCst) {
            self.pump();
            if self.now() - last >= tick_s { self.tick(); last = self.now(); }
            let warming = self.warm_step();   // the warm boot, a slice between turns: the door is never kept waiting for it
            each(self);
            if warming { continue; }
            match &self.waiter { Some(w) => { w.wait(Some(Duration::from_millis(50))); } None => std::thread::sleep(Duration::from_millis(50)) }
        }
        if let Some(w) = &self.waiter { w.interrupt_all(); }
    }

    // ---- the engine's output -----------------------------------------------------------------------------------------
    /// Take what the engine did since the last drain and apply it to the store, stamped. -> whether there was any.
    fn drain_apply(&self) -> bool {
        let _t = self.txn();
        self.fault_wanted();
        let (out, _calls) = { let mut g = self.eng(); let f = &mut g.1; (std::mem::take(&mut f.out), std::mem::take(&mut f.calls)) };
        let any = !out.is_empty();
        let ts = self.stamp_ts();
        let reactions = self.reactions_of(&out);
        // the packs whose `text` this batch wrote: the reply tells whether each loaded (#1143, the silent pack: see `pack_reactions`)
        let text_empty = |w: &J| w["new"].as_str().map_or(true, |t| t.trim().is_empty());
        let packs_emptied: Vec<String> = out.iter().filter(|w| w["prop"] == "text" && text_empty(w)).filter_map(|w| w["eid"].as_str()).filter_map(|e| e.strip_prefix(PACK_PREFIX)).filter(|n| !n.is_empty()).map(String::from).collect();
        let packs_written: Vec<String> = out.iter().filter(|w| w["prop"] == "text" && !text_empty(w)).filter_map(|w| w["eid"].as_str()).filter_map(|e| e.strip_prefix(PACK_PREFIX)).filter(|n| !n.is_empty()).map(String::from).collect();
        for w in out {
            if let Some(cls) = w.get("born").and_then(|c| c.as_str()) {
                let eid = w["eid"].as_str().unwrap_or("").to_string();
                let stamp = if w.get("virt") == Some(&json!(true)) { stamp_virtual_writer("create") } else { stamp_born_writer(cls) };
                let by = w.get("by").and_then(|b| b.as_str()).map(String::from);
                self.store_born(&eid, cls, &Map::new(), &stamp, by.as_deref());
                if is_outside(&stamp) { self.journal_push(Line::Born { ts, writer: stamp, eid, cls: cls.into(), props: Map::new(), by }); }
                continue;
            }
            let (Some(eid), Some(prop)) = (w["eid"].as_str(), w["prop"].as_str()) else { continue };
            let stamp = if w.get("virt") == Some(&json!(true)) { stamp_virtual_writer(w["writer"].as_str().unwrap_or("")) } else { stamp_writer(w["writer"].as_str().unwrap_or("")) };
            self.store_prop(eid, None, prop, &w["new"], &stamp);
            if prop == "text" { self.pack_text(eid, &w["new"]); }
            if prop == "privileged" && eid.starts_with(PACK_PREFIX) { self.packs_dirty.store(true, Ordering::SeqCst); }   // `granted_by` is mirrored
            if is_outside(&stamp) { self.journal_push(Line::Write { ts, writer: stamp, eid: eid.into(), prop: prop.into(), new: w["new"].clone() }); }
        }
        // `retire = true` (any writer): the entity leaves the engine and the store; what read it recalculates. Derived, so never journalled.
        // A parent owns its children by the colon path: retiring `A:1` retires `A:1:x` and `A:1:x:y` (not `A:10`).
        let retired = std::mem::take(&mut self.eng().1.retired);
        let mut packs_retired: Vec<String> = vec![];
        for eid in &retired {
            // a Pack entity retired (`retire = true`, or `Pack:<name> = None`) UNLOADS the pack: its definitions leave and the values its getters left are cleared (the layer below shows again)
            if let Some(name) = eid.strip_prefix(PACK_PREFIX).filter(|n| !n.is_empty()) {
                { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.remove_pack_clean(core, &NullHost, name, ts, seq); }
                self.refusals.lock().unwrap_or_else(|e| e.into_inner()).remove(name);
                self.packs_dirty.store(true, Ordering::SeqCst);
                packs_retired.push(name.to_string());
            }
            let below = format!("{eid}:");
            // a retired `Vm:<ns>` takes the whole namespace `<ns>::` with it (docs/graph-link.md, section 2)
            let ns = Core::namespace_of_vm(eid);
            let owned: Vec<String> = { let g = self.eng(); g.0.docs.keys().filter(|k| k.starts_with(&below) || ns.as_ref().map_or(false, |n| k.starts_with(n.as_str()))).cloned().collect() };
            if let Some(n) = &ns { let mut g = self.eng(); let (core, f, _) = &mut *g; f.link_forget(core, n); core.unloaded.retain(|k, _| !k.starts_with(n.as_str())); }
            self.eng().0.unloaded.retain(|k, _| k != eid && !k.starts_with(&below));   // a stored child that was never loaded is deleted with the rest, and does not wait to load
            for e in std::iter::once(eid).chain(owned.iter()) {
                { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.remove(core, &NullHost, e, ts, seq); }
            }
            let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(m) = self.pending.lock().unwrap_or_else(|e| e.into_inner()).as_mut() { m.retain(|k, _| k != eid && !k.starts_with(&below) && !ns.as_ref().map_or(false, |n| k.starts_with(n.as_str()))); }   // a retired row is not written back
            self.exec(&db, &format!("DELETE FROM {ROWS} WHERE k = ?1 OR substr(k, 1, length(?2)) = ?2"), params![eid, below]);
            if let Some(n) = &ns { self.exec(&db, &format!("DELETE FROM {ROWS} WHERE substr(k, 1, length(?1)) = ?1"), params![n]); }
        }
        let any = any || !retired.is_empty();
        // THE MEMORY POLICY (docs/cliffvm.md, "Unload"): over the memory budget, the coldest loaded entities leave the engine, their rows kept. The VM's own: nothing a pack or
        // the model writes names an entity to evict. Not journalled, changes no value.
        self.evict_under_pressure();
        // THE COLLECTOR (docs/cliffvm.md, "Garbage collection"): the virtual entities nothing holds any more leave the engine at the end of the round. Removing them
        // is a round of its own (what read them recalculates), which drains again.
        let dead = { let g = self.eng(); g.0.gc_dead() };
        let any = any || !dead.is_empty();
        for d in &dead { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.remove(core, &NullHost, d, ts, seq); core.note_collected(d); }
        if !dead.is_empty() { self.drain_apply(); }
        // THE REACTIONS of a model's write (docs/cliffvm.md, "The return entity"): each door write is a Request `mc_done` by the setter `mcp-write`; the derived
        // writes of the same round are what it caused. The list goes back on that Request as `reactions` (a derived write, never journalled; a replay drains the same way).
        packs_retired.extend(packs_emptied);   // an empty text unloads the pack too (pack_text), and the reply says so
        let reactions = self.pack_reactions(reactions, &packs_written, &packs_retired);
        let any = any || !reactions.is_empty();
        for (req, list) in reactions { self.inject_write(ts, &req, "reactions", list, "reactions", &stamp_writer("reactions")); }
        if self.packs_dirty.swap(false, Ordering::SeqCst) { self.refresh_packs(); self.load_new_residents(); return true; }
        any
    }

    /// THE MEMORY POLICY (docs/cliffvm.md, "Unload"), run at the end of every drain (so within a long cascade too, like an import) while a memory budget AND a batch are
    /// set (the boot facts; with either unset nothing is ever evicted). Pressure = the resident size (`process_rss`, or a test's injected reading) at or over `unload_hard` x
    /// budget (the budget itself when unset), or over `unload_soft` x budget (no soft line when unset). Under pressure ONE pass: the `unload_batch` coldest loaded entities
    /// leave, coldest = the smallest touch clock (`Ent::touched`: bumped by every read, write and load), so the least recently used or written. Candidates are only stored
    /// entities that may leave: never a RESIDENT or VIRTUAL class, never one with a running effect, never one a live virtual entity holds by `$ref` (`Core::pinned_reason`
    /// and `held_by_virtual`). The rows are written first (the store is written through, but a reload in this very transaction must read the row as it is), then the whole
    /// batch goes with ONE pass over the field's bookkeeping (`Fields::unload_many`: the entity and what the field remembers of it). -> whether any left.
    fn evict_under_pressure(&self) -> bool {
        let cfg = self.memory.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let (Some(mb), Some(batch)) = (cfg.budget_mb, cfg.batch) else { return false };
        if batch == 0 { return false; }
        let rss = match &*self.rss_reading.lock().unwrap_or_else(|e| e.into_inner()) { Some(f) => f(), None => process_rss() };
        let Some(rss) = rss else { return false };
        let b = (mb << 20) as f64;
        if !(rss as f64 >= b * cfg.hard.unwrap_or(1.0) || cfg.soft.map_or(false, |s| rss as f64 >= b * s)) { return false; }
        let go: Vec<String> = {
            let g = self.eng(); let (core, f, _) = &*g;
            let held = held_by_virtual(core, f);
            let mut cold: Vec<(u64, &String)> = core.docs.iter().filter(|(k, _)| !held.contains(*k) && core.pinned_reason(k).is_none()).map(|(k, e)| (e.touched.get(), k)).collect();
            let n = (batch as usize).min(cold.len());
            if n == 0 { return false; }
            if n < cold.len() { cold.select_nth_unstable(n - 1); cold.truncate(n); }
            cold.into_iter().map(|(_, k)| k.clone()).collect()
        };
        { let db = self.db.lock().unwrap_or_else(|e| e.into_inner()); self.flush(&db); }
        { let mut g = self.eng(); let (core, f, _) = &mut *g; f.unload_many(core, &go); }
        true
    }

    /// For every `mc_done` the door's `mcp-write` setter wrote in this batch: (the Request, the list of what else the engine wrote in the batch: setters' and
    /// getters' writes, each `{eid, prop, new, by, why}`; `why` is the reason of a null). The door's own bookkeeping (Request, Return, Pack entities) and the
    /// write itself are left out.
    fn reactions_of(&self, out: &[J]) -> Vec<(String, J)> {
        let asked: Vec<String> = out.iter().filter(|w| w["prop"] == "mc_done" && (w["writer"] == "field:mcp-write" || w["writer"] == "field:cliff-run")).filter_map(|w| w["eid"].as_str().map(String::from)).collect();
        if asked.is_empty() { return vec![]; }
        let g = self.eng();
        // a reaction is a REAL change: not a verb slot being consumed (`direct:consumed`, `call-consumed`), and not a birth's silent compute of a getter to none WITHOUT a reason (`rebuild`: none to none; a none that says why stays)
        let derived = |w: &J| w["writer"].as_str().map_or(false, |x| x.starts_with("field:") && x != "field:mcp-write" && x != "field:cliff-run"
            && !x.ends_with("consumed"));
        let list: Vec<J> = out.iter().enumerate()
            .filter(|(_, w)| derived(w) && w.get("prop").is_some() && w["prop"] != "parents" &&   // the class list is the engine's, not a property the model sees (operator 2026-11-14)
                w["eid"].as_str().map_or(false, |e| !e.starts_with("Request:") && !e.starts_with("Return:") && !e.starts_with(PACK_PREFIX)))
            .filter(|(i, w)| !out[i + 1..].iter().any(|x| derived(x) && x["eid"] == w["eid"] && x["prop"] == w["prop"]))   // the last value of each property in the round
            .filter_map(|(_, w)| {
                let (eid, prop) = (w["eid"].as_str().unwrap_or(""), w["prop"].as_str().unwrap_or(""));
                let why = if w["new"].is_null() { g.1.why_chain(&g.0, eid, prop).first().and_then(|c| c.get("reason").cloned()).unwrap_or(J::Null) } else { J::Null };
                if w["new"].is_null() && why.is_null() && w["writer"] == "field:rebuild" { return None; }
                // `cls`: the entity's class as `this.cls` reads it (the first of its class list that is not Error; the eid class if it is not listed): exact, not the eid prefix, which is only the birth default (#1143, docs/reply-size.md B2)
                let cls = g.0.docs.get(eid).map(|e| match e.class_names().into_iter().find(|c| !c.eq_ignore_ascii_case(crate::vm::ERROR_CLASS)) { Some(c) if !c.eq_ignore_ascii_case(&e.cls) => c, _ => e.cls.clone() }).unwrap_or_else(|| eid.split(':').next().unwrap_or("").to_string());
                Some(json!({"eid": eid, "prop": prop, "new": w["new"], "by": w["writer"].as_str().unwrap_or("").trim_start_matches("field:"), "why": why, "cls": cls}))
            }).collect();
        asked.into_iter().map(|r| (r, J::Array(list.clone()))).collect()
    }

    /// A model-written pack that does not load must not be silent in the write's reply (#1143, the delivery-route incident): for each pack whose `text` was written in
    /// this batch, add the reaction `Pack:<name>.loaded` (new: whether it is active, `why`: the engine's error text, verbatim). The mirror of `loaded`/`error` (refresh_packs)
    /// is a later round, so the reaction is made from the same state it reads. The door's reply words it (door_tools.c1f, `rx_pack`).
    fn pack_reactions(&self, reactions: Vec<(String, J)>, written: &[String], retired: &[String]) -> Vec<(String, J)> {
        if (written.is_empty() && retired.is_empty()) || reactions.is_empty() { return reactions; }
        let extra: Vec<J> = { let g = self.eng();
            let refusals = self.refusals.lock().unwrap_or_else(|e| e.into_inner());
            let mut seen = std::collections::HashSet::new();
            written.iter().filter(|n| seen.insert(n.as_str())).map(|name| {
                let active = g.1.pack_active(name) && !refusals.contains_key(name.as_str());
                let err = refusals.get(name.as_str()).cloned().or_else(|| g.1.pack_errors.get(name.as_str()).cloned());
                json!({"eid": format!("{PACK_PREFIX}{name}"), "prop": "loaded", "new": active, "by": PACK_WRITER, "why": err.map(J::from).unwrap_or(J::Null), "cls": PACK_CLASS})
            }).chain(retired.iter().map(|name| json!({"eid": format!("{PACK_PREFIX}{name}"), "prop": "unloaded", "new": true, "by": PACK_WRITER, "why": J::Null, "cls": PACK_CLASS}))).collect() };
        reactions.into_iter().map(|(r, l)| { let mut v = l.as_array().cloned().unwrap_or_default(); v.extend(extra.iter().cloned()); (r, J::Array(v)) }).collect()
    }

    // ---- Pack entities -----------------------------------------------------------------------------------------------
    /// An OUTSIDE write of `Pack:<name>.privileged` (the host's, the door's person): a grant or a revoke, by that writer. (A setter's own
    /// write is noted by the engine, with the pack that wrote it.)
    fn note_outside_grant(&self, eid: &str, new: &J, writer: &str) {
        let Some(name) = eid.strip_prefix(PACK_PREFIX).filter(|n| !n.is_empty()) else { return };
        self.eng().1.note_grant(name, *new == json!(true), writer);
        self.packs_dirty.store(true, Ordering::SeqCst);
    }

    /// A write to `text` of `Pack:<name>` loads, replaces or (none/empty) removes the pack; the outcome is mirrored at the end of the drain.
    fn pack_text(&self, eid: &str, text: &J) {
        let Some(name) = eid.strip_prefix(PACK_PREFIX).filter(|n| !n.is_empty()) else { return };
        let src = text.as_str().unwrap_or("");
        let mut g = self.eng();
        let (core, f, seq) = &mut *g;
        let mut refusals = self.refusals.lock().unwrap_or_else(|e| e.into_inner());
        if src.trim().is_empty() {
            f.remove_pack_clean(core, &NullHost, name, self.now(), seq);        // its getters' values are cleared too
            refusals.remove(name);
        } else {
            match f.set_pack_clean(core, &NullHost, name, src, self.now(), seq) {
                Ok(()) => { refusals.remove(name); }
                // a NEW pack waiting for an import stays in the engine and its reason is the engine's (it clears when the import arrives)
                Err(_) if f.pack_errors.contains_key(name) => { refusals.remove(name); }
                Err(e) => { refusals.insert(name.to_string(), e); }
            }
        }
        self.packs_dirty.store(true, Ordering::SeqCst);
    }

    /// Mirror every Pack entity's state back as DERIVED writes (`trigger:field:pack`, not journalled: a replay loads the same texts again
    /// and derives the same): `loaded`, `error`, `good_text` (the last text that loaded). Only what changed is written.
    fn refresh_packs(&self) {
        let want: Vec<(String, J, J)> = {
            let g = self.eng();
            let refusals = self.refusals.lock().unwrap_or_else(|e| e.into_inner());
            g.0.docs.iter().filter(|(k, e)| k.starts_with(PACK_PREFIX) && e.cls == PACK_CLASS).map(|(k, e)| {
                let name = &k[PACK_PREFIX.len()..];
                let active = g.1.pack_active(name);
                let err = refusals.get(name).cloned().or_else(|| g.1.pack_errors.get(name).cloned());
                let text = e.props.get("text").cloned().unwrap_or(J::Null);
                let good = if active && !refusals.contains_key(name) { text } else { e.props.get("good_text").cloned().unwrap_or(J::Null) };
                let by = g.1.granted_by(name).map(|b| json!(b)).unwrap_or(J::Null);
                (k.clone(), json!(active), json!({"error": err.map(J::from).unwrap_or(J::Null), "good_text": good, "granted_by": by}))
            }).collect()
        };
        let ts = self.now();
        for (eid, loaded, rest) in want {
            let set = |prop: &str, v: J| {
                let cur = self.get(&eid, prop);
                if cur != v { self.inject_write(ts, &eid, prop, v, PACK_WRITER, &stamp_writer(PACK_WRITER)); }
            };
            set("loaded", loaded);
            set("error", rest["error"].clone());
            set("good_text", rest["good_text"].clone());
            set("granted_by", rest["granted_by"].clone());      // who wrote the grant, from the engine: never from the pack's own text
        }
    }

    /// Start-up: every stored `Pack:*` row loads (name order) from its `text`, else from `good_text` (the error stays shown). The rows
    /// arrive in the engine as they are (no rewrite, no journal: they were journalled when written). -> the names that loaded.
    pub fn load_stored_packs(&self) -> Vec<String> {
        let rows: Vec<(String, Map<String, J>)> = self.rows_of_classes(&[PACK_CLASS]).into_iter()
            .filter(|(k, v)| k.starts_with(PACK_PREFIX) && v["cls"] == json!(PACK_CLASS))
            .map(|(k, v)| (k, v["props"].as_object().cloned().unwrap_or_default())).collect();
        let now = self.now();
        let mut names = vec![];
        for (eid, props) in rows {
            { let mut g = self.eng(); let (core, f, _) = &mut *g; f.arrive(core, eid.clone(), PACK_CLASS.into(), props.clone(), now); }
            let name = eid[PACK_PREFIX.len()..].to_string();
            if props.get("privileged") == Some(&json!(true)) {      // a stored grant is read back (the grant is by name)
                let by = props.get("granted_by").and_then(|b| b.as_str()).unwrap_or("stored").to_string();
                self.eng().1.note_grant(&name, true, &by);
            }
            // a Pack entity with NO text (a grant for a pack loaded from a file) does not remove that pack: only a written empty text does
            let Some(text) = props.get("text").cloned() else { names.push(name); continue };
            self.pack_text(&eid, &text);
            let refused = self.refusals.lock().unwrap_or_else(|e| e.into_inner()).get(&name).cloned();
            if let (Some(why), Some(good)) = (refused, props.get("good_text").and_then(|g| g.as_str())) {
                if self.eng().1.set_pack(&name, good).is_ok() { self.refusals.lock().unwrap_or_else(|e| e.into_inner()).insert(name.clone(), why); }
            }
            names.push(name);
        }
        names
    }

    /// Start-up, after the packs: the RESIDENT classes' rows arrive in the engine (their class, their props, the `parents` stamp among them), so what the model wrote
    /// (a banner line, a message) outlives the process; every other stored row of a LAZY class is only NOTED (`Core::unloaded`: eid -> class) and loads
    /// when something refers to it (`fault_wanted`, `Fields::ensure`) or in the background (`warm_step`). A VIRTUAL class's row (an older run stored it) is neither. The rows
    /// come back as they are, with one exception: a prop a GETTER wrote (`trigger:field:getter:*`, `trigger:field:rebuild`) is not read back: the rebuild that follows
    /// computes it again from the restored inputs, so a getter that changed since the row was stored shows its new value. No setter runs and nothing is journalled or
    /// rewritten (a restore runs no effects). -> how many entities arrived.
    pub fn load_stored_entities(&self) -> usize {
        let now = self.now();
        let resident: Vec<String> = { let g = self.eng(); g.1.classes_with(crate::fields::Storage::Resident) };
        let mut n = 0;
        for (eid, row, updated) in self.rows_with_updated(&resident.iter().map(|s| s.as_str()).collect::<Vec<_>>()) {
            let cls = row["cls"].as_str().unwrap_or("").to_string();
            if cls.is_empty() || cls == PACK_CLASS { continue; }
            let mut g = self.eng(); let (core, f, _) = &mut *g;
            core.updated.insert(eid.clone(), updated);
            if let Some(b) = row["born_by"].as_str() { core.born_by.insert(eid.clone(), b.to_string()); }
            f.arrive(core, eid, cls, restore_props(&row), now);
            n += 1;
        }
        self.note_unloaded();
        self.start_warm();
        n
    }

    /// Every stored row that is neither loaded, nor of a VIRTUAL class, nor a Pack: noted as unloaded (eid -> class). A covering index answers it (no body is read).
    fn note_unloaded(&self) {
        let rows: Vec<(String, String)> = {
            let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
            self.flush(&db);
            let mut st = db.prepare(&format!("SELECT k, cls FROM {ROWS} INDEXED BY c_field_cls")).expect("rows");
            st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default()))).expect("rows").filter_map(|r| r.ok()).collect()
        };
        let mut g = self.eng(); let (core, f, _) = &mut *g;
        for (eid, cls) in rows {
            if cls.is_empty() || cls == PACK_CLASS || f.storage_of(&cls) != crate::fields::Storage::Lazy || core.docs.contains_key(&eid) { continue; }
            core.unloaded.insert(eid, cls);
        }
    }

    /// The warm boot's queue: the newest-`updated` unloaded rows, up to the `warm_rows` budget (the index on `updated` is covering: no body is read).
    fn start_warm(&self) {
        let budget = self.warm_rows.load(Ordering::SeqCst);
        let mut q = std::collections::VecDeque::new();
        if budget > 0 {
            let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
            let g = self.eng();
            let mut st = db.prepare(&format!("SELECT k FROM {ROWS} INDEXED BY c_field_updated ORDER BY updated DESC")).expect("rows");
            let mut rows = st.query([]).expect("rows");
            while let Ok(Some(r)) = rows.next() {
                let k: String = r.get(0).unwrap_or_default();
                if g.0.unloaded.contains_key(&k) { q.push_back(k); if q.len() >= budget { break; } }
            }
        }
        *self.warm.lock().unwrap_or_else(|e| e.into_inner()) = q;
    }

    /// The host holds this entity itself (the collector never takes it): the boot's own state, `Boot:seat`, and the boot plan's steps. Call it BEFORE the entity is born.
    pub fn hold(&self, eid: &str) { self.eng().0.host_held.insert(eid.to_string()); }

    /// The memory policy's configuration (the boot facts `memory_budget_mb`, `unload_soft`, `unload_hard`, `unload_batch`; none has a default number).
    pub fn set_memory(&self, c: MemoryConfig) { *self.memory.lock().unwrap_or_else(|e| e.into_inner()) = c; }
    /// A test's own resident-size reading, consulted by the policy in place of the process's (`cli.rs` reads it from the env var `MEMBRANE_TEST_RSS`).
    pub fn set_rss_reading(&self, r: RssReading) { *self.rss_reading.lock().unwrap_or_else(|e| e.into_inner()) = Some(r); }

    /// The boot fact `warm_rows`: how many lazy rows load in the background after start (newest `updated` first). Set before `load_stored_entities`.
    pub fn set_warm_rows(&self, n: usize) { self.warm_rows.store(n, Ordering::SeqCst); }

    /// One slice of the warm boot: load queued rows until `WARM_SLICE_ROWS` or `WARM_SLICE_MS`, then let the loop answer the door. -> whether more is waiting.
    pub fn warm_step(&self) -> bool {
        let t = std::time::Instant::now();
        let mut done = 0;
        let mut loaded = false;
        while done < WARM_SLICE_ROWS && t.elapsed() < Duration::from_millis(WARM_SLICE_MS) {
            let Some(eid) = self.warm.lock().unwrap_or_else(|e| e.into_inner()).pop_front() else { break };
            done += 1;
            let now = self.now();
            let mut g = self.eng(); let (core, f, _) = &mut *g;
            loaded |= f.ensure(core, &eid, now);
        }
        if loaded { self.settle_loaded(); }
        !self.warm.lock().unwrap_or_else(|e| e.into_inner()).is_empty()
    }

    /// Load every stored row now (what a restore did before there were lazy classes): for tools and tests that want the whole store in the engine.
    pub fn load_everything(&self) -> usize {
        let now = self.now();
        let eids: Vec<String> = { let g = self.eng(); g.0.unloaded.keys().cloned().collect() };
        let mut n = 0;
        for e in eids { let mut g = self.eng(); let (core, f, _) = &mut *g; if f.ensure(core, &e, now) { n += 1; } }
        self.settle_loaded();
        n
    }

    /// How many stored entities are not loaded (lazy classes).
    pub fn unloaded_count(&self) -> usize { self.eng().0.unloaded.len() }

    /// A class a runtime pack just declared RESIDENT loads its stored rows now (it is "always loaded").
    fn load_new_residents(&self) {
        let now = self.now();
        let eids: Vec<String> = { let g = self.eng(); let (core, f, _) = &*g; core.unloaded.iter().filter(|(_, c)| f.storage_of(c) == crate::fields::Storage::Resident).map(|(e, _)| e.clone()).collect() };
        for e in eids { let mut g = self.eng(); let (core, f, _) = &mut *g; f.ensure(core, &e, now); }
        self.settle_loaded();
    }

    /// Rows were loaded: their getters (dirty since the arrival) are computed, and what the engine made of them is stored.
    fn settle_loaded(&self) {
        let _t = self.txn();
        let now = self.now();
        { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.settle(core, &NullHost, now, seq); }
        self.drain_apply();
    }

    /// What the engine looked for and did not find, though the store has it (`Core::wanted`): load those rows, let what read them recompute, until nothing more is asked.
    fn fault_wanted(&self) {
        for _ in 0..64 {
            let now = self.now();
            let mut g = self.eng();
            let (core, f, seq) = &mut *g;
            let any = core.fault_now() || !core.arrived.is_empty();
            if !any { return; }
            f.complete_arrivals(core, now);
            f.settle(core, &NullHost, now, seq);
        }
    }

    /// An OUTSIDE write or birth of an entity the store has and the engine has not (a lazy class): it loads first, so the write is made on what is stored.
    fn ensure_loaded(&self, eid: &str, ts: f64) {
        let mut g = self.eng(); let (core, f, _) = &mut *g;
        f.ensure(core, eid, ts);
    }

    /// The rows of the given classes, in key order (the `cls` index: no other row is read).
    fn rows_of_classes(&self, classes: &[&str]) -> std::collections::BTreeMap<String, J> {
        self.rows_with_updated(classes).into_iter().map(|(k, v, _)| (k, v)).collect()
    }

    fn rows_with_updated(&self, classes: &[&str]) -> Vec<(String, J, f64)> {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        self.flush(&db);
        let mut out = vec![];
        for c in classes {
            let mut st = db.prepare(&format!("SELECT k, body, updated FROM {ROWS} WHERE cls = ?1")).expect("rows");
            for r in st.query_map(params![c], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, f64>(2)?))).expect("rows").filter_map(|r| r.ok()) {
                out.push((r.0, serde_json::from_str(&r.1).unwrap_or(J::Null), r.2));
            }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out
    }

    // ---- the store ---------------------------------------------------------------------------------------------------
    /// Every write to the store goes through here: outside a transaction a statement commits (and syncs) by itself, which is counted.
    fn exec(&self, db: &Connection, sql: &str, p: impl rusqlite::Params) {
        let _ = db.execute(sql, p);
        self.wrote.store(true, Ordering::SeqCst);
        if self.depth.load(Ordering::SeqCst) == 0 { self.commits.fetch_add(1, Ordering::SeqCst); }
    }

    /// How many times the host has committed to the store (a transaction counts once, a statement outside one counts itself).
    /// A pragma of the store's connection, as text (tests and `doctor`-style checks: journal_mode, synchronous).
    pub fn store_pragma(&self, name: &str) -> String {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        db.query_row(&format!("PRAGMA {name}"), [], |r| r.get::<_, rusqlite::types::Value>(0)).map(|v| match v { rusqlite::types::Value::Text(t) => t, rusqlite::types::Value::Integer(i) => i.to_string(), o => format!("{o:?}") }).unwrap_or_default()
    }
    pub fn store_commits(&self) -> usize { self.commits.load(Ordering::SeqCst) }

    /// Open a transaction (a nested one joins the outer one): the host's writes of a round are ONE commit, and a row it changes is written once.
    pub fn txn(&self) -> Txn<'_> {
        if self.depth.fetch_add(1, Ordering::SeqCst) == 0 {
            let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
            let _ = db.execute_batch("BEGIN");
            *self.pending.lock().unwrap_or_else(|e| e.into_inner()) = Some(Default::default());
        }
        Txn { h: self }
    }

    fn end_txn(&self) {
        if self.depth.load(Ordering::SeqCst) != 1 { self.depth.fetch_sub(1, Ordering::SeqCst); return; }
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        self.flush(&db);   // still inside: the statements it runs are the transaction's
        self.depth.fetch_sub(1, Ordering::SeqCst);
        *self.pending.lock().unwrap_or_else(|e| e.into_inner()) = None;
        if self.wrote.swap(false, Ordering::SeqCst) { let _ = db.execute_batch("COMMIT"); self.commits.fetch_add(1, Ordering::SeqCst); }
        else { let _ = db.execute_batch("ROLLBACK"); }
    }

    /// Write back the rows the open transaction changed.
    fn flush(&self, db: &Connection) {
        let dirty: Vec<(String, (String, Map<String, J>, Map<String, J>), f64, Option<String>)> = {
            let mut g = self.pending.lock().unwrap_or_else(|e| e.into_inner());
            let Some(m) = g.as_mut() else { return };
            m.iter_mut().filter(|(_, p)| p.dirty).filter_map(|(k, p)| { p.dirty = false; p.row.clone().map(|r| (k.clone(), r, p.updated, p.by.clone())) }).collect()
        };
        for (eid, (cls, p, w), updated, by) in dirty { self.write_row(db, &eid, &cls, &p, &w, updated, by.as_deref()); }
    }

    fn write_row(&self, db: &Connection, eid: &str, cls: &str, props: &Map<String, J>, writers: &Map<String, J>, updated: f64, by: Option<&str>) {
        let mut body = json!({"cls": cls, "props": props, "writers": writers});
        if let Some(b) = by { body["born_by"] = json!(b); }   // one string, only for an entity that has a cause
        let body = body.to_string();
        // the row, its class and the time of the write are ONE statement of the write's own transaction
        self.exec(db, &format!("INSERT INTO {ROWS} (k, body, cls, updated) VALUES (?1, ?2, ?3, ?4) ON CONFLICT(k) DO UPDATE SET body = excluded.body, cls = excluded.cls, updated = excluded.updated"), params![eid, body, cls, updated]);
    }

    /// Make sure the open transaction has the row of `eid` (read from the store once, parsed once).
    fn load(&self, db: &Connection, eid: &str) {
        if self.pending.lock().unwrap_or_else(|e| e.into_inner()).as_ref().map_or(true, |m| m.contains_key(eid)) { return; }
        let got: Option<(String, f64)> = db.query_row(&format!("SELECT body, updated FROM {ROWS} WHERE k = ?"), params![eid], |r| Ok((r.get(0)?, r.get(1)?))).optional().ok().flatten();
        let updated = got.as_ref().map_or(0.0, |g| g.1);
        let body = got.map(|g| g.0);
        let parsed = body.and_then(|b| serde_json::from_str::<J>(&b).ok());
        let by = parsed.as_ref().and_then(|j| j["born_by"].as_str().map(String::from));
        let row = parsed.map(|j| (j["cls"].as_str().unwrap_or("").to_string(), j["props"].as_object().cloned().unwrap_or_default(), j["writers"].as_object().cloned().unwrap_or_default()));
        if let Some(m) = self.pending.lock().unwrap_or_else(|e| e.into_inner()).as_mut() { m.insert(eid.into(), Pending { row, dirty: false, updated, by }); }
    }

    fn store_born(&self, eid: &str, cls: &str, props: &Map<String, J>, stamp: &str, by: Option<&str>) {
        let ts = self.stamp_ts();
        if self.storage_of(cls) == crate::fields::Storage::Virtual { self.eng().0.updated.insert(eid.to_string(), ts); return; }   // RAM only: no row, but it has its `updated`
        let _t = self.txn();
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        self.load(&db, eid);
        let mut g = self.pending.lock().unwrap_or_else(|e| e.into_inner());
        let Some(slot) = g.as_mut().and_then(|m| m.get_mut(eid)) else { return };
        let created = slot.row.is_none();
        if created { if let Some(b) = by { slot.by = Some(b.to_string()); } }
        let (c, p, w) = slot.row.get_or_insert_with(|| (cls.to_string(), Map::new(), Map::new()));
        let mut changed = slot.dirty || created || c != cls;
        let mut wrote = created || c != cls;
        *c = cls.to_string();
        for (k, v) in props {
            if p.get(k) != Some(v) || w.get(k).and_then(|x| x.as_str()) != Some(stamp) { changed = true; wrote = true; p.insert(k.clone(), v.clone()); w.insert(k.clone(), json!(stamp)); }
        }
        slot.dirty = changed;   // nothing that differs: the row is not written
        if wrote { slot.updated = ts; }
        drop(g); drop(db);   // the engine's lock is never taken under the store's (the loader takes the store's under the engine's)
        if wrote { self.eng().0.updated.insert(eid.to_string(), ts); }
    }

    /// One property of a row, with the writer that wrote it; a `null` removes it (as a getter's none leaves it absent). A write that changes
    /// nothing (the same value, the same writer or another derived one) leaves the store alone.
    fn store_prop(&self, eid: &str, cls: Option<&str>, prop: &str, new: &J, stamp: &str) {
        let _t = self.txn();
        let known = self.eng().0.docs.get(eid).map(|e| e.cls.clone());
        let ts = self.stamp_ts();
        if self.storage_of(known.as_deref().or(cls).unwrap_or("")) == crate::fields::Storage::Virtual { self.eng().0.updated.insert(eid.to_string(), ts); return; }   // RAM only: no row, but it has its `updated`
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        self.load(&db, eid);
        let mut g = self.pending.lock().unwrap_or_else(|e| e.into_inner());
        let Some(slot) = g.as_mut().and_then(|m| m.get_mut(eid)) else { return };
        if slot.row.is_none() {
            let Some(c) = cls.map(String::from).or(known) else { return };
            slot.row = Some((c, Map::new(), Map::new()));
            slot.dirty = true;
        }
        let (_, p, w) = slot.row.as_mut().unwrap();
        let same_value = match p.get(prop) { Some(v) => v == new, None => new.is_null() };
        // the same value from the same writer, or from another derived writer (a getter's value stored by a rebuild or a later getter run): nothing to say
        let same_writer = match w.get(prop).and_then(|x| x.as_str()) { Some(o) => o == stamp || (is_getter_write(o) && is_getter_write(stamp)), None => false };
        if same_value && same_writer { return; }
        if new.is_null() { p.remove(prop); } else { p.insert(prop.into(), new.clone()); }
        w.insert(prop.into(), json!(stamp));
        slot.dirty = true;
        slot.updated = ts;
        drop(g); drop(db);   // the engine's lock is never taken under the store's (the loader takes the store's under the engine's)
        self.eng().0.updated.insert(eid.to_string(), ts);
    }

    /// Every stored row: eid -> {cls, props, writers}.
    pub fn rows(&self) -> std::collections::BTreeMap<String, J> {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        self.flush(&db);   // what an open transaction changed is in the table before it is read
        let mut st = db.prepare(&format!("SELECT k, body FROM {ROWS} ORDER BY k")).expect("rows");
        st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).expect("rows").filter_map(|r| r.ok())
            .map(|(k, b)| (k, serde_json::from_str(&b).unwrap_or(J::Null))).collect()
    }

    // ---- the journal -------------------------------------------------------------------------------------------------
    /// Whether the line is about an entity of a VIRTUAL class: not journalled (docs/cliffvm.md, "Storage modes"). A write's class is the engine's entity's, else the eid's.
    fn is_virtual_line(&self, l: &Line) -> bool {
        let cls = match l {
            Line::Born { cls, .. } => cls.clone(),
            Line::Write { eid, .. } => self.eng().0.docs.get(eid).map(|e| e.cls.clone()).unwrap_or_else(|| class_of_eid(eid)),
        };
        self.storage_of(&cls) == crate::fields::Storage::Virtual
    }

    fn journal_push(&self, l: Line) {
        if self.is_virtual_line(&l) { return; }
        let _t = self.txn();
        let mut j = self.journal.lock().unwrap_or_else(|e| e.into_inner());
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let body = l.to_json().to_string();
        self.exec(&db, &format!("INSERT OR REPLACE INTO {JOURNAL} (k, body) VALUES (?1, ?2)"), params![format!("{:012}", self.journal_next.fetch_add(1, Ordering::SeqCst)), body]);
        // THE IN-MEMORY COPY keeps no huge value: the stored line (c_journal, what a replay reads) is whole, the copy in RAM names its size instead. A query receipt of the history import
        // carries a page of rows (megabytes) and every page was kept here for the life of the process (measured 2026-11-14: ~100 MB of RSS a page). A flagged threshold (JOURNAL_RAM_MAX_BYTES).
        if body.len() > JOURNAL_RAM_MAX_BYTES {
            if let Line::Write { ts, writer, eid, prop, .. } = &l { j.push(Line::Write { ts: *ts, writer: writer.clone(), eid: eid.clone(), prop: prop.clone(), new: json!({"elided_bytes": body.len()}) }); return; }
        }
        j.push(l);
    }

    /// The memory breakdown (alloc-stats): (name, entries, bytes) of the engine's and the host's tables, one line.
    #[cfg(feature = "alloc-stats")]
    pub fn mem_line(&self) -> String {
        let mut v = { let g = self.eng(); let mut v = g.0.mem_sizes(); v.extend(g.1.mem_sizes()); if let Some(st) = &g.0.store { v.extend(st.mem_sizes()); } v };
        { let j = self.journal.lock().unwrap_or_else(|e| e.into_inner()); v.push(("host.journal".into(), j.len() as u64, j.iter().map(|l| crate::alloc_stats::jsize(&l.to_json())).sum())); }
        { let p = self.pending.lock().unwrap_or_else(|e| e.into_inner()); v.push(("host.pending".into(), p.as_ref().map(|m| m.len()).unwrap_or(0) as u64, p.as_ref().map(|m| m.values().map(|x| x.row.as_ref().map(|(a, b, c)| a.len() as u64 + b.iter().chain(c.iter()).map(|(k, x)| k.len() as u64 + crate::alloc_stats::jsize(x)).sum::<u64>()).unwrap_or(0)).sum()).unwrap_or(0))); }
        format!("mem-breakdown {}", v.iter().map(|(n, c, b)| format!("{n}={c}/{}", b / 1024)).collect::<Vec<_>>().join(" "))
    }

    pub fn journal(&self) -> Vec<Line> { self.journal.lock().unwrap_or_else(|e| e.into_inner()).clone() }

    /// The journal as the store holds it (what a later process replays).
    pub fn journal_stored(&self) -> Result<Vec<Line>, String> {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let mut st = db.prepare(&format!("SELECT body FROM {JOURNAL} ORDER BY k")).map_err(dbe)?;
        let rows: Vec<String> = st.query_map([], |r| r.get(0)).map_err(dbe)?.filter_map(|r| r.ok()).collect();
        rows.iter().map(|b| Line::from_json(&serde_json::from_str::<J>(b).map_err(|e| e.to_string())?)).collect()
    }

    // ---- reading -----------------------------------------------------------------------------------------------------
    /// One property of an entity as the engine holds it (null when it has none). A stored entity that is not loaded (a lazy class) is a REFERENCE like any other: it loads first.
    pub fn get(&self, eid: &str, prop: &str) -> J {
        if self.eng().0.unloaded.contains_key(eid) { self.ensure_loaded(eid, self.now()); self.settle_loaded(); }
        self.eng().0.docs.get(eid).and_then(|e| e.props.get(prop).cloned()).unwrap_or(J::Null)
    }
    /// The engine's mirror: eid -> {cls, props}.
    pub fn snapshot(&self) -> std::collections::BTreeMap<String, J> {
        self.eng().0.docs.iter().map(|(k, e)| (k.clone(), json!({"cls": e.cls, "props": e.props}))).collect()
    }
    /// The cap on the slow drivers' workers (Command, File, Fetch), the boot fact `effect_workers`.
    pub fn set_command_timeout(&self, s: f64) { if let Some(st) = &self.eng().0.store { st.set_command_timeout(s); } }
    pub fn set_effect_workers(&self, n: usize) { if let Some(st) = &self.eng().0.store { st.set_effect_workers(n); } }
    pub fn waiter(&self) -> Option<crate::store::Waiter> { self.waiter.clone() }
}

/// The seat tells the field it has started (fields_live.boot_started): the facts first, as properties of `Boot:seat` and then as the
/// whole dict `facts`, the announcement last, so a setter on `started` finds them. All outside writes (`host:boot`).
pub fn boot_started(h: &Host, facts: &Map<String, J>, announce: bool) {
    h.hold("Boot:seat");   // the seat's own state: the boot facts live as long as the run
    if !h.snapshot().contains_key("Boot:seat") {
        let mut p = Map::new();
        p.insert("name".into(), json!("this seat's start"));
        h.born("Boot:seat", "Boot", p, BOOT_WRITER);
    }
    for (k, v) in facts { h.write("Boot:seat", k, v.clone(), BOOT_WRITER); }
    let mut whole = h.get("Boot:seat", "facts").as_object().cloned().unwrap_or_default();
    for (k, v) in facts { whole.insert(k.clone(), v.clone()); }
    h.write("Boot:seat", "facts", J::Object(whole), BOOT_WRITER);
    if announce { h.write("Boot:seat", "started", json!(h.now()), BOOT_WRITER); }
}

/// THE ENVIRONMENT (#1143, operator 2026-11-14: "Environment node that holds envvars as its properties"): at the start the host writes ONE entity, `Environment:host`, whose PROPERTIES ARE the
/// environment variables (property name = variable name; a variable that is not set is absent). A STORED entity (class `Environment`, RESIDENT, declared by the pack `environment`), written by a
/// JOURNALLED outside write (`host:environment`), so a replay shows the value the boot saw. THE SECRET RULE: only the names of `ENV_DEFAULT_ALLOW` (plus the launch fact `env_allow`) carry their
/// value; a variable whose name holds KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL (any case) is published as the fixed text `set` when it is set and non-empty, never its value; every other variable is absent.
pub const ENV_WRITER: &str = "host:environment";
pub const ENV_ENTITY: &str = "Environment:host";
pub const ENV_SET: &str = "set";
pub const ENV_DEFAULT_ALLOW: [&str; 8] = ["HOME", "USER", "LANG", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "MEMBRANE_DATA_DIR", "CLAUDE_BIN", "TMPDIR"];
pub const ENV_SECRET_WORDS: [&str; 6] = ["KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL"];
pub fn env_secret_name(name: &str) -> bool { let u = name.to_uppercase(); ENV_SECRET_WORDS.iter().any(|w| u.contains(w)) }

/// The properties of `Environment:host` for this environment and these facts, and the names `env_allow` asked for that the secret rule refused (each as a line that says why; never a value).
pub fn environment_props(facts: &Map<String, J>, env: &[(String, String)]) -> (Map<String, J>, Vec<String>) {
    let asked: Vec<String> = match facts.get("env_allow") {
        Some(J::String(t)) => t.split(|c: char| c == ',' || c.is_whitespace()).filter(|x| !x.is_empty()).map(String::from).collect(),
        Some(J::Array(a)) => a.iter().filter_map(|v| v.as_str()).map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect(),
        _ => vec![],
    };
    let mut refused = vec![];
    let mut allow: Vec<String> = ENV_DEFAULT_ALLOW.iter().map(|s| s.to_string()).collect();
    for n in asked {
        if env_secret_name(&n) { refused.push(format!("env_allow: {n} is refused: a name with KEY, TOKEN, SECRET, PASSWORD, PASSWD or CREDENTIAL in it never carries its value (the environment shows only that it is set)")); }
        else { allow.push(n); }
    }
    let mut out = Map::new();
    for (k, v) in env {
        if v.is_empty() { continue; }
        if env_secret_name(k) { out.insert(k.clone(), json!(ENV_SET)); }
        else if allow.contains(k) { out.insert(k.clone(), json!(v)); }
    }
    (out, refused)
}

/// Write `Environment:host` (a quiet host whose packs do not declare the class writes nothing). A property of an earlier run's row that this environment no longer has is cleared (journalled).
/// -> the refusal lines for the caller to say.
pub fn boot_environment(h: &Host, facts: &Map<String, J>, env: &[(String, String)]) -> Vec<String> {
    if !h.eng().1.owns_class("Environment") { return vec![]; }
    let (props, refused) = environment_props(facts, env);
    if !h.snapshot().contains_key(ENV_ENTITY) { h.born(ENV_ENTITY, "Environment", Map::new(), ENV_WRITER); }
    let old: Vec<String> = h.snapshot().get(ENV_ENTITY).and_then(|e| e["props"].as_object().map(|p| p.keys().cloned().collect())).unwrap_or_default();
    // a property a getter of the class owns is the getter's, not a variable's: never cleared, never stored over
    let owned = |k: &str| h.eng().1.has_getter("Environment", k);
    for k in old { if k != "parents" && !props.contains_key(&k) && !owned(&k) { h.write(ENV_ENTITY, &k, J::Null, ENV_WRITER); } }
    for (k, v) in &props { if !owned(k) { h.write(ENV_ENTITY, k, v.clone(), ENV_WRITER); } }
    refused
}
