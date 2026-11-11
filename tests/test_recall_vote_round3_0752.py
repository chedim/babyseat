"""0.7.52 vote, the re-review's round 3: no embedder inside an apply, a move log that never double-counts, moments per
clause (the bench's facts.write), and ask-time costs that read postings only."""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

try:
    import spacy  # noqa: F401
    spacy.load("en_core_web_sm")
    HAVE_SPACY = True
except Exception:
    HAVE_SPACY = False
try:
    import dateparser  # noqa: F401
    HAVE_DATEPARSER = True
except Exception:
    HAVE_DATEPARSER = False

TALK = [("user", "I started a pottery class this spring."),
        ("model", "Pottery sounds relaxing, what do you make?"),
        ("user", "Mostly bowls with a green glaze for my pottery shelf."),
        ("model", "A green glaze on bowls sounds lovely."),
        ("user", "The glaze cracked once in the kiln."),
        ("user", "My cousin's wedding is in June.")]


def _field(be=None):
    return EF.EntityField(be or MemBackend(), FakeEmbedder(), "seat@host")


def _say(f, session, lines, t0=1_700_000_000.0):
    evs = [ST.Event(stream="discourse", role=r, text=t, ts=t0 + i * 60) for i, (r, t) in enumerate(lines)]
    keys = f.write_events(evs, session=session)
    f.process()
    return keys


def _same(test, a, b, places=5):
    test.assertEqual(set(a), set(b))
    for k in a:
        test.assertEqual(set(a[k]), set(b[k]), k)
        for e in a[k]:
            test.assertAlmostEqual(a[k][e], b[k][e], places=places, msg=(k, e))


# ---- A: never the embedder inside an apply ------------------------------------------------------
class NoEmbedInApply(unittest.TestCase):
    def test_a_word_with_no_stored_seed_is_seeded_in_the_background(self):
        f = _field()
        evs = [ST.Event(stream="discourse", role="user", text="I saw a zebra at the zoo.", ts=1_700_000_000.0)]
        k = f.write_events(evs, session="c")[0]
        f.b.delete("terms", f._term_key("zebra"))                 # its seed is not stored yet
        inside = []
        real_apply, real_embed = f._apply, f.emb.embed

        def apply(*a, **kw):
            inside.append(1)
            try:
                return real_apply(*a, **kw)
            finally:
                inside.pop()

        def embed(texts):
            # the move log never embeds inside an apply (the field's own materialization of a SPECIFIC thing -- a
            # speaker -- is the field's, not the move log's)
            assert not (inside and "zebra" in texts), f"the move log embedded inside an apply: {texts}"
            return real_embed(texts)
        f._apply, f.emb.embed = apply, embed
        f.process()
        f.drain_moves()
        g = _field()
        with mock.patch.object(EF, "K_MAT", 1):
            g = _field()
            kg = _say(g, "c", [("user", "I saw a zebra at the zoo.")])[0]
        self.assertAlmostEqual(f.move_log.moves[k]["w:zebra"], g.move_log.moves[kg]["w:zebra"], places=5)


# ---- B: no double counting ------------------------------------------------------------------------
class Idempotent(unittest.TestCase):
    def test_a_note_twice_is_one_note(self):
        from membrane.movelog import MoveLog
        ml = MoveLog()
        ml.place("k", "c", 1, True)
        ml.note("k", "w:x", 0.5, 0)
        ml.note("k", "w:x", 0.5, 0)
        ml.note("k", "w:x", 0.25, 1)
        self.assertAlmostEqual(ml.msum["k"]["w:x"], 0.75)
        self.assertAlmostEqual(ml.moves["k"]["w:x"], 0.5)

    def _talked(self):
        f = _field()
        keys = _say(f, "c", TALK)
        return f, keys, {k: dict(v) for k, v in f.move_log.msum.items()}

    def test_rebuild_does_not_double(self):
        f, _keys, want = self._talked()
        f.rebuild()
        _same(self, f.move_log.msum, want)

    def test_reembed_does_not_double(self):
        f, _keys, want = self._talked()
        f.reembed(FakeEmbedder())
        _same(self, f.move_log.msum, want)

    def test_recompute_does_not_double_and_keeps_the_flag(self):
        f, _keys, want = self._talked()
        st = f.recompute("w:pottery")
        _same(self, f.move_log.msum, want)
        self.assertTrue(all(len(p) > 7 and p[7] for p in (st.pending or [])))

    def test_forget_many_over_the_rebuild_bar(self):
        f, keys, want = self._talked()
        n0 = int(f.b.get("events", keys[-1])["n"])
        f.forget_many([n0], rebuild_over=0)
        want.pop(keys[-1], None)
        self.assertNotIn(keys[-1], f.move_log.msum)
        for k in want:
            for e, v in f.move_log.msum.get(k, {}).items():
                self.assertLessEqual(v, want[k].get(e, 0.0) + 1e-5, (k, e))   # never more than once


# ---- C: moments per clause --------------------------------------------------------------------------
@unittest.skipUnless(HAVE_SPACY and HAVE_DATEPARSER, "spaCy or dateparser not installed")
class Moments(unittest.TestCase):
    FEB_2024 = 1_707_000_000.0

    def test_each_verb_clause_keeps_its_own_moment(self):
        from membrane import instances as IN
        doc = IN.nlp(load=True)("Last May I ran a charity race, and now I mostly swim.")
        got = IN.moments_of(doc, self.FEB_2024)
        self.assertIn(self.FEB_2024, got)                              # the clause with no time words: said
        may = IN.own_span("May 2023")
        self.assertTrue(any(IN.time_fits(may, m) for m in got))       # "last May", from when it was said

    def test_no_verb_clause_no_moment(self):
        from membrane import instances as IN
        self.assertEqual(IN.moments_of(IN.nlp(load=True)("Great news!"), self.FEB_2024), [])


