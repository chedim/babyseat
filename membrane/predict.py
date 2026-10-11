"""MEMBRANE PREDICTS ARGUS (2026-10-10, the operator: "can the goal of
membrane's speech generator be predicting the model's answers? maybe even
actions? and then compare what it said/did to what the model said/did ...
in that mode the LLM would not get to see membrane's mumblings ... should
membrane's actions be virtual?").

Before every turn (the chat's and the watch loops'), membrane writes down
what it expects Argus to DO: the tools (family.action) and the entities he
will touch, top 3 each. When the turn ends the guess is scored against the
tool calls he really made, and the predictor learns from the miss:

  tools     the nearest past turns (by the message's vector) vote with
            their tools; the lane's own frequencies back them up
  entities  candidates from three sources -- the entities the message
            names, the ones the nearest past turns touched, the live field
            states nearest the message -- each source weighted by how
            often it has been right (its hits / its proposals)

THE COVENANTS: a guess is never executed (membrane has no hands: these
are virtual actions, a ledger line and a score); it never enters Argus's
context, the session's messages or the field. The chat's text guess is
the voice's own line (voice.py), graded by closeness as before.
Kill switch: MEMBRANE_PREDICT=0."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

TOP = 3
KNN = 16
MEM_MAX = 4000
NONE = "(none)"                       # a turn with no tool calls is a prediction too
SOURCES = ("named", "past", "near")
_ENT_KEYS = ("spec", "eid", "name", "entity", "key", "id", "object", "on", "over")
_CLASS_HEAD = re.compile(r"^[A-Z][A-Za-z]*:")


def enabled() -> bool:
    return os.environ.get("MEMBRANE_PREDICT", "1") != "0"


def norm_ent(x) -> str:
    """One spelling per entity: no seat namespace, no class head, no
    model-mirror head ('membrane::Component:ha:light.x' == 'ha:light.x')."""
    s = str(x or "").strip()
    if "::" in s:
        s = s.split("::", 1)[1]
    s = _CLASS_HEAD.sub("", s)
    if s.startswith("m:") and s.count(":") >= 2:
        s = s[2:]
    return s.lower()


def action_of(tool: str, args: dict) -> tuple[str, list[str]]:
    """A tool call -> ('family.action', the entities it names)."""
    from . import toolset as T
    try:
        impl, a = T.resolve(tool, dict(args or {}))
    except Exception:
        impl, a = str(tool or ""), dict(args or {})
    fam = T._LEGACY_FAMILY.get(impl)
    act = next((k for k, v in (T.FAMILIES.get(fam) or {}).get("actions", {}).items() if v == impl), "") if fam else ""
    name = f"{fam}.{act}" if fam else impl
    ents = []
    for k in _ENT_KEYS:
        v = a.get(k)
        if isinstance(v, str) and v.strip() and len(v) < 200 and " " not in v.strip():
            ents.append(norm_ent(v))
    return name, list(dict.fromkeys(e for e in ents if e))


def actions_of_parts(parts) -> tuple[list[str], list[str]]:
    """What the turn really did: its tool parts' actions and entities."""
    tools, ents = [], []
    for p in parts or []:
        if not isinstance(p, dict) or p.get("type") != "tool":
            continue
        st = p.get("state") or {}
        name, es = action_of(p.get("tool") or p.get("name") or "", st.get("input") or {})
        tools.append(name)
        ents.extend(es)
    return list(dict.fromkeys(tools)), list(dict.fromkeys(ents))


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


