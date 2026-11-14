# An append write for c1f (design note, #1143)

**Important first.** Recommendation: build it small, or not at all yet. The measured value is low today: in the shipped packs there is NO site
where two producers lose a list entry (the one that did, the session bus, is fixed by one bus entity per producer plus the driver-verb
exemption). The append would replace 4 read-modify-write lines (`decisions.c1f` logs) and would fix one latent loss (`mentions.c1f`
`mentioned_in`, two texts naming one word in one cascade) only in part. It does NOT help the bus's consumer, because a setter on the list fires once per
append. If the operator wants it anyway: `x.list << element`, ONE element per append, list-only, same-round order = the order setters already run,
getters see the list as of the round start, whole-value assignment in the same round refused, never journalled (derived writes are re-derived).
Estimate 5 rounds. Every number, bound, syntax and default below is a DECISION for the operator (list at the end); none is chosen here.

## (a) The problem, and where a workaround exists today
Engine rule (`fields.rs` `emit_as`): inside one cascade a (entity, property) is written once; the second write is refused (`loop: (<e>, <p>) already
written this round`, writer `field:loop-refused`). A write assigns the whole value, so "add to a list" is a read-modify-write
(`concat(this.l or [], [x])`) and two producers in one round collide. Driver verbs (`Request.send`, `Command.run`) are exempt (`is_driver_verb`): a verb is a
command, one effect per write.

Sites in the shipped packs (grep `loop:`, `same round`, `concat(this.<p>`, per-producer entities, `or []`):

| site | what it does today | is a write lost today? |
|---|---|---|
| `door_session.c1f` session bus | 3 producers (start, chunk, end) each write their OWN `SesBus:<kind>-<id>.out`; `send` is exempt | no (fixed by w72); one entity per producer, ~3 entity kinds |
| `decisions.c1f` 83/135/151/166 | `answer_log`, `drop_log` = `concat(this.x or [], [row])`; the two writers per log are exclusive by `WHEN` | no; the append would only shorten the line |
| `mentions.c1f` 118/122/126 | `Word|Identifier|Path:..[k].mentioned_in = concat([old entries minus this text], [new entries])` | yes, latent: two texts mentioned in one cascade that share a word: the second write is refused. Needs "replace my entries", not only append |
| `chat_turn.c1f` 147-150 | the `Chunk:<cmd>:acc` text/tools: a STRING accumulator, one chunk per round | no (one chunk per round); a string is not a list, `<<` does not apply |
| `chat_oai.c1f` 166-169 | `ids`/`names` = previous round's list + this round's, as getters over `prev` | no; a getter chain is the single writer by construction |
| `chat.c1f` 60 | `state` is written once per step; a constructor's first value and the start's write would collide | a comment, a workaround, not a list |
| `mentions.c1f` 105 | `mentioned` written once, by `record-mentions` only | a workaround, not a list |

So the construct is general (logs, queues, outboxes, collected results), but the shipped packs use it in 0 places that lose data today and 1 that may.

## (b) Syntax candidates
The statement parser (`syntax.rs` `statement`) takes `<target>.<prop> = <expr>`; `assign_eq` finds the `=`; nothing else is a statement.
1. `x.out << frame` (Ruby/C++ append). New statement form, no `=`; `<<` is not an operator in expressions (no shifts: `expr.rs` has `** //` only), so it cannot
   be misread inside a value. Parses next to writes as a third statement shape: `(target, prop, APPEND, expr)` beside `(target, prop, expr)`.
2. `x.out += [frame]`. Rejected: in c1f `+` on lists is element-wise vector addition (`[1] + [2]` is `[3.0]`, docs/c1f-stdlib.md "Lists are vectors"), so `+=` would
   read as arithmetic and append as a second meaning of one symbol. Against "no magic".
3. `append x.out frame` (keyword line). Explicit, but a new reserved word and a second line shape; wordier in a FOR body.
4. `x.out.append = frame` (a pseudo property). Parses with zero grammar change, but a property named `append` becomes unwritable and `.append` reads like a method; the
   entity-literal path (`Chat:message[id]:turn`) already uses `.` for properties. Rejected.

Recommendation: 1. ONE element per append (a list element is appended as one element, `x.out << [1, 2]` adds the list `[1, 2]`); several elements are
`FOR f IN frames: x.out << f` (existing). One way to say it. In a FOR the appends are ordered by the loop. DECISION D1 (syntax), D2 (one element only).

## (c) Semantics (each is a recommendation)
- **Order inside a round.** Appends are applied at emit time, in the order the engine runs the writes: within a setter, write order; across the setters of one trigger, the
  order they already run (docs/c1f-layers.md: top layer first, then stacked setters in load order); across triggers, the FIFO work queue. So the list is the
  appends in the order they were made, and a replay makes the same order (determinism_1143.rs pins the setter order).
- **Layers.** The "first write wins, the lower layer's line is skipped" rule (`claimed` in `run_writes`) must NOT apply to appends: two packs appending is the point.
  A same-name setter in an upper layer still replaces the lower one (unchanged); the stacked setters all append. In-layer double appends are allowed (the loop check is not taken). D3.
