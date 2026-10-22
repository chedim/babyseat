"""THE CLAUDE-AGENT RUNG ON AN API KEY (issue #413, a release gate).

- by default the rung authenticates with the Anthropic API key membrane
  already reads (ANTHROPIC_API_KEY, else `membrane auth anthropic`'s store),
  handed to the SDK as ANTHROPIC_API_KEY in ClaudeAgentOptions.env;
- in that mode the CLI cannot fall back to its stored login: the
  credentials the CLI ranks above the key are blanked in the subprocess env;
- the CLI login is an explicit opt-in (`membrane auth claude-agent
  --login`), never chosen silently -- the old `cli-login` mark is not that
  opt-in;
- no key and no opt-in: the rung refuses with a hint, and no model is called;
- the key never shows in errors, logs or replies, and is never written
  anywhere new.

The SDK is stubbed; no real key, no Anthropic call, no real home.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from tests.test_claude_agent_384 import (  # noqa: E402
    fake_sdk, _srv, _handler, PERSON)

FAKE_KEY = "sk-ant-test-0123456789abcdefFAKEFAKE"
CRED_ENV = ("ANTHROPIC_API_KEY", "MEMBRANE_CLAUDE_AGENT", "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


class _Case(unittest.TestCase):
    """A temp home (never the real ~/.membrane), a clean credential env, a
    stubbed SDK and a CLI that 'exists'."""

    env: dict = {}
    store: dict | None = None

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="ca413-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        if self.store is not None:
            (self.home / ".membrane").mkdir()
            (self.home / ".membrane" / "auth.json").write_text(json.dumps(self.store))
        self.sdk = fake_sdk()
        clean = {k: v for k, v in os.environ.items() if k not in CRED_ENV}
        clean.update(self.env)
        self._p = [mock.patch.dict(os.environ, clean, clear=True),
                   mock.patch.object(Path, "home", lambda: self.home),
                   mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}),
                   mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude")]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()

    def _reply_env(self):
        list(CA.iter_reply([{"role": "user", "content": "hi"}],
                           "claude-agent:sonnet", stream=False))
        return self.sdk.calls[0]["options"].kw["env"]


# ---- the default: the API key ------------------------------------------------

class TestKeyFromEnv(_Case):
    env = {"ANTHROPIC_API_KEY": FAKE_KEY}

    def test_default_mode_is_the_api_key(self):
        self.assertEqual(CA.auth_mode(), CA.MODE_KEY)

    def test_the_key_reaches_the_sdk_in_options_env(self):
        env = self._reply_env()
        self.assertEqual(env["ANTHROPIC_API_KEY"], FAKE_KEY)

    def test_no_silent_fallback_to_the_cli_login(self):
        """Every credential the CLI ranks above the key (a bearer token, a
        cloud-provider switch) or uses as a login is blanked."""
        env = self._reply_env()
        for name in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                     "CLAUDE_CODE_USE_FOUNDRY"):
            self.assertEqual(env.get(name), "", name)

    def test_competing_credentials_in_the_shell_are_overridden(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "bearer-x",
                                          "CLAUDE_CODE_OAUTH_TOKEN": "oauth-x",
                                          "CLAUDE_CODE_USE_BEDROCK": "1"}):
            env = self._reply_env()
        self.assertEqual(env["ANTHROPIC_API_KEY"], FAKE_KEY)
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "")
        self.assertEqual(env["CLAUDE_CODE_USE_BEDROCK"], "")

    def test_the_proxy_turn_carries_the_key(self):
        srv = _srv()
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": False,
              "messages": [{"role": "user", "content": PERSON}]}
        h._v1_deliver(up, b"{}", PERSON, None, {"verdict": "escalate"}, None)
        self.assertIn(b"200", h.wfile.getvalue().split(b"\r\n", 1)[0])
        self.assertEqual(self.sdk.calls[0]["options"].kw["env"]["ANTHROPIC_API_KEY"],
                         FAKE_KEY)


class TestKeyFromStore(_Case):
    store = {"anthropic": FAKE_KEY}

    def test_the_stored_anthropic_key_is_used(self):
        self.assertEqual(self._reply_env()["ANTHROPIC_API_KEY"], FAKE_KEY)


class TestOldMarkIsNotTheOptIn(_Case):
    """The mark the old command stored (`cli-login`) was not an explicit
    choice of the login: it means the default, the API key."""
    store = {"claude-agent": "cli-login", "anthropic": FAKE_KEY}

    def test_old_mark_means_the_api_key(self):
        self.assertEqual(CA.auth_mode(), CA.MODE_KEY)
        self.assertEqual(self._reply_env()["ANTHROPIC_API_KEY"], FAKE_KEY)


# ---- the login: an explicit opt-in -------------------------------------------

class TestLoginOptIn(_Case):
    store = {"claude-agent": "login"}
    env = {"ANTHROPIC_API_KEY": FAKE_KEY}

    def test_login_mode_only_when_opted_in(self):
        self.assertEqual(CA.auth_mode(), CA.MODE_LOGIN)

    def test_login_mode_blanks_the_key_so_the_login_answers(self):
        env = self._reply_env()
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertNotIn(FAKE_KEY, json.dumps(env))

    def test_login_mode_needs_no_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            CA.check_available()            # no Unavailable


class TestLoginOptInByEnv(_Case):
    env = {"MEMBRANE_CLAUDE_AGENT": "login"}

    def test_env_opt_in(self):
        self.assertEqual(CA.auth_mode(), CA.MODE_LOGIN)


# ---- no key: refused, with the hint --------------------------------------------

class TestNoKey(_Case):
    def test_check_available_refuses_with_the_hint(self):
        with self.assertRaises(CA.Unavailable) as cm:
            CA.check_available()
        msg = str(cm.exception)
        self.assertIn("membrane auth anthropic", msg)
        self.assertIn("ANTHROPIC_API_KEY", msg)

    def test_no_sdk_call_without_a_key(self):
        with self.assertRaises(CA.Unavailable):
            list(CA.iter_reply([{"role": "user", "content": "hi"}],
                               "claude-agent:sonnet", stream=False, sdk=self.sdk))
        self.assertEqual(self.sdk.calls, [])

    def test_the_proxy_turn_is_a_clear_503(self):
        srv = _srv()
        h = _handler(srv)
        up = {"model": "claude-agent:sonnet", "stream": True,
              "messages": [{"role": "user", "content": PERSON}]}
        h._v1_deliver(up, b"{}", PERSON, None, {"verdict": "escalate"}, None)
        raw = h.wfile.getvalue()
        self.assertIn("503", raw.split(b"\r\n", 1)[0].decode())
        body = json.loads(raw.split(b"\r\n\r\n", 1)[1])
        self.assertIn("membrane auth anthropic", body["error"]["message"])
        self.assertEqual(self.sdk.calls, [])


# ---- the key never shows -------------------------------------------------------

class TestKeyNeverShown(_Case):
    env = {"ANTHROPIC_API_KEY": FAKE_KEY}

    def _failing(self, stream):
        self.sdk = fake_sdk(error=RuntimeError(f"401 invalid x-api-key {FAKE_KEY}"))
        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": self.sdk}), \
                contextlib.redirect_stderr(err):
            if stream:
                body = b"".join(CA.SSEReply([{"role": "user", "content": "hi"}],
                                            "claude-agent:sonnet", self.sdk))
            else:
                body = CA.JSONReply([{"role": "user", "content": "hi"}],
                                    "claude-agent:sonnet", self.sdk).read()
        return body.decode(), err.getvalue()

    def test_a_streamed_error_never_carries_the_key(self):
        body, err = self._failing(True)
        self.assertIn("the turn failed", body)
        self.assertNotIn(FAKE_KEY, body)
        self.assertNotIn(FAKE_KEY, err)

    def test_a_buffered_error_never_carries_the_key(self):
        body, err = self._failing(False)
        self.assertIn("the turn failed", body)
        self.assertNotIn(FAKE_KEY, body)
        self.assertNotIn(FAKE_KEY, err)

    def test_iter_reply_reraises_without_the_key(self):
        self.sdk = fake_sdk(error=RuntimeError(f"bad key {FAKE_KEY}"))
        with self.assertRaises(Exception) as cm:
            list(CA.iter_reply([{"role": "user", "content": "hi"}],
                               "claude-agent:sonnet", stream=False, sdk=self.sdk))
        self.assertNotIn(FAKE_KEY, str(cm.exception))


# ---- `membrane auth claude-agent` ------------------------------------------------

class _AuthCmd(_Case):
    def _run(self, argv, st=None):
        from membrane import cli as C
        out, err = io.StringIO(), io.StringIO()
        status = mock.Mock(return_value=st or {})
        with mock.patch.object(CA, "cli_status", status), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = C._auth(argv)
        return rc, out.getvalue() + err.getvalue(), status

    def _store(self):
        p = self.home / ".membrane" / "auth.json"
        return json.loads(p.read_text()) if p.exists() else None


class TestAuthDefaultWithKey(_AuthCmd):
    env = {"ANTHROPIC_API_KEY": FAKE_KEY}

    def test_no_flag_enables_api_key_mode(self):
        rc, text, status = self._run(["claude-agent"])
        self.assertEqual(rc, 0, text)
        self.assertEqual(self._store(), {"claude-agent": CA.MODE_KEY})
        status.assert_not_called()          # the login is never consulted
        self.assertNotIn(FAKE_KEY, text)    # nor printed
        self.assertNotIn(FAKE_KEY[-4:] + "\n", text)
        self.assertIn("API key", text)


class TestAuthDefaultWithoutKey(_AuthCmd):
    def test_no_key_refuses_and_stores_nothing(self):
        rc, text, status = self._run(["claude-agent"])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(self._store())
        self.assertIn("membrane auth anthropic", text)
        self.assertIn("ANTHROPIC_API_KEY", text)
        status.assert_not_called()


class TestAuthLoginFlag(_AuthCmd):
    def test_login_flag_checks_the_cli_and_stores_the_opt_in(self):
        st = {"installed": True, "logged_in": True, "method": "claude.ai",
              "cli": "/usr/bin/claude", "error": ""}
        rc, text, status = self._run(["claude-agent", "--login"], st)
        self.assertEqual(rc, 0, text)
        status.assert_called()
        self.assertEqual(self._store(), {"claude-agent": CA.MODE_LOGIN})
        self.assertEqual(CA.auth_mode(), CA.MODE_LOGIN)

    def test_login_flag_refuses_without_a_login(self):
        st = {"installed": True, "logged_in": False, "method": "",
              "cli": "/usr/bin/claude", "error": ""}
        rc, _text, _s = self._run(["claude-agent", "--login"], st)
        self.assertNotEqual(rc, 0)
        self.assertIsNone(self._store())


class TestAuthKeyAlreadyStored(_AuthCmd):
    store = {"anthropic": FAKE_KEY}

    def test_enabling_keeps_the_key_where_it_was(self):
        rc, text, _s = self._run(["claude-agent"])
        self.assertEqual(rc, 0, text)
        self.assertEqual(self._store(), {"anthropic": FAKE_KEY,
                                         "claude-agent": CA.MODE_KEY})
        self.assertNotIn(FAKE_KEY, text)


if __name__ == "__main__":
    unittest.main()
