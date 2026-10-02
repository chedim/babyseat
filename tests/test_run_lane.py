"""THE RUN LANE (stage B, TDD): commands execute THROUGH entities. The
owner's two calls: entity scripts go UNsandboxed (os/subprocess allowed,
engine-level budgets stay); and the model runs shell via run --
one command -> one command entity (`c:<head>` -- the field's own canon
id, so every prose mention binds it) created or REUSED for throwaway
repeats, its result on the doc, the run spoken into the field, errors in
the script-error lanes. Plus the cfg: opencode's built-in toolbelt turns
OFF in the generated config (zero remaining built-ins -- the owner's
call), MEMBRANE_BUILTIN_TOOLS=1 as the ablation. Deterministic fakes only.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder
from membrane.efield import EntityField
from membrane.efstore import MemBackend
from membrane.entities import EntityStore, CLASS_TOOL
from membrane.night import run_command_entity, run_entity_script
from membrane import streams as ST

NS = "membrane::"


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    est = EntityStore(path=None, shared=False)
    return f, est


class TestSandboxLift(unittest.TestCase):
    def test_scripts_may_shell(self):
        """The owner lifted the ban: os/subprocess imports work; the
        engine's timeout budget is what stays."""
        out = run_entity_script(
            "import subprocess\n"
            "rc = subprocess.run(['echo', 'hi'], capture_output=True,"
            " text=True).returncode\n"
            "result = {'data': {'rc': rc}}", {})
        self.assertEqual(out["data"]["rc"], 0)


class TestRunLane(unittest.TestCase):
    def test_run_creates_a_command_entity_and_moves_it(self):
        """One command -> one `c:<head>` entity (cls Tool, command on the
        doc), the run spoken into the field, the entity's state moved."""
        f, est = _world()
        out = run_command_entity(f, est, None, "echo membrane-run-ok")
        self.assertTrue(out["ok"])
        self.assertEqual(out["eid"], "c:echo")
        doc = est.docs.get(NS + "shell:echo")
        self.assertIsNotNone(doc)
        self.assertEqual(doc.cls, CLASS_TOOL)
        self.assertEqual((doc.properties or {}).get("command"),
                         "echo membrane-run-ok")
        self.assertEqual((doc.properties or {}).get("run_outcome"), "ok")
        f.process()
        self.assertIsNotNone(f.get("c:echo"))

    def test_event_is_a_real_observation(self):
        """The run's field event is a proper observation (tool 'shell',
        its fact embeds the command heads + outcome) with the subject
        pinned."""
        f, est = _world()
        run_command_entity(f, est, None, "echo membrane-run-ok")
        ev = next(d for _, d in f.b.scan("events")
                  if d.get("stream") == "observation"
                  and d.get("tool") == "shell")
        self.assertEqual(ev["subject"], "c:echo")
        fact = ST.observation_fact(ST.Event(stream="observation",
                                            tool="shell",
                                            args=ev["args"],
                                            result_head=ev["result_head"],
                                            outcome=ev["outcome"]))
        # 'echo' is a _NOT_HEADS verb (prose guard) so it never lands in
        # the fact -- the fact keeps the tool+outcome shape; the SUBJECT
        # lane (ev.subject == 'c:echo') is what binds c:echo
        self.assertTrue(fact.startswith("shell"))
        self.assertIn("ok", fact)
        self.assertIn("membrane-run-ok", fact)

    def test_failure_rides_the_error_lanes(self):
        """rc != 0: the script-error lanes (announce + persisted
        script_last_error) plus an 'error-outcome' observation."""
        f, est = _world()
        out = run_command_entity(f, est, None,
                                 'python3 -c "raise SystemExit(3)"')
        self.assertFalse(out["ok"])
        doc = est.docs[NS + "shell:python3"]
        self.assertTrue((doc.properties or {}).get("script_last_error"))
        kinds = [a["kind"] for a in est.take_announcements()]
        self.assertIn("script-error", kinds)
        evs = [d for _, d in f.b.scan("events")
               if d.get("stream") == "observation"
               and d.get("tool") == "shell"]
        self.assertTrue(
            any(d["outcome"] in ("error", "empty", "failed") for d in evs))

    def test_throwaway_reuses_the_entity(self):
        """Two runs of the same head -> ONE doc (the owner: 'reuse the
        same entity for throwaway commands'), props refreshed."""
        f, est = _world()
        a = run_command_entity(f, est, None, "echo one")
        b = run_command_entity(f, est, None, "echo two")
        self.assertEqual(a["eid"], b["eid"])
        self.assertEqual(len([k for k in est.docs if k.startswith(NS + "shell:")]), 1)
        self.assertEqual((b["doc"].properties or {}).get("command"), "echo two")

    def test_timeout_is_a_failure(self):
        """A hanging command returns with the error lanes fed (the budget
        is real subprocess.TimeoutExpired handling, never a wedge)."""
        f, est = _world()
        out = run_command_entity(f, est, None, "sleep 5", timeout_s=1.0)
        self.assertFalse(out["ok"])
        evs = [d for _, d in f.b.scan("events")
               if d.get("stream") == "observation"
               and d.get("tool") == "shell"]
        self.assertTrue(any("timeout" in (d["outcome"] or "") for d in evs))


