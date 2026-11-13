//! THE HOST LOOP, in Rust (docs/cliffvm.md): the minimum of membrane/fields_live.py that a process with no Python needs.
//! It owns the engine (Core + Fields), one SQLite connection for the entity rows and the journal, and the door's inbound
//! requests. What it does, in the order fields_live does it:
//!   - hears OUTSIDE writes (the door's requests, the boot's facts, a person's write) and gives them to the engine;
//!   - drains the engine's output (`Fields::out`) and applies every write to the store with the writer stamped as
//!     fields_live.stamp_writer / stamp_born_writer do: `trigger:field:<setter>` for the engine's own writes (replay
//!     re-derives them), `host:...` for a Trip (a time kill cannot be re-derived: it depends on how long something took);
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

pub const WRITER: &str = "trigger:field:";
pub const TRIP_WRITER: &str = "field:driver:trip";
pub const TRIP_CLASS: &str = "Trip";
/// the collection (table `c_field`) of the entity rows: k = eid, body = {cls, props, writers}
pub const ROWS: &str = "c_field";
/// the journal's collection (table `c_journal`): k = sequence number, body = a line
pub const JOURNAL: &str = "c_journal";
/// the writer of what the door pushes in (a request) and of what the boot tells (facts, started)
pub const DOOR_WRITER: &str = "host:door";
pub const BOOT_WRITER: &str = "host:boot";
/// A Pack entity (docs/cliffvm.md, "Pack entities"): `Pack:<name>`, its c1f in `text`; the host mirrors the outcome back as derived writes
pub const PACK_PREFIX: &str = "Pack:";
pub const PACK_CLASS: &str = "Pack";
pub const PACK_WRITER: &str = "pack";
/// The pack the host itself marks privileged (packs/boot.c1f): the root of trust.

/// The store writer for an engine write (fields_live.stamp_writer): derived, except a trip (outside).
pub fn stamp_writer(engine_writer: &str) -> String {
    if engine_writer == TRIP_WRITER { return format!("host:{}", engine_writer.trim_start_matches("field:")); }
    format!("{WRITER}{}", engine_writer.trim_start_matches("field:"))
}

/// The writer for an entity the engine created (fields_live.stamp_born_writer): a Trip is outside.
pub fn stamp_born_writer(cls: &str) -> String {
    if cls == TRIP_CLASS { stamp_writer(TRIP_WRITER) } else { format!("{WRITER}create") }
}

/// An outside write is anything the engine can not re-derive.
pub fn is_outside(stamp: &str) -> bool { !stamp.starts_with(WRITER) }

/// One line of the journal.
#[derive(Clone, Debug, PartialEq)]
pub enum Line {
    Born { ts: f64, writer: String, eid: String, cls: String, props: Map<String, J> },
    Write { ts: f64, writer: String, eid: String, prop: String, new: J },
}

