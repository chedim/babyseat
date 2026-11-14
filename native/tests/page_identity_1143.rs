//! The page says what it is (#1143, operator 2026-11-14): its title and header read "babyseat" (not "Membrane seat") and it carries its icon.
use std::path::PathBuf;

fn ui() -> PathBuf { PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/apps/barest.c1f")) }

#[test]
fn the_page_title_and_the_header_say_babyseat() {
    let html = std::fs::read_to_string(ui().join("ui/index.html")).unwrap();
    assert!(html.contains("<title>babyseat</title>"), "the title of the page: {html}");
    let shell = std::fs::read_to_string(ui().join("shell_view.c1f")).unwrap();
    assert!(shell.contains("('DomNode:shell:title').text = 'babyseat'"), "the header text");
    assert!(!html.contains("Membrane seat") && !shell.contains("Membrane seat"));
}

#[test]
fn the_page_links_its_icon_and_the_icon_file_exists() {
    let html = std::fs::read_to_string(ui().join("ui/index.html")).unwrap();
    let link = html.lines().find(|l| l.contains("rel=\"icon\"")).expect("a <link rel=\"icon\"> in index.html");
    assert!(link.contains("image/svg+xml") && link.contains("favicon.svg"), "{link}");
    assert!(ui().join("ui/favicon.svg").is_file());
}
