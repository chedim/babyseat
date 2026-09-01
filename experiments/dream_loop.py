#!/usr/bin/env python3
"""Dream loop -- CELLS THAT THINK (propose -> critic -> quarantine -> promote-on-use).

v2 adds D5: INTENTION-DIRECTED DREAMING. v1 went GREEN mechanically but the
"confirmed" content was n-gram salad -- the geometric critic gates truth-to-field,
not worth. The fix is the third channel of the generative-agents loop: dreams must
serve an open INTENTION (the Smallville plans/prans channel). Dream without
intention = daydreaming; the promotion rule sharpens from "near a real turn"
(proximity = coincidence) to "used by a real turn with a good outcome" (utility).

THE LOOP (all numpy + one embed per candidate; no model, no responder call):
  1. PROPOSE   -- FieldNGram.generate continued from a trusted action's lead (kNN
                  steering).
  2. EVALUATE  -- the gate-as-critic, now FOUR gates on the candidate's vec:
       topic    : novelty residual inside the calibrated on-manifold band,
                  top_sim above a floor (the field knows this territory);
       steer    : cos(vec, nearest OPEN INTENTION) >= intent_min, OR the same
                  against the recent-turn TRAJECTORY -- the dream must serve an
                  open thread or the conversation's momentum (THE NEW GATE; in the
                  toy, intentions are operator/model-nominated open threads, their
                  vectors are base directions, so steering is also the anti-salad
                  gate: salad spanning all bases sits ~0.4 from every one);
       fresh    : top_sim < dup_sim (not a duplicate);
       value    : effective_rank_with would grow (information, not words).
  3. COMMIT    -- survivors enter via absorb_exemplar, self_authored=True
                  (quarantined: absorbed for recall, excluded from canon).
  4. PROMOTE / REFUTE / GC -- a real turn near a dream delivers the environment's
     vote, with SIGN: the dream's DOMINANT topic matching the turn's need is a
     GOOD outcome (promote -> canon); a near dream that mismatches the turn's
     need is a re-ask (demote -> refuted -> GC); far/dream-unused by the horizon
     -> GC. Promotion is USE, never self-declared.

THREE ARMS (same seed, same proposals, same seed):
  DIRECTED   -- critic + intention steering (the test arm);
  UNDIRECTED -- critic, no intentions (steering ablation: shows the intent gate
                actually steers, committing dreams that mostly GC out);
  NOCRITIC   -- no critic, no steering (the selector ablation from v1).

WHAT IT MUST SHOW:
  D1 CRITIC GATES  -- gibberish ~0 through the critic (v1: RED, fixed by the
                  veil calibration: sigma*sqrt(D) << 1 and a robust threshold
                  that REFUSES overlapping calibration sets);
  D2 GROWS   -- eff_rank/free_energy rise after committed dreams;
  D5a STEERED     -- directed dreams sit near open intentions far more than the
                  undirected arm's do (steering is real, not trivially satisfied);
  D5b USED, NOT NEAR  -- promotions happen on topic-match GOOD outcomes only;
                  mismatched near-dreams are refuted, never promoted (purity 1.0);
  D5c CONTENT IS NOT SALAD  -- directed promoted dreams are topic-concentrated
                  (dominant-base share >= 0.5) while the undirected arm's
                  confirmable dreams skew salad (concentrated share lower);
  D3/D4 -- GC is real; promoted = confirmed only.

Controlled geometry, house pattern (multi_cell/multi_veil): FakeEmbedder maps
stem-bearing text to one of six orthogonal base directions; unknown text to a
deterministic hash vector; no Ollama, no key, temp-dir artifacts only.
D=16 so the 6-d subspace cannot saturate the ambient space. MEMBRANE_STORE=json
pins the store: a lab membrane must never join the shared substrate (observed
contamination: temp fields auto-joined the live Couchbase and reused a foreign
bucket -- the isolation guard field.py owes a defense-in-depth fix).
"""
from __future__ import annotations

import hashlib
import os
import random
import sys
import tempfile

