"""The 👶 panel's Code tab: Blurp's cliffhanger code -- its own methods
(method_of self), each with its command and steps -- rides the sheet the
panel already reads (`code`), and the tab renders it read-only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from membrane.night import method_define  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL.bind(srv)
    return srv


def test_the_sheet_carries_blurps_own_methods():
    srv = _seat()
    est = srv.entity_store
    method_define(srv, est, None, None, on=SL._eid(), method="wave", command="self::nudge {{target}}")
    other = QuantumEntity(eid=MEMBRANE_NS + "Component:other", cls="Component", properties={"name": "other"})
    est.docs[other.eid] = other
    assert method_define(srv, est, None, None, on=other.eid, method="other_one", command="echo other")["ok"]
    code = SH.compute(srv)["code"]
    by = {m["name"]: m for m in code}
    for meth, cmd in SL.METHODS.items():
        assert by[meth]["command"] == cmd, by.get(meth)
    assert by["wave"]["command"] == "self::nudge {{target}}"
    assert "other_one" not in by                        # only Blurp's own methods
    assert [m["name"] for m in code] == sorted(by)      # a stable order
    assert SH.empty()["code"] == []


@NODE
def test_the_code_tab_renders_each_method_escaped():
    sheet = {"code": [{"name": "try", "command": "self::try {{seed}}", "runs": 3, "params": ["seed"]},
                      {"name": "x<y", "command": "echo <b>", "runs": 0,
                       "steps": [{"step": "a", "command": "echo 1"}]}]}
    js = "const H=require(%r); process.stdout.write(H.selfCodeHtml(%s))" % (str(UI / "home.js"), json.dumps(sheet))
    h = subprocess.check_output(["node", "-e", js]).decode()
    assert "self::try {{seed}}" in h and "x&lt;y" in h and "echo &lt;b&gt;" in h and "<b>" not in h
    assert "echo 1" in h
    js = "const H=require(%r); process.stdout.write(H.selfCodeHtml({code: []}))" % str(UI / "home.js")
    assert "no code" in subprocess.check_output(["node", "-e", js]).decode()


def test_the_panel_has_a_code_tab():
    src = (UI / "home.js").read_text()
    assert 'data-tab="code"' in src and 'id="sppane-code"' in src
