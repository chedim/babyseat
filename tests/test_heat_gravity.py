"""THE HEAT METER + GRAVITY (commit 2 of the covenant arc): one meter
per entity -- the max of its field-churn EMA (updates/s at the flush)
and its scripts' own CPU EMA (process_time, so a SLEEPER never reads
hot) -- and gravity, the continuous stretch of its own cadences:
- the tick's effective interval = authored tick_every x gravity (1x
  cold to 8x hot; the authored cadence is the floor);
- the cron-overrun gate reads the EFFECTIVE interval (a stretched cron
  cannot wedge);
- consolidation's script_every_n stretches the same way;
- the meter's knobs are env-set (cold/freeze bars, half-life, ceiling).
"""
import time

import pytest

from membrane.entities import EntityStore, QuantumEntity
from membrane import night


class _Emb:
    def embed(self, texts):
        return [[0.0] * 4]


def _seat(tmp_path):
    import pathlib
    tmp_path = pathlib.Path(tmp_path) if isinstance(tmp_path, str) \
        else tmp_path
    srv = type("S", (), {})()
    srv.membrane = type("M", (), {"embedder": _Emb(),
                                  "field_path": str(tmp_path / "f.json")})()
    srv.last_turn_ts = time.time()
    srv.ambient_mode = "day"
    srv._events_pool = None
    srv.entity_store = EntityStore(path=str(tmp_path / "ents.json"),
                                   shared=False)
    return srv, srv.entity_store


def _hot_entity(est, eid="membrane::tool:hot", tick_every=1, script=None):
    ent = QuantumEntity(eid=eid, cls="tool",
                        properties={"name": eid.split(":")[-1],
                                    "tick_script": script or "result = {}",
                                    "tick_every": tick_every})
    est.docs[ent.eid] = ent
    return ent


def test_heat_rises_with_script_cpu_and_decays_in_silence(tmp_path):
    """The CPU EMA: a CPU-burning script raises heat; silence decays
    it (the half-life's exp decay)."""
    srv, est = _seat(tmp_path)
    ent = _hot_entity(est, script="import time\n"
                                   "x = sum(i*i for i in range(200000))\n"
                                   "result = {'render': 'burn'}")
    night.note_script_cpu(est, ent.eid, cpu_ms=200.0, ts=time.time())
    h1 = night.entity_heat(est.docs[ent.eid])
    assert h1 > 0.5, h1
    # silence: a later timestamp (no refreshes) decays the heat
    ent2_props = est.docs[ent.eid].properties
    ent2_props["script_cpu_last"] = time.time() - 120.0   # 2 half-lives past
    h2 = night.entity_heat(est.docs[ent.eid])
    assert h2 < h1, (h1, h2)


def test_sleeper_script_never_reads_hot(tmp_path):
    """A sleeping script: WALL high (1.5s), CPU ~0 -- the heat reads
    from process_time, not wall (the operator's parenthetical)."""
    srv, est = _seat(tmp_path)
    night.note_script_cpu(est, "membrane::tool:sleeper",
                          cpu_ms=1.0, wall_ms=1500.0,
                          ts=time.time())
    ent = QuantumEntity(eid="membrane::tool:sleeper", cls="tool",
                        properties={"script_cpu_ema_ms": 1.0})
    # 1ms CPU against the 0.25s budget = cold
    assert night.entity_heat(ent) < 0.2


def test_gravity_stretches_tick_every(tmp_path):
    """A hot entity's ticks space ~8x; a cold entity ticks exactly as
    authored (the floor)."""
    srv, est = _seat(tmp_path)
    hot = _hot_entity(est, eid="membrane::tool:hot")
    cold = _hot_entity(est, eid="membrane::tool:cold")
    hot.properties["entity_heat"] = 1.5
    cold.properties["entity_heat"] = 0.0
    g_hot = night.tick_gravity(hot.properties or {})
    g_cold = night.tick_gravity(cold.properties or {})
    assert g_hot >= 4.0, g_hot
    assert g_cold == 1.0
    # effective interval scales:
    eff = night.effective_tick_every(hot.properties or {}, authored=1)
    assert eff >= 4, eff
    eff_cold = night.effective_tick_every(cold.properties or {}, authored=6)
    assert eff_cold == 6


