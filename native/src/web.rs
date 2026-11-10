//! The browser binding: the same field engine (getters, setters, `.c1f` packs, imports) running in a page,
//! compiled to WebAssembly (`wasm-pack build --features wasm --no-default-features`). Values cross as JSON text,
//! like the Python binding. The page's host (membrane/ui/fieldvm/host.js) mounts DomNode entities as the DOM.
use serde_json::{Map, Value as J};
use wasm_bindgen::prelude::*;

use crate::{fields, vm};

/// The page has no store to look things up in: the engine works from its own mirror.
struct PageHost;
impl vm::Host for PageHost {
    fn resolve(&self, _: &str) -> Option<String> { None }
    fn resolve_exact(&self, _: &str) -> Option<String> { None }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn err<E: std::fmt::Display>(e: E) -> JsValue { JsValue::from_str(&e.to_string()) }

fn obj(s: &str) -> Result<Map<String, J>, JsValue> {
    match serde_json::from_str::<J>(s).map_err(err)? { J::Object(m) => Ok(m), _ => Err(err("expected a JSON object")) }
}

#[wasm_bindgen]
pub struct Field { core: vm::Core, f: fields::Fields, seq: i64 }

#[wasm_bindgen]
impl Field {
    /// A field with the built-in goal graph pack (`PACK goalgraph 1`), so page packs can import it.
    #[wasm_bindgen(constructor)]
    pub fn new() -> Field { Field { core: vm::Core::new(), f: fields::Fields::goalgraph(), seq: 0 } }

    /// An entity into the mirror (a reload of a known one counts its changed fields as written).
    pub fn load(&mut self, eid: String, cls: String, props_json: &str) -> Result<(), JsValue> {
        let p = obj(props_json)?;
        let old = self.core.docs.get(&eid).map(|e| e.props.clone());
        self.core.load(eid.clone(), cls, p, None, 0.0);
        if let (Some(old), Some(new)) = (old, self.core.docs.get(&eid).map(|e| e.props.clone())) { self.f.reloaded(&eid, &old, &new); }
        Ok(())
    }

    /// An outside write (a click, a value from the seat): the field reacts; read what it did with `drain`.
    pub fn write(&mut self, eid: String, prop: String, value_json: &str, now: f64) -> Result<(), JsValue> {
        let new: J = serde_json::from_str(value_json).map_err(err)?;
        let old = self.core.docs.get(&eid).and_then(|e| e.props.get(&prop).cloned()).unwrap_or(J::Null);
        self.seq += 1;
        let w = vm::Write { eid, prop, new, old, writer: "page".into(), chain: serde_json::json!({}), seq: self.seq };
        self.core.hear(w.clone(), None, now);
        let mut s = self.seq;
        self.f.react(&mut self.core, &PageHost, w, now, &mut s);
        self.seq = s;
        Ok(())
    }

    /// The entity's fields as JSON (`null` when it doesn't exist).
    pub fn get(&self, eid: &str) -> String {
        self.core.docs.get(eid).map(|e| J::Object(e.props.clone()).to_string()).unwrap_or_else(|| "null".into())
    }

    /// What the field did since the last drain: {"writes": [{eid, prop, new, writer} | {eid, born}], "calls": [...]}.
    pub fn drain(&mut self) -> String {
        serde_json::json!({"writes": std::mem::take(&mut self.f.out), "calls": std::mem::take(&mut self.f.calls)}).to_string()
    }

    /// Load or replace a named `.c1f` pack. A parse error or a missing import is returned as the error text.
    pub fn set_pack(&mut self, name: &str, src: &str) -> Result<(), JsValue> { self.f.set_pack(name, src).map_err(err) }

    /// Packs that aren't active, and why: {name: reason}.
    pub fn pack_errors(&self) -> String { serde_json::to_string(&self.f.pack_errors).unwrap_or_else(|_| "{}".into()) }

    /// The classes the loaded packs declare on (JSON list).
    pub fn classes(&self) -> String { serde_json::to_string(&self.f.classes()).unwrap_or_else(|_| "[]".into()) }

    /// Compute every getter once, keeping the values; no setter runs.
    pub fn rebuild(&mut self, now: f64) {
        let mut s = self.seq;
        self.f.rebuild(&mut self.core, now, &mut s);
        self.seq = s;
    }

    /// A round with no outside write: new entities register, dirty getters settle.
    pub fn settle(&mut self, now: f64) {
        let mut s = self.seq;
        self.f.settle(&mut self.core, &PageHost, now, &mut s);
        self.seq = s;
    }

    /// The clock input.
    pub fn tick(&mut self, now: f64) {
        let mut s = self.seq;
        self.f.tick(&mut self.core, &PageHost, now, &mut s);
        self.seq = s;
    }
}

impl Default for Field { fn default() -> Self { Self::new() } }
