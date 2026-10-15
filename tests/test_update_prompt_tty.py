"""THE UPGRADE PROMPT ON A RAW TERMINAL (2026-10-15): "Update membrane now? [y/N]"
echoed ^M and never took the answer -- something earlier in that terminal had left it
without ICRNL (Enter's carriage return no longer becomes a newline), and input()
waited for a newline that never came; the seat sat at the prompt. The prompt now
puts the terminal back into line mode (CR -> NL, canonical, echo) before it asks."""

import os
import pty
import sys
import termios
import threading
import unittest

from membrane import update as U


@unittest.skipUnless(hasattr(os, "openpty"), "needs a pty")
class TestThePromptOnARawTerminal(unittest.TestCase):
    def _answer(self, typed: bytes, timeout=5.0):
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[0] &= ~termios.ICRNL                       # the broken terminal: Enter stays a CR
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        stdin = os.fdopen(slave, "r", buffering=1)
        stdout = os.fdopen(os.dup(slave), "w", buffering=1)
        got = {}

        def ask():
            old = sys.stdin, sys.stdout
            sys.stdin, sys.stdout = stdin, stdout
            try:
                got["a"] = U._ask("Update membrane now? [y/N] ")
            except Exception as e:                       # noqa: BLE001
                got["e"] = e
            finally:
                sys.stdin, sys.stdout = old
        t = threading.Thread(target=ask, daemon=True)
        t.start()
        # a person types AFTER the prompt shows (the terminal translates input
        # as it arrives): wait for the prompt on the terminal, then type
        import select
        seen = b""
        while b"[y/N]" not in seen and select.select([master], [], [], timeout)[0]:
            seen += os.read(master, 1024)
        os.write(master, typed)
        t.join(timeout)
        os.close(master)
        return got, t.is_alive()

    def test_enter_is_enter_even_when_the_terminal_lost_icrnl(self):
        got, stuck = self._answer(b"y\r")
        self.assertFalse(stuck, "the prompt never saw the end of the line")
        self.assertEqual(got.get("a", "").strip().lower(), "y")

    def test_a_plain_newline_still_works(self):
        got, stuck = self._answer(b"n\n")
        self.assertFalse(stuck)
        self.assertEqual(got.get("a", "").strip().lower(), "n")


if __name__ == "__main__":
    unittest.main()
