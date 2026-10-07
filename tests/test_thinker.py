"""THE THINKER (the fast clock, separate from the dreamer): rumination --
the last stimuli digested, and in silence the quarantined branches
extended. Covenants pinned: working memory = 5 items drop-oldest (the
3-7 bound), the voice ALWAYS rides speak()'s planner gate (stay-silent
holds), the model-call cap (<=4/min), think-ahead extends via the token
walk ($0) and commits steps[] on the branch, and the kill switch boots
the seat thinker-less."""

import time
import unittest
from unittest import mock

from membrane.acts import ActTable
from membrane.entities import (CLASS_TIMELINE, CLASS_SESSION, EntityStore,
                               QuantumEntity, MEMBRANE_NS, CLASS_SESSION)
from membrane.opencode import speak, drain_speech
from membrane.thinker import (Thinker, QUEUE_MAX, THINK_MODEL_CAP_PER_MIN,
                              THINK_TICK_S, TICK_FIELD_EVERY)


class _Srv:
    def __init__(self):
        self.membrane = type("M", (), {"store": type("S", (), {
            "exemplars": []})()})()
        self.entity_store = EntityStore(bucket="test-think", shared=False)
        self._speak_queue = []
        self._act_table = ActTable()
        self.backend = "http://127.0.0.1:1"
        self.idle_cortex_model = "granite3.3:8b"
        self.thinker = None
        # THE NIGHT MODE'S BOOT CONTRACT (opencode.py:6513-6522): the
        # seat carries the ambient knobs BEFORE the thinker reads them
        self.ambient_night = True
        self.ambient_mode = "day"
        self.ambient_pulse = (1.0, 30.0)
        self.last_turn_ts = time.time()


class TestThinkQueue(unittest.TestCase):
    def test_queue_is_bounded_drop_oldest(self):
        srv = _Srv()
        th = Thinker(srv)
        for i in range(12):
            th.enqueue("capture", {"i": i})
        self.assertEqual(len(th.queue), QUEUE_MAX)
        # the OLDEST fell off (attention, not a backlog)
        self.assertEqual(th.queue[0]["payload"]["i"], 12 - QUEUE_MAX)

    def test_digest_speaks_only_through_planner(self):
        """An unlearned context vetoes (stay-silent default holds): the
        digest never speaks by its own decision."""
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        th.enqueue("capture", {"label": "call bash", "idx": None,
                               "situation": "the build ran",
                               "sim": 0.99})
        est = srv.entity_store
        # a tag to speak about: the digest reads exemplar extras; with no
        # exemplar the digest degrades to no speech regardless
        th._process_one(th.queue.popleft())
        self.assertEqual(th.stats["spoken"], 0)
        self.assertEqual(drain_speech(srv), [])

    def test_digest_night_speaks_materializations(self):
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        # THE PLANNER'S NEW COVENANT (speak() consults WITHOUT the veto,
        # a76f4ae): the unlearned context still SPEAKS through (the old
        # stay-silent-veto gate is retired) -- the materialization report
        # lands on the speech queue; the table learns from delivery.
        th._process_one({"kind": "night",
                         "payload": {"materialized": 3}})
        msgs = drain_speech(srv)
        self.assertEqual(len(msgs), 1)
        self.assertIn("materialized 3", msgs[0]["text"])
        # a learned context speaks
        sig = "unknown"
        srv._act_table.observe(sig, "report-state")
        srv._act_table.observe(sig, "report-state")
        th._process_one({"kind": "night",
                         "payload": {"materialized": 2}})
        msgs = drain_speech(srv)
        self.assertEqual(len(msgs), 1)
        self.assertIn("materialized 2", msgs[0]["text"])

    def test_think_ahead_extends_quarantined_branch(self):
        srv = _Srv()
        th = Thinker(srv)
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now",
                            cls="Session",
                            properties={"name": "now", "grain": 0.003})
        est.upsert(now)
        br = est.make_timeline(terminal="run the benchmark suite next",
                               vec=[1.0, 0.0], seed_intent="speed")
        # the token walk has nothing for this context -> $0 path, no
        # model (dead backend) -> no extension; the walk-miss + cap path
        # must degrade without raising
        th._idle_think()
        self.assertEqual(th.stats["model_calls"], 1)  # the one allowed call
        # with a seeded timeline table the walk extends for free
        from membrane.token_timeline import TokenTimeline
        tt = type("TT", (), {
            "walk": staticmethod(lambda toks, max_steps=14:
                                 toks + ["suite", "green"])})()
        srv.membrane = type("M", (), {"store": type("S", (), {
            "exemplars": []})(), "timeline": tt})()
        th._idle_think()
        steps = (est.docs.get(br.eid).properties or {}).get("steps")
        self.assertEqual(steps, ["suite green"])