impl Line {
    pub fn writer(&self) -> &str { match self { Line::Born { writer, .. } | Line::Write { writer, .. } => writer } }
    pub fn to_json(&self) -> J {
        match self {
            Line::Born { ts, writer, eid, cls, props } => json!({"op": "born", "ts": ts, "writer": writer, "eid": eid, "cls": cls, "props": props}),
            Line::Write { ts, writer, eid, prop, new } => json!({"op": "write", "ts": ts, "writer": writer, "eid": eid, "prop": prop, "new": new}),
        }
    }
    pub fn from_json(j: &J) -> Result<Line, String> {
        let s = |k: &str| j.get(k).and_then(|v| v.as_str()).map(String::from).ok_or_else(|| format!("journal line: no {k}"));
        let ts = j.get("ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
        match j.get("op").and_then(|v| v.as_str()) {
            Some("born") => Ok(Line::Born { ts, writer: s("writer")?, eid: s("eid")?, cls: s("cls")?, props: j.get("props").and_then(|p| p.as_object()).cloned().unwrap_or_default() }),
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
    db: Mutex<Connection>,
    journal: Mutex<Vec<Line>>,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
    waiter: Option<crate::store::Waiter>,
    /// the last refusal of a Pack entity's text (parse, stale setter, PACK line, import clash): name -> message
    refusals: Mutex<std::collections::BTreeMap<String, String>>,
    packs_dirty: AtomicBool,
}

fn dbe(e: rusqlite::Error) -> String { e.to_string() }

impl Host {
    pub fn open(o: Options) -> Result<Host, String> {
        let db = match &o.store {
            Some(p) => Connection::open(p).map_err(|e| format!("store {p}: {e}"))?,
            None => Connection::open_in_memory().map_err(dbe)?,
        };
        db.busy_timeout(Duration::from_millis(5000)).map_err(dbe)?;
        if o.store.is_some() { let _: String = db.query_row("PRAGMA journal_mode = WAL", [], |r| r.get(0)).map_err(dbe)?; }
        for t in [ROWS, JOURNAL] {
            db.execute_batch(&format!("CREATE TABLE IF NOT EXISTS {t} (k TEXT PRIMARY KEY, body TEXT NOT NULL)")).map_err(dbe)?;
        }
        let mut core = Core::new();
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
        fields.inbound_by_host = true;
        // TRUST BY POSITION: nothing is privileged at open (not even a pack named `boot`); `load_app(.., trusted)` marks the first app's packs
        Ok(Host { eng: Mutex::new((core, fields, 0)), db: Mutex::new(db), journal: Mutex::new(vec![]), clock: o.clock, waiter, refusals: Mutex::new(Default::default()), packs_dirty: AtomicBool::new(false) })
    }

    /// Whether the named pack holds the privilege (exempt from rule B): the packs of the first app (the root of trust, by the host), any other by a
    /// privileged writer's grant.
    pub fn pack_privileged(&self, name: &str) -> bool { self.eng().1.is_privileged(name) }
    pub fn now(&self) -> f64 { (self.clock)() }
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
        let now = self.now();
        { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.rebuild(core, now, seq); }
        self.drain_apply();
    }

    // ---- hearing -----------------------------------------------------------------------------------------------------
    /// An outside write: journalled (the engine can not re-derive it), stored, heard by the engine, and what the engine made of it applied.
    pub fn write(&self, eid: &str, prop: &str, new: J, writer: &str) {
        let ts = self.now();
        self.journal_push(Line::Write { ts, writer: writer.into(), eid: eid.into(), prop: prop.into(), new: new.clone() });
        self.inject_write(ts, eid, prop, new, writer, writer);
    }

    /// An outside birth (a request, the boot's entity): journalled, stored, loaded, its getters run.
    pub fn born(&self, eid: &str, cls: &str, props: Map<String, J>, writer: &str) {
        let ts = self.now();
        self.journal_push(Line::Born { ts, writer: writer.into(), eid: eid.into(), cls: cls.into(), props: props.clone() });
        self.inject_born(ts, eid, cls, props, writer, writer);
    }

    /// `engine_writer` is what the engine sees (a Trip arrives as `driver:trip`, the way an effect's receipt does), `stamp` what the store keeps.
    fn inject_write(&self, ts: f64, eid: &str, prop: &str, new: J, engine_writer: &str, stamp: &str) {
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

    fn inject_born(&self, ts: f64, eid: &str, cls: &str, props: Map<String, J>, _engine_writer: &str, stamp: &str) {
        self.store_born(eid, cls, &props, stamp);
        if let Some(t) = props.get("text") { self.pack_text(eid, t); }
        if let Some(v) = props.get("privileged") { self.note_outside_grant(eid, v, stamp); }
        {
            let mut g = self.eng();
            let (core, f, seq) = &mut *g;
            if cls == TRIP_CLASS {
                core.load(eid.into(), cls.into(), props, None, ts);
                f.born(core, &NullHost, eid, ts, seq);        // a kill arrives like an effect's receipt: its node is dirty, as "the kill, nothing else"
            } else {
                f.arrive(core, eid.into(), cls.into(), props, ts);
                f.settle(core, &NullHost, ts, seq);
            }
        }
        self.drain_apply();
    }

    /// Replay: apply a journal to this (fresh) host, line by line. The lines are journalled again (the new journal equals the old one).
    pub fn replay(&self, lines: &[Line]) {
        for l in lines {
            self.journal_push(l.clone());
            match l {
                Line::Born { ts, writer, eid, cls, props } => self.inject_born(*ts, eid, cls, props.clone(), writer, writer),
                Line::Write { ts, writer, eid, prop, new } => {
                    // a time kill arrives the way an effect's receipt does (`driver:trip`), not as a `host:` writer
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
            if !self.drain_apply() && !had { break; }
        }
    }

    fn born_at(&self, ts: f64, eid: &str, cls: &str, props: Map<String, J>, writer: &str) {
        self.journal_push(Line::Born { ts, writer: writer.into(), eid: eid.into(), cls: cls.into(), props: props.clone() });
        self.inject_born(ts, eid, cls, props, writer, writer);
    }

    /// The clock input: armed waits come due.
    pub fn tick(&self) {
        let now = self.now();
        { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.tick(core, &NullHost, now, seq); }
        self.drain_apply();
    }

    /// Run until `stop`: pump, tick every `tick_s`, wait for the drivers (50 ms at most). `each` runs after every turn.
    pub fn run(&self, stop: &AtomicBool, tick_s: f64, each: &mut dyn FnMut(&Host)) {
        let mut last = self.now();
        while !stop.load(Ordering::SeqCst) {
            self.pump();
            if self.now() - last >= tick_s { self.tick(); last = self.now(); }
            each(self);
            match &self.waiter { Some(w) => { w.wait(Some(Duration::from_millis(50))); } None => std::thread::sleep(Duration::from_millis(50)) }
        }
        if let Some(w) = &self.waiter { w.interrupt_all(); }
    }

    // ---- the engine's output -----------------------------------------------------------------------------------------
    /// Take what the engine did since the last drain and apply it to the store, stamped. -> whether there was any.
    fn drain_apply(&self) -> bool {
        let (out, _calls) = { let mut g = self.eng(); let f = &mut g.1; (std::mem::take(&mut f.out), std::mem::take(&mut f.calls)) };
        let any = !out.is_empty();
        let ts = self.now();
        for w in out {
            if let Some(cls) = w.get("born").and_then(|c| c.as_str()) {
                let eid = w["eid"].as_str().unwrap_or("").to_string();
                let stamp = stamp_born_writer(cls);
                self.store_born(&eid, cls, &Map::new(), &stamp);
                if is_outside(&stamp) { self.journal_push(Line::Born { ts, writer: stamp, eid, cls: cls.into(), props: Map::new() }); }
                continue;
            }
            let (Some(eid), Some(prop)) = (w["eid"].as_str(), w["prop"].as_str()) else { continue };
            let stamp = stamp_writer(w["writer"].as_str().unwrap_or(""));
            self.store_prop(eid, None, prop, &w["new"], &stamp);
            if prop == "text" { self.pack_text(eid, &w["new"]); }
            if prop == "privileged" && eid.starts_with(PACK_PREFIX) { self.packs_dirty.store(true, Ordering::SeqCst); }   // `granted_by` is mirrored
            if is_outside(&stamp) { self.journal_push(Line::Write { ts, writer: stamp, eid: eid.into(), prop: prop.into(), new: w["new"].clone() }); }
        }
        // `retire = true` (any writer): the entity leaves the engine and the store; what read it recalculates. Derived, so never journalled.
        let retired = std::mem::take(&mut self.eng().1.retired);
        for eid in &retired {
            { let mut g = self.eng(); let (core, f, seq) = &mut *g; f.remove(core, &NullHost, eid, ts, seq); }
            let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
            let _ = db.execute(&format!("DELETE FROM {ROWS} WHERE k = ?1"), params![eid]);
        }
        let any = any || !retired.is_empty();
        if self.packs_dirty.swap(false, Ordering::SeqCst) { self.refresh_packs(); return true; }
        any
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
        let rows: Vec<(String, Map<String, J>)> = self.rows().into_iter()
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

    // ---- the store ---------------------------------------------------------------------------------------------------
    fn row(&self, db: &Connection, eid: &str) -> Option<(String, Map<String, J>, Map<String, J>)> {
        let body: Option<String> = db.query_row(&format!("SELECT body FROM {ROWS} WHERE k = ?"), params![eid], |r| r.get(0)).optional().ok().flatten();
        let j: J = serde_json::from_str(&body?).ok()?;
        Some((j["cls"].as_str().unwrap_or("").to_string(), j["props"].as_object().cloned().unwrap_or_default(), j["writers"].as_object().cloned().unwrap_or_default()))
    }

    fn put_row(&self, db: &Connection, eid: &str, cls: &str, props: &Map<String, J>, writers: &Map<String, J>) {
        let body = json!({"cls": cls, "props": props, "writers": writers}).to_string();
        let _ = db.execute(&format!("INSERT INTO {ROWS} (k, body) VALUES (?1, ?2) ON CONFLICT(k) DO UPDATE SET body = excluded.body"), params![eid, body]);
    }

    fn store_born(&self, eid: &str, cls: &str, props: &Map<String, J>, stamp: &str) {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let (mut p, mut w) = self.row(&db, eid).map(|(_, p, w)| (p, w)).unwrap_or_default();
        for (k, v) in props { p.insert(k.clone(), v.clone()); w.insert(k.clone(), json!(stamp)); }
        self.put_row(&db, eid, cls, &p, &w);
    }

    /// One property of a row, with the writer that wrote it; a `null` removes it (as a getter's none leaves it absent).
    fn store_prop(&self, eid: &str, cls: Option<&str>, prop: &str, new: &J, stamp: &str) {
        let known = self.eng().0.docs.get(eid).map(|e| e.cls.clone());
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let Some((c, mut p, mut w)) = self.row(&db, eid).or_else(|| cls.map(String::from).or(known).map(|c| (c, Map::new(), Map::new()))) else { return };
        if new.is_null() { p.remove(prop); } else { p.insert(prop.into(), new.clone()); }
        w.insert(prop.into(), json!(stamp));
        self.put_row(&db, eid, &c, &p, &w);
    }

    /// Every stored row: eid -> {cls, props, writers}.
    pub fn rows(&self) -> std::collections::BTreeMap<String, J> {
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let mut st = db.prepare(&format!("SELECT k, body FROM {ROWS} ORDER BY k")).expect("rows");
        st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).expect("rows").filter_map(|r| r.ok())
            .map(|(k, b)| (k, serde_json::from_str(&b).unwrap_or(J::Null))).collect()
    }

    // ---- the journal -------------------------------------------------------------------------------------------------
    fn journal_push(&self, l: Line) {
        let mut j = self.journal.lock().unwrap_or_else(|e| e.into_inner());
        let db = self.db.lock().unwrap_or_else(|e| e.into_inner());
        let _ = db.execute(&format!("INSERT OR REPLACE INTO {JOURNAL} (k, body) VALUES (?1, ?2)"), params![format!("{:012}", j.len()), l.to_json().to_string()]);
        j.push(l);
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
    pub fn get(&self, eid: &str, prop: &str) -> J { self.eng().0.docs.get(eid).and_then(|e| e.props.get(prop).cloned()).unwrap_or(J::Null) }
    /// The engine's mirror: eid -> {cls, props}.
    pub fn snapshot(&self) -> std::collections::BTreeMap<String, J> {
        self.eng().0.docs.iter().map(|(k, e)| (k.clone(), json!({"cls": e.cls, "props": e.props}))).collect()
    }
    pub fn waiter(&self) -> Option<crate::store::Waiter> { self.waiter.clone() }
}

/// The seat tells the field it has started (fields_live.boot_started): the facts first, as properties of `Boot:seat` and then as the
/// whole dict `facts`, the announcement last, so a setter on `started` finds them. All outside writes (`host:boot`).
pub fn boot_started(h: &Host, facts: &Map<String, J>, announce: bool) {
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
