"""THE CHAT THING (#333 part 3a; CHAT OUT since 0.6.1, the operator: "add the
field to push chat out messages like it can do with voice ... both, and the
same should be for voice").

One entity per person, in the seat's own store:

  chat:<person>  Component, chat_io "chat". The seat WRITES to the person by
                 setting its `say` property (an ordinary entity edit, no new
                 tool): the line lands in their latest chat as an assistant
                 message, only while `on` (default on). `said` keeps what was
                 posted. What the person says reaches `heard` (a ring) and
                 `heard_n` / `heard_ts` / `session` (heard()); its getter
                 reads `heard_n`, so Blurp's look at it reads news -- changed
                 when the person said something since its last look. Only the
                 person's lines are heard: Blurp's own sends never are, so its
                 own line is never news (the chat stays an other).

Until part 3a it was two: chat:out:<person> (Config, chat_io "out": the
writing side, voice:out's twin) and chat:in:<person> (Component, chat_io
"in": what the person said, the thing rung 6 looked at). THE MIGRATION
(migrate(), once per seat at boot; and whatever first makes a person's chat
thing, so the count never restarts): the chat thing copies heard / heard_n /
heard_ts / session from chat:in and on / say / said from chat:out; both old
entities get `merged_into` it (mark-don't-delete: the store resolves them to
it, so an edit of chat:out:<person>.say -- an agent, a script, the lanes edit
route -- keeps working); its field spot starts at chat:in's live state (where
the person's speech put it), else chat:out's, else its first look places it;
Blurp's world gains it as `migrated` (the old two stay members: the world
only grows). The record is never
rewritten: chat_of() reads an old line's eid as the chat thing it became.

And the field's own way out: an entity's script may return `chat` and/or
`voice` (with an optional `to`, a person) -- a watcher can tell you the garage
is open with no model turn. The engine posts/speaks; the script never does.

Where it lands: the person's newest chat (a session they own), else the
session their voice last talked in, else -- a seat with no people, the
operator's own -- the newest chat that is not a loop's. Never Blurp's
private chat (#337): that one is Blurp's alone.

BLURP'S PRIVATE CHAT (#337, the PM with Blurp): one session per person,
marked `blurp: true` (blurp_chat). Blurp's own lines (by="self") land there, not in the person's newest chat, and the field hears them under
the session tag blurp:<person> (blurp_tag). A message the person posts there
runs no model turn and the voice does not answer (server.py,
TurnRunner.blurp_message).

Guard rails: a line only goes out while the chat thing is on; one line per
CHAT_GAP_S per person; an entity's script pushes at most once per
SCRIPT_PUSH_GAP_S and never the same line twice in a row (a tick fires every
second)."""
from __future__ import annotations

import re
import threading
import time

from .voice_io import _ring, slug

CHAT_GAP_S = 2.0
CHAT_MAX = 2000
SCRIPT_PUSH_GAP_S = 30.0
OPERATOR = "operator"             # the seat with no people: its operator's chat thing
_BLURP_LOCK = threading.Lock()    # #337: one private chat per person, even when two ask at once
_MIGRATE_LOCK = threading.RLock()
_SAY_LOCK = threading.RLock()     # #576 part E: one post per `say`, whoever reads it first

_GETTER = (
    "result = {'state': {'heard_n': int(membrane.me.properties.get('heard_n') or 0)}}"
)
FROM_IN = ("heard", "heard_n", "heard_ts", "session")     # the migration: what chat:in carries over
FROM_OUT = ("on", "say", "said")                           # ...and chat:out


def _key(person: str) -> str:
    """A person's slot name; a blank person is the operator's slot (slug('')
    is 'unknown', never blank -- so `slug(p) or OPERATOR` never fell back)."""
    return slug(person) if str(person or "").strip() else OPERATOR


def name_of(person: str) -> str:
    """The chat thing's name: chat:<person>."""
    return f"chat:{_key(person)}"


def eid_of(person: str) -> str:
    """The chat thing's store eid (exact: a name fragment like chat:alice would
    also match an active-model:chat:alice)."""
    from .entities import CLASS_COMPONENT, entity_id
    return entity_id(CLASS_COMPONENT, name_of(person))


def _old_eids(key: str) -> tuple[str, str]:
    """A person's chat:in / chat:out eids from before part 3a."""
    from .entities import CLASS_COMPONENT, CLASS_CONFIG, entity_id
    return entity_id(CLASS_COMPONENT, f"chat:in:{key}"), entity_id(CLASS_CONFIG, f"chat:out:{key}")


