"""0.7.52 vote, the fidelity review's fixes: each against the bench's own rule (and, where the bench code is on this
machine, against the bench's own functions -- the golden tests skip elsewhere)."""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

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

THINGS_SETTLE = Path("~/projects/membrane/.worktrees/things/membrane/settle.py").expanduser()
BENCH = Path("~/projects/membrane-bench/qgraph/ideas").expanduser()


def _field(be=None):
    return EF.EntityField(be or MemBackend(), FakeEmbedder(), "seat@host")


def _say(f, session, lines, t0=1_700_000_000.0):
    evs = [ST.Event(stream="discourse", role=r, text=t, ts=t0 + i * 60) for i, (r, t) in enumerate(lines)]
    keys = f.write_events(evs, session=session)
    f.process()
    return keys


# ---- the bench's own code, loaded for the golden tests only --------------------------------------
def _bench_settle():
    import membrane
    spec = importlib.util.spec_from_file_location("membrane._bench_settle", THINGS_SETTLE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    _ = membrane
    return m


def _bench_arm(name):
    """A bench arm module; the trained-only modules it imports at load time are stubbed (never called here)."""
    import membrane
    saved = {}
    st = _bench_settle()
    for nm, mod in [("settle", st)] + [(x, types.ModuleType("membrane." + x))
                                       for x in ("structures", "meaning", "facts", "infer", "srl", "voter_weights")]:
        full = "membrane." + nm
        saved[full] = sys.modules.get(full)
        if nm == "settle" or not hasattr(membrane, nm):
            sys.modules[full] = mod
            setattr(membrane, nm, mod)
    spec = importlib.util.spec_from_file_location("bench_" + name[:3], BENCH / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


HAVE_BENCH = THINGS_SETTLE.exists() and (BENCH / "58_voting_eight.py").exists()

# words crossing K_MAT=3 mid-talk ("pottery", "glaze"), and ones said once or twice
TALK = [("user", "I started a pottery class this spring."),
        ("model", "Pottery sounds relaxing, what do you make?"),
        ("user", "Mostly bowls with a green glaze for my pottery shelf."),
        ("model", "A green glaze on bowls sounds lovely."),
        ("user", "The glaze cracked once in the kiln."),
        ("user", "My cousin's wedding is in June."),
        ("model", "Will you make pottery for the wedding?")]

# a fixed move log: (event, chat, [(thing, sentence step), ...]); every event a turn, in capture order
LOG = [("k1", "c1", [("w:pottery", 0.9), ("w:pottery", 0.5), ("w:class", 0.4)]),
       ("k2", "c1", [("w:wedding", 0.7)]),
       ("k3", "c1", [("w:class", 0.6), ("w:pottery", 0.3)]),
       ("k4", "c2", [("w:wedding", 0.55), ("w:weather", 0.8)]),
       ("k5", "c2", [("w:pottery", 0.2)]),
       ("k6", "c2", [("w:weather", 0.95)]),
       ("k7", "c2", [("w:class", 0.15), ("w:tea", 0.33)])]


def _port_field():
    from membrane.movelog import MoveLog
    ml = MoveLog()
    for i, (k, chat, steps) in enumerate(LOG):
        ml.place(k, chat, i + 1, True)
        for e, s in steps:
            ml.note(k, e, s)
    docs = {k: {"origin": "o", "status": "applied", "stream": "discourse", "role": "user", "n": i + 1}
            for i, (k, _c, _s) in enumerate(LOG)}
    b = types.SimpleNamespace(get_multi=lambda coll, keys: {k: docs[k] for k in keys if k in docs})
    return types.SimpleNamespace(move_log=ml, graph_fn=lambda: None, phrases=set(), origin="o", b=b)


def _bench_field():
    moves, wl, eps, ep_n = {}, {}, {}, {}
    for i, (k, chat, steps) in enumerate(LOG):
        eps[k] = (chat, ep_n.get(chat, 0))
        ep_n[chat] = ep_n.get(chat, 0) + 1
        for e, s in steps:
            moves.setdefault(k, {})[e] = moves.get(k, {}).get(e, 0.0) + s
            wl.setdefault(e, []).append((float(i), k, s))
    return types.SimpleNamespace(moves=moves, worldlines=wl, episodes=eps, graph_fn=lambda: None, phrases=set())


@unittest.skipUnless(HAVE_BENCH, "the bench code is not on this machine")
class Golden(unittest.TestCase):
    QS = ("What pottery class did I take?", "Tell me about the wedding.", "How was the weather and the tea?",
          "class pottery wedding weather")

    def test_settle_matches_the_bench(self):
        from membrane import movelog as ML
        BS = _bench_settle()
        for q in self.QS:
            want = [k for k, _s in BS.settle(_bench_field(), q, rounds=1, neighbors=True, move="max")]
            got = [k for k, _s in ML.settle(_port_field(), q, rounds=1)]
            self.assertEqual(got, want, q)

    def test_settle_on_a_k_mat_3_field_matches_the_bench_on_a_k_mat_1_field(self):
        from membrane import movelog as ML
        BS = _bench_settle()
        with mock.patch.object(EF, "K_MAT", 1):
            f1 = _field()
            keys = _say(f1, "c", TALK)
        f3 = _field()
        _say(f3, "c", TALK)
        ml = f1.move_log                      # the bench's field: its moves (sum), worldlines (max), episodes
        bf = types.SimpleNamespace(
            moves={k: dict(ml.msum[k]) for k in keys if k in ml.msum},
            worldlines={}, episodes={k: ("c", i) for i, k in enumerate(keys)},
            graph_fn=f1.graph_fn, phrases=f1.phrases)
        for k in keys:
            for e, v in ml.moves.get(k, {}).items():
                bf.worldlines.setdefault(e, []).append((float(keys.index(k)), k, v))
        for q in ("What did I make in my pottery class?", "Which glaze cracked?", "When is the wedding?"):
            want = [k for k, _s in BS.settle(bf, q, rounds=1, neighbors=True, move="max")]
            got = [k for k, _s in ML.settle(f3, q, rounds=1)]
            self.assertEqual(got, want, q)

    def test_entity_recall_matches_the_bench(self):
        from membrane import recall_vote as RV
        b58 = _bench_arm("58_voting_eight.py")
        hits = [("w:pottery", 0.9), ("w:wedding", 0.5), ("w:class", 0.25)]
        bf = _bench_field()
        bf.recall = lambda v, k=8, hop=False: hits
        ctx = {"srv": {"c": types.SimpleNamespace(efield=bf)}}
        b58.B = types.SimpleNamespace(_key2dia=lambda ctx, conv: {k: k for k, _c, _s in LOG})
        with mock.patch("membrane.efgate.message_vec", return_value=np.ones(4)):
            want = b58._entity(ctx, "c", {"question": "q"})
        pf = _port_field()
        pf.recall = lambda v, k=8, hop=False: hits
        n2k = {i + 1: k for i, (k, _c, _s) in enumerate(LOG)}
        got = [n2k[n] for n in RV.entity_recall(pf, "q", qvec=np.ones(4))]
        self.assertEqual(got, want[:RV.DEPTH])

    @unittest.skipUnless(HAVE_SPACY, "spaCy not installed")
    def test_instances_match_the_bench_build(self):
        from membrane import instances as IN
        b92f = _bench_arm("92f_joined_instances.py")
        talk = [("Ann", "I adopted a dog last week."),
                ("Ben", "Wow, what is the dog called?"),
                ("Ann", "My dog is called Rex and my cousin's beach wedding was lovely."),
                ("Ben", "Did you go to the wedding?"),
                ("Ann", "We had two parties in Boston and the party was loud.")]
        docs = {f"m{i}": types.SimpleNamespace(properties={"speaker": sp, "text": t}) for i, (sp, t) in enumerate(talk)}
        ctx = {"srv": {"c": types.SimpleNamespace(entity_store=types.SimpleNamespace(docs=docs))},
               "msg_of": {"c": {f"d{i}": f"m{i}" for i in range(len(talk))}}}
        inst, _n, _j = b92f.build(ctx, "c", pronouns=False)
        want = sorted((it["owner"], tuple(it["ladder"]), tuple(it["line"])) for it in inst.values())
        st = IN.Store(types.SimpleNamespace(b=None, origin="o"))
        m = IN.nlp(load=True)
        for i, (sp, t) in enumerate(talk):
            row = IN.read(m(t))
            row.update(n=f"d{i}", chat="c", seq=i, who=sp.lower())
            st.keep(f"k{i}", row, persist=False)
        got = sorted((it["owner"], tuple(it["ladder"]), tuple(it["line"])) for it in st.inst.values())
        self.assertEqual(got, want)


# ---- 1, 2: what the move log records -----------------------------------------------------------
class Moves(unittest.TestCase):
    def test_a_word_said_once_moves_as_on_the_bench(self):
        f = _field()                                     # K_MAT 3: the field keeps it in the nursery ...
        k = _say(f, "c", [("user", "I saw a zebra.")])[0]
        self.assertEqual(f.get("w:zebra").tier, "nursery")
        self.assertGreater(f.move_log.moves[k]["w:zebra"], 0.0)   # ... and the move log moves it from its seed

    def test_the_moves_match_a_k_mat_1_field_exactly(self):
        f3 = _field()
        with mock.patch.object(EF, "K_MAT", 1):
            f1 = _field()
            _say(f1, "c", TALK)
        _say(f3, "c", TALK)
        self.assertEqual(f3.get("w:pottery").tier, "live")             # crossed K_MAT=3 mid-talk
        for name in ("moves", "msum"):
            a, b = getattr(f1.move_log, name), getattr(f3.move_log, name)
            self.assertEqual(set(a), set(b))
            for k in a:
                self.assertEqual(set(a[k]), set(b[k]), k)
                for e in a[k]:
                    self.assertAlmostEqual(a[k][e], b[k][e], places=5, msg=(name, k, e))

    def test_the_backfill_matches_too(self):
        from membrane import movelog as ML
        be = MemBackend()
        f = _field(be)
        keys = _say(f, "c", TALK)
        want = {k: dict(f.move_log.moves[k]) for k in keys}
        f.flush()
        for dk, d in list(be.scan("entity_deltas", {"origin": f.origin, "mv": 1})):   # as 0.7.51 wrote it
            be.upsert("entity_deltas", dk, {x: v for x, v in d.items()
                                            if x not in ("mv", "moves", "msum", "chat", "turn")})
        g = _field(be)
        calls = g.emb.calls
        ML.of(g).backfill(g)
        self.assertEqual(g.emb.calls, calls)                           # no embedder: stored vectors and seeds only
        for k in keys:
            self.assertEqual(set(ML.of(g).moves[k]), set(want[k]), k)
            for e, v in want[k].items():
                self.assertAlmostEqual(ML.of(g).moves[k][e], v, places=4, msg=(k, e))

    @mock.patch.object(EF, "K_MAT", 1)
    def test_settle_reads_the_max_entity_recall_the_sum(self):
        from membrane import movelog as ML
        f = _field()
        k = _say(f, "c", [("user", "Pottery is my favourite hobby these days. "
                                   "Every single weekend I really love doing pottery.")])[0]
        mx, sm = f.move_log.moves[k]["w:pottery"], f.move_log.msum[k]["w:pottery"]
        self.assertGreater(sm, mx)
        self.assertAlmostEqual(ML.entity_scores(f, [("w:pottery", 1.0)])[k], sm, places=6)
        self.assertAlmostEqual(f.move_log.held[k]["w:pottery"], mx, places=6)

    def test_materialization_replay_survives_a_restart(self):
        be = MemBackend()
        f = _field(be)
        keys = _say(f, "c", [("user", "Pottery tonight."), ("user", "More pottery."), ("user", "Pottery again.")])
        self.assertIn("w:pottery", f.move_log.moves[keys[0]])        # replayed when it materialized
        f.flush()
        g = _field(be)
        g.move_log.load(g)
        self.assertIn("w:pottery", g.move_log.moves[keys[0]])
        self.assertAlmostEqual(g.move_log.moves[keys[0]]["w:pottery"], f.move_log.moves[keys[0]]["w:pottery"], 4)


# ---- 9: capture order, neighbors are turns ------------------------------------------------------
class Order(unittest.TestCase):
    @mock.patch.object(EF, "K_MAT", 1)
    def test_load_restores_capture_order(self):
        be = MemBackend()
        f = _field(be)
        keys = _say(f, "c", [("user", "pottery one"), ("user", "pottery two"), ("user", "pottery three")])
        f.flush()
        g = _field(be)
        real = be.scan
        with mock.patch.object(be, "scan", side_effect=lambda *a, **kw: list(reversed(list(real(*a, **kw))))):
            g.move_log.load(g)
        self.assertEqual(list(g.move_log.held_by["w:pottery"]), keys)

    @mock.patch.object(EF, "K_MAT", 1)
    def test_a_tool_output_is_never_a_neighbor(self):
        f = _field()
        ku = _say(f, "c", [("user", "Show me the pottery schedule.")])[0]
        kt = f.write_events([ST.Event(stream="observation", tool="shell", args={"command": "cat s"},
                                      result_head="pottery on mondays", outcome="ok", ts=1_700_000_100.0,
                                      call_id="t1")], session="c")[0]
        f.process()
        km = _say(f, "c", [("model", "It is on Mondays.")], t0=1_700_000_200.0)[0]
        self.assertEqual(f.move_log.prev_of(km), ku)
        self.assertNotEqual(f.move_log.prev_of(km), kt)


# ---- 3, 4, 5, 10, 12: the instance ballot's rules ------------------------------------------------
class Rules(unittest.TestCase):
    def test_question_possessives(self):
        from membrane import instances as IN
        self.assertEqual(IN._resolve("@3", "ben", "", "ann"), "ann")       # her -> the one person named
        self.assertEqual(IN._resolve("@2", "ben", "", "ann"), "")          # your -> nobody
        self.assertEqual(IN._resolve("@1", "ben", "", "ann"), "ben")       # my -> the asker
        self.assertEqual(IN._resolve("@3's cousin", "ben", "", "ann"), "ann's cousin")

    def test_an_unseen_or_stop_word_has_no_rarity(self):
        from membrane import instances as IN
        st = IN.Store(types.SimpleNamespace(b=None, origin="o"))
        st.keep("k", {"lemmas": ["pottery"], "seq": 1}, persist=False)
        st.keep("j", {"lemmas": ["tea"], "seq": 2}, persist=False)
        self.assertEqual(IN._idf(st, "zebra"), 0.0)
        self.assertEqual(IN._idf(st, "the"), 0.0)
        self.assertGreater(IN._idf(st, "pottery"), 0.0)

    def test_a_shared_lemma_is_near(self):
        from membrane import instances as IN
        f = types.SimpleNamespace(b=types.SimpleNamespace(get=lambda c, k: None), _term_key=lambda w: w)
        near = IN._near_fn(f)
        self.assertEqual(near("weddings", "wedding"), 1.0)
        self.assertEqual(near("ran", "run"), 1.0)
        self.assertEqual(near("pottery", "tea"), 0.0)          # no stored seeds: near nothing

    def test_person_cues_from_the_fields_person_things(self):
        from membrane import recall_vote as RV
        f = types.SimpleNamespace(get=lambda e: types.SimpleNamespace(surface="Caroline"))
        with mock.patch("membrane.movelog.cues", return_value={"person:p1", "w:dog"}):
            self.assertEqual(RV._people(f, "what did she adopt?"), ["caroline"])

    @unittest.skipUnless(HAVE_SPACY, "spaCy not installed")
    def test_her_dog_and_my_dog(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        ka = f.write_events([ST.Event(stream="discourse", role="user", text="Ann: My dog is called Rex.",
                                      ts=1_700_000_000.0)], session="c1")
        f.process()
        IN.drain(f)
        st = IN.store_of(f)
        # two people, set as the speakers the field heard
        rows = list(st.rows.values())
        self.assertTrue(rows)
        st2 = IN.Store(types.SimpleNamespace(b=None, origin="o"))
        m = IN.nlp(load=True)
        for i, (who, t) in enumerate([("ann", "My dog is called Rex."), ("ben", "My dog is called Max.")]):
            row = IN.read(m(t))
            row.update(n=i + 1, chat="c", seq=i, who=who)
            st2.keep(f"k{i}", row, persist=False)
        g = types.SimpleNamespace(_instance_store=st2, b=types.SimpleNamespace(get=lambda c, k: None),
                                  _term_key=lambda w: w)
        her, _w = IN.ballot(g, "What did Ann call her dog?")
        mine, _w = IN.ballot(g, "What is my dog called?", asker="ben")
        self.assertEqual(her[0], 1)
        self.assertEqual(mine[0], 2)
        _ = ka

    @unittest.skipUnless(HAVE_SPACY, "spaCy not installed")
    def test_the_place_ballot_never_embeds_nor_writes(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        _say(f, "c", [("user", "We walked around Paris all day."), ("user", "Lunch was in Lyon.")])
        IN.drain(f)
        calls = f.emb.calls
        with mock.patch.object(f.b, "upsert", side_effect=AssertionError("a write at ask time")):
            IN.class_ballots(f, "What did we do in Paris?", [])
        self.assertEqual(f.emb.calls, calls)


# ---- 6, 7: the vote's inputs ---------------------------------------------------------------------
class Inputs(unittest.TestCase):
    def test_filter_before_the_cut(self):
        from membrane import recall_vote as RV
        docs = {f"k{i}": {"origin": "o", "status": "forgotten" if i < 10 else "applied", "stream": "discourse",
                          "role": "user", "n": i} for i in range(40)}
        f = types.SimpleNamespace(origin="o", b=types.SimpleNamespace(
            get_multi=lambda c, ks: {k: docs[k] for k in ks if k in docs}))
        got = RV._ns(f, [f"k{i}" for i in range(40)])
        self.assertEqual(got, list(range(10, 30)))

    def test_the_voters_read_the_plain_question(self):
        from membrane import efgate as G
        from membrane import recall_banner as RB
        from membrane import recall_vote as RV
        Q = "Which colour of glaze do I like best for my pottery bowls?"
        f = _field()
        _say(f, "c", [("user", "Pottery classes are on Mondays."), ("user", "I like the green glaze best.")])
        seen = []
        with mock.patch.object(RV, "ballots", side_effect=lambda fld, cur, text, qvec=None, rows=None, asker="":
                               seen.append(qvec) or {}), \
                mock.patch("membrane.recall_rerank.scores", return_value=None):
            RB.build(f, Q, session="c")
        plain = G.message_vec(f, Q)
        self.assertIsNotNone(plain)
        self.assertTrue(seen)
        self.assertTrue(np.allclose(seen[0], plain))


# ---- 11-15: cost and safety at ask time ----------------------------------------------------------
class Cost(unittest.TestCase):
    def _scans(self, n_events):
        from membrane import instances as IN
        from membrane import recall_vote as RV
        from membrane.entity_block import recall_answers
        f = _field()
        IN.attach(f)
        lines = [("user", f"Note {i}: the pottery kiln {i} fired glaze batch {i}.") for i in range(n_events)]
        for lo in range(0, len(lines), 50):
            _say(f, "c", lines[lo:lo + 50], t0=1_700_000_000.0 + lo * 60)
        IN.drain(f)
        q = "Which glaze batch did kiln 3 fire?"
        pool = recall_answers(f, q, k=50, sim_min=0.0)
        scans = []
        real = f.b.scan
        f.b.scan = lambda coll, eq=None, *a, **kw: scans.append((coll, tuple(sorted((eq or {}).keys())))) or \
            real(coll, eq, *a, **kw)
        held_reads = []

        class Watch(dict):
            def __iter__(self):
                held_reads.append(1)
                return super().__iter__()

            def items(self):
                held_reads.append(1)
                return super().items()
        f.move_log.held = Watch(f.move_log.held)
        RV.vote(f, pool, q)
        return scans, held_reads

    @unittest.skipUnless(HAVE_SPACY, "spaCy not installed")
    def test_an_ask_reads_postings_never_the_whole_field(self):
        small, h1 = self._scans(20)
        big, h2 = self._scans(200)
        # every read is a keyed lookup (an event by n, a sentence's deltas by sid); entity_states is the test
        # backend's vector search (field.recall), the same at any size
        keyed = ("n", "sid", "event", "session")
        for scans in (small, big):
            self.assertFalse([s for s in scans if s[0] != "entity_states" and not set(keyed) & set(s[1])], scans)
        self.assertEqual(h1, [])
        self.assertEqual(h2, [])
        self.assertLessEqual(len(big), len(small) + 20)       # one point lookup per row the vote adds at most

    def test_without_spacy_loaded_the_vote_skips_instance_histories_and_never_loads_it(self):
        from membrane import instances as IN
        from membrane import language as LG
        from membrane import recall_vote as RV
        f = _field()
        _say(f, "c", [("user", "I started a pottery class on Tuesday.")])
        IN.attach(f, backfill=False)
        with mock.patch.dict(LG._NLP, {"m": None}), \
                mock.patch("spacy.load", side_effect=AssertionError("spaCy loaded inside a turn")):
            lists = RV.ballots(f, [], "What class did I start?")
        self.assertNotIn("instance histories", lists)

    @unittest.skipUnless(HAVE_SPACY, "spaCy not installed")
    def test_every_spacy_call_holds_the_lock(self):
        from membrane import instances as IN
        IN.nlp(load=True)
        entered = []

        class Lock:
            def __enter__(self):
                entered.append(1)

            def __exit__(self, *a):
                return False
        with mock.patch.object(IN, "NLP_LOCK", Lock()):
            IN.question("What is the dog called?")
            IN.Store(types.SimpleNamespace(b=types.SimpleNamespace(upsert=lambda *a: None), origin="o")).parse(
                [("k", {"text": "I adopted a dog.", "stream": "discourse"})])
        self.assertEqual(len(entered), 2)

    def test_the_move_log_backfill_runs_once(self):
        from membrane import movelog as ML
        be = MemBackend()
        f = _field(be)
        _say(f, "c", [("user", "I started a pottery class.")])
        f.flush()
        g = _field(be)
        ML.of(g).backfill(g)
        scans = []
        real = be.scan
        with mock.patch.object(be, "scan", side_effect=lambda *a, **kw: scans.append(a[0]) or real(*a, **kw)):
            self.assertEqual(ML.of(g).backfill(g), 0)
        self.assertNotIn("entity_deltas", scans)

    @unittest.skipUnless(HAVE_SPACY, "spaCy not installed")
    def test_the_parse_backfill_scans_only_past_its_mark(self):
        from membrane import instances as IN
        be = MemBackend()
        f = _field(be)
        IN.attach(f)
        _say(f, "c", [("user", "I adopted a dog.")])
        IN.drain(f)
        g = _field(be)
        eqs = []
        real = be.scan
        with mock.patch.object(be, "scan", side_effect=lambda coll, eq=None, *a, **kw: eqs.append((coll, eq)) or
                               real(coll, eq, *a, **kw)):
            IN.attach(g)
            IN.drain(g)
        ev = [eq for coll, eq in eqs if coll == "events"]
        self.assertTrue(ev)
        self.assertTrue(all("seq" in eq for eq in ev), ev)


try:
    import dateparser  # noqa: F401
    HAVE_DATEPARSER = True
except Exception:
    HAVE_DATEPARSER = False


# ---- the time ballot, rebuilt without training (spaCy DATE + dateparser) ----------------------------
@unittest.skipUnless(HAVE_SPACY and HAVE_DATEPARSER, "spaCy or dateparser not installed")
class TimeBallot(unittest.TestCase):
    MAY_2023 = 1_684_000_000.0          # 2023-05-13
    AUG_2023 = 1_692_000_000.0          # 2023-08-14
    FEB_2024 = 1_707_000_000.0          # 2024-02-03

    def _field(self):
        from membrane import instances as IN
        f = _field()
        IN.attach(f)
        keys = {}
        for name, ts, text in (("may", self.MAY_2023, "Caroline went hiking with her friends."),
                               ("aug", self.AUG_2023, "Caroline painted a sunset."),
                               ("lastmay", self.FEB_2024, "Last May Caroline ran a charity race."),
                               ("feb", self.FEB_2024, "Caroline cooked dinner.")):
            keys[name] = f.write_events([ST.Event(stream="discourse", role="user", text=text, ts=ts)],
                                        session="c-" + name)[0]
            f.process()
        IN.drain(f)
        return f, {k: int(f.b.get("events", v)["n"]) for k, v in keys.items()}

    def test_each_event_keeps_its_moment_from_its_own_time(self):
        from membrane import instances as IN
        f, ns = self._field()
        st = IN.store_of(f)
        by_n = {r["n"]: r for r in st.rows.values()}
        self.assertEqual(by_n[ns["may"]]["moments"], [self.MAY_2023])        # no date phrase: when it was said
        span = IN.own_span("May 2023")
        self.assertTrue(any(IN.time_fits(span, m) for m in by_n[ns["lastmay"]]["moments"]))   # "last May", 2024

    def test_a_question_in_may_2023_ranks_what_happened_then(self):
        from membrane import instances as IN
        f, ns = self._field()
        got = IN.time_ballot(f, IN.question("What did Caroline do in May 2023?"))
        self.assertEqual(set(got), {ns["may"], ns["lastmay"]})

    def test_the_time_ballot_sits_after_the_people_before_the_places(self):
        from membrane import instances as IN
        f, ns = self._field()
        bal = IN.class_ballots(f, "What did Caroline do in May 2023?", [99])
        self.assertEqual(bal[-1], [99])                                       # current recall last
        self.assertEqual(set(bal[-2]), {ns["may"], ns["lastmay"]})            # time just before it (no place)

    def test_a_question_without_a_time_has_no_time_ballot(self):
        from membrane import instances as IN
        f, _ns = self._field()
        self.assertIsNone(IN.time_ballot(f, IN.question("What did Caroline paint?")))

    def test_without_dateparser_the_time_ballot_is_skipped(self):
        from membrane import instances as IN
        f, _ns = self._field()
        with mock.patch.dict(sys.modules, {"dateparser": None}):
            self.assertIsNone(IN.time_ballot(f, IN.question("What did Caroline do in May 2023?")))


if __name__ == "__main__":
    unittest.main()
