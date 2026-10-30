"""#740: Blurp's own move (a granted look's live write to `self.at`) adds one `reach`
line to its record -- the shape the 👁 eye cam's trail draws (home.js reads kind, eid,
fid, ts). The world's push (`self.current`) never does."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import QuantumEntity  # noqa: E402

from test_self_is_body_732 import _self, _teach_and_grant_look, seat  # noqa: E402,F401  (the fixture)


def _reaches(srv):
    p = SL.record_path(srv)
    if p is None or not p.exists():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("kind") == "reach"]


def _with_record(srv, tmp_path):
    srv._models_path = str(tmp_path / "data" / "models.json")
    return srv


def test_a_live_look_writes_one_reach_line_with_the_entity_it_moved_to(seat, tmp_path):
    _with_record(seat, tmp_path)
    _teach_and_grant_look(seat)
    assert _reaches(seat) == []                      # the push alone wrote none
    f = seat.efield
    f.gen, f.near = 2, "w:c"
    B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()   # the granted look moves self.at
    assert _self(seat)["at"] == "w:b"
    lines = _reaches(seat)
    assert len(lines) == 1
    r = lines[0]
    assert r["eid"] == "w:b" and r["fid"] == field_id("w:b") and isinstance(r["ts"], float)


def test_a_current_reading_writes_no_line(seat, tmp_path):
    _with_record(seat, tmp_path)
    f = seat.efield
    for g, near in ((1, "w:a"), (2, "w:b"), (3, "w:c")):
        f.gen, f.near = g, near
        B._show_body(seat, seat.ch_vm); seat.ch_vm.drain()
    assert _self(seat).get("current") == "w:c"
    assert _reaches(seat) == []


def test_a_write_to_self_at_by_anyone_else_writes_no_line(seat, tmp_path):
    _with_record(seat, tmp_path)
    ent = seat.entity_store.docs.get(CH.SELF) or QuantumEntity(eid=CH.SELF, cls="Component", properties={})
    ent.properties["at"] = "w:a"
    seat.entity_store.upsert(ent, embedder=None)
    seat.ch_vm.drain()
    assert _reaches(seat) == []
