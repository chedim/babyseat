"""The per-rung turn budget (90 s since #431) is a setting: MEMBRANE_TURN_BUDGET_S. A slow local model (qwen3:8b
writes ~25 tokens/s, so a 2,000-token reply needs ~80 s) fails turns that a plain transcript harness waits out; the
owner can give it longer. Unset or unreadable keeps 90."""
import unittest

from membrane import lanes as L


class TurnBudget(unittest.TestCase):
    def test_unset_keeps_ninety(self):
        self.assertEqual(L.turn_budget({}), 90)

    def test_the_setting_sets_it(self):
        self.assertEqual(L.turn_budget({"MEMBRANE_TURN_BUDGET_S": "600"}), 600)
        self.assertEqual(L.turn_budget({"MEMBRANE_TURN_BUDGET_S": "2.5"}), 2.5)

    def test_nonsense_keeps_ninety(self):
        for v in ("", "abc", "0", "-5", "nan", "inf"):
            self.assertEqual(L.turn_budget({"MEMBRANE_TURN_BUDGET_S": v}), 90, v)


if __name__ == "__main__":
    unittest.main()
