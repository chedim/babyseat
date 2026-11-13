# SemText and legs: how code builds the graph (design, 2026-11-13; the tokenizer is built, the rest is not)

A conversation between the operator and the assistant, written down. Only the tokenizer is built (see "Tokenizer (built)" at the end). The rest is not built. It is the shape the semantic field
takes in the barest seat: ONE graph (the VM's entities), with the semantic state an opt-in component of an entity, and code (c1f
packs) building the graph from text.

## The idea in four lines
1. Text is a flat string with labels on top. The labels are layers of stand-off spans (start, end, kind, parent) over the string; the
   letter level is a view, never a node.
2. `SemText` is a CLASS (not a type). Its raw string is one property; its layers are getters; its parts are virtual child entities
   (`…:body:s[2]:w[3]`) that become stored entities only when something attaches to them.
3. A SemText is a LEG: the place where the graph touches the semantic field. Its sentences carry vectors. Only SemTexts (and what hangs
   under them) have a semantic state; requests, receipts, trips and turns are plain.
4. Code builds the graph from the legs: mention rules (c1f) turn word, identifier and path spans into entities and edges; an entity's
   position and state come from the legs that mention it.

## The pipeline (messages are the bootstrap)
1. A message arrives. The MESSAGE PACK creates its body explicitly, never implicitly:
   `membrane::Chat:message[id]:body = SemText(text: …)`.
2. A native tokenizer in a core pack (Unicode-aware: normalisation and segmentation tables compiled into the binary) produces the
   sentence, word, identifier and path spans. It replaces the Python spaCy pass for single words and identifiers; phrases wait.
3. Embeddings for the sentence spans come from a local Ollama over `Fetch`. The receipt of each call is a journalled outside write, so
   replay is exact.
4. Mention rules (c1f, in a pack the model can layer over) create `Word:<normalised surface>`, `File:<path>` and the like by
   constructor and link them to the spans (mention edges). An entity is OWNED by its mentions: it lives while one mention exists, so
   forgetting a message tidies the graph. The recall index holds weak references.
5. The field engine (the Rust port, against the golden fixtures in `tests/golden/efield/`) moves entity states by the sentence vectors.

## The model builds more legs
A leg is just a SemText, so any pack the model writes can make one: a command's output
(`membrane::Command:tests:output = SemText(text: …)`), a file entity's content when it changes, a fetched page, its own plan. These
are its senses. The model can also add or override mention rules in a layer above the shipped ones (`docs/c1f-layers.md`).

## Origin is the graph
There is no `origin` property. The owner chain says where a leg came from: `Chat:main › message › body` for the conversation,
`Command:tests › output` for a sensor. The per-stream statistics the old field kept (a background per origin, boilerplate frequency)
are keyed by that chain (the owner's class or the path prefix), so a noisy command's legs never mix with the conversation's.

## Decisions to bring to the operator before building (each is a threshold or a rule)
- Which spans get vectors: sentences only (words take their position from the legs that mention them).
- When a word becomes an entity: pending mention edges until K distinct sentences, then the entity (the old nursery). K is a threshold.
  Nothing is judged by quality ("nothing is junk in language"), only by count and dormancy.
- Which spans become entities by default: words, identifiers, file paths. The old `WORD_MIN_LEN = 4` ("dust") counts characters and is
  Latin-biased: count grapheme clusters, be script-aware, or drop it for the existing frequency gate.
- A budget for embedding calls (a cap per minute), so a runaway sensor cannot stall the seat.
- Untrusted legs: a fetched page or a file is not the person's words; the owner chain lets recall show where a thing came from.

## Risks
- The graph is only as good as the extraction: thin mention rules leave it sparse. Mention extraction has to exist early, even crude.
- Two representations of one graph (spans in compact arrays, entities in the engine) must stay consistent: virtual entities that
  become real when something attaches to them are the delicate part.
- Segmentation rules (code blocks, lists, scripts without spaces) can grow without limit: start with sentences, words, identifiers,
  paths and the mention edges; paragraphs, syntax and coreference come later as layers over the same spans.
- Evidence: the case for graph-first rests on one observation (the no-history map kept a thread through entities) and on reasoning,
  not on a held-out comparison.

## Tokenizer (built, #1143)
`native/src/text.rs` (`spans(text, layer)`, the c1f builtin `spans`, docs/c1f-stdlib.md) produces the span layers. It is compiled into the
binary: `unicode-segmentation` pinned at `=1.13.3` (default features off: it is no_std-capable and compiles for wasm32, which
`native/tests/wasm_check_1143.rs` keeps true). No spaCy, no model, no I/O; pure, deterministic, linear (1 MB, all six layers, about 0.75 s
in a debug build).

- **Offsets are chars (Unicode scalar values), not bytes.** c1f has no byte slicing: `len`, `head` and `tail` count chars, so a span cuts
  the string the same way in a pack as in Rust. Offsets are logical (a right-to-left text counts in reading order); a combining mark
  (`e` + U+0301) is two chars; a ZWJ emoji family is five. Byte offsets are not returned (a bytes view would be a second unit to keep consistent).
- **Layers** (each sorted, non-overlapping within itself; different layers overlap): `sentence` (UAX #29, plus a blank line, a heading, a
  list item or a fence line ends a sentence; single newlines are soft wraps; spans trimmed), `word` (UAX #29, tokens holding a letter or
  digit), `identifier` (snake_case, camelCase, PascalCase with a hump, kebab-case with a letter each side, dotted, `Class:eid`, `a::B:c:1`,
  backticked names without whitespace; not repeated inside a path or URL), `path` (`/a/b.c`, `~/x`, `./y`, `../z`, `C:\dir\f`, bare
  `dir/file.ext`; a `:line:col` suffix is cut off), `url` (`scheme://...`), `number` (`3.14`, `1,000`; not glued to letters).
- **Known limitations.** UAX #29 knows no abbreviations: `Dr. Smith` splits after `Dr.` (a lowercase continuation like `e.g. grep` does
  not). Word boundaries have no dictionary: Chinese and Japanese ideographs are one token each and hiragana one per character (katakana
  runs stay together). A hyphen splits words (`well-known` is two words and, by the kebab rule, also one identifier); `e.g` and
  `Hello.World` are dotted identifiers; `/help` is not a path (an absolute path needs a second `/` or an extension); paths cannot hold
  spaces. No lemma, no part of speech, no emoji kind (emoji and punctuation are skipped by the word layer), no markdown beyond the
  line rules above (fence content is read as ordinary lines).
- **Not built:** the `SemText` class and its stored string, virtual child spans (`…:s[2]:w[3]`), mention rules (spans to entities and
  edges), sentence embeddings, the recall index and the field engine's use of any of it.
