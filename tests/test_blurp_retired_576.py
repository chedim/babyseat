"""#576 part D1: the old learner retires; Blurp's body stays.

The cycle, the ladder, METHODS and the `self::` route are gone. What stays
is the body: the self entity, the switch, energy and sleep, the record and
the views. The loop still turns: an awake step runs the learner hook
(learn_step, where #576 D2 plugs the new learner in -- today it learns
nothing) and still charges energy for the time awake. Every time here is
injected: nothing sleeps.
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import energy as E  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

T0 = 1_760_000_000.0
FREE = 1_000_000_000


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv


class Retired(unittest.TestCase):
    def setUp(self):
        self._ram = mock.patch.object(E, "_avail_ram", return_value=None)
        self._ram.start()
        self._free = mock.patch.object(E, "_free", return_value=FREE)
        self._free.start()

    def tearDown(self):
        self._ram.stop()
        self._free.stop()

    def test_the_old_learner_is_gone(self):
        for name in ("cycle", "from_command", "METHODS", "ACHIEVEMENTS", "PROGRESS", "RUNG_NAMES", "set_limit"):
            self.assertFalse(hasattr(SL, name), name)
        self.assertFalse((ROOT / "membrane" / "toy_unlocks.py").exists())   # the ladder's toy unlocks

    def test_bind_defines_no_methods(self):
        srv = _seat()
        me = SL._eid()
        self.assertIn(me, srv.entity_store.docs)
        self.assertFalse([d for d in srv.entity_store.docs.values()
                          if me in ((d.properties or {}).get("method_of") or [])])

    def test_an_awake_step_learns_nothing_and_still_ticks_energy(self):
        srv = _seat()
        SL.set_mode(srv, "on")
        calls = []
        with mock.patch.object(SL, "learn_step", side_effect=lambda s, now: calls.append(now)):
            self.assertIsNone(SL.step(srv, T0))
            self.assertIsNone(SL.step(srv, T0 + 3600))
        self.assertEqual(calls, [T0, T0 + 3600])
        self.assertAlmostEqual(E.level(srv), 1.0 - E.BASAL_PER_S * 3600, places=6)
        self.assertIsNone(SL.learn_step(srv, T0))          # D2's hook: nothing learned yet

    def test_off_runs_no_learner(self):
        srv = _seat()
        with mock.patch.object(SL, "learn_step") as ls:
            self.assertIsNone(SL.step(srv, T0))
        ls.assert_not_called()

    def test_the_sheet_and_card_render_empty(self):
        srv = _seat()
        SL.set_mode(srv, "on")
        sh = SS.sheet(srv, T0, fresh=True)
        self.assertEqual(sh["tree"], [])
        self.assertEqual(sh["skills"], [])
        self.assertEqual(sh["code"], [])
        h = sh["header"]
        self.assertEqual((h["level"], h["mode"]), (0, "on"))
        self.assertIn("energy", sh["energy"])
        c = SS.card(srv, T0)
        self.assertEqual((c["level"], c["mode"], c["awake"]), (0, "on", True))
        self.assertEqual(SS.sheet(None)["tree"], [])

    def test_status_is_the_body(self):
        srv = _seat()
        st = SL.status(srv)
        self.assertEqual((st["mode"], st["level"], st["born"]), ("off", 0, False))
        SL.set_mode(srv, "on")
        self.assertTrue(SL.status(srv)["born"])

    def test_accept_is_a_stub_that_records_its_line(self):
        srv = _seat()
        out = SL.accept(srv, "chat", by="operator")
        self.assertTrue(out["ok"])
        self.assertFalse(out["already"])
        self.assertTrue(SL.accept(srv, "chat")["already"])
        self.assertEqual([r["id"] for r in SL._read(srv) if r.get("kind") == "accept"], ["chat"])
        self.assertFalse(SL.accept(srv, " ")["ok"])

    def test_the_self_route_is_gone(self):
        from membrane import night
        src = Path(night.__file__).read_text()
        self.assertNotIn("_SELF.from_command", src)


if __name__ == "__main__":
    unittest.main()
