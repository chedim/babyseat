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

- **W1a — pending-frame markers**: DONE 2026-09-01. The thought block
  carries a TIMING tail ("emit once, wait, act; do not re-ask"); a
  discipline-compliant TRAILING frame (last line of a long/fenced reply,
  fence-guarded, canon-or-short-bare only) is now detected where
  whole-message matching missed it (`_last_line_ask`).
- **W1b — audit the wait**: DONE 2026-09-01. A repeat of an UNDELIVERED
  ask is no longer silently skipped: it bumps a `frame-wait` counter
  (unique asks / served / re-asks / buried), writes a `frame-wait` audit
  record, and surfaces in `/membrane/state` + the state report. Verified
  live on a spare-port smoke: delivered ask -> `served: 1`; resend ->
  `re_asks: 1`; buried trailing frame -> `buried: 1`.
  (`tests/test_frame_wait.py`, 9 tests.)

## W2: stale-cell defense (friction #2) -- the tier was step one

- **W2a — supersede-on-confirm**: DONE 2026-09-01 (`tests/test_supersede.py`,
  11 tests; full suite 197 OK). A fresh answer that CONFLICTS with a live
  SAFE canon dup (token overlap < 0.7) is a correction, not a re-statement:
  the correction is learned as a fresh CANDIDATE and the old cell is linked
  out of live recall (`extra["superseded_by"]` -> the successor's CONTENT
  key, stable across compactor renumbering; `superseded_ts`/`supersede_how`
  /`superseded_by_text` ride along). The link is SELF-HEALING at read time:
  it holds while the successor is alive and un-refuted (canon-safe or a live
  candidate); a refuted correction (durable-unsafe), a demoted one, a
  forgotten or TTL-expired one dangles the link and the predecessor returns
  to live recall (the alternates rule, retold). Chains unwind level by
  level (A->B->C hides A and B while C lives). Read-side resolution is
  one-pass (`_superseded_set`, zero cost when no links exist). Live-recall
  surfaces opt in: the gate (`decide(live_only=True)`), chat grounding
  (rag `top_k(live_only=True)`), and `_find_dup` (a third opinion echoes the
  correction and promotes IT, never resurrects the stale cell); archaeology
  lanes (forget search, compactor, query surface) keep the default so
  superseded rows stay findable and the links resolvable. Tier-off keeps
  the conservative keep-first-seen covenant (no candidate machinery to
  police the successor). Audit carries `superseded: <idx>`; state report
  shows the superseded count.
- **W2b — recall freshness weighting**: DONE 2026-09-01
  (`tests/test_recall_freshness.py`, 5 tests; full suite 202 OK). Canon
  rows now carry `confirmed_ts`, stamped at `promote_ephemeral` (safe
  promotions only -- a refute invents no freshness); legacy rows are
  ageless (no stamp, no invented bonus). `recall_merged` adds a SMALL
  confirmation-freshness bonus (w_conf=0.05, tau=7d -- deliberately
  gentler than the candidate bonus w_rec=0.15: evidence beats recency) so
  at a near-tie the just-confirmed answer outranks ageless stale canon;
  snapshot recall self-corrects instead of relying on the reader's
  vigilance. Chat grounding (`_memory_block`) now reads the MERGED
  surface (supersede-hidden + freshness-weighted + candidate-aware);
  verified band coexistence: minutes-old candidate > just-confirmed canon
  > 12h-old candidate at a tie.
- **W2c — mass-demote sweep**: RESOLVED 2026-09-01 (verified, no code
  needed). `_hygiene_demote_screened_models` (opencode.py:190, runs at
  boot) was already in place from the postponed batch; the audit trace
  shows ZERO learned exemplars from screened-out models and the live field
  carries 29 stamped rows, all glm-5.3-flash:cloud -- the dumb-model-era
  junk (qwen-coder hijack era) is already gone from the field (compactor
  + refute paths ate it). The sweep stays as a boot guard against future
  screen regressions; nothing to sweep today.

## W3: lane-discipline guardrails (friction #3: strictness earns its keep)

- **W3a — frame-lint at ingest**: DONE 2026-09-01 (`tests/test_frame_lint.py`,
  8 tests; full suite 210 OK). `_frame_lint` (opencode.py) scans every
  delivered assistant TEXT reply: stacked (2+ canon frames in one reply --
  only the first is servable, the rest poison learn) and buried (a canon-
  frame-looking line mid-prose that neither whole-message nor last-line
  scans can serve) both fire a `frame_discipline_violation` audit event
  (kinds + a short preview) and in-memory counters that ride
  `_frame_wait_summary` into the state report (`- frame discipline: N
  violation(s) (x stacked, y buried mid-prose)`). Fenced code is exempt
  (code legitimately contains frame-like lines); a single trailing frame is
  the disciplined form, never flagged. Violations are now visible and
  countable instead of only poisoning silently.
