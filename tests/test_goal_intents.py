"""THE GOAL-BACKED INTENT BOARD (the operator's design: 'the fact that a
goal became active makes it an intent'): goals are LONG-LIVED shared
entities (no TTL); an intent is not an object -- it is a goal CURRENTLY
SELECTED into the seat's active set (top-K by urgency: present-touch +
recency + hit-rate, hysteresis on the swap). Fulfillment is SHARED (the
goal's status flips on the entity -- a project commitment done is done
for every seat); drop intent is SEAT-LOCAL suppression (the goal stays
open for other seats). The legacy intents.json migrates once into Goal
entities + pins."""

import unittest

from membrane.entities import (CLASS_GOAL, CLASS_SESSION, EntityStore,
                               QuantumEntity, MEMBRANE_NS)
from membrane.intent import (GoalSelection, IntentBoard, GOAL_SET_CAP,
                             GOAL_SWAP_MARGIN)


def _store():
    return EntityStore(bucket="test-goal-intents", shared=False)


def _goal(est, text, vec, born=0.0, hits=0):
    import hashlib, time
    slug = hashlib.sha256(text.lower().encode()).hexdigest()[:12]
    ent = QuantumEntity(
        eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
        properties={"name": text[:120], "summary": text[:600],
                    "status": "open",
                    "born": born or time.time(), "hits": hits})
    ent.position = list(vec)
    est.upsert(ent)
    return ent


class TestGoalSelection(unittest.TestCase):
    def setUp(self):
        self.est = _store()
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        self.est.upsert(now)
        self.sel = GoalSelection(self.est)

    def test_top_k_ranks_by_present_touch(self):
        """The goal nearest session:now's position ranks first (the
        present-touch weight dominates when recency/hits are equal)."""
        import time
        now = time.time()
        _goal(self.est, "far goal", [1.0, 0.0, 0.0], born=now)
        _goal(self.est, "near goal", [0.0, 1.0, 0.0], born=now)
        acts = self.sel.open_intents(now, now_vec=[0.0, 1.0, 0.0])
        self.assertEqual(acts[0].text, "near goal")
        self.assertLessEqual(len(acts), GOAL_SET_CAP)

    def test_no_ttl(self):
        """Goals do not expire: the view's expires is always 0 (the
        short-livedness is the selection dynamics, not a countdown)."""
        import time
        g = _goal(self.est, "old goal", [1.0, 0.0], born=time.time() - 86400)
        acts = self.sel.open_intents(time.time(), [1.0, 0.0])
        self.assertTrue(any(i.text == "old goal" for i in acts))
        self.assertTrue(all(i.expires == 0.0 for i in acts))

    def test_hysteresis_no_flapping(self):
        """An incumbent stays when the challenger's edge is under the
        swap margin -- the set does not flap on small score noise."""
        import time
        now = time.time()
        _goal(self.est, "incumbent", [0.0, 1.0, 0.0], born=now, hits=10)
        _goal(self.est, "challenger", [1.0, 0.0, 0.0], born=now, hits=10)
        now_vec = [0.0, 1.0, 0.0]
        self.sel.refresh(now, now_vec)
        first = list(self.sel._active)
        # the challenger is orthogonal-ish (0.5 present weight): under the
        # margin vs the incumbent's hits-saturated score -> no swap
        self.sel.refresh(now + 1, now_vec)
        self.assertEqual(self.sel._active, first)

    def test_drop_is_seat_local(self):
        """Suppression removes the goal from THIS seat's set only -- the
        goal's status stays open (other seats keep selecting it)."""
        import time
        g = _goal(self.est, "suppress me", [1.0, 0.0], born=time.time())
        self.sel.refresh(time.time(), [1.0, 0.0])
        self.sel.suppressed.add(g.eid)
        acts = self.sel.open_intents(time.time(), [1.0, 0.0])
        self.assertFalse(any(i.text == "suppress me" for i in acts))
        self.assertEqual((self.est.docs.get(g.eid)
                          .properties or {}).get("status"), "open")

    def test_pinned_beats_score(self):
        """An explicit pin (the operator's hand / membrane_intend) lifts a
        goal over a higher-scoring one."""
        import time
        now = time.time()
        _goal(self.est, "hot goal", [1.0, 0.0], born=now, hits=50)
        low = _goal(self.est, "pinned goal", [0.0, 1.0], born=now)
        now_vec = [1.0, 0.0, 0.0]
        self.sel.pinned[low.eid] = 5.0
        acts = self.sel.open_intents(now, now_vec)
        self.assertEqual(acts[0].text, "pinned goal")


