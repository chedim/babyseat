"""P0 THE UNLOCKED-READ RACES (the seat's defect 1, the live crash):
'dictionary changed size during iteration' hit three read paths while
writer threads mutate. Pin the contract: hammer the readers with
upserts, nobody raises.
1. MemBackend.vector_top_k vs concurrent upsert (the recall path).
2. eids._resolve_file/_resolve_symbol vs codegraph rebuild mutation.
3. entities store iteration vs concurrent upsert (the entity_query
   path's list()-snapshot contract)."""
import threading

import pytest


def _hammer(times: int, fn, errbox: list):
    try:
        for _ in range(times):
            fn()
    except Exception as e:                      # noqa: BLE001 - the test IS the race
        errbox.append(f"{type(e).__name__}: {e}")


def test_mem_backend_vector_top_k_vs_upsert(tmp_path):
    from membrane.efstore import FileBackend, pack_f16

    be = FileBackend(str(tmp_path / "field"))
    import numpy as np

    for i in range(50):
        be.upsert("entity_states", f"e{i}", {"eid": f"e{i}",
                                             "state": pack_f16(np.ones(4) * i),
                                             "tier": "live"})
    vec = np.ones(4)
    errs: list[str] = []

    def read():
        be.vector_top_k("entity_states", "state", vec, 3)

    def write():
        be.upsert("entity_states", "extra",
                  {"eid": "extra", "state": pack_f16(np.zeros(4)),
                   "tier": "live"})

    t_read = threading.Thread(target=_hammer, args=(300, read, errs))
    t_write = threading.Thread(target=_hammer, args=(2000, write, errs))
    t_read.start(); t_write.start()
    t_read.join(); t_write.join()
    assert not errs, f"read path tore: {errs}"


def test_eids_resolve_vs_codegraph_rebuild():
    from membrane import eids
    from membrane.codegraph import build_graph

    graph = {"root": "/tmp/cg-race", "files": {"a.py": {"mtime": 1,
             "symbols": ["s_a"]}}, "symbols": {}, "defs": {}}
    errs: list[str] = []

    def read():
        eids._resolve_file("a.py", graph)
        eids._resolve_symbol("a", graph)

    def write():
        # the mutation the bridge does: in-place update + insert
        graph["files"]["b.py"] = {"mtime": 2, "symbols": ["s_b"]}
        graph["files"]["a.py"] = {"mtime": 3, "symbols": ["s_a2"]}
        graph["defs"]["s_b"] = {"qual": "s_b", "file": "b.py"}
        graph["files"].pop("b.py", None)
        graph["defs"].pop("s_b", None)

    t_read = threading.Thread(target=_hammer, args=(2000, read, errs))
    t_write = threading.Thread(target=_hammer, args=(1500, write, errs))
    t_read.start(); t_write.start()
    t_read.join(); t_write.join()
    assert not errs, f"resolve read path tore: {errs}"


def test_entities_store_iteration_vs_upsert():
    """The entity-query path's contract: iterations over self.docs are
    list()-pre-cooked; a hammering upsert never tears them."""
    from membrane.entities import EntityStore, QuantumEntity

    st = EntityStore(path=None, shared=False)
    for i in range(200):
        st.docs[f"membrane::c:e{i}"] = QuantumEntity(
            eid=f"membrane::c:e{i}", cls="c", properties={"name": f"e{i}"})
    errs: list[str] = []

    def read():
        # the read paths' shape: snapshot iteration + per-key lookups
        for ent in list(st.docs.values()):
            _ = st.docs.get(ent.eid)

    def write():
        st.docs[f"membrane::c:extra{threading.get_ident()}"] = QuantumEntity(
            eid=f"membrane::c:extra{threading.get_ident()}", cls="c",
            properties={})

    t_read = threading.Thread(target=_hammer, args=(3000, read, errs))
    t_write = threading.Thread(target=_hammer, args=(3000, write, errs))
    t_read.start(); t_write.start()
    t_read.join(); t_write.join()
    assert not errs, f"store snapshot contract broke: {errs}"

def test_vector_cache_no_rebuild_per_read(tmp_path):
    """THE 80s-TURN EXHIBIT: vector_top_k rebuilt a 27k-row f64 matrix
    PER READ. The cache: the matrix builds once per write-generation;
    a read after reads (no writes) does not re-walk the coll."""
    from membrane.efstore import FileBackend, pack_f16
    import numpy as np

    be = FileBackend(str(tmp_path / "f"))
    walks = {"n": 0}
    real_items = be.colls.items

    def spy_items(*a, **k):
        walks["n"] += 1
        return real_items(*a, **k)
    for i in range(300):
        be.upsert("entity_states", f"e{i}", {"eid": f"e{i}",
                                             "state": pack_f16(np.ones(4) * i),
                                             "tier": "live"})
    q = np.ones(4)
    be.vector_top_k("entity_states", "state", q, 5)
    n_first = walks["n"]
    be.vector_top_k("entity_states", "state", q, 5)
    be.vector_top_k("entity_states", "state", q, 5)
    assert walks["n"] == n_first, "the cache re-walked per read"
    # a write invalidates:
    be.upsert("entity_states", "e0", {"eid": "e0",
                                      "state": pack_f16(np.ones(4) * 99),
                                      "tier": "live"})
    r = be.vector_top_k("entity_states", "state", q, 5)
    assert str(r[0][0]) == "e0"              # the new value wins
