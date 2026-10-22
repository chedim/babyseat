"""THE PULL SURFACE (#429, PR 3): the seat machinery the banner pushes every
turn becomes pullable, before anything is removed from the push.

- `state(section=...)` (MCP + toolset family) and the route behind it
  (`GET /membrane/state?section=...`) return ONE part of WHERE WE ARE:
  tracks, commands, interfaces, classes, loops, timelines, frame, digest,
  plus `changes` (SINCE YOUR LAST MESSAGE, peeked);
- each section returns exactly the lines the banner held for the same seat
  state, and pulling one consumes nothing (no ALERT drain, no digest drain,
  no peer-message read);
- `peer(action='unread')` reads (not 'read': a nameless {"action": "read"}
  call must keep routing to entity, its one owner) the unread peer-channel messages;
- the empty-board nudge is in the `goal` tool's description;
- the banner itself is unchanged.
"""
import io
import json
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lanes as L  # noqa: E402
from membrane import mcp_server as MCP  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

LINES = {
    "commands": "your commands (most-run): ls x3, git status x2",
    "interfaces": "interfaces: tool:executor(run)",
    "classes": "class proposals: c:sensor <- 3 entities",
    "loops": "loops: 1 running, 0 queued (watcher)",
}
DIGEST = ["watch digest (2 entities past your alert budget 3/10min, mode digest -- "
          "dial it with the attention tool):", "  f:a.py moved", ]
FRAME = "frame: leaning toward w:backup"


def _seat():
    """A seat with every banner section populated (made-up content)."""
    from membrane import efield as EF
    from membrane import streams as ST
    from membrane.efstore import MemBackend
    from membrane.entities import EntityStore
    import tempfile
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    f.write_events(ST.split_turn("tool edit for: fix x1 in router.py",
                                 "Applied the failover patch", ts=time.time()), session="s")
    f.process()
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "entities.json"))
    est.watch_track("files:router.py", label="router")
    tl = types.SimpleNamespace(properties={"status": "confirmed", "terminal": "ship the fix",
                                           "branch_sim": 0.81})
    est.open_timelines = lambda limit=3: [tl]
    srv = MagicMock()
    srv.paused = False
    srv.entity_store = est
    srv.efield = f
    srv._efield_boot_seq = 0
    srv.capturer = type("C", (), {"mode": "on", "sessions": None})()
    srv._recall_null = None
    srv.peers = None
    from membrane.frame import Frame
    srv._frame = Frame.__new__(Frame)
    return srv, f


def _patched():
    from membrane.frame import Frame
    return [mock.patch("membrane.night.command_library", lambda est: LINES["commands"]),
            mock.patch("membrane.night.interfaces_line", lambda srv: LINES["interfaces"]),
            mock.patch("membrane.classes.proposals_line", lambda srv: LINES["classes"]),
            mock.patch.object(L, "loops_banner_line", lambda srv: LINES["loops"]),
            mock.patch.object(L, "_digest_lines", lambda srv, drain=True: list(DIGEST)),
            mock.patch.object(Frame, "line", lambda self: FRAME)]


class _Case(unittest.TestCase):
    def setUp(self):
        self.srv, self.f = _seat()
        self._p = _patched()
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()

    def expected(self):
        from membrane.entity_block import track_line
        t = self.srv.entity_store.list_tracks()[0]
        return {"tracks": [track_line(self.f, t["key"], t.get("label") or "")],
                "timelines": ["timeline (confirmed, branch_sim 0.81): ship the fix"],
                "loops": [LINES["loops"]], "digest": list(DIGEST), "frame": [FRAME],
                "commands": [LINES["commands"]], "interfaces": [LINES["interfaces"]],
                "classes": [LINES["classes"]]}


class TestSections(_Case):
    def test_the_banner_is_unchanged(self):
        e = self.expected()
        order = ["tracks", "timelines", "loops", "digest", "frame", "commands",
                 "interfaces", "classes"]
        want = "WHERE WE ARE:\n- " + "\n- ".join(ln for s in order for ln in e[s])
        self.assertEqual(L._where_we_are(self.srv, None, drain=False), want)

    def test_each_section_is_what_the_banner_held(self):
        banner = L._where_we_are(self.srv, None, drain=False)
        for sec, lines in self.expected().items():
            got = L.where_section(self.srv, sec)
            self.assertEqual(got, "\n".join("- " + ln for ln in lines), sec)
            for ln in lines:
                self.assertIn(ln, banner, sec)

    def test_the_sections_are_named(self):
        self.assertEqual(set(L.PULL_SECTIONS),
                         {"tracks", "commands", "interfaces", "classes", "loops",
                          "timelines", "frame", "digest", "changes"})

    def test_unknown_section_is_refused_with_the_names(self):
        with self.assertRaises(ValueError) as cm:
            L.where_section(self.srv, "vibes")
        self.assertIn("commands", str(cm.exception))

    def test_an_empty_section_is_none(self):
        with mock.patch("membrane.classes.proposals_line", lambda srv: None):
            self.assertIsNone(L.where_section(self.srv, "classes"))

    def test_a_pull_consumes_nothing(self):
        est = self.srv.entity_store
        est.take_alerts = MagicMock(return_value=[])
        est.take_announcements = MagicMock(return_value=[])
        peers = MagicMock()
        self.srv.peers = peers
        drains = []
        with mock.patch.object(L, "_digest_lines",
                               lambda srv, drain=True: drains.append(drain) or list(DIGEST)):
            for sec in L.PULL_SECTIONS:
                L.where_section(self.srv, sec)
        est.take_alerts.assert_not_called()
        est.take_announcements.assert_not_called()
        peers.fetch_unread.assert_not_called()
        peers.heartbeat.assert_not_called()
        self.assertEqual(drains, [False])

    def test_a_pull_leaves_the_banner_stage_clock_alone(self):
        self.srv._banner_stages = {"tracks": 1.0}
        L.where_section(self.srv, "commands")
        self.assertEqual(self.srv._banner_stages, {"tracks": 1.0})


