"""THE STATELESS LLM (the membrain upgrade: --no-context): the field is
the only memory. The strip keeps the system prompts + the current
turn's exchanges (the turn boundary), drops the transcript history; the
reconstruction block rebuilds coherence from the field (the last N
learned turns, bounded); the recall budget scales generously."""

import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from membrane.opencode import (_strip_context, _reconstruct_history,
                               _elide_tool_tail)
from membrane.field import Exemplar, ExemplarStore


def _vec(w: str, dim: int = 32) -> np.ndarray:
    v = np.zeros(dim)
    for i, tok in enumerate(w.split()):
        v[i % dim] += 1.0 + (hash((tok, i)) % 97) / 97.0
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


class _Store(ExemplarStore):
    def save(self, path=None):
        pass


class _Mem:
    def __init__(self, store):
        self.store = store


class _Srv:
    def __init__(self, store, no_context: bool):
        self.membrane = _Mem(store)
        self.no_context = no_context
        self.rag_ctx_chars = 2000
        self.rag_k = 2
        self.rag_mem_chars = 200
        self.rag_sim_min = 0.0
        self.rag_mmr_sim = 0.90
        self.momentum = False
        self.global_field = None
        self.momentum_vec = None
        self.momentum_alpha = 0.0


class _Rag:
    """Just the methods under test, bound to a fake server."""
    from membrane import rag as R
    _memory_block = R.RAGProxyHandler._memory_block
    _momentum_skew = R.RAGProxyHandler._momentum_skew
    _global_top_up = R.RAGProxyHandler._global_top_up

    def __init__(self, store, no_context: bool):
        self.server = _Srv(store, no_context)


class TestStrip(unittest.TestCase):
    def test_keeps_system_and_turn_boundary(self):
        """The strip: the system message(s) + everything from the last
        real user message forward -- the history between drops."""
        msgs = [
            {"role": "system", "content": "the tools + persona"},
            {"role": "user", "content": "old question 1"},
            {"role": "assistant", "content": "old answer 1"},
            {"role": "user", "content": "old question 2"},
            {"role": "assistant", "content": "old answer 2"},
            {"role": "user", "content": "the current ask"},
        ]
        out = _strip_context(msgs)
        # THE TURN BOUNDARY: the system rides + the last TWO exchanges
        # (the amnesia fix: a chat follow-up keeps the previous reply --
        # 'old question 2'/'old answer 2' + 'the current ask'); the deeper
        # history ('old question 1') drops (the field is the only memory)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["content"], "the tools + persona")
        self.assertEqual(out[-1]["content"], "the current ask")
        self.assertNotIn("old question 1", [m.get("content")
                                            for m in out])

    def test_tool_exchanges_ride_the_turn(self):
        """The current turn's tool exchanges (the assistant's tool_calls +
        the tool results) ride with the turn boundary (the agent loop's
        state survives the strip)."""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "ancient history"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "do the thing"},
            {"role": "assistant", "content": "working",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "bash",
                                          "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
            {"role": "user", "content": "and now this"},
        ]
        out = _strip_context(msgs)
        # the boundary is the last real user text: 'ancient history'
        # drops; 'do the thing' + the tool loop + 'and now this' stay
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["content"], "sys")
        self.assertIn("do the thing", [m.get("content") for m in out])
        self.assertIn("and now this", [m.get("content") for m in out])