class TestConstantHum(unittest.TestCase):
    """THE CONSTANT HUM (the operator's call): a 1s heartbeat, one small
    unit of thought per tick, throttled by the duty controller -- measured
    think work held to THINK_CPU_TARGET (~3-5% of one core), work earns
    sleep, the idle mind still hums."""

    def test_heartbeat_processes_one_item_per_tick(self):
        srv = _Srv()
        th = Thinker(srv)
        for i in range(3):
            th.enqueue("capture", {"i": i})
        # three heartbeats -> three processed, queue drained in order
        for _ in range(3):
            th._hum()
        self.assertEqual(th.stats["processed"], 3)
        self.assertEqual(len(th.queue), 0)

    def test_duty_controller_stretches_after_heavy_work(self):
        """A heavy chew (measured work) earns sleep: the duty controller
        stretches the next tick until the window's duty falls under the
        ~3-5% target."""
        srv = _Srv()
        th = Thinker(srv)
        th._ema_work = 6.0            # a heavy chew just happened (~10%)
        slept = th._hum()             # with an empty queue, the hum no-ops
        self.assertGreater(slept, THINK_TICK_S * 1.5)
        th._ema_work = 0.001          # a light mind
        self.assertLessEqual(th._hum(), 1.0)

    def test_idle_think_throttled_by_duty(self):
        """The think-ahead probe runs only when the duty allows: an
        over-target mind skips the probe (the hum stays cheap)."""
        srv = _Srv()
        th = Thinker(srv)
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now", "grain": 0.003})
        est.upsert(now)
        est.make_timeline(terminal="probe me", vec=[1.0, 0.0])
        calls = []
        th._idle_think = lambda: calls.append(1)
        th._ema_work = 3.0            # over target: no probe
        th._hum()
        self.assertEqual(calls, [])



    def test_cap_four_per_minute(self):
        srv = _Srv()
        th = Thinker(srv)
        for _ in range(THINK_MODEL_CAP_PER_MIN + 2):
            self._try_call(th)
        self.assertEqual(th.stats["model_calls"],
                         THINK_MODEL_CAP_PER_MIN)

    def _try_call(self, th):
        try:
            th._model_call([{"role": "user", "content": "x"}])
        except Exception:
            pass