class TestChanges(_Case):
    def test_changes_since_the_last_delivered_turn_peeked(self):
        from membrane import streams as ST
        from membrane.entity_block import state_diff, watermark
        mark = watermark(self.f)
        self.srv._efield_mark = dict(mark)
        self.f.write_events(ST.split_turn("tool edit for: tune the retry in gateway.py",
                                          "Raised the retry", ts=time.time()),
                            session="other-session")
        self.f.process()
        want = state_diff(self.f, mark, L._own_session_prefix(self.srv))
        self.assertTrue(want)
        self.assertEqual(L.where_section(self.srv, "changes"), want)
        self.assertEqual(self.srv._efield_mark, mark)          # the watermark did not move

    def test_no_delivered_turn_yet_no_changes(self):
        self.srv._efield_mark = None
        self.assertIsNone(L.where_section(self.srv, "changes"))


def _handler(srv, path):
    h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
    h.server = srv
    h.path = path
    h.out, h.err = [], []
    h._json_out = lambda obj: h.out.append(obj)
    h._json_err_raw = lambda code, msg: h.err.append((code, msg))
    return h


class TestRoute(_Case):
    def test_state_section_route(self):
        self.srv._state_cache = {"ts": time.time(), "data": {"cached": True}}
        h = _handler(self.srv, "/membrane/state?mcp=1&section=commands")
        h._membrane_state()
        self.assertEqual(h.out, [{"section": "commands", "text": "- " + LINES["commands"]}])

    def test_unknown_section_is_a_400(self):
        h = _handler(self.srv, "/membrane/state?section=vibes")
        h._membrane_state()
        self.assertEqual(h.out, [])
        self.assertEqual(h.err[0][0], 400)
        self.assertIn("tracks", h.err[0][1])

    def test_peer_read_route(self):
        peers = MagicMock()
        peers.fetch_unread.return_value = [{"from": "proxy-seat2", "text": "handoff: tests green",
                                            "ts": 1.0}]
        self.srv.peers = peers
        h = _handler(self.srv, "/membrane/peer/read")
        h._peer_read()
        self.assertEqual(h.out, [{"messages": [{"from": "seat2", "text": "handoff: tests green"}]}])

    def test_peer_read_without_peers_is_a_503(self):
        self.srv.peers = None
        h = _handler(self.srv, "/membrane/peer/read")
        h._peer_read()
        self.assertEqual(h.err[0][0], 503)

    def test_peer_read_is_routed(self):
        h = _handler(self.srv, "/membrane/peer/read")
        h._auth_gate = lambda: True
        h._peer_read = MagicMock()
        h.do_POST()
        h._peer_read.assert_called_once()


class TestToolSurface(unittest.TestCase):
    def test_state_tool_takes_a_section(self):
        schema = next(t for t in MCP.TOOLS if t["name"] == "state")["inputSchema"]
        self.assertEqual(set(schema["properties"]["section"]["enum"]), set(L.PULL_SECTIONS))
        seen = []

        def fake(port, method, path, body=None):
            seen.append((method, path))
            return {"section": "commands", "text": "- " + LINES["commands"]}
        with mock.patch.object(MCP, "_http", fake):
            out = MCP.tool_state({"section": "commands"}, 1)
        self.assertEqual(seen, [("GET", "/membrane/state?mcp=1&section=commands")])
        self.assertIn(LINES["commands"], out)

    def test_an_empty_section_says_so(self):
        with mock.patch.object(MCP, "_http", lambda *a, **k: {"section": "classes", "text": ""}):
            out = MCP.tool_state({"section": "classes"}, 1)
        self.assertIn("nothing", out.lower())

    def test_state_family_passes_the_section(self):
        self.assertEqual(TS.resolve("state", {"section": "tracks"}), ("state", {"section": "tracks"}))
        self.assertEqual(TS.resolve("state", {}), ("state", {}))

    def test_peer_read_tool(self):
        self.assertEqual(TS.resolve("peer", {"action": "unread"}), ("peer_read", {}))
        self.assertIn("peer_read", MCP.TOOL_IMPLS)
        # like peer_seats: reached through the peer family, not its own MCP schema
        out = {"messages": [{"from": "seat2", "text": "handoff: tests green"}]}
        with mock.patch.object(MCP, "_http", lambda *a, **k: out):
            self.assertIn("peer[seat2]: handoff: tests green", MCP.tool_peer_read({}, 1))
        with mock.patch.object(MCP, "_http", lambda *a, **k: {"messages": []}):
            self.assertIn("no unread", MCP.tool_peer_read({}, 1).lower())

    def test_the_chat_lanes_may_read_peers(self):
        from membrane import server as MS
        self.assertIn("peer_read", MS.OPERATOR_TOOLS)
        self.assertIn("peer_read", MS.PLAN_TOOLS)

    def test_the_goal_tool_carries_the_empty_board_nudge(self):
        d = TS.FAMILIES["goal"]["description"].lower()
        self.assertIn("empty board", d)
        self.assertIn("unsteered", d)
        self.assertIn("help('goals')", d)
        self.assertIn("worth keeping across sessions", TS.help_text("goals"))

    def test_the_sections_are_documented_behind_help(self):
        txt = TS.help_text("state")
        for sec in L.PULL_SECTIONS:
            self.assertIn(sec, txt)
        self.assertIn("unread", txt)
        self.assertEqual(MCP._PULL_SECTIONS, L.PULL_SECTIONS)


if __name__ == "__main__":
    unittest.main()
