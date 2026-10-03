"""THE HARD BUDGET (the 146s-poke exhibit): a tick script that runs
100x over its 0.25s budget is ABANDONED (the fire returns an
over-budget error) and STRIKES (a slow-success counts -- 3 wedging
runs disable the script). The per-entity lock covenant holds."""
import json
import tempfile
import time

import pytest

from membrane.night import run_entity_update_script, _SCRIPT_TIMEOUTS
from membrane.entities import EntityStore, QuantumEntity


def _seat(tmp_path, script):
    srv = type("S", (), {})()
    srv.membrane = type("M", (), {})()

    class _Emb:
        def embed(self, texts):
            return [[0.0] * 4]
    srv.membrane.embedder = _Emb()
    srv.membrane.field_path = str(tmp_path / "f.json")
    srv.last_turn_ts = time.time()
    srv.ambient_mode = "day"
    st = EntityStore(path=str(tmp_path / "ents.json"), shared=False)
    srv.entity_store = st
    ent = QuantumEntity(eid="membrane::tool:sleeper", cls="tool",
                        properties={"name": "sleeper",
                                    "tick_script": script,
                                    "tick_every": 1})
    st.docs[ent.eid] = ent
    return srv, st, ent


def test_over_budget_tick_is_abandoned(tmp_path):
    """A script that sleeps 5s under the 0.25s budget: the fire returns
    fast (well under 5s), the error is the over-budget kind."""
    srv, st, ent = _seat(
        tmp_path, "import time\ntime.sleep(5.0)\nresult = {'render': 'ok'}")
    t0 = time.time()
    out = run_entity_update_script(srv, ent, "tick")
    el = time.time() - t0
    assert el < 4.0, f"the fire waited {el:.1f}s -- no abandon"
    assert "over-budget" in str(out.get("error"))


def test_slow_success_strikes(tmp_path):
    """No error, but 100x over budget: the strike lands (a wedging
    success is a wedge)."""
    import os
    from membrane.night import SCRIPT_MAX_FAILURES
    srv, st, ent = _seat(
        tmp_path, "import time\ntime.sleep(1.5)\nresult = {'render': 'slow but fine'}")
    for i in range(SCRIPT_MAX_FAILURES):
        out = run_entity_update_script(srv, ent, "tick")
        # each fire abandons at 0.25s (the script keeps sleeping out-of-band)
        assert "over-budget" in str(out.get("error")) or out.get("error"), out
        # wait for the abandoned worker to land before re-arming (the
        # per-entity lock would block the next fire otherwise)
        deadline = time.time() + 5.0
        while ent.properties.get("script_last_ms") is None and \
                time.time() < deadline:
            time.sleep(0.1)
    # 3 strikes -> disabled
    assert ent.properties.get("script_disabled") is True


def test_fast_tick_is_untouched(tmp_path):
    """A well-behaved script fires normally (no overrun, no strike)."""
    srv, st, ent = _seat(tmp_path, "result = {'render': 'clock 11:26'}")
    out = run_entity_update_script(srv, ent, "tick")
    assert out.get("render") == "clock 11:26"
    assert not out.get("error")
    assert ent.properties.get("script_failures") in (None, 0)


def test_budget_map_has_the_tick():
    assert _SCRIPT_TIMEOUTS.get("tick") == 0.25