"""A decided or dropped Decision card tells the CHAT (one message, one turn)
unless a watch loop planted the card: then the event stays in the loops' pool."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402

VAR = {"type": "bool"}


class _Pool:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


def _setup(**props):
    d = Path(tempfile.mkdtemp())
    est = EntityStore(path=str(d / "e.json"), shared=False)
    led = UserLedger(str(d / "f.ledger.jsonl"))
    est._events_pool = pool = _Pool()
    est._decision_chat = chat = []
    est._decision_chat = lambda text, ev, _c=chat: _c.append((text, ev))
    est._chat_log = chat
    eid = est.add(cls="Decision", name="merge PR 70", summary="x")["eid"]
    assert est.edit(eid, properties={"status": "open", "var": VAR, **props})["ok"]
    return est, led, pool, chat, eid


class TestDecisionEventToChat(unittest.TestCase):
    def test_answer_goes_to_chat_not_pool(self):
        est, led, pool, chat, eid = _setup(planted_by="chat")
        self.assertTrue(DM.answer(est, led, eid, True)["ok"])
        self.assertEqual(len(chat), 1)
        self.assertIn("decided", chat[0][0])
        self.assertEqual(pool.events, [])

    def test_drop_goes_to_chat_not_pool(self):
        est, led, pool, chat, eid = _setup(planted_by="chat")
        self.assertTrue(DM.drop(est, eid)["ok"])
        self.assertEqual(len(chat), 1)
        self.assertIn("dropped", chat[0][0])
        self.assertEqual(pool.events, [])

    def test_loop_planted_card_stays_in_the_pool(self):
        est, led, pool, chat, eid = _setup(planted_by="loop")
        DM.answer(est, led, eid, True)
        self.assertEqual(chat, [])
        self.assertEqual(len(pool.events), 1)

    def test_loop_planted_drop_stays_in_the_pool(self):
        est, led, pool, chat, eid = _setup(planted_by="loop")
        DM.drop(est, eid)
        self.assertEqual(chat, [])
        self.assertEqual(pool.events[0]["value"], "dropped")

    def test_no_chat_sink_falls_back_to_the_pool(self):
        est, led, pool, chat, eid = _setup()
        del est._decision_chat
        DM.answer(est, led, eid, True)
        self.assertEqual(len(pool.events), 1)

    def test_chat_message_is_flagged_so_it_cannot_chain(self):
        est, led, pool, chat, eid = _setup(planted_by="chat")
        DM.answer(est, led, eid, True)
        self.assertTrue(chat[0][1].get("kind") == "decision")


if __name__ == "__main__":
    unittest.main()


class TestPlanter(unittest.TestCase):
    def _planted(self, writer):
        from membrane import writes as W
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        with W.as_writer(writer):
            res = est.add(cls="Decision", name="which runner", summary="x")
            DM.stamp_planter(est, res)
        return est.resolve(res["eid"], exact=True).properties.get("planted_by")

    def test_loop_writer_stamps_loop(self):
        self.assertEqual(self._planted("llm:loop"), "loop")

    def test_chat_writer_stamps_chat(self):
        self.assertEqual(self._planted("llm"), "chat")


class TestWakeChat(unittest.TestCase):
    def test_posts_a_user_row_and_starts_one_turn(self):
        import threading, types
        from membrane import chat_io
        ran = threading.Event(); got = {}
        class R:
            sessions = types.SimpleNamespace(list=lambda: [{"id": "s1", "owner": "bob", "time": {"updated": 1}}])
            _agents = {}
            def _mint_user_row(self, sid, text): return {"id": "m1"}, [{"text": text}]
            def run(self, sid, text, user_info=None, user_parts=None):
                got.update(sid=sid, text=text); ran.set()
        srv = types.SimpleNamespace(_loop_runner=R())
        self.assertEqual(chat_io.wake_chat(srv, "decided: x = yes"), "s1")
        self.assertTrue(ran.wait(2))
        self.assertEqual(got, {"sid": "s1", "text": "decided: x = yes"})

    def test_no_chat_returns_empty(self):
        import types
        from membrane import chat_io
        srv = types.SimpleNamespace(_loop_runner=None)
        self.assertEqual(chat_io.wake_chat(srv, "x"), "")


class TestWakeChatActive(unittest.TestCase):
    def test_wakes_the_seats_most_recently_active_chat_whoever_owns_it(self):
        import threading, types
        from membrane import chat_io
        ran = threading.Event(); got = {}
        class R:
            sessions = types.SimpleNamespace(list=lambda: [
                {"id": "old", "owner": "bob", "time": {"updated": 1}},
                {"id": "live", "owner": "", "time": {"updated": 9}},
                {"id": "priv", "owner": "bob", "blurp": True, "time": {"updated": 99}}])
            _agents = {}
            def _mint_user_row(self, sid, text): return {"id": "m1"}, [{"text": text}]
            def run(self, sid, text, user_info=None, user_parts=None):
                got["sid"] = sid; ran.set()
        srv = types.SimpleNamespace(_loop_runner=R())
        self.assertEqual(chat_io.wake_chat(srv, "decided: x = yes"), "live")
        self.assertTrue(ran.wait(2)); self.assertEqual(got["sid"], "live")
