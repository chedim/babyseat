"""The field dashboard's reads through the proxy (2026-10-08, red first):
exactly the brain's read-only field surfaces, GET only."""
import asyncio
import unittest

from ai_terminal import serve_proxy as SP
from ai_terminal.serve_proxy import allowed_path, read_only_path


class TestFieldPaths(unittest.TestCase):
    def test_field_reads_allowed(self):
        for p in ("membrane/state", "membrane/map/field", "membrane/field/entity",
                  "membrane/map/distance",
                  "membrane/map/field3d", "membrane/map/tile", "membrane/map/locate", "v1/models",
                  "membrane/entity/read", "membrane/entity-related"):
            self.assertTrue(allowed_path(p), p)
            self.assertTrue(read_only_path(p), p)

    def test_writes_and_other_seat_routes_denied(self):
        for p in ("membrane/entity/add", "membrane/run", "membrane/learn",
                  "membrane/map/cloud/../../entity/add", "membrane",
                  "membrane/intend", "v1/chat/completions"):
            self.assertFalse(allowed_path(p), p)

    def test_chat_paths_are_not_read_only(self):
        self.assertFalse(read_only_path("session"))
        self.assertFalse(read_only_path("session/x/message"))


class TestMindsModels(unittest.TestCase):
    def test_reachable_and_admin_gated(self):
        self.assertTrue(allowed_path("membrane/models"))
        self.assertFalse(read_only_path("membrane/models"))      # POST passes the read-only gate...
        self.assertIn("membrane/models", SP.ADMIN_WRITE_EXACT)   # ...behind the admin check


class TestLiveLayer(unittest.TestCase):
    """The viewer's live layer (2026-10-08): field.* / mind.* frames are
    session-less -- admins see them, nobody else does."""

    def test_admins_only(self):
        for t in ("field.moved", "field.applied", "mind.touch", "mind.focus"):
            ev = {"type": t, "properties": {"eids": ["f:a"]}}
            self.assertTrue(SP.visible_event(ev, {}, "u1", True), t)
            self.assertFalse(SP.visible_event(ev, {}, "u1", False), t)

    def test_other_session_less_frames_stay_inside(self):
        self.assertFalse(SP.visible_event({"type": "session.updated", "properties": {}}, {}, "u1", True))

    def test_chat_frames_keep_their_owner_rule(self):
        ev = {"type": "message.updated", "properties": {"sessionID": "s9"}}
        self.assertFalse(SP.visible_event(ev, {"s9": "other"}, "u1", True))
        self.assertTrue(SP.visible_event(ev, {"s9": "u1"}, "u1", False))


class _User:
    id = "u1"
    name = "D"


class _Req:
    def __init__(self, method, path):
        self.method = method
        self.query = {}
        self.match_info = {"path": path}
        self.app = {"hass": type("H", (), {"data": {"ai_terminal": {
            "serve_url": "http://b:4096", "serve_user": "o", "serve_pass": "p",
            "owners": {}, "owners_store": None}}})()}

    def get(self, k, d=None):
        return _User() if k == "hass_user" else d

    async def read(self):
        return b"{}"


class TestMethodGate(unittest.TestCase):
    def test_post_to_a_read_path_is_405(self):
        r = asyncio.run(SP.ServeProxyView().post(_Req("POST", "membrane/state"),
                                                 path="membrane/state"))
        self.assertEqual(r.status, 405)


if __name__ == "__main__":
    unittest.main()
