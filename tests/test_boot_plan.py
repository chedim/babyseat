"""The boot's order and policy are data (docs/python-to-c1f.md, piece 1): `packs/boot.c1f` says when a step is ready,
`packs/boot.json` lists the steps (order, whether the boot dies with one, whether one runs in the background once the
UI is up) -- and the NATIVE VM walks it (`Fields.boot` / `Fields.boot_up`, native/src/plan.rs). Python only hands over
the closures of the steps by name (the bridge: a stopgap that shrinks as steps become c1f)."""
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
V = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "membrane" / "packs"
SRC = (ROOT / "membrane" / "opencode.py").read_text()


class _Host:
    pass


def _dir(steps):
    d = Path(tempfile.mkdtemp())
    (d / "boot.c1f").write_text((PACKS / "boot.c1f").read_text())
    (d / "boot.json").write_text(json.dumps(steps))
    return str(d)


def _boot(steps, fns, up=False):
    f = V.Fields(_Host())

    def bridge(name):
        fn = fns.get(name)
        if fn is None:
            return False
        fn()
    rep = json.loads(f.boot(_dir(steps), bridge))
    return f, bridge, rep


def test_steps_run_in_the_order_the_data_gives():
    ran = []
    fns = {n: (lambda n=n: ran.append(n)) for n in "abc"}
    _f, _b, rep = _boot([{"name": "c"}, {"name": "a", "blocks": ["b"]}, {"name": "b", "blocks": ["c"]}], fns)
    assert ran == ["a", "b", "c"] and rep["c"]["state"] == "done"


def test_a_failed_optional_step_is_recorded_and_the_boot_goes_on():
    ran = []

    def boom():
        raise ValueError("no embedder")
    _f, _b, rep = _boot([{"name": "a", "blocks": ["b"]}, {"name": "b"}], {"a": boom, "b": lambda: ran.append("b")})
    assert rep["a"]["state"] == "failed" and "no embedder" in rep["a"]["error"] and ran == ["b"]


def test_a_failed_essential_step_raises_its_own_exception():
    def boom():
        raise KeyError("no store")
    with pytest.raises(KeyError):
        _boot([{"name": "a", "essential": True, "blocks": ["b"]}, {"name": "b"}], {"a": boom, "b": lambda: None})


def test_a_system_exit_in_any_step_ends_the_boot_unchanged():
    def bye():
        raise SystemExit(3)
    with pytest.raises(SystemExit) as e:
        _boot([{"name": "a"}], {"a": bye})
    assert e.value.code == 3


def test_a_step_with_nothing_to_run_is_skipped():
    _f, _b, rep = _boot([{"name": "a", "blocks": ["b"]}, {"name": "b"}], {"b": lambda: None})
    assert rep["a"]["state"] == "skipped" and rep["b"]["state"] == "done"


def test_background_steps_wait_for_boot_up_and_fail_alone():
    ran = []
    steps = [{"name": "serve"}, {"name": "warm", "background": True}, {"name": "fore", "background": True}]
    f, bridge, rep = _boot(steps, {"serve": lambda: ran.append("serve"), "warm": lambda: 1 / 0,
                                   "fore": lambda: ran.append("fore")})
    assert ran == ["serve"] and rep["warm"]["state"] == "pending"
    rep = json.loads(f.boot_up(bridge))
    assert ran == ["serve", "fore"] and rep["warm"]["state"] == "failed" and rep["fore"]["state"] == "done"


def test_the_shipped_plan_names_exactly_the_steps_main_runs():
    plan = json.loads((PACKS / "boot.json").read_text())
    names = [s["name"] for s in plan if not s.get("background")]
    ran = re.findall(r"_STEPS\['([^']+)'\]", SRC)
    assert names == ran
    assert [s["name"] for s in plan if s.get("background")] == re.findall(r'_defer\("([^"]+)"', SRC)


def test_the_shipped_plan_loads_without_pack_errors():
    f = V.Fields(_Host())
    f.boot(str(PACKS), lambda n: None)
    assert json.loads(f.pack_errors()) == {}
