"""The in-window review surface -- what I (Claude, in the window) run to see what
Cortex learned. Passive supervision, not user labeling.

The honest "notify you in this window" mechanism: an outside process cannot push into
the Claude Code window, so Cortex "notifies" by appending to the audit log, and I notice
by reading it -- on demand, when the user asks (or, later, on a scheduled poll). This
module is that read: it joins the audit log (decisions + outcome events + contest
events) to the field and prints what the training loop did -- how many absorbed vs
escalated, which absorbs drew a re-ask vote, which demotions the model contested, and
the current state of the field's two-authority evidence (reask vs contest counts).

The boundary is two authorities: the environment (re-ask = "this replay was not useful")
and the model (contest = "my answer was correct anyway"). `safe` is derived from the net
of their votes; the review shows both sides so neither can silently overrule the other.
The live model-judge (Cortex calling a fresh me per outcome via the MCP inversion) is
deferred; until then this on-demand read is the channel, and the passive re-ask self-train
(cortex/cortex.py ``_detect_reask``) keeps the field honest without anyone reading it.

Run:
  python -m cortex.review --audit cortex-audit.jsonl --field cortex-field.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(audit_path: str | Path, field_path: str | Path) -> str:
    """Read the audit + field and return a human-readable training-loop summary."""
    audit_path = Path(audit_path)
    field_path = Path(field_path)

    decisions = []
    outcomes = []
    contests = []
    if audit_path.exists():
        for ln in audit_path.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ev = rec.get("event", "decision")          # old audit lines have no event -> decision
            if ev == "outcome":
                outcomes.append(rec)
            elif ev == "contest":
                contests.append(rec)
            else:
                decisions.append(rec)

    field = []
    if field_path.exists():
        try:
            field = json.loads(field_path.read_text())
        except json.JSONDecodeError:
            field = []

    n_act = sum(1 for d in decisions if d.get("verdict") == "act")
    n_hint = sum(1 for d in decisions if d.get("verdict") == "escalate-with-hint")
    n_esc = sum(1 for d in decisions if d.get("verdict") == "escalate")
    learned = sum(1 for d in decisions if d.get("learned"))

    unsafe = [(i, e) for i, e in enumerate(field) if not e.get("safe", True)]
    contested = [(i, e) for i, e in enumerate(field) if e.get("contest_count", 0) > 0]

    lines = []
    lines.append("cortex training-loop review")
    lines.append("=" * 40)
    lines.append(f"field:        {len(field)} exemplars")
    lines.append(f"decisions:    {len(decisions)} "
                 f"(absorbed {n_act} / with-hint {n_hint} / escalated {n_esc})")
    lines.append(f"learned:      {learned} exemplars appended from escalations")
    lines.append(f"re-asks:      {len(outcomes)} (environment votes against an absorb)")
    lines.append(f"contests:     {len(contests)} (model votes for a demoted answer)")
    lines.append("")
    if outcomes:
        lines.append("re-ask votes (the environment checking the model):")
        for o in outcomes:
            ex = o.get("ex_idx")
            s = o.get("sim")
            sim_s = f"{s:.3f}" if isinstance(s, (int, float)) else "?"
            safe_s = "demoted" if not o.get("safe", True) else "still-safe"
            lines.append(f"  - exemplar #{ex}: reask={o.get('reask_count')} "
                         f"contest={o.get('contest_count')} -> {safe_s} "
                         f"(sim={sim_s})  q={str(o.get('query'))[:50]!r}")
    else:
        lines.append("re-ask votes: none (no wrong absorbs caught yet)")
    lines.append("")
    if contests:
        lines.append("contests (the model checking the environment):")
        for c in contests:
            ex = c.get("ex_idx")
            source = c.get("source", "?")
            restore = c.get("restore", True)
            first = c.get("first", True)
            if c.get("safe", True):
                effect = "restored (replayable)"
            elif restore:
                effect = "filed, net votes still demote"
            else:
                effect = "record-only, demotion stands"
            tag = f"{source}"
            if source == "judge":
                tag += "/first-veto" if first else "/record-only"
            lines.append(f"  - exemplar #{ex} [{tag}]: reask={c.get('reask_count')} "
                         f"contest={c.get('contest_count')} -> {effect}  "
                         f"reason={str(c.get('reason'))[:45]!r}")
    else:
        lines.append("contests: none (the model has not vetoed a demotion)")
    lines.append("")
    if unsafe:
        lines.append(f"unsafe exemplars now ({len(unsafe)}) -- escalate-with-hint, not replayed:")
        for i, e in unsafe:
            lines.append(f"  - #{i}  reask={e.get('reask_count',0)} "
                         f"contest={e.get('contest_count',0)}  q={str(e.get('situation'))[:50]!r}")
    else:
        lines.append("unsafe exemplars: none (every exemplar is safe-to-replay)")
    if contested:
        lines.append("")
        lines.append(f"contested exemplars ({len(contested)}) -- the model stood by these:")
        for i, e in contested:
            lines.append(f"  - #{i}  reask={e.get('reask_count',0)} "
                         f"contest={e.get('contest_count',0)}  safe={e.get('safe',True)}  "
                         f"q={str(e.get('situation'))[:50]!r}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Review what the Cortex training loop learned.")
    ap.add_argument("--audit", default="cortex-audit.jsonl", help="path to the audit log")
    ap.add_argument("--field", default="cortex-field.json", help="path to the exemplar field")
    args = ap.parse_args()
    print(summarize(args.audit, args.field))


if __name__ == "__main__":
    main()