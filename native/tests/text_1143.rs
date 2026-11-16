//! The native tokenizer (#1143, docs/semtext-and-legs.md "Tokenizer (built)"): span layers over a flat string, offsets in
//! Unicode scalar values (chars), never bytes. Every test also checks the invariants: sorted, non-overlapping within the
//! layer, inside the string, and slicing by the offsets gives the surface.
use membrane_vm::text::{spans, Layer, Span};

fn chars_of(t: &str, s: &Span) -> String { t.chars().skip(s.start).take(s.end - s.start).collect() }

fn check(t: &str, l: Layer) -> Vec<String> {
    let sp = spans(t, l);
    let n = t.chars().count();
    let mut prev = 0;
    for s in &sp {
        assert!(s.start < s.end, "empty span {s:?} in {t:?}");
        assert!(s.end <= n, "span {s:?} outside a {n}-char text");
        assert!(s.start >= prev, "spans overlap or are unsorted at {s:?} in {t:?}");
        prev = s.end;
    }
    sp.iter().map(|s| chars_of(t, s)).collect()
}
fn sent(t: &str) -> Vec<String> { check(t, Layer::Sentence) }
fn word(t: &str) -> Vec<String> { check(t, Layer::Word) }
fn ident(t: &str) -> Vec<String> { check(t, Layer::Identifier) }
fn path(t: &str) -> Vec<String> { check(t, Layer::Path) }
fn url(t: &str) -> Vec<String> { check(t, Layer::Url) }
fn num(t: &str) -> Vec<String> { check(t, Layer::Number) }
fn v(a: &[&str]) -> Vec<String> { a.iter().map(|s| s.to_string()).collect() }

#[test]
fn english_sentences_trim_their_trailing_space() {
    assert_eq!(sent("It works. Does it? Yes!  Done."), v(&["It works.", "Does it?", "Yes!", "Done."]));
}

#[test]
fn abbreviations_are_a_known_limitation_of_uax29() {
    // UAX #29 does not know 'Dr.' or 'e.g.': a lowercase continuation is kept, a capital after the dot splits (documented)
    assert_eq!(sent("I saw Dr. Smith today."), v(&["I saw Dr. ".trim(), "Smith today."]));
    assert_eq!(sent("Use tools, e.g. grep and sed."), v(&["Use tools, e.g. grep and sed."]));
}

#[test]
fn russian_german_chinese_japanese_sentences() {
    assert_eq!(sent("Привет, мир. Как дела?"), v(&["Привет, мир.", "Как дела?"]));
    assert_eq!(sent("Schön, größer. Über uns!"), v(&["Schön, größer.", "Über uns!"]));
    assert_eq!(sent("你好。今天天气很好。"), v(&["你好。", "今天天气很好。"]));
    assert_eq!(sent("こんにちは。元気ですか？"), v(&["こんにちは。", "元気ですか？"]));
}

#[test]
fn words_keep_letters_and_digits_only() {
    assert_eq!(word("Hello, world! 42 times."), v(&["Hello", "world", "42", "times"]));
    assert_eq!(word("don't stop"), v(&["don't", "stop"]));                 // an apostrophe inside a word
    assert_eq!(word("well-known"), v(&["well", "known"]));                 // a hyphen splits (UAX #29)
    assert_eq!(word("snake_case x"), v(&["snake_case", "x"]));             // an underscore joins (UAX #29 ExtendNumLet)
    assert_eq!(word("3.14 and 1,000"), v(&["3.14", "and", "1,000"]));
}

#[test]
fn cyrillic_german_words() {
    assert_eq!(word("Привет, мир!"), v(&["Привет", "мир"]));
    assert_eq!(word("Größe über"), v(&["Größe", "über"]));
}

