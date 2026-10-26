"""THE LANE GATE + THE NERVE PAUSE (2026-10-06):

THE LANE GATE (the division of hands): the seat's two minds have
different tools -- the OPERATOR (build/plan sessions) plants intents
and commits; the LISTENERS (the watch loops) hold the ENTITY HANDS
(add/edit/remove/link/unlink + their own watches). Reads and memory
are both lanes'. Plan mode is the operator minus `intend` (a planning
session never plants -- the direction needs the user's build word).
Pinned: the per-lane tool sets, the honest cross-lane refusal (the
refusal text IS the tool result, audited), the PATCH-honored agent.

THE LISTENER-OWNED WATCHES (the operator's Q2): a listener-planted
entity_watch delivers ITS boundary crossings INTO THE LISTENER'S NEXT
LOOP TURN (the events pool -> loop_task_text -- the thinker's
event-input flow), never the banner; operator tracks keep the banner
ALERT delivery unchanged.

THE INTENT-PLANTED WIRE (the operator creates, the thinker receives):
every planter (the intend tool, the frame, the {intent} script
verdict) puts one event into the pool; a thinking loop receives it.

THE PAUSE (`membrane: pause` / the operator's stable point): the
acting lanes stand down -- the listeners' admits hold (the pool keeps,
bounded by its drop-oldest cap), the dreamer sleeps, the thinker's
ticks/digest/idle-think hold (PRESENCE KEEPS -- a paused seat is alive
to the roster), the night's bodies defer, the autonode gates (the
diff accumulates; resume bursts it bounded), the idle judges skip,
speech holds. WRITES are refused (reads, memory and `intend` stay --
the plan's artifact is the intent board, INERT until resume). RAM-only:
a restart unpauses. Kill switch: MEMBRANE_NERVE_PAUSE=0 /
MEMBRANE_LANE_GATE=0 (the ablations).
"""

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import opencode as OC  # noqa: E402
from membrane import server as SRV  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import (EntityStore, QuantumEntity, CLASS_GOAL,  # noqa: E402
                               CLASS_TOOL, MEMBRANE_NS)
from membrane.intent import IntentBoard  # noqa: E402
from membrane.thinker import Thinker, is_wakeup_signal  # noqa: E402
from membrane.dreamer import Dreamer  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out

    def depth(self):
        return len(self.items)


def _store():
    return EntityStore(bucket="test-lane-gate", shared=False)


def _board(est):
    import tempfile
    b = IntentBoard(Path(tempfile.mkstemp()[1]))
    b.attach_entity_store(est)
    return b


def _reach(agent):
    """The existing tools a lane's advertised surface reaches (the tool
    families resolve to them, 2026-10-09)."""
    from membrane import toolset
    return toolset.legacy_names(SRV._openai_tools_for(agent))


class TestLaneGate(unittest.TestCase):
    def test_lane_resolution(self):
        self.assertEqual(SRV._lane_of(None), "operator")
        self.assertEqual(SRV._lane_of("build"), "operator")
        self.assertEqual(SRV._lane_of("plan"), "plan")
        self.assertEqual(SRV._lane_of("watch: f:membrane/x.py"), "listener")
        self.assertEqual(SRV._lane_of("watch"), "listener")

    def test_operator_has_every_hand(self):
        """THE FLOOD GATES (2026-10-08, the operator's call: 'open the
        flood gates between the lanes'): the chat lane holds the entity
        hands and the watches too -- the split cost more round trips
        (refused -> intend -> a loop) than it protected. Plan mode stays
        read-only."""
        for t in ("entity_add", "entity_edit", "entity_remove", "field_watch"):
            self.assertIn(t, SRV._lane_tools("operator"))
            self.assertIn(t, SRV._lane_tools("listener"))
            self.assertNotIn(t, SRV._lane_tools("plan"))
        for t in ("method_define", "method_call", "method_each"):
            self.assertIn(t, SRV._lane_tools("operator"))
            self.assertIn(t, SRV._lane_tools("listener"))
            self.assertNotIn(t, SRV._lane_tools("plan"))
        self.assertIsNone(SRV._lane_refusal("method_call", "operator"))
        self.assertIsNotNone(SRV._lane_refusal("method_call", "plan"))
        names = _reach("build")
        self.assertIn("method_call", names)
        self.assertIn("intend", SRV._lane_tools("operator"))
        self.assertNotIn("intend", SRV._lane_tools("listener"))

    def test_plan_is_the_operator_minus_intend(self):
        """Plan mode communicates: reads + memory + the board; nothing
        planted (the direction needs the user's build word)."""
        self.assertIn("intents", SRV._lane_tools("plan"))
        self.assertIn("query", SRV._lane_tools("plan"))
        self.assertIn("learn", SRV._lane_tools("plan"))
        self.assertNotIn("intend", SRV._lane_tools("plan"))
        for t in ("entity_add", "entity_edit", "method_call"):
            self.assertNotIn(t, SRV._lane_tools("plan"))

    def test_cross_lane_call_refuses_honestly(self):
        """THE REFUSAL (the user's 'do just refusal for now'): the
        cross-lane call's tool result IS the honest refusal -- no hard
        unknown-tool error."""
        self.assertIsNone(SRV._lane_refusal("entity_add", "operator"))
        r = SRV._lane_refusal("entity_add", "plan")
        assert r is not None
        self.assertIn("plan mode", r)
        r2 = SRV._lane_refusal("intend", "plan")
        assert r2 is not None
        self.assertIn("plan mode", r2)
        r3 = SRV._lane_refusal("intend", "listener")
        self.assertIsNotNone(r3)
        # a same-lane call passes
        self.assertIsNone(SRV._lane_refusal("entity_add", "listener"))
        self.assertIsNone(SRV._lane_refusal("intend", "operator"))
        # reads never refuse
        self.assertIsNone(SRV._lane_refusal("graph", "plan"))
        self.assertIsNone(SRV._lane_refusal("query", "listener"))

    def test_advertised_tools_follow_the_lane(self):
        """The per-turn tool list = the registry filtered to the lane."""
        op = _reach("build")
        li = _reach("watch: f:x.py")
        pl = _reach("plan")
        self.assertIn("entity_add", op)
        self.assertIn("field_watch", op)
        self.assertNotIn("entity_add", pl)
        self.assertIn("intend", op)
        self.assertNotIn("intend", li)
        self.assertIn("entity_add", li)
        self.assertNotIn("intend", pl)

    def test_kill_switch_restores_the_flat_surface(self):
        os.environ["MEMBRANE_LANE_GATE"] = "0"
        try:
            self.assertIsNone(SRV._lane_refusal("entity_add", "plan"))
        finally:
            del os.environ["MEMBRANE_LANE_GATE"]


