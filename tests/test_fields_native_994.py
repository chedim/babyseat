"""Fields reachable from Python (#994 gaps 1, 2, 7): membrane_vm.Fields."""
import json
import pytest

MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")
if not hasattr(MV, "Fields"):
    pytest.skip("membrane_vm built without Fields", allow_module_level=True)


class H:
    def resolve(self, s): return None
    def resolve_exact(self, s): return None
    def dispatch(self, e, m): return None
    def vel(self, e, n, t): return False
    def align(self, e, t): return False
    def live(self, e, p): return None


def world():
    f = MV.Fields(H())
    f.load("t:a", "Task", json.dumps({"status": "open"}))
    f.load("t:b", "Task", json.dumps({"status": "open", "blocks": ["t:a"]}))
    f.load("t:c", "Task", json.dumps({"status": "open", "blocks": ["t:a"]}))
    return f


def ob(f, e="t:a"):
    return json.loads(f.get(e)).get("open_blockers")


def test_fields_class_is_exposed_next_to_core_and_evaluate():
    assert hasattr(MV, "Core") and hasattr(MV, "evaluate") and hasattr(MV, "Fields")


def test_getter_does_not_drift_under_doubled_and_missed_writes():
    f = world()
    f.write("t:b", "status", '"open"', 1.0)
    base = ob(f)
    f.write("t:b", "status", '"done"', 2.0)
    once = ob(f)
    f.write("t:b", "status", '"done"', 3.0)  # doubled
    assert ob(f) == once and once == base - 1
    # a missed write (t:c finished without t:c's own write heard): the next status write recounts from state
    f.load("t:c", "Task", json.dumps({"status": "done", "blocks": ["t:a"]}))
    f.write("t:a", "status", '"open"', 4.0)
    f.write("t:b", "status", '"done"', 5.0)
    assert ob(f) == once - 1 or ob(f) == 0


def test_a_read_fires_no_setter():
    f = world()
    f.write("t:b", "status", '"done"', 1.0)
    w, calls = f.writes(), f.calls()
    for _ in range(5):
        f.get("t:a"); f.get("t:b")
    assert f.writes() == w and f.calls() == calls
