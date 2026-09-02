# The membrane monologue + memory-flow banner

The `[membrane]` block in your thinking channel is the proxy's per-turn
machinery log — streamed as reasoning deltas, rendered exactly like
`/thinking`, always separated from the model's own reasoning.

## What each line means

```
[membrane] route=escalate sim=0.62 field=5101 eff_rank=179.5 free_energy=181.3 safe=99% novelty=off(0.04)
```
- `route` — the gate verdict: `act-replayed` (served from memory),
  `escalate` (model call), `escalate-with-hint` (model call with a demoted
  memory as hint), `teed-pair` (a tool result learned), `settle-replay`.
- `sim` — cosine of the best memory match for this turn.
- `field` — exemplar count. `eff_rank` — the field's variety (how many
  independent memory directions); NOT intelligence, a health gauge.
- `free_energy` — variety weighted by trust (safe_frac); the value number.
- `safe=NN%` — the trusted fraction of the field.
- `novelty=on/off(ratio)` — whether the novelty gate is currently allowing
  new memories in.

```
[membrane] router: <model> (<kind>) — <why>
```
The escalation router's pick and its reason (`cloud-floor`, `earned-local`,
`operator pick: <name>`, `prefer-local: local first`...). On an
`autoselect`/`autoselect-local` selection the router IS the chooser.

```
[membrane] gave 2 slice(s) to the LLM: "topic one"; "topic two"
```
What the field INJECTED into the model's context this turn — the actual
memory topics, so you can see what the model is being grounded on.

```
[membrane] learned #5141: how to deploy the worker via flyctl
[membrane] learn: dedup'd into #5122 (no new cell)
```
What the turn did to the field: a new cell (+index +topic), a dedup merge,
a repair of a demoted cell, a candidate promotion, or a guard skip
(vague/variety). The field's write-side ledger, per turn.

```
[night] demoted 12 (gray 3) of 200
[night] drifted 5 entities (avg 0.031), materialized 2
```
The compactor night's gate note (rides the banner's memory-flow): the
maintenance clock's last pass — the junk sweep's demotions (consensus
screened, blast-radius capped, origin-tagged, overturnable), the
gray-band registration's grown anchors, and the entity trackers'
measured drift. The field cleaning itself, visible.

```
[membrane] gate: re-ask vote (cosine, sim 0.91) on #88
[membrane] gate: DEMOTED #88 (reask=2 contest=0)
```
Quality machinery: re-ask votes, demotions, judge contests.

## The track lines (banner lane)

```
me (glm-5.3-flash:cloud): began with <beat>, then <beat>, and most
recently: <beat>. Around it: <neighbor> (3 shared), <neighbor> (2 shared)
[shared cells].
```
One MIXED line per planted entity track:
- **History half** — fresh-aligned beats (cells scored by cosine to the
  current turn + a freshness credit that halves every 6h), chronological,
  newest anchored last. If the newest beat is >24h old the history is
  SILENT (silence beats a stale story).
- **Structure half** ("Around it: ...") — the entity's neighborhood:
  co-occurring entities (cells mentioning both — a real graph edge) or,
  for young entities, embedding-centroid nearness. Rendered only when the
  neighborhood CHANGED since the last render; unchanged -> silence.
- **ALERT lines** — boundary transitions (`enter N`/`exit N` on the live
  safe-cell count), one alert per transition, re-entry re-arms, rendered
  from the exactly-once drain.

The rendering covenant: every lane is event-conditional or silent. Raw
tool output (pytest bars, ps listings, proxy JSON tables, log lines),
teed meta-voice ("The user is laughing about ..."), config dumps, and
horoscope suffixes ("Moving fast — 2 changes in 7h") are junk — never
rendered. Fewer bytes by EARNING, not by hiding.

## Toggle

`/monologue` in the TUI (or send `membrane: monologue on|off|toggle` as a
plain message). Handled pre-gate, zero model calls. Default ON.