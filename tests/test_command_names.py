"""THE COMMAND LABELS (2026-10-08, the review, red first).

A model's label fused into the command ('shell:shell:mosq-call-tap::').
Pinned: a `label :: command` splits into its label and its command (the run
lane still peels a label off a stored command line). Since #576 part F the
model no longer names one-shot commands (the link door is gone), so the
command-line naming that lived here went with it.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402


class TestCommandNames(unittest.TestCase):
    def test_labels_name_the_command(self):
        self.assertEqual(N.split_label("mosq-call-tap :: mosquitto_sub -t x"),
                         ("mosq-call-tap", "mosquitto_sub -t x"))
        self.assertEqual(N.split_label("shell:mosq-call-tap:: mosquitto_sub -t x"),
                         ("mosq-call-tap", "mosquitto_sub -t x"))
        self.assertEqual(N.split_label("[ -f x ] && echo y"), ("", "[ -f x ] && echo y"))

    def test_a_label_may_carry_the_seats_colons(self):
        """2026-10-11: 'argus:battery.soc :: <cmd>' went to the shell whole and
        died 'argus:battery.soc: command not found' -- Argus read it as 'ANY
        colon gets mangled' and wrote a '~'-for-':' runner around it."""
        self.assertEqual(N.split_label("argus:battery.soc :: curl -s http://h:8123/api/states"),
                         ("argus:battery.soc", "curl -s http://h:8123/api/states"))
        self.assertEqual(N.split_label("argus:mqtt:pub-soc::mosquitto_pub -t a -m 1"),
                         ("argus:mqtt:pub-soc", "mosquitto_pub -t a -m 1"))
        self.assertEqual(N.split_label("shell:argus:x :: echo y"), ("argus:x", "echo y"))
        # colons in the command itself were never the problem and stay untouched
        self.assertEqual(N.split_label("curl -s http://h:8123/x"), ("", "curl -s http://h:8123/x"))
        self.assertEqual(N.split_label("echo a::b"), ("", "echo a::b"))


if __name__ == "__main__":
    unittest.main()
