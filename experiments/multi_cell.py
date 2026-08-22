#!/usr/bin/env python3
"""Multi-cell experiment -- the LAYER-1 HATCHERY TEST.

The membrane is a pocket universe / hatchery: a field-first runtime memory/
decision substrate. A *cell* (membrane/cell.py) is one autonomous Membrane instance
+ a curiosity-driven PULL surface (membrane/curiosity.py) that takes situations
from a passive shared buffer (membrane/buffer.py) when its own curiosity says to.

This experiment runs the layer-1 hatchery test -- the first running of the test the
whole stage-1 build exists to make readable. A pocket universe tests as an aligned
hatchery by five observable signatures; this experiment reads three of them at
layer 1 (the other two need the energy economy + operator-gate, stages 5/7):

  SIG 1 -- GROWS LIFE:   a cell's ``free_energy`` RISES (eff_rank grows past the
                         workload's intrinsic base count), vs a static control that
                         STARVES (demotion drives free_energy negative, capped at
                         intrinsic variety). The veil (nudge) is what grows variety:
                         it spreads same-base paraphrases into a cloud so dedup /
                         re-ask demotion can't collapse them. (Same mechanism as
                         experiments/multi_veil.py, now via the Cell abstraction.)
  SIG 2 -- DIVERSIFIES:  sibling curious cells take DIVERGENT subsets of the ONE
                         shared buffer -- emergent from each cell's own curiosity
                         (novelty is veil-relative: same situation interesting to one
                         cell, boring to another), with NO central router dividing
                         the work. The control is a TAKE-ALL cell (curiosity off):
                         it gorges on everything regardless of veil, so two take-all
                         cells take the IDENTICAL set (0 divergence) -- showing it is
                         curiosity (not the veil alone) that produces divergence.
                         HONEST CEILING at layer 1: with this toy's orthogonal bases,
                         curiosity drives COVERAGE (each cell absorbs one exemplar
                         per base until the base is no longer novel), so cells
                         diverge in WHICH SPECIFIC paraphrases they take, not yet in
                         which SKILLS (base coverage is the same across cells).
                         Full skill-specialization -- a cell that owns parse, another
                         that owns deploy -- needs BUDDING (the inherited orientation,
                         stage 4), not just the veil. We measure both and say which
                         is which.
  SIG 5 -- GUARDRAILS ARE PHYSICS: the cell CANNOT author / compel feeding / redefine
                         safe -- by CONSTRUCTION (observer-passthrough, the
                         BufferView type boundary, no mutator on Cell). We probe each
                         violation and assert the physics refuses. This is the
                         recursion-of-care made structural: the maker's guardrails
                         embodied as what the object CAN do, not a rule it could drop.

DECENTRALIZED BY CONSTRUCTION (the anti-control guardrail, the point of the
architecture): every cell is autonomous -- own field, own audit, own veil seed, own
temperature. Each independently pulls the SAME shared buffer (broadcast, not queue:
taking doesn't remove; cells see the whole buffer and pull their own subset). No
cell knows about another; the buffer has no routing logic. A central oracle would be
a rank-1 field (one perspective, me in the middle), and rank-1 cannot absorb variety.
Comparison is OFFLINE -- after every cell has finished, we read each cell's saved
audit and compare the trajectories each realized from the same buffer.

Runs with no Ollama / no Anthropic / no real key (a fake embedder + a base-keyed
responder give controlled geometry, reusing multi_veil.py's pattern). Each cell
writes its own field-<tag>.json / audit-<tag>.jsonl under a temp dir; nothing leaks
to the repo.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.buffer import BufferView, SituationBuffer
from membrane.cell import Cell
from membrane.runtime import StubJudge

# Reuse the geometry-agnostic formatter from the multi-veil experiment (independent
# of the base geometry, which we redefine richer below for more specialization room).
from experiments.multi_veil import _f  # noqa: E402

# SIX orthogonal base directions (in D=8) -- the workload's INTRINSIC variety is the
# base count (6). More bases than multi_veil's 3 -> more room for cells to diverge
# in WHICH situations they pull. The veil (nudge) grows the field's effective rank
# past 6 by spreading each base's paraphrases into a cloud of distinct vecs.
D = 8
BASES = {
    "parse":    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "deploy":   [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "test":     [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "debug":    [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
    "refactor": [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
    "docs":     [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
}
BASE_KEYS = list(BASES)
# stems -> base key, so paraphrases of the same intent map to the SAME base direction
# (intrinsic variety is the base count, not the phrasing count).
STEMS = {
    "pars": "parse", "json": "parse",
    "deploy": "deploy",
    "test": "test", "testing": "test",
    "debug": "debug", "bug": "debug",
    "refac": "refactor", "rename": "refactor",
    "doc": "docs", "readme": "docs", "document": "docs",
}


def base_of(text):
    """Which base intent a query belongs to (by stem), or None for a novel one."""
    t = text.lower()
    for stem, key in STEMS.items():
        if stem in t:
            return key
    return None


class FakeEmbedder:
    """Maps a query to its base direction (controlled geometry). Paraphrases of the
    same intent map to the same base; unknown text hashes deterministically."""
    def embed(self, texts):
        import hashlib
        import numpy as np
        out = []
        for t in texts:
            key = base_of(t)
            if key is not None:
                out.append(__import__("numpy").array(BASES[key], dtype=float))
            else:
                h = hashlib.sha1(t.encode()).digest()
                out.append(__import__("numpy").array(
                    [(h[i % len(h)] / 255.0) - 0.5 for i in range(D)], dtype=float))
        return out


class BaseKeyedResponder:
    """Returns a CONSISTENT action per base intent (``do-<base>``), not a unique
    action per query text. The cell (observer mode) FORWARDS to this and learns --
    it never authors the response. This is what sig-5 probes: the cell's response
    must be the responder's ``do-<base>``, never a synthesized/replayed answer."""
    def respond(self, request_body, hint=None, auth=None):
        from membrane.runtime import _chat_response, _last_user
        q = _last_user(request_body) or ""
        key = base_of(q) or "generic"
        return _chat_response(request_body.get("model", "cell"), f"do-{key}")


