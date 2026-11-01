"""#826 / #798: a goal's Wait must find `done:x` by name, fail visibly at its deadline, and
still be checked every every_s after a restart left its `check` flag at 'due'."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401
from test_goal_wait_773 import CI, _plan, _push_done, _sweep, DEADLINE  # noqa: E402,F401

DONE_X = MEMBRANE_NS + "Component:done:x"
COND = {"field": "done:x", "prop": "rc", "equals": "0"}


def _armed(seat, rc="0"):
    seat.entity_store.upsert(QuantumEntity(eid=DONE_X, cls="Component", properties={"name": "done:x", "rc": rc}))
    write(seat, GOAL, "plan", _plan(COND))
    _push_done(seat)
    assert st(seat, CI) == GG.ARMED


def test_a_condition_naming_done_x_finds_the_component(seat):          # (a) #826
    _armed(seat)
    _sweep(seat, GG.WAIT_TICK_S + 1)
    p = seat.entity_store.docs[CI].properties
    assert "no entity" not in str(p.get("last_check_result")), p.get("last_check_result")
    assert st(seat, CI) == "done"


def test_an_unmet_wait_fails_visibly_at_its_deadline(seat):            # (b) #826
    _armed(seat, rc="1")
    _sweep(seat, GG.WAIT_TICK_S + 1)
    d = seat.entity_store.docs[CI]
    d.properties = {**d.properties, "armed_ts": time.time() - DEADLINE * 60 - 5}
    seat.entity_store.upsert(d, embedder=None)
    _sweep(seat, 2 * GG.WAIT_TICK_S + 2)
    assert st(seat, CI) == "failed"
    assert [t for t in seat.told if t[0] == CI]                        # the planner is told


def test_a_wait_left_at_check_due_is_checked_again_after_a_restart(seat):  # (c) #798
    from membrane import adapters as AD
    from membrane import ch as CH
    _armed(seat, rc="1")
    _sweep(seat, GG.WAIT_TICK_S + 1)
    # the restart: the queued check died with the process, the flag is left at 'due'
    AD.stop()
    seat.ch_vm.detach()
    d = seat.entity_store.docs[CI]
    d.properties = {**d.properties, "check": "due", "last_check_ts": time.time() - 600,
                    "armed_ts": time.time() - DEADLINE * 60 - 5}
    seat.entity_store.upsert(d, embedder=None)
    seat.ch_vm = CH.VM(seat.entity_store)                  # the new process: fresh VM and queue
    seat.ch_vm.attach()
    AD.start(seat)
    settle(seat)
    seat.ch_vm.sweep(now=time.time() + 3 * GG.WAIT_TICK_S)
    settle(seat)
    p = seat.entity_store.docs[CI].properties
    assert float(p["last_check_ts"]) > time.time() - 60, "never checked again"
    assert st(seat, CI) == "failed"
