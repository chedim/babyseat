"""#721: which writers reach Blurp's learner with no chain (a count per writer
kind on its card), and a getter the field fires during a turn writes in that
turn's chain."""
import os
import sys
import threading
import types
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch_learn as L  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import writes as W  # noqa: E402


def _learner():
    lr = L.Learner.__new__(L.Learner)
    import collections
    lr.counts = collections.Counter()
    return lr


def _w(writer, path="membrane::toy:a.x", chain=None):
    eid, _, prop = path.rpartition(".")
    return {"writer": writer, "path": path, "eid": eid, "prop": prop, "new": 1, "chain": chain}


def test_no_chain_counts_per_writer_kind():
    lr = _learner()
    for w in (_w("field"), _w("field"), _w("script:membrane::toy:a"), _w("script:membrane::toy:b"), _w("llm")):
        lr.observe(w)
    assert lr.counts["pass:no_chain"] == 5
    assert lr.counts["no_chain:field"] == 2
    assert lr.counts["no_chain:script"] == 2          # the kind, not one key per entity
    assert lr.counts["no_chain:llm"] == 1


def test_own_writes_and_chained_writes_are_not_in_the_breakdown():
    lr = _learner()
    lr.observe(_w("trigger:membrane::x"))
    assert not [k for k in lr.counts if k.startswith("no_chain:")]


def test_field_read_runs_the_getter_in_the_reading_turns_chain():
    seen = {}
    done = threading.Event()

    def read_back(srv, ent, by="model"):
        seen["chain"] = (W.current() or {}).get("id")
        seen["writer"] = W.writer()
        done.set()
    ent = types.SimpleNamespace(eid="membrane::toy:g", properties={})
    srv = types.SimpleNamespace(entity_store=object())
    with mock.patch.object(N, "effective_script", return_value=("result = {}", None)), \
            mock.patch("membrane.watch_agents.entity_read_back", side_effect=read_back):
        with W.turn("s1"):
            want = W.current()["id"]
            assert N.field_read(srv, ent)
        assert done.wait(2)
    assert seen["chain"] == want
