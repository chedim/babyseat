# Cliffhanger fields: the design (goal 606594617c19, issue #994)

Status: DRAFT for operator review. Nothing here switches on a live seat without the operator's go.
Syntax source: github.com/chedim/cliffhanger README (read 2026-11-09). Where the README is silent
the doc says so ("MEMBRANE EXTENSION") instead of inventing cliffhanger syntax.

## 1. Cliffhanger syntax used (checked against the spec)

- Access guards: `= value` on a path reads (the guard answers a read); `/path = value` writes.
- Triggers: `WHEN <xpath cond>:` / `ELSE WHEN ...:` / `ELSE:`, each followed by a mutation list.
  Adjacent `WHEN`s merge with OR; nested `WHEN`s merge as the README shows.
- Variables in a trigger: `$THIS` (the new value), `$OLD` (the value being overwritten),
  `$FIELD` (first path element from the trigger to the changed value), `$NODE`, `$REQUEST`.
- Layers: each module is a trigger layer; a write fires the triggers on its path and then every
  ancestor path up to the root, layers in reverse load order, triggers within a layer in
  definition order.
- Not in the README (MEMBRANE EXTENSION, to be agreed): `every`, cached getters, setters,
  two-phase setters. Section 2 defines them as fields; they are the translation target.

## 2. The rules (1-9)

1. **Fields.** Everything is a field on an entity; entities are instances of classes; a field is
   declared once on a class (name, type, getter, setters).
2. **Getters are cached and eager.** A field with a getter holds its last computed value. A read
   returns the cache; a read never writes and never fires anything.
3. **Own name = previous cached value.** Inside a getter the field's own name is its previous
   value: `x = (x or 0) + 1` is an accumulator.
4. **A recalculated value is a write through the setters.** When inputs change the getter runs
   and its result is written like any write.
5. **Every change fires.** A write whose stored value differs fires the triggers/getters that
   depend on that field. A write of the same value is no change and fires nothing.
6. **Setters.** A field has several setters, run in the order added. A write to the field's own
   name from inside its setter stores directly and bypasses the setters. Three jobs: effects
   (chat, exec, llm), guarding (refuse/alter), write-through computed values.
7. **Two phases (decision 5).** `set` setters run before the store, in added order, and may change
   or refuse the value (a refusal stops the chain, nothing is stored). The value is stored once.
   Then `on` setters run in added order, see the stored value, and do effects and writes to
   other fields.
8. **Loops.** Getters see the previous values of fields, in a fixed evaluation order. A setter
   chain that returns to a field already written in the chain is refused and reported (via the
   existing loop rule, `writes.source` chains).
9. **Events come only from outside, as writes.** A sensor, a person or a watcher writes a field.
   Time is the clock tick, itself a write; there is no `every`. A timed rule reads the clock field.

## 3. The five decisions of 2026-11-09

1. **Sensors are pushed, never pulled on read.** Written from clock/watcher; a read never runs
   code. The doctrine line "getter script that reads its real source, so reading returns the world
   as it is now" and the SCHEDULED READS paragraph become "keep it fresh" (as fresh as the last
   tick or event). Read-triggered getter scripts become tick/watch-driven inputs.
2. **Freeze delays effects only.** On a hot entity, heat/gravity/freeze pace the `on` setters'
   effects (chat, exec, llm). Getters always recalculate at once.
3. **A setter may write any field the writer is allowed to write.** Each target field enforces
   through its own `set` guards. This widens the entity-scripts covenant in docs/entity-field.md
   (updated in the same PR).
4. **A rebuild never runs effects.** Recompute after migration, lost cache or a new getter is a
   silent restore. Accumulators come from the saved cache or from #971 journal replay.
5. **Two-phase setters**, as rule 7.

Cold start: new world = effects run once; warm restart = none; rebuild = silent (decision 4).

## 4. Today's constructs mapped

| Today | Field form |
| - | - |
| trigger `changed ?x.p` | an `on` setter (or dependent getter) on field `p` |
| `isa ?x C` | the field is declared on class C |
| `filter expr` | `WHEN expr:` in the guard/getter |
| `below p N` | `WHEN $THIS < N:` |
| `every S` | a rule on the clock field (`WHEN clock - last >= S:`), rule 9 |
| write `?x.p = e` | a setter writing `p`, or a computed field `p` with getter `e` |
| method dispatch | a write to a method field; the adapter is its `on` setter |
| calls as writes (adapters.py) | `llm.call` etc. are fields; writing one is the call |
| VM WHEN/WHERE/WRITE | translation layer, removed at the end |

