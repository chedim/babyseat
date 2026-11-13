//! SemText (#1143, docs/semtext-and-legs.md): the class whose one stored string is `text` and whose layers are getters over the
//! tokenizer; its span entities (virtual, attached by a write), the form classes of a word span, the mentions (Word entities and
//! their edges), the message body and the recall by edges. Every test runs on the SHIPPED packs of the barest seat (`barest.packs`).
use membrane_vm::host::{Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// the barest seat's packs and app, no drivers (no effects), as the help harness loads them
fn seat() -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.rebuild();
    h
}

/// a scratch pack on top of the seat: `Box.go` makes `Box:<n>:body = SemText(text: <this.t>)` (what a pack of the model does)
const MAKER: &str = "PACK semmaker 1\nIMPORT semtext 1\nBox.go on-setter make:\n  ALWAYS:\n    Box:a:body = SemText(text: this.t)\n";
fn with_body(text: &str) -> Host {
    let h = seat();
    h.load_pack("semmaker", MAKER).unwrap();
    h.rebuild();
    h.born("Box:a", "Box", props(json!({"t": text})), "person");
    h.pump();
    h.write("Box:a", "go", json!(true), "person");
    h.pump();
    h
}

const B: &str = "Box:a:body";

#[test]
fn a_semtext_made_by_a_write_has_its_text_and_its_layers_as_getters() {
    let h = with_body("Open ~/a.txt now. Then my_fn runs at https://x.io/p.");
    assert_eq!(h.get(B, "text"), json!("Open ~/a.txt now. Then my_fn runs at https://x.io/p."));
    let texts = |p: &str| -> Vec<String> { h.get(B, p).as_array().unwrap().iter().map(|s| s["text"].as_str().unwrap().to_string()).collect() };
    assert_eq!(texts("sentences"), ["Open ~/a.txt now.", "Then my_fn runs at https://x.io/p."]);
    assert_eq!(texts("words")[..3], ["Open", "a.txt", "now"]);
    assert_eq!(texts("identifiers"), ["my_fn"]);
    assert_eq!(texts("paths"), ["~/a.txt"]);
    assert_eq!(texts("urls"), ["https://x.io/p"]);
    let h = with_body("Costs 3.5 or 12 units");
    assert_eq!(h.get(B, "numbers").as_array().unwrap().iter().map(|s| s["text"].as_str().unwrap()).collect::<Vec<_>>(), ["3.5", "12"]);
}

#[test]
fn a_change_of_the_text_recomputes_every_layer() {
    let h = with_body("One two.");
    assert_eq!(h.get(B, "words").as_array().unwrap().len(), 2);
    h.write(B, "text", json!("One two three. Four."), "person");
    h.pump();
    assert_eq!(h.get(B, "words").as_array().unwrap().len(), 4);
    assert_eq!(h.get(B, "sentences").as_array().unwrap().len(), 2);
}

// ---- item 2: the span entities (virtual, attached by a write, held while something holds them) ------------------------------------------
fn live(h: &Host, eid: &str) -> bool { h.snapshot().contains_key(eid) }
const T: &str = "Open my_fn now. Then stop.";
fn span(layer: &str, start: usize) -> String { format!("Span:of:Box:a:body:{}:{start}", &layer[..1]) }

#[test]
fn a_write_of_attach_makes_the_virtual_span_entity_with_its_offsets_and_a_text_cut_from_the_parent() {
    let h = with_body(T);
    h.write(B, "attach", json!({"layer": "word", "start": 5}), "person");
    h.pump();
    let s = span("word", 5);
    assert!(live(&h, &s), "no {s}");
    assert_eq!(h.get(&s, "layer"), json!("word"));
    assert_eq!(h.get(&s, "start"), json!(5));
    assert_eq!(h.get(&s, "end"), json!(10));
    assert_eq!(h.get(&s, "text"), json!("my_fn"));
    assert_eq!(h.get(&s, "of"), json!({"$ref": B}));
    // the text is a getter over the parent's (no copy): the parent's text changes, the cut follows
    h.write(B, "text", json!("Open my_fn now. Then stop. "), "person");
    h.pump();
    assert_eq!(h.get(&s, "text"), json!("my_fn"));
    h.write(B, "text", json!("Open MY_FN now. Then stop."), "person");
    h.pump();
    assert_eq!(h.get(&s, "text"), json!("MY_FN"));
    h.write(B, "text", json!(T), "person");
    h.pump();
    // a SENTENCE and an IDENTIFIER are spans too, under their own letters
    h.write(B, "attach", json!({"layer": "sentence", "start": 16}), "person");
    h.pump();
    assert_eq!(h.get(&span("sentence", 16), "text"), json!("Then stop."));
}

#[test]
fn a_span_is_virtual_never_a_stored_row() {
    let h = with_body(T);
    h.write(B, "attach", json!({"layer": "word", "start": 0}), "person");
    h.pump();
    assert_eq!(h.storage_of("Span"), membrane_vm::fields::Storage::Virtual);
}

#[test]
fn a_span_nothing_holds_is_collected_and_what_holds_one_keeps_it() {
    let h = with_body(T);
    h.write(B, "attach", json!({"layer": "word", "start": 0}), "person");
    h.pump();
    assert!(live(&h, &span("word", 0)), "the attach receipt holds the span it made");
    // the next attach replaces the receipt: the first span is held by nothing now and is collected; the second is held
    h.write(B, "attach", json!({"layer": "word", "start": 5}), "person");
    h.pump();
    assert!(!live(&h, &span("word", 0)), "an unheld span is collected");
    assert!(live(&h, &span("word", 5)));
    // a live entity that holds it by a reference keeps it past the next attach (a model's pack: `Reader.keep = Span:...`)
    h.born("Reader:r", "Reader", Default::default(), "person");
    h.write("Reader:r", "keep", json!({"$ref": span("word", 5)}), "person");
    h.pump();
    h.write(B, "attach", json!({"layer": "word", "start": 11}), "person");
    h.pump();
    assert!(live(&h, &span("word", 5)), "held by Reader:r");
    h.write("Reader:r", "keep", J::Null, "person");
    h.pump();
    assert!(!live(&h, &span("word", 5)), "released, collected");
}

#[test]
fn attaching_a_span_the_text_has_not_makes_nothing() {
    let h = with_body(T);
    h.write(B, "attach", json!({"layer": "word", "start": 3}), "person");   // no word starts at 3
    h.pump();
    assert!(!live(&h, &span("word", 3)));
    h.write(B, "attach", json!({"layer": "paragraph", "start": 0}), "person");
    h.pump();
    assert!(!live(&h, &span("paragraph", 0)));
}