def _chat_re():
    from .entities import CLASSES
    cls = "|".join(re.escape(c) for c in CLASSES)
    return re.compile(r"^(?:membrane::|m:)?(?:(?:%s):)?chat:(?:(?:in|out):)?([^:\s]+)$" % cls, re.I)


_CHAT_RE = None


def person_of(eid) -> str | None:
    """The person whose chat this is, for any spelling of it -- the chat thing
    (chat:<p>), the old chat:in:<p> / chat:out:<p>, a store eid or a field id;
    None for anything else."""
    global _CHAT_RE
    if not isinstance(eid, str) or not eid:
        return None
    if _CHAT_RE is None:
        _CHAT_RE = _chat_re()
    m = _CHAT_RE.match(eid.strip())
    return m.group(1).lower() if m else None


def chat_of(eid):
    """THE ALIAS for record readers: chat:in:<p> / chat:out:<p> (any spelling)
    -> the chat thing's eid, chat:<p>; anything else is itself. Old lines keep
    counting toward the same person."""
    p = person_of(eid)
    return eid if p is None else eid_of(p)


def _place_from(srv, new_eid: str, olds) -> None:
    """The chat thing's field spot starts at the first old one's live state."""
    fld = getattr(srv, "efield", None)
    get, seat = getattr(fld, "get", None), getattr(fld, "place_vec", None)
    if get is None or seat is None:
        return
    from .eids import field_id
    try:
        st = get(field_id(new_eid))
        if st is not None and st.tier == "live" and st.state is not None:
            return
        for old in olds:
            o = get(field_id(old))
            if o is not None and o.tier == "live" and o.state is not None:
                seat(field_id(new_eid), o.state)
                return
    except Exception:
        pass


def _migrate(srv, ent, key: str) -> bool:
    """Fold a person's chat:in / chat:out into their chat thing (once: an old
    entity already merged is left alone). -> whether anything was folded."""
    est = srv.entity_store
    old_in, old_out = (est.docs.get(e) for e in _old_eids(key))
    olds = [o for o in (old_in, old_out)
            if o is not None and not (o.properties or {}).get("merged_into")]
    if not olds:
        return False
    pp = ent.properties or {}
    for src, keys in ((old_in, FROM_IN), (old_out, FROM_OUT)):
        if src is None or src not in olds:
            continue
        sp = src.properties or {}
        for k in keys:
            if k in sp:
                pp[k] = list(sp[k]) if isinstance(sp[k], list) else sp[k]
    ent.properties = pp
    for o in olds:
        op = o.properties or {}
        op["merged_into"] = ent.eid
        o.properties = op
        est.upsert(o, embedder=None)
    est.upsert(ent, embedder=None)
    _place_from(srv, ent.eid, [o.eid for o in (old_in, old_out) if o is not None])
    try:
        from . import self_world as _SW
        _SW.join(srv, [ent.eid], "migrated")
    except Exception:
        pass
    return True


def entity(srv, person: str, create: bool = True):
    """A person's chat thing, chat:<person> (made on first use; making it
    folds in their old chat:in / chat:out, see the module doc)."""
    from .entities import CLASS_COMPONENT
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    key = _key(person)
    eid = eid_of(person)
    with _MIGRATE_LOCK:
        ent = est.docs.get(eid)
        if ent is None and create:
            r = est.add(cls=CLASS_COMPONENT, name=name_of(person),
                        summary=(f"CHAT with {person or 'the operator'}: what they say reaches `heard` (a "
                                 "look reads it as news); write to them by editing properties.say -- it "
                                 "lands in their latest chat (only while `on`). A script can do the same "
                                 "by returning {'chat': '...', 'to': '<person>'}."))
            ent = est.docs.get(r.get("eid", "")) if isinstance(r, dict) else None
            if ent is None:
                return None
            ent.properties.update({"person": "" if key == OPERATOR else slug(person),
                                   "chat_io": "chat", "on": True, "heard_n": 0, "getter_script": _GETTER})
            if not _migrate(srv, ent, key):
                est.upsert(ent, embedder=None)               # its props on the next save
            try:
                est.flush()
            except Exception:
                pass
        return ent


