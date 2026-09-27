"""E25 -- CLAIMS vs EVIDENCE, and STRUCTURE.

Part A (the two streams joined, E24's split): what the model SAID it did
or found, checked against what the observations SHOW.
  A1 action claims   model prose like "I updated `field.py`" -> is there an
                     applied edit (edit/write, or a writing shell command)
                     to that file within [-45 min, +5 min]?
  A2 test claims     model prose like "524 pass" -> the nearest test run
                     observed before it (<= 60 min): same count?
  A3 the port case   E17's wrong compact note ("opencode cannot select
                     another port"): was the contrary evidence
                     (_pick_listen_port walking up) ever OBSERVED, and when?

Part B (composite entities): "A calls B" vs "B calls A" for real call
edges from membrane's own code graph (B does not call A). For each
representation: similarity of a fact to its reversal, normalized by the
similarity of two unrelated facts:
  structure-awareness = (1 - sim(fact, reversal)) / (1 - sim(fact, other))
  0 = order-blind (the reversal IS the fact), 1 = the reversal is as
  different as an unrelated fact.

    PYTHONPATH=. .venv/bin/python experiments/e25_claims_and_structure.py
"""

from __future__ import annotations

import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from e18_entity_field import embed_cached, unit  # noqa: E402
from e24_two_streams import split_audit  # noqa: E402
from membrane import vsa  # noqa: E402
from membrane.codegraph import _resolve, build_graph  # noqa: E402
from membrane.embed import OllamaEmbedder  # noqa: E402

_FILE = r"[\w./-]+\.(?:py|md|json|sh|js|ts|toml|ya?ml|html|txt|jsonl)"
_ACTION = re.compile(
    r"\b(updated|edited|changed|fixed|added|modified|patched|wrote|created|"
    r"rewrote|removed)\b[^.\n]{0,80}?`?(" + _FILE + r")`?", re.I)
_TESTS_CLAIM = re.compile(r"\b(\d{2,4})\s+(?:tests?\s+)?(?:pass|passed|passing)\b", re.I)
_RAN = re.compile(r"Ran (\d+) tests?|(\d+) passed")
_WRITES = re.compile(r"sed -i|>>?\s*\S|tee\s|\bpatch\b|git apply|\bmv\b|\bcp\b")


def base(f: str) -> str:
    return f.rsplit("/", 1)[-1].lower()


def part_a(discourse, obs, distilled):
    print("=== PART A: claims vs evidence ===")
    edits = defaultdict(list)                   # file -> [ts] of applied writes
    runs = []                                   # (ts, count) of observed test runs
    for o in obs:
        a, tool = o["args"], o["tool"]
        if tool in ("edit", "write") and o["outcome"] == "applied":
            fp = a.get("filePath") or ""
            if fp:
                edits[base(fp)].append(o["ts"])
        cmd = a.get("command") if isinstance(a.get("command"), str) else ""
        if tool == "bash" and cmd and _WRITES.search(cmd):
            for f in re.findall(_FILE, cmd):
                edits[base(f)].append(o["ts"])
        m = _RAN.search(o["result"] or "")
        if m and tool == "bash":
            runs.append((o["ts"], int(m.group(1) or m.group(2))))
    runs.sort()

    sup, uns, examples = 0, 0, []
    for d in discourse:
        for m in _ACTION.finditer(d["model"] or ""):
            f = base(m.group(2))
            ok = any(-45 * 60 <= t - d["ts"] <= 5 * 60 for t in edits.get(f, []))
            sup += ok
            uns += not ok
            if not ok and len(examples) < 6:
                examples.append(m.group(0)[:100])
    n = sup + uns
    print(f"\nA1 action claims in the model's prose: {n}")
    if n:
        print(f"   backed by an observed write to that file (-45..+5 min): {sup} ({sup / n:.0%})")
        print(f"   no observed write nearby: {uns} ({uns / n:.0%}); e.g.:")
        for e in examples:
            print(f"     - {e!r}")

    match, mism, norun, mex = 0, 0, 0, []
    for d in discourse:
        for m in _TESTS_CLAIM.finditer(d["model"] or ""):
            claimed = int(m.group(1))
            prior = [c for t, c in runs if 0 <= d["ts"] - t <= 3600]
            if not prior:
                norun += 1
            elif prior[-1] == claimed:
                match += 1
            else:
                mism += 1
                if len(mex) < 5:
                    mex.append(f"claimed {claimed}, last observed run {prior[-1]}")
    n2 = match + mism + norun
    print(f"\nA2 test-count claims: {n2}")
    if n2:
        print(f"   match the last observed run: {match} ({match / n2:.0%}); "
              f"differ: {mism} ({mism / n2:.0%}); no run observed within 60 min: "
              f"{norun} ({norun / n2:.0%})")
        for e in mex:
            print(f"     - {e}")

    print("\nA3 the port case (E17's wrong note):")
    ev = [o for o in obs if "_pick_listen_port" in (o["result"] or "") + str(o["args"])]
    walks = [o for o in ev if re.search(r"port \+= 1|walks up|port \+ 1|next free", o["result"] or "")]
    notes = [x for x in distilled if re.search(r"another or (?:even )?random port|random port", x["text"], re.I)]
    fmt = lambda t: __import__("datetime").datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
    print(f"   observations touching _pick_listen_port: {len(ev)}; showing the walk-up "
          f"code: {len(walks)}" + (f" (first {fmt(walks[0]['ts'])})" if walks else ""))
    for x in notes[:2]:
        print(f"   compact note {fmt(x['ts'])}: {x['text'][:200]!r}")
    if walks and notes:
        before = [w for w in walks if w["ts"] <= notes[0]["ts"]]
        print(f"   evidence observed BEFORE the note was written: {len(before)} "
              f"observation(s) -- an entity holding both would have caught it"
              if before else "   the evidence came only AFTER the note")
    elif notes and not walks:
        print("   the walk-up code was never observed in a tool result -- the "
              "contrary evidence was not in the stream")


