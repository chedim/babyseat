# Post-restart plan: routing fixes verification + next steps

Recorded 2026-08-31, from the session that diagnosed "membrane picks the
dumbest models": rolled the router back, then rebuilt it forward.

## What changed (landed on `main`, needs proxy restart to activate)

1. **Reversed cost ladder** — cheapest eligible cloud holds the floor
   (fast, cool); a local takes the rank only on EARNED evidence
   (>= 6 trials, >= 70% wins, fit margin LOCAL_FIT_MARGIN over the floor)
   AND only if it holds without a cold load.
2. **Opencode-compatibility screen** — locals need Ollama `completion` +
   `tools` caps + the non-chat name screen + `--max-local-gib` (default 12).
   Same screen filters `/v1/models`, the picker, and the judge ladder.
   Kills bakllava/whisper/32b/toy routes at the source.
3. **Operator ceiling** — the user's opencode model pick caps the ladder:
   never pricier cloud than the selected model; a local pick forbids cloud.
4. **Outage ladder** — 401/402/403/429/5xx/connect failures cool a rung
   down (5 min, persisted) and fall to the next rung; when all rungs are
   down the field itself is the last resort (field grows in importance
   automatically as rungs drop).
5. **`_RAG_SYSTEM` rewritten** — explains membrane (what it is, what the
   blocks mean, how to consult it) to dumb and smart models alike.
6. Flags: `--disable-llm-router` (kill switch), `--prefer-local`,
   `--max-local-gib`. 22/22 router tests pass.

## Verify after restart — DONE 2026-08-31

- boot line shows `router: on (36 candidates, cloud-floor)` (opencode.log:5002)
- all `cost-floor: local first` lines are pre-restart; post-restart log shows
  only `cloud-floor` + one legit `exploring unsampled local` (eps-explore,
  trials=0 eligible coder model)
- operator ceiling exercised live: cloud pick -> cloud-floor; local pick ->
  "no cloud available: best eligible local", zero cloud routes
- 22/22 router tests pass (run with ~/.membrane/venv python, unittest)

## Next steps (in order)

0. **Landed 2026-08-31 (needs restart)**: explore hijack closed — chat turns
   never eps-explore while a cloud floor holds (test_offline_box_still_explores
   keeps the offline covenant); new `@explore` sentinel lane (subagents point
   at it, least-tried eligible local, never cloud); `/membrane/delegate`
   endpoint (model-pointed work, router-disposed: caller's ceiling ignored,
   operator's opencode pick is law, depth-1 guard, outcomes + latency + spend
   recorded, capture tagged `delegation`). 34/34 tests pass. THEN: forget the
   qwen-coder hijack junk exemplars (obj['response'] + system-prompt-leak),
   verify boot line, and confirm chat routes never show
   `exploring unsampled local` while glm holds the floor.
0b. **Landed 2026-08-31 (postponed batch, needs proxy restart)**:
   1. *Smartness metric* — losses walk a per-model loss EMA (router.py);
      `fit_hat` is outcome-conditioned (`outcome_fit`: wins up on their
      topic, chronic-loss topics down by LOSS_FIT_W). 0 trials -> claim only.
   2. *Capture-machinery quota* — `_CAP_ARGS` 2400->900, `_CAP_BODY`
      8192->2400; compactor seeds clusters with a capture before prose
      (`_pick_cluster`).
   3. *Auto-settle loop* — repeat-k rule in `gate_turn` (`settle_k=3`,
      `settle_sim=0.92`): an escalating near-dup that kept failing to learn
      replays the safe near match (`settle-replay`) or escalates with the
      demoted one as hint; ring wiped per settle. Tests:
      tests/test_postponed_batch.py.
   4. *Recall hygiene* — `Exemplar.model` stamp (the answering model,
      learned from the route verdict), `[via <model>]` in recall surfaces,
      `_hygiene_demote_screened_models(srv)` at boot mass-demotes exemplars
      answered by screen-rejected models (evidence-path; contest restores).
   5. *Field-only banner* — when the ladder is exhausted (route() found
      nothing alive or every rung failed at delivery), the proxy serves
      "membrane: FIELD-ONLY" + safe recall instead of a bare 500/502; never
      learned (no self-feeding).
   6. *Boot imprints* — lazy: `ensure_seeded()` arms the interview thread on
      the first route()/ladder(), not at boot.
1. **Smartness metric**: outcome-conditioned per-situation fit — rank models
   by earned competence direction on the query topic, not scalar win-rate.
