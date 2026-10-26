"""#570: one session per watched entity, not one per wake. A loop pass
reuses its entity's session and starts it empty (the task text already
carries the entity's last verdicts), so nothing replays and the store keeps
one pass per entity. The boot collapses the duplicates an older seat left."""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.opencode import loop_session, collapse_loop_sessions  # noqa: E402
from membrane.server import MessageStore, SessionStore  # noqa: E402


def _seat():
    d = Path(tempfile.mkdtemp())
    runner = types.SimpleNamespace(sessions=SessionStore(str(d / "s.json")),
                                   messages=MessageStore(str(d / "m.json")))
    return types.SimpleNamespace(_loop_runner=runner), runner, d


def _say(runner, sid, n):
    runner.messages.append(sid, {"id": f"msg_{sid}_{n}", "role": "user", "time": {"created": n}},
                           [{"type": "text", "text": f"pass {n}"}])


def test_one_session_per_entity_and_each_pass_starts_empty():
    srv, runner, d = _seat()
    a = loop_session(srv, "watch: membrane::Component:x")
    _say(runner, a, 1)
    b = loop_session(srv, "watch: membrane::Component:x")
    assert b == a
    assert runner.messages.list(a) == []                       # nothing replays
    assert a not in json.loads((d / "m.json").read_text())     # and the file forgets it too
    c = loop_session(srv, "watch: membrane::Component:y")
    assert c != a
    assert len([s for s in runner.sessions.list() if s.get("agent", "").startswith("watch:")]) == 2


def test_a_deleted_sessions_messages_stay_gone_after_another_flush():
    srv, runner, d = _seat()
    keep, gone = runner.sessions.create("k")["id"], runner.sessions.create("g")["id"]
    _say(runner, keep, 1)
    _say(runner, gone, 2)
    assert runner.messages.delete(gone)
    _say(runner, keep, 3)                                      # a later flush merges with disk
    on_disk = json.loads((d / "m.json").read_text())
    assert gone not in on_disk and len(on_disk[keep]) == 2


def test_the_boot_collapses_duplicate_loop_sessions():
    srv, runner, d = _seat()
    ids = []
    for n in range(3):
        s = runner.sessions.create("watch: membrane::Component:x")
        s["agent"] = "watch: membrane::Component:x"
        s["time"]["updated"] = 1000 + n
        ids.append(s["id"])
        _say(runner, s["id"], n)
    chat = runner.sessions.create("my chat")["id"]
    _say(runner, chat, 9)
    assert collapse_loop_sessions(runner) == 2
    left = {s["id"] for s in runner.sessions.list()}
    assert left == {ids[-1], chat}                             # the newest pass stays; chats untouched
    on_disk = json.loads((d / "m.json").read_text())
    assert ids[0] not in on_disk and ids[1] not in on_disk and chat in on_disk
    assert collapse_loop_sessions(runner) == 0                 # idempotent