class TestReconstruct(unittest.TestCase):
    def test_renders_last_n_learned_turns_time_ordered(self):
        import time as _t
        st = ExemplarStore()
        now = _t.time()
        E = Exemplar
        for i in range(10):
            ex = E(situation=f"question {i}", action=f"answer {i}",
                   outcome="pending", vec=[0.0] * 1024)
            ex.safe = True
            ex.extra = {"eph_born": now - (60 * (9 - i))}
            st.exemplars.append(ex)
        block = _reconstruct_history(st, n=3)
        # the last 3 turns (7, 8, 9), oldest first
        self.assertIn("question 7", block)
        self.assertIn("question 9", block)
        self.assertNotIn("question 4", block)
        self.assertLess(block.index("question 7"),
                        block.index("question 9"))

    def test_budget_bounds_the_block(self):
        import time as _t
        st = ExemplarStore()
        now = _t.time()
        E = Exemplar
        for i in range(6):
            ex = E(situation=f"q {i}", action="A" * 500,
                   outcome="pending", vec=[0.0] * 1024)
            ex.safe = True
            ex.extra = {"eph_born": now - (60 * (5 - i))}
            st.exemplars.append(ex)
        block = _reconstruct_history(st, n=6, budget=1500)
        self.assertLessEqual(len(block or ""), 1550)

    def test_stamp_less_cells_survive_between_stamped_neighbors(self):
        """The eph_born=0 fallback: a capture whose clock didn't stick used
        to vanish from the recon entirely. Capture order IS time order, so
        the birth interpolates from the nearest stamped neighbors -- the
        stamp-less cell rides its neighborhood into the session window."""
        import time as _t
        st = ExemplarStore()
        now = _t.time()
        stamped = {"eph_born": now - 60}
        # index 0: stamped slightly older; index 1: NO stamp (the victim);
        # index 2: fresh stamped. The gap is deliberately small so the
        # interpolation lands INSIDE the 6h window (a wide gap would
        # legitimately interpolate outside it -- the window is the contract).
        e0 = Exemplar(situation="old stamped question",
                      action="old stamped answer with enough words",
                      outcome="pending", vec=[0.0] * 1024)
        e0.extra = {"eph_born": now - 120}        # inside the 6h window
        e1 = Exemplar(situation="stamp less question",
                      action="stamp less answer with enough words",
                      outcome="pending", vec=[0.0] * 1024)
        e2 = Exemplar(situation="fresh stamped question",
                      action="fresh stamped answer with enough words",
                      outcome="pending", vec=[0.0] * 1024)
        e2.extra = dict(stamped)
        for e in (e0, e1, e2):
            e.safe = True
            st.exemplars.append(e)
        block = _reconstruct_history(st, n=3)
        self.assertIsNotNone(block)
        self.assertIn("stamp less question", block or "")

    def test_stamp_less_field_stays_empty(self):
        """A field with NO stamps at all has no recency evidence: the recon
        stays empty (no invented freshness)."""
        st = ExemplarStore()
        for i in range(3):
            ex = Exemplar(situation=f"no clock question {i}",
                          action=f"no clock answer {i} with padding words",
                          outcome="pending", vec=[0.0] * 1024)
            ex.safe = True
            st.exemplars.append(ex)
        self.assertIsNone(_reconstruct_history(st, n=3))

    def test_recon_skips_capture_junk_and_strips_spans(self):
        """The recon's recall hygiene (the live leak: capture-lane cells --
        reasoning:/plan:/tool-for: -- were learned verbatim AND rendered
        into the coherence block, because _memory_block's surface screens
        never ran here)."""
        import time as _t
        st = ExemplarStore()
        now = _t.time()
        junk = Exemplar(
            situation="reasoning: the word <system-reminder>mode changed"
                      " to build</system-reminder>",
            action="plan: do x then y", outcome="pending",
            vec=list(_vec("reasoning plan mode")))
        junk.extra = {"eph_born": now - 60}
        junk.safe = True
        clean = Exemplar(situation="a perfectly clean question",
                         action="a perfectly clean answer, plenty of words",
                         outcome="pending", vec=list(_vec("clean question")))
        clean.extra = {"eph_born": now - 30}
        clean.safe = True
        st.exemplars.extend([junk, clean])
        block = _reconstruct_history(st, n=6)
        self.assertIsNotNone(block)
        self.assertNotIn("reasoning:", block or "")
        self.assertNotIn("system-reminder", block or "")
        self.assertIn("a perfectly clean question", block or "")


# ---- the k-compensation regression (the pick cap rides k_eff) ---------------

