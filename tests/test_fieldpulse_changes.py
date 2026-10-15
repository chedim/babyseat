"""THE FIELD'S CHANGED PULSE (2026-10-15): every script apply and every
edit lands a `field.changed` frame -- the graph shows ALL changes, no
matter the source -- each carrying a tiny kind/brief label and never the
render/prose text. Silent (per-tick) applies pulse too: the heartbeat is
its own change."""
import time

from membrane.entities import EntityStore, QuantumEntity
from membrane import fieldpulse as FP
from membrane import night


class _Hub:
    def __init__(self):
        self.frames = []

    def publish(self, f):
        self.frames.append(f)


class _Srv:
    pass


def _seat(tmp_path):
    srv = _Srv()
    srv.membrane = type("M", (), {})()
    srv.efield = None
    srv.entity_store = EntityStore(path=str(tmp_path / "ents.json"),
                                   shared=False)
    ent = QuantumEntity(eid="membrane::tool:clock", cls="tool",
                        properties={"name": "clock"})
    srv.entity_store.docs[ent.eid] = ent
    srv._field_pulse = FP.FieldPulse(srv, hub=_Hub())
    return srv, ent


def _changed_frame(p):
    return next((f for f in p.frames()
                 if f["type"] == "field.changed"), None)


class TestChangedFrame:
    def test_changed_records_and_frames_emits(self):
        p = FP.FieldPulse(_Srv(), hub=_Hub())
        p.changed("f:a", "tick", "")
        p.changed("f:b", "getter", "battery")
        p.changed("f:a", "edit", "say")            # last write wins
        ch = _changed_frame(p)
        assert ch == {"type": "field.changed",
                      "changes": [{"eid": "f:a", "kind": "edit", "brief": "say"},
                                  {"eid": "f:b", "kind": "getter", "brief": "battery"}]}
        assert _changed_frame(p) is None           # drained

    def test_changed_brief_truncated_to_48(self):
        p = FP.FieldPulse(_Srv(), hub=_Hub())
        p.changed("f:a", "tick", "x" * 100)
        ch = _changed_frame(p)
        assert len(ch["changes"][0]["brief"]) <= 48

    def test_changed_feeds_the_recent_window(self):
        p = FP.FieldPulse(_Srv(), hub=_Hub())
        p.changed("f:clock", "tick", "")
        assert "f:clock" in p.recent_since(time.time() - 5.0)

    def test_field_applied_is_untouched(self):
        p = FP.FieldPulse(_Srv(), hub=_Hub())
        p.applied(["f:a", "f:b"], "discourse")
        p.changed("f:a", "tick", "")
        kinds = [f["type"] for f in p.frames()]
        assert kinds == ["field.applied", "field.changed"]


class TestApplyPulses:
    def test_a_silent_tick_pulses_with_kind_tick(self, tmp_path):
        srv, ent = _seat(tmp_path)
        night.apply_entity_update(srv, ent, {"render": "12:00:00"},
                                  "tick", silent=True)
        ch = _changed_frame(srv._field_pulse)
        assert ch is not None
        assert ch["changes"] == [{"eid": "membrane::tool:clock",
                                  "kind": "tick", "brief": ""}]

    def test_a_position_patch_pulses_moved(self, tmp_path):
        srv, ent = _seat(tmp_path)
        night.apply_entity_update(srv, ent,
                                  {"patch": {"position": [0.0, 1.0, 0.0]}},
                                  "tick", silent=True)
        ch = _changed_frame(srv._field_pulse)
        assert ch["changes"][0]["brief"] == "moved"

    def test_an_intent_pulses_goal(self, tmp_path):
        srv, ent = _seat(tmp_path)
        night.apply_entity_update(srv, ent, {"intent": "steer home"},
                                  "watch", silent=True)
        ch = _changed_frame(srv._field_pulse)
        assert ch["changes"][0]["brief"] == "goal"

    def test_a_getter_script_data_apply_pulses_the_kind(self, tmp_path):
        srv, ent = _seat(tmp_path)
        night.apply_entity_update(srv, ent,
                                  {"patch": {"script_data": {"n": 7}}},
                                  "getter", silent=True)
        ch = _changed_frame(srv._field_pulse)
        assert ch["changes"][0] == {"eid": "membrane::tool:clock",
                                    "kind": "getter", "brief": ""}

    def test_no_render_text_rides_the_tick_frame(self, tmp_path):
        srv, ent = _seat(tmp_path)
        night.apply_entity_update(srv, ent,
                                  {"render": "the captain's private note"},
                                  "tick", silent=True)
        ch = _changed_frame(srv._field_pulse)
        assert ch["changes"][0]["brief"] == ""


class TestEditPulse:
    def test_edit_pulses_the_property_key(self, tmp_path):
        from membrane.lanes import _pulse_edit
        srv, _ent = _seat(tmp_path)
        _pulse_edit(srv, {"ok": True, "eid": "c:argus"},
                    {"properties": {"say": "hello"}})
        ch = _changed_frame(srv._field_pulse)
        assert ch == {"type": "field.changed",
                      "changes": [{"eid": "c:argus", "kind": "edit",
                                   "brief": "say"}]}

    def test_a_refused_edit_does_not_pulse(self, tmp_path):
        from membrane.lanes import _pulse_edit
        srv, _ent = _seat(tmp_path)
        _pulse_edit(srv, {"ok": False, "error": "no match"},
                    {"properties": {"say": "hello"}})
        assert _changed_frame(srv._field_pulse) is None
