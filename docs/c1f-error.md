# c1f `Error`: a value kind for failure, distinct from null

Built (#1143). Tests: `cliffvm:tests/error_1143.rs`, `error_ops_1143.rs`, `error_producers_1143.rs`, `error_store_1143.rs`, `error_infer_1143.rs`, `error_class_1143.rs`. The model-facing page is `Help:c1f` (and "Errors" in `Help:upgrade`).

`null` means not initialised, absent or not applicable; it may carry a reason (`why(x)`). `Error` means something FAILED; it is a value kind that carries its reason (the remedy is part of the text).
`x == None` is False for an Error. `or` stays the default operator and never hides an Error (there is no `??`). `x is Error` tests it, `x is not Error` is accepted, `why(x)` reads its text.

## Reasons are values (cliffvm 0.3.0)
A null's or an Error's reason is a text or an entity. `None('text')` and `Error('text')` make a text; `None(<entity>)` and `Error(<entity>)` keep the entity. `why(x)` returns the reason as it is.
The engine's own reasons are entities of four classes, so a pack matches on the class, never on the text: `Pending` (a foreign read that has not arrived), `Absent` (no such entity or property, or a foreign node the peer says is gone), `Refused` (a peer refused it; its reason is the peer's) and `Collected` (a collected virtual entity).
`why(x) is Refused` tests it (`why(x).cls == 'Refused'` is the same, but the type checker types `why` as text and reports the `.cls`), `str(why(x))` is the message (`refused: <detail>`). An engine reason has no row: only `cls`, `eid` and `text` read on it. Used as text (`str`, `+`, `==` against a text, `startswith`, `len` and the other text builtins) it is its `text`, and a getter that stores `why(x)` stores the text; `in` does not coerce, so write `str(why(x))` there.
An Error's `failed` stores a text reason as text and an entity reason as `{"$ent": id}`. The reactions rows of the door carry `why_cls` beside `why`, and `why_chain` rows `reason_ent` beside `reason`. Tests: `cliffvm:tests/entity_reasons_1143.rs`; in this repo `peers_1143.rs` (`a_reply_cell_the_peer_refuses...`).

## Where an Error comes from
- a failed cast (`Int('a')`: `not-a-type: ...`), `json(text)` of text that is not JSON (`not-json: ...`; a pack that means "not JSON is fine" reads `j = json(x)` then `{} if j is Error else j`);
- a bad entity-literal key (`the key of <entity> is ...`; on the left of a write nothing is created);
- a getter body that raised, and the step budget (`budget exceeded (N steps)`: 100,000 steps by default, `docs/c1f-stdlib.md`, "Limits");
- a timed-out `Command` (`output`, `errors`, `ok`, `rc`: `membrane/packs/proc.c1f`) and a failed `SqlQuery` run (`rows`, `ok`, `rc`, `changes`);
- a pack's own `Error('reason')`.

Still null, not Error: a missing property (`absent: ...`), `.x` of an absent value, `pending: ...` (not answered yet), a collected virtual entity (`collected: nothing held it`), an operation on a null (`f of a null`).
Time never makes an Error: only the step budget does (docs/c1f-stdlib.md, "Time decides no value"). The reasons carry no `error: ` prefix; the kind says it.

## How an Error behaves
- It PROPAGATES: arithmetic, `-x`, `x[i]`, `x.a`, comparisons (but not `x == None`), `not`, `and`, `or`, an `if` / `WHEN` / comprehension condition and every call but `why` give that same Error
  (the first one wins; an Error wins over a plain null). A getter that reads an Error returns it unchanged (no chain; `Why` names the getter). A `WHEN` over an Error does NOT fall through to the next arm.
- `is` has comparison precedence; its right side is a bare name that is not evaluated: a value kind (`Int Float Num Text Bool List Dict Error`) or a class (`x is Task`). `x is (Task, Place)` needs ALL (an entity with both classes in its `parents`);
  `x is (Float, Text)` is a parse error (no value is both). `Num` is a value kind that Int and Float extend (`x is Num` holds for both). The names `Error` and `Num` are reserved (no class, no local).
- Inside one evaluation a list or dict element may be an Error (`[x for x in xs if not (x is Error)]` drops failed items); the container is not itself an Error. Across a store the element is a plain null.
- A setter or constructor that writes an Error is refused with a `refused` receipt and the old value stays. A setter local or FOR list that is an Error stops the setter (a receipt).
- Inference (`cliffvm:src/infer.rs`): Error is a kind, a failure site types as `T | Error`; `membrane/packs/types.json` is the snapshot.

## Stored, and the class `Error`
A derived Error is never stored as a value: the cell is null and the VM keeps the failure beside it. `Error` is a REAL CLASS, owned by the root pack `membrane/packs/entity.c1f`, with one property, `failed` (a dict `{property: reason}`).
While a property of an entity fails, the VM writes (derived writes, writer `error`, never journalled) the reason into `failed[prop]` and puts the class `Error` on TOP of the entity's `parents`;
when every failure clears, the class leaves and `failed` is gone. A read of a listed property is the Error: the getter still recomputes under it, and a success, a redefinition or a pack removal takes the entry out. An arm that loses resolution is never evaluated,
so `failed` names only properties whose CURRENT result is the failure. `x is Error` follows the class list for an entity; `x is (Command, Error)` holds for a failed Command; `instances('Error')` and `entity_query kind:Error` list the failed ones.
`this.cls` skips `Error`, and rule B ignores it. `parents` and `failed` are in the stored row, so a restart and a lazy load bring the Errors back.

Loop safety: the class entry wakes the readers of the class list (`x is Error`, `instances('Error')`) but none of the entity's own getters; an entity's class entry flips at most twice (gain, lose) in one run, and a third flip waits for the end of the run.

`entity_read` and `entity_query` show no value; `run_cliff` answers an Error as `Error: <reason>` (an error result) and a null with a reason as `null: <reason>` (not an error). The JSON routes read named properties and show an Error as null.
The browser page's entity JSON (`cliffvm:src/web.rs`) carries `parents` and `failed`.
