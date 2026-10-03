"""THE NIGHT MODE (the operator's ask, the animal covenant): the seat's
ambient mind slows its body when the room goes quiet and wakes on
signal -- ears open, body at rest.

- enter: room idle >= NIGHT_IDLE_S (60s), no pending events -> 'night';
- wake: any turn arrival / watch fire / script error flips 'day';
- the thinker's pulse: 1.0s day -> NIGHT_PULSE_S (30s) night;
- tick scripts PAUSE in night (the body stops; the wakeup lanes stay);
- wakeup signals (script-error, peer-miss, watch-attached/-detached)
  both WAKE and run (a fire at night is worth one beat);
- speech holds at night (no new composed lines except alert kinds);
- the capture/codegraph mtime sweep stretches (day cycle x NIGHT_SWEEP_X);
- the banner + the mind rows carry the mode;
- --no-night holds day forever; an explicitly-authored tick script can
  flag 'wakes: true' to keep its ear open at night (the animal's whisker).
"""
import time

import pytest


class _FakeStop:
    """stop event stub: wait() returns False (alive), is_set False."""
    def is_set(self):
        return False

    def wait(self, t):
        return False


class _M:
    """The membrane sliver the script lanes touch."""
    def __init__(self):
        self.embedder = None
        self.field_path = "/tmp/fake-field.json"
        self.audit_path = "/tmp/fake-audit.jsonl"


class _FakeSrv:
    """The sliver of the seat the ambient lanes read."""
    def __init__(self, store=None, efield=None):
        self.entity_store = store
        import tempfile as _tf
        self.membrane = _M()
        self.efield = efield
        self.last_turn_ts = time.time() - 10_000.0   # long idle
        self.loops = None
        self.events_pool = None
        self.ambient_mode = "day"
        self._speak_queue = []
        self._act_table = None
        self._cg_swept = 0.0
        self._boot_greeting_pending = False
        self.ambient_pulse = (1.0, 30.0)             # (day, night)
        self.ambient_night = True

    def note_wakeup(self, why):
        if not hasattr(self, "_wakeups"):
            self._wakeups = []
        self._wakeups.append(why)


def _thinker(srv, store):
    from membrane.thinker import Thinker
    th = Thinker.__new__(Thinker)                # no thread boot
    th.srv = srv
    th.queue = __import__("collections", fromlist=["deque"]).deque(maxlen=8)
    th.stats = {"processed": 0, "spoken": 0, "extended": 0, "skipped": 0,
                "model_calls": 0, "ticks": 0, "presence": {"ok": False}}
    th.mind_log = __import__("collections", fromlist=["deque"]).deque(maxlen=6)
    th._ema_work = 0.0
    th._beat = 0
    th._stop = _FakeStop()
    th._model_ts = []
    th._presence_git = None
    th._touched_replay = None
    return th


def _store_with(tick=False):
    from membrane.entities import EntityStore, QuantumEntity
    st = EntityStore(path=None, shared=False)
    ent = QuantumEntity(eid="membrane::tool:clock", cls="tool",
                        properties={"name": "clock",
                                    **({"tick_script": "result = {'render': 'clock 12:00'}",
                                        "tick_every": 1} if tick else {})})
    st.docs[ent.eid] = ent
    return st, ent


def _hum_pair(th):
    """One _hum call's (slept, mode) — the pulse it chose + the mode."""
    from membrane.thinker import THINK_TICK_S
    slept = th._hum()
    return slept, th.srv.ambient_mode


def test_night_enters_on_idle_and_slows_the_pulse():
    srv = _FakeSrv(store=_store_with()[0])
    th = _thinker(srv, srv.entity_store)
    slept, mode = _hum_pair(th)
    assert mode == "night"
    assert slept >= 20.0, f"the night pulse didn't slow: {slept}s"


