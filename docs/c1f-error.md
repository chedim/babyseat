# c1f `Error`: a value kind for failure, distinct from null (design note, #1143)

**STATUS (2026-11-13): BUILT, in six rounds (branch w39-error).** The note below is the design; where the build differs, this block says so. Built exactly as the
note says, with the operator's decisions of 2026-11-13: `V::Error`, `is` / `is not` (class lists follow `parents`), the operator table, derived Errors stored as
null with the kind in `Core.failed` beside `Core.why`, a setter writing an Error refused with a `refused` receipt, `Error('reason')`, the reserved name `Error`,
`why(x)` reading an Error, inference as a kind (`T|Error`), one `types.json` regenerate commit. Differences and open points:
(1) the Error carries its reason only (no separate `source` field: a getter that reads an Error returns it unchanged, and `Why` names the getter);
(2) the `error: ` prefix is gone from failure reasons (`budget exceeded (N steps)`, `not-a-type: ...`, the key errors): the kind says it;
(3) `json(text)` of bad text is now an Error (`not-json: ...`, follow-up 2026-11-13; the ~17 pack lines that read `json(x) or {}` / `json(x) != None` as "not JSON is fine" read a local and test `is Error`; the door answers a malformed body as before); refused
writes stay `refused` receipt TEXT (they are not values); `run_cliff` shows a null with a reason as `null: <reason>` (not an error) and an Error as `Error: <reason>` (an error);
(4) the cast union `Int(v) or Text(v)` no longer works (a failed cast is an Error and `or` never hides it): it is written `v if v is Int or v is Text else ...`;
(5) `Num` is now a value kind that Int and Float extend (follow-up 2026-11-13): `x is Num` is true for an Int or a Float, `x is (Int, Num)` holds for an Int, `x is (Float, Text)` stays a parse error; `Num` is reserved like `Error`;
(6) a setter LOCAL or FOR list that is an Error stops the setter (a receipt), because a setter local holds JSON.

**Important first (the original note).** This is a design note, no code. The kind is decided by the operator (2026-11-13): null = absent / not applicable (keeps its
`why` as a note, `x == None` and `or` work as today); `Error` = something failed, carries reason + remedy, `x == None` is FALSE for it, it
PROPAGATES through getters unchanged (no "via" chain), and the check is the operator `is`. Old JSON routes and `tests/golden/` stay
byte-identical (an Error serialises as null there); only `entity_read` and `run_cliff` show the kind. Three things in the brief do not
match the tree and need your call first: (1) c1f has NO `??` today (the default is `or`, which also fires on False/0/empty; the null-only
default is `x if x != None else d`, `docs/c1f-stdlib.md:43`); (2) `is` is currently a parse error (`native/src/expr.rs:414`, listed as
dropped at `docs/c1f-stdlib.md:118`), so this note adds it; (3) `x is <Class>` is legal for entities (any class name; `Trip` was the example then; 2026-11-13 the Trip class is gone). Decisions that need numbers/names/defaults are in the last section; none is chosen here.

## Today: one kind, `V::Nil(reason)`

A value is `V::None` (no reason) or `V::Nil(String)` (a reason) (`expr.rs:147`). A stored getter value is plain JSON null; the reason lives
in a side map `core.why[(eid, prop)]` (`vm.rs:199`) and a read gives `Nil` back (`vm.rs:289`). `x == None`, `truthy`, `py_eq`, `nullish`
treat both alike (`expr.rs:673-704`); `to_json` drops the reason (`expr.rs:1314`); `why(x)` returns the text (`expr.rs:841`). Failure and
absence are told apart only by the reason's PREFIX (`error: ...`), e.g. `door_tools.c1f:163` does `startswith(why, 'error')`. That is the gap.

## (a) Where reasoned nulls come from: failure vs absent

**FAILURE (become Error; recommended):**

