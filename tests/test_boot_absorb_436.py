"""The boot's absorb step runs off the main thread (#436).

Inline, it parsed every Claude Code transcript of the project (since #401
found the real folder: 1+ GB on a long-lived project) on the main thread,
after :4096 was up and before the proxy's serve loop started -- :11500 sat
in LISTEN, accepting nothing.
"""
import threading
import time
import unittest
from unittest import mock


class TestBootAbsorbOffTheMainThread(unittest.TestCase):
    """The live hang's cause: the boot's absorb step parsed every Claude Code
    transcript of the project (since #401 found the real folder) on the main
    thread, after :4096 was up and before the proxy's serve loop started --
    :11500 sat in LISTEN, accepting nothing, at 100 % CPU."""

    def test_boot_absorb_returns_at_once_and_absorbs_in_the_background(self):
        from membrane import absorb as AB
        gate = threading.Event()
        done = []

        def slow_absorb(*a, **k):
            gate.wait(10)
            done.append(k.get("cwd"))
            return {"imported": [], "skipped": []}
        lines = []
        with mock.patch.object(AB, "absorb", slow_absorb), \
                mock.patch.object(AB, "absorb_gate", lambda *a: True):
            t0 = time.time()
            th = AB.boot_absorb(object(), object(), None, field_path="f.json", new_session=False,
                                no_absorb=False, native_up=True, cwd="/w", marker_path="m.json",
                                model_id="x", emit=lines.append)
            self.assertLess(time.time() - t0, 1.0)      # the caller is never held
            self.assertIsNotNone(th)
            self.assertTrue(th.daemon)
            self.assertEqual(done, [])
            gate.set()
            th.join(10)
        self.assertEqual(done, ["/w"])
        self.assertEqual(lines, ["[absorb] nothing new since the seat's last run"])

    def test_boot_absorb_gated_off_starts_nothing(self):
        from membrane import absorb as AB
        with mock.patch.object(AB, "absorb_gate", lambda *a: False):
            self.assertIsNone(AB.boot_absorb(object(), object(), None, field_path="f.json",
                                             new_session=True, no_absorb=False, native_up=True,
                                             cwd="/w", marker_path="m.json", model_id="x"))

    def test_boot_absorb_failure_is_reported_never_raised(self):
        from membrane import absorb as AB
        lines = []

        def boom(*a, **k):
            raise RuntimeError("bad transcript")
        with mock.patch.object(AB, "absorb", boom), \
                mock.patch.object(AB, "absorb_gate", lambda *a: True):
            th = AB.boot_absorb(object(), object(), None, field_path="f.json", new_session=False,
                                no_absorb=False, native_up=True, cwd="/w", marker_path="m.json",
                                model_id="x", emit=lines.append)
            th.join(10)
        self.assertEqual(len(lines), 1)
        self.assertIn("bad transcript", lines[0])

    def test_the_seat_never_absorbs_at_boot(self):
        """2026-10-31, the operator: "no fucking autoabsorption was ever requested" --
        "manual - yes, automatic - wtf?". The boot imports no external session."""
        import inspect
        from membrane import opencode as OC
        src = inspect.getsource(OC.main)
        self.assertNotIn("boot_absorb(", src)
        self.assertNotIn("absorb(sess_store", src)


class TestUnchangedTranscriptsAreNotReparsed(unittest.TestCase):
    """The approved follow-up: a transcript whose modification time is the one
    the last absorb accounted for is not opened again on the next boot; a
    touched one is."""

    def setUp(self):
        import sys
        import tempfile
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        from test_absorb import _asst_line, _user_text_line, _write_claude_project
        self.d = Path(tempfile.mkdtemp())
        self.home = self.d / "clhome"
        self.mp = str(self.d / "marker.json")
        self.lines = [_user_text_line("2026-10-01T10:00:00.000Z", "q"),
                      _asst_line("2026-10-01T10:00:01.000Z", "qwen3-coder",
                                 [{"type": "text", "text": "a"}])]
        self.more = _asst_line("2026-10-01T10:05:00.000Z", "qwen3-coder",
                               [{"type": "text", "text": "b"}])
        self.fp = _write_claude_project(self.home, self.lines, uuid="u-436-a")
        self.fp2 = _write_claude_project(self.home, self.lines, uuid="u-436-b")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _absorb(self, cap=5):
        from membrane import absorb as AB
        from membrane.server import MessageStore, SessionStore
        parsed = []
        real = AB._parse_claude_file

        def spy(fp):
            parsed.append(fp.stem)
            return real(fp)
        with mock.patch.object(AB, "_parse_claude_file", spy):
            rep = AB.absorb(SessionStore(), MessageStore(), None, cwd="/x/proj",
                            marker_path=self.mp, model_id="m", home=self.home,
                            db=self.d / "none.db", max_sessions=cap)
        return parsed, rep

    def test_unchanged_is_not_parsed_touched_is(self):
        import json
        import os
        parsed, rep = self._absorb()
        self.assertEqual(sorted(parsed), ["u-436-a", "u-436-b"])
        self.assertEqual(len(rep["imported"]), 2)
        parsed, rep = self._absorb()
        self.assertEqual(parsed, [])                    # nothing changed: nothing opened
        with open(self.fp, "a") as fh:                  # the session grew
            fh.write(json.dumps(self.more) + "\n")
        st = os.stat(self.fp)
        os.utime(self.fp, (st.st_atime, st.st_mtime + 5))
        parsed, rep = self._absorb()
        self.assertEqual(parsed, ["u-436-a"])           # only the touched one
        self.assertEqual([r["sid"] for r in rep["imported"]], ["u-436-a"])
        parsed, _ = self._absorb()
        self.assertEqual(parsed, [])

    def test_a_session_cut_by_the_cap_is_parsed_again(self):
        parsed, rep = self._absorb(cap=1)
        self.assertEqual(len(rep["imported"]), 1)
        parsed, rep = self._absorb(cap=1)
        self.assertEqual(len(parsed), 1)                # the one the cap cut is still owed a look


if __name__ == "__main__":
    unittest.main()
