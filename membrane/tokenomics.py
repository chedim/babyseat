"""The 📊 stats window's numbers: the seat's tokenomics, read-only.

GET /membrane/stats (the serve port; the page calls /ui/serve/membrane/stats)
answers `build(...)`:

  today / hour   since local midnight, and the last hour: calls, input and
                 output tokens, in total, by mind and by model. The minds:
                 `chat` (a person's foreground turns), `loops` (the Goal
                 worker: the watch / goal loops' turns, and their proxy-only
                 degrade), `cortex` (the Idle thinker: the thinker and its
                 claim judges) and `other` (the router's interview,
                 /delegate, the untagged background calls).
  claude         the Claude rung since boot (claude_agent.usage_stats, the
                 ledger /membrane/diag/claude-agent reads): answers, steps,
                 and per answer / first step / later step the cache read,
                 cache write, uncached input, output and the EFFECTIVE input
                 = 0.1 x read + 1.25 x write + uncached (what the input bills
                 as, in uncached tokens).

Where the numbers come from, and why it stays cheap on a 10 s refresh:

  * the foreground: the serve messages' info.tokens (the same ruler as the
    background: input = the whole prompt). Each session's rows are in time
    order, so the scan walks each one BACKWARD and stops at the first row
    older than the window -- it reads today's rows, never the history.
  * the background: bg-usage.json's 10-minute slots (bgusage, tallied on
    write, kept about a day).
"""
from __future__ import annotations

from . import bgusage

_KEYS = ("calls", "input", "output")
_SPLIT = ("cache_read", "cache_write", "input", "output")

# the background lanes -> the minds (bgusage's lane names)
_LANE_MIND = {"thinker": "cortex", "claim-judge": "cortex", "watch": "loops"}


def _i(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _window() -> dict:
    return {"total": dict.fromkeys(_KEYS, 0), "minds": {}, "models": {}}


def _add(w: dict, mind: str, model: str, calls: int, inp: int, out: int) -> None:
    for row in (w["total"], w["minds"].setdefault(mind, dict.fromkeys(_KEYS, 0)),
                w["models"].setdefault(model or "?", dict.fromkeys(_KEYS, 0))):
        row["calls"] += calls
        row["input"] += inp
        row["output"] += out


def _mind_of(agent) -> str:
    """A turn's agent -> its mind: the loops' agents lane as listeners."""
    from .server import _lane_of
    return "loops" if _lane_of(agent) == "listener" else "chat"


def foreground(msgs: dict, since: float, hour_since: float) -> dict:
    """The serve messages' assistant rows since `since` (today) and since
    `hour_since`: {"today": window, "hour": window}. Times in seconds."""
    today, hour = _window(), _window()
    stop_ms = min(since, hour_since) * 1000
    for rows in (msgs or {}).values():
        for i in range(len(rows or ()) - 1, -1, -1):
            info = (rows[i] or {}).get("info") or {}
            created = _i((info.get("time") or {}).get("created"))
            if created < stop_ms:
                break                       # rows are in time order: the rest is older
            if info.get("role") != "assistant":
                continue
            t = info.get("tokens") or {}
            inp, out = _i(t.get("input")), _i(t.get("output"))
            if not (inp or out):
                continue                    # a minted row: no model call
            mind, model = _mind_of(info.get("agent")), str(info.get("modelID") or "?")
            if created >= since * 1000:
                _add(today, mind, model, 1, inp, out)
            if created >= hour_since * 1000:
                _add(hour, mind, model, 1, inp, out)
    return {"today": today, "hour": hour}


def background(doc: dict | None, since: float) -> dict:
    """bg-usage.json's slots since `since`, as a window."""
    w = _window()
    for lane, models in bgusage.window(doc or {}, since).items():
        for model, row in models.items():
            _add(w, _LANE_MIND.get(lane, "other"), model,
                 _i(row.get("calls")), _i(row.get("input")), _i(row.get("output")))
    return w


def _merge(a: dict, b: dict) -> dict:
    out = _window()
    for w in (a, b):
        for split in ("minds", "models"):
            for name, row in w[split].items():
                acc = out[split].setdefault(name, dict.fromkeys(_KEYS, 0))
                for k in _KEYS:
                    acc[k] += row[k]
        for k in _KEYS:
            out["total"][k] += w["total"][k]
    return out


def effective(row: dict) -> int:
    """0.1 x cache read + 1.25 x cache write + uncached input."""
    return round(0.1 * _i(row.get("cache_read")) + 1.25 * _i(row.get("cache_write"))
                 + _i(row.get("input")))


def _split(row: dict | None) -> dict:
    out = {k: _i((row or {}).get(k)) for k in _SPLIT}
    out["effective"] = effective(out)
    return out


def claude(u: dict | None) -> dict:
    """claude_agent.usage_stats() -> the window's Claude rung section."""
    u = u or {}
    st = u.get("steps") or {}
    return {"answers": _i(u.get("calls")), "steps": _i(st.get("steps")),
            "total": _split(u), "per_answer": _split(u.get("per_answer")),
            "first_step": _split(st.get("per_first_step")),
            "later_step": _split(st.get("per_later_step"))}


def build(msgs: dict, bg_doc: dict | None, claude_usage: dict | None,
          now: float, midnight: float) -> dict:
    """The whole read: today, the last hour, the Claude rung."""
    return _assemble(foreground(msgs, midnight, now - 3600), bg_doc,
                     claude_usage, now, midnight)


def _assemble(fg: dict, bg_doc, claude_usage, now: float, midnight: float) -> dict:
    return {"now": now, "midnight": midnight,
            "today": _merge(fg["today"], background(bg_doc, midnight)),
            "hour": _merge(fg["hour"], background(bg_doc, now - 3600)),
            "claude": claude(claude_usage)}


def read(store, now: float | None = None) -> dict:
    """GET /membrane/stats: the seat's message store (server.MessageStore,
    scanned under its lock), bg-usage.json, the Claude rung's ledger."""
    import time
    from . import claude_agent as _CA
    now = time.time() if now is None else now
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    p = bgusage.path()
    bg = bgusage.load(p) if p else bgusage._mem
    msgs = getattr(store, "msgs", None) or {}
    with getattr(store, "_lock", None) or _nolock():
        fg = foreground(msgs, midnight, now - 3600)
    out = _assemble(fg, bg, _CA.usage_stats(), now, midnight)
    from . import quota as _Q
    out["paused"] = _Q.snapshot(now)     # the models out of tokens (quota.py)
    return out


def _nolock():
    import contextlib
    return contextlib.nullcontext()
