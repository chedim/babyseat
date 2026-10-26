"""#230 (part of #161): self in the UI -- the exp bar and the 👶 panel.

The seat side: self's record, newest first and capped, on a small read-only
route (GET /membrane/self/recent?n=30) behind the same JWT gate as every
route (#576 retired status()'s toys and their health marks). The page side (panel.js / home.js): the exp bar's states, the
activity lines rendered plainly, the report card, and the 👶 panel's wiring
(read-only, remembered per browser, refreshing only while open).
"""
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
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
SECRET = "s" * 40


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv


# ---- the record, newest first and capped --------------------------------------

class TestRecent(unittest.TestCase):
    def test_an_empty_record_is_an_empty_list(self):
        self.assertEqual(SL.recent(_seat()), [])

    def test_newest_first(self):
        srv = _seat()
        for i in range(5):
            SL._append(srv, {"ts": 1000.0 + i, "kind": "wait", "n": i})
        got = SL.recent(srv, 3)
        self.assertEqual([r["n"] for r in got], [4, 3, 2])

    def test_capped(self):
        srv = _seat()
        for i in range(SL.RECENT_MAX + 50):
            SL._append(srv, {"ts": 1000.0 + i, "kind": "wait", "n": i})
        self.assertEqual(len(SL.recent(srv, 10_000)), SL.RECENT_MAX)
        self.assertEqual(len(SL.recent(srv)), 30)                      # the default
        self.assertEqual(len(SL.recent(srv, 0)), 1)                    # never nothing by a bad n
        self.assertEqual(len(SL.recent(srv, "junk")), 30)

    def test_it_is_a_copy(self):
        srv = _seat()
        SL._append(srv, {"ts": 1.0, "kind": "wait", "n": 1})
        SL.recent(srv)[0]["n"] = 99
        self.assertEqual(SL.recent(srv)[0]["n"], 1)                    # the record is never edited


# ---- the route: read-only, capped, behind the JWT gate ------------------------

def _jwt_env(**kw):
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


class TestRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        cls.srv = _seat()
        for i in range(SL.RECENT_MAX + 20):
            SL._append(cls.srv, {"ts": 1000.0 + i, "kind": "wait", "n": i})
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

    def _get(self, p, tok=None):
        h = {"Authorization": f"Bearer {tok}"} if tok else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", headers=h),
                                        timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_newest_first_and_capped(self):
        with _jwt_env():
            code, body = self._get("/membrane/self/recent?n=5")
            self.assertEqual(code, 200)
            self.assertEqual([r["n"] for r in body["lines"]], [SL.RECENT_MAX + 19 - i for i in range(5)])
            self.assertEqual(len(self._get("/membrane/self/recent?n=9999")[1]["lines"]), SL.RECENT_MAX)
            self.assertEqual(len(self._get("/membrane/self/recent")[1]["lines"]), 30)

    def test_behind_the_jwt_gate(self):
        from membrane import jwtauth as J
        with _jwt_env(MEMBRANE_JWT_SECRET=SECRET):
            self.assertEqual(self._get("/membrane/self/recent")[0], 401)
            code, body = self._get("/membrane/self/recent?n=2", tok=J.mint(SECRET, "alice"))
            self.assertEqual(code, 200)
            self.assertEqual(len(body["lines"]), 2)

    def test_the_ha_relay_forwards_it_read_only(self):
        relay =(Path(__file__).resolve().parent.parent / "custom_components" / "membrane" / "relay.py").read_text()
        self.assertIn('"membrane/self/recent"', relay.split("READ_ONLY_EXACT = (", 1)[1].split(")", 1)[0])


# ---- the page: pure logic in panel.js (node) ----------------------------------

def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


# the old ladder's names (#576 retired it in the seat; the page still draws a status that has one)
RUNG_NAMES = {0: "sleep and wake", 1: "feel itself", 2: "look", 3: "tell things apart", 4: "patience",
              5: "notice the world changed", 6: "meet an other", 7: "time its turn", 8: "expect a reply",
              9: "recognize itself", 10: "change itself"}


