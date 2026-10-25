"""THE OUT-OF-TOKENS PAUSE (operator, 2026-10-23/24): one model at a time.

When a cloud model says its quota is spent -- the claude-agent rung's
`rate_limit` ("You've hit your session limit · resets 10:50pm
(America/New_York)"), a usage-limit or billing error, an Ollama cloud or
provider 429 / 402 -- ONLY that model pauses. Every other model keeps working
until it hits its own limit.

  model -> {since, until, probe_at, reason}

  until      the reset the error gave (an epoch: the claude CLI's rejected
             rate-limit event, else the "resets 10:50pm (Zone/City)" line,
             else a Retry-After header). At `until` the pause ends by itself.
  probe_at   no reset given: the pause lasts PROBE_S, then the NEXT REAL CALL
             goes out as the probe (nothing synthetic is ever sent). A
             success clears the pause (ok()); another quota error pauses the
             model for PROBE_S more (note()).

While a model is paused its calls are not sent at all: the proxy's backend
door (lanes._open_backend), the claude-agent rung (claude_agent.open_reply)
and the background chat (rag._model_chat) all ask paused() first and answer
QuotaPaused instead. What waits:

  * a person's chat: the TurnRunner holds the message (server.TurnRunner.held)
    and the page shows "<model> is out of tokens · it resumes at HH:MM" with
    Resume now; the reset, the probe time or Resume now sends it;
  * the Goal worker's loops: their events wait in the pool, goals stay open
    (watch_agents); the pulse wakes no goal on a paused model;
  * the Idle thinker: its calls answer "" (the beat thinks nothing).

The state lives here, in RAM and in <data dir>/quota.json (MEMBRANE_DATA_DIR;
none set -- tests, one-off tools -- RAM only), so a restart keeps a pause.
The serve port answers GET /membrane/quota (status()) and POST
/membrane/quota/resume {model} (resume()). Nothing polls the clock: each pause
arms ONE timer at its exact deadline (the reset, or the probe time); clearing
the pause (Resume now, an answer) cancels it, a new pause replaces it, and the
seat's boot re-arms the kept ones (arm_all). Listeners (subscribe) hear
(model, why): paused | reset | probe | resumed | cleared.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import weakref
from datetime import datetime, timedelta
from pathlib import Path

PROBE_S = 600            # no reset in the error: the next real call goes out
                         # 10 min after the pause (approved by the operator 2026-10-25)
FILE = "quota.json"
ERROR_TYPE = "quota_paused"    # the proxy's error body / SSE event type
REASON_MAX = 300

_lock = threading.RLock()
_state: dict = {}        # model -> entry
_loaded: str | None = None
_parked: dict = {}       # model -> {key: label}: what a background mind parked
_listeners: list = []
_timers: dict = {}       # model -> its one threading.Timer, at the pause's deadline


class QuotaPaused(RuntimeError):
    """The model is out of tokens: the call was not sent (or was refused).
    .model, .until (when it resumes: the reset, or the probe time), .reason."""

    def __init__(self, model: str, until=None, reason: str = ""):
        self.model = str(model or "")
        self.until = float(until) if until else None
        self.reason = str(reason or "")[:REASON_MAX]
        when = (f" · it resumes at {hhmm(self.until)}" if self.until else "")
        super().__init__(f"{self.model} is out of tokens{when}")

    @classmethod
    def of(cls, entry: dict) -> "QuotaPaused":
        return cls(entry.get("model", ""), entry.get("resumes_at"), entry.get("reason", ""))


def hhmm(ts) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except Exception:
        return "?"


# ---- reading the errors ----------------------------------------------------------

_QUOTA_RE = re.compile(
    r"rate[_ ]limit|usage limit|session limit|weekly limit|monthly limit|"
    r"hit your [\w -]{0,20}limit|limit reached|quota|out of (?:tokens|credits)|"
    r"billing_error|credit balance|too many requests|payment required|\b429\b",
    re.I)
_EPOCH_RE = re.compile(r"limit reached\|(\d{9,11})", re.I)
_AT_RE = re.compile(
    r"resets?\s+(?:at\s+|on\s+)?"
    r"(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?"
    r"(?:\s*\((?P<tz>[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)\))?", re.I)
_IN_RE = re.compile(r"(?:resets?|try again)\s+in\s+(?:(?P<h>\d+)\s*h\w*)?\s*(?:(?P<m>\d+)\s*m\w*)?", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def is_quota_error(text, code=None) -> bool:
    """Does this error say the model's quota (or rate limit) is spent?"""
    if code in (402, 429):
        return True
    return bool(_QUOTA_RE.search(str(text or "")))


