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

THE ACT FORECAST (2026-10-15): every tool call, before its result. Its ACT --
the tool and its arguments, digits normalized, never the result (the result
names its outcome) -- is embedded once (the vector rides the event as act_vec,
so a restart never embeds it again) and forecast as the mean of two rates: the
program's own failure rate (grep, git, curl...: the coded kind membrane already
keeps) and the failure share of the 10 most similar EARLIER acts, every session.
The gate (two seats' real fields, first-time acts, strictly causal): program
alone AUC 0.743 / 0.758; with similar earlier acts 0.774 / 0.806 -- mostly the
RECENT ones (what just happened to acts like this); learned clusters of acts
lost (0.700 / 0.600). Written as forecast_act, counted in 'forewarn_act'.
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
ACT_FLAG = 0.5            # an act forecast at this failure rate or above is flagged
PROG_PRIOR = 5.0          # a program's rate leans on the pool's rate for its first calls


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


def _act_text(ev: dict) -> str:
    """The act alone: tool + arguments, digits ignored -- never the result."""
    a = ev.get("args") or {}
    body = a.get("command") if isinstance(a.get("command"), str) else \
        __import__("json").dumps(a, sort_keys=True, default=str)
    return re.sub(r"\s+", " ", re.sub(r"\d", "0", f"{ev.get('tool')}: {body}"))[:400]


def _program(ev: dict) -> str:
    """The coded kind: the program a shell-like call runs, else the tool."""
    a = ev.get("args") or {}
    c = a.get("command")
    if not isinstance(c, str):
        return ev.get("tool") or "-"
    c = re.sub(r"^\s*(cd\s+\S+\s*(&&|;)\s*)+", "", c)
    c = re.sub(r"^\s*(\w+=\S*\s+)+", "", c)
    w = (c.split() or ["-"])[0]
    return f"{ev.get('tool')}:{w.split('/')[-1]}"


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
        self._acts: list = []            # (key, program, vec, bad, act text)
        self._act_keys: set = set()
        self._act_seen: set = set()      # act texts already pooled
        self._prog: dict = {}            # program -> [calls, bad]
        self._act_mat = None

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

    # ---- the act forecast ------------------------------------------------------------
    def _act_vec(self, ev: dict):
        from .efstore import pack_f16, unpack_f16
        if ev.get("act_vec"):
            return _unit(unpack_f16(ev["act_vec"]))
        v = _unit(self.fld.emb.embed([_act_text(ev)])[0])
        ev["act_vec"] = pack_f16(v)
        return v

    def act_forecast(self, ev: dict, vec) -> dict | None:
        """{bad, program, like_bad, first, flag, like} from the program's rate and
        the k most similar earlier acts; None while the pool is too small."""
        with self._lock:
            if len(self._acts) < self.min_pool:
                return None
            if self._act_mat is None:
                self._act_mat = (np.stack([r[2] for r in self._acts]),
                                 np.asarray([r[3] for r in self._acts]))
            M, B = self._act_mat
            prior = float(B.mean())
            n, b = self._prog.get(_program(ev), (0, 0))
            prog = (b + PROG_PRIOR * prior) / (n + PROG_PRIOR)
            top = np.argsort(-(M @ _unit(vec)))[:self.k]
            like_bad = float(B[top].mean())
            p = (prog + like_bad) / 2
            return {"bad": round(p, 3), "program": round(prog, 3), "like_bad": round(like_bad, 3),
                    "base": round(prior, 3), "first": _act_text(ev) not in self._act_seen,
                    "flag": bool(p >= ACT_FLAG),
                    "like": [{"act": self._acts[i][4][:120], "bad": bool(B[i])} for i in top[:3]]}

    def _act_add(self, key: str, ev: dict, vec) -> None:
        bad = str(ev.get("outcome") or "") in BAD
        prog = _program(ev)
        self._act_keys.add(key)
        self._act_seen.add(_act_text(ev))
        self._acts.append((key, prog, _unit(vec), bad, _act_text(ev)))
        c = self._prog.setdefault(prog, [0, 0])
        c[0] += 1
        c[1] += int(bad)
        if len(self._acts) > self.pool_max:
            old = self._acts.pop(0)
            self._act_keys.discard(old[0])
            oc = self._prog.get(old[1])
            if oc:
                oc[0] -= 1
                oc[1] -= int(old[3])
        self._act_mat = None

    def _act_step(self, key: str, ev: dict, live: bool) -> dict | None:
        """A tool call: forecast it (live, ready) BEFORE its outcome joins the pool."""
        if _kind(ev) != "obs" or key in self._act_keys:
            return None
        vec = self._act_vec(ev)
        fa = self.act_forecast(ev, vec) if live and self._ready else None
        with self._lock:
            self._act_add(key, ev, vec)
        return fa

    # ---- the listener ----------------------------------------------------------------
    def prepare(self, ev: dict) -> None:
        """EntityField.step_prepare: the act's embedding, made before the field lock is
        taken, so the listener (called under it) never waits on the embedder."""
        if enabled() and _kind(ev) == "obs":
            self._act_vec(ev)

    def listener(self, key: str, ev: dict, x) -> None:
        """EntityField.listeners: cheap, never raises."""
        if not enabled():
            return
        try:
            fa = self._act_step(key, ev, live=True)
            if fa is not None:
                ev["forecast_act"] = fa
                cal = self.fld._ostats.setdefault(
                    "forewarn_act", {"n": 0, "bad": 0, "flagged": 0, "flagged_bad": 0,
                                     "first": 0, "first_bad": 0})
                bad = str(ev.get("outcome") or "") in BAD
                cal["n"] += 1
                cal["bad"] += int(bad)
                cal["flagged"] += int(fa["flag"])
                cal["flagged_bad"] += int(fa["flag"] and bad)
                cal["first"] += int(fa["first"])
                cal["first_bad"] += int(fa["first"] and bad)
        except Exception:
            pass
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
            need = [(k2, e2) for k2, e2 in chunk if _kind(e2) == "obs" and not e2.get("act_vec")
                    and k2 not in self._act_keys]
            if need:
                from .efstore import pack_f16
                for lo2 in range(0, len(need), 64):
                    part = need[lo2:lo2 + 64]
                    for (k2, e2), v in zip(part, self.fld.emb.embed([_act_text(e2) for _, e2 in part])):
                        e2["act_vec"] = pack_f16(_unit(v))
                        try:
                            fld.b.upsert("events", k2, e2)     # embedded once, kept
                        except Exception:
                            pass
            for key, ev in chunk:
                try:
                    self._act_step(key, ev, live=False)
                except Exception:
                    pass
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
            from .cblite import background
            with background():           # #541: a person's turn reads first
                self.bootstrap()
        except Exception:
            self._ready = True           # degrade: forecast from what the live stream pools
