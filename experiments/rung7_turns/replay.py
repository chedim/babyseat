"""#258: what the live record says about timing -- the timer era's replay.

Reads read-only COPIES (never the live files) of self's record and the seat's
chat store, and asks, for the pushes to the current other:

  - CONTINGENCY: how often a timer push landed within N s after the other
    last spoke, against the same pushes at shuffled moments (each push
    time shifted by one random offset, circularly over the span; 500 draws);
  - PAYOFF: the reply rate per spoke_age bin (rung 7's bins), where a reply
    is a line from the other AFTER the push and by its reply check -- and,
    next to it, the share of those checks that read `changed` (a look reads
    `changed` for a line from before the push self had not seen yet).

    D=/some/scratch/dir
    cp <seat>/.membrane/self/record.jsonl $D/record.copy.jsonl
    cp <seat>/.membrane/serve-msgs.json $D/serve-msgs.copy.json
    cp <seat>/.membrane/serve-sessions.json $D/serve-sessions.copy.json
    python -I experiments/rung7_turns/replay.py $D

The other's lines are the user messages (not voice) in the chat sessions
owned by the person the record's latest push went to. No text is read past
the message's role and time; nothing is printed that names anyone.
"""
import bisect
import json
import random
import sys

EDGES = (120.0, 600.0, 1800.0)              # self_loop.TURN_BINS
NAMES = ("0-2m", "2-10m", "10-30m", "30m+")


def tbin(age):
    if age is None:
        return 3
    for i, hi in enumerate(EDGES):
        if age < hi:
            return i
    return 3


def load(d):
    recs = [json.loads(ln) for ln in open(f"{d}/record.copy.jsonl") if ln.strip()]
    msgs = json.load(open(f"{d}/serve-msgs.copy.json"))
    sess = json.load(open(f"{d}/serve-sessions.copy.json"))
    return recs, msgs, sess


def lines_of(to, msgs, sess):
    """The other's lines (s), from the sessions its chat:in belongs to."""
    key = to.rsplit("chat:in:", 1)[-1]
    sids = {k for k, v in sess.items() if v.get("owner")
            and str(v["owner"]).strip().lower().replace(" ", "-") == key}
    return sorted(m["info"]["time"]["created"] / 1000.0 for sid in sids for m in msgs.get(sid, [])
                  if m["info"].get("role") == "user" and not m["info"].get("voiceIn"))


def main(d):
    recs, msgs, sess = load(d)
    pushes = [r for r in recs if r.get("kind") == "push"]
    to = pushes[-1]["to"]
    pushes = [p for p in pushes if p.get("to") == to]
    speak = lines_of(to, msgs, sess)
    looks = [r for r in recs if r.get("kind") == "reach" and r.get("eid") == to]
    lts = [float(r["ts"]) for r in looks]

    def last_spoke(t):
        i = bisect.bisect_right(speak, t)
        return speak[i - 1] if i else None

    def spoke_in(a, b):
        i = bisect.bisect_right(speak, a)
        return i < len(speak) and speak[i] <= b

    rows = []
    for p in pushes:
        t = float(p["ts"])
        i = bisect.bisect_right(lts, t)
        chk = looks[i] if i < len(looks) else None
        if chk is None or not chk.get("reply") or chk.get("answer") not in ("changed", "unchanged"):
            continue
        ls = last_spoke(t)
        rows.append({"age": (t - ls) if ls is not None else None, "replied": spoke_in(t, float(chk["ts"])),
                     "changed": chk["answer"] == "changed", "dt": float(chk["ts"]) - t})
    t0, t1 = float(pushes[0]["ts"]), float(pushes[-1]["ts"])
    print(f"pushes to the current other: {len(pushes)} over {(t1 - t0) / 3600:.1f} h "
          f"({len(pushes) / ((t1 - t0) / 3600):.1f}/h); their lines in that store: {len(speak)}")
    gaps = sorted(float(b["ts"]) - float(a["ts"]) for a, b in zip(pushes, pushes[1:]))
    print(f"push gap: median {gaps[len(gaps) // 2]:.0f} s, min {gaps[0]:.0f} s (the timer)")
    print(f"pushes with a reply check: {len(rows)} (median check {sorted(r['dt'] for r in rows)[len(rows) // 2]:.0f} s "
          f"after the push); replied {sum(r['replied'] for r in rows)}, the check read changed "
          f"{sum(r['changed'] for r in rows)}")
    print("\nCONTINGENCY: timer pushes landing within N s after the other spoke, vs shuffled moments")
    pts = [float(p["ts"]) for p in pushes]
    rng = random.Random(1)

    def share(ts, n):
        return sum(1 for t in ts if (ls := last_spoke(t)) is not None and t - ls <= n) / len(ts)
    for n in (120, 300, 600):
        null = sorted(share([t0 + ((t - t0 + rng.uniform(0, t1 - t0)) % (t1 - t0)) for t in pts], n)
                      for _ in range(500))
        print(f"  N={n:4d} s: {share(pts, n):.3f}   shuffled mean {sum(null) / len(null):.3f} "
              f"[q05 {null[25]:.3f}, q95 {null[475]:.3f}]")
    print("\nPAYOFF: reply rate per spoke_age bin (replied = a line after the push, by the check)")
    print("  bin       pushes  replied  rate   (check read changed)")
    for b, name in enumerate(NAMES):
        rs = [r for r in rows if tbin(r["age"]) == b]
        k = sum(r["replied"] for r in rs)
        c = sum(r["changed"] for r in rs)
        print(f"  {name:8s} {len(rs):6d}  {k:7d}  {k / max(1, len(rs)):.2f}   ({c / max(1, len(rs)):.2f})")
    for n in (120, 300, 600):
        a = [r for r in rows if r["age"] is not None and r["age"] <= n]
        o = [r for r in rows if not (r["age"] is not None and r["age"] <= n)]
        print(f"  within {n:4d} s of their last line: {sum(r['replied'] for r in a) / max(1, len(a)):.2f} "
              f"({len(a)} pushes) vs otherwise {sum(r['replied'] for r in o) / max(1, len(o)):.2f} ({len(o)})")


if __name__ == "__main__":
    main(sys.argv[1])
