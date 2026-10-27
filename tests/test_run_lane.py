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
        """rc != 0: THE COVENANT (the entity-scripts reshape) -- the
        failure BUBBLES as an error ENTITY (the entity graph's surface);
        the persisted script_last_error stays on the command's doc."""
        f, est = _world()
        out = run_command_entity(f, est, None,
                                 'python3 -c "raise SystemExit(3)"')
        self.assertFalse(out["ok"])
        doc = est.docs[NS + "shell:python3"]
        self.assertTrue((doc.properties or {}).get("script_last_error"))
        bubbles = [e for e in est.docs.values()
                   if str(e.eid).startswith("membrane::error:")]
        self.assertTrue(bubbles, "the run failure must bubble")
        self.assertEqual((bubbles[0].properties or {}).get("errored"),
                         NS + "shell:python3")
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
    def test_membrane_run_demolished(self):
        """THE DEMOLITION (the operator's ruling 2026-10-02): 'run' is
        GONE from the MCP surface; the tool-by-name routes 410 with
        the honest pointer; the ENGINE (run_command_entity) SURVIVES
        entity-side (a command entity's script returning {run: true}
        is the only exec)."""
        import json as _json
        from membrane import mcp_server as M
        self.assertNotIn("run", [t["name"] for t in M.TOOLS])
        self.assertNotIn("entity_run", M.TOOL_IMPLS)
        self.assertNotIn("run", M.TOOL_IMPLS)
        # THE ENGINE LIVES (the script verdict path's executor):
        from membrane.night import run_command_entity
        self.assertTrue(callable(run_command_entity))


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

class TestRunExecOnce(unittest.TestCase):
    """The exec-once pin: run_command_entity once shipped a DUPLICATED
    subprocess.run block in the est-branch -- every est-mounted command
    executed TWICE, the rerun silently overwriting the first's results
    (non-idempotent commands like 'echo >>' double-fired). One sentinel
    file, one line, one singleton count."""

    def test_shell_command_executes_exactly_once(self):
        import shutil
        import tempfile
        f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        est = EntityStore(path=None, shared=False)
        d = tempfile.mkdtemp(prefix="m-exec-once-")
        self.addCleanup(shutil.rmtree, d, True)
        sentinel = os.path.join(d, "count.txt")
        out = run_command_entity(f, est, None, f"echo hit >> {sentinel}")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(Path(sentinel).read_text(), "hit" + chr(10))
        doc = est.docs.get(NS + "tool:run")
        self.assertIsNotNone(doc)
        self.assertEqual((doc.properties or {}).get("run_count"), 1)


class TestNonUtf8Output(unittest.TestCase):
    """A command whose output is not UTF-8 used to kill the whole
    thinker beat: subprocess.run(text=True) decodes STRICTLY, one bad
    byte (0xae/0xb6 in the seat's own log, 2026-10-14) raised
    UnicodeDecodeError out of run_command_entity, run_pending_links
    died before landing, and the same link was retried forever. THE
    CONTRACT: the receipt is honest -- rc and the output with the bad
    bytes REPLACED (U+FFFD marks them), the run lands like every
    other."""

    CMD = "python3 -c 'import sys; sys.stdout.buffer.write(b\"pre\\xae\\xb6-post\")'"

    def test_bad_bytes_become_a_receipt(self):
        f, est = _world()
        out = run_command_entity(f, est, None, self.CMD)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["rc"], 0)
        self.assertIn("pre", out["head"])
        self.assertIn("post", out["head"])
        self.assertIn("�", out["head"])

    def test_bad_bytes_on_stderr_too(self):
        f, est = _world()
        cmd = "python3 -c 'import sys; sys.stderr.buffer.write(b\"e\\xb6\")'"
        out = run_command_entity(f, est, None, cmd)
        self.assertTrue(out["ok"], out)
    # rc is 0 -- stderr content lands with the bytes replaced
        self.assertIn("e�", out["head"])

    def test_the_landing_is_an_observation(self):
        f, est = _world()
        run_command_entity(f, est, None, self.CMD)
        evs = [d for _, d in f.b.scan("events")
               if d.get("stream") == "observation" and d.get("tool") == "shell"]
        self.assertTrue(evs)
        self.assertTrue(all(d["outcome"] == "ok" for d in evs))


