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
    _grant(vm)
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
    import ast                                              # no second scorer imported, anywhere in the module
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(L))):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[-1])
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name.split(".")[-1] for a in node.names)
    assert not names & {"predict", "outcomes"}


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


# ---- review of PR 939 (red first) ----------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from membrane import toybox as TBX  # noqa: E402
from membrane.entities import MEMBRANE_NS  # noqa: E402


def _trig(path):
    return SimpleNamespace(properties={"write": [path, "1"]})


@pytest.mark.parametrize("path", [
    "chat:operator.say",
    MEMBRANE_NS + "presence.restart",
    MEMBRANE_NS + "llm.call",
    MEMBRANE_NS + "llm.wake",
    "toy:tower.poke",
    TBX._eid() + ".reset",
    TBX._eid() + ".remove",
    "membrane::goal:g1.plan",
    "membrane::goal:g1.replan",
])
def test_every_adapter_branch_is_irreversible(path):
    assert L.reversible(_trig(path)) is False


def test_reversible_reuses_the_adapter_branch_table(monkeypatch):
    from membrane import adapters as AD
    seen = []
    real = AD._branch
    monkeypatch.setattr(AD, "_branch", lambda p: (seen.append(p), real(p))[1])
    L.reversible(_trig("x.a"))
    assert seen == ["x.a"]


def _grant(vm, granted=True):
    vm.learner.granted = lambda path: granted


def test_habit_in_toys_world_does_not_fire_on_a_command(world, monkeypatch):
    est, vm = world
    c = _proven(est, vm)                                     # learned while commands were the world
    monkeypatch.setattr(L, "WORLD", "toys")                  # the real gate (#727), not forced to commands
    assert "toy" not in c.properties
    vm.learner._habits.add(c.eid)
    _grant(vm)
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") is None


def test_habit_needs_a_grant_to_go_live(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    _grant(vm, False)
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") is None


def test_habit_pays_the_purse_when_it_fires(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    _grant(vm)
    before = vm.learner.balance(c.eid)
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") == 2
    assert vm.learner.balance(c.eid) == before - L.WRITE_COST


def test_habit_does_not_fire_without_funds(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    _grant(vm)
    vm.learner.purse.can_spend = lambda: False
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") is None


def test_habit_never_acts_while_the_llm_works(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    _grant(vm)
    vm.learner.llm_works = lambda chain: True               # #576 H: a proposal only suggests then
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") is None


def test_live_habit_fire_enters_pending_to_be_rescored(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    _grant(vm)
    z = _ent(est, "z")
    _turn(est, vm, (z, "a", 1), writer="person")
    assert est.docs[z.eid].properties.get("b") == 2
    assert any(cand == c.eid for rows in vm.learner._pending.values() for cand, _p, _v in rows)


def test_room_removing_a_candidate_drops_its_habit(world, monkeypatch):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    vm.learner._bal[c.eid] = -1.0
    monkeypatch.setattr(L, "CANDIDATES_MAX", 1)
    assert vm.learner._room() is True
    assert c.eid not in est.docs and c.eid not in vm.learner.habits()


def test_prune_drops_the_habit_too(world):
    est, vm = world
    c = _proven(est, vm)
    vm.learner.graduate(c.eid, threshold=LINE)
    vm.learner._bal[c.eid] = -5 * L.WRITE_COST
    assert c.eid in vm.learner.prune()
    assert c.eid not in vm.learner.habits()
