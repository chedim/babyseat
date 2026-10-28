"""A GOAL RE-ASKING AN ANSWERED QUESTION IS FED THE ANSWER (#650)."""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.decision_map import STATUS_OPEN  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

Q = "on operator: which runner should the suite use"
GOAL = "membrane::goal:g650"


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))


def _est():
    est = EntityStore(bucket=f"test-ask-650-{time.time_ns()}", shared=False)
    est._events_pool = _Pool()
    return est


def _answered(est, status, value=None):
    """Ask once, then the operator settles the card (status/value set directly)."""
    props = {}
    card = WA.ask_operator(est, GOAL, props, Q)
    assert card
    doc = est.docs[card]
    p = dict(doc.properties)
    p.update({"status": status, "value": value})
    doc.properties = p
    est.upsert(doc)
    return card, props


class AskOperatorAgain(unittest.TestCase):
    def test_answered_card_is_fed_not_replanted(self):
        est = _est()
        card, _ = _answered(est, "decided", "pytest")
        props = {}                                  # the goal asks again
        self.assertEqual(WA.ask_operator(est, GOAL, props, Q), "")
        evs = est._events_pool.items
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["kind"], evs[0]["entity"], evs[0]["value"]),
                         ("decision", card, "pytest"))
        self.assertEqual(evs[0]["blocks"], [GOAL])
        self.assertEqual(props["answer_fed"], [card])

    def test_still_blocked_after_the_feed_plants_a_fresh_card(self):
        est = _est()
        old, _ = _answered(est, "decided", "pytest")
        props = {}
        WA.ask_operator(est, GOAL, props, Q)        # the feed
        fresh = WA.ask_operator(est, GOAL, props, Q)
        self.assertTrue(fresh)
        self.assertNotEqual(fresh, old)
        self.assertEqual(est.docs[fresh].properties["status"], STATUS_OPEN)
        self.assertEqual(est.docs[fresh].properties["blocks"], [GOAL])
        self.assertEqual(len(est._events_pool.items), 1)   # fed once, not twice

    def test_dropped_card_event_says_dropped(self):
        est = _est()
        _answered(est, "dropped")
        self.assertEqual(WA.ask_operator(est, GOAL, {}, Q), "")
        self.assertEqual(est._events_pool.items[0]["value"], "dropped")


if __name__ == "__main__":
    unittest.main()
