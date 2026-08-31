"""P0c: recall hygiene at surface time.

The field may LEARN a broader class of turns than recall should SURFACE.
These tests pin the surfaced-side screen in membrane.rag:

- strip_machinery_text: <system-reminder>/<system-warning> spans dropped from
  the SURFACED text (stored cells keep their bytes), whitespace collapsed;
  empty when nothing but machinery remained;
- is_capture_junk: capture-lane cells (reasoning:/plan:/call/result/tool/
  delegated:/system-reminder) never surface; compact notes always do;
- echoes_transcript: a memory whose exchange already appears in the request's
  transcript is the conversation restating itself -- skipped;
- _memory_block end-to-end: a fake server + a real ExemplarStore with the
  session's actual junk specimens -> block contains only clean picks, the
  echo cell gone, the machinery span stripped, the compact note kept.

The junk specimens are REAL cells observed 2026-08-31: a plan->build handoff
turn ("can you use tools?" + a system-reminder, answered "Yes, I can use
tools.") learned verbatim and served back as recall.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import rag as R
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
    def __init__(self, store):
        self.membrane = _Mem(store)
        self.rag_ctx_chars = 4000
        self.rag_k = 3
        self.rag_mem_chars = 200
        self.rag_sim_min = 0.0
        self.rag_mmr_sim = 0.99
        self.momentum = False
        self.global_field = None
        self.momentum_vec = None
        self.momentum_alpha = 0.0


class _Rag:
    """Just the methods under test, bound to a fake server."""
    _memory_block = R.RAGProxyHandler._memory_block
    _momentum_skew = R.RAGProxyHandler._momentum_skew
    _global_top_up = R.RAGProxyHandler._global_top_up


# ---- strip_machinery_text ---------------------------------------------------

class TestStripMachinery(unittest.TestCase):
    def test_system_reminder_span_removed(self):
        t = R.strip_machinery_text(
            "can you use tools? <system-reminder>mode changed to build</system-reminder>")
        self.assertEqual(t, "can you use tools?")

    def test_multiline_span_removed(self):
        t = R.strip_machinery_text(
            "Q text\n<system-reminder>\nline1\nline2\n</system-reminder>\nmore")
        self.assertEqual(t, "Q text more")

    def test_warning_span_removed_case_insensitive(self):
        t = R.strip_machinery_text("keep me <SYSTEM-WARNING>x y z</SYSTEM-WARNING>")
        self.assertEqual(t, "keep me")

    def test_plain_text_untouched(self):
        t = R.strip_machinery_text("  spaces   collapse,  rest stays  ")
        self.assertEqual(t, "spaces collapse, rest stays")

    def test_only_machinery_becomes_empty(self):
        self.assertEqual(R.strip_machinery_text("<system-reminder>x</system-reminder>"), "")

    def test_empty_safe(self):
        self.assertEqual(R.strip_machinery_text(""), "")


# ---- is_capture_junk --------------------------------------------------------

class TestCaptureJunk(unittest.TestCase):
    def test_capture_prefixes_junk(self):
        for s in ("reasoning: I should look at the file",
                  "plan: 1. do x 2. do y",
                  "call bash for: fix the test -> bash(args)",
                  "result bash for: fix\ntools: {...}",
                  "tool edit for: patching file",
                  "delegated: analyze the field",
                  "system-reminder operational mode changed"):
            self.assertTrue(R.is_capture_junk(s), s)

    def test_compact_note_surfaces(self):
        self.assertFalse(R.is_capture_junk("compact note: testing discipline"))

    def test_prose_surfaces(self):
        self.assertFalse(R.is_capture_junk("how do I run the tests here"))

    def test_the_live_specimen_is_junk(self):
        # the actual cell learned from this session's plan->build handoff
        self.assertTrue(R.is_capture_junk("system-reminder: mode changed to build"))

    def test_inline_notjunk(self):
        # only the SITUATION head is screened -- a quoted prefix mid-prose stays
        self.assertFalse(R.is_capture_junk("what does the plan: prefix mean"))


# ---- echoes_transcript ------------------------------------------------------

class TestEchoTranscript(unittest.TestCase):
    def _idx(self, msgs):
        return R._transcript_index(msgs)

    def test_exact_pair_echo(self):
        tt, tp = self._idx([
            {"role": "user", "content": "can you use tools?"},
            {"role": "assistant", "content": "Yes, I can use tools."},
        ])
        self.assertTrue(R.echoes_transcript("can you use tools?",
                                            "yes, i can use tools.", tt, tp))

    def test_different_exchange_not_echo(self):
        tt, tp = self._idx([
            {"role": "user", "content": "can you use tools?"},
            {"role": "assistant", "content": "Yes, I can use tools."},
        ])
        self.assertFalse(R.echoes_transcript("how do I run tests",
                                             "python -m unittest", tt, tp))

    def test_token_overlap_echo(self):
        tt, tp = self._idx([
            {"role": "user", "content": "restart the proxy on port 11500"},
            {"role": "assistant", "content": "done."},
        ])
        self.assertTrue(R.echoes_transcript(
            "restart the proxy on port 11500", "anything", tt, tp))

    def test_machinery_stripped_before_match(self):
        # the live specimen: reminder span rides INSIDE the user turn
        tt, tp = self._idx([
            {"role": "user", "content":
                "can you use tools? <system-reminder>build mode on</system-reminder>"},
            {"role": "assistant", "content": "Yes, I can use tools."},
        ])
        self.assertTrue(R.echoes_transcript("can you use tools?",
                                            "yes, i can use tools.", tt, tp))

    def test_partial_overlap_below_bar(self):
        tt, tp = self._idx([
            {"role": "user", "content": "restart the proxy please"},
            {"role": "assistant", "content": "ok"},
        ])
        self.assertFalse(R.echoes_transcript(
            "restart the compactor instead", "anything", tt, tp))

    def test_empty_transcript(self):
        tt, tp = R._transcript_index([])
        self.assertFalse(R.echoes_transcript("anything", "x", tt, tp))


# ---- _memory_block end-to-end with the session's real specimens -------------

class TestMemoryBlockHygiene(unittest.TestCase):
    def _rag(self, store):
        r = _Rag()
        r.server = _Srv(store)
        return r

    def _field_with_specimens(self):
        store = _Store()
        q_echo = "can you use tools?"
        a_echo = "Yes, I can use tools."
        # 1. the live junk cell: machinery echo riding a chat turn (learned
        #    verbatim during this session's plan->build handoff)
        store.append(Exemplar(
            situation=q_echo + " <system-reminder>Your operational mode has "
                      "changed from plan to build.</system-reminder>",
            action=a_echo, outcome="teed-pair", vec=list(_vec(q_echo))))
        # 2. a legit compact note (the compactor's distillate)
        store.append(Exemplar(
            situation="compact note: testing discipline",
            action="Run .venv/bin/python -m unittest discover -s tests; keep "
                   "new tests beside the module they pin.",
            outcome="compact-note", vec=list(_vec("compact note testing discipline"))))
        # 3. another machinery-y cell (delegated lane, previously leaked)
        store.append(Exemplar(
            situation="delegated: analyze the core memory machinery",
            action="explored; findings recorded", outcome="teed-pair",
            vec=list(_vec("delegated analyze core memory machinery"))))
        # 4. a real prose memory
        store.append(Exemplar(
            situation="how do I run membrane tests",
            action=".venv/bin/python -m unittest discover -s tests",
            outcome="teed-pair", model="glm-5.3-flash:cloud",
            vec=list(_vec("how do I run membrane tests"))))
        return store, q_echo

    def test_block_excludes_junk_strips_spans_keeps_notes(self):
        store, q_echo = self._field_with_specimens()
        r = self._rag(store)
        # the live shape: the echo pair IS in the recent transcript (that is
        # when the echo cell's restatement fires); the prose memory's exchange
        # is NOT in the transcript, so it must still surface.
        msgs = [{"role": "user", "content": q_echo + " <system-reminder>mode: build</system-reminder>"},
                {"role": "assistant", "content": "Yes, I can use tools."},
                {"role": "user", "content": "run the test suite for me"}]
        block = r._memory_block(_vec("run the tests"), messages=msgs)
        self.assertIsNotNone(block)
        self.assertNotIn("can you use tools?", block)      # echo cell: gone
        self.assertNotIn("delegated:", block)              # capture lane: gone
        self.assertNotIn("system-reminder", block)         # span: stripped
        self.assertIn("compact note: testing discipline", block)  # notes: kept
        self.assertIn("unittest discover", block)          # real memory: kept
        self.assertIn("[via glm-5.3-flash:cloud]", block)  # provenance: kept

    def test_block_echo_screen_on_transcript_pair(self):
        store, q_echo = self._field_with_specimens()
        r = self._rag(store)
        block = r._memory_block(
            _vec(q_echo),
            messages=[{"role": "user", "content": q_echo + " <system-reminder>x y z</system-reminder>"},
                      {"role": "assistant", "content": "Yes, I can use tools."}])
        self.assertTrue(block is None or q_echo not in block)


if __name__ == "__main__":
    unittest.main()