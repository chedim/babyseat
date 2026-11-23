# The goal graph (the one pack `goalgraph`)

A goal is worked as a graph of tasks, waits and cards. A planner pass writes the graph, a pool of workers runs its tasks, and the chat is told when nothing moves any more.

**It is one pack, shipped** (`membrane/packs/goalgraph.c1f`, listed in `apps/barest.packs`). It is named `goalgraph` because that is the name of the engine's older built-in goal graph, which it replaces; a pack of any other name would run beside the built-in and fight it over the same classes. The model is `Pool:workers.model` (the `pick` of every planner and worker message; empty = the seat's own pick). Everything below happens by itself once a goal exists: see "What runs without being asked".

## The entities

- `Goal:<g>`: `text`, `status` (open until closed; `parked` stops the reflex), `replan` (why a planner pass is wanted), `plan` (the planner's one write), `plan_result`, `max_plans` (see the ceiling).
- `Task:<g>:<key>`: one worker turn. `open` (something it waits on is not done), `owed` (ready), `running`, then `done`, `failed`, `stuck`, `split` or `dropped`. A task given a sub-task before it ran is `split` and is done when its sub-tasks are.
- `Wait:<g>:<key>`: something that takes time outside a turn. It arms when what it follows is done, is checked every `every_s` seconds (at least 10), is `done` when its condition holds ({field, prop, equals} or {method, args, equals}), and `failed` after `deadline_min` minutes.
- `Decision:<g>:<key>` (a card): a choice only the operator can make; it blocks the tasks that need it. `Pool:workers`: `size` tasks run at once (3 unless it says otherwise).
- Readiness is a getter (`is_ready`, `open_blockers`), counted, never decremented, so a dropped or doubled write cannot make it drift.

## The plan

`Goal:x.plan = {tasks, cards, waits, drop}`, applied by one setter. `after` and `blocks` name a key of the plan, an earlier key of the goal, or an eid. A plan is refused whole (nothing changes, `plan_result` says why) when an edge names an unknown node, a wait lacks a condition, a pace of at least 10 s or a deadline, or **the edges close a cycle of any length, counting the `blocks` that earlier plans already wrote**. A task that is `stuck` or `failed` and listed again runs again; a `done` one runs again with `redo: true`; `drop` frees what the task blocked.

## What runs without being asked

- A goal made with text asks for its first plan (`replan` = `new goal`).
- The reflex: an open, unparked goal with nothing moving and no plan pending is planned once per graph state (`replan` = `idle` and the state), so a plan that changes nothing does not loop.
- A task that ends, or a card that is answered, asks its goal to replan. The pool takes one owed task per beat while a slot is free. Armed waits are checked on the beat.
- When nothing moves and no plan is pending, the chat is told once per graph state: the planner's FULFILLED claim, the receipts, any task running over 15 minutes. A failed wait and a card the graph waits on are told at once.

## The lanes

The planner and the workers are model turns. This seat refuses `membrane::llm.call`, so each pass is a message in the chat `gg`, on the model `Pool:workers.model` names, and the turn's answer text is the reply (`planner_reply`, `worker_reply`): the last `FULFILLED` line of the planner's answer is the claim; the last `DONE` / `FAILED` / `STUCK` / `SPLIT` line of a worker's answer ends its task. A worker works its task to the end of its turn, with as many tool calls as it needs; `SPLIT` is only for a task too big for one turn. A worker answer with no verdict is one more step (twice at most), then the task is `stuck`.

## What keeps it from getting stuck

- A planner pass answers the reason it was called for when its reply lands, whether or not it wrote a plan (UNCHANGED, FULFILLED, an error, a refused plan). Without this a goal whose planner wrote no plan waited for ever. A refused plan is told to the next pass (`Your last plan was refused: ...`).
- A planner pass nobody answered for 15 minutes (its turn was lost), or one cut off by a restart, is asked again. A worker nobody answered for 15 minutes becomes `stuck`, which frees its pool slot and asks the planner. A task that was running or owed at boot goes back to the pool.

## The ceiling

`Goal.max_plans` is the most planner passes a goal may use (`plan_calls`). It has no default: a goal without one is never held back, and a goal that has used them is planned no more, by a reason or by the reflex. The chat is still told when nothing moves, and the report says the limit is reached. Raise it, or close the goal.

## Numbers that are in the packs and wait for the operator

Pool size 3, a task is stalled after 900 s, a wait's pace is at least 10 s, a worker step may continue twice, and the lengths the prompts and receipts are cut to (160, 400, 600 and 200 characters).

## Not verified

The specs (`tests/specs/goalgraph.json`, `goalgraph_lanes.json`) run the pack in the engine with scripted replies. Nothing here shows a real model answering a planner or a worker lane, and the cost of the beat on a seat with many goals has not been measured. A running goal can spend model turns without anyone asking: set `max_plans` for any goal you leave alone.
