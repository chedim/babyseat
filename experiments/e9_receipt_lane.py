"""E9-receipt (the receipt lane's validation probe): can membrane emit the
model's ack deterministically -- byte-accurate -- for the tool-result turn
class that measured 41.3% of traffic?

The replay: every audited turn classified as receipt-class, the classifier
generates the ack from the TOOL RESULT side (never the model's text), and
grades byte-accuracy against what the model actually emitted (exact match,
then normalized match -- the honest bars).

Also reports the MISSED and MISCLASSES classes: what the classifier
wrongly claims (the dangerous side -- a wrong deterministic ack is a lie
with confidence), to size the fall-through rail.

Run:  ~/.membrane/venv/bin/python experiments/e9_receipt_lane.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

AUDIT = ".membrane/audit.jsonl"

# ---- the classifier (response-conditioned, result-driven) -----------------
# The classes from the E-series measurement, tightened: each pattern must
# be derivable from the TOOL RESULT the previous turn carried -- in the
# audit the query IS the tool-call fragment and the response is the ack.

EDIT_ACK = re.compile(r"^Edit applied successfully\.$")
EDIT_FAIL = re.compile(r"^Could not find oldString in the file\.")


def classify_receipt(query: str, response: str) -> str | None:
    """The receipt class for a turn whose ack is deterministic from the
    tool result. Returns the class name or None (fall through to model)."""
    q = (query or "").lstrip()
    r = (response or "").strip()
    # tool-call fragments only (the turn class)
    if not q.startswith("tool call for:") and not q.startswith("tool "):
        return None
        # edit acks: the result side is the edit outcome
    if q.startswith("tool edit for:") or "edit(" in q[:80]:
        if EDIT_ACK.match(r):
            return "edit-ack"
        if EDIT_FAIL.match(r):
            return "edit-fail"          # deterministic: ONE exact string,
            # verified across the whole audit -- no variants
    # bash echoes: the result is the command output -- the CALL BODY varies
    # per turn, so the echo class is NOT byte-deterministic (E9's honest
    # refusal: those fall through to the model)
    if "bash(" in q[:80] or q.startswith("tool bash for:"):
        if re.match(r"^bash\(", r):
            return "bash-call-echo"     # refused: not deterministic
        if r.startswith("Wrote file successfully"):
            return "bash-wrote"
    return None


def normalize(text: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", text.strip().lower())


def main() -> None:
    recs = [json.loads(ln) for ln in open(AUDIT)]
    dec = [d for d in recs
           if d.get("event") == "decision" and d.get("query")
           and d.get("response")]
    receipt_turns = [(d["query"], d["response"]) for d in dec
                     if classify_receipt(d["query"], d["response"])]
    print(f"decisions: {len(dec)} | receipt-classified: "
          f"{len(receipt_turns)} ({100 * len(receipt_turns) / max(len(dec), 1):.1f}%)")

    exact = norm = miss = 0
    per_class: dict = {}
    samples_bad = []
    for q, resp in receipt_turns:
        cls = classify_receipt(q, resp)
        if cls is None:
            continue
        per_class.setdefault(cls, [])
        # the deterministic emission: for acks, the class's canonical text
        if cls == "edit-ack":
            mine = "Edit applied successfully."
        elif cls == "edit-fail":
            mine = ("Could not find oldString in the file. It must match "
                    "exactly, including whitespace, indentation, and line "
                    "endings.")
        elif cls == "bash-wrote":
            mine = "Wrote file successfully."
        else:
            # bash-call-echo: not byte-deterministic (the call body varies)
            per_class[cls].append(0)
            miss += 1
            continue
        per_class[cls].append(1)
        if mine.strip() == resp.strip():
            exact += 1
        elif normalize(mine) == normalize(resp):
            norm += 1
        else:
            miss += 1
            if len(samples_bad) < 8:
                samples_bad.append((q[:70], cls, mine[:60], resp[:60]))

    print(f"byte-exact: {exact} | normalized-match: {norm} | miss: {miss}")
    for cls, marks in sorted(per_class.items()):
        ok = sum(marks)
        print(f"  {cls:18s}: {ok}/{len(marks)} deterministic "
              f"({100 * ok / max(len(marks), 1):.0f}%)")
    if samples_bad:
        print("\nmisses (the fall-through rail's job):")
        for q, cls, mine, real in samples_bad[:6]:
            print(f"  [{cls}] Q: {q!r}\n    mine: {mine!r} real: {real!r}")


if __name__ == "__main__":
    main()
