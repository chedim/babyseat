"""Issue #442: the vector search extension must never be unloaded.

CouchbaseLiteVectorSearch registers a pthread_atfork handler. LiteCore
loads it into each SQLite connection and SQLite dlcloses it when the
connection closes, so once the last store closed, the extension was
unmapped while its handler stayed registered, and the next fork() (any
subprocess) jumped into unmapped memory: SIGSEGV. The fix pins the
extension with RTLD_NODELETE before vector search is enabled.

These tests prove it structurally; nothing here forks or crashes."""
import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402


class TestExtensionBinary(unittest.TestCase):
    def test_framework_layout(self):
        with tempfile.TemporaryDirectory() as d:
            fw = Path(d) / "CouchbaseLiteVectorSearch.framework"
            (fw / "Versions" / "A").mkdir(parents=True)
            (fw / "Versions" / "A" / "CouchbaseLiteVectorSearch").write_bytes(b"")
            self.assertEqual(cblite.extension_binary(fw),
                             fw / "Versions" / "A" / "CouchbaseLiteVectorSearch")

    def test_shared_object_layouts(self):
        for name in ("libCouchbaseLiteVectorSearch.so", "CouchbaseLiteVectorSearch.so"):
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / name).write_bytes(b"")
                self.assertEqual(cblite.extension_binary(Path(d)), Path(d) / name)

    def test_missing_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cblite.extension_binary(Path(d)))


class TestLibPinsExtension(unittest.TestCase):
    def test_pinned_with_nodelete_before_vector_search_is_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            fw = Path(d) / "CouchbaseLiteVectorSearch.framework"
            (fw / "Versions" / "A").mkdir(parents=True)
            binary = fw / "Versions" / "A" / "CouchbaseLiteVectorSearch"
            binary.write_bytes(b"")
            events = []
            lib = mock.MagicMock()
            del lib.CBL_Init                       # the desktop build: no Android init
            lib.CBL_EnableVectorSearch.side_effect = lambda *a: events.append("enable") or True

            def fake_cdll(path, mode=ctypes.DEFAULT_MODE):
                if str(path) == "libcblite":
                    return lib
                events.append(("pin", str(path), mode))
                return mock.MagicMock()

            with mock.patch.object(cblite.C, "CDLL", side_effect=fake_cdll):
                L = cblite.Lib("libcblite", str(fw))
            pins = [e for e in events if e != "enable"]
            self.assertEqual(len(pins), 1, events)
            _, path, mode = pins[0]
            self.assertEqual(Path(path), binary)
            self.assertTrue(mode & os.RTLD_NODELETE, "the pin must be RTLD_NODELETE")
            self.assertLess(events.index(pins[0]), events.index("enable"))
            self.assertIsNotNone(L._vs_pin)        # the handle lives as long as the Lib


def _loaded_images() -> list[str]:
    if sys.platform == "darwin":
        dyld = ctypes.CDLL(None)
        dyld._dyld_image_count.restype = ctypes.c_uint32
        dyld._dyld_get_image_name.restype = ctypes.c_char_p
        dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
        return [(dyld._dyld_get_image_name(i) or b"").decode(errors="replace")
                for i in range(dyld._dyld_image_count())]
    return Path("/proc/self/maps").read_text().splitlines()


@unittest.skipUnless(cblite.lib_paths() is not None and sys.platform in ("darwin", "linux"),
                     "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestExtensionSurvivesLastClose(unittest.TestCase):
    def test_extension_stays_loaded_after_every_store_closes(self):
        lib = cblite.load()
        with tempfile.TemporaryDirectory() as d:
            db = cblite.Database(lib, "pin442", d)
            open_names = {n for n in _loaded_images() if "CouchbaseLiteVectorSearch" in n}
            db.close()
        if sys.platform == "darwin":                # one image: the pin IS the one LiteCore uses
            self.assertEqual(len(open_names), 1, open_names)
        self.assertTrue(any("CouchbaseLiteVectorSearch" in n for n in _loaded_images()),
                        "the vector search extension was unloaded with its atfork handler live")


if __name__ == "__main__":
    unittest.main()
