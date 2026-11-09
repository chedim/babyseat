"""A goal of ANY length arrives byte-identical at every hop: the stored goal,
the board intention, the planner prompt, each task detail and the worker prompt."""
import re
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _words(n_chars: int) -> str:
    out, i, n = [], 0, 0
    while n < n_chars:
        w = f"w{i:06d}"
        out.append(w)
        n += len(w) + 1
        i += 1
    return " ".join(out)[:n_chars].rstrip()


FINDINGS = "\n".join(f"Finding {i}: " + _words(300 + 40 * i) + f" END{i}" for i in range(1, 8))
CASES = {"1k": _words(1000), "50k": _words(50_000), "500k": _words(500_000), "7-findings": FINDINGS}


def _srv():
    d = Path(tempfile.mkdtemp())
    from membrane.intent import IntentBoard
    return types.SimpleNamespace(entity_store=EntityStore(path=str(d / "e.json"), shared=False),
                                 _intent_board=IntentBoard(d / "intents.json"),
                                 membrane=types.SimpleNamespace(embedder=None))


def _plant(srv, text):
    res = L._membrane_intent_create_or_pin(srv, text)
    assert res.get("ok", True) and "eid" in res, f"intend failed: {res}"
    return res["eid"]


@pytest.mark.parametrize("name", list(CASES))
def test_goal_text_identical_at_every_hop(name):
    text = CASES[name]
    srv = _srv()
    est = srv.entity_store
    eid = _plant(srv, text)
    assert est.docs[eid].properties["text"] == text, "stored goal text differs"

    why = "because"
    assert GG.planner_text(est, eid, why) == GG.PLANNER_TEXT.format(
        goal=eid, text=text, why=why, graph=GG._graph_lines(est, eid), tick_s=GG.WAIT_TICK_S), \
        "planner prompt differs from the goal text"

    GG.apply_plan(srv, eid, {"tasks": [{"key": "k1", "title": "t", "detail": text}]})
    task = next(k for k in est.docs if k.endswith(":k1"))
    assert est.docs[task].properties["detail"] == text, "task detail differs"
    wt = GG.worker_text(est, task)
    p = est.docs[task].properties
    assert wt == GG.WORKER_TEXT.format(task=task, goal=p.get("goal"), title=p.get("title") or task,
                                       detail=text, done="nothing"), "worker prompt differs"


def test_board_intention_keeps_whole_text():
    text = CASES["50k"]
    srv = _srv()
    eid = _plant(srv, text)
    assert srv.entity_store.docs[eid].properties["text"] == text
    assert eid in srv._intent_board.selection.pinned


def test_goals_sharing_a_long_prefix_stay_distinct():
    base = _words(2000)
    srv = _srv()
    a = _plant(srv, base + " alpha")
    b = _plant(srv, base + " beta")
    assert a != b
    assert srv.entity_store.docs[a].properties["text"] == base + " alpha"
    assert srv.entity_store.docs[b].properties["text"] == base + " beta"


def test_blocker_receipt_reaches_worker_whole():
    srv = _srv()
    est = srv.entity_store
    eid = _plant(srv, "short goal")
    GG.apply_plan(srv, eid, {"tasks": [{"key": "k1", "title": "a", "detail": "x"},
                                       {"key": "k2", "title": "b", "detail": "y", "after": ["k1"]}]})
    k1 = next(k for k in est.docs if k.endswith(":k1"))
    k2 = next(k for k in est.docs if k.endswith(":k2"))
    receipt = _words(30_000)
    est.docs[k1].properties["receipt"] = receipt
    assert f"{k1}: {receipt}" in GG.worker_text(est, k2)


def test_script_intent_path_has_no_cut():
    src = (Path(__file__).resolve().parent.parent / "membrane" / "night.py").read_text()
    m = re.search(r'intent_text = str\(out\["intent"\]\)(.*)', src)
    assert m and "[:" not in m.group(1), f"night.py cuts the script intent: {m and m.group(0)}"