class TestTheKilledJobSpeaksItsWhy(unittest.TestCase):
    """THE RECEIPT'S REASON (issue #51, 2026-10-15): the executor's inline
    budget (EXEC_TIMEOUT_S = 15) killed `sleep 15` while its twin at 14
    lived, and the receipt said only FAILED rc=-1 -- the engine computed
    the why ('timeout after {t}s') into a LOCAL that never left
    (run_command_entity's return had no error key, and the executor's
    own already-wired out.get('error') read nothing). The why rides the
    return, the executor's summary and the edge's receipt carry it, and
    the inline cap meets the plain runs' (the same command over two
    doors got budgets two apart)."""

    def _srv(self, f, est):
        import types
        return types.SimpleNamespace(efield=f, entity_store=est,
                                     membrane=None, capturer=None)

    def _seedf(self):
        import membrane.night as N
        f, est = _world()
        N.seed_executor_entity(est)
        return f, est

    def test_the_return_carries_the_reason(self):
        f, est = _world()
        out = run_command_entity(f, est, None, "sleep 5", timeout_s=1.0)
        self.assertEqual(out["rc"], -1)
        self.assertEqual(out["outcome"], "timeout")
        err = out.get("error") or ""
        self.assertIn("timeout", err)
        self.assertIn("1", err)

    def test_a_method_call_carries_it(self):
        # since #576 part F a command runs as a method the model defined
        import membrane.night as N
        from membrane.entities import CLASS_COMPONENT
        f, est = self._seedf()
        srv = self._srv(f, est)
        obj = est.docs[est.add(cls=CLASS_COMPONENT, name="napper", summary="x")["eid"]]
        N.method_define(None, est, None, None, obj.eid, "nap", "sleep 5")
        saved = N.EXEC_TIMEOUT_S
        N.EXEC_TIMEOUT_S = 0.5
        try:
            r = N.method_call(srv, est, f, None, obj.eid, "nap")
            self.assertEqual(r.get("rc"), -1, r)
            self.assertFalse(r.get("ok"))
            self.assertIn("timeout", r.get("error") or "")
        finally:
            N.EXEC_TIMEOUT_S = saved

    def test_the_edge_receipt_says_why(self):
        import membrane.night as N
        f, est = self._seedf()
        srv = self._srv(f, est)
        saved = N.EXEC_TIMEOUT_S
        N.EXEC_TIMEOUT_S = 0.5
        try:
            import time
            from membrane import relations as R
            from membrane.entities import CLASS_TOOL, QuantumEntity
            from membrane import lang as LG
            import types
            srv.capturer = types.SimpleNamespace(mode="on")
            root = LG.mint(srv, "sh", "sleep 5")
            # an owed run anyone wrote (#614): the reaper lands it
            R.upsert(f.b, f.origin, root, N.EXEC_REL, NS + "tool:executor",
                     "test", ts=time.time())
            N.run_pending_links(srv, est, f, None,
                                NS + "tool:executor")
        finally:
            N.EXEC_TIMEOUT_S = saved
        rows = [d for _k, d in f.b.scan("relations")
                if d.get("rel") == "executing"]
        self.assertTrue(rows)
        rcp = (rows[-1].get("receipt") or {})
        self.assertEqual(rcp.get("op"), "done")
        self.assertIn("timeout", str(rcp.get("why") or ""))

    def test_the_cap_meets_the_plain_runs(self):
        import membrane.night as N
        self.assertGreaterEqual(N.EXEC_TIMEOUT_S,
                                N.RUN_TIMEOUT_DEFAULT_S)


if __name__ == "__main__":
    unittest.main()
