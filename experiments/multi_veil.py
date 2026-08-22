#!/usr/bin/env python3
"""Multi-veil experiment -- "only variety can absorb variety" (a.k.a. "variety
is life"): Ashby's law with the control-theory skin peeled off.

Control theory's telos is anti-variety: the regulator exists to COLLAPSE the
disturbance's variety down to a controlled, low-variety output ("destroy
variety"). Variety is the adversary to be neutralized into order. The membrane
inverts the ethics: variety is not the threat to be contained, it is the life to
be grown -- ``free_energy`` (safe variety) is the field's vitality. Same law,
opposite telos: control theory says "grow enough control variety to KEEP THE
OUTPUT CONTROLLED"; the membrane says "grow enough field variety to ABSORB MORE
OF THE WORLD." This experiment grows and SPENDS variety; it does not measure
control.

DESIGN (decentralized -- the point, and the anti-control move):
  There is NO central input and NO live orchestrator. Each veil is an AUTONOMOUS
  Membrane instance -- its own field, its own audit, its own nudge seed, its own
  temperature. Each veil independently pulls the SAME public workload (just data:
  a fixed transcript) and processes it on its own. The runner is a BATCH LAUNCHER,
  not a brain in the middle: it does not route per-turn, does not fan a central
  input, does not compare live. Comparison is OFFLINE -- after every veil has
  finished, we read each veil's saved audit and compare the thermodynamic
  trajectories each veil realized from the same workload. (A central oracle is a
  rank-1 field -- one perspective, one bar, me in the middle -- and rank-1 cannot
  absorb variety. Only a population of differently-nudged veils can.)

WHAT IT SHOWS (the spend-variety loop, in observer mode so the absorb signal is
clean -- no reask-after-act entanglement):
  Over a LOW-intrinsic-variety workload (paraphrases of a few base intents):
    - a STATIC veil: its variety is capped at the workload's intrinsic base
      count; it would_absorb the repeats (cosine ~1) but never grows new variety.
    - a VEILED veil (own seed): the nudge perturbs every transit, so paraphrases
      become a CLOUD of distinct vecs -> the field grows variety the workload
      lacks on its own -> it learns more, eff_rank climbs past the base count.
    - an ADAPTIVE veil: as that safe variety (free_energy) grows, sim_min EASES
      toward the floor -> the veil starts would_absorbing the very nudged traffic
      it would have escalated cold. Variety grown is variety SPENT on absorbing
      more. "Only variety can absorb variety" made visible as a trajectory.

Runs with no Ollama / no Anthropic / no real key (a fake embedder + a base-keyed
responder give controlled geometry, like the test suite). Each veil writes its
own field-<tag>.json / audit-<tag>.jsonl under a temp dir; nothing leaks to the
repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.runtime import Membrane, StubJudge, _chat_response, _last_user

D = 8
# THREE orthogonal base directions -- the workload's INTRINSIC variety is the base
# count (low: 3). The nudge spreads each base into a cloud, growing the field's
# effective rank beyond 3. Three bases (not six) so a grown cloud of paraphrases
# can DOMINATE the top-5 neighbors -> consensus can form -> the gate can absorb.
# (With six distinct-action bases and k=5, every query's top-5 spans ~5 different
# actions -> consensus ~0.2 -> nobody can ever absorb, which buries the signal.)
BASES = {
    "parse":  [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "deploy": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "test":   [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
}
# stems -> base key, so paraphrases of the same intent map to the SAME base
# direction (the workload's intrinsic variety is the base count, not the phrasing
# count). "parsing"/"parsed" -> parse, "deploying"/"deployed" -> deploy.
STEMS = {
    "pars": "parse", "json": "parse",
    "deploy": "deploy",
    "test": "test", "testing": "test",
}


def base_of(text):
    """Which base intent a query belongs to (by stem), or None for a novel one."""
    t = text.lower()
    for stem, key in STEMS.items():
        if stem in t:
            return key
    return None


class FakeEmbedder:
    """Maps a query to its base direction (controlled geometry). Paraphrases of
    the same intent map to the same base; unknown text hashes deterministically."""
    def embed(self, texts):
        out = []
        for t in texts:
            key = base_of(t)
            if key is not None:
                out.append(np.array(BASES[key], dtype=float))
            else:
                h = hashlib.sha1(t.encode()).digest()
                out.append(np.array([(h[i % len(h)] / 255.0) - 0.5 for i in range(D)],
                                     dtype=float))
        return out


class BaseKeyedResponder:
    """Returns a CONSISTENT action per base intent (``do-<base>``), not a unique
    action per query text. This is what lets neighbor CONSENSUS form: similar
    situations share an action, so the gate can become ``sure`` and would_absorb.
    (The plain StubResponder returns the query text in the action -- every
    exemplar a unique action -> consensus always 0 -> nobody can ever absorb.)"""
    def __init__(self, tag="stub"):
        self.tag = tag

    def respond(self, request_body, hint=None, auth=None):
        q = _last_user(request_body) or ""
        key = base_of(q) or "generic"
        return _chat_response(request_body.get("model", self.tag), f"do-{key}")


# The public workload: SIX paraphrases of THREE base intents, interleaved (not
# blocked, so repeats are not adjacent -> no false reask-after-act). Low intrinsic
# variety (~3 bases); the nudge is what grows the field past that. Six per base so
# a grown cloud can dominate the top-5 neighbors and let consensus form.
WORKLOAD = [
    "how do I parse the json file", "deploy the service to prod", "run the test suite",
    "parse json please", "how to deploy this", "how do I run tests",
    "json parsing help", "deploying the app", "testing the module",
    "parse the config json", "deploy steps please", "run tests please",
    "help me parse json", "deploy this now", "please run the tests",
    "parse json for me", "how do I deploy", "run my tests",
]


def _req(text):
    return {"model": "stub", "messages": [{"role": "user", "content": text}]}


def run_veil(tag, cfg, workload, tmp):
    """One AUTONOMOUS veil: its own Membrane + field/audit, processing the shared
    workload in order. OBSERVER mode (forward+learn every turn, never replay) so
    the absorb signal is clean -- no reask-after-act entanglement, and the gate's
    would_absorb is the "could it absorb?" signal recorded per turn. Self-builds
    from an empty field (no seeding) so the variety GROWTH is visible from 0."""
    field = os.path.join(tmp, f"field-{tag}.json")
    audit = os.path.join(tmp, f"audit-{tag}.jsonl")
    for p in (field, audit):
        if os.path.exists(p):
            os.remove(p)
    mem = Membrane(
        responder=BaseKeyedResponder(),
        field_path=field, audit_path=audit,
        embedder=FakeEmbedder(), judge=StubJudge(),
        sim_min=cfg["sim_min"], consensus_min=0.60, dedup_sim=0.95,
        observer=True,                # clean absorb signal: forward+learn, would_absorb
        vague_filter=False,            # isolate the nudge/temperature effect
        variety_gate=False,
        variety_temp=cfg.get("variety_temp", False),
        temp_driver=cfg.get("temp_driver", "free_energy"),
        temp_tau=cfg.get("temp_tau", 1.0),
        sim_min_floor=cfg.get("sim_min_floor", 0.70),
        nudge_sigma=cfg.get("nudge_sigma", 0.0),
        nudge_seed=cfg.get("nudge_seed", 0),
    )
    for text in workload:
        _resp, verdict = mem.handle(_req(text))
        if os.environ.get("MEMBRANE_DEBUG") == tag:
            print(f"  [{tag}] {text[:28]:<28} v={verdict.get('verdict')!s:<18} "
                  f"sim={round(float(verdict.get('sim') or 0),3)!s:<7} "
                  f"sim_min_eff={round(float(verdict.get('sim_min_eff') or 0),3)!s:<7} "
                  f"fe={round(float(verdict.get('free_energy') or 0),3)!s:<7} "
                  f"field={verdict.get('ex_idx')!s}")
    return tag


def trajectory(tag, tmp):
    """OFFLINE comparison: read a veil's saved audit and return the per-decision
    thermodynamic trajectory. Read AFTER the veil finished, from its own artifact
    -- the runner never sees this live."""
    audit = os.path.join(tmp, f"audit-{tag}.jsonl")
    if not os.path.exists(audit):
        return []
    rows = []
    for ln in open(audit).read().splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        rows.append({
            "would_absorb": r.get("would_absorb") is True,
            "learned": r.get("learned"),
            "eff_rank": r.get("eff_rank"),
            "free_energy": r.get("free_energy"),
            "sim_min_eff": r.get("sim_min_eff"),
            "field_size": r.get("field_size"),
        })
    return rows


def _f(x, d=2):
    return f"{x:.{d}f}" if isinstance(x, (int, float)) else str(x)


def main():
    tmp = tempfile.mkdtemp(prefix="multi-veil-")
    # independent veil configs. Each is autonomous; the list is a batch plan, not a
    # live router. static = control (capped at intrinsic variety). The veiled ones
    # vary by seed (divergent realizations); the adaptive one SPENDS grown variety
    # by easing its floor low enough to absorb the nudged traffic.
    veils = {
        "static":    {"sim_min": 0.85, "nudge_sigma": 0.0},
        "veil-tight": {"sim_min": 0.85, "nudge_sigma": 0.10, "nudge_seed": 1},
        "veil-loose": {"sim_min": 0.85, "nudge_sigma": 0.25, "nudge_seed": 2},
        "adaptive":  {"sim_min": 0.85, "variety_temp": True, "temp_tau": 0.5,
                      "sim_min_floor": 0.55, "nudge_sigma": 0.25, "nudge_seed": 3},
    }
    print(f"multi-veil experiment -- {len(veils)} autonomous veils, "
          f"{len(WORKLOAD)} turns each, workload intrinsic variety ~{len(BASES)} bases")
    print("each veil pulls the same public workload on its own; no central router; "
          "comparison is offline from each veil's saved audit\n")
    for tag, cfg in veils.items():
        run_veil(tag, cfg, WORKLOAD, tmp)
        print(f"  veil {tag:<11} done")

    # OFFLINE comparison: read each veil's audit and summarize the trajectory it
    # realized from the SAME workload.
    print("\n--- thermodynamic trajectory per veil (first / mid / last decision) ---")
    hdr = (f"{'veil':<12} {'would_absorb':<13} {'learned':<8} "
           f"{'eff_rank':<16} {'free_energy':<16} {'sim_min_eff':<16}")
    print(hdr)
    print("-" * len(hdr))
    for tag in veils:
        rows = trajectory(tag, tmp)
        if not rows:
            continue
        n = len(rows)
        first, mid, last = rows[0], rows[n // 2], rows[-1]
        n_absorb = sum(1 for r in rows if r["would_absorb"])
        n_learned = sum(1 for r in rows if r.get("learned"))
        er = f"{_f(first['eff_rank'])}/{_f(mid['eff_rank'])}/{_f(last['eff_rank'])}"
        fe = f"{_f(first['free_energy'])}/{_f(mid['free_energy'])}/{_f(last['free_energy'])}"
        sm = f"{_f(first['sim_min_eff'])}/{_f(mid['sim_min_eff'])}/{_f(last['sim_min_eff'])}"
        print(f"{tag:<12} {n_absorb:<13} {n_learned:<8} {er:<16} {fe:<16} {sm:<16}")

    print("\nlegend: each triple = first / mid / last decision in the veil's audit")
    print("only variety can absorb variety (variety is life):")
    print("  static:    stuck at 1 exemplar per intent (dedup kills paraphrases) ->")
    print("             eff_rank capped at the intrinsic base count, free_energy goes")
    print("             NEGATIVE (the lone exemplars demote each other), 0 absorbs.")
    print("             No variety grown, none to spend.")
    print("  veil-tight: too-tight a nudge ~= no nudge -- paraphrases stay near-dup,")
    print("             dedup caps the cloud (~2-3/intent), too thin for neighbor")
    print("             consensus -> still 0 absorbs. (A nudge must actually inject variety.)")
    print("  veil-loose: bigger nudge -> the cloud grows (eff_rank past the base count)")
    print("             AND stays safe (the spread defeats the reask demotion), but the")
    print("             cloud is too spread to clear the COLD sim_min -> 0 absorbs.")
    print("  adaptive:  SAME nudge as veil-loose, same variety grown -- but as its safe")
    print("             free_energy grows, sim_min EASES toward the floor (0.85 -> 0.58).")
    print("             That eased bar is what lets it would_absorb the one turn that also")
    print("             clears the SECOND gate -- the novelty veto. Why so few absorbs?")
    print("             rank_ratio = eff_rank/n stays ~0.25 (< 0.5), so the null-space")
    print("             novelty veto stays ARMED: a nudged query sits slightly off the")
    print("             experienced manifold -> novelty > 2.0 -> vetoed -> escalate. The")
    print("             nudge grows variety (eff_rank up) but appends every turn (n up")
    print("             too), so variety-per-COUNT stays low. That is 'only variety can")
    print("             absorb variety' on the novelty axis too: the field needs enough")
    print("             variety RELATIVE to its size to lift the veto and absorb freely.")
    print("             (veil-loose vs adaptive differ ONLY in the temperature knob: same")
    print("             nudge, same variety grown, only adaptive SPENDS it by easing sim_min.)")
    print(f"\nartifacts (per-veil field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


if __name__ == "__main__":
    main()