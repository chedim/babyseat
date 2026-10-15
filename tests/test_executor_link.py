"""THE EXECUTOR (the edge-backed exec organ, 2026-10-06): the model
links command entities to the seeded `tool:executor` and the ENGINE
runs them inline -- the 'executes' edge (a relations triple) IS the
ledger: its existence marks the link action; the run's residue
(run_rc/outcome/head/tail/ms) lands on the COMMAND entity (the
run-lane covenant, reached through the link door). Pinned here:

1. LINK -> RUN: entity_link resolves/creates the command docs, runs
   each inline (the same run_command_entity lane), and the RETURN
   carries every command's post-run state (the sync rule).
2. ON-THE-FLY: an unresolved spec becomes a new Tool entity whose
   `command` prop is the spec itself (one-call authoring);
   create=False refuses instead.
3. THE EDGE: a real relations-triple (subj=executor, rel='executes');
   a re-link bumps n (re-run); the dedupe is the store's own merge.
4. UNLINK: supersedes the edges (mark-don't-delete); the residue
   stays on the command docs; a not-linked spec is an honest miss.
5. THE CAPS: >3 commands per link is an ERROR ('failed to link'),
   never a partial link; >32 live edges errors the same way.
6. THE SYNC RULE (general): entity post-processing rides the tool
   return -- `_entity_post_process` fires the getter chain inline and
   attaches the result as 'processed' on the add/edit response.
7. THE SEED: tool:executor plants once at boot, idempotent.
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
from membrane.night import (entity_link, entity_unlink, executor_value,
                            seed_executor_entity)  # noqa: E402


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


class TestLink(unittest.TestCase):
    def test_link_runs_inline_and_returns_post_run_state(self):
        f, est = _world()
        est.add(cls=CLASS_TOOL, name="run tests",
                summary="the suite runner")
        doc = _doc(est, "run tests")
        doc.properties["command"] = "echo linked-run-ok"
        est.upsert(doc)
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:executor", ["run tests"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["executor"], MEMBRANE_NS + "tool:executor")
        self.assertEqual(len(r["runs"]), 1)
        run = r["runs"][0]
        self.assertTrue(run["ok"])
        self.assertEqual(run["rc"], 0)
        self.assertEqual(run["head"], "linked-run-ok")
        # THE COVENANT: the residue lands on the COMMAND entity
        props = _doc(est, doc.eid).properties or {}
        self.assertEqual(props.get("run_rc"), 0)
        self.assertEqual(props.get("run_outcome"), "ok")
        self.assertEqual(props.get("run_head"), "linked-run-ok")

    def test_on_the_fly_creation_is_one_call(self):
        f, est = _world()
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:executor",
                        ["echo born-to-run"])
        self.assertTrue(r["ok"])
        run = r["runs"][0]
        self.assertTrue(run["ok"])
        self.assertTrue(r["linked"][0]["created"])
        doc = _doc(est, run["eid"])
        self.assertEqual(doc.cls, CLASS_TOOL)
        self.assertEqual((doc.properties or {}).get("command"),
                         "echo born-to-run")
        self.assertEqual((doc.properties or {}).get("run_rc"), 0)

    def test_create_false_refuses_unresolved(self):
        f, est = _world()
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:executor", ["echo nope"],
                        create=False)
        self.assertTrue(r["ok"])          # the link itself handled
        run = r["runs"][0]
        self.assertFalse(run["ok"])
        self.assertIn("no entity matches", run["error"])
        self.assertEqual(r["linked"], [])

    def test_a_bracketed_spec_reuses_the_clean_command(self):
        """issue #87: a command name wrapped as a list/array ('['pwd-ls-73f4']'
        or '[pwd-ls-73f4]') re-links the SAME entity -- no ghost minted with
        the brackets/quotes baked into its name."""
        f, est = _world()
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:executor", ["pwd-ls-73f4"])
        self.assertTrue(r["ok"])
        eid = r["linked"][0]["eid"]
        for bad in ("['pwd-ls-73f4']", "[pwd-ls-73f4]", '["pwd-ls-73f4"]'):
            r2 = entity_link(srv, est, f, None, "tool:executor", bad)
            self.assertTrue(r2["ok"], bad)
            self.assertEqual(r2["linked"][0]["eid"], eid, bad)
            self.assertFalse(r2["linked"][0]["created"], bad)
        # one command doc, not one per spelling
        docs = [d for d in est.docs.values()
                if (d.properties or {}).get("command") == "pwd-ls-73f4"]
        self.assertEqual(len(docs), 1, [d.eid for d in docs])

    def test_relink_bumps_the_edge_and_reruns(self):
        f, est = _world()
        srv = _Srv(f, est)
        entity_link(srv, est, f, None, "tool:executor", ["echo again"])
        import time as _t
        _t.sleep(0.01)
        r2 = entity_link(srv, est, f, None, "tool:executor", ["echo again"])
        self.assertTrue(r2["ok"])
        # THE EDGE'S COUNT: the store's own dedupe -- one triple, n=2
        rows = [d for _k, d in f.b.scan("relations")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rel"], "executes")
        self.assertEqual(int(rows[0]["n"]), 2)
        self.assertEqual(r2["runs"][0]["rc"], 0)

    def test_batch_cap_is_a_hard_error(self):
        f, est = _world()
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:executor",
                        ["echo a", "echo b", "echo c", "echo d"])
        self.assertFalse(r["ok"])
        self.assertIn("failed to link", r["error"])
        self.assertIn("cap 3", r["error"])
        # NO partial link: no new command docs, no edges
        rows = [d for _k, d in f.b.scan("relations")]
        self.assertEqual(len(rows), 0)

    def test_edge_cap_is_a_hard_error(self):
        f, est = _world()
        srv = _Srv(f, est)
        import membrane.night as _nn
        # THE QUEUE SHAPE (unlink-on-done): a run that LANDS stamps its
        # edge superseded -- the live-edge cap counts OWED work. Fill
        # the queue with cap-continued walks (the ancestors outrun the
        # node budget -- walk nodes continue on the reaper; the edge
        # stays live until the last node lands).
        _saved = _nn.EXEC_WALK_NODES_CAP
        _nn.EXEC_WALK_NODES_CAP = 1
        try:
            # 32 composition nodes, each a 2-parent walk that never
            # finishes under the 1-node budget -> each link's edge stays
            # live (owed; the reaper continues it); the 33rd is refused
            for i in range(N.EXEC_EDGES_CAP):
                w = QuantumEntity(eid=MEMBRANE_NS + f"c:w{i}",
                                  cls=CLASS_TOOL,
                                  properties={"name": f"w{i}",
                                              "command": "true"})
                est.docs[w.eid] = w
                for j in range(2):
                    p = QuantumEntity(eid=MEMBRANE_NS + f"c:w{i}p{j}",
                                      cls=CLASS_TOOL,
                                      properties={"name": f"p",
                                                  "command": "true"})
                    est.docs[p.eid] = p
                    MultiParentOps.set_parents(est, w.eid, [p.eid])
                r = entity_link(srv, est, f, None, "tool:executor",
                                [f"c:w{i}"])
                self.assertTrue(r["ok"], (i, r))
                self.assertTrue(r["runs"][0].get("continued"))
            over = entity_link(srv, est, f, None, "tool:executor",
                               ["overboard --version"])
            self.assertFalse(over["ok"])
            self.assertIn("edge cap", over["error"])
        finally:
            _nn.EXEC_WALK_NODES_CAP = _saved

    def test_linked_entity_without_command_is_honest(self):
        f, est = _world()
        srv = _Srv(f, est)
        est.add(cls="Component", name="not a command",
                summary="carries no command prop")
        r = entity_link(srv, est, f, None, "tool:executor",
                        ["not a command"], create=False)
        run = r["runs"][0]
        self.assertFalse(run["ok"])
        self.assertIn("no 'command' prop", run["error"])

    def test_missing_executor_is_an_error(self):
        f, est = _world()
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:nope", ["echo x"])
        self.assertFalse(r["ok"])
        self.assertIn("no entity matches", r["error"])

    def test_no_store_degrades(self):
        f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
        r = entity_link(_Srv(f, None), None, f, None, "tool:executor",
                        ["echo x"])
        self.assertFalse(r["ok"])
        self.assertIn("unavailable", r["error"])


class TestUnlink(unittest.TestCase):
    def test_unlink_supersedes_and_keeps_the_residue(self):
        f, est = _world()
        srv = _Srv(f, est)
        entity_link(srv, est, f, None, "tool:executor", ["echo gone-soon"])
        # the fly-minted doc lives under its command name (head + a stable
        # hash of the line, 2026-10-08 -- no longer the bare head verb)
        from membrane.night import command_name
        key = "shell:" + command_name("echo gone-soon")
        doc = _doc(est, key)
        self.assertEqual((doc.properties or {}).get("run_rc"), 0)
        # THE UNLINK-ON-DONE: the RUN already superseded the edge
        # (processed -- success OR error); a manual unlink of a done
        # edge is an honest miss now
        u = entity_unlink(srv, est, f, None, "tool:executor",
                          [key])
        self.assertTrue(u["ok"])
        self.assertEqual(u["removed"], [], "run landed: the edge was "
                         "already superseded by the unlink-on-done")
        # THE EDGE: exactly one row, superseded by the RUN not the
        # unlink (mark-don't-delete: n + evidence stay)
        rows = [d for _k, d in f.b.scan("relations")]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["superseded"])
        # THE RESIDUE stays on the command doc
        self.assertEqual((_doc(est, key)
                          .properties or {}).get("run_rc"), 0)
        # a re-LINK RE-QUEUES (un-supersedes + n bumps) and re-runs;
        # the run lands + supersedes again
        r2 = entity_link(srv, est, f, None, "tool:executor",
                         [key])
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["runs"][0].get("rc"), 0)
        rows = [d for _k, d in f.b.scan("relations")]
        self.assertEqual(int(rows[0]["n"]), 2)
        self.assertTrue(rows[0]["superseded"],
                        "landed again: processed (the queue closed)")

    def test_unlink_of_not_linked_is_an_honest_miss(self):
        f, est = _world()
        srv = _Srv(f, est)
        u = entity_unlink(srv, est, f, None, "tool:executor",
                          ["shell:never-linked"])
        self.assertTrue(u["ok"])
        self.assertEqual(u["removed"], [])
        self.assertEqual(len(u["misses"]), 1)

    def test_unlink_without_field_uses_the_pending_marker(self):
        est = EntityStore(path=None, shared=False)
        seed_executor_entity(est)
        doc = QuantumEntity(eid=MEMBRANE_NS + "c:pending",
                            cls=CLASS_TOOL,
                            properties={"command": "echo pending",
                                        "pending_run_by":
                                        MEMBRANE_NS + "tool:executor"})
        est.docs[doc.eid] = doc

        class _NoFieldSrv:
            efield = None
            entity_store = est
            membrane = None
            capturer = None
        u = entity_unlink(_NoFieldSrv(), est, None, None, "tool:executor",
                          ["c:pending"])
        self.assertTrue(u["ok"])
        self.assertEqual(u["removed"], [doc.eid])
        self.assertIsNone((_doc(est, doc.eid).properties or {})
                          .get("pending_run_by"))


class TestExecutorValue(unittest.TestCase):
    def test_value_carries_pending_and_done(self):
        """THE QUEUE'S VALUE SHAPE (unlink-on-done): a landed run's
        command sits in DONE (the superseded edge); a queued-not-run
        command sits in PENDING (the live edge -- the no-field form)."""
        f, est = _world()
        srv = _Srv(f, est)
        entity_link(srv, est, f, None, "tool:executor", ["echo val-check"])
        v = executor_value(est, "tool:executor", be=f.b,
                           origin=f.origin)
        self.assertTrue(v["ok"])
        self.assertEqual(v["eid"], MEMBRANE_NS + "tool:executor")
        self.assertEqual(v["pending"], [])
        self.assertEqual(len(v["done"]), 1)
        self.assertEqual(v["done"][0]["run_rc"], 0)
        self.assertIn("val-check", v["done"][0]["command"])
        self.assertEqual(v["done"][0]["n_times"], 1)
        # script internals never ride
        self.assertNotIn("script_", " ".join(v["properties"]))
        # THE PENDING side: a never-run queued node (the no-field
        # degrade's marker -- the fieldless shape)
        est2 = EntityStore(path=None, shared=False)
        N.seed_executor_entity(est2)
        q = QuantumEntity(eid=MEMBRANE_NS + "c:queued",
                          cls=CLASS_TOOL,
                          properties={"name": "queued", "command": "true",
                                      "pending_run_by":
                                      MEMBRANE_NS + "tool:executor"})
        est2.docs[q.eid] = q
        v2 = executor_value(est2, "tool:executor")
        self.assertEqual([e["eid"] for e in v2["pending"]],
                         [q.eid])
        self.assertEqual(v2["done"], [])

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

class TestLinkRefusesMethods(unittest.TestCase):
    """ISSUE #21 (the banner listed it): a method earns its use-count
    through the method_call door; re-running it by name through the
    LINKER used to run the raw template with no call context (its
    {{self.*}} stayed literal, rc=127) and streams.outcome's text order
    relabeled that 'not-found' -- the model saw 'FAILED -- not-found'
    while the banner advertised the same name the same turn."""

    def test_a_resolved_method_refuses_through_the_linker(self):
        f, est = _world()
        # the method doc, exactly as method_define mints it:
        # name='{owner}.{method}', method_name, method_of, a {{self.*}}
        # command template
        eid = MEMBRANE_NS + "shell:presence.exec"
        est.docs[eid] = QuantumEntity(
            eid=eid, cls=CLASS_TOOL,
            properties={"name": "presence.exec", "method_name": "exec",
                        "method_of": [MEMBRANE_NS + "presence"],
                        "command": "js::{{code}}", "runs": 3})
        srv = _Srv(f, est)
        r = entity_link(srv, est, f, None, "tool:executor",
                        ["presence.exec"], args={"code": "echo hi"})
        self.assertFalse(r["ok"], r)
        self.assertIn("is a method, not a command line", r.get("error", ""))
        self.assertIn("action='call'", r.get("error", ""))
        # refusal BEFORE the run ledger: no runs bump on a lie
        self.assertEqual(est.docs[eid].properties.get("runs"), 3)
        # and the banner now points at the honest door
        lib = N.command_library(est)
        self.assertIn("presence.exec", lib)
        self.assertIn("action=call", lib)
        # no phantom edges: nothing linked
        self.assertFalse(r.get("linked"))
