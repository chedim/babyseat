"""THE SPEECH FOOTER + THE DELTA-RENDER CONTRACT pins: membrane's
voice ([membrane-says]) rides the BOTTOM edge of the transcript (a
system message appended AFTER the last turn by rag._inject_after),
never as the tail of the context block above the user's message; the
watchers' geometry deltas are engine-built (autonode._delta_apply),
honored from the entity's delta_render prop as well as the script's
result['delta'] flag, and an empty diff never erases a script's own
render."""
from membrane.rag import _inject, _inject_after


def test_footer_appends_after_the_last_turn():
    msgs = [{"role": "system", "content": "doctrine"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"}]
    out = _inject_after(msgs, "[membrane-says] the field hums")
    assert out[-1]["role"] == "system"
    assert out[-1]["content"].startswith("[membrane-says]")
    assert out[-2] == msgs[-1]
    assert msgs[-1]["content"] == "again"


def test_the_old_ctx_tail_path_is_gone():
    src = open("membrane/opencode.py").read()
    assert "ctx + " + repr("\n\n") + " + speak_block" not in src
    assert src.count("_inject_after(_msgs, speak_block)") == 1


def test_delta_render_contract_present():
    import membrane.autonode as an
    snap = an._geometry_snap(type("E", (), {
        "position": [0.1, 0.2, 0.3], "properties": {"status": "armed"},
        "ring_velocity": lambda self, t: 0.5,
        "position_history": [1, 2]})())
    assert snap["status"] == "armed" and snap["dim"] == 3
    d = an._delta_string({}, snap)
    assert "dim=3" in d and "status=armed" in d
    assert an._delta_string(snap, snap) == ""


def test_empty_diff_keeps_the_script_render():
    """THE TWO-PHASE FIRE: the first _delta_apply sees everything as
    new (the calibration fire -- its stash does not exist yet), so the
    pin emulates the apply round-trip (patch -> props) before the
    second call finds nothing moved and keeps the script voice."""
    import membrane.autonode as an
    ent = type("E", (), {"properties": {}, "position": [0, 0, 0],
                         "ring_velocity": lambda self, t: 0.0,
                         "position_history": []})()
    trig = type("T", (), {"kind": "file",
                          "payload": {"path": "app.py"}})()
    first = {"render": "filetree: created app.py"}
    an._delta_apply(None, ent, first, trig)
    assert first["render"].startswith("delta: dim=")
    ent.properties = dict((ent.properties or {}),
                          _delta_prev=an._geometry_snap(ent))
    second = {"render": "filetree: changed app.py"}
    an._delta_apply(None, ent, second, trig)
    assert second["render"] == "filetree: changed app.py"