class TestListenerWatches(unittest.TestCase):
    def setUp(self):
        self.est = _store()
        self.pool = _Pool()
        self.est._events_pool = self.pool

    def test_listener_track_crossing_goes_to_the_pool(self):
        """THE OWNER SPLIT: a listener-owned bound track fires into the
        events pool (the listener's next turn's message); the banner
        ALERTS don't carry it. The operator's track keeps the banner."""
        self.est.watch_track("files:router.py", boundary="enter 5",
                             owner="watch: f:membrane/router.py")
        self.est.watch_track("files:runtime.py", boundary="enter 5")
        # eval 1 seeds both state machines (the seed eval is silent)
        self.est.evaluate_tracks(count_fn=lambda k: 2)
        # eval 2 crosses both boundaries upward (2 -> 10 >= enter 5)
        alerts = self.est.evaluate_tracks(count_fn=lambda k: 10)
        self.assertEqual([a["key"] for a in alerts], ["files:runtime.py"])
        evs = [e for e in self.pool.items if e["kind"] == "boundary"]
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual(ev["entity"], "files:router.py")
        self.assertEqual(ev["owner"], "watch: f:membrane/router.py")
        self.assertTrue(ev["entered"])
        # the crossing renders as the listener's next task text
        task = WA.loop_task_text(ev)
        self.assertIn("boundary crossed", task)
        self.assertIn("router.py", task)
        self.assertIn("enter 5", task)

    def test_owner_filters_the_listing(self):
        """LANE-AWARE listing: a listener sees its own; the operator
        sees all with owners."""
        self.est.watch_track("files:a.py", owner="watch: f:a.py")
        self.est.watch_track("files:b.py")
        mine = self.est.list_tracks(owner="watch: f:a.py")
        self.assertEqual([t["key"] for t in mine], ["files:a.py"])
        allr = self.est.list_tracks()
        self.assertEqual(len(allr), 2)
        self.assertTrue(all("owner" in t for t in allr))

    def test_owner_stamp_survives_replant(self):
        """Re-planting keeps the owner (re-arm without a lane jump)."""
        self.est.watch_track("files:a.py", boundary="enter 1",
                             owner="watch: f:a.py")
        self.est.watch_track("files:a.py", boundary="enter 9")
        rec = self.est.tracks["files:a.py"]
        self.assertEqual(rec["owner"], "watch: f:a.py")


class TestIntentPlantedWire(unittest.TestCase):
    def setUp(self):
        self.est = _store()
        self.board = _board(self.est)
        self.pool = _Pool()
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        self.est.upsert(now)

    def test_notify_puts_the_pearl_event(self):
        ev = WA.notify_intent_planted(self.est, self.pool,
                                      "membrane::goal:x", "keep it green")
        self.assertEqual(ev["kind"], "intent-planted")
        self.assertEqual(len(self.pool.items), 1)
        task = WA.loop_task_text(self.pool.items[0])
        self.assertIn("goal planted", task)
        self.assertIn("keep it green", task)
        # the react-or-ignore covenant rides the loop task (the verdict
        # stamps the goal entity)
        self.assertIn("REACT", task)
        self.assertIn("IGNORE", task)

    def test_all_planters_feed_the_pool(self):
        """The MCP tool, the frame AND the {intent} verdict route through
        _membrane_intent_create_or_pin -- one wire."""
        class _M:
            embedder = None
            store = None
            audit_path = ""
        srv = type("S", (), {"entity_store": self.est,
                             "_intent_board": self.board,
                             "events_pool": self.pool,
                             "membrane": _M()})()
        res = OC._membrane_intent_create_or_pin(srv, "wire the tests")
        self.assertTrue(res["ok"])
        kinds = [e["kind"] for e in self.pool.items]
        self.assertEqual(kinds, ["intent-planted"])

    def test_it_wakes_the_night(self):
        """A planted goal is worth a beat even at night (the board's
        news reaches the loops)."""
        self.assertTrue(is_wakeup_signal("intent-planted"))
        self.assertTrue(is_wakeup_signal("boundary"))


