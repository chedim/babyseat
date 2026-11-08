"""Issue #936: ~15 script errors a minute, none of them strike.
(1) a tick script that fails on every beat is struck and disabled;
(2) score's error count matches the strikes; (3) a stale
script_last_error ages out instead of lingering forever."""
import time

from membrane import score as SC
from membrane.night import run_entity_update_script, SCRIPT_MAX_FAILURES
from membrane.entities import EntityStore, QuantumEntity

SLOW = "import time\ntime.sleep(5.0)\nresult = {'render': 'ok'}"


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
    ent = QuantumEntity(eid="membrane::Component:stopgap", cls="Component",
                        properties={"name": "stopgap", "tick_script": script,
                                    "tick_every": 1})
    st.docs[ent.eid] = ent
    return srv, st, ent


def _drain_score():
    with SC._lock:
        SC._pending.clear()


def _pending_errors():
    with SC._lock:
        return sum(b.get("error", 0) for b in SC._pending.values())


def _beat_until_struck(srv, ent, beats):
    for _ in range(beats):
        run_entity_update_script(srv, ent, "tick")


def test_every_beat_failure_is_struck_and_disabled(tmp_path):
    srv, st, ent = _seat(tmp_path, SLOW)
    _beat_until_struck(srv, ent, SCRIPT_MAX_FAILURES + 1)
    p = ent.properties
    assert int(p.get("script_failures") or 0) >= SCRIPT_MAX_FAILURES
    assert p.get("script_disabled") is True
    assert p.get("script_disabled_by") == "strikes"


def test_score_error_count_matches_strikes(tmp_path):
    _drain_score()
    srv, st, ent = _seat(tmp_path, SLOW)
    _beat_until_struck(srv, ent, SCRIPT_MAX_FAILURES)
    strikes = int(ent.properties.get("script_failures") or 0)
    assert strikes >= 1
    assert _pending_errors() == strikes, (_pending_errors(), strikes)


def test_stale_last_error_ages_out(tmp_path):
    srv, st, ent = _seat(tmp_path, "result = {'render': 'ok'}")
    ent.properties["script_last_error"] = "over-budget: abandoned after 0.25s"
    ent.properties["script_last_error_ts"] = time.time() - 3 * 86400
    SC.bind(srv)
    SC.refresh(srv)
    assert not ent.properties.get("script_last_error")
