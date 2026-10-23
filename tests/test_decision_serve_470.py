"""#470: the decision board reads and answers through the serve port.

The page (membrane/ui/decisions.js) calls /ui/serve/membrane/decisions and
/ui/serve/membrane/decision/answer, which reach the serve handler (server.py)
on :4096, not the proxy where step 1 put the routes -- the live board said
"can't read the decisions: /membrane/decisions -> 404". The serve port now has
them, plus /membrane/decision/chat, which records the chat a card opened (the
board's only other write; entity/edit is not a serve route)."""
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import seatauth as SA  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402
from tests.test_nerve_pause_348 import _Env, _seat, _serve  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402


class TestTheBoardOnTheServePort(_Env):
    def setUp(self):
        super().setUp()
        self.srv, self.r = _seat(self.tmp.name)
        self.srv.user_ledger = UserLedger(str(Path(self.tmp.name) / "f.ledger.jsonl"))
        est = self.srv.entity_store
        self.card = est.add(cls="Decision", name="turn limit", summary="how long a turn may run")["eid"]
        est.edit(self.card, properties={"status": "open", "var": {"type": "int", "unit": "s"}, "pick": 1800})
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
        super().tearDown()

    def _go(self, method, path, body=None, offbox=False):
        if self.httpd is None:
            self.httpd = _serve(self.srv, self.r, offbox)
        code, b, _ = _req(self.httpd.server_address[1], path, method, body, None)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def test_the_board_reads_its_cards(self):
        code, out = self._go("GET", "/membrane/decisions")
        self.assertEqual(code, 200)
        self.assertEqual([c["title"] for c in out["cards"]], ["turn limit"])

    def test_an_answer_decides_the_card_and_writes_the_ledger(self):
        code, out = self._go("POST", "/membrane/decision/answer", {"card": self.card, "value": 1800})
        self.assertEqual(code, 200, out)
        self.assertEqual(self.srv.entity_store.docs[self.card].properties["status"], "decided")
        self.assertIn("turn limit = 1800 s", self.srv.user_ledger.entries()[-1]["text"])
        code, out = self._go("POST", "/membrane/decision/answer", {"card": self.card, "value": 60})
        self.assertEqual(code, 409)
        code, out = self._go("POST", "/membrane/decision/answer", {"card": "no such", "value": 1})
        self.assertEqual(code, 400)

    def test_the_chat_a_card_opened_is_recorded(self):
        code, out = self._go("POST", "/membrane/decision/chat", {"card": self.card, "chat": "ses_abc"})
        self.assertEqual(code, 200, out)
        self.assertEqual(self.srv.entity_store.docs[self.card].properties["chat"], "ses_abc")
        code, _ = self._go("POST", "/membrane/decision/chat", {"card": self.card, "chat": ""})
        self.assertEqual(code, 400)

    def test_an_answer_from_the_chat_names_its_message(self):
        # the cockpit: the card was talked over in its chat, and ✓ came with that chat's newest message
        code, out = self._go("POST", "/membrane/decision/answer",
                             {"card": self.card, "value": 1800, "conv": "ses_abc", "message": "msg_42"})
        self.assertEqual(code, 200, out)
        p = self.srv.entity_store.docs[self.card].properties
        self.assertEqual((p["chat"], p["message"]), ("ses_abc", "msg_42"))
        code, out = self._go("GET", "/membrane/decisions")
        self.assertEqual(out["cards"][0]["message"], "msg_42")

    def test_a_message_without_its_chat_is_not_kept(self):
        code, out = self._go("POST", "/membrane/decision/answer", {"card": self.card, "value": 1800, "message": "msg_42"})
        self.assertEqual(code, 200, out)
        self.assertNotIn("message", self.srv.entity_store.docs[self.card].properties)

    def test_the_cockpit_reads_the_open_goals(self):
        est = self.srv.entity_store
        g = est.add(cls="Goal", name="ship the cockpit", summary="the board turned 90 degrees")["eid"]
        done = est.add(cls="Goal", name="an old goal")["eid"]
        est.edit(done, properties={"status": "fulfilled"})
        code, out = self._go("GET", "/membrane/goals")
        self.assertEqual(code, 200, out)
        self.assertEqual([(x["eid"], x["title"], x["summary"]) for x in out["goals"]],
                         [(g, "ship the cockpit", "the board turned 90 degrees")])
        self.assertIn("active", out["goals"][0])

    def test_off_box_an_answer_needs_the_seat_token(self):
        SA.ensure()
        code, _ = self._go("POST", "/membrane/decision/answer", {"card": self.card, "value": 1800}, offbox=True)
        self.assertEqual(code, 401)
        self.assertEqual(self.srv.entity_store.docs[self.card].properties["status"], "open")


def test_the_board_records_its_chat_through_the_serve_route():
    ui = Path(__file__).resolve().parent.parent / "membrane" / "ui"
    src = (ui / "decisions.js").read_text() + (ui / "home.js").read_text()
    assert '"/membrane/decision/chat"' in src
    assert '"/membrane/entity/edit"' not in src
