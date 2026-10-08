"""THE SPECS THAT NEVER RESOLVED (2026-10-08, the transcript review, red first).

30% of entity_read calls failed (232 of 771), and 18 misses were CIRCULAR:
the refusal's "nearest" was the very spec that had just failed
('component:argus:sense:agdroid-call' -> "nearest: component:argus:sense:
agdroid-call"). near_specs lowercased the eid; the resolver compared case-
sensitively against 'membrane::Component:...'. And the keys the banner and
entities_related print never resolved: m: (the field's mirror of
membrane::), s:/f:/c:/u: (the field's short kinds of the store's long
symbols:/files:/shell:/urls:), w:/p: (field-only words and phrases).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _est():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    for eid, cls in (("Component:argus:sense:agdroid-call", "Component"),
                     ("symbols:?tick_every", "Symbol"),
                     ("files:membrane/router.py", "File"),
                     ("goal:abc123", "Goal"),
                     ("models:glm-5.3-flash", "Model")):
        est.docs[MEMBRANE_NS + eid] = QuantumEntity(eid=MEMBRANE_NS + eid, cls=cls,
                                                    properties={"name": eid})
    return est


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.est = _est()

    def _is(self, spec, eid):
        e = self.est._resolve_spec(spec)
        self.assertIsNotNone(e, spec)
        self.assertEqual(e.eid, MEMBRANE_NS + eid, spec)

    def test_case_does_not_matter(self):
        self._is("component:argus:sense:agdroid-call", "Component:argus:sense:agdroid-call")
        self._is("membrane::component:argus:sense:agdroid-call", "Component:argus:sense:agdroid-call")

    def test_the_mirror_prefix_peels(self):
        self._is("m:goal:abc123", "goal:abc123")
        self._is("m:component:argus:sense:agdroid-call", "Component:argus:sense:agdroid-call")
        self._is("m:glm-5.3-flash", "models:glm-5.3-flash")

    def test_field_short_kinds_map_to_the_store(self):
        self._is("s:?tick_every", "symbols:?tick_every")
        self._is("f:membrane/router.py", "files:membrane/router.py")

    def test_nearest_is_never_the_failed_spec_and_always_resolves(self):
        for spec in ("component:argus:sense:agdroid-cal", "agdroid-call-x", "tick_ever"):
            near = self.est.near_specs(spec)
            for n in near:
                self.assertNotEqual(n.lower(), spec.lower())
                self.assertIsNotNone(self.est._resolve_spec(n), (spec, n))

    def test_field_only_words_explain_themselves(self):
        self.assertIsNone(self.est._resolve_spec("w:battery"))
        self.assertIn("field", self.est.miss_hint("w:battery"))
        self.assertIn("entities_related", self.est.miss_hint("w:battery"))
        self.assertEqual(self.est.miss_hint("component:nope"), "")


if __name__ == "__main__":
    unittest.main()
