# The freeze-outage chronicle (2026-10-02 -- seat unavailability post-mortems)

*The seats' stuck/swirl/queued-forever arc, written on EVIDENCE (the
serve-msgs session stores, the opencode logs, macOS `sample` thread
maps, the process tables) -- the same discipline as
[crash-covenant.md](crash-covenant.md). Five root causes, all landed
and pinned; read this before debugging a "membrane is stuck" report,
because each of these has already happened.*

## The exhibits (the operator's symptoms, newest first)

| Symptom | The real cause | Fix commit |
|---|---|---|
| "messages go into nowhere" | the busy contract's 300s wait DIED queued POSTs with a `busy` stub -- a turn past 5min (a 22min agentic loop) meant every later message: recorded, never run, never completed | `6bb0ec8` |
| "utterly broken, swirls, does nothing" | `apply_pending` held the field lock whole-burst (30s+) -> the run-lane relay false-errored at its 30s timeout -> the model burned 24 rounds retrying | `9a07efd` |
| "can't even type in the announcer seat" | the WRITE-AMPLIFICATION CPU bomb: 6344 word-entities (78% of the field) with 216k per-word delta rows; every event touched ~15 word states; the night drift = O(words x deltas) | `472d8c9` |
| "stuck again" | a SECOND race class (FileBackend._dirty, a plain set) + the snapshot iterations; PLUS the field's data dir was double-nested (`.membrane/.membrane/field`) with 520MB of history under the wrong path and snapshots failing | `87e28e0`, `16e3c62` |
| "keeps spinning, never answers" | the DISK hit 100% (7.7GB free) -- the snapshot rewrite cycle churned ~1GB per 2s flush; on a full disk the writes STALL and the stall wedges the tool executor | `3017711` |
| "stuck again" (again) | THE JOURNAL: the snapshots were NEVER the fix -- re-serializing 285MB of 1024-dim float vectors GIL-held EVERY 2s flush (the content-hash skip hashed the already-dumped bytes: the dump was the cost) | `c4aaf7b` |

The evidence trail that cracked it: macOS `sample` (the CPU thread's
C stack: `json.dumps`' encoder + `float_repr`/dtoa -- 1225 of 1576
samples), the serve-msgs stores (which turns completed vs queued), and
the opencode logs (the SLOW/PENDING tool warnings -- 37 in one boot).

## The root causes, in one line each

1. **The queue-less busy contract**: a second POST waited 300s then
   stubbed out -- the queued turn never ran.
2. **The lock-holding applier**: whole-burst lock holds (30s+) behind
   a live turn's relays.
3. **The write amplification**: word-entities carrying per-word delta
   rows (the delta consumers scanned them per tick).
4. **The doubled field dir**: `FIELD_DIRNAME` joined onto a parent
   that WAS `.membrane` -> snapshots failing for want of parents,
   history under the wrong path.
5. **The disk-full stall**: 100%-full disk + whole-file snapshot
   rewrites = the wedged executor.
6. **The 285MB GIL-held re-serialization**: the snapshot world's
   fundamental cost (the content-hash skip did NOT fix it -- the dump
   WAS the cost). THE JOURNAL (append-only jsonl everywhere,
   O(changed) flushes) is the fix.

## The covenant that landed (for the next debugger)

- **The queue**: queued turns RUN (the per-session FIFO drains in the
  finishing turn's finally; `{'status':'queued'}` + `result_of(qid)`
  for the sync lane; the stub only covers the flood guard).
- **Lock discipline**: no whole-burst lock holds; every backend op
  takes the RAM lock; every iteration iterates a SNAPSHOT.
- **Exec**: the model has NO run verb -- a command entity's script
  returning `{run: true}` routes through the engine (the observation,
  the error lanes, the audit); the routes answer 410.
- **The field's persistence**: journals only (O(changed) writes); the
  snapshots retire to `.migrated`; the events.jsonl pattern is the
  ONE pattern.
- **The watchdog**: every tool call timed; >5s warns, >45s dumps ALL
  threads' stacks to the REAL stderr handle (a StringIO has no
  fileno; the evidence is the dump, not the kill).

## The operational notes

- **The disk matters**: watch `df -h /System/Volumes/Data` (100% =
  the seat's freeze, not a crash). Docker.raw is a grow-only ratchet
  (prune volumes/build cache; the raw never shrinks by itself).
- **The seats' fields migrated** (2026-10-02): membrane 8334
  entity_states / 27811 sentences / 20119 deltas; the announcer +
  cortex seats likewise -- the `.json.migrated` files are the
  originals (the bytes survived).
- **The corrupt-file heal** (the announcer seat's torn entities.json,
  37MB): doc-by-doc `raw_decode` recovered 374/375 -- the technique
  for the next torn store: parse docs individually, drop the torn
  tail, rename the backup `.torn-backup`.
- **A known red that is NOT yours**: `test_digest_night_speaks_
  materializations` fails on the uncommitted planner WIP another
  seat left in the tree -- red with and without any of today's
  changes. Leave it to that workstream.