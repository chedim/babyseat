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
