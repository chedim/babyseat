"""Issue #127: a second process that only wants to READ the field must not
take the writer lock. `membrane review --metrics` and `--ab` (audit) are
read-only telemetry/snapshots -- beside a live serve they used to raise
StoreBusy (the sidecar flock), so the operator had to stop the seat to see
what the field holds.

The fix: a `readonly` open that never takes the sidecar writer flock and
never starts the write-behind/vector threads; `review` uses it."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402

HAVE = cblite.lib_paths() is not None


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestReadonlyOpen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_a_readonly_backend_opens_beside_a_live_store(self):
        from membrane.cblstore import CBLiteBackend
        p = Path(self.tmp) / "field.cblite"
        w = CBLiteBackend(p)
        try:
            w.upsert("entity_states", "e1",
                     {"origin": "o", "tier": "live", "kind": "Device", "mass": 1.0})
            w.flush()                              # drain to disk: the reader loads from disk
            # the live writer holds the flock; a read-only open must NOT raise
            ro = CBLiteBackend(p, readonly=True)
            try:
                self.assertIsNone(ro._lock_fd)     # no writer lock was taken
                self.assertIsNone(ro._writer)      # no write-behind thread
                self.assertEqual(ro.get("entity_states", "e1")["mass"], 1.0)
                self.assertEqual(ro.get("entity_states", "e1")["kind"], "Device")
            finally:
                ro.close()
        finally:
            w.close()

    def test_open_backend_readonly_is_readonly_cbl(self):
        from membrane.cblstore import CBLiteBackend
        # open_backend derives the cblite dir as <parent>/.membrane/field.cblite
        # from a field path like <parent>/field.json -- open the writer there.
        p = Path(self.tmp) / ".membrane" / "field.cblite"
        p.parent.mkdir(parents=True, exist_ok=True)
        w = CBLiteBackend(p)
        try:
            w.flush()
            with mock.patch("membrane.substrate._is_lab_field_path", return_value=False):
                ro = efstore.open_backend(str(Path(self.tmp) / "field.json"), readonly=True)
            try:
                self.assertIsInstance(ro, CBLiteBackend)
                self.assertTrue(ro.readonly)
            finally:
                ro.close()
        finally:
            w.close()


class TestReviewReadonly(unittest.TestCase):
    def test_vitals_and_audit_pass_readonly_to_open_backend(self):
        from membrane import review
        seen = []

        def fake_open(field_path="", readonly=False):
            seen.append(bool(readonly))
            return MemBackend()

        with mock.patch("membrane.efstore.open_backend", side_effect=fake_open), \
                mock.patch("membrane.project_env.load_project_env"):
            review.vitals_report("/tmp/x/field.json")
            review.ab_summary("/tmp/x/field.json")
        self.assertEqual(seen, [True, True],
                         "vitals (--metrics) and audit (--ab) must open read-only")


if __name__ == "__main__":
    unittest.main()
