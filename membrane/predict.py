"""MEMBRANE PREDICTS THE SEAT (2026-10-10, the operator: "can the goal of
membrane's speech generator be predicting the model's answers? maybe even
actions? and then compare what it said/did to what the model said/did ...
in that mode the LLM would not get to see membrane's mumblings ... should
membrane's actions be virtual?").

EVERY MOVE (2026-10-11, the operator: "it should predict after each LLM
action/message, not just between full moves ... treat every generated
message/tool call as a separate move"): before each of the seat's model rounds
(the chat's and the watch loops'), membrane writes down what that round will
DO -- its tools (family.action, or '(reply)' for a plain answer) and the
entities it touches, top 3 each. The round's response scores the guess at
once, and the predictor learns from the miss:

  tools     the nearest past moves (by what the seat just saw: the message,
            or the tool results he is reading) vote with their tools; what
            usually follows his previous action backs them up
  entities  candidates from three sources -- the entities the message
            names, the ones the nearest past turns touched, the live field
            states nearest the message -- each source weighted by how
            often it has been right (its hits / its proposals)

THE COVENANTS: a guess is never executed (membrane has no hands: these
are virtual actions, a ledger line and a score); it never enters the seat's
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
NONE = "(reply)"                      # (legacy: a whole round with no tool calls)
TEXT = "(text)"                       # a text message: a move of its own, its words predicted too
START = "(start)"                     # the action before a turn's first round
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
        self.kinds: dict[str, dict[str, float]] = {}
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
                self.kinds = d.get("kinds") or {}
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
                d = {"turns": self.turns, "freq": self.freq, "src": self.src, "stats": self.stats,
                     "kinds": self.kinds}
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

    def guess(self, qv, lane: str, named=(), near=(), prev: str = START) -> dict:
        """{'tools': [[name, p]...], 'ents': [[eid, score, source]...]} -- top 3."""
        nb = self._neighbours(qv, lane) if qv is not None else []
        # three distributions, mixed: the nearest past moves, what follows his
        # previous action (the move's own grammar), the lane's habits
        knn: dict[str, float] = {}
        for sim, t in nb:
            w = max(sim, 0.0) ** 2
            for name in (t.get("tools") or [NONE]):
                knn[name] = knn.get(name, 0.0) + w
        tv: dict[str, float] = {}
        for dist, wt in ((knn, 0.5), (self.freq.get(f"{lane}|{prev}") or {}, 0.35), (self.freq.get(lane) or {}, 0.15)):
            tot = sum(dist.values())
            if not tot:
                continue
            for name, c in dist.items():
                tv[name] = tv.get(name, 0.0) + wt * c / tot
        # KIND FIRST (2026-10-11, the operator: "it always predicts exec ... punish
        # it severely when it predicts a tool instead of text and vice-versa"):
        # text or a tool call is decided on its own, from the same three
        # sources plus the kind ledger the misses feed (KIND_PENALTY); the
        # actions are then ranked inside the winning kind
        kv = {"text": 0.0, "tool": 0.0}
        for name, v in tv.items():
            kv[kind_of(name)] += v
        for key, wt in ((f"{lane}|{prev}", 0.5), (lane, 0.2)):
            kd = self.kinds.get(key) or {}
            tot = sum(kd.values())
            if tot:
                for k in kv:
                    kv[k] += wt * kd.get(k, 0.0) / tot
        kz = sum(kv.values()) or 1.0
        p_text = kv["text"] / kz
        win = "text" if p_text >= 0.5 else "tool"
        inner = {n: v for n, v in tv.items() if kind_of(n) == win} or {TEXT: 1.0}
        other = {n: v for n, v in tv.items() if kind_of(n) != win}
        zi, zo = sum(inner.values()) or 1.0, sum(other.values()) or 1.0
        pw = p_text if win == "text" else 1.0 - p_text
        ranked = ([(n, round(pw * v / zi, 3)) for n, v in sorted(inner.items(), key=lambda x: -x[1])]
                  + [(n, round((1.0 - pw) * v / zo, 3)) for n, v in sorted(other.items(), key=lambda x: -x[1])])
        tools = ranked[:TOP]
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
        return {"tools": [list(t) for t in tools], "ents": ents, "kind": win, "p_text": round(p_text, 3)}

    # ---- the score -------------------------------------------------------------
    def score(self, guess: dict, qv, lane: str, tools: list[str], ents: list[str], meta=None,
              prev: str = START) -> dict:
        """The guess against what the seat did; then the predictor learns."""
        actual_t = tools or [NONE]
        gt = [t[0] for t in guess.get("tools") or []]
        ge = [e[0] for e in guess.get("ents") or []]
        actual_kind = kind_of(actual_t[0])
        guessed_kind = guess.get("kind") or (kind_of(gt[0]) if gt else "tool")
        res = {"tool1": bool(gt) and gt[0] in actual_t,
               "tool3": any(t in actual_t for t in gt),
               "kind": guessed_kind == actual_kind,
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
            for key in (lane, f"{lane}|{prev}"):
                f = self.freq.setdefault(key, {})
                for t in actual_t:
                    f[t] = f.get(t, 0.0) + 1.0
                # the kind ledger: a right kind is one vote, a WRONG kind is
                # KIND_PENALTY votes for the kind it should have been
                kd = self.kinds.setdefault(key, {"text": 0.0, "tool": 0.0})
                kd[actual_kind] = kd.get(actual_kind, 0.0) + (1.0 if res["kind"] else KIND_PENALTY)
            if qv is not None:
                self.vecs.append(_unit(qv))
                self.turns.append({"lane": lane, "tools": tools, "ents": ents[:12]})
                if len(self.vecs) > MEM_MAX:
                    self.vecs = self.vecs[-MEM_MAX:]
                    self.turns = self.turns[-MEM_MAX:]
            s = self.stats.setdefault(lane, {"turns": 0, "tool1": 0, "tool3": 0, "ent_turns": 0, "ent3": 0,
                                             "recent": []})
            s["turns"] += 1
            s["kind"] = s.get("kind", 0) + int(res["kind"])
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
                    "kind": round(s.get("kind", 0) / max(1, s["turns"]), 3),
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


PHANTOM_LOG = 400
KIND_PENALTY = 5.0                    # a text guessed as a tool call (or back): five votes for the right kind


def kind_of(action: str) -> str:
    return "text" if action in (TEXT, NONE) else "tool"


def _label(g: dict) -> tuple[str, str]:
    """(kind, label) of a guess's top move: a tool call with its entity, or text."""
    t = (g.get("tools") or [[TEXT, 0]])[0][0]
    if g.get("kind") == "text" or t in (TEXT, NONE):
        return "text", ""
    e = (g.get("ents") or [[""]])[0][0]
    return "tool", t + (f" {e}" if e else "")


