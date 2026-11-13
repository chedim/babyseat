"""The entity field's golden fixtures (tests/golden/efield/) match the current Python.

The fixtures are the exact target of the Rust port of membrane/efield.py. A change
to the Python field that moves any recorded value fails here: regenerate with
`python scripts/golden_efield.py` knowingly, and the port must follow.
Floats compare at abs 1e-9 (BLAS summation order may differ in the last bits
across machines); the port's own tolerances are in tests/golden/efield/README.md.
"""
import importlib.util
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden" / "efield"
_spec = importlib.util.spec_from_file_location("golden_efield", ROOT / "scripts" / "golden_efield.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


def same(a, b, path="", tol=1e-9):
    """None when equal, else a message naming the first difference."""
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return f"{path}: keys {sorted(set(a) ^ set(b))[:5]}"
        for k in a:
            r = same(a[k], b[k], f"{path}/{k}", tol)
            if r:
                return r
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = same(x, y, f"{path}[{i}]", tol)
            if r:
                return r
        return None
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9, abs_tol=tol):
            return None
        return f"{path}: {a!r} != {b!r}"
    return None if a == b else f"{path}: {a!r} != {b!r}"


class TestGoldenEfield(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fresh = G.build()

    def test_every_scenario_has_a_committed_fixture(self):
        self.assertEqual({p.stem for p in GOLDEN.glob("*.json")}, set(G.SCENARIOS))

    def test_fixtures_match_the_current_field(self):
        for name, doc in self.fresh.items():
            with self.subTest(name):
                committed = json.loads((GOLDEN / f"{name}.json").read_text())
                self.assertIsNone(same(doc, committed), "regenerate: python scripts/golden_efield.py")

    def test_generation_is_deterministic(self):
        again = G.build()
        for name, doc in self.fresh.items():
            with self.subTest(name):
                self.assertEqual(G.dumps(doc), G.dumps(again[name]))

    def test_rebuild_reproduces_the_incremental_states(self):
        d = self.fresh["rebuild"]
        self.assertEqual(d["rebuild_applied"], len(d["inputs"]))
        self.assertIsNone(same(d["incremental"], d["rebuilt"], tol=0.0))
        self.assertEqual(d["incremental_origin"], d["rebuilt_origin"])


if __name__ == "__main__":
    unittest.main()
