//! No test may reach the real Ollama (#1143): `serve` (and so `scripts/seat`) defaults `seat_backend` to http://127.0.0.1:11434 (membrane/packs/launcher.c1f), and the chat asks that backend for the
//! vector of every message (membrane/packs/embed.c1f). A test file that launches a seat through `serve` or `scripts/seat` AND posts chat messages must therefore name a dead backend itself
//! (`--fact seat_backend=http://127.0.0.1:9`). This guard reads the test sources, so a future test cannot forget it.
use std::path::Path;

#[test]
fn a_test_that_launches_a_seat_through_serve_and_posts_messages_names_a_dead_backend() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let mut files = vec![];
    for dir in ["native/tests", "tests/js"] {
        for e in std::fs::read_dir(root.join(dir)).unwrap().flatten() {
            let p = e.path();
            if matches!(p.extension().and_then(|x| x.to_str()), Some("rs") | Some("mjs")) { files.push(p); }
        }
    }
    let mut bad = vec![];
    for p in files {
        let name = p.file_name().unwrap().to_string_lossy().to_string();
        if name == "no_real_ollama_1143.rs" { continue; }
        let t = std::fs::read_to_string(&p).unwrap();
        let launches = t.contains("scripts/seat") || t.contains("\"serve\"");
        let posts = t.contains("chat/message") || t.contains("/session/");
        // seat_process.rs reads scripts/seat only to check what it passes to a stand-in binary: nothing runs there
        if launches && posts && name != "seat_process.rs" && !t.contains("seat_backend=") { bad.push(name); }
    }
    assert!(bad.is_empty(), "these tests launch `serve` / scripts/seat and post chat messages without a `seat_backend=` fact (the default is the real Ollama): {bad:?}");
}
