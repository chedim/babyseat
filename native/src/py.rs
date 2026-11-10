//! The Python bindings (PyO3): what `membrane/membrane_vm.so` exports to the seat.
#![allow(clippy::too_many_arguments)]
use crate::{expr, fields, mapped, topk, vm};
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use serde_json::{Map, Value as J};

use vm::{Host, Spec, Step, Trig, Write};

fn bad<E: std::fmt::Display>(e: E) -> PyErr { PyValueError::new_err(e.to_string()) }

struct PyHost<'py> { py: Python<'py>, obj: &'py Bound<'py, PyAny> }

impl<'py> PyHost<'py> {
    fn opt(&self, name: &str, args: impl pyo3::call::PyCallArgs<'py>) -> Option<String> {
        self.obj.call_method1(name, args).ok().and_then(|r| r.extract::<Option<String>>().ok()).flatten()
    }
    fn yes(&self, name: &str, args: impl pyo3::call::PyCallArgs<'py>) -> bool {
        self.obj.call_method1(name, args).ok().and_then(|r| r.is_truthy().ok()).unwrap_or(false)
    }
}

impl<'py> Host for PyHost<'py> {
    fn resolve(&self, spec: &str) -> Option<String> { let _ = self.py; self.opt("resolve", (spec,)) }
    fn resolve_exact(&self, spec: &str) -> Option<String> { self.opt("resolve_exact", (spec,)) }
    fn dispatch(&self, eid: &str, method: &str) -> Option<String> { self.opt("dispatch", (eid, method)) }
    fn vel(&self, eid: &str, now: f64, thr: f64) -> bool { self.yes("vel", (eid, now, thr)) }
    fn align(&self, eid: &str, thr: f64) -> bool { self.yes("align", (eid, thr)) }
    fn live(&self, eid: &str, position: bool) -> Option<String> { self.opt("live", (eid, position)) }
    /// A getter's host function: the host object's `call(name, args_json)` returns the result as JSON, None when
    /// it has no such function; a Python error is the getter's error.
    fn call(&self, f: &str, args_json: &str) -> Option<Result<String, String>> {
        if !self.obj.hasattr("call").unwrap_or(false) { return None; }
        match self.obj.call_method1("call", (f, args_json)) {
            Ok(r) => match r.extract::<Option<String>>() { Ok(Some(s)) => Some(Ok(s)), Ok(None) => None, Err(e) => Some(Err(e.to_string())) },
            Err(e) => Some(Err(e.to_string())),
        }
    }
}

fn obj(s: &str) -> PyResult<Map<String, J>> {
    match serde_json::from_str::<J>(s).map_err(bad)? { J::Object(m) => Ok(m), J::Null => Ok(Map::new()), _ => Err(bad("expected an object")) }
}

fn pos_of(j: &J) -> Option<Vec<f64>> {
    j.as_array().map(|a| a.iter().map(|x| x.as_f64().unwrap_or(0.0)).collect())
}

fn atoms(j: &J) -> Vec<Vec<J>> {
    j.as_array().map(|a| a.iter().map(|x| x.as_array().cloned().unwrap_or_default()).collect()).unwrap_or_default()
}

type Row = (String, String, Option<Map<String, J>>, Option<Vec<f64>>);

fn rows_of(rows_json: &str, pos: &[u8]) -> PyResult<Vec<Row>> {
    let rows: J = serde_json::from_str(rows_json).map_err(bad)?;
    let mut at = 0usize;
    let mut out = Vec::new();
    for r in rows.as_array().cloned().unwrap_or_default() {
        let a = r.as_array().cloned().unwrap_or_default();
        if a.len() < 4 { continue; }
        let dim = a[3].as_i64().unwrap_or(-1);
        let keep = dim == -2;
        let p = if dim >= 0 {
            let n = dim as usize * 8;
            if at + n > pos.len() { return Err(bad("positions buffer too short")); }
            let v: Vec<f64> = pos[at..at + n].chunks_exact(8).map(|b| f64::from_le_bytes(b.try_into().unwrap())).collect();
            at += n;
            Some(v)
        } else { None };
        out.push((a[0].as_str().unwrap_or("").to_string(), a[1].as_str().unwrap_or("").to_string(),
                  a[2].as_object().cloned(), if keep { Some(vec![f64::NAN]) } else { p }));
    }
    Ok(out)
}

