"""Card lifecycle: open -> decided -> (in_flight ->) closed, plus dropped.
A card carries a work_needed tag (set at creation, overridable when answering):
no work -> the answer closes it; work -> it stays decided until the watcher
loop moves it to in_flight, and it closes only with a receipt. Only the
operator drops a card."""
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


def _store():
    d = Path(tempfile.mkdtemp())
    return (EntityStore(path=str(d / "e.json"), shared=False),
            UserLedger(str(d / "f.ledger.jsonl")))


def _card(est, name, **props):
    eid = est.add(cls="Decision", name=name, summary=name)["eid"]
    assert est.edit(eid, properties={"status": "open", "var": VAR, **props})["ok"]
    return eid


def _status(est, eid):
    return est.resolve(eid, exact=True).properties["status"]


class _Pool:
    def __init__(self):
        self.events = []

    def put(self, ev):
        self.events.append(ev)


class TestLifecycle(unittest.TestCase):
    def test_no_work_answer_closes_the_card(self):
        est, led = _store()
        eid = _card(est, "pure choice", work_needed=False)
        self.assertTrue(DM.answer(est, led, eid, "yes")["ok"])
        self.assertEqual(_status(est, eid), "closed")

    def test_work_answer_stays_decided_and_wakes_the_loops(self):
        est, led = _store()
        est._events_pool = pool = _Pool()
        eid = _card(est, "needs work", work_needed=True)
        self.assertTrue(DM.answer(est, led, eid, "yes")["ok"])
        self.assertEqual(_status(est, eid), "decided")
        self.assertTrue(pool.events[0]["work_needed"])

    def test_no_work_answer_still_notifies_the_loops(self):
        est, led = _store()
        est._events_pool = pool = _Pool()
        eid = _card(est, "pure", work_needed=False)
        DM.answer(est, led, eid, "no")
        self.assertEqual(len(pool.events), 1)
        self.assertFalse(pool.events[0]["work_needed"])

    def test_answer_can_override_the_tag(self):
        est, led = _store()
        a = _card(est, "tagged work", work_needed=True)
        b = _card(est, "tagged none", work_needed=False)
        DM.answer(est, led, a, "yes", work=False)
        DM.answer(est, led, b, "yes", work=True)
        self.assertEqual((_status(est, a), _status(est, b)), ("closed", "decided"))

    def test_agent_moves_decided_work_card_to_in_flight(self):
        est, led = _store()
        eid = _card(est, "w", work_needed=True)
        DM.answer(est, led, eid, "yes")
        self.assertTrue(est.edit(eid, properties={"status": "in_flight"})["ok"])

    def test_agent_cannot_start_work_on_a_no_work_card(self):
        est, led = _store()
        eid = _card(est, "n", work_needed=False)
        DM.answer(est, led, eid, "yes")
        self.assertFalse(est.edit(eid, properties={"status": "in_flight"})["ok"])

    def test_closing_needs_a_receipt(self):
        est, led = _store()
        eid = _card(est, "w", work_needed=True)
        DM.answer(est, led, eid, "yes")
        est.edit(eid, properties={"status": "in_flight"})
        self.assertFalse(est.edit(eid, properties={"status": "closed"})["ok"])
        self.assertTrue(est.edit(eid, properties={"receipt": "PR merged, CI green"})["ok"])
        self.assertTrue(est.edit(eid, properties={"status": "closed"})["ok"])

    def test_open_card_cannot_be_closed_by_an_agent(self):
        est, _ = _store()
        eid = _card(est, "o", work_needed=True, receipt="x")
        self.assertFalse(est.edit(eid, properties={"status": "closed"})["ok"])

    def test_closed_card_is_locked(self):
        est, led = _store()
        eid = _card(est, "c", work_needed=False)
        DM.answer(est, led, eid, "yes")
        self.assertFalse(est.edit(eid, properties={"status": "open"})["ok"])
        self.assertFalse(DM.answer(est, led, eid, "no")["ok"])

    def test_agent_cannot_drop(self):
        est, _ = _store()
        eid = _card(est, "d")
        self.assertFalse(est.edit(eid, properties={"status": "dropped"})["ok"])

    def test_operator_drops_an_open_card(self):
        est, led = _store()
        est._events_pool = pool = _Pool()
        eid = _card(est, "d")
        res = DM.drop(est, eid)
        self.assertTrue(res["ok"])
        self.assertEqual(_status(est, eid), "dropped")
        self.assertEqual(pool.events[0]["kind"], "decision")
        self.assertFalse(DM.answer(est, led, eid, "yes")["ok"])

    def test_only_open_cards_drop(self):
        est, led = _store()
        eid = _card(est, "x", work_needed=False)
        DM.answer(est, led, eid, "yes")
        self.assertFalse(DM.drop(est, eid)["ok"])

    def test_board_lists_every_status(self):
        est, _ = _store()
        eid = _card(est, "d")
        DM.drop(est, eid)
        self.assertEqual(DM.board(est)["cards"][0]["status"], "dropped")


if __name__ == "__main__":
    unittest.main()


class AnsweredCardsStayAnswered(unittest.TestCase):
    """An answered card in flight is past, not open: it can't be answered
    again, and no agent can put it back to open (that's #470 step 3's
    timelines). A legacy in_flight card (the old open -> in_flight -> decided
    order, never answered) still takes its first answer."""

    def _in_flight(self):
        est, led = _store()
        eid = _card(est, "deploy", work_needed=True)
        self.assertTrue(DM.answer(est, led, eid, True)["ok"])
        self.assertTrue(est.edit(eid, properties={"status": "in_flight"})["ok"])
        return est, led, eid

    def test_an_in_flight_answered_card_takes_no_second_answer(self):
        est, led, eid = self._in_flight()
        n = len(led.entries()) if hasattr(led, "entries") else None
        res = DM.answer(est, led, eid, False)
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("conflict"))
        p = est.resolve(eid, exact=True).properties
        self.assertIs(p["value"], True)
        self.assertEqual(p["status"], "in_flight")
        if n is not None:
            self.assertEqual(len(led.entries()), n)

    def test_no_agent_puts_an_answered_card_back_to_open(self):
        est, led, eid = self._in_flight()
        self.assertFalse(est.edit(eid, properties={"status": "open"}).get("ok") and
                         _status(est, eid) == "open")
        self.assertEqual(_status(est, eid), "in_flight")

    def test_a_legacy_unanswered_in_flight_card_still_takes_its_answer(self):
        est, led = _store()
        eid = _card(est, "legacy")
        self.assertTrue(est.edit(eid, properties={"status": "in_flight"})["ok"])
        self.assertTrue(DM.answer(est, led, eid, True)["ok"])
