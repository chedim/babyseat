"""#343: Blurp's learner steps run back to back, throttled to a CPU share.

The wait after a step is the step's own wall duration x (1 / share - 1):
at the default 50% it waits as long as the step took, at 100% it runs back
to back. The share is a seat setting (`self_cpu_share` in <data dir>/
settings.json, 0 < share <= 1; the options window, #344, writes it). The
switch still wakes the loop at once. #576: the steps are the learner hook's
(self_loop.learn_step); the old cycle retired.
"""
import json
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv, d


# ---- the share's arithmetic ---------------------------------------------------------

def test_the_wait_is_the_cycles_own_duration_times_one_over_share_minus_one():
    assert SL.share_wait(0.2, 0.5) == pytest.approx(0.2)        # 50%: as long as the cycle took
    assert SL.share_wait(0.2, 1.0) == 0.0                       # 100%: back to back
    assert SL.share_wait(0.2, 0.25) == pytest.approx(0.6)       # 25%: three times as long
    assert SL.share_wait(0.0, 0.5) == 0.0
    assert SL.share_wait(-1.0, 0.5) == 0.0                      # a clock step back is no wait


def _share(d, v):
    """The seat's settings.json, as the options window (#344) writes it."""
    (d / "settings.json").write_text(json.dumps({"name": "x", "self_cpu_share": v}))


def test_the_share_defaults_to_half_and_is_read_from_the_seats_settings():
    srv, d = _seat()
    assert SL.cpu_share(srv) == 0.5                              # no file
    _share(d, 0.25)
    assert SL.cpu_share(srv) == 0.25                             # read each time: no restart
    _share(d, 1)
    assert SL.cpu_share(srv) == 1.0                              # 100% is allowed
    assert not (d / "self" / "settings.json").exists() or "self_cpu_share" not in \
        json.loads((d / "self" / "settings.json").read_text())  # never Blurp's own settings (#320)


@pytest.mark.parametrize("bad", [0, 0.0, -0.5, 1.01, 2, float("nan"), float("inf"), "x", None, True, [0.5]])
def test_a_share_outside_zero_to_one_reads_as_the_default(bad):
    srv, d = _seat()
    _share(d, bad)
    assert SL.cpu_share(srv) == 0.5


def test_a_broken_file_reads_as_the_default():
    srv, d = _seat()
    (d / "settings.json").write_text("{not json")
    assert SL.cpu_share(srv) == 0.5
    (d / "settings.json").write_text("[1, 2]")
    assert SL.cpu_share(srv) == 0.5


# ---- the loop ----------------------------------------------------------------------------

def _loop_with(monkeypatch, srv, dur, mode="on"):
    """The real thread over a fake learner step that takes `dur` s; -> its start times."""
    starts = []
    rt = SL._rt(srv)

    def fake(srv_, now=None):
        starts.append(time.monotonic())
        time.sleep(dur)
        rt["wake"].set()                                         # a learner that changed something
        return {"kind": "learn", "dur_s": dur}
    monkeypatch.setattr(SL, "learn_step", fake)
    SL.set_mode(srv, mode)
    SL.start(srv)
    return starts


def _gaps(starts, k):
    deadline = time.monotonic() + 10
    while len(starts) < k and time.monotonic() < deadline:
        time.sleep(0.01)
    return [b - a for a, b in zip(starts, starts[1:k])]


def test_the_loop_waits_as_long_as_the_cycle_took_at_half(monkeypatch):
    srv, d = _seat()
    starts = _loop_with(monkeypatch, srv, 0.1)
    try:
        gaps = _gaps(starts, 5)
    finally:
        SL.set_mode(srv, "off")
    assert len(gaps) == 4
    assert all(g >= 0.19 for g in gaps), gaps                    # 0.1 s of work + 0.1 s of wait


def test_at_full_share_it_runs_back_to_back(monkeypatch):
    srv, d = _seat()
    _share(d, 1.0)
    starts = _loop_with(monkeypatch, srv, 0.1)
    try:
        gaps = _gaps(starts, 5)
    finally:
        SL.set_mode(srv, "off")
    assert len(gaps) == 4
    assert sum(gaps) / len(gaps) < 0.18, gaps                    # no wait after the work


def test_a_lower_share_waits_longer(monkeypatch):
    srv, d = _seat()
    _share(d, 0.25)
    starts = _loop_with(monkeypatch, srv, 0.05)
    try:
        gaps = _gaps(starts, 4)
    finally:
        SL.set_mode(srv, "off")
    assert len(gaps) == 3
    assert all(g >= 0.19 for g in gaps), gaps                    # 0.05 s + 3 x 0.05 s


