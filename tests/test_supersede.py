"""W2a: supersede-on-confirm (stale-cell defense).

A fresh answer that conflicts with a live SAFE canon dup is a correction,
not a re-statement. The old dedup covenant ("keep first-seen") lost the
correction AND left the stale cell live in recall. Now:

- the correction is learned as a fresh CANDIDATE (the tier's evidence
  machinery polices it -- use confirms, refute/expire dangles the link);
- the old cell is linked out of LIVE recall (extra["superseded_by"] ->
  the successor's CONTENT key, stable across compactor renumbering);
- the link is SELF-HEALING: a refuted (durable-unsafe), forgotten, or
  expired successor dangles the link and the predecessor returns to
  live recall;
- the superseded row stays in the store (append-only for earned history)
  and stays findable by archaeology lanes (default top_k), but drops out
  of LIVE recall (live_only=True, recall_merged, the gate, chat rag).
"""

import math
import sys
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.field import Exemplar, ExemplarStore


def _vec(seed: float) -> np.ndarray:
    v = np.asarray([math.cos(seed), math.sin(seed), 0.3], dtype=float)
    return v / np.linalg.norm(v)


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class _Emb:
    """Unit 1024-dim embeddings via a deterministic token hash."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(1024)
            for i, w in enumerate((t or "").split()):
                v[i % 1024] += 1.0 + (hash((w, i)) % 97) / 97.0
            n = float(np.linalg.norm(v))
            out.append(v / n if n > 1e-12 else v)
        return out


class TestSupersedeLink(unittest.TestCase):
    def _store(self, *exs):
        s = _Store()
        for e in exs:
            s.append(e)
        return s

    def test_link_hides_from_live_recall_only(self):
        old = Exemplar(situation="deploy cmd?", action="deploy prod --force",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        new = Exemplar(situation="deploy cmd?", action="deploy prod --check",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        s = self._store(old, new)
        s.mark_superseded(0, by_key=s.content_key("deploy cmd?",
                                                  "deploy prod --check"),
                          by_text="deploy prod --check", how="learn-dedup")
        # live recall hides the old cell
        self.assertEqual([e.action for e, _ in s.top_k(_vec(1.0), k=5,
                                                       live_only=True)],
                         ["deploy prod --check"])
        # archaeology (default top_k) still sees both
        self.assertEqual(len(s.top_k(_vec(1.0), k=5)), 2)
        # merged recall hides it too
        self.assertEqual([e.action for e, _ in s.recall_merged(_vec(1.0), k=5)],
                         ["deploy prod --check"])

    def test_refuted_successor_dangles_link(self):
        old = Exemplar(situation="q?", action="old answer",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        new = Exemplar(situation="q?", action="new answer",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        s = self._store(old, new)
        s.mark_superseded(0, by_key=s.content_key("q?", "new answer"),
                          by_text="new answer", how="learn-dedup")
        self.assertTrue(s.is_superseded(s.exemplars[0]))
        # the environment refutes the correction: durable-unsafe
        new.safe = False
        self.assertFalse(s.is_superseded(s.exemplars[0]))    # link dangles
        self.assertEqual(len(s.top_k(_vec(1.0), k=5, live_only=True)), 2)

    def test_forgotten_successor_dangles_link(self):
        old = Exemplar(situation="q?", action="old answer",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        new = Exemplar(situation="q?", action="new answer",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        s = self._store(old, new)
        s.mark_superseded(0, by_key=s.content_key("q?", "new answer"))
        self.assertTrue(s.is_superseded(s.exemplars[0]))
        s.forget([1])                       # the correction is deleted
        self.assertFalse(s.is_superseded(s.exemplars[0]))
        # the predecessor serves again (the successor is gone entirely)
        live = s.top_k(_vec(1.0), k=5, live_only=True)
        self.assertEqual([e.action for e, _ in live], ["old answer"])

    def test_candidate_successor_holds_link(self):
        """A live candidate correction exists (just not confirmed yet): the
        old cell stays hidden while the correction awaits evidence."""
        old = Exemplar(situation="q?", action="old answer",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        new = Exemplar(situation="q?", action="new answer",
                       outcome="pending", vec=list(_vec(1.0)), safe=True)
        new.ephemeral = True
        new.extra = {"eph_born": 0.0, "eph_ttl": 3 * 86400}
        s = self._store(old, new)
        s.mark_superseded(0, by_key=s.content_key("q?", "new answer"),
                          by_text="new answer", how="learn-dedup")
        self.assertTrue(s.is_superseded(s.exemplars[0]))
        # the candidate still recallable on the merged surface (which sees
        # candidates); the live CANON surface (canon_only=True) is empty --
        # a candidate is never replayed
        self.assertEqual([e.action for e, _ in
                          s.recall_merged(_vec(1.0), k=5)], ["new answer"])
        self.assertEqual(s.top_k(_vec(1.0), k=5, canon_only=True, live_only=True),
                         [])
        # a REFUTED candidate revives the predecessor
        new.ephemeral = False
        new.safe = False
        self.assertFalse(s.is_superseded(s.exemplars[0]))

    def test_chain_unwinds_level_by_level(self):
        a = Exemplar(situation="q?", action="A", outcome="pending",
                     vec=list(_vec(1.0)), safe=True)
        b = Exemplar(situation="q?", action="B", outcome="pending",
                     vec=list(_vec(1.0)), safe=True)
        c = Exemplar(situation="q?", action="C", outcome="pending",
                     vec=list(_vec(1.0)), safe=True)
        s = self._store(a, b, c)
        s.mark_superseded(0, by_key=s.content_key("q?", "B"), how="x")
        s.mark_superseded(1, by_key=s.content_key("q?", "C"), how="x")
        # A->B->C: only C is live (unsafe/demoted cells still RECALL --
        # they just never replay; supersede is not the demotion path)
        self.assertEqual([e.action for e, _ in
                          s.top_k(_vec(1.0), k=5, live_only=True)], ["C"])
        c.safe = False                       # C refuted -> B returns (A stays)
        self.assertEqual(sorted(e.action for e, _ in
                          s.top_k(_vec(1.0), k=5, live_only=True)),
                         ["B", "C"])         # tie-order among equal vecs is argsort's
        b.safe = False                       # B refuted -> A returns
        self.assertEqual(len(s.top_k(_vec(1.0), k=5, live_only=True)), 3)

    def test_zero_cost_when_no_links(self):
        s = self._store(Exemplar(situation="q?", action="a", outcome="pending",
                                 vec=list(_vec(1.0)), safe=True))
        self.assertEqual(s._superseded_set(), frozenset())

    def test_key_survives_renumbering(self):
        """The link is a CONTENT key: dropping another cell (compactor
        renumbering) must not break it."""
        filler = Exemplar(situation="filler?", action="f", outcome="pending",
                          vec=list(_vec(2.0)), safe=True)
        old = Exemplar(situation="q?", action="old", outcome="pending",
                       vec=list(_vec(1.0)), safe=True)
        new = Exemplar(situation="q?", action="new", outcome="pending",
                       vec=list(_vec(1.0)), safe=True)
        s = self._store(filler, old, new)
        s.mark_superseded(1, by_key=s.content_key("q?", "new"), how="x")
        s.forget([0])                        # renumber: old moves 1 -> 0
        self.assertTrue(s.is_superseded(s.exemplars[0]))   # old, still linked
        self.assertFalse(s.is_superseded(s.exemplars[1]))  # new, the successor


class TestLearnDedupSupersede(unittest.TestCase):
    """The runtime hook: a conflicting learn on a safe canon dup supersedes."""

    def _mem(self):
        from membrane.runtime import Membrane, StubResponder
        # a tempdir, never ":memory:" -- that is a literal file in the cwd
        # the lab-isolation guard does not recognize (it joined the live
        # shared substrate and left :memory:.* files in the repo root)
        td = tempfile.mkdtemp(prefix="membrane-test-")
        self.addCleanup(shutil.rmtree, td, True)
        mem = Membrane(StubResponder(), field_path=os.path.join(td, "field.json"),
                       audit_path=os.path.join(td, "audit.jsonl"), embedder=_Emb(),
                       ephemeral=True)
        mem.store = _Store()
        return mem

    def test_conflicting_learn_supersedes_stale_canon(self):
        mem = self._mem()
        q = "what port does the viewer use"
        e = _Emb().embed([q])[0]
        mem.store.append(Exemplar(situation=q, action="port 8080",
                                  outcome="pending", vec=list(e), safe=True))
        resp = {"message": {"content": "port 9090"}}
        verdict = {"verdict": "escalate", "sim": 0.99, "reason": "t"}
        idx = mem._learn(q, e, verdict, resp, unsafe=False)
        # the correction was learned (as a candidate), the old cell linked out
        self.assertIsNotNone(idx)
        new = mem.store.exemplars[idx]
        self.assertTrue(new.ephemeral)
        self.assertEqual(mem.store._supersede_link(mem.store.exemplars[0]),
                         mem.store.content_key(q, "port 9090"))
        self.assertTrue(mem.store.is_superseded(mem.store.exemplars[0]))
        # the canon live surface skips the stale cell (the candidate is not
        # canon, so nothing is replayable until the correction earns trust)
        nbrs = mem.store.top_k(e, k=5, canon_only=True, live_only=True)
        self.assertEqual(nbrs, [])
        # merged recall serves the correction
        merged = mem.store.recall_merged(e, k=5)
        self.assertEqual([x.action for x, _ in merged], ["port 9090"])

    def test_rewording_does_not_supersede(self):
        """Token overlap >= 0.7: the same content, re-worded -- no supersede."""
        mem = self._mem()
        q = "what port does the viewer use"
        e = _Emb().embed([q])[0]
        mem.store.append(Exemplar(situation=q, action="use port 8080",
                                  outcome="pending", vec=list(e), safe=True))
        resp = {"message": {"content": "use the port 8080"}}
        verdict = {"verdict": "escalate", "sim": 0.99, "reason": "t"}
        idx = mem._learn(q, _Emb().embed([q])[0], verdict, resp, unsafe=False)
        # dedup merges (no new cell), the old cell stays live and unlinked
        self.assertEqual(idx, 0)
        self.assertFalse(mem.store.is_superseded(mem.store.exemplars[0]))
        self.assertEqual(len(mem.store), 1)

    def test_demoted_dup_takes_repair_not_supersede(self):
        mem = self._mem()
        q = "how to reset the cache"
        e = _Emb().embed([q])[0]
        bad = Exemplar(situation=q, action="rm -rf .git/objects",
                       outcome="pending", vec=list(e), safe=False)
        mem.store.append(bad)
        resp = {"message": {"content": "git reset --soft HEAD~1"}}
        idx = mem._learn(q, _Emb().embed([q])[0],
                         {"verdict": "escalate", "sim": 0.99, "reason": "t"},
                         resp, unsafe=False)
        self.assertEqual(idx, 0)             # merged into the repair
        self.assertFalse(mem.store.is_superseded(bad))

    def test_tier_off_keeps_first_seen(self):
        """Without the candidate tier there is no evidence path to police the
        successor -- the conservative keep-first-seen covenant holds."""
        from membrane.runtime import Membrane, StubResponder
        # a tempdir, never ":memory:" -- that is a literal file in the cwd
        # the lab-isolation guard does not recognize (it joined the live
        # shared substrate and left :memory:.* files in the repo root)
        td = tempfile.mkdtemp(prefix="membrane-test-")
        self.addCleanup(shutil.rmtree, td, True)
        mem = Membrane(StubResponder(), field_path=os.path.join(td, "field.json"),
                       audit_path=os.path.join(td, "audit.jsonl"), embedder=_Emb(),
                       ephemeral=False)
        mem.store = _Store()
        q = "what port does the viewer use"
        e = _Emb().embed([q])[0]
        mem.store.append(Exemplar(situation=q, action="port 8080",
                                  outcome="pending", vec=list(e), safe=True))
        idx = mem._learn(q, e, {"verdict": "escalate", "sim": 0.99,
                                "reason": "t"},
                         {"message": {"content": "port 9090"}}, unsafe=False)
        self.assertEqual(idx, 0)             # merged, keep-first-seen
        self.assertFalse(mem.store.is_superseded(mem.store.exemplars[0]))


if __name__ == "__main__":
    unittest.main()