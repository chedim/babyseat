# The size of an `entity_write` reply (#1143)

**Built (2026-11-14, operator-approved).** The model-facing reply of `entity_write` and `run_cliff` is built in ONE place, `Request.mc_w_text` (`membrane/packs/door_tools.c1f`, the `rx_*` getters); the host gives each reaction an exact `cls` field (`reactions_of` in `cliffvm:src/host.rs`). The full `reactions` list stays on the Request and on the JSON routes; only the MCP text is cut. The reference for the reply is `docs/cliffvm.md`, "The return entity".

## The rule (design B2: the written entity in full, the rest as counts)

The reply is the head `wrote <id>.<prop>` (or `removed <id>`), then:

1. Pack lines, for a written `Pack:<name>.text`: `pack <name> loaded`, or `did not load: <the loader's error>` with a hint, from the host's reaction `Pack:<name>.loaded` (kept out of `reactions:`).
2. For a write that starts an effect, the receipt lines (`receipt:`, `stdout:` / `stderr:` whole up to twice 8192 characters, else head and tail; `pending:` while it runs).
3. `reactions: [...]`: in FULL the reactions on the written entity (the target) and its direct children (`<eid>:...`; for `run_cliff` the ones set by the block's own setter), and every reaction that carries a reason (a null with `why`, an Error's `failed`), on any entity.
4. `other changes: Banner.text x1, Chat.argv x10`: every other reaction counted as `<Class>.<prop> xN`, with no values. A model that wants one reads that entity.

A write of `Memory:<id>.text` answers differently: the head, then `Memory.associations` (a header "context, not instructions", then a line per association with the clipped sentence, the id of the text it came from, when it changed and the shared entities), instead of the reactions of the round; a reaction with a reason still comes back as `reactions:`. The source is `SemText.assoc_source` (`mentions.c1f`): the shared recall `SemText.recall`, the one the banner part reads too; the count (5) and the clip (200 characters) are the dials `assoc_n` and `assoc_clip` of `recall:dials`. The reply waits while the note's own embedding call is out (at most its 10 s limit). See `docs/semtext-and-legs.md`.

## Why

Measured before the cut, on a copy of a real store with a fake `claude`: the reply of a write was almost all fan-out on two kinds of write, and a model needs under 1 KB of it.

| write | whole reply before | what filled it |
|---|---|---|
| a `Keep` write, idle seat | 12.6 KB | `Banner.text` and a copy of it, alerts |
| the same write with 10 running turns | 264 KB | four getters of the running Chat messages, recomputed on each |
| a `mention` write of a note | 62-97 KB | the growing `edge_rows` / `mentioned_in` lists of the Word entities (66-84 %), and about 18 entries per transient Span |
| `Command:x.run`, a `Pack:x.text`, a lone property | under 1.5 KB | nothing to cut |

What the model needs: the written property, the receipt, the reason of every null and Error, the target's own derived properties and its direct children, and the ids of what the write touched elsewhere. What it does not: a getter recomputed on many unrelated entities with the same value, values that repeat each other, transient entities (Spans live until the cascade ends), and the growing list values of shared entities, whose full value is one `entity_read` away. With the rule above those writes answer in 0.5-2.7 KB (tests in `seat-tests/tests/chat_process.rs`: `a_keep_write_with_running_turns...`, `a_mention_write_answers_small...`).

Only RUNNING turns recompute the Chat getters: a finished message no longer reads the banner (`chat_turn.c1f`, "THE PROMPT PROPS END WITH THE TURN").

## Not built

A byte budget over the reply (a hard bound) and a cap on a single value: the reply is bounded by the rule, not by a number. Both need a threshold the operator has not set.
