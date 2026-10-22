"""#429 PR 3: tool outputs in direct recall.

Many facts sit only in what a tool returned: a git log, an issue view, a
file read. Each tool result is cut into overlapping chunks at capture (and
older results by an idle backlog step), each chunk embedded, so a
fact-sized piece of a long output can match a question. Tool chunks join
direct recall only on question-like asks, and at most TOOL_HITS_MAX of
them enter the top k. A model reply whose preceding user turn was not a
person's (a receipt, a notification, a restart notice) is no direct
answer."""

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
FILLER = " ".join(f"line {i} of the build log compiled module part{i} cleanly." for i in range(60))
FACT = ("LICENSE: the project license is the PolyForm Noncommercial License 1.0.0; "
        "the project uses this license for its code and its docs")


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


def _read(f, out, ts=T0, cid="c1", session="s:1:0"):
    f.write_events(ST.split_turn("", "", results=[(cid, "read", {"path": "Dockerfile"}, out)], ts=ts),
                   session=session)


def _tool_rows(rows):
    return [r for r in rows if r.get("role") == "tool"]


class TestApprovedValues(unittest.TestCase):
    def test_chunk_values(self):
        self.assertEqual(ST.TOOL_CHUNK_CHARS, 600)
        self.assertEqual(ST.TOOL_CHUNK_OVERLAP, 120)

    def test_the_restart_notice_is_a_notification(self):
        from membrane import restarts
        self.assertEqual(ST.NOTICE_PREFIXES, {restarts.SIGN: "notice"})
        self.assertEqual(ST.author_of(restarts.SIGN + " the seat restarted"), "notice")

    def test_tool_hits_cap(self):
        self.assertEqual(EB.TOOL_HITS_MAX, 4)


class TestChunks(unittest.TestCase):
    def test_a_long_output_splits_into_overlapping_chunks(self):
        cs = ST.tool_chunks(FILLER)
        self.assertGreater(len(cs), 3)
        self.assertTrue(all(len(c) <= ST.TOOL_CHUNK_CHARS for c in cs))
        for a, b in zip(cs, cs[1:]):            # each chunk's tail opens the next
            self.assertIn(a[-20:].split()[-1], b[:ST.TOOL_CHUNK_OVERLAP + 40])
        self.assertIn("part59", cs[-1])

    def test_a_short_output_is_one_chunk_and_noise_none(self):
        self.assertEqual(ST.tool_chunks("the project license is PolyForm Noncommercial"),
                         ["the project license is PolyForm Noncommercial"])
        self.assertEqual(ST.tool_chunks("ok"), [])
        self.assertEqual(ST.tool_chunks(""), [])

    def test_capture_chunks_the_result(self):
        f = _field()
        _read(f, FILLER + " " + FACT + " " + FILLER)
        f.process()
        ev = next(d for _, d in f.b.scan("events", {"stream": "observation"}))
        self.assertGreater(len(ev["chunks"]), 3)
        rows = f.b.get_multi("chunks", ev["chunks"])
        self.assertTrue(any("PolyForm" in r["text"] for r in rows.values()))

    def test_a_call_without_result_has_no_chunks(self):
        f = _field()
        f.write_events(ST.split_turn("", "", calls={0: {"id": "c9", "function": {
            "name": "read", "arguments": "{\"path\": \"x\"}"}}}, ts=T0), session="s:1:0")
        f.process()
        self.assertEqual(f.b.count("chunks")["n"], 0)


class TestToolRecall(unittest.TestCase):
    def test_a_fact_in_a_long_tool_output_answers_a_question(self):
        f = _field()
        _read(f, FILLER + " " + FACT + " " + FILLER)
        f.process()
        rows = _tool_rows(EB.recall_answers(f, Q, k=8, sim_min=0))
        self.assertTrue(rows)
        top = rows[0]
        self.assertEqual(top["via"], "direct")
        self.assertIn("PolyForm", " ".join(top["answer"]))
        self.assertLessEqual(len(" ".join(top["answer"])), ST.TOOL_CHUNK_CHARS)  # a piece, not the log

    def test_not_on_an_ask_that_is_no_question(self):
        f = _field()
        _read(f, FILLER + " " + FACT)
        f.process()
        rows = EB.recall_answers(f, "tell me the project license label", k=8, sim_min=0)
        self.assertEqual(_tool_rows(rows), [])

    def test_at_most_the_cap_enter_the_top_k(self):
        f = _field()
        for i in range(EB.TOOL_HITS_MAX + 3):
            _read(f, f"build {i}: the project license label reads PolyForm Noncommercial {i}",
                  ts=T0 + i, cid=f"c{i}")
        f.process()
        rows = EB.recall_answers(f, Q, k=8, sim_min=0)
        self.assertEqual(len(_tool_rows(rows)), EB.TOOL_HITS_MAX)

    def test_one_row_per_tool_result(self):
        f = _field()
        _read(f, FACT + " " + FILLER + " " + FACT.replace("1.0.0", "1.0.1").replace("docs", "notes"))
        f.process()
        rows = _tool_rows(EB.recall_answers(f, Q, k=8, sim_min=0))
        self.assertEqual(len(rows), 1)

    def test_a_receipt_is_still_never_an_answer(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0,
                                 text="[membrane] job #2 finished: the project license label is MIT.")],
                       session="s:1:0")
        f.process()
        self.assertNotIn("MIT", " ".join(" ".join(r["answer"]) for r in
                                         EB.recall_answers(f, Q, k=8, sim_min=0)))

    def test_older_results_are_chunked_by_the_backlog(self):
        f = _field()
        _read(f, FILLER + " " + FACT)
        f.process()
        for k, d in list(f.b.scan("events", {"stream": "observation"})):
            d.pop("chunks", None)                     # a result captured before chunking
            f.b.upsert("events", k, d)
        for k, _ in list(f.b.scan("chunks")):
            f.b.delete("chunks", k)
        self.assertEqual(_tool_rows(EB.recall_answers(f, Q, k=8, sim_min=0)), [])
        self.assertEqual(f.chunk_backlog(), 1)
        self.assertEqual(f.chunk_backlog(), 0)         # idempotent
        self.assertTrue(_tool_rows(EB.recall_answers(f, Q, k=8, sim_min=0)))

    def test_forget_drops_the_chunks(self):
        f = _field()
        _read(f, FILLER + " " + FACT)
        f.process()
        n = next(d for _, d in f.b.scan("events", {"stream": "observation"}))["n"]
        f.forget(n)
        self.assertEqual(f.b.count("chunks")["n"], 0)
        self.assertEqual(_tool_rows(EB.recall_answers(f, Q, k=8, sim_min=0)), [])
        self.assertEqual(f.chunk_backlog(), 0)          # a forgotten result is never re-cut

    def test_the_backlog_is_an_idle_step_of_the_capturer(self):
        from membrane.capture import Capturer
        f = _field()
        c = Capturer(f, mode="on", start=False)
        self.assertIn(f.chunk_backlog, c.idle_hooks)

    def test_entity_evidence_still_demotes_observations_off_question(self):
        self.assertEqual(ST.OBSERVATION_PENALTY, 0.10)  # #434's rule, unchanged