def _st(**kw):
    s = {"mode": "on", "limit": 7, "rung": 6, "rung_name": "meet an other", "next": 7, "bar": 0.64,
         "waiting_on": "pairs", "slipping": False, "held": False, "cycles": 10,
         "ladder": [{"rung": n, "name": RUNG_NAMES[n], "state": "passed" if n <= 6 else "current" if n == 7 else "unbuilt"}
                    for n in range(11)]}
    s.update(kw)
    return {"self": s}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestExpBar(unittest.TestCase):
    def _x(self, **kw):
        return _js("L.selfExp(%s)" % json.dumps(_st(**kw)))

    def test_growing(self):
        x = self._x()
        self.assertEqual(x["state"], "growing")
        self.assertEqual(x["level"], "Lv 6 · meet an other")
        self.assertEqual(x["goal"], "→ 7 · time its turn")           # #258: the ladder flip; #300: the name
        self.assertEqual((x["pct"], x["fill"]), (64, 0.64))
        self.assertEqual(x["caption"], "waiting on: pairs")

    def test_slipping_held_capped_asleep_top(self):
        self.assertEqual(self._x(slipping=True)["state"], "slipping")
        self.assertEqual(self._x(held=True)["state"], "held")
        cap = self._x(waiting_on="your accept")                    # #299: an earned one waits for the operator's accept
        self.assertEqual(cap["state"], "capped")
        self.assertEqual(cap["label"], "accept?")
        self.assertEqual(self._x(mode="off")["state"], "asleep")
        top = self._x(rung=7, rung_name="understand an other", next=None, bar=1.0, waiting_on=None)
        self.assertEqual(top["state"], "top")
        self.assertIn("not built", top["goal"])

    def test_the_fill_is_clamped(self):
        self.assertEqual(self._x(bar=1.7)["fill"], 1)
        self.assertEqual(self._x(bar=-0.2)["pct"], 0)

    def test_the_chooser_view_carries_it(self):
        self.assertEqual(_js("L.selfView(%s).exp.state" % json.dumps(_st())), "growing")

    def test_the_retired_ladders_status_renders(self):
        """#576: the seat's status with no ladder (level 0, nothing next) draws without an error."""
        for mode in ("on", "off"):
            SL.set_mode(srv := _seat(), mode)
            st = {"self": SL.status(srv)}
            v = _js("L.selfView(%s)" % json.dumps(st))
            self.assertTrue(v["loaded"])
            self.assertEqual(v["on"], mode == "on")
            self.assertEqual(_js("L.selfExp(%s).rung" % json.dumps(st)), 0)
            self.assertIn("no status", _js("L.selfCardHtml(null)"))
            self.assertIsInstance(_js("L.selfCardHtml(%s)" % json.dumps(st)), str)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestActivityLines(unittest.TestCase):
    def _line(self, rec):
        return _js("L.selfLine(%s)" % json.dumps(rec))

    def test_the_kinds_read_plainly(self):
        self.assertEqual(self._line({"kind": "reach", "eid": "w:fast", "answer": "changed", "p": 0.98}),
                         "reach w:fast → changed (p 0.98)")
        self.assertEqual(self._line({"kind": "reach", "eid": MEMBRANE_NS + "Component:toy:dice",
                                     "answer": "none", "why": "throttled", "p": 0.4}),
                         "reach toy:dice → no answer (throttled)")
        self.assertEqual(self._line({"kind": "push", "to": "roomata-estorsky", "asked": True}),
                         "push → roomata-estorsky (asked the model)")
        self.assertEqual(self._line({"kind": "push", "to": "roomata-estorsky"}), "push → roomata-estorsky")
        self.assertEqual(self._line({"kind": "shift", "eid": "w:prime", "was": 0.8, "now": 0.0}),
                         "shift noticed on w:prime (0.8 → 0)")
        self.assertEqual(self._line({"kind": "wait", "n": 3}), "wait")
        self.assertEqual(self._line({"kind": "cycle", "act": "move", "gap": 0.0312}), "move (gap 0.031)")
        self.assertEqual(self._line({"kind": "rung", "rung": 3}), "rung 3 passed · tell things apart")
        self.assertIn("error", self._line({"kind": "error", "why": "boom"}))
        self.assertEqual(self._line({"kind": "birth"}), "born")

    def test_the_lines_are_escaped(self):
        html = _js("L.selfLinesHtml(%s, 2000)" % json.dumps([{"ts": 1990, "kind": "reach", "eid": "<b>x</b>",
                                                             "answer": "changed", "p": 0.5}]))
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertIn("10s", html)                                     # how long ago


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestReportCard(unittest.TestCase):
    def test_it_shows_what_the_status_measures(self):
        # #258: the model's numbers are rung 8's block now; rung 7's is the timing
        st = _st(slipping=True, limit=6,
                 rung7={"pushes": 30, "checked": 24, "best": "0-2m",
                        "bins": [{"bin": "0-2m", "pushes": 10, "replied": 7, "rate": 0.7},
                                 {"bin": "2-10m", "pushes": 6, "replied": 2, "rate": 0.3333},
                                 {"bin": "10-30m", "pushes": 4, "replied": 1, "rate": 0.25},
                                 {"bin": "30m+", "pushes": 4, "replied": 0, "rate": 0.0}],
                        "share": 0.42, "chance": 0.17, "z": 3.4, "payoff": 0.12, "payoff_z": 2.1,
                        "reply_rate": 0.42, "blind_rate": 0.3},
                 rung8={"asked": 8, "checked": 6, "paired": 5, "pair_rate": 0.83,
                        "brier_model": 0.37, "brier_self": 0.26, "brier_mix": 0.25, "brier_rate": 0.39, "advisor_weight": 0.0, "base_rate": 0.55},
                 curiosity=[{"eid": "w:fast", "progress": 0.05, "error": 0.12}],
                 world={"w:fast": "moving", "w:prime": "intermittent", "toy:mute": "silent"},
                 split={"chat:in:operator": "other", "w:fast": "body"},
                 toys=[{"eid": "w:fast", "getter": True, "reads": 4, "answered": 4, "health": "ok"},
                       {"eid": "cradle:mobile", "getter": False, "reads": 0, "answered": 0, "health": "no getter"}])
        st["self"]["ladder"][7]["state"] = "capped"
        html = _js("L.selfCardHtml(%s)" % json.dumps(st))
        for want in ("slipping", "awaiting your accept", "not built", "5 of 6", "0.37", "0.39", "0.55",
                     "rung 7: taking turns", "best window <b>0-2m</b>", "0-2m 0.7 ", "vs chance 0.17",
                     "vs timing-blind 0.3 ", "rung 8: the other",
                     "w:fast", "moving", "intermittent", "body", "other", "cradle:mobile",
                     'class="dot ok"', 'class="dot bad"'):
            self.assertIn(want, html, want)

    def test_asleep_says_so(self):
        self.assertIn("asleep", _js("L.selfCardHtml(%s)" % json.dumps(_st(mode="off"))))
        self.assertIn("no status", _js("L.selfCardHtml(null)"))