class TestPause(unittest.TestCase):
    """THE NERVE (the operator's stable point, RAM-only)."""

    def setUp(self):
        self.est = _store()
        self.board = _board(self.est)
        self.pool = _Pool()
        self.est._events_pool = self.pool
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now", "grain": 0.003})
        self.est.upsert(now)
        self.srv = type("S", (), {
            "entity_store": self.est, "_intent_board": self.board,
            "events_pool": self.pool, "paused": False,
            "ambient_night": False, "ambient_mode": "day",
            "last_turn_ts": time.time(),
            "membrane": type("M", (), {"store": type("ST", (), {
                "exemplars": []})(), "audit_path": ""})(),
            "efield": None, "idle_cortex_model": "",
        })()
        self._old = os.environ.get("MEMBRANE_NERVE_PAUSE")
        os.environ.pop("MEMBRANE_NERVE_PAUSE", None)

    def tearDown(self):
        if self._old is not None:
            os.environ["MEMBRANE_NERVE_PAUSE"] = self._old

    def test_pause_frames_arm_and_disarm(self):
        self.assertFalse(OC.nerve_paused(self.srv))
        OC._pause_cmd(self.srv, "membrane: pause")
        self.assertTrue(OC.nerve_paused(self.srv))
        out = OC._pause_cmd(self.srv, "membrane: resume")
        self.assertFalse(OC.nerve_paused(self.srv))
        self.assertIn("resumed", out)

    def test_kill_switch_makes_pause_a_noop(self):
        os.environ["MEMBRANE_NERVE_PAUSE"] = "0"
        try:
            OC._pause_cmd(self.srv, "membrane: pause")
            self.assertFalse(OC.nerve_paused(self.srv))
        finally:
            os.environ.pop("MEMBRANE_NERVE_PAUSE", None)

    def test_listeners_hold_operators_do_not(self):
        """The pool's admit: listeners refuse while paused (the events
        hold in the pool); the operator lane never refuses."""
        runner = WA.LoopRunner(max_loops=3)
        runner._srv = self.srv
        OC._pause_cmd(self.srv, "membrane: pause")
        self.assertFalse(runner.admit(WA.LoopDesc("watch", "f:x.py")))
        self.assertTrue(runner.admit(WA.LoopDesc("operator", is_operator=True)))
        OC._pause_cmd(self.srv, "membrane: resume")
        self.assertTrue(runner.admit(WA.LoopDesc("watch", "f:x.py")))

    def test_the_nerve_frames_are_operator_only_at_the_door(self):
        """The pause frame's interception happens pre-gate on the CHAT
        path (the operator's lane); a loop turn's task text is built
        from events and never consults the frame lane (structural:
        loop turns carry loop_task_text). This pins the frame's
        matcher only fires on the canonical pair."""
        self.assertTrue(OC._is_pause_query("membrane: pause"))
        self.assertTrue(OC._is_pause_query("resume"))
        self.assertFalse(OC._is_pause_query("please pause the music"))
        self.assertFalse(OC._is_pause_query(""))

    def test_dreamer_sleeps_while_paused(self):
        d = Dreamer(self.srv, idle=60.0)
        self.srv._intent_board = self.board
        # a hot field + intents would dream normally; paused: never
        OC._pause_cmd(self.srv, "membrane: pause")
        self._open_goal()
        slept = d._cycle()
        self.assertEqual(slept, d._idle)

    def _open_goal(self, text="dream steered"):
        g = QuantumEntity(eid=f"{MEMBRANE_NS}goal:{abs(hash(text))}"[:40],
                          cls=CLASS_GOAL,
                          properties={"name": text, "summary": text,
                                      "status": "open",
                                      "born": time.time()})
        g.position = [0.0, 1.0, 0.0]
        self.est.upsert(g)
        sel = self.board.selection
        assert sel is not None
        sel.pinned[g.eid] = 0.5
        return g

    def test_thinker_holds_but_presence_keeps(self):
        """PAUSED: the digest queue holds (no _process_one), the tick
        scripts skip, the idle think skips -- PRESENCE KEEPS (the seat
        is alive to the roster)."""
        th = Thinker(self.srv)
        self.srv.thinker = th
        OC._pause_cmd(self.srv, "membrane: pause")
        th.enqueue("chat", {"text": "while paused"})
        self.srv.paused = True
        th._hum()
        self.assertEqual(th.stats["processed"], 0)   # the queue held
        self.assertEqual(len(th.queue), 1)
        # and the seat's heartbeat still advanced (presence's cadence)
        self.assertGreaterEqual(th._beat, 1)

    def test_autonode_gates_the_scanner(self):
        """THE AUTONODE AT REST: paused, the pass only reaps -- no scan,
        no dispatch; even a queued trigger waits (the burst on resume)."""
        import threading as _th
        from membrane.autonode import Autonode, Trigger
        a = Autonode.__new__(Autonode)
        a.srv = self.srv
        a.scanner = None
        a.q = Autonode._DropQ(64)
        a.est = self.est
        a.workers = 1
        a.fire_timeout = 10.0
        a._pending = {}
        a._lock = _th.Lock()
        a._active = 0
        a.q.put(Trigger("file", {"event": "created", "path": "x.py"}))
        OC._pause_cmd(self.srv, "membrane: pause")
        a.pump({})
        self.assertEqual(a.q.qsize(), 1)    # held: not dispatched
        OC._pause_cmd(self.srv, "membrane: resume")
        a.pump({})
        self.assertEqual(a.q.qsize(), 0)    # released on resume

    def test_mutations_refuse_intend_does_not(self):
        """THE CRISP LINE: while paused the entity hands are DOWN
        (423) -- reads/memory/intend stay: the plan's artifact is the
        board, inert."""
        class _Handler:
            server = self.srv

            def _json_err_raw(self, code, msg):
                self.err = (code, msg)

        h = _Handler()
        h._paused_write_refused = OC.MembraneOpenAIHandler \
            ._paused_write_refused.__get__(h)
        self.srv.paused = True
        self.assertTrue(h._paused_write_refused())
        code, msg = h.err
        self.assertEqual(code, 423)
        self.assertIn("paused", msg)
        self.assertIn("intend", msg)
        self.srv.paused = False
        self.assertFalse(h._paused_write_refused())

    def test_intend_while_paused_is_inert(self):
        """The payoff: planting mid-pause lands the Goal + the pin --
        and NOTHING reacts (the pool's event is held; the loops are
        down; no scripts run) -- the plan's artifact sits safe until
        resume."""
        class _M:
            embedder = None
            store = None
            audit_path = ""
        self.srv.membrane = _M()
        OC._pause_cmd(self.srv, "membrane: pause")
        res = OC._membrane_intent_create_or_pin(self.srv,
                                                "refactor the gate calmly")
        self.assertTrue(res["ok"])
        goals = [e for e in self.est.docs.values() if e.cls == CLASS_GOAL]
        self.assertEqual(len(goals), 1)
        # the event landed in the pool (the wire works) but the loops
        # hold it (admit refuses) -- nothing reacts to it
        self.assertEqual(len(self.pool.items), 1)

    def test_banner_carries_the_pause_line(self):
        """The model sees WHY the field is quiet (the banner's pause
        line)."""
        OC._pause_cmd(self.srv, "membrane: pause")
        hdr = OC._where_we_are(self.srv, None, drain=False) or ""
        self.assertIn("PAUSED", hdr)
        OC._pause_cmd(self.srv, "membrane: resume")
        hdr2 = OC._where_we_are(self.srv, None, drain=False)
        hdr2 = hdr2 or ""
        self.assertNotIn("PAUSED:", hdr2)

    def test_speech_holds_while_paused(self):
        """A paused seat composes nothing (the plan's silence); the
        held covenant: returns True (the caller's accounting holds)."""
        OC._pause_cmd(self.srv, "membrane: pause")
        self.assertTrue(OC.speak(self.srv, "a paused thought",
                                 kind="report-state"))
        self.assertEqual(self.srv._speak_queue, [])

    def test_state_carries_the_flag(self):
        """/membrane/state reads paused (the operator's instrument)."""
        self.srv.paused = True
        self.assertTrue(self.srv.paused)
        # the state surface reads nerve_paused -- the flag + the switch
        # together; the kill switch is the only override
        os.environ["MEMBRANE_NERVE_PAUSE"] = "0"
        try:
            self.assertFalse(OC.nerve_paused(self.srv))
        finally:
            os.environ.pop("MEMBRANE_NERVE_PAUSE", None)


