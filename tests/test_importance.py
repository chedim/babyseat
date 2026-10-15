"""Issue #119: the importance axis for mentions. A definitional mention (a
term mined from a re-read document, role=doc) must outrank tens of routine
tool mentions (observation/code) in the ranking surfaces -- WITHOUT touching
the raw counts. The counts stay honest history; only the ordering changes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


def _alpha(i: int) -> str:
    """A distinct alphabetic token for ``i`` (a, b, … z, aa, ab, …): unique
    without digits, since ``norm_sentence`` folds digits to 0 and would
    collapse every numbered head into one boilerplate key."""
    s = ""
    while True:
        s = chr(ord("a") + i % 26) + s
        i = i // 26 - 1
        if i < 0:
            return s


def _shell_events(n=69):
    """``n`` routine tool mentions: distinct observation events naming only
    ``tool:shell`` (empty args, so no file/command entities are planted).
    Each result head is >= 12 chars and distinct per event, so every event
    yields its OWN observation fact -- a short shared head would collapse
    them into one boilerplate sentence and the honest ``(n)`` count would
    read 8 instead of ``n``."""
    return [ST.Event(stream="observation", tool="shell", args={},
                     result_head=f"shell result {_alpha(i)}", outcome="ok",
                     text=f"shell result {_alpha(i)}") for i in range(n)]


class TestImportanceAxis(unittest.TestCase):
    def test_a_definitional_mention_outranks_routine_tool_counts(self):
        f = _field()
        f.write_events([ST.Event(
            stream="discourse", role="doc",
            text="The equivalence principle states that gravity and "
                 "acceleration are locally indistinguishable.",
            src="artifact:paper.md", tool="mine", subject="f:paper.md")],
            session="artifact")
        f.write_events(_shell_events(69), session="s")
        f.process()
        ln = EB.now_line(f, 0)
        self.assertIsNotNone(ln)
        self.assertIn("f:paper.md", ln)
        self.assertIn("tool:shell", ln)
        self.assertLess(ln.index("f:paper.md"), ln.index("tool:shell"),
                        f"definitional mention buried under routine tool: {ln}")
        # the raw count is untouched: 69 still reads 69
        self.assertIn("tool:shell (69)", ln, f"count distorted: {ln}")

    def test_importance_reads_the_doc_role_only(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="doc",
                                 text="a defined term is a phrase",
                                 subject="f:term.md", tool="mine")],
                       session="artifact")
        f.write_events([ST.Event(stream="observation", tool="shell", args={},
                                 result_head="ok", outcome="ok", text="ok")],
                       session="s")
        f.process()
        self.assertGreater(EB.importance(f.get("f:term.md")), 0.0)
        self.assertEqual(EB.importance(f.get("tool:shell")), 0.0)

    def test_importance_fades_with_age(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="doc",
                                 text="a defined term is a phrase",
                                 subject="f:term.md", tool="mine")],
                       session="artifact")
        f.process()
        st = f.get("f:term.md")
        # anchor 'now' to the state's own last-seen time: a fresh definition
        # reads its full weight, and a week later it has halved (the half-life
        # is the fade, not the absolute clock)
        now = EF.last_seen(st) or 0.0
        fresh = EB.importance(st, now=now)
        self.assertGreater(fresh, 0.0)
        stale = EB.importance(st, now=now + EB.DEFINITIONAL_HALF_LIFE_S)
        self.assertLess(stale, fresh)
        self.assertGreater(stale, 0.0)

    def test_routine_entities_still_rank_by_count(self):
        f = _field()
        f.write_events(_shell_events(69), session="s")
        f.write_events([ST.Event(stream="observation", tool="read", args={},
                                 result_head="ok", outcome="ok", text="ok")],
                       session="s")
        f.process()
        ln = EB.now_line(f, 0)
        self.assertIsNotNone(ln)
        # no doc-role anywhere: the old ordering holds (more mentions first)
        self.assertLess(ln.index("tool:shell"), ln.index("tool:read"), ln)


if __name__ == "__main__":
    unittest.main()
