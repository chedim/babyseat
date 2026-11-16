//! The native tokenizer (#1143, docs/semtext-and-legs.md): the span layers of a text. A text is ONE flat string; a layer is a
//! sorted list of non-overlapping stand-off spans over it. Different layers overlap (an identifier covers several words).
//!
//! OFFSETS ARE UNICODE SCALAR VALUES (chars), not bytes: c1f has no byte slicing (`head`/`tail`/`len` count chars), so a span
//! `{start, end}` cuts the text the same way in a pack as here. Offsets are logical (right-to-left scripts count in reading
//! order), a combining mark is a char of its own. Pure, deterministic, linear time.
//!
//! Layers:
//! - `sentence`: UAX #29 sentence boundaries, run per block. A blank line, a heading line (`# `), a list item (`- `, `* `, `+ `,
//!   `1. `, `1) `) or a code-fence line ends the sentence before it; a fence line, a heading and a list item's line are
//!   sentences of their own start. Single newlines inside a block are soft wraps (read as spaces). Spans are trimmed.
//! - `word`: UAX #29 word boundaries, keeping tokens that hold a letter or a digit. No emoji kind: emoji and punctuation are skipped.
//! - `identifier`: snake_case, camelCase/PascalCase, kebab-case (a letter on both sides of the hyphen), dotted `a.b.c`,
//!   `Class:eid` / `a::B:c:123` forms, and backticked names without whitespace (the span is the name, without the backticks).
//!   Identifiers inside a path or URL span are not repeated.
//! - `path`: `/abs/x.c` (needs a second `/` or an extension), `~/x`, `./y`, `../z`, `C:\dir\f`, and bare `dir/file.ext`
//!   (an extension that starts with a letter). A trailing `:line[:col]` is not part of the span. No spaces inside a path.
//! - `url`: `scheme://...`, trailing punctuation trimmed.
//! - `number`: digit runs with inner `.` or `,` between digits, not glued to a letter or underscore (`x86` has none).
use unicode_segmentation::UnicodeSegmentation;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Span { pub start: usize, pub end: usize }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Layer { Sentence, Word, Identifier, Path, Url, Number }

impl Layer {
    pub const NAMES: &'static str = "sentence, word, identifier, path, url, number";
    pub fn parse(s: &str) -> Option<Layer> {
        Some(match s { "sentence" => Layer::Sentence, "word" => Layer::Word, "identifier" => Layer::Identifier, "path" => Layer::Path, "url" => Layer::Url, "number" => Layer::Number, _ => return None })
    }
}

pub fn spans(text: &str, layer: Layer) -> Vec<Span> {
    match layer {
        Layer::Word => words(text),
        Layer::Sentence => sentences(text),
        _ => {
            let cs: Vec<char> = text.chars().collect();
            match layer {
                Layer::Identifier => identifiers(&cs),
                Layer::Path => paths(&cs),
                Layer::Url => urls(&cs),
                _ => numbers(&cs),
            }
        }
    }
}

/// the spans of a layer with their surfaces, cut by char offsets (what the builtin returns)
pub fn surfaces(text: &str, layer: Layer) -> Vec<(Span, String)> {
    let cs: Vec<char> = text.chars().collect();
    spans(text, layer).into_iter().map(|s| (s, cs[s.start..s.end].iter().collect())).collect()
}

fn words(text: &str) -> Vec<Span> {
    let mut out = vec![];
    let mut at = 0;
    for (_, w) in text.split_word_bound_indices() {
        let n = w.chars().count();
        if w.chars().any(|c| c.is_alphanumeric()) { out.push(Span { start: at, end: at + n }); }
        at += n;
    }
    out
}

fn is_blank(l: &[char]) -> bool { l.iter().all(|c| c.is_whitespace()) }

/// 0 = text, 1 = blank, 2 = a line that is its own span (fence, heading), 3 = a list item (starts a block)
fn line_kind(l: &[char]) -> u8 {
    if is_blank(l) { return 1; }
    let t: Vec<char> = l.iter().skip_while(|c| c.is_whitespace()).cloned().collect();
    let starts = |p: &str| { let p: Vec<char> = p.chars().collect(); t.len() >= p.len() && t[..p.len()] == p[..] };
    if starts("```") || starts("~~~") { return 2; }
    if t[0] == '#' { let h = t.iter().take_while(|c| **c == '#').count(); if h <= 6 && t.get(h).map_or(false, |c| c.is_whitespace()) { return 2; } }
    if matches!(t[0], '-' | '*' | '+') && t.get(1).map_or(false, |c| *c == ' ' || *c == '\t') { return 3; }
    let d = t.iter().take_while(|c| c.is_ascii_digit()).count();
    if d > 0 && d < t.len() - 1 && matches!(t[d], '.' | ')') && (t[d + 1] == ' ' || t[d + 1] == '\t') { return 3; }
    0
}

