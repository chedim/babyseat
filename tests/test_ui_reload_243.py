"""#243 follow-up: a restart reuses the open tab, so the tab must notice when
the seat came back with NEW page code and reload itself -- once, never in a
loop, and never over an unsent draft (a chip asks instead)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from membrane import server as NS
from membrane import ui_serve as U

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


# ---- the seat: the build id -------------------------------------------------------

class TestBuildId(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "panel.js").write_text("one")
        (self.root / "index.html").write_text("<html>")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stable_when_nothing_changes(self):
        a = U.ui_build_id(self.root)
        self.assertTrue(a)
        self.assertEqual(a, U.ui_build_id(self.root))

    def test_changes_when_a_ui_file_changes(self):
        a = U.ui_build_id(self.root)
        (self.root / "panel.js").write_text("two")
        self.assertNotEqual(a, U.ui_build_id(self.root))

    def test_changes_when_a_ui_file_is_added_or_renamed(self):
        a = U.ui_build_id(self.root)
        (self.root / "home.js").write_text("x")
        b = U.ui_build_id(self.root)
        self.assertNotEqual(a, b)
        (self.root / "home.js").rename(self.root / "home2.js")
        self.assertNotEqual(b, U.ui_build_id(self.root))

    def test_the_shipped_ui_has_one(self):
        self.assertEqual(U.build_id(), U.ui_build_id(U.UI_DIR))
        self.assertIs(U.build_id(), U.build_id())        # computed once per boot


# ---- the seat: the route ------------------------------------------------------------

class TestConfCarriesBuild(unittest.TestCase):
    def test_conf_route_exposes_the_build(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": d, "MEMBRANE_STANDALONE": "1"}):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), NS.make_handler(hub=NS.SSEHub()))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{httpd.server_address[1]}/ui/conf", timeout=5) as r:
                    c = json.loads(r.read())
            finally:
                httpd.shutdown()
        self.assertEqual(c["build"], U.build_id())


# ---- the page: the reload decision (node) ------------------------------------------

def _js(expr):
    js = ('const L=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "panel.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _dec(loaded, current, draft="", tried=None, now=1_000_000):
    return _js("L.reloadDecision(%s, %s, %s, %s, %d)" % (
        json.dumps(loaded), json.dumps(current), json.dumps(draft), json.dumps(tried), now))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestReloadDecision(unittest.TestCase):
    def test_same_build_no_reload(self):
        self.assertEqual(_dec("a", "a"), "none")

    def test_new_build_reloads(self):
        self.assertEqual(_dec("a", "b"), "reload")

    def test_an_unknown_build_never_reloads(self):
        # an older seat (or HA's own conf) carries no build: leave the page be
        self.assertEqual(_dec("a", None), "none")
        self.assertEqual(_dec("", "b"), "none")

    def test_a_draft_gets_a_chip_not_a_reload(self):
        self.assertEqual(_dec("a", "b", draft="half a thought"), "chip")
        self.assertEqual(_dec("a", "b", draft="   \n"), "reload")    # blank is no draft

    def test_loop_guard(self):
        # it already reloaded for this very build a moment ago and still sees
        # an old one: a chip, never a second reload
        self.assertEqual(_dec("a", "b", tried={"build": "b", "at": 1_000_000 - 5_000}), "chip")
        # long ago, or for another build: a reload is fine again
        self.assertEqual(_dec("a", "b", tried={"build": "b", "at": 1_000_000 - 600_000}), "reload")
        self.assertEqual(_dec("a", "c", tried={"build": "b", "at": 1_000_000 - 5_000}), "reload")


class TestReloadWiring(unittest.TestCase):
    def setUp(self):
        self.panel = (UI / "panel.js").read_text()

    def test_it_checks_on_every_reconnect(self):
        body = self.panel.split("async _openStream()", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("this._checkBuild()", body)
        self.assertIn("conf.data.build", self.panel.split("async _boot()", 1)[1].split("\n    }\n", 1)[0])

    def test_the_open_chat_survives_the_reload(self):
        self.assertIn("membrane.chat.sid", self.panel)
        boot = self.panel.split("async _boot()", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("savedSid", boot)

    def test_the_guard_is_written_before_reloading(self):
        body = self.panel.split("async _checkBuild()", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("reloadDecision(", body)
        self.assertLess(body.index("membrane.reload.tried"), body.index("location.reload()"))
