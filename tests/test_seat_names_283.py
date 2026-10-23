"""#283 (the seat's half): each seat names its own Blurp.

Several seats in one Home Assistant (the zoo) show a card per seat; the cards
differ only if each seat's self has its own name. The name is a seat setting
(`self_name` in <data dir>/settings.json, "" = the default), the default is
self_sheet.NAME ("Blurp"), MEMBRANE_SELF_NAME overrides it, and every place the
seat says self's name to a person reads it: the sheet's header and prose, the
dock, a locked toy's refusal, the seat card. It is set by `membrane self name
<NAME>` or the 🌱 chooser's door (POST /membrane/seat/control {self_name}),
validated (trimmed, 1-24 printable characters). GET /membrane/self/card is
the zoo's cheap read: name, level, rung name, mode, awake, last activity.
"""
import json
import os
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
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_name as SN  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane import settings as S  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

T0 = 1_790_000_000.0


def _env(**kw):
    """A clean data dir (never the repo's .membrane) and no name override."""
    d = tempfile.mkdtemp()
    base = {k: v for k, v in os.environ.items() if k not in ("MEMBRANE_SELF_NAME", "MEMBRANE_DATA_DIR")}
    base["MEMBRANE_DATA_DIR"] = d
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv


class TestTheSetting(unittest.TestCase):
    def test_the_default_is_blurp(self):
        with _env():
            self.assertEqual(SS.NAME, "Blurp")
            self.assertEqual(SN.DEFAULT, SS.NAME)
            self.assertEqual(SN.current(), "Blurp")

    def test_it_is_a_seat_setting(self):
        with _env():
            self.assertIn("self_name", S.DEFAULTS)
            self.assertEqual(SN.set_name("  Zed "), "Zed")
            self.assertEqual(S.load()["self_name"], "Zed")
            self.assertEqual(SN.current(), "Zed")
            self.assertEqual(S.load()["name"], "")             # the seat's own name (the ceremony) is another thing

    def test_the_env_overrides(self):
        with _env(MEMBRANE_SELF_NAME="Pip"):
            SN.set_name("Zed")
            self.assertEqual(SN.current(), "Pip")
        with _env(MEMBRANE_SELF_NAME="   "):
            self.assertEqual(SN.current(), "Blurp")            # a blank override is no override

    def test_validation(self):
        self.assertEqual(SN.valid("  Zed  "), "Zed")
        self.assertEqual(SN.valid("x" * 24), "x" * 24)
        self.assertEqual(SN.valid("Mr Blob"), "Mr Blob")
        for bad in ("", "   ", "x" * 25, "a\nb", "a\x00b", "tab\there", None, 7):
            self.assertIsNone(SN.valid(bad), repr(bad))
        with _env():
            with self.assertRaises(ValueError):
                SN.set_name("x" * 25)
            self.assertEqual(SN.current(), "Blurp")

    def test_back_to_the_default(self):
        with _env():
            SN.set_name("Zed")
            self.assertEqual(SN.reset(), "Blurp")
            self.assertEqual(SN.current(), "Blurp")
            self.assertEqual(S.load()["self_name"], "")


class TestEverySayingReadsIt(unittest.TestCase):
    def test_the_dock_and_the_empty_sheet(self):
        with _env():
            SN.set_name("Zed")
            self.assertEqual(SS.dock(None)["name"], "Zed")
            self.assertEqual(SS.empty()["header"]["name"], "Zed")
            self.assertEqual(SS.sheet(None)["header"]["name"], "Zed")

    def test_the_sheet_of_a_live_seat(self):
        with _env():
            srv = _seat()
            SL._append(srv, {"ts": T0, "kind": "birth"})
            SN.set_name("Zed")
            s = SS.sheet(srv, now=T0 + 10, fresh=True)
            self.assertEqual(s["header"]["name"], "Zed")
            text = json.dumps(s)
            self.assertNotIn("Blurp", text)                    # the prose says it too (the caveat, the attributes)
            self.assertIn("hours of Zed per month", text)

    def test_a_rename_is_not_hidden_by_the_cache(self):
        with _env():
            srv = _seat()
            SL._append(srv, {"ts": T0, "kind": "birth"})
            self.assertEqual(SS.sheet(srv, now=T0 + 10)["header"]["name"], "Blurp")
            self.assertEqual(SS.dock(srv, now=T0 + 10)["name"], "Blurp")
            SN.set_name("Zed")
            self.assertEqual(SS.sheet(srv, now=T0 + 11)["header"]["name"], "Zed")
            self.assertEqual(SS.dock(srv, now=T0 + 11)["name"], "Zed")

    def test_rename_is_whole_words(self):
        with _env():
            SN.set_name("Zed")
            self.assertEqual(SN.say("not Blurp's; Blurpy"), "not Zed's; Blurpy")
        with _env():
            self.assertEqual(SN.say("not Blurp's"), "not Blurp's")

    def test_a_locked_toy_says_it(self):
        from membrane import toy_unlocks as TU
        with _env():
            srv = _seat()
            SL._append(srv, {"ts": T0, "kind": "birth"})
            SN.set_name("Zed")
            msg = TU.refusal(srv, "tower")
            self.assertIn("unlocks when Zed earns 'feel itself'", msg)   # #295: the achievement (#333 3e: the tower's)
            self.assertIn("Zed topples", msg)

    def test_the_seat_card_tells_the_model(self):
        from membrane import lanes as LN
        with _env():
            SN.set_name("Zed")
            card = LN._seat_card(types.SimpleNamespace(), False)
        self.assertIn("SELF'S NAME on this seat: Zed", card)


