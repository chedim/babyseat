"""The in-window review surface -- what I (Claude, in the window) run to see what
Cortex learned. Passive supervision, not user labeling.

The honest "notify you in this window" mechanism: an outside process cannot push into
the Claude Code window, so Cortex "notifies" by appending to the audit log, and I notice
by reading it -- on demand, when the user asks (or, later, on a scheduled poll). This
module is that read: it joins the audit log (decisions + outcome events) to the field
and prints what the training loop did -- how many absorbed vs escalated, which absorbs
got flipped to unsafe by a re-ask, and the current state of the field.

The model-judges-the-audit half (Cortex calling a fresh me per outcome via the MCP
inversion) is deferred; until then this on-demand read is the channel, and the passive
re-ask self-train (cortex/cortex.py ``_detect_reask``) is what keeps the field honest
without anyone reading it.

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

    lines = []
    lines.append("cortex training-loop review")
    lines.append("=" * 40)
    lines.append(f"field:        {len(field)} exemplars")
    lines.append(f"decisions:    {len(decisions)} "
                 f"(absorbed {n_act} / with-hint {n_hint} / escalated {n_esc})")
    lines.append(f"learned:      {learned} exemplars appended from escalations")
    lines.append(f"self-trains:  {len(outcomes)} (re-asks that flipped an absorb to unsafe)")
    lines.append("")
    if outcomes:
        lines.append("outcome events (the field self-correcting):")
        for o in outcomes:
            ex = o.get("ex_idx")
            s = o.get("sim")
            sim_s = f"{s:.3f}" if isinstance(s, (int, float)) else "?"
            lines.append(f"  - exemplar #{ex} -> {o.get('outcome')} "
                         f"(re-ask sim={sim_s})  q={str(o.get('query'))[:60]!r}")
    else:
        lines.append("outcome events: none (no wrong absorbs caught yet)")
    lines.append("")
    if unsafe:
        lines.append(f"unsafe exemplars now ({len(unsafe)}) -- escalate-with-hint, not replayed:")
        for i, e in unsafe:
            lines.append(f"  - #{i}  q={str(e.get('situation'))[:60]!r}")
    else:
        lines.append("unsafe exemplars: none (every exemplar is safe-to-replay)")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Review what the Cortex training loop learned.")
    ap.add_argument("--audit", default="cortex-audit.jsonl", help="path to the audit log")
    ap.add_argument("--field", default="cortex-field.json", help="path to the exemplar field")
    args = ap.parse_args()
    print(summarize(args.audit, args.field))


if __name__ == "__main__":
    main()