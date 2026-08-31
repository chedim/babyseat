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

## Verify after restart

- boot line shows `router: ... candidates, cloud-floor`
- escalation log shows `cloud-floor` reasons, never `cost-floor: local first`
- `field state` shows the compat screen counts
- pick a local model in opencode -> confirm zero cloud routes

## Next steps (in order)

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