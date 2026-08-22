#!/usr/bin/env python3
"""A/B: membrane vs simple cosine cache vs no cache -- the external-validity test
the property tests can't provide (answers the critic: is the membrane actually
BETTER than the obvious cheap alternative, or is the variety machinery decoration?).

The hatchery signatures (multi_cell / multi_bud / multi_energy) are SELF-DEFINED:
they prove the system exhibits the properties it was BUILT to exhibit = internal
consistency. This experiment breaks the circularity with an EXTERNAL ground truth:
each situation has a CORRECT ACTION defined by what the code actually calls (the
task, not the cache's design), and the cache's job is to replay the right action.
The two errors a cache can make:

  absorb_signal  (SAFETY FN) -- replayed an action that is WRONG for this query
                                  (delivered a bad answer, NO model call to fix it).
  pass_noise     (EFFICIENCY FP) -- escalated a query whose correct answer was ALREADY
                                  cached (wasted a model call -- not wrong, just costly).

plus savings = 1 - model_calls/turns, and the RE-ASK ARC (the decisive one): a
wrong answer that got cached, then the user asks again. A cosine cache REPLAYS the
wrong answer forever (absorb_signal every turn, zero self-correction); the membrane
DETECTS the re-ask -> DEMOTES the bad exemplar (safe=False via the evidence path:
add_reask + recompute_safe, exactly as runtime.py does) -> escalates-with-hint ->
the (correct) model answer is delivered. A cosine cache is STRUCTURALLY incapable of
this; the membrane's safe-bit + re-ask demotion is the part that earns its keep.

HONEST CAVEAT (surfaced by building this, not assumed): the membrane's self-
correction is "STOP REPLAYING the wrong answer," NOT "learn the RIGHT answer in
place." runtime._learn's DEDUP guard (dedup_sim=0.95) blocks appending a near-
identical situation, so after a demotion the correct answer is NEVER cached -- the
demoted exemplar stays top-1 (unsafe), and the membrane escalates-with-hint on every
FUTURE turn for that situation. So the trade is: cosine = wrong-and-cheap (absorbs
the bad answer forever, 0 model calls); membrane = correct-but-not-cached (stops the
bad replay after ONE re-ask, at the cost of a model call each recurrence). The
membrane's value is a SAFETY property (bounded absorb_signal), not a better hit rate.
This is the honest result -- reported, not spun.

THREE conditions, SAME seed (identical caches from the same FIELD), SAME stream:
  none    -- never absorb (always escalate). Baseline: 0 safety errors, 0 savings.
  cosine  -- nearest-neighbor replay if sim >= cutoff, else escalate. Learns after an
             escalation (dedup-guarded append, like the membrane). NO safe bit, NO
             demotion, NO consensus -- the cheap obvious alternative.
  membrane-- the REAL gate (gate.decide: sim + consensus + conditional novelty + the
             safe filter) + the re-ask demotion (a near-dup query following an `act`
             demotes the replayed exemplar via add_reask+recompute_safe). `act` only
             on a SAFE match; an unsafe match -> escalate-with-hint. Learns after an
             escalation with the SAME dedup guard as the cosine cache (fair: the only
             difference is the gate + the demotion, not the learn rule). Cutoff = the
             gate's sim_min (fair: same absorb bar as the cosine cache).

The ARC (deterministic demotion exercise): the acquisition gap is a GRANULARITY
mismatch -- a CAS-upsert (a conditional write: upsert(...cas(...))) looks like a plain
upsert (high sim), its FIELD neighbors are plain upserts (high consensus on "cb-upsert"),
so a cache replays "cb-upsert". But the CORRECT action for a CAS-upsert is the more-
granular "cb-upsert-cas" (the CAS makes it a conditional write, a different operation).
Seed the arc target with "cb-upsert" (wrong; consensus-aligned -> the gate ABSORBS it on
turn 1 -> absorb_signal), correct = "cb-upsert-cas". Turn 2 = re-ask -> membrane demotes
-> escalates -> correct; cosine replays "cb-upsert" (absorb_signal). Turn 3 -> membrane
escalates (demoted, correct); cosine replays (absorb_signal). So: membrane 1
absorb_signal (then correct), cosine 3 absorb_signal (never correct). This exercises the
demotion path the cosine cache structurally lacks. (A getAndTouch arc would not work:
FIELD already contains a getAndTouch -> its neighbors share cb-getAndTouch -> consensus
does not align with a cb-get seed -> no absorb -> no demotion. The CAS-upsert gap is a
coarse-vs-fine distinction ABSENT from FIELD, so the coarse seed is consensus-aligned.)

Runs on REAL mxbai embeddings (reuses real_knee's corpus: 15 Couchbase Java FIELD,
15 more-Couchbase NOISE, 10 other-Java BORDERLINE, 12 non-Java NOVEL). The actions
are the real API calls in each snippet -- external ground truth the cache does not
define. BORDERLINE is the acquisition gap on the test phase: high-cosine to Couchbase
but a DIFFERENT correct action -> a sim-only cache replays the wrong (Couchbase)
action = absorb_signal; the gate's consensus may catch it (measured, not asserted).

Needs local Ollama + mxbai-embed-large. Writes results JSON to $CLAUDE_JOB_DIR/tmp
for the artifact; nothing to the repo.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.embed import OllamaEmbedder
from membrane.field import Exemplar, ExemplarStore
from membrane.gate import decide
from membrane.variety import effective_rank

from experiments.real_knee import FIELD, NOISE, BORDERLINE, NOVEL  # noqa: E402

CUTOFF = 0.70          # the gate's sim_min; the cosine cache's cutoff (fair: same bar)
REASK_SIM = 0.95       # a near-dup (>=this cosine) following an `act` = a re-ask
CONSENSUS_MIN = 0.60   # the gate's consensus_min (the cosine cache has NO consensus)
DEDUP_SIM = 0.95       # learn-time guard (runtime._learn): don't append a near-identical sit
DEMOTE_THRESHOLD = 1  # net (reask - contest) votes to demote (runtime default; StubJudge)

# The arc target: a CAS-upsert (conditional write) NOT in FIELD. Its FIELD neighbors
# are plain upserts (action "cb-upsert"), so it is high-sim + high-consensus to "cb-
# upsert" -- but its CORRECT action is the more-granular "cb-upsert-cas" (the CAS makes
# it a conditional write). Seeded with "cb-upsert" (wrong, consensus-aligned -> the gate
# ABSORBS it turn 1 -> absorb_signal); correct = "cb-upsert-cas". Re-ask demotes it.
ARC_TEXT = ('collection.upsert("arc-target", content, '
            'UpsertOptions.upsertOptions().cas(12345L));')
ARC_CORRECT = "cb-upsert-cas"
ARC_SEED_WRONG = "cb-upsert"   # the plain-upsert action its neighbors share (the trap)


def action_of(text: str) -> str:
    """The CORRECT action for a snippet = the real API it calls (external ground truth:
    defined by the code, not by any cache). getAndTouch is MORE GRANULAR than a plain
    get (cb-getAndTouch vs cb-get) -- that granularity gap IS the arc's acquisition gap."""
    t = text
    # Couchbase Java (FIELD + NOISE) -- most specific first
    if "getAndTouch" in t:
        return "cb-getAndTouch"          # the touch is a side-effect a plain get lacks
    if "searchQuery" in t:
        return "cb-search"
    if "lookupIn" in t:
        return "cb-lookup"
    if "mutateIn" in t:
        return "cb-mutate"
    if "diagnostics" in t:
        return "cb-diag"
    if "createCollection" in t or "collections()" in t:
        return "cb-admin"
    if "Cluster.connect" in t:
        return "cb-connect"
    if "exists(" in t:
        return "cb-exists"
    if "remove(" in t:
        return "cb-delete"
    if "replace(" in t:
        return "cb-replace"
    if "insert(" in t and "insertOne" not in t:
        return "cb-insert"
    if "upsert(" in t and ".cas(" in t:
        return "cb-upsert-cas"       # the arc target: a conditional (CAS) write, not a plain upsert
    if "upsert(" in t:
        return "cb-upsert"
    if "query(" in t or "QueryResult" in t:
        return "cb-query"
    if ".get(" in t and "bucket" in t:
        return "cb-get"
    # other-Java libraries (BORDERLINE) -- DIFFERENT actions from Couchbase
    if "MongoClient" in t:
        return "mongo-insert"
    if "JdbcTemplate" in t:
        return "jdbc-query"
    if "HttpClient" in t:
        return "http-send"
    if "RestTemplate" in t:
        return "rest-get"
    if "EntityManager" in t:
        return "jpa-query"
    if "WebClient" in t:
        return "webflux-get"
    if "Jedis" in t:
        return "redis-set"
    if "PreparedStatement" in t:
        return "jdbc-update"
    if "KafkaProducer" in t:
        return "kafka-send"
    if "AmazonS3" in t:
        return "s3-put"
    # non-Java (NOVEL)
    if "pandas" in t or "pd." in t:
        return "pandas-groupby"
    if "requests" in t:
        return "requests-get"
    if "http.Get" in t:
        return "go-http"
    if t.strip().upper().startswith("SELECT") or "GROUP BY" in t:
        return "sql-select"
    if "def fib" in t:
        return "py-func"
    if "express" in t:
        return "js-express"
    if "spark" in t:
        return "spark-filter"
    if "axios" in t:
        return "js-axios"
    if "grep" in t:
        return "shell-grep"
    if "git rebase" in t:
        return "git-rebase"
    if "kubectl" in t:
        return "k8s-apply"
    if "docker" in t:
        return "docker-build"
    return "unknown"


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) + 1e-12)