class TestToolsOff(unittest.TestCase):
    def test_all_builtins_disabled_in_cfg_block(self):
        from membrane.opencode import _tools_block, _OC_BUILTIN_TOOLS
        blk = _tools_block()
        for t in _OC_BUILTIN_TOOLS:
            self.assertIs(blk[t], False)
        self.assertNotIn("query", blk)   # MCP tools are unaffected

    def test_env_keep_builtins(self):
        from membrane.opencode import _tools_block
        os.environ["MEMBRANE_BUILTIN_TOOLS"] = "1"
        try:
            self.assertIsNone(_tools_block())
        finally:
            os.environ.pop("MEMBRANE_BUILTIN_TOOLS", None)


class TestMcpRunTool(unittest.TestCase):
    def test_membrane_run_listed_and_relayed(self):
        """run in the MCP surface, its schema names the
        arguments, its relay is wired (the full request loop against a
        stub proxy lives in test_mcp.py's suite)."""
        import json as _json
        from membrane import mcp_server as M
        self.assertIn("run", [t["name"] for t in M.TOOLS])
        self.assertIn("run", M.TOOL_IMPLS)
        tool = next(t for t in M.TOOLS if t["name"] == "run")
        schema = _json.dumps(tool["inputSchema"]).lower()
        self.assertIn("command", schema)
        self.assertIn("timeout_s", schema)
        self.assertTrue(callable(M.TOOL_IMPLS["run"]))


class TestRunToolSelfEntity(unittest.TestCase):
    def test_run_auto_updates_the_tool_s_own_entity(self):
        """run is itself ONE entity: every executed command
        auto-updates the `tool:run` singleton (its rolling
        record: counts, rc/outcome, cumulative ms, last command) while
        each command keeps its own `c:<head>` doc."""
        f, est = _world()
        run_command_entity(f, est, None, "echo one")
        run_command_entity(f, est, None, "false")
        run_command_entity(f, est, None, "echo two")
        tk = est.docs.get(NS + "tool:run")
        self.assertIsNotNone(tk)
        p = tk.properties
        self.assertEqual(p.get("run_count"), 3)
        self.assertEqual(p.get("fail_count"), 1)          # `false` rc=1
        self.assertEqual(p.get("last_command"), "echo two")
        self.assertEqual(p.get("last_rc"), 0)
        self.assertEqual(p.get("last_outcome"), "ok")
        self.assertGreater(p.get("total_ms"), 0)
        self.assertTrue(p.get("last_ts"))
        # the singleton never replaces the per-command docs (both `echo`
        # commands share their head entity, `false` has its own)
        self.assertEqual(
            len([k for k in est.docs if k.startswith(NS + "shell:")]), 2)
        self.assertEqual(tk.cls, CLASS_TOOL)

    def test_run_tool_entity_degrades_without_store(self):
        """est=None (no entity store mounted): the run still execs and
        returns (the lane never wedges on a missing store)."""
        out = run_command_entity(None, None, None, "echo bare")
        self.assertTrue(out["ok"])
        self.assertEqual(out["rc"], 0)


if __name__ == "__main__":
    unittest.main()