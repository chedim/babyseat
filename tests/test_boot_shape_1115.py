"""THE FIELD GROWING WHILE THE SEAT BOOTS (#1115, operator 2026-11-11): the loading page draws the real
field's SHAPE -- the field view's own 3-D layout (field3d) of the heaviest entities, their masses and
strongest relations, as numbers only (the door answers without auth: no eids, names or words) --
appearing in the order the rows load, pulsing once recall's models are up, and handing over to the UI
when the seat is ready. The clean stop saves the shape in the warm snapshot; a boot with none draws a
generated field driven by the real progress numbers.
"""
import json
import re
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from membrane import bootprof as BP  # noqa: E402
from membrane.efstore import pack_f16  # noqa: E402
from membrane.sqlstore import SQLiteBackend  # noqa: E402


def _vec(i, d=16):
    v = np.random.default_rng(i).standard_normal(d)
    return v / np.linalg.norm(v)


def _seat(be):
    from _ef_fakes import FakeEmbedder
    from membrane.efield import EntityField
    return type("S", (), {"efield": EntityField(be, FakeEmbedder(16), "seat@host")})()


def _fill(be, n=60):
    for i in range(n):
        be.upsert("entity_states", f"seat@host|x{i}", {"origin": "seat@host", "eid": f"secret-name-{i}",
                                                        "tier": "live", "state": pack_f16(_vec(i)),
                                                        "accr": i + 1, "kind": "thing",
                                                        "surface": f"Private Words {i}"})
    for i in range(1, n):
        be.upsert("relations", f"r{i}", {"origin": "seat@host", "subj": f"secret-name-{i}",
                                         "obj": f"secret-name-{i - 1}", "rel": "uses", "n": 1,
                                         "superseded": False})


class TheShape(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="boot-shape-")
        self.be = SQLiteBackend(Path(self.dir) / ".membrane" / "field.sqlite")   # a seat's .membrane/field.sqlite
        _fill(self.be)

    def tearDown(self):
        try:
            self.be.close()
        except Exception:
            pass                                       # closed by the test (the stop)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_numbers_only_no_names_or_words(self):
        shape = BP.field_shape(_seat(self.be))
        raw = json.dumps(shape)
        self.assertNotIn("secret-name", raw)
        self.assertNotIn("Private", raw)
        self.assertEqual(set(shape), {"v", "n", "p", "m", "o", "e"})
        for k in ("p", "m", "o", "e"):
            self.assertTrue(all(type(x) is int for x in shape[k]), k)
        self.assertEqual(shape["n"], 60)
        self.assertEqual(len(shape["p"]), 3 * 60)
        self.assertEqual(len(shape["m"]), 60)
        self.assertEqual(len(shape["o"]), 60)
        self.assertTrue(all(0 <= x <= 1000 for x in shape["p"] + shape["o"]))
        self.assertTrue(shape["e"] and len(shape["e"]) % 2 == 0)
        self.assertTrue(all(0 <= x < 60 for x in shape["e"]))

    def test_the_load_order_is_the_rows_order(self):
        shape = BP.field_shape(_seat(self.be))
        # the heaviest come first in the shape; their load ranks follow the rows (x0 loaded first)
        heaviest_last_loaded = shape["o"][0]
        self.assertEqual(heaviest_last_loaded, max(shape["o"]))

    def test_the_point_cap_keeps_the_heaviest(self):
        old = BP.SHAPE_POINTS
        BP.SHAPE_POINTS = 10
        try:
            shape = BP.field_shape(_seat(self.be))
        finally:
            BP.SHAPE_POINTS = old
        self.assertEqual(shape["n"], 10)
        self.assertEqual(shape["m"], sorted(shape["m"], reverse=True))

    def test_the_stop_saves_it_and_the_boot_finds_it_from_the_field_path(self):
        from membrane import restarts
        self.be.close()
        restarts.save_warm(_seat(self.be))
        field_json = Path(self.dir) / ".membrane" / "field.json"
        got = BP.read_shape(str(field_json))
        self.assertIsNotNone(got)
        self.assertEqual(json.loads(got)["n"], 60)

    def test_no_snapshot_no_shape(self):
        self.assertIsNone(BP.read_shape(str(Path(self.dir) / "nothing" / "field.json")))


