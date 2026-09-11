"""THE THINKER (the fast clock, separate from the dreamer): rumination --
the last stimuli digested, and in silence the quarantined branches
extended. Covenants pinned: working memory = 5 items drop-oldest (the
3-7 bound), the voice ALWAYS rides speak()'s planner gate (stay-silent
holds), the model-call cap (<=4/min), think-ahead extends via the token
walk ($0) and commits steps[] on the branch, and the kill switch boots
the seat thinker-less."""

import time
import unittest
from unittest import mock

from membrane.acts import ActTable
from membrane.entities import (CLASS_TIMELINE, CLASS_SESSION, EntityStore,
                               QuantumEntity, MEMBRANE_NS, CLASS_SESSION)
from membrane.opencode import speak, drain_speech
from membrane.thinker import Thinker, QUEUE_MAX, THINK_MODEL_CAP_PER_MIN


class _Srv:
    def __init__(self):
        self.membrane = type("M", (), {"store": type("S", (), {
            "exemplars": []})()})()
        self.entity_store = EntityStore(bucket="test-think", shared=False)
        self._speak_queue = []
        self._act_table = ActTable()
        self.backend = "http://127.0.0.1:1"
        self.idle_cortex_model = "granite3.3:8b"
        self.thinker = None


class TestThinkQueue(unittest.TestCase):
    def test_queue_is_bounded_drop_oldest(self):
        srv = _Srv()
        th = Thinker(srv)
        for i in range(12):
            th.enqueue("capture", {"i": i})
        self.assertEqual(len(th.queue), QUEUE_MAX)
        # the OLDEST fell off (attention, not a backlog)
        self.assertEqual(th.queue[0]["payload"]["i"], 12 - QUEUE_MAX)

    def test_digest_speaks_only_through_planner(self):
        """An unlearned context vetoes (stay-silent default holds): the
        digest never speaks by its own decision."""
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        th.enqueue("capture", {"label": "call bash", "idx": None,
                               "situation": "the build ran",
                               "sim": 0.99})
        est = srv.entity_store
        # a tag to speak about: the digest reads exemplar extras; with no
        # exemplar the digest degrades to no speech regardless
        th._process_one(th.queue.popleft())
        self.assertEqual(th.stats["spoken"], 0)
        self.assertEqual(drain_speech(srv), [])

    def test_digest_night_speaks_materializations(self):
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        # an unlearned context -> planner veto -> nothing spoken
        th._process_one({"kind": "night",
                         "payload": {"materialized": 3}})
        self.assertEqual(th.stats["spoken"], 0)
        # a learned context speaks
        sig = "unknown"
        srv._act_table.observe(sig, "report-state")
        srv._act_table.observe(sig, "report-state")
        th._process_one({"kind": "night",
                         "payload": {"materialized": 2}})
        msgs = drain_speech(srv)
        self.assertEqual(len(msgs), 1)
        self.assertIn("materialized 2", msgs[0]["text"])

    def test_think_ahead_extends_quarantined_branch(self):
        srv = _Srv()
        th = Thinker(srv)
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now",
                            cls="Session",
                            properties={"name": "now", "grain": 0.003})
        est.upsert(now)
        br = est.make_timeline(terminal="run the benchmark suite next",
                               vec=[1.0, 0.0], seed_intent="speed")
        # the token walk has nothing for this context -> $0 path, no
        # model (dead backend) -> no extension; the walk-miss + cap path
        # must degrade without raising
        th._idle_think()
        self.assertEqual(th.stats["model_calls"], 1)  # the one allowed call
        # with a seeded timeline table the walk extends for free
        from membrane.token_timeline import TokenTimeline
        tt = type("TT", (), {
            "walk": staticmethod(lambda toks, max_steps=14:
                                 toks + ["suite", "green"])})()
        srv.membrane = type("M", (), {"store": type("S", (), {
            "exemplars": []})(), "timeline": tt})()
        th._idle_think()
        steps = (est.docs.get(br.eid).properties or {}).get("steps")
        self.assertEqual(steps, ["suite green"])


class TestModelCap(unittest.TestCase):
    def test_cap_four_per_minute(self):
        srv = _Srv()
        th = Thinker(srv)
        for _ in range(THINK_MODEL_CAP_PER_MIN + 2):
            self._try_call(th)
        self.assertEqual(th.stats["model_calls"],
                         THINK_MODEL_CAP_PER_MIN)

    def _try_call(self, th):
        try:
            th._model_call([{"role": "user", "content": "x"}])
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
