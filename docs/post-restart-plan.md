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