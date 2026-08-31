# Token economics and the router

Extracted from the README so it can go deep. This is the money story: where
savings come from, and how each turn picks a model.

## How it saves tokens

Two honest facts up front. First: **in this architecture, learning is free** —
every turn is absorbed as a local embedding (~\$0), so the training data
accumulates without model spend. In a measured real working session on this
repo, **4,219 turn exchanges (~1.17M chars ≈ 290k tokens of traffic) were
learned as `teed-pair` exemplars at zero marginal model cost** — the capture
rides the turns that were happening anyway. The savings come from what the
field then *answers without asking a model again*:

1. **Intercepts (0 tokens).** Five canonical frames —
   `membrane: query the field…`, `field state`, `membrane: learn that…`,
   `membrane: forget…`, `membrane: suggest` — are computed from the live
   field and never see a model; the agent's own prose-embedded asks (thought
   intercepts) are answered the same way. Every such turn is a full LLM-turn
   avoided.
2. **Replay (0 tokens).** A settled repeat of a safe memory returns the
   stored answer verbatim above `--replay-sim` (default 0.97):
   verbatim-similarity, demotion-guarded, tool-call-excluded. Rare early,
   compounding as the field earns trust — in the measured session 1 of ~180
   chat-escalations replayed (~180 tokens); the design intent is that a
   month-old field replays the boilerplate (the tenth "run the tests the same
   way" costs nothing).
3. **Compaction (fewer tokens per turn).** The idle-time Compactor distills
   clusters of retired raw turns into one note; later turns inject the small
   note instead of the raw exchanges on every single request. Measured: two
   compaction cycles on this repo distilled 8 exchanges into 2 notes — a
   per-turn context saving paid every call, forever after.
4. **Self-author mode (0 tokens per turn).** The extreme dial: the cell
   *recites* its nearest lived scene instead of calling the model, and the
   genesis cache amortizes one LLM call over ~8 world scenes (the virtual
   world runs this way end-to-end; see
   [exploration-game.md](exploration-game.md)).
5. **Momentum + cold tier (indirect).** Momentum-skew raises first-shot
   recall precision (fewer clarifying re-asks); the cold tier keeps the hot
   field small so kNN stays fast *and* the retrieval surface stays small.

Where the money actually goes after all of that: **escalations** — genuinely
new situations. That is the router's job.

## The router: ladder, ceiling, outage

The cheapest eligible cloud model holds the **floor** (fast, cool, always
"resident"); a LOCAL takes the rank only on earned evidence — ≥ 6 trials,
≥ 70% wins, a fit margin over the floor, and only if it holds without a cold
load. Scoring:
`score = fit_hat − λ_time·urgency·delay − λ_cost·price`.

- **Operator ceiling** — your opencode model pick caps the ladder: never
  pricier cloud than the selected model; a local pick forbids cloud entirely.
- **Compatibility screen** — a local candidate must declare `completion` +
  `tools` capability flags, clear the non-chat name screen, and fit the
  `--max-local-gib` budget. The same screen filters `/v1/models`, the picker
  and the judge ladder — toy/embedding/vision routes die at the source.
- **Outage ladder** — a rung that fails (401/402/403/429/5xx, connect
  failures) cools down (5 min, persisted) and the turn falls to the next
  rung. When every rung is down, **the field itself is the last resort** —
  reliance on learned memory grows automatically as the ladder drops out.
- **Explore lane** — chat turns never eps-explore while a cloud floor holds;
  `@explore`-pointed subagents sample least-tried eligible locals (in-memory
  only, never cloud).

Knobs: `--prefer-local` (classic local-first floor for offline/privacy),
`--cloud-budget` (daily cap), `--explore`, `--disable-llm-router` (the kill
switch — plain single-model escalation), `--max-local-gib`. Per-turn audit
lines (`route: model=… reason=… pressure=…`) tabulate claimed-vs-measured win
rates in `field state`. Tests: `tests/test_router.py`.

## Replay and the three verdicts

Per turn the gate picks one of:

- **REPLAY** — cosine ≥ `--replay-sim` (0.97) to a safe past turn → the
  stored answer, **zero model calls**. The LLM is replaced completely for
  the mundane.
- **ESCALATE** — forward to the routed backend (local Ollama by default; the
  Anthropic adapter fronts the cloud for Claude Code).
- **OBSERVE** — forward + learn, never replay a coding answer.

Replay, intercepts, and the compactor's distilled notes mean a well-worn
project runs long stretches with **zero** model calls.