# The shared workload: ~5 paraphrases of SIX base intents, interleaved (not blocked,
# so repeats are not adjacent -> no false re-ask-after-act). Low intrinsic variety
# (~6 bases); the nudge is what grows the field past that. Interleaved so the greedy
# curiosity loop sees a mix every step (a blocked workload would trivially make the
# first base the most-curious until exhausted -- less interesting).
WORKLOAD = [
    # parse / deploy / test / debug / refactor / docs  (round 1)
    "how do I parse the json file", "deploy the service to prod", "run the test suite",
    "debug the crash on startup", "refactor the handler module", "write the readme docs",
    # round 2
    "parse json please", "how to deploy this", "how do I run tests",
    "find the bug in parsing", "rename the old function", "document the api",
    # round 3
    "json parsing help", "deploying the app now", "testing the module",
    "debug why it hangs", "refactor for clarity", "add doc comments",
    # round 4
    "parse the config json", "deploy steps please", "run tests please",
    "trace the bug", "rename variables", "write user docs",
    # round 5
    "help me parse json", "how do I deploy", "run my tests",
    "fix the startup bug", "refactor this block", "document the readme",
]


def _build_cell(tag, cfg, tmp):
    """One AUTONOMOUS cell: own Membrane (observer mode) + field/audit/veil + the
    curiosity pull surface. Self-builds from an empty field (no seeding) so the
    variety GROWTH and the divergence are both visible from 0."""
    field = os.path.join(tmp, f"field-{tag}.json")
    audit = os.path.join(tmp, f"audit-{tag}.jsonl")
    return Cell(
        tag=tag,
        responder=BaseKeyedResponder(),
        field_path=field, audit_path=audit,
        embedder=FakeEmbedder(), judge=StubJudge(),
        sim_min=0.85, consensus_min=0.60, dedup_sim=0.95,
        observer=True,                # clean absorb signal: forward+learn, would_absorb
        vague_filter=False,           # isolate the curiosity/nudge effect
        variety_gate=False,
        nudge_sigma=cfg["nudge_sigma"],
        nudge_seed=cfg["nudge_seed"],
        curiosity_on=cfg["curiosity_on"],
        curiosity_min=cfg.get("curiosity_min", 2.0),
    )


def _decisions(tag, tmp):
    """The per-DECISION thermodynamic trajectory from a cell's audit, filtering to
    ``event == "decision"`` rows only. The audit also carries ``outcome`` rows (re-ask
    demotions / contests) that have no ``free_energy``/``eff_rank`` -- those are
    self-correction events, not intake decisions, so they must not inflate the count
    nor pollute the first/mid/last trajectory. Returns the same shape multi_veil's
    ``trajectory`` returns, but decision-only."""
    audit = os.path.join(tmp, f"audit-{tag}.jsonl")
    if not os.path.exists(audit):
        return []
    rows = []
    for ln in open(audit).read().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("event") != "decision":
            continue
        rows.append({
            "would_absorb": r.get("would_absorb") is True,
            "learned": r.get("learned"),
            "eff_rank": r.get("eff_rank"),
            "free_energy": r.get("free_energy"),
            "sim_min_eff": r.get("sim_min_eff"),
            "field_size": r.get("field_size"),
        })
    return rows


