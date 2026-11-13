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
    membrane_vm::expr::set_eval_time_ms(Some(10_000));   // the 10 ms self-time allowance of a debug build under a loaded machine would kill a getter at random: pin it
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

// ---- item 3: the form classes of a span (mentions.c1f) ----------------------------------------------------------------------------------
const FORMS: &str = "Dogs bark. See Rex, NASA and 3 cats at my_fn in ~/a.txt near boxes, cities, a bus and a class.";
fn at(t: &str, w: &str) -> usize { let b = t.find(w).unwrap_or_else(|| panic!("{w} not in {t}")); t[..b].chars().count() }
/// attach the span (layer, the first occurrence of `w`) of a body holding FORMS and read its class list
fn attached(h: &Host, layer: &str, w: &str) -> String {
    h.write(B, "attach", json!({"layer": layer, "start": at(FORMS, w)}), "person");
    h.pump();
    span(layer, at(FORMS, w))
}
/// the class list; a span with no form class has no `parents` written: its list is [its eid class]
fn classes(h: &Host, s: &str) -> Vec<String> { h.get(s, "parents").as_array().cloned().unwrap_or(vec![json!("cls:span")]).iter().map(|c| c.as_str().unwrap().to_string()).collect() }

#[test]
fn an_attached_span_gets_its_form_classes_by_rule_and_each_class_has_its_getters() {
    let h = with_body(FORMS);
    let s = attached(&h, "word", "Dogs");
    assert_eq!(classes(&h, &s), ["cls:semplural", "cls:span"], "sentence-initial: plural, not capital");
    assert_eq!(h.get(&s, "lemma"), json!("dog"));
    let s = attached(&h, "word", "Rex");
    assert_eq!(classes(&h, &s), ["cls:semcapital", "cls:span"]);
    assert_eq!(h.get(&s, "lemma"), json!("rex"), "the default lemma is the lowercased text");
    let s = attached(&h, "word", "NASA");
    assert_eq!(classes(&h, &s), ["cls:semallcaps", "cls:span"]);
    let s = attached(&h, "word", "3");
    assert_eq!(classes(&h, &s), ["cls:semnumber", "cls:span"]);
    let s = attached(&h, "word", "cats");
    assert_eq!(classes(&h, &s), ["cls:semplural", "cls:span"]);
    assert_eq!(h.get(&s, "lemma"), json!("cat"));
    assert_eq!(h.get(&attached(&h, "word", "boxes"), "lemma"), json!("box"));
    assert_eq!(h.get(&attached(&h, "word", "cities"), "lemma"), json!("city"));
    for w in ["bus", "class", "in", "and"] {
        let s = attached(&h, "word", w);
        assert_eq!(classes(&h, &s), ["cls:span"], "{w} is no plural");
        assert_eq!(h.get(&s, "lemma"), json!(w));
    }
    let s = attached(&h, "identifier", "my_fn");
    assert_eq!(classes(&h, &s), ["cls:semidentifier", "cls:span"]);
    let s = attached(&h, "path", "~/a.txt");
    assert_eq!(classes(&h, &s), ["cls:sempath", "cls:span"]);
    assert_eq!(h.get(&s, "lemma"), json!("~/a.txt"));
}

#[test]
fn a_span_with_two_classes_answers_from_the_nearest_and_writing_parents_reorders_them() {
    let h = with_body("See Dogs now.");
    h.write(B, "attach", json!({"layer": "word", "start": 4}), "person");
    h.pump();
    let s = span("word", 4);
    assert_eq!(classes(&h, &s), ["cls:semplural", "cls:semcapital", "cls:span"], "one word, two form classes");
    assert_eq!(h.get(&s, "form"), json!("plural"));
    assert_eq!(h.get(&s, "lemma"), json!("dog"));
    h.write(&s, "parents", json!(["cls:semcapital", "cls:semplural", "cls:span"]), "person");
    h.pump();
    assert_eq!(h.get(&s, "form"), json!("capital"), "the nearer class answers");
    assert_eq!(h.get(&s, "lemma"), json!("dog"), "SemCapital has no lemma: the list is searched in order and SemPlural, next, answers");
}

#[test]
fn a_layer_the_model_wrote_overrides_a_form_class_getter() {
    let h = with_body(FORMS);
    h.load_pack("mine", "PACK mine 1\nIMPORT mentions 1\nSemPlural.lemma getter: 'PLURAL OF ' + lower(this.text)\n").unwrap();
    h.rebuild();
    h.pump();
    let s = attached(&h, "word", "cats");
    assert_eq!(h.get(&s, "lemma"), json!("PLURAL OF cats"));
    assert_eq!(h.get(&attached(&h, "word", "Rex"), "lemma"), json!("rex"), "the other classes keep theirs");
}

