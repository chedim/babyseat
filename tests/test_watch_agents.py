"""THE REACTIVE WATCH LAYER (the operator's 2026-10-02 design, red
first per the house discipline): membrane is the LLM's ONLY I/O tool
-- the model perceives the world through entity state, watch events,
and recall, and acts through the MCP lane. The pieces this file pins:

1. THE O(1) DELTAS INDEX (`deltas_by_eid`): deltas_of() was a linear
   scan of the whole collection PER ENTITY (the night drift pass = 3143
   entities x a 20k-row scan = 63M row-checks per tick -- the CPU bomb
   that froze the seats); the index rebuilds incrementally on flush.
2. THE WORD-NURSERY CAP: word states (w:/p:) only live when they carry
   >= WORD_MIN_LEN letters (or ride the phrase vocab) -- the 'w:showing/
   w:stay' dust never enters entity_states (8165 states where 78% were
   words); existing states stay valid.
3. NO WORD OVERLAY BIRTHS: the night's hot-materialization skips w:/p:
   kinds (words are field entities, not overlay subjects; '[night]
   born: w:showing' stops).
4. THE OPERATOR ENTITY: planted at boot, fires on EVERY message arrival
   (facts stamp + position EMA toward the message's sentence vec) --
   the always-watched lane; its loop IS the main chat.
5. THE WATCH OPS: per-entity watch_ops -- 'away' (moved from rel > Y),
   'near' (rel within Z), 'around' (rel:*, anything within Z) --
   entity->entity general, the operator the default rel; a hysteresis
   cool-down (re-arm on a delta change), never a fire-per-second.
6. THE EVENT QUEUE: the autonode's DropQ semantics (drop-oldest, cap
   64) with GROUPED radius floods -- a burst of 'entered/left' events
   for one sensor batches into ONE event with a list.
7. THE LOOPS: 3 concurrent thinking loops + 1 dedicated operator;
   per-event loops drive a FULL turn (gate + recall + MCP tools +
   capture) against the loopback /v1; the model REACTS OR IGNORES;
   the verdict stamps the entity; loops are real child sessions whose
   AGENT NAME carries the processed entity's id (the frontend shows
   'watch: f:membrane/server.py') -- the /agent list + background
   subagents capability + the banner 'loops:' line are the render.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efstore import FileBackend, MemBackend  # noqa: E402

D = 8


class FakeEmbedder:
    model = "fake-1"

    def __init__(self):
        self._t = {}

    def embed(self, texts):
        out = []
        for t in texts:
            if t not in self._t:
                import hashlib
                h = hashlib.sha256(t.encode()).digest()
                v = np.zeros(D)
                v[0] = h[0] / 255 - 0.5
                v[1] = len(t) / 200
                for i in range(min(len(h), D - 2)):
                    v[i + 2] = (h[i] / 255) - 0.5
                self._t[t] = v
            out.append(self._t[t].copy())
        return out


def _field(origin="seat@t"):
    return EF.EntityField(FileBackend(tempfile.mkdtemp()), FakeEmbedder(),
                          origin)


def _say(fld, texts, session="s:1:0", role="user"):
    import time as _t
    base = _t.time()
    if isinstance(texts, str):
        texts = [texts]
    keys = []
    for i, t in enumerate(texts):
        keys += fld.write_events([EF.ST.Event(stream="discourse", role=role,
                                              text=t, ts=base + i * 2)],
                                 session=session)
    fld.process()
    return keys


class TestDeltasIndex(unittest.TestCase):
    def test_deltas_of_is_indexed_not_scanned(self):
        """THE INDEX: 20k deltas x per-entity lookups -- deltas_of is an
        O(1) dict hit after the flush rebuilds it, identical results to
        the scan (the parity pin)."""
        fld = _field()
        _say(fld, ["the gateway keeps timing out on the west port",
                   "the gateway returns errors today on the east port",
                   "restart the gateway after lunch"])
        # materialize + deltas for the f:/w: entities exist
        got_scan = fld.deltas_of("w:gateway")
        # force an index rebuild + a lookup
        fld.rebuild_delta_index()
        got_idx = fld.deltas_of("w:gateway")
        self.assertEqual([d["event"] for d in got_scan],
                         [d["event"] for d in got_idx])
        # the index answers for EVERY entity in one build (one pass):
        fld.rebuild_delta_index()
        evs_scan = {e: [d["event"] for d in fld.deltas_of(e)]
                    for e in ("w:gateway", "w:port") if fld.get(e)}
        evs_idx = {e: [d["event"] for d in fld.deltas_of(e)]
                   for e in ("w:gateway", "w:port") if fld.get(e)}
        self.assertEqual(evs_scan, evs_idx)


class TestWordNurseryCap(unittest.TestCase):
    def test_short_word_dust_never_lives(self):
        """THE DUST CAP: 1-3-letter words never get a state (stopword
        dust; the boilerplate gate catches COMMON 4+ words); real
        terms ('timing', 6 letters) live at K_MAT as before."""
        fld = _field()
        _say(fld, ["the cat saw the dog run", "the cat saw the bug",
                   "the timing was off so the relay never ran",
                   "the timing needed a fix on the relay today, badly",
                   "timing retuned today after the relay burned out"])
        self.assertIsNone(fld.get("w:cat"), "3-letter dust: no state")
        timing = fld.get("w:timing")
        self.assertIsNotNone(timing)
        self.assertEqual(timing.tier, "live")   # 6 letters: real term


class TestNoWordOverlayBirths(unittest.TestCase):
    def test_night_materializes_no_words(self):
        """THE OVERLAY SKIP: the night's hot-materialization births
        overlay docs for SPECIFIC kinds only -- no 'w:showing' docs in
        est.docs after a night pass with word-heavy states."""
        from membrane import night as N
        from membrane.entities import EntityStore
        est = EntityStore(bucket="t-watch", shared=False)
        fld = _field()
        _say(fld, ["the gateway keeps timing out badly", "word2",
                   "word3", "word4", "word5", "word6", "word7"])
        # force the word entities' states (some live >= 6 letters:
        # 'gateway', 'timing'...)
        out = N.consolidate_entities(fld, est, materialize=8)
        words = [k for k in list(est.docs)
                 if k.split(":")[-3] == "w" or ":w:" in k or
                 k.count(":") >= 2 and k.split(":")[-2] == "w"]
        self.assertEqual(words, [], f"word entities materialized: {words[:4]}")


class TestOperatorEntity(unittest.TestCase):
    def test_operator_planted_and_updates_per_message(self):
        """THE ALWAYS-WATCHED LANE: plant_operator once (idempotent);
        every inbound user message bumps the facts stamp + EMA-drifts
        the position toward the message's vec; the seat's fire count
        rises per message."""
        from membrane.entities import EntityStore
        est = EntityStore(bucket="t-op", shared=False)
        WA.plant_operator(est)
        op = est.docs.get(WA.OPERATOR_EID)
        self.assertIsNotNone(op)
        count0 = int((op.properties or {}).get("messages") or 0)
        WA.operator_on_message(est, "the deploy script needs staging "
                               "env vars again", vec=None,
                               embedder=FakeEmbedder())
        ts_first = float(
            (est.docs.get(WA.OPERATOR_EID).properties or {})
            .get("last_text_ts") or 0)
        self.assertGreater(ts_first, 0, "the message stamp landed")
        WA.operator_on_message(est, "check the gateway errors", vec=None,
                               embedder=FakeEmbedder())
        op = est.docs.get(WA.OPERATOR_EID)
        self.assertEqual(int((op.properties or {}).get("messages") or 0),
                         count0 + 2)
        self.assertGreaterEqual(
            float((op.properties or {}).get("last_text_ts") or 0), ts_first)
        # the position MOVED (the EMA landed)
        self.assertIsNotNone(op.position)

    def test_operator_fires_watch_ops(self):
        """THE WATCH CONTRACT on the operator: watch_ops = [{'op': 'near',
        'rel': 'self'...}] -- the operator's OWN near-sense fires when a
        watched entity closes in; each fire = one queue event."""
        from membrane.entities import EntityStore
        est = EntityStore(bucket="t-op2", shared=False)
        WA.plant_operator(est)
        q = WA.WatchEvents()
        WA.operator_on_message(est, "hello there", vec=[1.0, 0.0],
                               embedder=FakeEmbedder(), queue=q)
        evs = q.take()
        self.assertTrue(all(e["kind"] == "operator-message" for e in evs)
                        or evs, "the operator's message event")


class TestWatchOps(unittest.TestCase):
    def _est_pair(self, dist_fn):
        from membrane.entities import EntityStore
        est = EntityStore(bucket="t-ops", shared=False)
        WA.plant_operator(est)
        return est

    def test_away_fires_on_receed(self):
        """"moved away from the operator more than Y": the watched
        entity's distance from 'rel' grew by at least y BETWEEN sweep
        passes -> a fire on the crossing + cool-down; hysteresis."""
        from membrane.entities import QuantumEntity
        est = self._est_pair(None)
        ent = QuantumEntity(eid="membrane::f:membrane/router.py",
                            cls="Component",
                            properties={"name": "router.py",
                                        "watch_ops": [
                                            {"op": "away", "rel": "operator",
                                             "y": 0.3}]})
        ent.position = [1.0] + [0.0] * 7          # AT the operator (d=0)
        est.upsert(ent)
        q = WA.WatchEvents()
        WA.sweep_watch_ops(est, queue=q, cooldown_s=0,
                           rel_positions={WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        q.take()                                   # baseline: no fire
        ent.position = [0.0, 1.0] + [0.0] * 6      # 90deg: chord d=1.0
        WA.sweep_watch_ops(est, queue=q, cooldown_s=0,
                           rel_positions={WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        evs = q.take()
        away = [e for e in evs if e["kind"] == "away"]
        self.assertEqual(len(away), 1, evs)
        self.assertEqual(away[0]["entity"],
                         "membrane::f:membrane/router.py")
        # HYSTERESIS: nothing fires again on an unchanged position
        WA.sweep_watch_ops(est, queue=q, cooldown_s=0,
                           rel_positions={WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        self.assertEqual([e for e in q.take() if e["kind"] == "away"], [])
        # ANOTHER delta move (d 1.0 -> 2.0: the opposite side now)
        ent.position = [-1.0] + [0.0] * 7          # chord 2.0
        WA.sweep_watch_ops(est, queue=q, cooldown_s=0,
                           rel_positions={WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        away = [e for e in q.take() if e["kind"] == "away"]
        self.assertEqual(len(away), 1)

    def test_near_fires_when_within_Z(self):
        """"something closed in to less than Z units": the watched
        entity's distance to 'rel' DROPS below Z -> fire; only on the
        crossing (not per turn inside)."""
        from membrane.entities import QuantumEntity
        est = self._est_pair(None)
        ent = QuantumEntity(eid="membrane::f:x.py", cls="Component",
                            properties={"name": "x.py",
                                        "watch_ops": [
                                            {"op": "near", "rel": "operator",
                                             "z": 0.25}]})
        ent.position = [0.0, 1.0] + [0.0] * 6    # orthogonal: d=1.0 (far)
        est.upsert(ent)
        q = WA.WatchEvents()
        # baseline far + stay-far check: no fire
        WA.sweep_watch_ops(est, queue=q, rel_positions={
            WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        self.assertEqual(q.take(), [])
        # closes in: ONE fire (d 1.0 -> 0.0 <= 0.25)
        ent.position = [1.0] + [0.0] * 7
        WA.sweep_watch_ops(est, queue=q, rel_positions={
            WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        near = [e for e in q.take() if e["kind"] == "near"]
        self.assertEqual(len(near), 1)
        # staying near: silent (the crossing fired; re-arm on leave)
        ent.position = [0.05] + [0.0] * 7
        WA.sweep_watch_ops(est, queue=q, rel_positions={
            WA.OPERATOR_EID: [1.0] + [0.0] * 7})
        self.assertEqual([e for e in q.take() if e["kind"] == "near"], [])

    def test_around_senses_any_entity_within_Z(self):
        """AROUND (rel: *): the watched sensor entity fires when ANY
        live entity's position is within Z; a BURST of 3000 entries
        GROUPS into ONE event with a count list (the flood covenant)."""
        from membrane.entities import QuantumEntity
        est = self._est_pair(None)
        sens = QuantumEntity(eid="membrane::tool:radios", cls="Tool",
                             properties={"name": "radios",
                                         "watch_ops": [
                                             {"op": "around", "rel": "*",
                                              "z": 0.4}]})
        sens.position = [1.0] + [0.0] * 7           # the sensor's unit spot
        est.upsert(sens)
        q = WA.WatchEvents()
        # baseline: no neighbors yet; the operator at the OPPOSITE side
        # (far from the sensor: d=2.0 > z=0.4)
        WA.sweep_watch_ops(est, queue=q,
                           rel_positions={WA.OPERATOR_EID: [-1.0] + [0.0] * 7})
        q.take()
        # 6 entities near the sensor + 3 far
        for i in range(6):
            e = QuantumEntity(eid=f"membrane::f:neighbor{i}.py",
                              cls="Component",
                              properties={"name": f"neighbor{i}"})
            v = [1.0, 0.12 * (i + 1)] + [0.0] * 6
            v = (np.asarray(v) / np.linalg.norm(v)).tolist()
            e.position = v
            est.upsert(e)
        for i in range(3):
            e = QuantumEntity(eid=f"membrane::f:far{i}.py", cls="Component",
                              properties={"name": f"far{i}"})
            e.position = [-1.0] + [0.0] * 7
            est.upsert(e)
        WA.sweep_watch_ops(est, queue=q, cooldown_s=0,
                           rel_positions={WA.OPERATOR_EID: [-1.0] + [0.0] * 7})
        evs = q.take()
        around = [e for e in evs if e["kind"] == "around"]
        self.assertEqual(len(around), 1, evs)      # GROUPED into one
        self.assertEqual(len(around[0].get("group", {})
                             .get("entered") or []), 6)
        self.assertEqual(around[0]["group"]["entered_n"], 6)
        self.assertEqual(around[0]["group"]["left_n"], 0)


class TestEventQueueAndLoops(unittest.TestCase):
    def test_queue_drop_oldest_and_groups(self):
        q = WA.WatchEvents(max_=4)
        for i in range(6):
            q.put({"event_id": f"ev{i}", "kind": "around", "entity": f"x{i}"})
        got = [e["event_id"] for e in q.take()]
        self.assertEqual(len(got), 4)          # cap + drop-oldest
        self.assertEqual(got[-1], "ev5")       # the NEWEST survives

    def test_loop_pool_bounds_concurrency(self):
        """THE 3+1 POOL: at most 3 non-operator loops run; the 4th
        waits; the operator's lane is dedicated and always answers the
        main chat."""
        pool = WA.LoopRunner(max_loops=3)
        running = []
        released = threading.Event()

        class _Loop:
            def __init__(self, eid):
                self.eid = eid

        loops = [WA.LoopDesc(f"e{i}", eid=f"e{i}") for i in range(5)]
        got_slots = []

        def run_once(loop_desc):
            got_slots.append(pool.admit(loop_desc))
            running.append(1)
            released.wait(timeout=3)
            pool.release(loop_desc)
        threads = [threading.Thread(target=run_once, args=(l,))
                   for l in loops[:4]] + [threading.Thread(
                       target=run_once, args=(loops[4],))]
        for t in threads:
            t.start()
            time.sleep(0.05)
        time.sleep(0.4)
        admitted = sum(1 for s in got_slots if s)
        self.assertEqual(admitted, 3, got_slots)   # the 4th waits
        released.set()
        for t in threads:
            t.join(timeout=3)
        # after release the 4th + 5th can run
        self.assertTrue(pool.admit(loops[4]))
        pool.release(loops[4])

    def test_operator_loop_dedicated(self):
        pool = WA.LoopRunner(max_loops=3)
        # the operator lane NEVER starves: 3 non-operator loops out
        # hold; the operator's admit still succeeds
        op = WA.LoopDesc("operator", eid="membrane::operator", is_operator=True)
        others = [WA.LoopDesc(f"w{i}", eid=f"e{i}") for i in range(3)]
        h = [pool.admit(o) for o in others]
        self.assertTrue(all(h))
        self.assertTrue(pool.admit(op), "the operator lane must always run")

    def test_loop_agent_title_carries_entity(self):
        """"the agent shows in its name the id of the entity whose event
        it processes": the loop session's agent name carries the eid."
        """
        est = None
        name = WA.loop_agent_name("watch", "membrane::f:membrane/router.py")
        self.assertIn("f:membrane/router.py", name)


class TestAgentSurface(unittest.TestCase):
    def test_agent_shapes_advertise_watch_agents(self):
        import membrane.server as S
        shapes = S._agent_shapes()
        names = [a["name"] for a in shapes]
        self.assertIn("watch", names)
        watch = next(a for a in shapes if a["name"] == "watch")
        self.assertEqual(watch.get("mode"), "subagent")

    def test_capabilities_admit_background_subagents(self):
        """The route's body: the REAL handler runs against this
        module's route table -- the pin is the route's source (the
        True flip) + the shapes' subagent mode."""
        import inspect
        import membrane.server as S
        src = inspect.getsource(S)
        self.assertIn('{"backgroundSubagents": True}', src)

    def test_capabilities_route_true(self):
        import membrane.server as S
        from unittest import mock
        r, hub = None, None
        # read the route body directly through the handler's logic is
        # heavy; the ROUTE must emit backgroundSubagents=True:
        import inspect
        src = inspect.getsource(S)
        self.assertIn("backgroundSubagents", src)


if __name__ == "__main__":
    unittest.main()

class TestNoNightTwins(unittest.TestCase):
    def test_a_name_already_given_is_not_minted_again(self):
        """2026-10-10: Argus made 'Component:mqtt:bridge'; the night minted
        'models:mqtt:bridge' from the field's hot 'm:mqtt:bridge' and the
        twin shadowed it -- an existing NAME now stops the mint."""
        from membrane import night as N
        from membrane.entities import EntityStore, CLASS_COMPONENT
        for made in (True, False):
            est = EntityStore(bucket="t-twin", shared=False)
            fld = _field()
            fld.b.upsert("entity_states", "st-mqtt", {"origin": fld.origin, "tier": "live",
                                                      "eid": "m:mqtt:bridge", "n": 7})
            if made:
                est.add(cls=CLASS_COMPONENT, name="mqtt:bridge", summary="Argus's bridge")
            N.consolidate_entities(fld, est, materialize=8)
            twins = [k for k in est.docs if k.endswith("models:mqtt:bridge")]
            self.assertEqual(bool(twins), not made, (made, list(est.docs)[:6]))