class TheProgress(unittest.TestCase):
    def test_the_rows_load_counts_into_the_progress(self):
        d = tempfile.mkdtemp(prefix="boot-prog-")
        try:
            be = SQLiteBackend(Path(d) / "field.sqlite")
            _fill(be, 25)
            be.close()
            BP.PROGRESS.clear()
            be = SQLiteBackend(Path(d) / "field.sqlite")
            be.close()
            self.assertEqual(BP.PROGRESS.get("states"), 25)
            self.assertEqual(BP.PROGRESS.get("states_total"), 25)
            self.assertEqual(BP.PROGRESS.get("rows"), BP.PROGRESS.get("rows_total"))
            self.assertGreaterEqual(BP.PROGRESS.get("rows"), 49)
        finally:
            shutil.rmtree(d, ignore_errors=True)


def _reserved():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(16)
    return s


def _get(port, path, accept="application/json"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class TheDoorServesIt(unittest.TestCase):
    def setUp(self):
        self.sock = _reserved()
        self.port = self.sock.getsockname()[1]
        self.laps = BP.Laps("boot")
        self.laps.step("field rows")
        self.shape = json.dumps({"v": 1, "n": 2, "p": [0, 0, 0, 1000, 1000, 1000], "m": [9, 3],
                                 "o": [0, 1000], "e": [0, 1]}).encode()
        self.door = BP.BootDoor(self.sock, self.laps, shape=self.shape)

    def tearDown(self):
        self.door.handover()
        self.sock.close()

    def test_the_shape_route(self):
        code, body = _get(self.port, "/__boot/shape")
        self.assertEqual(code, 503)
        self.assertEqual(json.loads(body)["n"], 2)

    def test_the_status_carries_the_progress_and_the_recall_flag(self):
        BP.PROGRESS.clear()
        BP.PROGRESS.update({"rows": 5, "rows_total": 10, "states": 2, "states_total": 4})
        got = json.loads(_get(self.port, "/global/health")[1])
        self.assertEqual((got["rows"], got["rows_total"], got["states"], got["states_total"]), (5, 10, 2, 4))
        self.assertFalse(got["recall"])
        self.assertTrue(got["shape"])
        for s in ("entities", "router", "embedder", "config"):
            self.laps.step(s)
        self.assertTrue(json.loads(_get(self.port, "/global/health")[1])["recall"])

    def test_no_shape_says_so(self):
        self.door.handover()
        self.door = BP.BootDoor(self.sock, self.laps)
        self.assertFalse(json.loads(_get(self.port, "/global/health")[1])["shape"])
        self.assertEqual(json.loads(_get(self.port, "/__boot/shape")[1]), {"shape": None})


class ThePage(unittest.TestCase):
    def setUp(self):
        self.page = BP.page_html(BP.Laps("boot"))

    def test_it_animates_and_polls_instead_of_reloading(self):
        self.assertIn("<canvas", self.page)
        self.assertIn("/global/health", self.page)
        self.assertIn("/__boot/shape", self.page)
        head = self.page.split("<noscript>")[0]
        self.assertNotIn('http-equiv="refresh"', head)                 # only the no-script fallback reloads
        self.assertIn('http-equiv="refresh"', self.page)

    def test_no_external_assets(self):
        self.assertIsNone(re.search(r"""(src|href)\s*=\s*["']https?:""", self.page))
        self.assertNotIn("import(", self.page)

    def test_it_shows_the_step_and_seconds(self):
        laps = BP.Laps("boot")
        laps.step("entities")
        self.assertIn("entities", BP.page_html(laps))


if __name__ == "__main__":
    unittest.main()
