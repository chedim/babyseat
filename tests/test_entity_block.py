"""membrane/entity_block.py: which entities a question gets, and what the
block shows about them (verbatim evidence, the latest observation,
relations, conflicts, the budget)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def seeded():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    evs = [ST.Event(stream="discourse", role="user", ts=T0,
                    text="membrane/router.py picks the free model for each ask."),
           ST.Event(stream="discourse", role="model", ts=T0 + 5,
                    text="The fallback in membrane/router.py retries the gateway twice."),
           ST.Event(stream="discourse", role="user", ts=T0 + 9,
                    text="the weather is nice today")]
    f.write_events(evs, session="s")
    obs = ST.split_turn("x", results=[("c1", "edit", '{"filePath": "membrane/router.py"}',
                                        "Edit applied successfully.")], ts=T0 + 20)[1]
    f.write_events([obs], session="s")
    f.process()
    return f


class TestBlock(unittest.TestCase):
    def test_named_identifier_with_its_evidence_and_observation(self):
        f = seeded()
        views = EB.entities_for(f, "what does membrane/router.py do on failure?")
        self.assertEqual(views[0]["eid"], "f:membrane/router.py")
        v = views[0]
        self.assertTrue(all("membrane/router.py" in t for t in v["evidence"]))
        self.assertIn("edit", v["observed"])
        text = EB.render(views, now=T0 + 20 + 3600)
        self.assertIn("▸ f:membrane/router.py [file · said 1 · echo ~1 · observed 1 · 1h ago]", text)
        self.assertTrue(text.startswith(EB.HEADER))

    def test_everyday_words_need_coherence(self):
        f = seeded()
        eids = EB.question_entities(f, "is the weather nice today?")
        self.assertFalse([e for e in eids if e.startswith("w:")])

    def test_relations_and_conflicts_render(self):
        f = seeded()
        R.upsert(f.b, f.origin, "tool:edit", "edited", "f:membrane/router.py", "obs", ts=T0)
        st = f.get("f:membrane/router.py")
        f.b.upsert("claims", "cl:1", {"text": "I updated membrane/router.py.", "status": "contradicted",
                                      "evidence": [{"tool": "edit", "outcome": "failed"}]})
        st.conflicts.append("cl:1")
        text = EB.render(EB.entities_for(f, "membrane/router.py"), now=T0)
        # THE 'IT' FIX: a relation where the entity is the OBJECT names
        # the entity itself -- the block used to render the literal word
        # 'it' (the model read 'tool:edit edited it' every turn and
        # learned the pronoun as a fact)
        self.assertIn("relations: tool:edit edited f:membrane/router.py",
                      text)
        self.assertIn('! conflict: "I updated membrane/router.py." (contradicted: edit failed)', text)

    def test_budget_and_empty(self):
        f = seeded()
        self.assertIsNone(EB.render_for(f, "nothing known here"))
        self.assertIsNone(EB.render(EB.entities_for(f, "membrane/router.py"), budget=50))

    def test_related_lists_relation_partners(self):
        f = seeded()
        rel = EB.related(f, "membrane/router.py")
        self.assertEqual(rel[0]["key"], "f:membrane/router.py")
        self.assertEqual(rel[0]["how"], "named")
        self.assertIn({"key": "tool:edit", "shared": 1}, rel[0]["neighbors"])


class TestTracks(unittest.TestCase):
    def test_changes_since_a_watermark(self):
        f = seeded()
        ch = f.changes_since(0)
        self.assertIn("f:membrane/router.py", ch)
        last = f._ostats["seq_last"]
        self.assertEqual(f.changes_since(last), {})
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 99,
                                 text="membrane/router.py needs a timeout.")], session="s")
        f.process()
        self.assertEqual(list(f.changes_since(last)), ["f:membrane/router.py"])

    def test_track_keys_resolve_and_render(self):
        f = seeded()
        self.assertEqual(EB.track_eid(f, "files:membrane/router.py"), "f:membrane/router.py")
        self.assertEqual(EB.track_count(f, "f:membrane/router.py"), 3)
        ln = EB.track_line(f, "files:membrane/router.py", "router.py", now=T0 + 20)
        self.assertTrue(ln.startswith("track: f:membrane/router.py (router.py) — "
                                      "said 1 · echo ~1 · observed 1, last just now; latest: "), ln)
        self.assertIn("nothing in the field yet", EB.track_line(f, "files:nope.py"))
        self.assertEqual(EB.track_count(f, "files:nope.py"), 0)

    def test_now_line(self):
        f = seeded()
        self.assertTrue(EB.now_line(f, 0).startswith("now: f:membrane/router.py (3)"))
        self.assertIsNone(EB.now_line(f, f._ostats["seq_last"]))


class TestDiff(unittest.TestCase):
    def test_nothing_moved_nothing_said(self):
        f = seeded()
        self.assertIsNone(EB.state_diff(f, EB.watermark(f), "s"))

    def test_own_turns_count_external_news_quotes(self):
        f = seeded()
        mark = EB.watermark(f)
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 50,
                                 text="membrane/router.py should log the pick like membrane/gate.py does.")], session="s:1:0")
        f.write_events([ST.Event(stream="code", role="code", ts=T0 + 60,
                                 text="pick(model) in membrane/router.py: choose the cheapest model.",
                                 code={"file": "membrane/router.py", "full": "membrane.router.pick",
                                       "qual": "pick"})], session="code")
        f.process()
        d = EB.state_diff(f, mark, "s:1:")
        self.assertTrue(d.startswith(EB.DIFF_HEADER))
        self.assertIn("▸ f:membrane/router.py — code #", d)
        self.assertIn("choose the cheapest model", d)
        self.assertNotIn("should log the pick", d)            # own turn: counted, not quoted
        self.assertIn("this conversation moved: f:membrane/gate.py (1 discourse)", d)
        self.assertNotIn("this conversation moved:",
                         EB.state_diff(f, mark, "s:1:", include_own=False) or "")
        self.assertIsNone(EB.state_diff(f, EB.watermark(f), "s:1:"))

    def test_new_conflicts_after_the_mark(self):
        from membrane import claims as CL
        f = seeded()
        mark = EB.watermark(f)
        f.b.upsert("claims", "cl:9", {"text": "I updated membrane/router.py.", "status": "contradicted"})
        CL._conflict(f, "cl:9", ["f:membrane/router.py"])
        d = EB.state_diff(f, mark, "s")
        self.assertIn('! new conflict on f:membrane/router.py: "I updated membrane/router.py." '
                      '(contradicted)', d)
        self.assertIsNone(EB.state_diff(f, EB.watermark(f), "s"))


class TestReconstruct(unittest.TestCase):
    def test_the_conversation_as_events(self):
        f = seeded()
        r = EB.reconstruct(f, "s", now=T0 + 60)
        lines = r.splitlines()
        self.assertTrue(lines[0].startswith("THE CONVERSATION SO FAR"))
        self.assertIn("user: membrane/router.py picks the free model for each ask.", lines[1])
        self.assertIn("you: The fallback in membrane/router.py", lines[2])
        self.assertTrue(lines[-1].startswith("  [#") and "edit membrane/router.py" in lines[-1])
        self.assertIsNone(EB.reconstruct(f, "other-seat", now=T0 + 60))
        self.assertIsNone(EB.reconstruct(f, "s", now=T0 + 10 * 3600))
        short = EB.reconstruct(f, "s", budget=120, now=T0 + 60)
        self.assertIn("edit membrane/router.py", short)
        self.assertNotIn("picks the free model", short)


if __name__ == "__main__":
    unittest.main()


class TestListeners(unittest.TestCase):
    def test_the_watch_board_sees_applied_live_events(self):
        import tempfile
        from membrane import opencode as oc
        from membrane.watch import WatchBoard
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")

        class _Cap:
            mode = "on"

            class sessions:
                boot = 7

        class _Srv:
            efield, capturer = f, _Cap()
        srv = _Srv()
        with tempfile.TemporaryDirectory() as d:
            srv._watch_board = WatchBoard(Path(d) / "w.json")
            srv._intent_board = None
            probe = FakeEmbedder().embed(["membrane/router.py retries the gateway"])[0]
            srv._watch_board.set_watch("router retries", probe)
            f.listeners.append(oc._efield_watch_listener(srv))
            f.write_events([ST.Event(stream="discourse", role="user", ts=T0,
                                     text="membrane/router.py retries the gateway")], session="s:7:0")
            f.write_events([ST.Event(stream="discourse", role="user", ts=T0 + 1,
                                     text="membrane/router.py retries the gateway")], session="b:0")
            f.process()
            evs = srv._watch_board.events
            self.assertEqual(len(evs), 1)                       # history is not an arrival
            self.assertEqual(evs[0]["ex_idx"], 1)               # '#N' = the event number


class TestPickEvidence(unittest.TestCase):
    def test_answers_beat_restatements(self):
        rows = [{"text": "how long do restarts take?", "score": 0.97, "role": "user"},
                {"text": "how long do the restarts take?", "score": 0.95, "role": "user"},
                {"text": "a restart takes about 6.5 s, most of it the embedder warmup", "score": 0.71,
                 "role": "model"},
                {"text": "restarts are slow today", "score": 0.74, "role": "user"}]
        got = [r["text"] for r in EB.pick_evidence(rows, 2)]
        self.assertEqual(got[0], "a restart takes about 6.5 s, most of it the embedder warmup")
        self.assertNotIn("how long do the restarts take?", got)


class TestPastAnswers(unittest.TestCase):
    def test_what_was_worked_out_after_a_similar_ask(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events([
            ST.Event(stream="discourse", role="user", ts=T0, text="how long do membrane restarts take?"),
            ST.Event(stream="discourse", role="reasoning", ts=T0 + 1,
                     text="The user is asking how long membrane restarts take. "
                          "The boot spends most of its time streaming the field rows through the parser."),
            ST.Event(stream="discourse", role="model", ts=T0 + 2, text="Membrane restart timing"),
            ST.Event(stream="discourse", role="model", ts=T0 + 3,
                     text="A restart now takes about 1.2 seconds after the id-only scan landed."),
            ST.Event(stream="discourse", role="user", ts=T0 + 9, text="thanks, next topic")],
            session="s:1:0")
        f.process()
        rows = EB.past_answers(f, "how long do membrane restarts take?", sim_min=0.5)
        self.assertEqual(len(rows), 1)
        text = " ".join(rows[0]["answer"])
        self.assertIn("about 1.2 seconds", text)
        self.assertIn("streaming the field rows", text)
        self.assertNotIn("The user is asking", text)          # a restatement
        self.assertNotIn("Membrane restart timing", text)     # a session title
        block = EB.render_past(rows, now=T0 + 10)
        self.assertTrue(block.startswith(EB.PAST_HEADER))
        self.assertIn("worked out (#", block)
