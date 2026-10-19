"""#246: can a model told the RECENT reply rate beat habit on rung 7?

Replays the reply checks of a COPY of self's record (never the live file):

    cp <seat>/.membrane/self/record.jsonl experiments/rung7_recent/record.copy.jsonl
    python experiments/rung7_recent/replay.py experiments/rung7_recent/record.copy.jsonl

For every reply check at the other (the look RUNG7_CHECK_S after a push) it
scores, with only what was known BEFORE that check:

- habit      the p self actually recorded on the look (one-bit-ish memory)
- all-time   (k+1/2)/(n+1) over all earlier reply checks (#217's base rate)
- recent     (k+1/2)/(n+1) over the last RECENT_N reply checks (#246)
- switch     recent when it differs from all-time by > SWITCH, else all-time
- blend      w*recent + (1-w)*all-time, for a few w
- gap        the earlier reply checks' rate split by "self saw them speak
             within GAP_S before the push" -- the time-since-last-spoke fact
- last bit   the previous reply check's outcome, Jeffreys-smoothed per state
             (P(changed | last changed), P(changed | last unchanged))

and prints each one's Brier and rung-7 skill vs habit, (B_habit - B)/B_habit,
over all reply checks, over the last RUNG7_LOOKS, and over the checks that
were really paired with a model answer. Rung 7 passes at skill >= 0.15.
"""
import json
import sys

RECENT_N = 6
GAP_S = 600.0
MARGIN = 0.15
LOOKS = 20
SWITCH = 0.15            # "weigh the recent window when it disagrees": by more than this


def jeff(k, n):
    return (k + 0.5) / (n + 1.0)


def load(path):
    return [json.loads(ln) for ln in open(path) if ln.strip()]


def checks(recs):
    """The other's reply checks, oldest first, each with what was known before
    it: the outcome, habit's p, the model's p_llm (if paired), and the time
    since self last saw the other speak at the push."""
    rc = [r for r in recs if r.get("kind") == "reach" and r.get("reply") and not r.get("audit")
          and r.get("answer") in ("changed", "unchanged")]
    if not rc:
        return []
    eid = rc[-1]["eid"]
    looks = [r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid and not r.get("audit")
             and r.get("answer") in ("changed", "unchanged")]
    pushes = [r for r in recs if r.get("kind") == "push" and r.get("to") == eid]
    out = []
    for r in (x for x in rc if x["eid"] == eid):
        t = float(r["ts"])
        push = max((p for p in pushes if float(p["ts"]) < t), key=lambda p: p["ts"], default=None)
        t0 = float(push["ts"]) if push else t
        spoke = [x for x in looks if x["answer"] == "changed" and float(x["ts"]) < t0]
        out.append({"o": 1.0 if r["answer"] == "changed" else 0.0, "p": float(r["p"]),
                    "p_llm": r.get("p_llm"), "gap": (t0 - float(spoke[-1]["ts"])) if spoke else None})
    return out


def predictors(cs):
    """Each predictor's p per check, from the checks before it only."""
    preds = {"habit": [c["p"] for c in cs], "coin 0.5": [0.5] * len(cs), "all-time": [], "recent": [],
             "switch": [], "gap": [], "last bit": []}
    for w in (0.5, 0.67, 0.8):
        preds[f"blend w={w}"] = []
    for i, c in enumerate(cs):
        past = [x["o"] for x in cs[:i]]
        a = jeff(sum(past), len(past))
        w6 = past[-RECENT_N:]
        r = jeff(sum(w6), len(w6))
        preds["all-time"].append(a)
        preds["recent"].append(r)
        preds["switch"].append(r if abs(r - a) > SWITCH else a)   # #246's sentence, read literally
        for w in (0.5, 0.67, 0.8):
            preds[f"blend w={w}"].append(w * r + (1 - w) * a)
        near = c["gap"] is not None and c["gap"] <= GAP_S
        same = [x["o"] for x in cs[:i] if (x["gap"] is not None and x["gap"] <= GAP_S) == near]
        preds["gap"].append(jeff(sum(same), len(same)))
        if i == 0:
            preds["last bit"].append(0.5)
        else:
            prev = past[-1]
            follow = [past[j + 1] for j in range(len(past) - 1) if past[j] == prev]
            preds["last bit"].append(jeff(sum(follow), len(follow)))
    return preds


def brier(ps, os_):
    return sum((p - o) ** 2 for p, o in zip(ps, os_)) / len(os_)


def table(cs, preds, idx, title):
    os_ = [cs[i]["o"] for i in idx]
    bh = brier([preds["habit"][i] for i in idx], os_)
    rows = [f"### {title} (n={len(idx)}, answered {int(sum(os_))})", "",
            "| predictor | Brier | skill vs habit | passes +0.15 |", "|---|---|---|---|"]
    for name, ps in preds.items():
        b = brier([ps[i] for i in idx], os_)
        s = (bh - b) / bh if bh > 1e-9 else 0.0
        rows.append(f"| {name} | {b:.4f} | {s:+.3f} | {'yes' if s >= MARGIN else 'no'} |")
    return "\n".join(rows) + "\n"


def main(path):
    cs = checks(load(path))
    preds = predictors(cs)
    paired = [i for i, c in enumerate(cs) if c["p_llm"] is not None][-LOOKS:]
    preds_p = dict(preds)
    print(f"{len(cs)} reply checks, {int(sum(c['o'] for c in cs))} answered; "
          f"{len(paired)} paired with a model answer\n")
    o = [c["o"] for c in cs]
    print("outcomes:", "".join("1" if x else "0" for x in o))
    after1 = [o[j + 1] for j in range(len(o) - 1) if o[j]]
    after0 = [o[j + 1] for j in range(len(o) - 1) if not o[j]]
    print(f"lag-1: P(1 | 1) = {sum(after1) / len(after1):.3f} ({len(after1)}), "
          f"P(1 | 0) = {sum(after0) / len(after0):.3f} ({len(after0)})\n")
    print(table(cs, preds, list(range(len(cs))), "all reply checks"))
    print(table(cs, preds, list(range(len(cs)))[-LOOKS:], f"last {LOOKS} reply checks"))
    if paired:
        preds_p["model (recorded p_llm)"] = [c["p_llm"] if c["p_llm"] is not None else 0.0 for c in cs]
        print(table(cs, preds_p, paired, f"the last {LOOKS} paired checks (what rung 7 reads)"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "experiments/rung7_recent/record.copy.jsonl")
