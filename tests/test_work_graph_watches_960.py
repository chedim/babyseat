"""#960: work_graph draws live watches set on purpose (Memo / goal workers) as kind ``watch`` nodes,
with a ``watches`` edge to the watched entity and a ``triggers`` edge to what firing starts."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _store():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    G, T, X = (MEMBRANE_NS + x for x in ("g", "task:t1", "ci"))
    est.upsert(QuantumEntity(eid=G, cls="Goal", properties={"name": "ship", "status": "open"}))
    est.upsert(QuantumEntity(eid=T, cls="Task", properties={"title": "a", "status": "open", "goal": G, "blocks": []}))
    return est, G, T, X


def _op(owner, **kw):
    return dict({"op": "near", "rel": "x", "z": 1, "owner": owner, "ts": 1.0}, **kw)


def test_work_graph_includes_live_assistant_or_worker_watch_with_target_and_trigger():
    est, G, T, X = _store()
    est.upsert(QuantumEntity(eid=X, cls="Component", properties={
        "name": "ci", "escalate_when": "ci turns red", "trigger": T,
        "watch_ops": [_op("Memo"), _op("watch:" + T)]}))
    g = DM.work_graph(est)
    ws = [n for n in g["nodes"] if n["kind"] == "watch"]
    assert len(ws) == 2 and {n["owner"] for n in ws} == {"Memo", "watch:" + T}
    e = {(x["from"], x["to"], x["rel"]) for x in g["edges"]}
    for n in ws:
        assert (n["id"], X, "watches") in e          # what is watched
        assert (n["id"], T, "triggers") in e         # what firing wakes
        assert "ci turns red" in n["title"] or n.get("escalate_when") == "ci turns red"


def test_work_graph_excludes_finished_blurp_dispatch_and_ownerless_watches():
    est, G, T, X = _store()
    est.upsert(QuantumEntity(eid=X, cls="Component", properties={
        "name": "ci", "trigger": T,
        "watch_ops": [_op("Memo"), _op("Memo", done=True), _op("Blurp"), _op("membrane::self"),
                      _op("Memo", sense="dispatch"), _op(""), {"op": "near", "rel": "x", "z": 1}]}))
    g = DM.work_graph(est)
    ws = [n for n in g["nodes"] if n["kind"] == "watch"]
    assert len(ws) == 1 and ws[0]["owner"] == "Memo"   # only the live, owned, non-dispatch one
    assert len([x for x in g["edges"] if x["rel"] == "watches"]) == 1
