"""NO PHANTOM REBIRTH (issue #99's last two symptoms): a file entity's
sibling spellings never merge, and a mere prose mention of a dead path
re-grows the garbage. Two covenants:

1. THE SIBLING-SPELLING MERGE: a live, resolved file landing absorbs its
   '~basename' phantom -- the fallback spelling minted when the graph
   could not fold the path (same file, one entity; mark-don't-delete).
2. THE TRACK-ENTITY GATE: the boot's track-entity guarantee must NOT mint
   a store doc for a file track whose referent does not exist -- the same
   stat-at-mint covenant that already guards the night's materialization.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Fld:
    def __init__(self, graph=None):
        self.emb = type("E", (), {"model": "m"})()
        self._graph = graph
        if graph is not None:
            self.graph_fn = lambda: self._graph

    def write_events(self, evs, session=""):
        return None


def _repo(tmp_path):
    r = tmp_path / "repo"
    (r / "membrane").mkdir(parents=True)
    (r / "membrane" / "voice.py").write_text("x = 1\n")
    from membrane import codegraph
    return codegraph.build_graph(str(r))


def test_live_landing_absorbs_the_basename_phantom(tmp_path):
    from membrane.night import _file_land
    est = EntityStore(path=None, shared=False)
    # the pre-existing phantom: the unresolved '~basename' fallback
    # minted when the graph could not fold the path
    est.upsert(QuantumEntity(
        eid=MEMBRANE_NS + "files:~voice.py",
        cls="Component", properties={"name": "voice.py", "read_count": 7}),
        embedder=None)
    graph = _repo(tmp_path)
    fp = str(tmp_path / "repo" / "membrane" / "voice.py")
    eid, key, created = _file_land(_Fld(graph), est, None, fp,
                                   "read", True, "hi", {})
    assert eid == "f:membrane/voice.py", eid
    assert key == MEMBRANE_NS + "files:membrane/voice.py", key
    # the phantom retired into the resolved doc (mark-don't-delete):
    phantom = est.docs.get(MEMBRANE_NS + "files:~voice.py")
    assert phantom is not None
    assert (phantom.properties or {}).get("merged_into") == key
    # the resolved doc absorbed the phantom's counters:
    doc = est.docs[key]
    assert (doc.properties or {}).get("read_count") >= 7


def test_bare_fallback_landing_merges_nothing(tmp_path):
    """A landing that is ITSELF the '~basename' fallback (no graph) must
    not treat its own spelling as a phantom to absorb."""
    from membrane.night import _file_land
    est = EntityStore(path=None, shared=False)
    fp = str(tmp_path / "elsewhere" / "voice.py")
    (tmp_path / "elsewhere").mkdir(parents=True, exist_ok=True)
    open(fp, "w").write("x = 1\n")
    eid, key, created = _file_land(_Fld(None), est, None, fp,
                                   "read", True, "ok", {})
    assert eid == "f:~voice.py", eid
    assert created is True
    assert (est.docs[key].properties or {}).get("merged_into") is None


def test_dead_path_track_mints_no_store_doc():
    est = EntityStore(path=None, shared=False)
    dead = "f:/no/such/ghost.py"
    assert est.ensure_track_entity(dead) is None
    assert not any("ghost.py" in k for k in est.docs), list(est.docs.keys())


def test_live_file_track_still_mints():
    d = tempfile.mkdtemp()
    live = os.path.join(d, "there.py")
    open(live, "w").write("x = 1\n")
    est = EntityStore(path=None, shared=False)
    assert est.ensure_track_entity("f:" + live) is not None
