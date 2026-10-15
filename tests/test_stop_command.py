"""THE STOP COMMAND (2026-10-14, the seat that would not quit): the
tty's ctrl+c has been swallowed twice on the live seat (the wedge's
sample showed the main thread asleep in nanosleep while INT waited);
whatever eats the keystroke, stopping the seat must never DEPEND on tty
signal routing. `membrane stop` finds the seat's pids (the serve
port's listener; a pidfile), refuses foreign processes, and runs the
INT -> TERM -> KILL ladder with real bounds. Deterministic: stub seats
are two-process python chains shaped like the real ones (a listening
child with the seat's own SIGINT/SIGTERM handlers); the ladder's
bounds are parameters, so the tests stay fast."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import stopseat as SS

PY = sys.executable

_CHILD_GRACEFUL = (
    "import signal, time, socket\n"
    "def _t(s, f):\n"
    "    raise KeyboardInterrupt\n"
    "signal.signal(signal.SIGINT, _t)\n"
    "signal.signal(signal.SIGTERM, _t)\n"
    "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1)\n"
    "print('ready', s.getsockname()[1], flush=True)\n"
    "time.sleep(120)")

_CHILD_HARDENED = (
    "import signal, time, socket\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(120)")

_CHILD_FOREIGN = (
    "import time, socket\n"
    "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1)\n"
    "print('ready', s.getsockname()[1], flush=True)\n"
    "time.sleep(120)")

# a membrane-shaped stub: the marker reads the cmdline, so the stub
# rides a token the real seat also carries
_STUB_EXTRA = ["--standalone"]


def _spawn_port_holder(code, extra):
    p = subprocess.Popen([PY, "-c", code] + list(extra),
                         stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    _tag, port = p.stdout.readline().split()
    return p, int(port)


def _spawn_ready(code):
    """A stub that prints 'ready' only AFTER its handlers are installed
    (and its socket bound) -- without the barrier a SIGINT can land
    before python even executes the signal.signal line (the interpreter
    is still booting), and a hardened stub would die as if graceful."""
    p = subprocess.Popen([PY, "-c", code], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    assert p.stdout.readline().split()[0] == "ready"
    return p


class TestTheStopLadder(unittest.TestCase):
    """The engine: whatever pids it is given, INT -> wait a real bound ->
    TERM -> wait -> KILL, receipts say what happened per pid."""

    def test_a_graceful_seat_dies_on_int_alone(self):
        child = _spawn_ready(_CHILD_GRACEFUL)
        pids = [child.pid]
        out = SS.stop_pids(pids, int_s=3, term_s=2)
        self.assertEqual(out["stopped"], pids, out)
        self.assertEqual(out["killed"], [], out)
        dead, _ = os.waitpid(child.pid, os.WNOHANG)
        self.assertNotEqual(dead, 0)          # reaped: it really exited

    def test_an_ignoring_seat_is_escalated_to_kill(self):
        child = _spawn_ready(_CHILD_HARDENED)
        pids = [child.pid]
        out = SS.stop_pids(pids, int_s=2, term_s=2)
        self.assertEqual(out["killed"], pids, out)
        self.assertEqual(out["stopped"], [], out)
        dead, _ = os.waitpid(child.pid, os.WNOHANG)
        self.assertNotEqual(dead, 0)

    def test_an_already_gone_pid_is_honest_not_a_crash(self):
        out = SS.stop_pids([999999], int_s=0.2, term_s=0.2)
        self.assertEqual(out["stopped"], [])
        self.assertEqual(out["how"].get(999999), "already-gone", out)


class TestTheDiscoveryGate(unittest.TestCase):
    """Discovery finds seats; the marker refuses anything that is not
    one of ours -- a stop command that kills arbitrary pids is a loaded
    gun."""

    def test_stop_seat_refuses_a_foreign_listener(self):
        # a python sleeper bound to a port but NOT membrane-shaped
        child, _port = _spawn_port_holder(_CHILD_FOREIGN, [])
        try:
            out = SS.stop_seat(ports=[_port], int_s=2, term_s=2)
            self.assertEqual([p for p, _ in out["refused"]],
                             [child.pid], out)
            self.assertTrue(child.pid not in
                            out["stopped"] + out["killed"], out)
            self.assertIsNone(child.poll(),
                              "a foreign process must NOT be touched")
        finally:
            child.terminate()
            try:
                os.waitpid(child.pid, 0)
            except (ChildProcessError, OSError):
                pass

    def test_stop_seat_stops_a_shaped_seat_through_the_port(self):
        child, port = _spawn_port_holder(_CHILD_GRACEFUL, _STUB_EXTRA)
        try:
            out = SS.stop_seat(ports=[port], int_s=3, term_s=2)
            self.assertEqual(out["stopped"], [child.pid], out)
        finally:
            try:
                os.waitpid(child.pid, 0)
            except (ChildProcessError, OSError):
                pass

    def test_stop_seat_reads_the_pidfile_lane(self):
        # the wedged seat may still hold the port, but a pidfile also
        # covers a seat whose LISTEN lane is missing entirely
        child, _port = _spawn_port_holder(_CHILD_GRACEFUL, _STUB_EXTRA)
        try:
            with tempfile.TemporaryDirectory() as td:
                pf = Path(td) / "opencode.pid"
                pf.write_text(str(child.pid))
                out = SS.stop_seat(ports=[], pidfiles=[pf],
                                   int_s=3, term_s=2)
                self.assertEqual(out["stopped"], [child.pid], out)
        finally:
            try:
                os.waitpid(child.pid, 0)
            except (ChildProcessError, OSError):
                pass

    def test_the_marker_survives_a_long_cmdline(self):
        # procps (Linux -- CI, and a phone seat) truncates the ps command
        # column by width; the stub's -c buffer is long enough that the
        # final '--standalone' token fell off the end and the marker gate
        # refused the seat it must stop (red on CI, 2 discovery-gate
        # failures). The fix reads /proc/<pid>/cmdline there. Here the
        # same shape is pinned: a seat stub with a LONG cmdline must be
        # seat-shaped under this platform's cmdline reader.
        padded = ("# " + "p" * 600 + "\n") + _CHILD_GRACEFUL
        child, _ = _spawn_port_holder(padded, _STUB_EXTRA)
        try:
            self.assertTrue(SS._looks_like_seat(child.pid), SS._cmdline(child.pid)[-120:])
        finally:
            # this test never stops the stub itself -- a bare waitpid
            # would block on its full 120s sleep; put it down first
            child.kill()
            child.wait()

    def test_nothing_found_returns_an_honest_zero(self):
        out = SS.stop_seat(ports=[], pidfiles=[])
        self.assertEqual(out["stopped"], [])
        self.assertEqual(out["rc"], 1, out)


if __name__ == "__main__":
    unittest.main()