# ---- D-G: ask-time cost -----------------------------------------------------------------------------
class AskCost(unittest.TestCase):
    def test_the_asker_comes_from_an_index(self):
        from membrane import recall_banner as RB
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="user", text="Ann: hello there friend",
                                 ts=1_700_000_000.0)], session="c")
        f.process()
        scans = []
        real = f.b.scan
        f.b.scan = lambda coll, *a, **kw: scans.append(coll) or real(coll, *a, **kw)
        RB.asker_of(f, "c")
        self.assertNotIn("events", scans)

    def _store(self, n, who="operator"):
        from membrane import instances as IN
        st = IN.Store(types.SimpleNamespace(b=None, origin="o"))
        for i in range(n):
            st.keep(f"k{i}", {"n": i, "seq": i, "who": who, "chat": "c", "ts": 1_700_000_000.0 + i,
                              "lemmas": ["pottery"] if i in (7, 70, 700) else ["tea"],
                              "moments": [1_700_000_000.0 + i], "places": [], "phr": []}, persist=False)
        return st

    def _touched(self, st, fn):
        class Count(dict):
            n = 0

            def __getitem__(self, k):
                Count.n += 1
                return super().__getitem__(k)

            def get(self, k, d=None):
                Count.n += 1
                return super().get(k, d)
        st.rows = Count(st.rows)
        out = fn()
        return out, Count.n

    def test_a_long_person_timeline_stays_bounded(self):
        from membrane import instances as IN
        small, big = self._store(300), self._store(3000)
        a, na = self._touched(small, lambda: IN.person_ballot(small, "operator", ["pottery"]))
        b, nb = self._touched(big, lambda: IN.person_ballot(big, "operator", ["pottery"]))
        self.assertEqual(a[:3], [7, 70, 0])
        self.assertEqual(b[:3], [7, 70, 700])
        self.assertLessEqual(nb, na + 10)

    def test_a_wide_time_span_stays_bounded(self):
        from membrane import instances as IN
        small, big = self._store(300), self._store(3000)
        span = (1_600_000_000.0, 1_800_000_000.0, "year")
        a, na = self._touched(small, lambda: IN.span_ballot(small, span, ["pottery"]))
        b, nb = self._touched(big, lambda: IN.span_ballot(big, span, ["pottery"]))
        self.assertEqual(len(a), 20)
        self.assertEqual(b[:3], [7, 70, 700])
        self.assertLessEqual(nb, na + 10)

    def test_places_are_read_from_their_postings(self):
        from membrane import instances as IN
        st = IN.Store(types.SimpleNamespace(b=None, origin="o"))
        for i in range(500):
            st.keep(f"k{i}", {"n": i, "seq": i, "who": "", "chat": "c", "lemmas": [], "phr": [],
                              "places": [f"town{i}"] + (["paris"] if i == 42 else [])}, persist=False)
        reads = []
        f = types.SimpleNamespace(b=types.SimpleNamespace(get=lambda c, k: reads.append(k)), _term_key=lambda w: w)
        calls = []
        real = IN.lemmas
        with mock.patch.object(IN, "lemmas", side_effect=lambda w: calls.append(w) or real(w)):
            got = IN.place_ballot(f, st, ["paris"], [])
        self.assertEqual(got, [42])
        self.assertLessEqual(len(reads), 1)                            # the question place's own seed, at most
        self.assertLessEqual(len(calls), 2)

    def test_the_background_parse_takes_the_lock_one_text_at_a_time(self):
        from membrane import instances as IN
        entered = []

        class Lock:
            def __enter__(self):
                entered.append(1)

            def __exit__(self, *a):
                return False

        real = IN.nlp(load=True)

        class M:
            def __call__(self, text):
                return real(text) if real is not None else types.SimpleNamespace(ents=[], sents=[])

            def pipe(self, texts, **kw):
                raise AssertionError("a whole batch under one lock")
        st = IN.Store(types.SimpleNamespace(b=types.SimpleNamespace(upsert=lambda *a: None), origin="o"))
        with mock.patch.object(IN, "NLP_LOCK", Lock()), mock.patch.object(IN, "nlp", return_value=M()), \
                mock.patch.object(IN, "read", return_value={"phr": [], "lemmas": [], "places": []}), \
                mock.patch.object(IN, "moments_of", return_value=[]):
            st.parse([(f"k{i}", {"text": "One text.", "stream": "discourse"}) for i in range(3)])
        self.assertEqual(len(entered), 3)


# ---- H, I ------------------------------------------------------------------------------------------
class Warmth(unittest.TestCase):
    @unittest.skipUnless(HAVE_DATEPARSER, "dateparser not installed")
    def test_warm_imports_dateparser(self):
        from membrane import recall_vote as RV
        f = _field()
        sys.modules.pop("dateparser", None)
        with mock.patch("membrane.instances.attach"):
            RV.warm(f).join(30)
        self.assertIn("dateparser", sys.modules)

    def test_every_spacy_caller_shares_one_lock(self):
        from membrane import instances as IN
        from membrane import language as LG
        self.assertIs(IN.NLP_LOCK, LG.NLP_LOCK)
        entered = []

        class Lock:
            def __enter__(self):
                entered.append(1)

            def __exit__(self, *a):
                return False
        with mock.patch.object(LG, "NLP_LOCK", Lock()), \
                mock.patch.object(LG, "_nlp", return_value=lambda t: types.SimpleNamespace(text=t)):
            LG._doc("hello there")
        self.assertEqual(entered, [1])


if __name__ == "__main__":
    unittest.main()