class TestTheDoors(unittest.TestCase):
    def test_the_chooser_door(self):
        with _env():
            srv = _seat()
            self.assertIn("self_name", SP.CONTROLS)
            out = SP.control(srv, {"self_name": " Zed "})
            self.assertEqual(out["applied"]["self_name"], "Zed")
            self.assertEqual(SN.current(), "Zed")
            ev = srv.entity_store.docs["membrane::presence"].properties["last_event"]
            self.assertEqual((ev["kind"], ev["value"]), ("self-name", "Zed"))
            out = SP.control(srv, {"self_name": "x" * 30})
            self.assertNotIn("self_name", out["applied"])
            self.assertEqual(SN.current(), "Zed")
            self.assertEqual(SP.state(srv)["self_name"], "Zed")

    def test_the_cli(self):
        import contextlib
        import io
        with _env():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(SN.cli_main(["name"]), 0)
            self.assertIn("Blurp", out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(SN.cli_main(["name", "Mr", "Blob"]), 0)
            self.assertEqual(SN.current(), "Mr Blob")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(SN.cli_main(["name", "x" * 25]), 2)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(SN.cli_main(["name", "--reset"]), 0)
            self.assertEqual(SN.current(), "Blurp")

    def test_the_cli_knows_the_command(self):
        cli = (ROOT / "membrane" / "cli.py").read_text()
        self.assertIn('if cmd == "self":', cli)
        self.assertIn('"self"', cli.split('if cmd not in (', 1)[1].split("):", 1)[0])


class TestTheCard(unittest.TestCase):
    KEYS = {"name", "level", "rung_name", "mode", "awake", "last_activity", "generated"}

    def test_a_seat_without_self(self):
        with _env():
            c = SS.card(None)
        self.assertTrue(self.KEYS <= set(c))
        self.assertEqual((c["name"], c["level"], c["mode"], c["awake"], c["last_activity"]),
                         ("Blurp", 0, "off", False, None))

    def test_a_live_seat(self):
        with _env():
            srv = _seat()
            SL._append(srv, {"ts": T0, "kind": "birth"})
            SL._append(srv, {"ts": T0 + 5, "kind": "rung", "rung": 1, "skill": 0.6})
            SL.set_mode(srv, "on")
            SN.set_name("Zed")
            c = SS.card(srv, now=T0 + 10)
        self.assertEqual(c["name"], "Zed")
        self.assertEqual(c["level"], 1)
        self.assertEqual(c["rung_name"], SL.RUNG_NAMES[1])
        self.assertEqual(c["mode"], "on")
        self.assertTrue(c["awake"])
        self.assertEqual(c["last_activity"], SS.iso(T0 + 5))
        json.dumps(c)


class TestTheRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        cls.srv = _seat()
        SL._append(cls.srv, {"ts": T0, "kind": "birth"})
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

    def _req(self, p, body=None, tok=None):
        h = {"Authorization": f"Bearer {tok}"} if tok else {}
        if body is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", headers=h,
                                     method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_the_card(self):
        base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
        with mock.patch.dict(os.environ, base, clear=True), _env():
            SN.set_name("Zed")
            code, body = self._req("/membrane/self/card")
        self.assertEqual(code, 200)
        self.assertEqual(body["name"], "Zed")
        self.assertEqual(body["level"], 0)
        self.assertFalse(body["awake"])

    def test_the_card_is_behind_the_jwt_gate(self):
        from membrane import jwtauth as J
        secret = "s" * 40
        with _env(MEMBRANE_JWT_SECRET=secret):
            self.assertEqual(self._req("/membrane/self/card")[0], 401)
            self.assertEqual(self._req("/membrane/self/card", tok=J.mint(secret, "alice"))[0], 200)

    def test_renamed_through_the_seat_control(self):
        base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
        with mock.patch.dict(os.environ, base, clear=True), _env():
            code, body = self._req("/membrane/seat/control", body={"self_name": "Quux"})
            self.assertEqual(code, 200)
            self.assertEqual(body["applied"]["self_name"], "Quux")
            self.assertEqual(self._req("/membrane/self/card")[1]["name"], "Quux")


class TestTheDoctrine(unittest.TestCase):
    def test_it_says_the_name_is_per_seat(self):
        doctrine = (ROOT / "doctrine.md").read_text()
        self.assertIn("Blurp", doctrine)
        self.assertIn("per seat", doctrine)


if __name__ == "__main__":
    unittest.main()
