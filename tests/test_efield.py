"""membrane/efield.py on MemBackend: the delta rule, online statistics,
nursery promotion, idempotence, rebuild, surprise, and the E18/E19
distinguishability claim as a regression."""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane import fieldmap as FM  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def field(**kw):
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host", **kw)


def say(f, texts, role="user", session="s1"):
    evs = [ST.Event(stream="discourse", role=role, text=t, ts=1000.0 + i)
           for i, t in enumerate(texts)]
    f.write_events(evs, session=session)
    f.process()


def states(f):
    out = {}
    for _, d in f.b.scan("entity_states"):
        if d.get("tier") == "live":
            out[d["eid"]] = (EF.at_stored(d["state"]), d["n"], round(d["coherence"], 9))
    return out


class TestMath(unittest.TestCase):
    def test_online_coherence_is_mean_pairwise_cosine(self):
        rng = np.random.default_rng(1)
        R = [EF.unit(v) for v in rng.standard_normal((9, 16))]
        pair = np.mean([R[i] @ R[j] for i in range(9) for j in range(9) if i != j])
        self.assertAlmostEqual(EF.coherence_of(np.sum(R, axis=0), 9), pair, places=9)


class TestWrites(unittest.TestCase):
    def test_events_are_idempotent(self):
        f = field()
        ev = ST.Event(stream="discourse", role="user", text="the router picks models", ts=5.0)
        k1 = f.write_events([ev], session="s")
        k2 = f.write_events([ev], session="s")
        self.assertEqual(k1, k2)
        self.assertEqual(len(list(f.b.scan("events"))), 1)

    def test_a_result_upgrades_its_call_only_observation(self):
        f = field()
        call = ST.split_turn("x", calls={0: {"id": "c9", "function": {
            "name": "edit", "arguments": '{"filePath": "a.py"}'}}})[1]
        (k,) = f.write_events([call])
        self.assertEqual(f.b.get("events", k)["outcome"], "call-only")
        res = ST.split_turn("x", results=[("c9", "edit", '{"filePath": "a.py"}',
                                            "Edit applied successfully.")])[1]
        (k2,) = f.write_events([res])
        self.assertEqual(k, k2)
        d = f.b.get("events", k)
        self.assertEqual((d["outcome"], d["status"]), ("applied", "pending"))


class TestApplication(unittest.TestCase):
    def test_words_wait_in_the_nursery_specifics_do_not(self):
        f = field()
        say(f, ["please update membrane/router.py for the gateway", "the gateway keeps timing out"])
        self.assertEqual(f.get("f:membrane/router.py").tier, "live")
        self.assertEqual(f.get("w:gateway").tier, "nursery")
        say(f, ["the gateway returns errors today"])
        g = f.get("w:gateway")
        self.assertEqual(g.tier, "live")
        self.assertEqual(g.n, 3)

    def test_promotion_replay_equals_direct_application(self):
        f = field()
        texts = ["the gateway keeps timing out", "the gateway returns errors today",
                 "restart the gateway after lunch"]
        say(f, texts)
        emb = FakeEmbedder()
        # seeds are stored float16 (the terms cache): expect the rounded seed
        from membrane.efstore import pack_f16, unpack_f16
        s = EF.at_stored(unpack_f16(pack_f16(EF.unit(emb.embed(["gateway"])[0]))))
        for t in texts:                           # sentences stored PACKED f16 now
            x = EF.unit(unpack_f16(pack_f16(EF.unit(emb.embed([t])[0]))))
            s = EF.at_stored(EF.unit(s + EF.GAIN * (x - s)))   # stored precision
        np.testing.assert_allclose(f.get("w:gateway").state, s, atol=1e-12)

    def test_processing_twice_changes_nothing(self):
        f = field()
        say(f, ["the gateway keeps timing out", "the gateway returns errors today",
                "restart the gateway after lunch"])
        before = states(f)
        f.process()
        self.assertEqual(before.keys(), states(f).keys())
        for k in before:
            np.testing.assert_allclose(before[k][0], states(f)[k][0])

    def test_rebuild_equals_incremental(self):
        f = field()
        say(f, [f"the gateway {w} again today" for w in
                ("fails", "recovers", "stalls", "restarts", "fails")])
        say(f, ["membrane/router.py routes the gateway traffic"], role="model")
        before = states(f)
        f.rebuild()
        after = states(f)
        self.assertEqual(before.keys(), after.keys())
        for k in before:
            np.testing.assert_allclose(before[k][0], after[k][0], atol=1e-9)
            self.assertEqual(before[k][1], after[k][1])
            self.assertAlmostEqual(before[k][2], after[k][2], places=6)

    def test_boilerplate_moves_nothing(self):
        f = field()
        say(f, ["welcome back to the session friend"] * 1 +
            [f"welcome back to the session friend {'x' * i}" for i in range(0)])
        n0 = f.get("w:welcome").n if f.get("w:welcome") else 0
        for i in range(EF.BOILER_MIN + 3):
            say(f, ["Welcome back to the session friend"], session=f"s{i}")
        st = f.get("w:welcome")
        self.assertLessEqual(st.n, EF.BOILER_MIN)
        self.assertGreaterEqual(st.n, n0)

    def test_surprise_is_lower_for_known_entities(self):
        f = field()
        base = ["the gateway keeps timing out", "the gateway returns errors today",
                "restart the gateway after lunch", "the gateway logs look noisy"]
        say(f, base)
        say(f, ["the gateway keeps timing out again"], session="s2")
        say(f, ["purple zebras juggle quietly nearby"], session="s3")
        evs = {d["text"]: d.get("surprise") for _, d in f.b.scan("events")}
        self.assertLess(evs["the gateway keeps timing out again"],
                        evs["purple zebras juggle quietly nearby"])

    def test_session_context_is_ram_only(self):
        f = field()
        say(f, ["purple zebras juggle quietly nearby"], session="sx")
        self.assertIsNotNone(f.now("sx"))
        self.assertFalse(any(d["eid"].startswith("ctx:") for _, d in f.b.scan("entity_states")))

    def test_evidence_returns_the_entity_sentences(self):
        f = field()
        texts = ["the gateway keeps timing out", "the gateway returns errors today",
                 "restart the gateway after lunch"]
        say(f, texts)
        ev = f.evidence("w:gateway", k=5)
        self.assertEqual({e["text"] for e in ev}, set(texts))
        q = EF.unit(FakeEmbedder().embed(["gateway errors"])[0])
        self.assertEqual(f.evidence("w:gateway", k=1, query_vec=q)[0]["text"],
                         "the gateway returns errors today")


