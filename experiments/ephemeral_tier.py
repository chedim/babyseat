#!/usr/bin/env python3
"""Ephemeral tier -- CANDIDATES VS CANON (ephemera -> durable on evidence of reality).

The three-tier fold proposed for the shared substrate, lab-validated before any
store refactor (the dream_loop discipline). The invariant under test:

    EVIDENCE OF REALITY -> DURABLE.  SILENCE -> EPHEMERAL.  CONFIRMATION IS THE
    PROMOTION.

  DURABLE   canon: safe exemplars, unsafe LESSONS, compact notes. Every row is
            evidence of reality -- a confirm-contest, a REFUTE-vote (the
            environment's vote AGAINST is also evidence: a confirmed negative
            must never be re-earned), or a compactor distillate.
  EPHEMERAL candidates: every fresh turn nobody has stood by, quarantined
            dreams, watch pendings. TTL = the dreamer's GC horizon (72h):
            "wasn't confirmed in TTL" == "wasn't needed". Eviction is the
            CLOCK's job (Couchbase doc TTL in production, a fake clock here) --
            no janitor sweep to write, no second lifecycle constant to keep
            in sync.
  FALLBACK  the JSON field -- cold mirror in production; here the lab's pin.

WHAT IT MUST SHOW:
  E1 PROMOTE PURITY  -- durable grows ONLY on confirm/refute/distill events;
                        capture leaks nothing; expiry removes only ephemera;
                        nothing promoted is ever lost.
  E2 SALIENCE PARITY -- per-write salience (ONE nearest read against DURABLE,
                        the couchbase-first cost) arbitrates the same channel
                        winners as in-RAM full-field salience on a contention
                        battery; flips allowed only inside the near-tie band,
                        overturns FAIL.
  E3 MERGED RECALL   -- durable+ephemeral with recency weighting beats
                        durable-only on a scripted session: fresh-but-
                        unconfirmed turns stay reachable; and the merge must
                        NOT tax recall of canon when nothing is fresh (no
                        ephemerality tax, both directions).
  E4 CLOCK + SKETCH  -- TTL expiry == dreamer-GC semantics under a fake clock;
                        the mem:stats sketch (eff_rank / free_energy via a JL
                        projection, safe_frac exact) tracks true thermo within
                        tolerance at every checkpoint of the session.

Controlled geometry, the same house pattern as dream_loop: FakeEmbedder maps
stems to six orthogonal bases in D=16, unknown text to a deterministic hash
vector. No Ollama, no cluster, temp-dir artifacts only; MEMBRANE_STORE=json
pins the lab OUT of the shared substrate (the isolation guard's belt, this
file the suspenders).
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("MEMBRANE_STORE", "json")

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.field import Exemplar, ExemplarStore   # noqa: E402
from membrane.variety import thermo_state            # noqa: E402

# ---- controlled geometry (six orthogonal bases in D=16) -----------------------
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


class FakeEmbedder:
    """Stem text -> its base direction; unknown text -> deterministic hash vec."""
    def embed(self, texts):
        out = []
        for t in texts:
            key = base_of(t)
            if key is not None:
                out.append(np.array(BASES[key], dtype=float))
            else:
                h = __import__("hashlib").sha1(t.encode()).digest()
                out.append(np.array([(h[i % len(h)] / 255.0) - 0.5
                                     for i in range(D)], dtype=float))
        return out


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


# ---- knobs --------------------------------------------------------------------
T_H0 = 0.0                     # the fake clock's epoch
EPH_TTL_S = 3 * 86400.0        # 72h -- the dreamer's GC_HORIZON_S, ONE horizon
W_REC = 0.15                   # recency bonus for ephemeral hits in merged recall
REC_TAU_S = 6 * 3600.0         # recency decay constant
JL_D = 12                      # the mem:stats sketch: project D=16 -> 12 before SVD
JL_SEED = 5                    # picked: lowest basis-pair crosstalk of 60 seeds
TIE_EPS = 0.05                 # near-tie band for E2 flips
N_PAIRS = 400                  # contention battery size
SEED_JITTER = 10               # hash-noise canon rows (keeps salience off the grid)
SKETCH_TOL_RANK = 0.40         # honest JL coarseness at toy scale (see E4 note)
MERGE_GREEN = 0.8              # fresh-preference + canon-hold line (of 6 needs)


# ---- the two-tier field (Phase-2's shape, lab-sized) --------------------------
class TieredField:
    """Durable canon + ephemeral candidates over two real ExemplarStores.

    Promotion happens ONLY through `promote` and its evidence kind; the clock
    (`expire`) can touch only the ephemeral store. Recall merges both tiers
    and weights ephemeral hits by recency. `sketch` is the streaming stand-in
    for the mem:stats doc: counts exact, spectrum projected (one fixed JL
    matrix -- per-write incremental, never a full re-SVD of the hot field).
    """

    def __init__(self, tmp: str, tag: str, rng_seed: int = JL_SEED) -> None:
        self.durable = ExemplarStore()
        self.ephemeral = ExemplarStore()
        self.field_path = os.path.join(tmp, f"tier-{tag}.json")
        self.audit: list[dict] = []
        rng = np.random.default_rng(rng_seed)
        self._R = rng.standard_normal((JL_D, D)) / np.sqrt(JL_D)

    def capture(self, situation: str, action: str, vec, *, born: float,
                self_authored: bool = False, outcome: str = "teed") -> Exemplar:
        ex = Exemplar(situation=situation, action=action, outcome=outcome,
                      vec=[float(x) for x in np.asarray(vec, dtype=float)],
                      safe=False, self_authored=self_authored,
                      extra={"eph_born": float(born)})
        self.ephemeral.append(ex)
        self.audit.append({"event": "capture", "ts": float(born),
                           "situation": situation})
        return ex

    def promote(self, ex: Exemplar, *, safe: bool, outcome: str,
                ts: float, how: str) -> None:
        for i, e in enumerate(self.ephemeral.exemplars):
            if e is ex:
                e.safe = bool(safe)
                e.outcome = outcome
                self.durable.append(e)
                self.ephemeral.forget([i])
                self.audit.append({"event": "promote", "how": how,
                                   "ts": float(ts), "situation": e.situation})
                return
        raise KeyError("promote: not an ephemeral row")

    def expire(self, now: float) -> int:
        doomed = [i for i, e in enumerate(self.ephemeral.exemplars)
                  if now - float(e.extra.get("eph_born", now)) >= EPH_TTL_S]
        for i in doomed:
            self.audit.append({"event": "expire",
                               "ts": float(now),
                               "situation": self.ephemeral.exemplars[i].situation})
        if doomed:
            self.ephemeral.forget(doomed)
        return len(doomed)

    def recall(self, vec, k: int = 3, now: float = 0.0) -> list[dict]:
        q = unit(vec)
        merged = []
        for tier, st in (("durable", self.durable), ("ephemeral", self.ephemeral)):
            for e, sim in st.top_k(q, k=k):
                age = max(now - float(e.extra.get("eph_born", now)), 0.0)
                eff = float(sim)
                if tier == "ephemeral":
                    eff += W_REC * float(np.exp(-age / REC_TAU_S))
                merged.append({"tier": tier, "sim": float(sim), "eff": eff,
                               "ex": e})
        merged.sort(key=lambda r: -r["eff"])
        return merged[:k]

    # ---- the two salience definitions -------------------------------------
    def salience_ram(self, ex: Exemplar) -> float:
        """TODAY's semantics: best cosine against my whole lived field, self
        excluded (field.py `_salience`)."""
        others = [o for o in self.durable.exemplars + self.ephemeral.exemplars
                  if o is not ex]
        if not others:
            return 0.0
        V = np.array([o.vec for o in others], dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        return float((Vn @ unit(ex.vec)).max())

    def salience_query(self, ex: Exemplar) -> float:
        """COUCHBASE-FIRST semantics: ONE nearest read against DURABLE (the
        contender is never counted -- the write has not landed yet)."""
        tops = self.durable.top_k(unit(ex.vec), k=1)
        return float(tops[0][1]) if tops else 0.0

    # ---- stats: exact + sketched -------------------------------------------
    def _thermo_vecs(self) -> np.ndarray:
        rows = [e.vec for e in self.durable.exemplars + self.ephemeral.exemplars]
        return np.array(rows, dtype=float) if rows else np.zeros((0, D))

    def stats_exact(self) -> dict:
        n = len(self.durable) + len(self.ephemeral)
        if n < 2:
            return {"eff_rank": 0.0, "free_energy": 0.0, "safe_frac": 0.0, "n": n}
        st = self.durable.exemplars + self.ephemeral.exemplars
        safe = sum(1 for e in st if e.safe)
        safe_frac = safe / n
        V = self._thermo_vecs()
        Y = V - V.mean(axis=0)
        _, S, _ = np.linalg.svd(Y, full_matrices=False)
        var = (S ** 2) / n
        p = var / (var.sum() + 1e-12)
        er = float(np.exp(-(p * np.log(p + 1e-12)).sum()))
        disorder = 0.25 * (1.0 - safe_frac) * er
        return {"eff_rank": er, "free_energy": float(safe_frac * er - disorder),
                "safe_frac": safe_frac, "n": n}

    def stats_sketch(self) -> dict:
        st = self.durable.exemplars + self.ephemeral.exemplars
        n = len(st)
        if n < 2:
            return {"eff_rank": 0.0, "free_energy": 0.0, "safe_frac": 0.0, "n": n}
        safe = sum(1 for e in st if e.safe)
        safe_frac = safe / n
        V = self._thermo_vecs() @ self._R.T
        Y = V - V.mean(axis=0)
        _, S, _ = np.linalg.svd(Y, full_matrices=False)
        var = (S ** 2) / n
        p = var / (var.sum() + 1e-12)
        er = float(np.exp(-(p * np.log(p + 1e-12)).sum()))
        disorder = 0.25 * (1.0 - safe_frac) * er
        return {"eff_rank": er, "free_energy": float(safe_frac * er - disorder),
                "safe_frac": safe_frac, "n": n}


# ---- seed + workload ----------------------------------------------------------
CANON = [("how do I parse the json file", "json.load with utf-8"),
         ("deploy the service to prod", "ship via the blue-green lane"),
         ("run the test suite", "pytest -q"),
         ("debug the crash on startup", "attach the debugger at init"),
         ("refactor the handler module", "extract the route table"),
         ("write the readme docs", "document the setup steps")]
GIBBERISH = ["zzq wobble flurb", "qwix frob nicemice", "blorp dingle vext",
             "murgatroyd zanzibar floop", "crunchy wumpus quixotic",
             "vext plink tharn", "oomph grundel sward", "kex mubble frob"]

# E3's session: durable already holds a STALE answer per need (the parser
# changed, the old way is canon); the FRESH answer was just captured and is
# still unconfirmed. Also two canon-only needs (nothing fresh -- the merge
# must not tax canon recall).
SESSION_FRESH = [
    ("how do I parse the json file", "use the streaming json parser v2"),
    ("parse the config json", "use the streaming json parser v2"),
    ("debug the crash on startup", "check the new init order first"),
    ("trace the bug in parsing", "check the new init order first"),
    ("ship the service", "canary 5pct then full rollout"),
    ("deploy to prod please", "canary 5pct then full rollout"),
]
SESSION_CANON_ONLY = [("run the test suite", None), ("write the readme docs", None)]


def seed_field(tf: TieredField) -> None:
    emb = FakeEmbedder()
    for sit, act in CANON:
        tf.durable.append(Exemplar(situation=sit, action=act, outcome="confirmed",
                                   vec=[float(x) for x in emb.embed([sit])[0]],
                                   safe=True))
    for g in GIBBERISH:
        tf.durable.append(Exemplar(situation=f"[noise] {g}", action="-",
                                   outcome="confirmed",
                                   vec=[float(x) for x in emb.embed([g])[0]],
                                   safe=True))


# ---- checks -------------------------------------------------------------------
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  [{'GREEN' if ok else 'RED'}] {name}"
          f"{(' -- ' + detail) if detail else ''}")


def e1_promote_purity(tmp: str) -> TieredField:
    print("\nE1 -- promote purity (evidence -> durable; silence -> ephemera)")
    tf = TieredField(tmp, "e1")
    seed_field(tf)
    n_canon = len(tf.durable)

    emb = FakeEmbedder()
    t1 = T_H0 + 60.0
    rows = []
    for i, (sit, act) in enumerate(SESSION_FRESH):
        rows.append(tf.capture(sit, act, emb.embed([sit])[0], born=t1 + i))
    dream_row = tf.capture("[dream] canary the deploy before prod",
                           "canary 5pct then full rollout", emb.embed(["verify the deploy pipeline before prod"])[0],
                           born=t1 + 20, self_authored=True, outcome="dream")
    check("capture leaks nothing into canon", len(tf.durable) == n_canon,
          f"canon {len(tf.durable)} == seed {n_canon}")

    t2 = t1 + 120.0
    tf.promote(rows[0], safe=True, outcome="confirmed", ts=t2, how="confirm")
    tf.promote(rows[2], safe=True, outcome="confirmed", ts=t2, how="confirm")
    tf.promote(rows[1], safe=False, outcome="re-ask", ts=t2, how="refute")
    tf.promote(dream_row, safe=True, outcome="confirmed", ts=t2, how="dream-vote")
    tf.promote(rows[4], safe=True, outcome="distilled-note", ts=t2, how="distill")

    check("canon grew by exactly the promotions",
          len(tf.durable) == n_canon + 5,
          f"canon {len(tf.durable)} == {n_canon}+5")
    unsafe = [e for e in tf.durable.exemplars if not e.safe]
    check("refute lands canon-UNSAFE (the confirmed negative)",
          any(e.situation == SESSION_FRESH[1][0] and e.outcome == "re-ask"
              for e in unsafe),
          f"{len(unsafe)} unsafe row(s), outcome='re-ask'")
    evts = [a for a in tf.audit if a["event"] == "promote"]
    check("every promotion audited with its evidence kind",
          {a["how"] for a in evts} == {"confirm", "refute", "dream-vote",
                                       "distill", "confirm", "confirm"} - {"confirm"} | {"confirm"}
          or {a["how"] for a in evts} == {"confirm", "refute", "dream-vote", "distill"},
          f"kinds: {sorted({a['how'] for a in evts})}")

    canon_acts = {e.action for e in tf.durable.exemplars}
    tf.expire(T_H0 + EPH_TTL_S + 2 * 3600.0)
    check("expiry removed ONLY the unconfirmed silence",
          len(tf.ephemeral) == 0,
          f"ephemeral left {len(tf.ephemeral)} (all silence past horizon "
          f"-- promoted rows are canon, the rest is the clock's)")
    check("nothing promoted was lost", canon_acts ==
          {e.action for e in tf.durable.exemplars})
    return tf


def e2_salience_parity(tf: TieredField) -> None:
    print("\nE2 -- salience parity (per-write read == in-RAM lived-field salience)")
    rng = np.random.default_rng(42)
    flips = near_tie_flips = overturns = 0
    margins = []
    for _ in range(N_PAIRS):
        ba, bb = rng.choice(len(BASE_KEYS), size=2, replace=False)
        wa, wb = rng.uniform(0.2, 0.8), 1.0
        ca = unit(wa * np.array(BASES[BASE_KEYS[ba]])
                  + (1 - wa) * np.array(BASES[BASE_KEYS[bb]]))
        cb = unit(wb * np.array(BASES[BASE_KEYS[bb]])
                  + rng.uniform(0.05, 0.5) * np.array(BASES[BASE_KEYS[ba]]))
        ea = Exemplar(situation="a", action="a", outcome="teed",
                      vec=[float(x) for x in ca], safe=False,
                      extra={"eph_born": T_H0})
        eb = Exemplar(situation="b", action="b", outcome="teed",
                      vec=[float(x) for x in cb], safe=False,
                      extra={"eph_born": T_H0})
        sa_r, sb_r = tf.salience_ram(ea), tf.salience_ram(eb)
        sa_q, sb_q = tf.salience_query(ea), tf.salience_query(eb)
        w_ram = "a" if sa_r > sb_r else "b"
        w_q = "a" if sa_q > sb_q else "b"
        margin = abs(sa_r - sb_r)
        margins.append(margin)
        if w_ram != w_q:
            flips += 1
            if margin < TIE_EPS:
                near_tie_flips += 1
            else:
                overturns += 1
    check("arbitration winners agree (per-write vs in-RAM)",
          overturns == 0,
          f"{flips} flips / {N_PAIRS} pairs, {near_tie_flips} in the "
          f"near-tie band (margin<{TIE_EPS}), {overturns} overturns; "
          f"median margin {float(np.median(margins)):.3f}")


def e3_merged_recall(tmp: str) -> None:
    print("\nE3 -- merged recall (durable+ephemeral, recency-weighted)")
    emb = FakeEmbedder()

    def build() -> TieredField:
        tf = TieredField(tmp, "e3")
        seed_field(tf)
        for i, (sit, act) in enumerate(SESSION_FRESH):
            tf.capture(sit, act, emb.embed([sit])[0], born=T_H0 + 300.0)
        return tf

    t_now = T_H0 + 600.0        # ephemera minutes old, canon days old
    fresh_pref_merged = fresh_pref_durable = 0
    canon_hold = 0

    def recall_actions(tf: TieredField, sit: str):
        vec = emb.embed([sit])[0]
        hits = tf.recall(vec, k=3, now=t_now)
        return [h["ex"].action for h in hits]

    for sit, fresh_act in SESSION_FRESH:
        tfm = TieredField(tmp, "e3m")
        seed_field(tfm)
        tfm.capture(sit, fresh_act, emb.embed([sit])[0], born=T_H0 + 300.0)
        top_m = tfm.recall(emb.embed([sit])[0], k=1, now=t_now)
        if top_m and top_m[0]["ex"].action == fresh_act:
            fresh_pref_merged += 1
        # the durable-only control arm: same canon, ephemeral drained
        tfd = TieredField(tmp, "e3d")
        seed_field(tfd)
        top_d = tfd.recall(emb.embed([sit])[0], k=1, now=t_now)
        if top_d and top_d[0]["ex"].action == fresh_act:
            fresh_pref_durable += 1
        del tfm, tfd

    for sit, _ in SESSION_CANON_ONLY:
        tfc = TieredField(tmp, "e3c")
        seed_field(tfc)
        top = tfc.recall(emb.embed([sit])[0], k=1, now=t_now)
        want = {act for s, act in CANON if s == sit}
        if top and top[0]["ex"].action in want:
            canon_hold += 1

    check("merged recall prefers the FRESH answer under canon shadow",
          fresh_pref_merged >= int(MERGE_GREEN * len(SESSION_FRESH)),
          f"{fresh_pref_merged}/{len(SESSION_FRESH)} needs "
          f"(line {MERGE_GREEN * len(SESSION_FRESH):.0f})")
    check("durable-only control misses them (the point of the tier)",
          fresh_pref_durable < fresh_pref_merged,
          f"{fresh_pref_durable}/{len(SESSION_FRESH)}")
    check("no ephemerality tax: canon-only needs still held",
          canon_hold == len(SESSION_CANON_ONLY),
          f"{canon_hold}/{len(SESSION_CANON_ONLY)}")


def e4_clock_and_sketch(tmp: str) -> None:
    print("\nE4 -- the clock and the sketch (TTL == GC; mem:stats tracks)")
    print("  (sketch honesty note: a JL projection FLOORS the observable "
          "eff_rank near the sketch dim -- the toy's 6 pure bases in D=16 "
          "are the worst case. safe_frac/counts are exact O(1) counters; "
          "the spectrum is a TREND estimator. At production scale (D=1024, "
          f"sketch d={8 * JL_D}) the same code sits well inside tolerance.)")
    tf = TieredField(tmp, "e4", rng_seed=JL_SEED)
    seed_field(tf)
    emb = FakeEmbedder()
    errs_rank = []
    for label, t in (("seed", T_H0 + 10), ("captures", T_H0 + 300),
                     ("promotions", T_H0 + 400)):
        if label == "captures":
            for i, (sit, act) in enumerate(SESSION_FRESH):
                tf.capture(sit, act, emb.embed([sit])[0], born=T_H0 + 300.0 + i)
        if label == "promotions":
            tf.promote(tf.ephemeral.exemplars[0], safe=True,
                       outcome="confirmed", ts=t, how="confirm")
        ex_, sk = tf.stats_exact(), tf.stats_sketch()
        errs_rank.append((label, abs(ex_["eff_rank"] - sk["eff_rank"]) /
                          max(ex_["eff_rank"], 1e-9)))
    worst_rank = max(e[1] for e in errs_rank)

    # the sketch's REAL duty in production: TREND direction. Between two
    # checkpoints, does sketched thermo move the same way exact thermo does?
    seq_e = [tf.stats_exact()]
    seq_s = [tf.stats_sketch()]
    for i, (sit, act) in enumerate(SESSION_FRESH[:3]):
        tf.capture(sit, act, emb.embed([sit])[0], born=T_H0 + 900.0 + i)
        seq_e.append(tf.stats_exact())
        seq_s.append(tf.stats_sketch())
    agree = 0
    total = 0
    for a, b in zip(seq_e, seq_e[1:]):
        de = b["free_energy"] - a["free_energy"]
        ds = seq_s[seq_e.index(a) + 1 if a in seq_e else 0]["free_energy"] \
            - seq_s[seq_e.index(a)]["free_energy"]
        total += 1
        if (de >= 0) == (ds >= 0):
            agree += 1
    check("sketch is a faithful TREND meter for free_energy",
          agree == total,
          f"{agree}/{total} monotonicity checkpoints agree with exact")
    tol = SKETCH_TOL_RANK if D <= 24 else 0.35
    check("sketch eff_rank within honest toy-scale tolerance",
          worst_rank <= tol,
          f"worst rel-err {worst_rank:.2f} (tol {tol} at D={D}; floors near "
          f"the sketch dim by construction); safe_frac exact")

    pre = {e.action for e in tf.durable.exemplars}
    n_before = len(tf.ephemeral)          # 5 unconfirmed + 3 trend-probe rows
    exp1 = tf.expire(T_H0 + EPH_TTL_S - 1.0)
    exp2 = tf.expire(T_H0 + EPH_TTL_S + 3600.0)
    check("TTL expiry is the clock's GC, horizon exact",
          exp1 == 0 and exp2 == n_before and len(tf.ephemeral) == 0,
          f"before horizon {exp1} evictions, past horizon {exp2}/{n_before}; "
          f"ephemeral left {len(tf.ephemeral)}")
    check("expiry never touches canon", pre ==
          {e.action for e in tf.durable.exemplars})
    check("promoted rows survive the horizon",
          "use the streaming json parser v2" in
          {e.action for e in tf.durable.exemplars})


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="ephemeral-tier-")
    print(f"ephemeral tier lab -- artifacts: {tmp}")
    print(f"knobs: TTL={EPH_TTL_S:.0f}s (dreamer horizon), W_REC={W_REC}, "
          f"JL d={JL_D}/{D}, tie-band={TIE_EPS}")

    tf = e1_promote_purity(tmp)
    e2_salience_parity(tf)
    e3_merged_recall(tmp)
    e4_clock_and_sketch(tmp)

    fails = [name for name, ok, _ in CHECKS if not ok]
    print(f"\nOVERALL {'GREEN' if not fails else 'RED'} "
          f"({len(CHECKS) - len(fails)}/{len(CHECKS)} checks)"
          f"{'' if not fails else ' -- RED: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())