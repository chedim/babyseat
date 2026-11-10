//! membrane_vm: the cliffhanger VM, native (#971). Python sees one class, `Core`; values cross the
//! boundary as JSON text (simple first; typed conversion later, where measurements say it matters).

pub mod adapters;
pub mod learn;
pub mod banner;
pub mod recall;
pub mod syntax;
#[cfg(unix)]
pub mod mapped;
pub mod topk;
pub mod packs;
pub mod expr;
pub mod fields;
pub mod getters;
pub mod translate;
pub mod vm;

#[cfg(feature = "python")]
mod py;
#[cfg(feature = "wasm")]
pub mod web;