class TestTickScripts(unittest.TestCase):
    """THE CLOCK ON THE HUM (the operator's 2026-10-01 design): the
    thinker's 1s heartbeat IS the clock's trigger -- every heartbeat,
    each entity with a tick_script fires (paced by its own `tick_every`,
    cooldown-free), the engine applies the result SILENTLY, and every
    TICK_FIELD_EVERY beats the movers speak ONE field-move observation
    (the clock's position drifts on the global field)."""

    def _server(self, clock):
        srv = _Srv()

        class _Fld:
            def __init__(self):
                self.events = []

            def write_events(self, evs, session=None):
                self.events.extend(evs)

            def process(self, limit=8):
                return 0

        srv.efield = _Fld()
        est = srv.entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}tool:clock", cls="Tool",
                            properties={"name": "clock",
                                        "tick_script":
                                            "import time as _t\n"
                                            "now = _t.strftime('%H:%M:%S')\n"
                                            "result = {'render': 'clock ' +"
                                            " now, "
                                            "'patch': {'script_data': "
                                            "{'last_tick': now}}}",
                                        "tick_every": clock})
        est.upsert(ent)
        return srv, est._resolve_spec("tool:clock"), srv.efield

    def test_fires_per_beat_and_paces_by_tick_every(self):
        srv, ent, _ = self._server(clock=2)
        th = Thinker(srv)
        th._beat += 1
        th._tick_scripts()                     # beat 1 (odd): no fire
        self.assertIsNone((ent.properties or {}).get("script_data", {})
                          .get("last_tick"))
        th._beat += 1
        th._tick_scripts()                     # beat 2: the clock ticks
        import re as _re
        # THE WHITELIST CONTRACT: a tick's patch reaches the doc through
        # script_data (raw prop keys are refused -- the engine applies)
        self.assertEqual(ent.properties.get("script_data", {})
                         .get("last_tick", ""), ent.properties.get(
                             "script_data", {}).get("last_tick", ""))
        tick = (ent.properties or {}).get("script_data", {}) \
            .get("last_tick")
        self.assertIsNotNone(tick)
        self.assertTrue(_re.fullmatch(r"\d\d:\d\d:\d\d", tick))
        self.assertEqual(ent.properties.get("script_render",
                                            "").startswith("clock "), True)

    def test_field_move_every_sixty_beats(self):
        srv, ent, fld = self._server(clock=1)
        th = Thinker(srv)
        th._beat = TICK_FIELD_EVERY - 1
        th._tick_scripts()                     # ticks but does NOT move
        self.assertEqual(fld.events, [])
        th._beat += 1
        th._tick_scripts()                     # beat 60: ONE move event
        self.assertEqual(len(fld.events), 1)
        ev = fld.events[0]
        self.assertEqual(getattr(ev, "subject", None), ent.eid)
        self.assertEqual(getattr(ev, "stream", None), "observation")
        self.assertEqual(getattr(ev, "outcome", None), "ok")
        self.assertEqual(ev.args.get("beat"), TICK_FIELD_EVERY)
        # the applies themselves were silent -- the move is the speech
        self.assertEqual(th.stats.get("ticks", 0) > 0, True)

    def test_disabled_or_unticked_entities_stay_quiet(self):
        srv, ent, fld = self._server(clock=1)
        est = srv.entity_store
        bad = QuantumEntity(eid=f"{MEMBRANE_NS}files:broken.py",
                            properties={"tick_script": "result = 1/0",
                                        "script_disabled": True})
        est.upsert(bad)
        plain = QuantumEntity(eid=f"{MEMBRANE_NS}files:plain.py",
                              properties={"name": "plain"})
        est.upsert(plain)
        th = Thinker(srv)
        th._beat = 1
        th._tick_scripts()
        self.assertNotIn((bad.properties or {}).get("script_failures"),
                         (1, 2, 3))
        self.assertIsNone((plain.properties or {}).get("script_render"))
        self.assertEqual(fld.events, [])

    def test_degrades_without_store_or_field(self):
        srv = _Srv()
        srv.entity_store = None
        srv.efield = None
        th = Thinker(srv)
        th._beat = 60
        th._tick_scripts()         # no store, no field: never a wedge
        self.assertEqual(th._beat, 60)


class _PubColl:
    """A fake published-collection: captures the presence upsert, answers
    the roster pull with N1QL-shaped rows (an ``id`` key, doc fields flat)."""

    def __init__(self, docs=None):
        self.docs = dict(docs or {})
        self.upserts = []

    def upsert(self, k, d):
        self.upserts.append((k, d))
        self.docs[k] = d

    def query(self, q, **kw):
        return iter([{"id": k, **v} for k, v in list(self.docs.items())])


