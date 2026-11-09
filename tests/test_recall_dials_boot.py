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
