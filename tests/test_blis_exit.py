"""spaCy's blis registers an atexit finalizer that abort()s when a parse on another thread still
holds its memory pool at exit (two Python crash reports on 2026-11-01: bli_apool_finalize ->
bli_abort, from atexit). Loading the model takes that finalizer off; the OS frees the memory."""
import atexit
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import language as LL  # noqa: E402


@unittest.skipUnless(LL.available(), "spaCy and en_core_web_sm not installed")
class BlisExit(unittest.TestCase):
    def test_loading_the_model_takes_blis_finalize_off_exit(self):
        LL._nlp()
        import blis.cy
        before = atexit._ncallbacks()
        atexit.unregister(blis.cy.finalize)
        self.assertEqual(atexit._ncallbacks(), before)   # it was already off


if __name__ == "__main__":
    unittest.main()
