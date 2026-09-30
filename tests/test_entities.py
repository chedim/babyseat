"""Entity layer: deterministic extraction, QuantumEntity docs (cortex
compat), the boundary state machine, and the EntityStore merge contract.
Pure-data tests (no Ollama, no Couchbase -- the store degrades to
JSON-only)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import (extract_entities,
                               query_entities, QuantumEntity, EntityStore,
                               CLASS_DECISION, CLASS_MODEL, CLASSES,
                               CLASS_COMPONENT, CLASS_SESSION,
                               MEMBRANE_NS, entity_id)


class TestExtract(unittest.TestCase):
    def test_files_symbols_models(self):
        # SITUATION-primary harvest: the cell is tagged by what it is ABOUT.
        t = extract_entities(
            "fix the banner in membrane/router.py and glm-5.3-flash:cloud handles it",
            "edited Registry.route")
        self.assertIn("membrane/router.py", t["files"])
        self.assertIn("glm-5.3-flash:cloud", t["models"])
        # symbols harvest from the SITUATION: Registry.route is in the
        # action -- situation-primary means the cell tags as the file/model
        # it is ABOUT, not the symbol the answer passes through
        t2 = extract_entities(
            "edited Registry.route and glm-5.3-flash:cloud handles it")
        self.assertTrue(any("route" in s for s in t2.get("symbols", [])))

    def test_action_mention_alone_does_not_tag(self):
        """The regression that filled the tracks with passing references:
        a model name in the ANSWER text does not tag the cell when the
        conversation was about something else."""
        t = extract_entities(
            "reasoning: how to fix?",   # the situation: nothing about ling
            "the free-tier rung ling-3.0-flash-fin:free overflowed the window")
        self.assertNotIn("models", t)   # the passing mention does not tag

    def test_ha_and_urls(self):
        t = extract_entities(
            "does sensor.battery_voltage feed the pump logic? curl "
            "http://127.0.0.1:11500/membrane/state to check")
        self.assertIn("sensor.battery_voltage", t["ha"])
        self.assertTrue(any(u.startswith("http://127.0.0.1") for u in t["urls"]))

    def test_pronoun_dots_dropped(self):
        t = extract_entities("we.think it.does work well", "")
        self.assertNotIn("symbols", t)

    def test_query_side(self):
        keys = query_entities("what was the ling-3.0-flash-fin:free bug?")
        self.assertIn("models:ling-3.0-flash-fin:free", keys)


class TestQuantumEntity(unittest.TestCase):
    def test_doc_roundtrip_and_merge(self):
        e = QuantumEntity(eid="membrane::synth:x", cls=CLASS_DECISION,
                          properties={"name": "x", "summary": "s"},
                          links=[1], derived_by=["membrane-idle"])
        doc = e.to_doc()
        self.assertEqual(doc["meta"]["type"], "QuantumEntity")
        self.assertEqual(doc["data"]["class"], CLASS_DECISION)
        self.assertEqual(doc["data"]["properties"]["links"], [1])
        back = QuantumEntity.from_doc("membrane::synth:x", doc)
        self.assertEqual(back.eid, e.eid)
        self.assertEqual(back.cls, e.cls)
        # merge: union, never duplicate
        e.merge(QuantumEntity(eid="membrane::synth:x", cls=CLASS_DECISION,
                              properties={"extra": "y"}, links=[1, 2],
                              derived_by=["operator"]))
        self.assertEqual(e.links, [1, 2])
        self.assertEqual(set(e.derived_by),
                         {"membrane-idle", "operator"})
        self.assertEqual(e.properties["extra"], "y")

    def test_from_doc_tolerant(self):
        self.assertIsNone(QuantumEntity.from_doc("x", {"weird": "shape"}))

    def test_entity_id_namespaced(self):
        self.assertTrue(entity_id("models", "glm-5.3-flash:cloud")
                        .startswith(MEMBRANE_NS + "models:"))
        self.assertIn(CLASS_MODEL, CLASSES)


class TestEntityStore(unittest.TestCase):
    def test_merge_persist_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "entities.json")
            st = EntityStore(path=p)
            st.upsert(QuantumEntity(eid="membrane::synth:a",
                                    cls=CLASS_DECISION,
                                    properties={"name": "a"}, links=[1],
                                    derived_by=["membrane-idle"]))
            # merge on collision (same eid, second derivation)
            st.upsert(QuantumEntity(eid="membrane::synth:a",
                                    cls=CLASS_DECISION,
                                    properties={"summary": "s"},
                                    links=[1, 2], derived_by=["operator"]))
            self.assertEqual(st.docs["membrane::synth:a"].links, [1, 2])
            # never a second doc OF THE SYNTH ENTITY (the store also
            # carries the 7 junk-anchor seeds)
            self.assertEqual(len([k for k in st.docs
                                  if "junk-anchor" not in k]), 1)
            st2 = EntityStore(path=p)               # reload from JSON
            self.assertEqual(len([k for k in st2.docs
                                  if "junk-anchor" not in k]), 1)
            self.assertEqual(st2.find("a").eid, "membrane::synth:a")


class _Count:
    """The count side of the tracks: the field counts mentions of a track
    key (opencode passes track_count(fld, k)) -- the test mutates one
    integer instead of seeding exemplar cells."""

    def __init__(self, n: int = 0):
        self.n = n

    def __call__(self, key: str) -> int:
        return self.n


class TestBoundaryStateMachine(unittest.TestCase):
    """The banner's EVENT lane: 'enter N'/'exit N' boundaries over the live
    mention count -- a state machine (alert once per transition; re-entry
    re-arms by construction), not a level check."""

    def _store(self, tmp):
        p = str(Path(tmp) / "entities.json")
        return EntityStore(path=p)

    def test_parse_boundary(self):
        self.assertEqual(EntityStore.parse_boundary(""), {})
        self.assertEqual(EntityStore.parse_boundary("enter 5"),
                         {"enter": 5})
        self.assertEqual(EntityStore.parse_boundary("exit 2"), {"exit": 2})
        self.assertEqual(EntityStore.parse_boundary(" 7 "), {"enter": 7})
        for bad in ("enter", "exit", "enter x", "enter -1", "5.5",
                    "enter 5 exit 2"):
            with self.assertRaises(ValueError):
                EntityStore.parse_boundary(bad)

    def test_enter_transition_fires_once_and_rearms(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cnt = _Count(3)
            r = st.watch_track("files:router.py", boundary="enter 3")
            self.assertTrue(r["ok"])
            # seed eval: silent (no alert on plant)
            self.assertEqual(st.evaluate_tracks(cnt), [])
            self.assertTrue(st.list_tracks()[0]["inside"])
            # +1 -> no transition -> no alert (still inside)
            cnt.n = 4
            self.assertEqual(st.evaluate_tracks(cnt), [])
            # drop to 1 -> EXIT transition -> one alert
            cnt.n = 1
            alerts = st.evaluate_tracks(cnt)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["kind"], "exited")
            # re-grow to 3 -> RE-ENTER fires again (re-arm by construction)
            cnt.n = 3
            alerts = st.evaluate_tracks(cnt)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["kind"], "entered")
            # stable inside -> no more alerts
            self.assertEqual(st.evaluate_tracks(cnt), [])

    def test_exit_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cnt = _Count(5)
            st.watch_track("files:router.py", boundary="exit 2")
            self.assertEqual(st.evaluate_tracks(cnt), [])  # seed: inside
            cnt.n = 2                                      # still in
            self.assertEqual(st.evaluate_tracks(cnt), [])
            cnt.n = 1                                      # 2 -> 1: out
            alerts = st.evaluate_tracks(cnt)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["kind"], "exited")
            self.assertEqual(st.list_tracks()[0]["inside"], False)

    def test_take_alerts_drains_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cnt = _Count(1)
            st.watch_track("files:router.py", boundary="enter 2")
            st.evaluate_tracks(cnt)                        # seed (silent)
            cnt.n = 2                                      # -> inside
            alerts = st.evaluate_tracks(cnt)
            self.assertEqual(len(alerts), 1)
            # drained exactly-once; second drain empty
            self.assertEqual(len(st.take_alerts()), 1)
            self.assertEqual(st.take_alerts(), [])
            # list shows the state, no pending
            t = st.list_tracks()[0]
            self.assertEqual(t["inside"], True)
            self.assertEqual(t["pending_alerts"], 0)

    def test_count_only_track_never_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cnt = _Count(1)
            st.watch_track("files:router.py")
            self.assertEqual(st.evaluate_tracks(cnt), [])
            cnt.n = 2
            self.assertEqual(st.evaluate_tracks(cnt), [])
            self.assertEqual(st.take_alerts(), [])
            t = st.list_tracks()[0]
            self.assertNotIn("enter", t)
            self.assertNotIn("inside", t)

    def test_watch_track_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            self.assertFalse(st.watch_track("router.py")["ok"])  # no kind:
            self.assertFalse(st.watch_track("files:router.py",
                                            boundary="enter many")["ok"])

    def test_boundary_change_resets_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cnt = _Count(5)
            st.watch_track("files:router.py", boundary="enter 2")
            st.evaluate_tracks(cnt)                        # seed: inside
            self.assertTrue(st.list_tracks()[0]["inside"])
            # changed boundary -> fresh machine; new seed is silent
            st.watch_track("files:router.py", boundary="enter 9")
            self.assertIsNone(st.list_tracks()[0].get("inside"))
            self.assertEqual(st.evaluate_tracks(cnt), [])  # silent seed
            self.assertFalse(st.list_tracks()[0]["inside"])

    def test_tracks_persist_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "entities.json")
            st = EntityStore(path=p)
            st.watch_track("files:router.py", boundary="enter 3",
                           label="router watch")
            cnt = _Count(3)
            st.evaluate_tracks(cnt)                        # seed: inside
            st2 = EntityStore(path=p)                      # reload
            t = st2.list_tracks()[0]
            self.assertEqual(t["key"], "files:router.py")
            self.assertEqual(t["label"], "router watch")
            self.assertEqual(t["enter"], 3)
            self.assertEqual(t["inside"], True)

class TestAnnouncements(unittest.TestCase):
    """The announcement queue: entity births and merges announce once to
    the model (ALERT-style banner lines); nursery activity (side-index
    tags, read-through, reharvest) stays silent. Exactly-once: the queue
    is the delivery ledger."""

    def _store(self):
        return EntityStore(path=None, shared=False)   # RAM-only, isolated

    def test_birth_announces_exactly_once(self):
        st = self._store()
        st.add(cls=CLASS_DECISION, name="the-big-refactor",
               summary="settled: extract entities.py")
        self.assertEqual(len(st.announcements), 1)
        a = st.announcements[0]
        self.assertEqual(a["kind"], "new")
        self.assertIn("the-big-refactor", a["eid"])
        # the edit (update, not birth) stays silent
        st.edit("the-big-refactor", summary="settled v2")
        self.assertEqual(len(st.announcements), 1)
        # drained exactly-once: second drain empty
        out = st.take_announcements()
        self.assertEqual(len(out), 1)
        self.assertEqual(st.take_announcements(), [])

    def test_merge_announces_and_marks(self):
        st = self._store()
        st.add(cls=CLASS_COMPONENT, name="dup-a", summary="first shape")
        st.add(cls=CLASS_COMPONENT, name="dup-b", summary="second shape")
        self.assertEqual(len(st.announcements), 2)     # two births
        st.announcements = []                          # clear births
        r = st.edit("dup-a", merged_into="dup-b")
        self.assertTrue(r["ok"])
        self.assertEqual(r["merged_into"], st.docs[
            "membrane::Component:dup-b"].eid)
        # the source STAYS, marked
        src = st.docs["membrane::Component:dup-a"]
        self.assertEqual(src.properties.get("merged_into"),
                         "membrane::Component:dup-b")
        # the survivor inherited the source's links
        # (none here, but the union ran) -- and the merge announced
        out = st.take_announcements()
        self.assertEqual([a["kind"] for a in out], ["merged"])
        # POINTER-FOLLOWING: the merged id resolves to its survivor
        self.assertIs(st._resolve_spec("dup-a"),
                      st.docs["membrane::Component:dup-b"])

    def test_ephemeral_never_announces(self):
        st = self._store()
        ent = QuantumEntity(eid="membrane::session:now", cls=CLASS_SESSION,
                            properties={"name": "now", "ephemeral": True},
                            derived_by=["x"])
        st.docs[ent.eid] = ent
        self.assertEqual(st.announcements, [])

    def test_queue_bounded(self):
        st = self._store()
        for i in range(20):
            st.add(cls=CLASS_COMPONENT, name=f"ent-{i}",
                   summary=f"entity {i}")
        self.assertLessEqual(len(st.announcements), 8)


if __name__ == "__main__":
    unittest.main()
