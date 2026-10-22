"""#429 PR 2: direct answer recall.

The ask path finds a person's earlier asks like this one and returns what
followed them. Most facts sit elsewhere: in the model's replies to other
asks, in learn notes, in ingested docs and in the code. Recall also
searches those directly by similarity to the ask and merges them with the
ask path. A person's lines, receipts, notifications, sensor readings and
observations are never a direct answer."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0
Q = "which license does the project use?"


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


def _texts(rows):
    return " ".join(" ".join(r["answer"]) for r in rows)


class TestApprovedValues(unittest.TestCase):
    def test_answer_roles(self):
        self.assertEqual(set(EB.DIRECT_ROLES), {"model", "note", "doc", "code"})


class TestDirectSources(unittest.TestCase):
    def test_a_learn_note_answers_with_no_earlier_ask(self):
        f = _field()
        f.learn("the project license is the PolyForm Noncommercial license now")
        f.process()
        self.assertEqual(EB.past_answers(f, Q, k=8, sim_min=0), [])   # no earlier ask
        rows = EB.recall_answers(f, Q, k=8, sim_min=0)
        self.assertIn("PolyForm Noncommercial", _texts(rows))
        row = next(r for r in rows if "PolyForm" in " ".join(r["answer"]))
        self.assertEqual(row["via"], "direct")
        self.assertEqual(row["role"], "note")

    def test_a_reply_to_another_ask(self):
        f = _field()
        f.write_events([
            ST.Event(stream="discourse", role="user", ts=T0, text="tidy the readme for me please"),
            ST.Event(stream="discourse", role="model", ts=T0 + 1,
                     text="Done. I also noted that the project license is PolyForm Noncommercial.")],
            session="s:1:0")
        f.process()
        self.assertIn("PolyForm", _texts(EB.recall_answers(f, Q, k=8, sim_min=0)))

    def test_code_and_doc_text(self):
        f = _field()
        f.write_events([
            ST.Event(stream="code", role="code", ts=T0, src="codegraph",
                     text="license_name() in membrane/meta.py: the project license is PolyForm."),
            ST.Event(stream="discourse", role="doc", ts=T0, src="ingest:LICENSE",
                     text="This project license is the PolyForm Noncommercial License 1.0.0 text.")],
            session="ingest")
        f.process()
        rows = EB.recall_answers(f, Q, k=8, sim_min=0)
        self.assertEqual({r["role"] for r in rows if r.get("via") == "direct"}, {"code", "doc"})

    def test_never_a_person_a_receipt_or_an_observation(self):
        f = _field()
        rc = ST.Event(stream="discourse", role="user", ts=T0,
                      text="[membrane] job #2 finished: the project license check ran ok.")
        f.write_events([rc], session="s:1:0")
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 5,
                                 text="i think the project license is the MIT one.")],
                       session="s:1:1")
        f.write_events(ST.split_turn("", "", results=[("c1", "read", {"path": "LICENSE"},
                                                        "which license does the project use: text")],
                                     ts=T0 + 9), session="s:1:2")
        f.process()
        direct = [r for r in EB.recall_answers(f, Q, k=8, sim_min=0) if r.get("via") == "direct"]
        self.assertEqual(direct, [])

    def test_the_floor_holds(self):
        f = _field()
        f.learn("the project license is PolyForm Noncommercial")
        f.learn("bananas ripen faster beside apples in a paper bag")
        f.process()
        rows = EB.recall_answers(f, Q, k=8, sim_min=0.5)
        self.assertNotIn("bananas", _texts(rows))
        self.assertTrue(all(r["sim"] >= 0.5 for r in rows))

    def test_a_reply_restating_the_ask_is_not_an_answer(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="model", ts=T0,
                                 text="Which license does the project use?")], session="s:1:0")
        f.process()
        self.assertEqual([r for r in EB.recall_answers(f, Q, k=8, sim_min=0)
                          if r.get("via") == "direct"], [])


class TestMerge(unittest.TestCase):
    def _both(self):
        f = _field()
        f.write_events([
            ST.Event(stream="discourse", role="user", ts=T0, text="which license does the project use now?"),
            ST.Event(stream="discourse", role="model", ts=T0 + 1,
                     text="The project moved to the PolyForm Noncommercial license last week.")],
            session="s:1:0")
        f.learn("the project license file sits at the repository root as LICENSE")
        f.process()
        return f

    def test_ask_path_and_direct_rows_merge_once(self):
        f = self._both()
        rows = EB.recall_answers(f, Q, k=8, sim_min=0)
        vias = [r.get("via", "ask") for r in rows]
        self.assertIn("ask", vias)
        self.assertIn("direct", vias)
        events = [n for r in rows for n in r["a_n"]]
        self.assertEqual(len(events), len(set(events)))       # the reply once, not twice
        self.assertLessEqual(len(rows), 8)

    def test_k_caps_the_merge(self):
        f = self._both()
        self.assertEqual(len(EB.recall_answers(f, Q, k=1, sim_min=0)), 1)

    def test_rendered_block_carries_direct_rows(self):
        f = _field()
        f.learn("the project license is the PolyForm Noncommercial license now")
        f.process()
        block = EB.render_past(EB.recall_answers(f, Q, k=8, sim_min=0), now=T0)
        self.assertTrue(block.startswith(EB.PAST_HEADER))
        self.assertIn("note:", block)
        self.assertIn("PolyForm", block)

    def test_injection_block_uses_direct_recall(self):
        f = _field()
        f.learn("which license does the project use now that it moved: PolyForm Noncommercial")
        f.process()
        q = "which license does the project use now that it moved?"
        rows = EB.recall_answers(f, q)                       # the injection's floor
        self.assertTrue(rows)
        self.assertIn("PolyForm", EB.render_for(f, q) or "")


class TestAskEndpoint(unittest.TestCase):
    def test_membrane_ask_returns_direct_hits(self):
        from membrane import opencode as oc
        f = _field()
        f.learn("the project license is the PolyForm Noncommercial license now")
        f.process()

        class _Srv:
            efield = f
            fid = "x"
        h = object.__new__(oc.MembraneOpenAIHandler)
        h.server = _Srv()
        h.path = "/membrane/ask?q=which+license+does+the+project+use%3F&k=8&sim_min=0"
        got = {}
        h._json_out = lambda d: got.update(d)
        h._membrane_ask()
        self.assertTrue(got["hit"])
        hit = next(x for x in got["hits"] if "PolyForm" in x["action"])
        self.assertEqual(hit["via"], "direct")
        self.assertEqual(hit["role"], "note")
        self.assertTrue(hit["events"])


if __name__ == "__main__":
    unittest.main()