def _zone(name):
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return None


def parse_reset(text, now: float | None = None) -> float | None:
    """The reset time an error names, as an epoch, or None. Reads "resets
    10:50pm (America/New_York)", "resets Oct 27, 10am (Europe/London)",
    "resets in 2h 30m" and the old "...limit reached|<epoch>". A wall time
    already past today is tomorrow; no zone: the seat's local time."""
    s = str(text or "")
    now = time.time() if now is None else float(now)
    m = _EPOCH_RE.search(s)
    if m:
        return float(int(m.group(1)))
    for m in _AT_RE.finditer(s):
        if not (m.group("ap") or m.group("m")):
            continue                       # "resets 5" is not a time
        h, mi = int(m.group("h")), int(m.group("m") or 0)
        ap = (m.group("ap") or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            continue
        tz = _zone(m.group("tz"))
        base = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
        tzi = base.tzinfo
        if m.group("mon"):
            mon = _MONTHS.index(m.group("mon").lower()[:3]) + 1
            try:
                dt = datetime(base.year, mon, int(m.group("day")), h, mi, tzinfo=tzi)
                if dt < base - timedelta(days=1):
                    dt = dt.replace(year=base.year + 1)
            except ValueError:
                continue
        else:
            dt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
            if dt <= base:
                dt = dt + timedelta(days=1)
        if not tz:                         # local wall time: let the OS resolve DST
            return float(time.mktime(dt.replace(tzinfo=None).timetuple()))
        return float(dt.timestamp())
    m = _IN_RE.search(s)
    if m and (m.group("h") or m.group("m")):
        return now + int(m.group("h") or 0) * 3600 + int(m.group("m") or 0) * 60
    return None


def retry_after(value, now: float | None = None) -> float | None:
    """A Retry-After header (seconds, or an HTTP date) as an epoch."""
    if value in (None, ""):
        return None
    now = time.time() if now is None else float(now)
    try:
        return now + max(0.0, float(str(value).strip()))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        return float(parsedate_to_datetime(str(value)).timestamp())
    except Exception:
        return None


# ---- the state ------------------------------------------------------------------

def path() -> Path | None:
    d = os.environ.get("MEMBRANE_DATA_DIR")
    return Path(d) / FILE if d else None


def _load() -> dict:
    """The state, (re)read when the data dir changed (or never was read)."""
    global _state, _loaded
    p = path()
    key = str(p) if p else ""
    if _loaded == key:
        return _state
    _state, _loaded = {}, key
    if p is not None:
        try:
            doc = json.loads(p.read_text())
            for m, e in (doc.get("models") or {}).items():
                if isinstance(e, dict) and m:
                    _state[str(m)] = {k: e.get(k) for k in ("since", "until", "probe_at", "reason")}
        except Exception:
            pass
    return _state


def _save() -> None:
    p = path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"models": {m: {k: e.get(k) for k in ("since", "until", "probe_at", "reason")}
                                              for m, e in _state.items()}}))
        os.replace(tmp, p)
    except Exception:
        pass


def _view(model: str, e: dict, now: float) -> dict:
    until = e.get("until")
    probe = e.get("probe_at")
    return {"model": model, "reason": e.get("reason") or "", "since": e.get("since"),
            "until": until, "probe_at": None if until else probe,
            "resumes_at": until or probe, "kind": "reset" if until else "probe",
            "probing": bool(not until and probe and now >= probe)}


def _forget() -> None:
    """Drop the RAM state, the timers and the listeners (tests; a 'restart')."""
    global _state, _loaded
    with _lock:
        for t in _timers.values():
            t.cancel()
        _timers.clear()
        _state, _loaded = {}, None
        _parked.clear()
        _listeners.clear()


