"""membrane/efgate.py: the gate on answer events (sim, entity agreement,
consensus, trust, the staleness guard) and the replay A/B log."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efgate as G  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0
Q = "how do I restart membrane/router.py cleanly?"
A = "Stop the seat, then run make restart; membrane/router.py reloads its table."


def field_with_history():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    f.write_events([ST.Event(stream="discourse", role="user", ts=T0, text=Q),
                    ST.Event(stream="discourse", role="model", ts=T0 + 5, text=A),
                    ST.Event(stream="discourse", role="user", ts=T0 + 50,
                             text="the weather is nice")], session="s:1:0")
    f.process()
    return f


class TestGate(unittest.TestCase):
    def test_the_same_ask_is_replayable(self):
        f = field_with_history()
        v = G.candidate(f, Q)
        self.assertTrue(v["eligible"], v)
        self.assertEqual((v["answer"], v["q_event"], v["a_event"]), (A, 1, 2))

    def test_a_different_entity_never_replays(self):
        f = field_with_history()
        v = G.candidate(f, "how do I restart membrane/gateway.py cleanly?", sim_min=0.0)
        self.assertFalse(v["eligible"])
        self.assertTrue(v["reason"].startswith("entities differ"), v)

    def test_far_asks_and_empty_history(self):
        f = field_with_history()
        self.assertFalse(G.candidate(f, "what is the capital of france")["eligible"])
        e = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        self.assertEqual(G.candidate(e, Q)["reason"], "no earlier ask")

    def test_the_staleness_guard(self):
        f = field_with_history()
        obs = ST.split_turn("x", results=[("c1", "edit", '{"filePath": "membrane/router.py"}',
                                            "Edit applied successfully.")], ts=T0 + 60)[1]
        f.write_events([obs], session="s:1:0")
        f.process()
        v = G.candidate(f, Q)
        self.assertFalse(v["eligible"])
        self.assertEqual(v["reason"], "stale: observation on f:membrane/router.py after the answer")

    def test_trust_and_binding(self):
        f = field_with_history()
        a_key = G.candidate(f, Q)["a_key"]
        self.assertEqual(G.candidate(f, Q, binding_failed=True)["reason"], "unresolved pronoun")
        f.b.upsert("annotations", f"an:{a_key}", {"safe": False})
        self.assertEqual(G.candidate(f, Q)["reason"], "the answer is marked unsafe")


class TestAB(unittest.TestCase):
    def test_open_close_judge_report(self):
        f = field_with_history()
        v = G.candidate(f, Q)
        k = G.ab_open(f, v, Q, served=False, ts=T0 + 100)
        d = G.ab_close(f, k, A)
        self.assertAlmostEqual(d["answer_sim"], 1.0, places=4)
        self.assertIsNone(G.ab_close(f, k, A))                  # closes once
        n = G.ab_judge_pending(f, lambda prompt: "agree: same steps")
        self.assertEqual(n, 1)
        G.ab_open(f, v, Q + " again", served=True, ts=T0 + 200)
        r = G.ab_report(f)
        self.assertEqual((r["eligible"], r["served_replay"], r["paired"], r["judged"]), (2, 1, 1, 1))
        self.assertEqual(r["verdicts"], {"agree": 1})

    def test_serve_replay_fraction(self):
        self.assertFalse(G.serve_replay(0.0, rng=lambda: 0.0))
        self.assertTrue(G.serve_replay(0.3, rng=lambda: 0.1))
        self.assertFalse(G.serve_replay(0.3, rng=lambda: 0.5))


if __name__ == "__main__":
    unittest.main()


class TestGeometry(unittest.TestCase):
    def test_geometry_on_states(self):
        f = field_with_history()
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 70 + i,
                                 text=f"membrane/m{i}.py handles part {i} of the pipeline")
                        for i in range(5)], session="s:1:0")
        f.process()
        g = f.geometry(max_age_s=0)
        self.assertGreaterEqual(g["entities"], 2)
        self.assertGreater(g["eff_rank"], 1.0)
        self.assertEqual(g["conflict_frac"], 0.0)
        self.assertAlmostEqual(g["free_energy"], g["rank_ratio"], places=4)
        e = EF.EntityField(MemBackend(), FakeEmbedder(), "o")
        self.assertIsNone(e.geometry(max_age_s=0)["eff_rank"])


class TestReviewAB(unittest.TestCase):
    def test_the_report_lists_pairs(self):
        from membrane.review import ab_summary
        f = field_with_history()
        v = G.candidate(f, Q)
        G.ab_close(f, G.ab_open(f, v, Q, served=False, ts=T0 + 100), A)
        out = ab_summary("x", origin=f.origin, backend=f.b)
        self.assertIn("1 replayable turns, 0 served from the field, 1 paired", out)
        self.assertIn("[q #1 -> a #2", out)
        self.assertIn("judge pending", out)
