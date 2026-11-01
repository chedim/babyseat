"""VERBS ARE ENTITIES (the operator, 2026-11-01: "map cliffhanger methods onto verb entities";
"different linked nouns might have different verb meanings/implementations"; "some words can
be both verbs and nouns").

Pinned here:
  - a method names a verb: `verb:<lemma>` (class Verb) -implemented-by-> the method, the
    method's own `verb` property pointing back; the verb entity mirrors its implementations
    (`implemented_by`), so dispatch needs no relations store;
  - one verb, many classes: dispatch picks by the object -- its own method, then its classes'
    (the ISA order), then a global one; a noun with no implementation gets none (another
    class's meaning is never borrowed);
  - the word and the role are separate: verb:run -word-> w:run, the field's word entity;
  - boot notes every existing method; method_define notes a new one; a retired method stops
    implementing; method names keep working (night.resolve_method unchanged);
  - the pronoun resolver's verb fit reads the verb entity.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import verbs as V  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import CLASS_COMPONENT, EntityStore  # noqa: E402


def _add(est, name, **props):
    d = est.docs[est.add(cls=CLASS_COMPONENT, name=name, summary="x")["eid"]]
    d.properties.update(props)
    return d


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None,
                         "capturer": type("C", (), {"mode": "on"})()})()
    est._srv = srv
    pr, goal = _add(est, "pull request"), _add(est, "goal kind")
    p803 = _add(est, "pr 803", parents=[pr.eid])
    g1 = _add(est, "goal one", parents=[goal.eid])
    readme = _add(est, "readme")
    return srv, f, est, pr, goal, p803, g1, readme


def _define(srv, f, est, on, name, cmd="echo x"):
    out = N.method_define(srv, est, f, None, on, name, cmd)
    assert out.get("ok"), out
    return est.docs[out["eid"]]


class Verbs(unittest.TestCase):
    def test_a_method_names_a_verb(self):
        srv, f, est, pr, *_ = _world()
        m = _define(srv, f, est, pr.eid, "close", "echo close-pr")
        v = est.docs[V.verb_eid("close")]
        self.assertEqual(v.cls, V.CLASS_VERB)
        self.assertEqual(v.properties["implemented_by"], [m.eid])
        self.assertEqual(m.properties["verb"], v.eid)
        rows = R.of(f.b, f.origin, v.eid, "implemented-by")
        self.assertEqual([r["obj"] for r in rows if r["subj"] == v.eid], [m.eid])

    def test_one_verb_many_classes_dispatch_by_the_object(self):
        srv, f, est, pr, goal, p803, g1, readme = _world()
        m_pr = _define(srv, f, est, pr.eid, "close", "echo close-pr")
        m_goal = _define(srv, f, est, goal.eid, "close", "echo close-goal")
        self.assertEqual(V.implementation(est, p803, "close")[0].eid, m_pr.eid)
        self.assertEqual(V.implementation(est, g1, "close")[0].eid, m_goal.eid)
        self.assertIsNone(V.implementation(est, readme, "close")[0])
        self.assertEqual(sorted(V.implementers(est, "close")), sorted([pr.eid, goal.eid]))

    def test_an_objects_own_method_comes_before_its_class(self):
        srv, f, est, pr, goal, p803, *_ = _world()
        _define(srv, f, est, pr.eid, "close", "echo class")
        own = _define(srv, f, est, p803.eid, "close", "echo own")
        self.assertEqual(V.implementation(est, p803, "close")[0].eid, own.eid)

    def test_the_word_and_the_role_are_separate(self):
        srv, f, est, pr, *_ = _world()
        _define(srv, f, est, pr.eid, "run_tests", "echo t")
        v = est.docs[V.verb_eid("run tests")]
        self.assertEqual(v.properties["word"], "w:run")
        rows = [r for r in R.of(f.b, f.origin, v.eid, "word") if r["subj"] == v.eid]
        self.assertEqual([r["obj"] for r in rows], ["w:run"])

    def test_boot_notes_existing_methods_and_a_retired_one_stops(self):
        srv, f, est, pr, goal, p803, *_ = _world()
        m = _define(srv, f, est, pr.eid, "close", "echo x")
        est.docs.pop(V.verb_eid("close"))
        m.properties.pop("verb", None)
        self.assertEqual(V.boot(srv)["verbs"], 1)
        self.assertEqual(est.docs[V.verb_eid("close")].properties["implemented_by"], [m.eid])
        m.properties["retired"] = True
        V.boot(srv)
        self.assertIsNone(V.implementation(est, p803, "close")[0])

    def test_method_names_keep_working(self):
        srv, f, est, pr, goal, p803, *_ = _world()
        m = _define(srv, f, est, pr.eid, "close", "echo x")
        self.assertEqual(N.resolve_method(est, p803, "close")[0].eid, m.eid)

    def test_the_resolver_reads_the_verb(self):
        from membrane import convgraph as CG
        srv, f, est, pr, goal, p803, g1, readme = _world()
        _define(srv, f, est, pr.eid, "merge", "echo m")
        self.assertEqual(CG._applicable(est, [readme.eid, p803.eid], "merge"), [p803.eid])


if __name__ == "__main__":
    unittest.main()
