"""#161: `self` (Blurp) is born off and can be switched on.

A per-seat switch, off by default; off runs nothing and erases nothing; a
restart keeps the mode and the record. #576 retired the old learner (the
try / expect / notice cycle and its ladder): the loop's step runs the
learner hook (self_loop.learn_step), which learns nothing until #576 D2.
The field's current, the store's guard on self's state and the panel's
switch are tested here too.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

import numpy as np  # noqa: E402

EID = MEMBRANE_NS + "self"


def _seat(d=None):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    if p is None or not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _steps(srv, n, now=1000.0):
    """n steps of the loop, PACE_S apart (the loop's rest tick, faked)."""
    for i in range(n):
        SL.step(srv, now=now + i * SL.PACE_S)


# ---- born off; sleep and wake ---------------------------------------------------

def test_self_is_born_off_with_no_methods():
    srv = _seat()
    est = srv.entity_store
    assert EID in est.docs
    assert SL.mode(srv) == "off"
    assert not [d for d in est.docs.values() if EID in ((d.properties or {}).get("method_of") or [])]


def test_off_runs_nothing():
    srv = _seat()
    assert SL.step(srv, now=1000.0) is None
    assert _record(srv) == []
    assert srv.entity_store.docs[EID].position is None


def test_switching_on_writes_the_birth_once():
    srv = _seat()
    SL.set_mode(srv, "on")
    SL.set_mode(srv, "off")
    SL.set_mode(srv, "on")
    births = [r for r in _record(srv) if r["kind"] == "birth"]
    assert len(births) == 1


def test_a_bad_mode_is_refused():
    srv = _seat()
    assert SL.set_mode(srv, "awake") is False
    assert SL.mode(srv) == "off"


def test_off_is_sleep_not_death():
    srv = _seat()
    SL.set_mode(srv, "on")
    _steps(srv, 3)
    before = len(_record(srv))
    SL.set_mode(srv, "off")
    assert SL.step(srv, now=9999.0) is None
    assert len(_record(srv)) == before


def test_a_restart_keeps_the_mode_and_the_record():
    d = tempfile.mkdtemp()
    srv = _seat(d)
    SL.set_mode(srv, "watch")
    _steps(srv, 2)
    srv.entity_store.flush()
    srv2 = _seat(d)
    assert SL.mode(srv2) == "watch"
    assert [r["kind"] for r in _record(srv2)] == ["birth"]
    SL.set_mode(srv2, "on")
    assert [r["kind"] for r in _record(srv2)].count("birth") == 1


def test_a_paused_seat_runs_nothing(monkeypatch):
    srv = _seat()
    SL.set_mode(srv, "on")
    srv.paused = True
    ran = []
    monkeypatch.setattr(SL, "learn_step", lambda s, now: ran.append(now))
    assert SL.step(srv, now=1000.0) is None
    assert ran == []
    assert [r["kind"] for r in _record(srv)] == ["birth"]


# ---- the field's current --------------------------------------------------------


def _talk(f, session, text, ts):
    f.write_events([ST.Event(stream="discourse", role="user", text=text, ts=ts)], session=session)
    f.process()


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def test_the_field_names_its_current():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    assert f.current() is None
    _talk(f, "s1", "the boat needs a new battery", 1000.0)
    assert np.allclose(f.current(), f.now("s1"))     # one stream: the current is its context


def test_every_active_stream_pulls_at_once():
    # the combined current: two live sessions pull together (their sum), not
    # whichever wrote last -- a write no longer flips the world
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    _talk(f, "s1", "the boat needs a new battery", 1000.0)
    _talk(f, "s2", "what time does the ferry leave", 1001.0)
    cur = f.current()
    assert float(cur @ _unit(f.now("s1"))) > 0.3
    assert float(cur @ _unit(f.now("s2"))) > 0.3
    assert not np.allclose(cur, _unit(f.now("s2")))


def test_a_quiet_stream_fades_out_of_the_current():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    _talk(f, "s1", "the boat needs a new battery", 1000.0)
    _talk(f, "s2", "what time does the ferry leave", 1001.0)
    t = f._now_ts["s2"]
    f._now_ts["s1"] = t - 30 * 60                     # s1 went quiet half an hour ago
    assert float(f.current(now=t) @ _unit(f.now("s2"))) > 0.99


def test_one_machinery_event_cannot_flip_the_current():
    # the live seat's flip (#161, 2026-10-16): a getter reading in its own
    # session took the whole current. Now the busy conversation still leads.
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    for i, line in enumerate(["the boat needs a new battery", "which battery fits the boat",
                              "a lithium battery for the boat", "the boat battery is dying"]):
        _talk(f, "chat", line, 1000.0 + i)
    before = f.current()
    _talk(f, "readings", "pump pressure 3.1 bar", 1005.0)
    after = f.current()
    assert float(after @ before) > float(after @ _unit(f.now("readings")))


def test_the_model_cannot_write_its_state():
    srv = _seat()
    srv.entity_store.edit(EID, properties={"script_data": {"a": 1}})
    assert not (srv.entity_store.docs[EID].properties or {}).get("script_data")


def test_mentions_never_move_it():
    from membrane.efield import wire_store_names
    srv = _seat()                                   # the store holds membrane::self, as on a live seat
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    wire_store_names(f, srv.entity_store)
    # a one-segment m: name is junk to the field (eids.is_junk_tag), so even
    # with membrane::self in the store no mention gives it a state: this pins
    # that, so a later change to the junk rule can't let talk move self
    f.write_events([ST.Event(stream="observation", tool="read", args={"path": "x"},
                             result_head="self looked at itself", outcome="ok",
                             ts=1000.0, subject="m:self")], session="s1")
    f.process()
    f.flush()
    assert f.get("m:self") is None
    assert not [d for _, d in f.b.scan("entity_deltas") if d.get("eid") == "m:self"]


# ---- the operator's switch on the seat panel --------------------------------

def test_the_seat_panel_switches_it():
    srv = _seat()
    out = SP.control(srv, {"self": "on"})
    assert out["applied"]["self"] == "on"
    assert SL.mode(srv) == "on"
    assert SP.state(srv)["self"]["mode"] == "on"
    ev = srv.entity_store.docs[MEMBRANE_NS + "presence"].properties["last_event"]
    assert ev["kind"] == "self" and ev["value"] == "on"


def test_the_seat_panel_ignores_a_bad_value():
    srv = _seat()
    out = SP.control(srv, {"self": "awake"})
    assert "self" not in out["applied"]
    assert SL.mode(srv) == "off"


def test_the_panel_has_the_sleep_toggle_not_the_chooser():      # #299: the 🌱 chooser gave way to acceptance
    js = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js").read_text()
    assert 'class="selfpick"' not in js
    assert 'class="btn icon selfsleep"' in js
    assert '["selfsleep", SELF_NAME + ": off"]' in js            # #460: the on/off switch
    assert "selfbtn" not in js
