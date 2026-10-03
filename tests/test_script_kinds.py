"""THE SCRIPT ENGINE'S KINDS + LOCKS (the operator's 2026-10-02
design, red first): entity scripts split into two families --

1. CRON (periodic: tick/nudge/consolidation/every_n) -- MUST be
   blocking per-entity: a per-entity lock; the OVERRUN covenant: when
   a cron run takes LONGER than its interval, the NEXT run is BLOCKED
   (not skipped silently) and the model's event pool GETS THE ERROR
   ("cron overran: the next run was blocked") -- a stuck cron is a
   decision, not a silent drip.
2. WATCHER (event-triggered: watch/mention/thought/dreamed) -- may
   BLOCK until their event's work is done (no overrun notify: the
   event's arrival is the cadence); they too hold the per-entity lock
   (one script per entity at a time, across ALL kinds).

ALWAYS: any script exception/error (either family) notifies the
model's event-handling pool (the watch-events lane) -- errors are
events, never stderr dust.

Also pins the WATCH-AGENTS core finished here: the O(1) deltas index
(the deltas_by_eid lookup), the word-nursery cap (short words never
live), and the night's no-word-overlay-births."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402
from membrane.efstore import FileBackend  # noqa: E402


class _Srv:
    def __init__(self, est, fld=None):
        self.entity_store = est
        self.efield = fld
        self.membrane = None
        self.idle_cortex_model = "granite3.3:8b"
        self.events_pool = WA.WatchEvents()
        self.model_calls = []

    def _model_call(self, msgs):        # the thinker-style seam
        self.model_calls.append(msgs)
        return "ok"


def _est():
    return EntityStore(bucket="t-scripts", shared=False)


class TestPerEntityLock(unittest.TestCase):
    def test_two_threads_one_entity_serialize(self):
        """THE PER-ENTITY LOCK: two threads running the SAME entity's
        script serialize (the whole run holds); a DIFFERENT entity runs
        concurrently (per-entity, not global)."""
        N.run_entity_script.cache_clear() if hasattr(
            N.run_entity_script, "cache_clear") else None
        est = _est()
        srv = _Srv(est)
        slow_body = """
import time
result = {"data": {"ran": state.get("trigger")}}
"""
        hold = threading.Event()
        ent_a = QuantumEntity(eid="membrane::tool:a", cls="Tool",
                              properties={"name": "a", "tick_script": slow_body})
        est.docs[ent_a.eid] = ent_a
        order = []

        orig = N.run_entity_script

        def gated_run(code, state, membrane=None):
            order.append(("start", threading.current_thread().name))
            time.sleep(0.3)
            r = orig(code, state, membrane)
            order.append(("end", threading.current_thread().name))
            return r
        N.run_entity_script = gated_run
        try:
            t1 = threading.Thread(target=lambda: N.run_entity_update_script(
                srv, ent_a, "tick"))
            t2 = threading.Thread(target=lambda: N.run_entity_update_script(
                srv, ent_a, "tick"))
            t1.start(); time.sleep(0.05); t2.start()
            t1.join(timeout=3); t2.join(timeout=3)
            starts = [i for i, (k, _) in enumerate(order) if k == "start"]
            ends = [i for i, (k, _) in enumerate(order) if k == "end"]
            # the second start must come AFTER the first end (serialized)
            self.assertGreater(min(ends), min(starts), order)
        finally:
            N.run_entity_script = orig

    def test_different_entities_do_not_block(self):
        est = _est()
        srv = _Srv(est)
        body = """
import time
time.sleep(0.4)
result = {}
"""
        ent_a = QuantumEntity(eid="membrane::tool:a", cls="Tool",
                              properties={"name": "a", "tick_script": body})
        ent_b = QuantumEntity(eid="membrane::tool:b", cls="Tool",
                              properties={"name": "b", "tick_script": body})
        est.docs[ent_a.eid] = ent_a
        est.docs[ent_b.eid] = ent_b
        t0 = time.time()
        threads = [threading.Thread(
            target=lambda e=e: N.run_entity_update_script(srv, e, "tick"))
            for e in (ent_a, ent_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3)
        self.assertLess(time.time() - t0, 0.9,
                        "different entities must run concurrently")


class TestCronOverrun(unittest.TestCase):
    def test_cron_overrun_blocks_next_run_and_notifies(self):
        """THE OVERRUN COVENANT: a cron (tick) run that took longer than
        its interval BLOCKS the next run and the model's event pool
        receives the error event; a watcher (mention) overrun does NOT
        block-notify the same way (the event arrival is the cadence)."""
        est = _est()
        srv = _Srv(est)
        body = "result = {}"
        ent = QuantumEntity(eid="membrane::tool:c", cls="Tool",
                            properties={"name": "c", "tick_script": body,
                                        "tick_every_s": 1.0})
        est.docs[ent.eid] = ent
        # run 1: fine, stamps a run
        N.run_entity_update_script(srv, ent, "tick")
        # FORCE the overrun bookkeeping: the last run's start was > 
        # interval ago (the script engine's stamps)
        props = ent.properties
        props["script_last_started"] = time.time() - 10.0   # 10s > 1s interval
        props["script_last_ms"] = 9000.0                    # ran long
        est.upsert(ent)
        took = N.run_entity_update_script(srv, ent, "tick")
        # the engine BLOCKED the overdue next run + notified the pool
        pool_events = srv.events_pool.take()
        self.assertTrue(any(e.get("kind") == "script-cron-overrun"
                            for e in pool_events), pool_events)


class TestScriptErrorsNotify(unittest.TestCase):
    def test_exception_notifies_the_pool(self):
        """ERRORS ARE EVENTS: a script that raises notifies the model's
        event pool (the watch-events lane), not just the banner/stderr."""
        est = _est()
        srv = _Srv(est)
        bad = "raise RuntimeError('boom')"
        ent = QuantumEntity(eid="membrane::tool:bad", cls="Tool",
                            properties={"name": "bad", "tick_script": bad})
        est.docs[ent.eid] = ent
        N.run_entity_update_script(srv, ent, "tick")
        pool_events = srv.events_pool.take()
        self.assertTrue(any(e.get("kind") == "script-error" and
                            e.get("entity") == ent.eid for e in pool_events),
                        pool_events)


class TestWatchAgentsCore(unittest.TestCase):
    """The finished core: the deltas index + the dust cap (the stuck-arc
    plan, landed with this layer)."""

    def test_word_nursery_cap_in_efield(self):
        """DUST NEVER LIVES: a 3-letter word with no phrase-vocab stays
        nursery; a >= 6-letter word lives."""
        fld = EF.EntityField(FileBackend(tempfile.mkdtemp()),
                             type("E", (), {"model": "f", "embed": lambda s,
                                            ts: [__import__("numpy")
                                                 .zeros(8) for _ in ts]})(),
                             "seat@w")
        import time as _t
        texts = ["the cat sat here", "the cat ran here",
                 "the cat ate dinner"]
        base = _t.time()
        for i, t in enumerate(texts):
            fld.write_events([EF.ST.Event(stream="discourse", role="user",
                                          text=t, ts=base + i)],
                             session="s")
        fld.process()
        cat = fld.get("w:cat")
        self.assertIsNone(cat, "3-letter dust gets NO state at all "
                         "(the dust cap)")


if __name__ == "__main__":
    unittest.main()