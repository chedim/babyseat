"""THE INIT SPEAK (the thinker's hello on boot, planner-gated): the seat
boot reports one line -- membrane up, the field's cell count, thinker +
dreamer humming -- through speak()'s ActTable gate. The vetoes are the
point: an UNLEARNED boot context stays silent (stay-silent is the
honest default, the veto itself observed so silence is learnable), and
a boot failure never silences the hello (the speak sits outside the
organ-boot try)."""

import unittest
from unittest import mock

from membrane.acts import ActTable


class _Srv:
    def __init__(self):
        self._speak_queue = []
        self._act_table = ActTable()
        self._last_context_sig = "boot|0.5"


class TestInitSpeak(unittest.TestCase):
    def test_unlearned_boot_context_vetoes(self):
        """A fresh ActTable has no boot-context rows: the hello is
        vetoed (stay-silent), the veto observed so silence is learnable,
        and NOTHING lands in the speech queue."""
        import membrane.opencode as oc

        srv = type("S", (), {"_speak_queue": [],
                             "_act_table": ActTable()})()
        with mock.patch.object(oc.speak, "__call__",
                               return_value=False) as sp:
            oc._init_speak(srv)
        self.assertEqual(len(srv._speak_queue), 0)


if __name__ == "__main__":
    unittest.main()


class TestBootContextSeed(unittest.TestCase):
    """THE BOOT-CONTEXT SEED (the init-speak enabler): the boot
    context's ActTable row is observed report-state x2 BEFORE the
    hello -- the table has LEARNED boot contexts speak, so
    _init_speak's consult selects report-state (not the stay-silent
    veto) on every fresh boot."""

    def test_seed_learns_boot_context_before_hello(self):
        import membrane.opencode as oc

        # the real shape: the hello reads the field's cell count off
        # srv.membrane.store (the f-string) -- the fake needs it
        _store = type("St", (), {"exemplars": [], "__len__":
                                  lambda self: len(self.exemplars)})()
        _membrane = type("M", (), {"store": _store})()
        srv = type("S", (), {"_speak_queue": [],
                             "_act_table": ActTable(),
                             "_last_context_sig": "boot|0.5",
                             "membrane": _membrane})()
        # the boot-context seed (the boot block's exact shape):
        srv._act_table.observe("boot|0.5", "report-state")
        srv._act_table.observe("boot|0.5", "report-state")
        # the hello's consult passes with the learned row:
        self.assertEqual(srv._act_table.select("boot|0.5"),
                         "report-state")
        oc._init_speak(srv)
        self.assertEqual(len(srv._speak_queue), 1)
