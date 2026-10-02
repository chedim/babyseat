"""THE MODEL-WRITTEN SCRIPTS (the programmable-entities organ, the
operator's example): a getter (mention) script on a tracked entity
MONITORS a process and can MOVE the entity toward 'done' coordinates.
The engine applies (the authorship covenant): the script returns
{render, patch, delete}; apply_entity_update writes inside the store
(a script may move ITS OWN doc, never another's)."""

import re
import unittest

from membrane.entities import (CLASS_SESSION, EntityStore, QuantumEntity,
                               MEMBRANE_NS)
from membrane.night import (apply_entity_update, run_entity_update_script,
                            run_entity_script)


class _Srv:
    def __init__(self):
        self.membrane = None
        self.entity_store = EntityStore(bucket="test-scripts",
                                        shared=False)


class TestModelScripts(unittest.TestCase):
    def test_edit_carries_trigger_scripts(self):
        """The model's edit path (entities.edit) accepts + stores the
        trigger-typed scripts (mention/thought/dreamed)."""
        est = _Srv().entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}files:probe.py",
                            cls="Component",
                            properties={"name": "probe.py"})
        est.upsert(ent)
        res = est.edit("files:probe.py",
                       mention_script="result = {'render': 'mentioned'}")
        self.assertTrue(res["ok"])
        got = (est.docs.get(f"{MEMBRANE_NS}files:probe.py")
               .properties or {}).get("mention_script")
        self.assertEqual(got, "result = {'render': 'mentioned'}")

    def test_mention_script_runs_and_renders(self):
        """The engine dispatches the mention trigger under the 0.25s
        budget + the cooldown; the render lands in script_render."""
        srv = _Srv()
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        est.upsert(now)
        est.make_timeline(terminal="probe branch", vec=[1.0, 0.0])
        ent = est._resolve_spec("session:now")
        ent.properties["mention_script"] = (
            "result = {'render': 'seen: ' + state['extra'].get("
            "'operator_text', '')[:40]}")
        est.upsert(ent)
        srv._last_context_sig = "boot|0.5"
        srv._act_table = type("AT", (), {})()  # the digest path doesn't need it
        out = run_entity_update_script(
            srv, ent, "mention",
            extra_state={"operator_text": "the build is running"})
        self.assertNotIn("error", out)
        self.assertIn("seen: the build", str(out.get("render", "")))

    def test_getter_moves_entity_toward_done(self):
        """THE MONITORING ORGAN (the operator's example): a getter patch
        moves the entity's own properties toward 'done' coordinates --
        the engine applies, the script never mutates directly."""
        srv = _Srv()
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now", "progress": 0})
        est.upsert(now)
        ent = est._resolve_spec("session:now")
        out = {"render": "progress advanced",
               "patch": {"progress": 50, "status": "running"}}
        res = apply_entity_update(srv, ent, out, "mention")
        self.assertEqual(res["applied"], 3)   # render + 2 patches
        got = est.docs.get(ent.eid)
        self.assertEqual((got.properties or {}).get("progress"), 50)
        self.assertEqual((got.properties or {}).get("status"), "running")

    def test_script_cannot_forge_engine_keys(self):
        """The patch boundary: script_* keys (the engine's own) are
        untouchable from a script's patch."""
        srv = _Srv()
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        est.upsert(now)
        ent = est._resolve_spec("session:now")
        apply_entity_update(srv, ent, {"patch": {"script_disabled": True}},
                            "mention")
        got = est.docs.get(ent.eid)
        self.assertIsNone((got.properties or {}).get("script_disabled"))

    def test_cooldown_bounds_the_fire_rate(self):
        """The cooldown: the second mention inside the window is refused
        (the hot entity cannot exec every digest)."""
        srv = _Srv()
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        est.upsert(now)
        ent = est._resolve_spec("session:now")
        ent.properties["mention_script"] = "result = {'render': 'x'}"
        est.upsert(ent)
        srv._last_context_sig = "boot|0.5"
        first = run_entity_update_script(srv, ent, "mention", {})
        second = run_entity_update_script(srv, ent, "mention", {})
        self.assertNotIn("error", first)
        self.assertEqual(second.get("error"), "cooldown")


class _Fld:
    """A field fake that records the events written to it."""

    def __init__(self):
        self.events = []

    def write_events(self, evs, session=None):
        self.events.extend(evs)

    def process(self, limit=8):
        return 0


