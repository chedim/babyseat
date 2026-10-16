"""#149: a planted tick script hit a SyntaxError, struck out in ~3 beats and
was auto-disabled FOREVER -- the thinker skipped it silently, the heartbeat
stayed off as if the seat were idle, and nothing kept saying why. A
strikes-disabled script now retries on a backoff (each failed probe
re-surfaces the error; a passing probe re-enables it), and the failing
state is part of the entity's read."""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS  # noqa: E402

BROKEN = "result = {'render': 'ok'\n"
FIXED = "result = {'render': 'ok'}\n"


def _seat(code=BROKEN):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    e = QuantumEntity(eid=MEMBRANE_NS + "tool:hb", cls="Component",
                      properties={"name": "hb", "tick_script": code})
    est.upsert(e)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None,
                                events_pool=None)
    return srv, est, e


def _strike_out(srv, e):
    for _ in range(N.SCRIPT_MAX_FAILURES):
        N.run_entity_update_script(srv, e, "tick")
    assert e.properties.get("script_disabled")


def _disabled_hits(est):
    return sum(int(d.properties.get("n_hits") or 0) for k, d in est.docs.items()
               if k.startswith(MEMBRANE_NS + "error:")
               and d.properties.get("kind") == "disabled")


def test_struck_out_script_is_held_until_its_retry():
    srv, est, e = _seat()
    _strike_out(srv, e)
    assert e.properties["script_retry_at"] > time.time()
    out = N.run_entity_update_script(srv, e, "tick")
    assert "disabled" in out["error"]
    assert "SyntaxError" in out["error"]               # the why rides the refusal
    assert N.script_held(e.properties, time.time())


def test_a_failed_probe_resurfaces_and_backs_off():
    srv, est, e = _seat()
    _strike_out(srv, e)
    first_wait = e.properties["script_retry_at"] - time.time()
    hits = _disabled_hits(est)
    e.properties["script_retry_at"] = time.time() - 1  # the retry is due
    assert not N.script_held(e.properties, time.time())
    out = N.run_entity_update_script(srv, e, "tick")
    assert "SyntaxError" in out["error"]               # the probe really ran
    assert e.properties.get("script_disabled")
    assert _disabled_hits(est) > hits                  # said again, not once
    assert e.properties["script_retry_at"] - time.time() > first_wait


def test_a_passing_probe_re_enables():
    srv, est, e = _seat()
    _strike_out(srv, e)
    e.properties["tick_script"] = FIXED
    e.properties["script_retry_at"] = time.time() - 1
    out = N.run_entity_update_script(srv, e, "tick")
    assert not out.get("error"), out
    for k in ("script_disabled", "script_retry_at", "script_failures"):
        assert k not in e.properties, k


def test_an_operator_kill_is_never_auto_retried():
    srv, est, e = _seat(FIXED)
    e.properties["script_disabled"] = True             # the operator's own switch
    assert N.script_held(e.properties, time.time() + 10 * 3600)


def test_the_failing_state_is_readable():
    srv, est, e = _seat()
    assert N.script_health(e.properties) is None
    N.run_entity_update_script(srv, e, "tick")
    h = N.script_health(e.properties)
    assert h["state"] == "failing" and h["failures"] == 1
    assert "SyntaxError" in h["last_error"]
    _strike_out(srv, e)
    h = N.script_health(e.properties)
    assert h["state"] == "disabled" and h["retry_at"] > time.time()


def test_the_thinker_retries_a_struck_out_heartbeat(tmp_path):
    """The issue's own path: the THINKER's tick pass. A heartbeat failing
    on a missing file strikes out, the beats skip it while held -- and once
    the file is back and the retry is due, the next beat fires it again and
    it is re-enabled (before: dead until someone edited it)."""
    from unittest import mock
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_night_mode import _FakeSrv, _thinker
    need = tmp_path / "sensor.txt"
    est = EntityStore(path=None, shared=False)
    e = QuantumEntity(eid=MEMBRANE_NS + "tool:heart", cls="tool",
                      properties={"name": "heart", "tick_every": 1,
                                  "tick_script": f"open({str(need)!r}).read()\n"
                                                 "result = {'render': 'beat'}"})
    est.docs[e.eid] = e
    srv = _FakeSrv(store=est)
    th = _thinker(srv, est)
    for _ in range(N.SCRIPT_MAX_FAILURES):
        th._tick_scripts()
        th._beat += 1
    assert e.properties.get("script_disabled")
    started = e.properties.get("script_last_started")
    th._tick_scripts()                                # held: the beat skips it
    assert e.properties.get("script_last_started") == started
    need.write_text("ok")                             # the cause heals ...
    later = time.time() + N.SCRIPT_RETRY_S + 1
    with mock.patch("time.time", return_value=later):
        th._tick_scripts()                            # ... and the retry is due
    assert not e.properties.get("script_disabled"), e.properties
    assert th.stats["ticks"] == 1
