"""The HA integration's seat supervisor (custom_components/membrane/supervisor.py):
its pure parts, without Home Assistant installed -- the child's command, its
environment (never HA's own: no SUPERVISOR_TOKEN for a seat that runs model-
written commands), the pair and the name it reads."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "membrane"


@pytest.fixture(scope="module")
def sup():
    pytest.importorskip("aiohttp")
    pkg = types.ModuleType("cc_membrane"); pkg.__path__ = [str(ROOT)]
    sys.modules["cc_membrane"] = pkg
    for name in ("const", "supervisor"):
        spec = importlib.util.spec_from_file_location(f"cc_membrane.{name}", ROOT / f"{name}.py")
        mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
    return sys.modules["cc_membrane.supervisor"]


def test_the_child_env_is_chosen_not_inherited(sup, monkeypatch, tmp_path):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "secret")
    monkeypatch.setenv("HASSIO_TOKEN", "secret")
    env = sup.child_env(tmp_path, tmp_path / ".membrane")
    assert "SUPERVISOR_TOKEN" not in env and "HASSIO_TOKEN" not in env
    assert env["HOME"] == str(tmp_path / "home") and env["MEMBRANE_DATA_DIR"] == str(tmp_path / ".membrane")
    assert env["MEMBRANE_NAME_CEREMONY"] == "1"


def test_the_child_listens_on_loopback_only(sup, tmp_path):
    a = sup.child_args(tmp_path, "http://ollama:11434", "m", "e", 18500, 18096, python="py")
    assert a[:4] == ["py", "-m", "membrane.opencode", "--serve"]
    assert a[a.index("--serve-host") + 1] == "127.0.0.1" and a[a.index("--listen-host") + 1] == "127.0.0.1"
    assert "--no-launch" in a and "--no-absorb" in a
    assert a[a.index("--field") + 1] == str(tmp_path / "field.json")


def test_name(sup, tmp_path):
    assert sup.read_name(tmp_path) == (sup.DEFAULT_NAME, [])
    (tmp_path / "settings.json").write_text(json.dumps({"name": "Juniper", "aliases": ["juni"]}))
    assert sup.read_name(tmp_path) == ("Juniper", ["juni"])


def test_no_homeassistant_in_the_package():
    pkg = Path(__file__).resolve().parent.parent / "membrane"
    bad = [str(p) for p in pkg.rglob("*.py") if "import homeassistant" in p.read_text()
           or "from homeassistant" in p.read_text()]
    assert bad == []


def test_the_seat_is_ready_when_it_answers(sup, tmp_path):
    # #144: readiness was 'the pair is known'; an open seat has no pair --
    # ready = its health answers
    import asyncio
    hass = types.SimpleNamespace(config=types.SimpleNamespace(path=lambda p: str(tmp_path / p)))
    comp = {"serve_url": "http://127.0.0.1:1"}
    s = sup.SeatSupervisor(hass, comp, "http://b", "m", "e")
    answers = iter([False, False, True])

    async def _healthy():
        return next(answers)
    s._healthy = _healthy
    asyncio.run(s._ready(poll_s=0))
    assert comp["serve_ready"] is True


def test_no_gate_reads_the_password_as_readiness():
    # every 'is the seat up?' gate keyed on serve_pass; with no pair they all
    # stayed shut (sensors, chat relay, HA access) -- readiness is serve_ready
    import re
    bad = []
    for f in sorted(ROOT.glob("*.py")):
        for i, ln in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"""(\bif\b|\bnot\b|\band\b|\bbool\()[^#]*get\(["']serve_pass["']\)""", ln):
                bad.append(f"{f.name}:{i}: {ln.strip()}")
    assert not bad, "\n".join(bad)


def test_connect_needs_no_password():
    # #144: a seat has no login now -- the connect form must not demand one
    # (only a reverse proxy in front may ask; then it is given)
    src = (ROOT / "config_flow.py").read_text()
    assert "vol.Required(CONF_SERVE_PASS)" not in src
    assert "vol.Required(CONF_SERVE_USER" not in src
