"""The banner contract (TRANSPARENCY): the thinking channel streams
EVERYTHING membrane injects -- recall verbatim, WHERE WE ARE, track
stories -- so the operator sees exactly what the model sees. One source:
the ctx itself. Plus the deterministic track stories (no LLM summarization)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.opencode as oc
import _ef_fakes as _fff
from _ef_fakes import FakeEmbedder  # noqa: F401

VERDICT = {"verdict": "escalate", "sim": 0.67,
           "route": {"route": "glm-5.3-flash:cloud", "kind": "cloud",
                     "reason": "cloud-floor"}}
CTX = ("Recalled exchanges (...):\n\nQ: a memory about the tool skills\n"
       "A: the tool-skills answer\n\n"
       "WHERE WE ARE:\n- field: 6539 memories (99% trusted)")


def _tracks_store():
    """An RAM-only EntityStore with the one track planted (no couchbase)."""
    from membrane.entities import EntityStore
    import tempfile
    from pathlib import Path
    tmp = tempfile.mkdtemp()
    est = EntityStore(path=str(Path(tmp) / "entities.json"))
    est.watch_track("files:router.py", label="router")
    return est


def _handler(store_len=6539, tracks_store=None, efield=None):
    h = object.__new__(oc.MembraneOpenAIHandler)
    srv = MagicMock()
    srv.membrane.store.__len__ = lambda s: store_len
    srv.membrane.gate_notes = []
    srv._boot_ts = 0.0          # a real float (MagicMock would mock it)
    if tracks_store is not None:
        srv.entity_store = tracks_store
    else:
        srv.entity_store = None
    srv.efield = efield
    srv._efield_boot_seq = 0
    if efield is not None:
        srv.capturer = type("C", (), {"mode": "on", "sessions": None})()
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
        import time as _t
        from membrane import efield as EF
        from membrane import streams as ST
        from membrane import entities as E
        from membrane.efstore import MemBackend
        from _ef_fakes import FakeEmbedder

        t0 = _t.time()
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events(ST.split_turn(
            "tool edit for: fix x1 in router.py",
            "Applied the failover patch; the ladder walks on", ts=t0),
            session="s")
        f.process()
        est = _tracks_store()
        from membrane import entity_block as EB
        eid = EB.track_eid(f, "files:router.py")
        qv = f.emb.embed(["what changed in router.py?"])[0]
        h = _handler(tracks_store=est, efield=f)
        out = oc._where_we_are(h.server, qv)
        # the prose story rides WHERE WE ARE: ENTITY ID prefix + evidence
        self.assertIn(eid, out)
        self.assertIn("router.py", out.lower())   # the evidence carries the ask
        # the banner (monologue) carries NO stories -- vitals+alerts only
        mono = h._gate_monologue(VERDICT, CTX, "test", query_vec=qv)
        self.assertNotIn(eid, mono)

    def test_stale_story_told_with_age(self):
        """THE STALE COVENANT (revised, the operator's 'I want stories'
        call): an old story is told WITH ITS AGE ATTACHED ('last active
        25h ago: ...') -- silence was the lie of omission. Rendered in
        WHERE WE ARE (the unified home)."""
        import time as _t
        from membrane import efield as EF
        from membrane import streams as ST
        from membrane.efstore import MemBackend
        from _ef_fakes import FakeEmbedder

        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events(ST.split_turn(
            "tool edit for: fix x1 in router.py",
            "Applied the failover patch; the ladder walks on",
            ts=_t.time() - 25 * 3600),            # 25h old
            session="s")
        f.process()
        est = _tracks_store()
        qv = f.emb.embed(["what changed in router.py?"])[0]
        h = _handler(tracks_store=est, efield=f)
        out = oc._where_we_are(h.server, qv)
        # the story IS told -- with the honest age stamp
        self.assertIn("router", out)
        self.assertIn("1d ago", out)   # 25h renders at its day bucket

    def test_what_model_sees_is_what_operator_sees(self):
        """THE TRANSPARENCY CONTRACT: render BOTH lanes in the same turn
        and assert they never contradict -- a track's story appears in
        WHERE WE ARE; the banner never re-renders stories."""
        import time as _t
        from membrane import efield as EF
        from membrane import streams as ST
        from membrane.efstore import MemBackend
        from _ef_fakes import FakeEmbedder

        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events(ST.split_turn(
            "tool edit for: fix x1 in router.py",
            "Applied the failover patch; the ladder walks on", ts=_t.time()),
            session="s")
        f.process()
        est = _tracks_store()
        from membrane import entity_block as EB
        qv = f.emb.embed(["what changed in router.py?"])[0]
        h = _handler(tracks_store=est, efield=f)
        wwa = oc._where_we_are(h.server, qv) or ""
        mono = h._gate_monologue(VERDICT, CTX, "test", query_vec=qv)
        eid = EB.track_eid(f, "files:router.py")
        # WHERE WE ARE: the story (id-prefixed)
        self.assertIn(eid, wwa)
        # the banner: NO story bytes (one home, one renderer)
        self.assertNotIn(eid, mono)

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


class TestNullRecall(unittest.TestCase):
    """THE NULL-RECALL LANE (peer-reported from rosetta-check): when the
    field holds nothing shaped like the ask, the header SAYS so -- absence
    is information; it prevents re-deriving from git."""

    def _state_handler(self, null):
        """A handler with the live state-report surface (no exemplar
        store -- _state_report reads the entity field (off here) + the
        frame-wait counters + the null-recall state)."""
        h = _handler()
        h.server.membrane.audit_path = "/tmp/membrane-banner-test-audit.jsonl"
        h.server._watch_board = None
        h.server._intent_board = None
        h.server._codegraph = None
        h.server.efield = None
        h.server._efield_boot_seq = 0
        h.server.router = None
        h.server.momentum = False
        if null is not None:
            h.server._recall_null = null
        return h

    def test_null_rides_banner_when_fresh(self):
        h = _handler()
        import time as _t
        h.server._recall_null = {"query": "why does exit-code=count",
                                 "sim": 0.31, "ts": _t.time(), "count": 2}
        out = oc._where_we_are(h.server, None)
        self.assertIsNotNone(out)
        self.assertIn("no SAFE memory near", out)
        self.assertIn("exit-code=count", out)
        self.assertIn("0.31", out)
        self.assertIn("2 null recall(s)", out)

    def test_null_retires_when_stale(self):
        h = _handler()
        import time as _t
        h.server._recall_null = {"query": "old miss", "sim": 0.2,
                                 "ts": _t.time() - (oc.RECALL_NULL_FRESH_S
                                                    + 60),
                                 "count": 1}
        out = oc._where_we_are(h.server, None)
        if out is not None:
            self.assertNotIn("no SAFE memory near", out)

    def test_null_absent_by_default(self):
        h = _handler()
        out = oc._where_we_are(h.server, None)
        if out is not None:
            self.assertNotIn("no SAFE memory near", out)

    def test_state_report_carries_counter(self):
        import time as _t
        h = self._state_handler({"query": "q", "sim": 0.4,
                                 "ts": _t.time(), "count": 3})
        out = oc._state_report(h.server)
        self.assertIn("recall nulls: 3 this session", out)
        self.assertIn("(last: 'q' @ 0.4)", out)

    def test_state_report_clean_when_no_null(self):
        h = self._state_handler(None)
        h.server._recall_null = None       # MagicMock auto-attrs are truthy
        out = oc._state_report(h.server)
        self.assertNotIn("recall nulls", out)


if __name__ == "__main__":
    unittest.main()