"""THE EXECUTOR'S DURABILITY PLAN (the membrane seat's design,
delivered 2026-10-06):

1. THE SYMMETRY RULE: a cancellation is as durable as the debt --
   every supersession carries a RECEIPT ({op: done|expired|withdrawn,
   ts, rc/outcome/why}) written ON the edge row (the relations journal
   persists it); the phantom-debt exhibit (46 live no-receipt edges
   surviving N boots) dies here.
2. EXPIRY-WITH-RESIDUE: an owed edge past EXEC_EDGE_TTL_S expires at
   boot/night -- superseded + receipt {op: expired, age_s, why};
   NEVER re-run; re-link = the deliberate re-queue.
3. BOOT-DERIVED WAKES: the boot reads the OPEN UNCLAIMED intents and
   re-queues an intent-planted event per goal (the durable steering;
   intent_planted_ts guards the double-fire); the volatile (lanes,
   pools, loops) stays RAM -- the board re-derives them.
4. THE CLAIM RECEIPTS: a listener's landing for a goal stamps the
   GOAL's doc (landings[], capped 8) -- the fulfill-by evidence.
"""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import opencode as OC  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import (EntityStore, QuantumEntity, CLASS_GOAL,  # noqa: E402
                               CLASS_TOOL, MEMBRANE_NS, MultiParentOps)
from membrane.intent import IntentBoard  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    return f, est


def _owe(f, eid):
    """An owed `executes` edge (live): what a link left behind before #576
    part F took the link door away -- the engine still lands or expires it."""
    R.upsert(f.b, f.origin, MEMBRANE_NS + "tool:executor", N.EXEC_REL, eid,
             "model-link", ts=time.time())


class _Srv:
    def __init__(self, f, est, mem=None):
        self.efield = f
        self.entity_store = est
        self.membrane = mem
        self.capturer = None


def _doc(est, spec: str) -> QuantumEntity:
    """The dereference anchor (pyright-clean)."""
    d = est._resolve_spec(spec)
    assert d is not None, f"missing doc: {spec}"
    return d


class TestExpiryWithReceipt(unittest.TestCase):
    def test_stale_owed_edge_expires_with_receipt_never_reruns(self):
        """THE METABOLISM: an edge owed past its TTL expires -- stamped
        + received; the command doc is NEVER executed by the expire
        (run_rc stays None); the re-link re-queues honestly."""
        f, est = _world()
        srv = _Srv(f, est)
        saved = N.EXEC_WALK_NODES_CAP
        N.EXEC_WALK_NODES_CAP = 0
        try:
            stale = QuantumEntity(eid=MEMBRANE_NS + "c:stale",
                                  cls=CLASS_TOOL,
                                  properties={"name": "s",
                                              "command": "false"})
            est.docs[stale.eid] = stale
            _owe(f, stale.eid)
            # forge age: the edge's first_ts pushed past the TTL
            _now = time.time()
            for key, d in list(f.b.scan("relations")):
                if d.get("rel") == "executes":
                    d["first_ts"] = _now - 100_000.0
                    f.b.upsert("relations", key, d)
            receipts = N.expire_stale_edges(srv, est, f, srv.membrane,
                                            ttl_s=60.0)
            self.assertEqual(len(receipts), 1)
            rows = [d for _k, d in f.b.scan("relations")
                    if d.get("rel") == "executes"]
            self.assertTrue(rows[0]["superseded"])
            self.assertEqual(rows[0]["receipt"]["op"], "expired")
            # NEVER RE-RUN: the command doc carries no residue
            self.assertIsNone((_doc(est, stale.eid).properties
                               or {}).get("run_rc"))
        finally:
            N.EXEC_WALK_NODES_CAP = saved

    def test_fresh_edges_survive_the_pass(self):
        f, est = _world()
        srv = _Srv(f, est)
        saved = N.EXEC_WALK_NODES_CAP
        N.EXEC_WALK_NODES_CAP = 0
        try:
            fresh = QuantumEntity(eid=MEMBRANE_NS + "c:fresh",
                                  cls=CLASS_TOOL,
                                  properties={"name": "f",
                                              "command": "true"})
            est.docs[fresh.eid] = fresh
            _owe(f, fresh.eid)
            self.assertEqual(N.expire_stale_edges(srv, est, f,
                                                  srv.membrane, ttl_s=60.0),
                             [])
            rows = [d for _k, d in f.b.scan("relations")
                    if d.get("rel") == "executes"]
            self.assertFalse(rows[0]["superseded"], "still owed: fresh")
        finally:
            N.EXEC_WALK_NODES_CAP = saved


