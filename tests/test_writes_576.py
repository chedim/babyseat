"""#576 part A: every write on the field is an event with provenance --
{path, old, new, writer, chain}. The store diffs against what each entity last
held, so an in-place edit followed by upsert still reports the old value; the
chain a source starts rides queued events through to later writes."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


@pytest.fixture
def seen():
    got = []
    W.on_write(got.append)
    yield got
    W.off_write(got.append)


def _store(path=None):
    return EntityStore(path=path or str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _ent(est, name="lamp", **props):
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:" + name, cls="Component",
                      properties={"name": name, **props})
    est.upsert(e)
    return est.docs[e.eid]


def test_an_in_place_edit_reports_old_and_new(seen):
    est = _store()
    e = _ent(est, status="open")
    seen.clear()
    e.properties["status"] = "decided"
    est.upsert(e)
    assert [(w["path"], w["old"], w["new"]) for w in seen] == [(e.eid + ".status", "open", "decided")]
    assert seen[0]["writer"] == "field" and seen[0]["chain"] is None


def test_a_birth_and_a_removal_are_writes(seen):
    est = _store()
    r = est.add("Component", "kettle")
    assert (seen[-1]["path"], seen[-1]["old"]) == (r["eid"], None)
    seen.clear()
    assert est.remove(r["eid"]) == 1
    assert [(w["path"], w["new"]) for w in seen] == [(r["eid"], None)]


def test_no_change_is_no_write(seen):
    est = _store()
    e = _ent(est, status="open")
    seen.clear()
    est.upsert(e)
    assert seen == []


def test_a_move_is_a_position_write(seen):
    est = _store()
    e = _ent(est)
    seen.clear()
    e.position = [0.0, 1.0, 0.0]
    est.upsert(e)
    assert [w["path"] for w in seen] == [e.eid + ".position"]


def test_the_writer_and_the_chain_ride_along(seen):
    est = _store()
    e = _ent(est, status="open")
    seen.clear()
    with W.source("person", "op"), W.as_writer("llm:sonnet"):
        e.properties["status"] = "decided"
        est.upsert(e)
    assert seen[0]["writer"] == "llm:sonnet"
    assert seen[0]["chain"]["id"].startswith("person-op-") and seen[0]["chain"]["root"] == "person"


def test_a_queued_event_carries_its_chain_to_later_writes(seen):
    from membrane.watch_agents import WatchEvents
    est = _store()
    e = _ent(est, status="open")
    pool = WatchEvents()
    with W.source("person", "op"):
        pool.put({"kind": "decision", "entity": e.eid})
    ev = pool.take()[0]
    assert ev["chain"]["id"].startswith("person-op-")
    seen.clear()
    with W.resume(ev):                    # the loop turn that handles it, later
        e.properties["status"] = "in_flight"
        est.upsert(e)
    assert seen[0]["chain"]["id"] == ev["chain"]["id"]


def test_an_entity_loaded_from_disk_reports_its_first_edit(seen, tmp_path):
    p = str(tmp_path / "e.json")
    est = _store(p)
    e = _ent(est, status="open")
    est.save()
    est2 = _store(p)
    seen.clear()
    e2 = est2.docs[e.eid]
    e2.properties["status"] = "closed"
    est2.upsert(e2)
    assert [(w["path"], w["old"]) for w in seen] == [(e.eid + ".status", "open")]


def test_reparenting_is_an_edge_write(seen):
    est = _store()
    _ent(est, "lights")
    e = _ent(est, "lamp")
    seen.clear()
    from membrane.entities import MultiParentOps
    assert MultiParentOps.set_parents(est, e.eid, ["lights"]).get("ok")
    assert any(w["path"] == e.eid + ".parents" for w in seen), seen


def test_a_new_field_relation_is_a_write(seen):
    from membrane import relations as R

    class B:
        def __init__(self):
            self.d = {}

        def cas_update(self, _coll, key, fn):
            self.d[key] = fn(self.d.get(key))

    b = B()
    R.upsert(b, "seat", "lamp", "in", "kitchen", source="test")
    assert [w["path"] for w in seen] == ["rel:lamp|in|kitchen"]
    seen.clear()
    R.upsert(b, "seat", "lamp", "in", "kitchen", source="test")    # a repeat: the count only
    assert seen == []


def test_a_thread_carries_the_chain():
    import threading
    got = []
    with W.source("person", "op"):
        t = threading.Thread(target=W.carry(lambda: got.append(W.current())))
    t.start(); t.join()
    assert got[0]["id"].startswith("person-op-")


def test_the_relay_headers_round_trip(seen):
    est = _store()
    e = _ent(est, status="open")
    with W.source("person", "op"), W.as_writer("llm:sonnet"):
        h = W.headers()
    seen.clear()
    with W.from_headers(h):                       # the seat's handler, another thread
        e.properties["status"] = "decided"
        est.upsert(e)
    assert seen[0]["chain"]["id"].startswith("person-op-") and seen[0]["writer"] == "llm:sonnet"
    assert W.headers(default_writer="llm") == {W.HDR_WRITER: "llm"}   # an MCP child with no context


def test_a_turn_continues_a_woken_chain_or_starts_a_persons(seen):
    with W.turn("ses_1"):
        assert W.current()["root"] == "person" and W.writer() == "llm"
    with W.source("watch", "x") as ch, W.turn("ses_loop"):
        assert W.current()["id"] == ch["id"]


def test_job_threads_and_the_relay_and_the_turn_are_wired():
    import inspect
    from membrane import jobs, mcp_server, server, lanes
    assert "carry(" in inspect.getsource(jobs)
    assert "_W.headers(" in inspect.getsource(mcp_server._http)
    assert "from_headers(" in inspect.getsource(server) and "from_headers(" in inspect.getsource(lanes)
    assert "_W.turn(" in inspect.getsource(server.TurnRunner.run)


def test_a_script_writes_as_its_entity(seen):
    import types
    from membrane import night
    est = _store()
    e = _ent(est, "battery")
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None)
    seen.clear()
    night.apply_entity_update(srv, e, {"patch": {"script_data": {"pct": 61}}}, "getter", silent=True)
    mine = [w for w in seen if w["path"].startswith(e.eid)]
    assert mine and all(w["writer"] == f"script:{e.eid}" for w in mine), seen


def test_a_sensor_fire_starts_a_chain():
    import types
    from membrane import autonode
    got = []
    node = autonode.Autonode.__new__(autonode.Autonode)
    node._fire_body = lambda eid, trig: got.append(W.current()) or {}
    node._fire("membrane::Component:door", autonode.Trigger("file", {"path": "x"}, 0.0))
    assert got[0]["root"] == "sensor" and got[0]["id"].startswith("sensor-file:")


# ---- #577 review ------------------------------------------------------------------------

def test_a_nested_in_place_edit_keeps_the_true_old_value(seen):
    est = _store()
    e = _ent(est, script_data={"pct": 40})
    seen.clear()
    e.properties["script_data"]["pct"] = 41          # the same dict object, mutated
    est.upsert(e)
    assert [(w["old"], w["new"]) for w in seen] == [({"pct": 40}, {"pct": 41})]


def test_every_start_is_its_own_chain():
    with W.turn("ses_1"):
        a = W.current()["id"]
    with W.turn("ses_1"):
        b = W.current()["id"]
    assert a != b


def test_a_birth_straight_through_docs_is_a_write_and_its_first_edit_too(seen):
    est = _store()
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:direct", cls="Component",
                      properties={"name": "direct", "status": "open"})
    est.docs[e.eid] = e                               # how ~25 call sites create entities
    assert (seen[-1]["path"], seen[-1]["old"]) == (e.eid, None)
    seen.clear()
    e.properties["status"] = "closed"
    est.upsert(e)
    assert [(w["path"], w["old"], w["new"]) for w in seen] == [(e.eid + ".status", "open", "closed")]
    seen.clear()
    est.docs.pop(e.eid)
    assert [(w["path"], w["new"]) for w in seen] == [(e.eid, None)]


def test_a_big_old_value_is_marked_not_none(seen):
    est = _store()
    e = _ent(est, blob="x" * (W.SMALL + 10))
    seen.clear()
    e.properties["blob"] = "y"
    est.upsert(e)
    assert seen[0]["old"] == W.BIG


def test_a_relation_retry_is_not_a_birth(seen):
    from membrane import relations as R

    class Retry:
        def cas_update(self, _coll, key, fn):
            fn(None)                                  # the first attempt saw nothing...
            fn({"_k": "rel", "n": 3, "evidence": [], "superseded": False})   # ...the retry, the edge

    R.upsert(Retry(), "seat", "lamp", "in", "kitchen", source="test")
    assert seen == []


def test_the_position_event_is_a_copy(seen):
    est = _store()
    e = _ent(est)
    seen.clear()
    e.position = [0.0, 1.0]
    est.upsert(e)
    assert seen[0]["new"] == [0.0, 1.0] and seen[0]["new"] is not e.position


def test_a_listener_error_is_counted():
    n0 = W.errors["n"]

    def bad(_w):
        raise ValueError("x")
    W.on_write(bad)
    try:
        W.emit("p", None, 1)
    finally:
        W.off_write(bad)
    assert W.errors["n"] == n0 + 1


def test_a_wake_fires_in_the_chain_that_set_it(monkeypatch):
    import types
    import time as _t
    from membrane import llm as L
    est = _store()
    bat = _ent(est, "bat")
    srv = types.SimpleNamespace(entity_store=est, events_pool=None, paused=False)
    est._srv = srv
    now = _t.time()
    with W.source("person", "op") as ch:
        assert L.set_wake(srv, bat.eid, "in 2h", "check it", caller="t", now=now)["rc"] == 0
    got = []
    monkeypatch.setattr(L, "request", lambda *a, **k: got.append(W.current()) or {"rc": 0, "text": "ok"})
    L.fire_due(srv, now=now + 7200 + 5)
    assert got and got[0]["id"] == ch["id"]


def test_parallel_reads_run_in_the_turns_chain():
    from concurrent.futures import ThreadPoolExecutor
    with W.source("person", "op") as ch:
        f = ThreadPoolExecutor(1).submit(W.carry(lambda: W.current()))
    assert f.result()["id"] == ch["id"]
    import inspect
    from membrane import server
    assert "_W.carry(_jobs.dispatch)" in inspect.getsource(server)     # and the turn's reads use it


def test_a_new_entity_through_upsert_is_one_birth(seen):
    est = _store()
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:fresh", cls="Component",
                      properties={"name": "fresh", "status": "open"})
    est.upsert(e)                                  # (the store's own seeded anchors are births too)
    assert [(w["path"], w["old"], w["new"]) for w in seen if w["path"].startswith(e.eid)] == \
        [(e.eid, None, e.eid)]                     # one birth; the properties ride the entity


def test_big_is_json_safe():
    import json
    assert json.loads(json.dumps({"old": W.BIG}))["old"] == W.BIG
