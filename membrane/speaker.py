"""THE SPEAKER LANE (2026-10-12, the operator: "on cutting in for voice -- lets
introduce a separate per-seat 'speaker' event handler for voice comms").

A spoken message used to start a turn IN THE CHAT, so every sentence said aloud
cut whatever the chat was working on. Now a voice message (voiceIn) runs in the
seat's own speaker session, beside the chat, never through it:

  - the chat keeps working: no preempt, no "not run" boxes
  - the speaker turn is framed for voice: a sentence or two, spoken back
    automatically through voice:out when the turn ends
  - it sees what the chat is doing (busy or idle, its last line), so "how's it
    going?" is answered without interrupting; real work is handed to the chat
    or a loop (a goal, an llm call), not done inline
  - the voiceIn row stays in the chat session for the voice log, marked
    speakerRouted so the chat's history doesn't replay a question it never answered

membrane::speaker carries the session and `enabled` (false: voice goes back to
the chat, the old way).
"""
from __future__ import annotations

import time

ENT = "speaker"
AGENT = "speaker"
TITLE = "🎙 voice -- the speaker lane"


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties={
            "name": "speaker",
            "summary": ("THE SPEAKER LANE: what people SAY to you arrives here, in its own session beside "
                        "the chat -- answered in a sentence or two, spoken back automatically. The chat keeps "
                        "working meanwhile; hand real work to it (a goal, an llm call). `enabled` false sends "
                        "voice back into the chat."),
            "enabled": True})
        est.docs[ent.eid] = ent
    return ent


def enabled(srv) -> bool:
    ent = _entity(srv)
    return ent is not None and (ent.properties or {}).get("enabled") is not False


def session(runner) -> str | None:
    srv = getattr(runner, "srv", None)
    ent = _entity(srv)
    if ent is None:
        return None
    pp = ent.properties or {}
    sid = str(pp.get("session") or "")
    if sid and runner.sessions.get(sid) is not None:
        return sid
    s = runner.sessions.create(title=TITLE)
    sid = s["id"]
    try:
        row = runner.sessions.sessions.get(sid)
        if row is not None:
            row["agent"] = AGENT
            runner.sessions.flush()
    except Exception:
        pass
    pp["session"] = sid
    ent.properties = pp
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass
    return sid


def chat_status(runner, chat_sid: str) -> str:
    busy = chat_sid in (getattr(runner, "_busy", None) or {})
    last = ""
    try:
        for r in reversed(runner.messages.list(chat_sid) or []):
            if (r.get("info") or {}).get("role") == "assistant":
                t = " ".join(str(p.get("text") or "") for p in r.get("parts") or [] if p.get("type") == "text").strip()
                if t:
                    last = " ".join(t.split())[:180]
                    break
    except Exception:
        pass
    return (("the chat is BUSY with a turn right now" if busy else "the chat is idle")
            + (f"; its last line: \"{last}\"" if last else ""))


def frame(text: str, status: str) -> str:
    return ("[speaker lane] You're answering by VOICE: your reply is spoken aloud automatically "
            "(voice:out) -- one or two short spoken sentences, no lists, no markdown, no need to set say. "
            f"Meanwhile {status}. Don't redo the chat's work here; if this asks for real work, plant a goal "
            "or call the llm entity so the chat or a loop does it, and say so.\n" + text)


def route(runner, chat_sid: str, text: str, user_info: dict, parts: list):
    """A voiceIn message -> (speaker sid, framed text, info, parts), or None to
    leave it to the chat (not voice, the lane off, no seat)."""
    if not (user_info or {}).get("voiceIn"):
        return None
    srv = getattr(runner, "srv", None)
    if srv is None or not enabled(srv):
        return None
    sp = session(runner)
    if not sp or sp == chat_sid:
        return None
    user_info["speakerRouted"] = True            # the chat's row: the voice log keeps it, its history skips it
    try:
        runner.messages.append(chat_sid, user_info, parts)
    except Exception:
        pass
    framed = frame(text, chat_status(runner, chat_sid))
    from .server import _gen_id
    info = {**{k: v for k, v in user_info.items() if k != "speakerRouted"},
            "id": _gen_id("msg"), "sessionID": sp, "agent": AGENT, "speakerTurn": True, "chatSid": chat_sid,
            "time": {"created": int(time.time() * 1000)}}
    p2 = [{"id": _gen_id("prt"), "sessionID": sp, "messageID": info["id"], "type": "text", "text": framed}]
    runner.messages.append(sp, info, p2)
    return sp, framed, info, p2


def speak_reply(runner, info: dict, result, started: float) -> bool:
    """The speaker turn ended: its words go out through the person's voice:out
    (unless the turn already spoke itself)."""
    srv = getattr(runner, "srv", None)
    try:
        from . import voice_io as VIO
        from .speakers import split_speaker
        text_in = ""
        for p in (runner.messages.get_msg(info["sessionID"], info["id"]) or {}).get("parts") or []:
            text_in += str(p.get("text") or "")
        who = split_speaker(text_in.split("\n", 1)[-1])[0] or split_speaker(text_in)[0]
        out = VIO.entity(srv, "out", who, create=False) if who else None
        if out is None:
            return False
        pp = out.properties or {}
        if not pp.get("on") or float(pp.get("said_ts") or 0) > started:
            return False                          # voice:out off, or the turn already said it
        reply = _reply_text(result)
        if not reply:
            return False
        pp["say"] = reply
        out.properties = pp
        VIO.on_edit(srv, out)
        srv.entity_store.upsert(out, embedder=None)
        return True
    except Exception:
        return False


def _reply_text(result) -> str:
    if isinstance(result, dict):
        parts = result.get("parts") or []
        t = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
        t = t or str(result.get("text") or "")
    else:
        t = str(result or "")
    return " ".join(t.split()).strip()


def run(runner, routed) -> dict:
    sp, framed, info, parts = routed
    t0 = time.time()
    res = runner.run(sp, framed, user_info=info, user_parts=parts)
    speak_reply(runner, info, res, t0)
    return res
