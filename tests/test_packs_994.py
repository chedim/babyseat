"""#994 row 7: the native seed-pack JSON files declare the same entities the Python packs ship (names aside)."""
import json
import pathlib

from membrane import recall_policy

PACKS = pathlib.Path(__file__).resolve().parent.parent / "native" / "packs"


def _load(name):
    return json.loads((PACKS / (name + ".json")).read_text())


def test_recall_pack_json_matches_python_pack():
    a, b = _load("recall-policy"), recall_policy._pack()
    assert (a["name"], a["version"]) == (b["name"], b["version"])
    assert a["entities"][0]["properties"] == b["entities"][0]["properties"]
