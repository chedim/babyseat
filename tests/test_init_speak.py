"""THE INIT SPEAK (the thinker's hello on boot): the seat boot reports
one line -- membrane up, the field's cell count, thinker + dreamer
humming -- through speak()'s UNVETOED queue (the ActTable gate is
retired: the consult may still pick the realization, but a learned
stay-silent no longer drops anything). A boot failure never silences
the hello (the speak sits outside the organ-boot try)."""

import unittest

from membrane.acts import ActTable


class TestInitSpeak(unittest.TestCase):
    def test_unlearned_boot_context_still_speaks(self):
        """THE RULING: a fresh ActTable has no boot rows -- its
        stay-silent select loses the vote, so the hello LANDS in the
        speech queue (the retired veto would have dropped it) and the
        startup-poke flag arms."""
        import membrane.opencode as oc

        srv = type("S", (), {"_speak_queue": [],
                             "_act_table": ActTable()})()
        oc._init_speak(srv)
        self.assertEqual(len(srv._speak_queue), 1)
        self.assertIn("membrane up", srv._speak_queue[0]["text"])
        self.assertTrue(getattr(srv, "_boot_greeting_pending", False))


class TestBootContextSeed(unittest.TestCase):
    """THE BOOT-CONTEXT SEED (the consult's enabler): the boot
    context's ActTable row is observed report-state x2 BEFORE the
    hello -- so _init_speak's consult selects report-state (a real
    learned realization) on every fresh boot. The seed matters even
    with the veto retired: it feeds the consult and the label."""

    def test_seed_learns_boot_context_before_hello(self):
        import membrane.opencode as oc

        srv = type("S", (), {"_speak_queue": [],
                             "_act_table": ActTable(),
                             "_last_context_sig": "boot|0.5"})()
        # the boot-context seed (the boot block's exact shape):
        srv._act_table.observe("boot|0.5", "report-state")
        srv._act_table.observe("boot|0.5", "report-state")
        # the hello's consult carries the learned realization:
        self.assertEqual(srv._act_table.select("boot|0.5"),
                         "report-state")
        oc._init_speak(srv)
        self.assertEqual(len(srv._speak_queue), 1)


if __name__ == "__main__":
    unittest.main()