class TestNoContextPickCap(unittest.TestCase):
    """THE COMPENSATION BUG: --no-context promises 'k x8' -- but the pick
    loop capped at plain srv.rag_k, so the x8 only deepened the candidate
    pool while the injected count stayed small: the stateless LLM was
    starved of exactly the memory the compensation promised it."""

    def _field(self, count: int) -> ExemplarStore:
        st = _Store()
        for i in range(count):
            # one-hot-ish on its own axis: pairwise cosine ~0 so the MMR
            # filter never kills a second pick (the pick cap is what's under
            # test, not the dedup)
            v = [0.0] * 1024
            v[i * 3] = 1.0
            v[i * 3 + 1] = 0.5
            st.append(Exemplar(
                situation=f"distinct useful question number {i} about "
                          f"component {i}",
                action=f"distinct useful answer number {i} with real "
                       f"knowledge about component {i}",
                outcome="teed-pair",
                vec=v))
        return st

    def _query(self, st: ExemplarStore) -> np.ndarray:
        # the query rides the field's own axes, WITH a small boost so every
        # cell ties ABOVE the no-context floor (the floor is tested on its
        # own; the PICK CAP is what separates cells here, not the ranking)
        V = np.array([e.vec for e in st.exemplars], dtype=float)
        q = V.mean(axis=0) * 1.0 + V.sum(axis=0) * 0.05
        return q / (np.linalg.norm(q) + 1e-12)

    def test_no_context_recalls_beyond_plain_k(self):
        # 3 cells: the equal-tie query sits at 1/sqrt(3) ~ 0.577 -- above
        # the 0.55 floor (6 orthogonal axes would cap the tie at 0.408,
        # under the floor -- geometry, not the cap)
        st = self._field(3)
        r = _Rag(st, no_context=True)               # rag_k=2 -> k_eff=8
        block = r._memory_block(self._query(st), messages=[])
        self.assertIsNotNone(block)
        picked = ((block or "").count("Memory (sim ")
                  + (block or "").count("situation: ")
                  - (block or "").count("[situation: "))
        # the OLD bug pinned this at rag_k=2; the compensation carries 3
        self.assertGreaterEqual(picked, 3,
                                f"no-context picked {picked} < 3")

    def test_transcript_mode_keeps_plain_k(self):
        st = self._field(6)
        r = _Rag(st, no_context=False)              # k_eff == rag_k == 2
        block = r._memory_block(self._query(st), messages=[])
        self.assertIsNotNone(block)
        picked = (block or "").count("Memory (sim ") + \
            (block or "").count("Q: ")
        self.assertLessEqual(picked, 2,
                             f"transcript mode picked {picked} > k")

    def test_no_context_floor_shrinks_instead_of_padding(self):
        """THE HONEST FLOOR: a vague query whose best evidence sits below
        the floor gets NOTHING instead of a junk recency fill (in the
        field-only mode, junk recall is the model's only history)."""
        # a field of unrelated cells; the query is orthogonal to all
        st = _Store()
        for i in range(4):
            v = [0.0] * 1024
            v[i * 3] = 1.0
            st.append(Exemplar(
                situation=f"totally unrelated topic {i} about mqtt",
                action=f"totally unrelated answer {i} about mosquitto",
                outcome="teed-pair", vec=v))
        q = [0.0] * 1024
        q[500] = 1.0                    # orthogonal to every cell
        r = _Rag(st, no_context=True)
        block = r._memory_block(np.asarray(q, dtype=float), messages=[])
        # the floor (0.55) vs these cells' ~0.0 sims: NOTHING pads the block
        self.assertIsNone(block)


# ---- Phase 1: the capture-time machinery strip ------------------------------

class TestCaptureStrip(unittest.TestCase):
    """THE POISON-AT-BIRTH FIX: opencode's system-reminder spans used to be
    stored VERBATIM in the cell's situation -- a stale '# Plan Mode ACTIVE'
    re-armed as live instruction whenever the field-only mode served it.
    learn_pair now strips the situation at birth (before the embed); the
    audit keeps the raw bytes."""

    def _mem(self):
        from membrane.runtime import Membrane, StubResponder

        class _Emb:
            def embed(self, texts):
                out = []
                for t in texts:
                    v = np.zeros(1024)
                    for i, w in enumerate((t or "").split()):
                        v[i % 1024] += 1.0 + (hash((w, i)) % 97) / 97.0
                    n = float(np.linalg.norm(v))
                    out.append(v / n if n > 1e-12 else v)
                return out
        # a tempdir, never ":memory:" -- that is a literal file in the cwd
        # the lab-isolation guard does not recognize (it joined the live
        # shared substrate and left :memory:.* files in the repo root)
        td = tempfile.mkdtemp(prefix="membrane-test-")
        self.addCleanup(shutil.rmtree, td, True)
        mem = Membrane(StubResponder(), field_path=os.path.join(td, "field.json"),
                       audit_path=os.path.join(td, "audit.jsonl"), embedder=_Emb())
        # THE ISOLATION (the lab-isolation lesson): the constructor's store
        # may have auto-detected the shared Couchbase substrate -- swap in a
        # RAM-only store so the test never touches shared memories.
        mem.store = _Store()
        return mem

    def test_reminder_span_stripped_at_capture(self):
        mem = self._mem()
        idx = mem.learn_pair(
            "could you plan first? <system-reminder>Plan mode ACTIVE"
            " -- read-only</system-reminder>",
            "the plan text")
        self.assertIsNotNone(idx)
        self.assertNotIn("<system-reminder>",
                         mem.store.exemplars[idx].situation)
        self.assertIn("could you plan first?",
                      mem.store.exemplars[idx].situation)

    def test_machinery_only_situation_writes_nothing(self):
        mem = self._mem()
        idx = mem.learn_pair(
            "<system-reminder>mode changed to build</system-reminder>",
            "an answer nobody should learn from machinery alone")
        self.assertIsNone(idx)
        self.assertEqual(len(mem.store.exemplars), 0)


