"""#980 rows 11,16,17: seed tables as data entities -- routing parity + perf."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane import lanes, seedpack, seedtables as ST  # noqa: E402
from membrane.entities import JUNK_ANCHOR_SEEDS  # noqa: E402


class _E:
    def __init__(self):
        self.docs = {}

    def upsert(self, q, embedder=None):
        self.docs[q.eid] = q


def _loaded():
    est = _E()
    seedpack.load(est, ST.build_pack())
    return est


def test_tables_from_entities_equal_the_builtin_tuples():
    est = _loaded()
    assert ST.intercept_seeds(est) == lanes._INTERCEPT_SEEDS
    assert ST.anti_seeds(est) == lanes._INTERCEPT_ANTI_SEEDS
    assert sorted(ST.junk_anchor_texts(est)) == sorted(JUNK_ANCHOR_SEEDS)
    assert ST.imprint_prompt(est) == ST.IMPRINT_PROMPT


def test_fallback_without_pack():
    assert ST.intercept_seeds(_E()) == lanes._INTERCEPT_SEEDS
    assert ST.anti_seeds(None) == lanes._INTERCEPT_ANTI_SEEDS


def test_routing_parity_on_fixed_samples():
    est = _loaded()
    samples = list(lanes._INTERCEPT_SEEDS) + list(lanes._INTERCEPT_ANTI_SEEDS) + [
        "forget the staging notes", "remember that the key rotates", "show memory stats"]
    old = [lanes._family_of(s) for s in samples]
    new_seeds = ST.intercept_seeds(est)
    assert new_seeds == lanes._INTERCEPT_SEEDS
    assert [lanes._family_of(s) for s in samples] == old
    assert [t for t, _ in lanes._seed_topics(type("S", (), {"entity_store": est})())] == new_seeds


def test_edit_is_picked_up_and_read_is_fast():
    est = _loaded()
    est.docs[ST.PREFIX + "intercept:0"].properties["summary"] = "custom seed"
    assert ST.intercept_seeds(est)[0] == "custom seed"
    t = time.perf_counter()
    for _ in range(200):
        ST.intercept_seeds(est)
    assert (time.perf_counter() - t) / 200 < 0.005