def publish(runner, sid: str, item: dict) -> None:
    """A phantom: membrane's guess of one of the seat's moves, for the chat to
    draw greyed beside the real one (and kept for a reload)."""
    log = runner.__dict__.setdefault("_predict_log", {}).setdefault(sid, {})
    log[item["id"]] = {**log.get(item["id"], {}), **item}
    while len(log) > PHANTOM_LOG:
        log.pop(next(iter(log)))
    try:
        runner.hub.publish({"type": "membrane.predict", "sessionID": sid, "item": dict(log[item["id"]])})
    except Exception:
        pass


def phantoms(runner, sid: str) -> list[dict]:
    return list(((runner.__dict__.get("_predict_log") or {}).get(sid) or {}).values())


def calls_of(tool_calls) -> tuple[list[str], list[str]]:
    """A round's tool calls (the model's own shape) -> actions, entities."""
    tools, ents = [], []
    for tc in tool_calls or []:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {}
        name, es = action_of(fn.get("name") or "", args if isinstance(args, dict) else {})
        tools.append(name)
        ents.extend(es)
    return list(dict.fromkeys(tools)), list(dict.fromkeys(ents))


def context_of(history, first_text: str = "") -> str:
    """What the seat is looking at before this round: the message (first round)
    or the tool results since his last move."""
    tail = []
    for m in reversed(history or []):
        if m.get("role") == "tool":
            tail.append(str(m.get("content") or "")[:600])
        else:
            break
    return "\n".join(reversed(tail)) if tail else first_text


