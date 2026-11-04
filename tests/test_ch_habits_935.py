"""#935 Habits: a learned trigger with a proven ch_learn balance runs without a model round.

Red first. The habit lives ON ch_learn: it reads Learner.balance, no parallel scorer.
Names under test (to be built): Learner.graduate(cand, *, threshold), Learner.habits(),
Learner.is_habit(cand), Learner.consequence(cand, bad), ch_learn.reversible(trigger).
The graduation line is the operator's: a REQUIRED named parameter, no default.
"""
import inspect
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_learn as L  # noqa: E402
from membrane import score as S  # noqa: E402
from tests.test_ch_learn_576 import Purse, _cands, _ent, _method, _turn  # noqa: E402,F401
from membrane.entities import EntityStore  # noqa: E402

LINE = 3 * L.WRITE_COST          # a test-only line; the real one is the operator's


@pytest.fixture(autouse=True)
def _commands_world(monkeypatch):
    monkeypatch.setattr(L, "WORLD", "commands")


@pytest.fixture
def world():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    vm = CH.VM(est)
    vm.learner = L.Learner(vm, Purse())
    vm.attach()
    yield est, vm
    vm.detach()


def _proven(est, vm, balance=5 * L.WRITE_COST):
    x, y = _ent(est, "x"), _ent(est, "y")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))
    _turn(est, vm, (y, "a", 1), (y, "b", 2))
    c = _cands(vm)[0]
    vm.learner._bal[c.eid] = balance
    return c


def _answers(monkeypatch):
    seen = []
    monkeypatch.setattr(S, "note", lambda kind, n=1, lane="", ts=None: seen.append(kind))
    return seen


def test_threshold_is_required_and_named_no_default():
    p = inspect.signature(L.Learner.graduate).parameters["threshold"]
    assert p.default is inspect.Parameter.empty and p.kind is inspect.Parameter.KEYWORD_ONLY
    assert not hasattr(L, "GRADUATE_AT") and not hasattr(L, "GRADUATION_LINE")


def test_over_the_line_runs_with_no_model_round_and_counts_as_field_work(world, monkeypatch):
    est, vm = world
    c = _proven(est, vm)
    assert vm.learner.graduate(c.eid, threshold=LINE) is True
    assert c.eid in vm.learner.habits()
    seen = _answers(monkeypatch)
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")            # no model wrote b: the habit does
    assert est.docs[z.eid].properties.get("b") == 2
    assert "answer" in seen


def test_under_the_line_does_not_graduate(world):
    est, vm = world
    c = _proven(est, vm, balance=LINE - 0.01)
    assert vm.learner.graduate(c.eid, threshold=LINE) is False
    assert vm.learner.habits() == []


def test_no_parallel_scorer_it_reads_the_ch_learn_balance(world):
    est, vm = world
    c = _proven(est, vm, balance=0.0)
    assert vm.learner.graduate(c.eid, threshold=LINE) is False
    vm.learner._bal[c.eid] = LINE                           # the only input is the balance
    assert vm.learner.graduate(c.eid, threshold=LINE) is True
    for mod in ("predict", "outcomes"):                     # no second scorer imported
        assert mod not in (inspect.getsource(L).split("import")[0] or "")
    src = inspect.getsource(L)
    assert "from . import predict" not in src and "from . import outcomes" not in src


def test_one_bad_consequence_demotes_to_candidate(world, monkeypatch):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    vm.learner.consequence(c.eid, bad=True)
    assert vm.learner.habits() == [] and c.eid in est.docs   # back to a candidate, not deleted
    seen = _answers(monkeypatch)
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") is None and "answer" not in seen


def test_a_good_consequence_keeps_the_habit(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    vm.learner.consequence(c.eid, bad=False)
    assert c.eid in vm.learner.habits()


def test_irreversible_action_never_graduates_whatever_the_balance(world):
    est, vm = world
    x, y, z = _ent(est, "x"), _ent(est, "y"), _ent(est, "z")
    m = _method(est)
    _turn(est, vm, (x, "a", 7, "person:op"), (m, "last_call", {"object": x.eid, "args": {"n": 7}}))
    c = next(t for t in _cands(vm) if t.properties["write"][0].endswith(".invoke"))
    vm.learner._bal[c.eid] = 1e9
    assert L.reversible(c) is False
    assert vm.learner.graduate(c.eid, threshold=LINE) is False
    assert c.eid not in vm.learner.habits()


def test_a_field_write_is_reversible(world):
    est, vm = world
    c = _proven(est, vm)
    assert L.reversible(c) is True
