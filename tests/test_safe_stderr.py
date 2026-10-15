"""Issue #121: the seat hard-crashes with SIGABRT when CPython 3.14's
buffered-I/O reentrancy guard fires -- launch mode redirects sys.stderr to
one shared line-buffered handle that ~20 threads write to unguarded, so two
concurrent writes trip `_enter_buffered_busy` (fatal on 3.14).

SafeStderr is a thread-safe, write-through stand-in for sys.stderr: every
write is one atomic, unbuffered os.write under a lock -- no buffered layer
to re-enter, no torn lines."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.safestderr import SafeStderr  # noqa: E402


class TestSafeStderr(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _path(self):
        return os.path.join(self._tmpdir.name, "log.txt")

    def test_write_is_write_through(self):
        s = SafeStderr(self._path())
        self.addCleanup(s.close)
        s.write("hello\n")
        with open(self._path(), "rb") as f:       # no flush() in between
            self.assertEqual(f.read(), b"hello\n")

    def test_concurrent_writes_do_not_tear_lines(self):
        s = SafeStderr(self._path())
        self.addCleanup(s.close)
        n = 64
        lines = [f"{i:04d}-" + "x" * 36 + "\n" for i in range(n)]
        threads = [threading.Thread(target=s.write, args=(line,))
                   for line in lines]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(self._path(), "rb") as f:
            content = f.read()
        got = sorted(content.decode("utf-8").splitlines())
        self.assertEqual(got, sorted(line.rstrip("\n") for line in lines))

    def test_file_protocol(self):
        s = SafeStderr(self._path())
        self.addCleanup(s.close)
        self.assertFalse(s.isatty())
        self.assertEqual(s.encoding, "utf-8")
        self.assertGreaterEqual(s.fileno(), 0)
        self.assertIsNone(s.flush())              # nothing buffered

    def test_empty_write_is_a_noop(self):
        s = SafeStderr(self._path())
        self.addCleanup(s.close)
        self.assertEqual(s.write(""), 0)


if __name__ == "__main__":
    unittest.main()
