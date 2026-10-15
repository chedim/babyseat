"""THE FOREWARNING (2026-10-15, stage 3a -- observation only).

The gate (experiments/delta_lane, rung 14, a seat's real field): when the seat
SAYS what it is about to do and a tool result follows, the outcomes of the
results that followed the most similar past moments -- what it said + the
previous result, in OTHER sessions -- predict the result's outcome before it
exists: AUC 0.650 (intent alone 0.616, previous result 0.595, random 0.523);
flagged on 12% of steps, 44% of those failed vs 23% overall. Surprise is NOT
failure here (failures are the most EXPECTED results: they repeat), so the
expectation that matters is a forewarning: 'the last times you were here and
said this, it went like this'.

A field listener (EntityField.listeners): per session it keeps the last step
(the seat's prose / a tool result / the user) and the last result; a tool
result right after the seat's prose is a forecast MOMENT -- forecast from the
pool of earlier moments of other sessions (centered cosine, as the gate ran:
the stream's shared direction is no match), written onto the observation
event (like surprise), counted against what happened (origin stats
'forewarn': n / bad / flagged / flagged_bad -- the live calibration), and only
THEN pooled. A moment already pooled is never forecast again (a rebuild
replays events the pool holds: their forecast would read the future).

Stage 3a RECORDS only: nothing reaches the model until the live calibration
holds. The pool rebuilds from the field's history at boot (bootstrap, a
background thread; no forecasts until ready -- none rather than wrong ones).
Kill switch: MEMBRANE_FOREWARN=0.
"""
from __future__ import annotations

import os
import re
import threading

import numpy as np

K = 10                    # recalled moments per forecast
FLAG_LIFT = 0.25          # flagged: the recalled failure share this far above the pool's rate
MIN_POOL = 50             # other-session moments needed before forecasting
POOL_MAX = 20000          # the newest moments kept
BAD = ("error", "timeout", "tests-failed", "not-found", "failed")


def enabled() -> bool:
    return os.environ.get("MEMBRANE_FOREWARN", "1") not in ("0", "false", "off")


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / (float(np.linalg.norm(v)) + 1e-12)


def _kind(ev: dict) -> str | None:
    if ev.get("stream") == "observation":
        if ev.get("tool") == "tick" or ev.get("outcome") == "call-only":
            return None
        return "obs"
    if ev.get("stream") == "discourse":
        return "user" if ev.get("role") == "user" else "asst"
    return None


def _txt(ev: dict) -> str:
    """The step's text, digits ignored: a repeat (a tick, a re-run) is not a new step."""
    t = ev.get("text") or ev.get("result_head") or ""
    return re.sub(r"\d", "0", " ".join(str(t).split()))[:200]


