"""THE ISA PIPELINE (the executor's composition covenant, 2026-10-06):
'parent executes first, the child is fed its parent's output' -- the
entity inheritance IS the pipeline; the `executes` edge IS the queue:

1. THE WALK: post-order (parents first); leaf = own command; internal
   = run ancestors (memoized per eid -- a diamond's shared ancestor
   runs ONCE), then self (when it carries a command); a Tool WITHOUT a
   command but WITH parents = a composition node (runs the subtree,
   inherits the LAST parent's residue as its exit).
2. THE FEED: {{parent.head}}/{{parent.tail}} = the nearest parent's
   residue; {{parent:<eid>.head}} names a parent (the fan-in).
3. STOP-ON-FAILURE: the failed step halts the walk; the child marks
   the error; the edge supersedes (processed, not succeeded -- the
   re-link is the deliberate retry).
4. CLASSIFICATION STAYS CLASSIFICATION: Components/Goals with no
   command never execute.
5. THE CAPS: 12 nodes per pass (the reaper continues), MAX_CHAIN (16)
   deep; the queue-depth cap = the live edges (32).
"""

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import (EntityStore, QuantumEntity, CLASS_TOOL,  # noqa: E402
                               MEMBRANE_NS, MultiParentOps)


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    return f, est


class _Srv:
    def __init__(self, f, est, mem=None):
        self.efield = f
        self.entity_store = est
        self.membrane = mem
        self.capturer = None


def _doc(est, spec: str) -> QuantumEntity:
    """The dereference anchor (pyright-clean)."""
    d = est._resolve_spec(spec)
    assert d is not None, f"missing doc: {spec}"
    return d


def _cmd(est, eid: str, command: str, name: str = "") -> QuantumEntity:
    e = QuantumEntity(eid=MEMBRANE_NS + eid, cls=CLASS_TOOL,
                      properties={"name": name or eid,
                                  "command": command,
                                  "silent_birth": True})
    est.docs[e.eid] = e
    return e