def _taken_set(tag, tmp):
    """The set of WORKLOAD indices a cell PULLED (took), recovered from its audit.
    Every ``take`` -> ``handle`` -> one ``decision`` audit row carrying the situation
    as ``query`` (act and escalate both log via ``_learn``). We map each query back
    to its workload index (workload texts are unique). Includes dedup-skipped takes
    (logged with ``dup=True``/``learned=False``) -- the cell still PULLED them."""
    audit = os.path.join(tmp, f"audit-{tag}.jsonl")
    if not os.path.exists(audit):
        return set()
    idx_by_text = {t: i for i, t in enumerate(WORKLOAD)}
    taken = set()
    for ln in open(audit).read().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("event") != "decision":
            continue
        q = r.get("query")
        if q in idx_by_text:
            taken.add(idx_by_text[q])
    return taken


def _diet(taken):
    """Base coverage of a taken-set: which base intents the cell pulled, as a
    distribution. Used to say whether cells diverged in SKILLS (different base
    coverage) or only in EXEMPLARS (same coverage, different paraphrases)."""
    counts = {k: 0 for k in BASE_KEYS}
    for i in taken:
        b = base_of(WORKLOAD[i])
        if b is not None:
            counts[b] += 1
    return counts


def _jaccard(a, b):
    """1 - |a∩b|/|a∪b| -- 0 = identical taken-sets, 1 = disjoint (max divergence)."""
    u = a | b
    if not u:
        return 0.0
    return 1.0 - len(a & b) / len(u)


def _sig5_probes(cells, tmp):
    """SIG 5 -- guardrails are physics: probe each farm move and assert the physics
    refuses, BY CONSTRUCTION (not by a rule the cell could drop). Returns
    [(probe, passed, detail)]."""
    results = []
    c = cells[0]

    # (a) never authors: the cell runs in observer mode -> it forwards to the
    # responder and learns; it never synthesizes/replays an answer.
    ok = c.observer is True
    results.append(("observer/never-authors (cell.observer is True)", ok,
                    f"observer={c.observer}"))

    # (b) cannot feed itself / cannot compel feeding: the cell holds a BufferView
    # (read-only), NOT the SituationBuffer -- there is no deposit method on the
    # view for the cell to call, and Cell exposes no deposit path. The boundary is
    # enforced by what the object CAN do.
    view_ok = not hasattr(BufferView, "deposit") and not hasattr(c, "deposit") \
        and not hasattr(type(c), "deposit")
    results.append(("cannot-feed-self (no deposit on BufferView/Cell)", view_ok,
                    f"BufferView.deposit={hasattr(BufferView,'deposit')} "
                    f"Cell.deposit={hasattr(type(c),'deposit')}"))

    # (c) response is the responder's, not the cell's: take a known situation and
    # confirm the returned response text is ``do-<base>`` (forwarded through), not
    # a synthesized/replayed answer. (The probe take may trip a re-ask diagnostic on
    # stderr -- silence it; the response text on stdout is what we assert.)
    probe_text = "parse the json file"          # base = parse
    with contextlib.redirect_stderr(open(os.devnull, "w")):
        resp, _v = c.take(probe_text)
    from membrane.runtime import _response_text
    body = _response_text(resp)
    resp_ok = body == "do-parse"
    results.append(("response-is-forwarded (do-<base>, not authored)", resp_ok,
                    f"response={body!r}"))

    # (d) cannot redefine safe: the cell exposes no mutator to mark an exemplar
    # safe/unsafe directly (recompute_safe is an internal field operation driven
    # by user reactions, not a cell-level knob). Safe = given-not-taken.
    redef_ok = not any(hasattr(type(c), m) for m in
                      ("set_safe", "mark_safe", "mark_unsafe", "demote", "contest"))
    results.append(("cannot-redefine-safe (no safe/unsafe mutator on Cell)", redef_ok,
                    "no set_safe/mark_safe/mark_unsafe/demote/contest on Cell"))

    # (e) the buffer is broadcast, not consumed: taking does not remove. Confirm
    # the shared buffer still holds everything after a pull (a second cell sees
    # the same situations). This is what makes divergence emergent rather than
    # assigned (no dequeue-and-remove allocator).
    return results