def step_start(runner, sid: str, text: str, lane: str, step: int = 1, mid: str = "") -> None:
    """Before a round: the guess for this move (its own thread; the round
    never waits on it). Keyed by (session, round): moves never cross."""
    p = of(getattr(runner, "srv", None))
    if p is None or not text:
        return
    turn = runner.__dict__.setdefault("_predict_turn", {}).setdefault(
        sid, {"moves": 0, "top1": 0, "top3": 0, "first": None, "prev": START, "did": []})
    prev = turn["prev"]
    key = (sid, int(step))

    def go():
        srv = getattr(runner, "srv", None)
        try:
            from .voice import clean
            emb = getattr(getattr(srv, "membrane", None), "embedder", None)
            if emb is None:
                return
            qv = np.asarray(emb.embed([(clean(text) or text)[:2000]])[0], dtype=np.float32)
            named, near = candidates(srv, text, qv)
            g = p.guess(qv, lane, named=named, near=near, prev=prev)
            kind, label = _label(g)
            said = None
            if kind == "text":
                try:
                    from . import voice as _voice
                    said = _voice.say_guess(srv, sid, qv, text)
                    label = (said or {}).get("text") or ""
                except Exception:
                    said = None
            n = (runner.__dict__.get("_predict_seq") or {}).get((sid, mid), 0)
            runner.__dict__.setdefault("_predict_pending", {})[key] = {
                "guess": g, "qv": qv, "lane": lane, "prev": prev, "ts": time.time(),
                "named": named, "near": near, "ctx": text[:600], "said": said, "n": n}
            if label:
                publish(runner, sid, {"id": f"{mid}:{n}", "mid": mid, "n": n, "kind": kind,
                                      "label": label[:300], "pending": True})
        except Exception as e:
            print(f"[predict] guess failed: {e!r}", file=sys.stderr)

    th = threading.Thread(target=go, daemon=True, name="predict")
    runner.__dict__.setdefault("_predict_threads", {})[key] = th
    th.start()


def moves_of(tool_calls, content: str = "") -> list[dict]:
    """A round's response as the seat's MOVES, in order (2026-10-11, the operator:
    "predict/learn inside the LLM's step, every text message and every tool
    call"): the text he writes (a lead-in or the answer), then each tool call."""
    out = []
    if (content or "").strip():
        out.append({"action": TEXT, "ents": [], "text": content.strip()})
    for tc in tool_calls or []:
        t, e = calls_of([tc])
        if t:
            out.append({"action": t[0], "ents": e, "text": ""})
    return out