## 5. All 12 goalgraph.TRIGGERS rewritten

Counting becomes a computed field instead of decrement-on-event: idempotent and replay-safe.

```
-- 1 unblock by task, 2 unblock by card, 6 unblock by wait
Task.open_blockers getter:
  len([b for b in blockers_of($THIS) if finished(b)==False])
  finished(b): Task/Wait -> b.status = 'done'; Decision -> b.status = 'closed'
-- 7 wait follows a task, 8 wait follows a card
Wait.open_blockers getter: same shape (blockers Task: done; Decision: closed)
-- 3 ready
Task.status on-setter:  WHEN open_blockers < 0.5 and status = 'open': /status = 'owed'
   (also an on-setter on open_blockers, the original trigger input)
-- 4 replan on task end
Task.status on-setter:  WHEN status in ('done','failed','stuck'):
                          /goal/replan = 'task ' + eid + ' ' + status
-- 5 replan on card
Decision.status on-setter: WHEN status in ('closed','dropped'):
                          /goal/replan = 'card ' + eid + ' ' + status
-- 9 wait armed
Wait.status on-setter:  WHEN open_blockers < 0.5 and status = 'open':
                          /status = 'armed'; /armed_since = clock
-- 10 wait check (was every WAIT_TICK_S = 10)
Wait.due getter:  WHEN status = 'armed' and clock - armed_since >= every_s: true ELSE false
Wait.due on-setter: WHEN $THIS: /check = 'due'      -- adapters' check branch
-- 11 plan
Goal.replan on-setter:  WHEN (status or 'open') = 'open' and replan:
                          /llm/call = {about: eid, prompt: 'plan the goal: ' + replan, lane: 'planner'}
-- 12 plan after reopen
Goal.status on-setter:  WHEN status = 'open' and replan and planned_ts
                              and replan != planned_replan:
                          /llm/call = {same as 11}
```
Wait example fields: `open_blockers`, `armed`, `armed_since`, `due`. Note: today's `wait check`
fires every 10 s; in fields `due` recalculates on each clock tick, so the check cadence is the
tick, and the adapter must be idempotent per `every_s`. The parity checksum will show any drift.

Getter rows 13 and 15: ASSUMPTION, confirm with the operator. I read them as rows 13 and 15 of
the entity-script group (migration rows 12-15): row 13 `autonode.py` tick scripts become
clock-driven input fields (decision 1); row 15 `chat_io.py` chat/say scripts become `on`
setters on a `say` field paced by freeze (decision 2).

## 6. Migration table, in the order we would do it

| # | Item | Source | Becomes |
| - | - | - | - |
| 1 | Parity gate | #971 journal | the checksum test (section 7) |
| 2 | Goal-graph triggers (12) | goalgraph.py | section 5 |
| 3 | Banner rules | graphbanner.py | computed fields |
| 4 | Recall policy | recall_policy.py #983 | guard setters |
| 5 | Calls as writes | adapters.py #959/#956 | `on` setters |
| 6 | Self learned triggers (179) | ch_learn.py | field rules |
| 7 | Seed packs | seedpack.py #982, #984-991 | field declarations (finish data moves only; no new WHEN/WHERE/WRITE) |
| 8 | Entity scripts (rows 12-15) | night.py, autonode.py, chat_io.py | getters/setters; sensors pushed |
| 9 | VM | native/ (Rust, the only VM target), ch_native.py | WHEN/WHERE/WRITE as translation layer, then removed. Fields are built on the native VM only; no Python VM (ch.py) work. Python VM removal is the separate goal membrane::goal:aeac0f121bf2 |

## 7. How

One conversion per item, behind the issue's switch; no item switches on a live seat without the
operator's go. Parity gate: replay the same #971 journal under today's triggers and under fields,
both sides run on the native VM; checksum the whole field (every entity's stored and cached fields, sorted) and compare. Per-rule
"Done means" tests stay as VM unit tests. The checksum gates every migration row.

## 8. Same-PR doc edits (not in this draft)

doctrine.md (sensor line, SCHEDULED READS) and docs/entity-field.md (entity-scripts covenant,
decision 3). Until the design lands, #980 PRs #984-991 add no more WHEN/WHERE/WRITE triggers.
