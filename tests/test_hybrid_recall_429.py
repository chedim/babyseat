"""#429: hybrid recall -- exact words next to the embeddings.

Embeddings blur exact tokens: an identifier, an env var, a license name,
an issue number or a file name sits near everything that sounds like it.
A small BM25 index over the field's own sentences finds the rows that
carry the very word, and recall fuses that list with the embedding lists
by reciprocal rank. A question that names an entity also reads that
entity's own sentences. The #434/#435 rules hold: only replies, notes,
docs and code answer directly (tool outputs on question-like asks); a
person's lines, receipts and the model's reasoning never do."""

import sys
import time
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
SECRET_NOTE = ("For the docker deployment the signing key is read at startup from the "
               "environment variable MEMBRANE_JWT_SECRET and the proxy refuses to start "
               "without it on a public interface.")
# shares many words with the ask, never the identifier
DECOY = "which variable does the seat read for the secret of the proxy, the doc says."
ASK = "which variable holds the MEMBRANE_JWT_SECRET?"


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


def _texts(rows):
    return " ".join(" ".join(r["answer"]) for r in rows)


class TestApprovedValues(unittest.TestCase):
    """Proposed in the PR, awaiting the operator's approval."""

    def test_bm25_and_fusion_constants(self):
        self.assertEqual((LX.K1, LX.B), (1.2, 0.75))           # Lucene's defaults
        self.assertEqual(EB.RRF_K, 60)                          # Cormack et al. 2009
        self.assertEqual(LX.DF_MAX_FRAC, 0.01)
        self.assertEqual(LX.MAX_TOKEN, 64)

    def test_stopwords_are_lucenes_english_set(self):
        self.assertEqual(len(LX.STOPWORDS), 33)
        self.assertIn("the", LX.STOPWORDS)
        self.assertNotIn("which", LX.STOPWORDS)


class TestTokens(unittest.TestCase):
    def test_identifier_counts_whole_and_by_parts(self):
        t = LX.tokens("set MEMBRANE_JWT_SECRET now")
        for w in ("membrane_jwt_secret", "membrane", "jwt", "secret"):
            self.assertIn(w, t)

    def test_file_name_issue_number_and_plurals(self):
        t = LX.tokens("stored at ~/.membrane/seat-token, see #277; Blurp stands for it")
        for w in ("seat-token", "277", "blurp", "stand"):
            self.assertIn(w, t)
        self.assertNotIn("for", t)                               # a stopword

    def test_a_blob_is_not_a_token(self):
        self.assertEqual(LX.tokens("x" * 65), [])


class TestExactWords(unittest.TestCase):
    def _secret_field(self):
        f = _field()
        f.learn(SECRET_NOTE)
        f.learn(DECOY)
        f.process()
        return f

    def test_the_embedding_alone_misses_the_identifier(self):
        f = self._secret_field()                # the premise: the floor drops it
        self.assertNotIn("MEMBRANE_JWT_SECRET", _texts(EB.direct_answers(f, ASK, k=3)))

    def test_an_exact_identifier_is_recalled_past_the_floor(self):
        f = self._secret_field()
        rows = EB.recall_answers(f, ASK, k=3)                  # the injection's floor
        self.assertIn("MEMBRANE_JWT_SECRET", _texts(rows))
        row = next(r for r in rows if "MEMBRANE_JWT_SECRET" in " ".join(r["answer"]))
        self.assertEqual((row["via"], row["role"]), ("direct", "note"))
        self.assertIn("words", row["how"])

    def test_an_issue_number_and_a_file_name(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="model", ts=T0, text=(
            "Merged: the seat now names itself Blurp, display-only, as decided in #277. "
            "Off-box writes need the credential kept in ~/.membrane/seat-token with mode 0600."))],
            session="s:1:0")
        f.process()
        self.assertIn("#277", _texts(EB.recall_answers(f, "what did #277 decide?", k=3)))
        self.assertIn("seat-token", _texts(EB.recall_answers(f, "where is seat-token kept?", k=3)))

    def test_a_bare_token_ask_is_answered_by_its_words(self):
        f = self._secret_field()                # too short for a sentence vector
        for q in ("MEMBRANE_JWT_SECRET", "#277"):
            self.assertIsNone(__import__("membrane.efgate", fromlist=["x"]).message_vec(f, q))
        self.assertIn("MEMBRANE_JWT_SECRET", _texts(EB.recall_answers(f, "MEMBRANE_JWT_SECRET", k=3)))
        self.assertIn("MEMBRANE_JWT_SECRET", EB.render_for(f, "MEMBRANE_JWT_SECRET") or "")

    def test_never_a_person_a_receipt_or_reasoning(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0,
                                 text="please set MEMBRANE_JWT_SECRET on the box today.")],
                       session="s:1:0")
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 1,
                                 text="[membrane] job #2 finished: MEMBRANE_JWT_SECRET exported.")],
                       session="s:1:1")
        f.write_events([ST.Event(stream="discourse", role="reasoning", ts=T0 + 2,
                                 text="Maybe MEMBRANE_JWT_SECRET is unset in the shell.")],
                       session="s:1:2")
        f.process()
        direct = [r for r in EB.recall_answers(f, ASK, k=8, sim_min=0) if r.get("via") == "direct"]
        self.assertEqual(direct, [])

    def test_ordinary_words_alone_are_the_embeddings_job(self):
        f = _field()
        f.learn("Using the learn tool, a later reply mentioned the phone and its remote process "
                "among a dozen other unrelated things about the garden and the weather today.")
        f.process()
        rows = EB.recall_answers(f, "how was the remote process on the phone killed?", k=8, sim_min=0)
        self.assertFalse([r for r in rows if "words" in r.get("how", [])])

    def test_a_common_word_alone_is_no_exact_evidence(self):
        f = _field()
        for i in range(120):                     # 'seat' in every row: df far above the cap
            f.learn(f"the seat ran job number {i} on a quiet afternoon without trouble.")
        f.process()
        self.assertEqual(LX.index_of(f).search("seat", 10), [])


