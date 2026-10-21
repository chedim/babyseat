"""RESTARTS, TOLD TO ARGUS (2026-10-11, the operator: "announce HA and seat
restarts to Argus going forward ... thru its ollama proxy endpoint").

A restart used to be silent: the loops resumed, a watch re-fired, and Argus
read the gap as the world going quiet. Now each one reaches him as a short
signed brief through the seat's own /v1 proxy -- the same door the
engineer's briefs use, so it passes through the field like any message.
A brief is words only (a /v1 reply can't call tools): he hears it, it
changes nothing by itself.

  the seat   at boot: when, how long it was down (the last heartbeat the
             panel left), and why -- a deploy writes RESTART_REASON before
             it kills the serve; no reason = unexpected (a crash, the
             memory killer, the phone)
  HA         the integration posts {kind: "ha-start"} to the seat when
             Home Assistant has started, and {kind: "ha-stop"} when it is
             going down (POST /membrane/seat/event)

A PLANNED RESTART ASKS FIRST (the operator: "and let them delay it if
they're busy or need some time to shut down stuff gracefully"): the
heads-up names what restarts and why; his reply DELAY <minutes> (up to
MAX_DELAY_MIN) or GO decides, and a turn or loop still running holds it
until idle (the same cap). A deploy asks through ASK_FILE (seat-install
writes it, the panel's refresh answers in ANSWER_FILE); an HA restart
asks over POST /membrane/seat/restart-ask.

Every notice is also a seat event (membrane::presence).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

from . import jwtauth as _JWT

SIGN = "[membrane seat notice]"
ALIVE_FILE = "alive.ts"
REASON_FILE = "restart-reason"
WAIT_PROXY_S = 240.0
POST_TIMEOUT_S = 600.0
HA_DEDUP_S = 120.0
MAX_DELAY_MIN = 15
ASK_FILE = "restart-ask.json"
ANSWER_FILE = "restart-answer.json"
IDLE_POLL_S = 10.0
_last_ha = {"ha-start": 0.0, "ha-stop": 0.0}


def _dir(srv) -> str:
    # the seat's own data dir (the field's folder, opencode.py exports it); the old
    # fallback wrote restart files to ~/membrane/.membrane wherever the seat ran
    d = (getattr(srv, "data_dir", None) or os.environ.get("MEMBRANE_DATA_DIR")
         or os.path.expanduser("~/membrane/.membrane"))
    return str(d)


def heartbeat(srv) -> None:
    """The panel's refresh: the seat was alive now (the next boot reads it)."""
    try:
        with open(os.path.join(_dir(srv), ALIVE_FILE), "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def boot_text(srv, now: float | None = None) -> str:
    """The seat's own restart, in two or three sentences (and the reason consumed)."""
    now = now or time.time()
    d = _dir(srv)
    alive = _read(os.path.join(d, ALIVE_FILE))
    reason = _read(os.path.join(d, REASON_FILE))
    try:
        os.remove(os.path.join(d, REASON_FILE))
    except Exception:
        pass
    down = ""
    try:
        gap = now - float(alive)
        if 0 < gap < 7 * 86400:
            down = f", down about {int(gap // 60)} min {int(gap % 60)} s" if gap >= 60 else f", down about {int(gap)} s"
    except (TypeError, ValueError):
        pass
    why = (f"Why: {reason}." if reason else
           "Why: not announced -- no deploy asked for it (a crash, the memory killer, or the phone itself).")
    return (f"{SIGN} The seat (membrane serve) restarted at {_clock(now)}{down}. {why} "
            "Your loops, watches, goals and the field are back; anything you were doing mid-turn was cut off.")


def ha_text(now: float | None = None, detail: str = "") -> str:
    now = now or time.time()
    return (f"{SIGN} Home Assistant restarted -- it was back up at {_clock(now)}{(' (' + detail + ')') if detail else ''}. "
            "Its entities, automations and the chat relay reloaded; states read in the first minute may be "
            "unavailable or stale.")


def ha_stop_text(detail: str = "") -> str:
    return (f"{SIGN} Home Assistant is going down{(' (' + detail + ')') if detail else ''} -- maintenance or a "
            "restart. Its entities, services and the chat relay go quiet; a fresh token lands when it is "
            "back, and I will say so then.")


def _proxy_url(srv) -> str | None:
    try:
        return f"http://127.0.0.1:{int(srv.server_address[1])}/v1/chat/completions"
    except Exception:
        return None


def _model(srv) -> str:
    r = getattr(srv, "_loop_runner", None)
    return str(getattr(srv, "default_model", "") or getattr(r, "model", "") or "")


def post(srv, text: str, wait_s: float = WAIT_PROXY_S) -> bool:
    return reply_to(srv, text, wait_s) is not None


def reply_to(srv, text: str, wait_s: float = WAIT_PROXY_S) -> str | None:
    """The brief through the seat's own /v1 proxy (retrying until it
    answers): his reply's text, or None (never reached)."""
    url = _proxy_url(srv)
    if not url:
        return None
    body = {"stream": False, "messages": [{"role": "user", "content": text}]}
    m = _model(srv)
    if m:
        body["model"] = m
    deadline = time.time() + wait_s
    while True:
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", **_JWT.internal_headers()})
            out = json.loads(urllib.request.urlopen(req, timeout=POST_TIMEOUT_S).read() or b"{}")
            return str((((out.get("choices") or [{}])[0].get("message")) or {}).get("content") or "")
        except Exception:
            if time.time() > deadline:
                return None
            time.sleep(10)


def _send(srv, kind: str, text: str) -> None:
    def go():
        ok = post(srv, text)
        try:
            from . import seat_panel as SP
            SP.note(srv, "told-argus", what=kind, ok=ok)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True, name=f"notice-{kind}").start()