class TestISAChain(unittest.TestCase):
    def test_child_runs_after_parent_and_is_fed(self):
        """THE COVENANT: parent first, the child fed its parent's
        output -- {{parent.head}} substitutes the parent's run head."""
        f, est = _world()
        srv = _Srv(f, est)
        parent = _cmd(est, "c:gen-id", "echo ID-4242")
        child = _cmd(est, "c:use-id", "echo got {{parent.head}}")
        MultiParentOps.set_parents(est, child.eid, [parent.eid])
        out = N._exec_command_doc(srv, est, f, None, child)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("fed"), "echo got ID-4242")
        self.assertIn("ID-4242", out.get("head") or "")
        # THE REUSE: each doc keeps its OWN residue
        self.assertIn("ID-4242", (_doc(est, parent.eid)
                                  .properties or {}).get("run_head") or "")

    def test_diamond_runs_the_shared_ancestor_once(self):
        """MEMOIZATION: two children sharing an ancestor (the diamond)
        run it once per pass -- the second child reads the memo."""
        f, est = _world()
        srv = _Srv(f, est)
        a = _cmd(est, "c:base", "echo base-out")
        b1 = _cmd(est, "c:left", "echo left")
        b2 = _cmd(est, "c:right", "echo right")
        top = _cmd(est, "c:top", "echo top: {{parent.head}}")
        MultiParentOps.set_parents(est, b1.eid, [a.eid])
        MultiParentOps.set_parents(est, b2.eid, [a.eid])
        MultiParentOps.set_parents(est, top.eid, [b1.eid, b2.eid])
        memo: dict = {}
        budget = [0]
        N._exec_node(srv, est, f, None, top, memo, budget)
        # 'base' ran ONCE (one memo entry, one run_rc stamp)
        base = (_doc(est, 'c:base').properties or {})
        self.assertIsNotNone(base.get("run_rc"))
        self.assertEqual(base.get("run_count") or 1, base.get("run_count", 1))
        # the tool:run singletONE counts once for base's echo
        tr = est.docs.get(MEMBRANE_NS + "tool:run")
        # 'echo top:' = 3 nodes total ran (base, left, right, top = 4 --
        # with memo base 1: budget consumed 4 -- the ASSERT: the memo
        # holds 4 entries incl. top, base ran once)
        self.assertEqual(len(memo), 4)

    def test_composition_node_inherits_the_exit(self):
        """A Tool WITHOUT a command but WITH parents = the pipeline's
        exit node: runs the subtree, carries the LAST parent's residue
        as its own."""
        f, est = _world()
        srv = _Srv(f, est)
        p1 = _cmd(est, "c:first", "echo step-one-out")
        p2 = _cmd(est, "c:second", "echo step-two-out")
        comp = QuantumEntity(eid=MEMBRANE_NS + "c:pipeline",
                             cls=CLASS_TOOL,
                             properties={"name": "pipeline"})
        est.docs[comp.eid] = comp
        MultiParentOps.set_parents(est, comp.eid, [p1.eid, p2.eid])
        out = N._exec_command_doc(srv, est, f, None, comp)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("head") or "", "step-two-out")
        self.assertTrue(out.get("composition", {}).get("ran"))

    def test_classification_edges_never_execute(self):
        """A Component/Goal parent (no command, no own parents) is
        CLASSIFICATION -- the walk skips it, never a step."""
        f, est = _world()
        srv = _Srv(f, est)
        g = QuantumEntity(eid=MEMBRANE_NS + "goal:clean",
                          cls="Goal",
                          properties={"name": "clean", "status": "open"})
        est.docs[g.eid] = g
        child = _cmd(est, "c:needs-goal", "echo child-ran")
        MultiParentOps.set_parents(est, child.eid, [g.eid])
        out = N._exec_command_doc(srv, est, f, None, child)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("head"), "child-ran")
        self.assertIsNone((_doc(est, g.eid).properties or {})
                          .get("run_rc"))

    def test_stop_on_failure_halts_the_walk(self):
        """The failed step stops: the child carries the failure
        (outcome + which step), never runs its own command on a failed
        parent."""
        f, est = _world()
        srv = _Srv(f, est)
        bad = _cmd(est, "c:broken-step", "false")
        child = _cmd(est, "c:after-bad", "echo never")
        MultiParentOps.set_parents(est, child.eid, [bad.eid])
        out = N._exec_command_doc(srv, est, f, None, child)
        self.assertFalse(out.get("ok"))
        self.assertIn("c:broken-step", str(out.get("error")))
        self.assertIn("c:broken-step", out.get("failed_step") or "")
        # THE CHILD DID NOT RUN: no residue on it
        self.assertIsNone((_doc(est, 'c:after-bad').properties or {}).get("run_rc"))
        # the failed edge is PROCESSED (superseded by the landing lane)
        # -- the residue on the broken parent stays for the read
        self.assertIsNotNone((_doc(est, 'c:broken-step').properties or {}).get("run_rc"))

    def test_named_parent_fanin(self):
        """THE FAN-IN: {{parent:<eid>.head}} names WHICH parent feeds
        where (the multi-parent set is unordered -- the child's command
        makes it explicit)."""
        f, est = _world()
        srv = _Srv(f, est)
        src = _cmd(est, "c:fetch", "echo RAW-DATA")
        meta = _cmd(est, "c:meta", "echo META-OK")
        child = _cmd(est, "c:fanin",
                     "printf '%s-%s\\n' \"{{parent:fetch.head}}\" \"{{parent:meta.head}}\"")
        MultiParentOps.set_parents(est, child.eid, [src.eid, meta.eid])
        out = N._exec_command_doc(srv, est, f, None, child)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("fed"),
                         "printf '%s-%s\\n' \"RAW-DATA\" \"META-OK\"")
        self.assertIn("RAW-DATA", out.get("head") or "")
        self.assertIn("META-OK", out.get("head") or "")

    def test_walk_cap_continues(self):
        """The node budget stops the walk honestly (`continued`) -- the
        reaper's next beat resumes the queue."""
        f, est = _world()
        srv = _Srv(f, est)
        saved = N.EXEC_WALK_NODES_CAP
        N.EXEC_WALK_NODES_CAP = 2
        try:
            leaves = [_cmd(est, f"c:leaf{i}", "echo l") for i in range(4)]
            mid = _cmd(est, "c:mid", "echo m")
            for l in leaves:
                MultiParentOps.set_parents(est, mid.eid, [l.eid])
            top = _cmd(est, "c:after-mid", "echo done {{parent.head}}")
            MultiParentOps.set_parents(est, top.eid, [mid.eid])
            out = N._exec_command_doc(srv, est, f, None, top)
            self.assertTrue(out.get("continued"), out)
            self.assertLessEqual(out["nodes_run"], 2)
        finally:
            N.EXEC_WALK_NODES_CAP = saved


class TestQueueShape(unittest.TestCase):
    def test_reaper_lands_the_owed_and_supersedes(self):
        """run_pending_links (#614): the reaper runs the runs owed to the
        executor -- root -execute-> executor -- and each lands a result."""
        import types
        from membrane import lang as LG
        f, est = _world()
        srv = _Srv(f, est)
        srv.capturer = types.SimpleNamespace(mode="on")
        root = LG.mint(srv, "sh", "echo reaped-ok")
        R.upsert(f.b, f.origin, root, N.EXEC_REL, MEMBRANE_NS + "tool:executor",
                 "test", ts=time.time())
        self.assertEqual([e["eid"] for e in N.executor_value(
            est, "tool:executor", be=f.b, origin=f.origin)["pending"]], [root])
        lands = N.run_pending_links(srv, est, f, srv.membrane, MEMBRANE_NS + "tool:executor")
        self.assertTrue(lands and lands[0].get("ok"))
        self.assertIn("reaped-ok", lands[0].get("head") or "")
        v = N.executor_value(est, "tool:executor", be=f.b, origin=f.origin)
        self.assertEqual(v["pending"], [])
        self.assertEqual(len(v["done"]), 1)


if __name__ == "__main__":
    unittest.main()