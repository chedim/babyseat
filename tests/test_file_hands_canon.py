"""THE FILE HANDS MINT CANONICAL IDS (fix #8's mint half, issue #8):
the night's file hands (_file_land, file_read_entity) canonicalized
ABSOLUTE paths with graph=None, so a seat whose cwd is not the repo
root minted `f:~voice.py` -- persisted, unresolvable, unreadable --
while the field's own readers minted `f:membrane/voice.py` (the same
file, two ids). THE CONTRACTS:

1. a repo-absolute path + the field's graph -> the repo-relative id
   (eids' absolute->root fold at _resolve_file:73);
2. no graph (fld None or a fussy graph_fn) -> the honest '~basename'
   fallback (same spelling the field itself falls back to);
3. the read hand's missing-file branch carries the same canon.
"""
from membrane.entities import EntityStore, MEMBRANE_NS


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


def test_land_mints_repo_relative_under_a_graph(tmp_path):
    from membrane.night import _file_land
    est = EntityStore(path=None, shared=False)
    r = tmp_path / "repo"
    fp = str(r / "membrane" / "voice.py")
    eid, key, created = _file_land(_Fld(_repo(tmp_path)), est, None, fp,
                                   "read", True, "hi", {})
    # the seat's cwd is irrelevant: the graph folds the absolute path
    # into the repo root -> the canonical repo-relative id:
    assert eid == "f:membrane/voice.py", eid
    assert key == MEMBRANE_NS + "files:membrane/voice.py", key
    assert created is True
    assert est.docs.get(key) is not None


def test_land_honest_bare_fallback_without_a_graph(tmp_path):
    """No graph: outside-repo absolute stays the '~basename' fallback --
    the same spelling the field's own canon produces (not doubled)."""
    from membrane.night import _file_land
    est = EntityStore(path=None, shared=False)
    fp = str(tmp_path / "elsewhere" / "voice.py")
    eid, key, _ = _file_land(_Fld(None), est, None, fp,
                             "write", True, "ok", {})
    assert eid == "f:~voice.py", eid
    assert key == MEMBRANE_NS + "files:~voice.py", key


def test_read_hand_missing_file_mints_the_graph_canon(tmp_path):
    from membrane.night import file_read_entity
    est = EntityStore(path=None, shared=False)
    fp = str(tmp_path / "repo" / "membrane" / "ghost.py")
    got = file_read_entity(_Fld(_repo(tmp_path)), est, None, fp, 0, None)
    assert got.get("ok") is False
    assert got.get("eid") == "f:membrane/ghost.py", got
    assert est.docs.get(MEMBRANE_NS + "files:membrane/ghost.py") is not None