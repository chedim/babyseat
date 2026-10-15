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

import re
import threading
import time

ENT = "speaker"
AGENT = "speaker"
TITLE = "🎙 voice -- the speaker lane"

# CHUNKED STT (Phase 2, "more is coming"): route()'s sentinel for a streamed
# partial transcript -- it is recorded + shown but starts no turn.
INTERIM = "interim"


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
    return ("[speaker lane] You're answering by VOICE: your reply is spoken aloud automatically, "
            "sentence by sentence as you write it (voice:out) -- so say your answer FIRST, in one short "
            "sentence, before any tool call. A tool call is silent and invisible to the person: when you "
            "must act, MAKE the call itself, and never write a tool call into your spoken words (to plant a "
            "goal, call the goal tool: goal, action 'plant', text '<the goal>'). One or two short spoken "
            "sentences in all, no lists, no markdown, no emoji, no need to set say. "
            f"Meanwhile {status}. Don't redo the chat's work here; if this asks for real work, plant a goal "
            "or call the llm entity so the chat or a loop does it -- and say in your own words that you've "
            "handed it over.\n" + text)


def _words(text: str) -> str:
    """The person's bare spoken words (no speaker line, no 🎙 stamp)."""
    from .speakers import split_speaker
    from . import voice_io as VIO
    return VIO._bare(split_speaker(text or "")[1]).strip()


def _streams(runner) -> dict:
    st = getattr(runner, "_voice_streams", None)
    if st is None:
        st = runner._voice_streams = {}
    return st


def _stream_interim(runner, chat_sid: str, stream: str, text: str, user_info: dict, parts: list) -> str:
    """An interim transcript: coalesce its words into the utterance's buffer,
    mark the voice-log row `voiceInterim` (history never replays it), and tell
    the page "more is coming". No turn."""
    st = _streams(runner)
    buf = st.setdefault(chat_sid, {}).setdefault(stream, {"text": "", "info": user_info, "parts": parts})
    w = _words(text)
    buf["text"] = " ".join(x for x in (buf["text"], w) if x)
    buf["info"], buf["parts"] = user_info, parts
    user_info["voiceInterim"] = True
    try:
        runner.messages.append(chat_sid, user_info, parts)
    except Exception:
        pass
    try:
        runner.hub.publish({"type": "membrane.stream", "sessionID": chat_sid, "stream": stream,
                            "final": False, "text": buf["text"]})
    except Exception:
        pass
    return INTERIM


def _stream_final(runner, chat_sid: str, stream: str) -> dict | None:
    """The utterance's final transcript: drop its buffer (the final text wins,
    but a whisper flush that returns nothing falls back to the buffered words)."""
    return (_streams(runner).get(chat_sid) or {}).pop(stream, None)


def route(runner, chat_sid: str, text: str, user_info: dict, parts: list):
    """A voiceIn message -> (speaker sid, framed text, info, parts), INTERIM for
    a streamed partial (handled, no turn), or None to leave it to the chat (not
    voice, the lane off, no seat)."""
    if not (user_info or {}).get("voiceIn"):
        return None
    srv = getattr(runner, "srv", None)
    if srv is None or not enabled(srv):
        return None
    sp = session(runner)
    if not sp or sp == chat_sid:
        return None
    stream = (user_info or {}).get("stream")
    if stream is not None and not (user_info or {}).get("final"):
        return _stream_interim(runner, chat_sid, stream, text, user_info, parts)
    if stream is not None:
        buf = _stream_final(runner, chat_sid, stream)
        if buf and not _words(text):
            text = buf["text"]                    # the whisper flush lost the words
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


# THE REPLY SPOKEN AS IT STREAMS (2026-10-15, the operator: hands-free voice
# should feel fluid and snappy, like talking to a person). A spoken reply used to
# reach the page only after the WHOLE turn, tool rounds included -- a median 16 s
# from the end of speech to the first word. Now every finished sentence of the
# turn's TEXT parts is spoken the moment it streams; reasoning and code never are.
_SENTENCE = re.compile(r"(.+?[.!?…][\"'”’)\]]*)(?=\s|$)", re.S)
# INLINE CODE (2026-10-15): the voice-framed model narrates a tool call as
# `goal(action='plant', text='...')` inline code instead of making the call --
# that must never be voiced (it reads as noise, and the goal isn't planted).
_INLINE_CODE = re.compile(r"`[^`]*`")