- **W3b — one-frame assert in dev**: DONE 2026-09-01
  (`tests/test_frame_lint.py` TestDevWarn, 3 tests; full suite 233 OK). A
  `--dev` flag: when the W3a lint flags a violation (stacked/buried canon
  frames), a ONE-SHOT marked warning (`[membrane-intercept] frame
  discipline: ...`) is planted on the server and rides the NEXT model-bound
  request (`_dev_warn_take`, consumed exactly once, never blocks — the
  reply is already served by lint time). Trains the habit at the source;
  boot line shows `dev: on (one-shot frame-discipline warnings)`.

## W4: provenance axis (split the trust, not the membrane)

The operator's follow-up plus the second seat's concurrence. A literal
internal/external split is rejected (fragments the one-geometry math, and
a split without an invalidation lane is worse than no split): "the field
is the conversation -- and the conversation is shared."

- **W4a -- per-cell `src` tag**: DONE 2026-09-01
  (`tests/test_provenance.py`, 12 tests; full suite 233 OK). Every learned
  cell now carries `extra["src"]` (wire: top-level `src`, only when set --
  legacy rows stay byte-identical). Earned at learn time via
  `learn_pair(src=...)`: `ground` for tool RESULT captures (the
  call->result hook signs the environment saw it), `model` for agent
  notes/MCP writes/delegate captures, `ingest` for docs/warmup cold-start,
  `user` the default (operator turns, teed Q/A, cell pulls). Surfaced on
  every recall hit (`[src: ground -- environment-verified]` in the chat
  grounding block, `[src: ...]` in `_recall_report` + the MCP query tool
  + peer-ask JSON) with the trust doctrine line (ground > user > model).
  Trust order lives in supersede-on-confirm: a ground dup is only
  corrected by a ground-signaled answer; model claims never supersede
  ground (`_src_get`/`_src_of` in runtime.py).
- **W4b -- the user ledger**: DONE 2026-09-01 (`membrane/ledger.py`,
  `tests/test_ledger.py`, 8 tests; full suite 233 OK). Append-only
  verbatim JSONL at `<field>.ledger.jsonl` (`{"ts","text"}`), written ONLY
  by the operator learn executor (`_learn_report` -- reached from USER
  turns; agent notes/MCP/captures never touch it), model-readable,
  user-editable on disk (mtime-keyed cache re-reads a hand-pruned file;
  corrupt lines skipped, never fatal). Recalled VERBATIM when near the
  current turn: a `[membrane-ledger]` marked block rides the model-bound
  request next to the RAG context, labeled "law, not recall". State
  report + boot line show the count.
- **W4c (parked, evidence-conditional) -- model-side scratch field**:
  ONLY if self-feed collapse is OBSERVED (not feared): move model scratch
  (intents, working lessons) to a model-side field, invalidation lane
  designed day one.

## E1 (parked, evidence-first): embeddings leverage -- DONE 2026-09-01

- DONE (experiments/e1_nearmiss.py, commit b888e7a). Script-first replay of
  the 92 near-miss escalations (86 distinct queries, 22% of escalations)
  graded against the ground truth we hold -- the model's eventual answer.
  (a) lower sim_min (0.80/0.84): flips are 86-95% WRONG -- the retrieved
  servable row is a different conversation beat (greetings are mutually
  similar; continuations are context-bound). (b) context-prepended text:
  WORSE (0% precision) -- prepending the prior turn drags the match onto
  the prior beat. Complaints land on only 14/92 near-miss queries:
  escalate was the right call; the band is honest uncertainty, not lost
  replays. The near-miss matches are dominated by machinery captures
  flooding the field (3319 tool + 232 reasoning vs 243 servable rows) --
  the capture-machinery quota (postponed batch) is the lever, already
  shipped. No threshold change, no embedder swap; W4c stays parked
  (no self-feed collapse observed).

## Order of attack after reboot

1. V0 verification (above) -- 10 min.
2. W1a + W1b (smallest UX win, unblocks trust in the wait).
3. W2a + W2b (stale-cell defense completes what the tier started).
4. W3a (make discipline violations visible).
5. W4a + W4b provenance axis (after the current plan items land; W4a
   pairs with W2a, W4b is standalone).
6. E1 experiment (near-miss replay) -- before any embedder talk.