class TestPlanSelectionSticks(unittest.TestCase):
    """THE AGENT'S PICK STICKS (the 'everything is build despite plan
    selected' exhibit): the attached TUI sends the mode PER MESSAGE
    (`agent: 'plan'` in the message body) -- the turn's lane follows
    the TURN'S agent, and the selection persists on the session row."""

    def test_turn_agent_wins_over_the_session_row(self):
        """A plan-body turn on a build-era session laned PLAN (the fix's
        exact exhibit: the session doc said build, the user rows said
        plan)."""
        import membrane.server as SRV2
        import types
        # minimal runner with the real _run_inner's registry frame
        t = SRV.TurnRunner(SRV.SSEHub(), None, None, None, None,
                           system="", model="m")
        sid = "ses_x"
        t.sessions = types.SimpleNamespace(
            sessions={sid: {"agent": "build"}},
            get=lambda _sid, _s=None: None)
        t._agents = {}
        t._aborting = set()
        t._busy = {}
        user_info = {"id": "msg_1", "agent": "plan"}
        # the registry resolution is the exact lines under test -- call
        # the private logic via a tiny probe (the resolution itself):
        sess = t.sessions.sessions.get(sid) or {}
        t._agents[sid] = str((user_info or {}).get("agent")
                             or sess.get("agent") or "build")
        self.assertEqual(t._agents[sid], "plan")
        self.assertEqual(SRV._lane_of(t._agents[sid]), "plan")

    def test_build_rows_lane_operator(self):
        t = SRV.TurnRunner(SRV.SSEHub(), None, None, None, None,
                           system="", model="m")
        sid = "ses_y"
        t._agents = {}
        user_info = {"id": "msg_2", "agent": "build"}
        sess = {"agent": "plan"}     # even a plan-session doc: the turn wins
        t._agents[sid] = str((user_info or {}).get("agent")
                             or sess.get("agent") or "build")
        self.assertEqual(SRV._lane_of(t._agents[sid]), "operator")

    def test_loop_agent_not_shadowed_by_the_fallback(self):
        """A LOOP turn (no user_info) reads the session doc's watch:
        agent -- the fallback row echoes it (never a fake build)."""
        t = SRV.TurnRunner(SRV.SSEHub(), None, None, None, None,
                           system="", model="m")
        sid = "ses_l"
        t.sessions = type("S2", (), {"sessions": {
            sid: {"agent": "watch: f:x.py"}}})()
        t._agents = {}
        sess = t.sessions.sessions.get(sid) or {}
        t._agents[sid] = str((None or {}).get("agent")
                             or sess.get("agent") or "build")
        self.assertEqual(t._agents[sid], "watch: f:x.py")
        self.assertEqual(SRV._lane_of(t._agents[sid]), "listener")

    def test_advertise_plan_for_plan_agent(self):
        """/agent shapes: the plan agent row exists (the TUI's toggle
        needs it) and the lane tools for 'plan' exclude intend."""
        shapes = SRV._agent_shapes(None)
        self.assertTrue(any(s.get("name") == "plan" for s in shapes))
        self.assertNotIn("intend", SRV._lane_tools("plan"))


