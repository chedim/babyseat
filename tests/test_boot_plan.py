"""The boot's order and policy are data (docs/python-to-c1f.md, piece 1, stage 1): `packs/boot.c1f` says when a step is
ready (the steps before it are done or failed), `packs/boot.json` lists the steps (their order, whether the boot dies
with them, whether they run in the background once the UI is up). Python only runs the closure the field names and
writes its state back. No new VM primitive."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import bootplan  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


def _plan(steps):
    return bootplan.Plan(steps)


def test_steps_run_in_the_order_the_data_gives():
    ran = []
    p = _plan([{"name": "a", "blocks": ["b"]}, {"name": "b", "blocks": ["c"]}, {"name": "c"}])
    for n in "abc":
        p.run(n, lambda n=n: ran.append(n))
    assert ran == ["a", "b", "c"] and p.state("c") == "done"


def test_a_step_before_its_turn_is_refused():
    p = _plan([{"name": "a", "blocks": ["b"]}, {"name": "b"}])
    with pytest.raises(bootplan.OutOfOrder):
        p.run("b", lambda: None)


def test_a_failed_optional_step_is_recorded_and_the_boot_goes_on():
    p = _plan([{"name": "a", "blocks": ["b"]}, {"name": "b"}])

    def boom():
        raise ValueError("no embedder")
    p.run("a", boom)
    assert p.state("a") == "failed" and "no embedder" in p.error("a")
    p.run("b", lambda: None)
    assert p.state("b") == "done"


def test_a_failed_essential_step_stops_the_boot():
    p = _plan([{"name": "a", "essential": True, "blocks": ["b"]}, {"name": "b"}])

    def boom():
        raise ValueError("no store")
    with pytest.raises(ValueError):
        p.run("a", boom)
    assert p.state("a") == "failed"


def test_background_steps_wait_for_the_ui_and_fail_alone():
    ran = []
    p = _plan([{"name": "serve"}, {"name": "warm", "background": True}, {"name": "fore", "background": True}])
    p.defer("warm", lambda: 1 / 0)
    p.defer("fore", lambda: ran.append("fore"))
    p.run("serve", lambda: ran.append("serve"))
    assert ran == ["serve"] and p.state("warm") == "pending"
    out = p.up()
    assert ran == ["serve", "fore"] and p.state("warm") == "failed" and p.state("fore") == "done"
    assert [n for n, _ in out] == ["warm", "fore"]


def test_the_shipped_plan_names_exactly_the_steps_main_runs():
    plan = json.loads((Path(bootplan.__file__).parent / "packs" / "boot.json").read_text())
    names = [s["name"] for s in plan if not s.get("background")]
    ran = re.findall(r"_run_step\('([^']+)'", SRC)
    assert names == ran
    bg = [s["name"] for s in plan if s.get("background")]
    assert bg == re.findall(r'_defer\("([^"]+)"', SRC)


def test_the_shipped_plan_loads_without_pack_errors():
    p = bootplan.Plan(bootplan.shipped())
    assert p.errors() == {}