class TestIncremental(unittest.TestCase):
    def test_a_note_learned_after_the_build_is_found_without_a_rebuild(self):
        f = _field()
        f.learn("bananas ripen faster beside apples in a paper bag")
        f.process()
        ix = LX.index_of(f)
        n0 = len(ix)
        f.learn(SECRET_NOTE)
        f.process()
        self.assertIs(LX.index_of(f), ix)                       # the same index, grown
        self.assertGreater(len(ix), n0)
        self.assertIn("MEMBRANE_JWT_SECRET", _texts(EB.recall_answers(f, ASK, k=3)))

    def test_a_forgotten_event_is_not_recalled(self):
        f = _field()
        f.learn(SECRET_NOTE)
        f.process()
        self.assertIn("MEMBRANE_JWT_SECRET", _texts(EB.recall_answers(f, ASK, k=3)))
        n = next(d["n"] for _, d in f.b.scan("events", {"role": "note"}))
        f.forget(n)
        self.assertNotIn("MEMBRANE_JWT_SECRET", _texts(EB.recall_answers(f, ASK, k=3, sim_min=0)))

    def test_while_the_boot_build_runs_recall_still_answers(self):
        f = _field()
        f.learn("the project license is the PolyForm Noncommercial license now")
        f.process()
        gate = __import__("threading").Event()
        real = LX.build
        LX.build = lambda fld: (gate.wait(5), real(fld))[1]
        try:
            LX.warm(f)
            self.assertIsNone(LX.index_of(f))                   # building: skip the lane
            rows = EB.recall_answers(f, "which license does the project use?", k=3, sim_min=0)
            self.assertIn("PolyForm", _texts(rows))             # the embedding lanes still answer
        finally:
            gate.set()
            LX.build = real
        for _ in range(100):
            if LX.index_of(f) is not None:
                break
            time.sleep(0.02)
        self.assertTrue(LX.index_of(f).ready)


class TestNamedEntity(unittest.TestCase):
    def test_a_named_file_reads_its_own_code(self):
        f = _field()
        f.write_events([ST.Event(stream="code", role="code", ts=T0, src="codegraph",
                                 code={"file": "membrane/self_loop.py", "full": "self_loop.INFO_FLOOR",
                                       "qual": "INFO_FLOOR"},
                                 text="INFO_FLOOR = 0.1  # the floor on a look's binary entropy")],
                       session="ingest")
        f.process()
        rows = EB.recall_answers(f, "in self_loop.py what floor does a look's entropy get?", k=3)
        self.assertIn("INFO_FLOOR = 0.1", _texts(rows))

    def test_a_named_word_entity_ranks_its_own_sentences(self):
        f = _field()
        for i in range(30):
            f.learn(f"Blurp grew a little on cycle {i} while the seat was quiet.")
        f.learn("The name Blurp stands for a baby slurp: the self that grows on what membrane slurps up.")
        f.process()
        rows = EB.recall_answers(f, "What does Blurp stand for?", k=3)
        self.assertIn("baby slurp", _texts(rows))
        row = next(r for r in rows if "baby slurp" in " ".join(r["answer"]))
        self.assertIn("entity", row["how"])


