"""THE ACTIVE MODELS (2026-10-10): one entity per model role; linking it to a
model switches the role -- by membrane's own hands or the operator's controls."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import active_models as AM  # noqa: E402
from membrane import minds_models as MM  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS  # noqa: E402

KNOWN = ["glm-5.3-flash:cloud", "qwen3:8b", "deepseek-v4-pro:cloud"]


def _srv():
    srv = type("S", (), {})()
    srv.entity_store = EntityStore(path=None, shared=False)
    srv._loop_runner = type("R", (), {"model": "glm-5.3-flash:cloud"})()
    srv.idle_cortex_model = "qwen3:8b"
    srv._models_path = None
    for name in KNOWN:
        e = QuantumEntity(eid=MEMBRANE_NS + "models:" + name, cls="AIModel", properties={"name": name})
        srv.entity_store.docs[e.eid] = e
    return srv


class TestActiveModels(unittest.TestCase):
    def test_the_roles_are_planted_with_what_runs_now(self):
        srv = _srv()
        AM.ensure(srv)
        est = srv.entity_store
        for role in AM.ROLES:
            self.assertIsNotNone(est._resolve_spec("active-model:" + role), role)
        self.assertEqual(est._resolve_spec("active-model:loops").properties["model"], "glm-5.3-flash:cloud")
        self.assertEqual(AM.ensure(srv), [])                              # idempotent

    def test_linking_a_model_switches_the_role(self):
        srv = _srv(); AM.ensure(srv); est = srv.entity_store
        loops = est._resolve_spec("active-model:loops")
        loops.properties.pop("model", None)
        loops.properties["parents"] = [MEMBRANE_NS + "models:deepseek-v4-pro:cloud"]   # linked like membrane would
        out = AM.on_change(srv, loops, known=KNOWN)
        self.assertTrue(out["applied"], out)
        self.assertEqual(srv._loop_runner.model, "deepseek-v4-pro:cloud")
        cortex = est._resolve_spec("active-model:cortex")
        cortex.properties["model"] = "glm-5.3-flash:cloud"
        AM.on_change(srv, cortex, known=KNOWN)
        self.assertEqual(srv.idle_cortex_model, "glm-5.3-flash:cloud")

    def test_a_model_the_backends_dont_serve_is_refused(self):
        srv = _srv(); AM.ensure(srv)
        chat = srv.entity_store._resolve_spec("active-model:chat")
        chat.properties["model"] = "gpt-9-imaginary"
        out = AM.on_change(srv, chat, known=KNOWN)
        self.assertFalse(out["applied"])
        self.assertIn("not served here", chat.properties["model_refused"])
        self.assertIsNone(AM.for_turn(srv, "[HA user: D]\nhi", "operator"))

    def test_a_person_has_their_own_and_the_seat_has_one_for_everyone(self):
        srv = _srv(); AM.ensure(srv)
        AM.mirror(srv, "chat", "qwen3:8b")                                   # the seat's chat
        AM.mirror(srv, "chat", "deepseek-v4-pro:cloud", person="Dmitrii Chechetkin")   # his picker
        self.assertEqual(AM.for_turn(srv, "[HA user: Dmitrii Chechetkin]\nhey", "operator"), "deepseek-v4-pro:cloud")
        self.assertEqual(AM.for_turn(srv, "[HA user: Rudy]\nhey", "operator"), "qwen3:8b")
        self.assertEqual(AM.for_turn(srv, "goal pulse", "listener"), "glm-5.3-flash:cloud")   # a loop: the loops' model
        self.assertIsNotNone(srv.entity_store._resolve_spec("active-model:chat:dmitrii-chechetkin"))

    def test_the_operators_minds_switch_writes_the_entity(self):
        srv = _srv(); AM.ensure(srv)
        MM.apply(srv, cortex="deepseek-v4-pro:cloud", persist=False)
        self.assertEqual(srv.entity_store._resolve_spec("active-model:cortex").properties["model"], "deepseek-v4-pro:cloud")

    def test_boot_reapplies_what_the_entities_say(self):
        srv = _srv(); AM.ensure(srv)
        srv.entity_store._resolve_spec("active-model:loops").properties["model"] = "qwen3:8b"
        srv._loop_runner.model = "something-else"
        AM.known_models = lambda s: KNOWN
        AM.load(srv)
        self.assertEqual(srv._loop_runner.model, "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
