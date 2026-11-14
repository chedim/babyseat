# The size of an `entity_write` reply (#1143)

**Important first.** The reply of an `entity_write` is almost all fan-out on the two writes that hurt (99.9 % for the Keep write with running turns), and a model needs under 1 KB of it. Measured on a copy of Throwy's store (private seat, fake `claude`, packs of `memo-hotfix-0753` at `242948c1`): a Keep write is **12.6 KB** on an idle seat and **264 KB with 10 running turns** (4 getters recomputed on every running Chat message, ~25 KB per message); a `mention` write is **62-97 KB** (the Word entities' growing `edge_rows` / `mentioned_in` lists are 66-84 % of it, not the Spans). Every other write measured is under 1.5 KB. The reply format is built in ONE place, `Request.mc_w_text` (`door_tools.c1f`), so a cut is pack code plus, for the exact class, one host field; the released JSON routes and `tests/specs` are not touched (the `reactions` list reaches the reply only as the MCP text of `entity_write` / `run_cliff`). Every number below (budget, N, value cap) is an operator decision; none is chosen here.

## 1. Measured replies (bytes of the whole tool text)

| write | entries | reply | where it goes |
|---|---|---|---|
| `Keep:x.line` write, idle seat | 8 | 12 613 | Banner.text 6 047, Banner.keeps 5 681 (a copy of the text's keeps), Banner.alert_lines 313, Watch.* 369, Keep.* 170 |
| the same Keep write, 3 running turns | 20 | 87 793 | Chat.argv 20 352, Chat.sys 18 591, Chat.banner_text 18 144, Chat.banner_slot 18 144 (3 messages each), the rest as above |
| the same Keep write, 10 running turns | 48 | **264 467** | Chat.argv 68 111, Chat.sys 62 240, Chat.banner_text 60 750, Chat.banner_slot 60 750 (10 each) = 251 851; Banner 12 044 |
| `Note:body:n2.mention` write (a) | 165 | **96 656** | Word 81 339 (edge_rows 55 659 in 8 entities, mentioned_in 25 680 in 8), Span 14 458 (144 entries, 18 per span, ~100 B), Note 820 |
| `Note:body:n1.mention` write (b) | 215 | 61 992 | Word 40 703 (edge_rows 27 984, mentioned_in 12 719), Span 20 978 (204 entries), Note 272 |
| `Command:x.run = {id, cmd}` (echo) | 13 | 1 473 | all 13 are the Command's own props (receipt 151 B is in the reply head) |
| `Pack:x.text` write | 0 | 34 | nothing |
| lone property `Scratch:lone.n` | 0 | 34 | nothing (first write on a new entity: one `summary` rebuild, 120 B) |
| `Note:n2.say` write | 0 | 31 | nothing |

Top contributors, written as class.prop (entries, bytes): Keep-10-running: Chat.argv (10, 68 111), Chat.sys (10, 62 240), Chat.banner_text (10, 60 750), Chat.banner_slot (10, 60 750), Banner.text (1, 6 049), Banner.keeps (1, 5 682), Banner.alert_lines (1, 313), Watch.bloat_flag (1, 197), Keep.summary (1, 88), Watch.keep_chars (1, 88). Mention (a): Word.edge_rows (8, 55 659), Word.mentioned_in (8, 25 680), then eight Span props of (8, ~800-1 040) each (summary, target, of, parents, sentence_no, forms, f_allcaps, f_capital). The single biggest entry is `Word:lemma:the.edge_rows` at 26.7 KB (one row per text that holds "the": it grows with the store, so the reply of a `mention` is unbounded in the history); `Word:lemma:the.mentioned_in` is 12 KB. The Chat getters are recomputes of one getter on many messages: the same `banner_text` value (6 075 B) 10 times. Only RUNNING turns do it (finished ones are guarded since `chat_turn.c1f` "Finished messages stop reading the banner"); Throwy's ~300 KB is ~11 running or queued messages. The earlier note in `docs/semtext-and-legs.md` ("20 entries per span") is right for Spans (18) but Spans are only 15-34 % of a mention reply.

## 2. What the model needs, and what is noise

Needed: the written property (the reply head `wrote <id>.<prop>`); the receipt (a separate line, already capped); the reason of every null and every Error (`why`); the target entity's own derived props and its direct children (`id == target` or `id` starts with `target:`): the answer to "what did my write do to the thing I wrote"; the ids of entities the write BORN or touched elsewhere (a Word got a `mentioned_in`: the id matters, the 12 KB value does not).
Noise: a getter recomputed on many unrelated entities with the same value (Chat.argv / sys / banner_text / banner_slot x running turns); values that repeat each other (Banner.text and Banner.keeps are the same text); transient entities (Spans live until the cascade ends; the model can never read them); the growing list values of shared entities (Word.edge_rows, Word.mentioned_in), whose full value is one `entity_read` away.
On the 8 writes the model-needed part is 21-900 B; the rest is 0 % (Command, Pack, lone, say) to 99.9 % (Keep, 10 running) of the reply.

## 3. Three reply designs, measured on the same writes

Sizes are the whole reply in bytes, computed offline from the raw replies above (compact JSON; the live line is rendered by `str()` and differs by a few percent; add ~11 B for the line label). Keys: "full" = the entry as today; "written" = the target and its children, and any entry with a `why`.

* A, group by (class, prop): `{class, prop, n, e_g: <one eid>, by}` per group, no values, and nothing is shown in full.
* B, written entities in full + a count of the rest. B1: just `+N more`; B2: the rest grouped as in A (`more: [{class, prop, n, e_g, by}]`).
* C, byte budget: written entries first, then the smallest first, until the budget; then `+N more omitted`. Candidates 2 048 / 4 096 / 8 192.
* D, cap each value (candidate 256 chars: `{"elided_bytes": N}`), everything else as today. E = D on the written entries + B2.

| write | today | A | B1 | B2 | C 2048 | C 4096 | C 8192 | D 256 | E 256 |
|---|---|---|---|---|---|---|---|---|---|
| Keep, idle | 12 613 | 736 | 211 | 770 | 901 | 901 | 6 582 | 1 107 | 770 |
| Keep, 10 running turns | 264 467 | 1 182 | 212 | 1 216 | 902 | 902 | 6 584 | 6 247 | 1 216 |
| Note mention (a) | 96 656 | 2 352 | 869 | 2 732 | 2 038 | 4 066 | 8 236 | 17 985 | 2 732 |
| Note mention (b) | 61 992 | 2 292 | 321 | 2 297 | 2 046 | 4 087 | 8 214 | 22 683 | 2 297 |
| Command run | 1 473 | 1 263 | 1 481 | 1 481 | 1 489 | 1 489 | 1 489 | 1 473 | 1 481 |
| Pack / lone / say | 18-34 | same | same | same | same | same | same | same | same |

What the model loses, and the construct each needs:

* **A** loses every value and the target-first view (even the written entity's `summary` becomes a count); it keeps "something recomputed 10 Chat.argv". Smaller than B for nothing the model reads. Needs `group_by` (exists, used by `mentions.c1f`), a class per entity: the eid prefix gives it (`mc_w_cls` does it for the target) but a derived class is exact only from the host (`reactions_of` knows `doc.cls`): one extra field `cls` per reaction, a Rust change, no new builtin. A cap on the example count per group is a decision.
* **B2** (the one that fits the doctrine "a write answers with what it caused": the thing written in full, the world as counts) loses the VALUE of reactions on other entities (it keeps their class, prop, count and one id). A model that wants one reads that entity. Needs a "starts with target + ':'" test (`startswith` exists), `group_by`, `len`; pack code in `mc_w_text`, no new builtin. B1 is B2 without the grouped line: smaller, but the model cannot tell a Banner change from a Word change; B2 costs ~0.5-1.4 KB more on the 4 fan-out writes.
* **C** keeps the most numerous small facts and is the only one with a hard bound (budget + ~100 B), but a cut by size drops arbitrary entries (a `why` is kept only because the written ones go first) and needs a running byte sum: not expressible as one c1f getter today (no locals, no fold), so it is a host cut in `reactions_of` (Rust) or a new construct. Its "read via `entity_query` ..." hint line is free text.
* **D/E**: D alone does not fix it (a 6 KB `Chat.argv` is cut but 40 Chat entries stay, 6 247 B; mention stays 18-23 KB because the Span/Word entries are many and small). Only worth it combined (E = B2 with values capped), which on these writes equals B2.

Releases and fixtures: `reactions` is a derived write on `Request` and is only read by `mc_w_text` (entity_write) and `cl_*` (run_cliff). The `/membrane/*` JSON routes and `tests/specs/door_tools.json` (which feeds `reactions: []` as an INPUT) do not see it. The tests that read the `reactions:` line as a JSON array (`chat_process.rs`: `a_write_returns_the_reactions...`, `a_clifflet_that_writes...`, `reactions_list_only_real_changes...`, `a_banner_change_returns_reactions...`) would change with any design that changes the shape; the three help pages (`help/c1f.md`, `help/packs.md`, `help/cliff.md`) and `docs/cliffvm.md` describe `reactions` and need the same edit. Today's shape is one JSON array on one line.

## 4. Open decisions (no number chosen)

1. Design: A, B1, B2 or C (or B2 under a byte budget as a backstop). Measured: B2 is 0.8-2.7 KB on the writes that hurt (today 12-265 KB); C 4 096 is bounded at ~4 KB.
2. If a budget or a cap: candidates 2 048 / 4 096 / 8 192 bytes for the budget, 256 for the value cap; and N examples per group (1 shown).
3. Does the model need `by` (the setter or getter that wrote it) on grouped lines? It is 10-30 B per line.
4. Treat `Banner.keeps` (same text as `Banner.text`) and the Word lists as noise by rule, or only by the written-entity test?
5. The class: eid prefix (pack-only, right for every class here) or an added `cls` field from the host (exact; Rust change).
