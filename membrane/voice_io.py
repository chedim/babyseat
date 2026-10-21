"""VOICE IN, VOICE OUT (2026-10-11, the operator: "a per-seat voice
output/input entity that is enabled/disabled when the ui buttons are flipped
and let Argus use those parallel with the chat ... the voice should not be
appearing/duplicating as a message in the text chat ... and it should not
voice the chat since we have voice:out ... a separate voice log I could open,
hidden by default").

Two entities per person, in the seat's own store:

  voice:in:<person>   on / mode (ptt, lock, hotword, addressed) / device --
                      flipped by the page's mic buttons; `heard` keeps what
                      the person said (their words still start a turn, as a
                      message marked voiceIn: hidden from the chat, shown in
                      the voice log)
  voice:out:<person>  on / device -- flipped by 🔊; Argus SPEAKS by setting
                      its `say` property (an ordinary entity edit, no new
                      tool): the page says it aloud, the chat keeps the text
                      it wrote. `said` keeps what was spoken.

Text and voice are two channels: the page never reads the chat aloud.
Guard rails: a line only goes out while voice:out is on; at most one line
per SAY_GAP_S per person, SAY_MAX chars; never to a phone call or a loop.
"""
from __future__ import annotations

import re
import time

RING = 30
SAY_GAP_S = 1.5
SAY_MAX = 400
IN_MODES = ("off", "ptt", "lock", "hotword", "addressed")


def slug(name: str) -> str:
    from .speakers import slug as _slug
    return _slug(name or "")


def _est(srv):
    return getattr(srv, "entity_store", None)


def name_of(kind: str, person: str) -> str:
    return f"voice:{kind}:{slug(person)}"


def entity(srv, kind: str, person: str, create: bool = True):
    from .entities import CLASS_CONFIG
    est = _est(srv)
    if est is None or not slug(person):
        return None
    nm = name_of(kind, person)
    ent = est._resolve_spec(nm)
    if ent is None and create:
        how = ("what they say reaches you as a message marked 🎙 (spoken); their mic mode is `mode`"
               if kind == "in" else
               "speak to them: edit this entity's properties.say with one or two short spoken sentences "
               "(only while `on`); the chat keeps your full text")
        r = est.add(cls=CLASS_CONFIG, name=nm,
                    summary=f"VOICE {kind.upper()} for {person}: {how}.")
        ent = est.docs.get(r.get("eid", "")) if isinstance(r, dict) else None
        if ent is not None:
            ent.properties.update({"person": person, "on": False, "voice_io": kind})
    return ent


def _ring(pp: dict, key: str, item: dict) -> None:
    pp[key] = (list(pp.get(key) or []) + [item])[-RING:]


def set_state(srv, person: str, in_on=None, in_mode=None, out_on=None, device: str = "") -> dict:
    """The page's buttons flipped: the entities follow."""
    out = {}
    for kind, on in (("in", in_on), ("out", out_on)):
        ent = entity(srv, kind, person)
        if ent is None:
            continue
        pp = ent.properties or {}
        if on is not None:
            pp["on"] = bool(on)
        if kind == "in" and in_mode is not None:
            pp["mode"] = in_mode if in_mode in IN_MODES else "off"
        if device:
            pp["device"] = str(device)[:80]
        pp["state_ts"] = time.time()
        ent.properties = pp
        out[kind] = {k: pp.get(k) for k in ("on", "mode", "device") if k in pp}
    return out


def _bare(text: str) -> str:
    """The spoken words without the page's 🎙 and the seat's state tag."""
    import re
    return re.sub(r"^\s*🎙\s*(?:\[voice:out:[^\]]*\]\s*)?", "", str(text or "")).strip()


_WAKE = re.compile(r"^\s*hey[\s,]*([A-Za-z]+)([\s,.]*(.*))?$", re.IGNORECASE)


def _normalize_wake(text: str) -> str:
    """A wake word the STT mis-heard ('Hey mama' for 'Hey Memo') reaches the
    heard log verbatim; the log is what the operator audits, so the configured
    hotword stays canonical and the heard form rides as an annotation
    (issue #104)."""
    from . import naming
    name = (naming.current() or "").strip()
    t = str(text or "")
    m = _WAKE.match(t)
    if not m or not name:
        return t
    word, rest = m.group(1), (m.group(3) or "").strip()
    if word.lower() == name.lower():
        return t                        # the hotword came through clean
    out = f"Hey {name} [heard: {word}]"
    return out + (f" {rest}" if rest else "")


