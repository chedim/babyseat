"""The receipt lane (E9-validated): byte-deterministic edit acks served
model-free, replayed from the TOOL RESULT side -- never in anticipation.

Verified assumptions (the covenant rails, each a test):
  R1 result-side replay -- the ack derives from the captured tool result;
     no cached result => fall through (never anticipate)
  R2 classification     -- edit success/fail map to the canonical strings;
     non-edit tools and ambiguous results fall through
  R3 escape hatch       -- 'model:' prefix wins over a receipt
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.opencode import _receipt_class, _EDIT_ACK, _EDIT_FAIL


class TestReceiptLane(unittest.TestCase):
    def test_r1_no_record_falls_through(self):
        """No captured tool record => None (never anticipate a result)."""
        self.assertIsNone(_receipt_class(None))
        self.assertIsNone(_receipt_class({}))

    def test_r2_edit_ack_from_result(self):
        rec = {"name": "edit", "query": "edit the file please",
               "result": "The file /tmp/x.py was edited successfully"}
        self.assertEqual(_receipt_class(rec), "edit-ack")
        # the audit's own phrasing (E9's corpus)
        self.assertEqual(_receipt_class(
            {"name": "edit", "result": "Edit applied successfully."}),
            "edit-ack")

    def test_r2b_edit_fail_from_result(self):
        rec = {"name": "edit",
               "result": "Error: Could not find oldString in the file. "
                         "It must match exactly."}
        self.assertEqual(_receipt_class(rec), "edit-fail")
        rec2 = {"name": "edit", "result":
                "Could not find oldString in the file. It must match "
                "exactly, including whitespace, indentation, and line "
                "endings."}
        self.assertEqual(_receipt_class(rec2), "edit-fail")

    def test_r3_non_edit_falls_through(self):
        rec = {"name": "bash", "result": "some output"}
        self.assertIsNone(_receipt_class(rec))

    def test_r2c_ambiguous_result_falls_through(self):
        """The degrade covenant: an edit result that is neither the
        canonical success nor the oldString miss goes to the model."""
        rec = {"name": "edit", "result": "some unusual edit outcome"}
        self.assertIsNone(_receipt_class(rec))


if __name__ == "__main__":
    unittest.main()
