//! `graphlink-bridge`: TCP outward, shared memory inward (github.com/chedim/graphlink, docs/graph-link.md section 18). The program is the graphlink-bridge crate's; the seat ships the same binary so a seat and its bridge are one install.
fn main() { graphlink_bridge::run() }
