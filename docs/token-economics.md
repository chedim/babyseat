# Token economics and the router

Extracted from the README so it can go deep. This is the money story: where
savings come from, and how each turn picks a model.

## Measured numbers (the operator's live seat)

From the seat's own per-turn upstream usage, not an estimate —
`membrane stats --store .membrane/serve-msgs.json --baseline` (read-only, no
model call). 1,215 sessions / 1,955 user messages / 3,840 assistant turns
through 2026-10-15.

| metric | value |
|---|---|
| input / output / reasoning | 36.1M / 5.5M / 8.5M tokens |
| cache-read | 14.2M — 39.5% of input (prompt caching) |
| tokens per turn (in+out) | 10.8K avg · 1.55K median · 137K p99 |
| LLM calls per turn (tool-loop rounds) | ~4.9 |
| recall injection | ~700 tokens median · ~1.1K p99 |

**The savings baseline** — against a naive full-context runner that replays
the whole transcript every turn (O(n²)):

| replay strategy | context tokens | vs full-context |
|---|---|---|
| full-context replay | 74.3M | — |
| bounded window (12 msgs × 4K chars) | 1.9M | −97% |
| actual (window + recall + tool loop) | 36.1M | −51% net |

The window's saving lands almost entirely on long conversations (turns with
200+ prior messages carry 68.5M of the 72.4M saved); under the 12-message
window it is neutral and the recall + tool loop is the standing ~34M-token
cost. The naive side uses the same ~4 chars/token estimate the seat's own
fallback uses, so the −51% is conservative.

## How it saves tokens

Two honest facts up front. First: **in this architecture, learning is free** —
every turn is absorbed as one embedding of each distinct sentence (~\$0), so
the field's evidence accumulates without model spend. The capture rides the
turns that were happening anyway. The savings come from what the field then
*answers without asking a model again*:

1. **Intercepts (0 tokens).** Five canonical frames —
   `membrane: query the field…`, `field state`, `membrane: learn that…`,
   `membrane: forget <#N>…`, `membrane: suggest` — are computed from the
   live field and never see a model; the agent's own prose-embedded asks
   (thought intercepts) are answered the same way. Every such turn is a
   full LLM-turn avoided.
2. **The state diff instead of re-injection (fewer tokens per turn).**
   Normal mode does not re-serve the field every turn: the model sees the
   ENTITIES relevant to its ask once, and afterwards only the **diff** —
   which entity states moved since the last message it processed, one line
   each. On a quiet turn the diff is empty and the injection is a few
   hundred bytes instead of a grounded block.
3. **Replay, as an A/B (the saver, measured before served).** Every real
   turn the gate finds the earlier ask nearest this one; eligible pairs
   (fresh evidence, trusted answer, agreement across near asks) are logged
   — the stored answer next to what the model actually answered — and the
   idle judge scores their agreement. Default is **shadow**: you are never
   bypassed. `--replay-ab P` serves the replay on a fraction P of eligible
   turns. The measured upside (a well-worn project runs long stretches
   replay-free) is real but the staleness cost is real too — hence
   measure-first: after a day of use, `membrane review --ab` shows the
   eligible count and the judge's verdicts before any serving.
4. **Distilled notes stay a claim source, not a context cost.** The old
   compact notes live as `distilled` events — they surface through the
   entity field's blocks when relevant, not as a standing re-injection.

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
  rung. When every rung is down, **the field itself is the last resort**
  (`[membrane] FIELD-ONLY` — the field's own answer, honestly marked, never
  learned: the field does not feed on itself).
- **Explore lane** — chat turns never eps-explore while a cloud floor holds;
  `@explore`-pointed subagents sample least-tried eligible locals (in-memory
  only, never cloud).

Knobs: `--prefer-local` (classic local-first floor for offline/privacy),
`--cloud-budget` (daily cap), `--explore`, `--disable-llm-router` (the kill
switch — plain single-model escalation), `--max-local-gib`, `--replay-ab`.
Per-turn audit lines (`route: model=… reason=… pressure=…`) tabulate
claimed-vs-measured win rates in `field state`. Tests:
`tests/test_router.py`.

## Background model budget

Idle background organs (the thinker, the dreamer, the NightClock's script
and claim judges, the replay judge) run on a LOCAL model by default
(`--idle-cortex-model`, default = the first local Ollama chat model by
family preference — granite, glm, qwen3, qwen2.5-coder, …). **The watch
loops are the exception:** they are real seat turns on the seat's model,
which on the ThinkPhone seat is `glm-5.3-flash:cloud`, so every loop pass
is a cloud call. Their cost rules (wake backoff, BLOCKED parking, the
verdict line, the per-pass tool budget, the alert gate) are in
`background-mind.md` → "The goal loops' rhythm". The router's free-tier covenant:
`--cloud-budget` defaults to $0, which keeps free-tier rungs (price ≤ 0.1)
reachable and paid rungs unreachable until an explicit budget.

Intercepts, the state diff, and an idle-humming background mean a
well-worn project runs long stretches with **zero** model calls.