# ---- Phase 2b: the recon's stamp tiebreak -----------------------------------

class TestReconStampTiebreak(unittest.TestCase):
    def test_stamped_cell_wins_the_near_tie(self):
        import time as _t
        st = ExemplarStore()
        now = _t.time()
        base = now - 60
        bare = Exemplar(situation="a bare recency question",
                        action="a bare recency answer with plenty words",
                        outcome="pending", vec=[0.0] * 1024)
        bare.extra = {"eph_born": base - 1.0}
        bare.safe = True
        stamped = Exemplar(situation="a stamped provenance question",
                           action="a stamped provenance answer, ample text",
                           outcome="pending", vec=[0.0] * 1024)
        stamped.extra = {"eph_born": base - 2.0, "why": "the design reason"}
        stamped.safe = True
        st.exemplars.extend([bare, stamped])
        block = _reconstruct_history(st, n=1)
        # the near-tie (1s apart): the stamped cell takes the slot
        self.assertIsNotNone(block)
        self.assertIn("a stamped provenance question", block or "")


def _loop(n_tools: int, size: int = 2000) -> list:
    """A system prompt, the turn's ask, then n tool exchanges with big
    results and a big edit argument each."""
    msgs = [{"role": "system", "content": "you are opencode"},
            {"role": "user", "content": "refactor the gate"}]
    for i in range(n_tools):
        args = json.dumps({"filePath": f"f{i}.py",
                           "newString": "x" * size})
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "edit",
                                                  "arguments": args}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": f"result {i} " + "y" * size})
    return msgs


class TestToolElision(unittest.TestCase):
    """--no-context's in-turn half: old tool traffic shrinks, the recent
    exchanges and the call/result structure survive."""

    def test_old_results_shrink_recent_stay(self):
        msgs = _loop(6)
        out, saved = _elide_tool_tail(msgs, keep_last=3)
        tools = [m for m in out if m["role"] == "tool"]
        self.assertTrue(all("elided" in m["content"] for m in tools[:3]))
        self.assertEqual([m["content"] for m in tools[3:]],
                         [m["content"] for m in msgs if m["role"] == "tool"][3:])
        self.assertGreater(saved, 3 * 1500)

    def test_pairing_and_order_survive(self):
        msgs = _loop(6)
        out, _ = _elide_tool_tail(msgs, keep_last=2)
        self.assertEqual([m["role"] for m in out], [m["role"] for m in msgs])
        ids = [tc["id"] for m in out if m.get("tool_calls")
               for tc in m["tool_calls"]]
        res = [m["tool_call_id"] for m in out if m["role"] == "tool"]
        self.assertEqual(ids, res)

    def test_old_args_stay_valid_json_small_fields_whole(self):
        out, _ = _elide_tool_tail(_loop(4), keep_last=1)
        first = json.loads(out[2]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(first["filePath"], "f0.py")
        self.assertIn("elided", first["newString"])
        last = json.loads(out[-2]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(last["newString"], "x" * 2000)   # recent: verbatim

    def test_small_traffic_and_disabled_are_untouched(self):
        msgs = _loop(6, size=100)
        self.assertEqual(_elide_tool_tail(msgs, keep_last=1), (msgs, 0))
        big = _loop(6)
        self.assertEqual(_elide_tool_tail(big, keep_last=-1), (big, 0))

    def test_input_is_never_mutated(self):
        msgs = _loop(5)
        snap = json.dumps(msgs)
        _elide_tool_tail(msgs, keep_last=1)
        self.assertEqual(json.dumps(msgs), snap)


if __name__ == "__main__":
    unittest.main()