def on_boot(srv) -> str:
    text = boot_text(srv)
    _SEAT["booted"] = True                      # only now may the panel's beat touch alive.ts / the pending file
    _send(srv, "seat-restart", text)
    return text


def booted() -> bool:
    """THE BOOT ORDER (2026-10-11 sweep): the seat panel's first beat ran
    before bind/on_boot -- its heartbeat overwrote alive.ts (the notice said
    'down 0 s') and check_pending queued a restart for code just loaded."""
    return bool(_SEAT.get("booted"))


def on_event(srv, body: dict) -> dict:
    """POST /membrane/seat/event (the HA integration): {kind: 'ha-start' | 'ha-stop', detail?}."""
    kind = str((body or {}).get("kind") or "")
    if kind not in ("ha-start", "ha-stop"):
        return {"ok": False, "error": f"unknown kind {kind!r} (ha-start, ha-stop)"}
    now = time.time()
    if now - _last_ha.get(kind, 0.0) < HA_DEDUP_S:
        return {"ok": True, "dedup": True}
    _last_ha[kind] = now
    try:
        from . import seat_panel as SP
        SP.note(srv, "ha-restart" if kind == "ha-start" else "ha-stop")
    except Exception:
        pass
    detail = str((body or {}).get("detail") or "")[:80]
    _send(srv, "ha-restart" if kind == "ha-start" else "ha-stop",
          ha_text(now, detail) if kind == "ha-start" else ha_stop_text(detail))
    return {"ok": True}


# ---- a planned restart asks first -------------------------------------------
def ask_text(what: str, reason: str, in_s: int = 60) -> str:
    return (f"{SIGN} Planned restart of {what} in about {int(in_s)} s. Why: {reason or 'maintenance'}. "
            f"If you are in the middle of something or need time to wrap up gracefully, reply "
            f"DELAY <minutes> (up to {MAX_DELAY_MIN}) and what you are finishing; otherwise reply GO.")


