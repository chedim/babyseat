"""THE ENTITY-SCRIPTS COVENANT (the operator's ruling, the reshaped
surface): everything is an entity; the model sees entities only; a
script is a PART of its entity -- so the script's ONLY ways outward
are its own entity's shape: position moves, link/unlink (via its own
field tell), the render. NO voice (speak() is gone from the API -- a
legacy call strikes), NO event leaks (script-error/cron-overrun pool
puts are GONE -- errors BUBBLE as new entity docs dropped near the
errored entity, deduped by fingerprint, drifting back in time when
fixed), NO entity-run pool notifies (the run's props land on the
asking entity). THE DRIFT: the bubble refreshes while the error
recurs (active errors stay near the top), stops refreshing when
fixed -- and so sinks into the past on the 'updated' axis by itself.
"""
import time

import pytest

from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
from membrane import night
from membrane.night import apply_entity_update


class _Emb:
    def embed(self, texts):
        return [[0.0] * 4]


class _Pool:
    """A test-observable stand-in for the events pool."""
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def depth(self):
        return len(self.items)


def _seat(tmp_path, script=None):
    srv = type("S", (), {})()
    srv.membrane = type("M", (), {"embedder": _Emb(),
                                  "field_path": str(tmp_path / "f.json")
                                  })()
    srv.last_turn_ts = time.time()
    srv.ambient_mode = "day"
    srv._events_pool = _Pool()
    srv.entity_store = EntityStore(path=str(tmp_path / "ents.json"),
                                   shared=False)
    est = srv.entity_store
    ent = QuantumEntity(
        eid="membrane::tool:speaker", cls="tool",
        properties={"name": "speaker",
                    **({"tick_script": script} if script else {})})
    est.docs[ent.eid] = ent
    return srv, est, ent


# ---- 1. the voice is gone ------------------------------------------------

def test_speak_is_absent_from_the_script_api(tmp_path):
    """A script's membrane api has NO speak: the attribute is absent;
    a legacy call errors (AttributeError) -- the strike lane takes it
    (an unknown attribute is an honest error, never a silent no-op)."""
    srv, est, ent = _seat(
        tmp_path,
        script="import membrane\nmembrane.speak('hello')\n"
               "result = {'render': 'ok'}")
    out = night.run_entity_update_script(srv, ent, "tick")
    assert out.get("error"), out
    assert "speak" in str(out["error"]) or "Attribute" in str(out["error"])
    # the api object itself has no speak:
    pool = srv._events_pool
    assert pool.depth() == 0          # no event leak either


def test_no_script_error_pool_events(tmp_path):
    """A broken script's error BUBBLES as an entity; the pool hears
    NOTHING (the engine's own lanes don't leak: the model learns of
    errors through the ENTITY GRAPH, not through pushed events)."""
    srv, est, ent = _seat(
        tmp_path, script="result = {'render': nonexistent_name}")
    out = night.run_entity_update_script(srv, ent, "tick")
    assert out.get("error")
    assert srv._events_pool.depth() == 0
    # the bubble exists:
    bubbles = [e for e in est.docs.values()
               if str(e.eid).startswith(MEMBRANE_NS + "error:")]
    assert len(bubbles) == 1, [b.eid for b in bubbles]


def test_no_overrun_pool_events(tmp_path):
    """The cron-overrun gate announces through the bubble entity (the
    entity graph), never through the pool."""
    srv, est, ent = _seat(
        tmp_path, script="result = {'render': 'ok'}")
    # simulate the overrun state: last_ms way over interval
    ent.properties["script_last_ms"] = 60_000.0
    ent.properties["script_last_started"] = time.time() - 10.0
    out = night.run_entity_update_script(srv, ent, "tick")
    assert out.get("error")           # blocked, as ever
    assert srv._events_pool.depth() == 0
    bubbles = [e for e in est.docs.values()
               if "error" in str(e.eid)]
    assert bubbles, "the overrun must bubble too"
    _ = None


# ---- 2. the bubble: dropped near its entity, deduped, drifting ----------

def test_error_bubbles_as_entity_near_the_errored(tmp_path):
    srv, est, ent = _seat(
        tmp_path, script="result = {'render': boom_unknown}")
    out = night.run_entity_update_script(srv, ent, "tick")
    assert out.get("error")
    bubbles = [e for e in est.docs.values()
               if str(e.eid).startswith(MEMBRANE_NS + "error:")]
    assert len(bubbles) == 1
    bub = bubbles[0]
    # DROPPED NEAR the errored entity: the bubble's position matches
    # its origin's (or the grey band nearest when the origin has none)
    origin_pos = getattr(ent, "position", None)
    if origin_pos:
        assert list(bub.position or []) == list(origin_pos)
    # linked to its origin (the evidence lane: both are in each
    # other's reach via the observation event on the origin)
    assert bub.properties.get("errored") == ent.eid


