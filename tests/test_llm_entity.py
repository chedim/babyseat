"""THE MODEL AS AN ENTITY (2026-10-11, the operator: "replace our enforced
synthetic llm calls with a more natural call-llm entity"): llm.call(about,
prompt) queues a loop turn on `about`; capped per caller and per hour;
the pause holds it; scripts call it once per fire; never a shell."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import llm as L  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)


class _Srv:
    def __init__(self):
        self.entity_store = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        self.entity_store._srv = self
        self.events_pool = _Pool()
        self.paused = False
        bat = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component", properties={"name": "bat"})
        self.entity_store.docs[bat.eid] = bat


class TestTheCall(unittest.TestCase):
    def setUp(self):
        L._calls.clear()

    def test_it_queues_a_loop_turn_on_the_entity(self):
        srv = _Srv()
        out = L.request(srv, "check the battery trend", about="Component:bat", caller="argus")
        self.assertEqual(out["rc"], 0)
        ev = srv.events_pool.items[0]
        self.assertEqual((ev["kind"], ev["entity"], ev["prompt"]), ("llm-call", MEMBRANE_NS + "Component:bat",
                                                                     "check the battery trend"))
        calls = srv.entity_store.docs[MEMBRANE_NS + "llm"].properties["calls"]
        self.assertEqual(calls[-1]["caller"], "argus")

    def test_capped_per_caller_and_in_all(self):
        srv = _Srv()
        for i in range(L.CALLER_PER_HOUR):
            self.assertEqual(L.request(srv, "x", caller="script:a", now=1000.0 + i)["rc"], 0)
        self.assertIn("this hour", L.request(srv, "x", caller="script:a", now=1100.0)["text"])
        self.assertEqual(L.request(srv, "x", caller="script:b", now=1100.0)["rc"], 0)     # another caller
        self.assertEqual(L.request(srv, "x", caller="script:a", now=1000.0 + 3700)["rc"], 0)  # the hour passed

    def test_the_pause_holds_it_and_a_prompt_is_needed(self):
        srv = _Srv()
        self.assertEqual(L.request(srv, "  ", caller="a")["rc"], 2)
        with mock.patch("membrane.opencode.nerve_paused", return_value=True):
            self.assertIn("paused", L.request(srv, "x", caller="a")["text"])
        self.assertEqual(srv.events_pool.items, [])

    def test_the_method_command_never_reaches_a_shell(self):
        srv = _Srv()
        L._SEAT["srv"] = srv
        with mock.patch("subprocess.run") as sh:
            N.run_command_entity(None, srv.entity_store, None, "llm::call Component:bat 'is it charging?'")
        sh.assert_not_called()
        self.assertEqual(srv.events_pool.items[-1]["prompt"], "is it charging?")
        self.assertEqual(L.from_command("only-one")["rc"], 2)

    def test_a_script_calls_once_per_fire(self):
        srv = _Srv()
        ent = srv.entity_store.docs[MEMBRANE_NS + "Component:bat"]
        api = N._script_membrane_api(ent, srv, srv.entity_store, "tick", 1.0, {})
        self.assertEqual(api.call_llm("the battery fell 5% in a minute")["rc"], 0)
        self.assertEqual(api.call_llm("again")["rc"], 1)
        ev = srv.events_pool.items[-1]
        self.assertEqual((ev["entity"], ev["caller"]), (MEMBRANE_NS + "Component:bat", "script:Component:bat"))

    def test_the_loop_reads_the_prompt(self):
        t = WA.loop_task_text({"kind": "llm-call", "entity": "membrane::Component:bat", "caller": "argus",
                               "prompt": "is it charging?"})
        self.assertIn("a model call (argus) about 'membrane::Component:bat'", t)
        self.assertIn("is it charging?", t)

    def test_the_doctrine_names_it(self):
        d = (Path(__file__).resolve().parent.parent / "doctrine.md").read_text()
        self.assertIn("THE MODEL IS AN ENTITY", d)


if __name__ == "__main__":
    unittest.main()