- **What a getter sees mid-round.** Getters recompute after the work queue empties, so a getter over the list sees the list after ALL the round's appends (once, not per
  append). A setter or block reading `this.out` plainly sees the appends made so far (plain writes are visible at once: `docs/cliffvm.md`; an on-setter sees GETTERS as of before
  the write). That is the same rule as today, no new one.
- **Append + assignment in one round.** Refused, both orders: the later of the two is a receipt `refused: <e>.<p> was appended in this round; a whole-value write would drop it`
  (an assignment-then-append is allowed only if D4 says so; recommended refused too, symmetric, simplest to explain). Assignment alone keeps the existing single-write rule.
- **Non-list or None.** `None`/absent: the list starts as `[element]` (a log needs no `or []` seed; D5). A present non-list (text, number, dict): refused with a receipt
  `refused: <e>.<p> is <type>, not a list` (a setter that writes an Error is already refused with a receipt; there is no new Error kind). An element that is an `Error`: refused like any
  Error write (docs/c1f-error.md), the list is unchanged.
- **On a property whose getter failed (`Error` class).** The list lives in a plain property; a property with a getter is a derived value and is written by the getter, so an
  append to it is refused ("`<p>` is a getter"), as an assignment to a getter's property already is by first-write rules (verify in the tests). A failed getter's null is not a list: same refusal. D6.
- **Typing (`infer.rs`).** The union of types WRITTEN to `Class.prop` gets `List(T)` where T is the element's inferred type; two appends unify like two assignments (`List(T1|T2)`);
  an append to a property that is also assigned a non-list is a finding (certain: the append would be refused).
- **`Why`.** `why(Class.prop)` lists the writer as `<setter> (append)` and each append of the round in order; a refused one reads `refused: ...` as today. The `reactions` batch (host.rs
  `drain_apply`) keeps "the last value per property": it reports the whole list once.

