"""THE RUN DEMOLITION (the operator's ruling, 2026-10-02, red first):
THE MODEL HAS NO EXEC TOOL AT ALL. The model's hands:

- entity_add / entity_edit -- the ONLY deploy verbs: a command entity
  (class Tool, the 'command' prop, parent + summary + ttl) with its
  SCRIPTS;
- the getter_script (the READ-BACK trigger): a READ of the entity
  fires its script (dict-in/dict-out); the return = the entity's
  current facts;
- {run: true} -- the ONLY exec lane: a getter/tick script's verdict
  makes the ENGINE exec the entity's own command (run_command_entity:
  the observation event, the result props, the error lanes, the
  audit) -- the entity decides THROUGH its script, the engine
  applies; no tool, no route, no direct exec;
- the MCP tools surface carries NO 'run'/'entity_run' (a client
  still calling them: the routes 410 with the honest pointer).

Also pinned: the read-back's LRU (5s) and the getter's pool events.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out


class _Srv:
    def __init__(self, est=None, pool=None, tmp=None):
        self.entity_store = est or EntityStore(bucket="t-run",
                                               shared=False)
        self.events_pool = pool or _Pool()
        self.efield = None
        self.membrane = None
        self.idle_cortex_model = "granite3.3:8b"
        self.cwd = tmp or os.getcwd()


class TestRunDemolished(unittest.TestCase):
    def test_no_run_nor_entity_run_on_the_surface(self):
        """THE DEMOLITION: NO exec tool on the MCP surface (run,
        entity_run -- both gone; the tools all operate on the field)."""
        import membrane.mcp_server as M
        names = {t["name"] for t in M.TOOLS}
        self.assertNotIn("run", names)
        self.assertNotIn("entity_run", names)
        self.assertNotIn("run", M.TOOL_IMPLS)
        self.assertNotIn("entity_run", M.TOOL_IMPLS)

    def test_run_routes_go_410(self):
        """The HTTP routes return 410 (gone) with the honest pointer:
        a command entity's script returning {run: true} is the only
        exec."""
        import membrane.opencode as OC
        import inspect
        src = inspect.getsource(OC)
        self.assertIn("the run route was demolished", src)


class TestEngineRunVerdict(unittest.TestCase):
    def test_getter_run_true_execs_through_engine(self):
        """THE ONLY EXEC: a getter script returning {run: true} makes
        the ENGINE run the entity's command (the result props + the
        pool's event land); the script itself never sees raw exec."""
        est = _Srv().entity_store
        srv = _Srv(est)
        ent = QuantumEntity(eid="membrane::tool:df", cls="Tool",
                            properties={"name": "df",
                                        "command": "echo engine-run-ok",
                                        "getter_script":
                                        "result = {'run': True}"})
        est.docs[ent.eid] = ent
        # the READ-BACK fires the getter; {run: true} routes to the
        # engine:
        r = WA.entity_read_back(srv, ent)
        self.assertTrue(r.get("fired"), r)
        props = est.docs.get(ent.eid).properties or {}
        self.assertEqual(props.get("run_outcome"), "ok", props)
        self.assertEqual(props.get("run_head"), "engine-run-ok", props)
        # THE COVENANT: the pool hears NOTHING (the model's watchers
        # decide what to watch; the exec's surface = the entity's props)
        evs = srv.events_pool.take()
        self.assertEqual(evs, [])

    def test_run_without_command_honest_refusal(self):
        est = _Srv().entity_store
        srv = _Srv(est)
        ent = QuantumEntity(eid="membrane::tool:nocmd", cls="Tool",
                            properties={"name": "nocmd",
                                        "getter_script":
                                        "result = {'run': True}"})
        est.docs[ent.eid] = ent
        r = WA.entity_read_back(srv, ent)
        self.assertEqual(
            ((r or {}).get("run_result") or {}).get("error"),
            "run: true but the entity carries no 'command' prop")


class TestGetterScript(unittest.TestCase):
    def test_read_back_applies_the_state_dict(self):
        est = _Srv().entity_store
        srv = _Srv(est)
        ent = QuantumEntity(eid="membrane::tool:disk", cls="Tool",
                            properties={"name": "disk",
                                        "getter_script":
                                        "result = {'state': {'free': 42}}"})
        est.docs[ent.eid] = ent
        WA.entity_read_back(srv, ent)
        self.assertEqual((est.docs[ent.eid].properties or {})
                         .get("script_data", {}).get("free"), 42)

    def test_read_back_lru_paces_hammering(self):
        est = _Srv().entity_store
        srv = _Srv(est)
        ent = QuantumEntity(eid="membrane::tool:disk", cls="Tool",
                            properties={"name": "disk",
                                        "getter_script":
                                        "result = {'state': {'n': 1}}"})
        est.docs[ent.eid] = ent
        WA.entity_read_back(srv, ent)             # runs
        calls = {"n": 0}
        orig = N.run_entity_script

        def counting(code, state, membrane=None):
            calls["n"] += 1
            return orig(code, state, membrane)
        N.run_entity_script = counting
        try:
            WA.entity_read_back(srv, ent)          # within 5s: LRU
            self.assertEqual(calls["n"], 0)
            WA._getter_last_ts.clear()
            WA.entity_read_back(srv, ent)
            self.assertEqual(calls["n"], 1)
        finally:
            N.run_entity_script = orig

    def test_getter_kind_in_the_engine(self):
        est = _Srv().entity_store
        srv = _Srv(est)
        ent = QuantumEntity(eid="membrane::tool:g", cls="Tool",
                            properties={"name": "g",
                                        "getter_script":
                                        "result = {'state': {'n': 7}}"})
        est.docs[ent.eid] = ent
        out = N.run_entity_update_script(srv, ent, "getter")
        self.assertFalse(out.get("error"), out)

    def test_entity_edit_carries_the_getter_param(self):
        """The MCP surface: entity_edit(getter_script=...) lands."""
        import membrane.mcp_server as M
        import inspect
        src = inspect.getsource(M)
        self.assertIn('"getter_script"', src)


if __name__ == "__main__":
    unittest.main()