def _arm(model: str, e: dict, now: float | None = None) -> None:
    """ONE timer for this pause, at its deadline (the reset, else the probe
    time); a timer it had is replaced. Call under _lock."""
    old = _timers.pop(model, None)
    if old is not None:
        old.cancel()
    at = e.get("until") or e.get("probe_at")
    if not at:
        return
    now = time.time() if now is None else float(now)
    t = threading.Timer(max(0.0, float(at) - now), _due, args=(model, float(at)))
    t.daemon = True
    t.name = f"quota-timer:{model}"[:60]
    _timers[model] = t
    t.start()


def _due(model: str, at: float) -> None:
    """A pause's deadline came: a reset clears it ('reset'); a probe time
    lets the next real call go ('probe'). A timer for a deadline since
    replaced or cleared does nothing."""
    why = None
    with _lock:
        e = _load().get(model)
        if not e:
            return
        if e.get("until") and float(e["until"]) == at:
            _clear(model)
            why = "reset"
        elif not e.get("until") and e.get("probe_at") and float(e["probe_at"]) == at:
            _timers.pop(model, None)
            why = "probe"
    if why:
        _notify(model, why)


def arm_all(now: float | None = None) -> int:
    """The boot: a timer for every pause kept in quota.json (a deadline
    already past fires at once). Returns how many."""
    with _lock:
        st = _load()
        for m, e in st.items():
            _arm(m, e, now)
        return len(st)


def subscribe(fn) -> None:
    """fn(model, why) on every change; a bound method is held weakly."""
    try:
        ref = weakref.WeakMethod(fn)
    except TypeError:
        ref = (lambda f=fn: f)
    with _lock:
        _listeners.append(ref)


def _notify(model: str, why: str) -> None:
    with _lock:
        refs = list(_listeners)
    dead = []
    for ref in refs:
        fn = ref()
        if fn is None:
            dead.append(ref)
            continue
        try:
            fn(model, why)
        except Exception:
            pass
    if dead:
        with _lock:
            for r in dead:
                if r in _listeners:
                    _listeners.remove(r)


def note(model: str, reason="", until=None, now: float | None = None) -> dict:
    """Pause `model`: until its reset (an epoch), else for PROBE_S, after
    which the next real call is the probe. A model already paused keeps its
    `since`."""
    now = time.time() if now is None else float(now)
    model = str(model or "")
    try:
        until = float(until) if until else None
    except (TypeError, ValueError):
        until = None
    if until is not None and until <= now:
        until = None                       # a reset already past: probe instead
    with _lock:
        st = _load()
        e = dict(st.get(model) or {"since": now})
        e["reason"] = str(reason or "")[:REASON_MAX]
        e["until"] = until
        e["probe_at"] = None if until else now + PROBE_S
        st[model] = e
        _save()
        _arm(model, e, now)
        v = _view(model, e, now)
    _notify(model, "paused")
    return v


def note_error(model: str, text, code=None, retry_after=None, now: float | None = None) -> dict | None:
    """Pause `model` when the error says its quota is spent (else None):
    the reset from the error's words, else the Retry-After header, else the
    probe."""
    if not model or not is_quota_error(text, code):
        return None
    now = time.time() if now is None else float(now)
    until = parse_reset(text, now)
    if until is None:
        until = globals()["retry_after"](retry_after, now)
    return note(model, text or f"HTTP {code}", until=until, now=now)


def paused(model: str, now: float | None = None) -> dict | None:
    """The pause on `model` (a view: resumes_at, reason, ...), or None: not
    paused, its reset has come (cleared), or its probe is due (the caller's
    real call is the probe)."""
    now = time.time() if now is None else float(now)
    model = str(model or "")
    with _lock:
        e = _load().get(model)
        if not e:
            return None
        until = e.get("until")
        if until and now >= until:
            _clear(model)
            reset = True
        else:
            reset = False
            if not until and now >= float(e.get("probe_at") or 0):
                return None
            return _view(model, e, now)
    if reset:
        _notify(model, "reset")
    return None


