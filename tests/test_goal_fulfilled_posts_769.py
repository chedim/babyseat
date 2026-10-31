"""#769: a fulfilled goal posts `goal <id> fulfilled: <receipt>` into the chat that
planted it (convs[-1]) through watch_agents.escalate; no convs -> the latest chat;
an unfulfilled close posts nothing."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


class _Srv:
    pass


def _seat(monkeypatch):
    d = Path(tempfile.mkdtemp())
    est = EntityStore(path=str(d / "e.json"), shared=False)
    est._events_pool = WA.WatchEvents()
    est.sent = []
    est._decision_chat = lambda sid, text: est.sent.append((sid, text))
    srv = _Srv()
    srv.entity_store = est
    est._srv = srv
    monkeypatch.setattr(CIO, "latest_chat", lambda s, p: "ses_latest")
    return srv, est


def _goal(est):
    return est.add(cls="Goal", name="ship the suite", summary="x")["eid"]


def _close(est, eid, receipt="rc=0 all green"):
    est.edit(eid, properties={"status": "fulfilled", "fulfilled_ts": 100.0,
                              "fulfilled_evidence": receipt})
    return WA.notify_goal_fulfilled(est, est._events_pool, eid, by="planner", evidence=receipt)


def test_fulfilled_goal_posts_receipt_into_its_planting_chat(monkeypatch):
    srv, est = _seat(monkeypatch)
    eid = _goal(est)
    est.edit(eid, properties={"convs": ["ses_A", "ses_B"]})
    _close(est, eid)
    assert est.sent == [("ses_B", f"goal {eid} fulfilled: rc=0 all green")]


def test_goal_without_convs_posts_into_the_latest_chat(monkeypatch):
    srv, est = _seat(monkeypatch)
    eid = _goal(est)
    _close(est, eid)
    assert est.sent == [("ses_latest", f"goal {eid} fulfilled: rc=0 all green")]


def test_unfulfilled_close_posts_nothing(monkeypatch):
    srv, est = _seat(monkeypatch)
    eid = _goal(est)
    est.edit(eid, properties={"convs": ["ses_A"], "status": "open"})
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="planner", evidence="not a close")
    assert est.sent == []


def test_open_goal_is_not_marked_announced_so_its_later_close_posts(monkeypatch):
    srv, est = _seat(monkeypatch)
    eid = _goal(est)
    est.edit(eid, properties={"convs": ["ses_A"], "status": "open", "fulfilled_ts": 100.0})
    WA.notify_goal_fulfilled(est, est._events_pool, eid, by="planner", evidence="early")
    assert "fulfilled_announced" not in (est.docs[eid].properties or {})
    _close(est, eid)
    assert est.sent == [("ses_A", f"goal {eid} fulfilled: rc=0 all green")]