def parse_delay(reply: str | None) -> int:
    """Minutes asked for (0 = go, also when nothing came back)."""
    import re
    m = re.search(r"\bDELAY\b\D{0,12}(\d{1,3})", str(reply or ""), re.I)
    if not m:
        return 0
    return max(0, min(MAX_DELAY_MIN, int(m.group(1))))


def busy(srv) -> list[str]:
    """What is still running: a chat turn mid-flight, a loop."""
    out = []
    r = getattr(srv, "_loop_runner", None)
    try:
        st = r.status() if r is not None and hasattr(r, "status") else {}
        out += [f"turn {sid}" for sid, v in (st or {}).items() if (v or {}).get("type") == "busy"]
    except Exception:
        pass
    try:
        n = srv.loops.running()
        if n:
            out.append(f"{n} loop(s)")
    except Exception:
        pass
    return out


def ask(srv, what: str, reason: str, in_s: int = 60, wait_idle: bool = True) -> dict:
    """The heads-up, his answer, the hold: {delay_s, asked_min, reply, busy}.
    The caller restarts after delay_s."""
    reply = reply_to(srv, ask_text(what, reason, in_s))
    mins = parse_delay(reply)
    t0 = time.time()
    cap = t0 + MAX_DELAY_MIN * 60
    if mins:
        time.sleep(min(mins * 60, cap - time.time()))
    held = []
    if wait_idle:
        while time.time() < cap:
            b = busy(srv)
            if not b:
                break
            held = b
            time.sleep(IDLE_POLL_S)
    out = {"delay_s": round(time.time() - t0, 1), "asked_min": mins, "reply": (reply or "")[:400],
           "held_for": held, "reached": reply is not None}
    try:
        from . import seat_panel as SP
        SP.note(srv, "restart-asked", what=what, asked_min=mins, delay_s=out["delay_s"])
    except Exception:
        pass
    return out


_asking = {"on": False}


def check_ask_file(srv) -> None:
    """The panel's refresh: a deploy's ask (ASK_FILE) is answered in ANSWER_FILE."""
    d = _dir(srv)
    p = os.path.join(d, ASK_FILE)
    if _asking["on"] or not os.path.exists(p):
        return
    try:
        req = json.loads(_read(p) or "{}")
        os.remove(p)
    except Exception:
        return
    _asking["on"] = True

    def go():
        try:
            res = ask(srv, str(req.get("what") or "the seat"), str(req.get("reason") or ""),
                      int(req.get("in_s") or 60))
        except Exception as e:                  # noqa: BLE001
            res = {"delay_s": 0, "error": repr(e)}
        try:
            with open(os.path.join(d, ANSWER_FILE), "w") as f:
                json.dump({**res, "id": req.get("id")}, f)
        finally:
            _asking["on"] = False
    threading.Thread(target=go, daemon=True, name="restart-ask").start()


# ---- Argus restarts the seat himself ------------------------------------------
# (2026-10-11, the operator: "or better yet -- have Argus do those restarts
# instead of you? ... if he's 1 minute late to restart then you should
# consult me"). A deploy only STAGES: seat-install writes PENDING_FILE and
# leaves the serve running. The seat turns it into the entity
# membrane::restart and a restart-pending event -- a loop turn with real
# hands -- and he restarts when nothing of his is mid-flight:
# exec(action='call', object='presence', method='restart', args={why}).
# Due DUE_S after staging; a minute past due the seat notes restart-overdue
# (the engineer then asks the operator -- nothing restarts on its own).
PENDING_FILE = "restart-pending.json"
DUE_S = 1800.0
REMIND_BEFORE_S = 300.0
OVERDUE_GRACE_S = 60.0
ENT = "restart"
METHOD = "restart"
COMMAND = "seat::restart {{why}}"