| place | today |
|---|---|
| getter body raised (bad operand, no attribute, sql/read/stat driver `Err`) `fields.rs:827-833` | null, `error: <e>` + a receipt |
| step budget `budget exceeded (N steps)` `expr.rs:23,118`, via 827-833 | null |
| (REMOVED 2026-11-13: a self-time kill `timeout N ms (allowance M ms)` and its Trip entity. Time decides no value; the step budget is the one limit that gives an Error, docs/c1f-stdlib.md) | |
| `Command` timed-out run: `output errors ok rc` `membrane/packs/proc.c1f:20-31` (`None(this.timeout_why)`) | null, remedy text |
| failed cast `Int('a')` -> `not-a-type: ...` `expr.rs:836` | null |
| bad constructor key `error: the key of ... ` `expr.rs:1209-1212` | null (and the write creates nothing) |
| operation that failed BECAUSE of a null `error: f of a null` `expr.rs:1288` | null (this one stays null: see below) |
| failed parse: `json(text)` on bad text returns plain `None` `expr.rs:1035` | null, no reason at all |
| refused write: receipt `refused` `fields.rs:1122-1125` (setter condition error), loop refusal `1134-1137`, VM-owned props `1402,1406`; driver refusals `adapters.rs:51-65`, `drivers.rs:40,100`, `fields.rs:1529`, pinned unload `host.rs:561` | a `refused` property / receipt text, not a value |
| door text: `refused: ...` `door_tools.c1f:117`, `has no <prop>` `197`, the `err` flag `163`, `mc_is_err` `234` | text, sniffed by prefix |

**TRUE absent (stay null):** missing property (`vm.rs:289`), `.x` of an absent value `expr.rs:1165`, absent operand `expr.rs:678`,
`pending: sql/read/stat/jsonl(...)` `vm.rs:798,823,840,857` (not yet answered, not failed), `None('waiting for a price')` written by a pack as
a note, an out-of-range index / missing dict key `expr.rs:1236-1240`. **Judgment calls:** collected virtual entity read `collected: nothing
held it` (`vm.rs:157,276`): the entity is gone, but nothing failed; recommend null (absent), it is the same as reading a missing entity.
An operation on a null that cannot be done (`1288`): recommend null with the reason (the failure belongs to the earlier absence).

Cost: about 12 constructor sites move from `V::Nil(format!("error: ..."))` to `V::Err(reason, remedy)`; the `error:` prefix stops being the
signal, so `door_tools.c1f:163` and `:234` lose their `startswith` sniff (a gain).

## The value

`V::Err { reason: String, source: String }` (new variant; `source` = the `Entity.prop` where it began, so the reason "names the source";
the remedy is part of the reason text, as in `proc.c1f:20`). Propagation: a getter that reads an Error and does not catch it returns the
SAME Error value (reason and source unchanged, no chain), same shape as `ev_attr` passing `Nil` on (`expr.rs:1164`). `why(x)` returns the
reason text for an Error as for a null (recommended, so every `why` user keeps working); `x is Error` is the test.

## (b) Operators on an Error, and containers

Rule of thumb: **an Error is contagious everywhere a value is USED, and never hidden by a default.** Recommended:

- arithmetic, `-x`, `x[i]`, `x.a`, calls (`ev_call` `expr.rs:1288`), string `+`: result is that Error (today `null_of` returns the first `Nil`;
  Error wins over a plain null when both are operands, the first Error wins among several).
- comparison `==`, `!=`, `<`...: an Error operand makes the result the Error, not a Bool (so `x == 3` over a timed-out `x` is not quietly False).
  `x == None` is the exception: it is the false-for-Error test the operator asked for (`py_eq(Err, None)` = False), and so is `x != None`.
- `not x`, `and`, `or`: `or` is the c1f default operator. Recommend `x or d` over an Error is the Error (a default must not hide a
  failure); `and` short-circuits on the Error the same way (returns it, like the first falsy operand it returns today, `expr.rs:1153`).
  The escape for "I want the default anyway" is `d if not (x is Error) and x != None else ...` written out: verbose on purpose.
  DECISION below on `or` vs a new `??`.
- `if`/`WHEN` condition over an Error: recommend the WHOLE getter is that Error (the arm cannot be chosen: a false would silently pick
  `ELSE`, which is exactly the hiding the operator forbids). A WHEN condition that tests `x is Error` is no problem: `is` is the only
  operator that reads an Error as a value without propagating (together with `x == None`, `why(x)`).
