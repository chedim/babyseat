# THE LIVE NAME (issue #6): rename and the opt-in CTA reads the agreed name
# -- a fresh page from /ui/conf, an OPEN page from the hub. The lost seat
# renamed itself and its pages still asked "let Argus act as you here":
# the panel fetched /ui/conf and never read the name out of it, the SEAT
# binding was a const with a hard-coded nickname fallback, and the
# adoption published nothing for open pages to hear.
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from membrane import naming as N
from membrane.entities import CLASS_CONFIG, MEMBRANE_NS, QuantumEntity

PANEL = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js"


class _Hub:
    def __init__(self):
        self.events = []

    def publish(self, ev):
        self.events.append(ev)


def _srv(hub):
    return types.SimpleNamespace(_loop_runner=types.SimpleNamespace(hub=hub))


class TestANameAnnouncesItself(unittest.TestCase):
    """The adoption reaches the wires it promised to: /ui/conf already
    serves it (it did); the hub event goes out with it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": self.tmp.name})
        self.env.start()
        self.vocab = mock.patch.object(
            N, "vosk_words", return_value={"juniper", "nova", "wren", "membrane", "bob"})
        self.vocab.start()

    def tearDown(self):
        self.vocab.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _ent(self, **pp):
        return QuantumEntity(eid=MEMBRANE_NS + "name", cls=CLASS_CONFIG, properties=pp)

    def test_adoption_publishes_the_name_event(self):
        hub = _Hub()
        out = N.on_edit(_srv(hub), self._ent(name="juniper", agreed_with="Dmitrii"))
        self.assertTrue(out["adopted"])
        self.assertIn({"type": "membrane.name", "name": "Juniper"}, hub.events)

    def test_a_refused_adoption_announces_nothing(self):
        hub = _Hub()
        ent = self._ent(name="Kestrel", agreed_with="Dmitrii")
        self.assertFalse(N.on_edit(_srv(hub), ent)["adopted"])
        self.assertEqual(hub.events, [])

    def test_no_runner_no_event_no_crash(self):
        self.assertTrue(N.on_edit(None, self._ent(name="juniper",
                                                  agreed_with="Dmitrii"))["adopted"])


class TestThePageHearsIt(unittest.TestCase):
    """The page's wiring (plain JS, pinned at the source): the conf fetch
    applies the name, the hub event applies it live, and the SEAT binding
    can change -- no const with a nickname that a rename makes stale."""

    def test_the_conf_fetch_applies_the_name(self):
        src = PANEL.read_text()
        self.assertIn("this._applySeatName(conf.data.name)", src)

    def test_the_hub_event_applies_the_name(self):
        src = PANEL.read_text()
        self.assertIn('ev.type === "membrane.name"', src)

    def test_the_seat_binding_is_mutable_and_never_a_nickname(self):
        src = PANEL.read_text()
        self.assertRegex(src, r"\blet SEAT\b")
        self.assertNotIn('"Argus"', src)


if __name__ == "__main__":
    unittest.main()

class TestTheJsSwitchSpeaksTheSwitch(unittest.TestCase):
    """THE SWEEP'S COPY (2026-10-15, the operator's ask): the JS toggle in
    the menu named the SEAT ('let Bob act as you here: off') -- the copy
    names the switch instead, in the operator's own words (the seat's
    name belongs to its own rename, not every button row)."""

    def test_the_menu_row_names_the_switch(self):
        self.assertIn('"JS scripting: off"', PANEL.read_text())

    def test_the_paint_names_the_switch(self):
        src = PANEL.read_text()
        self.assertIn('"JS scripting: ON"', src)
        self.assertIn('"JS scripting: off"', src)

    def test_the_old_seat_naming_is_swept_out_of_the_page(self):
        self.assertNotIn("act as you here", PANEL.read_text())

    def test_the_no_tab_error_names_the_new_door(self):
        import membrane.jsexec as J
        self.assertNotIn("act as you here", Path(J.__file__).read_text())
