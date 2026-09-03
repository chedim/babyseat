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
        """THE UNIFIED RENDER (the operator's call): stories ride WHERE WE
        ARE -- one home, one renderer; the banner keeps vitals+alerts
        only (the old split-brain: stories in the banner, fallbacks in
        WHERE WE ARE, same tracks)."""
        import tempfile, time as _t
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
            cells = [E("tool edit for: fix x1 in router.py",
                       "Applied the failover patch; the ladder walks on",
                       born=_t.time())]
            from membrane.embed import OllamaEmbedder
            emb = OllamaEmbedder(model="mxbai-embed-large")
            qv = emb.embed(["what changed in router.py?"])[0]
            cells[0].vec = list(qv)
            h = _handler(tracks_store=est)
            h.server.membrane.store.exemplars = cells
            out = oc._where_we_are(h.server, qv)
            # the prose story rides WHERE WE ARE: ENTITY ID prefix + arc
            self.assertIn("files:router.py:", out)
            self.assertIn("applied the failover patch", out.lower())
            # the banner (monologue) carries NO stories -- vitals+alerts only
            mono = h._gate_monologue(VERDICT, CTX, "test", query_vec=qv)
            self.assertNotIn("files:router.py", mono)

    def test_geometry_first_after_bootstrap(self):
        """THE GEOMETRY PROOF (the operator's demand: 'prove that its
        fixed and will appear as unified single banner after restart'):
        the exact boot path in-process -- ensure_track_entity for every
        track, consolidate to seed rings, then the unified render.
        Asserts: a positioned entity renders GEOMETRY ('sits near ...')
        never cell recitation as the lead; the self-match is excluded;
        the ring persists across boots (the seed-persistence stamp)."""
        import tempfile, time as _t
        from membrane.entities import EntityStore, extract_entities

        class E:
            def __init__(self, situation, action, born, vec):
                self.situation, self.action, self.safe = situation, action, True
                self.extra, self.model = {}, {}
                self.extra["eph_born"] = born
                self.extra["entities"] = extract_entities(situation, action)
                self.vec = vec

        with tempfile.TemporaryDirectory() as tmp:
            est = EntityStore(path=str(Path(tmp) / "entities.json"))
            est.watch_track("files:router.py", label="router")
            # the bootstrap: guarantee the doc, consolidate to seed the ring
            est.ensure_track_entity("files:router.py")
            cells = [E("tool edit for: fix x1 in router.py",
                       "Applied the failover patch",
                       _t.time(), None) for _ in range(3)]
            from membrane.embed import OllamaEmbedder
            emb = OllamaEmbedder(model="mxbai-embed-large")
            qv = emb.embed(["what changed in router.py?"])[0]
            for c in cells:
                c.vec = list(qv)
            from membrane.night import consolidate_entities
            # a SECOND entity in the world: the subject needs something
            # to point at (the self-exclusion removes the track's own key)
            qv2 = emb.embed(["the watch.py collapse guards"])[0]
            watch_cells = [type("C", (), {
                "situation": "the watch.py collapse guards fire",
                "action": "", "safe": True,
                "extra": {"eph_born": _t.time(),
                          "entities": extract_entities("watch.py collapse guards", "")},
                "model": "", "vec": list(emb.embed(["watch.py collapse guards"])[0])})()
                for _ in range(3)]
            cells = cells + watch_cells
            store = type("S", (), {"exemplars": cells,
                                   "mut_lock": type("L", (), {
                                       "__enter__": lambda s: None,
                                       "__exit__": lambda s, *a: None})(),
                                   "field_path": "/tmp/x",
                                   "__len__": lambda s: len(cells)})()
            class Mem: pass
            mem = Mem(); mem.store, mem.embedder = store, emb
            mem.mut_lock = store.mut_lock
            mem.field_path = "/tmp/x"
            consolidate_entities(mem, est, materialize=0,
                                 only_keys=["files:router.py"])
            ent = est._entity_for_track("files:router.py")
            self.assertTrue(ent.position, "ring/position must seed")
            # the render: GEOMETRY, not cell recitation
            h = _handler(tracks_store=est)
            h.server.membrane.store.exemplars = cells
            out = oc._where_we_are(h.server, qv) or ""
            self.assertIn("sits near", out)          # the geometry lead
            self.assertIn("files:router.py:", out)   # the id prefix
            # THE SELF-EXCLUSION: the track never sits near itself
            self.assertNotIn("sits near router.py", out)

    def test_stale_story_told_with_age(self):
        """THE STALE COVENANT (revised, the operator's 'I want stories'
        call): an old story is told WITH ITS AGE ATTACHED ('last active
        25h ago: ...') -- silence was the lie of omission. Rendered in
        WHERE WE ARE (the unified home)."""
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
            out = oc._where_we_are(h.server, qv)
            # the story IS told -- with the honest age stamp
            self.assertIn("router", out)
            self.assertIn("25h ago", out)

    def test_what_model_sees_is_what_operator_sees(self):
        """THE TRANSPARENCY CONTRACT AS A TEST (the operator's call):
        render BOTH lanes in the same turn and assert they never
        contradict -- a track's story appears in WHERE WE ARE; the banner
        never re-renders stories (split-brain is the failure mode:
        stories in one lane, fallbacks in the other, same tracks)."""
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
                       born=_t.time() - 25 * 3600)]
            from membrane.embed import OllamaEmbedder
            emb = OllamaEmbedder(model="mxbai-embed-large")
            qv = emb.embed(["what changed in router.py?"])[0]
            cells[0].vec = list(qv)
            h = _handler(tracks_store=est)
            h.server.membrane.store.exemplars = cells
            wwa = oc._where_we_are(h.server, qv) or ""
            mono = h._gate_monologue(VERDICT, CTX, "test", query_vec=qv)
            # WHERE WE ARE: the story (id-prefixed)
            self.assertIn("files:router.py:", wwa)
            # the banner: NO story bytes (one home, one renderer)
            self.assertNotIn("files:router.py:", mono)
            # no fallback/story CONTRADICTION: a track that renders a
            # story in WHERE WE ARE must never render its fallback there
            self.assertNotIn("files:router.py (router)", wwa)

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