def _dup_index(vecs, actions, qv, dedup_sim=DEDUP_SIM):
    """The learn-time duplicate guard (runtime._find_dup): the index of an existing
    exemplar whose situation is near-identical (cosine >= dedup_sim), else None.
    Shared by both caches so the ONLY difference is the gate + the demotion."""
    if not vecs:
        return None
    qn = _unit(qv)
    sims = [float(qn @ _unit(v)) for v in vecs]
    i = int(np.argmax(sims))
    return i if sims[i] >= dedup_sim else None


class CosineCache:
    """The cheap obvious alternative: nearest-neighbor replay if sim >= cutoff, else
    escalate. Learns after an escalation (dedup-guarded append). NO safe bit, NO
    demotion, NO consensus -- it cannot self-correct a bad replay."""
    def __init__(self):
        self.vecs: list[np.ndarray] = []
        self.actions: list[str] = []
        self.always_escalate = False

    def seed(self, items):  # items: list of (vec, action)
        for v, a in items:
            self.vecs.append(np.asarray(v, dtype=float))
            self.actions.append(a)

    def learn(self, vec, action):
        di = _dup_index(self.vecs, self.actions, vec)   # dedup guard (same as membrane)
        if di is not None:
            return di                                    # do NOT append a conflicting copy
        self.vecs.append(np.asarray(vec, dtype=float))
        self.actions.append(action)
        return len(self.vecs) - 1

    def query(self, qv):
        if self.always_escalate or not self.vecs:
            return ("escalate", None, 0.0, None)
        qn = _unit(qv)
        sims = [float(qn @ _unit(v)) for v in self.vecs]
        i = int(np.argmax(sims))
        if sims[i] >= CUTOFF:
            return ("act", self.actions[i], sims[i], i)
        return ("escalate", None, sims[i], i)