def _clear(model: str) -> None:
    t = _timers.pop(model, None)
    if t is not None and t is not threading.current_thread():
        t.cancel()
    _state.pop(model, None)
    _parked.pop(model, None)
    _save()


def ok(model: str) -> None:
    """A real call on `model` answered: its pause (a probing one) is over."""
    with _lock:
        if str(model or "") not in _load():
            return
        _clear(str(model))
    _notify(str(model), "cleared")


def resume(model: str) -> bool:
    """A person's Resume now: the pause is cleared (they may have more tokens
    now); what waited goes. False: it was not paused."""
    with _lock:
        if str(model or "") not in _load():
            return False
        _clear(str(model))
    _notify(str(model), "resumed")
    return True


def snapshot(now: float | None = None) -> list[dict]:
    """Every paused model (a reset already come is cleared)."""
    now = time.time() if now is None else float(now)
    gone = []
    with _lock:
        out = []
        for m, e in list(_load().items()):
            if e.get("until") and now >= e["until"]:
                _clear(m)
                gone.append(m)
                continue
            out.append(_view(m, e, now))
    for m in gone:
        _notify(m, "reset")
    return sorted(out, key=lambda v: (v["resumes_at"] or 0, v["model"]))


# ---- what waits -------------------------------------------------------------------

def park(model: str, key: str, label: str) -> None:
    """A background mind parked `key` (a goal's eid) on the paused model."""
    with _lock:
        _parked.setdefault(str(model), {})[str(key)] = str(label or key)


def parked(model: str) -> dict:
    with _lock:
        return dict(_parked.get(str(model)) or {})


def mind_model(srv, mind: str) -> str:
    """The model a background mind runs on: `loops` (the Goal worker: the
    loop runner's model) or `cortex` (the Idle thinker). A claude-agent rung
    the mind was not explicitly given is not its model (the #384 guard)."""
    if srv is None:
        return ""
    if mind == "loops":
        m = str(getattr(getattr(srv, "_loop_runner", None), "model", "") or "")
    else:
        m = str(getattr(srv, "idle_cortex_model", "") or "")
    if not m:
        return ""
    try:
        from . import claude_agent as _CA
        from . import minds_models as _MM
        if _CA.is_claude_rung(srv, m) and not _MM.claude_allowed(srv, mind, m):
            return ""
    except Exception:
        pass
    return m


def mind_paused(srv, mind: str, now: float | None = None) -> dict | None:
    m = mind_model(srv, mind)
    return paused(m, now) if m else None


def status(srv=None, runner=None, now: float | None = None) -> dict:
    """GET /membrane/quota: each paused model with what waits on it -- the
    minds on it (Chat when a person's message is held, the Goal worker, the
    Idle thinker), the held chats and the parked goals."""
    now = time.time() if now is None else float(now)
    out = []
    loops, cortex = mind_model(srv, "loops"), mind_model(srv, "cortex")
    for v in snapshot(now):
        m = v["model"]
        chats = []
        try:
            chats = list(runner.held(m)) if runner is not None and hasattr(runner, "held") else []
        except Exception:
            chats = []
        minds = (["Chat"] if chats else []) + (["Goal worker"] if loops == m else []) \
            + (["Idle thinker"] if cortex == m else [])
        goals = [{"eid": k, "name": n} for k, n in parked(m).items()]
        out.append(dict(v, minds=minds, chats=chats, goals=goals))
    return {"now": now, "paused": out, "probe_s": PROBE_S}


# ---- on the wire ----------------------------------------------------------------------

def error_obj(e: QuotaPaused) -> dict:
    """The proxy's answer for a paused model (HTTP 429 body / SSE event)."""
    return {"error": {"type": ERROR_TYPE, "message": str(e), "model": e.model,
                      "until": e.until, "reason": e.reason}}


def from_error_obj(obj) -> QuotaPaused | None:
    err = obj.get("error") if isinstance(obj, dict) else None
    if not isinstance(err, dict) or err.get("type") != ERROR_TYPE:
        return None
    return QuotaPaused(err.get("model") or "", err.get("until"), err.get("reason") or "")