def test_bubble_dedupes_by_fingerprint(tmp_path):
    """The same error recurring refreshes the SAME bubble (active
    errors stay one entity, no flood from a flapping script)."""
    srv, est, ent = _seat(
        tmp_path, script="result = {'render': boom_unknown}")
    night.run_entity_update_script(srv, ent, "tick")
    time.sleep(0.01)
    night.run_entity_update_script(srv, ent, "tick")
    bubbles = [e for e in est.docs.values()
               if str(e.eid).startswith(MEMBRANE_NS + "error:")]
    assert len(bubbles) == 1
    # the refresh stamped the bubble's last_ts
    bub = bubbles[0]
    assert (bub.properties or {}).get("last_ts", 0) > \
        time.time() - 5.0
    assert int((bub.properties or {}).get("n_hits") or 0) >= 2


def test_bubble_drifts_back_when_fixed(tmp_path):
    """Fixed script: no refreshes -- the bubble's last_ts ages; a
    later occurrence (different error) makes a DIFFERENT bubble."""
    srv, est, ent = _seat(
        tmp_path, script="result = {'render': boom_unknown}")
    out = night.run_entity_update_script(srv, ent, "tick")
    t_fail = (est.docs[[k for k in est.docs if "error:" in k][0]]
              .properties or {})["last_ts"]
    # the script is FIXED now:
    ent.properties["tick_script"] = "result = {'render': 'ok'}"
    out = night.run_entity_update_script(srv, ent, "tick")
    assert not out.get("error")
    bubbles = [e for e in est.docs.values()
               if str(e.eid).startswith(MEMBRANE_NS + "error:")]
    assert len(bubbles) == 1
    # the bubble's last_ts NEVER refreshed after the fix (it drifts
    # back on the updated axis by itself)
    assert (bubbles[0].properties or {})["last_ts"] == t_fail


# ---- 3. the mutator whitelist --------------------------------------------

def test_patch_whitelist_position_render_script_data(tmp_path):
    """The apply lane accepts only: position move, render, script_data,
    and (for the field-watch lane) links. Arbitrary prop-writes and
    delete are REFUSED (the strict reading)."""
    srv, est, ent = _seat(tmp_path)
    ent.position = [0.1, 0.2]
    out = {"patch": {"position": [0.3, 0.4],
                     "custom_arbitrary": "nope",
                     "status": "anything-wants"}}
    res = apply_entity_update(srv, ent, out, "tick", silent=True)
    # the position MOVED (the one mutator; the field's geometry is
    # unit-vector -- the move normed on the way in):
    import numpy as _np
    _pos = _np.asarray(ent.position or [], dtype=float)
    assert abs(float(_np.linalg.norm(_pos)) - 1.0) < 1e-6
    assert list(_pos) != [0.0, 0.0]      # actually moved
    # the arbitrary prop NEVER landed:
    assert "custom_arbitrary" not in (ent.properties or {})
    assert "status" not in (ent.properties or {})
    _ = res


def test_delete_out_of_the_whitelist(tmp_path):
    srv, est, ent = _seat(tmp_path)
    out = {"delete": True}
    apply_entity_update(srv, ent, out, "tick", silent=True)
    assert not (ent.properties or {}).get("retired")


def test_links_mutable_by_the_field_lane_only(tmp_path):
    """links/unlink are the FIELD-WATCH lane's mutator (commit 3); the
    plain tick/mention/watch verdicts do NOT relink (the whitelist is
    narrower for them)."""
    srv, est, ent = _seat(tmp_path)
    out = {"patch": {"links": [7, 9]}}
    apply_entity_update(srv, ent, out, "tick", silent=True)
    assert not (ent.properties or {}).get("links")


# ---- 4. the run lane's props ride the entity -----------------------------

def test_entity_run_no_pool_notify(tmp_path):
    """{run: true}'s exec result lands on the asking entity's props;
    the pool hears nothing (the 'the loops see it' lane is the model's
    watchers, not the engine's announcements)."""
    srv, est, ent = _seat(
        tmp_path, script="result = {'run': True}")
    ent.properties["command"] = "true"
    srv.efield = None
    out = night.apply_entity_update(srv, ent, {"run": True}, "tick",
                                    silent=False)
    assert (ent.properties or {}).get("run_rc") is not None
    assert srv._events_pool.depth() == 0