def step_end(runner, sid: str, tool_calls, content: str = "", step: int = 1, wait: bool = False,
             mid: str = ""):
    """The round answered: every move in it is predicted from the moves before
    it (the round's first move by the guess made before the model ran; each
    next one given what came before -- never the move itself), then scored and
    learned. The moves are noted now (the next round builds on them); the
    scoring runs in a thread (or inline: wait=True)."""
    p = of(getattr(runner, "srv", None))
    if p is None:
        return None
    moves = moves_of(tool_calls, content)
    turn = (runner.__dict__.get("_predict_turn") or {}).get(sid)
    if turn is not None and moves:
        turn["prev"] = moves[-1]["action"]
        turn["did"].append([m["action"] for m in moves])
    key = (sid, int(step))
    srv = getattr(runner, "srv", None)
    seq = runner.__dict__.setdefault("_predict_seq", {})
    base = seq.get((sid, mid), 0)
    seq[(sid, mid)] = base + len(moves)                 # each move's place in its message

    def go():
        th = (runner.__dict__.get("_predict_threads") or {}).pop(key, None)
        if th is not None:
            th.join(20)
        pend = (runner.__dict__.get("_predict_pending") or {}).pop(key, None)
        if not pend or not moves:
            return None
        if pend.get("n", base) != base:                    # the message moved on: re-place the first guess
            ((runner.__dict__.get("_predict_log") or {}).get(sid) or {}).pop(f"{mid}:{pend['n']}", None)
        results = []
        prev, g = pend["prev"], pend["guess"]
        for i, mv in enumerate(moves):
            if i:                                          # the next move, given the ones before it
                g = p.guess(pend["qv"], pend["lane"], named=pend.get("named") or (),
                            near=pend.get("near") or (), prev=prev)
            meta = {"sid": sid, "move": f"{int(step)}.{i + 1}", "action": mv["action"]}
            kind, label = _label(g)
            if kind == "text":
                try:                                       # membrane's speech generator said it first
                    from . import voice as _voice
                    rec = pend.get("said") if i == 0 else _voice.say_guess(srv, sid, pend["qv"], pend.get("ctx") or "")
                    graded = _voice.grade_guess(srv, sid, rec, mv["text"]) if mv["action"] == TEXT else None
                    label = (rec or {}).get("text") or ""
                    if graded:
                        meta.update(graded)
                except Exception as e:
                    print(f"[predict] text guess failed: {e!r}", file=sys.stderr)
            res = p.score(g, pend["qv"], pend["lane"], [mv["action"]], mv["ents"], meta=meta, prev=prev)
            if "closeness" in meta:
                res["closeness"] = meta["closeness"]
            results.append(res)
            if label:
                publish(runner, sid, {"id": f"{mid}:{base + i}", "mid": mid, "n": base + i, "kind": kind,
                                      "label": label[:300], "pending": False, "hit": bool(res["tool1"]),
                                      "top3": bool(res["tool3"]), "actual": mv["action"],
                                      **({"closeness": res["closeness"]} if "closeness" in res else {})})
            if turn is not None:
                with p._lock:
                    turn["moves"] += 1
                    turn["top1"] += int(res["tool1"])
                    turn["top3"] += int(res["tool3"])
                    if "closeness" in res:
                        turn.setdefault("closeness", []).append(res["closeness"])
                    if turn["first"] is None:
                        turn["first"] = g
            prev = mv["action"]
        return results

    if wait:
        return go()
    threading.Thread(target=go, daemon=True, name="predict-score").start()
    return None


def end(runner, sid: str, parts=None) -> dict | None:
    """The turn ended: the summary of its moves' guesses (for its voice line)."""
    deadline = time.time() + 25
    while time.time() < deadline and any(k[0] == sid for k in list(runner.__dict__.get("_predict_threads") or {})):
        time.sleep(0.1)                                    # the last moves' scores land first
    for k in ("_predict_threads", "_predict_pending"):
        d = runner.__dict__.get(k) or {}
        for key in [x for x in list(d) if x[0] == sid]:
            d.pop(key, None)
    turn = (runner.__dict__.get("_predict_turn") or {}).pop(sid, None)
    if not turn or not turn["moves"]:
        return None
    did = [t for ts in turn["did"] for t in ts]
    return {"guess": turn["first"],
            "did": {"tools": list(dict.fromkeys(did)), "ents": []},
            "score": {"moves": turn["moves"], "top1": turn["top1"], "top3": turn["top3"],
                      "tool1": turn["top1"] == turn["moves"], "tool3": turn["top3"] == turn["moves"],
                      **({"text_closeness": round(sum(turn["closeness"]) / len(turn["closeness"]), 3)}
                         if turn.get("closeness") else {})}}