class TestTickScripts(unittest.TestCase):
    """THE TICK TRIGGER (the clock organ, the operator's 2026-10-01
    design): a tick_script fires on the THINKER'S HEARTBEAT (the seat's
    1s hum), paced by the entity's own `tick_every`, cooldown-free --
    time is a per-second fact, a 60s cooldown would make it a per-minute
    lie. The engine applies the result SILENTLY (render + props, no
    per-tick field tell, no per-tick audit -- ~86k applies/day would be
    spam); failures keep the 3-strikes lanes."""

    def _clock(self, srv, script):
        est = srv.entity_store
        ent = QuantumEntity(eid=f"{MEMBRANE_NS}tool:clock", cls="Tool",
                            properties={"name": "clock",
                                        "tick_script": script,
                                        "tick_every": 1})
        est.upsert(ent)
        return est._resolve_spec("tool:clock")

    def test_tick_dispatches_cooldown_free(self):
        srv = _Srv()
        ent = self._clock(
            srv, "result = {'render': 'clock ' + "
                 "state['extra'].get('now', '')}")
        # two fires in the SAME instant: the cooldown never gates tick
        a = run_entity_update_script(srv, ent, "tick",
                                     extra_state={"now": "12:00:01"})
        b = run_entity_update_script(srv, ent, "tick",
                                     extra_state={"now": "12:00:01"})
        self.assertNotIn("error", a)
        self.assertNotIn("error", b)
        self.assertEqual(a["render"], "clock 12:00:01")

    def test_tick_budget_is_registered(self):
        from membrane.night import _SCRIPT_TIMEOUTS
        self.assertLessEqual(_SCRIPT_TIMEOUTS["tick"], 0.5)

    def test_erroring_tick_script_strikes_out(self):
        srv = _Srv()
        ent = self._clock(srv, "result = 1/0")
        errs = [run_entity_update_script(srv, ent, "tick")
                for _ in range(3)]
        self.assertTrue(all("error" in e for e in errs))
        self.assertEqual((ent.properties or {}).get("script_failures"), 3)
        self.assertTrue((ent.properties or {}).get("script_disabled"))
        # the disabled entity's 4th fire is refused (never a wedge)
        self.assertIn("disabled",
                      run_entity_update_script(srv, ent, "tick").get(
                          "error", ""))

    def test_silent_apply_skips_the_tell_and_the_audit(self):
        """A silent (tick-pace) apply lands render + props on the doc
        but writes NO field event and NO audit row (the noisy pace)."""
        srv = _Srv()
        ent = self._clock(srv, "")
        out = {"render": "clock 13:11:05",
               "patch": {"last_tick": "13:11:05"}}
        res = apply_entity_update(srv, ent, out, "tick", silent=True)
        self.assertEqual(res["applied"], 2)
        got = srv.entity_store.docs.get(ent.eid)
        self.assertEqual((got.properties or {}).get("last_tick"),
                         "13:11:05")
        # probe both lanes with REAL recorders: none of them heard it
        fld = _Fld()
        srv.efield = fld
        from unittest import mock as _mock
        with _mock.patch(
                "membrane.opencode._write_audit") as audit_probe:
            apply_entity_update(srv, ent, out, "tick", silent=True)
            self.assertEqual(fld.events, [])
            self.assertEqual(audit_probe.call_count, 0)

    def test_seed_clock_entity(self):
        """The seat plants the clock ONCE at boot: `tool:clock`, class
        Tool, the default script + tick_every=1, silent_birth; a second
        seed does not touch it (an operator- or model-REWRITTEN clock
        survives a restart)."""
        from membrane.night import (DEFAULT_CLOCK_TICK_SCRIPT,
                                    seed_clock_entity)
        srv = _Srv()
        est = srv.entity_store
        self.assertTrue(seed_clock_entity(est))
        doc = est.docs.get(f"{MEMBRANE_NS}tool:clock")
        self.assertIsNotNone(doc)
        self.assertEqual(doc.cls, "Tool")
        p = doc.properties or {}
        self.assertEqual(p.get("tick_script"), DEFAULT_CLOCK_TICK_SCRIPT)
        self.assertEqual(p.get("tick_every"), 1)
        self.assertTrue(p.get("silent_birth"))
        est.edit("tool:clock", properties={"tick_every": 7})
        self.assertFalse(seed_clock_entity(est))
        self.assertEqual((est.docs.get(
            f"{MEMBRANE_NS}tool:clock").properties or {}).get(
            "tick_every"), 7)
        self.assertTrue(seed_clock_entity(None) is False)

    def test_default_clock_script_prints_the_time(self):
        """The default script: render 'clock <HH:MM:SS>' + the patch that
        stores the tick as last_tick (the operator's ask -- the last
        output survives on the doc)."""
        from membrane.night import DEFAULT_CLOCK_TICK_SCRIPT
        out = run_entity_script(DEFAULT_CLOCK_TICK_SCRIPT, {}, membrane={})
        m = re.fullmatch(r"clock (\d\d:\d\d:\d\d)", str(out.get("render")))
        self.assertIsNotNone(m)
        self.assertEqual(out["patch"], {"last_tick": m.group(1)})
        self.assertEqual(out["patch"]["last_tick"], out["render"][6:])


if __name__ == "__main__":
    unittest.main()


class TestPropertyPatch(unittest.TestCase):
    """THE MODEL'S WRITE PATH FOR GOAL STATE (the read-only complaint's
    fix): entities.edit accepts a bounded properties patch -- the goal's
    status IS touchable (fulfill/drop via the edit surface, audited); the
    engine's script_* keys are not."""

    def test_edit_patches_goal_status(self):
        srv = _Srv()
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        est.upsert(now)
        est.make_timeline(terminal="probe", vec=[1.0, 0.0])
        from membrane.entities import CLASS_GOAL, QuantumEntity as QE
        goal = QE(eid=f"{MEMBRANE_NS}goal:probe-goal", cls=CLASS_GOAL,
                  properties={"name": "probe goal", "status": "open"})
        est.upsert(goal)
        res = est.edit("goal:probe-goal",
                       properties={"status": "fulfilled"})
        self.assertTrue(res["ok"])
        got = (est.docs.get(goal.eid).properties or {}).get("status")
        self.assertEqual(got, "fulfilled")

    def test_patch_cannot_touch_script_keys(self):
        srv = _Srv()
        est = srv.entity_store
        now = QuantumEntity(eid=f"{MEMBRANE_NS}session:now", cls="Session",
                            properties={"name": "now"})
        est.upsert(now)
        est.edit("session:now", properties={"script_disabled": True})
        got = est.docs.get(f"{MEMBRANE_NS}session:now")
        self.assertIsNone((got.properties or {}).get("script_disabled"))