// ---- item 4: the mentions (mentions.c1f): words, identifiers and paths become entities, with edges ---------------------------------------
fn entities(h: &Host, prefix: &str) -> Vec<String> { h.snapshot().keys().filter(|k| k.starts_with(prefix)).cloned().collect() }
fn mention(h: &Host, body: &str, token: J) { h.write(body, "mention", token, "person"); h.pump(); }
const M: &str = "See Dogs now. The dogs and cities use my_fn in ~/a.txt.";

#[test]
fn mentioning_a_body_makes_a_word_entity_per_lemma_at_its_first_mention() {
    let h = with_body(M);
    assert!(entities(&h, "Word:").is_empty(), "nothing is mentioned until the write");
    mention(&h, B, json!(true));
    let words: Vec<String> = entities(&h, "Word:lemma:");
    assert_eq!(words, ["Word:lemma:and", "Word:lemma:city", "Word:lemma:dog", "Word:lemma:in", "Word:lemma:now", "Word:lemma:see", "Word:lemma:the", "Word:lemma:use"], "K = 1: every word, each once, under its lemma");
    assert_eq!(h.get("Word:lemma:dog", "lemma"), json!("dog"));
}

#[test]
fn a_word_lists_the_text_and_the_sentences_that_mention_it() {
    let h = with_body(M);
    mention(&h, B, json!(true));
    assert_eq!(h.get("Word:lemma:dog", "mentioned_in"), json!([{"text": B, "sentence": 0}, {"text": B, "sentence": 1}]));
    assert_eq!(h.get("Word:lemma:city", "mentioned_in"), json!([{"text": B, "sentence": 1}]));
    assert_eq!(h.get("Word:lemma:see", "mentioned_in"), json!([{"text": B, "sentence": 0}]));
    // no text is copied: an entry names the text and one sentence number
    assert!(h.get("Word:lemma:dog", "mentioned_in").to_string().len() < 100);
}

#[test]
fn identifiers_and_paths_are_entities_too_by_their_exact_text() {
    let h = with_body(M);
    mention(&h, B, json!(true));
    assert_eq!(h.get("Identifier:name:my_fn", "mentioned_in"), json!([{"text": B, "sentence": 1}]));
    assert_eq!(h.get("Path:path:~/a.txt", "mentioned_in"), json!([{"text": B, "sentence": 1}]));
    assert!(entities(&h, "File:").is_empty(), "a mention makes no File (a File reads the disk)");
}

#[test]
fn a_span_points_at_the_entity_it_mentions() {
    let h = with_body(M);
    h.write(B, "attach", json!({"layer": "word", "start": at(M, "Dogs")}), "person");
    h.pump();
    assert_eq!(h.get(&span("word", at(M, "Dogs")), "target"), json!({"$ref": "Word:lemma:dog"}));
    h.write(B, "attach", json!({"layer": "identifier", "start": at(M, "my_fn")}), "person");
    h.pump();
    assert_eq!(h.get(&span("identifier", at(M, "my_fn")), "target"), json!({"$ref": "Identifier:name:my_fn"}));
}

#[test]
fn mentioning_again_adds_no_duplicates_and_a_second_text_adds_its_own_entry() {
    let h = with_body(M);
    mention(&h, B, json!(true));
    let before = entities(&h, "Word:").len() + entities(&h, "Identifier:").len() + entities(&h, "Path:").len();
    mention(&h, B, json!(2));
    assert_eq!(h.get("Word:lemma:dog", "mentioned_in"), json!([{"text": B, "sentence": 0}, {"text": B, "sentence": 1}]), "re-mentioning replaces the text's own entry");
    assert_eq!(entities(&h, "Word:").len() + entities(&h, "Identifier:").len() + entities(&h, "Path:").len(), before);
    // a second text that holds the word
    h.load_pack("semmaker2", "PACK semmaker2 1\nIMPORT semmaker 1\nIMPORT semtext 1\nBox.go2 on-setter make2:\n  ALWAYS:\n    Box:b:body = SemText(text: this.t)\n").unwrap();
    h.rebuild();
    h.born("Box:b", "Box", props(json!({"t": "A dog barks."})), "person");
    h.pump();
    h.write("Box:b", "go2", json!(true), "person");
    h.pump();
    mention(&h, "Box:b:body", json!(true));
    assert_eq!(h.get("Word:lemma:dog", "mentioned_in"), json!([{"text": B, "sentence": 0}, {"text": B, "sentence": 1}, {"text": "Box:b:body", "sentence": 0}]));
}