2. **Capture-machinery quota**: tool/result captures are ~95% of the store;
   compress/archive them harder than prose so the hot field stays
   retrieval-shaped.
3. **Auto-settle loop**: repeated escalations at sim 0.92-0.98 with
   `learned=None` should converge to replay or demotion faster
   (repeat-k rule).
4. **Recall hygiene review**: surface "answer sourced from model X";
   mass-demote exemplars answered by models the compat screen now rejects
   (the dumb-model era junk is still in the field).
5. **Field-only banner**: when the whole ladder is down, opencode should see
   "membrane: field-only" instead of a bare error.
6. **Boot imprints**: lazy-seed only when a model first becomes routable.

---

# Post-restart plan: acting on the operator's honest-use feedback

Recorded 2026-09-01, from the session that shipped the couchbase two-tier
port (58337d9) + default-on ephemeral tier (f880ea2). The operator (coding
behind membrane daily, in this seat and a second seat) gave a three-point
critique; this plan converts each into work, most-earn-first.

## Context this plan inherits

- The ephemeral tier is NOW DEFAULT-ON (f880ea2): fresh captures are 3-day
  TTL candidates; use/contest/dedup-use promote durable-safe, refute
  promotes durable-unsafe. This directly attacks friction #2 (stale cells)
  -- but only after both seats reboot onto it. Verify first (V0).
- Near-miss evidence parked earlier: 22% of gate decisions sit at sim
  0.70-0.85 (86/393, all escalations) -- the embeddings-leverage work.

## V0: verification (first turn after reboot, before any code)

1. Field state honest read: canon ~4599 stable (no growth from tier-on
   seat), n_ephemeral >= 0, safe_frac sane, drift key present in fresh
   audit decisions.
2. Grep boot log: `ephemeral tier` hydrate line, no fallback warnings,
   router on.
3. If the operator already worked a bit: first capture should be a
   CANDIDATE in cortex-ephemeral (not canon) -- the default-on proof.

## W1: timing/intercept UX (friction #1: "emit once, wait, act")

The instinct to re-ask is human; the system can carry more of it.

- **W1a — pending-frame markers**: when a turn emits a canonical frame and
  the intercept hasn't answered yet, the recall block should say so
  explicitly ("frame pending, answer lands next turn"). Cheap: a flag on
  the frame-detection path surfaced in the recall block.
- **W1b — audit the wait**: count frames whose answer never arrived (the
  re-ask-because-nothing-happened signature) in audit analysis; if real,
  the pre-gate timing needs a look, not just the doctrine wording.

## W2: stale-cell defense (friction #2) -- the tier was step one

- **W2a — supersede-on-confirm**: when a correction is learned and an old
  canon cell contradicts it (high sim, conflicting action), mark the old
  cell `superseded_by` instead of leaving both live in recall. Promotion
  machinery exists (promote_ephemeral/demote paths); add the supersede
  link at learn-dedup time.
- **W2b — recall freshness weighting**: recall_merged already has an
  ephemeral recency bonus; extend weighting so stale canon (old eph_born,
  low evidence, low use count) ranks below recently confirmed cells --
  snapshot recall self-corrects instead of relying on the reader's
  vigilance.
- **W2c — mass-demote sweep** (carried from the previous plan, item 4):
  audit-sourced demotion of exemplars answered by models the compat screen
  rejects; the dumb-model-era junk is still surfacing.

## W3: lane-discipline guardrails (friction #3: strictness earns its keep)

- **W3a — frame-lint at ingest**: when a stacked/buried frame is absorbed
  as junk memory, the boot/audit layer should FLAG it in the audit (event:
  "frame_discipline_violation") so violations are visible and countable
  instead of only poisoning silently.
- **W3b — one-frame assert in dev**: a --dev flag that warns (never
  blocks) when a reply contains two canonical frames, to train the habit
  without breaking live traffic.

## E1 (parked, evidence-first): embeddings leverage

- Experiment script first (no production change): replay the 86 near-miss
  queries (audit sim 0.70-0.85) with (a) context-prepended text, (b) lower
  sim_min; measure crossings over 0.85 against the live field. Then decide
  query contextualization vs threshold calibration vs embedder swap.

## Order of attack after reboot

1. V0 verification (above) -- 10 min.
2. W1a + W1b (smallest UX win, unblocks trust in the wait).
3. W2a + W2b (stale-cell defense completes what the tier started).
4. W3a (make discipline violations visible).
5. E1 experiment (near-miss replay) -- before any embedder talk.