"""The in-window review surface + metrics -- what I (Claude, in the window) run to see
what Membrane learned and whether it is working. Passive supervision, not user labeling.

The honest "notify you in this window" mechanism: an outside process cannot push into
the Claude Code window, so Membrane "notifies" by appending to the audit log, and I notice
by reading it -- on demand. This module is that read: it joins the audit log (decisions +
re-ask outcomes + contests) to the field and prints what the training loop did -- the
two authorities (re-ask vs contest) AND the metrics that say whether the proxy is earning
its keep (the hit/miss ratio, the detected wrong-absorb rate, the absorb rate, the
token savings, and the judge's precision).

THE GROUND-TRUTH ASYMMETRY (the one honesty constraint that shapes the metrics): Membrane's
only ground-truth signal is the re-ask. A re-ask is a HARD NEGATIVE (the absorb was wrong --
proven). The absence of a re-ask is a SOFT POSITIVE (the user was probably satisfied -- but
silence is not approval). So:
  - misses are DETECTED (a re-ask caught a wrong absorb) -> the miss count is a LOWER BOUND
    (wrong absorbs that nobody re-asked are invisible);
  - hits are INFERRED (an absorb never re-asked) -> the hit count is an UPPER BOUND
    (some "hits" are merely unconfirmed).
The "is it working" number is the PRODUCT of efficiency and safety: a high absorb rate
that is ALSO a low detected-miss rate. The wrong-absorb rate is reported as a lower bound.

Run:
  python -m membrane.review --audit membrane-audit.jsonl --field membrane-field.json          # narrative
  python -m membrane.review --audit ... --field ... --metrics                              # numbers only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(audit_path: str | Path, field_path: str | Path):
    """Read the audit (in log order) + field. Returns (ordered, decisions, outcomes,
    contests, field), where `ordered` is every audit record in file order (for
    "did a re-ask happen AFTER this restore?" timeline queries)."""
    audit_path = Path(audit_path)
    field_path = Path(field_path)
    ordered, decisions, outcomes, contests = [], [], [], []
    if audit_path.exists():
        for ln in audit_path.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ordered.append(rec)
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
    return ordered, decisions, outcomes, contests, field


def compute_metrics(decisions, outcomes, contests, field):
    """The numbers that say whether Membrane is working. All rates are over the audit log.

    Two authorities -> two miss axes (this is the subtlety a single ratio hides):
      - USEFULNESS miss (env axis): the user re-asked -> the absorb didn't satisfy. ANY re-ask.
      - CORRECTNESS miss (model axis): the re-ask STOOD (judge accepted / couldn't save) -> the
        answer was actually wrong. A re-ask the judge OVERTURNED (restored) was NOT a bad answer;
        it was a user misunderstanding, and is a usefulness-miss but NOT a correctness-miss.

    Metrics:
      - absorb_rate   = act / decisions        -- efficiency: turns that skipped the model.
      - reask_rate    = reasked_ex / absorbed_ex  -- USEFULNESS (env axis): fraction of replayed
                        answers the user re-asked (lower bound; un-re-asked dissatisfactions are invisible).
      - wrong_absorb  = stood_ex / absorbed_ex    -- SAFETY (model axis, lower bound): fraction of
                        replayed answers CONFIRMED wrong (re-asked AND not restored). Judge-overturned
                        re-asks are NOT counted here -- the answer was correct.
      - hit/miss      = (absorbed_ex - reasked_ex) : reasked_ex  -- usefulness-axis hit/miss.
                        hit = replayed & never re-asked (presumed good, UPPER bound);
                        miss = replayed & re-asked (the env pushed back).
      - value         = absorb_rate * (1 - wrong_absorb) -- the one "is it working" number: absorbs-per-turn
                        that were detectably CORRECT. Judge saves don't hurt it (a restored answer was right).
      - tokens_saved  ~= absorbs * avg escalated response length / 4 -- cost avoided (estimate).
      - judge_prec    = restores not re-asked again / restores -- the live judge's precision (a restore
                        that gets re-asked again was the judge defending a bad answer = wrong).
    """
    n_dec = len(decisions)
    n_act = sum(1 for d in decisions if d.get("verdict") == "act")
    n_hint = sum(1 for d in decisions if d.get("verdict") == "escalate-with-hint")
    n_esc = sum(1 for d in decisions if d.get("verdict") == "escalate")
    absorb_rate = (n_act / n_dec) if n_dec else 0.0

    absorbed = {d.get("ex_idx") for d in decisions
                if d.get("verdict") == "act" and d.get("ex_idx") is not None}
    reasked = {o.get("ex_idx") for o in outcomes if o.get("ex_idx") is not None}
    n_absorbed = len(absorbed)
    n_reasked = len(reasked)
    # split the re-asked by final safe state: stood (demoted & stayed = confirmed wrong) vs
    # overturned (judge restored = the answer was correct, re-ask was a misunderstanding)
    stood = overturned = 0
    for ex in reasked:
        if ex is not None and 0 <= ex < len(field):
            overturned += 1 if field[ex].get("safe", True) else 0
            stood += 0 if field[ex].get("safe", True) else 1
    n_hit = n_absorbed - n_reasked                # never re-asked (presumed good)
    reask_rate = (n_reasked / n_absorbed) if n_absorbed else 0.0
    wrong_absorb = (stood / n_absorbed) if n_absorbed else 0.0
    hit_per_miss = (n_hit / n_reasked) if n_reasked else float("inf")
    value = absorb_rate * (1.0 - wrong_absorb)

    esc_lens = [len(d.get("response", "")) for d in decisions
                if d.get("verdict") != "act" and d.get("response")]
    avg_chars = (sum(esc_lens) / len(esc_lens)) if esc_lens else 0.0
    est_tokens_saved = int(n_act * avg_chars / 4)   # ~4 chars/token, rough

    # judge precision: a restore (post-contest safe=True) that is re-asked AGAIN afterward was
    # the judge defending a bad answer. Test: final field reask_count > reask_count at the restore.
    n_contests = len(contests)
    n_record_only = sum(1 for c in contests if c.get("restore", True) is False)
    restored = [c for c in contests if c.get("safe", False)]   # post-contest safe True == restored it
    judge_right = judge_wrong = 0
    for c in restored:
        ex = c.get("ex_idx")
        at_restore = c.get("reask_count", 0)
        final = field[ex].get("reask_count", 0) if (ex is not None and 0 <= ex < len(field)) else at_restore
        judge_wrong += 1 if final > at_restore else 0
        judge_right += 0 if final > at_restore else 1
    judge_precision = (judge_right / (judge_right + judge_wrong)) if restored else None

    return {
        "decisions": n_dec, "act": n_act, "hint": n_hint, "esc": n_esc,
        "absorb_rate": absorb_rate,
        "absorbed_ex": n_absorbed, "hit": n_hit, "miss": n_reasked,
        "stood": stood, "overturned": overturned,
        "reask_rate": reask_rate, "wrong_absorb_rate": wrong_absorb,
        "hit_per_miss": hit_per_miss,
        "value": value, "tokens_saved_est": est_tokens_saved,
        "contests": n_contests, "record_only": n_record_only,
        "judge_right": judge_right, "judge_wrong": judge_wrong, "judge_precision": judge_precision,
    }


def _fmt_metrics(m: dict) -> list[str]:
    def pct(x):
        return f"{x * 100:.1f}%" if x else "0.0%"
    lines = []
    lines.append("metrics -- is it working?")
    lines.append("-" * 40)
    lines.append(f"decisions:    {m['decisions']} (absorbed {m['act']} / with-hint {m['hint']} / escalated {m['esc']})")
    lines.append(f"absorb rate:  {pct(m['absorb_rate'])}  (efficiency -- turns that skipped the model)")
    lines.append("")
    lines.append(f"hit / miss:   {m['hit']} : {m['miss']}  (usefulness axis)"
                 + (f"  ({m['hit_per_miss']:.1f} hits per miss)" if m['miss'] else "  (no re-asks)")
                 + (f"  [misses: {m['stood']} stood / {m['overturned']} overturned by judge]"
                    if m['miss'] else ""))
    lines.append(f"re-ask rate:  {pct(m['reask_rate'])}  (usefulness, lower bound -- the env pushed back)")
    lines.append(f"wrong-absorb: {pct(m['wrong_absorb_rate'])}  (SAFETY, lower bound -- confirmed wrong; "
                 f"judge saves excluded)")
    lines.append(f"value:        {pct(m['value'])}  (absorb rate x (1 - wrong-absorb)) -- the one number")
    lines.append(f"tokens saved: ~{m['tokens_saved_est']:,}  (absorbs x avg escalation length / 4, estimate)")
    jp = m["judge_precision"]
    if jp is not None:
        lines.append(f"judge prec.: {pct(jp)}  ({m['judge_right']} right / {m['judge_wrong']} wrong -- "
                     f"restores not re-asked again)")
    elif m["contests"]:
        lines.append("judge prec.: n/a (contests filed but none restored)")
    return lines


def summarize(audit_path: str | Path, field_path: str | Path) -> str:
    """Read the audit + field and return a human-readable training-loop summary + metrics."""
    ordered, decisions, outcomes, contests, field = _load(audit_path, field_path)

    n_act = sum(1 for d in decisions if d.get("verdict") == "act")
    n_hint = sum(1 for d in decisions if d.get("verdict") == "escalate-with-hint")
    n_esc = sum(1 for d in decisions if d.get("verdict") == "escalate")
    learned = sum(1 for d in decisions if d.get("learned"))

    unsafe = [(i, e) for i, e in enumerate(field) if not e.get("safe", True)]
    contested = [(i, e) for i, e in enumerate(field) if e.get("contest_count", 0) > 0]

    lines = []
    lines.append("membrane training-loop review")
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

    lines.append("")
    m = compute_metrics(decisions, outcomes, contests, field)
    lines.extend(_fmt_metrics(m))
    return "\n".join(lines)


def metrics(audit_path: str | Path, field_path: str | Path) -> str:
    """The numbers only -- the 'is it working' readout."""
    _, decisions, outcomes, contests, field = _load(audit_path, field_path)
    return "\n".join(_fmt_metrics(compute_metrics(decisions, outcomes, contests, field)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Review what the Membrane training loop learned.")
    ap.add_argument("--audit", default="membrane-audit.jsonl", help="path to the audit log")
    ap.add_argument("--field", default="membrane-field.json", help="path to the exemplar field")
    ap.add_argument("--metrics", action="store_true", help="print only the metrics readout")
    args = ap.parse_args()
    print(metrics(args.audit, args.field) if args.metrics else summarize(args.audit, args.field))


if __name__ == "__main__":
    main()