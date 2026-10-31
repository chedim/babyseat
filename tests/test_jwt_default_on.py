"""#762: JWT default-on with generated secrets (red tests).
Per-folder <cwd>/.membrane/jwt.secret signs user tokens; per-machine
~/.membrane/jwt.secret signs the startup internal token; MEMBRANE_JWT=0 is off."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from membrane import jwtauth as J

ENV_KEYS = ("MEMBRANE_JWT", "MEMBRANE_JWT_SECRET", "MEMBRANE_JWT_SECRET_FILE")


class DefaultOn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name, "home"); self.home.mkdir()
        self.cwd = Path(self.tmp.name, "folder"); self.cwd.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        env["HOME"] = str(self.home)
        p = mock.patch.dict(os.environ, env, clear=True); p.start(); self.addCleanup(p.stop)
        old = os.getcwd(); os.chdir(self.cwd); self.addCleanup(os.chdir, old)
        self.addCleanup(self.tmp.cleanup)

    def test_default_on_generates_folder_secret(self):
        cfg = J.config()
        self.assertIsNotNone(cfg)
        f = self.cwd / ".membrane" / "jwt.secret"
        self.assertTrue(f.is_file())
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertGreaterEqual(len(f.read_bytes().rstrip(b"\r\n")), 32)
        self.assertEqual(cfg.secret, f.read_bytes().rstrip(b"\r\n"))
        self.assertEqual(J.config().secret, cfg.secret)

    def test_explicit_secret_replaces_folder_secret(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_JWT_SECRET": "x" * 40}):
            self.assertEqual(J.config().secret, b"x" * 40)
        self.assertFalse((self.cwd / ".membrane" / "jwt.secret").exists())

    def test_machine_secret_signs_internal_token(self):
        tok = J.internal_token()
        m = self.home / ".membrane" / "jwt.secret"
        self.assertTrue(m.is_file())
        self.assertEqual(stat.S_IMODE(m.stat().st_mode), 0o600)
        ident = J.verify(tok, m.read_bytes().rstrip(b"\r\n"))
        self.assertEqual(ident.sub, J.INTERNAL_SUB)
        with self.assertRaises(J.JWTError):
            J.verify(tok, J.config().secret)

    def test_jwt_zero_turns_off(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_JWT": "0"}):
            self.assertIsNone(J.config())
            self.assertFalse(J.enabled())
            self.assertIsNone(J.internal_token())
            self.assertEqual(J.internal_headers(), {})
        self.assertFalse((self.cwd / ".membrane" / "jwt.secret").exists())
