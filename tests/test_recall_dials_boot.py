"""The recall dials are installed at boot, so a seat can tune recall (Memo, 2026-11-09: reading
`membrane::recall:dials` on the seat returned "no entity"; recall ran on the coded defaults)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import recall_policy as RP  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _est():
    return EntityStore(path=None, shared=False)


def test_boot_seed_installs_the_dials_and_points_recall_at_them():
    est = _est()
    try:
        RP.seed(est)
        assert RP.DIALS_EID in est.docs
        assert RP.dials()["banner_n"] == est.docs[RP.DIALS_EID].properties["banner_n"]
        # a seat's edit is what recall reads next
        e = est.docs[RP.DIALS_EID]
        e.properties = {**e.properties, "banner_n": 7}
        est.upsert(e)
        assert RP.dials()["banner_n"] == 7
        # a second boot keeps the edit
        RP.seed(est)
        assert RP.dials()["banner_n"] == 7
    finally:
        RP.reset()


def _edit(est, **kw):
    e = est.docs[RP.DIALS_EID]
    e.properties = {**e.properties, **kw}
    est.upsert(e)


def test_a_bad_edit_is_ignored_and_the_good_keys_still_apply():
    """Memo's review of #1024: one bad edit must not break recall on every turn."""
    est = _est()
    try:
        RP.seed(est)
        d0 = RP._defaults()
        _edit(est, banner_n="7", pool=-3, dedupe_jaccard=1.5, rerank="yes", lane_rules={"vector_lanes": "ask"},
              weights={"default": {"bias": "big"}}, rrf_k=30, header="H:")
        e = est.docs[RP.DIALS_EID]
        e.properties = {k: v for k, v in e.properties.items() if k != "recent_turns"}   # a dropped key
        est.upsert(e)
        d = RP.dials()
        for k in ("banner_n", "pool", "dedupe_jaccard", "rerank", "lane_rules", "weights", "recent_turns"):
            assert d[k] == d0[k], k
        assert d["rrf_k"] == 30 and d["header"] == "H:"          # the good edits apply
        assert RP.lanes(True, True)["ask"] is True               # recall still runs
    finally:
        RP.reset()


def test_a_float_where_an_int_is_wanted_falls_back_and_ints_pass_for_floats():
    est = _est()
    try:
        RP.seed(est)
        _edit(est, banner_n=7.5, no_topic_min=1, fact_chars=150)
        d = RP.dials()
        assert d["banner_n"] == RP._defaults()["banner_n"]
        assert d["no_topic_min"] == 1 and d["fact_chars"] == 150
    finally:
        RP.reset()


def test_a_new_release_keeps_the_seats_edits_on_keys_it_did_not_change(monkeypatch):
    """Memo's question: a new pack version writes only the keys whose packaged value changed; the seat's edit on
    such a key is parked as an overlay, every other edit stays."""
    est = _est()
    try:
        RP.seed(est)
        _edit(est, banner_n=7, pool=40)
        v1 = RP._pack
        def v2():
            p = v1()
            p["version"] = 2
            p["entities"][0]["properties"] = {**p["entities"][0]["properties"], "pool": 60}
            return p
        monkeypatch.setattr(RP, "_pack", v2)
        RP.seed(est)
        d = RP.dials()
        assert d["banner_n"] == 7            # untouched by the release: the seat's edit stays
        assert d["pool"] == 60               # the release changed it: the release wins (the edit is parked)
    finally:
        RP.reset()
