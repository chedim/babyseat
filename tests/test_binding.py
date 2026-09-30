"""Pronoun binding: the kettle/lamp deictic fix (membrane/binding.py).

THE FAILURE (field sim 0.9): "turn it off" for the kettle and "turn it off"
for the lamp embed identically -- the pronoun's referent lives outside the
sentence. The field dedups them or replays the wrong device's answer. The
fix is capture-time binding: rewrite before the embed + stamp the event;
the gate escalates any replay whose binding conflicts with the turn's.
The pure text mechanics here survive unchanged; the old Membrane-gate
integration tests died with runtime.py (the opencode seat owns the gate
now -- its binding use exercises _efgate's replay guard).
"""
import unittest

import numpy as np  # noqa: F401  (kept: the shape tests may bind vecs)

from membrane import binding


class TestBindingUnit(unittest.TestCase):
    def test_substitute_rewrites_first_pronoun(self):
        self.assertEqual(
            binding.substitute("turn it off", {"pronoun": "it",
                                               "referent": "lamp"}),
            "turn lamp off")
        self.assertEqual(
            binding.substitute("Turn it off please",
                               {"pronoun": "it", "referent": "lamp"}),
            "Turn lamp off please")

    def test_substitute_drops_reminders(self):
        q = 'turn it off <system-reminder>x</system-reminder>'
        self.assertEqual(
            binding.substitute(q, {"pronoun": "it", "referent": "lamp"}).strip(),
            "turn lamp off")

    def test_resolve_kettle_not_assistant(self):
        # the prepositional tail is not the referent: the object of the
        # clause before "to" is
        b = binding.resolve("turn it on", ["add a kettle to home assistant"])
        self.assertEqual(b, {"pronoun": "it", "referent": "kettle"})

    def test_resolve_skips_verb_only_turns(self):
        # "run it" has no noun candidate; the walk falls to the older turn
        b = binding.resolve("it", ["run it", "add a kettle to home assistant"])
        self.assertEqual(b, {"pronoun": "it", "referent": "kettle"})

    def test_resolve_none_when_no_antecedent(self):
        self.assertIsNone(binding.resolve("turn it on", ["yes okay"]))
        self.assertIsNone(binding.resolve("turn lamp on", ["the kettle"]))

    def test_conflict_rules(self):
        lamp = {"pronoun": "it", "referent": "lamp"}
        self.assertTrue(binding.conflict("turn it off",
                                         {"pronoun": "it",
                                          "referent": "kettle"}, lamp))
        self.assertFalse(binding.conflict("turn it off",
                                          {"pronoun": "it",
                                           "referent": "lamp"}, lamp))
        # unbound cell: geometry decides (legacy rows never conflict)
        self.assertFalse(binding.conflict("turn it off",
                                          {"pronoun": "it",
                                           "referent": "kettle"}, None))
        # explicit query: never conflicts
        self.assertFalse(binding.conflict("turn the lamp off", None, lamp))
        # unresolved pronoun: a replay would be a guess -> conflict
        self.assertTrue(binding.conflict("turn it off", None, lamp))

    def test_stamp(self):
        extra = {}
        binding.stamp(extra, {"binding": None, "query_raw": "x"})
        self.assertEqual(extra, {})          # no binding -> legacy shape
        binding.stamp(extra, {"binding": {"pronoun": "it", "referent": "lamp"},
                              "query_raw": "turn it off"})
        self.assertEqual(extra["bind"], {"pronoun": "it", "referent": "lamp"})
        self.assertEqual(extra["sit_raw"], "turn it off")


if __name__ == "__main__":
    unittest.main()