def test_wake_on_turn_flips_to_day_and_fast_pulse():
    srv = _FakeSrv(store=_store_with()[0])
    th = _thinker(srv, srv.entity_store)
    _hum_pair(th)                                          # night
    srv.last_turn_ts = time.time()                        # a turn happened
    slept, mode = _hum_pair(th)
    assert mode == "day"
    assert slept <= 1.5, f"the day pulse didn't return: {slept}s"


def test_wake_on_signal_flips_to_day():
    from membrane.thinker import note_wakeup_signal
    srv = _FakeSrv(store=_store_with()[0])
    th = _thinker(srv, srv.entity_store)
    _hum_pair(th)                                          # night
    note_wakeup_signal(srv, "script-error", {"entity": "a"})
    assert srv.ambient_mode == "day"
    assert any(w.get("kind") == "script-error"
               for w in getattr(srv, "_wakeups", []))


def test_wakeup_signals_run_at_night():
    from membrane.thinker import is_wakeup_signal
    for kind in ("script-error", "peer-miss", "watch-attached",
                 "watch-detached", "watch"):
        assert is_wakeup_signal(kind), kind
    for kind in ("note", "delta-jump", "report-state"):
        assert not is_wakeup_signal(kind), kind


def test_tick_scripts_pause_at_night(capsys):
    srv = _FakeSrv(store=_store_with(tick=True)[0])
    th = _thinker(srv, srv.entity_store)
    th._hum()
    # the clock's tick script must NOT have fired this beat (night
    # pauses the body): stats['ticks'] stays 0 and the entity's
    # last_tick didn't move
    assert th.stats["ticks"] == 0


def test_tick_scripts_run_in_day():
    srv = _FakeSrv(store=_store_with(tick=True)[0])
    srv.last_turn_ts = time.time()          # the room is awake
    th = _thinker(srv, srv.entity_store)
    th._hum()
    assert th.stats["ticks"] >= 1


def test_whisker_scripts_fire_at_night():
    """The animal's whisker: a script flagged 'wakes: true' keeps its
    ear open at night (fire on the night pulse too)."""
    from membrane.entities import EntityStore, QuantumEntity
    st, clock = _store_with(tick=True)
    srv = _FakeSrv(store=st)                 # the seat's store IS st
    whisker = QuantumEntity(
        eid="membrane::tool:memguard", cls="tool",
        properties={"name": "memguard", "tick_script": "result = {'render': 'memguard ok'}",
                    "tick_every": 1, "wakes": True})
    st.docs[whisker.eid] = whisker
    th = _thinker(srv, st)
    th._hum()
    saw = {"clock": False, "memguard": False}
    # only the whisker's render/props may advance at night; the clock's
    # must hold
    for t in th.mind_log:
        pass
    assert th.stats["ticks"] >= 1          # at least the whisker fired
    # the CLOCK entity's last_tick must be unchanged:
    assert not (clock.properties or {}).get("last_tick")


def test_no_night_holds_day():
    srv = _FakeSrv(store=_store_with()[0])
    srv.ambient_night = False
    th = _thinker(srv, srv.entity_store)
    slept, mode = _hum_pair(th)
    assert mode == "day" and slept <= 1.5

def test_night_speech_holds_chatter_but_not_alerts():
    """The animal doesn't chatter in its sleep: the compose lane holds
    the chatter kinds at night; an alert barks."""
    from membrane.opencode import speak
    srv = _FakeSrv()
    srv.ambient_mode = "night"
    srv._act_table = None
    ok = speak(srv, "delta-jump: something", kind="report-state")
    assert ok and len(srv._speak_queue) == 0     # held
    ok = speak(srv, "ALERT: disk full", kind="alert")
    assert ok and len(srv._speak_queue) == 1     # the bark lands


def test_day_speech_composes():
    from membrane.opencode import speak
    srv = _FakeSrv()
    srv.ambient_mode = "day"
    srv._act_table = None
    ok = speak(srv, "a day note", kind="report-state")
    assert ok and len(srv._speak_queue) == 1