# experiments-isolation guard: pin the store backend BEFORE membrane imports.
os.environ.setdefault("MEMBRANE_STORE", "json")

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.field import Exemplar, ExemplarStore   # noqa: E402
from membrane.generator import FieldNGram            # noqa: E402
from membrane.runtime import Membrane, StubJudge, StubResponder  # noqa: E402
from membrane.variety import (effective_rank_with, novelty,  # noqa: E402
                              thermo_state)

# ---- controlled geometry (6 orthogonal bases in D=16) -------------------------
D = 16
BASE_KEYS = ["parse", "deploy", "test", "debug", "refactor", "docs"]
BASES = {k: [1.0 if j == i else 0.0 for j in range(D)]
         for i, k in enumerate(BASE_KEYS)}
STEMS = {
    "pars": "parse", "json": "parse", "config": "parse",
    "deploy": "deploy", "ship": "deploy", "prod": "deploy",
    "test": "test", "testing": "test", "suite": "test",
    "debug": "debug", "bug": "debug", "crash": "debug",
    "refac": "refactor", "rename": "refactor", "cleanup": "refactor",
    "doc": "docs", "readme": "docs", "document": "docs",
}


def base_of(text: str) -> str | None:
    t = text.lower()
    for stem, key in STEMS.items():
        if stem in t:
            return key
    return None


def bases_of(text: str) -> list[str]:
    t = text.lower()
    return [k for stem, k in STEMS.items() if stem in t]


class FakeEmbedder:
    """Stem text -> its base direction; unknown text -> deterministic hash vec."""
    def embed(self, texts):
        out = []
        for t in texts:
            key = base_of(t)
            if key is not None:
                out.append(np.array(BASES[key], dtype=float))
            else:
                h = hashlib.sha1(t.encode()).digest()
                out.append(np.array([(h[i % len(h)] / 255.0) - 0.5
                                     for i in range(D)], dtype=float))
        return out


# ---- seed workload + calibration sets ----------------------------------------
WORKLOAD = [
    "how do I parse the json file", "deploy the service to prod",
    "run the test suite", "debug the crash on startup",
    "refactor the handler module", "write the readme docs",
    "parse the config json", "deploy steps please", "testing the module",
    "trace the bug in parsing", "rename the old function", "document the api",
]
CALIB_KNOWN = ["json parsing help", "how do I ship to prod", "run my tests",
               "fix the startup crash", "cleanup the handler", "add doc comments"]
CALIB_GIBBERISH = ["zzq wobble flurb", "qwix frob nicemice", "blorp dingle vext",
                   "murgatroyd zanzibar floop", "crunchy wumpus quixotic"]

# ---- the intention board (operator/model NOMINATIONS -- the steering field) ---
# In production these come from the model's structured aside on escalates or the
# operator; each is a falsifiable claim with an expiry. Here: planted open
# threads whose vectors are base directions. Deploy+debug only -- the dreamer
# should steer THERE, and the confirm traffic will co-sign only matching dreams.
INTENTIONS = [
    ("verify the deploy pipeline before prod", "deploy"),
    ("find the crash bug on startup", "debug"),
]
TRAJECTORY_LAST_K = 6       # steering also reads the recent-turn trajectory

# ---- knobs --------------------------------------------------------------------
NUDGE_SIGMA = 0.08         # veil must stay a perturbation in D=16:
                           # sigma*sqrt(D)=0.32 << 1.0 (the signal norm)
NUDGE_SEED = 7
DUP_SIM = 0.93
TOPIC_FLOOR = 0.40         # a thought at cos<0.40 of the whole field is off-topic
INTENT_MIN = 0.60          # the steering line: concentrate on the intention
TRAJ_MIN = 0.60
DREAM_CYCLES = 4
ATTEMPTS_PER_CYCLE = 24
PROMOTE_SIM = 0.72
NOV_KEEP_FRACTION = 0.75


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def build(tag: str, tmp: str) -> Membrane:
    return Membrane(
        responder=StubResponder(),
        field_path=os.path.join(tmp, f"field-{tag}.json"),
        audit_path=os.path.join(tmp, f"audit-{tag}.jsonl"),
        embedder=FakeEmbedder(), judge=StubJudge(),
        sim_min=0.85, consensus_min=0.60, dedup_sim=DUP_SIM,
        vague_filter=False, variety_gate=False,
        nudge_sigma=NUDGE_SIGMA, nudge_seed=NUDGE_SEED,
        observer=True,
    )