def part_b(emb):
    print("\n=== PART B: structure -- 'A calls B' vs 'B calls A' ===")
    g = build_graph("membrane")
    syms = g["symbols"]
    stop = {"get", "join", "append", "items", "keys", "values", "update", "add",
            "copy", "pop", "read", "write", "run", "main", "load", "save", "close"}
    E = set()
    for s, info in syms.items():
        for c in info.get("calls", []):
            for t in _resolve(syms, c):
                if t != s and "." not in t and "." not in s and t.lower() not in stop \
                        and len(t) >= 6 and len(s) >= 6:
                    E.add((s, t))
    edges = sorted((a, b) for a, b in E if (b, a) not in E)
    rng = random.Random(25)
    edges = rng.sample(edges, min(300, len(edges)))
    fact = [f"{a} calls {b}" for a, b in edges]
    rev = [f"{b} calls {a}" for a, b in edges]
    other = [fact[(i + 1) % len(fact)] for i in range(len(fact))]
    print(f"   {len(edges)} real call edges (B does not call A), e.g. {fact[0]!r}")

    words = sorted({w for a, b in edges for w in (a, b)} | {"calls"})
    W = embed_cached(emb, words, "cg-words")
    wv = {w: W[i] for i, w in enumerate(words)}
    roles = np.random.default_rng(5).choice([-1.0, 1.0], size=(3, W.shape[1]))

    def sims_real(f):
        F = unit(np.array([f(t) for t in fact]))
        R = unit(np.array([f(t) for t in rev]))
        O = unit(np.array([f(t) for t in other]))
        return float(np.mean(np.sum(F * R, 1))), float(np.mean(np.sum(F * O, 1)))

    def triple(t):
        a, _, b = t.split(" ")
        return a, b

    reps = {}
    XF = embed_cached(emb, fact, "cg-fact")
    XR = embed_cached(emb, rev, "cg-rev")
    idx = {t: i for i, t in enumerate(fact)}
    reps["mxbai (sentence embedding)"] = (
        float(np.mean(np.sum(XF * XR, 1))),
        float(np.mean(np.sum(XF * XF[[(i + 1) % len(fact) for i in range(len(fact))]], 1))))
    reps["sum of word meanings"] = sims_real(
        lambda t: wv[triple(t)[0]] + wv["calls"] + wv[triple(t)[1]])
    reps["role-bound sum (subj/verb/obj)"] = sims_real(
        lambda t: roles[0] * wv[triple(t)[0]] + roles[1] * wv["calls"] + roles[2] * wv[triple(t)[1]])
    # membrane's own VSA lane (embedder-seeded codebook, order-sensitive)
    book = vsa.VSACodebook(embedder=emb)
    lane = vsa.VSALane(book)

    def vsim(a, b):
        return 1.0 - 2.0 * vsa.hamming(a, b)       # 1 same, 0 unrelated
    LF = [lane.encode(t) for t in fact]
    LR = [lane.encode(t) for t in rev]
    LO = [LF[(i + 1) % len(LF)] for i in range(len(LF))]
    reps["membrane VSA lane (encode)"] = (
        float(np.mean([vsim(a, b) for a, b in zip(LF, LR)])),
        float(np.mean([vsim(a, b) for a, b in zip(LF, LO)])))
    # explicit role binding in the VSA codebook: subj*A + verb*calls + obj*B
    # role vectors must be RANDOM (quasi-orthogonal), never semantic: the
    # embedder-seeded codebook makes '__role_subj'/'__role_obj' near-equal
    rs = [vsa.hash_token_vec(f"__role_{r}", book.dim) for r in ("subj", "verb", "obj")]

    def vroles(t):
        a, b = triple(t)
        return vsa.weighted_majority([vsa.bind(rs[0], book.token_vec(a.lower())),
                                      vsa.bind(rs[1], book.token_vec("calls")),
                                      vsa.bind(rs[2], book.token_vec(b.lower()))],
                                     [1.0, 1.0, 1.0])
    VF = [vroles(t) for t in fact]
    VR = [vroles(t) for t in rev]
    VO = [VF[(i + 1) % len(VF)] for i in range(len(VF))]
    reps["VSA codebook, role-bound"] = (
        float(np.mean([vsim(a, b) for a, b in zip(VF, VR)])),
        float(np.mean([vsim(a, b) for a, b in zip(VF, VO)])))

    print(f"   {'representation':<32} {'sim(fact,reversal)':>19} {'sim(fact,other)':>16} "
          f"{'structure-awareness':>20}")
    for name, (sr, so) in reps.items():
        aw = (1 - sr) / (1 - so) if so < 1 else float("nan")
        print(f"   {name:<32} {sr:>19.3f} {so:>16.3f} {aw:>20.2f}")


def main() -> None:
    emb = OllamaEmbedder(model="mxbai-embed-large")
    discourse, obs, distilled = split_audit(Path(".membrane/audit.jsonl"))
    part_a(discourse, obs, distilled)
    part_b(emb)


if __name__ == "__main__":
    main()
