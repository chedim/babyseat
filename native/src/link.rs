//! The graph link's engine side (docs/graph-link.md, "Slice 2: engine interface"): the subscription table and the replica notes live in the `graphlink-core` crate
//! (https://github.com/chedim/graphlink, pinned in Cargo.toml); the engine hooks that use them are in `fields.rs` and `vm.rs`. The engine never talks to a network.
pub use graphlink_core::*;
