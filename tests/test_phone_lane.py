"""THE PHONE LANE + LIVE HA STATE (2026-10-08, the transcript review, red first).

  * a phone call laned `build`: a misheard sentence could reach the shell
    executor, and "how's the battery?" cost 20+ executor shell calls
    hunting a token -- then a false "the BMS never publishes a SOC";
  * there was no read of live Home Assistant state at all: the field is
    MEMORY, not telemetry.

Pinned: agent 'phone' lanes 'phone' (reads, learn, intend, ha_state --
never exec, entity writes or closes); `ha_state` is every lane's read,
holding the token server-side; its search finds the BMS by words.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ha_state as H  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import server as S  # noqa: E402

STATES = [
    {"entity_id": "sensor.agatha_bms_battery", "state": "81",
     "attributes": {"unit_of_measurement": "%", "friendly_name": "Agatha BMS Battery"},
     "last_changed": "2026-10-08T18:00:00+00:00"},
    {"entity_id": "sensor.agatha_bms_current", "state": "-14.2",
     "attributes": {"unit_of_measurement": "A", "friendly_name": "Agatha BMS Current"},
     "last_changed": "2026-10-08T18:00:00+00:00"},
    {"entity_id": "light.salon", "state": "off", "attributes": {"friendly_name": "Salon"},
     "last_changed": "2026-10-08T12:00:00+00:00"},
]


class TestLane(unittest.TestCase):
    def test_phone_is_its_own_lane(self):
        self.assertEqual(S._lane_of("phone"), "phone")
        tools = S._lane_tools("phone")
        for t in ("ha_state", "entity_read", "query", "learn", "intend", "state"):
            self.assertIn(t, tools)
        for t in ("entity_link", "entity_add", "entity_edit", "entity_remove",
                  "fulfill", "field_watch", "entity_inherit", "peer_post"):
            self.assertNotIn(t, tools)
        r = S._lane_refusal("entity_link", "phone")
        self.assertIn("phone", r)
        self.assertNotIn("plan mode", r)

    def test_ha_state_is_everyones_read(self):
        for tools in (S.OPERATOR_TOOLS, S.LISTENER_TOOLS, S.PLAN_TOOLS):
            self.assertIn("ha_state", tools)
        self.assertIn("ha_state", M.TOOL_IMPLS)
        self.assertIn("ha_state", {t["name"] for t in M.TOOLS})

    def test_a_phone_turn_counts_as_a_person_speaking(self):
        self.assertNotEqual(S._lane_of("phone"), "listener")


class TestHaState(unittest.TestCase):
    def test_env_file(self):
        p = Path(tempfile.mkdtemp()) / "hass.env"
        p.write_text("HASS_URL=http://ha:8123\nexport HASS_TOKEN='abc'\n")
        env = H.load_env(str(p))
        self.assertEqual(env, {"url": "http://ha:8123", "token": "abc"})

    def test_search_by_words(self):
        rows = H.search(STATES, "bms battery")
        self.assertEqual(rows[0]["entity_id"], "sensor.agatha_bms_battery")
        self.assertEqual(H.search(STATES, "salon")[0]["entity_id"], "light.salon")
        self.assertEqual(H.search(STATES, "nothing like it"), [])

    def test_render_is_compact(self):
        line = H.render(STATES[0])
        self.assertIn("sensor.agatha_bms_battery", line)
        self.assertIn("81 %", line)
        self.assertIn("Agatha BMS Battery", line)

    def test_never_the_token(self):
        out = H.render_rows(H.search(STATES, "bms"))
        self.assertNotIn("abc", out)


if __name__ == "__main__":
    unittest.main()