def _pending(srv) -> dict | None:
    try:
        p = os.path.join(_dir(srv), PENDING_FILE)
        d = json.loads(_read(p) or "null")
        if isinstance(d, dict) and not d.get("staged_at"):
            d["staged_at"] = os.path.getmtime(p)   # a stable stamp: 'now' re-queued the event every beat
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties={
            "name": "restart",
            "summary": ("A PENDING SEAT RESTART (new code staged by the engineer): restart the seat "
                        "yourself when nothing of yours is mid-flight -- exec(action='call', "
                        "object='presence', method='restart', args={why: '<one line>'}) -- before `due`.")})
        est.docs[ent.eid] = ent
    return ent


_SEAT = {"srv": None}


def _seat_srv():
    return _SEAT["srv"]


def bind(srv) -> None:
    """Boot: presence implements `restart`; a pending restart is now loaded (cleared)."""
    _SEAT["srv"] = srv
    try:
        from . import seat_panel as SP
        from .night import method_define
        est = getattr(srv, "entity_store", None)
        pres = SP._entity(srv)
        if est is not None and pres is not None:
            def _of(d):
                mo = (d.properties or {}).get("method_of")
                return pres.eid in (mo if isinstance(mo, list) else [mo])
            if not any(_of(d) and (d.properties or {}).get("method_name") == METHOD
                       for d in list((est.docs or {}).values())):
                method_define(srv, est, None, None, on=pres.eid, method=METHOD, command=COMMAND)
    except Exception as e:                      # noqa: BLE001
        import sys
        print(f"[restarts] presence.restart not wired ({e!r})", file=sys.stderr)
    p = os.path.join(_dir(srv), PENDING_FILE)
    if os.path.exists(p):                       # any restart loads what was staged
        try:
            os.remove(p)
        except Exception:
            pass
    ent = _entity(srv)
    if ent is not None:
        pp = ent.properties or {}
        pp.update({"pending": False, "state": "nothing pending"})
        ent.properties = pp


def check_pending(srv, now: float | None = None) -> dict | None:
    """The panel's refresh: a staged restart -> the entity, the event (once,
    and a reminder REMIND_BEFORE_S before due), the overdue note."""
    now = now or time.time()
    pend = _pending(srv)
    ent = _entity(srv)
    if ent is None:
        return None
    pp = ent.properties or {}
    if pend is None:
        if pp.get("pending"):
            pp.update({"pending": False, "state": "nothing pending"})
            ent.properties = pp
        return None
    staged = float(pend.get("staged_at") or now)
    due = staged + DUE_S
    pp.update({"pending": True, "why": str(pend.get("why") or ""), "files": list(pend.get("files") or []),
               "staged_at": staged, "due": due,
               "state": f"restart pending since {_clock(staged)}, due {_clock(due)}: {pend.get('why') or 'new code'}"})
    pool = getattr(srv, "events_pool", None)
    ev = {"kind": "restart-pending", "entity": ent.eid, "why": pp["why"], "files": pp["files"],
          "due": due, "ts": now}
    if pp.get("_told") != staged:
        pp["_told"] = staged
        if pool is not None:
            pool.put(ev)
    elif now >= due - REMIND_BEFORE_S and pp.get("_reminded") != staged:
        pp["_reminded"] = staged
        if pool is not None:
            pool.put({**ev, "reminder": True})
    if now >= due + OVERDUE_GRACE_S and pp.get("_overdue") != staged:
        pp["_overdue"] = staged
        try:
            from . import seat_panel as SP
            SP.note(srv, "restart-overdue", why=pp["why"], staged_at=staged)
        except Exception:
            pass
    ent.properties = pp
    return pp


# #151: a restart the seat ASKED for -- main()'s teardown ends in a re-exec
# instead of an exit (a foreground seat has no supervisor to respawn it)
RESTART = {"requested": False}

