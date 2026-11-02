"""#772: the goal graph tells goals apart -- live-only colours, a dash that renders, cancelled = finished."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _js(file, expr):
    js = "const F=require(%r); process.stdout.write(JSON.stringify(%s))" % (str(UI / file), expr)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _g(*goals):
    return {"nodes": [{"id": i, "kind": "goal", "status": s, "title": i} for i, s in goals], "edges": []}


def _styles(graph):
    return _js("fieldview.js", "F.edgeStyles(%s)" % json.dumps(graph))


@NODE
class LiveGoalColours(unittest.TestCase):
    def test_finished_goals_take_no_slot(self):
        st = _styles(_g(("a", "fulfilled"), ("b", "open"), ("c", "open")))
        self.assertNotIn("a", st)
        self.assertEqual(st["b"]["color"], _styles(_g(("b", "open")))["b"]["color"])

    def test_colours_stable_when_a_goal_appears(self):
        before = _styles(_g(("a", "open"), ("b", "open")))
        after = _styles(_g(("a", "open"), ("b", "open"), ("c", "open")))
        for k in before:
            self.assertEqual(before[k], after[k])

    def test_stable_when_an_earlier_goal_finishes(self):
        before = _styles(_g(("a", "open"), ("b", "open")))
        after = _styles(_g(("a", "fulfilled"), ("b", "open")))
        self.assertEqual(before["b"], after["b"])

    def test_no_two_live_goals_identical(self):
        st = _styles(_g(*[("g%d" % i, "open") for i in range(20)]))
        keys = {(v["color"], v["dash"]) for v in st.values()}
        self.assertEqual(len(keys), 20)


@NODE
class DashRenders(unittest.TestCase):
    def test_work_lines_use_a_dashed_material(self):
        src = (UI / "field3d.js").read_text()
        a = src.index("_stepWorkEdges()")
        body = src[a:src.index("_ring(s)", a)]
        self.assertIn("LineDashedMaterial", body)
        self.assertIn("computeLineDistances", body)


@NODE
class DashRoutes(unittest.TestCase):
    def test_different_dashes_land_in_different_line_objects(self):
        es = [{"dash": "", "n": 1}, {"dash": "6 4", "n": 2}, {"dash": "", "n": 3}, {"dash": "2 3", "n": 4}]
        g = _js("field3d.js", "F.workDashGroups(%s)" % json.dumps(es))
        self.assertEqual({k: [e["n"] for e in v] for k, v in g.items()}, {"": [1, 3], "6 4": [2], "2 3": [4]})

    def test_one_line_object_per_dash_entry(self):
        src = (UI / "field3d.js").read_text()
        a = src.index("_stepWorkEdges()")
        body = src[a:src.index("_ring(s)", a)]
        self.assertIn("LineBasicMaterial", body)
        self.assertIn("workDashGroups", body)


@NODE
class CancelledIsFinished(unittest.TestCase):
    def _graph(self):
        return {"nodes": [{"id": "g", "kind": "goal", "status": "cancelled", "title": "g"},
                          {"id": "t", "kind": "task", "status": "cancelled", "title": "t"},
                          {"id": "l", "kind": "goal", "status": "open", "title": "l"}],
                "edges": [{"from": "t", "to": "g", "rel": "of"}]}

    def test_items_hidden(self):
        ids = [m["id"] for m in _js("field3d.js", "F.workMarkers(%s,%s)" % (
            json.dumps(self._graph()), json.dumps({"g": [0, 0, 0], "t": [1, 1, 1], "l": [2, 2, 2]})))]
        self.assertEqual(ids, ["l"])

    def test_lines_hidden(self):
        es = _js("field3d.js", "F.workEdges(%s,%s,{})" % (
            json.dumps(self._graph()), json.dumps({"g": [0, 0, 0], "t": [1, 1, 1], "l": [2, 2, 2]})))
        self.assertEqual(es, [])


if __name__ == "__main__":
    unittest.main()