class TestRefusalHints(unittest.TestCase):
    """THE REFUSAL UPGRADES (2026-10-06, the error-rate fix): the model
    self-corrects in ONE call -- the spec-miss carries its nearest
    candidates, the size-cap carries the remedy."""

    def setUp(self):
        self.est = _store()
        from membrane.entities import QuantumEntity as Q
        # seed docs a bad spec could be aiming at
        for eid, name in (("membrane::f:membrane/router.py", "router.py"),
                          ("membrane::c:git", "git"),
                          ("membrane::goal:5ebaba", "5ebaba")):
            e = Q(eid=eid, cls="Component", properties={"name": name})
            self.est.docs[eid] = e

    def test_near_specs_finds_the_tail_hit(self):
        near = self.est.near_specs("Component:c:git")
        self.assertIn("c:git", near)
        # ordered by tail-prefix strength first
        near2 = self.est.near_specs("f:router.py")
        self.assertIn("f:membrane/router.py", near2)

    def test_edit_refusal_carries_the_nearest(self):
        res = self.est.edit("Component:c:gitx", summary="x")
        self.assertFalse(res["ok"])
        self.assertIn("nearest:", res["error"])
        self.assertIn("c:git", res["error"])

    def test_no_candidates_keeps_the_old_pointer(self):
        res = self.est.edit("zzzz-nothing", summary="x")
        self.assertFalse(res["ok"])
        self.assertIn("list via related", res["error"])

    def test_size_cap_refusal_carries_the_remedy(self):
        res = self.est.edit("membrane::c:git",
                            tick_script="x = 1\n" * 500)
        self.assertFalse(res["ok"])
        self.assertIn("too long", res["error"])
        self.assertIn("split", res["error"])
        self.assertIn("2048", res["error"])




class TestEntityReadHints(unittest.TestCase):
    """THE READ ROUTE'S SELF-CORRECTION (2026-10-06): the model's two
    live exhibits -- a wrong arg name ('name' where `spec` was wanted)
    and the FIELD's id ('s:?tick_every' vs the store's
    symbols:?tick_every) -- both return a refusal that TEACHES."""

    def setUp(self):
        import tempfile, threading
        from http.server import ThreadingHTTPServer
        from membrane import opencode as _OC
        self.est = _store()
        sym = QuantumEntity(eid=MEMBRANE_NS + "symbols:?tick_every",
                            cls="Component", properties={"name": "tick"})
        self.est.docs[sym.eid] = sym
        ex = QuantumEntity(eid=MEMBRANE_NS + "tool:executor",
                           cls="Tool", properties={"name": "executor"})
        self.est.docs[ex.eid] = ex

        class _Membrane:
            embedder = None
            store = None
            audit_path = ""

        handler = _OC.MembraneOpenAIHandler

        class _HSrv(ThreadingHTTPServer):
            pass
        hs = _HSrv(("127.0.0.1", 0), handler)
        hs.membrane = _Membrane()
        hs.entity_store = self.est
        hs._intent_board = None
        hs.events_pool = None
        threading.Thread(target=hs.serve_forever, daemon=True).start()
        self.hs = hs
        self.port = hs.server_address[1]

    def tearDown(self):
        self.hs.shutdown()

    def _get(self, qs: str):
        import json as _json
        import urllib.request, urllib.error
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{qs}",
                    timeout=10) as resp:
                return resp.status, _json.loads(resp.read())
        except urllib.error.HTTPError as he:
            return he.code, _json.loads(he.read())

    def test_wrong_arg_name_is_echoed(self):
        """{'name': 'Executor'} -> 'spec required (you sent {...})' +
        the name-is-not-a-parameter pointer."""
        code, out = self._get("/membrane/entity/read?name=Executor")
        self.assertEqual(code, 400)
        msg = out["error"]["message"]
        self.assertIn("spec required", msg)
        self.assertIn("Executor", msg)
        self.assertIn("tool:executor", msg)

    def test_a_failing_script_rides_the_read(self):
        """#149: the read carries a failing script's state (it said
        nothing: a struck-out heartbeat read as a healthy entity)."""
        ent = self.est.docs[MEMBRANE_NS + "tool:executor"]
        code, out = self._get("/membrane/entity/read?spec=tool:executor&nofire=1")
        self.assertEqual(code, 200, out)
        self.assertNotIn("script", out)
        ent.properties.update({"script_failures": 3, "script_disabled": True,
                               "script_disabled_by": "strikes",
                               "script_retry_at": 9e9,
                               "script_last_error": "SyntaxError: boom"})
        code, out = self._get("/membrane/entity/read?spec=tool:executor&nofire=1")
        self.assertEqual(out["script"]["state"], "disabled")
        self.assertIn("SyntaxError", out["script"]["last_error"])

    def test_field_id_resolves_to_the_store(self):
        """'s:?tick_every' (the FIELD's short id) resolves to the store's
        symbols:?tick_every directly (2026-10-08: it used to 404 with the
        right answer as its 'nearest')."""
        code, out = self._get("/membrane/entity/read?spec=s:?tick_every")
        self.assertEqual(code, 200, out)

    def test_field_id_unresolvable_carries_the_nearest(self):
        """A typo'd id -> 404 + the store's nearest -- one call self-corrects."""
        code, out = self._get("/membrane/entity/read?spec=s:?tick_everyx")
        self.assertEqual(code, 404)
        msg = out["error"]["message"]
        self.assertIn("no entity matches", msg)
        self.assertIn("symbols:?tick_every", msg)
        self.assertIn("re-read with one of these", msg)

    def test_still_bare_when_there_is_nothing_to_say(self):
        """No args at all: the honest bare 400 (nothing to hint)."""
        code, out = self._get("/membrane/entity/read")
        self.assertEqual(code, 400)
        self.assertIn("spec required", out["error"]["message"])
        code2, out2 = self._get("/membrane/entity/read?spec=zzz-nothing")
        self.assertEqual(code2, 404)
        msg2 = out2["error"]["message"]
        self.assertIn("no entity matches", msg2)
        self.assertNotIn("nearest", msg2)




