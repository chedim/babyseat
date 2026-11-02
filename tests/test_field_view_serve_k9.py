"""#772: the field view reads and says through the SERVE port (the page's HSERVE), not only the proxy.

fieldview.js calls /membrane/work-graph, /membrane/entity-history, /membrane/item-chat and
/membrane/item-chat/say; the serve handler (server.py) had none of them and answered 404."""
import json
import os

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from tests.test_nerve_pause_348 import _Env, _seat, _serve  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402


class TestTheFieldViewOnTheServePort(_Env):
    def setUp(self):
        super().setUp()
        self.srv, self.r = _seat(self.tmp.name)
        est = self.srv.entity_store
        self.goal = est.add(cls="Goal", name="ship the view", summary="s")["eid"]
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
        super().tearDown()

    def _go(self, method, path, body=None):
        if self.httpd is None:
            self.httpd = _serve(self.srv, self.r, False)
        code, b, _ = _req(self.httpd.server_address[1], path, method, body, None)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def test_the_work_graph_is_served(self):
        code, out = self._go("GET", "/membrane/work-graph")
        self.assertEqual(code, 200, out)
        self.assertIn("nodes", out)
        self.assertIn(self.goal, [n.get("id") or n.get("eid") for n in out["nodes"]])

    def test_the_history_and_the_mini_chat_are_served(self):
        code, out = self._go("GET", "/membrane/entity-history?spec=" + self.goal)
        self.assertEqual(code, 200, out)
        code, out = self._go("GET", "/membrane/item-chat?spec=" + self.goal)
        self.assertEqual(code, 200, out)
        self.assertEqual(out.get("messages"), [])

    def test_saying_in_the_mini_chat_lands(self):
        code, out = self._go("POST", "/membrane/item-chat/say", {"spec": self.goal, "text": "hello"})
        self.assertEqual(code, 200, out)
        self.assertTrue(out.get("ok") and out.get("message"), out)   # listing them is test_item_card_k7's
        code, out = self._go("POST", "/membrane/item-chat/say", {"spec": self.goal, "text": ""})
        self.assertEqual(code, 400)
