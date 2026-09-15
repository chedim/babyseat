"""THE MODEL-WRITTEN SCRIPTS (the programmable-entities organ, the
operator's example): a getter (mention) script on a tracked entity
MONITORS a process and can MOVE the entity toward 'done' coordinates.
The engine applies (the authorship covenant): the script returns
{render, patch, delete}; apply_entity_update writes inside the store
(a script may move ITS OWN doc, never another's)."""

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