def stamp(srv, person: str, text: str) -> str:
    """'🎙 <words>' -> '🎙 [voice:out:<person> on|off] <words>' (the state only)."""
    out = entity(srv, "out", person)
    on = bool(((out.properties or {}) if out is not None else {}).get("on"))
    tag = f"[{name_of('out', person)} {'on' if on else 'off'}]"
    return text.replace("🎙 ", f"🎙 {tag} ", 1) if "🎙 " in text else text


def heard(srv, person: str, text: str, sid: str = "") -> None:
    """The person spoke (their words also start a turn, as a voiceIn message);
    a sleeping Blurp wakes (#345)."""
    try:
        from . import energy as _E          # #345: a person speaking wakes a sleeping Blurp
        _E.person_spoke(srv, person)
    except Exception:
        pass
    ent = entity(srv, "in", person)
    if ent is None:
        return
    text = _bare(text)
    text = _normalize_wake(text)
    pp = ent.properties or {}
    _ring(pp, "heard", {"ts": time.time(), "text": str(text or "")[:600], "session": sid})
    pp["last_heard"] = str(text or "")[:600]
    if sid:
        pp["session"] = sid                     # where their voice talks: voice:out answers there
    ent.properties = pp


def on_edit(srv, ent, stream: bool = False) -> dict | None:
    """An edit of a voice:out entity's `say`: speak it (or say why not).
    stream=True: one sentence of a reply being spoken as it streams (the speaker
    lane) -- the gap between lines guards against a chatty model, not against the
    next sentence of the same answer."""
    pp = (getattr(ent, "properties", None) or {})
    if pp.get("voice_io") != "out" or not str(pp.get("say") or "").strip():
        return None
    text = " ".join(str(pp.get("say") or "").split())[:SAY_MAX]
    person = str(pp.get("person") or "")
    pp["say"] = ""                              # consumed: the next edit is the next line
    if not pp.get("on"):
        pp["say_refused"] = f"{person}'s voice:out is off -- not spoken (the chat carries your text)"
        ent.properties = pp
        return {"spoken": False, "why": "off"}
    now = time.time()
    if not stream and now - float(pp.get("said_ts") or 0) < SAY_GAP_S:
        pp["say_refused"] = f"too soon after the last line ({SAY_GAP_S}s apart)"
        ent.properties = pp
        return {"spoken": False, "why": "gap"}
    pp.pop("say_refused", None)
    pp["said_ts"] = now
    _ring(pp, "said", {"ts": now, "text": text})
    ent.properties = pp
    vin = entity(srv, "in", person, create=False)
    sid = str(((vin.properties or {}) if vin else {}).get("session") or pp.get("session") or "")
    r = getattr(srv, "_loop_runner", None)
    try:
        if r is not None and sid:
            r.hub.publish({"type": "membrane.say", "sessionID": sid, "person": person,
                           "line": {"text": text, "ts": now, "who": "argus"}})
    except Exception:
        pass
    _hold_ear(srv, person)                     # the ear never hears his own voice
    return {"spoken": bool(sid), "session": sid}


def read(srv, person: str) -> dict:
    """The person's two voice entities (state + their rings): the voice log."""
    out = {}
    for kind in ("in", "out"):
        ent = entity(srv, kind, person, create=False)
        pp = (ent.properties or {}) if ent is not None else {}
        out[kind] = {k: pp.get(k) for k in ("on", "mode", "device", "session")}
        out[kind]["log"] = list(pp.get("heard" if kind == "in" else "said") or [])
    return out


LISTEN_MAX_S = 30
LOCK_S = 8.0                     # THE SHORTER LOCK (Phase 3): a follow-up answer
                                 # opens this long -- a reply, not a phone call


