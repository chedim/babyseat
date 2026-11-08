"""A corrupt journal line is counted (skipped_docs), not silently dropped."""
import tempfile
import unittest
from pathlib import Path

from membrane.efstore import FileBackend


class TestSkippedDocs(unittest.TestCase):
    def test_corrupt_line_is_counted(self):
        with tempfile.TemporaryDirectory(prefix="skipped-") as tmp:
            be = FileBackend(tmp)
            be.upsert("terms", "t:x", {"_k": "term", "term": "x"})
            be.close()
            with (Path(tmp) / "terms.jsonl").open("a") as fh:
                fh.write('{"k": "t:y", "d": {"te\n')
            be2 = FileBackend(tmp)
            self.assertEqual(be2.skipped_docs.get("terms"), 1)
            self.assertIn("t:x", be2.colls["terms"])
            be2.close()


if __name__ == "__main__":
    unittest.main()