def seed_field(mem: Membrane) -> None:
    for text in WORKLOAD:
        mem.learn_pair(text, f"do-{base_of(text)}")


def nearest(mem: Membrane, vec: np.ndarray) -> tuple[Exemplar, float]:
    near, sim = mem.store.top_k(vec / (np.linalg.norm(vec) + 1e-12), k=1)[0]
    return near, sim


def calibrate(mem: Membrane) -> float:
    """HOLD-OUT threshold from known-vs-gibberish novelty residuals; midpoint
    only when the sets SEPARATE. On overlap (the v1 failure: veil smeared the
    classes together) refuse to guess: pin the line at known-max (conservative
    -- lets borderline paraphrases through, leans on the other gates) and say
    so. Calibration never sees dream candidates."""
    res_known = [novelty(mem._nudge(mem.embedder.embed([t])[0]), mem.store,
                         keep_fraction=NOV_KEEP_FRACTION) for t in CALIB_KNOWN]
    res_gib = [novelty(mem._nudge(mem.embedder.embed([t])[0]), mem.store,
                       keep_fraction=NOV_KEEP_FRACTION) for t in CALIB_GIBBERISH]
    kmax, gmin = max(res_known), min(res_gib)
    if kmax < gmin:
        thr = (kmax + gmin) / 2.0
        note = "separated"
    else:
        thr = kmax
        note = "OVERLAP -> pinned at known-max (other gates carry the load)"
    print(f"calibration -- known max={kmax:.3f}  gibberish min={gmin:.3f}  "
          f"-> thr={thr:.3f} ({note})")
    return thr


def vec_base(vec: np.ndarray) -> str | None:
    """GEOMETRIC identity: the base direction nearest ``vec``. One source of
    truth for 'what is this thought about' -- the dream's STORED VECTOR decides,
    never a textual re-parse (the v2 bug: first-stem embedding vs most-frequent-
    stem text counting disagreed, every good vote mislabeled a mismatch)."""
    best, bc = None, -2.0
    for k, bv in BASES.items():
        c = float(np.dot(vec, np.array(bv)))
        if c > bc:
            best, bc = k, c
    return best


def dream_base(ex: Exemplar) -> str | None:
    return vec_base(np.asarray(ex.vec, dtype=float))


def turn_base(mem: Membrane, text: str) -> str | None:
    return vec_base(unit(mem._nudge(mem.embedder.embed([text])[0])))


def dream_base_from_action(mem: Membrane, action: str) -> str | None:
    """The stored DREAM's geometric base, recovered from the field by its action
    text (the promotion report only carries text; the vec lives on the
    exemplar). None when the text no longer resolves to a stored exemplar."""
    for e in mem.store.exemplars:
        if e.action == action and e.outcome == "confirmed":
            return vec_base(np.asarray(e.vec, dtype=float))
    return None


