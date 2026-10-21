"""CHAT OUT (0.6.1, the operator: "add the field to push chat out messages like
it can do with voice ... both, and the same should be for voice").

One entity per person, in the seat's own store, the twin of voice:out:

  chat:out:<person>  on (default on) -- Argus WRITES to the person by setting
                     its `say` property (an ordinary entity edit, no new
                     tool): the line lands in their latest chat as an
                     assistant message. `said` keeps what was posted.

And the field's own way out: an entity's script may return `chat` and/or
`voice` (with an optional `to`, a person) -- a watcher can tell you the garage
is open with no model turn. The engine posts/speaks; the script never does.

Where it lands: the person's newest chat (a session they own), else the
session their voice last talked in, else -- a seat with no people, the
operator's own -- the newest chat that is not a loop's. Never Blurp's
private chat (#337): that one is Blurp's alone.

BLURP'S PRIVATE CHAT (#337, the PM with Blurp): one session per person,
marked `blurp: true` (blurp_chat). Blurp's own lines (by="self") land there,
not in the person's newest chat, and the field hears them under the
session tag blurp:<person> (blurp_tag). A message the person posts there
runs no model turn and the voice does not answer (server.py,
TurnRunner.blurp_message).

Guard rails: a line only goes out while chat:out is on; one line per
CHAT_GAP_S per person; an entity's script pushes at most once per
SCRIPT_PUSH_GAP_S and never the same line twice in a row (a tick fires every
second)."""
from __future__ import annotations

import threading
import time

from .voice_io import _ring, slug

CHAT_GAP_S = 2.0
CHAT_MAX = 2000
SCRIPT_PUSH_GAP_S = 30.0
OPERATOR = "operator"             # the seat with no people: its operator's chat:out
_BLURP_LOCK = threading.Lock()    # #337: one private chat per person, even when two ask at once


def _key(person: str) -> str:
    """A person's slot name; a blank person is the operator's slot (slug('')
    is 'unknown', never blank -- so `slug(p) or OPERATOR` never fell back)."""
    return slug(person) if str(person or "").strip() else OPERATOR


def name_of(person: str) -> str:
    return f"chat:out:{_key(person)}"


def entity(srv, person: str, create: bool = True):
    from .entities import CLASS_CONFIG
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    nm = name_of(person)
    ent = est._resolve_spec(nm)
    if ent is None and create:
        r = est.add(cls=CLASS_CONFIG, name=nm,
                    summary=(f"CHAT OUT for {person or 'the operator'}: write to them -- edit this "
                             "entity's properties.say with the message; it lands in their latest "
                             "chat (only while `on`). A script can do the same by returning "
                             "{'chat': '...', 'to': '<person>'}."))
        ent = est.docs.get(r.get("eid", "")) if isinstance(r, dict) else None
        if ent is not None:
            ent.properties.update({"person": "" if nm.endswith(":" + OPERATOR) else slug(person),
                                   "on": True, "chat_io": "out"})
    return ent


def name_in(person: str) -> str:
    return f"chat:in:{_key(person)}"


_IN_GETTER = (
    "result = {'state': {'heard_n': int(membrane.me.properties.get('heard_n') or 0)}}"
)


def entity_in(srv, person: str, create: bool = True):
    """THE OTHER SELF MEETS (rung 6): chat:in:<person> -- a normal reach
    target. Its getter reads `heard_n`, so a new heard since self's last
    look reads as `changed` (an answer); nothing new reads `unchanged`."""
    from .entities import CLASS_COMPONENT
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    nm = name_in(person)
    ent = est._resolve_spec(nm)
    if ent is None and create:
        r = est.add(cls=CLASS_COMPONENT, name=nm,
                    summary=(f"CHAT IN for {person or 'the operator'}: what the other says "
                             "reaches `heard`; self reaches this to read it back."))
        ent = est.docs.get(r.get("eid", "")) if isinstance(r, dict) else None
        if ent is not None:
            ent.properties.update({"person": "" if nm.endswith(":" + OPERATOR) else slug(person),
                                   "chat_io": "in", "heard_n": 0,
                                   "getter_script": _IN_GETTER})
    return ent


def heard(srv, person: str, text: str, sid: str = "") -> None:
    """The other answered (or spoke) -- self's next look reads it as news,
    and a sleeping Blurp wakes (#345)."""
    try:
        from . import energy as _E          # #345: a person speaking wakes a sleeping Blurp
        _E.person_spoke(srv, person)
    except Exception:
        pass
    ent = entity_in(srv, person)
    if ent is None:
        return
    pp = ent.properties or {}
    pp["heard_n"] = int(pp.get("heard_n") or 0) + 1
    pp["heard_ts"] = time.time()
    _ring(pp, "heard", {"ts": time.time(), "text": str(text or "")[:600], "session": sid})
    if sid:
        pp["session"] = sid
    ent.properties = pp


