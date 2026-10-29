"""#586 follow-ups: a labelled or spaced retired-dialect line is refused too; a forced
removal of one method takes its step nodes; ensure_write_method's duplicate cleanup
takes the duplicates' step nodes."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _owner(est):
    c = QuantumEntity(eid=MEMBRANE_NS + "Component:scratch", cls="Component", properties={"name": "scratch"})
    est.upsert(c)
    return c


def test_labelled_and_spaced_retired_dialects_are_refused(tmp_path):
    est, target = _est(), tmp_path / "pwned"
    for line in (f"x: js::echo pwned > {target}", f"js ::echo pwned > {target}",
                 f"toybox :: reset {target}", f"job: llm.call a b > {target}",
                 f"c:js::echo pwned > {target}"):
        out = N.run_command_entity(None, est, None, line)
        assert out.get("rc") == 2 and "retired" in str(out), (line, out)
    assert not target.exists()


def test_a_plain_shell_line_is_not_taken_for_a_dialect():
    for line in ("echo js::x", "ls self", "git log: --oneline"):
        assert not N.RETIRED_DIALECT.match(line), line


def test_forcing_one_method_takes_its_step_nodes():
    est = _est()
    c = _owner(est)
    N.method_define(None, est, None, None, c.eid, "two", "", steps=[{"name": "s1", "command": "echo 1"},
                                                                  {"name": "s2", "command": "echo 2"}])
    m = next(e for e, d in est.docs.items() if c.eid in ((d.properties or {}).get("method_of") or []))
    nodes = [e for e in est.docs if e.startswith(m + ":")]
    assert nodes
    r = N.remove_entity(est, m, force=True)
    assert r["removed"] == 1 + len(nodes)
    assert not any(e.startswith(m) for e in est.docs)
    assert c.eid in est.docs


def test_a_duplicate_definition_goes_with_its_step_nodes():
    est = _est()
    c = _owner(est)
    N.ensure_write_method(None, est, c.eid, "set", "self.level", "n")
    keep = next(e for e, d in est.docs.items() if (d.properties or {}).get("method_name") == "set")
    dup = QuantumEntity(eid=MEMBRANE_NS + "Method:dup", cls="Method",
                        properties={"method_of": [c.eid], "method_name": "set", "command": "echo old"})
    est.upsert(dup)
    est.upsert(QuantumEntity(eid=dup.eid + ":s1", cls="Tool", properties={"command": "echo 1"}))
    assert N.ensure_write_method(None, est, c.eid, "set", "self.level", "n")
    assert dup.eid not in est.docs and dup.eid + ":s1" not in est.docs
    assert keep in est.docs
