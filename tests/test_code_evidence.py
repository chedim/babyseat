"""membrane/code_evidence.py: the codebase as evidence -- one CODE event
per definition, its own symbol + file as entities, supersede on change,
and call/defined-in relations that resolve only when unambiguous."""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import code_evidence as CE  # noqa: E402
from membrane import codegraph  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane.capture import Capturer  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

SRC = {
    "pkg/net.py": '''
        def pick_port(preferred, tries=64):
            """A port the proxy can actually bind. The probe walks up from it."""
            return free(preferred)
        def free(port):
            """True when nothing listens there."""
        def main():
            """Run net."""
            pick_port(11500)
        ''',
    "pkg/cli.py": '''
        def main():
            """Run the cli."""
            free(1)
        ''',
}


def repo():
    d = tempfile.mkdtemp(prefix="ef-code-")
    for rel, src in SRC.items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    return d


class TestCodeEvents(unittest.TestCase):
    def setUp(self):
        self.root = repo()
        self.g = codegraph.build_graph(self.root)

    def test_one_event_per_definition_with_its_docstring(self):
        evs = {e.code["full"]: e for e in CE.code_events(self.g)}
        self.assertEqual(sorted(evs), ["pkg.cli.main", "pkg.net.free", "pkg.net.main",
                                       "pkg.net.pick_port"])
        self.assertEqual(evs["pkg.net.pick_port"].text,
                         "pick_port(preferred, tries=64) in pkg/net.py: A port the proxy "
                         "can actually bind. The probe walks up from it.")

    def test_relations(self):
        rel = set(CE.code_relations(self.g))
        self.assertIn(("s:pkg.net.pick_port", "calls", "s:pkg.net.free"), rel)
        self.assertIn(("s:pkg.net.main", "calls", "s:pkg.net.pick_port"), rel)
        self.assertIn(("s:pkg.cli.main", "defined_in", "f:pkg/cli.py"), rel)
        # 'main' is ambiguous (two files): nothing resolves a call to it
        self.assertFalse(any(o.endswith(".main") and r == "calls" for _, r, o in rel))

    def test_the_symbol_entity_carries_its_code_as_evidence(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host", graph_fn=lambda: self.g)
        f.write_events(CE.code_events(self.g))
        f.process()
        st = f.get("s:pkg.net.pick_port")
        self.assertEqual(st.tier, "live")
        self.assertEqual(st.n_by_stream, {"code": 1})
        ev = f.evidence("s:pkg.net.pick_port")
        self.assertIn("The probe walks up from it.", ev[0]["text"])
        self.assertEqual(f.get("f:pkg/net.py").n, 3)

    def test_a_changed_definition_supersedes_its_old_evidence(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host", graph_fn=lambda: self.g)
        cap = Capturer(f, mode="on", start=False)
        cap.on_code(self.g, ["pkg/net.py", "pkg/cli.py"])
        cap.flush()
        p = Path(self.root) / "pkg/net.py"
        p.write_text(p.read_text().replace("The probe walks up from it.",
                                           "It never retries another port."))
        r = codegraph.rebuild_paths(self.g, ["pkg/net.py"])
        cap.on_code(self.g, r["touched"])
        cap.flush()
        texts = [e["text"] for e in f.evidence("s:pkg.net.pick_port", k=10)]
        self.assertEqual(len(texts), 1)
        self.assertIn("never retries", texts[0])
        sup = [d for _, d in f.b.scan("events", {"status": "superseded"})]
        self.assertEqual(len(sup), 1)
        self.assertIn("walks up", sup[0]["text"])


if __name__ == "__main__":
    unittest.main()
