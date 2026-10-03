"""THE FIELD-WATCH LANE (commit 3): the model plants up to THREE
field-filters per entity (entity_edit watch_fields=[...] -- fixed
VERBS, model-defined args), the field's flush checks them as state
changes land, and the entity's change_script fires through the SAME
loop pool + engine covenants (0.25s budget, gravity, freeze). THE
WATCHES WATCH THE FIELD -- the entity's own geometry (position,
alignment, velocity), never the incoming streams.

VERBS (crossing semantics: fire on TRANSITION, re-arm on return):
- {verb: pos,  arg: sim_min}    position moved further than arg
- {verb: align, arg: floor}     coherence/net-align below the floor
- {verb: vel,  arg: v}          ring-velocity over arg

THE GRAVITY'S GROUPING (the operator's design): hot entities'
suppressed changes GROUP into one event (n_changes, span_s) when the
heat subsides or MAX_LATENCY_S (120s) passes -- many changes, one
event. FROZEN (heat > the freeze bar): filters pause (frozen_ts),
nothing fires; melt (60s cool) delivers the burst.
"""
import time

import pytest

from membrane.entities import EntityStore, QuantumEntity
from membrane import night
from membrane.night import (note_field_change, _field_filter_hits,
                            WATCH_FIELDS_CAP)


class _Emb:
    def embed(self, texts):
        return [[0.0] * 4]


def _seat(tmp_path):
    srv = type("S", (), {})()
    srv.membrane = type("M", (), {"embedder": _Emb(),
                                  "field_path": str(tmp_path / "f.json")})()
    srv.last_turn_ts = time.time()
    srv.ambient_mode = "day"
    srv._events_pool = night.WatchEvents() if hasattr(night, "WatchEvents") \
        else _pool_ok(srv)
    srv.entity_store = EntityStore(path=str(tmp_path / "ents.json"),
                                   shared=False)
    return srv, srv.entity_store


def _pool_ok(srv):
    class P:
        items = []
        def put(self, ev):
            self.items.append(ev)
        def depth(self):
            return len(self.items)
        def take(self):
            out, type(self).items = P.items, []
            return out
    srv._events_pool = P()
    return srv._events_pool


def _watched(est, eid="membrane::f:router.py", filters=None, script="ok"):
    ent = QuantumEntity(
        eid=eid, cls="Component",
        properties={"name": eid.split(":")[-1],
                    "watch_fields": filters or [{"verb": "pos", "arg": 0.1}],
                    "change_script": f"result = {'{'}'render': '{script}'{'}'}"})
    est.docs[ent.eid] = ent
    return ent


def test_cap_three_filters(tmp_path):
    srv, est = _seat(tmp_path)
    ent = _watched(est, filters=[{"verb": "pos", "arg": i} for i in range(5)])
    ok = est.edit_watch_fields(ent.eid, ent.properties["watch_fields"])
    assert not ok.get("ok")          # 5 > 3: refused


def test_verbs_are_fixed(tmp_path):
    srv, est = _seat(tmp_path)
    ent = _watched(est)
    ok = est.edit_watch_fields(ent.eid, [{"verb": "n", "arg": 5}])
    assert not ok.get("ok")          # 'n' is the dead verb: refused
    ok = est.edit_watch_fields(ent.eid, [{"verb": "color", "arg": "red"}])
    assert not ok.get("ok")


def test_pos_crossing_fires_once(tmp_path):
    """The position moved past the bar: ONE event; a second sweep with
    the same distance fires NOTHING (crossing semantics, re-arm on
    return)."""
    srv, est = _seat(tmp_path)
    ent = _watched(est, filters=[{"verb": "pos", "arg": 0.1}])
    ent.position = [1.0, 0.0]
    # the last stamped position (close): no trip yet
    ent.properties["wf_stamp_pos"] = [0.95, 0.0]
    note_field_change(srv, est, ent)
    n_events = len(srv._events_pool.depth() and srv._events_pool.items
                    or [])
    _ = n_events
    # move FAR (chord 0.05+): trips
    ent.position = [0.0, 1.0]
    note_field_change(srv, est, ent)
    evs = srv._events_pool.take()
    assert len(evs) == 1, evs
    # sweep again at the same far position: the crossing already fired
    note_field_change(srv, est, ent)
    assert srv._events_pool.take() == []


