"""THE NAME LINT (2026-10-08, the seat's own review, red first):

  "No name lint at creation. This turn's field has m:m:c:agdroid:doorbell
   (doubled prefix); two turns ago, c:{'cmd': -- a dict-literal as a
   command name. Worse: doorbell exists under two nodes (m:m:c: and
   m:component:c:) -- split-brain."

`m:` is the FIELD'S MIRROR of membrane's own entities (membrane::X is
m:X in the field) -- legit when READING (a track on m:c:agdroid:hass-token),
a bug when a model NAMES a new entity with it (the mirror prefix lands
twice) or puts the class into the name (component:c: beside c:). Names a
model creates are refused with a suggestion; merging the duplicates that
already exist stays the model's call (entity_edit merged_into).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import eids as E  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


class TestLintName(unittest.TestCase):
    def test_plain_names_pass(self):
        for n in ("agatha", "c:agdroid:doorbell", "ha-home", "agdroid-gh-search",
                  "tool:clock", "ha:device_tracker.agatha"):
            self.assertIsNone(E.lint_name(n), n)

    def test_the_seats_exhibits_are_refused(self):
        cases = {
            "c:{'cmd': 'uname -a'}": "brace",
            "m:m:c:agdroid:doorbell": "mirror",
            "m:c:agdroid:doorbell": "mirror",
            "membrane::c:agdroid:doorbell": "mirror",
            "component:c:agdroid:doorbell": "class",
            "c:c:agdroid": "stacked",
            "a\nb": "plain",
            "x" * 200: "long",
        }
        for name, word in cases.items():
            err = E.lint_name(name)
            self.assertIsNotNone(err, name)
            self.assertIn(word, err.lower(), (name, err))

    def test_suggestions_point_at_the_clean_name(self):
        self.assertIn("'c:agdroid:doorbell'", E.lint_name("m:m:c:agdroid:doorbell"))
        self.assertIn("'c:agdroid:doorbell'", E.lint_name("component:c:agdroid:doorbell"))

    def test_track_keys_may_read_the_mirror(self):
        self.assertIsNone(E.lint_key("m:c:agdroid:hass-token"))
        self.assertIsNotNone(E.lint_key("m:{'x'"))
        self.assertIsNotNone(E.lint_key("y" * 300))


class TestCreationSites(unittest.TestCase):
    def test_entity_add_refuses(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        r = est.add("Component", "m:m:c:agdroid:doorbell")
        self.assertFalse(r["ok"])
        self.assertIn("c:agdroid:doorbell", r["error"])
        self.assertTrue(est.add("Component", "c:agdroid:doorbell")["ok"])

    def test_a_dict_literal_is_not_a_command(self):
        self.assertIsNotNone(N.lint_command("{'cmd': 'uname -a'}"))
        self.assertIsNotNone(N.lint_command('{"cmd": "uname -a"}'))
        self.assertIsNone(N.lint_command("uname -a"))
        self.assertIsNone(N.lint_command("[ -f /etc/hosts ] && echo y"))


if __name__ == "__main__":
    unittest.main()
