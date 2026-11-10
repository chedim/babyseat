"""The work graph reads the store a fixed number of times, not once per task or goal (the field view polls it every
10 s; a scan per node held the GIL for seconds on a big field and slowed every other thread)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Counted(dict):
    scans = 0

    def values(self):
        _Counted.scans += 1
        return super().values()


def _store(goals: int, tasks: int):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    for g in range(goals):
        G = f"{MEMBRANE_NS}g{g}"
        est.upsert(QuantumEntity(eid=G, cls="Goal", properties={"name": f"g{g}", "status": "open"}))
        for t in range(tasks):
            nxt = [f"{MEMBRANE_NS}task:{g}-{t + 1}"] if t + 1 < tasks else []
            est.upsert(QuantumEntity(eid=f"{MEMBRANE_NS}task:{g}-{t}", cls="Task",
                                     properties={"title": str(t), "status": "open", "goal": G, "blocks": nxt}))
    est.docs = _Counted(est.docs)
    return est


def _scans(fn, est) -> int:
    _Counted.scans = 0
    fn(est)
    return _Counted.scans


def test_work_graph_scans_do_not_grow_with_nodes():
    small, big = _store(1, 2), _store(5, 20)
    assert _scans(DM.work_graph, big) == _scans(DM.work_graph, small)


def test_work_graph_same_answer():
    est = _store(2, 3)
    by = {n["id"]: n for n in DM.work_graph(est)["nodes"]}
    t1 = by[f"{MEMBRANE_NS}task:0-1"]
    assert [w["id"] for w in t1["waiting_on"]] == [f"{MEMBRANE_NS}task:0-0"]
    assert by[f"{MEMBRANE_NS}task:0-0"]["waiting_on"] == []
    g0 = by[f"{MEMBRANE_NS}g0"]
    assert {w["id"] for w in g0["waiting_on"]} == {f"{MEMBRANE_NS}task:0-0", f"{MEMBRANE_NS}task:0-1"}


def test_goal_view_scans_do_not_grow_with_tasks():
    small, big = _store(1, 2), _store(1, 30)
    G = f"{MEMBRANE_NS}g0"
    assert _scans(lambda e: GG.view(e, G), big) == _scans(lambda e: GG.view(e, G), small)