## (d) Replay and the journal
The journal holds OUTSIDE writes only; derived writes (a setter's) are never in it and a replay re-derives them (docs/cliffvm.md "OUTSIDE writes"). So:
- A setter append is derived: not journalled, re-derived by replay from the same outside writes in the same order. Replay ends in the same state.
- An outside write stays a whole-value write (`entity_write` has no append): the journalled value is the full list, nothing to merge. An outside append is NOT proposed (D7): it would need a
  journalled "append" line kind and an order against concurrent outside writes. The model that wants to add to a list reads, adds, writes, as today.
- Skip-unchanged (`emit_as` returns when the new value equals the stored one; `store_prop` skips an equal value): an append always changes the list (it grows), so no skip. An append of nothing
  is not a statement (no zero-element form).
- The store: a property is ONE value in the row (`store_prop`), so each round's write rewrites the whole list (O(n) per round; O(n^2) over a log of n entries). That cost is already paid by
  `answer_log` today. A bound is a policy (keep the last N), not a mechanism; recommended NO default bound in the language, and the pack says its own (`head`/`tail` on a getter, or a retire).
  D8: bound or none; if a bound, its number and which end drops.
- Storage modes (virtual/resident): the append follows the entity's class. A VIRTUAL entity's list is RAM only and is gone with the entity (collected at the round's end when nothing holds it;
  the session bus entities are virtual). RESIDENT/lazy: the list is in the row like any property.
- One more replay risk: an append inside `FOR x IN instances('C')` is ordered by `instances`; the test must pin that order (determinism).

## (e) Cost to the evaluator and to the host
- No extra round: an append applies at emit time like an assignment; getters recompute once after the queue empties; the cascade merges nothing, it applies in order.
- Per append: one clone of the list (`Write{old,new}` carry whole values): O(n). A k-append round costs O(k*n) unless the write carries the delta (an in-place push; an implementation detail, measure first).
  The step budget counts the right-hand side only; the append itself is one step (the same as a write). D9: whether the append counts the list's length against the budget (recommended no: "a native's own cost is not counted").
- The setters on the list fire once PER APPEND (each is a Write in the queue). A consumer that sends the whole list per firing (`SesBus.out on-setter fan-out` does `for f in this.out`) would
  resend earlier frames after the second append. Coalesced firing (once per round, after the queue drains) is possible (the engine has a `late` queue) but is a new rule. Recommended: per append, no
  coalescing; a consumer of a queue should read getters (they are coalesced) or use a driver verb.
- **Driver verbs stay exempt from the loop check; the append does not change that.** A verb is a command: each write is one effect run in order and the slot is consumed at once, so there is no state
  to merge. An append is state: it merges into one value, and two appends are only both kept if the engine applies them in order. Making verbs "append to a queue" would add the journal and
  store cost of a list to what is today a consumed slot, and would turn `send` into state. Keep both: verbs for effects, `<<` for state.

## (f) Alternatives on the same three cases
A = driver-verb exemption only (built). B = a pack-only outbox: one writer entity per producer, a getter joins them. C = this append write.

| case | A (built) | B (pack outbox) | C (append) |
|---|---|---|---|
| session bus | works: `send` is exempt; 3 producer entities + the fan-out | what is shipped: 3 bus entities, a joined stream | producers append to ONE `SesBus:main.out`; the fan-out fires per append and must send only the new frame: needs an `added` read or coalesced firing (a new rule). Net: no gain, more engine |
| mention log (`mentioned_in`) | no help (not a verb) | one `Mention:<text>:<word>` edge entity per pair, `mentioned_in` a getter over `instances` (a scan per word, or an index): loss-free, more entities (one per pair; cost: an entity per mention) | `Word:lemma[k].mentioned_in << {'text': ..}` is loss-free for a FIRST mention; a re-mention (replace my entries) is a removal, which append cannot say: the line stays a rebuild and keeps the hole |
| Command/Chunk accumulator | no help | the current `:acc` string, one writer | a list of chunks: `Chunk:<cmd>:acc.parts << piece`, a getter joins; one writer today, so no loss to fix; the list grows with the turn as the string does. No gain |
| cost | 0 | 0 rounds of build; entities/getters per site | ~5 rounds; engine rule + tests + doc + types.json churn |
| risk | none | scan cost, more entities | the consumer-firing question, the replace/dedupe gap |

C wins only where producers are many, the list is read by a getter, and entries are added never removed (logs, collected results, an outbox read by a getter). A shipped pack does not need it today.

## (g) Tests (red first, one round each) and the build plan
1. Parse (`syntax_994`-style): `x.out << e` parses to the append shape; `<<` in an expression, `+=`, `x.out <<` with no value, two `<<` on one line are errors with a line number.
2. Order: two setters (two layers) and a FOR append to one list in one round: the list is in the documented order; a lower layer's append is not skipped; an in-layer double append works.
3. Refusals: append then assignment and assignment then append in one round (receipt, old value kept); append to a text / a dict / an Error element / a getter's property.
4. Replay (`host_loop`-style): a live run with two producers, replayed from the journal into a fresh engine: the same lists, the same journal; an append to a virtual entity leaves no row and no line.
5. Typing and `Why`: `List(T)` unions, the finding for a non-list assign, the `Why` text; the pack snapshots (`types.json`, `surface.json`) regenerate in one commit.
Plan (estimate, rounds): R1 parser + red tests; R2 `run_writes` apply (order, layers, refusals); R3 replay + store + virtual; R4 `infer.rs` + `Why`; R5 docs (c1f-stdlib, cliffvm, Help) and one
pack site (the `decisions.c1f` logs, to prove it). 5 rounds; 4 if no pack site is converted. D10: the number of rounds and whether a pack site goes in the same change.

## (h) Risks
- The consumer: a setter on the list fires per append (see (e)); a design that adds appends without saying how a consumer sees "only the new ones" invites the old bug in a new place.
- Unbounded growth: `answer_log` is already unbounded; every log written with `<<` makes it easy to add one more; the store rewrites the whole value per round.
- A gap: replace-by-key (`mentioned_in`) and removal are not appends; the temptation is an `upsert`/`remove` family (not proposed; "no inventions").
- Second meaning of the loop rule: the loop check protects against cycles. An append in a cycle (a setter on `out` that appends to `out`) would grow the list each pass; the engine's loop check would
  not fire (appends are exempt). Recommended: an append is checked against the cascade's trigger: an append whose target is the property that started the chain, or one already appended by the same
  setter on the same (entity, property) for the same trigger write, is refused. D11.
- Determinism: replay order depends on setter order and `instances` order; both are pinned by tests, a new pack that appends from a FOR over a dict-keyed set must be checked.
- Layers: a lower layer cannot suppress an upper layer's appends (today a top layer's write overrides). A quiet merge where a pack used to override is a behaviour change; `Why` must show it.

## Decisions for the operator (nothing is chosen)
1. D1 syntax: `x.out << e` (recommended), `+=`, `append x.out e`, or none yet.
2. D2 one element per append (recommended) or a list of elements.
3. D3 layers: appends of several layers all kept, in layer order (recommended), or first layer wins as for assignments.
4. D4 append after an assignment in one round: refused (recommended) or allowed.
5. D5 append to None/absent starts the list (recommended) or is a refusal.
6. D6 append to a getter-owned property: refused (recommended).
7. D7 outside append (an `entity_write` form, a journal line kind): none (recommended).
8. D8 a bound on a list's length, its number and which end drops: none in the language (recommended), each pack's own policy.
9. D9 the step budget counts the list length per append: no (recommended).
10. D10 build: 5 rounds, with one pack site (`decisions.c1f` logs) in the same change, or 4 without.
11. D11 how an append loop (a setter that appends to the list it fires on) is stopped: the rule in (h) or a count cap (a number the operator sets).
12. D12 whether to build at all now, given (a): the shipped packs lose no data today; the alternative is B for `mentioned_in` only (an edge entity per pair), which needs no engine change.
