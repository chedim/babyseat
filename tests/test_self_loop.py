"""#161 RUNGS 0 + 1: `self` is born off, can be switched on, and feels itself.

The skill ladder's first two rungs. Rung 0 (sleep and wake): a per-seat
switch, off by default; off runs nothing and erases nothing; a restart keeps
the mode and the record. Rung 1 (feel itself, the amoeba's problem): a
looped cliffhanger object `self` -- try / expect / notice -- whose only act
moves its own position on the field. The world pushes it while it moves (the
field's current: the live conversation's running context, which self does
not control), so where it meant to go and where it ended up differ. The gap
is the signal: rung 1 passes when experience -- expect accounting for the
push -- measurably shrinks it against not accounting for the push at all.
"""
import json
import math
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

import numpy as np  # noqa: E402

EID = MEMBRANE_NS + "self"
DIM = 16


def _dir(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


class _World:
    """The field's current, steerable by the test (None: a quiet seat)."""
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return None if self.cur is None else np.array(self.cur, dtype=float)


def _seat(d=None):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = _World(_dir(1))
    SL.bind(srv)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    if p is None or not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _cycles(srv, n, now=1000.0):
    """n cycles, each one guard interval apart (the loop's pace, faked)."""
    for i in range(n):
        SL.cycle(srv, now=now + i * SL.PACE_S)
    return now + n * SL.PACE_S


# ---- rung 0: born off; sleep and wake ---------------------------------------

def test_self_is_born_off_with_its_three_methods():
    srv = _seat()
    est = srv.entity_store
    assert EID in est.docs
    assert SL.mode(srv) == "off"
    names = {(d.properties or {}).get("method_name") for d in est.docs.values()
             if EID in ((d.properties or {}).get("method_of") or [])}
    assert {"try", "expect", "notice"} <= names


def test_binding_twice_does_not_duplicate_its_methods():
    srv = _seat()
    SL.bind(srv)
    est = srv.entity_store
    tries = [d for d in est.docs.values()
             if EID in ((d.properties or {}).get("method_of") or [])
             and (d.properties or {}).get("method_name") == "try"]
    assert len(tries) == 1


def test_off_runs_nothing():
    srv = _seat()
    assert SL.cycle(srv, now=1000.0) is None
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
    _cycles(srv, 3)
    before = len(_record(srv))
    pos = list(srv.entity_store.docs[EID].position)
    SL.set_mode(srv, "off")
    assert SL.cycle(srv, now=9999.0) is None
    assert len(_record(srv)) == before
    assert list(srv.entity_store.docs[EID].position) == pos


def test_a_restart_keeps_the_mode_and_the_record():
    d = tempfile.mkdtemp()
    srv = _seat(d)
    SL.set_mode(srv, "watch")
    _cycles(srv, 2)
    srv.entity_store.flush()
    srv2 = _seat(d)
    assert SL.mode(srv2) == "watch"
    assert [r["kind"] for r in _record(srv2)] == ["birth", "cycle", "cycle"]
    SL.set_mode(srv2, "on")
    assert [r["kind"] for r in _record(srv2)].count("birth") == 1


def test_a_paused_seat_runs_nothing():
    srv = _seat()
    SL.set_mode(srv, "on")
    srv.paused = True
    assert SL.cycle(srv, now=1000.0) is None
    assert [r["kind"] for r in _record(srv)] == ["birth"]


# ---- rung 1: feel itself (positional) ---------------------------------------

def test_one_cycle_moves_it_and_feels_the_push():
    srv = _seat()
    SL.set_mode(srv, "on")
    out = SL.cycle(srv, now=1000.0)
    assert out is not None
    rec = _record(srv)[-1]
    assert rec["kind"] == "cycle" and rec["act"] == "move"
    for k in ("step", "push", "gap", "naive"):
        assert isinstance(rec[k], float) and rec[k] >= 0.0
    assert rec["push"] > 0.0                 # the current pushed it
    assert rec["gap"] > 0.0                  # and it did not see that coming (no experience yet)
    assert rec["change"] == "world"
    pos = srv.entity_store.docs[EID].position
    assert pos is not None and len(pos) == DIM
    assert math.isclose(float(np.linalg.norm(pos)), 1.0, rel_tol=1e-6)


def test_experience_shrinks_the_gap_in_a_steady_current():
    srv = _seat()
    SL.set_mode(srv, "on")
    _cycles(srv, 40)
    cyc = [r for r in _record(srv) if r["kind"] == "cycle"]
    early = sum(r["gap"] for r in cyc[:3]) / 3
    late = sum(r["gap"] for r in cyc[-10:]) / 10
    assert late < early
    assert sum(r["gap"] for r in cyc[-20:]) < sum(r["naive"] for r in cyc[-20:])


def test_a_change_in_the_world_reopens_the_gap_and_experience_closes_it_again():
    srv = _seat()
    SL.set_mode(srv, "on")
    t = _cycles(srv, 60)
    settled = [r["gap"] for r in _record(srv) if r["kind"] == "cycle"][-10:]
    srv.efield.cur = _dir(2)                        # the conversation turned
    t = _cycles(srv, 1, now=t)
    surprise = _record(srv)[-1]["gap"]
    assert surprise > 3 * (sum(settled) / len(settled))
    _cycles(srv, 40, now=t)
    assert SL.status(srv)["skill"] >= SL.RUNG1_SKILL  # it learned the new current


def test_without_a_world_there_is_nothing_to_feel():
    srv = _seat()
    srv.efield = _World(None)
    SL.set_mode(srv, "on")
    assert SL.cycle(srv, now=1000.0) is None
    assert SL.status(srv)["waiting_on"] == "the world"
    assert [r["kind"] for r in _record(srv)] == ["birth"]


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


def test_its_methods_refuse_any_caller_but_its_loop():
    srv = _seat()
    est = srv.entity_store
    out = N.method_call(srv, est, None, None, EID, "try", {"seed": "7"})
    assert out["rc"] == 1
    assert "own loop" in str(out.get("head") or "") + str(out.get("tail") or "")
    assert est.docs[EID].position is None


def test_its_methods_can_be_looked_at():
    srv = _seat()
    out = N.method_inspect(srv.entity_store, EID, "try", {"seed": "7"})
    assert "self::try" in json.dumps(out)


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


# ---- the guard --------------------------------------------------------------

def test_the_guard_keeps_the_pace():
    srv = _seat()
    SL.set_mode(srv, "on")
    assert SL.cycle(srv, now=1000.0) is not None
    assert SL.cycle(srv, now=1000.0 + SL.PACE_S / 2) is None


def test_the_guard_caps_a_burst(monkeypatch):
    monkeypatch.setattr(SL, "PACE_S", 0.1)
    srv = _seat()
    SL.set_mode(srv, "on")
    ran = sum(1 for i in range(100) if SL.cycle(srv, now=1000.0 + i * 0.2) is not None)
    assert ran == SL.BURST_PER_MIN


def test_three_failed_cycles_hold_the_loop(monkeypatch):
    srv = _seat()
    SL.set_mode(srv, "on")
    monkeypatch.setattr(SL, "_try", lambda srv, seed: {"rc": 2, "text": "broken"})
    _cycles(srv, 3)
    assert SL.status(srv)["held"] is True
    assert SL.cycle(srv, now=99999.0) is None
    SL.set_mode(srv, "off")
    SL.set_mode(srv, "on")                                    # the next toggle clears the hold
    assert SL.status(srv)["held"] is False


# ---- the ladder and the exp bar ---------------------------------------------

def test_the_bar_fills_with_pushed_cycles_and_names_what_it_waits_on():
    srv = _seat()
    SL.set_mode(srv, "on")
    _cycles(srv, SL.RUNG1_CYCLES // 2)
    st = SL.status(srv)
    assert st["rung"] == 0
    assert 0.0 < st["bar"] <= 0.5 + 1e-9
    assert st["waiting_on"] in ("pushes", "skill")


def test_rung_one_opens_once_and_says_the_next_is_not_built():
    srv = _seat()
    SL.set_mode(srv, "on")
    t = _cycles(srv, SL.RUNG1_CYCLES * 2)
    _cycles(srv, 3, now=t)
    st = SL.status(srv)
    assert st["rung"] == 1
    assert st["bar"] == pytest.approx(1.0)
    assert st["skill"] >= SL.RUNG1_SKILL
    assert st["next"] is None
    assert len([r for r in _record(srv) if r["kind"] == "rung"]) == 1


def test_a_passed_rung_can_slip_and_stays_on_the_record():
    srv = _seat()
    SL.set_mode(srv, "on")
    t = _cycles(srv, SL.RUNG1_CYCLES * 2)
    assert SL.status(srv)["rung"] == 1
    for i in range(SL.RUNG1_CYCLES):            # the current turns every cycle: habit can't keep up
        srv.efield.cur = _dir(100 + i)
        SL.cycle(srv, now=t + i * SL.PACE_S)
    st = SL.status(srv)
    assert st["slipping"] is True
    assert st["bar"] < 1.0
    assert st["rung"] == 1
    assert st["waiting_on"] == "skill"


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


def test_the_panel_has_the_switch():
    js = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js").read_text()
    assert 'class="btn icon selfbtn"' in js
    assert "JSON.stringify({ self: next })" in js
    assert '["selfbtn", "self: off"]' in js
