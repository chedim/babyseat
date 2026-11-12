"""#1141: an entity with `tick_every_s` (seconds) and a tick_script that stores `last_tick` must tick: its output lands in
`script_data` (the script's patch), on the pace the seconds say."""
import time

from membrane.entities import QuantumEntity
from membrane.thinker import Thinker
from test_heat_gravity import _seat

SCRIPT = "import time as t\nresult = {'patch': {'script_data': {'last_tick': t.time()}}}"


def _th(srv):
    th = Thinker.__new__(Thinker)
    th.srv = srv
    th.stats = {"ticks": 0, "processed": 0, "spoken": 0, "extended": 0, "skipped": 0, "model_calls": 0, "presence": {}}
    th.mind_log = __import__("collections").deque(maxlen=4)
    th._beat = 0
    return th


def test_tick_every_s_drives_script_data(tmp_path):
    srv, est = _seat(tmp_path)
    ent = QuantumEntity(eid="membrane::tool:timer", cls="tool",
                        properties={"name": "timer", "tick_script": SCRIPT, "tick_every_s": 10})
    est.docs[ent.eid] = ent
    th = _th(srv)
    for _ in range(12):
        th._beat += 1
        th._tick_scripts()
    assert (est.docs[ent.eid].properties.get("script_data") or {}).get("last_tick")