#[pyclass]
struct Core { c: std::sync::Mutex<vm::Core>, host: Py<PyAny> }

impl Core {
    fn g(&self) -> std::sync::MutexGuard<'_, vm::Core> { self.c.lock().unwrap_or_else(|e| e.into_inner()) }
}

#[pymethods]
impl Core {
    #[new]
    fn new(host: Py<PyAny>) -> Self { Core { c: std::sync::Mutex::new(vm::Core::new()), host } }

    /// One entity into the mirror (the VM coming up over an existing store).
    fn load(&self, eid: String, cls: String, props_json: &str, pos_json: &str, now: f64) -> PyResult<()> {
        let pos = pos_of(&serde_json::from_str::<J>(pos_json).map_err(bad)?);
        self.g().load(eid, cls, obj(props_json)?, pos, now);
        Ok(())
    }

    /// Many entities' live content at once (a sweep): rows [[eid, cls, props | null, dim], ...] as JSON,
    /// their positions as one buffer of little-endian f64 (dim numbers per row, dim -1 = no position).
    fn refresh_many(&self, rows_json: &str, pos: &[u8]) -> PyResult<()> {
        let rows = rows_of(rows_json, pos)?;
        let mut c = self.g();
        for (eid, cls, props, p) in rows { c.refresh(eid, cls, props, p); }
        Ok(())
    }

    /// The VM coming up: the same rows, in one call.
    fn load_many(&self, rows_json: &str, pos: &[u8], now: f64) -> PyResult<()> {
        let rows = rows_of(rows_json, pos)?;
        let mut c = self.g();
        for (eid, cls, props, p) in rows { c.load(eid, cls, props.unwrap_or_default(), p, now); }
        Ok(())
    }

    /// What a sweep can read: {"all": bool, "eids": [...]}.
    fn sweep_eids(&self, py: Python<'_>) -> PyResult<String> {
        let h = self.host.bind(py).clone();
        let host = PyHost { py, obj: &h };
        let (all, geo, eids) = self.g().sweep_eids(&host);
        Ok(serde_json::json!({"all": all, "positions": geo, "eids": eids}).to_string())
    }

    /// A write heard: {eid, prop, new, old, writer, chain[, born: {cls, props, position}]}.
    fn hear(&self, write_json: &str, now: f64) -> PyResult<()> {
        let w: J = serde_json::from_str(write_json).map_err(bad)?;
        let get = |k: &str| w.get(k).cloned().unwrap_or(J::Null);
        let born = w.get("born").and_then(|b| b.as_object()).map(|b| (
            b.get("cls").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            b.get("props").and_then(|x| x.as_object()).cloned().unwrap_or_default(),
            b.get("position").and_then(pos_of),
        ));
        let wr = Write {
            eid: get("eid").as_str().unwrap_or("").to_string(),
            prop: get("prop").as_str().unwrap_or("").to_string(),
            new: get("new"), old: get("old"),
            writer: get("writer").as_str().unwrap_or("").to_string(),
            chain: get("chain"),
            seq: get("seq").as_i64().unwrap_or(-1),
        };
        self.g().hear(wr, born, now);
        Ok(())
    }

    fn set_cls(&self, eid: &str, cls: String) { self.g().set_cls(eid, cls); }

    /// The triggers, in definition order: [{eid, spec: {when, where, write} | null, mine}].
    fn set_triggers(&self, trs_json: &str) -> PyResult<()> {
        let v: J = serde_json::from_str(trs_json).map_err(bad)?;
        let trs = v.as_array().cloned().unwrap_or_default().into_iter().map(|t| {
            let spec = t.get("spec").filter(|s| s.is_object()).map(|s| {
                let wr = s.get("write").and_then(|x| x.as_array()).cloned().unwrap_or_default();
                Spec {
                    when: atoms(s.get("when").unwrap_or(&J::Null)),
                    wh: atoms(s.get("where").unwrap_or(&J::Null)),
                    target: wr.first().and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    expr: wr.get(1).and_then(|x| x.as_str()).unwrap_or("").to_string(),
                }
            });
            Trig { eid: t.get("eid").and_then(|x| x.as_str()).unwrap_or("").to_string(), spec,
                   mine: t.get("mine").and_then(|x| x.as_bool()).unwrap_or(false) }
        }).collect();
        self.g().set_triggers(trs);
        Ok(())
    }

