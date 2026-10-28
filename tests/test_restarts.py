"""RESTARTS, TOLD TO ARGUS -- AND HIS TO DO (2026-10-11, the operator: "announce
HA and seat restarts to Argus ... thru its ollama proxy endpoint ... let them
delay it if they're busy ... or better yet, have Argus do those restarts ...
if he's 1 minute late to restart then you should consult me")."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import restarts as RS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)


class _Srv:
    def __init__(self):
        self.data_dir = tempfile.mkdtemp()
        self.entity_store = EntityStore(path=str(Path(self.data_dir) / "e.json"), shared=False)
        self.entity_store._srv = self
        self.events_pool = _Pool()


class TestTheNotices(unittest.TestCase):
    def test_a_deploy_says_why_and_how_long(self):
        srv = _Srv()
        Path(srv.data_dir, RS.ALIVE_FILE).write_text("1000")
        Path(srv.data_dir, RS.REASON_FILE).write_text("Argus restarted it (new score code)")
        t = RS.boot_text(srv, now=1095)
        self.assertIn("down about 1 min 35 s", t)
        self.assertIn("Why: Argus restarted it (new score code).", t)
        self.assertTrue(t.startswith(RS.SIGN))
        self.assertFalse(Path(srv.data_dir, RS.REASON_FILE).exists())        # consumed

    def test_no_reason_is_unexpected(self):
        self.assertIn("not announced", RS.boot_text(_Srv(), now=1e9))

    def test_ha_start_is_told_once(self):
        srv = _Srv()
        RS._last_ha["ts"] = 0
        with mock.patch.object(RS, "_send") as send:
            self.assertEqual(RS.on_event(srv, {"kind": "ha-start"}), {"ok": True})
            self.assertTrue(RS.on_event(srv, {"kind": "ha-start"}).get("dedup"))
        self.assertEqual(send.call_count, 1)
        self.assertIn("Home Assistant restarted", send.call_args[0][2])

    def test_delay_is_his_word_capped(self):
        self.assertEqual(RS.parse_delay("DELAY 5 -- finishing the battery getter"), 5)
        self.assertEqual(RS.parse_delay("delay: 90"), RS.MAX_DELAY_MIN)
        self.assertEqual(RS.parse_delay("GO"), 0)
        self.assertEqual(RS.parse_delay(None), 0)


class TestArgusRestarts(unittest.TestCase):
    def _staged(self, srv, at):
        Path(srv.data_dir, RS.PENDING_FILE).write_text(json.dumps(
            {"staged_at": at, "files": ["score.py"], "why": "the score counts entity-hours"}))

    def test_staged_once_reminded_then_overdue(self):
        srv, t = _Srv(), 1_800_000_000.0
        self._staged(srv, t)
        with mock.patch("membrane.seat_panel.note") as note:
            RS.check_pending(srv, now=t + 1)
            RS.check_pending(srv, now=t + 20)                                 # told once
            self.assertEqual([e["kind"] for e in srv.events_pool.items], ["restart-pending"])
            RS.check_pending(srv, now=t + RS.DUE_S - RS.REMIND_BEFORE_S + 1)
            self.assertTrue(srv.events_pool.items[-1].get("reminder"))
            RS.check_pending(srv, now=t + RS.DUE_S + 30)
            self.assertFalse(note.called)                                     # due, not yet a minute late
            RS.check_pending(srv, now=t + RS.DUE_S + RS.OVERDUE_GRACE_S + 1)
            self.assertEqual(note.call_args[0][1], "restart-overdue")
        ent = srv.entity_store.docs[MEMBRANE_NS + "restart"]
        self.assertTrue(ent.properties["pending"])

    def test_his_restart_records_why_and_stops_cleanly(self):
        srv = _Srv()
        self._staged(srv, 1.0)
        with mock.patch("os.kill") as kill, mock.patch("membrane.seat_panel.note"):
            out = RS.do_restart(srv, "nothing of mine in flight", delay_s=0)
            import time
            end = time.time() + 10                   # never leave the patch before the
            while not kill.called and time.time() < end:   # thread's SIGTERM lands in it
                time.sleep(0.01)
        self.assertEqual(out["rc"], 0)
        kill.assert_called_once()
        self.assertIn("the seat restarted it (nothing of mine in flight); it loads the staged score.py",
                      Path(srv.data_dir, RS.REASON_FILE).read_text())

    def test_any_boot_loads_the_staged_code(self):
        srv = _Srv()
        self._staged(srv, 1.0)
        RS.bind(srv)
        self.assertFalse(Path(srv.data_dir, RS.PENDING_FILE).exists())

    def test_the_loop_is_told_how(self):
        t = WA.loop_task_text({"kind": "restart-pending", "entity": "membrane::restart", "why": "new score",
                               "files": ["score.py"], "due": 1_800_000_000})
        self.assertIn("a seat restart is pending", t)
        self.assertIn("method='restart'", t)
        self.assertIn("STEP", t)

    def test_the_doctrine_says_the_restart_is_his(self):
        d = (Path(__file__).resolve().parent.parent / "doctrine.md").read_text()
        self.assertIn("RESTARTS:", d)
        self.assertIn("method='restart'", d)


class TestARestartRestarts(unittest.TestCase):
    """#151: a foreground seat has no supervisor -- its self-restart's SIGTERM
    was a stop. A requested restart now re-execs the seat after teardown
    (same pid: a supervisor waiting on it just sees it continue)."""

    def setUp(self):
        RS.RESTART["requested"] = False       # do_restart elsewhere sets it

    def tearDown(self):
        RS.RESTART["requested"] = False

    def test_do_restart_marks_the_restart_requested(self):
        srv = _Srv()
        # no thread at all: its SIGTERM is aimed at THIS process -- a patch
        # that ends before the thread fires killed pytest on CI (exit 143)
        with mock.patch.object(RS.threading, "Thread") as th, \
             mock.patch("membrane.seat_panel.note"):
            RS.do_restart(srv, "staged fixes", delay_s=0)
        th.return_value.start.assert_called_once()
        self.assertTrue(RS.RESTART["requested"])

    def test_a_requested_restart_reexecs_the_same_command(self):
        RS.RESTART["requested"] = True
        argv = ["/x/membrane/opencode.py", "--standalone", "--field", "f.json"]
        with mock.patch.object(RS.os, "execv") as ex, \
             mock.patch.object(RS.sys, "argv", argv):
            RS.reexec_if_requested()
        ex.assert_called_once_with(
            RS.sys.executable,
            [RS.sys.executable, "-m", "membrane.opencode",
             "--standalone", "--field", "f.json"])

    def test_the_restart_says_so_while_the_log_is_open(self):
        # the teardown prints this BEFORE it closes the log file stderr
        # rides; printed at exec time it went nowhere
        self.assertIsNone(RS.restart_line())
        RS.RESTART["requested"] = True
        self.assertIn("re-exec", RS.restart_line())

    def test_a_plain_stop_never_reexecs(self):
        with mock.patch.object(RS.os, "execv") as ex:
            RS.reexec_if_requested()
        ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestRestartKeepsPorts648(unittest.TestCase):
    """#648: a re-exec with the original argv raced for ports again and two seats
    swapped. The re-exec now pins the ports the seat holds, unless argv did."""

    def tearDown(self):
        RS.RESTART["requested"] = False
        RS.RESTART.pop("ports", None)

    def test_unpinned_argv_gets_the_bound_ports(self):
        out = RS.pin_ports(["--standalone", "--field", "f.json"], listen=11501, serve=4097)
        self.assertEqual(out, ["--standalone", "--field", "f.json",
                               "--listen", "11501", "--serve-port", "4097"])

    def test_pinned_argv_is_unchanged(self):
        argv = ["--listen", "11500", "--serve-port=4096"]
        self.assertEqual(RS.pin_ports(argv, listen=11501, serve=4097), argv)

    def test_a_seat_with_no_serve_pins_only_the_proxy(self):
        self.assertEqual(RS.pin_ports(["--x"], listen=11501, serve=None), ["--x", "--listen", "11501"])

    def test_the_reexec_carries_the_ports(self):
        RS.RESTART["requested"] = True
        RS.RESTART["ports"] = (11501, 4097)
        with mock.patch.object(RS.os, "execv") as ex, mock.patch.object(RS.sys, "argv", ["m", "--standalone"]):
            RS.reexec_if_requested()
        self.assertEqual(ex.call_args[0][1][-5:],
                         ["--standalone", "--listen", "11501", "--serve-port", "4097"][-5:])

class TestRestartHoldsWhileBusy647(unittest.TestCase):
    """#647: presence.restart waited 2 s whatever ran, and a person's reply was
    lost. The signal now waits on busy(), up to the same cap as a planned one."""

    def test_the_hold_returns_when_the_seat_goes_idle(self):
        seq = [["turn s1"], ["turn s1"], []]
        with mock.patch.object(RS, "busy", side_effect=lambda s: seq.pop(0)) as b, \
             mock.patch.object(RS.time, "sleep") as sl:
            held = RS.hold_until_idle(_Srv())
        self.assertEqual(held, ["turn s1"])
        self.assertEqual(b.call_count, 3)
        self.assertEqual(sl.call_count, 2)

    def test_the_hold_is_capped(self):
        clock = iter([0, 1, RS.MAX_DELAY_MIN * 60 + 5, RS.MAX_DELAY_MIN * 60 + 6])
        with mock.patch.object(RS, "busy", return_value=["1 loop(s)"]), \
             mock.patch.object(RS.time, "sleep"), \
             mock.patch.object(RS.time, "time", side_effect=lambda: next(clock)):
            held = RS.hold_until_idle(_Srv())
        self.assertEqual(held, ["1 loop(s)"])

    def test_do_restart_signals_only_after_the_hold(self):
        order = []
        with mock.patch.object(RS, "hold_until_idle", side_effect=lambda s: order.append("hold") or []), \
             mock.patch.object(RS.os, "kill", side_effect=lambda *a: order.append("kill")), \
             mock.patch.object(RS.time, "sleep"), \
             mock.patch("membrane.seat_panel.note"):
            RS.RESTART["requested"] = False
            RS.do_restart(_Srv(), "x", delay_s=0)
            for t in RS.threading.enumerate():
                if t.name == "restart":
                    t.join(2)
        RS.RESTART["requested"] = False
        self.assertEqual(order, ["hold", "kill"])