fn sentences(text: &str) -> Vec<Span> {
    let cs: Vec<char> = text.chars().collect();
    let mut out = vec![];
    // block = [from, to) in chars, the lines of one paragraph or list item
    let mut block: Option<(usize, usize)> = None;
    let mut i = 0;
    let flush = |b: &mut Option<(usize, usize)>, out: &mut Vec<Span>| {
        if let Some((a, z)) = b.take() { block_sentences(&cs, a, z, out); }
    };
    while i <= cs.len() && !(i == cs.len() && cs.is_empty()) {
        let mut j = i; while j < cs.len() && cs[j] != '\n' { j += 1; }
        let line = &cs[i..j];
        match line_kind(line) {
            1 => flush(&mut block, &mut out),
            2 => { flush(&mut block, &mut out); trimmed_push(&cs, i, j, &mut out); }
            3 => { flush(&mut block, &mut out); block = Some((i, j)); }
            _ => { block = Some(match block { Some((a, _)) => (a, j), None => (i, j) }); }
        }
        if j >= cs.len() { break; }
        i = j + 1;
    }
    flush(&mut block, &mut out);
    out
}

fn trimmed_push(cs: &[char], a: usize, z: usize, out: &mut Vec<Span>) {
    let (mut a, mut z) = (a, z);
    while a < z && cs[a].is_whitespace() { a += 1; }
    while z > a && cs[z - 1].is_whitespace() { z -= 1; }
    if a < z { out.push(Span { start: a, end: z }); }
}

fn block_sentences(cs: &[char], a: usize, z: usize, out: &mut Vec<Span>) {
    // soft wraps read as spaces: \n and \r are one byte like ' ', so the char counts do not move
    let s: String = cs[a..z].iter().map(|c| if *c == '\n' || *c == '\r' { ' ' } else { *c }).collect();
    let mut at = a;
    for (_, piece) in s.split_sentence_bound_indices() {
        let n = piece.chars().count();
        trimmed_push(cs, at, at + n, out);
        at += n;
    }
}

fn word_char(c: char) -> bool { c.is_alphanumeric() || c == '_' }

fn urls(cs: &[char]) -> Vec<Span> {
    let mut out = vec![];
    let n = cs.len();
    let mut i = 0;
    while i + 3 <= n {
        // a scheme ends at "://"; look for it at ':' and walk back over the scheme
        if cs[i] == ':' && cs[i + 1] == '/' && cs[i + 2] == '/' {
            let mut a = i;
            while a > 0 && (cs[a - 1].is_ascii_alphanumeric() || matches!(cs[a - 1], '+' | '.' | '-')) { a -= 1; }
            while a < i && !cs[a].is_ascii_alphabetic() { a += 1; }
            let mut z = i + 3;
            while z < n && !cs[z].is_whitespace() && !matches!(cs[z], '<' | '>' | '"' | '\'' | '`') { z += 1; }
            while z > i + 3 && matches!(cs[z - 1], '.' | ',' | ';' | ':' | '!' | '?' | ')' | ']' | '}') { z -= 1; }
            if a < i && z > i + 3 { out.push(Span { start: a, end: z }); }
            i = z.max(i + 1);
        } else { i += 1; }
    }
    out
}

fn numbers(cs: &[char]) -> Vec<Span> {
    let mut out = vec![];
    let n = cs.len();
    let mut i = 0;
    while i < n {
        if cs[i].is_ascii_digit() && (i == 0 || !word_char(cs[i - 1])) {
            let mut j = i;
            loop {
                while j < n && cs[j].is_ascii_digit() { j += 1; }
                if j + 1 < n && matches!(cs[j], '.' | ',') && cs[j + 1].is_ascii_digit() { j += 1; } else { break; }
            }
            if j >= n || !word_char(cs[j]) { out.push(Span { start: i, end: j }); }
            i = j.max(i + 1);
            // skip the rest of a glued token ("1a2")
            while i < n && word_char(cs[i]) { i += 1; }
        } else { i += 1; }
    }
    out
}

/// bare path characters: letters, digits and `_ . - ~ @ % + = ,`
fn path_char(c: char) -> bool { c.is_alphanumeric() || matches!(c, '_' | '.' | '-' | '~' | '@' | '%' | '+' | '=' | ',') }

fn paths(cs: &[char]) -> Vec<Span> {
    let mut out = vec![];
    let n = cs.len();
    let mut i = 0;
    while i < n {
        if cs[i].is_whitespace() { i += 1; continue; }
        let mut j = i; while j < n && !cs[j].is_whitespace() { j += 1; }
        let (mut a, mut z) = (i, j);
        while a < z && matches!(cs[a], '(' | '[' | '{' | '"' | '\'' | '`' | '<') { a += 1; }
        while z > a && matches!(cs[z - 1], ')' | ']' | '}' | '"' | '\'' | '`' | '>' | '.' | ',' | ';' | ':' | '!' | '?') { z -= 1; }
        // a trailing :line or :line:col is not the path (after the punctuation above, so "f.rs:12." works)
        for _ in 0..2 {
            let mut k = z; while k > a && cs[k - 1].is_ascii_digit() { k -= 1; }
            if k < z && k > a + 1 && cs[k - 1] == ':' && cs[a..k - 1].iter().any(|c| !c.is_ascii_digit()) { z = k - 1; }
        }
        if a < z && is_path(&cs[a..z]) { out.push(Span { start: a, end: z }); }
        i = j;
    }
    out
}

