"""A long goal text is never silently cut on its way to the planner and workers."""
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

LONG = " ".join(f"w{i:04d}" for i in range(715))[:5000]   # 5000 chars, every word distinct


def _srv():
    d = Path(tempfile.mkdtemp())
    from membrane.intent import IntentBoard
    return types.SimpleNamespace(entity_store=EntityStore(path=str(d / "e.json"), shared=False),
                                 _intent_board=IntentBoard(d / "intents.json"),
                                 membrane=types.SimpleNamespace(embedder=None))


def _plant(srv, text):
    """Whole, or a clear error: returns the goal eid, or None when intend refused loudly."""
    try:
        res = L._membrane_intent_create_or_pin(srv, text)
    except Exception as e:
        assert str(e).strip(), "a refusal must say why"
        return None
    assert "eid" in res, f"intend failed without saying why: {res}"
    return res["eid"]


def test_intend_5000_chars_reaches_planner_whole_or_errors():
    srv = _srv()
    eid = _plant(srv, LONG)
    if eid is None:
        return
    assert LONG in GG.planner_text(srv.entity_store, eid, "plan it"), "planner prompt cut the goal text"


def test_task_detail_5000_chars_reaches_worker_whole_or_errors():
    srv = _srv()
    eid = _plant(srv, "short goal")
    GG.apply_plan(srv, eid, {"tasks": [{"key": "k1", "title": "t", "detail": LONG}]})
    task = next(k for k in srv.entity_store.docs if ":k1" in k)
    assert LONG in GG.worker_text(srv.entity_store, task), "worker prompt cut the task detail (goalgraph detail[:2000])"


def test_blocker_receipt_reaches_worker_whole():
    srv = _srv()
    eid = _plant(srv, "short goal")
    GG.apply_plan(srv, eid, {"tasks": [{"key": "k1", "title": "a", "detail": "x"},
                                       {"key": "k2", "title": "b", "detail": "y", "after": ["k1"]}]})
    est = srv.entity_store
    k1 = next(k for k in est.docs if k.endswith(":k1"))
    k2 = next(k for k in est.docs if k.endswith(":k2"))
    est.docs[k1].properties["receipt"] = LONG[:1000]
    assert LONG[:1000] in GG.worker_text(est, k2), "worker 'done' line cut a blocker receipt to 160 chars"
