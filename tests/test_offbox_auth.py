"""THE OWNER'S DOOR (issue #253): off-box writes need a token.

Both ports keep their LAN bind. Reads stay open from the LAN, localhost needs
nothing, and every state-changing request from another address needs the
seat token (JWT mode off) or a valid JWT (JWT mode on, unchanged).

An off-box caller is made by the server itself: `_OffBox.get_request` swaps
the accepted socket's peer address for a LAN one, so the handler sees exactly
what a phone on the Wi-Fi would give it (`client_address`), with no header
involved."""
import base64
import http.client
import json
import os
import stat
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from membrane import jwtauth as J
from membrane import seatauth as SA
from tests.test_jwt_auth import SECRET, _StubBackend, _canned_chat, _jwt_env

LAN = "192.168.1.50"


class _OffBox(ThreadingHTTPServer):
    """A server whose every caller looks like a LAN host (the socket's peer
    address as the handler reads it), whatever the test connected from."""
    peer = LAN

    def get_request(self):
        sock, addr = super().get_request()
        return sock, (self.peer, addr[1])


def _start(handler_cls, offbox: bool):
    srv = (_OffBox if offbox else ThreadingHTTPServer)(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _Est:
    """The entity store, recording what reached it."""

    def __init__(self):
        self.edits = []

    def edit(self, spec, **kw):
        self.edits.append((spec, kw))
        return {"ok": True, "eid": spec, "updated": True}

    def list_tracks(self, owner=None):
        return []


def _proxy(offbox: bool):
    from membrane import opencode as OC
    backend = _start(_StubBackend, offbox=False)
    srv = _start(OC.MembraneOpenAIHandler, offbox=offbox)
    srv.backend = f"http://127.0.0.1:{backend.server_address[1]}"
    srv.membrane = type("M", (), {"embedder": None, "store": None})()
    srv.entity_store = _Est()
    return backend, srv


def _req(port, path, method="GET", body=None, headers=None):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


SCRIPT_EDIT = {"spec": "c:thing", "tick_script": "return {run: true}"}


class _TokenFile(unittest.TestCase):
    """Each test: JWT mode off, a fresh seat token file of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tfile = os.path.join(self.tmp.name, "seat-token")
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=self.tfile, MEMBRANE_DATA_DIR=self.tmp.name)
        self.env.start()
        self.tok = SA.ensure()
        self.edits = mock.patch("membrane.lanes._pulse_edit", lambda *a: None)
        self.post = mock.patch("membrane.lanes._entity_post_process", lambda *a: None)
        self.hook = mock.patch("membrane.lanes._active_model_hook", lambda *a: None)
        for p in (self.edits, self.post, self.hook):
            p.start()

    def tearDown(self):
        for p in (self.edits, self.post, self.hook):
            p.stop()
        self.env.stop()
        self.tmp.cleanup()


# ---------------------------------------------------------------------------
# the token file + the CLI
# ---------------------------------------------------------------------------

class TestTokenFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "seat-token")
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=self.path)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_created_0600_even_with_an_open_umask(self):
        old = os.umask(0)
        try:
            tok = SA.ensure()
        finally:
            os.umask(old)
        self.assertGreaterEqual(len(tok), 40)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(SA.ensure(), tok)                       # stable across boots
        self.assertEqual(SA.seat_token(), tok)

    def test_loose_bits_tightened(self):
        SA.ensure()
        os.chmod(self.path, 0o644)
        SA.ensure()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_rotate(self):
        a = SA.ensure()
        b = SA.ensure(rotate=True)
        self.assertNotEqual(a, b)
        self.assertTrue(SA.matches(b))
        self.assertFalse(SA.matches(a))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_default_lives_in_home(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMBRANE_SEAT_TOKEN_FILE", None)
            with mock.patch.dict(os.environ, {"HOME": self.tmp.name}):
                self.assertEqual(str(SA.token_file()),
                                 os.path.join(self.tmp.name, ".membrane", "seat-token"))

    def test_cli_prints_it(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(J.cli_main(["seat"]), 0)
        self.assertEqual(out.getvalue().strip(), SA.ensure())
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(J.cli_main(["seat", "--rotate"]), 0)
        self.assertEqual(out.getvalue().strip(), SA.ensure())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(J.cli_main(["seat", "--path"]), 0)
        self.assertEqual(out.getvalue().strip(), self.path)

    def test_boot_makes_it_and_prints_only_when_asked(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            SA.boot()
        tok = SA.ensure()
        self.assertNotIn(tok, err.getvalue())
        self.assertIn("membrane token seat", err.getvalue())
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"MEMBRANE_SEAT_TOKEN_PRINT": "1"}), contextlib.redirect_stderr(err):
            SA.boot()
        self.assertIn(tok, err.getvalue())

    def test_loopback_is_the_peer_address_only(self):
        def h(addr, headers=None):
            return types.SimpleNamespace(client_address=(addr, 1), headers=headers or {})
        self.assertTrue(SA.is_loopback(h("127.0.0.1")))
        self.assertTrue(SA.is_loopback(h("::1")))
        self.assertTrue(SA.is_loopback(h("::ffff:127.0.0.1")))
        self.assertFalse(SA.is_loopback(h(LAN)))
        self.assertFalse(SA.is_loopback(h("10.0.0.2")))
        self.assertFalse(SA.is_loopback(h("100.100.1.1")))                    # a tailnet is off-box
        # a header never makes a caller local; on a loopback socket it makes it remote
        self.assertFalse(SA.is_loopback(h(LAN, {"X-Forwarded-For": "127.0.0.1"})))
        self.assertFalse(SA.is_loopback(h("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"})))
        self.assertFalse(SA.is_loopback(h("127.0.0.1", {"Forwarded": "for=127.0.0.1"})))
        self.assertFalse(SA.is_loopback(h("127.0.0.1", {"X-Real-IP": "127.0.0.1"})))

    def test_carries_code(self):
        self.assertTrue(SA.carries_code({"spec": "x", "getter_script": "1"}))
        self.assertTrue(SA.carries_code({"watch_script": "x"}))
        self.assertFalse(SA.carries_code({"spec": "x", "tick_script": None, "summary": "s"}))


# ---------------------------------------------------------------------------
# :11500 from the LAN
# ---------------------------------------------------------------------------

class TestProxyOffBox(_TokenFile):
    @classmethod
    def setUpClass(cls):
        cls.backend, cls.srv = _proxy(offbox=True)
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.backend.shutdown()

    def setUp(self):
        super().setUp()
        from membrane import opencode as OC
        self.chat = mock.patch.object(OC.MembraneOpenAIHandler, "_v1_chat", _canned_chat)
        self.chat.start()
        _canned_chat.bodies.clear()
        _StubBackend.seen.clear()
        self.srv.entity_store.edits.clear()

    def tearDown(self):
        self.chat.stop()
        super().tearDown()

    def _bearer(self, tok=None):
        return {"Authorization": f"Bearer {tok or self.tok}"}

    def test_script_edit_refused_without_the_token(self):
        for h in ({}, self._bearer("nope"), {"Authorization": f"Basic {self.tok}"},
                  {"Cookie": f"membrane_jwt={self.tok}"}, {"X-Forwarded-For": "127.0.0.1"}):
            code, b, hd = _req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT, headers=h)
            self.assertEqual(code, 401, h)
            self.assertTrue(hd.get("WWW-Authenticate", "").startswith("Bearer"))
            self.assertEqual(json.loads(b)["error"]["message"], "missing or invalid token")
        self.assertEqual(self.srv.entity_store.edits, [])

    def test_script_edit_with_the_seat_token(self):
        code, b, _ = _req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT, headers=self._bearer())
        self.assertEqual(code, 200, b)
        self.assertEqual(self.srv.entity_store.edits[-1][1]["tick_script"], "return {run: true}")

    def test_every_write_refused(self):
        for p in ("/v1/chat/completions", "/api/chat", "/api/generate", "/v1/embeddings",
                  "/membrane/entity/add", "/membrane/entity/remove",
                  "/membrane/entity/inherit", "/membrane/method/define",
                  "/membrane/method/call", "/membrane/method/each", "/membrane/filetool",
                  "/membrane/learn", "/membrane/forget", "/membrane/intend", "/membrane/fulfill",
                  "/membrane/watch", "/membrane/attention", "/membrane/entity-track",
                  "/membrane/entity-track/remove", "/membrane/junk-cut", "/membrane/delegate",
                  "/membrane/peer/post", "/membrane/whatever", "/api/pull", "/api/delete"):
            code, _, _ = _req(self.port, p, "POST", {"model": "m", "messages": [], "prompt": "x"})
            self.assertEqual(code, 401, p)
        for m in ("PUT", "DELETE", "PATCH"):
            self.assertEqual(_req(self.port, "/api/x", m, {})[0], 401, m)
        self.assertEqual(_req(self.port, "/membrane/intend?text=x")[0], 401)   # a GET that writes
        self.assertEqual(_StubBackend.seen, [])
        self.assertEqual(_canned_chat.bodies, [])

    def test_chat_with_the_token(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        self.assertEqual(_req(self.port, "/v1/chat/completions", "POST", body, headers=self._bearer())[0], 200)
        self.assertEqual(len(_canned_chat.bodies), 1)

    def test_reads_stay_open(self):
        for p in ("/v1/models", "/api/tags", "/membrane/gate-stages"):
            self.assertEqual(_req(self.port, p)[0], 200, p)
        self.assertEqual(_req(self.port, "/api/tags", "HEAD")[0], 200)
        # the read-only POSTs are reads
        for p in sorted(SA.PROXY_READ_POSTS):
            self.assertFalse(SA.proxy_is_write("POST", p), p)
        self.assertEqual(_req(self.port, "/membrane/entity-track/list", "POST", {})[0], 200)


class TestProxyLocal(_TokenFile):
    """Localhost: unchanged -- no token, every route."""

    @classmethod
    def setUpClass(cls):
        cls.backend, cls.srv = _proxy(offbox=False)
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.backend.shutdown()

    def test_writes_need_nothing(self):
        code, b, _ = _req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT)
        self.assertEqual(code, 200, b)
        self.assertEqual(_req(self.port, "/api/pull", "POST", {})[0], 200)

    def test_internal_callers(self):
        from membrane import mcp_server as M
        self.assertEqual(M._http(self.port, "GET", "/membrane/gate-stages"), {"rounds": []})
        self.assertTrue(M._http(self.port, "POST", "/membrane/entity/edit", SCRIPT_EDIT)["ok"])

    def test_forwarded_header_is_off_box(self):
        # a proxy on this machine relaying someone else: off-box until the token
        h = {"X-Forwarded-For": "127.0.0.1"}
        self.assertEqual(_req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT, headers=h)[0], 401)
        self.assertEqual(_req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT,
                              headers={**h, "Authorization": f"Bearer {self.tok}"})[0], 200)
        self.assertEqual(_req(self.port, "/v1/models", headers=h)[0], 200)         # reads stay open


class TestScriptBelt(unittest.TestCase):
    """The second check where scripts are written: an off-box handler
    without the token never lands one, even past the route gate."""

    def test_entity_edit_refuses_code(self):
        from membrane import lanes as L
        with tempfile.TemporaryDirectory() as d, \
                _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(d, "t")):
            est = _Est()
            sent = []
            h = types.SimpleNamespace(
                client_address=(LAN, 1), headers={}, server=types.SimpleNamespace(entity_store=est),
                _paused_write_refused=lambda: False, _json_body=lambda: dict(SCRIPT_EDIT),
                _json_err_raw=lambda c, m: sent.append((c, m)), _json_out=sent.append,
                _entity_audit=lambda *a: None)
            with mock.patch.object(J, "send_unauthorized", lambda hh: sent.append(401)):
                L.MembraneOpenAIHandler._entity_edit(h)
            self.assertEqual(sent, [401])
            self.assertEqual(est.edits, [])


# ---------------------------------------------------------------------------
# :4096 from the LAN
# ---------------------------------------------------------------------------

class _Serve(_TokenFile):
    OFFBOX = True

    @classmethod
    def setUpClass(cls):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        cls.backend, cls.proxy = _proxy(offbox=False)
        cls.hub = NS.SSEHub()
        cls.sessions = _store()
        cls.msgs = NS.MessageStore()
        runner = _StubRunner()
        runner.status = lambda sid: {"type": "idle"}
        handler = NS.make_handler(hub=cls.hub, sessions=cls.sessions, messages=cls.msgs,
                                  config=_CFG, runner=runner, srv=cls.proxy)
        cls.httpd = _start(handler, offbox=cls.OFFBOX)
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.proxy.shutdown()
        cls.backend.shutdown()

    def setUp(self):
        super().setUp()
        self.std = mock.patch.dict(os.environ, {"MEMBRANE_STANDALONE": "1"})
        self.std.start()

    def tearDown(self):
        self.std.stop()
        super().tearDown()

    def _req(self, p, method="GET", body=None, headers=None):
        code, b, h = _req(self.port, p, method, body, headers)
        try:
            return code, json.loads(b or b"{}"), h
        except ValueError:
            return code, b, h


class TestServeOffBox(_Serve):
    def test_writes_refused_without_the_token(self):
        for method, p, body in (("POST", "/session", {}), ("POST", "/ui/serve/session", {}),
                                ("PATCH", "/session/x", {}), ("DELETE", "/session/x", None),
                                ("POST", "/membrane/toybox/poke", {}), ("POST", "/membrane/seat/control", {}),
                                ("POST", "/membrane/models", {}), ("POST", "/membrane/field/purge", {}),
                                ("POST", "/membrane/ha/token", {}), ("POST", "/tui/submit-prompt", {}),
                                ("POST", "/membrane/voice", {}), ("POST", "/ui/stt", {}),
                                ("POST", "/ui/log", {})):
            code, _, h = self._req(p, method, body)
            self.assertEqual(code, 401, (method, p))
            self.assertTrue(h.get("WWW-Authenticate", "").startswith("Bearer"))

    def test_reads_stay_open(self):
        for p in ("/session", "/global/health", "/ui/conf", "/", "/ui/serve/session", "/config"):
            self.assertEqual(self._req(p)[0], 200, p)

    def test_bearer_basic_and_cookie(self):
        self.assertEqual(self._req("/session", "POST", {}, {"Authorization": f"Bearer {self.tok}"})[0], 200)
        basic = "Basic " + base64.b64encode(f"opencode:{self.tok}".encode()).decode()
        self.assertEqual(self._req("/session", "POST", {}, {"Authorization": basic})[0], 200)
        self.assertEqual(self._req("/session", "POST", {}, {"Cookie": f"membrane_jwt={self.tok}"})[0], 200)
        wrong = "Basic " + base64.b64encode(b"opencode:pw").decode()
        self.assertEqual(self._req("/session", "POST", {}, {"Authorization": wrong})[0], 401)

    def test_ui_login_with_the_seat_token(self):
        code, out, _ = self._req("/ui/conf")
        self.assertEqual((out.get("auth"), out.get("owner")), ("seat", False))
        code, _, h = self._req("/ui/login", "POST", {"token": "nope"})
        self.assertEqual(code, 401)
        self.assertIsNone(h.get("Set-Cookie"))
        code, _, h = self._req("/ui/login", "POST", f"token={self.tok}".encode(),
                               {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 415)
        code, out, h = self._req("/ui/login", "POST", {"token": self.tok})
        self.assertEqual(code, 200)
        ck = h["Set-Cookie"]
        self.assertTrue(ck.startswith(f"membrane_jwt={self.tok};"))
        for attr in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age="):
            self.assertIn(attr, ck)
        cookie = {"Cookie": f"membrane_jwt={self.tok}"}
        self.assertEqual(self._req("/ui/conf", headers=cookie)[1]["owner"], True)
        _, s, _ = self._req("/ui/serve/session", "POST", {}, cookie)
        code, _, _ = self._req(f"/ui/serve/session/{s['id']}/message", "POST",
                               {"parts": [{"type": "text", "text": "hi"}]}, cookie)
        self.assertEqual(code, 200)
        code, _, h = self._req("/ui/logout", "POST", {})
        self.assertEqual(code, 200)
        self.assertIn("Max-Age=0", h["Set-Cookie"])

    def test_proxied_page_logs_in_with_the_token(self):
        fwd = {"X-Forwarded-For": "8.8.8.8"}
        self.assertEqual(self._req("/ui/conf", headers=fwd)[0], 403)            # the LAN guard, as before
        self.assertEqual(self._req("/ui/conf", headers={**fwd, "Cookie": f"membrane_jwt={self.tok}"})[0], 200)

    def test_jwt_mode_unchanged(self):
        with _jwt_env(MEMBRANE_JWT_SECRET=SECRET, MEMBRANE_SEAT_TOKEN_FILE=self.tfile,
                      MEMBRANE_STANDALONE="1", MEMBRANE_DATA_DIR=self.tmp.name):
            alice = J.mint(SECRET, "alice", name="Alice")
            self.assertEqual(self._req("/session")[0], 401)                    # reads need a token too
            self.assertEqual(self._req("/session", "POST", {}, {"Authorization": f"Bearer {self.tok}"})[0], 401)
            self.assertEqual(self._req("/ui/login", "POST", {"token": self.tok})[0], 401)
            self.assertEqual(self._req("/session", "POST", {}, {"Authorization": f"Bearer {alice}"})[0], 200)
            self.assertEqual(self._req("/ui/conf", headers={"Authorization": f"Bearer {alice}"})[1]["auth"], "jwt")


class TestServeLocal(_Serve):
    OFFBOX = False

    def test_writes_need_nothing(self):
        code, s, _ = self._req("/session", "POST", {})
        self.assertEqual(code, 200)
        self.assertEqual(self._req(f"/session/{s['id']}", "PATCH", {"title": "t"})[0], 200)
        self.assertEqual(self._req("/ui/conf")[1]["owner"], True)

    def test_forwarded_header_is_off_box(self):
        self.assertEqual(self._req("/session", "POST", {}, {"X-Forwarded-For": "127.0.0.1"})[0], 401)


class TestProxyJWTOffBox(unittest.TestCase):
    """JWT mode from the LAN: a valid JWT writes, the seat token does not."""

    @classmethod
    def setUpClass(cls):
        cls.backend, cls.srv = _proxy(offbox=True)
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.backend.shutdown()

    def test_jwt(self):
        with tempfile.TemporaryDirectory() as d, \
                _jwt_env(MEMBRANE_JWT_SECRET=SECRET, MEMBRANE_SEAT_TOKEN_FILE=os.path.join(d, "t")), \
                mock.patch("membrane.lanes._pulse_edit", lambda *a: None), \
                mock.patch("membrane.lanes._entity_post_process", lambda *a: None), \
                mock.patch("membrane.lanes._active_model_hook", lambda *a: None):
            seat = SA.ensure()
            alice = J.mint(SECRET, "alice")
            self.assertEqual(_req(self.port, "/v1/models")[0], 401)
            self.assertEqual(_req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT,
                                  {"Authorization": f"Bearer {seat}"})[0], 401)
            self.assertEqual(_req(self.port, "/membrane/entity/edit", "POST", SCRIPT_EDIT,
                                  {"Authorization": f"Bearer {alice}"})[0], 200)


# ---------------------------------------------------------------------------
# Home Assistant + Docker carry the token
# ---------------------------------------------------------------------------

class TestCarriers(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(__file__), "..")

    def _read(self, *p):
        with open(os.path.join(self.ROOT, *p)) as f:
            return f.read()

    def test_ha_sends_the_seat_token_and_can_change_it(self):
        # HA already speaks Basic with the connect form's password on every
        # call (relay, conversation, coordinator, ha_access); the seat reads
        # the password as the token. #283: the token lives on its seat in the
        # zoo's list -- the options' seat forms set or change it after setup,
        # and the migration carries an options-set token onto the seat.
        cf = self._read("custom_components", "membrane", "config_flow.py")
        zoo = self._read("custom_components", "membrane", "zoo.py")
        self.assertIn('vol.Optional("token", default=s.get("token", ""))', cf)
        self.assertIn("opts[CONF_SERVE_PASS] if CONF_SERVE_PASS in opts", zoo)
        for fn in (("strings.json",), ("translations", "en.json")):
            s = json.loads(self._read("custom_components", "membrane", *fn))
            self.assertIn("token", s["options"]["step"]["edit_seat"]["data"])
            self.assertIn("membrane token seat", s["config"]["step"]["connect"]["description"])

    def test_the_doctrine_names_the_owner(self):
        d = self._read("doctrine.md")
        self.assertIn("a seat runs code only from its owner", d)
        self.assertIn("The owner is a credential", d)
        self.assertIn("never a speaker line", d)
        self.assertIn("Self may refuse others, never its owner", d)

    def test_docker_prints_the_token_at_boot(self):
        self.assertIn("MEMBRANE_SEAT_TOKEN_PRINT=1", self._read("docker", "docker-entrypoint.sh"))


if __name__ == "__main__":
    unittest.main()
