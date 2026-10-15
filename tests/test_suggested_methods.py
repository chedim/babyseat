"""THE SUGGESTED METHODS (2026-10-15): an idiom the seat keeps repeating, offered as
a cliffhanger method it could define.

On a real seat's field, shell idioms act on the SAME thing: grep -> sed share an
argument in 54% of consecutive pairs (`grep -n "def _inject" opencode.py` ->
`sed -n '6896,7040p' opencode.py`: find the line, read around it), git -> grep 86%,
python -> git 50%. field.suggested_methods() reads an idiom's real instances, makes
what both commands share {{path}} (or {{target}}), each step's other arguments its
own numbered params, keeps the flags, and offers the most common shape (seen at
least 3 times) as a ready exec(action='define', steps=[...]) -- with a real example.
Never defined by itself: offering is recording; defining stays a deliberate act.
Tool-to-tool idioms (entity_add -> entity_edit) are no commands: no suggestion."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import fieldmap as FM  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

FILES = ["membrane/a.py", "membrane/b.py", "membrane/c.py", "membrane/d.py", "membrane/e.py"]
PATTERNS = ["def run", "class Field", "import os", "TODO", "return x"]


def _call(t, tool, args, cid):
    return ST.Event(stream="observation", tool=tool, args=args, result_head=f"out {cid}",
                    outcome="ok", ts=t, call_id=cid)


def _world():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")
    t = 1000.0
    for i, (p, pat) in enumerate(zip(FILES, PATTERNS)):
        f.write_events([
            _call(t, "shell", {"command": f'grep -n "{pat}" {p}'}, f"{i}a"),
            _call(t + 1, "shell", {"command": f"sed -n '{10 * i},{10 * i + 40}p' {p}"}, f"{i}b"),
            _call(t + 2, "entity_add", {"name": f"x{i}"}, f"{i}c"),
            _call(t + 3, "entity_edit", {"spec": f"x{i}"}, f"{i}d"),
            _call(t + 4, "shell", {"command": f"ls /tmp/{i}"}, f"{i}e")], session=f"s{i}")
        t += 100
    f.process()
    return f


class TestTheSuggestions(unittest.TestCase):
    def test_a_shell_idiom_on_one_file_becomes_a_two_step_method(self):
        f = _world()
        got = {(s["from"], s["to"]): s for s in f.suggested_methods(min_n=3)}
        s = got[("grep", "sed")]
        self.assertEqual(s["n"], len(FILES))
        steps = s["define"]["steps"]
        self.assertEqual([x["name"] for x in steps], ["grep", "sed"])
        self.assertIn("{{path}}", steps[0]["command"])
        self.assertIn("{{path}}", steps[1]["command"])
        self.assertTrue(steps[0]["command"].startswith("grep -n "))
        self.assertTrue(steps[1]["command"].startswith("sed -n "))
        self.assertEqual(s["define"]["action"], "define")
        self.assertIn("path", s["params"])
        self.assertIn("membrane/", s["example"][0])           # a real instance

    def test_tool_to_tool_idioms_suggest_nothing(self):
        f = _world()
        pairs = {(s["from"], s["to"]) for s in f.suggested_methods(min_n=1)}
        self.assertNotIn(("entity_add", "entity_edit"), pairs)

    def test_commands_that_share_nothing_suggest_nothing(self):
        f = _world()
        pairs = {(s["from"], s["to"]) for s in f.suggested_methods(min_n=1)}
        self.assertNotIn(("sed", "ls"), pairs)                  # sed X -> ls /tmp/i: nothing shared

    def test_a_shared_redirection_is_no_shared_thing(self):
        """On a real field 'ls dir/ 2>/dev/null' -> 'grep -n x file 2>/dev/null' read
        as sharing a path: a redirection is shell plumbing, never an argument."""
        from membrane import idioms as I
        self.assertIsNone(I.generalize("ls", "grep", "ls membrane/x/ 2>/dev/null",
                                       'grep -n "queu" membrane/y.py 2>/dev/null'))
        ga, gb = I.generalize("grep", "sed", 'grep -n "def run" membrane/a.py 2>/dev/null',
                              "sed -n '1,9p' membrane/a.py 2>&1")
        self.assertNotIn("dev/null", ga + gb)
        self.assertNotIn("2>", ga + gb)

    def test_the_suggestion_is_a_method_the_seat_accepts(self):
        os.environ.setdefault("MEMBRANE_RUN", "0")
        from membrane import night as N
        from membrane.entities import EntityStore
        f = _world()
        s = next(x for x in f.suggested_methods(min_n=3) if (x["from"], x["to"]) == ("grep", "sed"))
        est = EntityStore(path=None, shared=False)
        N.seed_executor_entity(est)
        d = s["define"]
        r = N.method_define(None, est, f, None, "files", d["method"], "", steps=d["steps"])
        self.assertTrue(r["ok"], r)

    def test_nothing_is_ever_defined_by_itself(self):
        f = _world()
        f.suggested_methods(min_n=3)
        self.assertFalse([d for _, d in f.b.scan("relations") if "suggest" in str(d)])

    def test_the_command_card_offers_it(self):
        f = _world()
        st = f.get("c:grep")
        card = FM.entity_card(st.to_doc(f.origin), [], [], [], suggested=f.suggested_for("c:grep"))
        self.assertEqual(card["suggested"][0]["to"], "sed")
        self.assertEqual(f.suggested_for("f:membrane/a.py"), [])



import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestTheDrawerOffersIt(unittest.TestCase):
    def test_the_card_draws_the_suggestion_escaped(self):
        f = _world()
        sug = f.suggested_for("c:grep")
        sug[0]["example"] = ["grep -n '<b>' a.py", "sed -n 1,2p a.py"]
        js = (f"const L = require({json.dumps(str(Path(__file__).resolve().parent.parent / 'membrane/ui/field.js'))});\n"
              f"process.stdout.write(L.cardHtml({json.dumps({'eid': 'c:grep', 'n': 5, 'suggested': sug})}, () => true));")
        h = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
        self.assertIn("Suggested method", h)
        self.assertIn("grep_then_sed", h)
        self.assertIn("{{path}}", h)
        self.assertIn("define it", h)
        self.assertNotIn("<b>'", h)                           # what was run is escaped


if __name__ == "__main__":
    unittest.main()