def ensure(srv, spec: str) -> None:
    """The edit route's first step: an edit of chat:out:<person> that does
    not exist yet creates it (Argus can write to anyone the seat knows)."""
    s = str(spec or "").strip()
    if s.lower().startswith("membrane::"):
        s = s[len("membrane::"):]
    if s.lower().startswith("chat:out:") and s[len("chat:out:"):].strip():
        entity(srv, s[len("chat:out:"):].strip())


def _runner(srv):
    return getattr(srv, "_loop_runner", None)


def _is_loop(r, sid: str) -> bool:
    from .jobs import _is_loop as _loop
    return _loop(r, sid)


def _chats(srv) -> list:
    r = _runner(srv)
    if r is None or getattr(r, "sessions", None) is None:
        return []
    rows = [s for s in r.sessions.list() if not _is_loop(r, s["id"])]
    return sorted(rows, key=lambda s: int((s.get("time") or {}).get("updated") or 0))


def latest_chat(srv, person: str) -> str:
    """The person's newest chat (see the module doc for the fallbacks) --
    never Blurp's private chat (#337)."""
    rows = [s for s in _chats(srv) if not s.get("blurp")]
    p = slug(person)
    if p:
        own = [s for s in rows if slug(str(s.get("owner") or "")) == p]
        if own:
            return own[-1]["id"]
        from . import voice_io as VIO
        vin = VIO.entity(srv, "in", person, create=False)
        return str(((vin.properties or {}) if vin is not None else {}).get("session") or "")
    return rows[-1]["id"] if rows else ""


def _active(s: dict) -> int:
    """When the person last chatted there: a chat's last update -- Blurp's
    private chat by the person's own last message (#337: `person_ts`), never
    by its making or Blurp's own lines."""
    if s.get("blurp"):
        return int(s.get("person_ts") or 0)
    return int((s.get("time") or {}).get("updated") or 0)


def recent_person(srv) -> str:
    """Whoever chatted last (the owner of the newest owned chat); '' = the operator."""
    own = sorted((s for s in _chats(srv) if s.get("owner")), key=_active)
    return str(own[-1]["owner"]) if own else ""


SELF_MARK = "\U0001F331"           # a sprout: a line self pushed (#208), not the seat's own
BLURP_TAG = "blurp:"               # #337: the field's session tag for Blurp's private chat


def blurp_tag(person: str) -> str:
    """#337: the capture session of a person's private chat with Blurp."""
    return BLURP_TAG + _key(person)


def _owns(s: dict, person: str) -> bool:
    """A session is this person's (the operator's: no owner)."""
    o = str(s.get("owner") or "")
    return slug(o) == slug(person) if str(person or "").strip() else not o.strip()


def blurp_chat(srv, person: str, create: bool = True) -> str:
    """#337: the person's private chat with Blurp (the PM with Blurp) -- one
    session per person, marked `blurp: true`, made on first use; '' when
    there is none (create=False) or no runner. Opening it brings the
    person's chat:in / chat:out into Blurp's world."""
    r = _runner(srv)
    if r is None or getattr(r, "sessions", None) is None:
        return ""
    rows = r.sessions.list()
    # the owner as the person's sessions spell it (chat:out keeps only a
    # slug; the JWT door compares the owner exactly)
    who = str(person or "").strip()
    if who:
        who = next((str(x["owner"]) for x in reversed(rows)
                    if not x.get("blurp") and x.get("owner") and _owns(x, who)), who)
    with _BLURP_LOCK:
        for s in rows:
            if s.get("blurp") and _owns(s, person):
                if who and s.get("owner") != who and str(person or "").strip() != slug(person):
                    s["owner"] = who                    # the person's own spelling wins over a slug
                    r.sessions.flush()
                return s["id"]
        if not create:
            return ""
        from . import self_name as _SN
        s = r.sessions.create(title=f"PM with {_SN.current()}")
        row = r.sessions.sessions.get(s["id"])
        if row is not None:
            row["blurp"] = True
            if who:
                row["owner"] = who
            r.sessions.flush()
        sid = s["id"]
    try:
        from . import self_world as _SW
        _SW.join_person(srv, person, "chat")
    except Exception:
        pass
    try:
        r.hub.publish({"type": "session.updated", "sessionID": sid, "info": dict(r.sessions.get(sid) or {})})
    except Exception:
        pass
    return sid


def _hear_blurp(srv, person: str, text: str) -> None:
    """#337: the field hears Blurp's own line, tagged as its private chat's."""
    cap = getattr(srv, "capturer", None)
    if cap is None:
        return
    try:
        cap.on_self(text, session=blurp_tag(person))      # its own role: never the model's prose
    except Exception:
        pass


