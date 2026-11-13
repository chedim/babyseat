"""#980 Q3: the versioned seed pack -- release wins, the seat edit is kept as an overlay."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import seedpack as SP  # noqa: E402
from membrane import graphbanner as GB  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

E = "membrane::t:1"


def _est():
    return EntityStore(path=None, shared=False)


def _pack(v, show="a"):
    return {"name": "t", "version": v, "entities": [
        {"eid": E, "cls": "Other", "properties": {"show": show, "order": 1}}]}


def _props(est, eid=E):
    return dict(est.docs[eid].properties)


def test_first_load_installs_and_records_version():
    est = _est()
    r = SP.load(est, _pack(1))
    assert r["installed"] == 1 and _props(est)["show"] == "a"
    assert SP.installed_version(est, "t") == 1


def test_same_version_is_a_noop():
    est = _est()
    SP.load(est, _pack(1))
    assert SP.load(est, _pack(1)) == {"installed": 0, "updated": 0, "overlaid": 0, "adopted": 0, "reinstalled": []}


def test_unedited_entity_takes_the_new_release_silently():
    est = _est()
    SP.load(est, _pack(1))
    r = SP.load(est, _pack(2, "b"))
    assert r["updated"] == 1 and r["overlaid"] == 0
    assert _props(est)["show"] == "b" and SP.overlays(est, "t") == []


def test_edited_entity_release_wins_and_edit_is_kept_as_overlay():
    est = _est()
    SP.load(est, _pack(1))
    est.docs[E].properties["show"] = "seat"
    r = SP.load(est, _pack(2, "b"))
    assert r["overlaid"] == 1 and _props(est)["show"] == "b"
    ov = SP.overlays(est, "t")
    assert len(ov) == 1 and ov[0]["eid"] == E
    assert ov[0]["properties"]["show"] == "seat" and ov[0]["status"] == "pending"


def test_reapply_puts_the_seat_edit_back_and_discard_drops_it():
    est = _est()
    SP.load(est, _pack(1))
    est.docs[E].properties["show"] = "seat"
    SP.load(est, _pack(2, "b"))
    assert SP.reapply(est, "t", E)["ok"]
    assert _props(est)["show"] == "seat"
    assert SP.overlays(est, "t")[0]["status"] == "applied"
    est.docs[E].properties["show"] = "seat2"
    SP.load(est, _pack(3, "c"))
    assert _props(est)["show"] == "c"
    assert SP.discard(est, "t", E)["ok"]
    assert _props(est)["show"] == "c"
    assert not [o for o in SP.overlays(est, "t") if o["status"] == "pending"]


def test_edit_equal_to_release_makes_no_overlay():
    est = _est()
    SP.load(est, _pack(1))
    est.docs[E].properties["show"] = "b"
    assert SP.load(est, _pack(2, "b"))["overlaid"] == 0


def test_scripts_in_a_pack_are_refused_unless_packaged():
    est = _est()
    bad = _pack(1)
    bad["entities"][0]["properties"]["tick_script"] = "result={}"
    try:
        SP.load(est, bad)
    except SP.PackRefused:
        pass
    else:
        raise AssertionError("script-carrying pack from a non-release source loaded")
    assert E not in est.docs
    SP.load(est, bad, release=True)
    assert "tick_script" in _props(est)


def test_peer_import_never_carries_scripts():
    props = {"show": "x", "tick_script": "r", "getter_script": "r", "watch_script": "r"}
    assert SP.strip_scripts(props) == {"show": "x"}


def test_banner_rules_ship_as_a_pack_and_an_edit_is_not_stranded():
    est = _est()
    assert GB.seed(est) == len(GB.SEED)
    eid = GB._rule_eid("open decisions")
    est.docs[eid].properties["show"] = "edited"
    GB.seed(est)
    assert est.docs[eid].properties["show"] == "edited"
    pk = GB.pack()
    pk["version"] += 1
    for e in pk["entities"]:
        if e["eid"] == eid:
            e["properties"]["show"] = "released v2"
    SP.load(est, pk, release=True)
    assert est.docs[eid].properties["show"] == "released v2"
    assert SP.overlays(est, "banner")[0]["properties"]["show"] == "edited"


def test_first_adoption_keeps_seat_edit_and_missing_entity_comes_back(capsys):
    est = _est()
    GB.seed(est)
    eid = GB._rule_eid("open decisions")
    est.docs[eid].properties["show"] = "edited"
    est.docs[SP._led_eid("banner")].properties["base"] = {}
    GB.seed(est)
    assert est.docs[eid].properties["show"] == "edited"
    del est.docs[eid]
    GB.seed(est)
    assert eid in est.docs and eid in capsys.readouterr().out


def test_code_change_without_version_bump_reaches_seat_and_corrupt_version_is_survived():
    est = _est()
    GB.seed(est)
    eid = GB._rule_eid("open decisions")
    pk = GB.pack()
    for e in pk["entities"]:
        if e["eid"] == eid:
            e["properties"]["show"] = "changed in code"
    est.docs[SP._led_eid("banner")].properties["version"] = "junk"
    SP.load(est, pk, release=True)
    assert est.docs[eid].properties["show"] == "changed in code"
