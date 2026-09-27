"""membrane/efmind.py: the background mind on the entity field -- stimuli
for the thinker, births and junk at night, the dreamer's source."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import efmind as M  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def fld():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


class TestStimuli(unittest.TestCase):
    def test_tags_and_fn(self):
        ev = {"mentions": [["f:membrane/router.py", 0, "membrane/router.py"], ["w:gate", 0, "gate"],
                           ["tool:edit", 0, "edit"]]}
        self.assertEqual(M.legacy_tags(ev), ["files:membrane/router.py", "tool:edit"])
        self.assertIsNone(M.fn_of({"stream": "discourse"}))
        self.assertEqual(M.fn_of({"stream": "observation", "tool": "edit", "outcome": "applied",
                                  "args": {"filePath": "a.py"}}),
                         {"op": "edit", "binds": "a.py", "returns": "applied"})

    def test_live_events_reach_the_thinker(self):
        f = fld()

        class _Srv:
            dreamer = None
            thinker = mock.Mock()
        f.listeners.append(M.on_applied_fn(_Srv, lambda: "s:1:"))
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0,
                                 text="membrane/router.py is slow")], session="s:1:0")
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 1,
                                 text="membrane/gate.py is old")], session="b:0")
        f.process()
        _Srv.thinker.enqueue.assert_called_once()
        kind, item = _Srv.thinker.enqueue.call_args[0]
        self.assertEqual((kind, item["idx"], item["tags"]), ("capture", 1, ["files:membrane/router.py"]))


class TestNight(unittest.TestCase):
    def _busy(self):
        f = fld()
        evs = [ST.Event(stream="discourse", role="user", ts=T0 + i,
                        text=f"membrane/router.py routes asks to the cheapest gateway model, pass {i}")
               for i in range(14)]
        f.write_events(evs, session="s")
        f.process()
        return f

    def test_births_announce_once(self):
        f = self._busy()
        est = mock.Mock(announcements=[])
        with mock.patch.object(M, "BIRTH_SPEC", -1.0), mock.patch.object(M, "BIRTH_MASS", 1):
            first = M.births(f, est, limit=50)
            again = M.births(f, est, limit=50)
        self.assertIn("f:membrane/router.py", [a["eid"] for a in first])
        self.assertEqual(again, [])
        self.assertEqual(len(est.announcements), len(first))

    def test_night_pass_degrades_step_by_step(self):
        f = self._busy()

        class _Srv:
            membrane = None
        out = M.night(_Srv, f, None, now=T0 + 100)
        self.assertIn("births", out)
        self.assertGreater(out["activity"], 0)
        self.assertIn("null", out)
        self.assertEqual(M.night(_Srv, f, None, now=T0 + 200)["activity"], 0)


class TestDreamSource(unittest.TestCase):
    def test_corpus_and_vet(self):
        f = fld()
        f.write_events([ST.Event(stream="discourse", role="model", ts=T0 + i,
                                 text=f"membrane/router.py keeps model {i} warm") for i in range(4)],
                       session="s")
        f.process()
        src = M.DreamSource(f)
        self.assertEqual(len(src.trusted_texts()), 4)
        said = src.embed("membrane/router.py keeps model 1 warm")
        self.assertFalse(src.vet(said))                   # already said: a duplicate


if __name__ == "__main__":
    unittest.main()