class TestWarmVectors(unittest.TestCase):
    def test_warming_a_big_collection_answers_the_first_read(self):
        import time
        import numpy as np
        from membrane.efstore import pack_f16
        b = MemBackend()
        rng = np.random.default_rng(7)
        for i in range(2100):                         # past the inline-build size gate
            v = rng.standard_normal(8)
            b.upsert("chunks", f"k:{i}", {"vec": pack_f16(v / np.linalg.norm(v)), "text": str(i)})
        b.warm_vectors("chunks", "vec")
        deadline = time.time() + 10
        while time.time() < deadline and not b.vector_top_k("chunks", "vec", np.ones(8), 3):
            time.sleep(0.05)
        self.assertEqual(len(b.vector_top_k("chunks", "vec", np.ones(8), 3)), 3)


from membrane import cblite as _CBL  # noqa: E402


@unittest.skipUnless(_CBL.lib_paths() is not None, "Couchbase Lite libraries not fetched")
class TestOlderStoreReadonly(unittest.TestCase):
    def test_a_readonly_open_of_a_store_without_chunks_reads_it(self):
        import tempfile
        from unittest import mock
        from membrane import cblstore
        p = Path(tempfile.mkdtemp()) / "field.cblite"
        older = tuple(c for c in cblstore.COLLECTIONS if c != "chunks")
        with mock.patch.object(cblstore, "COLLECTIONS", older):
            w = cblstore.CBLiteBackend(p)                 # a store from before #429 PR 3
            w.upsert("entity_states", "e1", {"origin": "o", "mass": 1.0})
            w.flush()
            w.close()
        ro = cblstore.CBLiteBackend(p, readonly=True)
        try:
            self.assertEqual(ro.get("entity_states", "e1")["mass"], 1.0)
            self.assertEqual(list(ro.scan("chunks")), [])
            self.assertNotIn("chunks", ro.db.colls)       # the snapshot created nothing
        finally:
            ro.close()


class TestRepliesToMachines(unittest.TestCase):
    def _reply_after(self, user_text):
        f = _field()
        f.write_events([
            ST.Event(stream="discourse", role="user", ts=T0, text=user_text),
            ST.Event(stream="discourse", role="model", ts=T0 + 1,
                     text="Noted. By the way, the project license is PolyForm Noncommercial.")],
            session="s:1:0")
        f.process()
        return [r for r in EB.recall_answers(f, Q, k=8, sim_min=0)
                if "PolyForm" in " ".join(r["answer"])]

    def test_a_reply_to_a_receipt_is_no_answer(self):
        self.assertEqual(self._reply_after("[membrane] job #4 finished (exec, 2 s): rc=0"), [])

    def test_a_reply_to_a_restart_notice_is_no_answer(self):
        self.assertEqual(self._reply_after("[membrane seat notice] the seat restarted after 40 s"), [])

    def test_a_reply_to_a_task_notification_still_answers(self):
        # the client's background task finished; the reply reports its
        # result to the person (measured: a pinned answer of this shape)
        rows = self._reply_after("<task-notification> <task-id>b1</task-id> completed")
        self.assertTrue(rows)

    def test_a_reply_to_a_person_still_answers(self):
        rows = self._reply_after("tidy the readme for me please")
        self.assertTrue(rows)


class TestAskEndpoint(unittest.TestCase):
    def test_membrane_ask_returns_a_tool_hit(self):
        from membrane import opencode as oc
        f = _field()
        _read(f, FILLER + " " + FACT)
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
        hit = next(x for x in got["hits"] if "PolyForm" in x["action"])
        self.assertEqual(hit["role"], "tool")
        self.assertIn("tool read:", EB.render_past(EB.recall_answers(f, Q, k=8, sim_min=0), now=T0))
        self.assertTrue(hit["situation"].startswith("tool read #"))


if __name__ == "__main__":
    unittest.main()