class TestTheClaims(unittest.TestCase):
    """E18/E19/E23 as regressions on a planted-topic corpus."""

    TOPICS = {"router": "cheapest model ladder budget free tier",
              "gateway": "timeout latency socket retry proxy",
              "compactor": "distill summary cluster notes idle",
              "viewer": "map stars render browser canvas",
              "thinker": "heartbeat digest speech idle stimuli",
              "backfill": "audit replay cursor resume chunk",
              "codegraph": "callers callees symbols impact ast",
              "intake": "capture split events stream hook",
              "claims": "backed contradicted judge evidence verdict",
              "tracks": "banner watch alerts ring motion",
              "peers": "registry heartbeat seats channel ask",
              "ledger": "consensus shards promote commons vote"}

    def corpus(self, rng, n=12):
        names = list(self.TOPICS)
        msgs = []
        for _ in range(n):
            a, b = rng.choice(names, 2, replace=False)
            sa = " ".join(rng.choice(self.TOPICS[a].split(), 3, replace=False))
            sb = " ".join(rng.choice(self.TOPICS[b].split(), 3, replace=False))
            msgs.append((a, b, f"the {a} needs {sa} work here. also the {b} shows {sb} issues now."))
        return msgs

    def test_sentence_tracking_distinguishes_better_than_whole_message(self):
        rng = np.random.default_rng(7)
        msgs = self.corpus(rng, 60)
        f = field()
        say(f, [m for _, _, m in msgs])
        emb = FakeEmbedder()
        # the whole-message baseline: each entity blends toward the MESSAGE
        whole = {e: EF.unit(emb.embed([e])[0]) for e in self.TOPICS}
        for a, b, m in msgs:
            x = EF.unit(emb.embed([m])[0])
            for e in (a, b):
                whole[e] = EF.unit(whole[e] + EF.GAIN * (x - whole[e]))
        from membrane.eids import canon
        span = {e: f.get(canon("w", e)).state for e in self.TOPICS}

        def margin(states):
            """How far the right entity scores above the best wrong one,
            for a probe made of its topic words (no ceiling, unlike top-1)."""
            ms = []
            for e, words in self.TOPICS.items():
                probe = EF.unit(emb.embed([" ".join(words.split()[:2])])[0])
                right = float(states[e] @ probe)
                wrong = max(float(states[o] @ probe) for o in states if o != e)
                ms.append(right - wrong)
            return float(np.mean(ms))
        self.assertGreater(margin(span), margin(whole))

    def test_topic_words_are_more_coherent_than_filler(self):
        rng = np.random.default_rng(3)
        f = field()
        texts = []
        for i in range(20):
            topic = rng.choice(list(self.TOPICS))
            extra = " ".join(rng.choice(self.TOPICS[topic].split(), 2, replace=False))
            texts.append(f"honestly the {topic} work {extra} continues")
        texts += [f"the router needs {w} budget attention" for w in ("cheapest", "ladder", "free", "tier")]
        say(f, texts)
        from membrane.eids import canon
        self.assertGreater(f.get(canon("w", "router")).coherence,
                           f.get(canon("w", "honestly")).coherence)