# #243: the re-exec'd boot's word that it IS a restart (no new browser tab)
REEXEC_ENV = "MEMBRANE_REEXEC"
# #395: the person's pause, handed to the re-exec'd boot (the environment, never disk)
PAUSE_ENV = "MEMBRANE_REEXEC_PAUSED"
_BOOT: dict = {}                           # this boot's reads of the markers


def was_reexec() -> bool:
    """True once on the boot a requested restart re-exec'd; the marker is
    cleared on read so nothing launched later inherits it."""
    got = os.environ.pop(REEXEC_ENV, None) == "1"
    _BOOT["reexec"] = _BOOT.get("reexec", False) or got
    return got


def carried_pause() -> bool:
    """#395: True when the restart that re-exec'd this boot left the person's
    pause on. Only a re-exec carries it: on a cold start a stray marker is
    ignored (a persisted pause = a stuck seat). The marker is cleared on read,
    so no child process inherits it."""
    val = os.environ.pop(PAUSE_ENV, None)
    reexec = os.environ.get(REEXEC_ENV) == "1" or bool(_BOOT.get("reexec"))
    return val == "1" and reexec


def apply_carried_pause(srv) -> bool:
    """#395: the seat's boot takes the pause the restart carried -- the same
    setter as the person's own press, its audit line by "restart"."""
    if not carried_pause():
        return False
    from .lanes import nerve_set
    nerve_set(srv, True, by="restart")
    print("[seat] paused: the restart kept the pause", file=sys.stderr)
    return True


def restart_line() -> str | None:
    """The log's word that this stop is a restart -- the teardown prints it
    while the log file is still open (at exec time stderr is closed)."""
    return "[seat] restarting (re-exec)" if RESTART["requested"] else None


def reexec_if_requested(srv=None) -> None:
    """The last line of the seat's teardown: a requested restart re-execs the
    same command (same pid, same terminal -- ctrl+c still stops it; a
    supervisor waiting on the process just sees it continue). The person's
    pause rides along in the environment (#395); the battery's does not --
    the new boot's battery guard re-derives it."""
    if not RESTART["requested"]:
        return
    os.environ[REEXEC_ENV] = "1"           # the next boot skips the browser (#243)
    carry = False
    try:
        from .lanes import person_paused
        carry = srv is not None and person_paused(srv)
    except Exception:
        pass
    if carry:
        os.environ[PAUSE_ENV] = "1"
    else:
        os.environ.pop(PAUSE_ENV, None)
    os.execv(sys.executable,
             [sys.executable, "-m", "membrane.opencode"] + sys.argv[1:])


def do_restart(srv, why: str, delay_s: float = 2.0) -> dict:
    """presence.restart: the reason for the next boot, then a clean SIGTERM
    (the drain runs as on any stop; the teardown's end re-execs, #151)."""
    import signal
    RESTART["requested"] = True
    pend = _pending(srv) or {}
    files = ", ".join(pend.get("files") or [])
    why = " ".join(str(why or "").split())[:200] or "no reason given"
    try:
        with open(os.path.join(_dir(srv), REASON_FILE), "w") as f:
            f.write(f"Argus restarted it ({why})" + (f"; it loads the staged {files}" if files else ""))
    except Exception:
        pass
    try:
        from . import seat_panel as SP
        SP.note(srv, "restart", by="argus", why=why)
    except Exception:
        pass

    def bye():
        time.sleep(delay_s)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=bye, daemon=True, name="restart").start()
    text = f"restarting in {delay_s:g}s -- you'll get a notice when the seat is back"
    try:
        from .lanes import person_paused
        kept = person_paused(srv)
    except Exception:
        kept = False
    if kept:                                    # #395: the person's pause rides the re-exec
        text += " (the seat is paused now; the pause is kept -- it comes back paused)"
    elif getattr(srv, "paused", False):         # the battery's pause re-derives on the new boot
        text += (" (the battery paused the seat; that pause is not carried -- "
                 "the new boot pauses again if the battery is still low)")
    return {"rc": 0, "text": text}