fn has_ext(seg: &[char]) -> bool {
    match seg.iter().rposition(|c| *c == '.') {
        Some(d) if d > 0 && d + 1 < seg.len() => seg[d + 1].is_alphabetic() && seg[d + 1..].iter().all(|c| c.is_alphanumeric()),
        _ => false,
    }
}

fn is_path(t: &[char]) -> bool {
    if t.windows(3).any(|w| w == [':', '/', '/']) { return false; }   // a URL
    // C:\dir\f
    if t.len() > 3 && t[0].is_ascii_alphabetic() && t[1] == ':' && t[2] == '\\' {
        return t[3..].iter().all(|c| path_char(*c) || *c == '\\') && t[3..].iter().any(|c| c.is_alphanumeric());
    }
    let body: &[char] = if t.starts_with(&['~', '/']) { &t[2..] }
        else if t.starts_with(&['.', '.', '/']) { &t[3..] }
        else if t.starts_with(&['.', '/']) { &t[2..] }
        else if t[0] == '/' { &t[1..] }
        else { return bare_path(t); };
    if body.is_empty() || !body.iter().all(|c| path_char(*c) || *c == '/') || !body.iter().any(|c| c.is_alphanumeric()) { return false; }
    if t[0] == '/' && !t.starts_with(&['.', '/']) {
        // "/help" is a command, not a path: an absolute path needs a second slash or an extension
        return body.contains(&'/') || has_ext(body);
    }
    true
}

fn bare_path(t: &[char]) -> bool {
    if !t.iter().all(|c| path_char(*c) || *c == '/') { return false; }
    let segs: Vec<&[char]> = t.split(|c| *c == '/').collect();
    segs.len() >= 2 && segs.iter().all(|s| !s.is_empty() && s.iter().any(|c| c.is_alphanumeric())) && has_ext(segs[segs.len() - 1])
}

fn identifiers(cs: &[char]) -> Vec<Span> {
    let n = cs.len();
    let mut cand: Vec<Span> = vec![];
    let mut i = 0;
    while i < n {
        let c = cs[i];
        if (c.is_alphabetic() || c == '_') && (i == 0 || !word_char(cs[i - 1])) {
            let mut j = i;
            let mut joined = false;
            let mut shaped = false;
            loop {
                let r = j;
                while j < n && word_char(cs[j]) { j += 1; }
                if is_shaped(&cs[r..j]) { shaped = true; }
                // a connector, then the next part
                let k = if j + 2 < n && cs[j] == ':' && cs[j + 1] == ':' && word_char(cs[j + 2]) { j + 2 }
                    else if j + 1 < n && cs[j] == ':' && cs[j + 1] != ':' && word_char(cs[j + 1]) { j + 1 }
                    else if j + 1 < n && cs[j] == '.' && (cs[j + 1].is_alphabetic() || cs[j + 1] == '_') { j + 1 }
                    else if j + 1 < n && j > 0 && cs[j] == '-' && cs[j - 1].is_alphabetic() && cs[j + 1].is_alphabetic() { j + 1 }
                    else { break };
                joined = true;
                j = k;
            }
            if joined || shaped { cand.push(Span { start: i, end: j }); }
            i = j.max(i + 1);
        } else if c == '`' {
            // `name`: a single backtick pair around a whitespace-free name; runs of backticks (fences) are skipped
            let mut e = i; while e < n && cs[e] == '`' { e += 1; }
            if e - i == 1 {
                let mut z = e; while z < n && cs[z] != '`' && !cs[z].is_whitespace() { z += 1; }
                if z < n && cs[z] == '`' && z > e && (z + 1 >= n || cs[z + 1] != '`') { cand.push(Span { start: e, end: z }); i = z + 1; continue; }
            }
            i = e;
        } else { i += 1; }
    }
    // drop what sits inside a path or URL, then overlaps (a backticked name found twice): earlier, then longer, wins
    let mut fence = urls(cs); fence.extend(paths(cs)); fence.sort_by_key(|s| (s.start, s.end));
    let mut out: Vec<Span> = vec![];
    let mut f = 0;
    cand.sort_by_key(|s| (s.start, std::cmp::Reverse(s.end)));
    for s in cand {
        while f < fence.len() && fence[f].end <= s.start { f += 1; }
        // fences may nest (a URL holding a path-like tail): look at those that reach the span
        if fence[f..].iter().take_while(|p| p.start <= s.start).any(|p| p.end >= s.end) { continue; }
        if let Some(l) = out.last() { if s.start < l.end { continue; } }
        out.push(s);
    }
    out
}

/// one part of a name that is an identifier by its shape alone: an underscore with a letter, or a lower-to-upper hump
fn is_shaped(p: &[char]) -> bool {
    (p.contains(&'_') && p.iter().any(|c| c.is_alphabetic())) || p.windows(2).any(|w| (w[0].is_lowercase() || w[0].is_ascii_digit()) && w[1].is_uppercase())
}
