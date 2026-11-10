"""0.7.5: with membrane::self disabled, toybox toys are dormant."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))


def test_toys_dormant_when_self_is_off(tmp_path):
    from membrane import self_loop
    from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
    from test_night_mode import _FakeSrv, _thinker
    marker = tmp_path / "ran.txt"
    est = EntityStore(path=None, shared=False)
    toy = QuantumEntity(eid=MEMBRANE_NS + "toy:fast", cls="tool", properties={
        "name": "toy:fast", "tick_every": 1,
        "tick_script": f"open({str(marker)!r}, 'a').write('x')\n"
                       "result = {'render': 'x'}"})
    est.docs[toy.eid] = toy
    srv = _FakeSrv(store=est)
    th = _thinker(srv, est)
    with mock.patch.object(self_loop, "mode", return_value="off"):
        for _ in range(5):
            th._tick_scripts()
            th._beat += 1
    props = toy.properties
    assert not marker.exists(), "toy tick script ran while self is off"
    assert not props.get("script_last_started")
    assert not props.get("script_failures") and not props.get("script_disabled"), "strikes recorded while self is off"


def test_w_burst_toy_dormant_when_self_is_off(tmp_path):
    """A toy is recognised by the toybox marker, not by a 'toy:' name prefix."""
    from membrane import self_loop
    from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
    from test_night_mode import _FakeSrv, _thinker
    marker = tmp_path / "ran.txt"
    est = EntityStore(path=None, shared=False)
    toy = QuantumEntity(eid=MEMBRANE_NS + "c:w:burst", cls="tool", properties={
        "name": "w:burst", "tick_every": 1,
        "toybox": {"kind": "burst", "name": "w:burst"},
        "tick_script": f"open({str(marker)!r}, 'a').write('x')\n"
                       "result = {'render': 'x'}"})
    est.docs[toy.eid] = toy
    th = _thinker(_FakeSrv(store=est), est)
    with mock.patch.object(self_loop, "mode", return_value="off"):
        for _ in range(5):
            th._tick_scripts()
            th._beat += 1
    assert not marker.exists(), "w:burst toy ticked while self is off"
