"""#741: one shape logic for every program. A method fixes its program -- a
parameter may never be the program (a command name, or an interpreter's script):
those PASSTHROUGHS are refused at define and retired at boot. Every method's
template is noted on its shape's node; a one-shot whose tree matches a method's
template (the fixed parts equal, the params binding the run's literals in order)
runs AS that method, and the reply names it; a define duplicating an existing
method's template is refused, naming it."""
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                capturer=types.SimpleNamespace(mode="on"))
    est._srv = srv
    for name in ("box", "crate"):
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + f"Component:{name}", cls="Component",
                                 properties={"name": name}), embedder=None)
    return srv, est, f


BOX, CRATE = MEMBRANE_NS + "Component:box", MEMBRANE_NS + "Component:crate"


def _define(srv, est, f, on, method, command):
    return N.method_define(srv, est, f, None, on, method, command)


@pytest.mark.parametrize("cmd", ["bash -c {{cmd}}", "sh -c {{script}}", "{{cmd}}", "{{cmd}} --flag x",
                                 "python -c {{code}}", "python3 -c {{code}}", "node -e {{js}}",
                                 "eval {{x}}", "cd /tmp && bash -c {{cmd}}", "sh -c 'ls {{d}}'",
                                 "zsh -c {{cmd}}", "perl -e {{p}}",
                                 "nohup {{cmd}}", "env {{cmd}}", "sudo bash -c {{x}}",
                                 "nohup bash -c {{x}}", "env FOO=1 {{cmd}}", "time {{cmd}}",
                                 "sudo -u root {{cmd}}", "exec {{x}}", "source {{f}}", ". {{f}}",
                                 "nohup env {{cmd}}"])
def test_a_passthrough_is_refused_at_define(cmd):
    srv, est, f = _world()
    out = _define(srv, est, f, BOX, "run", cmd)
    assert not out.get("ok")
    assert "a method fixes its program" in out["error"] or cmd == "{{cmd}}"   # a bare {{x}} is the dict lint's


@pytest.mark.parametrize("cmd", ["gh pr view {{n}}", "cd {{wt}} && git log --oneline -1",
                                 "sed -n {{a}},{{b}}p {{file}}", "bash scripts/x.sh {{arg}}",
                                 "nohup pytest {{path}} > {{log}}", "time make {{target}}",
                                 "env FOO={{x}} mycmd", "xargs -n1 rm < {{list}}",
                                 "sudo -u {{user}} ls {{d}}", "nohup pytest {{path}} > {{log}} 2>&1 &"])
def test_a_method_whose_params_are_data_is_allowed(cmd):
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "m", cmd).get("ok")


def _raw_method(est, label, owner, command):
    eid = MEMBRANE_NS + "c:" + label
    est.upsert(QuantumEntity(eid=eid, cls="Tool", properties={
        "name": label, "command": command, "method_name": label.split(".")[-1], "method_of": [owner]}),
        embedder=None)
    return eid


def test_boot_retires_the_existing_passthroughs_and_indexes_the_rest():
    srv, est, f = _world()
    bad = _raw_method(est, "box.sh", BOX, "bash -c {{cmd}}")
    good = _raw_method(est, "box.view", BOX, "echo pr view {{n}}")
    out = LG.boot_methods(srv)
    assert out["retired"] == [bad]
    assert est.docs[bad].properties.get("retired")                 # marked, never deleted
    assert not est.docs[good].properties.get("retired")
    assert N.resolve_method(est, est.docs[BOX], "sh")[0] is None     # a retired method is not called
    assert not N.method_call(srv, est, f, None, BOX, "sh", {"cmd": "echo hi"}).get("ok")
    assert LG.run(srv, code="echo pr view 5.0", oneshot=True).get("routed")   # the backfilled index routes


def test_a_one_shot_matching_a_method_runs_as_that_method_and_says_so():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}} --json {{f}}").get("ok")
    out = LG.run(srv, code="echo pr view 731 --json title", oneshot=True)
    assert out.get("ok") and out.get("rc") == 0, out
    assert "pr view 731 --json title" in str(out.get("output") or out.get("head"))
    assert out["routed"]["method"].endswith("box.view")
    assert "view(n, f)" in out["note"] and "call it next time" in out["note"]
    m = est._resolve_spec(out["routed"]["method"])
    assert m.properties["runs"] == 1                               # recorded as a call of the method
    assert m.properties["last_call"]["args"] == {"n": "731", "f": "title"}


def test_a_routed_run_never_counts_toward_the_one_shot_gate():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}}").get("ok")
    for i in range(5):
        assert LG.run(srv, code=f"echo pr view {i}.0", oneshot=True).get("routed")


def test_a_one_shot_matching_no_method_still_counts():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}}").get("ok")
    for i in range(3):
        out = LG.run(srv, code=f"echo pr list {i}.0", oneshot=True)
        assert out.get("ok") and not out.get("routed")
    assert not LG.run(srv, code="echo pr list 9.0", oneshot=True).get("ok")   # #734's 4th


def test_differing_fixed_parts_are_no_match():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "open", "echo pr list --state open --limit {{n}}").get("ok")
    assert _define(srv, est, f, BOX, "every", "echo pr list --state all --limit {{n}}").get("ok")
    out = LG.run(srv, code="echo pr list --state closed --limit 5", oneshot=True)
    assert out.get("ok") and not out.get("routed")


def test_a_param_never_binds_an_expansion():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "say", "echo said {{x}}").get("ok")
    out = LG.run(srv, code="echo said $HOME", oneshot=True)
    assert out.get("ok") and not out.get("routed")                 # its meaning would change quoted


def test_a_define_duplicating_an_existing_method_is_refused_naming_it():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}} --json {{f}}").get("ok")
    out = _define(srv, est, f, CRATE, "show", "echo pr view {{x}} --json {{y}}")
    assert not out.get("ok") and "box.view" in out["error"] and "call it" in out["error"]
    out = _define(srv, est, f, CRATE, "one", "echo pr view 731 --json title")
    assert not out.get("ok") and "box.view" in out["error"]
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}} --json {{f}} --web").get("ok")   # redefining itself


def test_two_matching_methods_run_the_more_called_and_name_the_other():
    srv, est, f = _world()
    # neither covers the other, so both may exist; one run matches both
    assert _define(srv, est, f, BOX, "flagged", "echo pr view {{n}} --json title").get("ok")
    assert _define(srv, est, f, CRATE, "json", "echo pr view 731 --json {{f}}").get("ok")
    for i in range(2):
        N.method_call(srv, est, f, None, BOX, "flagged", {"n": "1"})
    out = LG.run(srv, code="echo pr view 731 --json title", oneshot=True)
    assert out["routed"]["method"].endswith("box.flagged")
    assert "crate.json" in out["note"]


def test_collect_keeps_a_shape_that_knows_a_method():
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}}").get("ok")
    LG.collect(srv)
    assert LG.run(srv, code="echo pr view 3.0", oneshot=True).get("routed")


def test_the_mcp_reply_shows_a_routed_run_and_its_note(monkeypatch):
    from membrane import mcp_server as M
    srv, est, f = _world()
    assert _define(srv, est, f, BOX, "view", "echo pr view {{n}}").get("ok")
    out = LG.run(srv, code="echo pr view 7.0", oneshot=True)
    monkeypatch.setattr(M, "_http", lambda *a, **k: out)
    text = M.tool_code_run({"code": "echo pr view 7.0"}, 0)
    assert "refused" not in text and "call it next time" in text and "box.view" in text
    assert "pr view 7.0" in text