#[test]
fn the_work_of_one_mention_is_bounded_by_the_cap_and_the_spans_are_collected_after() {
    // 450 distinct words: only the first MENTION_CAP (400) are mentioned
    let words: Vec<String> = (0..450).map(|i| format!("{}{}{}", (b'a' + (i / 676) as u8) as char, (b'a' + ((i / 26) % 26) as u8) as char, (b'a' + (i % 26) as u8) as char)).collect();
    let h = with_body(&words.join(" "));
    mention(&h, B, json!(true));
    let n = entities(&h, "Word:lemma:").len();
    assert_eq!(n, 400, "the cap");
    assert!(h.snapshot().contains_key(&format!("Word:lemma:{}", words[399])));
    assert!(!h.snapshot().contains_key(&format!("Word:lemma:{}", words[400])));
    assert!(entities(&h, "Span:").is_empty(), "the spans were transient: nothing holds them after the mention");
}

// ---- item 6: recall by edges (SemText.related) ----------------------------------------------------------------------------------------
const DOCS: &str = "PACK semdocs 1\nIMPORT semtext 1\nDoc.go on-setter make:\n  ALWAYS:\n    Doc:body[this.k] = SemText(text: this.t)\n    Doc:body[this.k].mention = True\n";
fn docs(texts: &[(&str, &str)]) -> Host {
    let h = seat();
    h.load_pack("semdocs", DOCS).unwrap();
    h.rebuild();
    for (k, t) in texts {
        h.born(&format!("Doc:{k}"), "Doc", props(json!({"k": k, "t": t})), "person");
        h.pump();
        h.write(&format!("Doc:{k}"), "go", json!(true), "person");
        h.pump();
    }
    h
}
fn related(h: &Host, k: &str, n: usize) -> J {
    let b = format!("Doc:body:{k}");
    assert_eq!(h.get(&b, "related"), J::Null, "nothing is computed before a question");
    h.write(&b, "related_n", json!(n), "person");
    h.pump();
    h.get(&b, "related")
}

#[test]
fn two_texts_that_share_a_rare_word_relate_and_the_text_is_not_related_to_itself() {
    let h = docs(&[("a", "Quokkas eat grass. Rain falls."), ("b", "I saw a quokka. It slept."), ("c", "Nothing like it here.")]);
    let r = related(&h, "a", 5);
    let rows = r.as_array().unwrap();
    assert_eq!(rows[0], json!({"semtext": "Doc:body:b", "sentence_index": 0, "text": "I saw a quokka.", "shared": 1}), "{r}");
    assert!(rows.iter().all(|x| x["semtext"] != "Doc:body:a"), "never itself: {r}");
    assert_eq!(rows.iter().filter(|x| x["semtext"] == "Doc:body:c").count(), 0, "c shares no word with a");
}

#[test]
fn a_frequent_word_does_not_dominate_the_number_of_mentions_divides_it() {
    // `the` and `and` are in every text; `quokka` only in two. c shares TWO words with a (the, and), b shares ONE (quokka): by the plain count c wins, by the divisor b wins.
    let mut v: Vec<(String, String)> = vec![("a".into(), "The quokka and birds.".into()), ("b".into(), "My quokka sleeps.".into()), ("c".into(), "The cat and dog.".into())];
    for i in 0..6 { v.push((format!("f{i}"), format!("The word{i} and the other{i}."))); }
    let refs: Vec<(&str, &str)> = v.iter().map(|(k, t)| (k.as_str(), t.as_str())).collect();
    let h = docs(&refs);
    let r = related(&h, "a", 3);
    let rows = r.as_array().unwrap();
    assert_eq!(rows[0]["semtext"], "Doc:body:b", "the rare shared word ranks first: {r}");
    assert_eq!(rows[0]["shared"], 1);
    let c = rows.iter().find(|x| x["semtext"] == "Doc:body:c").unwrap_or_else(|| panic!("{r}"));
    assert!(c["shared"].as_i64().unwrap() >= 2, "c shares the frequent words: {r}");
}

#[test]
fn the_dial_is_the_answers_length() {
    let h = docs(&[("a", "Red apples."), ("b", "Red apples again."), ("c", "More red apples."), ("d", "Red.")]);
    assert_eq!(related(&h, "a", 2).as_array().unwrap().len(), 2);
    h.write("Doc:body:a", "related_n", json!(1), "person");
    h.pump();
    assert_eq!(h.get("Doc:body:a", "related").as_array().unwrap().len(), 1);
}

// ---- item 7: the help ------------------------------------------------------------------------------------------------------------------
#[test]
fn the_help_teaches_semtext_and_never_the_tokenizer_builtin() {
    let dir = repo().join("membrane/apps/barest.c1f/help");
    for e in std::fs::read_dir(&dir).unwrap().flatten() {
        let t = std::fs::read_to_string(e.path()).unwrap();
        assert!(!t.contains("spans("), "{:?} teaches the internal builtin", e.path());
    }
    let c = std::fs::read_to_string(dir.join("c1f.md")).unwrap();
    assert!(c.contains("`Help:semtext`"), "Help:c1f names the topic");
    assert!(std::fs::read_to_string(repo().join("membrane/apps/barest.c1f/help.c1f")).unwrap().contains("Help:semtext.file"), "the entity is seeded");
}