if __name__ == "__main__":
    unittest.main()


class TestLearnForget(unittest.TestCase):
    def test_learn_writes_a_note(self):
        f = field()
        k = f.learn("  membrane/router.py   prefers free models ")
        f.process()
        d = f.b.get("events", k)
        self.assertEqual((d["role"], d["text"], d["status"]), ("note", "membrane/router.py prefers free models", "applied"))
        self.assertEqual(f.get("f:membrane/router.py").n, 1)
        self.assertIsNone(f.learn("   "))

    def test_forget_equals_never_having_seen_it(self):
        texts = ["membrane/router.py picks the gateway model",
                 "the gateway in membrane/router.py times out",
                 "membrane/router.py retries the gateway twice",
                 "the gateway is slow on mondays"]
        a = field()
        say(a, texts)
        b = field()
        say(b, [t for i, t in enumerate(texts) if i != 1])
        n = next(d["n"] for _, d in a.b.scan("events") if "times out" in d["text"])
        r = a.forget(n)
        self.assertIn("f:membrane/router.py", r["entities"])
        ra, rb = a.get("f:membrane/router.py"), b.get("f:membrane/router.py")
        self.assertEqual((ra.n, ra.tier), (rb.n, rb.tier))
        np.testing.assert_allclose(ra.state, rb.state, atol=1e-5)
        # 'gateway' drops below K_MAT distinct sentences: back to the nursery
        self.assertEqual(a.get("w:gateway").tier, b.get("w:gateway").tier)
        ev = a.event_by_number(n)[1]
        self.assertEqual((ev["status"], ev["text"]), ("forgotten", ""))
        self.assertTrue(a.forget(n)["already"])
        self.assertIsNone(a.forget(99999))
        a.rebuild()                                   # a rebuild keeps it forgotten
        self.assertEqual(a.get("f:membrane/router.py").n, 2)

    def test_event_numbers_are_short_and_backfillable(self):
        f = field()
        say(f, ["one thing about membrane/a.py", "another about membrane/b.py"])
        self.assertEqual(sorted(d["n"] for _, d in f.b.scan("events")), [1, 2])
        k, d = next(iter(f.b.scan("events")))
        d.pop("n")
        f.b.upsert("events", k, d)
        self.assertEqual(f.number_events(), 1)
        self.assertEqual(f.b.get("events", k)["n"], 3)


