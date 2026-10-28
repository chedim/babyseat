"""A command's graph lives in its `script` property, and properties are screened
by entities.json_safe on their way to disk: past depth 6 a value becomes its str().
A stage's words sit at depth 7, so after a restart every stored graph came back
with words like "{'raw': 'bash', ...}" and every command method died with a
TypeError (the proxy dropped the connection). What runs must come from the
command itself, and a screened graph must never be trusted.

#614 C: what runs is the command's code tree -- one shallow entity per node --
so the trip through the disk must leave every tree printing its command."""
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lang as LG  # noqa: E402
from membrane import scripts as S  # noqa: E402
from membrane.entities import EntityStore, sanitize_properties  # noqa: E402
from tests.test_scripts_576 import CORPUS  # noqa: E402

DEEP = "(cd {{path}} && (echo a | (cat; echo b))) 2>&1 | tail -5"


def _restarted(cmd):
    doc = types.SimpleNamespace(properties={"command": cmd})
    S.graph_of(doc)                                           # cached on the entity
    doc.properties = sanitize_properties(doc.properties)      # the trip through the disk
    return doc


@pytest.mark.parametrize("cmd", CORPUS + [DEEP])
def test_a_command_runs_the_same_after_a_restart(cmd):
    est = EntityStore(path=None, shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, capturer=None)
    root = LG.mint(srv, "sh", cmd, params=True)
    for d in est.docs.values():                               # the trip through the disk
        d.properties = sanitize_properties(d.properties)
    assert LG.text(est, root) == cmd


@pytest.mark.parametrize("cmd", CORPUS + [DEEP])
def test_its_graph_is_whole_again_after_a_restart(cmd):
    g = S.graph_of(_restarted(cmd))
    assert all(isinstance(w, dict) for st in S.stages(g) for w in st["words"])