class TestFulfillShared(unittest.TestCase):
    def test_fulfill_flips_the_shared_entity(self):
        """Fulfillment closes the goal SHARED: the entity's status flips
        (every seat's selection drops it automatically); the data stays."""
        est = _store()
        g = _goal(est, "done deal", [1.0, 0.0])
        g.properties["status"] = "fulfilled"
        g.properties["fulfilled_ts"] = 123.0
        est.upsert(g)
        sel = GoalSelection(est)
        acts = sel.open_intents(0.0, [1.0, 0.0])
        self.assertFalse(any(i.text == "done deal" for i in acts))
        self.assertEqual((est.docs.get(g.eid)
                          .properties or {}).get("status"), "fulfilled")


class TestMigration(unittest.TestCase):
    def test_json_intents_migrate_to_goals(self):
        """The legacy board's open intents import once: Goal entities +
        priority pins; the JSON entries are marked migrated (the data
        stays as the record)."""
        import tempfile, time
        from pathlib import Path
        est = _store()
        board = IntentBoard(Path(tempfile.mkstemp()[1]))
        i, _ = board.intend("migrate me", vec=[0.5, 0.5])
        board.attach_entity_store(est)
        # the goal entity exists + is pinned
        goals = [e for e in est.docs.values() if e.cls == CLASS_GOAL]
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0].properties.get("name"), "migrate me")
        self.assertIn(goals[0].eid, board.selection.pinned)
        # the json entry is marked migrated (not re-imported)
        self.assertEqual(board.intents[i.iid].status, "migrated")


class TestMcpIntendRoute(unittest.TestCase):
    """POST /membrane/intend on the real proxy's route table: the MCP
    membrane_intend relay POSTs -- the esphome seat's read-only verdict
    ("memory service is read-only") was the do_POST catch-all catching a
    body-bearing route the table never routed. The same text that works
    on the GET lane must work POSTed."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from membrane import opencode as OC
        cls.est = _store()

        class _Board:
            selection = GoalSelection(cls.est)      # no path: RAM only

        handler = OC.MembraneOpenAIHandler

        class _Srv(ThreadingHTTPServer):
            pass

        class _Membrane:
            embedder = None
            store = None
            gate_notes = []

        srv = _Srv(("127.0.0.1", 0), handler)
        srv.membrane = _Membrane()
        srv.entity_store = cls.est
        srv._intent_board = _Board()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.srv = srv
        cls.port = srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, body):
        import json as _json
        import urllib.request
        rq = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/membrane/intend",
            data=_json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=10) as resp:
            return _json.loads(resp.read())

    def test_post_intend_creates_the_goal(self):
        out = self._post({"text": "route the intend POST"})
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("created"), out)
        self.assertIn(out.get("eid"), self.est.docs)
        # the pin landed on the active set
        self.assertIn(out["eid"], self.srv._intent_board.selection.pinned)

    def test_post_intend_pins_an_existing_goal(self):
        g = _goal(self.est, "route the intend POST", [0.1, 0.2])
        out = self._post({"text": "route the intend POST"})
        self.assertTrue(out.get("ok"), out)
        self.assertFalse(out.get("created"), out)
        self.assertEqual(out.get("eid"), g.eid)


if __name__ == "__main__":
    unittest.main()
