import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

from membrane import momentum_map as MM


def st(kind, state, seed=None, eps=(), ts=1.0, surface=""):
    return NS(kind=kind, tier="live", state=np.array(state, dtype=np.float32),
              seed=None if seed is None else np.array(seed, dtype=np.float32),
              episodes=[(np.array(e, dtype=np.float32), 3) for e in eps], last_ts=ts, surface=surface)


class Map(unittest.TestCase):
    def setUp(self):
        MM._WORDS.update(t=0.0, M=None, names=[])

    def test_speed_and_heading(self):
        fld = NS(_states={
            "w:east": st("w", [1, 0], surface="east"), "w:north": st("w", [0, 1], surface="north"),
            "f:a.py": st("f", [1, 0], eps=[[0, 0]], ts=5.0),
            "person:op": st("p", [0, 1], seed=[0, 0.5], ts=4.0)})
        ls = MM.lines(fld)
        self.assertTrue(ls[0].startswith("f:a.py  ↗ 1.00  east"), ls)
        self.assertTrue(ls[1].startswith("person:op  ↗ 0.50  north"), ls)
        self.assertFalse(any(l.startswith("w:") for l in ls))

    def test_every_printed_id_resolves(self):
        class Est:
            known = {"membrane::goal:g1", "person:op"}

            def _resolve_spec(self, spec):
                return spec if spec in self.known else None
        fld = NS(_states={
            "w:gradle": st("w", [1, 0], surface="gradle"),
            "m:goal:g1": st("m", [1, 0], eps=[[0, 0]], ts=5.0),
            "m:session:now": st("m", [0, 1], seed=[0, 0], ts=6.0),      # nothing to read: left out
            "person:op": st("p", [0, 1], seed=[0, 0.5], ts=4.0)})
        ls = MM.lines(fld, Est())
        ids = [l.split()[0] for l in ls]
        self.assertEqual(ids, ["membrane::goal:g1", "person:op"])
        for i in ids:
            self.assertIsNotNone(Est()._resolve_spec(i))

    def test_rare_words_head_and_common_words_never(self):
        # #1105: no stop-list -- a word everyone says is simply not rare
        fld = NS(_states={
            "w:the": NS(**{**vars(st("w", [1, 0], surface="the")), "n": 10000}),
            "w:something": NS(**{**vars(st("w", [0.99, 0.14], surface="something")), "n": 500}),
            "w:gradle": NS(**{**vars(st("w", [0.9, 0.43], surface="gradle")), "n": 2}),
            "w:x1": NS(**{**vars(st("w", [0.95, 0.3], surface="x1")), "n": 100}),
            "f:a.py": st("f", [1, 0], eps=[[0, 0]], ts=5.0)})
        line = MM.lines(fld)[0]
        self.assertTrue(line.split("  ")[-1].startswith("gradle"), line)
        self.assertNotIn("the", line.split("  ")[-1].split(" · "))

    def test_rewrite_drops_history_and_banners(self):
        fld = NS(_states={})
        msgs = [{"role": "system", "content": "doctrine"}, {"role": "user", "content": "old"},
                {"role": "assistant", "content": "old reply"}, {"role": "system", "content": "BANNER"},
                {"role": "user", "content": "now"}, {"role": "assistant", "tool_calls": [1]},
                {"role": "tool", "content": "r"}]
        out = MM.rewrite(msgs, fld)
        self.assertEqual(out[0]["content"], "doctrine")
        self.assertIn("No chat history is sent", out[1]["content"])
        self.assertEqual([m["role"] for m in out[2:]], ["user", "assistant", "tool"])
        self.assertNotIn("BANNER", str(out))
        self.assertNotIn("old", str(out[2:]))

    def test_word_kind_identifiers_are_their_own_kind(self):
        for w in ("script_key", "mcp__membrane__entity_link", "MEMBRANE_JWT_SECRET", "tests/test_recall_banner_668.py",
                  "momentum_map.py", "membrane::goal", "dropped_why", "quantumEntity"):
            self.assertEqual(MM.word_kind(w), "identifier", w)
        for w in ("harbor", "weather", "anchorage", "Seattle", "follow-up", "liveaboard"):
            self.assertEqual(MM.word_kind(w), "prose", w)


