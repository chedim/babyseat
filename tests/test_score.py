"""THE SCORE IS AN ENTITY (2026-10-11, the operator: the LLM's goal is to build
the field to substitute it for mundane things, without sacrificing quality --
"make the score a field entity and not put it into the banner")."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import score as SC  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = None
        self.membrane = None
        self.efield = None


def _srv():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = _Srv(est)
    est._srv = srv
    return srv, est


class TestScore(unittest.TestCase):
    def setUp(self):
        SC._pending.clear()
        SC._credits.clear()
        SC._last.clear()
        SC._counted.clear()

    def test_the_share_and_the_quality(self):
        srv, est = _srv()
        now = 1_800_000_000.0
        for _ in range(6):
            SC.note("reading", ts=now - 60)
        SC.note("tick", n=2, ts=now - 60)
        SC.note("round", lane="chat", n=2, ts=now - 60)
        SC.note("error", ts=now - 60)
        cur = SC.refresh(srv, now=now)
        self.assertEqual(cur["field_total"], 8)
        self.assertEqual(cur["rounds"], {"chat": 2})
        self.assertEqual(cur["share"], 0.8)
        self.assertAlmostEqual(cur["score"], round(0.8 * (1 - 1 / 9), 3))
        ent = est.docs[MEMBRANE_NS + "score"]
        self.assertIn("80% field", ent.properties["state"])

    def test_the_hours_survive_and_the_trend_compares_days(self):
        srv, est = _srv()
        now = 1_800_000_000.0
        SC.note("round", n=4, ts=now - 86400 - 600)            # yesterday: all model
        SC.note("reading", n=3, ts=now - 600)                  # today: the field took some
        SC.note("round", n=1, ts=now - 600)
        cur = SC.refresh(srv, now=now)
        self.assertEqual(cur["trend"], 0.75)
        again = SC.refresh(srv, now=now)                       # nothing new: the same picture, from the entity
        self.assertEqual(again["field_total"], 3)

    def test_old_hours_are_dropped(self):
        srv, est = _srv()
        now = 1_800_000_000.0
        SC.note("reading", ts=now - 3 * 86400)
        SC.refresh(srv, now=now)
        self.assertEqual(est.docs[MEMBRANE_NS + "score"].properties["hours"], {})

    def test_a_watch_read_counts_a_forced_read_does_not_and_a_new_getter_is_credited(self):
        srv, est = _srv()
        ent = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component",
                            properties={"name": "bat", "getter_script": "result = {'state': {'pct': 40}}"})
        est.docs[ent.eid] = ent
        WA.entity_read_back(srv, ent, force=True)              # the model asked: not the field's work
        self.assertNotIn("reading", sum((list(b) for b in SC._pending.values()), []))
        self.assertEqual(len(SC._credits), 1)                  # but the getter is new: credited
        ent.properties["getter_script"] = "result = {'state': {'pct': 39}}"
        WA._getter_last_ts.clear()
        WA.entity_read_back(srv, ent, by="field")              # a script of the field read it
        self.assertEqual(sum(b.get("reading", 0) for b in SC._pending.values()), 1)

    def test_an_entity_kept_live_counts_once_an_hour(self):
        SC._last.clear(); SC._counted.clear()
        t = 1_800_000_000.0
        self.assertTrue(SC.note_changed("tick", "e1", {"render": "40%"}, ts=t))
        self.assertFalse(SC.note_changed("tick", "e1", {"render": "39%"}, ts=t + 60))     # same hour: busy, not more work
        self.assertTrue(SC.note_changed("tick", "e2", {"render": "on"}, ts=t + 60))       # another entity: counts
        self.assertFalse(SC.note_changed("tick", "e1", {"render": "39%"}, ts=t + 3600))   # unchanged: never
        self.assertTrue(SC.note_changed("tick", "e1", {"render": "38%"}, ts=t + 3600))    # next hour, moved: counts
        self.assertEqual(sum(b.get("tick", 0) for b in SC._pending.values()), 3)

    def test_growth_never_adds_and_a_collapse_takes_away(self):
        self.assertEqual(SC.growth_factor({"eff_rank": 90}, {"eff_rank": 60}), 1.0)
        self.assertEqual(SC.growth_factor({"eff_rank": 45}, {"eff_rank": 60}), 0.75)
        self.assertEqual(SC.growth_factor({"eff_rank": 5}, {"eff_rank": 60}), SC.GROWTH_FLOOR)
        self.assertEqual(SC.growth_factor({"eff_rank": 5}, None), 1.0)

    def test_the_day_snapshot_and_the_degrading_word(self):
        srv, est = _srv()
        now = 1_800_000_000.0
        geo = {"v": {"eff_rank": 60.0, "rank_ratio": 0.12, "free_energy": 0.1, "conflict_frac": 0.0}}
        class _F:
            def geometry(self):
                return geo["v"]
        srv.efield = _F()
        SC.note("round", ts=now - 86400 - 60); SC.note("tick", ts=now - 86400 - 60)
        SC.refresh(srv, now=now - 86400)                      # yesterday's picture
        geo["v"] = {"eff_rank": 30.0, "rank_ratio": 0.06, "free_energy": 0.05, "conflict_frac": 0.0}
        SC.note("round", ts=now - 60); SC.note("tick", ts=now - 60)
        cur = SC.refresh(srv, now=now)
        self.assertEqual(cur["growth_factor"], 0.5)
        self.assertEqual(cur["score"], 0.25)                  # share 0.5 x growth 0.5
        self.assertEqual(cur["growth_trend"]["eff_rank"], -30.0)
        self.assertIn("DEGRADING", est.docs[MEMBRANE_NS + "score"].properties["state"])

    def test_the_doctrine_states_the_purpose_and_names_the_entity(self):
        d = (Path(__file__).resolve().parent.parent / "doctrine.md").read_text()
        self.assertIn("make yourself less needed", d)
        self.assertIn("field entity `score`", d)


if __name__ == "__main__":
    unittest.main()