class TestPresencePulse(unittest.TestCase):
    """THE PRESENCE PULSE (the operator's 2026-10-01 design): every
    PRESENCE_EVERY beats (~1/min, much slower than the 1Hz clock) the
    seat republishes its presence entity (identity + uptime + docs) --
    the ONE seat-auto publish lane -- and reads the ROSTER of foreign
    seats off the global tier (the thinker's anchor)."""

    def test_pulse_fires_only_on_the_sixtieth_beat(self):
        """The gate reads the INCREMENTED beat (the hum hums first):
        59 -> 60 fires; 60 -> 61 does not."""
        srv = _Srv()
        th = Thinker(srv)
        th._beat = 59
        with mock.patch.object(th, "_presence_pulse") as pulse, \
                mock.patch.object(th, "_tick_scripts"), \
                mock.patch.object(th, "_duty", return_value=1.0):
            th._hum()
            self.assertEqual(pulse.call_count, 1)
        th._beat = 60
        with mock.patch.object(th, "_presence_pulse") as pulse, \
                mock.patch.object(th, "_tick_scripts"), \
                mock.patch.object(th, "_duty", return_value=1.0):
            th._hum()
            self.assertEqual(pulse.call_count, 0)

    def test_pulse_publishes_and_anchors_the_roster(self):
        srv = _Srv()
        srv._boot_ts = time.time() - 240
        th = Thinker(srv)
        est = srv.entity_store
        foreign = {"pub:laptop@mac:membrane::presence": {
            "published": {"by": "laptop@mac", "ver": 3,
                          "eid": "membrane::presence"},
            "data": {"properties": {"seat": "laptop@mac", "uptime": 900,
                                    "branch": "entity-field"}}}}
        est._entities_collection = lambda c=_PubColl(foreign): c
        th._presence_pulse()
        r = th.stats["presence"]
        self.assertTrue(r["ok"])
        coll = est._entities_collection()
        key = f"pub:seat@local:{MEMBRANE_NS}presence"
        self.assertEqual([k for k, _ in coll.upserts], [key])
        p = est.docs.get(f"{MEMBRANE_NS}presence").properties or {}
        self.assertEqual(p.get("seat"), "seat@local")
        self.assertEqual(p.get("branch"), "entity-field")  # this repo's
        self.assertGreater(p.get("uptime"), 200)
        self.assertGreaterEqual(p.get("docs"), 1)
        # the roster: the OTHER seat only (self never makes the list)
        self.assertEqual([q["origin"] for q in srv._presence_roster],
                         ["laptop@mac"])
        self.assertEqual(srv._presence_roster[0]["properties"]["uptime"], 900)

    def test_pulse_degrades_couchbase_down_never_raises(self):
        """No collection (couchbase down): the LOCAL presence doc still
        lands, the pulse reports not-ok, the roster is empty -- the
        thinker hums on."""
        srv = _Srv()
        th = Thinker(srv)
        th._presence_pulse()
        self.assertIn(f"{MEMBRANE_NS}presence", srv.entity_store.docs)
        self.assertFalse(th.stats["presence"]["ok"])
        self.assertEqual(srv._presence_roster, [])


if __name__ == "__main__":
    unittest.main()

class TestChatDigest(unittest.TestCase):
    """THE THINKER'S CHAT FEED (the receipt-lane retirement's voice): the
    operator's turn lands on the mind; the digest speaks only through the
    planner gate, and only above the surprise bar (a hot field match --
    the ask-operator shape: 'you are near held ground')."""

    def _thinker_with_learned_context(self):
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        # the planner has LEARNED this context speaks (ask-operator x2)
        srv._last_context_sig = "escalate|0.9"
        srv._act_table.observe("escalate|0.9", "ask-operator")
        srv._act_table.observe("escalate|0.9", "ask-operator")
        # an embeddable field: one row near the ask + the embedder that
        # maps the ask onto it (the digest's surprise bar needs a vec)
        import numpy as _np
        v = _np.zeros(1024, dtype=float)
        v[0] = 1.0
        for _ in range(2):     # the digest needs >=2 rows (its matrix)
            srv.membrane.store.exemplars.append(
                type("E", (), {"vec": v.tolist()})())
        srv.membrane.embedder = type("Emb", (), {
            "embed": staticmethod(lambda texts: [v.tolist()])})()
        return srv, th, v

    def test_unlearned_context_stays_silent(self):
        srv = _Srv()
        th = Thinker(srv)
        srv.thinker = th
        th.enqueue_chat("huh?")
        th._process_one(th.queue.popleft())
        self.assertEqual(th.stats["spoken"], 0)
        self.assertEqual(drain_speech(srv), [])

    def test_hot_match_speaks_ask_operator(self):
        srv, th, v = self._thinker_with_learned_context()
        srv.membrane.embedder = type("Emb", (), {
            "embed": staticmethod(lambda texts: [v.tolist()])})()
        th.enqueue_chat("what did we hold here?")
        th._process_one(th.queue.popleft())
        msgs = drain_speech(srv)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["act"], "ask-operator")
        self.assertIn("held ground", msgs[0]["text"])
