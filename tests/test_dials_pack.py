"""#980 rows 26-27,39: the dials ride the seed pack; module constants equal the old literals."""
from membrane import attention, claims, consequences, dials, toybox
from membrane.entities import MEMBRANE_NS


def test_constants_parity():
    assert consequences.HORIZONS == (3600.0, 86400.0, 3 * 86400.0)
    assert consequences.SNAP_KEYS == ("state", "value", "status", "online", "enabled", "fulfilled_ts", "blocked_until")
    assert "exec" in consequences.WRITE_TOOLS and consequences.WRITE_TOOLS[-1] == "fulfill"
    assert attention.ALERT_EXEMPT == ("intent-planted", "intent-landed")
    assert claims.ACTION_WINDOW == (-2700, 300) and claims.RETRIEVAL_WINDOW == (-600, 120)
    assert claims.TESTS_WINDOW == 3600
    assert set(toybox.KINDS) >= {"fast", "tower", "ball", "mirror"} and toybox.MIRROR_LAG == toybox.KINDS["mirror"]["params"]["lag_s"]
    assert toybox.ACTIONS["tower"] == ("knock", "rebuild")


def test_pack_installs_and_overlay():
    class Est:
        docs = {}

        def upsert(self, e, embedder=None):
            self.docs[e.eid] = e
    est = Est()
    assert dials.install(est)["installed"] == 4
    assert dials.install(est)["installed"] == 0
    eid = MEMBRANE_NS + "dials:claims"
    est.docs[eid].properties["TESTS_WINDOW"] = 7200
    dials.sync(est)
    try:
        assert dials.get("claims", "TESTS_WINDOW") == 7200
    finally:
        dials._LIVE.clear()
    assert dials.get("claims", "TESTS_WINDOW") == 3600
