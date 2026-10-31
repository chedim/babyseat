"""#768 / #776: a loop's turn (worker, planner, watch, triage) gets no recall banner.

The planner already wrote what the worker needs into the task text; the
banner's recall made every worker gate wait 23-85 s and a person's turn
queued behind them (10-30: gates of 50-185 s while 3-4 workers ran). The
chat's turns and the planner's keep their banner."""
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import capture as C  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane.watch_agents import loop_agent_name  # noqa: E402
from test_conversation_banner_429 import _seat, _turn  # noqa: E402


def _runner(rows: dict):
    """The serve runner's session store, as loop_session leaves it: each row carries its agent."""
    return types.SimpleNamespace(sessions=types.SimpleNamespace(sessions=rows))


class TheWorkerTurnHasNoBanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv, self.f = _seat(self.tmp)
        _turn(self.f, "sid:a", "make `tools/backup.py` skip big files", "ok", ts=time.time() - 900)
        self.f.process()
        self.srv.no_banner = False
        self.srv.no_context = False
        self.srv._recall_null = None
        self.srv.peers = None
        self.srv.injection_mode = "conversation"
        self.srv._loop_runner = _runner({
            "ses_w": {"id": "ses_w", "agent": loop_agent_name(GG.LANE_WORKER, "membrane::task:g1:k1")},
            "ses_p": {"id": "ses_p", "agent": loop_agent_name(GG.LANE_PLANNER, "membrane::goal:g1")},
            "ses_c": {"id": "ses_c", "agent": "build"},
            "ses_t": {"id": "ses_t", "agent": loop_agent_name("watch", "f:x.py")},
            "ses_ph": {"id": "ses_ph", "agent": "phone"},
            "ses_sp": {"id": "ses_sp", "agent": "speaker"},
        })

    def tearDown(self):
        C.set_conversation("")

    def ctx(self, sid):
        C.set_conversation("sid:a")
        body = {"messages": [{"role": "user", "content": "what's next?"}], "_turn_sid": sid}
        return L._state_ctx(self.srv, body, None, False)[0]

    def test_a_worker_turn_builds_no_banner(self):
        from membrane import conv_banner as CB
        with mock.patch.object(CB, "build", return_value="THIS CONVERSATION") as b:
            self.assertFalse(self.ctx("ses_w"))
        b.assert_not_called()

    def test_a_worker_turn_skips_the_old_banner_too(self):
        self.srv.injection_mode = "banner"
        with mock.patch.object(L, "_where_we_are", return_value="WHERE WE ARE") as w:
            self.assertNotIn("WHERE WE ARE", self.ctx("ses_w") or "")
        w.assert_not_called()

    def test_the_chat_keeps_its_banner(self):
        self.assertIn("THIS CONVERSATION", self.ctx("ses_c") or "")

    def test_the_planner_and_a_watch_loop_build_no_banner_either(self):     # #776
        from membrane import conv_banner as CB
        with mock.patch.object(CB, "build", return_value="THIS CONVERSATION") as b:
            self.assertFalse(self.ctx("ses_p"))
            self.assertFalse(self.ctx("ses_t"))
        b.assert_not_called()

    def test_a_person_on_the_phone_or_the_speaker_keeps_the_banner(self):
        self.assertIn("THIS CONVERSATION", self.ctx("ses_ph") or "")
        self.assertIn("THIS CONVERSATION", self.ctx("ses_sp") or "")

    def test_a_turn_with_no_sid_keeps_its_banner(self):
        self.assertIn("THIS CONVERSATION", self.ctx("") or "")

    def test_is_loop_turn(self):
        for sid in ("ses_w", "ses_p", "ses_t"):
            self.assertTrue(L._is_loop_turn(self.srv, {"_turn_sid": sid}), sid)
        for sid in ("ses_c", "ses_ph", "ses_sp", "", "ses_unknown"):
            self.assertFalse(L._is_loop_turn(self.srv, {"_turn_sid": sid}), sid)
        self.assertFalse(L._is_loop_turn(types.SimpleNamespace(), {"_turn_sid": "ses_w"}))


if __name__ == "__main__":
    unittest.main()


class TheTurnSidIsPerThread(unittest.TestCase):
    """#768: the seat's one TurnRunner runs the chat and the workers at once; the turn
    sid the loopback body carries (`_turn_sid`) must be the calling turn's own, or a
    chat turn reads as a worker's (and its captures stamp the worker's chat)."""

    def test_two_concurrent_turns_each_see_their_own_sid(self):
        import threading
        from membrane.server import TurnRunner
        r = TurnRunner.__new__(TurnRunner)
        a_set, b_set, seen = threading.Event(), threading.Event(), {}

        def turn(sid, mine, other):
            r._current_turn_sid = sid
            mine.set()
            other.wait(2)
            seen[sid] = r._current_turn_sid

        ta = threading.Thread(target=turn, args=("ses_chat", a_set, b_set))
        tb = threading.Thread(target=turn, args=("ses_w", b_set, a_set))
        ta.start(); tb.start(); ta.join(3); tb.join(3)
        self.assertEqual(seen, {"ses_chat": "ses_chat", "ses_w": "ses_w"})
        self.assertIsNone(r._current_turn_sid)          # an outside thread has no turn
<<<<<<< HEAD
=======


class TheLoopTurnRunsNoRecall(TheWorkerTurnHasNoBanner):
    """2026-10-31, profiled: a goal loop's turn still ran render_for's recall (the
    no-context rebuild); its cold reads held the field store's one lock while a person's
    banner waited 4 minutes for it."""

    def recon(self, sid):
        from membrane import entity_block as EB
        body = {"messages": [{"role": "user", "content": "what's next?"}], "_turn_sid": sid}
        with mock.patch.object(EB, "render_for", return_value="PAST ANSWERS") as r, \
                mock.patch.object(EB, "reconstruct", return_value="RECON"):
            out = L._recon_block(self.srv, self.f, body, "what's next?", None)
        return out, r

    def test_a_loop_turn_gets_its_reconstruction_but_no_recall(self):
        for sid in ("ses_w", "ses_p", "ses_t"):
            out, r = self.recon(sid)
            self.assertEqual(out, "RECON")
            r.assert_not_called()

    def test_a_persons_turn_keeps_its_recall(self):
        for sid in ("ses_c", "ses_ph", ""):
            out, r = self.recon(sid)
            self.assertIn("PAST ANSWERS", out)
            r.assert_called_once()
>>>>>>> origin/main
