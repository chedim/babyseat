#!/usr/bin/env python
"""Golden fixtures for the semantic entity field (membrane/efield.py).

Records the CURRENT PYTHON behaviour of the field on fixed inputs so a port
(the Rust field) has an exact target. Deterministic: vectors are seeded
(sha256 -> numpy default_rng, dim 64), every ts/now/seq is injected, no
model, no network, no wall clock in any recorded value.

    python scripts/golden_efield.py            # (re)write tests/golden/efield/*.json
    python scripts/golden_efield.py --check    # exit 1 if the committed files differ

The field is driven the way the seat drives it, minus the NLP: sentence rows
and event docs are written straight into a MemBackend (status 'embedded', with
sids and mentions already set -- what embed_pending would have produced), then
EntityField.apply_pending / rebuild run the real code.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from membrane import efield as EF                      # noqa: E402
from membrane.efield import EntityField                # noqa: E402
from membrane.efstore import MemBackend, pack_f16, unpack_f16   # noqa: E402

OUT = ROOT / "tests" / "golden" / "efield"
DIM = 64
ORIGIN = "golden@seat"
T0 = 1_800_000_000.0           # an injected epoch; never the wall clock
DAY = 86400.0


# ---- deterministic vectors ---------------------------------------------------------
def seeded(key: str, dim: int = DIM) -> np.ndarray:
    """A unit vector from sha256(key): the same on every machine and run."""
    seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    v = np.random.default_rng(seed).standard_normal(dim)
    return v / np.linalg.norm(v)


def near(base: str, tag: str, noise: float = 0.6) -> np.ndarray:
    v = seeded(base) + noise * seeded(f"{base}|{tag}")
    return v / np.linalg.norm(v)


def mix(parts: dict, tag: str, noise: float = 0.3) -> np.ndarray:
    v = sum(w * seeded(k) for k, w in parts.items()) + noise * seeded(f"mix|{tag}")
    return v / np.linalg.norm(v)


def L(v) -> list:
    return [float(x) for x in np.asarray(v, dtype=float).ravel()]


class FakeEmbedder:
    """Never called by the generator (vectors are fed directly); present for the facade."""
    model = "golden-fake"

    def embed(self, texts):
        return [seeded(t) for t in texts]


# ---- the driven field ----------------------------------------------------------------
class World:
    def __init__(self):
        self.f = EntityField(MemBackend(), FakeEmbedder(), ORIGIN)
        self.seq = 0
        self.inputs: list = []          # the recorded input log (events with their sentence vectors)

    def seed(self, surface: str, vec) -> None:
        """Pre-store a term seed (what _seed would embed)."""
        self.f.b.upsert("terms", self.f._term_key(surface), {
            "_k": "term", "term": surface.lower(), "model": self.f.model, "vec": pack_f16(vec)})

    def add(self, sentences, mentions, ts, role="user", stream="discourse", session="s1"):
        """sentences: [(text, vec)]; mentions: [(eid, sidx, surface)]."""
        self.seq += 1
        sids, rows = [], []
        for text, v in sentences:
            sid = self.f.sentence_id(text)
            packed = pack_f16(v / (np.linalg.norm(v) + 1e-12))
            self.f.b.insert_if_absent("sentences", sid, {
                "_k": "sent", "text": text, "model": self.f.model, "vec": packed, "first_ts": 0.0})
            sids.append(sid)
            rows.append({"sid": sid, "text": text, "vec_f16": L(unpack_f16(packed))})
        key = f"v:{self.f.o}:{self.seq:06d}"
        seq = self.seq * 1000
        doc = {"_scm": EF.EF_SCHEMA, "_k": "ev", "origin": ORIGIN, "session": session,
               "stream": stream, "role": role, "text": " ".join(t for t, _ in sentences),
               "ts": ts, "seq": seq, "n": self.seq, "status": "embedded",
               "sids": sids, "mentions": [list(m) for m in mentions]}
        self.f.b.upsert("events", key, doc)
        self.f._ostats["seq_last"] = max(int(self.f._ostats["seq_last"]), seq)
        self.inputs.append({"key": key, "seq": seq, "ts": ts, "stream": stream, "role": role,
                            "mentions": [list(m) for m in mentions], "sentences": rows})
        return key

    def step(self, key: str) -> dict:
        """Apply exactly the next pending event; return its recorded outcome."""
        assert self.f.apply_pending(1) == 1
        ev = self.f.b.get("events", key)
        return {"event": key, "surprise": ev.get("surprise"), "coords": ev.get("coords")}

    def snap(self, eids=None) -> dict:
        out = {}
        for eid in sorted(eids if eids is not None else self.f._states):
            st = self.f.get(eid)
            if st is not None:
                out[eid] = snap_state(st)
        return out

    def origin_snap(self) -> dict:
        o = self.f._ostats
        return {"boiler": dict(sorted(o["boiler"].items())),
                "pair": float(o["pair"]), "pair_n": int(o["pair_n"]),
                "bg": {k: L(unpack_f16(v)) for k, v in sorted(o["bg"].items())},
                "bg_n": dict(sorted(o["bg_n"].items()))}


def snap_state(st) -> dict:
    v = lambda a: None if a is None else L(a)
    return {
        "tier": st.tier, "kind": st.kind, "surface": st.surface, "n": st.n,
        "state": v(st.state), "mean": v(st.mean), "rsum": v(st.rsum), "seed": v(st.seed),
        "coherence": float(st.coherence), "net_align": float(st.net_align), "accr": int(st.accr),
        "first_ts": st.first_ts, "last_ts": st.last_ts, "last_accrue_ts": st.last_accrue_ts,
        "last_pos": list(st.last_pos), "recent": list(st.recent),
        "n_by_stream": dict(sorted(st.n_by_stream.items())),
        "n_by_role": dict(sorted(st.n_by_role.items())),
        "echo": float(st.echo), "echo_ts": st.echo_ts,
        "pending": [list(p) for p in st.pending], "distinct": list(st.distinct),
        "episodes": [[L(e), int(c)] for e, c in st.episodes],
        "ep_acc": v(st.ep_acc), "ep_k": int(st.ep_k),
        "ep_meta": [list(m) for m in st.ep_meta], "last_x": v(st.last_x),
    }


def head(doc: str, **extra) -> dict:
    return {"doc": doc, "dim": DIM, "origin": ORIGIN, "t0": T0,
            "constants": {"GAIN": EF.GAIN, "K_MAT": EF.K_MAT, "BOILER_MIN": EF.BOILER_MIN,
                          "ACCRETE": EF.ACCRETE, "EP_RUN": EF.EP_RUN, "EP_MAX": EF.EP_MAX,
                          "CTX_MIN_RESID": EF.CTX_MIN_RESID, "ECHO_HALF_LIFE_S": EF.ECHO_HALF_LIFE_S,
                          "MASS_HALF_LIFE_S": EF.MASS_HALF_LIFE_S,
                          "MASS_CONFLICT_DAMP": EF.MASS_CONFLICT_DAMP},
            **extra}


# ---- (a) one entity, a sequence of mentions ---------------------------------------------
def scenario_single() -> dict:
    w = World()
    w.seed("alpha.py", seeded("seed|alpha.py"))
    w.seed("tempo", seeded("seed|tempo"))
    steps = []
    # 14 mentions of a file entity (materializes at 1; closes 2 episodes of EP_RUN=5; a drifting
    # topic), the roles alternate user/model so echo and n_by_role move; one mention is far
    # off-topic (align < ACCRETE: no accretion), also a word entity that stays in the nursery
    for i in range(14):
        base = "topicA" if i < 8 else "topicB"
        role = ("user", "model", "reasoning")[i % 3]
        key = w.add([(f"mention {i} of the alpha file", near(base, f"a{i}"))],
                    [("f:alpha.py", 0, "alpha.py")] + ([("w:tempo", 0, "tempo")] if i % 4 == 0 else []),
                    ts=T0 + 100.0 * i, role=role)
        out = w.step(key)
        out["touched"] = w.snap(["f:alpha.py", "w:tempo"])
        out["origin"] = w.origin_snap()
        steps.append(out)
    return {**head("one entity, 14 mentions: state/mean/rsum/n/coherence/episodes/echo after each step"),
            "inputs": w.inputs, "steps": steps, "final": w.snap()}


# ---- (b) several entities, a message fitted onto their states --------------------------------
def scenario_fit() -> dict:
    w = World()
    names = ["f:a.py", "f:b.py", "f:c.py"]
    for n in names:
        w.seed(n.split(":")[1], seeded(f"seed|{n}"))
    ts = T0
    for i in range(4):                    # warm the three entities with distinct topics
        for n in names:
            ts += 10.0
            w.step(w.add([(f"warm {n} {i} pad", near(f"dir|{n}", f"w{i}"))],
                         [(n, 0, n.split(":")[1])], ts=ts))
    cases = []
    msgs = [
        ("two subjects", {"dir|f:a.py": 1.0, "dir|f:b.py": 0.7}, ["f:a.py", "f:b.py"]),
        ("three subjects", {"dir|f:a.py": 0.5, "dir|f:b.py": 0.5, "dir|f:c.py": 0.5}, names),
        ("news only", {"unrelated|news": 1.0}, ["f:a.py", "f:c.py"]),
        ("one subject", {"dir|f:c.py": 1.0}, ["f:c.py"]),
    ]
    for i, (label, parts, ents) in enumerate(msgs):
        x_in = mix(parts, f"m{i}")
        stored = unpack_f16(pack_f16(x_in))
        x_msg = EF.unit(stored)                                   # the field's x_msg (one sentence)
        live = sorted(ents)
        A = [w.f.get(e).state for e in live]                      # the states BEFORE the event
        xh, wts = EF.fit_weights(x_msg, A)
        ts += 10.0
        key = w.add([(f"msg {i} {label} pad", x_in)], [(e, 0, e.split(":")[1]) for e in ents], ts=ts)
        out = w.step(key)
        cases.append({"label": label, "mentioned": live, "A_states": [L(a) for a in A],
                      "x_msg": L(x_msg), "weights": L(wts), "fit": L(xh),
                      "residual": L(x_msg - xh), "residual_norm": float(np.linalg.norm(x_msg - xh)),
                      "surprise_direct": float(1.0 - EF.unit(xh) @ x_msg),
                      "event": out, "after": w.snap(live)})
    return {**head("NNLS fit of a message vector onto mentioned states; the event's surprise/coords"),
            "inputs": w.inputs, "cases": cases, "final": w.snap()}


# ---- (c) nursery promotion and boilerplate ------------------------------------------------------
def scenario_nursery() -> dict:
    w = World()
    w.seed("zephyr", seeded("seed|zephyr"))
    w.seed("boilerword", seeded("seed|boilerword"))
    ts, steps = T0, []

    def go(label, sentences, mentions, **kw):
        nonlocal ts
        ts += 50.0
        out = w.step(w.add(sentences, mentions, ts=ts, **kw))
        out["label"] = label
        out["touched"] = w.snap([m[0] for m in mentions if len(m[2]) >= 4 or not m[0].startswith("w:")])
        steps.append(out)
        return out

    s1, s2, s3, s4 = (near("zephyrtopic", f"z{i}") for i in range(4))
    go("1st distinct sentence: nursery", [("zephyr sentence one here", s1)], [("w:zephyr", 0, "zephyr")])
    go("same sentence again: still 1 distinct", [("zephyr sentence one here", s1)], [("w:zephyr", 0, "zephyr")])
    go("2nd distinct: nursery", [("zephyr sentence two here", s2)], [("w:zephyr", 0, "zephyr")])
    go("3rd distinct == K_MAT: promoted, buffered mentions replayed from seed",
       [("zephyr sentence three here", s3)], [("w:zephyr", 0, "zephyr")])
    go("4th after promotion: ordinary update", [("zephyr sentence four here", s4)], [("w:zephyr", 0, "zephyr")])
    go("dust word (3 letters): never a state", [("abc sentence dust here", near("dust", "d"))], [("w:abc", 0, "abc")])
    # boilerplate: the same normalized sentence heard BOILER_MIN+2 times moves nothing past BOILER_MIN
    bp = near("boiler", "b")
    for i in range(EF.BOILER_MIN + 2):
        go(f"boilerplate heard {i + 1}x" + (" (> BOILER_MIN: skipped)" if i + 1 > EF.BOILER_MIN else ""),
           [("ok thanks for that note", bp)], [("f:boil.py", 0, "boil.py")])
    return {**head("nursery promotion at K_MAT distinct sentences; dust cap; boilerplate gate (> BOILER_MIN)"),
            "inputs": w.inputs, "steps": steps, "final": w.snap(), "origin": w.origin_snap()}


# ---- (d) mass metabolism --------------------------------------------------------------------------
def scenario_mass() -> dict:
    w = World()
    w.seed("m.py", seeded("seed|m.py"))
    ts = T0
    for i in range(10):                   # accreting (aligned) mentions, then silence
        ts += 3600.0
        role = "model" if i % 2 else "user"
        # an unrelated chatter event in between keeps the origin's pair baseline low, so the
        # tight mentions of m.py ALIGN (>= ACCRETE) and accrete
        w.step(w.add([(f"chatter {i} pad pad", seeded(f"chatter|{i}"))], [("f:chat.py", 0, "chat.py")],
                     ts=ts - 1.0, role="user"))
        w.step(w.add([(f"m mention {i} pad", near("mtopic", f"m{i}", 0.3))],
                     [("f:m.py", 0, "m.py")], ts=ts, role=role))
    st = w.f.get("f:m.py")
    last = st.last_accrue_ts
    nows = [last, last + 3600.0, last + DAY, last + 7 * DAY, last + 14 * DAY, last + 28 * DAY, last + 100 * DAY,
            last - 500.0]
    doc_plain = {"accr": 7, "last_accrue_ts": last, "last_ts": last + 5.0, "conflicts": []}
    doc_conf = {"accr": 7, "last_accrue_ts": last, "last_ts": last + 5.0, "conflicts": ["x", "y"]}
    doc_nots = {"accr": 7, "last_accrue_ts": None, "last_ts": last, "conflicts": []}
    doc_noclock = {"accr": 7, "last_accrue_ts": None, "last_ts": None, "conflicts": []}
    doc_zero = {"accr": 0, "last_accrue_ts": last, "conflicts": []}
    rows = []
    saved = EF.MASS_METABOLISM
    try:
        for flag in (False, True):
            EF.MASS_METABOLISM = flag
            for now in nows:
                rows.append({
                    "metabolism": flag, "now": now, "accr": st.accr, "echo": st.echo, "echo_ts": st.echo_ts,
                    "mass_eff_state": EF.mass_eff(st, now), "echo_now_state": EF.echo_now(st, now),
                    "mass_eff_doc": EF.mass_eff(doc_plain, now), "mass_eff_doc_conflicts2": EF.mass_eff(doc_conf, now),
                    "mass_eff_doc_no_accrue_ts": EF.mass_eff(doc_nots, now),
                    "mass_eff_doc_no_clock": EF.mass_eff(doc_noclock, now),
                    "mass_eff_doc_accr0": EF.mass_eff(doc_zero, now)})
    finally:
        EF.MASS_METABOLISM = saved
    return {**head("mass_eff / echo_now at several now, MASS_METABOLISM False (default) and True"),
            "docs": {"plain": doc_plain, "conflicts2": doc_conf, "no_accrue_ts": doc_nots,
                     "no_clock": doc_noclock, "accr0": doc_zero},
            "inputs": w.inputs, "state_after_replay": w.snap(["f:m.py"]), "rows": rows}


# ---- (e) rebuild == incremental ----------------------------------------------------------------------
def scenario_rebuild() -> dict:
    w = World()
    for n in ("f:r1.py", "f:r2.py", "w:ripple"):
        w.seed(n.split(":")[1], seeded(f"seed|{n}"))
    ts = T0
    for i in range(24):
        ts += 77.0
        ents = [("f:r1.py", 0, "r1.py")] if i % 3 else [("f:r1.py", 0, "r1.py"), ("f:r2.py", 1, "r2.py")]
        sents = [(f"r sentence {i} first", near("rtopic1", f"{i}a"))]
        if len(ents) == 2:
            sents.append((f"r sentence {i} second", near("rtopic2", f"{i}b")))
        if i % 2 == 0:
            ents.append(("w:ripple", 0, "ripple"))
        w.step(w.add(sents, ents, ts=ts, role=("user", "model")[i % 2]))
    before, ob = w.snap(), w.origin_snap()
    n = w.f.rebuild()
    after, oa = w.snap(), w.origin_snap()
    return {**head("rebuild() re-applies events in seq order and reproduces the incremental states"),
            "inputs": w.inputs, "rebuild_applied": n, "incremental": before, "rebuilt": after,
            "incremental_origin": ob, "rebuilt_origin": oa}


# ---- (f) pure functions on edge cases ---------------------------------------------------------------------
def scenario_pure() -> dict:
    z = np.zeros(DIM)
    a, b = seeded("pa"), seeded("pb")
    out: dict = {}
    out["unit"] = {
        "zero_in": L(z), "zero_out": L(EF.unit(z)),
        "generic_in": L(3.0 * a), "generic_out": L(EF.unit(3.0 * a)),
        "tiny_in": L(1e-20 * a), "tiny_out": L(EF.unit(1e-20 * a)),
        "packed_f16_in": pack_f16(a), "packed_f16_out": L(EF.unit(pack_f16(a))),
    }
    out["at_stored"] = {"in": L(a), "out": L(EF.at_stored(a))}
    out["f32"] = {"in": L(a), "out": L(EF.f32(a))}
    out["move"] = {"before": L(a), "x": L(b), "move_to": L(EF.move_to(a, b)), "move_step": EF.move_step(a, b),
                   "move_to_same": L(EF.move_to(a, a)), "move_step_same": EF.move_step(a, a)}
    out["coherence_of"] = []
    rs = a + b
    for n in (0, 1, 2, 3, 10):
        out["coherence_of"].append({"n": n, "rsum": L(rs), "value": EF.coherence_of(rs, n)})
    out["coherence_of"].append({"n": 2, "rsum": L(a + a), "value": EF.coherence_of(a + a, 2),
                                "note": "identical unit vectors: coherence 1"})

    def fcase(label, x, A, **kw):
        A = np.asarray(A, dtype=float)
        xh, wt = EF.fit_weights(x, A)
        return {"label": label, "x": L(x), "A": [L(r) for r in A], "weights": L(wt), "fit": L(xh)}

    c = seeded("pc")
    cases = [
        fcase("orthogonalish x on one row", a, [a]),
        fcase("x in the span of two rows (0.7a+0.3b)", 0.7 * a + 0.3 * b, [a, b]),
        fcase("three rows, x = a + b", a + b, [a, b, c]),
        fcase("negative-best row is dropped (x = a, rows a and -a+0.1b)", a, [a, -a + 0.1 * b]),
        fcase("identical duplicate rows (degenerate A)", a, [a, a]),
        fcase("near-collinear rows (1e-9 apart)", a + 0.2 * b, [a, a + 1e-9 * b]),
        fcase("zero row among others", a, [z, a]),
        fcase("all-zero A: weights fall back to uniform", a, [z, z]),
        fcase("x orthogonal to every row (all gradients <= tol): uniform fallback",
              np.linalg.qr(np.stack([a, b, c], axis=1))[0][:, 2], [a, b]),
        fcase("zero x: uniform fallback", z, [a, b]),
        fcase("x opposite to the only row: weight 0 -> uniform fallback (1 row: weight 1)", -a, [a]),
    ]
    out["fit_weights"] = cases
    M = np.stack([a, b, c], axis=1)
    out["nnls"] = [
        {"label": "plain", "M_cols": [L(M[:, j]) for j in range(3)], "x": L(a + 2 * b),
         "w": L(EF._nnls(M, a + 2 * b))},
        {"label": "max_on=1", "M_cols": [L(M[:, j]) for j in range(3)], "x": L(a + 2 * b),
         "max_on": 1, "w": L(EF._nnls(M, a + 2 * b, max_on=1))},
        {"label": "max_on=2", "M_cols": [L(M[:, j]) for j in range(3)], "x": L(a + 2 * b + 0.5 * c),
         "max_on": 2, "w": L(EF._nnls(M, a + 2 * b + 0.5 * c, max_on=2))},
    ]
    return {**head("pure functions on edge cases: unit, at_stored, f32, move_to/move_step, coherence_of, fit_weights, _nnls"),
            **out}


SCENARIOS = {
    "single_entity": scenario_single,
    "fit_message": scenario_fit,
    "nursery_boilerplate": scenario_nursery,
    "mass_metabolism": scenario_mass,
    "rebuild": scenario_rebuild,
    "pure_functions": scenario_pure,
}


def build() -> dict:
    """name -> the JSON-ready fixture, regenerated in memory."""
    return {name: json.loads(json.dumps(fn())) for name, fn in SCENARIOS.items()}


def dumps(doc) -> str:
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"


def main(argv) -> int:
    fixtures = build()
    if "--check" in argv:
        bad = [n for n, d in fixtures.items()
               if not (OUT / f"{n}.json").exists() or (OUT / f"{n}.json").read_text() != dumps(d)]
        print("differs:" if bad else "ok", *bad)
        return 1 if bad else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for n, d in fixtures.items():
        (OUT / f"{n}.json").write_text(dumps(d))
        print(f"wrote {OUT / (n + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
