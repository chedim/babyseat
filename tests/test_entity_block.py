"""membrane/entity_block.py: which entities a question gets, and what the
block shows about them (verbatim evidence, the latest observation,
relations, conflicts, the budget)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def seeded():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    evs = [ST.Event(stream="discourse", role="user", ts=T0,
                    text="membrane/router.py picks the free model for each ask."),
           ST.Event(stream="discourse", role="model", ts=T0 + 5,
                    text="The fallback in membrane/router.py retries the gateway twice."),
           ST.Event(stream="discourse", role="user", ts=T0 + 9,
                    text="the weather is nice today")]
    f.write_events(evs, session="s")
    obs = ST.split_turn("x", results=[("c1", "edit", '{"filePath": "membrane/router.py"}',
                                        "Edit applied successfully.")], ts=T0 + 20)[1]
    f.write_events([obs], session="s")
    f.process()
    return f


class TestBlock(unittest.TestCase):
    def test_named_identifier_with_its_evidence_and_observation(self):
        f = seeded()
        views = EB.entities_for(f, "what does membrane/router.py do on failure?")
        self.assertEqual(views[0]["eid"], "f:membrane/router.py")
        v = views[0]
        self.assertTrue(all("membrane/router.py" in t for t in v["evidence"]))
        self.assertIn("edit", v["observed"])
        text = EB.render(views, now=T0 + 20 + 3600)
        self.assertIn("▸ f:membrane/router.py [file · said 2 · observed 1 · 1h ago]", text)
        self.assertTrue(text.startswith(EB.HEADER))

    def test_everyday_words_need_coherence(self):
        f = seeded()
        eids = EB.question_entities(f, "is the weather nice today?")
        self.assertFalse([e for e in eids if e.startswith("w:")])

    def test_relations_and_conflicts_render(self):
        f = seeded()
        R.upsert(f.b, f.origin, "tool:edit", "edited", "f:membrane/router.py", "obs", ts=T0)
        st = f.get("f:membrane/router.py")
        f.b.upsert("claims", "cl:1", {"text": "I updated membrane/router.py.", "status": "contradicted",
                                      "evidence": [{"tool": "edit", "outcome": "failed"}]})
        st.conflicts.append("cl:1")
        text = EB.render(EB.entities_for(f, "membrane/router.py"), now=T0)
        self.assertIn("relations: tool:edit edited it", text)
        self.assertIn('! conflict: "I updated membrane/router.py." (contradicted: edit failed)', text)

    def test_budget_and_empty(self):
        f = seeded()
        self.assertIsNone(EB.render_for(f, "nothing known here"))
        self.assertIsNone(EB.render(EB.entities_for(f, "membrane/router.py"), budget=50))

    def test_related_lists_relation_partners(self):
        f = seeded()
        rel = EB.related(f, "membrane/router.py")
        self.assertEqual(rel[0]["key"], "f:membrane/router.py")
        self.assertEqual(rel[0]["how"], "named")
        self.assertIn({"key": "tool:edit", "shared": 1}, rel[0]["neighbors"])


if __name__ == "__main__":
    unittest.main()