    fn forget_state(&self, eid: &str) { self.g().forget_state(eid); }

    /// Run to the next firing: None (nothing queued), "#<seq>" (that write finished), or the firing's JSON.
    fn step(&self, py: Python<'_>, now: f64, fallback_chain_json: &str) -> PyResult<Option<String>> {
        let h = self.host.bind(py).clone();
        let host = PyHost { py, obj: &h };
        let fb: J = serde_json::from_str(fallback_chain_json).map_err(bad)?;
        Ok(match self.g().step(&host, now, &fb) {
            Step::Idle => None,
            Step::Done(seq) => Some(format!("#{}", seq)),
            Step::Fire(j) => Some(j.to_string()),
        })
    }

    fn start_sweep(&self, now: f64, chain_json: &str) -> PyResult<()> {
        self.g().start_sweep(now, serde_json::from_str(chain_json).map_err(bad)?);
        Ok(())
    }

    fn busy(&self) -> bool { self.g().busy() }
    fn skip_trigger(&self) { self.g().skip_trigger(); }
    /// Tracing (tests): which triggers each write visited, in order.
    fn set_trace(&self, on: bool) { self.g().trace = if on { Some(Vec::new()) } else { None }; }
    fn take_trace(&self) -> Vec<String> { self.g().trace.as_mut().map(std::mem::take).unwrap_or_default() }
    fn set_learner(&self, on: bool) { self.g().learner = on; }
    fn queued(&self) -> usize { self.g().queued() }
    fn fired_ok(&self, teid: &str) { self.g().fired_ok(teid); }
    fn error(&self, teid: &str, msg: String) { self.g().error(teid, msg); }

    fn report(&self) -> String {
        let c = self.g();
        serde_json::json!({"dropped": c.dropped, "fired": c.fired, "refused": c.refused,
                           "errors": c.errors, "queued": c.queued()}).to_string()
    }
}

