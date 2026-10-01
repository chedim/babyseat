"""The night on the entity field: overlay materialization + drift
(consolidate_entities), the scripted entities (delete/nudge lifecycle,
import ban, 3 strikes, size cap). Deterministic: the sha256-seeded fake
embedder, a RAM MemBackend (no Ollama, no Couchbase), an isolated RAM
EntityStore."""

import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder
from membrane.efield import EntityField
from membrane.efstore import MemBackend
from membrane.entities import EntityStore, QuantumEntity, CLASS_COMPONENT
from membrane.night import consolidate_entities, run_scripted_entities
from membrane import streams as ST


def _isolated_store(tmp=None):
    """A TEST-ISOLATED store: shared=False (never touches the couchbase
    commons)."""
    if tmp:
        return EntityStore(path=tmp, shared=False)
    return EntityStore(path=None, shared=False)


def _fake_fld(emb=None):
    """The slice of run_scripted_entities' first argument the code uses:
    only fld.emb (persist-time embedding) -- overlay-only tests pass a
    stub; field-backed ones pass the real EntityField."""
    if emb is not None:
        return EntityField(MemBackend(), emb, "seat@host")
    return types.SimpleNamespace(emb=None)


class TestConsolidation(unittest.TestCase):
    def _world(self):
        emb = FakeEmbedder()
        f = EntityField(MemBackend(), emb, "seat@host")
        est = _isolated_store()
        return f, emb, est

    def _turns(self, f, n=3):
        t0 = time.time()
        for i in range(n):
            f.write_events(ST.split_turn(
                f"reasoning: look at router.py {i}\n",
                "Working on router.py's ladder again", ts=t0 - i * 600),
                session="s")
        f.process()

    def test_materialize_and_seed(self):
        """Hot field entities (mentions >= 3) earn overlay docs at night."""
        f, emb, est = self._world()
        self._turns(f)
        r = consolidate_entities(f, est)
        self.assertGreaterEqual(r["materialized"], 1)
        # the bare file never joined a graph: its eid keeps the '~' prefix
        ent = next(d for k, d in est.docs.items()
                   if k.startswith("membrane::files:")
                   and "router.py" in k)
        self.assertTrue(ent.position)

    def test_quiet_cycle_no_rematerialize(self):
        f, emb, est = self._world()
        self._turns(f)
        consolidate_entities(f, est)
        r2 = consolidate_entities(f, est)
        self.assertEqual(r2["materialized"], 0)   # one doc, never doubled

    def test_only_keys_skips_materialization(self):
        """The plant-time path: consolidate ONLY the planted track's key --
        a fresh ring must not wait for a night nor materialize others."""
        f, emb, est = self._world()
        self._turns(f)
        r = consolidate_entities(f, est, only_keys={"router.py"})
        self.assertEqual(r["materialized"], 0)
        self.assertEqual([k for k in est.docs
                          if k.startswith("membrane::files:")], [])


