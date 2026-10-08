"""THE GETTER PATH THE SEAT COULDN'T WALK (2026-10-08, the review, red first).

A seat that models the world builds an entity whose getter reads the real
source -- the operator's design (no capability tools handed to it). The
transcripts show that path failing on the engine, not the model:

  * `entity_read run=true` still hit the 60s script cooldown ("ERROR
    cooldown"), and the cooldown survived an entity_edit of the script --
    the model guessed result/value/out for minutes;
  * the getter's budget was 1.0s: a curl to Home Assistant can't fit;
  * a FORCED read whose getter returned state crashed (`now` unbound).

Pinned: a getter is paced by the read LRU only (a forced read always
runs); rewriting a script clears its cooldown; the getter budget fits a
network read; a forced read with state works; a real cooldown says when.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = None
        self.membrane = None
        self.efield = None


def _world(code="result = {'state': {'soc': 81}}"):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    ent = QuantumEntity(eid=MEMBRANE_NS + "Component:bms", cls="Component",
                        properties={"name": "bms", "getter_script": code})
    est.docs[ent.eid] = ent
    est._srv = srv = _Srv(est)
    return srv, est, ent


class TestGetterPath(unittest.TestCase):
    def test_the_getter_is_not_under_the_60s_cooldown(self):
        self.assertTrue(N._cooldown_ok({"script_cooldowns": {"getter": 1e12}}, "getter", 1e12 + 1))

    def test_a_rewrite_clears_the_cooldown(self):
        srv, est, ent = _world()
        ent.properties["script_cooldowns"] = {"tick": 9e12, "getter": 9e12}
        r = est.edit("Component:bms", getter_script="result = {'state': {'soc': 82}}")
        self.assertTrue(r.get("ok"), r)
        self.assertNotIn("script_cooldowns", est.docs[ent.eid].properties)

    def test_the_budget_fits_a_network_read(self):
        self.assertGreaterEqual(N._SCRIPT_TIMEOUTS["getter"], 8.0)

    def test_a_forced_read_with_state_works(self):
        srv, est, ent = _world()
        out = WA.entity_read_back(srv, ent, force=True)
        self.assertTrue(out.get("fired"), out)
        self.assertNotIn("error", out)

    def test_a_real_cooldown_says_when(self):
        props = {"script_cooldowns": {"nudge": 1000.0}}
        self.assertIn("retry in", N.cooldown_message(props, "nudge", 1010.0))


if __name__ == "__main__":
    unittest.main()
