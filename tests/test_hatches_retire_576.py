"""#576 G2: the last hatches retire. `llm::`, `toybox::`, `seat::restart` and
`js::` no longer dispatch from a command line; the methods that used them are
WRITE STEPS -- `{name, write: [target, expr]}`: the step writes a property (an
adapter branch runs the effect) and returns the adapter's receipt. A write step
on the adapters' own worker (a learned invoke) runs its effect inline."""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as AD  # noqa: E402
from membrane import jsexec as JS  # noqa: E402
from membrane import llm as LLM  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import restarts as RS  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


@pytest.fixture
def seat(tmp_path, monkeypatch):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False,
                                _models_path=str(tmp_path / "m.json"), data_dir=str(tmp_path))
    calls = []

    def rec(name, ret=None):
        def fn(*a, **k):
            calls.append((name, a, k))
            return ret if ret is not None else {"rc": 0, "text": f"{name} done"}
        return fn
    monkeypatch.setattr(LLM, "request", rec("request"))
    monkeypatch.setattr(LLM, "set_wake", rec("set_wake"))
    monkeypatch.setattr(LLM, "unwake", rec("unwake"))
    monkeypatch.setattr(RS, "do_restart", rec("do_restart"))
    monkeypatch.setattr(JS, "run", rec("js", {"rc": 0, "text": "=> 42"}))
    monkeypatch.setattr(TB, "from_command", rec("toybox", {"rc": 0, "text": "toys: die"}))
    srv.calls = calls
    LLM.bind(srv)
    TB.bind(srv)
    RS.bind(srv)
    JS.bind(srv)
    AD.start(srv)
    yield srv
    AD.stop()


def _call(srv, obj, meth, args):
    return N.method_call(srv, srv.entity_store, None, None, obj, meth, args)


def test_a_write_step_is_defined_and_checked(seat):
    est = seat.entity_store
    o = QuantumEntity(eid=MEMBRANE_NS + "Component:box", cls="Component", properties={"name": "box"})
    est.upsert(o)
    ok = N.method_define(seat, est, None, None, o.eid, "say", "",
                         steps=[{"name": "w", "write": ["self.note", "text"]}])
    assert ok["ok"], ok
    bad = N.method_define(seat, est, None, None, o.eid, "bad", "", steps=[{"name": "w", "write": ["self.command", "x"]}])
    assert not bad["ok"]
    out = _call(seat, o.eid, "say", {"text": "hello"})
    assert out["status"] == "ok" and est.docs[o.eid].properties["note"] == "hello"


def test_llm_methods_are_writes_and_return_the_receipt(seat):
    out = _call(seat, "llm", "call", {"about": "x", "prompt": "check it"})
    assert out["status"] == "ok" and "request done" in (out.get("head") or ""), out
    assert [c[0] for c in seat.calls] == ["request"]
    _call(seat, "llm", "wake", {"about": "x", "at": "in 2h", "prompt": "p"})
    _call(seat, "llm", "unwake", {"id": "w1"})
    assert [c[0] for c in seat.calls] == ["request", "set_wake", "unwake"]


def test_toybox_and_presence_restart_are_writes(seat):
    out = _call(seat, "toybox", "list", {})
    assert "toys: die" in (out.get("head") or "")
    _call(seat, "toybox", "poke", {"toy": "toy:die", "action": "roll"})
    assert seat.calls[-1][0] == "toybox" and "poke" in seat.calls[-1][1][0]
    pres = SP._entity(seat).eid
    _call(seat, pres, "restart", {"why": "test"})
    assert seat.calls[-1][0] == "do_restart"
    out = _call(seat, pres, "exec", {"code": "6*7"})          # #614: JS runs as code, not a method
    assert out.get("status") != "ok" and seat.calls[-1][0] == "do_restart"


def test_the_hatch_prefixes_no_longer_dispatch(seat):
    for line in ("llm::call x y", "toybox::list", "seat::restart why", "js::1+1"):
        out = N.run_command_entity(None, seat.entity_store, None, line)
        assert out.get("rc") != 0
    assert seat.calls == []                          # nothing reached llm / toybox / restart / js


