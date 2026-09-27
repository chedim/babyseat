"""THE BACKFILL: rebuild the entity field from history, through the same
intake the live seat uses.

    python -m membrane.backfill [--audit .membrane/audit.jsonl] [--code .]
        [--origin <tag>] [--max-minutes N] [--store auto|mem]

Order: (1) the codebase (code events + relations), (2) the audit log in
file order (the six capture shapes -> events; sessions split at 30-min
gaps; the first ~11k records carry no timestamp and take their line number
as seq, so they sort first), (3) embed + apply everything, (4) the null
coherence, activity and claims. Idempotent (content keys: a second run
writes nothing) and resumable (the byte offset is kept in origin_stats).

Run it with the seat's capture OFF (MEMBRANE_EFIELD=off, the default):
history's sequence numbers must sort before live events."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import claims as CL
from . import code_evidence as CE
from . import streams as ST

SESSION_GAP_S = 30 * 60


def _progress(msg: str) -> None:
    print(f"[backfill] {msg}", file=sys.stderr, flush=True)


def backfill_code(field, root: str, progress=_progress) -> int:
    if field._ostats.get("backfill_code_done"):
        return 0
    from .codegraph import build_graph
    graph = build_graph(root)
    evs = CE.code_events(graph)
    for lo in range(0, len(evs), 500):
        field.write_events(evs[lo:lo + 500], session="code")
    CE.store_relations(field, graph, list(graph.get("files") or {}))
    field._ostats["backfill_code_done"] = True
    field.b.upsert("origin_stats", field._okey, field._ostats)
    progress(f"code: {len(evs)} definitions")
    return len(evs)


def backfill_audit(field, audit: str, chunk: int = 200, deadline: float | None = None,
                   max_records: int | None = None, progress=_progress) -> dict:
    """Read the audit log from the saved offset; write events chunk by chunk,
    embed + apply after each chunk, save the offset. Returns counters."""
    st = field._ostats.setdefault("backfill", {"path": audit, "offset": 0, "line": 0,
                                               "done": False, "session": 0, "last_ts": None})
    if st.get("done"):
        return {"records": 0, "events": 0, "done": True}
    path = Path(audit)
    seen = 0
    events = 0
    buf: list = []
    with path.open("rb") as fh:
        fh.seek(int(st["offset"]))
        while True:
            raw = fh.readline()
            if not raw:
                break
            st["line"] += 1
            try:
                rec = json.loads(raw)
            except ValueError:
                rec = None
            if rec and rec.get("event") == "decision" and rec.get("query"):
                ts = rec.get("ts")
                if ts is not None and st["last_ts"] is not None and ts - st["last_ts"] > SESSION_GAP_S:
                    st["session"] += 1
                if ts is not None:
                    st["last_ts"] = ts
                sess = f"b:{st['session']}" if ts is not None else "b:no-ts"
                for i, ev in enumerate(ST.split_audit_record(rec)):
                    seq = None if ts is not None else st["line"] * 16 + i
                    buf.append((ev, sess, seq))
                seen += 1
            if len(buf) >= chunk or (max_records is not None and seen >= max_records):
                events += _flush(field, buf)
                buf = []
                st["offset"] = fh.tell()
                field.b.upsert("origin_stats", field._okey, field._ostats)
                progress(f"audit line {st['line']}: {events} events")
                if max_records is not None and seen >= max_records:
                    return {"records": seen, "events": events, "done": False}
                if deadline is not None and time.time() > deadline:
                    return {"records": seen, "events": events, "done": False}
        events += _flush(field, buf)
        st["offset"] = fh.tell()
    st["done"] = True
    field.b.upsert("origin_stats", field._okey, field._ostats)
    return {"records": seen, "events": events, "done": True}


def _flush(field, buf) -> int:
    """Write a chunk (grouped by session, preserving order), then embed +
    apply it."""
    n = 0
    i = 0
    while i < len(buf):
        sess = buf[i][1]
        j = i
        while j < len(buf) and buf[j][1] == sess:
            j += 1
        group = buf[i:j]
        seqs = [s for _, _, s in group]
        evs = [e for e, _, _ in group]
        if all(s is None for s in seqs):
            field.write_events(evs, session=sess)
        else:
            field.write_events(evs, session=sess,
                               seqs=[s if s is not None else field._next_seq(e.ts)
                                     for e, s in zip(evs, seqs)])
        n += len(group)
        i = j
    field.process()
    field.trim_cache()
    return n


def finish(field, progress=_progress) -> dict:
    field.process()
    null = field.refresh_null()
    settled = CL.evaluate(field, now=float("inf"))
    progress(f"null coherence {null[0]:.3f} +- {null[1]:.3f}; {settled} claims settled")
    return field.vitals()


def main() -> None:
    ap = argparse.ArgumentParser(prog="membrane.backfill")
    ap.add_argument("--audit", default=".membrane/audit.jsonl")
    ap.add_argument("--code", default=".")
    ap.add_argument("--field", default=".membrane/field.json")
    ap.add_argument("--origin", default=None)
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--store", default="auto", choices=("auto", "mem"))
    args = ap.parse_args()
    from .efield import EntityField
    from .efstore import MemBackend, open_backend
    from .embed import OllamaEmbedder
    from .field import _origin_tag
    from .project_env import load_project_env
    load_project_env()
    be = MemBackend() if args.store == "mem" else open_backend(args.field)
    origin = args.origin or _origin_tag(args.field)
    graph_holder = {}

    def graph_fn():
        if "g" not in graph_holder:
            from .codegraph import build_graph
            graph_holder["g"] = build_graph(args.code)
        return graph_holder["g"]
    field = EntityField(be, OllamaEmbedder(model=args.embed_model), origin, graph_fn=graph_fn)
    _progress(f"origin {origin} on {type(be).__name__}")
    t0 = time.time()
    deadline = t0 + args.max_minutes * 60 if args.max_minutes else None
    backfill_code(field, args.code)
    r = backfill_audit(field, args.audit, deadline=deadline)
    if r["done"]:
        _progress(json.dumps(finish(field)))
    else:
        _progress(f"paused after {r['records']} records ({time.time() - t0:.0f}s); run again to resume")


if __name__ == "__main__":
    main()
