"""#249 (part of #161): self's 👶 panel as a character sheet.

membrane/self_sheet.py computes one JSON sheet from self's record, status()
and energy (cached ~30 s) and GET /membrane/self/sheet serves it read-only
behind the JWT gate. The sheet is laid out like an RPG character's: a header
(name, Lv, age), attributes, the quest log, achievements, the growth curve,
where the time went, the inventory and relationships. #576 retired the old
learner: those sections are empty until the new one fills them, and the page
renders them as they are. The page side (panel.js) is tested with node, and
so is the toy dock (GET /membrane/toybox/state).
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
SECRET = "s" * 40
H = 3600.0
# the operator's seat was born 2026-10-16 13:55Z: a seat born that morning
# lived through both bugs
T0 = dt.datetime(2026, 10, 16, 13, 0, 0, tzinfo=dt.timezone.utc).timestamp()


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv


def _put(srv, rec):
    SL._append(srv, rec)


def _born(srv):
    """A Blurp born at T0 (its birth line)."""
    _put(srv, {"ts": T0, "kind": "birth", "by": "operator"})
    return srv


def _sheet(srv, now):
    return SH.sheet(srv, now=now, fresh=True)


# ---- the header, the empty record, the cache --------------------------------------

class TestHeaderAndCache(unittest.TestCase):
    def test_the_header(self):
        s = _sheet(_born(_seat()), T0 + 12 * H)
        h = s["header"]
        self.assertEqual(h["name"], "Blurp")
        self.assertEqual((h["level"], h["rung_name"], h["next"]), (0, "", []))   # #576: the ladder retired
        self.assertAlmostEqual(h["age_h"], 12.0, places=2)
        self.assertEqual(h["born"], "2026-10-16T13:00:00+00:00")
        self.assertIsNone(h["skill"])

    def test_an_empty_record_has_the_whole_shape(self):
        s = _sheet(_seat(), T0)
        for key in ("header", "attributes", "quest", "achievements", "growth", "time", "inventory", "relationships",
                    "tree", "skills", "toys", "energy", "code"):
            self.assertIn(key, s)
        self.assertTrue(s["empty"])
        self.assertEqual(s["achievements"], [])
        self.assertEqual(s["header"]["level"], 0)
        self.assertEqual(s["header"]["age_h"], 0.0)
        json.dumps(s)                                               # it serialises

    def test_it_is_cached_for_30_seconds(self):
        srv = _seat()
        a = SH.sheet(srv, now=T0 + 10)
        _put(srv, {"ts": T0 + 11, "kind": "birth"})
        self.assertIs(SH.sheet(srv, now=T0 + 10 + SH.CACHE_S - 1), a)
        b = SH.sheet(srv, now=T0 + 10 + SH.CACHE_S + 1)
        self.assertIsNot(b, a)
        self.assertEqual(b["header"]["born"], SH.iso(T0 + 11))

    def test_a_sheet_built_before_self_is_bound_is_not_cached(self):   # #309
        srv = types.SimpleNamespace(entity_store=EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False),
                                    paused=False, efield=None)
        srv._models_path = str(Path(tempfile.mkdtemp()) / "models.json")
        a = SH.sheet(srv, now=T0 + 10)
        self.assertIsNot(SH.sheet(srv, now=T0 + 11), a)          # mode not loaded yet: never served from cache
        SL.bind(srv)
        b = SH.sheet(srv, now=T0 + 12)
        self.assertIs(SH.sheet(srv, now=T0 + 13), b)             # once bound it caches again


# ---- the route: read-only, behind the JWT gate -------------------------------------

def _jwt_env(**kw):
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


class TestRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        cls.srv = _born(_seat())
        runner = _StubRunner()
        runner.srv = cls.srv
        handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                                  config=_CFG, runner=runner)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _get(self, p, tok=None, method="GET"):
        h = {"Authorization": f"Bearer {tok}"} if tok else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", headers=h, method=method,
                                     data=b"{}" if method == "POST" else None)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_the_sheet(self):
        with _jwt_env():
            code, body = self._get("/membrane/self/sheet")
        self.assertEqual(code, 200)
        for key in ("header", "attributes", "quest", "achievements", "growth", "time", "inventory", "relationships"):
            self.assertIn(key, body)
        self.assertEqual(body["header"]["level"], 0)

    def test_behind_the_jwt_gate(self):
        from membrane import jwtauth as J
        with _jwt_env(MEMBRANE_JWT_SECRET=SECRET):
            self.assertEqual(self._get("/membrane/self/sheet")[0], 401)
            code, body = self._get("/membrane/self/sheet", tok=J.mint(SECRET, "alice"))
        self.assertEqual(code, 200)
        self.assertIn("header", body)

    def test_a_seat_without_self_answers_an_empty_sheet(self):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        runner = _StubRunner()
        runner.srv = None
        handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                                  config=_CFG, runner=runner)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with _jwt_env():
                with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/membrane/self/sheet",
                                            timeout=10) as r:
                    body = json.loads(r.read())
            self.assertTrue(body["empty"])
        finally:
            httpd.shutdown()

    def test_the_ha_relay_forwards_it_read_only(self):
        relay = (ROOT / "custom_components" / "membrane" / "relay.py").read_text()
        ro = relay.split("READ_ONLY_EXACT = (", 1)[1].split(")", 1)[0]
        self.assertIn('"membrane/self/sheet"', ro)
        self.assertNotIn('"membrane/self/sheet"', relay.split("ALLOWED_EXACT = (", 1)[1].split(")", 1)[0])


# ---- the page: pure logic in panel.js (node) ---------------------------------------

def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


@NODE
class TestAttrBar(unittest.TestCase):
    def _b(self, **a):
        return _js("L.attrBar(%s)" % json.dumps(a))

    def test_a_skill_on_0_to_1(self):
        b = self._b(kind="skill", value=0.6, **{"pass": 0.5}, better="higher")
        self.assertEqual((b["lo"], b["hi"]), (0, 1))
        self.assertEqual((b["zero"], b["from"], b["to"]), (0, 0, 60))
        self.assertEqual(b["mark"], 50)
        self.assertTrue(b["met"])
        self.assertFalse(b["neg"])

    def test_a_negative_skill_grows_left_of_zero(self):
        b = self._b(kind="skill", value=-0.34, **{"pass": 0.15}, better="higher")
        self.assertEqual((b["lo"], b["hi"]), (-1, 1))
        self.assertEqual(b["zero"], 50)
        self.assertEqual((b["from"], b["to"]), (33, 50))
        self.assertEqual(b["mark"], 57.5)
        self.assertTrue(b["neg"])
        self.assertFalse(b["met"])
        self.assertEqual(b["text"], "−0.34")

    def test_an_error_lower_is_better(self):
        b = self._b(kind="error", value=0.05, **{"pass": 0.1}, better="lower")
        self.assertEqual((b["lo"], b["hi"]), (0, 0.5))
        self.assertEqual((b["from"], b["to"]), (0, 10))
        self.assertEqual(b["mark"], 20)
        self.assertTrue(b["met"])

    def test_clamped_and_unmeasured(self):
        self.assertEqual(self._b(kind="skill", value=-3, **{"pass": 0.2}, better="higher")["from"], 0)
        self.assertEqual(self._b(kind="share", value=1.7, **{"pass": 1}, better="higher")["to"], 100)
        n = self._b(kind="skill", value=None, **{"pass": 0.2}, better="higher")
        self.assertTrue(n["none"])
        self.assertEqual(n["text"], "not measured yet")


@NODE
class TestSkillAtAGlance(unittest.TestCase):
    def _st(self, **kw):
        s = {"mode": "on", "limit": 7, "rung": 6, "rung_name": "meet an other", "next": 7, "bar": 0.5,
             "waiting_on": "skill", "slipping": False, "held": False, "ladder": []}
        s.update(kw)
        return {"self": s}

    def test_the_exp_bar_carries_the_skill_and_its_bar(self):
        x = _js("L.selfExp(%s)" % json.dumps(self._st(skill=-0.13, skill_pass=0.15)))
        self.assertEqual(x["skill"], "skill −0.13 / +0.15")
        self.assertIn("skill −0.13 / +0.15", x["caption"])
        self.assertIn("skill −0.13 / +0.15", x["title"])

    def test_without_a_bar_or_a_skill(self):
        self.assertEqual(_js("L.selfExp(%s).skill" % json.dumps(self._st(skill=0.42, skill_pass=None))), "skill +0.42")
        self.assertEqual(_js("L.selfExp(%s).skill" % json.dumps(self._st())), "")
        self.assertEqual(_js("L.selfExp(%s).skill" % json.dumps(self._st(mode="off", skill=0.3))), "")

    def test_an_unmeasured_skill_says_so_not_zero(self):
        x = _js("L.selfExp(%s)" % json.dumps(self._st(skill=0.0, skill_pass=0.15, skill_measured=False)))
        self.assertEqual(x["skill"], "skill not measured yet / +0.15")


    def test_the_markup_has_a_place_for_it(self):
        panel = (UI / "panel.js").read_text()
        paint = panel.split("function paintExp(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('class="xp-sk"', paint)
        self.assertIn("x.skill", paint)

    def test_the_sheet_shows_it_big_with_its_trend(self):
        sk = {"rung": 7, "label": "skill vs habit", "value": -0.13, "pass": 0.15, "trend_h": 0.05,
              "series": [{"ago_h": 1.0, "value": -0.2}, {"ago_h": 0.5, "value": -0.18}, {"ago_h": 0.0, "value": -0.13}]}
        h = _js("L.skillStatHtml(%s)" % json.dumps(sk))
        for want in ("−0.13", "+0.15", "to beat", "skill vs habit", "↗", "+0.05", "<svg"):
            self.assertIn(want, h, want)
        self.assertIn("↘", _js("L.skillStatHtml(%s)" % json.dumps(dict(sk, trend_h=-0.04))))
        self.assertIn("→", _js("L.skillStatHtml(%s)" % json.dumps(dict(sk, trend_h=0.0))))
        self.assertEqual(_js("L.skillStatHtml(null)"), "")
        sheet = _demo()
        sheet["skills"][0]["trend"] = sk                             # #300: on the skill's own card
        self.assertIn("to beat", _js("L.selfSheetHtml(%s, {}, 0)" % json.dumps(sheet)))


@NODE
class TestCurve(unittest.TestCase):
    ROWS = [{"rung": 1, "built": True, "fastest_h": 0.06, "working_h": 0.19, "wall_h": 0.19, "child_h": 0.08},
            {"rung": 2, "built": True, "fastest_h": 0.12, "working_h": 1.47, "wall_h": 1.47, "child_h": 2.51},
            {"rung": 3, "built": True, "fastest_h": 0.28, "working_h": None, "wall_h": None, "child_h": 3.35},
            {"rung": 4, "built": False, "fastest_h": None, "working_h": None, "wall_h": None, "child_h": 4.0}]

    def _g(self, W=400, Hh=200, now=2.0, wall=3.0):
        g = {"rows": self.ROWS, "now_working_h": now, "now_wall_h": wall}
        return _js("L.curveGeom(%s, %d, %d)" % (json.dumps(g), W, Hh))

    def test_a_log_x_and_rung_y(self):
        g = self._g()
        self.assertEqual(g["x0"], g["m"]["l"])                       # the domain's floor at the left
        self.assertAlmostEqual(g["xOf"]["1"], g["m"]["l"] + (400 - g["m"]["l"] - g["m"]["r"]) *
                               (0 - __import__("math").log10(g["lo"])) / (__import__("math").log10(g["hi"]) - __import__("math").log10(g["lo"])), places=1)
        self.assertGreaterEqual(g["hi"], 4.0)                         # the child's last point fits
        self.assertEqual(g["yOf"]["0"], 200 - g["m"]["b"])
        self.assertEqual(g["yOf"][str(g["top"])], g["m"]["t"])
        self.assertEqual(g["top"], 4)

    def test_steps_only_through_the_rungs_with_a_value(self):
        g = self._g()
        self.assertEqual(g["fast"].count("L"), 2 * 3 + 1)            # 3 floors, then out to the edge
        self.assertEqual(g["work"].count("L"), 2 * 2 + 1)            # 2 rungs passed, then on to now
        self.assertEqual(len(g["kids"]), 4)
        self.assertEqual(g["now"]["rung"], 2)

    def test_hour_labels(self):
        self.assertEqual(_js("L.hoursText(0.25)"), "15 min")
        self.assertEqual(_js("L.hoursText(2.345)"), "2.3 h")
        self.assertEqual(_js("L.hoursText(null)"), "–")


@NODE
class TestTimeBar(unittest.TestCase):
    def test_three_parts_that_add_up(self):
        p = _js("L.timeParts(%s)" % json.dumps({"alive_h": 10, "down_h": 2, "bugs_h": 3, "working_h": 5}))
        self.assertEqual([x["key"] for x in p], ["working", "bugs", "down"])
        self.assertEqual([x["pct"] for x in p], [50, 30, 20])

    def test_nothing_alive(self):
        p = _js("L.timeParts(%s)" % json.dumps({"alive_h": 0, "down_h": 0, "bugs_h": 0, "working_h": 0}))
        self.assertEqual([x["pct"] for x in p], [0, 0, 0])


def _demo():
    return {"empty": False, "generated": 1000,
            "header": {"name": "self", "level": 6, "rung_name": "meet an other", "next": 7, "next_name": "understand an other",
                       "bar": 0.5, "waiting_on": "skill", "slipping": False, "mode": "on", "age_h": 61.4,
                       "working_h": 12.5, "running_h": 38.6, "born": "2026-10-16T13:55:57+00:00", "cycles": 13743,
                       "looks": 5731, "lines_sent": 367},
            # #300: traits of self as a whole; the skills it is learning; a slipping line
            "attributes": [{"key": "curiosity", "label": "Curiosity", "value": 0.57, "unit": "share", "better": "higher",
                            "what": "share of its looks at things it is still learning", "series": [], "detail": {}},
                           {"key": "insight", "label": "Insight", "value": -0.34, "unit": "skill", "better": "higher",
                            "what": "Brier skill", "series": [], "detail": {}},
                           {"key": "focus", "label": "Focus", "value": 0.1, "unit": "share", "better": "higher",
                            "what": "<b>x</b>", "series": [], "detail": {}}],
            "skills": [{"id": "expect", "name": "expect a reply", "bar": 0.5, "waiting_on": "skill",
                        "criteria": [{"key": "looks", "text": "20/20 reply pairs", "met": True, "frac": 1},
                                     {"key": "skill", "text": "skill vs habit −0.34 → +0.15", "met": False, "frac": 0}],
                        "trend": None}],
            "slipping": [{"id": "reach", "name": "first reach", "text": "first reach: its test no longer holds"}],
            "quest": {"rung": 7, "name": "understand an other", "waiting_on": "skill", "bar": 0.5,
                      "summary": "20/20 reply pairs ✓ · skill vs habit −0.34 → +0.15",
                      "criteria": [{"key": "looks", "text": "20/20 reply pairs", "met": True, "frac": 1},
                                   {"key": "skill", "text": "skill vs habit −0.34 → +0.15", "met": False, "frac": 0}]},
            "achievements": [{"rung": 1, "name": "feel itself", "at": "2026-10-16T14:07:30+00:00", "working_h": 0.19,
                              "wall_h": 0.19, "skill": 0.5667},
                             {"rung": 6, "name": "meet an other", "at": "2026-10-17T09:07:00+00:00", "working_h": 6.91,
                              "wall_h": 33.0, "skill": 1.0, "earned_at": "2026-10-18T18:40:00+00:00",
                              "note": "first passed against the empty operator slot (#208)"}],
            "growth": {"rows": TestCurve.ROWS, "now_working_h": 12.5, "now_wall_h": 61.4, "hours_per_child_month": 0.84,
                       "caveat": "the child scale is an analogy"},
            "time": {"alive_h": 61.4, "down_h": 22.8, "bugs_h": 26.1, "working_h": 12.5, "running_h": 38.6,
                     "bugs": [{"issue": 191, "what": "rung 5 could not notice", "from": "2026-10-16T17:21:58+00:00",
                               "to": "2026-10-16T21:43:37+00:00", "running_h": 4.31}]},
            "inventory": [{"eid": "membrane::Component:toy:die", "name": "toy:die", "kind": "die", "toy": "toy:die",
                           "health": "ok", "looks": 40, "changed_share": 0.3, "error_early": 0.3, "error_late": 0.2,
                           "changes": {"self": 0, "world": 30, "alice": 2}, "last_look_s": 90}],
            "relationships": {"others": [{"who": "alice", "kind": "person", "current": True,
                                          "chat": {"looks": 20, "lines": 10, "replied": 0.5, "brier": 0.25, "skill": 0.1,
                                                   "seen_as": "other"},
                                          "play": {"changes": 2, "looks": 2, "brier": 0.4, "toys": ["toy:die"]}},
                                         {"who": "the seat's model", "kind": "model", "current": False,
                                          "rung8": {"pairs": 20, "brier_model": 0.40, "brier_self": 0.30, "weight": 0.0}}],
                              "body": ["w:body"], "other": ["chat:in:alice"]}}


@NODE
class TestSheetHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _js("L.selfSheetHtml(%s, {}, 1100)" % json.dumps(_demo()))

    def test_the_sections_in_order_as_collapsibles(self):
        order = ["Attributes", "Learning now", "Growth curve", "Where the time went",
                 "Inventory", "Toys", "Relationships"]                          # #267: Toys; #300: earned skills left
        at = [self.html.index(">" + t + "<") for t in order]
        self.assertEqual(at, sorted(at))
        self.assertEqual(self.html.count("<details"), len(order))

    def test_attributes_and_quest_open_by_default(self):
        self.assertIn('<details class="sec" data-sec="attributes" data-was="1" open>', self.html)
        self.assertIn('<details class="sec" data-sec="quest" data-was="1" open>', self.html)
        self.assertIn('<details class="sec" data-sec="growth" data-was="0">', self.html)

    def test_a_remembered_choice_wins(self):
        html = _js("L.selfSheetHtml(%s, %s, 1100)" % (json.dumps(_demo()), json.dumps({"growth": True, "quest": False})))
        self.assertIn('<details class="sec" data-sec="growth" data-was="1" open>', html)
        self.assertIn('<details class="sec" data-sec="quest" data-was="0">', html)

    def test_the_header(self):
        for want in ("self", "Lv 6", "meet an other", "61.4 h", "12.5 h"):
            self.assertIn(want, self.html, want)

    def test_attributes_honest(self):
        self.assertIn("−0.34", self.html)
        self.assertIn("slipping", self.html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", self.html)             # escaped
        self.assertNotIn("<b>x</b>", self.html)

    def test_the_quest_and_achievements(self):
        self.assertIn("20/20 reply pairs", self.html)
        self.assertIn("waiting on", self.html)
        self.assertNotIn("#208", self.html)                          # #300: earned skills live in the Skill tree tab

    def test_the_growth_curve_is_inline_svg_with_its_caveat(self):
        self.assertIn("<svg", self.html)
        self.assertIn("stroke-dasharray", self.html)                 # the fastest possible
        self.assertIn("analogy", self.html)

    def test_inventory_keeps_the_stats_but_play_moved_to_the_dock(self):
        self.assertNotIn("data-poke-toy", self.html)                 # the dock is where you play
        self.assertIn("toy:die", self.html)
        self.assertIn("alice 2", self.html)
        self.assertIn('class="dot ok"', self.html)

    def test_relationships(self):
        self.assertIn("alice", self.html)
        self.assertIn("the seat's model", self.html)
        self.assertIn("w:body", self.html)

    def test_empty_and_missing(self):
        self.assertIn("not reachable", _js("L.selfSheetHtml(null, {}, 0)"))
        e = _js("L.selfSheetHtml(%s, {}, 0)" % json.dumps({"empty": True, "header": {"name": "self", "level": 0},
                                                          "attributes": [], "quest": {"rung": None}, "achievements": [],
                                                          "growth": {"rows": []}, "time": {"alive_h": 0, "bugs": []},
                                                          "inventory": [], "relationships": {"others": [], "body": [], "other": []}}))
        self.assertIn("not been switched on", e)


# ---- the toy dock (the operator's addition to #249) ----------------------------------

class TestDockData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": cls.tmp})
        cls.env.start()
        srv = _born(_seat())
        now = T0 + 12 * H
        for kind, name in (("tower", "toy:tower"), ("die", "toy:die"), ("drift", "toy:drift"),
                           ("burst", "toy:burst"), ("body", "toy:body"), ("fast", "toy:fast")):
            assert TB.add(srv, kind, name, now=T0)["ok"], kind
        TB.poke("toy:tower", "knock", 4, by="alice", now=now - 3)
        TB.poke("toy:die", "roll", by="model", now=now - 100)
        cls.srv, cls.now = srv, now
        cls.d = {t["name"]: t for t in SH.dock(srv, now=now, fresh=True)["toys"]}

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def test_one_per_toybox_toy_with_its_kind(self):
        self.assertEqual(set(self.d), {"toy:tower", "toy:die", "toy:drift", "toy:burst", "toy:body", "toy:fast"})
        self.assertEqual(self.d["toy:die"]["kind"], "die")

    def test_the_live_reading(self):
        self.assertEqual(self.d["toy:tower"]["reading"]["blocks"], [True, True, True, False, False])
        self.assertIn(self.d["toy:die"]["reading"]["value"], range(1, 7))
        self.assertIsInstance(self.d["toy:drift"]["reading"]["value"], int)
        self.assertIn(self.d["toy:burst"]["active"], (True, False))
        self.assertEqual(self.d["toy:drift"]["params"]["hi"], 100)

    def test_the_last_change_and_its_author(self):
        last = self.d["toy:tower"]["last"]
        self.assertEqual(last["by"], "alice")
        self.assertAlmostEqual(last["age_s"], 3, places=0)
        self.assertEqual(self.d["toy:die"]["last"]["by"], "model")
        self.assertIsNone(self.d["toy:body"]["last"])               # never changed

    def test_the_change_counts(self):
        self.assertEqual(self.d["toy:tower"]["changes"]["alice"], 1)

    def test_burst_live_matches_its_clock(self):
        st = TB.state("toy:burst")
        self.assertEqual(self.d["toy:burst"]["active"], TB.burst_live(st["seed"], st["params"], self.now))

    def test_cached_briefly(self):
        a = SH.dock(self.srv, now=self.now)
        self.assertIs(SH.dock(self.srv, now=self.now + SH.DOCK_CACHE_S - 0.5), a)
        self.assertIsNot(SH.dock(self.srv, now=self.now + SH.DOCK_CACHE_S + 0.5), a)

    def test_a_poke_drops_the_cache(self):
        a = SH.dock(self.srv, now=self.now)
        SH.forget_dock(self.srv)
        self.assertIsNot(SH.dock(self.srv, now=self.now + 0.1), a)
        server = (ROOT / "membrane" / "server.py").read_text()
        poke = server.split('if path == "/membrane/toybox/poke":', 1)[1].split("if path in", 1)[0]
        self.assertIn("forget_dock", poke)

    def test_no_toybox_no_toys(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": tempfile.mkdtemp()}):
            self.assertEqual(SH.dock(_seat(), now=T0, fresh=True)["toys"], [])


class TestDockRoute(TestRoute):
    def test_the_toy_state(self):
        with _jwt_env():
            code, body = self._get("/membrane/toybox/state")
        self.assertEqual(code, 200)
        self.assertIn("toys", body)

    def test_toy_state_behind_the_jwt_gate(self):
        from membrane import jwtauth as J
        with _jwt_env(MEMBRANE_JWT_SECRET=SECRET):
            self.assertEqual(self._get("/membrane/toybox/state")[0], 401)
            self.assertEqual(self._get("/membrane/toybox/state", tok=J.mint(SECRET, "bob"))[0], 200)

    def test_the_relay_forwards_toy_state_read_only(self):
        relay = (ROOT / "custom_components" / "membrane" / "relay.py").read_text()
        self.assertIn('"membrane/toybox/state"', relay.split("READ_ONLY_EXACT = (", 1)[1].split(")", 1)[0])


def _toy(**kw):
    t = {"name": "toy:tower", "kind": "tower", "health": "ok", "reading": {"blocks": [True, True, False, False, True]},
         "looks": 12, "error_early": 0.3, "error_late": 0.1, "changes": {"self": 4, "world": 0, "alice": 2},
         "last": {"by": "alice", "age_s": 2, "what": "knock", "block": 3}, "active": None, "params": {"n": 5}}
    t.update(kw)
    return t


@NODE
class TestDockGlyph(unittest.TestCase):
    def _g(self, **kw):
        return _js("L.toyGlyph(%s)" % json.dumps(_toy(**kw)))

    def test_a_tower_is_a_stack_with_fallen_blocks_dimmed(self):
        g = self._g()
        self.assertEqual(g["html"].count('class="blk"'), 3)
        self.assertEqual(g["html"].count('class="blk down"'), 2)
        self.assertEqual(g["label"], "toy:tower, a tower: 3 of 5 blocks standing")
        # the top block (5, standing) is drawn first (block 1 is the bottom)
        self.assertTrue(g["html"].index('class="blk"') < g["html"].index('class="blk down"'))

    def test_a_die_shows_its_face(self):
        self.assertEqual(self._g(kind="die", name="toy:die", reading={"value": 3})["html"], "⚂")
        self.assertEqual(self._g(kind="die", name="toy:die", reading={"value": 6})["html"], "⚅")
        self.assertEqual(self._g(kind="die", name="toy:die", reading={"value": 9})["html"], "9")

    def test_a_drift_is_a_gauge(self):
        g = self._g(kind="drift", name="toy:drift", reading={"value": 25}, params={"lo": 0, "hi": 100})
        self.assertIn('class="gauge"', g["html"])
        self.assertIn("height:25%", g["html"])

    def test_burst_active_or_quiet(self):
        self.assertIn("active", self._g(kind="burst", reading={"value": 4}, active=True)["label"])
        self.assertIn("quiet", self._g(kind="burst", reading={"value": 4}, active=False)["label"])

    def test_fast_slow_value_and_body_self_only(self):
        self.assertEqual(self._g(kind="fast", reading={"value": 42})["html"], "42")
        b = self._g(kind="body", name="toy:body", reading={"value": 7})
        self.assertEqual(b["html"], "7")
        self.assertIn("Blurp only", b["label"])

    def test_escaped(self):
        self.assertNotIn("<b>", self._g(kind="fast", reading={"value": "<b>"})["html"])


@NODE
class TestDockBadge(unittest.TestCase):
    def _b(self, last, s=6):
        return _js("L.toyBadge(%s, %d)" % (json.dumps(_toy(last=last)), s))

    def test_the_last_author_while_fresh(self):
        self.assertEqual(self._b({"by": "alice", "age_s": 1})["text"], "A")
        self.assertEqual(self._b({"by": "self", "age_s": 1})["text"], "Blurp")
        self.assertEqual(self._b({"by": "world", "age_s": 1})["text"], "world")
        self.assertEqual(self._b({"by": "model", "age_s": 1})["text"], "M")
        self.assertTrue(self._b({"by": "alice", "age_s": 1})["fresh"])

    def test_it_fades(self):
        self.assertFalse(self._b({"by": "alice", "age_s": 7})["fresh"])
        self.assertIsNone(_js("L.toyBadge(%s, 6)" % json.dumps(_toy(last=None))))
        self.assertIn("alice", self._b({"by": "alice", "age_s": 1})["title"])


@NODE
class TestDockPopover(unittest.TestCase):
    def _p(self, res=None, **kw):
        return _js("L.toyPopoverHtml(%s, %s)" % (json.dumps(_toy(**kw)), json.dumps(res)))

    def test_the_stats(self):
        h = self._p()
        for want in ("12 looks", "0.30 → 0.10", "Blurp 4", "alice 2", "by alice", "2s ago"):
            self.assertIn(want, h, want)

    def test_the_tower_offers_knock_or_rebuild_per_block(self):
        h = self._p()
        self.assertEqual(h.count('data-poke-action="knock"'), 3)
        self.assertEqual(h.count('data-poke-action="rebuild"'), 2)
        self.assertIn('data-poke-arg="3"', h)
        self.assertIn('data-poke-toy="toy:tower"', h)

    def test_the_other_kinds(self):
        self.assertIn('data-poke-action="roll"', self._p(kind="die", name="toy:die", reading={"value": 2}))
        self.assertIn('data-poke-action="nudge"', self._p(kind="drift", name="toy:drift", reading={"value": 2}))
        body = self._p(kind="body", name="toy:body", reading={"value": 2})
        self.assertNotIn("data-poke-action", body)
        self.assertIn("Blurp only", body)

    def test_the_result_inline(self):
        self.assertIn("✓ knock", self._p(res={"ok": True, "text": "✓ knock"}))
        self.assertIn('class="pres bad"', self._p(res={"ok": False, "text": "✗ refused"}))

    def test_the_dock_items(self):
        h = _js("L.toyDockHtml(%s)" % json.dumps([_toy(), _toy(name="toy:die", kind="die", reading={"value": 1})]))
        self.assertEqual(h.count('data-dock-toy="'), 2)
        self.assertIn('aria-label="toy:tower, a tower: 3 of 5 blocks standing', h)
        self.assertIn('aria-haspopup="dialog"', h)


# ---- the page: the wiring (source) ---------------------------------------------------

class TestWiring(unittest.TestCase):
    def setUp(self):
        self.home = (UI / "home.js").read_text()
        self.panel = (UI / "panel.js").read_text()

    def test_the_panel_fetches_the_sheet(self):
        self.assertIn('"/membrane/self/sheet"', self.panel)
        self.assertIn("_selfSheet(", self.home)
        self.assertIn("_selfSheetHtml(", self.home)

    def test_sections_are_remembered_per_browser(self):
        self.assertIn('const LS_SELFSHEET = "membrane.home.selfsheet";', self.home)
        self.assertIn("lsSet(LS_SELFSHEET", self.home)

    def test_activity_is_a_section_too(self):
        self.assertIn('data-sec="activity"', self.home)

    def test_no_chart_library(self):
        self.assertNotIn("d3.", self.panel)
        self.assertNotIn("d3.", self.home)

    def test_the_dock_under_the_baby_tab(self):
        self.assertIn('class="toydock"', self.home)
        self.assertIn('class="dockbtn"', self.home)                  # the phone's 🧸
        self.assertIn("🧸", self.home)
        self.assertIn('"/membrane/toybox/state"', self.panel)
        self.assertIn("this._toyDock,", self.home)                   # in the labels' occluders
        self.assertIn("_toyState(", self.home)

    def test_reduced_motion_and_the_phone(self):
        self.assertIn("prefers-reduced-motion", self.home)
        self.assertIn(".selfpanel .sheet", self.home)


if __name__ == "__main__":
    unittest.main()
