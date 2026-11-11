"""0.7.52 (the token bench, 2026-11-11): "What is Rrrr's real name?" missed "Rrrr's real name is Krabbs." although
the field held it. The answer sat inside a long docs sentence, so its vector was diluted below the floor, and the
word lane -- the lane for exactly this -- was filtered out: with a question vector, a word row must match a word the
ask writes as a NAME, and lexical.exact_words knew only digits, separators and inner capitals (MEMBRANE_JWT, PolyForm),
never a plain capitalized name (Rrrr, Caroline). It now also counts a capitalized word that is not the ask's opening
word -- the rule the entity lane (named_entities) already uses. A slash control the client appends (qwen's
` /no_think`) is never a name. Common words stay out: the word index only searches words in at most DF_MAX_FRAC of
the rows."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import lexical as LX  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0
BURIED = ("The page polls the seat's state on load, every 15 s while the page is visible, and after "
          "Rrrr's real name is Krabbs, the reconnect backoff doubles up to a minute with jitter added.")


class ExactWords(unittest.TestCase):
    def test_a_capitalized_name_inside_the_ask_is_a_name(self):
        self.assertIn("rrrr", LX.exact_words("What is Rrrr's real name?"))
        self.assertIn("caroline", LX.exact_words("What is Caroline's job?"))
        self.assertIn("rrrr", LX.exact_words("Where does Rrrr live?"))

    def test_the_opening_word_is_not_a_name_by_its_capital(self):
        self.assertNotIn("what", LX.exact_words("What is Rrrr's real name?"))
        self.assertNotIn("where", LX.exact_words("Where does Rrrr live? Then tell me."))
        self.assertNotIn("then", LX.exact_words("Where does Rrrr live? Then tell me."))

    def test_a_slash_control_is_not_a_name(self):
        self.assertEqual(LX.exact_words("What is Rrrr's real name? /no_think"), {"rrrr"})
        self.assertNotIn("think", LX.exact_words("summarize this /think"))

    def test_the_old_name_shapes_still_count(self):
        self.assertIn("membrane_jwt_secret", LX.exact_words("which variable holds the MEMBRANE_JWT_SECRET?"))
        self.assertIn("polyform", LX.exact_words("is it under PolyForm?"))


class BuriedNameIsRecalled(unittest.TestCase):
    def _field(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0, text=BURIED)], session="s:1:0")
        for i in range(40):                       # the rest of the book: names of other things
            f.learn(f"The banner section {i} names the model, the chat and the seat in its header line today.")
        f.process()
        return f

    def test_the_question_finds_the_buried_sentence(self):
        rows = EB.recall_answers(self._field(), "What is Rrrr's real name? /no_think", k=8)
        texts = " ".join(" ".join(r["answer"]) for r in rows)
        self.assertIn("Krabbs", texts)
        row = next(r for r in rows if "Krabbs" in " ".join(r["answer"]))
        self.assertIn("words", row["how"])


if __name__ == "__main__":
    unittest.main()
