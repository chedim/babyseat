"""#725: code_run refuses a program whose SHAPE already ran 3 times as a
one-shot. The shape is the code tree (#614) with its literal leaves as holes
(the data-like arguments: paths, numbers, quoted strings, heredoc bodies,
expansions; command names, flags and bare words stay). The count lives on the
shape's own node; a method call never counts; new and rarer shapes run as
today. K = 3 is the operator's (2026-10-29)."""
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                capturer=types.SimpleNamespace(mode="on"))
    est._srv = srv
    box = QuantumEntity(eid=MEMBRANE_NS + "Component:box", cls="Component", properties={"name": "box"})
    est.upsert(box, embedder=None)
    return srv, est, f, box


def _one(srv, code=None, node=None):
    return LG.run(srv, code=code, node=node, oneshot=True)


def test_same_recipe_different_literals_is_one_shape():
    a = LG.shape_eid_of(LG.parse("sh", "cd /tmp/a && sed -n 1,5p x/y.py"))
    b = LG.shape_eid_of(LG.parse("sh", "cd /tmp/b && sed -n 9,12p z/w.py"))
    c = LG.shape_eid_of(LG.parse("sh", "cd /tmp/b && grep -n foo z/w.py"))
    assert a == b and a != c


def test_bare_words_and_flags_stay_in_the_shape():
    s = lambda t: LG.shape_eid_of(LG.parse("sh", t))     # noqa: E731
    assert s("git status") != s("git log")
    assert s("gh pr view 731") == s("gh pr view 729")
    assert s('echo "a"') == s('echo "b c"')
    assert s("ls -la") != s("ls -1")


def test_the_fourth_one_shot_of_a_shape_is_refused_naming_the_earlier_roots():
    srv, est, f, box = _world()
    roots = []
    for i in range(3):
        out = _one(srv, f"echo {i}.0 > /dev/null")
        assert out.get("ok"), out
        roots.append(out["root"])
    out = _one(srv, "echo 9.0 > /dev/null")
    assert not out.get("ok") and "refused" in out["error"]
    for r in roots:
        assert r in out["error"]
    assert "method" in out["error"] and "{{" in out["error"]


def test_the_count_lives_on_the_shapes_node():
    srv, est, f, box = _world()
    _one(srv, "echo 1.0")
    _one(srv, "echo 2.0")
    shape = LG.shape_eid_of(LG.parse("sh", "echo 3.0"))
    assert est.docs[shape].properties.get(LG.SHAPE_RUNS) == 2


def test_a_rerun_by_node_counts_too():
    srv, est, f, box = _world()
    r = _one(srv, "echo 1.0")["root"]
    _one(srv, node=r)
    _one(srv, node=r)
    assert not _one(srv, node=r).get("ok")


def test_a_method_call_never_counts():
    srv, est, f, box = _world()
    assert N.method_define(srv, est, f, None, box.eid, "say", "echo {{x}}").get("ok")
    for i in range(5):
        out = N.method_call(srv, est, f, None, box.eid, "say", {"x": f"{i}.0"})
        assert out.get("ok"), out
    assert _one(srv, "echo 7.0").get("ok")                # its shape has no one-shot runs yet


def test_new_and_rare_shapes_run_and_plain_run_is_ungated():
    srv, est, f, box = _world()
    for i in range(3):
        assert _one(srv, f"echo {i}.0").get("ok")
    assert _one(srv, "printf x").get("ok")                # a new shape
    assert LG.run(srv, code="echo 5.0").get("ok")         # not the one-shot door: no gate


def test_collect_keeps_a_counted_shape():
    srv, est, f, box = _world()
    for i in range(3):
        _one(srv, f"echo {i}.0")
    shape = LG.shape_eid_of(LG.parse("sh", "echo 0.0"))
    LG.collect(srv)
    assert shape in est.docs and est.docs[shape].properties.get(LG.SHAPE_RUNS) == 3
