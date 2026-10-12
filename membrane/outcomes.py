"""OUTCOME LEARNING -- THE SHADOW GATE (2026-10-11, phase 2; the operator:
"i dont wanna wait 3 days"; the design run by Argus, his review adopted).

For every gateable watch-loop wake the field guesses, as it arrives,
whether Argus will IGNORE it -- then his real verdict lands and the guess
is scored. SHADOW ONLY: nothing is skipped, and the guess never reaches
him (BLIND VERDICTS -- his review: a clone that sees its teacher's answer
learns to clone itself). It lives in the wake ledger and on
membrane::outcomes, where its record is earned in the open.

THE SCORER (the boring supervised version first -- his words): decayed
ignore/react counts at four levels, each backing off to the one above:
  (kind) <- (kind, class) <- (kind, entity) <- (kind, entity, what changed)
'what changed' = the state keys that moved since the previous wake on that
entity (a getter-applied that only nudged a level vs one whose status
flipped). It predicts IGNORE only when the backed-off estimate is >= P_IGN
with at least MIN_N at the deciding level.

THE BAR (his review): the expensive error is predicting ignore where he
reacted. The record keeps it per kind, with the Wilson lower bound of the
ignore-precision; a class needs that bound >= 0.95 over >= 100 predicted
ignores before anyone may even propose the switch. The switch doesn't
exist yet: the merge with the other gates comes in its own restart.

THE INVARIANTS (code, not prose): EXEMPT kinds are never predicted on,
never gateable -- the operator's messages, goals, model calls, restarts,
field-calls (readonly).

THE BACKFILL: the loop passes already in the seat's messages (4 days)
replay through the scorer at half weight, so it starts warm; their record
is kept apart ('replay'), never mixed with the live, blind one.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

ENT = "outcomes"
EXEMPT_KINDS = frozenset(("operator-message", "intent-planted", "llm-call", "restart-pending"))
EXEMPT_PREFIX = ("goal", "op-")
HALF_LIFE_S = 3 * 86400.0
PRIOR_K = 4.0
P_IGN = 0.9
MIN_N = 5.0
BACKFILL_WEIGHT = 0.5
BAR_LB = 0.95
BAR_N = 100
_CLOCK = re.compile(r"(^|_)(ts|time|at|age|updated|stamp|ago|seen|last|uptime|now)(_|$)", re.I)

_lock = threading.Lock()
_GATES: dict[int, "ShadowGate"] = {}


def exempt(ev: dict) -> bool:
    """THE INVARIANT: these never reach the gate -- not predicted, never skipped."""
    k = str((ev or {}).get("kind") or "")
    return (k in EXEMPT_KINDS or k.startswith(EXEMPT_PREFIX) or bool((ev or {}).get("readonly"))
            or bool((ev or {}).get("rewake")))


def label_of(outcome: str) -> str | None:
    """A loop verdict -> 'ignore' / 'react'; no label for what never reached a loop."""
    o = str(outcome or "")
    if o == "ignored":
        return "ignore"
    if o in ("done", "step", "blocked", "reacted"):
        return "react"
    return None


def wilson_lb(k: float, n: float, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - r) / d)


def changed_sig(prev: dict | None, cur: dict | None) -> str:
    """The non-clock state keys that moved since the last wake ('' unknown)."""
    if not isinstance(cur, dict) or not isinstance(prev, dict):
        return ""
    keys = sorted(k for k in set(prev) | set(cur) if not _CLOCK.search(str(k)) and prev.get(k) != cur.get(k))
    return ",".join(keys[:6]) or "(nothing)"


class ShadowGate:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.counts: dict[str, list] = {}       # level key -> [ignore, total, ts]
        self.last_state: dict[str, dict] = {}   # entity -> the state its last wake carried
        self.record = {"live": {}, "replay": {}}   # kind -> {pred_ign, tp, fp, labelled}
        self.seeded = False
        self._load()

    # ---- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            d = json.load(open(self.path))
            self.counts = d.get("counts") or {}
            self.last_state = d.get("last_state") or {}
            self.record = d.get("record") or self.record
            self.seeded = bool(d.get("seeded"))
        except Exception:
            pass

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with _lock:
                json.dump({"counts": self.counts, "last_state": self.last_state, "record": self.record,
                           "seeded": self.seeded}, open(tmp, "w"))
            tmp.replace(self.path)
        except Exception:
            pass

    # ---- the scorer ----------------------------------------------------------
    @staticmethod
    def keys(kind: str, cls: str | None, ent: str, sig: str) -> list[str]:
        ks = [f"k|{kind}", f"c|{kind}|{cls or '?'}", f"e|{kind}|{ent}"]
        if sig:
            ks.append(f"s|{kind}|{ent}|{sig}")
        return ks

    def _get(self, key: str, now: float) -> tuple[float, float]:
        c = self.counts.get(key)
        if not c:
            return 0.0, 0.0
        f = 0.5 ** (max(0.0, now - float(c[2])) / HALF_LIFE_S)
        return float(c[0]) * f, float(c[1]) * f

    def estimate(self, ks: list[str], now: float) -> dict:
        p, deciding, n_dec = 0.5, None, 0.0
        for key in ks:
            ign, n = self._get(key, now)
            if n <= 0:
                continue
            p = (ign + PRIOR_K * p) / (n + PRIOR_K)
            deciding, n_dec = key, n
        return {"p_ignore": round(p, 4), "level": deciding.split("|", 1)[0] if deciding else None,
                "n": round(n_dec, 2)}

    def features(self, ev: dict, cls: str | None) -> tuple[str, str, str]:
        kind = str(ev.get("kind") or "")
        ent = str(ev.get("entity") or "")
        sig = changed_sig(self.last_state.get(ent), ev.get("state")) if kind == "getter-applied" else ""
        return kind, ent, sig

    def predict(self, ev: dict, cls: str | None = None, now: float | None = None) -> dict | None:
        if exempt(ev):
            return None
        now = now or time.time()
        kind, ent, sig = self.features(ev, cls)
        ks = self.keys(kind, cls, ent, sig)
        est = self.estimate(ks, now)
        est["predict"] = "ignore" if (est["p_ignore"] >= P_IGN and est["n"] >= MIN_N) else "react"
        est.update({"kind": kind, "sig": sig, "keys": ks})
        return est

    def learn(self, pred: dict | None, outcome: str, state: dict | None = None, entity: str = "",
              now: float | None = None, weight: float = 1.0, book: str = "live") -> str | None:
        lab = label_of(outcome)
        if pred is None or lab is None:
            return None
        now = now or time.time()
        with _lock:
            for key in pred["keys"]:
                ign, n = self._get(key, now)
                self.counts[key] = [ign + (weight if lab == "ignore" else 0.0), n + weight, now]
            if entity and isinstance(state, dict):
                self.last_state[entity] = dict(list(state.items())[:8])
            r = self.record.setdefault(book, {}).setdefault(pred["kind"], {"pred_ign": 0, "tp": 0, "fp": 0,
                                                                           "labelled": 0, "ignored": 0})
            r["labelled"] += 1
            r["ignored"] += int(lab == "ignore")
            if pred["predict"] == "ignore":
                r["pred_ign"] += 1
                r["tp" if lab == "ignore" else "fp"] += 1
        return lab

    def report(self) -> dict:
        out = {}
        with _lock:                                # learn() adds kinds from other threads
            snap = {b: {k: dict(r) for k, r in ks.items()} for b, ks in self.record.items()}
        for book, kinds in snap.items():
            for kind, r in kinds.items():
                out.setdefault(book, {})[kind] = {
                    **r, "precision": round(r["tp"] / r["pred_ign"], 3) if r["pred_ign"] else None,
                    "precision_lb": round(wilson_lb(r["tp"], r["pred_ign"]), 3),
                    "coverage": round(r["pred_ign"] / r["labelled"], 3) if r["labelled"] else None,
                    "meets_bar": bool(r["pred_ign"] >= BAR_N and wilson_lb(r["tp"], r["pred_ign"]) >= BAR_LB)}
        return out

    # ---- the backfill ----------------------------------------------------------
    def replay(self, rows: list[dict], cls_of=None, weight: float = BACKFILL_WEIGHT) -> int:
        """History, oldest first: each guessed BEFORE it's learned (an honest
        replay), booked apart from the live record."""
        n = 0
        for r in sorted(rows, key=lambda x: float(x.get("ts") or 0)):
            ev = {"kind": r.get("kind"), "entity": r.get("entity"), "state": r.get("state")}
            if exempt(ev) or not ev["kind"]:
                continue
            pred = self.predict(ev, cls_of(ev["entity"]) if cls_of else None, now=float(r.get("ts") or 0) or None)
            if pred is not None and self.learn(pred, r.get("verdict") or r.get("outcome") or "",
                                               now=float(r.get("ts") or 0) or None, weight=weight,
                                               book="replay"):
                n += 1
        self.seeded = True
        return n


# ---- the seat's wiring --------------------------------------------------------
def of(srv) -> ShadowGate | None:
    if srv is None:
        return None
    g = _GATES.get(id(srv))
    if g is None:
        mp = getattr(srv, "_models_path", None)
        path = (Path(mp).parent / "voice" / "shadow_gate.json") if mp else None
        g = _GATES[id(srv)] = ShadowGate(path)
    return g


def _cls(srv, eid: str):
    est = getattr(srv, "entity_store", None)
    doc = None
    if est is not None and eid:
        doc = (est.docs or {}).get(eid) or (est.resolve(eid, exact=True) if hasattr(est, "resolve") else None)
    return getattr(doc, "cls", None) if doc is not None else None


def predict(srv, ev: dict) -> dict | None:
    g = of(srv)
    if g is None:
        return None
    return g.predict(ev, _cls(srv, str(ev.get("entity") or "")))


def learn(srv, ev: dict, pred: dict | None, outcome: str) -> str | None:
    g = of(srv)
    if g is None:
        return None
    lab = g.learn(pred, outcome, state=ev.get("state"), entity=str(ev.get("entity") or ""))
    if lab is not None:
        _maybe_paint(srv)
    return lab


_PAINT = {"ts": 0.0}


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties={
            "name": "outcomes",
            "summary": ("THE FIELD LEARNING YOUR DECISIONS (shadow): for each watch wake it guesses, "
                        "blind, whether you'll ignore it, then scores itself on your verdict. It skips "
                        "nothing. `gate` holds its record per kind: precision, its Wilson lower bound, "
                        "coverage, and whether it meets the bar (>= 0.95 over >= 100).")})
        est.docs[ent.eid] = ent
    return ent


def paint(srv) -> None:
    g, ent = of(srv), _entity(srv)
    if g is None or ent is None:
        return
    rep = g.report()
    live = rep.get("live") or {}
    bits = [f"{k}: {v['tp']}/{v['pred_ign']} ignores right (lb {v['precision_lb']}), covers "
            f"{v['coverage']}" for k, v in sorted(live.items(), key=lambda x: -x[1]["labelled"])[:4]]
    pp = ent.properties or {}
    pp["gate"] = rep
    pp["state"] = ("shadow gate (skips nothing): " + ("; ".join(bits) if bits else "no live wakes scored yet")
                   + ("" if not any(v.get("meets_bar") for v in live.values()) else " -- a kind MEETS the bar"))
    ent.properties = pp
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass
    g.save()


def _maybe_paint(srv, every_s: float = 60.0) -> None:
    if time.time() - _PAINT["ts"] >= every_s:
        _PAINT["ts"] = time.time()
        paint(srv)


def start(srv) -> None:
    """Boot: the entity; the backfill once (the seat's own loop history)."""
    g = of(srv)
    if g is None:
        return
    if not g.seeded:
        try:
            rows = backfill_rows(srv)
            if rows:
                n = g.replay(rows, cls_of=lambda e: _cls(srv, e))
                print(f"[outcomes] shadow gate seeded from {n} past loop passes", flush=True)
        except Exception as e:      # noqa: BLE001
            print(f"[outcomes] backfill skipped: {e!r}", flush=True)
    paint(srv)


_HEAD = re.compile(r"(watch event \((\S+)\) on|watch (attached|detached):|goal still open|goal planted|"
                   r"a model call|REMINDER|a seat restart)\s*'?([^']*)")


def backfill_rows(srv) -> list[dict]:
    """The loop passes in the seat's own messages: (ts, kind, entity, verdict)."""
    from .watch_agents import parse_verdict
    runner = getattr(srv, "_loop_runner", None)
    sess = getattr(getattr(runner, "sessions", None), "sessions", None) or {}
    ms = getattr(runner, "messages", None)
    if ms is None:
        return []
    rows = []
    for sid, s in list(sess.items()):
        if not str(s.get("agent") or "").startswith(("watch", "loop")):
            continue
        task = None
        for r in ms.list(sid) or []:
            i = r.get("info") or {}
            t = "\n".join(p.get("text") or "" for p in r.get("parts") or [] if p.get("type") == "text")
            if i.get("role") == "user":
                task = t
            elif i.get("role") == "assistant" and task is not None and t.strip():
                m = _HEAD.match(task.split("\n", 1)[0])
                if m:
                    kind = m.group(2) or (f"watch-{m.group(3)}" if m.group(3) else "goal")
                    rows.append({"ts": (i.get("time") or {}).get("created", 0) / 1000, "kind": kind,
                                 "entity": (m.group(4) or "").strip(), "verdict": parse_verdict(t)[0]})
                task = None
    return rows
