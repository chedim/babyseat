# THE CARD'S NAMES SURVIVE EVERY SPELLING THE WIRE CAUGHT (issue #24).
# The seat card advertises the typed tool registry; nothing taught the
# resolver -- or the honest refusal -- that the card's names are the wire's
# names, so the lost seat's wire refused 112 calls across 63 invented
# spellings of names the card had invited (entity-read, membranes-exec,
# membrane exec, functions._name_0). Every non-retired registry name must
# pass the operator gate, the card must not render a retired tool, and the
# spellings caught in the seat's own message store must route.
import unittest

from membrane import lanes
from membrane import mcp_server as M
from membrane import server as S
from membrane import toolset as T


class TestEveryCardNameResolves(unittest.TestCase):
    """THE INVARIANT (issue #24's expect branch 1) -- every name the card
    lists is a name the gate lets through on the operator lane."""

    def test_the_registry_names_pass_the_gate(self):
        for t in M.TOOLS:
            n = t["name"]
            if n in T.RETIRED:
                continue
            known = n in T.FAMILIES or n in set(T._LEGACY_FAMILY)
            self.assertTrue(known, f"registry name {n!r} is not on the wire")
            try:
                rl, _ = T.resolve(n, {})
                self.assertIsNone(S._lane_refusal(rl, "operator"),
                                  f"registry name {n} refused as {rl!r}")
            except ValueError as e:
                # a bare family name without a no-action default refuses
                # honestly, naming its actions -- a known tool, a wrong use
                self.assertIn("actions:", str(e), n)

    def test_the_card_never_renders_a_retired_tool(self):
        card = "\n".join(lanes._mcp_tool_lines())
        self.assertNotIn("junk_cut", card)


class TestTheCaughtSpellingsRoute(unittest.TestCase):
    """The invented spellings the store caught -- each refused on the old
    wire -- are the card's own names underneath."""

    def test_the_dashed_names_are_the_typed_tools(self):
        self.assertEqual(T.resolve("entity-read", {"spec": "presence"}),
                         ("entity_read", {"spec": "presence"}))
        self.assertEqual(T.resolve("entity-watch", {"name": "s"}),
                         ("entity_watch", {"name": "s"}))
        self.assertEqual(T.resolve("field-watch", {"topic": "bats"})[0],
                         "field_watch")

    def test_the_tongue_slip_and_space_prefixed_spellings(self):
        self.assertEqual(T.resolve("membranes-exec", {"action": "call", "object": "o",
                                                      "method": "m"}),
                         ("method_call", {"object": "o", "method": "m"}))
        self.assertEqual(T.resolve("membrane exec", {"action": "call", "object": "o",
                                                     "method": "m"})[0],
                         "method_call")

    def test_the_unnamed_call_artifact_routes_by_action(self):
        rl, _ = T.resolve("functions._name_0", {"action": "read",
                                                "name": "presence"})
        self.assertEqual(rl, "entity_read")

    def test_the_empty_action_form_refuses_honestly(self):
        with self.assertRaises(ValueError) as cm:
            T.resolve("entity/action", {})
        self.assertIn("entity: unknown action", str(cm.exception))


if __name__ == "__main__":
    unittest.main()