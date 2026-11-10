"""The chat shows the person's own message as theirs when the seat writes the id form of the speaker line
(`[user#owner: owner]`, speakers._ID_LINE), not as a grey machine line with the tag showing (2026-11-10)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

PANEL = str(Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js")


def _run(expr):
    js = f"const L = require({json.dumps(PANEL)}); console.log(JSON.stringify({expr}));"
    return json.loads(subprocess.check_output(["node", "-e", js], timeout=30))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestSpeakerIdLine(unittest.TestCase):
    def test_the_id_form_is_a_person_and_its_tag_is_stripped(self):
        t = "[user#owner: owner]\nhotfix my chat please"
        self.assertTrue(_run(f"L.isPerson({json.dumps(t)})"))
        self.assertEqual(_run(f"L.stripSpeaker({json.dumps(t)})"), "hotfix my chat please")

    def test_the_old_forms_still_are_and_a_loop_line_is_not(self):
        for t in ("[user: Dima]\nhi", "[HA user: Dima]\nhi"):
            self.assertTrue(_run(f"L.isPerson({json.dumps(t)})"), t)
            self.assertEqual(_run(f"L.stripSpeaker({json.dumps(t)})"), "hi")
        self.assertFalse(_run('L.isPerson("planner: split the goal")'))
        self.assertTrue(_run('L.SPEAKER_RE.test("x [user#owner: owner] y")'))