class TestEntityPool(unittest.TestCase):
    """The entity lane as in the sister fork's EntityPool: the ask's keys come
    from capture's own extractor (every shared class), the pool is the
    entity -> events index over answering rows only, the newest 24."""

    def test_pool_cap_value(self):
        self.assertEqual(EB.ENTITY_POOL_MAX, 24)

    def test_every_shared_class_is_named_on_the_query_side(self):
        f = _field()
        asks = {"f": "what does membrane/lexical.py do?",
                "s": "why does EntityField.deltas_of rebuild?",
                "m": "is qwen3:14b slow on the box?",
                "u": "what did https://example.com/api/v1 return?",
                "c": "what did pytest -q report last time?",
                "ha": "what is sensor.boat_battery reading?"}
        for kind, ask in asks.items():
            kinds = {eid.split(":", 1)[0] for eid, _ in EB.named_entities(f, ask)}
            self.assertIn(kind, kinds, ask)

    def _many(self, f, n, text="membrane/lexical.py keeps the word index, cycle {i}."):
        for i in range(n):
            f.write_events([ST.Event(stream="discourse", role="note", ts=T0 + i,
                                     text=text.format(i=i))], session=f"s:{i}")

    def test_the_pool_is_the_newest_24_answering_events(self):
        f = _field()
        self._many(f, 40)
        f.process()
        pool = EB.entity_pool(f, "what does membrane/lexical.py do?")
        self.assertEqual(len(pool), 24)
        ns = sorted(f.b.get("events", k)["n"] for k in pool)
        self.assertEqual(ns, list(range(17, 41)))           # the newest 24 of 40

    def test_rows_that_never_answer_do_not_take_pool_slots(self):
        f = _field()
        self._many(f, 3)                                    # notes #1-#3, the oldest
        for i in range(30):                                 # a person's lines, newer
            f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 100 + i,
                                     text=f"open membrane/lexical.py again please, try {i}.")],
                           session=f"u:{i}")
        f.process()
        pool = EB.entity_pool(f, "what does membrane/lexical.py do?")
        self.assertEqual(sorted(f.b.get("events", k)["n"] for k in pool), [1, 2, 3])

    def test_a_pool_row_needs_no_other_rare_word(self):
        f = _field()
        self._many(f, 1, "membrane/lexical.py keeps a BM25 index over the field's sentences.")
        f.process()
        rows = EB.entity_answers(f, "what about membrane/lexical.py?", k=3)
        self.assertIn("BM25 index", _texts(rows))
        self.assertEqual(rows[0]["how"], ["entity"])

    def test_a_command_named_in_prose_reads_its_rows(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="model", ts=T0, text=(
            "I ran `pytest -q` and it reported 812 passed, 3 skipped."))], session="s:1:0")
        f.process()
        rows = EB.entity_answers(f, "what did pytest -q report last time?", k=3)
        self.assertIn("812 passed", _texts(rows))


class TestFusion(unittest.TestCase):
    def test_reciprocal_rank_fusion(self):
        a = [{"key": "x"}, {"key": "y"}]
        b = [{"key": "y"}, {"key": "z"}]
        fused = EB.rrf([a, b], key=lambda r: r["key"])
        self.assertEqual([r["key"] for r, _ in fused][0], "y")  # in both lists
        self.assertAlmostEqual(dict((r["key"], s) for r, s in fused)["y"],
                               1 / (EB.RRF_K + 2) + 1 / (EB.RRF_K + 1))

    def test_the_embedding_rows_keep_their_similarity_order(self):
        # #435's approved merge: earlier-ask and direct rows by raw
        # similarity, never interleaved list by list
        f = _field()
        for i, w in enumerate(("apples", "pears", "plums")):
            f.write_events([
                ST.Event(stream="discourse", role="user", ts=T0 + 10 * i,
                         text=f"which license does the project use for {w} now?"),
                ST.Event(stream="discourse", role="model", ts=T0 + 10 * i + 1,
                         text=f"The {w} answer is unrelated to anything else here at all.")],
                session=f"s:{i}:0")
        f.learn("the project license is the PolyForm Noncommercial license now")
        f.learn("which license does the project use: the PolyForm Noncommercial one")
        f.process()
        rows = EB.recall_answers(f, "which license does the project use?", k=8, sim_min=0)
        emb = [r for r in rows if "words" not in r["how"] and "entity" not in r["how"]]
        self.assertEqual([r["sim"] for r in emb], sorted((r["sim"] for r in emb), reverse=True))

    def test_one_row_per_event_and_k(self):
        f = _field()
        f.learn(SECRET_NOTE)
        f.learn("MEMBRANE_JWT_SECRET is also read by the docker entrypoint script.")
        f.process()
        rows = EB.recall_answers(f, ASK, k=8, sim_min=0)
        events = [n for r in rows for n in r["a_n"]]
        self.assertEqual(len(events), len(set(events)))
        self.assertEqual(len(EB.recall_answers(f, ASK, k=1, sim_min=0)), 1)


class TestAskEndpoint(unittest.TestCase):
    def test_membrane_ask_carries_how_a_hit_was_found(self):
        from membrane import opencode as oc
        f = _field()
        f.learn(SECRET_NOTE)
        f.process()

        class _Srv:
            efield = f
            fid = "x"
        h = object.__new__(oc.MembraneOpenAIHandler)
        h.server = _Srv()
        h.path = "/membrane/ask?q=which+variable+holds+the+MEMBRANE_JWT_SECRET%3F&k=3"
        got = {}
        h._json_out = lambda d: got.update(d)
        h._membrane_ask()
        hit = next(x for x in got["hits"] if "MEMBRANE_JWT_SECRET" in x["action"])
        self.assertIn("words", hit["how"])


if __name__ == "__main__":
    unittest.main()
