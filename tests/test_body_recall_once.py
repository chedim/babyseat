"""Blurp's body reads the entity nearest the current with a field recall; an
unchanged current must cost no recall (the seat's exchange comes every ~5 s,
and a full field search there queues behind the slow injections) -- #731 review."""
from types import SimpleNamespace

from membrane import ch_blurp as B


class _Fld:
    def __init__(self, cur):
        self.cur, self.recalls = cur, 0

    def current(self):
        return self.cur

    def current_gen(self):
        return hash(tuple(self.cur))

    def recall(self, v, k=1, by="state"):
        self.recalls += 1
        return [("membrane::c:near", 1.0)]


def test_unchanged_current_costs_no_recall():
    srv, fld = SimpleNamespace(), _Fld([1.0, 0.0, 0.0])
    B._read_body(srv, fld, [0.0, 1.0, 0.0])
    B._read_body(srv, fld, [0.0, 0.9, 0.1])            # the body moved, the current did not
    B._read_body(srv, fld, [0.0, 0.8, 0.2])
    assert fld.recalls == 1


def test_a_moved_current_recalls_again():
    srv, fld = SimpleNamespace(), _Fld([1.0, 0.0, 0.0])
    B._read_body(srv, fld, [0.0, 1.0, 0.0])
    fld.cur = [0.9, 0.1, 0.0]
    B._read_body(srv, fld, [0.0, 1.0, 0.0])
    assert fld.recalls == 2


# ---- #731 re-review: the real current drifts in its last bits; only its inputs say it moved ----

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from membrane import ch_body as BODY  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


@pytest.fixture
def clock(monkeypatch):
    t = [T0]
    monkeypatch.setattr(EF.time, "time", lambda: t[0])
    return t


def _field():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "t@host")
    f.recalls = 0
    def recall(v, k=1, by="state"):
        f.recalls += 1
        return [("membrane::c:near", 1.0)]
    f.recall = recall
    return f


def test_no_pull_between_two_readings_writes_no_ticked_and_costs_no_recall(clock):
    f, srv = _field(), SimpleNamespace()
    f.pull_current("a", [1.0, 0.3, 0.2], now=T0)
    f.pull_things("a", [0.2, 1.0, 0.1], now=T0)
    at = [0.0, 0.0, 1.0]
    told = []
    for dt in (5, 10, 15, 20):                         # the seat's exchange, every ~5 s, nobody speaks
        clock[0] = T0 + dt
        told.append(B._read_body(srv, f, at))
    assert f.recalls == 1
    assert told[1:] == [None, None, None]              # the world did not move: no ticked


def test_a_new_pull_moves_the_current(clock):
    f, srv = _field(), SimpleNamespace()
    f.pull_current("a", [1.0, 0.0, 0.0], now=T0)
    clock[0] = T0 + 5
    B._read_body(srv, f, [0.0, 1.0, 0.0])
    f.pull_current("a", [0.0, 1.0, 0.0], now=T0 + 6)
    clock[0] = T0 + 10
    r = B._read_body(srv, f, [0.0, 1.0, 0.0])
    assert f.recalls == 2
    assert r["by"] == ["world"]


def test_a_session_dropping_out_moves_the_current(clock):
    f = _field()
    f.pull_current("a", [1.0, 0.0, 0.0], now=T0)
    f.pull_current("b", [0.0, 1.0, 0.0], weight=1000.0, now=T0)
    g = f.current_gen(now=T0 + 1)
    assert f.current_gen(now=T0 + 2) == g             # time alone moves nothing
    assert f.current_gen(now=T0 + 2000) != g          # a fell under CURRENT_MIN_PULL, b still pulls


def test_body_read_keys_the_world_on_the_generation():
    srv = SimpleNamespace()
    BODY.read(srv, [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], "w:a", gen=3)
    assert BODY.read(srv, [1.0, 0.0, 0.0], [0.0, 1.0, 1e-17], "w:a", gen=3) is None
    assert BODY.read(srv, [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], "w:a", gen=4)["by"] == ["world"]
