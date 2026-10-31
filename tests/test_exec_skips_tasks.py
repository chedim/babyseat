"""The code executor takes only code (2026-10-31: the goal graph's `task -execute->
tool:workers` edge reached lang.process, which took it -- the edge turned `executing`
and stayed so -- then raised KeyError('') on a root with no language)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import lang as LG  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.entities import CLASS_COMPONENT, MEMBRANE_NS, QuantumEntity  # noqa: E402
from test_code_tree import _rows, _seat  # noqa: E402

TASK = MEMBRANE_NS + "task:abc:k1"
WORKERS = MEMBRANE_NS + "tool:workers"


def test_a_task_edge_is_not_taken_by_the_code_executor():
    srv = _seat()
    srv.entity_store.upsert(QuantumEntity(eid=WORKERS, cls=CLASS_COMPONENT, properties={"executor": True}))
    srv.entity_store.upsert(QuantumEntity(eid=TASK, cls="Task", properties={"status": "owed"}))
    R.upsert(srv.efield.b, srv.efield.origin, TASK, "execute", WORKERS, "goal-graph")
    assert LG.process(srv, TASK, WORKERS) is None
    live = {r["rel"] for r in _rows(srv, TASK)}
    assert "execute" in live and "executing" not in live
