import io, sys
import numpy as np
from membrane import entities as E


def _mk():
    import dataclasses
    kw = {}
    for f in dataclasses.fields(E.QuantumEntity):
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            kw[f.name] = "x"
    return E.QuantumEntity(**kw)


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
