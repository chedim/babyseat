"""The slash lane (membrane/slash.py): foreign words -> the field's
dialect as slash-groups; field words untouched; the antonym guard; the
advisory merge never demotes the plain lane."""

import unittest

import numpy as np

from membrane.slash import SlashVocab, merge_max, _same_stem

# a tiny semantic space: synonyms share an axis, everything else is its own
_AXES = {"revert": 0, "undo": 0, "rollback": 0,
         "restart": 1, "reboot": 1, "reset": 1,
         "commit": 2, "field": 3, "memory": 4}


class _Emb:
    model = "fake"

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(64)
            words = t.lower().replace("/", " ").split()
            for w in words:
                v[_AXES.get(w, 10 + sum(map(ord, w)) % 50)] += 1.0
            out.append(v / (np.linalg.norm(v) + 1e-12))
        return out


_CORPUS = ["revert the last commit", "revert the commit please",
           "revert it again", "restart the field", "restart memory now",
           "restart the memory field", "the commit landed", "commit it",
           "commit and push", "field memory field", "memory memory"]


class TestSlash(unittest.TestCase):
    def setUp(self):
        self.v = SlashVocab(_Emb(), _CORPUS, min_freq=3)

    def test_vocab_is_the_fields_frequent_content_words(self):
        self.assertIn("revert", self.v.words)
        self.assertNotIn("the", self.v.words)      # stopword
        self.assertNotIn("push", self.v.words)     # seen once

    def test_foreign_word_joins_the_fields_synonym(self):
        self.assertEqual(self.v.expand("undo the commit"),
                         "undo/revert the commit")

    def test_field_words_stay_untouched(self):
        self.assertEqual(self.v.expand("revert the commit"),
                         "revert the commit")

    def test_every_mode_groups_field_words_too(self):
        # every=True slashes known words as well, but only when a
        # non-inflection neighbour exists (none for "revert" here)
        self.assertEqual(self.v.expand("reboot the field", every=True),
                         "reboot/restart the field")

    def test_no_neighbour_leaves_text_as_is(self):
        self.assertEqual(self.v.expand("banana split"), "banana split")

    def test_context_guard_can_veto_a_group(self):
        # an impossible bar: no swap keeps the sentence at cos >= 1.01
        self.assertEqual(self.v.expand("undo the commit", ctx_min=1.01),
                         "undo the commit")

    def test_inflections_are_not_synonyms(self):
        self.assertTrue(_same_stem("tests", "testing"))
        self.assertTrue(_same_stem("fix", "fixed"))
        self.assertTrue(_same_stem("commit", "committed"))
        self.assertFalse(_same_stem("revert", "rollback"))
        self.assertFalse(_same_stem("reset", "restart"))

    def test_merge_never_demotes_the_plain_lane(self):
        plain = np.array([0.9, 0.1, 0.5])
        slash = np.array([0.2, 0.8, 0.4])
        np.testing.assert_allclose(merge_max(plain, slash), [0.9, 0.8, 0.5])


if __name__ == "__main__":
    unittest.main()
