"""A thread-safe, write-through stand-in for sys.stderr (issue #121).

CPython 3.14 made the buffered-I/O reentrancy guard FATAL: two threads inside
the same buffered writer at once abort with `_enter_buffered_busy` (SIGABRT,
no traceback). Launch mode redirects sys.stderr to one shared line-buffered
log handle that ~20 threads print to unguarded, so the shutdown race could
trip it.

SafeStderr replaces that shared handle: every write is one atomic, unbuffered
os.write under a lock -- there is no buffered layer to re-enter, and no two
writes interleave into a torn line. It is a drop-in for print(..., file=...):
the ~20 call sites need no change.
"""
from __future__ import annotations

import os
import threading


class SafeStderr:
    """A sys.stderr-shaped object: write-through, lock-serialized, unbuffered."""

    def __init__(self, path: str) -> None:
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
        self._lock = threading.Lock()
        self.encoding = "utf-8"
        self.errors = "replace"
        self._closed = False

    def write(self, s) -> int:
        """One atomic write: encode to utf-8 and os.write under the lock
        (no buffered-I/O layer, so the 3.14 reentrancy guard can never fire)."""
        if not s:
            return 0
        data = s if isinstance(s, bytes) else str(s).encode("utf-8", "replace")
        with self._lock:
            if self._closed:
                return len(s)
            os.write(self._fd, data)
        return len(s)

    def flush(self) -> None:
        return None                     # write-through: nothing is buffered

    def writable(self) -> bool:
        return not self._closed

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return False

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    os.close(self._fd)
                except OSError:
                    pass

    @property
    def closed(self) -> bool:
        return self._closed