class TestScriptedEntities(unittest.TestCase):
    """PROGRAMMABLE ENTITIES (the operator's design): the model writes
    small lifecycle scripts; membrane runs them at consolidation time.
    delete_script first (retire), then nudge_script (render/data);
    import-banned, 3-strike disabled, size-capped, announced on errors."""

    def _world(self):
        est = _isolated_store()
        return est, _fake_fld()

    def test_nudge_render_replaces_track_line(self):
        est, fld = self._world()
        est.upsert(QuantumEntity(
            eid="membrane::files:probe.py", cls=CLASS_COMPONENT,
            properties={"name": "probe.py", "script_every_n": 1,
                        "nudge_script":
                        "result = {'render': '3 failures in a row, "
                        "watch the ladder'}"}))
        out = run_scripted_entities(fld, est)
        self.assertEqual(out["nudged"], 1)
        # the script_render rides the entity; the render hook picks it up
        ent = est.docs["membrane::files:probe.py"]
        self.assertIn("watch the ladder",
                      ent.properties["script_render"])

    def test_nudge_silence(self):
        est, fld = self._world()
        est.upsert(QuantumEntity(
            eid="membrane::files:quiet.py", cls=CLASS_COMPONENT,
            properties={"name": "quiet.py", "script_every_n": 1,
                        "nudge_script": "result = {}"}))
        out = run_scripted_entities(fld, est)
        self.assertEqual(out["nudged"], 0)
        ent = est.docs["membrane::files:quiet.py"]
        self.assertNotIn("script_render", ent.properties)

    def test_delete_retires_tombstoned(self):
        est, fld = self._world()
        est.upsert(QuantumEntity(
            eid="membrane::files:doomed.py", cls=CLASS_COMPONENT,
            properties={"name": "doomed.py", "script_every_n": 1,
                        "nudge_script": "result = {'render': 'x'}",
                        "delete_script":
                        "result = {'delete': state['cells'] < 5}"}))
        est.watch_track("files:doomed.py", label="doomed")
        out = run_scripted_entities(fld, est)
        self.assertEqual(out["deleted"], 1)
        ent = est.docs["membrane::files:doomed.py"]
        self.assertTrue(ent.properties.get("retired"))     # tombstone
        self.assertEqual(est.list_tracks(), [])            # dropped
        # announced
        kinds = [a["kind"] for a in est.take_announcements()]
        self.assertIn("retired", kinds)

    def test_imports_unrestricted(self):
        """THE SANDBOX IS LIFTED (the operator's 2026-10-01 call): a
        process-level import works like a pure-compute one; the engine's
        budgets are the only bounds left."""
        est, fld = self._world()
        est.upsert(QuantumEntity(
            eid="membrane::files:bad.py", cls=CLASS_COMPONENT,
            properties={"name": "bad.py", "script_every_n": 1,
                        "nudge_script": "import os\nresult ="
                        " {'data': {'pwn': os.getpid() > 0}}"}))
        out = run_scripted_entities(fld, est)
        self.assertEqual(out["errors"], 0)
        ent = est.docs["membrane::files:bad.py"]
        self.assertEqual((ent.properties or {}).get("script_failures"), None)
        # the pure-compute stdlib keeps working (was allowed before too)
        est.upsert(QuantumEntity(
            eid="membrane::files:good.py", cls=CLASS_COMPONENT,
            properties={"name": "good.py", "script_every_n": 1, "nudge_script":
                        "import math\n"
                        "result = {'render': f'speed {math.ceil(2.1)}'}"}))
        out = run_scripted_entities(fld, est)
        self.assertEqual(out["errors"], 0)
        self.assertIn("speed 3",
                      est.docs["membrane::files:good.py"]
                      .properties["script_render"])

    def test_three_strikes_disable(self):
        est, fld = self._world()
        est.upsert(QuantumEntity(
            eid="membrane::files:broken.py", cls=CLASS_COMPONENT,
            properties={"name": "broken.py", "script_every_n": 1,
                        "nudge_script": "result = 1 / 0"}))
        for _ in range(3):
            run_scripted_entities(fld, est)
        ent = est.docs["membrane::files:broken.py"]
        self.assertTrue(ent.properties.get("script_disabled"))
        # disabled scripts skip silently (no further error events)
        est.announcements = []
        run_scripted_entities(fld, est)
        self.assertEqual([a["kind"] for a in est.announcements
                          if "broken.py" in a["eid"]], [])

    def test_size_cap(self):
        est, fld = self._world()
        est.upsert(QuantumEntity(
            eid="membrane::files:cap.py", cls=CLASS_COMPONENT,
            properties={"name": "cap.py"}))
        big = "x" * 3000
        r = est.edit("files:cap.py", nudge_script=big)
        self.assertFalse(r["ok"])
        self.assertIn("too long", r["error"])


if __name__ == "__main__":
    unittest.main()