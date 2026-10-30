"""#760 review: the boot reads the re-exec marker more than once (the port
pins ~line 804, the browser/boot check ~line 2124). was_reexec() clears the
marker on read, so every read after the first must still say True."""

import os
import unittest
from unittest import mock

from membrane import restarts
from membrane.lanes import port_pinned


class TestReexecBootPins(unittest.TestCase):
    def test_reexec_boot_pins_neither_port_and_later_check_still_true(self):
        argv = ["--serve", "--listen", "11500", "--serve-port", "4100"]
        with mock.patch.dict(os.environ, {restarts.REEXEC_ENV: "1"}), \
                mock.patch.dict(restarts._BOOT, {}, clear=True):
            # the same reads opencode.main makes, in the same order
            listen_pinned = port_pinned(argv, "--listen", reexec=restarts.was_reexec())
            serve_pinned = port_pinned(argv, "--serve-port", reexec=restarts.was_reexec())
            later = restarts.was_reexec()          # ~line 2124
        self.assertFalse(listen_pinned)
        self.assertFalse(serve_pinned)
        self.assertTrue(later)


if __name__ == "__main__":
    unittest.main()