def test_the_switch_wakes_it_at_once_and_off_stops_it(monkeypatch):
    srv, d = _seat()
    starts = _loop_with(monkeypatch, srv, 0.02, mode="off")
    time.sleep(0.2)
    assert starts == []                                          # off runs nothing
    t = time.monotonic()
    SL.set_mode(srv, "on")
    deadline = t + 3
    while not starts and time.monotonic() < deadline:
        time.sleep(0.005)
    assert starts and starts[0] - t < 1.0                        # not a PACE_S poll later
    SL.set_mode(srv, "off")
    time.sleep(0.1)
    n = len(starts)
    time.sleep(0.3)
    assert len(starts) == n                                      # off: no more cycles


# ---- the pace is the throttle x energy (#345's energy.share): a tired Blurp slows ---------

def _energy(srv, e):
    from membrane import energy as E
    E._state(srv)["energy"] = e


@pytest.fixture
def no_ram(monkeypatch):
    from membrane import energy as E
    monkeypatch.setattr(E, "_avail_ram", lambda: None)          # no test reads the machine's RAM


def test_the_pace_share_is_the_throttle_times_energy(no_ram):
    srv, d = _seat()
    assert SL.pace_share(srv) == pytest.approx(0.5)              # rested, the default throttle
    _energy(srv, 0.5)
    assert SL.pace_share(srv) == pytest.approx(0.25)
    _share(d, 1.0)
    assert SL.pace_share(srv) == pytest.approx(0.5)
    assert SL.pace_wait(srv, 0.1) == pytest.approx(0.1)          # 0.1 x (1 / 0.5 - 1)
    _share(d, 0.5)
    assert SL.pace_wait(srv, 0.1) == pytest.approx(0.3)          # 0.1 x (1 / 0.25 - 1)


def test_a_share_of_zero_is_no_wait_to_count_and_no_division(no_ram):
    srv, d = _seat()
    _energy(srv, 0.0)
    assert SL.pace_share(srv) == 0.0
    assert SL.pace_wait(srv, 0.1) is None                        # no next cycle on a timer: the rest tick


def test_a_tired_blurp_waits_longer_in_the_loop(monkeypatch, no_ram):
    srv, d = _seat()
    _energy(srv, 0.1)                                            # 0.5 x 0.1 = 5%: 19 x the cycle
    starts = _loop_with(monkeypatch, srv, 0.02)
    try:
        gaps = _gaps(starts, 3)
    finally:
        SL.set_mode(srv, "off")
    assert len(gaps) == 2
    assert all(g >= 0.3 for g in gaps), gaps                     # 0.02 s + 19 x 0.02 s, not 0.02 + 0.02


def test_at_share_zero_the_loop_waits_for_the_rest_tick_not_a_busy_loop(monkeypatch, no_ram):
    from membrane import energy as E
    srv, d = _seat()
    monkeypatch.setattr(E, "share", lambda throttle, srv_: 0.0)  # awake with nothing left to spend
    monkeypatch.setattr(SL, "PACE_S", 0.2)
    starts = _loop_with(monkeypatch, srv, 0.01)
    try:
        gaps = _gaps(starts, 4)
    finally:
        SL.set_mode(srv, "off")
    assert len(gaps) == 3
    assert all(g >= 0.19 for g in gaps), gaps                    # one cycle per rest tick (PACE_S)


def test_asleep_the_loop_runs_no_cycle(monkeypatch, no_ram):
    from membrane import energy as E
    srv, d = _seat()
    E._state(srv).update(asleep=True, energy=0.0)                # asleep and spent: it rests, no cycle
    starts = _loop_with(monkeypatch, srv, 0.01)
    time.sleep(0.3)
    SL.set_mode(srv, "off")
    assert starts == []


# ---- one source for the share: options.cpu_share(srv), the loop and the record cap read it ----

@pytest.mark.parametrize("v", [0.25, 1, 0.01, 0, 2, "x", None, True])
def test_both_readers_agree_when_the_settings_sit_beside_self_and_the_data_dir_points_there(monkeypatch, v):
    from membrane import options as O
    srv, d = _seat()
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(d))
    _share(d, v)
    assert SL.cpu_share(srv) == O.cpu_share() == O.cpu_share(srv)


def test_the_loops_share_is_the_options_share(monkeypatch):
    from membrane import options as O
    srv, d = _seat()
    seen = []
    monkeypatch.setattr(O, "cpu_share", lambda srv_=None: seen.append(srv_) or 0.125)
    assert SL.cpu_share(srv) == 0.125 and seen == [srv]          # one source: the loop delegates


def test_the_record_cap_reads_the_seats_share_not_another_data_dir(monkeypatch):
    from membrane import energy as E
    srv, d = _seat()
    other = Path(tempfile.mkdtemp())
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(other))          # a data dir that is not this seat's
    _share(other, 1.0)
    _share(d, 0.25)
    assert E._record_cap(1000, srv) == 250.0                     # the seat's own settings beside self/


