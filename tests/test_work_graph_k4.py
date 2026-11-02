"""k4: ONE graph call -- decision_map.work_graph(est) -> {nodes:[goals,tasks,decisions], edges}."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def test_work_graph_nodes_and_edges():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    G, T1, T2, D = (MEMBRANE_NS + x for x in ("g", "task:t1", "task:t2", "d"))
    est.upsert(QuantumEntity(eid=G, cls="Goal", properties={"name": "ship", "status": "open"}))
    est.upsert(QuantumEntity(eid=T1, cls="Task", properties={"title": "a", "status": "open", "goal": G, "blocks": [T2]}))
    est.upsert(QuantumEntity(eid=T2, cls="Task", properties={"title": "b", "status": "open", "goal": G, "blocks": []}))
    est.upsert(QuantumEntity(eid=D, cls="Decision", properties={"name": "q", "status": "open", "goal": G, "blocks": [T2]}))
    g = DM.work_graph(est)
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kinds == {G: "goal", T1: "task", T2: "task", D: "decision"}
    e = {(x["from"], x["to"], x["rel"]) for x in g["edges"]}
    assert (T1, T2, "blocks") in e and (D, T2, "blocks") in e
    assert (T1, G, "of") in e and (D, G, "of") in e and (T2, G, "of") in e
    assert all(x["from"] in kinds and x["to"] in kinds for x in g["edges"])
