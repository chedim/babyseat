"""#837: goal outcomes go to the goal's own person chat; capture tags are normalised."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
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


def test_conv_session_strips_capture_tags():
    assert CIO.conv_session("sid:ses_A") == "ses_A"
    assert CIO.conv_session("sid:sid:ses_A") == "ses_A"
    assert CIO.conv_session("ses_A") == "ses_A"
    assert CIO.conv_session("head:abc123") == ""
    assert CIO.conv_session("") == ""


def test_fulfilled_normalises_tag(monkeypatch):
    srv, est = _seat(monkeypatch)
    eid = est.add(cls="Goal", name="g", summary="x")["eid"]
    est.edit(eid, properties={"convs": ["sid:sid:ses_B"], "status": "fulfilled", "fulfilled_ts": 1.0})
    WA.notify_goal_fulfilled(est, est._events_pool, eid, evidence="ok")
    assert est.sent[0][0] == "ses_B"


def test_report_goes_to_goal_person_chat_skipping_loop_sessions(monkeypatch):
    srv, est = _seat(monkeypatch)
    eid = est.add(cls="Goal", name="g", summary="x")["eid"]
    est.edit(eid, properties={"convs": ["sid:ses_P", "sid:worker:ses_W", "planner:x"]})
    assert GG.report(srv, eid, claim="done") is True
    assert est.sent and est.sent[0][0] == "ses_P"


def test_stalled_running_task_is_not_moving():
    class D:
        cls = GG.TASK
        properties = {"status": GG.RUNNING, "started_ts": 1.0}
    assert GG._moving(D) is False
    D.properties = {"status": GG.RUNNING, "started_ts": time.time()}
    assert GG._moving(D) is True
