"""#108: the spec=/name= dialect split.

Two tools refused a value the model DID send, under the name it reaches for
instead of the schema's:

- `entity(action=add, spec=X)` and `entity_add(spec=X)` -> "name required"
  (tool_entity_add reads only `name`), while `entity_add(name=X)` works and
  `entity(action=inherit, spec=X)` already works.
- `fulfill(intent=176237)` / `fulfill(#N)` -> "spec required"
  (tool_fulfill's `_arg` list has no `intent`), while `fulfill(spec=<eid>)`
  works -- even though `intend`'s own message says "fulfill #N closes it".

`spec` is the add dialect's name and `intent` the fulfill dialect's spec;
accept both, schema name still wins.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402


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


class TestAddSpecIsName(unittest.TestCase):
    def test_entity_add_spec_is_name(self):
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_entity_add({"spec": "router.py", "class": "file"}, 0)
        self.assertEqual(h.calls[0][2]["name"], "router.py")

    def test_entity_add_name_still_wins(self):
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_entity_add({"name": "a", "spec": "b"}, 0)
        self.assertEqual(h.calls[0][2]["name"], "a")

    def test_entity_add_spec_not_flagged_ignored(self):
        reply = {"ok": True, "name": "router.py", "class": "file",
                 "eid": "membrane::Component:files:router.py",
                 "merged": False}
        with _Http(reply) as h:
            msg = M.tool_entity_add({"spec": "router.py", "class": "file"}, 0)
        self.assertNotIn("Ignored unknown arg(s): spec", msg)


class TestFulfillIntentIsSpec(unittest.TestCase):
    def test_fulfill_intent_is_spec(self):
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_fulfill({"intent": "176237", "evidence": "rc=0 output"}, 0)
        self.assertEqual(h.calls[0][2]["spec"], "176237")

    def test_fulfill_intent_hash_form(self):
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_fulfill({"intent": "#3", "evidence": "rc=0 output"}, 0)
        self.assertEqual(h.calls[0][2]["spec"], "#3")


if __name__ == "__main__":
    unittest.main()