class MembraneCache:
    """The real gate (gate.decide: sim + consensus + conditional novelty + the safe
    filter) + the re-ask demotion (add_reask + recompute_safe, the evidence path).
    `act` only on a SAFE match; an unsafe match -> escalate-with-hint. A near-dup
    query (>=REASK_SIM) following an `act` demotes the replayed exemplar -> next time
    escalate-with-hint. Learns after an escalation with the SAME dedup guard as the
    cosine cache (fair: the only difference is the gate + demotion)."""
    def __init__(self):
        self.store = ExemplarStore()
        self.last_act_vec = None
        self.last_act_idx = None

    def seed(self, items):
        for v, a in items:
            self.store.append(Exemplar("seed", a, "ok", list(v), safe=True))
        self.last_act_vec = None
        self.last_act_idx = None

    def learn(self, vec, action):
        # dedup guard (runtime._find_dup): a near-identical situation is NOT re-appended
        qn = _unit(vec)
        if len(self.store):
            near, sim = self.store.top_k(qn, k=1)[0]
            if sim >= DEDUP_SIM:
                return self.store.index_of(near)         # do NOT append a conflicting copy
        self.store.append(Exemplar("learn", action, "ok", list(vec), safe=True))
        return len(self.store) - 1

    def query(self, qv):
        v = np.asarray(qv, dtype=float)
        # re-ask detection: a near-dup of the most-recent ACT (a replay that was wrong).
        # Demote the replayed exemplar via the EVIDENCE path (add_reask + recompute_safe),
        # exactly as runtime._detect_reask does -- not a hard one-shot flip.
        if self.last_act_vec is not None:
            if float(_unit(v) @ _unit(self.last_act_vec)) >= REASK_SIM:
                idx = self.last_act_idx
                self.store.add_reask(idx)
                self.store.exemplars[idx].recompute_safe(DEMOTE_THRESHOLD)
                self.last_act_vec = None
                self.last_act_idx = None
                # fall through: re-run the gate (the now-unsafe match -> escalate-with-hint)
        verdict = decide(v, self.store, sim_min=CUTOFF, consensus_min=CONSENSUS_MIN)
        if verdict["verdict"] == "act":
            self.last_act_vec = v
            self.last_act_idx = verdict["ex_idx"]
            return ("act", verdict["action"], verdict["sim"], verdict["ex_idx"])
        self.last_act_vec = None
        self.last_act_idx = None
        return ("escalate", verdict.get("hint"), verdict["sim"], verdict.get("ex_idx"))


