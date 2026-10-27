"""#600 (the operator): a fresh seat locked up on glm-5.3-flash:cloud's rate
limit -- nobody had picked it, the family heuristic had -- until the models were
changed by hand and the seat restarted. Now:
- with nobody's pick, no cloud model is the default (only cloud: none);
- the options say whether the Goal worker's model (the chat's default and the
  watch loops', one runner) was ever picked;
- the first run asks for it beside the name, through the options route the ⚙
  window uses, so the pick applies without a restart."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import minds_models as MM  # noqa: E402
from membrane import options as OP  # noqa: E402
from membrane.lanes import _pick_default_model  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def test_no_cloud_model_is_anyones_default():
    assert _pick_default_model(["glm-5.3-flash:cloud", "gpt-oss:20b-cloud", "mistral:7b"], None) == "mistral:7b"
    assert _pick_default_model(["glm-5.3-flash:cloud", "deepseek-v4-pro:cloud"], None) is None
    assert _pick_default_model(["glm-5.3-flash:cloud"], "glm-5.3-flash:cloud") == "glm-5.3-flash:cloud"   # a pick wins


class _Runner:
    model = "qwen3:8b"


class _Srv:
    def __init__(self, d):
        self._loop_runner = _Runner()
        self.idle_cortex_model = "qwen3:8b"
        self._models_path = str(Path(d) / "models.json")
        self.backend = ""


def test_the_options_say_whether_a_model_was_picked(monkeypatch):
    monkeypatch.setattr(MM, "pickable", lambda srv: ["qwen3:8b", "glm-5.3-flash:cloud"])
    srv = _Srv(tempfile.mkdtemp())
    assert OP.read(srv)["background"]["picked"] is False
    MM.apply(srv, loops="glm-5.3-flash:cloud")
    assert OP.read(srv)["background"]["picked"] is True
    assert json.load(open(srv._models_path))["loops"] == "glm-5.3-flash:cloud"


def test_the_first_run_asks_for_the_model_beside_the_name():
    src = (UI / "index.html").read_text()
    assert "/membrane/options" in src and ".picked" in src       # asked only when nobody picked
    assert '"loops"' in src or "loops:" in src                   # saved as the Goal worker's model


def test_a_boot_is_not_a_pick(monkeypatch, tmp_path):
    """The boot plants the active-model entities and reapplies them: that must not
    save models.json, or every fresh seat reads as picked and never asks (seen on
    a throwaway fresh seat). Only a real pick saves."""
    from membrane import active_models as AM
    from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
    srv = _Srv(str(tmp_path))
    srv.entity_store = EntityStore(path=None, shared=False)
    for name in ("qwen3:8b", "glm-5.3-flash:cloud"):
        e = QuantumEntity(eid=MEMBRANE_NS + "models:" + name, cls="AIModel", properties={"name": name})
        srv.entity_store.docs[e.eid] = e
    monkeypatch.setattr(AM, "known_models", lambda s: ["qwen3:8b", "glm-5.3-flash:cloud"])
    AM.load(srv)
    assert MM.picked(srv) is False
    ent = srv.entity_store._resolve_spec("active-model:loops")
    ent.properties["model"] = "glm-5.3-flash:cloud"
    AM.on_change(srv, ent)                                     # a person's edit is a pick
    assert MM.picked(srv) is True