class Predictor:
    def __init__(self, data_dir=None):
        self.dir = Path(data_dir) / "voice" if data_dir else None
        self._lock = threading.Lock()
        self.vecs: list[np.ndarray] = []
        self.turns: list[dict] = []           # {"lane", "tools", "ents"} per remembered turn
        self.freq: dict[str, dict[str, float]] = {}
        self.src = {s: {"hit": 1.0, "prop": 2.0} for s in SOURCES}
        self.stats: dict[str, dict] = {}
        self._load()

    # ---- persistence -----------------------------------------------------------
    def _load(self) -> None:
        if self.dir is None:
            return
        try:
            p = self.dir / "predict_state.json"
            if p.exists():
                d = json.load(open(p))
                self.turns = d.get("turns") or []
                self.freq = d.get("freq") or {}
                self.src.update(d.get("src") or {})
                self.stats = d.get("stats") or {}
            z = self.dir / "predict_vecs.npy"
            if z.exists() and self.turns:
                M = np.load(str(z)).astype(np.float32)
                self.vecs = [M[i] for i in range(min(len(M), len(self.turns)))]
                self.turns = self.turns[:len(self.vecs)]
        except Exception as e:
            print(f"[predict] load failed: {e}", file=sys.stderr)

    def save(self) -> None:
        if self.dir is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                d = {"turns": self.turns, "freq": self.freq, "src": self.src, "stats": self.stats}
                M = np.stack(self.vecs).astype(np.float16) if self.vecs else np.zeros((0, 1), np.float16)
            tmp = self.dir / "predict_state.json.tmp"
            json.dump(d, open(tmp, "w"))
            os.replace(tmp, self.dir / "predict_state.json")
            np.save(str(self.dir / "predict_vecs.npy"), M)
        except Exception as e:
            print(f"[predict] save failed: {e}", file=sys.stderr)

    def _ledger(self, rec: dict) -> None:
        if self.dir is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / "predict.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    # ---- the guess -------------------------------------------------------------
    def _neighbours(self, qv, lane: str):
        with self._lock:
            if not self.vecs:
                return []
            M = np.stack(self.vecs)
            turns = list(self.turns)
        s = M @ _unit(qv)
        idx = np.argsort(-s)[:KNN * 2]
        out = [(float(s[i]), turns[i]) for i in idx if turns[i].get("lane") == lane][:KNN]
        return out or [(float(s[i]), turns[i]) for i in idx[:KNN]]

    def guess(self, qv, lane: str, named=(), near=()) -> dict:
        """{'tools': [[name, p]...], 'ents': [[eid, score, source]...]} -- top 3."""
        nb = self._neighbours(qv, lane) if qv is not None else []
        tv: dict[str, float] = {}
        for sim, t in nb:
            w = max(sim, 0.0) ** 2
            for name in (t.get("tools") or [NONE]):
                tv[name] = tv.get(name, 0.0) + w
        prior = self.freq.get(lane) or {}
        tot = sum(prior.values()) or 1.0
        for name, c in prior.items():
            tv[name] = tv.get(name, 0.0) + 0.25 * c / tot
        z = sum(tv.values()) or 1.0
        tools = sorted(((n, round(v / z, 3)) for n, v in tv.items()), key=lambda x: -x[1])[:TOP]
        cand: dict[str, list] = {}

        def add(e, score, src):
            e = norm_ent(e)
            if not e:
                return
            w = self.src[src]["hit"] / self.src[src]["prop"]
            c = cand.setdefault(e, [0.0, src, set()])
            c[0] += w * score
            c[2].add(src)
            if w * score > 0 and src != c[1] and w * score >= c[0] / 2:
                c[1] = src

        for e in named:
            add(e, 1.0, "named")
        for sim, t in nb:
            for e in t.get("ents") or []:
                add(e, max(sim, 0.0) ** 2, "past")
        for e, s in near:
            add(e, max(float(s), 0.0), "near")
        ents = sorted(([e, round(v[0], 3), v[1], sorted(v[2])] for e, v in cand.items()), key=lambda x: -x[1])[:TOP]
        return {"tools": [list(t) for t in tools], "ents": ents}

    # ---- the score -------------------------------------------------------------
    def score(self, guess: dict, qv, lane: str, tools: list[str], ents: list[str], meta=None) -> dict:
        """The guess against what Argus did; then the predictor learns."""
        actual_t = tools or [NONE]
        gt = [t[0] for t in guess.get("tools") or []]
        ge = [e[0] for e in guess.get("ents") or []]
        res = {"tool1": bool(gt) and gt[0] in actual_t,
               "tool3": any(t in actual_t for t in gt),
               "ent3": (any(e in ents for e in ge) if ents else None),
               "ents_touched": len(ents)}
        with self._lock:
            # the sources learn: every proposal of a top-3 entity, every hit
            for e in guess.get("ents") or []:
                for src in (e[3] if len(e) > 3 else [e[2]]):
                    if src in self.src:
                        self.src[src]["prop"] += 1.0
                        if e[0] in ents:
                            self.src[src]["hit"] += 1.0
            f = self.freq.setdefault(lane, {})
            for t in actual_t:
                f[t] = f.get(t, 0.0) + 1.0
            if qv is not None:
                self.vecs.append(_unit(qv))
                self.turns.append({"lane": lane, "tools": tools, "ents": ents[:12]})
                if len(self.vecs) > MEM_MAX:
                    self.vecs = self.vecs[-MEM_MAX:]
                    self.turns = self.turns[-MEM_MAX:]
            s = self.stats.setdefault(lane, {"turns": 0, "tool1": 0, "tool3": 0, "ent_turns": 0, "ent3": 0,
                                             "recent": []})
            s["turns"] += 1
            s["tool1"] += int(res["tool1"])
            s["tool3"] += int(res["tool3"])
            if res["ent3"] is not None:
                s["ent_turns"] += 1
                s["ent3"] += int(res["ent3"])
            s["recent"] = (s.get("recent") or [])[-49:] + [[int(res["tool1"]), int(res["tool3"]),
                                                           -1 if res["ent3"] is None else int(res["ent3"])]]
        self._ledger({"time": int(time.time() * 1000), "lane": lane, "guess": guess,
                      "did": {"tools": tools, "ents": ents}, "score": res, **(meta or {})})
        n = sum(x["turns"] for x in self.stats.values())
        if n % 10 == 0:
            self.save()
        return res

    def report(self) -> dict:
        """Per lane: all-time and the last 50 turns' hit rates."""
        out = {}
        with self._lock:
            for lane, s in self.stats.items():
                r = s.get("recent") or []
                re_ = [x for x in r if x[2] >= 0]
                out[lane] = {
                    "turns": s["turns"],
                    "tool_top1": round(s["tool1"] / max(1, s["turns"]), 3),
                    "tool_top3": round(s["tool3"] / max(1, s["turns"]), 3),
                    "entity_top3": round(s["ent3"] / max(1, s["ent_turns"]), 3),
                    "last50": {"tool_top1": round(sum(x[0] for x in r) / max(1, len(r)), 3),
                               "tool_top3": round(sum(x[1] for x in r) / max(1, len(r)), 3),
                               "entity_top3": round(sum(x[2] for x in re_) / max(1, len(re_)), 3)}}
            out["sources"] = {k: round(v["hit"] / v["prop"], 3) for k, v in self.src.items()}
            out["memory"] = len(self.turns)
        return out


