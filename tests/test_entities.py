"""Entity layer: deterministic extraction, the entity index, QuantumEntity
docs (cortex compat), the synthesizer's parser, and the EntityStore merge
contract. Pure-data tests (no Ollama, no Couchbase -- the store degrades to
JSON-only)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import (extract_entities, entity_index, iter_entities,
                               query_entities, QuantumEntity, EntityStore,
                               CLASS_DECISION, CLASS_MODEL, CLASSES,
                               MEMBRANE_NS, entity_id)
from membrane.entity_synthesis import parse_entity_reply, synthesize_from_cluster


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


class TestIndex(unittest.TestCase):
    def test_invert_and_roundtrip(self):
        class E:
            def __init__(self, sit, act, safe=True):
                self.situation, self.action, self.safe = sit, act, safe
                self.extra = {"entities": extract_entities(sit, act)}
                self.model = ""
        cells = [
            E("the router.py ladder skips empty answers",
              "note_outage cools the rung down"),
            E("unrelated prose", "nothing here"),
            E("unsafe one", "x", safe=False),
        ]
        inv = entity_index(cells)
        self.assertIn("files:router.py", inv)
        self.assertEqual(inv["files:router.py"], [0])   # the unsafe one out


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


class TestParseReply(unittest.TestCase):
    def test_json_with_prose(self):
        items = parse_entity_reply(
            'Entities:\n[{"class": "FailureMode", "name": "empty-200",'
            ' "summary": "free rung answers 200 empty"}] done.')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["class"], "FailureMode")

    def test_garbage_degrades(self):
        self.assertEqual(parse_entity_reply("no json here"), [])
        self.assertEqual(parse_entity_reply("[]"), [])
        self.assertEqual(parse_entity_reply(""), [])
        # unknown class -> Component (safest)
        items = parse_entity_reply(
            '[{"class": "Weird", "name": "some-entity", "summary": "s"}]')
        self.assertEqual(items[0]["class"], "Component")

    def test_no_name_dropped(self):
        self.assertEqual(parse_entity_reply(
            '[{"class": "Decision", "summary": "no name"}]'), [])
        # a 1-char name is noise -> dropped
        self.assertEqual(parse_entity_reply(
            '[{"class": "Decision", "name": "x", "summary": "s"}]'), [])


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
            self.assertEqual(len(st.docs), 1)       # never a second doc
            st2 = EntityStore(path=p)               # reload from JSON
            self.assertEqual(len(st2.docs), 1)
            self.assertEqual(st2.find("a").eid, "membrane::synth:a")


class TestBoundaryStateMachine(unittest.TestCase):
    """The banner's EVENT lane: 'enter N'/'exit N' boundaries over the live
    safe-cell count -- a state machine (alert once per transition; re-entry
    re-arms by construction), not a level check."""

    def _store(self, tmp):
        p = str(Path(tmp) / "entities.json")
        return EntityStore(path=p)

    class E:
        def __init__(self, sit, act="", safe=True):
            self.situation, self.action, self.safe = sit, act, safe
            self.extra = {"entities": extract_entities(sit, act)}
            self.model = ""

    def _seed(self, st, n, sit="work on router.py"):
        st.docs = {}
        cells = [self.E(sit) for _ in range(n)]
        # safe cells only: entity_index skips unsafe
        for c in cells:
            st_field = type("F", (), {"exemplars": cells})()
        return cells

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
            cells = [self.E("touch router.py") for _ in range(3)]
            r = st.watch_track("files:router.py", boundary="enter 3")
            self.assertTrue(r["ok"])
            # seed eval: silent (no alert on plant)
            self.assertEqual(st.evaluate_tracks(cells), [])
            self.assertTrue(st.list_tracks()[0]["inside"])
            # +1 cell -> no transition -> no alert (still inside)
            cells.append(self.E("touch router.py"))
            self.assertEqual(st.evaluate_tracks(cells), [])
            # drop to 1 -> EXIT transition -> one alert
            del cells[1:]
            alerts = st.evaluate_tracks(cells)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["kind"], "exited")
            # re-grow to 3 -> RE-ENTER fires again (re-arm by construction)
            cells.extend(self.E("touch router.py") for _ in range(2))
            alerts = st.evaluate_tracks(cells)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["kind"], "entered")
            # stable inside -> no more alerts
            self.assertEqual(st.evaluate_tracks(cells), [])

    def test_exit_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cells = [self.E("touch router.py") for _ in range(5)]
            st.watch_track("files:router.py", boundary="exit 2")
            self.assertEqual(st.evaluate_tracks(cells), [])  # seed: inside
            del cells[2:]                                    # 5 -> 2, still in
            self.assertEqual(st.evaluate_tracks(cells), [])
            del cells[1:]                                    # 2 -> 1: out
            alerts = st.evaluate_tracks(cells)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["kind"], "exited")
            self.assertEqual(st.list_tracks()[0]["inside"], False)

    def test_take_alerts_drains_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cells = [self.E("touch router.py")]
            st.watch_track("files:router.py", boundary="enter 2")
            st.evaluate_tracks(cells)                        # seed (silent)
            cells.append(self.E("touch router.py"))          # -> inside
            alerts = st.evaluate_tracks(cells)
            self.assertEqual(len(alerts), 1)
            # drained exactly once; second drain empty
            self.assertEqual(len(st.take_alerts()), 1)
            self.assertEqual(st.take_alerts(), [])
            # list shows the state, no pending
            t = st.list_tracks()[0]
            self.assertEqual(t["inside"], True)
            self.assertEqual(t["pending_alerts"], 0)

    def test_count_only_track_never_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = self._store(tmp)
            cells = [self.E("touch router.py")]
            st.watch_track("files:router.py")
            self.assertEqual(st.evaluate_tracks(cells), [])
            cells.append(self.E("touch router.py"))
            self.assertEqual(st.evaluate_tracks(cells), [])
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
            cells = [self.E("touch router.py") for _ in range(5)]
            st.watch_track("files:router.py", boundary="enter 2")
            st.evaluate_tracks(cells)                        # seed: inside
            self.assertTrue(st.list_tracks()[0]["inside"])
            # changed boundary -> fresh machine; new seed is silent
            st.watch_track("files:router.py", boundary="enter 9")
            self.assertIsNone(st.list_tracks()[0].get("inside"))
            self.assertEqual(st.evaluate_tracks(cells), [])  # silent seed
            self.assertFalse(st.list_tracks()[0]["inside"])

    def test_tracks_persist_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = str(Path(tmp) / "entities.json")
            st = EntityStore(path=p)
            st.watch_track("files:router.py", boundary="enter 3",
                           label="router watch")
            cells = [self.E("touch router.py") for _ in range(3)]
            st.evaluate_tracks(cells)                        # seed: inside
            st2 = EntityStore(path=p)                        # reload
            t = st2.list_tracks()[0]
            self.assertEqual(t["key"], "files:router.py")
            self.assertEqual(t["label"], "router watch")
            self.assertEqual(t["enter"], 3)
            self.assertEqual(t["inside"], True)


if __name__ == "__main__":
    unittest.main()