- `truthy(Err)`: only reachable through the cases above; defined as an Error propagation, never a Bool.
- `len`, `join`, `sort_by`, `pluck` etc. over an Error argument (today `Nil` counts as an empty list, `expr.rs:849-971`): recommend the Error
  propagates; over a plain null they stay empty (as today, #1065).
- **Containers.** A list element or dict value that is an Error stays an Error in memory (`V::List` holds `V` values). Today the reason of a
  `Nil` in a list is lost as soon as the value is stored (`to_json` -> `null`, `expr.rs:1314`; `why` is keyed by (eid, prop) only, `vm.rs:199`): that is
  the open reasoned-null-in-list bug. Recommend: inside one evaluation the element keeps the Error (`l[2] is Error`, `why(l[2])` work);
  a container that holds an Error is NOT itself an Error (the other elements are fine); a STORED container serialises the element as null
  (the old-routes rule), so across a store the element is a plain null. The reason survives only if the getter value is kept live; to
  keep it for a stored list, a side map keyed by (eid, prop, path) is the fix and is a DECISION (cost: memory, and a path key into `why`).
  `[x for x in xs if not (x is Error)]` is the idiom to drop failed items.

## (c) Storage

A derived Error is NEVER stored: the stored cell is JSON null (as now) and `core.why` gains a kind (`why: (eid, prop) -> (Null|Error, text)`,
`vm.rs:199`, written at `fields.rs:757-760,818-832`) so a read gives `Err` back (`vm.rs:289`) and a null stays a null. (The Trip path that rebuilt an Error from a Trip entity is removed, 2026-11-13.) A SETTER that writes an Error (`X.p = <Error value>`, or an `entity_write`
whose value expression is one): the write is REFUSED, nothing is stored, the old value stays; the setter's receipt `refused` (the same
property as `fields.rs:1125`) carries `refused: <reason> (an Error is never stored; ...)`; the door reply of `entity_write` is
`refused: ...` (`door_tools.c1f:117`). Alternative: store as null with `why` (what a null write does now). Recommend refuse: a refused
write is loud, a stored-null write is exactly the hidden failure. The effect-receipt rows (`Command.run_result`) are outside values and
stay as they are.

## (d) Type inference, surface and types snapshots

