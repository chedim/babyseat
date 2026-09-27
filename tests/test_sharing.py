"""Sharing on the entity field: disjoint writers on one backend, merged
reads, divergence, the working set, the claims commons, the move."""

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import claims as CL  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import move  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def seats():
    b = MemBackend()
    return (EF.EntityField(b, FakeEmbedder(), "membrane@laptop"),
            EF.EntityField(b, FakeEmbedder(), "membrane@phone"))


def say(f, texts, t=0.0):
    f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + t + i, text=x)
                    for i, x in enumerate(texts)], session="s")
    f.process()


class TestMerged(unittest.TestCase):
    def test_writers_are_disjoint_reads_merge(self):
        a, b = seats()
        say(a, ["membrane/router.py picks the free model"])
        say(b, ["membrane/router.py times out on the phone"], t=10)
        self.assertEqual(a.get("f:membrane/router.py").n, 1)       # own state only
        m = a.merged("f:membrane/router.py")
        self.assertEqual(sorted(o["origin"] for o in m["origins"]),
                         ["membrane@laptop", "membrane@phone"])
        self.assertAlmostEqual(float(np.linalg.norm(m["state"])), 1.0, places=6)

    def test_divergence(self):
        a, b = seats()
        say(a, ["membrane/router.py picks the free model"])
        say(b, ["membrane/router.py times out on the phone"], t=10)
        with unittest.mock.patch.object(EF.EntityField, "DIVERGE_COS", 1.01):
            self.assertTrue(a.merged("f:membrane/router.py")["divergence"])
        with unittest.mock.patch.object(EF.EntityField, "DIVERGE_COS", -1.01):
            self.assertFalse(a.merged("f:membrane/router.py")["divergence"])

    def test_the_block_shows_the_other_seat(self):
        a, b = seats()
        say(a, ["membrane/router.py picks the free model"])
        say(b, ["membrane/router.py times out on the phone"], t=10)
        say(b, ["membrane/phone_only.py drives the camera"], t=20)
        text = EB.render_for(a, "what about membrane/router.py and membrane/phone_only.py?")
        self.assertIn("another seat (phone): membrane/router.py times out on the phone", text)
        self.assertIn("▸ f:membrane/phone_only.py", text)          # only the phone knows it

    def test_working_set_adds_foreign_states(self):
        a, b = seats()
        say(a, ["membrane/router.py picks the free model"])
        say(b, ["membrane/camera.py drives the camera"], t=10)
        v = b.get("f:membrane/camera.py").state
        ws = a.working_set(v, k=2, foreign=2)
        self.assertIn(("f:membrane/camera.py", "membrane@phone"), [(e, o) for e, _, o in ws])


class TestCommons(unittest.TestCase):
    def test_two_seats_make_a_commons_claim(self):
        a, b = seats()
        for f in (a, b):
            f.write_events([ST.Event(stream="discourse", role="note", ts=T0,
                                     text="membrane/opencode.py cannot bind another port.")],
                           session="s")
            f.process()
        self.assertEqual(CL.promote_commons(a.b), 1)
        (c,) = CL.commons_of(a.b, "f:membrane/opencode.py")
        self.assertEqual((c["status"], c["origins"]), ("consensus", ["membrane@laptop", "membrane@phone"]))
        self.assertIn("commons (consensus, 2 seats)", EB.render_for(a, "membrane/opencode.py"))

    def test_the_global_field_converts_once(self):
        a, _ = seats()

        class Ex:
            situation, action = "q", "membrane/router.py is the only model picker."
        self.assertEqual(CL.convert_global(a.b, [Ex()]), 1)
        self.assertEqual(CL.convert_global(a.b, [Ex()]), 0)
        self.assertEqual(CL.commons_of(a.b, "f:membrane/router.py")[0]["status"], "curated")


class TestMove(unittest.TestCase):
    def test_copy_and_dump_by_origin(self):
        a, b = seats()
        say(a, ["membrane/router.py picks the free model"])
        say(b, ["membrane/camera.py drives the camera"], t=10)
        dst = MemBackend()
        counts = move.copy_efield(a.b, dst, origins={"membrane@phone"})
        self.assertGreater(counts["events"], 0)
        self.assertEqual({d["origin"] for _, d in dst.scan("events")}, {"membrane@phone"})
        self.assertEqual(len(list(dst.scan("sentences"))), len(list(a.b.scan("sentences"))))
        with tempfile.TemporaryDirectory() as d:
            n = move.dump_efield(a.b, Path(d))
            self.assertEqual(n["events"], len((Path(d) / "events.jsonl").read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
