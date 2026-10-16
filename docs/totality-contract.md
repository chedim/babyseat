# The totality contract

> One page. The invariants that keep a write finite, deterministic and
> auditable — the ones membrane already enforces, and two it deliberately
> declines to take on yet (a cost, not a debt). Not a research claim: a
> checklist.

Membrane's command core is not Turing-complete; it is a bounded, feed-forward
dispatch core with quarantined escapes. That is deliberate, not a gap: every
guarantee below — termination, audit, replay — rests on it. The
non-totality lives in the escapes (shell, Python), which is exactly why they
are quarantined, timed and capped.

## What this contract is (and is not)

These are **per-transition** invariants: they hold for each write, each call,
each script pass. They are **not** a global totality proof — membrane's state
space is unbounded (an open, LLM-driven field), so there is no closed
state × input table to prove total. Totality here is *local, enforced by
caps*; the global version (a closed FSM, a total transition function) is a
different, smaller object. The external formalization of that global version
is SRS-002 ("Agent Totality & Health FSM Specification"); membrane borrows its
discipline, not its closed FSM.

The ambient minds — the self loop, the thinker, the dreamer, the reaper —
iterate in Python. They are the *harness* that schedules transitions, not the
transitions themselves. The boundedness invariant binds the transition, not
the scheduler that keeps coming back.

## The invariants

### 1. Every transition is bounded. — ENFORCED

A transition — a call, a composite, a fan-out, a script pass, a command —
runs in bounded steps and bounded time: no recursion, no unbounded iteration.

- composite nesting ≤ 3 (`night.py`, `COMPOSITE_DEPTH` — "feed-forward, not
  Turing-complete")
- ancestry walk ≤ 16 deep, ≤ 12 nodes a pass (`EXEC_WALK_DEPTH`,
  `EXEC_WALK_NODES_CAP`; `entities.py`, `MAX_CHAIN`)
- fan-out ≤ 150 members (`EACH_MAX_CEILING`); ≤ 8 parents an entity
  (`MAX_PARENTS`)
- a command times out at 600 s (`RUN_TIMEOUT_MAX_S`); a script pass runs at
  most 20 scripts (`SCRIPT_MAX_PER_PASS`) and auto-disables after 3 straight
  failures (`SCRIPT_MAX_FAILURES`)

### 2. No mutation without a committed record. — ENFORCED

A write is guarded and audited before it lands: dedup, the variety gate,
length limits, fenced-code refusal, junk cut. Every write carries its origin
(`agent` / `mcp` / `night` / `operator`) into `audit.jsonl`, and every claim
traces back to the events that earned it. "Every write stands trial" is the
rule, not the convention.

### 3. Nondeterminism enters only through admitted observations. — DEFERRED (a product decision)

Time, randomness and IO MUST be admitted as logged observations, never read
directly. Today this does **not** hold: entity scripts are unsandboxed
("scripts may import/shell anything") and read `time.time()`, RNG and the
field directly. Until this holds, the replay critic is advisory, not exact —
the same log replayed twice reads a different clock.

**The cost is the point worth naming.** Closing this re-sandboxes entity
scripts, reversing the operator's 2026-10-01 call (the sandbox was lifted on
purpose). Scripts are the field's senses — the getter that reads the real
clock, the sensor, the shell. Making them pure functions over logged inputs
turns the open field into a deterministic core: the "different, smaller
object" this document explicitly declines to be. That is not a checklist
item; it is a decision about what membrane is, and it is not this document's
to make.

### 4. A replayed log reproduces the field. — DEFERRED (gated on 3)

Event records MUST be canonicalized (RFC 8785 / JCS), and a `replay-verify`
check MUST reproduce the field from the log bit-for-bit. Today the audit is
append-only but not JCS-canonicalized, and there is no replay-equivalence
assertion — `membrane reembed` re-applies the log in order but does not diff.

This is **not an independent item**: bit-for-bit replay is only possible once
invariant 3 holds — a replayed log that reads a different clock cannot
reproduce the field. Adopting 4 without 3 buys nothing; adopting 3 without a
product decision overpays. Take both together, or take neither.

## The quarantined escapes

Shell (`subprocess.run(shell=True)`) and Python (`exec`) are deliberately
Turing-complete, and deliberately outside this contract's totality. They are
*bounded*, not total: timeouts, pass caps, failure auto-disable. That
quarantine is the point — do not close it, and do not "reclaim" completeness
by promoting loops into the core. The system is complete only through these
escapes, and that is fine.

## References

- `doctrine.md` — the operating doctrine
- `docs/cliffhanger.md` — the command language this contract bounds
- `docs/ab-critic-scorecard.md` — the (dated, substrate-phase) replay critic
  invariant 3 would make sound
- `docs/crash-covenant.md` — fail-closed behavior
- SRS-002 "Agent Totality & Health FSM Specification" (axilog.io) — the
  external, global formalization this document borrows its discipline from
