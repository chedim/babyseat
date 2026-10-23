"""#466: the conversation banner follows a window of the last 3 messages
(WORKING ON, ABOUT), shows the open board goals with their origin, carries
one BLURP status line and the model answering this turn, and says PAUSED only
while the pause is on."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import conv_banner as CB  # noqa: E402
from test_conversation_banner_429 import NOW, UTC, _field, _goal, _seat, _turn  # noqa: E402

MIN = 60.0


def _topic_change():
    f = _field()
    _turn(f, "c1", "fix the parser in alpha_one.py", "ok", ts=NOW - 50 * MIN)
    _turn(f, "c1", "alpha_one.py still fails on tabs", "ok", ts=NOW - 40 * MIN)
    _turn(f, "c1", "switch to beta_two.py now", "ok", ts=NOW - 30 * MIN)
    _turn(f, "c1", "beta_two.py needs a retry", "ok", ts=NOW - 20 * MIN)
    return f


class TestWindow(unittest.TestCase):
    def test_window_is_three_messages(self):
        self.assertEqual(CB.WINDOW_MESSAGES, 3)

    def test_working_on_follows_the_window_not_the_old_topic(self):
        f = _topic_change()
        ev = CB.conv_events(f, "c1")
        it = CB.working_on(ev, "what next", ["f:~alpha_one.py"], f)
        self.assertEqual(it.text, "beta_two.py needs a retry")

    def test_about_follows_the_window(self):
        f = _topic_change()
        about = CB.focus(f, CB.conv_events(f, "c1"), "what next")
        self.assertFalse(any("alpha_one" in a for a in about), about)
        self.assertTrue(any("beta_two" in a for a in about), about)

    def test_the_current_message_counts_as_one_of_the_three(self):
        f = _topic_change()
        win = CB.window_events(CB.conv_events(f, "c1"), "and the tests?")
        asks = [d["text"] for _k, d in win if d.get("role") == "user"]
        self.assertEqual(len(asks), 2)          # two earlier asks + the one being answered
        self.assertIn("beta_two.py needs a retry", asks[-1])

    def test_a_short_chat_is_all_in_the_window(self):
        f = _field()
        _turn(f, "c2", "look at gamma_three.py", "ok", ts=NOW - 10 * MIN)
        ev = CB.conv_events(f, "c2")
        self.assertEqual(CB.window_events(ev, "next"), ev)


class TestOpenBoardGoals(unittest.TestCase):
    def test_board_goals_show_with_their_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv, _f = _seat(tmp)
            _goal(srv.entity_store, "here", "ship the banner", convs=["c1"], born=NOW - 10 * MIN)
            _goal(srv.entity_store, "away", "tidy the zoo", convs=["c9"], born=NOW - 20 * MIN)
            _goal(srv.entity_store, "board", "keep the seat honest", born=NOW - 30 * MIN)
            got = {i.text: i.src for i in CB.open_items(srv, "c1")}
            self.assertEqual(got["ship the banner"], "goal · this chat")
            self.assertEqual(got["tidy the zoo"], "goal · another chat")
            self.assertEqual(got["keep the seat honest"], "goal · board")

    def test_this_chat_first_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv, _f = _seat(tmp)
            for n in range(5):
                _goal(srv.entity_store, f"b{n}", f"board {n}", born=NOW - n * MIN)
            _goal(srv.entity_store, "mine", "mine", convs=["c1"], born=NOW - 99 * MIN)
            got = CB.open_items(srv, "c1")
            self.assertEqual(len(got), CB.OPEN_MAX)
            self.assertEqual(got[0].text, "mine")

    def test_closed_board_goals_stay_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv, _f = _seat(tmp)
            _goal(srv.entity_store, "done", "already done", status="fulfilled")
            self.assertEqual(CB.open_items(srv, "c1"), [])


class TestBlurpLine(unittest.TestCase):
    def test_one_status_line(self):
        ln = CB.blurp_line({"mode": "on", "level": 3, "rung_name": "feel the push",
                            "waiting": "your accept"})
        self.assertTrue(ln.startswith("BLURP: "), ln)
        self.assertNotIn("\n", ln)
        for part in ("on", "feel the push", "your accept"):
            self.assertIn(part, ln)

    def test_off_or_unknown_has_no_line(self):
        self.assertIsNone(CB.blurp_line({"mode": "off"}))
        self.assertIsNone(CB.blurp_line({}))
        self.assertIsNone(CB.blurp_line(None))

    def test_render_carries_it_between_the_chat_lines_and_more(self):
        out = CB.render({"working": CB.Item("make it so", "person", NOW - 400),
                         "blurp": "BLURP: on · rung 3", "more": True}, NOW, UTC)
        lines = out.split("\n")
        self.assertIn("BLURP: on · rung 3", lines)
        self.assertLess(lines.index("BLURP: on · rung 3"), lines.index(CB.MORE_LINE))

    def test_blurp_alone_does_not_make_a_block(self):
        self.assertIsNone(CB.render({"blurp": "BLURP: on"}, NOW, UTC))


class TestPausedOnlyWhilePaused(unittest.TestCase):
    def _build(self, paused):
        with tempfile.TemporaryDirectory() as tmp:
            srv, f = _seat(tmp)
            srv.paused = paused
            _turn(f, "c1", "keep going on alpha_one.py", "ok", ts=NOW - 5 * MIN)
            return CB.build(srv, {"messages": [{"role": "user", "content": "next"}]}, "c1", NOW, UTC)

    def test_no_paused_line_when_running(self):
        self.assertNotIn("PAUSED", self._build(False))

    def test_paused_line_while_paused(self):
        self.assertIn("PAUSED", self._build(True))


class TestModelLine(unittest.TestCase):
    BLOCK = CB.render({"working": CB.Item("make it so", "person", NOW - 400)}, NOW, UTC)

    def test_line_follows_the_header(self):
        out = CB.add_model_line(self.BLOCK, "glm-5")
        lines = out.split("\n")
        self.assertEqual(lines[2], "MODEL: glm-5 is answering this turn")
        self.assertEqual(lines[:2], self.BLOCK.split("\n")[:2])
        self.assertEqual(lines[3:], self.BLOCK.split("\n")[2:])

    def test_no_model_no_change(self):
        self.assertEqual(CB.add_model_line(self.BLOCK, ""), self.BLOCK)
        self.assertEqual(CB.add_model_line(self.BLOCK, None), self.BLOCK)

    def test_restamp_updates_only_the_banner_in_a_system_message(self):
        msgs = [{"role": "system", "content": "a\n" + self.BLOCK + "\n\nrecall tail"},
                {"role": "user", "content": "q"}]
        new = CB.restamp(msgs, "glm-5")
        self.assertIn("MODEL: glm-5 is answering this turn", new[0]["content"])
        self.assertTrue(new[0]["content"].startswith("a\n")
                        and new[0]["content"].endswith("\n\nrecall tail"))
        self.assertEqual(new[1], {"role": "user", "content": "q"})

    def test_restamp_twice_keeps_one_model_line(self):
        msgs = [{"role": "system", "content": self.BLOCK}]
        CB.restamp(msgs, "m1")
        CB.restamp(msgs, "m2")
        self.assertEqual(msgs[0]["content"].count("MODEL: "), 1)
        self.assertIn("MODEL: m2 ", msgs[0]["content"])

    def test_restamp_keeps_the_injected_type(self):
        from membrane.rag import Injected
        msgs = [Injected(role="system", content=self.BLOCK), {"role": "user", "content": "q"}]
        new = CB.restamp(msgs, "m1")
        self.assertIsInstance(new[0], Injected)

    def test_restamp_never_touches_a_user_message(self):
        msgs = [{"role": "user", "content": self.BLOCK}]
        CB.restamp(msgs, "m1")
        self.assertEqual(msgs[0]["content"], self.BLOCK)

    def test_restamp_without_the_banner_is_a_no_op(self):
        msgs = [{"role": "user", "content": "q"}]
        self.assertEqual(CB.restamp(msgs, "m1"), msgs)
        self.assertEqual(CB.restamp(None, "m1"), None)


class TestEmptyStaysUnsent(unittest.TestCase):
    def test_no_chat_state_and_no_goals_sends_no_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv, _f = _seat(tmp)
            out = CB.build(srv, {"messages": [{"role": "user", "content": "hi"}]}, "c9", NOW, UTC)
            self.assertIsNone(out)



if __name__ == "__main__":
    unittest.main()
