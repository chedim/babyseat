"""#980 R-rows (recall): the recall policy is field data + methods; native code calls it and falls back.
Parity: the field path equals the old native path on the recorded queries; perf: no real slowdown."""
import copy
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from membrane import entity_block as EB  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402
from membrane import recall_policy as RP  # noqa: E402
from membrane import recall_rerank as RR  # noqa: E402
from membrane import seedpack as SP  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

QUERIES = json.loads((Path(__file__).parent / "data" / "recall_queries_980.json").read_text())


@pytest.fixture(autouse=True)
def _no_reranker(monkeypatch):
    # a deterministic stand-in for the bge runtime: a pure function of the texts
    monkeypatch.setattr(RR, "scores", lambda q, docs: [((len(d) * 7 + len(q)) % 13) / 13.0 for d in docs])
    RP.reset()
    yield
    RP.reset()


def test_recorded_queries_exist():
    assert len(QUERIES) >= 12 and any("listener" in r for q in QUERIES for r in q["rows"])


def test_pack_installs_dials_with_the_native_values():
    est = EntityStore()
    SP.load(est, RP.PACK, release=False)
    p = est.docs[RP.DIALS_EID].properties
    assert p["version"] == 1 and p["banner_n"] == RB.BANNER_N and p["pool"] == RB.POOL
    assert p["rrf_k"] == EB.RRF_K and p["rerank_top"] == RR.RERANK_TOP
    assert p["weights"]["default"] == RB.DEFAULT_WEIGHTS and p["weights"]["rerank"] == RB.RERANK_WEIGHTS
    assert SP.installed_version(est, RP.PACK["name"]) == 1


def test_pack_carries_no_script():
    SP.load(EntityStore(), RP.PACK, release=False)      # raises PackRefused on a script


@pytest.mark.parametrize("rerank", [False, True])
def test_rank_parity_on_recorded_queries(rerank):
    for q in QUERIES:
        old = RB._rank_native(copy.deepcopy(q["rows"]), q["text"], None, rerank)
        new = RB.rank(copy.deepcopy(q["rows"]), q["text"], None, rerank)
        assert new == old, q["text"]


def test_pick_parity_on_recorded_queries():
    for q in QUERIES:
        ranked = RB._rank_native(copy.deepcopy(q["rows"]), q["text"], None, True)
        old = RB._pick_native(ranked, q["text"], skip=q["skip"], ranked=ranked)
        assert RB.pick(ranked, q["text"], skip=q["skip"], ranked=ranked) == old, q["text"]


def test_render_parity():
    for q in QUERIES:
        lines = ["▸ #1 (1d, reply) " + q["text"]]
        assert RB.render(lines) == RB._render_native(lines) and RB.render([]) is None


def test_fuse_parity_on_recorded_lists():
    for q in QUERIES:
        a, b = q["rows"][::2], q["rows"][1::2]
        key = lambda r: tuple(r["a_n"])                  # noqa: E731
        assert RP.fuse([a, b], key) == EB._rrf_native([a, b], key)


def test_lane_choice_follows_the_rules():
    assert RP.lanes(question=True, has_vec=True) == {"ask": True, "direct": True, "tool": True, "word": True, "entity": True}
    assert RP.lanes(question=False, has_vec=True)["tool"] is False
    assert RP.lanes(question=True, has_vec=False) == {"ask": False, "direct": False, "tool": False, "word": True, "entity": True}


def test_dials_are_data_a_seat_edit_changes_the_policy():
    est = EntityStore()
    SP.load(est, RP.PACK, release=False)
    est.docs[RP.DIALS_EID].properties["banner_n"] = 2
    RP.install(est)
    q = QUERIES[0]
    ranked = RB._rank_native(copy.deepcopy(q["rows"]), q["text"], None, False)
    assert len(RB.pick(ranked, q["text"], ranked=ranked, n=None)) <= 2


def test_native_falls_back_when_the_field_method_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("field down")
    monkeypatch.setattr(RP, "rank_policy", boom)
    monkeypatch.setattr(RP, "select", boom)
    monkeypatch.setattr(RP, "fuse", boom)
    q = QUERIES[1]
    assert RB.rank(copy.deepcopy(q["rows"]), q["text"], None, False) == RB._rank_native(copy.deepcopy(q["rows"]), q["text"], None, False)
    r = q["rows"]
    assert EB.rrf([r], lambda x: tuple(x["a_n"])) == EB._rrf_native([r], lambda x: tuple(x["a_n"]))


def test_methods_are_declared_for_the_class():
    assert {"lanes", "fuse", "rank_policy", "select", "render"} <= set(RP.METHODS)


def test_perf_field_path_is_not_slower_than_1_5x():
    rows = [(q["text"], q["rows"]) for q in QUERIES]

    def run(fn):
        best = 1e9
        for _ in range(7):
            t = time.perf_counter()
            for text, r in rows:
                fn(copy.deepcopy(r), text, None, False)
            best = min(best, time.perf_counter() - t)
        return best
    old, new = run(RB._rank_native), run(RB.rank)
    assert new <= old * 1.5 + 0.002, (old, new)