def test_live_hatch_methods_are_redefined_at_boot(tmp_path, monkeypatch):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False,
                                _models_path=str(tmp_path / "m.json"), data_dir=str(tmp_path))
    LLM.bind(srv)
    m = next(d for d in est.docs.values() if (d.properties or {}).get("method_name") == "call")
    m.properties["command"] = "llm::call {{about}} {{prompt}}"          # an old seat's
    m.properties.pop("parents", None)
    LLM.bind(srv)
    m = next(d for d in est.docs.values() if (d.properties or {}).get("method_name") == "call")
    assert not m.properties.get("command") and m.properties.get("parents")


def test_a_write_step_on_the_adapters_worker_runs_inline(seat):
    est = seat.entity_store
    m = next(d for d in est.docs.values() if (d.properties or {}).get("method_name") == "call")
    m.properties["invoke"] = {"object": "membrane::llm", "args": {"about": "x", "prompt": "p"}}
    est.upsert(m)
    deadline = time.time() + 5
    while time.time() < deadline and not any(c[0] == "request" for c in seat.calls):
        time.sleep(0.05)
    assert any(c[0] == "request" for c in seat.calls)                  # no deadlock on the one worker


# ---- #585 review -----------------------------------------------------------------------------

def test_a_retired_dialect_line_is_refused_never_run_as_shell(seat, tmp_path):
    target = tmp_path / "pwned"
    for line in (f"js::echo pwned > {target}", f"toybox::reset {target}", f"seat::restart now > {target}",
                 f"llm::call x y > {target}", f"self::try 1 > {target}"):
        out = N.run_command_entity(None, seat.entity_store, None, line)
        assert out.get("rc") == 2 and "retired" in str(out), (line, out)
    assert not target.exists()


def test_concurrent_write_steps_each_get_their_own_receipt(seat, monkeypatch):
    import threading
    seen = []
    monkeypatch.setattr(TB, "from_command", lambda line: (time.sleep(0.05), seen.append(line),
                                                          {"rc": 0, "text": f"done {line}"})[2])
    outs = []
    ts = [threading.Thread(target=lambda: outs.append(_call(seat, "toybox", "list", {}))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(seen) == 3 and all(o["status"] == "ok" for o in outs)          # three effects, three receipts


def test_an_empty_write_is_refused_at_once(seat):
    t0 = time.time()
    out = _call(seat, "llm", "unwake", {"id": ""})
    assert out.get("rc") not in (0, None) and time.time() - t0 < 2


def test_a_failing_shell_step_is_what_the_composite_returns(seat):
    est = seat.entity_store
    o = QuantumEntity(eid=MEMBRANE_NS + "Component:job", cls="Component", properties={"name": "job"})
    est.upsert(o)
    assert N.method_define(seat, est, None, None, o.eid, "go", "",
                           steps=[{"name": "fail", "command": "echo oops; exit 3"},
                                  {"name": "after", "command": "echo never"}])["ok"]
    out = _call(seat, o.eid, "go", {})
    assert out["rc"] == 3 and "oops" in (out.get("head") or "") and "never" not in (out.get("head") or "")


def test_duplicate_hatch_definitions_are_cleared(seat):
    est = seat.entity_store
    llm = est.docs[MEMBRANE_NS + "llm"]
    dup = QuantumEntity(eid=MEMBRANE_NS + "shell:llm.call-old", cls="Tool",
                        properties={"name": "llm.call", "method_name": "call", "method_of": [llm.eid],
                                    "command": "llm::call {{about}} {{prompt}}"})
    est.upsert(dup)
    LLM.bind(seat)
    calls = [d for d in est.docs.values() if (d.properties or {}).get("method_name") == "call"
             and llm.eid in ((d.properties or {}).get("method_of") or [])]
    assert len(calls) == 1 and not calls[0].properties.get("command")
