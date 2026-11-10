"""The seat's upgrade step for entity scripts (#1044, the operator 2026-11-10: "that script migration should be part
of seat's upgrade when it starts on old version data"; translation (c): rules first, the model for the rest, a replay
check gates every switch; finished one-offs retired; Blurp's toys skipped while Blurp is off)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import scriptport as SP  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

HEARD = "result = {'state': {'heard_n': int(membrane.me.properties.get('heard_n') or 0)}}"


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _add(est, eid, **props):
    est.upsert(QuantumEntity(eid=eid, cls="Component", properties=props), embedder=None)


def test_a_known_template_becomes_its_kind_after_the_replay_matches():
    est = _est()
    _add(est, "membrane::Component:chat:dima", name="chat:dima", heard_n=3, getter_script=HEARD)
    rep = SP.upgrade(est)
    p = est.docs["membrane::Component:chat:dima"].properties
    assert "getter_script" not in p or not p["getter_script"]
    assert "membrane::cls:chatthing" in p["parents"]
    assert p["script_port"]["to"] == "ChatThing" and p["script_port"]["code"] == HEARD
    assert rep["ported"] == ["membrane::Component:chat:dima"]
    assert SP.upgrade(est)["ported"] == []                          # done once: a second boot changes nothing


def test_a_replay_that_disagrees_keeps_the_python_script():
    est = _est()
    _add(est, "membrane::Component:chat:odd", name="chat:odd", heard_n="not a number", getter_script=HEARD)
    rep = SP.upgrade(est)
    p = est.docs["membrane::Component:chat:odd"].properties
    assert p["getter_script"] == HEARD and "chatthing" not in str(p.get("parents"))
    assert rep["pending"] == ["membrane::Component:chat:odd"]


def test_a_finished_one_off_is_retired_and_a_live_one_is_not(tmp_path):
    est = _est()
    gone = "/tmp/membrane-scriptport-test-gone.rc"
    if os.path.exists(gone):
        os.remove(gone)
    here = f"/tmp/membrane-scriptport-test-here-{os.getpid()}.rc"
    Path(here).write_text("0")
    _add(est, "membrane::Component:done:x", name="done:x", tick_script=f"import os\nif os.path.exists('{gone}'):\n    result = {{}}")
    _add(est, "membrane::Component:done:y", name="done:y", tick_script=f"import os\nif os.path.exists('{here}'):\n    result = {{}}")
    rep = SP.upgrade(est)
    assert not est.docs["membrane::Component:done:x"].properties.get("tick_script")
    assert est.docs["membrane::Component:done:x"].properties["script_port"]["retired"]
    assert "membrane::Component:done:x" in rep["retired"]
    assert est.docs["membrane::Component:done:y"].properties.get("tick_script")      # its file is still there
    os.remove(here)


def test_blurps_toys_are_left_alone_while_blurp_is_off():
    est = _est()
    _add(est, "membrane::Component:toy:ball", name="toy:ball", toybox={"name": "ball"},
         getter_script="from membrane import toybox as _tb\nresult = _tb.getter(state)")
    rep = SP.upgrade(est, blurp=False)
    assert est.docs["membrane::Component:toy:ball"].properties["getter_script"]
    assert rep["skipped"] == ["membrane::Component:toy:ball"]