def busy_code_field(chat_words, extra_moves=()):
    """#1105's scene: a field busy with code work (fast-moving files and symbols, moved only by tool output) and a
    chat whose turns moved its own things and words. chat_words: [(turn's words and things)]."""
    from membrane.movelog import MoveLog
    ml = MoveLog()
    states = {
        # machinery: fast, recent, moved by tool output only
        "f:tests/test_recall_banner_668.py": st("f", [0, 0, 1], eps=[[0, 0, 0]], ts=99.0),
        "s:?dropped_why": st("s", [0, 0.2, 0.9], eps=[[0, 0, 0]], ts=98.0),
        # the chat's own things: slower, older
        "m:goal:trip-north": st("m", [1, 0, 0], eps=[[0.7, 0.71, 0]], ts=10.0),
        "person:dmitrii": st("p", [0.9, 0.43, 0], seed=[0.8, 0.6, 0], ts=9.0),
        "m:decision:inside-route": st("m", [0.95, 0, 0.3], eps=[[0.8, 0.5, 0.3]], ts=8.0),
        # words: the code identifiers are field-rare and point the machinery's way and the chat's way
        "w:script_key": NS(**{**vars(st("w", [0.7, 0, 0.7], surface="script_key")), "n": 2}),
        "w:membrane_jwt_secret": NS(**{**vars(st("w", [0.75, 0, 0.66], surface="membrane_jwt_secret")), "n": 2}),
        "w:subprocess": NS(**{**vars(st("w", [0.6, 0, 0.8], surface="subprocess")), "n": 3}),
        "w:passage": NS(**{**vars(st("w", [0.8, -0.6, 0], surface="passage")), "n": 400}),
        "w:anchorage": NS(**{**vars(st("w", [0.85, -0.5, 0.1], surface="anchorage")), "n": 300}),
        "w:weather": NS(**{**vars(st("w", [0.7, -0.7, 0], surface="weather")), "n": 900}),
        "w:route": NS(**{**vars(st("w", [0.6, -0.8, 0], surface="route")), "n": 800}),
    }
    for i, things in enumerate(chat_words):
        k = f"turn{i}"
        for e in things:
            ml.note(k, e, 0.1)
        ml.place(k, "ses_sail", i + 1, True)
    for i, e in enumerate(("f:tests/test_recall_banner_668.py", "s:?dropped_why", "w:script_key",
                           "w:membrane_jwt_secret", "w:subprocess") + tuple(extra_moves)):
        ml.note(f"tool{i}", e, 0.3)                     # tool output in the SAME chat: not a turn
        ml.place(f"tool{i}", "ses_sail", 100 + i, False)
    return NS(_states=states, move_log=ml)


class Relevance(unittest.TestCase):
    """#1105: rows by relevance first, headings from the conversation's own vocabulary."""

    def setUp(self):
        MM._WORDS.update(t=0.0, M=None, names=[])

    SAIL = [("m:goal:trip-north", "w:passage", "w:weather", "w:route"),
            ("person:dmitrii", "m:decision:inside-route", "w:anchorage", "w:route", "w:weather"),
            ("m:goal:trip-north", "w:passage", "w:route", "w:weather")]

    def test_rows_the_conversation_moved_rank_first(self):
        fld = busy_code_field(self.SAIL)
        ids = [l.split()[0] for l in MM.lines(fld, chat="ses_sail")]
        self.assertEqual(set(ids[:3]), {"m:goal:trip-north", "person:dmitrii", "m:decision:inside-route"}, ids)
        # machinery is not banned, it ranks below
        self.assertIn("f:tests/test_recall_banner_668.py", ids[3:])

    def test_without_a_conversation_speed_still_ranks(self):
        fld = busy_code_field(self.SAIL)
        ids = [l.split()[0] for l in MM.lines(fld, chat="ses_other")]
        self.assertEqual(ids[0], "f:tests/test_recall_banner_668.py", ids)

    def test_rows_held_by_the_context_stack_rank_first(self):
        fld = busy_code_field([])
        ids = [l.split()[0] for l in MM.lines(fld, chat="ses_sail", held=["membrane::decision:inside-route"])]
        self.assertEqual(ids[0], "m:decision:inside-route", ids)

    def test_non_code_chat_headings_are_its_own_prose_words(self):
        fld = busy_code_field(self.SAIL)
        said = {"passage", "weather", "route", "anchorage"}
        for l in MM.lines(fld, chat="ses_sail"):
            heads = l.split("  ")[-1].split(" · ")
            self.assertTrue(heads and set(heads) <= said, l)
            for h in heads:
                self.assertEqual(MM.word_kind(h), "prose", l)

    def test_heading_rarity_is_within_the_conversation(self):
        # anchorage was said in one turn of three, route/weather in all: anchorage is the chat's rare word
        fld = busy_code_field(self.SAIL)
        line = [l for l in MM.lines(fld, chat="ses_sail") if l.startswith("m:decision:inside-route")][0]
        self.assertEqual(line.split("  ")[-1].split(" · ")[0], "anchorage", line)

    def test_code_chat_allows_identifiers(self):
        code = [("f:tests/test_recall_banner_668.py", "w:script_key", "w:subprocess", "w:route"),
                ("s:?dropped_why", "w:membrane_jwt_secret", "w:script_key")]
        fld = busy_code_field(code)
        lines = MM.lines(fld, chat="ses_sail")
        top = lines[0]
        self.assertTrue(top.startswith("f:tests/test_recall_banner_668.py"), lines)
        heads = top.split("  ")[-1].split(" · ")
        self.assertTrue(any(MM.word_kind(h) == "identifier" for h in heads), top)

    def test_held_reads_the_context_stack(self):
        from membrane import callctx as CC
        import tempfile
        srv = NS(data_dir=tempfile.mkdtemp())
        CC.new_turn(srv, "ses_sail")
        CC.enter(srv, "membrane::goal:trip-north")
        CC.touch(srv, "person:dmitrii")
        self.assertEqual(set(CC.held(srv)), {"membrane::goal:trip-north", "person:dmitrii"})

    def test_rewrite_passes_the_chat_through(self):
        fld = busy_code_field(self.SAIL)
        out = MM.rewrite([{"role": "system", "content": "d"}, {"role": "user", "content": "now"}], fld,
                         chat="ses_sail")
        rows = out[1]["content"].splitlines()[1:]
        self.assertTrue(rows[0].split()[0] in {"m:goal:trip-north", "person:dmitrii", "m:decision:inside-route"},
                        rows)

    def test_switch(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_EXP_MOMENTUM_MAP": "1"}):
            self.assertTrue(MM.on())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(MM.on())
