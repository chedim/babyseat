# Wavelink: getters as a wave, setters as transactions (DESIGN, not built; #1143)

Status: **design from the operator's 2026-11-15 conversation. Nothing here is built and no branch exists.** Every number, new builtin and new native piece below is a DECISION for the operator.
What the engine does today is in [cliffhanger-fields.md](cliffhanger-fields.md) (rules 2 to 9 of section 2); this page says what could change and why it is written down: so that a later parallel engine
and [graph-link.md](graph-link.md) are built to fit each other.

**Wavelink** (the operator's name, 2026-11-16) is the algorithm the threads of ONE VM would use: level-by-level getter waves, setter frontiers merged in the serial order, a conflict check, losers bumped to the next frontier (sections 2 to 4). It runs on shared memory and gives exactly the serial engine's result. Between VMs the protocol is GraphLink ([graph-link.md](graph-link.md)):

| Scale | Mechanism | Consistency | State |
|---|---|---|---|
| inside one VM (threads) | wavelink | exact: equal to the serial engine | shared memory, no copies |
| between VMs (processes, devices, machines, or threads kept isolated on purpose) | GraphLink | eventual: a foreign read is `pending` first | replicas copied over frames |

There is no third layer: threads of one process need no messages. A thread that must stay isolated is a GraphLink peer over the in-memory pipe.

## 1. What is true today (from cliffhanger-fields.md)

- The engine is ONE thread. Drivers run on their own threads and hand results back as outside writes in a later round.
- Each outside write starts one round. In a round every downstream getter recalculates at most once, in dependency order; the edge that closes a cycle reads the previous value (rule 8).
- A write whose value is unchanged fires nothing: early cutoff stops the wave (rule 5).
- Setters run in added order; `set` setters before the store, `on` setters after it (rules 6 and 7). A setter chain that returns to a field already written in the chain is refused (rule 8).
- A getter writes only its own cell and cannot call a setter. An effect is a write of a verb property on a driver entity; its result comes back as a receipt write.

## 2. The picture

A write changes one cell. The cells that read it are the next level and are recomputed together, then the cells that read those, level by level up the dependency graph: a **wave**.
- It dies where nothing changes (early cutoff), so most waves are small.
- It touches only cells reached along the recorded read edges.
- Cells in one level do not depend on each other, so a level can run in parallel. Setters start new waves, so a round is a cascade: setters, a getter wave, the setters that wave triggered, another wave, until nothing is left.

## 3. Getters are views, setters are transactions

- A **getter** is a rule triggered by a change in a cell it read. It writes only its own cell. Evaluating a level in parallel cannot change what another getter of that level reads, so a level needs no conflict check.
- A **setter** is a rule triggered by a write to a named cell. It reads a settled state and produces a set of writes, effects included, that commit **together or not at all**: a transaction.
- Neither is called. There is no call stack and no return value; the program is a graph of rules that fire when something they depend on changes. A getter is the restricted case: one cell written. Only setters can lose a conflict.
- A cascade is a chain of transactions, not one big one. Nothing makes the whole chain atomic; an atomic cascade would be a new feature.
- The journal is the write-ahead log and replay is recovery. `born_by` is the provenance of a row: which transaction made it.

## 4. The setter frontier (design)

1. The setters triggered by a step form a **frontier**. They run in parallel, each against the state settled at the start of the step, and each records what it read and what it wants to write.
2. In the fixed serial order of today (level, entity, setter index) the frontier is checked: a setter **loses** when an earlier, committed setter's writes, or the getters those writes would dirty, are in its reads. The check follows the recorded read edges one step, so it is a lookup and not a recomputation.
3. A loser's writes are dropped, effects included, and it is bumped to the **next frontier**. It re-runs from the start for the same trigger, with its `WHEN` checked again on the settled values; it may now do nothing. It goes first in that frontier, ahead of newly triggered setters.
4. The winners' writes are applied, the getter wave runs, then the next frontier.

Properties: the first setter in the order never loses, so there is progress and no starvation. Two setters that dirty the same getter without reading it do not conflict; the getter is recomputed once after both. A setter triggered twice in a frontier runs once.
The result must equal the serial engine's, and that is a test: run both engines on the same journals and require identical state.

**Effects need no rule of their own.** An effect is a write on a driver entity, part of the setter's write set. A bumped setter loses it with the rest, so nothing starts; the host starts the real work when the writes commit. The receipt and chunks come back as ordinary writes through the same rules. Replay uses recorded receipts, so a run that never committed leaves nothing in the journal.

## 5. What would stay serial, and what is not checked

- **Off-engine reads.** `sql(...)`, `recall` and `rerank` register a request when evaluated and read `None` until rows land. Parallel evaluation needs that table to be thread-safe, or those getters stay on the serial path.
- **The clock.** A getter that reads the time reads the round's one timestamp, so parallel workers must share it.
- **Hot cells:** setters writing one cell (a counter, the banner) always conflict.
- **Not checked:** how today's serial engine treats a getter that sits between two setters of the same step; the frontier must reproduce exactly that. This has to be read in the code before any build.
- **Pipelining** (the next wave trailing the first by a level, with a few versions per cell) is a later optimisation. The first version is one wave at a time, parallel inside a level.

## 5b. Wavelink across processes: a leader and workers (a later shape, not built)

Two owners of one namespace are not possible (ownership is one VM per namespace). What is possible is one OWNER and workers: the leader owns the namespace, applies every write and keeps the serial order and the journal; a worker holds replicas of what it reads (its demand-driven subscriptions) and evaluates a frontier's rules against the settled state, returning each rule's read set and proposed writes; the leader merges them in the fixed order, runs the conflict check, drops and bumps the losers and applies the winners. The result still equals the serial engine's. A worker never starts an effect: only the leader commits. It costs a round trip per frontier and replicas to keep current, so it pays across processes or machines, not between threads of one process, where shared memory is cheaper. It needs a task op and a result op in the frame spec (the `wr` and `res` of docs/frames.md are forwarded writes, a different thing): a protocol decision for the operator.

## 6. What this means for graph-link

Written here so the link and the engine are built to fit; none of it is decided (decisions at the end).
- **A frame is a round boundary.** The link already sends one frame per peer at the end of a round and takes answers as outside writes in a later one (graph-link.md section 4). A parallel engine keeps that: frames are cut between rounds, never inside a frontier.
- **A forwarded `wr` is a remote transaction.** The owner applies it as an outside write, and its `res` should mean "committed". If `res` carries the node's revision `r`, the client knows when its replica shows the write (graph-link.md has no stated order between `res` and the following `val`).
- **The order of `val`s is the owner's wave order,** and `val`s of one round coalesce to the latest value per node.
- **The library stays sans-I/O** (`graphlink-core`, github.com/chedim/graphlink): it holds no threads and no locks, its state belongs to the engine thread, and transports are threads that only move frames. A parallel engine changes nothing for it.
- **A peer is a root namespace with its own engine.** A device or a driver process has its own rounds; the link joins two round machines and never shares a transaction between them.

## 7. Decisions for the operator

1. Is the rule "a setter's writes happen together or not at all" the sentence the model's help should carry? Not written into help yet.
2. Build order if this is ever built: read how the serial engine orders getters between setters (section 5), then a frontier that runs serially but records read and write sets, then the conflict check with the journal-equality test, and only then threads. No thread before the test.
3. `res` carrying `r` and the frame boundary rule of section 6 go into the graphlink frame spec; neither is added until the operator agrees.