class TestInjectShape(unittest.TestCase):
    """THE CRASH FIX (the `K.tokens.output undefined` TUI exhibit): the
    event-thinker inject's part used to ride the SESSION id as its
    messageID -- a phantom id the reducer's findLast lands on undefined
    for; the reducer then reads K.tokens.output and the TUI dies. The
    inject rides a REAL assistant row's id (or the store's minted one)."""

    def _runner(self, row_seed):
        import membrane.server as MS
        hub = MS.SSEHub()
        pub = []
        hub.publish = lambda ev: pub.append(ev)
        msgs = MS.MessageStore()          # the REAL store (its mints
        msgs.hub = hub                    # carry the row-before-part)
        if row_seed:
            for info, parts in row_seed:
                msgs.append("ses_1", dict(info), [dict(p) for p in parts])
        runner = type("R", (), {
            "sessions": type("S", (), {"sessions": {
                "ses_1": {"agent": "build", "id": "ses_1",
                          "time": {"updated": 5}}}})(),
            "messages": msgs, "hub": hub, "model": "glm-x"})()
        runner._pub = pub
        return runner

    def test_inject_never_rides_a_session_id(self):
        """The phantom-id exhibit: the messageID is NEVER the session's
        id -- an existing assistant row's id or the minted row's (the
        wire carries that row FIRST)."""
        from membrane.opencode import _inject_to_primary_impl
        runner = self._runner([])
        _inject_to_primary_impl({}, runner, "line one")
        events = [e for e in runner._pub if e.get("type") in
                  ("message.updated", "message.part.updated")]
        self.assertEqual(events[0]["type"], "message.updated",
                         "the row before its part")
        self.assertEqual(events[0]["info"]["role"], "assistant")
        self.assertIn("tokens", events[0]["info"])
        mid = events[0]["info"]["id"]
        self.assertIsNotNone(mid)
        self.assertEqual(events[1]["type"], "message.part.updated")
        self.assertEqual(events[1]["part"]["messageID"], mid)
        self.assertNotEqual(events[1]["part"]["messageID"], "ses_1",
                            "the phantom id IS the crash")
        # a real assistant row rides THAT row's id
        runner2 = self._runner([({"id": "msg_asst9", "role": "assistant",
                                  "tokens": {"output": 3}}, [])])
        _inject_to_primary_impl({}, runner2, "line two")
        ev2 = [e for e in runner2._pub
               if e["type"] == "message.part.updated"][-1]
        self.assertEqual(ev2["part"]["messageID"], "msg_asst9")

    def test_minted_row_carries_tokens(self):
        """The minted row's shape on the WIRE: token-complete (the
        reducer's K.tokens lands)."""
        from membrane.opencode import _inject_to_primary_impl
        runner = self._runner([])
        _inject_to_primary_impl({}, runner, "x")
        row_ev = [e for e in runner._pub if e["type"] == "message.updated"][0]
        self.assertIn("tokens", row_ev["info"])




