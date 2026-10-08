"""THE ARGS THAT WERE SENT, ROUND 2 (2026-10-08, the transcript review, red
first). ~96 refusals for values the model DID send, under the names and
forms it reaches for:

  entity_remove got `name` (17), graph got `q` (3), entity_watch got
  `entity` and free-text boundaries (25), entity_query got `name~x`,
  `kind=c`, `class:x` (37); entity_add SILENTLY DROPPED getter_script
  (the seat 'learned' that the add-hand drops scripts); learn got `data`;
  an unknown query verb answered without the grammar.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import entity_query as Q  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


class _Http:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __enter__(self):
        self.orig = M._http

        def fake(port, method, path, body=None):
            self.calls.append((method, path, body))
            return dict(self.replies.pop(0) if self.replies else {"ok": True})
        M._http = fake
        return self

    def __exit__(self, *a):
        M._http = self.orig


class TestAliases(unittest.TestCase):
    def test_entity_remove_name(self):
        with _Http({"ok": True, "removed": 1}) as h:
            M.tool_entity_remove({"name": "c:junk"}, 0)
        self.assertEqual(h.calls[0][2]["spec"], "c:junk")

    def test_graph_q(self):
        with _Http({"ok": True, "rows": []}) as h:
            M.tool_graph({"q": "battery"}, 0)
        self.assertIn("battery", h.calls[0][1])

    def test_entity_watch_entity(self):
        with _Http({"ok": True, "key": "f:x"}) as h:
            M.tool_watch_entity({"entity": "f:x"}, 0)
        self.assertEqual(h.calls[0][2]["key"], "f:x")

    def test_learn_data(self):
        with _Http({"ok": True}) as h:
            try:
                M.tool_learn({"data": "the BMS soc lives in sensor.agatha_bms_battery"}, 0)
            except Exception:
                pass
        self.assertTrue(h.calls, "learn refused `data`")

    def test_entity_add_forwards_its_scripts(self):
        with _Http({"ok": True, "name": "x", "class": "Component", "eid": "membrane::c:x"},
                   {"ok": True}) as h:
            out = M.tool_entity_add({"class": "Component", "name": "x",
                                     "getter_script": "print(1)"}, 0)
        self.assertEqual(len(h.calls), 2, out)
        self.assertEqual(h.calls[1][2]["spec"], "membrane::c:x")
        self.assertEqual(h.calls[1][2]["getter_script"], "print(1)")

    def test_entity_add_names_what_it_ignored(self):
        with _Http({"ok": True, "name": "x", "class": "Component", "eid": "membrane::c:x"}) as h:
            out = M.tool_entity_add({"class": "Component", "name": "x", "colour": "red"}, 0)
        self.assertIn("colour", out)


class TestBoundary(unittest.TestCase):
    def test_free_forms(self):
        P = EntityStore.parse_boundary
        self.assertEqual(P("enter 3"), {"enter": 3})
        self.assertEqual(P("enter: 3"), {"enter": 3})
        self.assertEqual(P(">= 5"), {"enter": 5})
        self.assertEqual(P("above 5 cells"), {"enter": 5})
        self.assertEqual(P("< 2"), {"exit": 2})
        self.assertEqual(P("exit below 2"), {"exit": 2})
        with self.assertRaises(ValueError):
            P("when the battery is low")


class TestQueryForms(unittest.TestCase):
    def test_loose_forms_parse(self):
        self.assertEqual(Q.parse("name~battery"), [[("name~", "battery")]])
        self.assertEqual(Q.parse("name=battery"), [[("name~", "battery")]])
        self.assertEqual(Q.parse("kind=c"), [[("kind", "c")]])
        self.assertEqual(Q.parse("class:component"), [[("kind", "component")]])
        self.assertEqual(Q.parse("kind:c AND name~:bat"), [[("kind", "c"), ("name~", "bat")]])

    def test_prose_gets_the_grammar(self):
        with self.assertRaises(ValueError) as cm:
            Q.parse("list components with tick_script")
        self.assertIn("kind:", str(cm.exception))
        self.assertIn("name~:", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
