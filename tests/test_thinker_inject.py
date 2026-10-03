"""THE EVENT-THINKER INJECT (the operator's design): a reacted loop's
line lands in the OPERATOR'S primary session as ONE part — no new
turn, no model call — the client sees it as part of the ongoing
'thinking loop' stream. The message persists (sessions/messages
store), the part streams on the hub."""
import json
import time

from membrane.server import (TurnRunner, SSEHub, SessionStore,
                             MessageStore, _gen_id)


def test_inject_lands_in_the_primary_session():
    import tempfile
    d = tempfile.mkdtemp()
    r = TurnRunner(SSEHub(), SessionStore(f"{d}/s.json"),
                   MessageStore(f"{d}/m.json"),
                   call_ladder=None, executor=None, system="", model="m")
    r.cosmetic_busy = True
    # create the operator's session + a user turn:
    s = r.sessions.create(title="the operator's chat")
    s_id = s["id"]
    r.messages.append(s_id, {"id": "u1", "sessionID": s_id,
                             "role": "user"},
                      [{"id": "p1", "type": "text",
                        "sessionID": s_id, "messageID": "u1",
                        "text": "hello"}])
    # inject an event-thinker's line:
    from membrane.opencode import _install_inject  # the boot's wire fn
    r.srv = type("S", (), {})()
    _install_inject(r)
    r.srv._inject_to_primary(r, "[event-thinker] sysmon: cpu hot")
    rows = r.messages.msgs.get(s_id) or []
    texts = [p.get("text") for row in rows for p in row["parts"]
             if p.get("type") == "text"]
    assert any("event-thinker" in t for t in texts), texts
    # the line landed on an ASSISTANT row (the inject's store lane):
    landed = [row for row in rows
              if any("event-thinker" in p.get("text") for p in
                     row["parts"] if p.get("type") == "text")]
    assert landed and landed[0]["info"]["role"] == "assistant", rows[-1]
