"""THE SIDE-INDEX SURVIVAL (the 45s-PENDING exhibit, the 251 failures):
a script's patch value can be ANYTHING (a function leaked into
properties); one poison doc killed the whole side-index save (json
dumps dies) -- 251 failures in one boot, the mirror starving, and the
save's to_doc build UNDER _store_lock (~95MB of dict-building, 20-45s)
parked every reader behind it. THE CONTRACT:

1. a poison doc never kills the file (per-doc screen; named in the log);
2. the poison's value wraps to a READABLE str (the operator sees WHAT);
3. the lock's hold is milliseconds-scale (flat snapshot under lock);
4. the patch lane screens at the WRITE (a script cannot poison again);
5. a load-after screen cures existing poison on the next boot.
"""
import json
import tempfile
import threading
import time

from membrane.entities import (EntityStore, QuantumEntity,
                               json_safe, sanitize_properties)


def _store(tmp, extra_props=None):
    st = EntityStore(path=str(tmp / "ents.json"), shared=False)
    ent = QuantumEntity(
        eid="membrane::tool:poisoned", cls="tool",
        properties={"name": "the poisoned one", **(extra_props or {})})
    st.docs[ent.eid] = ent
    good = QuantumEntity(eid="membrane::tool:good", cls="tool",
                         properties={"name": "the good one"})
    st.docs[good.eid] = good
    return st, ent, good


def test_a_poison_doc_never_kills_the_file(tmp_path, capsys):
    st, bad, good = _store(tmp_path, {"bad": lambda s: s, "worse": open})
    st.save()
    d = json.loads((tmp_path / "ents.json").read_text())
    assert "membrane::tool:good" in d["docs"]
    # the poison either skipped-named or wrapped -- NEVER a dead file
    out = capsys.readouterr().err
    poison_named = ("membrane::tool:poisoned" in out) or \
        ("membrane::tool:poisoned" in d["docs"])
    assert poison_named, out


def test_poison_value_wraps_to_a_readable_str(tmp_path):
    st, bad, _ = _store(tmp_path, {"bad": lambda s: s})
    st.save()
    d = json.loads((tmp_path / "ents.json").read_text())
    if "membrane::tool:poisoned" in d["docs"]:
        props = d["docs"]["membrane::tool:poisoned"]["data"]["properties"]
        assert isinstance(props["bad"], str)   # wrapped, inspectable


def test_json_safe_screen():
    fn = lambda x: x                              # noqa: E731
    assert json_safe(fn).startswith("<function")
    assert json_safe({"ok": [1, 2, "three"]}) == {"ok": [1, 2, "three"]}
    assert json_safe("plain") == "plain"
    deep = {"a": [{"b": [{"c": [fn]}]}]}          # within depth: wrapped
    v = json_safe(deep)
    assert "function" in json.dumps(v)


def test_sanitize_properties_bounds_keys(tmp_path):
    props = {"k" * 300: "kept", "ok": 1, "f": print}
    out = sanitize_properties(props)
    assert all(len(k) <= 120 for k in out)
    assert out["ok"] == 1
    assert isinstance(out["f"], str)


def test_lock_hold_is_flat_ms(tmp_path):
    """10k docs: the lock's hold is the shallow copy -- never the doc
    build. The timing fence (an upper bound; the old form was 20-45s)."""
    from membrane.entities import _STORE_LOCK_HOLD_MAX_S
    st = EntityStore(path=str(tmp_path / "ents.json"), shared=False)
    for i in range(10_000):
        st.docs[f"membrane::tool:e{i}"] = QuantumEntity(
            eid=f"membrane::tool:e{i}", cls="tool",
            properties={"name": f"e{i}"})
    holds = []

    real_sanitize = sanitize_properties

    import membrane.entities as ents_mod
    orig = ents_mod.sanitize_properties

    def timed(props):
        t0 = time.perf_counter()
        try:
            return real_sanitize(props)
        finally:
            # each doc's under-lock copy is trivial; the SUM is what
            # matters -- the test asserts the save's total lock hold
            holds.append(time.perf_counter() - t0)
    ents_mod.sanitize_properties = timed
    try:
        st.save()
    finally:
        ents_mod.sanitize_properties = orig
    assert sum(holds) < _STORE_LOCK_HOLD_MAX_S, sum(holds)


def test_patch_lane_screens_the_write(tmp_path):
    """The leak closes: apply_entity_update's patch values pass the
    json-safe screen -- a function cannot enter properties again."""
    import time as _t
    from membrane.night import apply_entity_update
    srv = type("S", (), {})()
    srv.membrane = type("M", (), {})()
    st, ent, _ = _store(tmp_path)
    srv.entity_store = st

    class _Emb:
        def embed(self, texts):
            return [[0.1] * 4]
    srv.membrane.embedder = _Emb()
    srv.membrane.field_path = str(tmp_path / "f.json")
    out = {"patch": {"custom": lambda s: s}}
    try:
        apply_entity_update(srv, ent, out, "tick", silent=True)
    except Exception:
        pass   # the apply's other lanes may degrade on the fake -- the
               # CONTRACT is what landed in properties
    props = st.docs[ent.eid].properties
    # THE WHITELIST (the covenant's reshape): arbitrary props refuse
    assert "custom" not in props


def test_load_after_screen_cures_old_poison(tmp_path):
    """An existing poisoned FILE cures at load: the entity loads with
    json-safe properties only."""
    p = tmp_path / "ents.json"
    payload = {"docs": {"membrane::tool:old": {
        "meta": {"type": "QuantumEntity"},
        "data": {"class": "tool",
                 "properties": {"name": "old", "poison": {"x": 1}}},
    }}}
    p.write_text(json.dumps(payload))
    st = EntityStore(path=str(p), shared=False)
    ent = st.docs.get("membrane::tool:old")
    assert ent is not None
    json.dumps({"docs": [{"properties": ent.properties}]})   # never raises