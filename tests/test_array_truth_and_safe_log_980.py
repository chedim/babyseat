import io, sys
import numpy as np
from membrane import entities as E


def _mk():
    return E.QuantumEntity(eid="membrane::test:x", cls=E.CLASS_COMPONENT)


def test_numpy_position_does_not_raise():
    e = _mk()
    e.position = np.array([1.0, 2.0, 3.0])
    e.position_history = []
    doc = e.to_doc(embedder=None)
    assert doc["state_space"]["position"]["tracked"] == [1.0, 2.0, 3.0]


def test_safe_err_swallows_closed_stderr(monkeypatch):
    class Dead(io.StringIO):
        def write(self, s):
            raise ValueError("I/O operation on closed file")
    monkeypatch.setattr(sys, "stderr", Dead())
    E._safe_err("boom")  # must not raise


def test_writers_store_list_of_float_not_ndarray():
    """The real writers (snap load, journal replay) must hand readers a list[float]."""
    from membrane import journal
    arr = np.array([0.5, 0.25, 0.125], dtype=np.float32)
    st = E.EntityStore.__new__(E.EntityStore)
    got = st._snap_to_entity("e1", {}, "Other", [], [], position=arr, position_history=[])
    assert isinstance(got.position, list) and all(type(x) is float for x in got.position)
    assert bool(got.position)

    class S:
        docs = {"e1": got}
        def upsert(self, e, **kw): pass
    journal.apply(S(), {"path": "e1.position", "eid": "e1", "prop": "position", "new": arr})
    assert isinstance(got.position, list) and all(type(x) is float for x in got.position)


def test_as_position_helper():
    assert E.as_position(None) is None
    assert E.as_position([]) == []
    assert E.as_position(np.array([1, 2])) == [1.0, 2.0]
