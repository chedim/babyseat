# SemText and legs: how code builds the graph (built: the tokenizer, SemText, spans, form classes, mentions, the message body, recall by edges, sentence vectors, the shared recall, the recall banner part, Memory, entity recall by worldline)

A conversation between the operator and the assistant, written down. The slices below are built ("Built: the first slice", "sentence vectors", "The field", "Entity recall by worldline", "Recall in the banner", "Memory"); recall's use of the field (the vote) and everything under "Not built" is not. It is the shape the semantic field
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
   sentence, word, identifier and path spans. It covers single words and identifiers; phrases wait.
3. Embeddings for the sentence spans come from the local model server over `Fetch` (`packs/embed.c1f`). The receipt of each call is a journalled outside write, so
   replay is exact.
4. Mention rules (c1f, in a pack the model can layer over) create `Word:<normalised surface>`, `File:<path>` and the like by
   constructor and link them to the spans (mention edges). An entity is OWNED by its mentions: it lives while one mention exists, so
   forgetting a message tidies the graph. The recall index holds weak references.
5. The field (`packs/field.c1f`, "The field" below) moves entity states by the sentence vectors as they land and logs each move on the text. Recall does not read it yet.

## The model builds more legs
A leg is just a SemText, so any pack the model writes can make one: a command's output
(`membrane::Command:tests:output = SemText(text: …)`), a file entity's content when it changes, a fetched page, its own plan. These
are its senses. The model can also add or override mention rules in a layer above the shipped ones (`docs/c1f-layers.md`).

