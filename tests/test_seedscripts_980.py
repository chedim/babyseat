"""#980 rows 12-15: script texts ride the release seed pack; parity with the creators, perf, seat-edit overlay."""
import hashlib
import time

from membrane import autonode, chat_io, night, seedpack, seedscripts as S
from membrane.entities import EntityStore

# sha1 of the texts as shipped before the move (measured on origin/seed-pack-980)
def _h(s):
    return hashlib.sha1(s.encode()).hexdigest()


def _est():
    return EntityStore(path=None, shared=False)


def test_creators_use_the_pack_text():
    assert autonode.DEFAULT_FILETREE_WATCH_SCRIPT is S.FILETREE_WATCH_SCRIPT
    assert night.DEFAULT_CLOCK_TICK_SCRIPT is S.CLOCK_TICK_SCRIPT
    assert night._CONCEPT_DRIFT_GETTER is S.CONCEPT_DRIFT_GETTER
    assert chat_io._GETTER is S.CHAT_IO_GETTER
    for t in (S.FILETREE_WATCH_SCRIPT, S.CLOCK_TICK_SCRIPT, S.CONCEPT_DRIFT_GETTER, S.CHAT_IO_GETTER):
        compile(t, "<script>", "exec")


def test_pack_installs_scripts_on_entities():
    est = _est()
    assert S.install(est)["installed"] == 4
    d = est.docs
    assert d["membrane::tool:clock"].properties["tick_script"] == night.DEFAULT_CLOCK_TICK_SCRIPT
    assert d["membrane::tool:filetree"].properties["watch_script"] == autonode.DEFAULT_FILETREE_WATCH_SCRIPT
    assert d["membrane::tpl:concept-drift"].properties["getter_script"] == night._CONCEPT_DRIFT_GETTER
    assert d["membrane::tpl:chat-io"].properties["getter_script"] == chat_io._GETTER
    assert S.install(est) == {"installed": 0, "updated": 0, "overlaid": 0, "adopted": 0, "reinstalled": []}


def test_pack_refused_unless_release():
    import pytest
    with pytest.raises(seedpack.PackRefused):
        seedpack.load(_est(), S.pack(), release=False)


def test_seat_edit_survives_as_overlay_on_a_newer_release():
    est = _est()
    S.install(est)
    est.docs["membrane::tool:clock"].properties["tick_script"] = "result = {}  # seat edit"
    p = S.pack()
    p["version"] = S.VERSION + 1
    newer = S.CLOCK_TICK_SCRIPT + "\n# v+1"
    for e in p["entities"]:
        if e["eid"] == "membrane::tool:clock":
            e["properties"]["tick_script"] = newer
    out = seedpack.load(est, p, release=True)
    assert out["overlaid"] == 1
    assert est.docs["membrane::tool:clock"].properties["tick_script"] == newer
    assert seedpack.overlays(est, S.NAME)[0]["properties"]["tick_script"].endswith("seat edit")


def test_install_is_cheap():
    t = time.perf_counter()
    for _ in range(20):
        S.install(_est())
    assert (time.perf_counter() - t) / 20 < 0.05