# ---- the seat's wiring --------------------------------------------------------
def of(srv):
    if srv is None or not enabled():
        return None
    p = getattr(srv, "_predictor", None)
    if p is None:
        mp = getattr(srv, "_models_path", None)
        p = srv._predictor = Predictor(Path(mp).parent if mp else None)
    return p


def candidates(srv, text: str, qv) -> tuple[list[str], list[tuple[str, float]]]:
    """(entities the message names, live field states nearest it)."""
    named, near = [], []
    try:
        from .lanes import _efield_of
        fld = _efield_of(srv)
    except Exception:
        fld = None
    if fld is None:
        return named, near
    try:
        from . import efgate
        named = sorted(efgate.specifics(fld, text))[:12]
    except Exception:
        pass
    if qv is not None:
        try:
            for key, s in fld.b.vector_top_k("entity_states", "state", qv, 8, eq={"tier": "live"}):
                d = fld.b.get("entity_states", key) or {}
                if d.get("eid"):
                    near.append((d["eid"], s))
        except Exception:
            pass
    return named, near


def start(runner, sid: str, text: str, lane: str, embed=None, qv=None) -> dict | None:
    """The guess for this turn, kept until it ends (a thread's job: the
    caller never waits on it). Returns the guess when computed inline."""
    srv = getattr(runner, "srv", None)
    p = of(srv)
    if p is None or not text:
        return None
    try:
        if qv is None:
            from .voice import clean
            emb = embed or getattr(getattr(srv, "membrane", None), "embedder", None)
            if emb is None:
                return None
            qv = np.asarray(emb.embed([(clean(text) or text)[:2000]])[0], dtype=np.float32)
        named, near = candidates(srv, text, qv)
        g = p.guess(qv, lane, named=named, near=near)
    except Exception as e:
        print(f"[predict] guess failed: {e!r}", file=sys.stderr)
        return None
    pend = runner.__dict__.setdefault("_predict_pending", {})
    pend[sid] = {"guess": g, "qv": qv, "lane": lane, "ts": time.time()}
    return g


def start_async(runner, sid: str, text: str, lane: str) -> None:
    th = threading.Thread(target=start, args=(runner, sid, text, lane), daemon=True, name="predict")
    runner.__dict__.setdefault("_predict_threads", {})[sid] = th
    th.start()


def end(runner, sid: str, parts) -> dict | None:
    """The turn ended: its guess scored against what it did."""
    p = of(getattr(runner, "srv", None))
    if p is None:
        return None
    th = (runner.__dict__.get("_predict_threads") or {}).pop(sid, None)
    if th is not None:
        th.join(30)
    pend = (runner.__dict__.get("_predict_pending") or {}).pop(sid, None)
    if not pend:
        return None
    tools, ents = actions_of_parts(parts)
    res = p.score(pend["guess"], pend["qv"], pend["lane"], tools, ents, meta={"sid": sid})
    return {"guess": pend["guess"], "did": {"tools": tools, "ents": ents}, "score": res}
