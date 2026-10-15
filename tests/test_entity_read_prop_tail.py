"""#105: entity_read truncates ring-backed lane logs at ~800 chars.

voice_io keeps bounded rings (RING=30) as entity properties
(voice:in:<person>.heard / voice:out:<person>.said, per-item text up to
600 chars). entity_read rendered the WHOLE properties blob with a
`[:800]` clip, so only the first ~2 ring items were visible and the
TAIL -- the newest entries, the ones an audit needs -- was unreachable.

`prop` extracts one property in full; `tail` pages a list-valued
property from its newest end.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402


def _ring(n=10):
    # each item's text is long enough that 10 items blow the old 800-char
    # clip -- the tail entries land past the cut and vanish from the read
    return [{"ts": i, "text": f"entry{i}|" + "x" * 100} for i in range(n)]


class _Http:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __enter__(self):
        self.orig = M._http
        M._http = lambda port, method, path, body=None: (
            self.calls.append((method, path, body)) or dict(self.reply))
        return self

    def __exit__(self, *a):
        M._http = self.orig


def _read(args, heard):
    reply = {"ok": True, "eid": "voice:in:operator", "cls": "Config",
             "properties": {"on": False, "heard": heard},
             "getter": {}}
    with _Http(reply) as h:
        return M.tool_entity_read(args, 0)


class TestPropExtract(unittest.TestCase):
    def test_prop_shows_the_full_ring_tail(self):
        msg = _read({"spec": "voice:in:operator", "prop": "heard"},
                    _ring())
        # the newest entry lands past the old 800-char cut
        self.assertIn("entry9|", msg)

    def test_prop_omits_unasked_scalars(self):
        msg = _read({"spec": "voice:in:operator", "prop": "heard"},
                    _ring())
        self.assertNotIn('"on": false', msg)


class TestPropTail(unittest.TestCase):
    def test_tail_pages_the_newest_entries(self):
        msg = _read({"spec": "voice:in:operator", "prop": "heard",
                     "tail": 2}, _ring())
        self.assertIn("entry8|", msg)
        self.assertIn("entry9|", msg)
        self.assertNotIn("entry0|", msg)


class TestPropMissing(unittest.TestCase):
    def test_missing_prop_names_what_is_available(self):
        msg = _read({"spec": "voice:in:operator", "prop": "nope"},
                    _ring())
        self.assertIn("no property 'nope'", msg)
        self.assertIn("heard", msg)


if __name__ == "__main__":
    unittest.main()
