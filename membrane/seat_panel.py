"""THE SEAT AS A PANEL (2026-10-11, the operator: "we have each seat as an
entity, does it expose the seat's state/controls/events?" -- it held an
uptime and a beat count, nothing else). The seat's presence entity
(membrane::presence, the one the roster already reads) now carries:

  state     refreshed every REFRESH_S: paused, the loops (running / cap),
            auto-chat and membrane's role per session, the active models,
            the seat's memory and the phone's free memory, live stream
            clients, failing scripts
  events    the last EVENTS_KEEP things that happened to the seat -- a boot,
            a pause/resume, auto-chat on/off, an empty continuation (the
            stall), memory pressure, a stream client cut off, a refused
            action -- each also published on the hub (membrane.seat) and
            readable by watches like any entity's change
  controls  applied by the seat itself, operator-only (POST
            /membrane/seat/control, an HA admin through the relay):
            paused (the one switch every acting lane honours), loops_max,
            and auto-chat / role per session. Argus READS all of it; an
            edit of these properties by a lane is not a control (the
            refresh writes the truth back) -- the stable point stays the
            operator's call, and he can never lift his own limits.
"""
from __future__ import annotations

import os
import threading
import time

ENT = "presence"
REFRESH_S = 15.0
EVENTS_KEEP = 20
MEM_LOW_MB = 350             # the phone's MemAvailable under this: pressure
RSS_HIGH_MB = 2300           # the seat itself over this: pressure
CONTROLS = ("paused", "loops_max", "auto", "role")


def _est(srv):
    return getattr(srv, "entity_store", None)


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_SEAT, QuantumEntity
    est = _est(srv)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_SEAT, properties={"seat": getattr(est, "origin", "")})
        est.docs[ent.eid] = ent
    return ent


def _mem() -> tuple[int | None, int | None]:
    """(the seat's RSS MB, the machine's available MB)."""
    rss = avail = None
    try:
        for ln in open("/proc/self/status"):
            if ln.startswith("VmRSS:"):
                rss = int(ln.split()[1]) // 1024
    except Exception:
        try:
            import resource, sys
            r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss = r // (1024 * 1024) if sys.platform == "darwin" else r // 1024
        except Exception:
            pass
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                avail = int(ln.split()[1]) // 1024
    except Exception:
        pass
    return rss, avail


# THE BATTERY (2026-10-11: the phone died overnight -- the seat drew more than
# agdroid's hub port gave): read through Termux:API, once a minute
BATTERY_EVERY_S = 60.0
BATTERY_LOW = 20
BATTERY_CRITICAL = 8
BATTERY_RESUME = 15
_bat = {"ts": 0.0, "val": None}


def battery(force: bool = False) -> dict | None:
    """{percentage, status, plugged, temperature} or None (no Termux:API)."""
    now = time.time()
    if not force and now - _bat["ts"] < BATTERY_EVERY_S:
        return _bat["val"]
    _bat["ts"] = now
    try:
        import json as _json
        import subprocess
        p = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=12)
        d = _json.loads(p.stdout or "{}")
        _bat["val"] = {k: d.get(k) for k in ("percentage", "status", "plugged", "temperature", "health")}
    except Exception:
        _bat["val"] = None
    return _bat["val"]


def _charging(b: dict) -> bool:
    return str(b.get("status") or "").upper() in ("CHARGING", "FULL")


def _battery_guard(srv, b, pp: dict) -> None:
    """Low: an event. Critical and not charging: the seat pauses itself (its
    acting lanes drain the battery); charging back past BATTERY_RESUME: resumed
    -- only a pause the battery made, never the operator's."""
    if not b or b.get("percentage") is None:
        return
    pct, chg = int(b["percentage"]), _charging(b)
    if pct < BATTERY_LOW and not chg and not pp.get("_bat_low"):
        pp["_bat_low"] = True
        note(srv, "battery-low", percentage=pct, status=b.get("status"))
    elif (pct >= BATTERY_LOW or chg) and pp.get("_bat_low"):
        pp["_bat_low"] = False
    if pct < BATTERY_CRITICAL and not chg and not getattr(srv, "paused", False):
        srv.paused = True
        pp["_bat_paused"] = True
        note(srv, "paused", by="battery", percentage=pct)
    elif pp.get("_bat_paused") and chg and pct >= BATTERY_RESUME:
        srv.paused = False
        pp["_bat_paused"] = False
        note(srv, "resumed", by="battery", percentage=pct)


def _runner(srv):
    return getattr(srv, "_loop_runner", None)