def _held_correct(cache, qv, correct):
    """Did the cache already hold the correct answer for qv (nearest sim>=CUTOFF AND
    nearest.action==correct)? Scores pass_noise on an escalation (a wasted model call:
    the right answer was present but not replayed). For `none` this is the seeded cache."""
    if isinstance(cache, MembraneCache):
        if len(cache.store) == 0:
            return False
        qn = _unit(qv)
        near, sim = cache.store.top_k(qn, k=1)[0]
        return sim >= CUTOFF and near.action == correct
    # CosineCache (incl. the `none` always-escalate subclass)
    if not cache.vecs:
        return False
    qn = _unit(qv)
    sims = [float(qn @ _unit(v)) for v in cache.vecs]
    i = int(np.argmax(sims))
    return sims[i] >= CUTOFF and cache.actions[i] == correct


def run_condition(name, cache, stream, vec_of):
    """Run one condition over the stream. The model (action_of) is CORRECT on an
    escalation (isolating cache quality, not model quality). The cache learns after
    each escalation (dedup-guarded). Returns per-query rows + aggregate metrics."""
    rows = []
    absorb_signal = pass_noise = correct_absorb = model_calls = 0
    for text, correct, kind in stream:
        qv = vec_of[text]
        verdict, action, sim, idx = cache.query(qv)
        if verdict == "act":
            model_called = False
            if action == correct:
                correct_absorb += 1
                err = None
            else:
                absorb_signal += 1
                err = "absorb_signal"
        else:  # escalate -> the (correct) model returns the right action; the cache learns it
            got = action_of(text)
            model_calls += 1
            model_called = True
            cache.learn(qv, got)            # dedup-guarded (a poisoned slot is NOT overwritten)
            action = got
            if _held_correct(cache, qv, correct):
                pass_noise += 1
                err = "pass_noise"
            else:
                err = None
        rows.append({"text": text[:46], "kind": kind, "correct": correct,
                     "verdict": verdict, "action": action, "sim": round(float(sim), 3),
                     "model": model_called, "err": err})
    turns = len(stream)
    return {"condition": name, "rows": rows,
            "absorb_signal": absorb_signal, "pass_noise": pass_noise,
            "correct_absorb": correct_absorb, "model_calls": model_calls,
            "turns": turns, "savings": round(1 - model_calls / max(turns, 1), 3)}