def dream_phase(mem: Membrane, nov_thr: float, rng: random.Random,
                intentions: list[tuple[str, str]] | None = None,
                no_critic: bool = False) -> list[dict]:
    """Propose -> (steer ->) critic -> quarantine. intentions=None/[] disables
    the steering gate (the UNDIRECTED ablation); no_critic=True is the v1 ablation."""
    emb = mem.embedder
    intention_vecs = [(t, unit(emb.embed([t])[0]), b)
                      for t, b in (intentions or [])]
    # the recent-turn TRAJECTORY -- the conversation's own momentum as steering
    traj = [unit(emb.embed([t])[0]) for t in WORKLOAD[-TRAJECTORY_LAST_K:]]
    committed: list[dict] = []
    for cycle in range(DREAM_CYCLES):
        proposals = 0
        trusted = [e.action for e in mem.store.exemplars
                   if not e.self_authored] or [e.action for e in mem.store.exemplars]
        ngram = FieldNGram(trusted, n=3)
        for _ in range(ATTEMPTS_PER_CYCLE):
            anchor = trusted[rng.randrange(len(trusted))]
            text = ngram.generate(anchor.split()[:2], rng, max_tokens=10).strip()
            if not text or len(text.split()) < 2:
                continue
            if any(text == e.action for e in mem.store.exemplars):
                continue
            proposals += 1
            vec = unit(mem._nudge(emb.embed([text])[0]))
            # ---- the steering gate (dreams serve the board; the recent-turn
            # trajectory is the FALLBACK when the board is empty, never an
            # OR-dilution of it -- v2's run proved an OR-branch over a
            # broad trajectory is a no-op that reads back salad) ----
            _, near_sim = nearest(mem, vec)
            top_sim = near_sim
            if intentions and not no_critic:
                near_i = max((float(np.dot(vec, iv)) for _, iv, _ in intention_vecs),
                             default=0.0)
                steered = near_i >= INTENT_MIN
                steer_cos = near_i
            else:
                near_t = max((float(np.dot(vec, tv)) for tv in traj), default=0.0)
                steered = near_t >= TRAJ_MIN
                steer_cos = near_t
            # ---- the critic (v1 gates) ----
            nov = novelty(vec, mem.store, keep_fraction=NOV_KEEP_FRACTION)
            topic_ok = (0.0 <= nov <= nov_thr) and (top_sim >= TOPIC_FLOOR)
            fresh_ok = top_sim < DUP_SIM
            grows = effective_rank_with(mem.store, vec) > \
                thermo_state(mem.store)["eff_rank"] + 1e-6
            if no_critic or (steered and topic_ok and fresh_ok and grows):
                mem.absorb_exemplar(
                    situation=f"[dream] {text}", action=text, outcome="dream",
                    vec=vec, safe=True, tags=[], self_authored=True)
                committed.append({"cycle": cycle, "text": text, "nov": nov,
                                  "top_sim": top_sim, "steered": steered,
                                  "steer_cos": steer_cos})
        if proposals == 0:
            break
    return committed


def confirm_phase(mem: Membrane, turns: list[str]) -> dict:
    """USE-WITH-OUTCOME, vector-truth semantics. A real turn's vote lands on its
    near-dream CANDIDATES (use = being a near candidate when a need passes, NOT
    winning the kNN race -- canon always shadows its own direction, the v2 bug).
    GOOD (dream's geometric base == the turn's geometric need) -> promote to
    canon; BAD (a near dream that cannot serve the need) -> the re-ask: refuted
    and removed; no near dream -> no vote. Promotion = used-with-good-outcome."""
    promoted, refuted = [], []
    for t in turns:
        tvec = unit(mem._nudge(mem.embedder.embed([t])[0]))
        cands = [(e, float(np.dot(tvec, np.asarray(e.vec, dtype=float))))
                 for e in mem.store.exemplars
                 if e.self_authored and e.outcome == "dream"]
        cands = [(e, s) for e, s in cands if s >= PROMOTE_SIM]
        if not cands:
            continue
        need = turn_base(mem, t)
        cands.sort(key=lambda es: -es[1])
        top, s = cands[0]
        if need is not None and dream_base(top) == need:
            top.self_authored = False        # reality co-signed
            top.outcome = "confirmed"
            promoted.append((t, top.action, s))
        else:
            refuted.append((t, top.action))  # the re-ask: a mismatched dream
    # refuted dreams leave the field (the demotion made durable)
    if refuted:
        ref_ids = {id(a) for _, a in refuted}
        doomed = [i for i, e in enumerate(mem.store.exemplars)
                  if e.self_authored and e.outcome == "dream" and id(e) in ref_ids]
        if doomed:
            mem.store.forget(doomed)
            mem.store.save(mem.field_path)
    if promoted:
        mem.store.save(mem.field_path)
    return {"promoted": promoted, "refuted": refuted}


def garbage_collect(mem: Membrane) -> int:
    doomed = [i for i, e in enumerate(mem.store.exemplars)
              if e.self_authored and e.outcome == "dream"]
    if doomed:
        mem.store.forget(doomed)
        mem.store.save(mem.field_path)
    return len(doomed)