class TestBootWakes(unittest.TestCase):
    def _board(self, est):
        b = IntentBoard.__new__(IntentBoard)
        b.path = None
        b.embedder = None
        b.intents = {}
        b._next_iid = 1
        b.attach_entity_store(est)
        return b

    def test_planted_goals_re_derive_their_wakes_at_boot(self):
        """THE BOARD IS THE DURABLE STEERING: the boot reads the open
        unclaimed intents and puts one intent-planted event per goal
        (created=False); a claimed goal (intent_planted_ts set) never
        double-fires."""
        f, est = _world()
        board = self._board(est)
        pool_items = []

        class Pool:
            def put(self, ev):
                pool_items.append(ev)

            def take(self):
                return []

            def depth(self):
                return len(pool_items)
        pool = Pool()
        # THE DURABLE PLANT: a goal entity (what a previous boot's
        # create-or-pin left) -- no intent_planted_ts yet
        g = QuantumEntity(eid=MEMBRANE_NS + "goal:durable-wake",
                          cls=CLASS_GOAL,
                          properties={"name": "finish the repair",
                                      "summary": "...", "status": "open",
                                      "born": time.time()})
        g.position = [0.0, 1.0, 0.0]
        est.docs[g.eid] = g
        # THE BOOT'S WAKE DERIVATION (the boot block's logic, exercised)
        wakes = 0
        for intent in board.open_intents():
            geid = getattr(intent, "eid", None) or ""
            if not geid:
                continue
            gdoc = est.docs.get(geid)
            gprops = (gdoc.properties or {}) if gdoc is not None else {}
            if gprops.get("intent_planted_ts"):
                continue
            WA.notify_intent_planted(est, pool, geid,
                                     str(getattr(intent, "text", "")),
                                     created=False)
            wakes += 1
        self.assertEqual(wakes, 1)
        self.assertEqual(pool_items[0]["kind"], "intent-planted")
        self.assertFalse(pool_items[0]["created"])
        self.assertEqual(pool_items[0]["entity"], g.eid)
        # the claim stamp SET => a second boot never double-fires
        _gdoc = est.docs.get(g.eid)
        self.assertIsNotNone((_gdoc.properties if _gdoc is not None
                              else {}).get("intent_planted_ts"))
        wakes2 = 0
        for intent in board.open_intents():
            geid = getattr(intent, "eid", "") or ""
            _doc2 = est.docs.get(geid) if geid else None
            _claimed = ((_doc2.properties or {}).get("intent_planted_ts")
                        if _doc2 is not None else None)
            if geid and not _claimed:
                wakes2 += 1
        self.assertEqual(wakes2, 0)


class TestGoalReceipts(unittest.TestCase):
    def test_landing_stamps_the_goal(self):
        """"CLAIM RECEIPTS ON THE GOAL": a listener's landing stamps the
        goal's landings[] ledger (capped 8) -- the fulfill-by evidence.
        THE STORM GUARD (the repeat-loop exhibit): the stamp puts
        NOTHING into the pool -- a landing is bookkeeping, never a
        stimulus (a landed receipt's pool event re-looped the drainer:
        reaction -> receipt -> reaction -> ... five self-spoken turns
        in two minutes)."""
        est = EntityStore(bucket="test-durability-goals", shared=False)
        g = QuantumEntity(eid=MEMBRANE_NS + "goal:receipted",
                          cls=CLASS_GOAL,
                          properties={"name": "g", "status": "open"})
        est.docs[g.eid] = g
        for i in range(10):
            WA.notify_intent_landed(est, None, g.eid,
                                    {"op": "done", "rc": 0,
                                     "step": f"step{i}"})
        props = (_doc(est, g.eid).properties or {})
        self.assertEqual(len(props.get("landings") or []), 8)
        self.assertEqual(props["landings"][-1]["step"], "step9")


class TestIntentDurability(unittest.TestCase):
    def test_plant_creates_the_store_doc(self):
        """THE SILENT-CLEAR FIX's ground shape: the create-or-pin's
        plant lands on the store (the entity doc + the pin) -- the doc
        survives (the boot's wake derivation reads IT back)."""
        f, est = _world()
        import tempfile
        board = IntentBoard(Path(tempfile.mkstemp()[1]))
        board.attach_entity_store(est)

        class _Membrane:
            embedder = None
            store = None
            audit_path = ""
        srv = _Srv(f, est)
        srv.membrane = _Membrane()
        srv._intent_board = board
        res = OC._membrane_intent_create_or_pin(srv, "durably steered")
        self.assertTrue(res.get("ok"), res)
        geid = res["eid"]
        self.assertIn("goal:", geid)
        # the doc LIVES in the store's RAM + carries born/open facts
        self.assertIn(geid, (est.docs or {}))
        props = (_doc(est, geid).properties or {})
        self.assertEqual(props.get("status"), "open")
        self.assertTrue(props.get("born"))


if __name__ == "__main__":
    unittest.main()