# The membrane monologue + memory-flow banner

The `[membrane]` block in your thinking channel is the proxy's per-turn
machinery log — streamed as reasoning deltas, rendered exactly like
`/thinking`, always separated from the model's own reasoning.

## What runs now

The banner is **vitals + alerts + injections**, not a route/sim
recitation: the field's health lives in `field state` (an intercept, or
`membrane doctor`); the banner shows what THIS turn moved.

- **WHERE WE ARE** (`_where_we_are`) — the seat's living present: the
  entity tracks' stories, one line per planted track (`history beats +
  latest: <the newest sentence that moved it>`), each rendered only when
  **something changed** since its last render (unchanged → silence). The
  `session:now` track is the default — the entities that moved most this
  session.
- **track alerts + entity announcements** (exactly-once drains):
  `ALERT: <label> crossed its boundary (…)` when a tracked entity's
  mention count enters/exits its alert boundary, and
  `new entity: <eid> [<class>] <first line>` when the night or the model
  gave birth to a store doc.
- **the state diff** (normal mode): which entity states moved since the
  last message the LLM processed — conversation turns counted, everything
  else (code, entity scripts, other seats) quoted. This is the
  "SINCE YOUR LAST MESSAGE" block; it rides the banner on every turn it
  has something to say.
- **`[membrane] injecting this turn:`** — the recall block (the entities
  the ask names, their evidence verbatim) delivered as a visible marked
  block, so you can see what grounded the model.
- **`[membrane] FIELD-ONLY:`** — when no model rung answered (ladder down,
  rungs cooling): the turn answered from lived memory alone, honestly
  marked stale-risk, never learned.

The gate's verdict + replay A/B lines ride the state report and the audit;
the gate itself runs every turn (see entity-field.md's "The gate and the
replay A/B") — only its story is `field state`'s, not the banner's.

## The rendering covenant

Every lane is event-conditional or silent. Raw tool output (pytest bars,
ps listings, proxy JSON tables, log lines) and config dumps are junk —
never rendered. Fewer bytes by EARNING, not by hiding.

## Toggle

`/monologue` in opencode (or send `membrane: monologue on|off|toggle` as a
plain message). Handled pre-gate, zero model calls. Default ON.
`--no-banner` removes the banner lane entirely (recall content stays).