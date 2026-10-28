"""THE CODE TREES ARE COLLECTED (the operator, 2026-10-27: "delete instead of
consolidate and configurable number of invocation histories (say, 30 per
path?)" -- per whatever we delete).

Pinned here:
  - one seat option, code_runs_keep (default 30), read and written like the
    others; a bad value is refused, nothing written;
  - each root keeps its newest N results; each method its newest N bound
    trees; each executor its newest N one-off trees (code_run's);
  - the night's collect deletes the rest -- the trees, their results, their
    edges -- and sweeps every node no kept tree reaches; a node a kept tree
    shares stays;
  - a method's own code and a run still owed are never collected.
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import options as OP  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

EX = MEMBRANE_NS + "tool:executor"
_DATA_DIR = os.environ.get("MEMBRANE_DATA_DIR")


def tearDownModule():
    if _DATA_DIR is None:
        os.environ.pop("MEMBRANE_DATA_DIR", None)
    else:
        os.environ["MEMBRANE_DATA_DIR"] = _DATA_DIR


def _seat(keep=None):
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    home = tempfile.mkdtemp()
    os.environ["MEMBRANE_DATA_DIR"] = home                 # the seat's settings.json lives here
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                capturer=types.SimpleNamespace(mode="on"),
                                _models_path=str(Path(home) / "models.json"))
    est._srv = srv
    if keep is not None:
        OP.apply({"code_runs_keep": keep}, srv)
    return srv


def _live(srv, eid, rel):
    return [r["obj"] for r in R.history_of(srv.efield.b, srv.efield.origin, eid, rel)
            if r["subj"] == eid and not r.get("superseded")]


def _box(srv):
    box = QuantumEntity(eid=MEMBRANE_NS + "Component:box", cls="Component", properties={"name": "box"})
    srv.entity_store.upsert(box, embedder=None)
    return box


class TestTheOption(unittest.TestCase):
    def test_code_runs_keep_defaults_to_30_and_is_writable(self):
        srv = _seat()
        o = OP.read(srv)
        self.assertEqual((o["code_runs_keep"], o["defaults"]["code_runs_keep"]), (30, 30))
        self.assertIn("code_runs_keep", o["writable"])
        self.assertEqual(OP.apply({"code_runs_keep": 5}, srv)["code_runs_keep"], 5)
        self.assertEqual(OP.code_runs_keep(srv), 5)

    def test_a_bad_value_is_refused(self):
        srv = _seat()
        for bad in (0, -1, 2.5, "7", True, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                OP.apply({"code_runs_keep": bad}, srv)
        self.assertEqual(OP.code_runs_keep(srv), 30)


class TestTheCollect(unittest.TestCase):
    def test_a_root_keeps_its_newest_results(self):
        srv = _seat(keep=2)
        r1 = LG.run(srv, code="echo hi")
        LG.run(srv, node=r1["root"])
        r3 = LG.run(srv, node=r1["root"])
        live = _live(srv, r1["root"], "result")
        self.assertEqual(len(live), 2)
        self.assertIn(r3["result"], live)
        self.assertNotIn(r1["result"], srv.entity_store.docs)

    def test_a_method_keeps_its_newest_bound_trees(self):
        srv = _seat(keep=2)
        est, box = srv.entity_store, _box(srv)
        d = N.method_define(srv, est, srv.efield, None, box.eid, "say", "echo {{text}}")
        code = est.docs[d["eid"]].properties["code"]
        outs = [N.method_call(srv, est, srv.efield, None, box.eid, "say", {"text": t})
                for t in ("one-alpha", "two-beta", "three-gamma")]
        LG.collect(srv)
        gone, kept = outs[0], outs[1:]
        self.assertNotIn(gone["root"], est.docs)
        self.assertNotIn(gone["result"], est.docs)
        for o in kept:
            self.assertIn(o["root"], est.docs)
            self.assertIn(o["result"], est.docs)
        words = {est.docs[e].properties.get("text") for e in est.docs if e.startswith(MEMBRANE_NS + "node:")}
        self.assertNotIn("one-alpha", words)                 # its own node is swept
        self.assertIn("echo", words)                          # a shared one stays
        self.assertIn(code, est.docs)                         # the method's code stays
        self.assertEqual(LG.text(est, code), "echo {{text}}")

    def test_an_executor_keeps_its_newest_one_off_trees(self):
        srv = _seat(keep=2)
        est = srv.entity_store
        outs = [LG.run(srv, code=f"echo once-{i}") for i in range(3)]
        LG.collect(srv)
        self.assertNotIn(outs[0]["root"], est.docs)
        self.assertNotIn(outs[0]["result"], est.docs)
        self.assertEqual(_live(srv, outs[0]["root"], "result"), [])     # its edges went too
        for o in outs[1:]:
            self.assertIn(o["root"], est.docs)

    def test_an_older_seats_roots_are_ordered_by_their_newest_result(self):
        # roots minted before collection kept no clocks: their newest result's
        # time and executor stand in, so the recently used ones are kept
        srv = _seat(keep=2)
        est = srv.entity_store
        outs = [LG.run(srv, code=f"echo legacy-{i}") for i in range(3)]
        LG.run(srv, node=outs[0]["root"])                    # the first is the most recently used
        for o in outs:
            for k in ("last_run_ts", "executor", "born_ts"):
                est.docs[o["root"]].properties.pop(k, None)
        LG.collect(srv)
        self.assertIn(outs[0]["root"], est.docs)
        self.assertIn(outs[2]["root"], est.docs)
        self.assertNotIn(outs[1]["root"], est.docs)

    def test_a_run_still_owed_is_never_collected(self):
        srv = _seat(keep=1)
        est = srv.entity_store
        old = LG.run(srv, code="echo owed")
        LG.run(srv, code="echo newer")
        R.upsert(srv.efield.b, srv.efield.origin, old["root"], "execute", EX, "test")
        LG.collect(srv)
        self.assertIn(old["root"], est.docs)
        self.assertEqual(LG.text(est, old["root"]), "echo owed")

    def test_a_methods_code_is_never_collected(self):
        srv = _seat(keep=1)
        est, box = srv.entity_store, _box(srv)
        d = N.method_define(srv, est, srv.efield, None, box.eid, "say", "echo {{text}} never-run")
        LG.run(srv, code="echo a")
        LG.run(srv, code="echo b")
        LG.collect(srv)
        self.assertEqual(LG.text(est, est.docs[d["eid"]].properties["code"]), "echo {{text}} never-run")


if __name__ == "__main__":
    unittest.main()