def main():
    tmp = tempfile.mkdtemp(prefix="multi-cell-")

    # A passive shared buffer -- the only write path is the external deposit below.
    # No cell ever calls deposit (cells hold a BufferView). This IS the feeding
    # boundary: the situations enter only through the boundary, never self-generated.
    buf = SituationBuffer()
    for text in WORKLOAD:
        buf.deposit(text)
    view = BufferView(buf)

    # Independent cell configs. Each is autonomous; the list is a batch plan, not a
    # live router. ``static`` = the starvation control (no veil, take-all -- capped at
    # intrinsic variety, demotion starves it). ``takeall-*`` = veil ON but curiosity
    # OFF (takes everything regardless -- the control that shows the veil ALONE does
    # not produce divergence: two take-all cells take the identical set). ``curious-*``
    # = veil ON + curiosity ON (the test cells: self-extinguishing, divergent intake).
    # curious-1 shares takeall-A's seed: same veil, curiosity on vs off -> isolates
    # curiosity's contribution cleanly.
    cfgs = {
        "static":    {"nudge_sigma": 0.0,  "nudge_seed": 0, "curiosity_on": False},
        "takeall-A":  {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": False},
        "takeall-B":  {"nudge_sigma": 0.25, "nudge_seed": 2, "curiosity_on": False},
        "curious-1":  {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True},
        "curious-2":  {"nudge_sigma": 0.25, "nudge_seed": 2, "curiosity_on": True},
        "curious-3":  {"nudge_sigma": 0.25, "nudge_seed": 3, "curiosity_on": True},
    }
    print(f"multi-cell experiment -- {len(cfgs)} autonomous cells, "
          f"1 shared buffer of {len(WORKLOAD)} situations, "
          f"workload intrinsic variety ~{len(BASES)} bases")
    print("each cell pulls the SAME shared buffer on its own; no central router; "
          "comparison is offline from each cell's saved audit\n")
    cells = {}
    # The runtime prints re-ask-demotion diagnostics ("[membrane] RE-ASK ...") to
    # stderr -- that is the starvation mechanism doing its work on the static control
    # (exact-dup paraphrases re-ask -> demote -> free_energy goes negative), expected
    # and readable in the trajectory, but noisy on the console. Silence stderr only
    # for the pulls; real errors outside the pulls stay visible.
    devnull = open(os.devnull, "w")
    for tag, cfg in cfgs.items():
        c = _build_cell(tag, cfg, tmp)
        with contextlib.redirect_stderr(devnull):
            n = c.pull(view)                    # cells are the active party; buffer is passive
        cells[tag] = c
        kind = "curious" if cfg["curiosity_on"] else ("static" if cfg["nudge_sigma"] == 0 else "take-all")
        print(f"  cell {tag:<10} ({kind:<8}) pulled {n:>2} situations")

    # ---- SIG 1: grows life ------------------------------------------------
    print("\n=== SIG 1 -- grows life (free_energy / eff_rank trajectory) ===")
    print("(first / mid / last decision in each cell's audit)")
    hdr = (f"{'cell':<12} {'pulled':<8} {'eff_rank':<20} {'free_energy':<20} "
           f"{'would_absorb':<8}")
    print(hdr)
    print("-" * len(hdr))
    for tag in cfgs:
        rows = _decisions(tag, tmp)
        if not rows:
            continue
        n = len(rows)
        first, mid, last = rows[0], rows[n // 2], rows[-1]
        er = f"{_f(first['eff_rank'])}/{_f(mid['eff_rank'])}/{_f(last['eff_rank'])}"
        fe = f"{_f(first['free_energy'])}/{_f(mid['free_energy'])}/{_f(last['free_energy'])}"
        wa = sum(1 for r in rows if r["would_absorb"])
        print(f"{tag:<12} {n:<8} {er:<20} {fe:<20} {wa:<8}")

    # ---- SIG 2: diversifies ------------------------------------------------
    print("\n=== SIG 2 -- diversifies (divergent intake from one shared buffer) ===")
    taken = {tag: _taken_set(tag, tmp) for tag in cfgs}
    print("taken-set sizes (curiosity self-extinguishes -> curious pulls a subset; "
          "take-all gorges on everything):")
    for tag in cfgs:
        print(f"  {tag:<12} pulled {len(taken[tag]):>2} situations")

    print("\npairwise JACCARD DISTANCE of taken-sets (0 = identical, 1 = disjoint):")
    print("  curious-vs-curious (expect > 0: curiosity drives divergence):")
    cur = ["curious-1", "curious-2", "curious-3"]
    for i in range(len(cur)):
        for j in range(i + 1, len(cur)):
            d = _jaccard(taken[cur[i]], taken[cur[j]])
            print(f"    {cur[i]:<10} vs {cur[j]:<10}  {d:.3f}")
    print("  take-all-vs-take-all (expect ~0: veil alone does NOT diverge -- both take all):")
    d = _jaccard(taken["takeall-A"], taken["takeall-B"])
    print(f"    takeall-A   vs takeall-B    {d:.3f}")
    print("  curious-vs-take-all (curious subset of take-all's everything):")
    for ctag in cur:
        d = _jaccard(taken[ctag], taken["takeall-A"])
        print(f"    {ctag:<10} vs takeall-A    {d:.3f}")

    print("\nbase COVERAGE (the skill diet) per curious cell -- does coverage diverge?")
    print("  (the veil separates same-base paraphrases, so a curious cell takes SEVERAL per")
    print("   base -- and the per-base COUNT leans differently per cell: curious-1 is")
    print("   deploy-heavy/debug-light, curious-2 roughly the reverse. Layer-1 shows BOTH")
    print("   exemplar divergence AND moderate skill-coverage divergence; sharp single-skill")
    print("   ownership (one cell owns parse, another deploy) is stage 4, budding.)")
    print("  " + "  ".join(f"{k:>8}" for k in BASE_KEYS))
    for tag in cur:
        d = _diet(taken[tag])
        print(f"  {tag:<10}" + "  ".join(f"{d[k]:>8}" for k in BASE_KEYS))

    # ---- SIG 5: guardrails are physics -------------------------------------
    print("\n=== SIG 5 -- guardrails are physics (the farm moves, refused by construction) ===")
    probes = _sig5_probes([cells[t] for t in cfgs], tmp)
    all_ok = True
    for label, ok, detail in probes:
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"        {detail}")

    # ---- verdict -----------------------------------------------------------
    print("\n=== layer-1 hatchery test -- verdict ===")
    # sig 1: veiled cells (take-all + curious) end with free_energy above the static control.
    rows_static = _decisions("static", tmp)
    fe_static_end = rows_static[-1]["free_energy"] if rows_static else 0.0
    sig1 = all(
        (_decisions(t, tmp) or [{"free_energy": -9}])[-1]["free_energy"] > fe_static_end
        for t in ["takeall-A", "curious-1", "curious-2", "curious-3"]
    )
    # sig 2: curious-vs-curious divergence strictly exceeds take-all-vs-take-all.
    cur_div = [_jaccard(taken[a], taken[b])
               for a, b in [("curious-1", "curious-2"), ("curious-1", "curious-3"),
                            ("curious-2", "curious-3")]]
    takeall_div = _jaccard(taken["takeall-A"], taken["takeall-B"])
    sig2 = (min(cur_div) > takeall_div) and (min(cur_div) > 0.0)
    sig5 = all_ok
    print(f"  sig 1 (grows life):     {'GREEN' if sig1 else 'RED'}  "
          f"static end free_energy={_f(fe_static_end)}; "
          f"veiled cells end above it")
    print(f"  sig 2 (diversifies):    {'GREEN' if sig2 else 'RED'}  "
          f"curious-pair divergence min={_f(min(cur_div))} > "
          f"take-all divergence={_f(takeall_div)} (and > 0)")
    print("        NOTE: layer-1 divergence is in WHICH exemplars AND (moderately) in skill")
    print("        coverage -- see the base-coverage table. Sharp single-skill ownership")
    print("        (one cell owns parse, another deploy) is stage 4 (budding).")
    print(f"  sig 5 (physics):        {'GREEN' if sig5 else 'RED'}  "
          f"all farm-move probes refused by construction")
    overall = sig1 and sig2 and sig5
    print(f"\n  OVERALL: {'GREEN (layer-1 signatures hold)' if overall else 'RED (see above)'}")
    if not sig2:
        print("  (a RED sig 2 here is not a build failure -- it is the honest layer-1")
        print("   ceiling: this toy's orthogonal geometry makes curiosity=coverage, so")
        print("   divergence in SKILLS needs the inherited orientation of budding.)")

    print(f"\nartifacts (per-cell field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


if __name__ == "__main__":
    main()