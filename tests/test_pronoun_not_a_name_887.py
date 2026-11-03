"""#887 C, live 2026-11-03: membrane's first words were "Now: I isn't yes" -- "I" was read as the
component named "I" (the predictor heartbeat). A pronoun means only what its register holds."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_living_language import HAVE, SID, _add, _world  # noqa: E402
from membrane import language as LL  # noqa: E402


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class PronounNotAName(unittest.TestCase):
    def test_i_is_not_the_thing_named_i(self):
        srv, f, est, *_ = _world()
        _add(est, "I")
        u = LL.understand(srv, SID, "yes, I did")
        self.assertIn("I", u["surprise"])
        self.assertNotIn("I isn't", LL.respond(srv, SID, "is I open?"))

    def test_a_thing_named_in_full_still_maps(self):
        srv, *_ = _world()
        self.assertEqual(LL.understand(srv, SID, "goal one")["share"], 1.0)


if __name__ == "__main__":
    unittest.main()
