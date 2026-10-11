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


def heard(srv, person: str, text: str, sid: str = "") -> None:
    """The person spoke (their words also start a turn, as a voiceIn message)."""
    ent = entity(srv, "in", person)
    if ent is None:
        return
    pp = ent.properties or {}
    _ring(pp, "heard", {"ts": time.time(), "text": str(text or "")[:600], "session": sid})
    pp["last_heard"] = str(text or "")[:600]
    if sid:
        pp["session"] = sid                     # where their voice talks: voice:out answers there
    ent.properties = pp


def on_edit(srv, ent) -> dict | None:
    """An edit of a voice:out entity's `say`: speak it (or say why not)."""
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
    if now - float(pp.get("said_ts") or 0) < SAY_GAP_S:
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