def on_listen(srv, ent) -> dict | None:
    """ARGUS OPENS THE MIC (2026-10-12, the operator: "let argus turn listening on
    and off instead of that follow-up mode -- they can control it if they need a
    response"). An edit of voice:in's `listen` tells the person's page what to do:
    seconds (0 closes, at most LISTEN_MAX_S -- a follow-up set mid-turn gets the
    shorter LOCK_S), or "pause"/"resume" (Phase 3's turn-taking: hold the ear
    while Argus speaks, reopen it after)."""
    pp = (getattr(ent, "properties", None) or {})
    if pp.get("voice_io") != "in" or "listen" not in pp:
        return None
    val = pp.pop("listen")
    if isinstance(val, str) and val in ("pause", "resume"):
        return _listen_toggle(srv, ent, pp, val)
    try:
        secs = max(0.0, min(float(val or 0), LISTEN_MAX_S))
    except (TypeError, ValueError):
        secs = 0.0
    person = str(pp.get("person") or "")
    sid = str(pp.get("session") or "")
    if secs and getattr(srv, "_speaker_turn_live", None):
        # THE QUESTION FIRST (2026-10-12, the operator: "follow-up mic turns on before
        # the q for it is voiced and blocks the q"): a speaker turn speaks its reply when
        # it ENDS -- a listen set inside it waits for that reply, then opens. The follow-up
        # is the SHORTER LOCK, not the full window.
        secs = min(secs, LOCK_S)
        pp["listen_pending"] = secs
        ent.properties = pp
        return {"listening": secs, "deferred": "after this turn's spoken reply"}
    pp["listen_ts"] = time.time()
    pp["listening_for_s"] = secs
    ent.properties = pp
    r = getattr(srv, "_loop_runner", None)
    try:
        if r is not None and sid:
            r.hub.publish({"type": "membrane.listen", "sessionID": sid, "person": person, "seconds": secs})
    except Exception:
        pass
    return {"listening": secs, "session": sid}


def _listen_toggle(srv, ent, pp: dict, word: str) -> dict:
    """The "listen" control's pause/resume (Phase 3): the ear holds its remaining
    window while Argus speaks, and reopens it after -- the page needn't guess."""
    if word == "pause":
        held = float(pp.get("listening_for_s") or 0)
        pp["listen_hold"] = held if held > 0 else pp.get("listen_hold") or 0
        pp["listening_for_s"] = 0
        pp["listen_ts"] = time.time()
        ent.properties = pp
        _publish_listen(srv, pp, 0, pause=True)
        return {"listening": 0, "paused": True, "session": str(pp.get("session") or "")}
    return resume(srv, ent)


def resume(srv, ent) -> dict | None:
    """Reopen a held ear: `listen_hold` seconds again (the auto-pause let go)."""
    pp = (getattr(ent, "properties", None) or {})
    if pp.get("voice_io") != "in":
        return None
    secs = float(pp.pop("listen_hold", 0) or 0)
    if secs <= 0:
        return None
    pp["listen_ts"] = time.time()
    pp["listening_for_s"] = secs
    ent.properties = pp
    _publish_listen(srv, pp, secs)
    return {"listening": secs, "session": str(pp.get("session") or "")}


def _publish_listen(srv, pp: dict, secs: float, pause: bool = False) -> None:
    r = getattr(srv, "_loop_runner", None)
    ev = {"type": "membrane.listen", "sessionID": str(pp.get("session") or ""),
          "person": str(pp.get("person") or ""), "seconds": secs}
    if pause:
        ev["pause"] = True
    try:
        if r is not None and ev["sessionID"]:
            r.hub.publish(ev)
    except Exception:
        pass


def _hold_ear(srv, person: str) -> None:
    """AUTO-PAUSE TURN-TAKING (Phase 3): while Argus speaks, the person's ear
    holds its remaining window -- it never hears his own voice. The speaker
    turn's release_listen reopens it."""
    vin = entity(srv, "in", person, create=False)
    if vin is None:
        return
    pp = vin.properties or {}
    if not pp.get("on") or float(pp.get("listening_for_s") or 0) <= 0:
        return
    pp["listen_hold"] = pp.get("listen_hold") or float(pp.get("listening_for_s") or 0)
    pp["listening_for_s"] = 0
    pp["listen_ts"] = time.time()
    vin.properties = pp
    _publish_listen(srv, pp, 0, pause=True)
