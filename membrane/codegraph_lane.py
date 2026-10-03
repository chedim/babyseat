"""THE CODEGRAPH RACE (P0, the seat's defect 1 second instance): the
incremental bridge (rebuild_paths) MUTATES the shared graph
(symbols/files/defs/indices) from capture/tool-lane threads while
readers (query/stats/mentions via graph) iterate it -- 'dictionary
changed size during iteration' crashed the graph reads live. THE FIX:
one module lock; mutators hold it, readers hold it too (reads are
cheap; the mutation tail is the long side only when re-parsing -- the
sweep's throttle keeps that off the hot path)."""
import threading

CG_LOCK = threading.RLock()  # reentrant: refresh wraps rebuild


def locked_query(graph, spec: str) -> str:
    with CG_LOCK:
        from . import codegraph as _cg
        return _cg.query(graph, spec)


def locked_stats(graph) -> dict:
    with CG_LOCK:
        from . import codegraph as _cg
        return _cg.stats(graph)


def locked_rebuild(graph, rels) -> dict:
    with CG_LOCK:
        from . import codegraph as _cg
        return _cg.rebuild_paths(graph, rels)


def locked_symbol_keys(graph) -> list:
    with CG_LOCK:
        return sorted((graph.get("symbols") or {}).keys())

def locked_stale_paths(graph) -> list:
    with CG_LOCK:
        from . import codegraph as _cg
        return _cg.stale_paths(graph)
