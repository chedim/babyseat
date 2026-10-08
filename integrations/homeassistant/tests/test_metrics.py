"""The brain's field metrics as hass sensors (2026-10-08, red first): pure
extraction from the seat's /membrane/state + the serve's /agent rows."""
import importlib.util
import unittest
from pathlib import Path

# metrics.py is pure: load it by path (the package __init__ needs HA)
_P = Path(__file__).resolve().parent.parent / "custom_components" / "membrane_ai" / "metrics.py"
_spec = importlib.util.spec_from_file_location("membrane_ai_metrics", _P)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
METRICS, metrics_from = _mod.METRICS, _mod.metrics_from

STATE = {
    "paused": False,
    "field_size": 5426,
    "efield": {
        "events": {"discourse/applied": 3500, "observation/applied": 3603,
                   "discourse/empty": 16, "observation/waiting": 17,
                   "discourse/pending": 1},
        "entities": {"live": 5426, "nursery": 2310},
        "geometry": {"eff_rank": 82.931, "rank_ratio": 0.162,
                     "free_energy": 0.162, "conflict_frac": 0.0123},
    },
    "audit_records": 2049, "n_verdicts": 3,
    "scripts": {"failing": [{}] * 20, "disabled": 1},
    "commons": {"peers_live": 0},
    "entity_tracks": [{}] * 6,
    "think": {"queue": [1, 2], "stats": {"processed": 17, "spoken": 1}},
    "retrieval": {"ctx_chars": 2400},
}
AGENTS = [
    {"name": "build", "mode": "primary"},
    {"name": "loop-1", "description": "A thinking-loop worker (processing watch: f:x); 4 event(s) queued."},
    {"name": "loop-2", "description": "A thinking-loop worker (idle); 4 event(s) queued."},
    {"name": "loop-3", "description": "A thinking-loop worker (processing); 4 event(s) queued."},
    {"name": "thinker", "description": "The seat's inner speech (night: the 30s night pulse): 17 beats"},
]


class TestMetrics(unittest.TestCase):
    def test_values(self):
        m = metrics_from(STATE, AGENTS)
        self.assertEqual(m["field_live"], 5426)
        self.assertEqual(m["field_nursery"], 2310)
        self.assertEqual(m["events_applied"], 7103)
        self.assertEqual(m["events_waiting"], 18)
        self.assertEqual(m["eff_rank"], 82.9)
        self.assertEqual(m["conflict_pct"], 1.23)
        self.assertEqual(m["audit_records"], 2049)
        self.assertEqual(m["verdicts"], 3)
        self.assertEqual(m["scripts_failing"], 20)
        self.assertEqual(m["thinker_beats"], 17)
        self.assertEqual(m["think_queue"], 2)
        self.assertEqual(m["tracks"], 6)
        self.assertEqual(m["loops_busy"], 2)
        self.assertEqual(m["events_queued"], 4)
        self.assertEqual(m["ambient"], "night")
        self.assertIs(m["paused"], False)

    def test_every_metric_has_a_spec_and_value_key(self):
        m = metrics_from(STATE, AGENTS)
        keys = {s["key"] for s in METRICS}
        self.assertEqual(keys, set(m))
        for s in METRICS:
            self.assertIn(s["platform"], ("sensor", "binary_sensor"))
            self.assertTrue(s["name"])

    def test_missing_parts_are_none_not_crash(self):
        m = metrics_from({}, [])
        self.assertIsNone(m["field_live"])
        self.assertIsNone(m["loops_busy"])
        self.assertIsNone(m["ambient"])
        m = metrics_from(None, None)
        self.assertIsNone(m["eff_rank"])


if __name__ == "__main__":
    unittest.main()
