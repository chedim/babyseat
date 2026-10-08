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



class TestTheContractSaysItself(unittest.TestCase):
    """The 9-minute exhibit (2026-10-08, 23:xx): six getter rewrites --
    result = {'value': ...}, {'soc': ...}, get(), main(), stdout -- each
    'fired {}' with no word of why: only result['state'] is read back."""

    def test_wrong_keys_are_named_with_the_shape(self):
        srv, est, ent = _world("result = {'value': 81, 'soc': 81}")
        out = WA.entity_read_back(srv, ent, force=True)
        note = out.get("note") or ""
        self.assertIn("value", note)
        self.assertIn("result['state']", note)

    def test_no_result_at_all_says_so(self):
        srv, est, ent = _world("def get():\n    return {'soc': 81}\nprint('soc 81')")
        note = WA.entity_read_back(srv, ent, force=True).get("note") or ""
        self.assertIn("no `result`", note)
        self.assertIn("result['state']", note)

    def test_the_right_shape_has_no_note(self):
        srv, est, ent = _world()
        self.assertNotIn("note", WA.entity_read_back(srv, ent, force=True))

    def test_the_tool_text_gives_the_shape(self):
        from membrane import mcp_server as M
        add = next(t for t in M.TOOLS if t["name"] == "entity_edit")
        desc = add["inputSchema"]["properties"]["getter_script"]["description"]
        self.assertIn("result = {'state':", desc)


if __name__ == "__main__":
    unittest.main()
