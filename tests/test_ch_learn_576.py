"""#576 part C: learning lives in the VM. From the writes in one chain the
learner guesses a trigger (the cause: a write up to 3 back in the chain; the
entity generalised to its class; the value a literal or a copy), defines it in
SHADOW (owner self), and its shadow firings write their prediction onto the
trigger itself. A later write in the same chain to the predicted path with the
predicted value (by anyone but the candidate) is a hit and refunds 2x the
write cost; every Blurp write costs WRITE_COST. A candidate is a proposal while
its balance stays positive, live on a granted branch, and sleep prunes the
negative ones."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_learn as L  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _ent(est, name, cls="toy", **props):
    e = QuantumEntity(eid=MEMBRANE_NS + f"{cls}:{name}", cls=cls, properties={"name": name, **props})
    est.upsert(e)
    return est.docs[e.eid]


class Purse:
    def __init__(self):
        self.spent, self.back, self.awake = 0.0, 0.0, True

    def charge(self, cost, why):
        self.spent += cost

    def refund(self, amount, why):
        self.back += amount

    def can_spend(self):
        return self.awake


@pytest.fixture
def world():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    vm = CH.VM(est)
    purse = Purse()
    granted = set()
    vm.learner = L.Learner(vm, purse, granted=lambda path: any(path.startswith(g) for g in granted))
    vm.attach()
    yield est, vm, purse, granted
    vm.detach()


def _turn(est, vm, *writes, writer="llm"):
    """One chain: each (entity, prop, value) written in order by `writer`."""
    with W.source("person", "t"), W.as_writer(writer):
        for ent, prop, val in writes:
            ent.properties[prop] = val
            est.upsert(ent)
    vm.drain()


def _cands(vm):
    return [t for t in vm.triggers() if (t.properties or {}).get("owner") == CH.SELF]


def test_a_chain_teaches_a_shadow_candidate(world):
    est, vm, purse, _ = world
    x = _ent(est, "x")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))
    cs = _cands(vm)
    assert len(cs) == 1
    p = cs[0].properties
    assert p["shadow"] is True and CH.render(cs[0]) == "WHEN changed(?x.a) WHERE isa(?x, toy): ?x.b = 2"
    assert purse.spent == pytest.approx(L.WRITE_COST)          # defining it was a write


def test_a_hit_refunds_and_makes_a_proposal(world):
    est, vm, purse, _ = world
    x, y, z = _ent(est, "x"), _ent(est, "y"), _ent(est, "z")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))                   # defined: -1
    _turn(est, vm, (y, "a", 1), (y, "b", 2))                   # fired -1, hit +2: it paid for its birth (0)
    c = _cands(vm)[0]
    assert c.properties["predicted"]["path"] == y.eid + ".b" and vm.learner.balance(c.eid) == pytest.approx(0)
    assert vm.learner.state(c.eid) == "shadow"
    _turn(est, vm, (z, "a", 1), (z, "b", 2))                   # fired -1, hit +2: +1
    assert vm.learner.balance(c.eid) == pytest.approx(L.WRITE_COST)
    assert vm.learner.state(c.eid) == "proposal"


def test_a_wrong_value_is_not_refunded(world):
    est, vm, purse, _ = world
    x, y = _ent(est, "x"), _ent(est, "y")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))
    _turn(est, vm, (y, "a", 1), (y, "b", 7))
    c = next(t for t in _cands(vm) if t.properties["write"][1] == "2")
    assert vm.learner.balance(c.eid) < 0 and vm.learner.state(c.eid) == "shadow"


def test_a_copied_value_generalises(world):
    est, vm, purse, _ = world
    x, y, z = _ent(est, "x"), _ent(est, "y"), _ent(est, "z")
    _turn(est, vm, (x, "a", 5), (x, "b", 5))
    c = next(t for t in _cands(vm) if t.properties["write"][1] == "x.a")
    _turn(est, vm, (y, "a", 9), (y, "b", 9))
    _turn(est, vm, (z, "a", 4), (z, "b", 4))
    assert vm.learner.state(c.eid) == "proposal"                  # the copy held on 9 and on 4


def test_the_same_guess_is_one_candidate(world):
    est, vm, purse, _ = world
    x, y = _ent(est, "x"), _ent(est, "y")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))
    _turn(est, vm, (y, "a", 1), (y, "b", 2))
    assert len([t for t in _cands(vm) if t.properties["write"] == ["?x.b", "2"]]) == 1


def test_it_does_not_learn_from_triggers_or_asleep(world):
    est, vm, purse, _ = world
    x = _ent(est, "x")
    _turn(est, vm, (x, "a", 1), (x, "b", 2), writer="trigger:membrane::Trigger:t")
    assert _cands(vm) == []
    purse.awake = False
    _turn(est, vm, (x, "a", 3), (x, "b", 4))
    assert _cands(vm) == []


def test_sleep_prunes_the_negative(world):
    est, vm, purse, _ = world
    x, y = _ent(est, "x"), _ent(est, "y")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))
    _turn(est, vm, (y, "a", 1), (y, "b", 7))                   # the "= 2" guess was wrong on y
    bad = [t.eid for t in _cands(vm) if vm.learner.balance(t.eid) < 0]
    assert bad
    gone = vm.learner.prune()
    assert set(bad) <= set(gone) and not any(e in est.docs for e in gone)


def test_a_proposal_on_a_granted_branch_writes_for_real(world):
    est, vm, purse, granted = world
    x, y, z, q = _ent(est, "x"), _ent(est, "y"), _ent(est, "z"), _ent(est, "q")
    _turn(est, vm, (x, "a", 1), (x, "b", 2))
    _turn(est, vm, (y, "a", 1), (y, "b", 2))
    _turn(est, vm, (z, "a", 1), (z, "b", 2))                   # now a proposal
    granted.add(MEMBRANE_NS + "toy:")
    _turn(est, vm, (q, "a", 1))
    assert q.properties.get("b") == 2                          # live: it wrote q.b itself