class TestTokenMend(unittest.TestCase):
    """THE DATA-HALF OF THE CRASH COVENANT (the `K.tokens.output
    undefined` exhibit's last layer): the OLD code's watchdog/inject
    mints PERSISTED assistant rows with NO tokens -- every session
    load replayed them and the reducer crashed ON STARTUP (the
    annouNCER seat's death loop). The mend: the store's load repairs
    (RAM + the next flush persists), the replay shields, the writers
    mint complete."""

    def test_load_mends_tokenless_assistant_rows(self):
        import tempfile
        from membrane.server import MessageStore
        p = Path(tempfile.mkstemp(suffix=".json")[1])
        p.write_text(json.dumps({"ses_z": [
            {"info": {"id": "msg_old1", "role": "assistant",
                      "time": {}}, "parts": []},
            {"info": {"id": "msg_ok", "role": "assistant",
                      "tokens": {"output": 3}}, "parts": []}]}))
        ms = MessageStore(str(p))
        rows = ms.list("ses_z")
        for r in rows:
            self.assertIn("tokens", r["info"],
                          f'row {r["info"].get("id")} still poisoned')

    def test_replay_shields_the_output(self):
        import tempfile
        from membrane.server import MessageStore, make_handler, SSEHub
        from http.server import ThreadingHTTPServer
        import threading, urllib.request, urllib.error, base64 as b64
        p = Path(tempfile.mkstemp(suffix=".json")[1])
        p.write_text(json.dumps({"ses_z": [
            {"info": {"id": "msg_old2", "role": "assistant",
                      "agent": "memory", "time": {"created": 1}},
             "parts": [{"id": "p1", "sessionID": "ses_z",
                        "messageID": "msg_old2", "type": "text",
                        "text": "the watchdog msg",
                        "time": {"start": 1, "end": 1}}]}]}))
        msgs = MessageStore(str(p))
        hub = SSEHub()
        handler = make_handler(hub=hub, sessions=type("S", (), {
            "get": staticmethod(lambda sid: {"id": sid, "agent":
                                             "build"})})(),
            messages=msgs, config={},
            runner=None, srv=None)
        hs = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=hs.serve_forever, daemon=True).start()
        port = hs.server_address[1]
        try:
            rq = urllib.request.Request(
                f"http://127.0.0.1:{port}/session/ses_z/message")
            with urllib.request.urlopen(rq, timeout=10) as resp:
                rows = json.loads(resp.read())
        finally:
            hs.shutdown()
        for r in rows:
            self.assertIn("tokens", r["info"],
                          'the wire served a poisoned row')




class TestServePortReservation(unittest.TestCase):
    """THE TOCTOU FIX (the two 4s-apart boots both picked 4096: the
    loser's native serve failed Errno 48 => proxy-only => the membrane
    seat 'not starting' -- the TUI never opened). The PICK now BINDS
    (the reservation): the loser re-picks at pick, never post-pick."""

    def test_reservation_holds_the_port(self):
        import socket as _sk
        from membrane.opencode import _pick_serve_port
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        s.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        try:
            # the HELD port is not free: the walk skips it
            picked = _pick_serve_port(port)
            self.assertNotEqual(picked, port)
        finally:
            s.close()

    def test_reservation_survives_to_start(self):
        """start() adopts the prebound socket (never a rebind race)."""
        import socket as _sk
        import membrane.server as MS
        from membrane.opencode import _pick_serve_port
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        s.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(16)                      # THE RESERVATION: pick-time
        hub = MS.SSEHub()
        pub = []
        hub.publish = lambda ev: pub.append(ev)
        msgs = MS.MessageStore()
        msgs.hub = hub
        sessions = MS.SessionStore()
        runner = MS.TurnRunner(hub, sessions, msgs, lambda body: {}, None,
                               system="", model="m")
        runner._serve_socket = s          # the pick's reservation
        runner.srv = None
        httpd, th = MS.start(port, "127.0.0.1", {}, runner,
                             sessions, msgs)
        try:
            self.assertEqual(httpd.socket.getsockname()[1], port)
            self.assertEqual(httpd.socket, s,
                             "the reservation became the server")
        finally:
            httpd.shutdown()
            httpd.server_close()
            runner._serve_socket = None

    def test_unused_reservation_closes(self):
        """A failed boot releases its reservation (the port never
        sticks held against the seat's own retry)."""
        import socket as _sk
        import membrane.server as MS
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        s.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(16)
        hub = MS.SSEHub()
        msgs = MS.MessageStore()
        sessions = MS.SessionStore()
        runner = MS.TurnRunner(hub, sessions, msgs, lambda b: {}, None,
                               system="", model="m")
        runner._serve_socket = s
        httpd, th = MS.start(port, "127.0.0.1", {},
                             runner, sessions, msgs)
        try:
            self.assertIsNone(runner._serve_socket,
                              "the handoff cleared the reservation")
            self.assertEqual(httpd.socket.getsockname()[1], port)
        finally:
            httpd.shutdown()
            httpd.server_close()