`infer.rs`: `Kind` (`infer.rs:22`) gets `Error`; `Ty` keeps `null: bool`, so Error is a kind, not a flag, and stays OUT of `null_if`.
Rules: any operation on a value that may be an Error may be an Error (`Text? ` + 1 is `Num? | Error`); `x is Error` narrows (`Bool`);
the shipped failure sites type as `T | Error` (e.g. `Command.output` is `Text? | Error`); `x or d` keeps Error in its type (matches the
runtime rule above). `why` stays `Text?`. `Int(x)` is `Int | Error` when `x` is not already Int. This is the larger change: every nullable
getter type that includes a failure site changes, so `membrane/packs/types.json` (`docs/c1f-stdlib.md:219`) changes broadly in one
regenerate; reviewing the diff is the cost. `surface.json` (a pack's imports/classes/writes) is not affected: Error adds no external
touch; a pack that reads `is Error` is not a surface item.

## (e) The wasm VM and `fieldvm.js`

The wasm build is the same Rust (`expr.rs`), so `V::Err`, `is` and the infer change come with it. `membrane/ui/fieldvm.js` exchanges values as
JSON (`f.write(eid, k, JSON.stringify(v ?? null), ...)`, lines 54, 66, 235) and reads text/attrs where `v == null` means empty (`131-134`):
an Error is a null there, so the page keeps working and hides the failure (consistent with the old-routes rule). Page-side change:
none required; optional: the page's `Error`-bearing UI entities could show `why`. Needs the wasm package rebuilt, nothing in the JS file.

## (f) Migration

Packs that rely on failure = null: (1) `proc.c1f:20-31` become `Error(...)`-valued (the constructor, DECISION on its name: `None('..')`
makes a null today); `door_tools.c1f:112` (`status == 'timeout'` text) and `163,197,234` drop the `startswith('error')` sniff for `is Error`.
(2) Every pack that does `x or default` on a failure site now gets the Error (not the default): grep `or` over `Command.*`, casts and
reads; each is a one-line decision (`x if x != None ...` is the old behaviour for nulls). (3) Tests that assert a failure is null /
`why` starts with `error:`: `native/tests/*` for budget, key errors, `cliffvm.md` examples pinned by `help_1143.rs`
(every `Help:` example) and the docs examples; golden fixtures must not change (a unit check: each golden route through an Error site
returns null). A grep of `'error'`, `startswith(`, `why(`, `timed out` over packs and tests enumerates them. Order of risk: packs
first (behaviour), then help pages (text), then types.json (data).

## (g) The `is` grammar

`comp_op` (`expr.rs:406-419`) gets `is` as a comparison operator with a name or a parenthesised name list on the right:
`x is Error`, `x is Task`, `x is (Task, Place)` (ALL must match: an entity that has both classes in its `parents`). Value kinds are the
`kind_of` names (`expr.rs:1097`: `int float text bool list dict`, entity) plus `Error`; the right side is written as a bare name, in
its own spelling (`Error`, a class name, and for values `Int Float Text Bool List Dict`, the same spellings the casts use, `expr.rs:347`).
`x is A or x is B` is the "any" form, no new syntax. An entity is `Class`-matched by `parents`, nearest first, root `Entity`
(`vm.rs:81-85,942` `in_classes`/`is_a`); a non-entity matched against a class is False. Null: `x == None`, no `is Null`. Interaction:
`is` has comparison precedence, so `a == b is Error` chains like Python comparisons: recommend it is a parse error to mix `is` into a
comparison chain (`not x is Error` reads as `not (x is Error)` since `not` is below comparison). The right-side name is NOT evaluated
(it is not a local or property), so a local called `Error` is not shadowed; a class literally called `Error` conflicts with the kind:
recommend reserving `Error` (no pack defines one today; grep found none). `is not` is a decision. Removing `is` from the dropped-operator
list at `docs/c1f-stdlib.md:118` and the `Is` parse error are part of the build.

## (h) Build plan: red test first, about 6 rounds

1. RED: `is` grammar tests (parse, kinds, class list, tuple-all, errors) then `V::Err` variant, `kind_of`, `why`, `to_json` -> null
   (the golden-safe rule), `py_eq(Err, None)` false. No producers yet.
2. RED: operator table (b): arithmetic, compare, `or`/`and`/`not`, calls, index, WHEN/if, containers in one evaluation; GREEN in `expr.rs`.
3. RED: storage (c): derived Error not stored, `why` carries the kind, getter propagation through a getter chain with the source named,
   a setter writing an Error is refused (receipt + door reply); GREEN in `fields.rs`/`vm.rs`.
4. RED: producers (a): budget, timeout, cast, key, getter raise, `json` failure, Command timeout in `proc.c1f`; golden/route tests
   assert old routes byte-identical; `entity_read` and `run_cliff` show `Error: <reason>` (`door_tools.c1f:163,197`).
5. RED: infer (d) plus the regenerated `types.json`; the pack migration (f) one pack per commit.
6. Docs: help pages (`c1f.md`, `upgrade.md`), `c1f-stdlib.md`, wasm rebuild check, the list-element side map if approved.
Estimate: 6 rounds (rounds 2 and 5 are the largest; round 5 has the widest diff).

## DECISIONS for the operator (nothing below is chosen)

1. Default operator: c1f has no `??`. Keep `or` as the default (and make `or` NOT hide an Error: recommended), or add `??` (null-only
   default, Error passes through)? Adding `??` is a new operator.
2. Should `x or d`, comparisons and WHEN over an Error PROPAGATE the Error (recommended) or give False/default?
3. Is a stored list/dict element's Error reason kept across a store (needs a path-keyed side map, a memory cost and a bound) or lost (recommended: lost, null across a store)?
4. Collected virtual entity read: null (recommended) or Error?  And `error: f of a null` (`expr.rs:1288`): null (recommended) or Error?
5. A setter writing an Error: refuse with a receipt (recommended) or store null with a `why`?
6. How a pack makes an Error: a constructor `Error('reason')` (as `None('why')` does for null), the name, and whether a separate remedy
   argument exists (`Error('reason', 'remedy')`) or the remedy is part of the text (as now).
7. Reserve the name `Error` as a kind (no class may be called `Error`)? And `x is not Error` accepted?
8. `x is (A, B)`: ALL must match (decided), but for VALUE kinds that can never both match, is it a parse error (recommended) or just False?
9. Does `why(x)` read an Error's text (recommended) or stay null-only with a new accessor?
10. `Error` in `types.json`: write the regenerate as one commit (recommended) or per pack?