# ---- the page: the wiring (source) ---------------------------------------------

class TestWiring(unittest.TestCase):
    def setUp(self):
        self.home = (UI / "home.js").read_text()
        self.panel = (UI / "panel.js").read_text()

    def test_the_baby_button(self):
        self.assertIn('class="babybtn"', self.home)
        self.assertIn('aria-label="${nm}: its character sheet and what it is doing"', self.home)   # #249
        self.assertIn("aria-expanded", self.home)
        self.assertIn("safe-area-inset-left", self.home)              # clear of the notch
        self.assertIn("👶", self.home)

    def test_open_or_closed_is_remembered_per_browser(self):
        self.assertIn('const LS_SELFPANEL = "membrane.home.selfpanel";', self.home)
        self.assertIn("lsSet(LS_SELFPANEL", self.home)                # lsGet / lsSet wrap localStorage in try/catch

    def test_it_refreshes_only_while_open(self):
        self.assertIn("clearInterval(this._selfPanelT)", self.home)
        self.assertIn('"/membrane/self/recent?n=" + ', self.panel)

    def test_it_is_read_only(self):
        # the panel posts nothing to self: the 🌱 chooser stays the only switch
        # (the toys' play buttons, #244, post through the chat's _toyPoke)
        body = self.home.split("THE 👶 PANEL", 1)[1].split("// ---- end of the 👶 panel", 1)[0]
        self.assertNotIn("POST", body)
        self.assertNotIn("_setSelfLimit", body)

    def test_the_level_up_respects_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", self.panel)
        self.assertIn("lvup", self.panel)
        self.assertIn("prefers-reduced-motion", self.home)

    def test_both_bars_paint_the_exp_bar(self):
        self.assertIn("paintExp(this._selfStat", self.panel)
        self.assertIn("c._paintExp(this._selfStat)", self.home)


if __name__ == "__main__":
    unittest.main()