/// Parse and evaluate one expression against plain values (tests: the language matches ch.py's).
#[pyfunction]
fn evaluate(src: &str, env_json: &str) -> PyResult<String> {
    struct NoWorld;
    impl expr::World for NoWorld { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    let env: Map<String, J> = obj(env_json)?;
    let env = env.iter().map(|(k, v)| (k.clone(), expr::from_json(v))).collect();
    let node = expr::parse(src).map_err(bad)?;
    let v = expr::eval(&node, &env, &NoWorld).map_err(bad)?;
    Ok(expr::to_json(&v).map_err(bad)?.to_string())
}

/// The autonode fingerprint walk (walk.rs) with the GIL released for the whole tree.
#[pyfunction]
#[pyo3(signature = (root, skip_dirs, skip_names, skip_prefixes, skip_suffixes, max_files))]
fn fingerprints(py: Python<'_>, root: &str, skip_dirs: Vec<String>, skip_names: Vec<String>, skip_prefixes: Vec<String>,
                skip_suffixes: Vec<String>, max_files: usize) -> Vec<(String, i64, u64)> {
    let skip = crate::walk::Skip { dirs: skip_dirs, names: skip_names, prefixes: skip_prefixes, suffixes: skip_suffixes, hidden_dirs: true };
    let root = std::path::PathBuf::from(root);
    py.detach(move || crate::walk::fingerprints(&root, &skip, max_files))
}

#[pyclass]
struct VecStore { s: std::sync::Mutex<topk::VecStore> }

#[pymethods]
impl VecStore {
    #[new]
    fn new(dim: usize) -> Self { VecStore { s: std::sync::Mutex::new(topk::VecStore::new(dim)) } }
    fn add(&self, id: &str, v: Vec<f32>) { self.s.lock().unwrap_or_else(|e| e.into_inner()).add(id, &v) }
    fn len(&self) -> usize { self.s.lock().unwrap_or_else(|e| e.into_inner()).len() }
    /// The cached `near` getter: [(id, cosine)] best first.
    fn near(&self, q: Vec<f32>, k: usize) -> Vec<(String, f32)> { self.s.lock().unwrap_or_else(|e| e.into_inner()).near(&q, k) }
}

/// #1025: the sentence vectors read in place from the store's float16 file (Metal no-copy buffers under
/// Metal on (unless MEMBRANE_GPU_TOPK=0), else the CPU over the same mapping).
#[pyclass]
struct MappedVecs { m: std::sync::Mutex<mapped::Mapped> }

#[pymethods]
impl MappedVecs {
    #[new]
    fn new(path: &str, dim: usize) -> PyResult<Self> {
        let m = mapped::Mapped::open(path, dim, topk::gpu_enabled()).map_err(bad)?;
        Ok(MappedVecs { m: std::sync::Mutex::new(m) })
    }
    /// The top `k` of the first `n` rows: [(row, cosine)], best first. The GIL is released while it runs.
    fn near(&self, py: Python<'_>, q: Vec<f32>, k: usize, n: usize) -> PyResult<Vec<(usize, f32)>> {
        py.detach(|| self.m.lock().unwrap_or_else(|e| e.into_inner()).near(&q, k, n)).map_err(bad)
    }
    fn on_gpu(&self) -> bool { self.m.lock().unwrap_or_else(|e| e.into_inner()).on_gpu() }
    fn chunks(&self) -> usize { self.m.lock().unwrap_or_else(|e| e.into_inner()).chunks() }
}

/// The engine's host while the GIL is released: each callback takes the GIL for its own Python call only, so a
/// field round (and a store query in it) never holds the GIL, and no thread waits for the engine while holding it.
struct DetachedHost { obj: Py<PyAny> }

impl DetachedHost {
    fn opt<A>(&self, name: &str, args: A) -> Option<String> where A: for<'py> pyo3::call::PyCallArgs<'py> {
        Python::attach(|py| self.obj.bind(py).call_method1(name, args).ok().and_then(|r| r.extract::<Option<String>>().ok()).flatten())
    }
    fn yes<A>(&self, name: &str, args: A) -> bool where A: for<'py> pyo3::call::PyCallArgs<'py> {
        Python::attach(|py| self.obj.bind(py).call_method1(name, args).ok().and_then(|r| r.is_truthy().ok()).unwrap_or(false))
    }
}

impl Host for DetachedHost {
    fn resolve(&self, spec: &str) -> Option<String> { self.opt("resolve", (spec,)) }
    fn resolve_exact(&self, spec: &str) -> Option<String> { self.opt("resolve_exact", (spec,)) }
    fn dispatch(&self, eid: &str, method: &str) -> Option<String> { self.opt("dispatch", (eid, method)) }
    fn vel(&self, eid: &str, now: f64, thr: f64) -> bool { self.yes("vel", (eid, now, thr)) }
    fn align(&self, eid: &str, thr: f64) -> bool { self.yes("align", (eid, thr)) }
    fn live(&self, eid: &str, position: bool) -> Option<String> { self.opt("live", (eid, position)) }
    fn call(&self, f: &str, args_json: &str) -> Option<Result<String, String>> {
        Python::attach(|py| {
            let o = self.obj.bind(py);
            if !o.hasattr("call").unwrap_or(false) { return None; }
            match o.call_method1("call", (f, args_json)) {
                Ok(r) => match r.extract::<Option<String>>() { Ok(Some(s)) => Some(Ok(s)), Ok(None) => None, Err(e) => Some(Err(e.to_string())) },
                Err(e) => Some(Err(e.to_string())),
            }
        })
    }
    /// The host object lists its slow functions in `later` (a set of names); those run on the driver's threads.
    fn call_later(&self, f: &str, args_json: &str, asker: &str) -> Option<vm::HostJob> {
        Python::attach(|py| {
            let o = self.obj.bind(py);
            let later = o.getattr("later").ok()?;
            if !later.contains(f).unwrap_or(false) { return None; }
            let obj = self.obj.clone_ref(py);
            let (f, a, k) = (f.to_string(), args_json.to_string(), asker.to_string());
            Some(Box::new(move || Python::attach(|py| match obj.bind(py).call_method1("call", (f.as_str(), a.as_str(), k.as_str())) {
                Ok(r) => match r.extract::<Option<String>>() { Ok(Some(s)) => Ok(s), Ok(None) => Err("no such function".into()), Err(e) => Err(e.to_string()) },
                Err(e) => Err(e.to_string()),
            })) as vm::HostJob)
        })
    }
}

type Engine = (vm::Core, fields::Fields, i64);

/// Fields (#994): the field engine over its own Core, reachable from Python. Thread-safe and non-blocking: every
/// method releases the GIL before it takes the engine's lock (so no thread ever waits for the engine while holding
/// the GIL), and a round's Python callbacks take the GIL back only for themselves.
#[pyclass]
struct Fields { c: std::sync::Mutex<Engine>, host: Py<PyAny>, waiter: std::sync::Mutex<Option<crate::store::Waiter>> }

impl Fields {
    fn with<R: Send>(&self, py: Python<'_>, f: impl FnOnce(&mut Engine) -> R + Send) -> R {
        py.detach(|| { let mut g = self.c.lock().unwrap_or_else(|e| e.into_inner()); f(&mut g) })
    }
    fn host(&self, py: Python<'_>) -> DetachedHost { DetachedHost { obj: self.host.clone_ref(py) } }
}

#[pymethods]
impl Fields {
    #[new]
    fn new(host: Py<PyAny>) -> Self {
        Fields { c: std::sync::Mutex::new((vm::Core::new(), fields::Fields::goalgraph(), 0)), host, waiter: std::sync::Mutex::new(None) }
    }
    /// The store driver: `sql(...)` in getters and setters reads this SQLite file (read-only, its own connection).
    fn set_store(&self, py: Python<'_>, path: &str) -> PyResult<()> {
        let st = crate::store::Store::open(path).map_err(bad)?;
        *self.waiter.lock().unwrap_or_else(|e| e.into_inner()) = Some(st.waiter());
        self.with(py, move |g| { g.0.store = Some(st); });
        Ok(())
    }
    /// The drivers without a database (fs, proc, jsonl): reads and effects in `dir` (the seat's folder).
    fn set_io(&self, py: Python<'_>, dir: &str) {
        let st = crate::store::Store::io_only(dir);
        *self.waiter.lock().unwrap_or_else(|e| e.into_inner()) = Some(st.waiter());
        self.with(py, move |g| { g.0.store = Some(st); });
    }
    /// Wait (without the engine's lock, without the GIL) until a getter's query finishes, or `timeout_s`; True when
    /// one did. Then `settle` lets the getters waiting for it recompute.
    #[pyo3(signature = (timeout_s=None))]
    fn sql_wait(&self, py: Python<'_>, timeout_s: Option<f64>) -> bool {
        let w = self.waiter.lock().unwrap_or_else(|e| e.into_inner()).as_ref().map(|w| crate::store::Waiter::clone(w));
        match w { None => false, Some(w) => py.detach(move || w.wait(timeout_s.map(std::time::Duration::from_secs_f64))) }
    }
    /// How many getters' queries are running.
    fn sql_pending(&self) -> usize {
        self.waiter.lock().unwrap_or_else(|e| e.into_inner()).as_ref().map(|w| w.pending()).unwrap_or(0)
    }
    /// Drop the kept answers of the getters of `eid` (a banner build starts fresh: the same words recall again).
    fn sql_forget(&self, eid: &str) {
        if let Some(w) = self.waiter.lock().unwrap_or_else(|e| e.into_inner()).as_ref() { w.forget(&format!("{}\u{0}", eid)); }
    }
    /// Stop every running query (shutdown).
    fn sql_interrupt(&self) {
        if let Some(w) = self.waiter.lock().unwrap_or_else(|e| e.into_inner()).as_ref() { w.interrupt_all(); }
    }
    fn load(&self, py: Python<'_>, eid: String, cls: String, props_json: &str) -> PyResult<()> {
        let p = obj(props_json)?;
        self.with(py, move |g| {
            let (core, f, _) = g;
            let old = core.docs.get(&eid).map(|e| e.props.clone());
            core.load(eid.clone(), cls, p, None, 0.0);
            if let (Some(old), Some(new)) = (old, core.docs.get(&eid).map(|e| e.props.clone())) { f.reloaded(&eid, &old, &new); }
        });
        Ok(())
    }
    /// An entity left the store (#1054): it leaves the engine; what read or referenced it recalculates.
    fn remove(&self, py: Python<'_>, eid: String, now: f64) {
        let host = self.host(py);
        self.with(py, move |g| {
            let (core, f, seq) = g;
            f.remove(core, &host, &eid, now, seq);
        });
    }
    /// A write: the Core hears it, the fields react (setters, getters, effects). Returns nothing; read back with `get`.
    #[pyo3(signature = (eid, prop, new_json, now, writer="py".to_string()))]
    fn write(&self, py: Python<'_>, eid: String, prop: String, new_json: &str, now: f64, writer: String) -> PyResult<()> {
        let new: J = serde_json::from_str(new_json).map_err(bad)?;
        let host = self.host(py);
        self.with(py, move |g| {
            let (core, f, seq) = g;
            let old = core.docs.get(&eid).and_then(|e| e.props.get(&prop).cloned()).unwrap_or(J::Null);
            *seq += 1;
            let w = Write { eid, prop, new, old, writer, chain: serde_json::json!({}), seq: *seq };
            core.hear(w.clone(), None, now);
            let mut s = *seq;
            f.react(core, &host, w, now, &mut s);
            *seq = s;
        });
        Ok(())
    }
    fn get(&self, py: Python<'_>, eid: String) -> String {
        self.with(py, move |g| g.0.docs.get(&eid).map(|e| J::Object(e.props.clone()).to_string()).unwrap_or_else(|| "null".into()))
    }
    fn writes(&self, py: Python<'_>) -> u64 { self.with(py, |g| g.1.writes) }
    /// Drain what the field did since the last drain: {"writes": [{eid, prop, new, writer}], "calls": [...]}.
    fn drain(&self, py: Python<'_>) -> String {
        self.with(py, |g| {
            let f = &mut g.1;
            serde_json::json!({"writes": std::mem::take(&mut f.out), "calls": std::mem::take(&mut f.calls)}).to_string()
        })
    }
    /// The clock input: waits whose check is due fire (Wait.due getter, then its setter).
    fn tick(&self, py: Python<'_>, now: f64) {
        let host = self.host(py);
        self.with(py, move |g| { let (core, f, seq) = g; let mut s = *seq; f.tick(core, &host, now, &mut s); *seq = s; });
    }
    /// Start-up: compute every getter once and keep the values, running no setter (a rebuild runs no effects).
    fn rebuild(&self, py: Python<'_>, now: f64) {
        self.with(py, move |g| { let (core, f, seq) = g; let mut s = *seq; f.rebuild(core, now, &mut s); *seq = s; });
    }
    /// Run a round with no outside write: a newly loaded entity's getters compute, and what references it recalculates.
    fn settle(&self, py: Python<'_>, now: f64) {
        let host = self.host(py);
        self.with(py, move |g| { let (core, f, seq) = g; let mut s = *seq; f.settle(core, &host, now, &mut s); *seq = s; });
    }
    /// Load or replace a named `.c1f` pack (a parse error raises and changes nothing). New getters compute at the
    /// next `rebuild`, which runs no setter.
    fn set_pack(&self, py: Python<'_>, name: String, src: String) -> PyResult<()> {
        self.with(py, move |g| g.1.set_pack(&name, &src)).map_err(bad)
    }
    /// Packs that aren't active, and why: {name: reason}.
    fn pack_errors(&self, py: Python<'_>) -> String {
        self.with(py, |g| serde_json::to_string(&g.1.pack_errors).unwrap_or_else(|_| "{}".into()))
    }
    fn remove_pack(&self, py: Python<'_>, name: String) -> bool { self.with(py, move |g| g.1.remove_pack(&name)) }
    /// The classes the loaded setters and getters are declared on (what the host mirrors).
    fn classes(&self, py: Python<'_>) -> Vec<String> { self.with(py, |g| g.1.classes()) }
    /// Is this entity in the field's mirror?
    fn has(&self, py: Python<'_>, eid: String) -> bool { self.with(py, move |g| g.0.docs.contains_key(&eid)) }
    fn calls(&self, py: Python<'_>) -> String { self.with(py, |g| J::Array(g.1.calls.clone()).to_string()) }
}

#[pymodule]
fn membrane_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Core>()?;
    m.add_class::<VecStore>()?;
    m.add_class::<MappedVecs>()?;
    m.add_class::<Fields>()?;
    m.add_function(wrap_pyfunction!(evaluate, m)?)?;
    m.add_function(wrap_pyfunction!(fingerprints, m)?)?;
    Ok(())
}