## Origin is the graph
There is no `origin` property. The owner chain says where a leg came from: `Chat:main › message › body` for the conversation,
`Command:tests › output` for a sensor. Per-stream statistics (a background per origin, boilerplate frequency) are not built; they would be keyed by that chain (the owner's class or the path prefix), so a noisy command's legs never mix with the conversation's.

Lexicon (synonyms, broader/narrower terms, idioms, base forms from WordNet, read in place): design note `docs/lexicon-design.md` (not built).

## Decisions to bring to the operator before building (each is a threshold or a rule)
- Which spans get vectors: sentences only (words take their position from the legs that mention them).
- When a word becomes an entity: pending mention edges until K distinct sentences, then the entity (a nursery). K is a threshold.
  Nothing is judged by quality ("nothing is junk in language"), only by count and dormancy.
- Which spans become entities by default: words, identifiers, file paths. A minimum word length counted in characters would be
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
  The entity ids carry a literal word before the key (`Word:lemma:cat`) because an entity literal with a computed key needs one; bare `Word:<x>` ids are not used. A path
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

### Built: sentence vectors, stage 1 of the semantic field (#1143, operator 2026-11-14)
`membrane/packs/embed.c1f`. Every sentence of every SemText gets a vector from the local Ollama, through the Fetch driver, stored as `Vector:of:<key>:s:<start>` (`of`, `start`, `v`; the class is the
vector pack's: MAPPED, f16). `<key>` is the text's eid with `::` written `/` (the Span key rule). There is no flag (operator 2026-11-15): a SemText is asked when its text is written (a setter on `text`; a
`Memory`, which becomes a SemText in answer to its text, when its SemText getters arrive) and it is not yet `embedded`. A tool's result is not a SemText and is not asked. The model server is the boot fact `seat_backend`, the model the fact `embed_model` (default `mxbai-embed-large`, set in `launcher.c1f`); the call is `POST <seat_backend>/api/embed
{model, input: [...]}` with a 120 s limit. The limits: at most 64 sentences a request, a pass of 256 sentences, a same-pass cache of 64 entries (a sentence said twice in a pass makes no second request).
- THE QUEUE is `Embed:queue` (`state` idle | working | paused | stuck, `reason`, `line`, `inflight`, `landed`, `cache`, `pass_n`, `hold`). ONE request at a time, one text per request: within the existing worker limit (`effect_workers`), no
  second limit. A request is sent only while no chat turn is running (each turn of any chat or session, when it becomes `running`, makes a small `Embed:turn:<key>` of this pack's resident class, and retires it when it is done or an error; the queue reads those, never lists `Chat`; a pointer of a dead run is not counted); one in flight finishes, then the queue waits for the turn to end and goes on by itself (the sender is a getter that reads the turns). An `Embed:ask:<key>` is retired when its text has all
  its vectors; a text is marked `embedded = true` and counts its sentences done in `emb_n` (a restart goes on where it stopped). A text whose own recall call is out (`in_flight`, below) waits:
  a `Memory` note's call embeds its sentences and writes their vectors itself (`embed-ask-vectors`), and the queue then only settles it.
- THE BACKLOG: the chat bodies nobody asked for (the imported history, messages from before) are found in the store (`sql`, 64 texts a page, OLDEST first by the message id, a page at a time: the query holds the engine while it runs; the waiting asks go first, oldest first too, so the vectors land in text order for the field, `packs/field.c1f`) and go through the same queue once
  the model server answers. The Settings row `import.embeddings` ("Sentence vectors", read-only) shows `Embed:queue.line`: done of total texts, or why the queue waits. While a history import runs the total is approximate (it never shows less than done plus the texts known
  to be waiting; the next start counts exactly); a page of the backlog is asked again at a chat event or at the import's next page, so the very last page of an import is found at the next chat event or start.
- WHEN IT FAILS (no model server, the model not installed (the reason says `ollama pull <model>`), a timeout, a wrong answer): `fail`/`reason`/`line`/`state: stuck` say why, the sentences stay pending, and the queue is held until the next
  change that is not its own: a chat event (message, turn start or end) or a new ask; then, with no turn running, it tries again. No timer, no loop, never an Error that stops a round.
- REPLAY: the Fetch driver's receipt is a derived write (`driver:request`), but a write to a MAPPED class (`Vector`) is journalled whole and compact, so a replay of the journal remakes the vectors (docs/cliffvm.md, "MAPPED").
- Tests: `native/tests/embed_1143.rs` (in-process, receipts written by the test, one end-to-end test against a tiny model server in the test) and `native/tests/embed_process_1143.rs` (the real seat, a closed port).

### The field (#1143, step 3; `membrane/packs/field.c1f`, tests `native/tests/field_1143.rs`)
The old membrane's entity field, in SemText terms. When a text's sentence vectors land (the embed queue writes its `emb_n`), every Word / Identifier / Path entity a landed sentence mentions moves:
`state <- unit(state + GAIN (x - state))`, x the sentence's unit vector. The step `|GAIN (x - state before)|` is the MOVE, written on the text as a row of `field_moves` (`{e, s, m}`: the entity, the
sentence number, the move); `field_n` on the text counts the sentences applied.
- **Which entities**: identifiers and paths get a state (`FieldState:<entity eid>`, `v` f16, `n` the mentions applied) at their first mention; a word waits in a NURSERY (`field_pend` on the Word: the
  landed sentences that mentioned it) until 3 distinct sentences, then materialises: its state starts at its seed and the waiting mentions are replayed in order, and their moves are written then, on the
  texts that made them; a word of 1-3 letters never gets a state or a move.
- **Seeds**: the bare term's own embedding, `FieldSeed:<entity eid>`, asked once per entity when it materialises: a `FieldTerm:<entity eid>` SemText (the term as its text) is asked through the
  embed queue like any text, and its one vector is written to `FieldSeed` (declared by the vector pack, because the embed queue writes it), never to `Vector`, so the cosine lane never returns a seed.
  A text whose entities need a seed waits (its moves apply when the seed lands; the seed asks go before the backlog).
- **Background**: `FieldBg:all.v`, the running mean of every sentence vector of a mentioned text (`n` counts them).
- **Episodes** (identifiers and paths, from the 40th mention on): every 5 mentions the open run (`run_k`, `run_acc` on the state) closes as `FieldEpisode:<entity eid>:<i>` (its centroid, `n`); the
  list is `eps` on the state; past 48 the two most alike neighbours merge (one merge per landing).
- **The mean** is not stored: `n` is, and a reader recomputes the mean from the sentence vectors for the few candidates it reads.
- **The constants** are getters on SemText that a model's layer can override: `field_gain` 0.5, `field_k_mat` 3, `field_word_min` 4, `field_ep_run` 5, `field_ep_max` 48, `field_mature` 40 (all carried from
  the old seat, each awaiting the operator's yes).
- **Stored, not replayed**: the classes are `MAPPED ... f16 derived` (docs/cliffvm.md): the states, seeds, episodes and the background are stored rows, restored at a restart like any entity, never journalled
  and never recomputed at start; the move log and the nursery are ordinary stored properties written by rules (derived). **A replay of the journal alone does not rebuild the field.**
- **Order**: the moves apply when the vectors land, and the queue sends the waiting asks and the backlog oldest first, so they land in text order (a live message asked while the backlog runs lands
  before older history: that history then moves the states after it).
- **How**: c1f has no loop with an accumulator, so one landing is one cascade: a setter makes a VIRTUAL `FieldStep` per (entity, mention), each chained to the previous mention of the same entity; their
  getters compute the walk; when every chain is computed, one setter writes each state, each text's moves, the nursery, the episodes and the background. The steps are collected when the round ends.
- `recall` does not read the field yet: entity recall (below) is built as its own getter, the move log voter and the vote (steps 4 and 6) are not.

### Entity recall by worldline (#1143, step 5; `membrane/packs/entityrecall.c1f`, tests `native/tests/entityrecall_1143.rs`)
The old membrane's entity recall voter. For an ask whose `recall_on` is true, `SemText.entity_votes` is the list of texts that moved the entities nearest the ask, best first, as rows
`{key, of, text, score}` (`key` = the text's eid + `#` + the start of its sentence, the row shape of the other lanes). It is NOT fused into `recall` yet: the vote (step 6) will read it.
- **The query** is the cosine lane's `r_query` (the weighted sum of the window's text vectors).
- **Candidates**: the entities of the 32 states nearest the query (`near(q, 'FieldState', ent_over x ent_k)`) and the owners of the 32 episodes nearest it (`near(q, 'FieldEpisode', ...)`,
  so an old entity whose state moved away is found by an old phase). `near` is a plain cosine, so the candidates come from the raw query, as in the old seat; the re-score is centred.
- **The worldline** of a candidate: a young entity (fewer than `field_mature` mentions, or no episode yet) is its MEAN; an old one is its episodes, its open run (`run_acc / run_k`) and,
  while those hold less than `ent_cover` of its mentions, its mean too. The mean is not stored: it is recomputed for the candidates only, as the mean of the unit vectors of the
  sentences that moved the entity (its `mentioned_in` entries whose text has applied that sentence, `field_n`); with none, the state stands in.
- **The score** of a candidate is the best, over its points, of `dot(unit(p - bg), unit(q - bg))`, `bg` = `FieldBg:all` (no background, or one of another length: no centring).
  The `ent_k` best are `entity_hits` (`[{e, s}]`), ties by the entity's eid.
- **The evidence** (the old `evidence` / `entity_scores`): every text that moved one of those entities (the entity's `mentioned_in` texts, their `field_moves`) scores the sum over the
  entities of (the entity's score x the move the text made of it, summed over its sentences). A row's sentence is the one that holds the largest part of that sum. The ask itself is
  never a row; the `ent_depth` best, ties by key. No score cut-off anywhere.
- **Empty, not an error**: with no state, or a query of another length, the votes are `[]`; the reason stays on the two searches (`ent_near_states`, `ent_near_eps`), as the cosine
  lane's `r_cos_raw` keeps its own. With the recall off nothing is computed (null).
- **The constants** are getters on SemText a layer overrides: `ent_k` 8 and `ent_depth` 20 (approved), `ent_over` 4 (the over-fetch, 32 candidates of each kind) and `ent_cover` 0.9 (both
  carried from the old seat, awaiting the operator's yes); the mature age is the field's `field_mature`.
- **Cost**: one stage per getter (each evaluation has its own step budget). The largest is reading the candidates' sentence vectors for their means (`ent_mvecs`), about 30 steps a
  mention read: with 300 texts naming 8 of 40 identifiers each (every entity 60 mentions, 32 candidates whose mean is a point) it takes about 57,600 steps of the 100,000 budget
  (28,800 at 150 texts; measured 2026-11-15 by bisecting the budget on `a_realistic_field_stays_inside_the_step_budget`). So the candidates may hold about 3,300 applied mentions
  between them; past that (a common word of thousands of mentions among the candidates) the evaluation answers `budget exceeded`, not a wrong list. Nothing is cut.
- Not built: the move log's previous turn (`held`), the answerable-events filter of the old voter, the old seat's word hop (it was off for the vote: `hop=False`).

### Not built
A backlog of stored texts that are not chat bodies (the backlog query reads chat bodies only; any other text is asked when its text is written), cross-text batching (a request holds one text's sentences: c1f has no flatten or zip), a request in parallel, paragraphs, syntax, coreference, recall's use of the field (the move log voter, the vote; entity recall is its own getter, not read by `recall`), a collection of stored Word entities, the
mention rules for urls and numbers (they have spans, no entities), re-mentioning when a text's `text` changes (write `mention` again), per-origin statistics, untrusted legs, lazy reads of `<semtext>:w[3]`, a nested comprehension (c1f has none: `related` flattens through JSON text; a `flatten` would be a builtin to propose).


**Cost notes (w36, #1143).** `SemText.related` reads each entity's cached `edge_rows` (a getter of Word / Identifier / Path: the entity's edges as JSON rows, rebuilt when its `mentioned_in` changes) instead of rebuilding every edge's text on every ask; the asking text's own rows are dropped after the grouping. `mention_done` reads the spans the mention setter listed (`mention_spans`) and waits for their `forms`. `group_by` finds a text key's group through a hash. The rest of the cost of `related` is linear in the edges walked; a cut on the edges of a very common word is an open operator decision (nothing is cut).

## Recall: one getter on SemText (#1143, operator 2026-11-15)
`SemText.recall` (`membrane/packs/mentions.c1f`) is the ONE recall: the banner part and `Memory` both read it. The ask is the SemText itself; the caller gives no list of texts, and the recall
finds the rest in the graph. It computes only while `recall_on` is true (the banner writes it on its ask's body and takes it off the body before; a `Memory` has it always). Tests: `native/tests/semrecall_1143.rs`.
- **The window.** A chat text (`<message>:body`, `<message>:turn:response:body`) reads the chat's texts counting back from itself in chat order: the ask, the answer before it, that message, the answer
  before that, ... (6 texts: the length of `r_weights`). The older message keys come from the store's key order (one `sql` over the `(cls, k)` index, key order is time order inside a chat,
  docs/chat-keys.md); the bodies are read by their ids; a text that is not there (a turn with no answer) is skipped. Each step back counts half as much as the step before it (`r_ratio`, 0.5, a getter a
  layer overrides: weights 1, 1/2, 1/4, ...). Any other text (a `Memory` note, a leg of a pack) is a window of itself. With no store (the `sql` fails) a chat text falls back to itself alone.
- **The edge lane.** The sentences that mention the Word / Identifier / Path entities of the window's texts; a shared entity adds the weight of the window text that mentions it times 1 / (the number
  of sentences that mention it) (`edge_rows`); at most `entity_pool_max`.
- **The cosine lane.** `near(r_query, 'Vector', pool)`. The query (`r_query`, operator 2026-11-15) is the weighted sum of the window's texts' vectors with the window's weights (`r_weights`,
  `vsum`); a text's vector is the unit of the sum of its sentence vectors (`Vector:of:<key>:s:<start>`, the embedding pipeline's). A window text with no vector yet is left out; with none,
  there is no query and no lane (`the_cosine_query_is_the_weighted_sum_of_the_windows_text_vectors`). The ASK's own vectors come from its own embedding call while that receipt is there
  (`ask_vecs`; 10 s limit; `in_flight` is true while it is out), else from its sentence vectors: what the call embeds is `ask_input`, the ask's whole text as one input for a chat ask
  (`Fetch:recall:ask`, shared by the chat's asks: one vector, a stand-in for its sentence vectors until the queue makes them), its first sentences (at most 64, the queue's batch) for a `Memory`
  (below).
- **Fusion and rerank** as before: reciprocal rank (`rrf_k`), ties by key; the first `rerank_top` rows reranked by the fitted weights over the cosine score, the fused place and the share of the ask's own
  entities the sentence mentions.
- **The ask itself is never a hit**; nothing else is left out (recent chat texts are hits: the model gets no chat history).
- **Near duplicates merge, greedy**: two hits are near duplicates when the shorter sentence has at least `dup_share` of its distinct words (`plain`, lowercase) in the other's; a hit is dropped
  only when it is a near duplicate of a better-placed hit that was KEPT, so in a chain A ~ B ~ C with A and C not alike, B goes and C stays (`a_chain_of_near_duplicates_keeps_its_far_end`).
  `dup_share` is a getter a layer overrides; it reads the dial `dup_share` of `recall:dials` (0.8; the old unused dial `dedupe_jaccard`, renamed). The shared words of every pair are counted
  with builtins and vector operations; a `fold` then walks the hits in order with the keep-mask so far, a few vector operations per hit (`r_kept`). Cost: with both pools full (24 + 50
  hits of 13 words) the largest evaluation takes about 37,700 steps of the 100,000 budget (about 35,000 before the fold; measured 2026-11-15 by bisecting the budget on
  `full_pools_of_long_sentences_stay_inside_the_step_budget`); the pair count grows with the square of the hits, so pools about twice as large would exceed it.
- **No score cut-off**: every hit, best first, `{semtext, start, text, score, sim, overlap}` (`score` the fused one); the readers cut the count.
- The dials are the entity `recall:dials` (`banner_n`, `pool`, `entity_pool_max`, `rrf_k`, `rerank`, `rerank_top`, `fact_chars`, `header`, `weights`, `assoc_n`, `assoc_clip`, `dup_share`; loaded from
  `native/packs/recall-policy.json`, guarded by the host).

## Recall in the banner (#1143)
`Banner:recall` (`membrane/apps/barest.c1f/recall.c1f`, a `RecallPart`) is a banner part: its `words` are the lines recalled for the message a turn is answering, shown after the main banner (the turn's banner is every non-empty `Banner:*`, `Banner:admin:main` first, then the others oldest update first; the banner block and the user's words are two text blocks of one stdin message, `chat_turn.c1f`).
It is a thin reader of `recall` of the ask's body (above): it picks the ask (the oldest queued message), turns the recall on for it, and words the first `banner_n` rows. Each line is clipped to `fact_chars` and says whose words it is (the person's, with the message and its time, or "NOT the person's words" with the source). A turn waits for its ask (`recall_wait`, the part's `ready`) while the ask's embedding call is out (no longer than its 10 s limit) or the window is being read from the store.
Like the main banner the part is the model's: it can reassign `parents` to a class of its own (`Help:banner`).

## Memory: what a note reminds of (#1143)
`Memory:<id>.text = "..."` makes a SemText for the model's own note, mentions it at once, and the write's reply carries `associations`, read from the ONE source getter `SemText.assoc_source`, which is the shared `recall` (a `Memory`'s recall is always on). Its own embedding call (`Fetch:recall:note:<key>`, one entity per note, so a second note written within the first one's 10 s never takes its place) embeds the note's SENTENCES and writes their `Vector`s (`packs/embed.c1f` `embed-ask-vectors`, with `emb_n`), so the note is embedded once: the queue skips a text while its own call is out and then settles the note (`embedded`, its ask retired); the call entity is retired when the vectors are made. Those vectors give the cosine lane its query; the write's reply waits while the call is out (`door_tools.c1f` `mc_w_ready`; at most the call's 10 s limit, then the edge lane alone, and the queue embeds the note later). Tests: `a_notes_own_call_writes_its_sentence_vectors_and_the_queue_sees_it_embedded`, `two_notes_written_quickly_each_have_their_own_embedding_call`. The count and the clip are the dials `assoc_n` (5) and `assoc_clip` (200). See `docs/reply-size.md` and `Help:semtext`.