def migrate(srv) -> dict:
    """THE BOOT MIGRATION (once per seat; SL.bind): every person with an old
    chat:in / chat:out not yet merged gets their chat thing. -> counts."""
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {}
    keys = set()
    for e in list((est.docs or {}).values()):
        pp = e.properties or {}
        if pp.get("chat_io") in ("in", "out") and not pp.get("merged_into"):
            p = person_of(e.eid)
            if p:
                keys.add(p)
    out = {"people": 0, "merged": 0}
    for k in sorted(keys):
        ent = entity(srv, "" if k == OPERATOR else k)
        if ent is not None:
            out["people"] += 1
            _migrate(srv, ent, k)                              # a chat thing made before its pair was folded
    out["merged"] = sum(1 for e in list((est.docs or {}).values())
                        if (e.properties or {}).get("chat_io") in ("in", "out")
                        and (e.properties or {}).get("merged_into"))
    try:
        est.flush()
    except Exception:
        pass
    return out


def heard(srv, person: str, text: str, sid: str = "") -> None:
    """The person answered (or spoke) -- Blurp's next look at their chat thing
    reads it as news, and a sleeping Blurp wakes (#345). Only a person's line
    comes here: Blurp's own sends never do."""
    try:
        from . import energy as _E          # #345: a person speaking wakes a sleeping Blurp
        _E.person_spoke(srv, person)
    except Exception:
        pass
    ent = entity(srv, person)
    if ent is None:
        return
    pp = ent.properties or {}
    pp["heard_n"] = int(pp.get("heard_n") or 0) + 1
    pp["heard_ts"] = time.time()
    _ring(pp, "heard", {"ts": time.time(), "text": str(text or "")[:600], "session": sid})
    if sid:
        pp["session"] = sid
    ent.properties = pp


def ensure(srv, spec: str) -> str | None:
    """The edit route's first step: an edit of a person's chat -- chat:<p> or
    the old chat:out:<p> -- makes their chat thing when it does not exist yet
    (the seat can write to anyone the seat knows) -> its exact eid, which the
    route edits (None: not a chat). The old spelling keeps resolving to it: a
    chat:out:<p> that never existed is made as a pointer (merged_into)."""
    s = str(spec or "").strip()
    if s.lower().startswith("membrane::"):
        s = s[len("membrane::"):]
    low = s.lower()
    if low.startswith("chat:out:") and s[len("chat:out:"):].strip():
        who = s[len("chat:out:"):].strip()
    elif low.startswith("chat:") and ":" not in s[len("chat:"):] and s[len("chat:"):].strip() \
            and s[len("chat:"):].strip().lower() not in ("in", "out"):
        who = s[len("chat:"):].strip()
    else:
        return None
    ent = entity(srv, "" if _key(who) == OPERATOR else who)
    if ent is None:
        return None
    if low.startswith("chat:out:"):
        est = srv.entity_store
        out_eid = _old_eids(_key(who))[1]
        if est.docs.get(out_eid) is None:
            from .entities import CLASS_CONFIG, QuantumEntity
            est.upsert(QuantumEntity(eid=out_eid, cls=CLASS_CONFIG, properties={
                "name": f"chat:out:{_key(who)}", "merged_into": ent.eid,
                "summary": f"the old name of {name_of(who)} (#333 part 3a)"}), embedder=None)
    return ent.eid


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
    person's chat thing into Blurp's world."""
    r = _runner(srv)
    if r is None or getattr(r, "sessions", None) is None:
        return ""
    rows = r.sessions.list()
    # the owner as the person's sessions spell it (the chat thing keeps only
    # a slug; the JWT door compares the owner exactly)
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
    """An edit of a chat thing's `say` (an unmigrated chat:out's too): post it
    (or say why not). `by` names a non-seat author (self's sends, #208) for
    _post to mark. One at a time (#576 part E: the edit route and the chat
    adapter both see a `say`; the first consumes it, the other finds it gone)."""
    with _SAY_LOCK:
        return _on_edit(srv, ent, by)


def _on_edit(srv, ent, by: str = "") -> dict | None:
    pp = (getattr(ent, "properties", None) or {})
    if pp.get("chat_io") not in ("chat", "out") or not str(pp.get("say") or "").strip():
        return None
    text = str(pp.get("say") or "").strip()[:CHAT_MAX]
    person = str(pp.get("person") or "")
    pp["say"] = ""                              # consumed: the next edit is the next message

    def _refuse(why: str, note: str) -> dict:
        pp["say_refused"] = note
        ent.properties = pp
        return {"posted": False, "why": why}
    if not pp.get("on", True):
        return _refuse("off", f"{person or 'the operator'}'s chat is off -- not posted")
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