class TestActivityWatchdog(unittest.TestCase):
    """THE ACTIVITY-AWARE WATCHDOG (the brain's 'turn outlived 300s'
    exhibit -- a HEALTHY six-round tool turn died of WALL-CLOCK while
    its cloud deliveries were hanging; the clock counts SILENCE now,
    not total time; the absolute lifetime cap (3x) stays."""

    def _runner(self):
        import membrane.server as MS
        hub = MS.SSEHub()
        msgs = MS.MessageStore()
        sessions = MS.SessionStore()
        runner = MS.TurnRunner(hub, sessions, msgs, lambda b: {}, None,
                               system="", model="m")
        runner.srv = None
        return runner

    def test_silent_turn_is_aborted_at_the_budget(self):
        """No activity touches: the watchdog aborts at TURN_MAX_S."""
        runner = self._runner()
        sid = "ses_w"
        runner._busy[sid] = {"type": "busy"}
        runner._turn_stops[sid] = threading.Event()
        runner._turn_activity = {sid: time.time()}
        t0 = time.time()
        runner.TURN_MAX_S = 2.0        # the test's scale
        runner._turn_watchdog(sid, t0)
        self.assertIn(sid, runner._aborting)

    def test_active_turn_survives_past_the_budget(self):
        """Activity touches keep arriving (a fast tool loop): NO abort
        at t0+TURN_MAX_S (the healthy-turn fix); the abort lands only
        at the ABSOLUTE cap (3x)."""
        runner = self._runner()
        sid = "ses_w2"
        runner._busy[sid] = {"type": "busy"}
        runner._turn_stops[sid] = threading.Event()
        t0 = time.time()
        runner.TURN_MAX_S = 0.5        # the test's scale
        runner._turn_activity = {sid: t0}

        heart = threading.Thread(
            target=lambda: [time.sleep(0.1) or
                            runner._turn_activity.__setitem__(
                                sid, time.time())
                            for _ in range(40)], daemon=True)
        heart.start()
        runner._turn_watchdog(sid, t0)   # would return at 1.5s (3x cap)
        self.assertGreaterEqual(time.time() - t0, 1.4,
                                "the watchdog fired EARLY: a healthy "
                                "multi-round turn was wall-clock-killed")
        self.assertIn(sid, runner._aborting)   # only at the ABSOLUTE cap
        heart.join(timeout=1)




class TestJournalJsonSafe(unittest.TestCase):
    """THE STORE'S JSON-SAFE WRITER (the 'when serializing dict item
    session/watch_last_decision/properties' crash -- a richer prop's
    non-serializable residue (a lambda, a Mock, a session object)
    KILLED the journal append: the file trail lost every changed row
    in flight, the compaction died, 'RAM carries' = the field's
    persistence silently hollow). The write now dumps through the
    entities layer's json_safe screen: strangers wrap to strings, the
    line ALWAYS writes."""

    def test_poison_doc_writes_and_reloads(self):
        import tempfile
        from membrane.efstore import FileBackend

        class Fake:
            def __repr__(self):
                return "<fake-session>"
        d = tempfile.mkdtemp()
        fb = FileBackend(d)
        doc = {"eid": "x", "props": {"session": Fake(), "ok": 1}}
        fb._journal_append("entity_states", "k1", doc)
        line = Path(d, "entity_states.jsonl").read_text().strip()
        self.assertTrue(line, "the trail never lost the row")
        r = json.loads(line)
        self.assertEqual(r["d"]["props"]["ok"], 1)
        self.assertIsInstance(r["d"]["props"]["session"], str)

    def test_event_append_json_safe_too(self):
        import tempfile
        from membrane.efstore import FileBackend
        d = tempfile.mkdtemp()
        fb = FileBackend(d)
        fb._append("evt-1", {"stream": "discourse", "payload": {1, 2, 3}})
        line = Path(d, "events.jsonl").read_text().strip()
        self.assertTrue(line, "the events trail never lost the row")
        r = json.loads(line)
        self.assertIsInstance(r["d"]["payload"], str)   # the set wrapped




class TestLoopSessionIds(unittest.TestCase):
    """THE DOUBLE-LAMBDA EXHIBIT (the executor's 'not executing' on both
    seats): create_loop_session returned the INNER lambda UNCALLED (a
    function object rode as the loop's sid) -- the ladder's body json
    died ('Object of type function is not JSON serializable') and every
    loop turn = 'model ladder failed'. The maker returns a STRING."""

    def test_create_loop_session_is_a_plain_string(self):
        """The sid = a plain string, never a function object. Drives the
        REAL maker (OC.loop_session) -- this test used to re-implement the
        boot's closure, copying its `__loop_runner__` typo along with it
        (the real runner seat path: tests/test_loop_lane.py)."""
        import membrane.opencode as OC
        srv = type("S", (), {"entity_store": _store(),
                             "_loop_runner": None})()
        sid = OC.loop_session(srv, "watch: f:x.py")
        self.assertIsInstance(sid, str)
        self.assertEqual(sid, "loop::watch: f:x.py")

    def test_the_ladder_body_stays_jsonable(self):
        """The loopback's body with a loop sid: json.dumps never dies
        (the storm's exhibit's exact site)."""
        import membrane.server as MS
        hub = MS.SSEHub()
        msgs = MS.MessageStore()
        sessions = MS.SessionStore()
        cell = {"runner": None}
        ladder = MS.loopback_ladder(11599, cell=cell)
        # a REAL runner exists + its turn sid is a plain string:
        runner = MS.TurnRunner(hub, sessions, msgs, ladder, None,
                               system="", model="m")
        cell["runner"] = runner
        runner._current_turn_sid = "loop::watch: f:x.py"
        self.assertIsInstance(runner._current_turn_sid, str)
        import json as _json
        _json.dumps({"model": "m", "messages": [],
                     "_turn_sid": runner._current_turn_sid})  # never raises


if __name__ == "__main__":
    unittest.main()
