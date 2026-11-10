"""REPLY FEEDBACK (G15 #1029, G9 #1032; docs/ui-pack-design.md): the chat's
footer under each finished reply -- thumbs up, thumbs down (with an optional
why) and retry. A press lands here as a Feedback entity, one per reply
(feedback:<message id>; a second press replaces the first), so the field, the
model's next turns and Blurp's record can all read how replies landed.
"""
from __future__ import annotations

import time

KINDS = ("up", "down", "clear")


def record(est, message: str, kind: str, why: str = "", session: str = "") -> dict:
    """Store one person's verdict on one reply. {ok, eid, kind} or {ok: False, error}."""
    if est is None:
        return {"ok": False, "error": "entity layer unavailable"}
    message = (message or "").strip()
    kind = (kind or "").strip().lower()
    if not message or len(message) > 200:
        return {"ok": False, "error": "message (the reply's id) required"}
    if kind not in KINDS:
        return {"ok": False, "error": "kind is one of up, down, clear"}
    why = (why or "").strip()[:500]
    name = "feedback:" + message
    label = {"up": "helpful", "down": "not helpful", "clear": "cleared"}[kind]
    summary = "Reply " + message + " rated " + label + (": " + why if why else "")
    res = est.add(cls="Feedback", name=name, summary=summary)
    if not res.get("ok"):
        return res
    eid = res.get("eid") or res.get("id") or name
    props = {"kind": kind, "why": why, "message": message, "session": (session or "")[:200],
             "at": time.time()}
    ed = est.edit(eid, properties=props)
    if isinstance(ed, dict) and ed.get("ok") is False:
        return ed
    return {"ok": True, "eid": eid, "kind": kind}
