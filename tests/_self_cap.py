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


REBUILT_IDS = ("meet", "turns", "expect", "mirror")


def _old_built_at() -> dict:
    """When the four's old tests merged (self_sheet.BUILT_AT before #333): the
    whole-tree mechanics below were measured against them."""
    import datetime as dt

    def utc(*a):
        return dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp()
    return {"meet": {"pr": 187, "merged": utc(2026, 10, 16, 19, 24, 2)},
            "turns": {"pr": 281, "merged": utc(2026, 10, 19, 15, 19, 43)},
            "expect": {"pr": 281, "merged": utc(2026, 10, 19, 15, 19, 43)},
            "mirror": {"pr": 314, "merged": utc(2026, 10, 20, 13, 21, 13)}}


OLD_BUILT_AT = _old_built_at()


def rebuilt(monkeypatch) -> None:
    """#333: meet, turns, expect and mirror are not built until they are
    rebuilt on the nudge (part 3), and a pass of theirs counts only with
    "ladder": 3. A test of the MECHANICS that walk the whole tree (acceptance,
    the tree's edges, the sheet's and the card's layout) builds them here, as
    part 3 will, and writes their passes with "ladder": 3 (ladder3())."""
    from membrane import self_loop as SL
    from membrane import self_sheet as SH
    for i in REBUILT_IDS:
        monkeypatch.setitem(SL.ACHIEVEMENTS[i], "built", True)
        monkeypatch.setitem(SH.BUILT_AT, i, OLD_BUILT_AT[i])
    monkeypatch.setattr(SL, "BUILT", [i for i, a in SL.ACHIEVEMENTS.items() if a["built"]])


def ladder3(lines: list) -> list:
    """The pass lines of rungs 6-9 tagged "ladder": 3 (see rebuilt())."""
    out = []
    for r in lines:
        if r.get("kind") in ("rung", "achieve") and r.get("rung") in (6, 7, 8, 9):
            r = {**r, "ladder": 3}
        out.append(r)
    return out


def start_rebuilt() -> list:
    """rebuilt() for a unittest module (setUpModule): the patches, started;
    stop them with stop_rebuilt() (tearDownModule)."""
    from unittest import mock
    from membrane import self_loop as SL
    from membrane import self_sheet as SH
    ps = [mock.patch.dict(SL.ACHIEVEMENTS[i], {"built": True}) for i in REBUILT_IDS]
    ps.append(mock.patch.dict(SH.BUILT_AT, OLD_BUILT_AT))
    ps.append(mock.patch.object(SL, "BUILT", [i for i, a in SL.ACHIEVEMENTS.items()
                                              if a["built"] or i in REBUILT_IDS]))
    for p in ps:
        p.start()
    return ps


def stop_rebuilt(ps: list) -> None:
    for p in reversed(ps or []):
        p.stop()
