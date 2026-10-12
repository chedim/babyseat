"""THE ONE SPELLING (2026-10-11 sweep): one stored entity lived as up to four
field states, and every cross-store lookup missed somewhere -- consequences
said "the entity is gone", field:changed never found its entity, a
Component track read the WORD, class entities never drifted (so their
proximity watches never fired), the night minted twins. Now:
eids.field_id (store -> field), EntityStore.resolve / by_field_id
(any spelling -> the stored entity)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import consequences as CQ  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _store():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    for eid, name in (("Component:decision", "decision"), ("Component:ha:light.aft", "aft"),
                      ("tool:clock", "clock"), ("files:router.py", "router.py"),
                      ("Component:indecision", "indecision")):
        e = QuantumEntity(eid=MEMBRANE_NS + eid, cls="Component", properties={"name": name, "state": "on"})
        est.docs[e.eid] = e
    return est


class TestFieldId(unittest.TestCase):
    def test_the_spellings(self):
        for a, b in (("membrane::Component:decision", "m:component:decision"),
                     ("membrane::Component:ha:light.aft", "ha:light.aft"),
                     ("membrane::tool:clock", "tool:clock"),
                     ("membrane::files:router.py", "f:router.py"),
                     ("membrane::shell:git", "c:git"),
                     ("membrane::llm", "m:llm"),
                     ("m:component:decision", "m:component:decision")):
            self.assertEqual(field_id(a), b, a)


class TestResolve(unittest.TestCase):
    def test_any_spelling_finds_the_doc(self):
        est = _store()
        for spec, want in (("m:component:decision", "Component:decision"),
                           ("component:decision", "Component:decision"),
                           ("f:router.py", "files:router.py"),
                           ("ha:light.aft", None)):                     # a different entity's inner name: not a guess
            hit = est.resolve(spec, exact=True)
            self.assertEqual(hit.eid if hit else None, MEMBRANE_NS + want if want else None, spec)

    def test_exact_never_guesses_a_suffix(self):
        est = _store()
        self.assertIsNone(est.resolve("decision", exact=True))
        self.assertIsNotNone(est.resolve("decision"))                   # the forgiving read still may

    def test_field_ids_back_to_docs(self):
        est = _store()
        for fid, want in (("m:component:decision", "Component:decision"), ("ha:light.aft", "Component:ha:light.aft"),
                          ("tool:clock", "tool:clock"), ("f:router.py", "files:router.py")):
            self.assertEqual(est.by_field_id(fid).eid, MEMBRANE_NS + want, fid)
        self.assertIsNone(est.by_field_id("w:decision"))


class TestTheConsumers(unittest.TestCase):
    def test_a_consequence_on_a_mirror_spec_is_not_gone(self):
        est = _store()
        srv = type("S", (), {"entity_store": est, "efield": None})()
        CQ.record(srv, "m:component:decision", "x", session="s1", kind="entity", now=1e9)
        CQ.settle(srv, now=1e9 + 3700)
        line = est.docs[MEMBRANE_NS + "Component:decision"].properties["consequences"][-1]["line"]
        self.assertNotIn("gone", line)

    def test_stamps_never_hit_a_suffix_lookalike(self):
        est = _store()
        est.stamp_watch_decision("decision", {"verdict": "done"})
        self.assertNotIn("watch_last_decision", est.docs[MEMBRANE_NS + "Component:indecision"].properties)

    def test_a_watch_rel_in_any_spelling(self):
        est = _store()
        est.docs[MEMBRANE_NS + "Component:decision"].position = [1.0] + [0.0] * 7
        w = QuantumEntity(eid=MEMBRANE_NS + "Component:watcher", cls="Component", properties={
            "name": "watcher", "watch_ops": [{"op": "near", "rel": "m:component:decision", "z": 0.25, "armed": True}]})
        w.position = [0.0, 1.0] + [0.0] * 6
        est.docs[w.eid] = w
        q = WA.WatchEvents()
        WA.sweep_watch_ops(est, q, {}, now=1.0, cooldown_s=0)
        w.position = [0.99, 0.1] + [0.0] * 6
        fired = WA.sweep_watch_ops(est, q, {}, now=2.0, cooldown_s=0)
        self.assertEqual([e["kind"] for e in fired], ["near"])

    def test_a_track_key_finds_its_doc_not_a_twin(self):
        est = _store()
        self.assertEqual(est._entity_for_track("m:component:decision").eid, MEMBRANE_NS + "Component:decision")


if __name__ == "__main__":
    unittest.main()
