"""#344: the options window's seat half -- one endpoint for the seat-wide options.

GET /membrane/options reads them, POST /membrane/options writes them (only the
writable ones, validated: a bad value is a 400 with the reason, never a silent
clamp). They live in the seat's settings.json (settings.py), so a restart keeps
them:

  self_cpu_share   Blurp's CPU share (#343), 0 < share <= 1, default 0.5. The
                   self loop reads it through options.cpu_share() -- the file,
                   validated on read -- so a write lands without a restart.
  current_tau_s    the field current's fade (efield.CURRENT_TAU_S), READ-ONLY:
                   changing it needs the operator's approval.
  health           Blurp's health (#333): None until it exists.
"""
import json
import os
import sys
import tempfile
import threading
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

from membrane import efield as EF  # noqa: E402
from membrane import options as O  # noqa: E402
from membrane import settings as S  # noqa: E402


def _env():
    """A clean data dir (never the repo's .membrane), no JWT mode."""
    d = tempfile.mkdtemp()
    base = {k: v for k, v in os.environ.items()
            if k != "MEMBRANE_DATA_DIR" and not k.startswith("MEMBRANE_JWT_")}
    base["MEMBRANE_DATA_DIR"] = d
    return mock.patch.dict(os.environ, base, clear=True)


class TestTheRead(unittest.TestCase):
    def test_the_defaults(self):
        with _env():
            o = O.read()
        self.assertEqual(o["self_cpu_share"], 0.5)
        self.assertEqual(o["defaults"]["self_cpu_share"], 0.5)
        self.assertEqual(o["current_tau_s"], EF.CURRENT_TAU_S)
        self.assertEqual(o["current_tau_s"], 180.0)
        self.assertIn("approval", o["current_tau_note"])
        self.assertIsNone(o["energy"])                     # #363: no seat given, nothing to report
        self.assertNotIn("health", o)                      # energy replaced health (#345)
        self.assertEqual(o["writable"], ["self_cpu_share"])
        json.dumps(o)

    def test_energy_comes_from_the_seat_363(self):
        from membrane import energy as E
        rep = {"energy": 0.873, "asleep": False, "reserves": {}, "pull": 0.1, "limiting": "energy"}
        with _env(), mock.patch.object(E, "report", return_value=rep) as r:
            o = O.read(srv=object())
        r.assert_called_once()
        self.assertEqual(o["energy"], {"energy": 0.873, "asleep": False})
        with _env(), mock.patch.object(E, "report", side_effect=RuntimeError("no self yet")):
            self.assertIsNone(O.read(srv=object())["energy"])   # a seat without a self: nothing, not a crash
        json.dumps(o)

    def test_it_is_a_seat_setting(self):
        self.assertEqual(S.DEFAULTS["self_cpu_share"], 0.5)
        self.assertEqual(O.CPU_SHARE_DEFAULT, 0.5)

    def test_a_bad_value_in_the_file_reads_as_the_default(self):
        for bad in (0, -1, 1.5, "x", True, None, float("nan")):
            with _env():
                S.path().parent.mkdir(parents=True, exist_ok=True)
                S.path().write_text(json.dumps({"self_cpu_share": bad}) if bad == bad else
                                    '{"self_cpu_share": NaN}')
                self.assertEqual(O.cpu_share(), 0.5, bad)
                self.assertEqual(O.read()["self_cpu_share"], 0.5, bad)


class TestTheWrite(unittest.TestCase):
    def test_it_writes_and_persists(self):
        with _env():
            out = O.apply({"self_cpu_share": 0.3})
            self.assertEqual(out["self_cpu_share"], 0.3)
            self.assertEqual(json.loads(S.path().read_text())["self_cpu_share"], 0.3)
            self.assertEqual(S.load()["self_cpu_share"], 0.3)
            self.assertEqual(O.cpu_share(), 0.3)          # what #343's loop reads: no restart
            self.assertEqual(O.read()["self_cpu_share"], 0.3)

    def test_the_edges(self):
        with _env():
            self.assertEqual(O.apply({"self_cpu_share": 1})["self_cpu_share"], 1.0)
            self.assertEqual(O.apply({"self_cpu_share": 0.01})["self_cpu_share"], 0.01)

    def test_it_keeps_the_other_settings(self):
        with _env():
            S.save({"name": "Argus", "self_name": "Zed"})
            O.apply({"self_cpu_share": 0.7})
            st = S.load()
        self.assertEqual((st["name"], st["self_name"], st["self_cpu_share"]), ("Argus", "Zed", 0.7))

    def test_bad_values_are_refused_with_a_reason(self):
        for bad in (0, -0.1, 1.01, 50, "0.5", True, None, float("nan"), float("inf"), [0.5]):
            with _env():
                with self.assertRaises(ValueError, msg=repr(bad)) as cm:
                    O.apply({"self_cpu_share": bad})
                self.assertIn("self_cpu_share", str(cm.exception))
                self.assertFalse(S.path().exists())          # nothing written

    def test_the_fade_is_read_only(self):
        with _env():
            with self.assertRaises(ValueError) as cm:
                O.apply({"current_tau_s": 60})
            self.assertIn("approval", str(cm.exception))
            with self.assertRaises(ValueError):
                O.apply({"health": 1.0})
            with self.assertRaises(ValueError) as cm:
                O.apply({"energy": 1.0})
            self.assertIn("read-only", str(cm.exception))
            self.assertEqual(EF.CURRENT_TAU_S, 180.0)

    def test_unknown_and_empty_are_refused(self):
        with _env():
            for body in ({"name": "x"}, {"backend": "http://x"}, {}, [], "s", None):
                with self.assertRaises(ValueError, msg=repr(body)):
                    O.apply(body)
            self.assertFalse(S.path().exists())

    def test_one_bad_key_writes_nothing(self):
        with _env():
            with self.assertRaises(ValueError):
                O.apply({"self_cpu_share": 0.4, "current_tau_s": 60})
            self.assertEqual(O.cpu_share(), 0.5)


class TestTheRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                                  config=_CFG, runner=_StubRunner())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _req(self, body=None, raw=None):
        h = {"Content-Type": "application/json"} if (body is not None or raw is not None) else {}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/membrane/options", headers=h,
                                     method="POST" if data is not None else "GET", data=data)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_get(self):
        with _env():
            code, body = self._req()
        self.assertEqual(code, 200)
        self.assertEqual(body["self_cpu_share"], 0.5)
        self.assertEqual(body["current_tau_s"], 180.0)

    def test_post_writes_and_get_reads_it_back(self):
        with _env():
            code, body = self._req({"self_cpu_share": 0.25})
            self.assertEqual(code, 200)
            self.assertEqual(body["self_cpu_share"], 0.25)
            self.assertEqual(self._req()[1]["self_cpu_share"], 0.25)
            self.assertEqual(S.load()["self_cpu_share"], 0.25)

    def test_post_refuses_with_400_and_a_reason(self):
        with _env():
            for bad in ({"self_cpu_share": 0}, {"self_cpu_share": 2}, {"current_tau_s": 60}, {"nope": 1}):
                code, body = self._req(bad)
                self.assertEqual(code, 400, bad)
                self.assertTrue(body.get("error"), bad)
            self.assertEqual(self._req()[1]["self_cpu_share"], 0.5)


if __name__ == "__main__":
    unittest.main()