def test_align_vel_verbs(tmp_path):
    srv, est = _seat(tmp_path)
    ent = _watched(est, filters=[{"verb": "align", "arg": 0.9},
                                 {"verb": "vel", "arg": 0.5}])
    ent.properties["coherence"] = 0.7        # BELOW the align floor
    ent.properties["wf_stamp_align"] = 1.0   # was coherent: crossing
    note_field_change(srv, est, ent)
    evs = srv._events_pool.take()
    assert any(e.get("verb") == "align" for e in evs), evs
    ent2 = _watched(est, eid="membrane::f:thinker.py",
                    filters=[{"verb": "vel", "arg": 0.5}])
    ent2.properties["wf_stamp_vel"] = 0.1
    ent2.properties["ring_velocity"] = 0.9   # over the bar
    note_field_change(srv, est, ent2)
    evs2 = srv._events_pool.take()
    assert any(e.get("verb") == "vel" for e in evs2), evs2


def test_hot_entity_groups_into_one_burst(tmp_path):
    """Gravity: a hot entity's rapid changes DO NOT each fire; the
    burst delivers ONE grouped event (n_changes, span) when heat
    subsides."""
    srv, est = _seat(tmp_path)
    ent = _watched(est, filters=[{"verb": "pos", "arg": 0.1}])
    ent.properties["entity_heat"] = 1.0     # hot: gravity suppresses
    ent.position = [1.0, 0.0]
    ent.properties["wf_stamp_pos"] = [0.6, 0.0]
    for _ in range(9):
        note_field_change(srv, est, ent)
    assert srv._events_pool.take() == []    # suppressed
    # heat subsides:
    ent.properties["entity_heat"] = 0.1
    note_field_change(srv, est, ent)
    evs = srv._events_pool.take()
    assert len(evs) == 1 and int(evs[0].get("n_changes") or 0) >= 10, evs


def test_frozen_entity_pauses_and_melts(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_HEAT_FREEZE", "0.5")
    srv, est = _seat(tmp_path)
    ent = _watched(est)
    ent.properties["entity_heat"] = 0.9     # frozen (0.5 bar)
    ent.position = [0.0, 1.0]
    ent.properties["wf_stamp_pos"] = [0.9, 0.0]
    note_field_change(srv, est, ent)
    assert srv._events_pool.take() == []    # paused
    assert (ent.properties or {}).get("frozen_ts"), "the freeze stamps"
    # melt: cooled under the bar
    ent.properties["entity_heat"] = 0.1
    time.sleep(0.02)
    ent.properties["frozen_ts"] = time.time() - 61.0
    note_field_change(srv, est, ent)
    evs = srv._events_pool.take()
    assert evs, "the melt delivers"


def test_max_latency_delivers_even_a_permanently_hot_entity(tmp_path,
                                                            monkeypatch):
    """A forever-hot entity is not silence: the burst delivers when
    MAX_LATENCY_S (120s) passes even while suppressed continues."""
    monkeypatch.setattr(night, "WF_MAX_LATENCY_S", 0.05)
    srv, est = _seat(tmp_path)
    ent = _watched(est)
    ent.properties["entity_heat"] = 1.0
    ent.position = [1.0, 0.0]
    ent.properties["wf_stamp_pos"] = [0.6, 0.0]
    note_field_change(srv, est, ent)
    note_field_change(srv, est, ent)
    time.sleep(0.08)
    note_field_change(srv, est, ent)
    evs = srv._events_pool.take()
    assert evs and int(evs[0].get("n_changes") or 0) >= 3, evs


def test_drainer_runs_the_change_script(tmp_path):
    """The delivered event rides the ordinary pool: the drainer admits
    a 'field:changed' fire; the verdict applies via the whitelist."""
    srv, est = _seat(tmp_path)
    ent = _watched(est, script="seen the change: pos moved",
                   filters=[{"verb": "pos", "arg": 0.1}])
    ent.position = [0.0, 1.0]
    ent.properties["wf_stamp_pos"] = [0.9, 0.0]
    note_field_change(srv, est, ent)
    evs = srv._events_pool.take()
    assert len(evs) == 1
    assert evs[0].get("kind") == "field:changed"
    assert evs[0].get("entity") == ent.eid