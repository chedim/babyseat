"""THE EXECUTOR (the edge-backed exec organ, 2026-10-06): the seeded
`tool:executor`. Since #576 part F the model has no link door to it
(tests/test_program_first_576.py); what stays pinned here:

1. THE VALUE: an unknown executor is an honest error.
2. THE SYNC RULE (general): entity post-processing rides the tool
   return -- `_entity_post_process` fires the getter chain inline and
   attaches the result as 'processed' on the add/edit response.
3. THE SEED: tool:executor plants once at boot, idempotent.
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import (EntityStore, QuantumEntity, CLASS_TOOL,
                               MEMBRANE_NS, MultiParentOps)  # noqa: E402
from membrane.night import executor_value, seed_executor_entity  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    seed_executor_entity(est)
    return f, est


def _doc(est, spec: str) -> QuantumEntity:
    """The dereference anchor (pyright-clean): the test asserts the doc
    lives before any read."""
    d = est._resolve_spec(spec)
    assert d is not None, f"missing doc: {spec}"
    return d


class _Srv:
    def __init__(self, f, est, mem=None):
        self.efield = f
        self.entity_store = est
        self.membrane = mem
        self.capturer = None


class TestSeed(unittest.TestCase):
    def test_seeds_once_and_survives_rewrites(self):
        f, est = _world()
        self.assertIn(MEMBRANE_NS + "tool:executor", est.docs)
        # idempotent: a second boot plants nothing (the model's edits
        # on the doc survive)
        doc = est.docs[MEMBRANE_NS + "tool:executor"]
        doc.properties["summary"] = "rewritten by the model"
        self.assertFalse(seed_executor_entity(est))
        self.assertEqual(est.docs[MEMBRANE_NS + "tool:executor"]
                         .properties["summary"], "rewritten by the model")

    def test_no_store_is_a_clean_false(self):
        self.assertFalse(seed_executor_entity(None))


class TestExecutorValue(unittest.TestCase):
    def test_value_of_unknown_is_an_error(self):
        est = EntityStore(path=None, shared=False)
        v = executor_value(est, "tool:ghost")
        self.assertFalse(v["ok"])


class TestSyncRule(unittest.TestCase):
    def test_post_process_attaches_the_getter_result(self):
        """THE GENERAL RULE: entity_add/entity_edit's route attaches the
        entity's post-processing value ('processed') -- the getter chain
        ran inline before the tool answered."""
        f, est = _world()
        srv = _Srv(f, est)
        res = {"ok": True, "eid": MEMBRANE_NS + "tool:executor"}
        # the executor has no getter: the chain runs, reports honestly
        from membrane.opencode import _entity_post_process
        _entity_post_process(srv, res)
        p = res["processed"]
        self.assertFalse(p["getter_fired"])
        self.assertEqual(p["getter_reason"], "no getter_script")

    def test_post_process_runs_a_run_getter_inline(self):
        f, est = _world()
        doc = QuantumEntity(eid=MEMBRANE_NS + "c:getter-run",
                            cls=CLASS_TOOL,
                            properties={"command": "echo via-getter",
                                        "getter_script":
                                        "result = {'run': True}"})
        est.docs[doc.eid] = doc
        srv = _Srv(f, est)
        res = {"ok": True, "eid": doc.eid}
        from membrane.opencode import _entity_post_process
        _entity_post_process(srv, res)
        p = res["processed"]
        self.assertTrue(p["getter_fired"], p)
        self.assertEqual((p.get("run_result") or {}).get("rc"), 0)
        props = _doc(est, doc.eid).properties or {}
        self.assertEqual(props.get("run_outcome"), "ok")
        self.assertEqual(props.get("run_head"), "via-getter")


if __name__ == "__main__":
    unittest.main()