#[test]
fn cjk_words_are_split_by_uax29_and_stable() {
    // documented: UAX #29 has no dictionary, so ideographs are one token each; katakana runs stay together, hiragana and ideographs are one token each
    assert_eq!(word("你好世界"), v(&["你", "好", "世", "界"]));
    assert_eq!(word("こんにちは"), v(&["こ", "ん", "に", "ち", "は"]));      // hiragana too (no word rule for it)
    assert_eq!(word("カタカナ です"), v(&["カタカナ", "で", "す"]));      // a katakana run stays together
    assert_eq!(word("你好世界"), word("你好世界"));
}

#[test]
fn right_to_left_offsets_are_logical() {
    let t = "שלום עולם. مرحبا بالعالم";
    assert_eq!(word(t), v(&["שלום", "עולם", "مرحبا", "بالعالم"]));
    let sp = spans(t, Layer::Word);
    assert_eq!((sp[0].start, sp[0].end), (0, 4));       // logical order from the start of the string
    assert_eq!((sp[1].start, sp[1].end), (5, 9));
}

#[test]
fn combining_marks_count_as_chars() {
    let t = "cafe\u{301} ok";                              // e + U+0301
    assert_eq!(t.chars().count(), 8);
    let sp = spans(t, Layer::Word);
    assert_eq!((sp[0].start, sp[0].end), (0, 5));         // the mark is a char of its own and stays inside the word
    assert_eq!((sp[1].start, sp[1].end), (6, 8));
}

#[test]
fn emoji_sequences_are_not_words_and_do_not_shift_offsets() {
    let t = "ok \u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467} done";   // a ZWJ family: 5 chars
    assert_eq!(word(t), v(&["ok", "done"]));
    let sp = spans(t, Layer::Word);
    assert_eq!((sp[1].start, sp[1].end), (9, 13));        // 3 + 5 + 1 chars before 'done'
    assert_eq!(sent("Fine \u{1F44D}. Next."), v(&["Fine \u{1F44D}.", "Next."]));
}

#[test]
fn identifiers() {
    assert_eq!(ident("call my_func and getUserName, PascalCase too"), v(&["my_func", "getUserName", "PascalCase"]));
    assert_eq!(ident("a pre-commit hook"), v(&["pre-commit"]));
    assert_eq!(ident("see os.path.join now"), v(&["os.path.join"]));
    assert_eq!(ident("Trip:main and membrane::Chat:message:123."), v(&["Trip:main", "membrane::Chat:message:123"]));
    assert_eq!(ident("run `Trip` and `x_y` then `two words`"), v(&["Trip", "x_y"]));
    assert_eq!(ident("plain words, Title Case: nothing here. 3.14"), Vec::<String>::new());
    assert_eq!(ident("a- b -c 1-2 x-1"), Vec::<String>::new());         // kebab needs a letter on both sides of the hyphen
}

#[test]
fn identifiers_inside_a_path_or_url_are_not_repeated() {
    assert_eq!(ident("open src/main_loop.rs and http://a.b/c_d"), Vec::<String>::new());
}

#[test]
fn paths() {
    assert_eq!(path("open /a/b.c and ~/x then ./y or ../z."), v(&["/a/b.c", "~/x", "./y", "../z"]));
    assert_eq!(path(r"on C:\dir\f.txt now"), v(&[r"C:\dir\f.txt"]));
    assert_eq!(path("edit src/lib.rs, (docs/a.md) and `x/y.py`"), v(&["src/lib.rs", "docs/a.md", "x/y.py"]));
    assert_eq!(path("see src/lib.rs:42:7 now"), v(&["src/lib.rs"]));       // a :line:col suffix is not part of the path
    assert_eq!(path("and/or 10/12/2024 a/b /help 3/4"), Vec::<String>::new());
    assert_eq!(path("see http://x.org/a/b.c"), Vec::<String>::new());     // a URL is its own layer
}

#[test]
fn urls() {
    assert_eq!(url("go to https://example.com/a?b=1, or (http://x.org/y). ok"), v(&["https://example.com/a?b=1", "http://x.org/y"]));
    assert_eq!(url("no scheme: example.com/a ://"), Vec::<String>::new());
}