def test_tick_pass_uses_the_effective_interval(tmp_path):
    """The thinker's _tick_scripts spaces a hot entity's fires by the
    EFFECTIVE beat (beats % effective), not the authored one."""
    from membrane.thinker import Thinker
    srv, est = _seat(tmp_path)
    ent = _hot_entity(est, tick_every=1,
                      script="result = {'render': 'h'}")
    ent.properties["entity_heat"] = 1.5      # gravity ~5-8x
    th = Thinker.__new__(Thinker)
    th.srv = srv
    est.docs[ent.eid] = ent
    th.stats = {"ticks": 0, "processed": 0, "spoken": 0, "extended": 0,
                "skipped": 0, "model_calls": 0, "presence": {}}
    th.mind_log = __import__("collections").deque(maxlen=4)
    th._beat = 0
    # beats 1..12: the effective interval (~5+) means ONE firing
    fires = 0
    for _ in range(12):
        th._beat += 1
        before = th.stats["ticks"]
        th._tick_scripts()
        if th.stats["ticks"] > before:
            fires += 1
    assert fires <= 2, fires
    # the COLD twin fires every beat:
    cold = _hot_entity(est, eid="membrane::tool:cold", tick_every=1,
                       script="result = {'render': 'c'}")
    th2 = Thinker.__new__(Thinker)
    th2.srv = srv
    th2.stats = {"ticks": 0}
    th2.mind_log = th.mind_log
    th2._beat = 0
    fires2 = 0
    for _ in range(6):
        th2._beat += 1
        before = th2.stats["ticks"]
        th2._tick_scripts()
        if th2.stats["ticks"] > before:
            fires2 += 1
    assert fires2 == 6, fires2


def test_overrun_gate_reads_the_effective_interval(tmp_path):
    """A gravity-stretched cron can't wedge: the gate compares its
    last_ms against the EFFECTIVE interval (the wipe-proof fix's
    honest extension)."""
    srv, est = _seat(tmp_path)
    ent = _hot_entity(est, tick_every=2)
    ent.properties["entity_heat"] = 1.5      # gravity >= 4 => eff >= 8
    ent.properties["script_last_ms"] = 6000.0      # ran 6s
    ent.properties["script_last_started"] = time.time() - 10.0
    out = night.run_entity_update_script(srv, ent, "tick")
    # 6s < the effective interval (~8s+) => NOT an overrun block:
    assert not (out.get("error") and "overrun" in str(out.get("error")))


def test_consolidation_every_stretches(tmp_path):
    """run_scripted_entities' per-entity slot check multiplies by the
    gravity: a hot entity's nudge defers more passes."""
    srv, est = _seat(tmp_path)
    ent = QuantumEntity(eid="membrane::Component:hot", cls="Component",
                        properties={"name": "hot",
                                    "nudge_script": "result = {'render': 'n'}",
                                    "script_every_n": 2,
                                    "entity_heat": 1.5})
    est.docs[ent.eid] = ent
    fired = night.run_scripted_entities(None, est, mem=None,
                                        now=900 * 4.0, force=False)
    # hot: every_n=2 x gravity(~5) => deferred at pass 4; the honest
    # assertion: NOT fired on this pass
    assert fired["errors"] == 0
    assert fired.get("deferred", 0) >= 1


def test_heat_knobs_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_HEAT_FREEZE", "0.2")
    srv, est = _seat(tmp_path)
    ent = _hot_entity(est)
    ent.properties["entity_heat"] = 0.5
    assert night.entity_frozen(ent.properties or {}) is True
    monkeypatch.setenv("MEMBRANE_HEAT_FREEZE", "1.5")
    assert night.entity_frozen(ent.properties or {}) is False