def state(srv) -> dict:
    """The seat now, in one dict."""
    from .lanes import nerve_paused
    from . import voice as V
    r = _runner(srv)
    loops = getattr(srv, "loops", None)
    rss, avail = _mem()
    auto = {}
    if r is not None:
        for sid in set((getattr(r, "_auto", None) or {})) | set((getattr(r, "_voice_role", None) or {})):
            s = V.auto_state(r, sid)
            title = str(((r.sessions.get(sid) or {}).get("title")) or sid)[:40] if getattr(r, "sessions", None) else sid
            auto[sid] = {"title": title, "on": s["on"], "mode": s["mode"], "role": s["role"], "rounds": s["rounds"]}
    failing = 0
    try:
        est = _est(srv)
        failing = sum(1 for d in list((est.docs or {}).values())
                      if (d.properties or {}).get("script_failures") or (d.properties or {}).get("script_disabled"))
    except Exception:
        pass
    hub = getattr(r, "hub", None) if r is not None else None
    return {
        "paused": bool(nerve_paused(srv)),
        "loops": {"running": loops.running() if loops is not None else 0,
                  "max": getattr(loops, "max", None)},
        "auto": auto,
        "models": dict(getattr(srv, "_active_models", None) or {}),
        "loops_model": str(getattr(r, "model", "") or "") if r is not None else "",
        "cortex_model": str(getattr(srv, "idle_cortex_model", "") or ""),
        "seat_rss_mb": rss,
        "mem_available_mb": avail,
        "stream_clients": len(getattr(hub, "_subs", []) or []) if hub is not None else 0,
        "failing_scripts": failing,
        "battery": battery(),
        "ts": time.time(),
    }


def note(srv, kind: str, **detail) -> None:
    """An event of the seat's: kept on its entity, published on the hub."""
    ent = _entity(srv)
    if ent is None:
        return
    ev = {"ts": time.time(), "kind": kind, **{k: v for k, v in detail.items() if v is not None}}
    pp = ent.properties or {}
    pp["events"] = (list(pp.get("events") or []) + [ev])[-EVENTS_KEEP:]
    pp["last_event"] = ev
    ent.properties = pp
    r = _runner(srv)
    try:
        if r is not None:
            r.hub.publish({"type": "membrane.seat", "event": ev})
    except Exception:
        pass


def refresh(srv) -> dict:
    """The state onto the entity (and the pressure edges noted once each)."""
    ent = _entity(srv)
    if ent is None:
        return {}
    st = state(srv)
    pp = ent.properties or {}
    prev = pp.get("state") or {}
    pp["state"] = st
    ent.properties = pp
    pressure = ((st.get("mem_available_mb") or 10 ** 6) < MEM_LOW_MB) or ((st.get("seat_rss_mb") or 0) > RSS_HIGH_MB)
    was = bool(pp.get("_pressure"))
    if pressure and not was:
        note(srv, "memory-pressure", seat_rss_mb=st.get("seat_rss_mb"), mem_available_mb=st.get("mem_available_mb"))
    elif was and not pressure:
        note(srv, "memory-ok", seat_rss_mb=st.get("seat_rss_mb"), mem_available_mb=st.get("mem_available_mb"))
    pp["_pressure"] = pressure
    _battery_guard(srv, st.get("battery"), pp)
    try:                                       # restarts.py: the heartbeat, a deploy's ask, a staged restart
        from . import restarts as _RS
        if not _RS.booted():
            raise RuntimeError("restarts not booted yet")
        _RS.heartbeat(srv)
        _RS.check_ask_file(srv)
        _RS.check_pending(srv)
    except Exception:
        pass
    st["paused"] = bool(getattr(srv, "paused", False)) or st["paused"]
    if prev.get("paused") != st["paused"] and prev and not pp.get("_bat_paused"):
        note(srv, "resumed" if not st["paused"] else "paused", by="decree")
    try:
        _est(srv).upsert(ent, embedder=None)
    except Exception:
        pass
    return st


def control(srv, body: dict, by: str = "operator") -> dict:
    """The operator's switches, applied by the seat (never by an edit)."""
    from . import voice as V
    r = _runner(srv)
    done = {}
    if "paused" in body:
        srv.paused = bool(body["paused"])
        done["paused"] = srv.paused
        note(srv, "paused" if srv.paused else "resumed", by=by)
        try:                                       # the refresh's diff must not note it again "by decree"
            _st = (_entity(srv).properties or {}).get("state")
            if isinstance(_st, dict):
                _st["paused"] = srv.paused
        except Exception:
            pass
    if "loops_max" in body and getattr(srv, "loops", None) is not None:
        try:
            n = max(1, min(8, int(body["loops_max"])))
            srv.loops.max = n
            done["loops_max"] = n
            note(srv, "loops-cap", value=n, by=by)
        except (TypeError, ValueError):
            pass
    sid = str(body.get("session") or "")
    if sid and r is not None and r.sessions.get(sid) is not None:
        if body.get("role"):
            done["role"] = V.role_set(r, sid, str(body["role"]))
        if "auto" in body:
            done["auto"] = V.auto_set(r, sid, bool(body["auto"]), str(body.get("mode") or "explore"))["on"]
    refresh(srv)
    return {"ok": True, "applied": done, "state": (_entity(srv).properties or {}).get("state")}


def start(srv) -> None:
    """Boot: note the boot, refresh every REFRESH_S."""
    note(srv, "boot", pid=os.getpid())

    def loop():
        while True:
            try:
                refresh(srv)
            except Exception:
                pass
            time.sleep(REFRESH_S)

    threading.Thread(target=loop, daemon=True, name="seat-panel").start()


def note_from(runner, kind: str, **detail) -> None:
    """For the code that holds the runner, not the seat."""
    srv = getattr(runner, "srv", None)
    if srv is not None:
        note(srv, kind, **detail)