class Forewarn:
    def __init__(self, fld, k: int = K, min_pool: int = MIN_POOL, pool_max: int = POOL_MAX):
        self.fld, self.k, self.min_pool, self.pool_max = fld, k, min_pool, pool_max
        self._lock = threading.RLock()
        self._ready = False
        self._live: dict = {}           # session -> {kind, vec, txt, obs}
        self._keys: set = set()          # pooled event keys
        self._rows: list = []            # (key, session, intent, prev, bad, text)
        self._mean = None                # the running mean of every step vector
        self._mean_n = 0
        self._mat = None                 # cached (sessions, I, P, bad) arrays

    # ---- the pool --------------------------------------------------------------------
    def _note_mean(self, x) -> None:
        self._mean_n += 1
        self._mean = x.copy() if self._mean is None else self._mean + (x - self._mean) / self._mean_n

    def _step(self, state: dict, key: str, ev: dict, x, live: bool) -> dict | None:
        """One step through a session's state; returns the forecast (live only)."""
        kind = _kind(ev)
        if kind is None:
            return None
        sess, txt = ev.get("session") or "", _txt(ev)
        st = state.setdefault(sess, {})
        if st.get("txt") == txt:
            return None                  # the same step again
        x = _unit(x)
        self._note_mean(x)
        fc = None
        if kind == "obs" and st.get("kind") == "asst" and st.get("obs") is not None \
                and key not in self._keys:
            bad = str(ev.get("outcome") or "") in BAD
            if live and self._ready:
                fc = self.forecast(sess, st["vec"], st["obs"])
            self._add(key, sess, st["vec"], st["obs"], bad, ev.get("result_head") or ev.get("text") or "")
        if kind == "obs":
            st["obs"] = x
        st.update(kind=kind, vec=x, txt=txt)
        return fc

    def _add(self, key, sess, intent, prev, bad, text) -> None:
        self._keys.add(key)
        self._rows.append((key, sess, intent, prev, bool(bad), str(text)[:200]))
        if len(self._rows) > self.pool_max:
            old = self._rows.pop(0)
            self._keys.discard(old[0])
        self._mat = None

    def _arrays(self):
        if self._mat is None and self._rows:
            self._mat = (np.asarray([r[1] for r in self._rows], dtype=object),
                         np.stack([r[2] for r in self._rows]),
                         np.stack([r[3] for r in self._rows]),
                         np.asarray([r[4] for r in self._rows]))
        return self._mat

    def forecast(self, sess: str, intent, prev) -> dict | None:
        """{bad, base, lift, n, flag, like} from the k most similar earlier
        moments of OTHER sessions; None while the pool is too small."""
        with self._lock:
            arr = self._arrays()
            if arr is None:
                return None
            S, I, P, B = arr
            other = np.where(S != sess)[0]
            if len(other) < self.min_pool:
                return None
            m = self._mean if self._mean is not None else 0.0
            c = lambda M: M - m
            un = lambda M: M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-12)
            sim = un(c(I[other])) @ un(c(_unit(intent))) + un(c(P[other])) @ un(c(_unit(prev)))
            top = other[np.argsort(-sim)[:self.k]]
            share, base = float(B[top].mean()), float(B[other].mean())
            like = [{"text": self._rows[i][5][:160], "bad": bool(B[i])} for i in top[:3]]
            return {"bad": round(share, 3), "base": round(base, 3),
                    "lift": round(share - base, 3), "n": int(len(top)),
                    "flag": bool(share - base >= FLAG_LIFT), "like": like}

    # ---- the listener ----------------------------------------------------------------
    def listener(self, key: str, ev: dict, x) -> None:
        """EntityField.listeners: cheap, never raises."""
        if not enabled():
            return
        try:
            with self._lock:
                fc = self._step(self._live, key, ev, x, live=True)
            if fc is None:
                return
            ev["forecast"] = fc
            cal = self.fld._ostats.setdefault("forewarn", {"n": 0, "bad": 0, "flagged": 0, "flagged_bad": 0})
            bad = str(ev.get("outcome") or "") in BAD
            cal["n"] += 1
            cal["bad"] += int(bad)
            cal["flagged"] += int(fc["flag"])
            cal["flagged_bad"] += int(fc["flag"] and bad)
        except Exception:
            pass

    # ---- the history -----------------------------------------------------------------
    def ready(self) -> None:
        self._ready = True

    def bootstrap(self, limit: int = 60000, batch: int = 500) -> int:
        """Pool the field's history (applied events in seq order) with a state
        of its own -- the live sessions' state is never overwritten by the past."""
        from .efield import unit
        fld, state, n = self.fld, {}, 0
        evs = sorted(fld.b.scan("events", {"origin": fld.origin, "status": "applied"}),
                     key=lambda kv: (kv[1].get("seq") or 0))[-limit:]
        for lo in range(0, len(evs), batch):
            chunk = evs[lo:lo + batch]
            got = fld.b.get_multi("sentences", list({s for _, e in chunk for s in e.get("sids") or []}))
            for key, ev in chunk:
                vs = [unit(got[s]["vec"]) for s in ev.get("sids") or [] if s in got]
                if not vs:
                    continue
                with self._lock:
                    self._step(state, key, ev, unit(np.sum(vs, axis=0)), live=False)
                n += 1
        self._ready = True
        return n

    def start(self) -> None:
        """The boot's background pass (no forecasts until the pool is ready)."""
        if not enabled():
            return
        threading.Thread(target=self._boot, name="forewarn-bootstrap", daemon=True).start()

    def _boot(self) -> None:
        try:
            self.bootstrap()
        except Exception:
            self._ready = True           # degrade: forecast from what the live stream pools
