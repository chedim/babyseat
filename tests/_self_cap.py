"""#299: the 🌱 growth limit gave way to acceptance. The self tests that used
the limit to run one rung's machinery alone use cap() instead: it wakes self
(0: asleep), accepts what is earned in the closure -- as accept lines, the
way the old limit opened it, without the toy sync a real accept runs (a test
syncs the toys itself) -- and cuts the open skills to that closure (the
runtime's `isolate`, which the seat never sets). A pass the loop earns during
a test still waits for its accept, as on a seat."""
import time

from membrane import self_loop as SL


def cap(srv, n) -> bool:
    ids = SL._limit_ids(n)
    rt = SL._rt(srv)
    rt["isolate"] = ids
    if not ids:
        return SL.set_mode(srv, "off")
    ok = SL.set_mode(srv, rt["mode"] if rt["mode"] != "off" else "on")
    recs = SL._read(srv)
    for i in [i for i in SL._earn_order(recs) if i in ids and i not in SL._accepted(recs)]:
        SL._append(srv, {"ts": time.time(), "kind": "accept", "id": i, "by": "test"})
    return ok


def world(srv, *eids) -> list:
    """#337: Blurp sees only its world (self_world). A test that hands self
    things to look at puts them in its world -- the named eids, or every
    entity in the store but self's own methods -- as an unlock or a chat
    would on a seat."""
    from membrane import self_world as SW
    if not eids:
        eids = [e for e, d in list(srv.entity_store.docs.items())
                if not (d.properties or {}).get("method_of")]
    return SW.join(srv, eids, "test")


def ladder3(lines: list) -> list:
    """The pass lines of rungs 6-9 tagged "ladder": 3: #333 rebuilt meet, turns,
    expect and mirror on the nudge, and a pass of theirs counts only on ladder 3."""
    out = []
    for r in lines:
        if r.get("kind") in ("rung", "achieve") and r.get("rung") in (6, 7, 8, 9):
            r = {**r, "ladder": 3}
        out.append(r)
    return out
