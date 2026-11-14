//! membrane_vm: the cliffhanger VM, native (#971). Python sees one class, `Core`; values cross the
//! boundary as JSON text (simple first; typed conversion later, where measurements say it matters).

pub mod adapters;
pub mod learn;
pub mod recall;
pub mod syntax;
pub mod text;
#[cfg(unix)]
pub mod mapped;
pub mod topk;
pub mod packs;
pub mod expr;
pub mod infer;
pub mod clock;
pub mod walk;
pub mod fields;
pub mod getters;
pub mod translate;
pub mod vm;
pub mod plan;
pub mod stats;
#[cfg(feature = "alloc-stats")]
pub mod alloc_stats;

#[cfg(feature = "python")]
mod py;
#[cfg(feature = "store")]
pub mod store;
#[cfg(feature = "python")]
pub mod db;
#[cfg(feature = "store")]
pub mod sqlexec;
#[cfg(feature = "store")]
pub mod drivers;
#[cfg(feature = "wasm")]
pub mod web;
#[cfg(feature = "store")]
pub mod apps;
#[cfg(feature = "store")]
pub mod host;
#[cfg(feature = "store")]
pub mod cli;
#[cfg(feature = "store")]
pub mod http;
pub mod jwt;
#[cfg(unix)]
pub mod owner;
#[cfg(feature = "store")]
pub mod fetch;
#[cfg(feature = "store")]
pub mod spec;


/// Parse and evaluate one expression against plain values (`env_json`: a JSON object), no entities. The result as
/// JSON. For tests and tools; the Python binding's `evaluate` is the same.
pub fn evaluate_json(src: &str, env_json: &str) -> Result<String, String> {
    struct NoWorld;
    impl expr::World for NoWorld { fn attr(&self, _: &str, _: &str) -> expr::V { expr::V::None } }
    let env: serde_json::Map<String, serde_json::Value> = match serde_json::from_str(env_json).map_err(|e| e.to_string())? {
        serde_json::Value::Object(m) => m, _ => return Err("env: a JSON object".into()) };
    let env = env.iter().map(|(k, v)| (k.clone(), expr::from_json(v))).collect();
    let node = expr::parse(src)?;
    let v = expr::eval(&node, &env, &NoWorld)?;
    Ok(expr::to_json(&v)?.to_string())
}
#[cfg(feature = "store")]
pub mod sqldrv;