#[test]
fn numbers() {
    assert_eq!(num("took 3.14, 1,000 3.14s and 42; x86 a1 -7"), v(&["3.14", "1,000", "42", "7"]));
}

#[test]
fn a_code_block_with_identifiers_and_paths() {
    let t = "Run the build:\n\n```sh\ncargo test --test text_1143\n```\n\nThen edit native/src/text.rs and call spans_of(text) in `Chat:main`.";
    assert_eq!(path(t), v(&["native/src/text.rs"]));
    assert_eq!(ident(t), v(&["text_1143", "spans_of", "Chat:main"]));
}

#[test]
fn markdown_lists_headings_fences_and_blank_lines_end_sentences() {
    let t = "# Title\nIntro line one\nstill the intro.\n\n- first item\n- second item.\n1. numbered\n```rust\nlet x = 1;\n```\nAfter.";
    assert_eq!(sent(t), v(&["# Title", "Intro line one\nstill the intro.", "- first item", "- second item.", "1. numbered", "```rust", "let x = 1;", "```", "After."]));
}

#[test]
fn empty_and_blank_texts_have_no_spans() {
    for l in [Layer::Sentence, Layer::Word, Layer::Identifier, Layer::Path, Layer::Url, Layer::Number] {
        assert!(spans("", l).is_empty());
        assert!(spans(" \n\t \r\n ", l).is_empty());
    }
}

#[test]
fn offsets_round_trip_on_a_mixed_text() {
    let t = "Привет, Dr. Smith! Открой ~/мир/файл.txt и `my_fn` (https://x.org/é). 你好。\u{1F468}\u{200D}\u{1F469} cafe\u{301}\n- a_b\n";
    for l in [Layer::Sentence, Layer::Word, Layer::Identifier, Layer::Path, Layer::Url, Layer::Number] { check(t, l); }
    assert_eq!(path(t), v(&["~/мир/файл.txt"]));
}

#[test]
fn a_megabyte_is_linear() {
    let unit = "The quick brown fox, e.g. my_func at src/a/b.rs:3 and https://x.org/y; Trip:main costs 3.5 units. Привет мир!\n- item\n\n";
    let mut t = String::new();
    while t.len() < 1_000_000 { t.push_str(unit); }
    let t0 = std::time::Instant::now();
    let mut total = 0;
    for l in [Layer::Sentence, Layer::Word, Layer::Identifier, Layer::Path, Layer::Url, Layer::Number] { total += spans(&t, l).len(); }
    let dt = t0.elapsed().as_secs_f64();
    eprintln!("1 MB, all six layers: {total} spans in {dt:.3} s");
    assert!(total > 50_000);
    assert!(dt < 5.0, "all layers on 1 MB took {dt} s");
    // doubling the text must not square the time (generous: well under 4x)
    let t2 = format!("{t}{t}");
    let t1 = std::time::Instant::now();
    for l in [Layer::Sentence, Layer::Word, Layer::Identifier, Layer::Path, Layer::Url, Layer::Number] { spans(&t2, l); }
    let d2 = t1.elapsed().as_secs_f64();
    assert!(d2 < dt * 3.5 + 0.2, "2 MB took {d2} s against {dt} s for 1 MB");
}

#[test]
fn a_single_huge_line_with_no_breaks_is_linear() {
    let t = "ab ".repeat(300_000) + &"x".repeat(300_000);
    let t0 = std::time::Instant::now();
    for l in [Layer::Sentence, Layer::Word, Layer::Identifier, Layer::Path, Layer::Url, Layer::Number] { spans(&t, l); }
    assert!(t0.elapsed().as_secs_f64() < 5.0);
}

#[test]
fn layer_names_parse() {
    assert_eq!(Layer::parse("sentence"), Some(Layer::Sentence));
    assert_eq!(Layer::parse("url"), Some(Layer::Url));
    assert_eq!(Layer::parse("paragraph"), None);
}