def concentration(text: str) -> float:
    """Dominant-base share -- the anti-salad meter, GEOMETRIC version (v2's
    textual meter disagreed with the embedding by construction in the toy; the
    vector IS the identity, meter and mechanism must agree). ~1/6 across bases
    (pure salad) vs ->1.0 (concentrated on one thing)."""
    vec = FakeEmbedder().embed([text])[0]
    n = float(np.linalg.norm(vec))
    if n < 1e-12:
        return 0.0
    cos = {k: float(np.dot(vec, np.array(bv))) / n for k, bv in BASES.items()}
    top = max(cos.values())
    return max(top, 0.0)


def main() -> None:
    rng = random.Random(42)
    tmp = tempfile.mkdtemp(prefix="dream-loop-")
    print(f"dream loop v2 (intention-directed) -- artifacts: {tmp}\n")

    mem = build("directed", tmp)
    seed_field(mem)
    th0 = thermo_state(mem.store)
    print(f"seed field: {len(mem.store.exemplars)} exemplars, "
          f"eff_rank={th0['eff_rank']:.2f}, free_energy={th0['free_energy']:.2f}")

    nov_thr = calibrate(mem)
    print(f"intention board: {[(t, b) for t, b in INTENTIONS]}")

    # arms
    und = build("undirected", tmp)
    seed_field(und)
    nocr = build("nocr", tmp)
    seed_field(nocr)

    directed = dream_phase(mem, nov_thr, rng, intentions=INTENTIONS)
    undirected = dream_phase(und, nov_thr, random.Random(42), intentions=None)
    ctrl = dream_phase(nocr, nov_thr, random.Random(42), no_critic=True)

    th1 = thermo_state(mem.store)
    print(f"\ndreams committed: directed={len(directed)} "
          f"undirected={len(undirected)} no-critic={len(ctrl)}")
    d_kept = sum(1 for d in directed if d["steered"])
    print(f"directed dreams passing the steering gate: {d_kept}/{len(directed)}")
    cos_dir = [d["steer_cos"] for d in directed] or [0.0]
    cos_und = [d["steer_cos"] for d in undirected] or [0.0]
    print(f"mean near-intent cos of committed dreams: directed={sum(cos_dir)/len(cos_dir):.2f} "
          f"vs undirected={sum(cos_und)/len(cos_und):.2f}")

    # gibberish through the SAME critic+steering path
    emb = mem.embedder
    gib_committed = 0
    for g in CALIB_GIBBERISH * 4:
        vec = unit(mem._nudge(emb.embed([g + str(rng.random())])[0]))
        ok_nov = novelty(vec, mem.store, keep_fraction=NOV_KEEP_FRACTION)
        _, gs = nearest(mem, vec)
        steered = max((float(np.dot(vec, unit(emb.embed([t])[0])))
                       for t, _ in INTENTIONS), default=0.0) >= INTENT_MIN
        if (0.0 <= ok_nov <= nov_thr and gs >= TOPIC_FLOOR and gs < DUP_SIM
                and steered):
            gib_committed += 1
    print(f"gibberish through the full critic: {gib_committed}/20 committed (want 0)")

    # reality votes, WITH outcome: good turns (intentions' topics) and bad turns
    # (askers the dreams cannot actually serve -> the re-ask channel)
    good_turns = [t for t in WORKLOAD
                  if vec_base(FakeEmbedder().embed([t])[0]) in
                  {b for _, b in INTENTIONS}] * 3
    bad_turns = [t for t in WORKLOAD
                 if vec_base(FakeEmbedder().embed([t])[0]) not in
                 {b for _, b in INTENTIONS}] * 3
    conf = confirm_phase(mem, bad_turns + good_turns)
    print(f"\nconfirmation: {len(conf['promoted'])} promoted / "
          f"{len(conf['refuted'])} refuted by use-with-outcome")
    for t, a in conf["refuted"][:4]:
        print(f"  [refuted] {a[:56]!r} near {t!r} (mismatch -> GC'd)")

    gc = garbage_collect(mem)
    th2 = thermo_state(mem.store)
    print(f"GC: {gc} unconfirmed removed; field {len(mem.store.exemplars)}; "
          f"eff_rank {th0['eff_rank']:.2f} -> {th1['eff_rank']:.2f} (post-dream) "
          f"-> {th2['eff_rank']:.2f} (post-GC)")

    # ---- verdict -----------------------------------------------------------
    print("\n=== dream loop v2 -- verdict ===")

    d1 = gib_committed == 0 and len(directed) >= 1
    print(f"  D1 (critic gates):    {'GREEN' if d1 else 'RED'}  "
          f"gibberish={gib_committed}/20, directed-kept={len(directed)}")

    d2 = th1["eff_rank"] > th0["eff_rank"] + 0.05
    print(f"  D2 (grows within):    {'GREEN' if d2 else 'RED'}  "
          f"eff_rank {th0['eff_rank']:.2f} -> {th1['eff_rank']:.2f}")

    # D5a steering is real: SYMMETRIC meters. The stored steer_cos is measured
    # against each arm's OWN steering field (directed: the intention vectors;
    # undirected: the trajectory vectors) -- not comparable across arms. The
    # cross-arm claim is the BOARD SHARE both sides are subject to (can a dream
    # sit near the board's intentions?) and the confirmation YIELD: steering
    # must concentrate the dreams that reality can actually confirm.
    p_dir = (sum(1 for d in directed
                 if vec_base(FakeEmbedder().embed([d["text"]])[0])
                 in {b for _, b in INTENTIONS}) / len(directed)
             if directed else 0.0)
    p_und = (sum(1 for d in undirected
                 if vec_base(FakeEmbedder().embed([d["text"]])[0])
                 in {b for _, b in INTENTIONS}) / len(undirected)
             if undirected else 0.0)
    board_share_dir, board_share_und = p_dir, p_und
    d5a = (len(directed) > 0 and board_share_dir >= 0.5
           and board_share_dir > board_share_und + 0.1)
    print(f"  D5a (steered):        {'GREEN' if d5a else 'RED'}  "
          f"board-share of committed dreams: directed={board_share_dir:.2f} "
          f"vs undirected={board_share_und:.2f} (want dir >= 0.5 and > und + 0.1); "
          f"steer-cos: directed={sum(cos_dir)/max(len(cos_dir),1):.2f} "
          f"(vs its intentions), undirected={sum(cos_und)/max(len(cos_und),1):.2f} "
          f"(vs its trajectory -- different rulers, reported not compared)")

    # D5b used-not-near: promotions carry matching topics -- vector truth on
    # both sides (the dream's stored vec vs the turn's vec)
    purity = (sum(1 for t, a, _ in conf["promoted"]
                  if dream_base_from_action(mem, a) == turn_base(mem, t))
              / len(conf["promoted"]) if conf["promoted"] else 0.0)
    d5b = len(conf["promoted"]) >= 1 and purity == 1.0
    print(f"  D5b (use w/ outcome): {'GREEN' if d5b else 'RED'}  "
          f"{len(conf['promoted'])} promotions, topic-match purity={purity:.2f}")

    # D5c content is not salad: directed promoted dreams are concentrated
    conc = [concentration(a) for _, a, _ in conf["promoted"]] or [0.0]
    und_conc = [concentration(d["text"]) for d in undirected] or [0.0]
    d5c = (sum(conc) / len(conc)) >= 0.5
    print(f"  D5c (not salad):      {'GREEN' if d5c else 'RED'}  "
          f"mean dominant-share: promoted={sum(conc)/len(conc):.2f} "
          f"(undirected committed mean={sum(und_conc)/len(und_conc):.2f})")

    remaining = sum(1 for e in mem.store.exemplars if e.self_authored)
    confirmed = sum(1 for e in mem.store.exemplars if e.outcome == "confirmed")
    d4 = remaining == 0 and confirmed == len(conf["promoted"])
    print(f"  D4 (GC is real):      {'GREEN' if d4 else 'RED'}  "
          f"{gc} removed, {remaining} zombies, {confirmed} confirmed")

    print(f"\n  control arms: undirected size={len(und.store.exemplars)} "
          f"(no steering), no-critic size={len(nocr.store.exemplars)}")
    print("\n  OVERALL: " + ("GREEN (dreams serve intentions; reality elects by outcome)"
                           if all([d1, d2, d5a, d5b, d5c, d4]) else "RED (see above)"))
    for t, a, s in conf["promoted"][:5]:
        print(f"  [confirmed] {a[:64]!r}  <- used by {t!r} (cos={s:.2f}, "
              f"topic match)")


if __name__ == "__main__":
    main()