class _Streamer:
    """Follows one speaker turn's events on the hub and speaks its sentences."""

    def __init__(self, runner, sid: str, out):
        self.runner, self.sid, self.out = runner, sid, out
        self.srv = getattr(runner, "srv", None)
        self.q = runner.hub.subscribe()
        self.kind: dict = {}          # partID -> part type
        self.buf: dict = {}           # partID -> text not yet spoken
        self.fence: dict = {}         # partID -> inside a ``` block
        self.spoke = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name="speaker-stream", daemon=True)
        self._t.start()

    def _say(self, text: str) -> None:
        text = " ".join(text.split()).strip()
        text = " ".join(_INLINE_CODE.sub(" ", text).split()).strip()
        if len(text) < 2 or not any(ch.isalnum() for ch in text):
            return
        from . import voice_io as VIO
        pp = self.out.properties or {}
        pp["say"] = text
        self.out.properties = pp
        r = VIO.on_edit(self.srv, self.out, stream=True)
        try:
            if self.srv is not None:
                self.srv.entity_store.upsert(self.out, embedder=None)
        except Exception:
            pass
        if r and r.get("spoken") is not False:
            self.spoke += 1

    def _speakable(self, pid: str, chunk: str) -> str:
        """The chunk minus anything inside a code fence (state kept across deltas)."""
        out, inside = [], self.fence.get(pid, False)
        bits = chunk.split("```")
        for i, b in enumerate(bits):
            if i:
                inside = not inside
            if not inside:
                out.append(b)
        self.fence[pid] = inside
        return " ".join(out)

    def _feed(self, ev: dict, final: bool = False) -> None:
        typ = ev.get("type")
        if typ == "message.part.updated":
            part = ev.get("part") or {}
            if (part.get("sessionID") or ev.get("sessionID")) == self.sid and part.get("id"):
                self.kind[part["id"]] = part.get("type")
                if (part.get("time") or {}).get("end") and part.get("type") == "text":
                    self._flush(part["id"], tail=True)          # the part closed: its last words
            return
        if typ != "message.part.delta" or ev.get("sessionID") != self.sid:
            return
        pid = ev.get("partID")
        if self.kind.get(pid) != "text":
            return                                              # reasoning, tools: never spoken
        self.buf[pid] = self.buf.get(pid, "") + self._speakable(pid, str(ev.get("delta") or ""))
        self._flush(pid)

    def _flush(self, pid: str, tail: bool = False) -> None:
        text = self.buf.get(pid, "")
        last = 0
        for m in _SENTENCE.finditer(text):
            self._say(m.group(1))
            last = m.end()
        rest = text[last:]
        if tail and rest.strip():
            self._say(rest)
            rest = ""
        self.buf[pid] = rest

    def _loop(self) -> None:
        from queue import Empty
        while not self._stop.is_set():
            try:
                self._feed(self.q.get(timeout=0.05))
            except Empty:
                continue
            except Exception:
                continue

    def finish(self) -> None:
        """The turn ended: what is still queued, then every part's last words."""
        self._stop.set()
        self._t.join(timeout=2)
        from queue import Empty
        while True:
            try:
                self._feed(self.q.get_nowait())
            except Empty:
                break
            except Exception:
                continue
        for pid in list(self.buf):
            self._flush(pid, tail=True)
        try:
            self.runner.hub.unsubscribe(self.q)
        except Exception:
            pass


def _voice_out(runner, info: dict):
    """The person's voice:out entity for this speaker turn, if it is on."""
    srv = getattr(runner, "srv", None)
    try:
        from . import voice_io as VIO
        from .speakers import split_speaker
        text_in = ""
        for p in (runner.messages.get_msg(info["sessionID"], info["id"]) or {}).get("parts") or []:
            text_in += str(p.get("text") or "")
        who = split_speaker(text_in.split("\n", 1)[-1])[0] or split_speaker(text_in)[0]
        out = VIO.entity(srv, "out", who, create=False) if who else None
        return out if out is not None and (out.properties or {}).get("on") else None
    except Exception:
        return None


def run(runner, routed) -> dict:
    sp, framed, info, parts = routed
    t0 = time.time()
    srv = getattr(runner, "srv", None)
    if srv is not None:
        srv._speaker_turn_live = int(getattr(srv, "_speaker_turn_live", 0) or 0) + 1
    out = _voice_out(runner, info)
    streamer = _Streamer(runner, sp, out) if out is not None and getattr(runner, "hub", None) else None
    try:
        res = runner.run(sp, framed, user_info=info, user_parts=parts)
    finally:
        if streamer is not None:
            streamer.finish()
        if srv is not None:
            srv._speaker_turn_live = max(0, int(getattr(srv, "_speaker_turn_live", 1) or 1) - 1)
    if streamer is None or not streamer.spoke:
        speak_reply(runner, info, res, t0)      # nothing streamed (no deltas): the whole reply, as before
    release_listen(srv)                          # the LAST sentence is out: now the mic may open
    return res


def release_listen(srv) -> None:
    """A listen held during the turn opens after the reply was said: a
    deferred follow-up (`listen_pending`, voice_io.on_listen) and an ear the
    auto-pause held (`listen_hold`) both reopen now that Argus is done."""
    try:
        from . import voice_io as VIO
        est = getattr(srv, "entity_store", None)
        for ent in list((getattr(est, "docs", None) or {}).values()):
            pp = ent.properties or {}
            if pp.get("voice_io") != "in":
                continue
            if pp.get("listen_pending"):
                pp["listen"] = pp.pop("listen_pending")
                ent.properties = pp
                VIO.on_listen(srv, ent)
                est.upsert(ent, embedder=None)
            elif pp.get("listen_hold"):
                ent.properties = pp
                VIO.resume(srv, ent)
                est.upsert(ent, embedder=None)
    except Exception:
        pass