def _post(srv, sid: str, text: str, by: str = "") -> None:
    """Post an assistant line into a chat. `by="self"` marks it as self's
    (#208): a sprout before the text, and `"from": "self"` on the message and
    its part so a UI can name it -- the person can tell it apart and answer."""
    r = _runner(srv)
    if by == "self" and not text.startswith(SELF_MARK):
        text = f"{SELF_MARK} {text}"
    now = int(time.time() * 1000)
    agent = str((r.sessions.get(sid) or {}).get("agent") or "build")
    info = {"id": f"msg_push_{now}", "sessionID": sid, "role": "assistant", "parentID": "",
            "modelID": "membrane", "providerID": "membrane", "mode": "build", "agent": agent,
            "path": {"cwd": "", "root": ""}, "cost": 0.0,
            "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
            "time": {"created": now, "completed": now}, "finish": "stop", "pushed": True}
    part = {"id": f"prt_push_{now}", "sessionID": sid, "messageID": info["id"],
            "type": "text", "text": text, "time": {"start": now, "end": now}}
    if by:
        info["from"] = part["from"] = by
    r.messages.append(sid, info, [part])
    r.hub.publish({"type": "message.updated", "sessionID": sid, "info": dict(info)})
    r.hub.publish({"type": "message.part.updated", "part": dict(part), "time": now,
                   "sessionID": sid})


def on_edit(srv, ent, by: str = "") -> dict | None:
    """An edit of a chat:out entity's `say`: post it (or say why not). `by`
    names a non-seat author (self's pushes, #208) for _post to mark."""
    pp = (getattr(ent, "properties", None) or {})
    if pp.get("chat_io") != "out" or not str(pp.get("say") or "").strip():
        return None
    text = str(pp.get("say") or "").strip()[:CHAT_MAX]
    person = str(pp.get("person") or "")
    pp["say"] = ""                              # consumed: the next edit is the next message

    def _refuse(why: str, note: str) -> dict:
        pp["say_refused"] = note
        ent.properties = pp
        return {"posted": False, "why": why}
    if not pp.get("on", True):
        return _refuse("off", f"{person or 'the operator'}'s chat:out is off -- not posted")
    now = time.time()
    if now - float(pp.get("said_ts") or 0) < CHAT_GAP_S:
        return _refuse("gap", f"too soon after the last message ({CHAT_GAP_S:g}s apart)")
    # #337: self's own line goes to its private chat with the person
    sid = blurp_chat(srv, person) if by == "self" else latest_chat(srv, person)
    if not sid or _runner(srv) is None:
        return _refuse("no chat", f"{person or 'the operator'} has no chat to post into yet")
    _post(srv, sid, text, by=by)
    if by == "self":
        _hear_blurp(srv, person, text)
    pp.pop("say_refused", None)
    pp["said_ts"] = now
    _ring(pp, "said", {"ts": now, "text": text, "session": sid})
    ent.properties = pp
    return {"posted": True, "session": sid}


def script_push(srv, src, out: dict) -> dict:
    """A script's `chat` / `voice` result: the engine posts / speaks for it.
    Returns {channel: receipt} for the apply's summary."""
    texts = {via: " ".join(str(out.get(via) or "").split()) for via in ("chat", "voice")}
    texts = {via: t for via, t in texts.items() if t}
    if not texts:
        return {}
    sp = src.properties if getattr(src, "properties", None) is not None else {}
    now = time.time()
    line = " | ".join(f"{k}:{v}" for k, v in sorted(texts.items()))
    if now - float(sp.get("push_last_ts") or 0) < SCRIPT_PUSH_GAP_S:
        return {via: {"posted": False, "why": "script gap"} for via in texts}
    if line == sp.get("push_last_text"):
        return {via: {"posted": False, "why": "repeat"} for via in texts}
    person = str(out.get("to") or "") or recent_person(srv)
    res = {}
    if "chat" in texts:
        ent = entity(srv, person)
        if ent is None:
            res["chat"] = {"posted": False, "why": "no store"}
        else:
            ent.properties["say"] = texts["chat"]
            res["chat"] = on_edit(srv, ent) or {"posted": False, "why": "empty"}
    if "voice" in texts:
        from . import voice_io as VIO
        v = VIO.entity(srv, "out", person) if slug(person) else None
        if v is None:
            res["voice"] = {"spoken": False, "why": "no person to speak to"}
        else:
            v.properties["say"] = texts["voice"][:VIO.SAY_MAX]
            res["voice"] = VIO.on_edit(srv, v) or {"spoken": False, "why": "empty"}
    if any(r.get("posted") or r.get("spoken") for r in res.values()):
        sp["push_last_ts"] = now
        sp["push_last_text"] = line
    return res
