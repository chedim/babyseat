"""#762: JWT default-on with generated secrets (red tests).
Per-folder <cwd>/.membrane/jwt.secret signs user tokens; per-machine
~/.membrane/jwt.secret signs the startup internal token; MEMBRANE_JWT=0 is off."""
import io
import contextlib
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
        J.boot_check()      # only boot creates the folder secret (review #2)
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


def _secret(path):
    return Path(path).read_bytes().rstrip(b"\r\n")


class ReviewPR781(DefaultOn):
    """The PR #781 review: fallback, child_env, mint folder, off switch, reads, boot, cache."""

    def _boot(self):
        J.boot_check()
        return _secret(self.cwd / ".membrane" / "jwt.secret")

    def test_fallback_accepts_machine_signed_internal(self):
        self._boot()
        tok = J.internal_token()
        ident = J.verify_configured(tok)
        self.assertEqual(ident.sub, J.INTERNAL_SUB)

    def test_fallback_refuses_machine_signed_person(self):
        self._boot()
        m = J._machine_secret()
        tok = J.mint(m, "alice")
        with self.assertRaises(J.JWTError):
            J.verify_configured(tok)

    def test_child_env_without_explicit_secret(self):
        self._boot()
        out = J.child_env({"A": "1"})
        tok = out["MEMBRANE_JWT_TOKEN"]
        self.assertEqual(out["OPENCODE_SERVER_PASSWORD"], tok)
        self.assertEqual(out["A"], "1")
        self.assertEqual(J.verify_configured(tok).sub, J.INTERNAL_SUB)

    def _mint(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = J.cli_main(["mint", "--sub", "alice"])
        return rc, out.getvalue().strip(), err.getvalue()

    def test_mint_in_seat_folder_signs_with_folder_secret(self):
        secret = self._boot()
        rc, tok, _ = self._mint()
        self.assertEqual(rc, 0)
        self.assertEqual(J.verify(tok, secret).sub, "alice")

    def test_mint_in_other_folder_refuses_and_creates_nothing(self):
        other = Path(self.tmp.name, "elsewhere"); other.mkdir()
        os.chdir(other)
        rc, tok, err = self._mint()
        self.assertEqual(rc, 2)
        self.assertEqual(tok, "")
        self.assertIn("no seat here", err)
        self.assertFalse((other / ".membrane").exists())

    def test_jwt_zero_with_explicit_secret_is_off(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_JWT": "0", "MEMBRANE_JWT_SECRET": "x" * 40}):
            self.assertIsNone(J.config())
            self.assertFalse(J.enabled())
            self.assertIsNone(J.internal_token())
            rc, tok, err = self._mint()
        self.assertEqual(rc, 2)
        self.assertIn("off", err.lower())
        self.assertNotIn("no MEMBRANE_JWT_SECRET", err)

    def test_config_and_enabled_never_create_a_file(self):
        with self.assertRaises(J.JWTConfigError) as cm:
            J.config()
        self.assertIn("jwt.secret", str(cm.exception))
        self.assertTrue(J.enabled())        # fails closed
        self.assertFalse((self.cwd / ".membrane").exists())

    def test_vanished_secret_fails_closed_without_recreating(self):
        self._boot()
        (self.cwd / ".membrane" / "jwt.secret").unlink()
        with self.assertRaises(J.JWTConfigError):
            J.config()
        self.assertFalse((self.cwd / ".membrane" / "jwt.secret").exists())

    @unittest.skipIf(os.geteuid() == 0, "root ignores file modes")
    def test_boot_check_read_only_folder_exits_with_reason(self):
        d = self.cwd / ".membrane"; d.mkdir(); d.chmod(0o500)
        self.addCleanup(d.chmod, 0o700)
        with self.assertRaises(SystemExit) as cm:
            J.boot_check()
        self.assertIn("jwt.secret", str(cm.exception))
        self.assertIn("refused to start", str(cm.exception))

    def test_read_secret_file_error_names_the_file(self):
        missing = str(self.cwd / "nope" / "jwt.secret")
        with self.assertRaises(J.JWTConfigError) as cm:
            J._read_secret_file(missing)
        self.assertIn(missing, str(cm.exception))
        self.assertNotIn("MEMBRANE_JWT_SECRET_FILE", str(cm.exception))

    def test_file_cache_keeps_one_entry_per_path(self):
        a, b = self.cwd / "a.secret", self.cwd / "b.secret"
        a.write_bytes(b"a" * 40 + b"\n"); b.write_bytes(b"b" * 40 + b"\n")
        J._file_cache.clear()
        real, reads = open, []

        def spy(path, *args, **kw):
            if str(path) in (str(a), str(b)):
                reads.append(str(path))
            return real(path, *args, **kw)
        with mock.patch("builtins.open", spy):
            for _ in range(3):
                self.assertEqual(J._read_secret_file(str(a)), b"a" * 40)
                self.assertEqual(J._read_secret_file(str(b)), b"b" * 40)
        self.assertEqual(sorted(reads), sorted([str(a), str(b)]))

    def test_generated_secret_never_visible_empty(self):
        import secrets
        final = self.cwd / ".membrane" / "gen.secret"
        seen = []
        real = secrets.token_urlsafe

        def spy(n):
            seen.append(final.exists())      # a half-made file must not be at the final path yet
            return real(n)
        with mock.patch("secrets.token_urlsafe", spy):
            got = J._generated_secret(str(final))
        self.assertEqual(seen, [False])
        self.assertEqual(got, final.read_bytes().rstrip(b"\r\n"))
        self.assertGreaterEqual(len(got), 32)
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o600)
