# SemText and legs: how code builds the graph (design, 2026-11-13; built in part: the tokenizer, SemText, spans, form classes, mentions, the message body, recall by edges)

A conversation between the operator and the assistant, written down. The first slice is built (see "Built: the first slice" at the end); vectors, the recall index, the field engine's use of the legs and everything under "Not built" there are not. It is the shape the semantic field
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

Lexicon (synonyms, broader/narrower terms, idioms, base forms from WordNet, read in place): design note `docs/lexicon-design.md` (not built).

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

## Built: the first slice (#1143, operator decisions 2026-11-13)
What landed, where, and what it changed against the design above. Packs: `membrane/packs/semtext.c1f` (class, layers, spans) and `membrane/packs/mentions.c1f`
(form classes, mentions, recall by edges), both in `membrane/apps/barest.packs`; the chat packs write the bodies. Tests: `native/tests/semtext_1143.rs`,
`semtext_process_1143.rs`, `chat_process.rs` (`a_message_body_and_the_answer_are_semtexts_...`), `help_1143.rs`. Model-facing page: `Help:semtext`
(`membrane/apps/barest.c1f/help/semtext.md`); it never teaches `spans`, the builtin is internal to the SemText pack.

- **`SemText` is a CLASS**, made by a constructor (`<owner>:body = SemText(text: ...)`); its `text` is the one stored string; `sentences words identifiers paths
  urls numbers` are getters over `spans(this.text, <layer>)`, recomputed when `text` changes. Storage: the default (lazy: stored, loaded on reference). RESIDENT would
  load every text ever said at start. (Each layer is a cached getter value, so a stored body also holds its layers' rows: the cost of an eager layer; see "Cost".)
- **Spans are VIRTUAL entities, attached by one explicit write.** `<semtext>.attach = {layer: 'word', start: 5}` makes `Span:of:<key>:<letter>:<start>` (letters
  s w i p u n; the key is the text's eid with `::` written `/`, because the storage mode follows the eid's class and an eid that began with the text's class, `Chat`, would
  be a stored entity). `Span` is VIRTUAL; it has `of` (a `$ref` to the text), `layer`, `start`, `end`; its `text` is a getter that cuts the parent's string, so nothing
  is duplicated. What holds it is a `$ref`: the write's receipt `attach_result` (the last one) or any live entity that keeps it; the garbage collector
  (docs/cliffvm.md) collects the rest at the end of the round. Differences from the design: (1) the key is the START OFFSET (`:w:5`), not a list index (`:w[3]`): c1f has no
  enumerate, and (layer, start) is unique; (2) the colon-path ownership rule is not used: the span is not a child by path of the text (that would need the eid to begin with the text's
  class, which would make it a stored entity), so it lives by references; (3) there are no lazy reads of `<semtext>:w[3]`: the VM cannot make an entity from a read, so attach is the one way.
- **K = 1.** Every word becomes an entity at its first mention (no nursery: the K-distinct-sentences gate of the design is K = 1). Words fold by lowercase only (no stemming)
  and by their form class's lemma. No collection of stored Word entities.
- **Form classes (c1f, mentions.c1f).** A span's class list `parents` is written from its `forms` getter (a setter on the getter, because a born entity's getters are computed when
  the round ends and a birth runs no setter): `SemNumber` (a `number` span), `SemPlural`, `SemAllCaps`, `SemCapital` (not the first word of a sentence), `SemIdentifier`, `SemPath`,
  then `span`. A word may carry several. Each class has its own getters (`form`, `lemma`); the nearest class answers first, and a layer can override one
  (`SemPlural.lemma` in a pack that imports `mentions`). The rules are crude and documented in the pack: plural = more than 3 letters, ends in s, not ss/us/is, and the suffix
  rules `ies -> y`, `ses xes zes ches shes -> drop es`, else drop s (`houses` -> `hous`); all-caps looks at the first two and last two letters (c1f cannot walk a text letter by letter).