class TestMassMetabolism(unittest.TestCase):
    """THE FIELD'S METABOLISM (2026-10-13, docs/comparison.md §5, borrowed
    from holomem): mass fades on a half-life from the state's last accreting
    touch, a fresh accreting mention re-inflates it, an unresolved conflict
    damps it. Read-time only -- `accr` stays the raw counter, and a rebuild
    replays `last_accrue_ts` exactly like it replays `accr`. Flag OFF ->
    everything is the raw counter (the deploy-day no-op)."""

    HL = 100.0                       # a short half-life for the arithmetic

    def setUp(self):
        self._saved = (EF.MASS_METABOLISM, EF.MASS_HALF_LIFE_S)
        EF.MASS_METABOLISM = True
        EF.MASS_HALF_LIFE_S = self.HL

    def tearDown(self):
        EF.MASS_METABOLISM, EF.MASS_HALF_LIFE_S = self._saved

    def test_mass_halves_after_a_half_life_of_stillness(self):
        st = EF.EntityState(eid="w:gateway", kind="term", accr=4, last_accrue_ts=1000.0)
        self.assertEqual(EF.mass_eff(st, 1000.0), 4.0)
        self.assertAlmostEqual(EF.mass_eff(st, 1000.0 + self.HL), 2.0, places=6)
        self.assertAlmostEqual(EF.mass_eff(st, 1000.0 + 2 * self.HL), 1.0, places=6)
        self.assertLessEqual(EF.mass_eff(st, 9e9), 4.0)
        self.assertGreaterEqual(EF.mass_eff(st, 1000.0), 0.0)

    def test_a_fresh_accreting_mention_restores_mass(self):
        f = field()
        say(f, ["the gateway keeps timing out", "the gateway returns errors today",
                "restart the gateway after lunch"])
        st = f.get("w:gateway")
        self.assertGreaterEqual(st.accr, 1)
        mid = st.last_accrue_ts + 30 * self.HL          # long dead
        low = EF.mass_eff(st, mid)
        self.assertLess(low, float(st.accr) * 0.5)
        evs = [ST.Event(stream="discourse", role="user",
                        text="the gateway keeps timing out again", ts=mid + 5.0)]
        f.write_events(evs, session="s2")
        f.process()
        st2 = f.get("w:gateway")
        self.assertGreater(EF.mass_eff(st2, mid + 5.0), low)
        self.assertEqual(st2.last_accrue_ts, mid + 5.0)

    def test_rebuild_derives_the_last_accreting_touch(self):
        f = field()
        say(f, ["the gateway keeps timing out", "the gateway returns errors today",
                "restart the gateway after lunch"])
        st = f.get("w:gateway")
        before = st.to_doc(f.origin)
        f.rebuild()
        after = f.get("w:gateway").to_doc(f.origin)
        self.assertEqual(before["accr"], after["accr"])
        self.assertEqual(before["last_accrue_ts"], after["last_accrue_ts"])

    def test_an_unresolved_conflict_damps_the_mass(self):
        st = EF.EntityState(eid="w:gateway", kind="term", accr=4, last_accrue_ts=1000.0,
                            conflicts=["c"])
        self.assertAlmostEqual(
            EF.mass_eff(st, 1000.0), 4.0 / (1.0 + EF.MASS_CONFLICT_DAMP), places=9)
        st2 = EF.EntityState(eid="w:gateway", kind="term", accr=4, last_accrue_ts=1000.0,
                             conflicts=["c", "d"])
        self.assertAlmostEqual(
            EF.mass_eff(st2, 1000.0), 4.0 / (1.0 + 2 * EF.MASS_CONFLICT_DAMP), places=9)

    def test_edges_of_the_metabolism(self):
        self.assertEqual(EF.mass_eff({"accr": 0, "last_accrue_ts": 1.0}, 1.0), 0.0)
        self.assertEqual(EF.mass_eff(EF.EntityState(eid="w:g", kind="term"), 9e9), 0.0)
        # a pre-metabolism snapshot: only last_ts on the clock is the best it had
        legacy = {"accr": 8, "last_ts": 1000.0}
        self.assertAlmostEqual(EF.mass_eff(legacy, 1000.0 + self.HL), 4.0, places=6)
        # a clock it never had: no decay yet, mass stays
        self.assertEqual(EF.mass_eff({"accr": 8}, 9e9), 8.0)

    def test_the_flag_off_is_the_raw_counter(self):
        EF.MASS_METABOLISM = False
        st = EF.EntityState(eid="w:gateway", kind="term", accr=4, last_accrue_ts=0.0)
        self.assertEqual(EF.mass_eff(st, 9e9), 4.0)
        self.assertEqual(st.mass, 4)
        self.assertEqual(EF.mass_eff({"accr": 8, "last_ts": 0.0}, 9e9), 8.0)

    def test_the_card_and_the_map_carry_the_effective_mass(self):
        doc = {"eid": "w:gateway", "kind": "term", "surface": "gateway",
               "accr": 16, "mass_eff": 4.0}
        card = FM.entity_card(doc, [], [], [])
        self.assertEqual(card["mass_eff"], 4.0)
        self.assertEqual(card["accr"], 16)
        rng = np.random.default_rng(2)
        rows = [{"eid": f"w:e{i}", "kind": "term", "surface": f"e{i}",
                 "state": (rng.normal(size=8)).tolist(), "accr": 16,
                 "mass_eff": 4.0, "coherence": 0.5, "first_ts": 0, "last_ts": 0,
                 "activity": 0.0, "conflicts": []} for i in range(12)]
        m = FM.field_map(rows, [], k=2)
        ids = [a["id"] for a in m["axes"]]
        mass_i = ids.index("mass")
        for n in m["nodes"]:
            self.assertEqual(n["mass"], 4)                  # the node's mass
            self.assertEqual(n["v"][mass_i], round(math.log1p(4.0), 3))  # the axis
