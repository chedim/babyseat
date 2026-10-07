"""THE INTENT LANE, REMOUNTED (2026-10-06): the model's pen was orphaned
end-to-end -- tool_intend existed with a route but rode NEITHER the MCP
TOOLS list NOR TOOL_IMPLS, the rewritten doctrine dropped the frames,
and _intents_context_block (the OPEN INTENTIONS turn block) had ZERO
callers. The dreamer skips unsteered boards by design; the seats idle.
Pinned here:

1. THE MOUNT: 'intend' + 'intents' on the MCP surface (TOOLS + IMPLS).
2. THE BLOCK RIDES TURNS: _intents_context_block is called in the turn
   path -- the empty board carries the STEERING NUDGE (rate-limited,
   once per window), a planted board carries the OPEN INTENTIONS view.
3. THE NUDGE'S GATE: one nudge per INTENT_NUDGE_WINDOW_S window (a test
   window override proves the rate limit; planting opens it).
4. THE {intent} VERDICT (C2): an entity script planting its OWN
   commitment -- the engine create-or-pins the shared Goal + pins the
   seat's active set + announces; honest refusal when the goal layer is
   unmounted.
5. THE BOARD TOOL: the listing render (json + goal-backed shapes).
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import opencode as OC  # noqa: E402
from membrane.entities import (EntityStore, QuantumEntity,  # noqa: E402
                               CLASS_GOAL, CLASS_TOOL, MEMBRANE_NS)
from membrane.intent import IntentBoard  # noqa: E402


def _store():
    return EntityStore(bucket="test-intent-lane", shared=False)


def _board():
    return IntentBoard(Path(tempfile.mkstemp()[1]))


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out


class TestMount(unittest.TestCase):
    def test_intend_and_intents_on_the_surface(self):
        """THE MOUNT: the steering wheel's tools are on the MCP surface
        (schemas + impls) -- the orphaned-pen exhibit."""
        import membrane.mcp_server as M
        names = {t["name"] for t in M.TOOLS}
        self.assertIn("intend", names)
        self.assertIn("intents", names)
        self.assertEqual(M.TOOL_IMPLS.get("intend"), M.tool_intend)
        self.assertEqual(M.TOOL_IMPLS.get("intents"), M.tool_intents)
        for t in M.TOOLS:
            if t["name"] in ("intend", "intents"):
                self.assertIn("inputSchema", t)
                self.assertTrue(t["description"].strip())

    def test_intents_tool_relay(self):
        """The intents tool reads GET /membrane/intents (the board's
        render)."""
        import membrane.mcp_server as M
        out = M.tool_intents({}, _port_of_a_stub_serving_text(
            "Membrane intentions (0/8 open): none held."))
        self.assertIn("none held", out)
        self.assertTrue(out.startswith("Membrane intentions"))


def _port_of_a_stub_serving_text(text: str):
    """A one-response stub of the seat proxy answering GET
    /membrane/intents with {"text": ...}."""
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    box = {"port": 0}

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            b = _json.dumps({"text": text}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    box["port"] = srv.server_address[1]
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # the caller keeps the process alive thread; leak is fine for tests
    return box["port"]


class TestContextBlock(unittest.TestCase):
    def setUp(self):
        self.est = _store()
        self.board = _board()
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        self.est.upsert(now)
        self.board.attach_entity_store(self.est)
        assert self.board.selection is not None
        self.srv = type("S", (), {"_intent_board": self.board})()

    def _open_a_goal(self, text="steer me"):
        g = QuantumEntity(
            eid=f"{MEMBRANE_NS}goal:{abs(hash(text))}"[:40], cls=CLASS_GOAL,
            properties={"name": text, "summary": text, "status": "open",
                        "born": time.time()})
        g.position = [0.0, 1.0, 0.0]
        self.est.upsert(g)
        sel = self.board.selection
        assert sel is not None
        sel.pinned[g.eid] = 0.5
        return g

    def test_empty_board_carries_the_nudge_once_per_window(self):
        """THE NUDGE: empty board -> the steering nudge (the model's pen
        returned); rate-limited -- a second immediate ask returns None;
        the window expiring re-arms it."""
        b1 = OC._intents_context_block(self.srv) or ""
        self.assertIn("INTENT BOARD IS EMPTY", b1)
        self.assertIn("UNSTEERED", b1)
        b2 = OC._intents_context_block(self.srv)          # inside the window
        self.assertIsNone(b2)
        self.srv._intent_nudge_ts = 0.0                   # window expired
        b3 = OC._intents_context_block(self.srv)
        self.assertIsNotNone(b3)

    def test_open_intents_block_names_them(self):
        """A planted board renders OPEN INTENTIONS (the original block's
        contract, now actually delivered) -- and the nudge is gone."""
        self._open_a_goal("finish the executor lane")
        # the nudge's rate-limit state from another test: reset
        self.srv._intent_nudge_ts = 0.0
        blk = OC._intents_context_block(self.srv) or ""
        self.assertIn("OPEN INTENTIONS", blk)
        self.assertIn("finish the executor lane", blk)
        self.assertNotIn("INTENT BOARD IS EMPTY", blk)
        # planting RESETS the nudge stamp (the nudge's job is done):
        blk2 = OC._intents_context_block(self.srv) or ""
        self.assertIn("OPEN INTENTIONS", blk2)

    def test_no_board_is_none(self):
        r = OC._intents_context_block(type("S", (), {"_intent_board": None})())
        self.assertIsNone(r)

    def test_the_block_rides_the_turn_path(self):
        """THE REWIRE PIN: the turn path calls the builder (the orphaned
        exhibit -- defined, summarized by the surface's block map, never
        injected). Pinned at the source level."""
        import inspect
        src = (inspect.getsource(OC)
               + inspect.getsource(__import__("membrane.lanes",
                                              fromlist=["x"])))
        self.assertIn("THE INTENTIONS BLOCK", src)
        # the call follows the block's comment (the turn path's assembly)
        i = src.find("THE INTENTIONS BLOCK")
        self.assertIn("_intents_context_block(srv)", src[i:i + 400])


class TestIntentVerdict(unittest.TestCase):
    """C2: a script's {intent: ...} verdict -- the engine plants through
    the model tools' own create-or-pin lane."""

    def setUp(self):
        self.f = None
        self.est = _store()
        self.board = _board()
        self.board.attach_entity_store(self.est)
        assert self.board.selection is not None
        pool = _Pool()

        class _Membrane:
            embedder = None
            store = None

        self.srv = type("S", (), {
            "entity_store": self.est, "efield": None,
            "membrane": _Membrane(), "events_pool": pool,
            "_intent_board": self.board,
        })()

    def _plant(self, ent, text, srv=None):
        """apply the verdict dict (apply RETURNS {applied} -- the result
        stubs ride the VERDICT dict, so it's captured separately)."""
        verdict = {"intent": text}
        N.apply_entity_update(srv or self.srv, ent, verdict, trigger="tick")
        return verdict.get("intent_result") or {}

    def test_script_plants_a_goal(self):
        """{intent: text} -> the SHARED Goal entity exists, the seat's
        active set pins it, the result stub rides the script's verdict."""
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}Component:watcher-x",
                            cls="Component",
                            properties={"name": "watcher-x"})
        self.est.docs[ent.eid] = ent
        ires = self._plant(ent, "keep the announcer queue warm")
        self.assertTrue(ires.get("ok"), ires)
        self.assertTrue(ires.get("created"))
        goals = [e for e in self.est.docs.values() if e.cls == CLASS_GOAL]
        self.assertEqual(len(goals), 1)
        self.assertIn("announcer", goals[0].properties["name"])
        sel = self.board.selection
        assert sel is not None
        self.assertIn(goals[0].eid, sel.pinned)

    def test_script_intent_pins_existing_goal(self):
        """Re-planting the same DIRECTION pins (not duplicates)."""
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}Component:watcher-y",
                            cls="Component",
                            properties={"name": "watcher-y"})
        self.est.docs[ent.eid] = ent
        self._plant(ent, "warm the queue")
        self._plant(ent, "warm the queue")
        goals = [e for e in self.est.docs.values() if e.cls == CLASS_GOAL]
        self.assertEqual(len(goals), 1)

    def test_unmounted_goal_layer_is_honest(self):
        """No board: the verdict refuses cleanly (never raises)."""
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}Component:watcher-z",
                            cls="Component", properties={"name": "watcher-z"})
        self.est.docs[ent.eid] = ent
        no_board_srv = type("S", (), {
            "entity_store": self.est, "efield": None, "membrane": None,
            "events_pool": None, "_intent_board": None})()
        ires = self._plant(ent, "x", srv=no_board_srv)
        self.assertFalse(ires.get("ok"))
        self.assertIn("unavailable", ires.get("error", ""))


if __name__ == "__main__":
    unittest.main()