- **Mentions.** `<semtext>.mention = true` births armed spans for the words (not those inside an identifier, a path or a url), identifiers and paths of the text, the first MENTION_CAP
  of each layer; they classify themselves and know their `lemma` (words: lowercase + class; identifiers and paths: the exact text) and `target`, the `$ref` edge from the span to the
  entity: `Word:lemma:<lemma>`, `Identifier:name:<text>`, `Path:path:<text>`. When every span is classified one setter makes the entities by constructor and writes each entity's
  `mentioned_in` (`[{text: <SemText eid>, sentence: <n>}]`, no copy of any text, one write per entity per mention so two spans never write the same target in a cascade) and the text's
  `mentions` (a list of `$ref`s to the entities). The spans are then held by nothing and collected. Writing `mention` again replaces the text's own entries (no duplicates).
  The entity ids carry a literal word before the key (`Word:lemma:cat`) because an entity literal with a computed key needs one; the old Python seat's `Word:<x>` ids are not reused. A path
  is a `Path`, not a `File`: a `File` entity reads the disk and is RESIDENT.
- **The message body.** `chat.c1f` makes `membrane::Chat:message:<id>:body` and asks for its mentions; `chat_turn.c1f` makes `...:turn:response:body` when the turn ends without an
  error. `text` on the message is unchanged (the page's API). Tool results are not tokenized.
- **Recall by edges.** `<semtext>.related_n = N` then `<semtext>.related`: `[{semtext, sentence_index, text, shared}]`, the sentences of other texts that mention the same entities,
  ranked by the sum over the shared entities of 1 / (the number of `mentioned_in` entries of that entity), `shared` being the plain count. It follows `mentions` and `mentioned_in`;
  no embedding, no search. Computed only while `related_n` is set (a getter is eager).

### Cost (one mention)
A text of W words, I identifiers and P paths makes at most W + I + P virtual spans (each layer capped at 400), all collected when the cascade ends, and at most D new stored
entities (D = the distinct lemmas, identifiers and paths not seen before) with one write of `mentioned_in` each. A 300-word chat message: 1 body entity (stored; with the
layer rows cached on it), up to 300 transient spans, up to 300 new Word entities in the very first message and a handful afterwards (a common word costs one list entry).
Measured on the fake-CLI chat test: one message with its answer = 7 entities under the message (message, turn, body, response, response body, 2 tools) + 8 Word entities and 1
Identifier. `mentioned_in` of a very common word grows by one entry per sentence that holds it and is rewritten whole at each mention: the cost of K = 1 with no cut, flagged below.

`mentioned` is the value of `mention` last finished (`record-mentions` writes it once; the mention setter does not reset it: a field write that a round already made is refused, and resetting it there made the cascade refuse its own last write, `loop: (<text>, mentioned) already written this round`). A text whose `mention` takes a new value is mentioned again.
The reply of an `entity_write` of `mention` lists every property the cascade wrote, once for each transient span too: about 20 entries per span (~95 bytes each: ~47 KB for a 23-word text, ~0.6 MB for a 300-word one). That is the shape of `reactions` (every real change of the round), not of the mention.

### Thresholds flagged for the operator
MENTION_CAP = 400 per layer (`SemText.mention_cap`; none was given). `related_n` has no default: the asker gives it. The frequency divisor of `related` is the count itself (no number).
Open: a cut for the entities mentioned by very many sentences (their `mentioned_in` grows without bound and `related` reads it whole; one evaluation past the step budget answers a
reasoned null); the step budget of a getter or setter applies to the mention setter of a big text (time limits nothing).

### Not built
Vectors and embeddings (sentences carry none), paragraphs, syntax, coreference, the recall index, the field engine's use of the legs, a collection of stored Word entities, the
mention rules for urls and numbers (they have spans, no entities), re-mentioning when a text's `text` changes (write `mention` again), per-origin statistics, embeddings budget,
untrusted legs, lazy reads of `<semtext>:w[3]`, a nested comprehension (c1f has none: `related` flattens through JSON text; a `flatten` would be a builtin to propose).


**Cost notes (w36, #1143).** `SemText.related` reads each entity's cached `edge_rows` (a getter of Word / Identifier / Path: the entity's edges as JSON rows, rebuilt when its `mentioned_in` changes) instead of rebuilding every edge's text on every ask; the asking text's own rows are dropped after the grouping. `mention_done` reads the spans the mention setter listed (`mention_spans`) and waits for their `forms`. `group_by` finds a text key's group through a hash. The rest of the cost of `related` is linear in the edges walked; a cut on the edges of a very common word is an open operator decision (nothing is cut).
