"""forget is a WRITE (the operator, 2026-11-12: everything moves to cliffhanger): `Memory:seat.forget = {spec}`. The pack
(packs/memory.c1f) parses the spec -- '#12', '3-7', '3-7, 9' are indexes (at most 12 events), anything wordier only asks for
the nearest events -- and hands the driver `cut = {nums}` (delete those events) or `near = {spec}` (list candidates).
Nothing wordy ever deletes. The tool, its route and its bridge are gone."""
import os
import sys
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

EID = "Memory:seat"


@pytest.fixture
def seat(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    cut = []
    monkeypatch.setattr(L, "_efield_forget", lambda srv, nums: (cut.append(list(nums)), [{"n": n, "key": f"k{n}", "entities": ["a"]} for n in nums])[1])
    monkeypatch.setattr(L, "_write_audit", lambda *a, **k: None)
    near = []
    monkeypatch.setattr(AD, "_near_events", lambda srv, spec, k: (near.append(spec), [{"n": 7, "sim": 0.91, "text": "the bilge pump"}])[1])
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=types.SimpleNamespace(),
                                agent_writes=True, _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    FL.start(srv)
    AD.start(srv)
    FL.boot_started(est, facts={"agent_writes": True})
    srv.cut, srv.near = cut, near
    yield srv, est
    AD.stop()
    FL.stop(srv)


def _until(fn, timeout=6.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def _forget(est, spec):
    assert _until(lambda: EID in est.docs)
    d = est.docs[EID]
    d.properties = {**d.properties, "forget": {"spec": spec}}
    est.upsert(d, embedder=None)


@pytest.mark.parametrize("spec,nums", [("#12", [12]), ("12", [12]), ("3-5", [3, 4, 5]), ("3-5, 9", [3, 4, 5, 9]),
                                       ("#4 #4 4", [4]), ("9-7", [7, 8, 9])])
def test_an_index_spec_cuts_exactly_those_events(seat, spec, nums):
    srv, est = seat
    _forget(est, spec)
    assert _until(lambda: est.docs[EID].properties.get("cut_result"))
    assert sorted(srv.cut[0]) == nums and srv.near == []


@pytest.mark.parametrize("spec", ["the bilge pump", "everything", "#3 and #4", "3.5", "-5", "5-", "3--7", "a-b"])
def test_a_wordy_spec_only_lists_the_nearest_events_and_deletes_nothing(seat, spec):
    srv, est = seat
    _forget(est, spec)
    r = _until(lambda: est.docs[EID].properties.get("near_result"))
    assert "#7" in r and "Nothing removed" in r
    assert srv.cut == [] and srv.near == [spec]


@pytest.mark.parametrize("spec", ["1-13", "1-5, 6-12, 20", "1-100"])
def test_more_than_the_agent_cap_is_refused_and_nothing_is_cut(seat, spec):
    srv, est = seat
    _forget(est, spec)
    r = _until(lambda: est.docs[EID].properties.get("forget_result"))
    assert r["removed"] is None and "cap (12)" in r["reason"]
    time.sleep(0.3)
    assert srv.cut == [] and srv.near == []


def test_exactly_the_cap_is_fine(seat):
    srv, est = seat
    _forget(est, "1-12")
    assert _until(lambda: est.docs[EID].properties.get("cut_result"))
    assert srv.cut[0] == list(range(1, 13))


def test_an_empty_spec_is_refused(seat):
    srv, est = seat
    _forget(est, "   ")
    r = _until(lambda: est.docs[EID].properties.get("forget_result"))
    assert r["removed"] is None and "spec required" in r["reason"]


def test_the_tool_the_route_and_the_bridge_are_gone():
    assert "forget" not in M.METHOD_TOOLS and "forget" not in M.TOOL_IMPLS
    assert not [t for t in M.METHOD_TOOL_DEFS if t["name"] == "forget"]
    assert "def tool_forget" not in Path(M.__file__).read_text()
    lanes = Path(L.__file__).read_text()
    assert '"/membrane/forget"' not in lanes and "def _membrane_forget" not in lanes
