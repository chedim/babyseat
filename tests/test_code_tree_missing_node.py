"""A method's code tree that lost a node (live 2026-11-12: Memo's `gh issue view {{number}}` method, its tree one
node short -- method_call died with a KeyError in lang.load and the HTTP handler dropped the connection) is rebuilt
from the method's command on the next call: minting makes the missing nodes again. RED first."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from test_method_tree_614c import _world, _define  # noqa: E402


def _child_of(est, root):
    return next(iter((est.docs[root].properties or {}).get("args") or []))


def test_a_method_with_a_node_missing_from_its_tree_is_rebuilt_not_crashed():
    srv, est, f, box = _world()
    m = _define(srv, est, f, box, "echo {{who}}")
    root = m.properties["code"]
    assert LG.whole(est, root)
    est.docs.pop(_child_of(est, root))                 # the tree lost a node
    assert not LG.whole(est, root)
    again = N._code_root(srv, est, f, None, est.docs[m.eid])
    assert LG.whole(est, again) and LG.text(est, again) == "echo {{who}}"


def test_a_whole_tree_is_kept_as_it_is():
    srv, est, f, box = _world()
    m = _define(srv, est, f, box, "echo {{who}}")
    root = m.properties["code"]
    assert N._code_root(srv, est, f, None, est.docs[m.eid]) == root
