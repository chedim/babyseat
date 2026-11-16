//! The shipped model-facing text is domain-neutral (#1120, operator 2026-11-11; the Python tests that pinned it went with the Python seat, #1143).
//! babyseat is a general tool (a boat, a trip, a home, research); code is one domain among them. The texts the model reads on its own seat:
//! the Help topics, the doctrine the chat turn sends, and the default banner. They name no product and no software-only example. The
//! rules are the ones `tests/test_neutral_doctrine_1120.py` enforced (the CODE_ONLY phrases), plus the product names.
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }

/// the phrases of a software project's day (a review, a build): examples must work for a boat or a trip too
const CODE_ONLY: [&str; 10] = ["issue link", "issue to build", "PR head", "its CI", "CI going green", "CI takes", "pull request", "pytest", "unit test", "git commit"];
/// the seat's own brand and the coding clients: the model's text is about its field and its work, not about the product it runs in
/// (a home system such as Home Assistant is a DOMAIN the seat may serve, so Help:ha and the topic list name it)
const PRODUCTS: [&str; 3] = ["babyseat", "opencode", "Claude Code"];

/// (name, text) of every shipped model-facing text
fn texts() -> Vec<(String, String)> {
    let r = repo();
    let mut out = vec![];
    let mut helps: Vec<_> = std::fs::read_dir(r.join("membrane/apps/barest.c1f/help")).unwrap().map(|e| e.unwrap().path()).filter(|p| p.extension().map_or(false, |x| x == "md")).collect();
    helps.sort();
    for p in helps { out.push((format!("Help:{}", p.file_stem().unwrap().to_string_lossy()), std::fs::read_to_string(&p).unwrap())); }
    // the doctrine: the `note` getter of the chat turn (its sentences are a quoted string on the line under `WHEN this.over`)
    let t = std::fs::read_to_string(r.join("membrane/packs/chat_turn.c1f")).unwrap();
    let at = t.lines().position(|l| l.starts_with("ChatMessage.note getter:")).expect("ChatMessage.note getter");
    out.push(("chat_turn doctrine".into(), t.lines().nth(at + 2).unwrap().to_string()));
    // the default banner: the whole pack (its labels and its wording)
    out.push(("default banner".into(), std::fs::read_to_string(r.join("membrane/apps/barest.c1f/default_banner.c1f")).unwrap()));
    out
}

#[test]
fn the_shipped_model_facing_text_names_no_software_only_example() {
    let mut bad = vec![];
    for (name, text) in texts() { for w in CODE_ONLY { if text.contains(w) { bad.push(format!("{name}: {w:?}")); } } }
    assert!(bad.is_empty(), "\n{}\n", bad.join("\n"));
}

#[test]
fn the_shipped_model_facing_text_names_no_product() {
    let mut bad = vec![];
    for (name, text) in texts() { for w in PRODUCTS { if text.contains(w) { bad.push(format!("{name}: {w:?}")); } } }
    assert!(bad.is_empty(), "\n{}\n", bad.join("\n"));
}

#[test]
fn the_texts_are_found() {
    let t = texts();
    assert!(t.len() >= 12, "the ten help topics, the doctrine and the banner: {}", t.len());
    assert!(t.iter().any(|(n, x)| n == "chat_turn doctrine" && x.contains("banner")), "the doctrine line is the sentences");
}
