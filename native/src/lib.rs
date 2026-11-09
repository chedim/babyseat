//! membrane_vm: the cliffhanger VM, native (#971). Python sees one class, `Core`; values cross the
//! boundary as JSON text (simple first; typed conversion later, where measurements say it matters).

pub mod adapters;
pub mod learn;
pub mod banner;
pub mod recall;
pub mod syntax;
pub mod topk;
pub mod packs;
pub mod expr;
pub mod fields;
pub mod getters;
pub mod translate;
pub mod vm;

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


/// Fields (#994): the field engine over its own Core, reachable from Python (tests, a test seat).
#[pyclass]
struct Fields { c: std::sync::Mutex<(vm::Core, fields::Fields, i64)>, host: Py<PyAny> }

#[pymethods]
impl Fields {
    #[new]
    fn new(host: Py<PyAny>) -> Self {
        Fields { c: std::sync::Mutex::new((vm::Core::new(), fields::Fields::goalgraph(), 0)), host }
    }
    fn load(&self, eid: String, cls: String, props_json: &str) -> PyResult<()> {
        let p = obj(props_json)?;
        let mut g = self.c.lock().unwrap_or_else(|e| e.into_inner());
        g.0.load(eid, cls, p, None, 0.0);
        Ok(())
    }
    /// A write: the Core hears it, the fields react (setters, getters, effects). Returns nothing; read back with `get`.
    fn write(&self, py: Python<'_>, eid: String, prop: String, new_json: &str, now: f64) -> PyResult<()> {
        let new: J = serde_json::from_str(new_json).map_err(bad)?;
        let h = self.host.bind(py).clone();
        let host = PyHost { py, obj: &h };
        let mut g = self.c.lock().unwrap_or_else(|e| e.into_inner());
        let (core, f, seq) = &mut *g;
        let old = core.docs.get(&eid).and_then(|e| e.props.get(&prop).cloned()).unwrap_or(J::Null);
        *seq += 1;
        let w = Write { eid, prop, new, old, writer: "py".into(), chain: serde_json::json!({}), seq: *seq };
        core.hear(w.clone(), None, now);
        let mut s = *seq;
        f.react(core, &host, w, now, &mut s);
        *seq = s;
        Ok(())
    }
    fn get(&self, eid: &str) -> String {
        let g = self.c.lock().unwrap_or_else(|e| e.into_inner());
        g.0.docs.get(eid).map(|e| J::Object(e.props.clone()).to_string()).unwrap_or_else(|| "null".into())
    }
    fn writes(&self) -> u64 { self.c.lock().unwrap_or_else(|e| e.into_inner()).1.writes }
    fn calls(&self) -> String {
        J::Array(self.c.lock().unwrap_or_else(|e| e.into_inner()).1.calls.clone()).to_string()
    }
}

#[pymodule]
fn membrane_vm(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Core>()?;
    m.add_class::<Fields>()?;
    m.add_function(wrap_pyfunction!(evaluate, m)?)?;
    Ok(())
}