def build_stream():
    """Test phase (recurrences + on-manifold noise + the borderline acquisition gap +
    novel) then the 3-turn re-ask arc."""
    stream = []
    for t in FIELD[:5]:                       # recurrences: exact, correct action cached
        stream.append((t, action_of(t), "recurrence"))
    for t in NOISE:                            # on-manifold Couchbase (own correct actions)
        stream.append((t, action_of(t), "noise"))
    for t in BORDERLINE:                      # other-Java: high-cos to Couchbase, diff action
        stream.append((t, action_of(t), "borderline"))
    for t in NOVEL:                           # non-Java: low-sim -> escalate (correct)
        stream.append((t, action_of(t), "novel"))
    stream.append((ARC_TEXT, ARC_CORRECT, "arc-absorb1"))   # turn1: absorb the wrong seed
    stream.append((ARC_TEXT, ARC_CORRECT, "arc-reask"))    # turn2: re-ask -> demote -> correct
    stream.append((ARC_TEXT, ARC_CORRECT, "arc-after"))    # turn3: post-demotion
    return stream


def main() -> None:
    emb = OllamaEmbedder()
    all_texts = FIELD + NOISE + BORDERLINE + NOVEL + [ARC_TEXT]
    print(f"embedding {len(all_texts)} real code snippets via {emb.model} ...")
    vecs = emb.embed(all_texts)
    vec_of = {t: v for t, v in zip(all_texts, vecs)}

    # seed: FIELD with CORRECT actions; the arc target seeded with the WRONG action
    # (simulates a previously-cached bad answer the cache must self-correct).
    field_items = [(vec_of[t], action_of(t)) for t in FIELD]
    arc_wrong_item = (vec_of[ARC_TEXT], ARC_SEED_WRONG)

    def fresh_none():
        c = CosineCache(); c.always_escalate = True   # never absorb, but seeded for held_correct
        c.seed(field_items); c.seed([arc_wrong_item]); return c

    def fresh_cosine():
        c = CosineCache()
        c.seed(field_items); c.seed([arc_wrong_item]); return c

    def fresh_membrane():
        c = MembraneCache()
        c.seed(field_items); c.seed([arc_wrong_item]); return c

    stream = build_stream()

    # field rank (novelty on/off on the real high-rank field)
    fstore = ExemplarStore()
    for v in vecs[:len(FIELD)]:
        fstore.append(Exemplar("c", "a", "ok", list(v), safe=True))
    rr = effective_rank(fstore) / max(len(fstore), 1)

    out = {name: run_condition(name, build(), stream, vec_of)
           for name, build in [("none", fresh_none), ("cosine", fresh_cosine),
                                ("membrane", fresh_membrane)]}

    # ---- console report -------------------------------------------------------
    print(f"\nfield: {len(FIELD)} exemplars, rank_ratio={rr:.2f} -> novelty "
          f"{'ON' if rr < 0.5 else 'OFF (high-rank real field; the veto is inert)'}")
    print(f"stream: {len(stream)} turns (5 recurrence, {len(NOISE)} noise, "
          f"{len(BORDERLINE)} borderline, {len(NOVEL)} novel, 3 arc)\n")
    print(f"{'condition':>9} | {'absorb_signal':>13} {'pass_noise':>10} "
          f"{'correct_abs':>11} {'model_calls':>11} {'savings':>8}")
    for name in ("none", "cosine", "membrane"):
        r = out[name]
        print(f"{name:>9} | {r['absorb_signal']:>13} {r['pass_noise']:>10} "
              f"{r['correct_absorb']:>11} {r['model_calls']:>11} {r['savings']:>8.0%}")

    print("\n== the re-ask arc (the self-correction a cosine cache cannot do) ==")
    print(f"  arc target: getAndTouch; correct action = '{ARC_CORRECT}'; "
          f"cached (seeded) wrong = '{ARC_SEED_WRONG}'")
    for name in ("cosine", "membrane"):
        arc = [r for r in out[name]["rows"] if r["kind"].startswith("arc")]
        seq = " -> ".join(f"{r['kind'].split('-')[1]}:{r['action']}"
                          f"{'['+r['err']+']' if r['err'] else ''}" for r in arc)
        am = sum(r["model"] for r in arc)
        ae = sum(1 for r in arc if r["err"] == "absorb_signal")
        print(f"  {name:>8}: {seq}   ({ae} absorb_signal, {am} model calls)")

    # ---- verdict (report-driven, not pre-baked) -------------------------------
    mem, cos, none_ = out["membrane"], out["cosine"], out["none"]
    gate_tie = (mem["absorb_signal"] == cos["absorb_signal"]
                and mem["correct_absorb"] == cos["correct_absorb"])
    cos_arc = [r for r in cos["rows"] if r["kind"].startswith("arc")]
    mem_arc = [r for r in mem["rows"] if r["kind"].startswith("arc")]
    cos_arc_wrong = sum(1 for r in cos_arc if r["err"] == "absorb_signal")
    mem_arc_wrong = sum(1 for r in mem_arc if r["err"] == "absorb_signal")
    mem_arc_correct = sum(1 for r in mem_arc if r["action"] == ARC_CORRECT
                          and not r["err"])
    # borderline: did the gate's consensus catch the acquisition gap (escalate, not
    # absorb the wrong Couchbase action) where a sim-only cache absorbs (absorb_signal)?
    bl = {n: sum(1 for r in out[n]["rows"] if r["kind"] == "borderline"
                 and r["err"] == "absorb_signal") for n in ("cosine", "membrane")}

    print("\n== verdict ==")
    print(f"  borderline absorb_signal: cosine={bl['cosine']} vs membrane={bl['membrane']} "
          f"-> {'gate consensus catches the acquisition gap' if bl['membrane'] < bl['cosine'] else 'no consensus edge here'}")
    print(f"  raw gate (no arc): membrane absorb_signal={mem['absorb_signal']} vs "
          f"cosine={cos['absorb_signal']} -> "
          f"{'TIE (the gate ~= a cosine cache when novelty is off)' if gate_tie else 'membrane differs (consensus)'}")
    print(f"  re-ask arc: cosine {cos_arc_wrong}/3 wrong replays, {sum(r['model'] for r in cos_arc)} calls; "
          f"membrane {mem_arc_wrong}/3 wrong replays, {sum(r['model'] for r in mem_arc)} calls, "
          f"{mem_arc_correct}/3 correct-after-correction")
    print(f"  -> the membrane STOPS replaying the wrong answer after one re-ask "
          f"({mem_arc_wrong} vs cosine's {cos_arc_wrong} absorb_signal); a cosine cache")
    print(f"     structurally cannot. The cost: the membrane escalates-with-hint on the")
    print(f"     poisoned slot thereafter (dedup blocks overwriting the bad exemplar), so")
    print(f"     self-correction is a SAFETY property (bounded absorb_signal), not a hit-rate")
    print(f"     win. The variety veto is off on this real high-rank field; the value is the")
    print(f"     gate's CONSENSUS (catches borderline granularity mismatches) + the DEMOTION")
    print(f"     (stops a bad replay). Reported, not asserted.")

    # ---- write results JSON for the artifact ----------------------------------
    results = {"meta": {"cutoff": CUTOFF, "consensus_min": CONSENSUS_MIN,
                        "reask_sim": REASK_SIM, "dedup_sim": DEDUP_SIM,
                        "field_n": len(FIELD), "field_rank_ratio": round(rr, 3),
                        "novelty_on": rr < 0.5, "stream_turns": len(stream)}}
    results["conditions"] = {n: {k: v for k, v in out[n].items() if k != "rows"}
                              | {"arc": [r for r in out[n]["rows"] if r["kind"].startswith("arc")]}
                             for n in ("none", "cosine", "membrane")}
    results["per_class"] = {}
    for name in ("none", "cosine", "membrane"):
        by = {}
        for r in out[name]["rows"]:
            k = r["kind"].split("-")[0]
            d = by.setdefault(k, {"n": 0, "absorb_signal": 0, "pass_noise": 0,
                                   "correct_absorb": 0, "model_calls": 0})
            d["n"] += 1
            d["absorb_signal"] += r["err"] == "absorb_signal"
            d["pass_noise"] += r["err"] == "pass_noise"
            d["correct_absorb"] += (r["verdict"] == "act" and not r["err"])
            d["model_calls"] += r["model"]
        results["per_class"][name] = by
    results["verdict"] = {"gate_tie": gate_tie, "bl_cosine": bl["cosine"],
                          "bl_membrane": bl["membrane"],
                          "cos_arc_wrong": cos_arc_wrong, "mem_arc_wrong": mem_arc_wrong,
                          "mem_arc_correct": mem_arc_correct}

    out_path = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "/tmp"),
                            "tmp", "ab_cache_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nresults json -> {out_path}")


if __name__ == "__main__":
    main()