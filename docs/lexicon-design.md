# A lexicon in the graph: synonyms, broader/narrower terms, idioms, base forms (design note, #1143, 2026-11-14; nothing built)

**Important first.** (1) The data is already on this Mac: the Princeton WordNet **3.0** dict files (nltk's copy, 35 MB) in `/tmp/nltkdata/corpora/wordnet`
(wiped at reboot) and a copy in `~/.membrane/wordnet` (34 MB, the files Throwy's `wnq.sh` greps today). WordNet **3.1** is NOT on disk; I recommend 3.0, so
no download is needed. (2) Do not make the lexicon entities in the seat's store: 265k rows would cost ~60 MB of `c_field`, a slower boot and a bigger
journal for a table that never changes. Keep it a **read-only SQLite file** (measured: 20 MB, 206,941 senses, 161,832 relation rows) built offline by a
shell tool from the WordNet files, and read in place. (3) That needs **one new native piece**: a second read-only SQL source and a `lexsql(...)`
builtin (the same piece `docs/migration-design.md` R1 wants for `--legacy`). Everything else (lemmatising, synonym/broader/narrower coverage,
idiom match, Throwy's TOPIC CHECK) is SQL plus getters. No Python at runtime, no network. Every number, threshold and builtin below is a DECISION
(list at the end); I chose none, "recommended" is only my reading.

## (a) The data source
| source | size (measured here) | licence | idioms |
|---|---|---|---|
| WordNet 3.0 dict (`data.*`, `index.*`, `*.exc`) | 117,659 synsets, 147,306 distinct lemmas, 206,978 word-senses, 377,592 pointers; 35 MB raw (10 MB zip) | Princeton, permissive | 64,188 lemmas are multiword (`kick_the_bucket`); 2,829 of them verb phrases |
| WordNet 3.1 | not on disk. A download: `https://wordnetcode.princeton.edu/wn3.1.dict.tar.gz` (from memory, about 12-16 MB: verify URL and size before the operator spends time on it) | same terms (from memory) | same kind |
| Wiktionary idioms | not on disk, not measured | CC BY-SA 4.0 | the best idiom coverage |
| none | 0 | | idioms = only WordNet's multiword lemmas |

**WordNet licence** (the text in `LICENSE` of the copy, 1,625 bytes): "Permission to use, copy, modify and distribute this software and database
and its documentation for any purpose and without fee or royalty is hereby granted, provided that you agree to comply with the following copyright
notice and statements, including the disclaimer, and that the same appear on ALL copies of the software, database and documentation, including
modifications that you make for internal use or for distribution. WordNet 3.0 Copyright 2006 by Princeton University. All rights reserved." plus the
"AS IS" disclaimer and "the name of Princeton University or Princeton may not be used in advertising or publicity". Obligations: ship the full
`LICENSE` next to the built file (a `lexicon.LICENSE` that the build copies, and a `Lexicon:notice` row the seat can show); never use the name in
publicity. Cite: Fellbaum (ed.), *WordNet: An Electronic Lexical Database*, MIT Press, 1998.
**Wiktionary (CC BY-SA 4.0), if ever added:** credit Wiktionary and its editors, link the licence, say what was changed, and **share alike**: the
derived idiom table is itself CC BY-SA. Keep it a SEPARATE file with its own notice so the share-alike scope is that file and not the seat.
Recommendation: WordNet 3.0 only; idioms = its multiword lemmas; Wiktionary later as its own file if a test shows WordNet misses what the operator says.

## (b) The entity model
A WordNet word-sense is `(lemma, synset)`; a synset is a set of lemmas with typed pointers. Measured pointer kinds: hypernym `@` 89,089 (+ `@i` instance
8,577), hyponym `~` 89,089 (+ `~i` 8,577; the mirror of `@`, so it is NOT stored: a `dst` index answers it), derivation `+` 74,717, similar `&` 21,386,
member/part `#m` 12,293, `#p` 9,097, antonym `!` 7,979, others.

**Files, not entities.** The tables (`sense(lemma, syn)`, `rel(syn, kind, dst)`, `exc(form, pos, base)`, `morph(pos, seq, suffix, repl)`), WITHOUT ROWID,
bytes measured with `sense` + `rel` of the kinds `@ @i & #m #p %m %p` + both indexes: **20.3 MB** (34.4 MB with all kinds and plain rowid tables; +14 MB
with glosses, left out). One bulk table read with one indexed lookup per word: no row is a VM entity, so boot, RAM, journal and `warm_rows` do not
change for an idle seat (SQLite maps pages on demand; the first lookup costs page reads, not a load; not timed here).
**Entities born on touch, virtual.** What a pack holds is two VIRTUAL classes (never stored, collected at the end of the round, `docs/cliffvm.md`
storage modes): `Synset:id:<offset><pos>` (`lemmas`, `broader`, `narrower` getters over `lexsql`) and nothing for `Lemma`: a lemma already has an
entity, **`Word:lemma:<lemma>` (link, do not replace)**; add getters to `Word` (`senses`, `syns`, `broader`, `narrower`) that read the file by `this.lemma`.
A multiword lemma is `Word:lemma:kick_the_bucket` (underscores as WordNet writes it; a text's words are joined with `_` before the lookup).
Edges stay in the file; none is written into `Word.mentioned_in` (writing synonym edges would multiply every common word's list: see (d)).
Rejected: lazy stored entities (265k `c_field` rows; the 50,000-row measure in `cliffvm.md` was 6.8 s / 388 MB before lazy load and 0.33 s / 26 MB after,
so ~265k would be ~1.5 s and ~100+ MB noted-but-unloaded: an extrapolation, not a measure); RESIDENT (all of it in RAM).

## (c) How a pack reads it, the smallest way
Today `sql(query, args)` reads only the seat's own store (`native/src/sqlexec.rs`: read-only, `query_only`, ATTACH refused in the writer, async
`Pending`), so the file needs a way in. Options, from least to most native:
1. **No native piece:** a `Command` runs `sqlite3 lexicon.sqlite "..."` (what Throwy does with `wnq.sh`: grep over 15 MB per word, one process per batch).
   Works today, async, but a process per ask and a `sqlite3` binary on PATH. Fine as the stop-gap, not as the SemText path (it would run per mention).
2. **A second read-only SQL source + `lexsql(query, args...)`** (recommended): the boot names the file (`--lexicon <path>`, or a `--source lex=<path>`
   shared with `--legacy`), the host opens it `SQLITE_OPEN_READ_ONLY | immutable=1` (a separate file, never a WAL: the "one SQLite per process" hazard
   of #1111 is about one WAL file opened twice, not two files), and `lexsql` has exactly `sql`'s contract (async `Pending`, cached getter value,
   interrupt, no row cap). No file attached: the getter reads `Error('lexicon: no lexicon attached')`, never hangs. Needs: the executor
   (a copy of `NativeSqlExec` over another path, ~40 lines), the CLI flag, the builtin, and a `Store` handle. One generic form,
   `ro_sql(name, query, args...)` with named sources, would serve the lexicon and `legacy` (migration R1) with one builtin.
3. **Convert on touch** (the migration design's idea): a miss on `Word:lemma:x` copies that word's rows into `c_field`. It only pays when a
   consumer wants a stored fact; the lexicon is read-only and large, so reading in place is smaller. Not recommended.

**The build is an offline command, not a runtime dependency.** `tools/lexicon-build.sh <wordnet-dict-dir> <out.sqlite>`: POSIX `awk` +
the `sqlite3` CLI, the 25-line script I used to take the measures above (parse `data.*` lines: hex word count, then the pointer quadruples; `index.sense`
is not needed; `*.exc` and the morphy rules become `exc` and `morph`; then the two indexes and `VACUUM`; the licence is copied beside it). Seconds, repeatable,
deterministic (sorted inserts), and a test hashes the output of a tiny fixture dict. A Rust `cliffvm lexicon build` is the alternative if the operator wants
no `sqlite3`/`awk` in the toolchain; a Python build script is not allowed. The built file is a download-free artifact the operator makes once per seat
from the WordNet files (kept in `~/.membrane/wordnet`, not `/tmp`), not committed (20 MB): the repo carries the script, the fixture and the notice.

## (d) SemText integration
**One query per text, never one per span.** Each mention today costs no query; a per-span `lexsql` would be up to 400 (`mention_cap`) async reads. So
the lexicon is read in batches off the text's word list:
```c1f
-- the distinct lowercase surfaces of the text, capped as the mention is (`mention_cap` unchanged)
SemText.lex_words getter: head(distinct([lower(w['text']) for w in this.words]), this.mention_cap)
-- surface -> base form, in ONE read (rows {w, base}); morph is data: exceptions first, then the detachment rules in `seq` order, a candidate counts only if it is a lemma
SemText.lex_base getter:
  lexsql("SELECT w.value AS w, COALESCE((SELECT base FROM exc WHERE form=w.value LIMIT 1),
          (SELECT substr(w.value,1,length(w.value)-length(m.suffix))||m.repl FROM morph m
            WHERE w.value LIKE '%'||m.suffix AND EXISTS (SELECT 1 FROM sense s WHERE s.lemma = substr(w.value,1,length(w.value)-length(m.suffix))||m.repl)
            ORDER BY m.seq LIMIT 1), w.value) AS base FROM json_each(?) w", json(this.lex_words))
Span.lemma getter: ([r['base'] for r in prop(this.of, 'lex_base') or [] if r['w'] == lower(this.text)] or [lower(this.text)])[0]   -- no lexicon: the SemPlural crude rule stays the fallback
```
**Base forms (lemmatisation): c1f over data, no Rust.** WordNet's `morphy` is: look the form up in the exception list; else try each detachment rule of
the part of speech in order (noun: `s`->``, `ses`->`s`, `ves`->`f`, `xes`->`x`, `zes`->`z`, `ches`->`ch`, `shes`->`sh`, `men`->`man`, `ies`->`y`; verb: `s`, `ies`->`y`,
`es`->`e`, `es`, `ed`->`e`, `ed`, `ing`->`e`, `ing`; adjective `er`/`est` with and without `e`; from my memory of morphy, to be checked against the
`morph` rows the build writes), and keep the first result that is a lemma. That is the query above: 5,952 exception lines (`*.exc`) + ~25 rule rows. Order matters (`houses`: `s`->`` gives
`house`, a lemma, before `ses`->`s`). It also fixes the live bug of the crude `SemPlural.lemma` (`houses` -> `hous`). The cost: the new lemma changes
the id of NEW `Word:lemma:<x>` entities only (`hous` entities already in a seat stay): a migration question, flagged. Without a lexicon, mentions run as
today (the fallback).
**Mentions keep their rules.** `mention_cap` stays 400 per layer; spans, `mention_table`, `record-mentions` are unchanged; the only edit in
`mentions.c1f` is `Span.lemma` (above), so a mention costs ONE extra async read (a few hundred indexed lookups, not timed here) and its first
round waits for the rows, as the other getters wait for `forms`. The mention does not write synonym edges.
**`related` over synonyms: at ask time, not stored.** `SemText.related_syn` (new getter, dial `syn_cap` like `related_n`): one `lexsql` returns, for the text's
`mentions`, the lemmas of their synsets plus one step up (`@`, `@i`) and down (`~` by the `dst` index), at most `syn_cap` per word, then it reads the
existing `edge_rows` of those `Word` entities (a word nobody has said has no entity and no edges) and ranks as `related` does, each shared entity
`1/count`, scaled by a hop weight (a decision: same synset, broader, narrower each get a number). Cost: one read plus the `edge_rows` of at most
`mention_cap * syn_cap` entities, each already cached; a very common word keeps costing what it costs today.
**Idioms: phrase match over the words layer, in SQL.** The window generation Throwy wrote as nested comprehensions (a step hog, hence its 24-word cap) is
a recursive CTE over the text's word rows; tested on SQLite 3.51 against the lean file: `["he","will","kick","the","bucket","soon"]` returns `kick_the_bucket`:
```sql
WITH w AS (SELECT key i, value t FROM json_each(?1)),
 p(i,j,s) AS (SELECT i,i,t FROM w UNION ALL SELECT p.i, w.i, p.s||'_'||w.t FROM p JOIN w ON w.i=p.j+1 WHERE p.j-p.i < ?2)
SELECT DISTINCT p.s FROM p WHERE p.j>p.i AND EXISTS (SELECT 1 FROM sense WHERE lemma=p.s)
```
Words are the base forms of `lex_base` (`kicks the bucket` -> `kick_the_bucket`); adjacency = the gap between spans is one space (the sentence layer bounds a
window). Data: the multiword lemmas themselves (`lemma LIKE '%\_%'`: 64,188, of which 8,622 have a character outside a-z, 0-9, `_`: punctuation and digits, to
be matched by their word-layer shape). Window length: 1 underscore 54,533, 2 underscores 7,766, 3: 1,454, 4: 298, 5 or more: 137, so 5 words cover 99.9% of lemmas
(`?2` = 4 means a window of 5; a decision). Cost: W windows x 4, an index seek each. A noun compound (`boat house`) matches too: WordNet's multiwords are not only
idioms; the pack that wants idioms only keeps the verb phrases (rows whose synset ends in `v`) or a later Wiktionary file.

## (e) Throwy's TOPIC CHECK as a cliff pack (getters only; sketch)
Throwy's `Pack:semwatch` already has the right decisions in c1f and only the plumbing outside (`Sh:synq` running `wnq.sh`, grep over 15 MB per word). Over
the lexicon it becomes (the Keep text is a SemText `Sw:kb`; its `lex_base` is the keep side; `Watch.last_user_txt` is `Sw:ub`):
```c1f
Watch.cover getter:                      -- rows {w, alts}: each long user word and the lemmas that count as the same thing
  lexsql("WITH u AS (SELECT DISTINCT value w FROM json_each(?1)),
          s AS (SELECT u.w, e.syn FROM u JOIN sense e ON e.lemma=u.w),
          n AS (SELECT w,syn FROM s UNION SELECT s.w, r.dst FROM s JOIN rel r ON r.syn=s.syn AND r.kind IN ('@','@i')
                UNION SELECT s.w, r.syn FROM s JOIN rel r ON r.dst=s.syn AND r.kind IN ('@','@i'))
          SELECT n.w, e.lemma FROM n JOIN sense e ON e.syn=n.syn", json(this.user_bases))
Watch.sem_miss getter: [w for w in this.user_bases if len([r for r in this.cover if r['w'] == w and r['lemma'] in this.keep_bases]) == 0 and not (w in this.keep_bases)]
Watch.sem_idi  getter: lexsql(<the phrase query above>, json(this.user_bases), 4)
Watch.sem_flag getter: 'TOPIC CHECK: user words in no Keep (synonyms/idioms checked): ' + join(head(this.sem_miss, 6), ', ') + '. Keep what matters.' if len(this.sem_miss) >= 2 else ''
```
`user_bases` / `keep_bases` = `Sw:ub.lex_base` / `Sw:kb.lex_base` (`lex_base` returns pairs; the pack reads the base column). Deleted from Throwy's pack: the suffix
loop (`sem_sufs`), `sem_phr` window comprehension, `syn_cmd`, `Sh:synq`, `wnq.sh`, the `sem_stop` list stays or goes by the operator ("nothing is junk").
`boat` covered by `vessel`: the word `vessel` is a `Sw:kb` base; `boat`'s synset 02858304n has `@` -> 04530566n whose lemmas are `vessel, watercraft`.

## (f) Tests and a build plan (red first, each round its own failing test)
Golden lookups (fixture = the real build, and a tiny hand-made 40-row dict for CI so no WordNet is needed): `boat` -> `vessel`, `watercraft` (hypernym),
`vessel` -> `boat` (hyponym); `kicks the bucket` -> `kick_the_bucket`, same synset as `pass_away` (00358431v); `houses` -> `house`, `ran` -> `run`,
`geese` -> `goose` (exception), `studies` -> `study`; no lexicon attached -> `Error`, never a hang; a read-only proof (sha256 of the file unchanged,
a write through `lexsql` refused); the build is reproducible (same fixture, same bytes); `mention` of a 300-word text issues exactly one `lexsql`.
| round | red test, then the work | est. |
|---|---|---|
| L1 | the build script over the tiny fixture: tables, indexes, notice; golden counts | 1 |
| L2 | `--lexicon`/source + `lexsql` (read-only, async, no-file = Error, immutable); decision on `ro_sql` | 1-2 |
| L3 | `exc`/`morph` rows + `lex_base` (golden base forms), `Span.lemma` + fallback; one query per mention | 1-2 |
| L4 | `Word` getters + `Synset` virtual class, golden synonym/broader/narrower | 1 |
| L5 | idiom windows (golden: `kick_the_bucket`; 5-word cap; sentence bound) | 1 |
| L6 | `related_syn` with `syn_cap`; mention cost test | 1 |
| L7 | Throwy's pack rewritten over it (same flags as today on its Keep corpus), delete `wnq.sh` | 1 |
About **7-9 rounds**; L1-L3 carry the risk (the native piece, the changed lemma ids).

## The paraphrase ladder (designed in now; rungs 1-3 are this note, 4-7 later)
Each rung is a layer over the SemText layers (words, sentences) and adds one kind of "these two phrasings mean the same", cheapest first. A rung never
rewrites a lower one: `related` reads them as separate edge kinds with their own weight, so a rung can be switched off or replaced.
1. **Lemma** (morphology rules as data, `lex_base`; fixes the live `houses` -> `hous`). Exact, deterministic.
2. **Sense and synonym:** same synset = synonym; hypernym/hyponym = broader/narrower, weaker. The `Synset` virtual class and `Word` getters of (b).
3. **Phrase / idiom** over the sentence layer: the window match of (d); a phrase lemma and its synset siblings (`kick_the_bucket` ~ `pass_away`).
4. **Weighting by rarity and link type:** extends `related`: today each shared entity counts 1/(entries of its `mentioned_in`); a hop adds a link weight
   (same synset > broader/narrower) times that rarity. Only numbers change; the edge reads stay one `lexsql` plus cached `edge_rows`.
5. **Roles** (predicate-argument per sentence, so "A bought B" matches "B was sold by A"). No layer gives a parse today: the tokenizer stops at spans, WordNet
   has verb frames but no syntax. It would need either a native piece (a small deterministic parser compiled into a core pack, like `spans`, English only, with
   a size and an accuracy we would have to measure on held-out sentences), or the MODEL writing the roles as entities per sentence (`Role:<text>:<n>` with
   predicate, agent, patient, passive flag), as a leg in its own pack. The converse pair (`buy`/`sell`) is a lexicon row, a decision of its own.
6. **Learned edges:** when the model notices two phrasings mean the same it writes an entity of an explicit class owned by a pack, `Paraphrase:<a>:<b>`
   (RESIDENT only if listed whole, else lazy), props `a`, `b` (Word or phrase entities, never copied text), `evidence` (`$ref`s to the two sentences that
   showed it), `by` (writer), `state` (`proposed | kept | retired`). `related` reads kept edges next to the lexicon's, with the weight of a separate kind
   (a decision), through one `instances`-free lookup by the word's `paraphrases` getter. Corrected or retired by a write of `state` (never a delete, the
   evidence stays); a person can retire any. Cost: one stored row per edge (~300 bytes), read at ask time only for the text's own entities. DRIFT RISK: a model-authored
   edge is memory that can be wrong and then reinforces itself (a bad edge widens `related`, which feeds the next evidence). Checks that catch it: an edge counts
   only with evidence in two different texts; the edge is verified on demand against the lexicon (an edge between two words with NO shared synset or common
   hypernym is marked `unsupported`, shown, not hidden); a replay test over the evidence sentences (does the edge still make them `related`); and the
   operator-visible list of `proposed` edges with their evidence.
7. **Embeddings (optional derived layer):** sentence vectors from a local model. Not deterministic across machines or model builds (a replay on another machine
   would differ), so they are stored values with the model name and a hash, never recomputed in a getter. Cost seen in the legacy store: 57 MB of vectors.
   An option behind a dial, not a requirement; rungs 1-6 must work without it.

**Known limits.** Word-sense ambiguity (`bank`: all senses are linked until rung 5/7 or the model chooses one; precision falls on common words, which rung 4's
weighting softens); negation and scope ("did not buy" matches "bought" at every rung up to 4); world knowledge (nothing here knows a boat is a ship in a
given port); **non-English text:** WordNet is English, and its rules and `*.exc` lists are too; a Russian (or any other) lexicon is a separate source with its
own licence, build and morphology, loaded as another named read-only source, never mixed into the English file.

## Decisions for the operator (I chose none; "reco" is my reading)
1. Data: WordNet 3.0 (on disk, no download; reco) or 3.1 (a download, URL and size above, unverified)? Wiktionary idioms: no for now (reco), or later as a separate CC BY-SA file?
2. The file holds only `@ @i & #m #p %m %p` and no glosses (20.3 MB, reco) or all kinds and glosses (48 MB)? Which kinds count as "synonym", "broader", "narrower"?
3. The native piece: a second read-only source with `lexsql` (reco), a generic `ro_sql(name, ...)` shared with `--legacy`, or only the `Command` + `sqlite3` stop-gap? The flag name (`--lexicon`).
4. Builtins added: `lexsql` (or `ro_sql`); none else. Defaults: no lexicon attached = the crude `SemPlural` rule stays (reco).
5. Build tool: `awk` + `sqlite3` script (reco) or Rust `cliffvm lexicon build`; where the built file lives (`<seat>/.membrane/lexicon.sqlite`), and whether the repo ships the licence + fixture only.
6. Lemmatisation: the morphy rules as data (reco) and the CHANGE of new `Word` ids (`hous` -> `house`): accept, with old entities left, or migrate them?
7. Numbers: `mention_cap` 400 (kept), `syn_cap` per word, hop weights (same synset / broader / narrower), idiom window 5 words (4 underscores), the min word length 6 Throwy used, how many senses count per word (Throwy took the first 6 synsets, 16 pointers).
8. Idioms = every multiword lemma, or only verb phrases (2,829) / a Wiktionary list later; are noun compounds to be flagged as idioms?
9. Synonym edges are computed at ask time and never stored in `mentioned_in` (reco), or written at mention time (cost: a common word's list grows by its synonyms)?
10. Licence handling: ship `lexicon.LICENSE` with the file, a `Lexicon:notice` row, the Fellbaum citation: yes?
11. Throwy's interim `wnq.sh` + `~/.membrane/wordnet`: stays until L7, then removed.
12. Roles (rung 5): a native deterministic parser in a core pack (English only), the model writing `Role` entities per sentence, or neither for now? Also converse verb pairs (`buy`/`sell`) as lexicon rows: yes/no.
13. Learned edges (rung 6): the class name and owner pack, the `state` values, the evidence minimum (two texts), the weight of kept edges next to lexicon edges, and which drift checks run (evidence count, lexicon support mark, replay test).
14. Who may retire a learned edge: the writer, any person, or both.
15. Embeddings (rung 7): offered at all? If yes: the dial, stored with model name and hash, and the byte budget (57 MB seen in the legacy store).
16. Non-English: a separate source per language (a Russian lexicon is its own licence, build and morphology), or English only for now.
17. Word-sense ambiguity: link all senses (as in this note) or let the model pick one per word (a write) before edges count.
