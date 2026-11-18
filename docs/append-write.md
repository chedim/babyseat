# An append write for c1f (a proposal, NOT built; no branch carries it)

Nothing here exists in the code: c1f has no append statement (`x.list << element` does not parse). This page records the problem, the one measured site, the options, and the open decisions, so the
question is not asked again from nothing. Recommendation: do not build it yet; the measured value is low.

## The problem
Inside one cascade a (entity, property) is written once; a second write is refused (`loop: (<e>, <p>) already written this round by the field; refused ...`, `cliffvm:src/fields.rs`, `emit_as`).
A write assigns the whole value, so "add to a list" is a read-modify-write (`concat(this.l or [], [x])`) and two producers in one round collide. Driver verbs (`Request.send`, `Command.run`, ...) are exempt
(`is_driver_verb`): a verb is a command, one effect per write, and its slot is consumed.

## Where it shows in the shipped packs
| site | what it does | is a write lost today? |
|---|---|---|
| `door_session.c1f` session bus | three producers (start, chunk, end) each write their OWN virtual `SesBus:<kind>-<id>.out`; `send` is exempt | no: one entity per producer |
| `mentions.c1f` `record-mentions` | `Word:lemma[k].mentioned_in = concat([entries without this text], [new entries])` (same for `Identifier`, `Path`) | latent: two texts that share a word, mentioned in one cascade, make the second write refused. It needs "replace my entries", which an append cannot say |
| `chat_turn.c1f` | the `Chunk:<cmd>:acc` accumulator is a TEXT, one chunk per round | no (a string, one writer) |

So the shipped packs lose no data to this today, except the one latent case, and an append would only fix that one in part.

## The proposal, if it is ever wanted
- Syntax: `x.out << element` (a new statement; `<` `<` is not an operator in expressions). `+=` is rejected (`+` on lists is element-wise vector addition, `docs/c1f-stdlib.md`); a keyword line and a pseudo property were weighed and rejected.
- ONE element per append, list only; several are `FOR f IN frames: x.out << f`. Same-round order is the order the engine already runs the writes (setters top layer first, then load order; the FIFO queue across triggers).
- Layers: appends of every layer are kept (the "first write wins" rule of `docs/c1f-layers.md` would not apply to them).
- An append and a whole-value assignment in one round: refused, both orders. An append to a null starts the list; to a non-list or to a getter-owned property: refused with a receipt; an Error element: refused like any Error write.
- Never journalled (a setter's append is derived and re-derived by a replay); an outside `entity_write` stays a whole-value write. No bound on the list in the language (each pack says its own); the store still rewrites the whole value per round.
- A setter on the list fires once PER APPEND, so a consumer that sends the whole list per firing would resend earlier entries; a consumer of a queue should read a getter (coalesced) or use a driver verb.
- An append in a cycle (a setter on `out` that appends to `out`) would grow the list each pass and escape the loop check, so it would need its own check.

## Alternatives on the same cases
- Driver-verb exemption only (built): enough for the session bus.
- A pack-only outbox (no engine change): one writer entity per producer and a getter that joins them; for `mentioned_in`, one edge entity per (text, word) pair with `mentioned_in` a getter over `instances`.

## Decisions that are open (none is chosen)
1. Whether to build it at all now. 2. The syntax (`<<`, `+=`, a keyword, none). 3. One element per append or a list. 4. Layers: all appends kept, or the first layer wins. 5. An append after an assignment in one round: refused or allowed.
6. An append to a null: starts the list, or refusal. 7. An outside append (an `entity_write` form, a journal line kind): none recommended. 8. A bound on the list: none in the language recommended. 9. Whether the step budget counts the list's length per append: no recommended.
10. How an append loop is stopped. 11. The build size (about five rounds: parser, apply, replay and store, inference and `Why`, docs and one pack site).
