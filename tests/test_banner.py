"""The banner contract (TRANSPARENCY): the thinking channel streams
EVERYTHING membrane injects -- recall verbatim, WHERE WE ARE, track
stories -- so the operator sees exactly what the model sees. One source:
the ctx itself. Plus the deterministic track stories (no LLM summarization)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.opencode as oc

VERDICT = {"verdict": "escalate", "sim": 0.67,
           "route": {"route": "glm-5.3-flash:cloud", "kind": "cloud",
                     "reason": "cloud-floor"}}
CTX = ("Recalled exchanges (...):\n\nQ: a memory about the tool skills\n"
       "A: the tool-skills answer\n\n"
       "WHERE WE ARE:\n- field: 6539 memories (99% trusted)")


def _handler(store_len=6539, tracks_store=None):
    h = object.__new__(oc.MembraneOpenAIHandler)
    srv = MagicMock()
    srv.membrane.store.__len__ = lambda s: store_len
    srv.membrane.gate_notes = []
    srv._boot_ts = 0.0          # a real float (MagicMock would mock it)
    if tracks_store is not None:
        srv.entity_store = tracks_store
    else:
        srv.entity_store = None
    h.server = srv
    return h


class TestTransparency(unittest.TestCase):
    """THE CONTRACT: the operator sees EVERYTHING membrane sends the model.
    The thinking channel streams the full injection, verbatim, per block."""

    def test_stories_only_no_recall_duplication(self):
        """THE CONTRACT: the banner carries ONLY the entity stories. The
        recall payload rides the ctx above; reciting it here doubles the
        bytes (the token-waste bug)."""
        h = _handler()
        out = h._gate_monologue(VERDICT, CTX, "test")
        # NO recall duplication
        self.assertNotIn("Recalled exchanges", out)
        self.assertNotIn("a memory about the tool skills", out)
        # NO "sent to the model" section
        self.assertNotIn("sent to the model", out)
        # the ctx itself is untouched (the recall rides there)
        self.assertIn("a memory about the tool skills", CTX)

    def test_track_stories_ride(self):
        import tempfile, os, time as _t
        from membrane.entities import EntityStore, extract_entities

        class E:
            def __init__(self, situation, action, born=None):
                self.situation, self.action, self.safe = situation, action, True
                self.extra, self.model = {}, {}
                if born:
                    self.extra["eph_born"] = born
                t = extract_entities(situation, " " + action)
                if t:
                    self.extra["entities"] = t

        with tempfile.TemporaryDirectory() as tmp:
            est = EntityStore(path=str(Path(tmp) / "entities.json"))
            est.watch_track("files:router.py", label="router")
            # the cell must embed near the query for the alignment floor:
            # embed its situation THROUGH the same prefix geometry
            cells = [E("tool edit for: fix x1 in router.py",
                       "Applied the failover patch; the ladder walks on",
                       born=_t.time())]
            from membrane.embed import OllamaEmbedder
            emb = OllamaEmbedder(model="mxbai-embed-large")
            qv = emb.embed(["what changed in router.py?"])[0]
            cells[0].vec = list(qv)
            h = _handler(tracks_store=est)
            h.server.membrane.store.exemplars = cells
            out = h._gate_monologue(VERDICT, CTX, "test", query_vec=qv)
            # the prose story rides: label + temporal arc + the action
            self.assertIn("router:", out)
            self.assertIn("Applied the failover patch", out)
            # the story ends at the beats: no horoscope suffixes
            self.assertNotIn("Moving fast", out)
            self.assertNotIn("focus moved", out)

    def test_stale_story_silent(self):
        """THE STALE GATE: a story whose newest beat is >24h old renders
        NOTHING (silence beats a stale story recited back)."""
        import tempfile, time as _t
        from membrane.entities import EntityStore, extract_entities

        class E:
            def __init__(self, situation, action, born):
                self.situation, self.action, self.safe = situation, action, True
                self.extra, self.model = {}, {}
                self.extra["eph_born"] = born
                t = extract_entities(situation, " " + action)
                if t:
                    self.extra["entities"] = t

        with tempfile.TemporaryDirectory() as tmp:
            est = EntityStore(path=str(Path(tmp) / "entities.json"))
            est.watch_track("files:router.py", label="router")
            cells = [E("tool edit for: fix x1 in router.py",
                       "Applied the failover patch",
                       born=_t.time() - 25 * 3600)]   # 25h old
            from membrane.embed import OllamaEmbedder
            emb = OllamaEmbedder(model="mxbai-embed-large")
            qv = emb.embed(["what changed in router.py?"])[0]
            cells[0].vec = list(qv)
            h = _handler(tracks_store=est)
            h.server.membrane.store.exemplars = cells
            out = h._gate_monologue(VERDICT, CTX, "test", query_vec=qv)
            self.assertEqual(out, "")   # silence, not a stale story

    def test_meta_voice_stripped(self):
        """The teed meta-voice ('The user is laughing about ...') is
        narration junk: the story keeps the substance, drops the echo."""
        from membrane.entities import _narrative
        s = ("cont: oof that is scary — The user is laughing about "
             "\"a token leak\" — Honest take: yes, it helps.")
        out = _narrative(s, 300)
        self.assertNotIn("The user is laughing", out)
        self.assertIn("token leak", out)

    def test_no_llm_summarization(self):
        """Token saving: the stories are DETERMINISTIC (no LLM call)."""
        h = _handler()
        # the render is pure string formatting -- same inputs, same bytes
        out1 = h._gate_monologue(VERDICT, CTX, "test")
        out2 = h._gate_monologue(VERDICT, CTX, "test")
        self.assertEqual(out1, out2)

    def test_no_meta_lines(self):
        """No meta-info about membrane: no turn verdicts, no learn counts,
        no gate notes, no operator vitals."""
        h = _handler()
        h.server.membrane.gate_notes = ["learned #661: something"]
        out = h._gate_monologue(VERDICT, CTX, "test")
        self.assertNotIn("turn:", out)
        self.assertNotIn("learned #661", out)
        self.assertNotIn("gate:", out)
        self.assertNotIn("eff_rank", out)


if __name__ == "__main__":
    unittest.main()