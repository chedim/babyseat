"""membrane/claims.py: extraction (negation, intent, conditionals, passive,
test counts, state), evidence matching at the window edges, and the port
case end to end: a distilled note contradicted by the code."""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import claims as CL  # noqa: E402
from membrane import code_evidence as CE  # noqa: E402
from membrane import codegraph  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def one(s, role="model"):
    cs = CL.extract_claims(s, role, ts=T0)
    return (cs[0].kind, cs[0].polarity, cs[0].tense, cs[0].status) if cs else None


class TestExtraction(unittest.TestCase):
    def test_table(self):
        self.assertEqual(one("I updated membrane/router.py to pick free models."),
                         ("action", 1, "past", "pending"))
        self.assertEqual(one("I did not update membrane/router.py yet."),
                         ("action", -1, "past", "pending"))
        self.assertEqual(one("I'll update membrane/router.py next."),
                         ("action", 1, "future", "n/a"))
        self.assertEqual(one("membrane/router.py was fixed in the last pass."),
                         ("action", 1, "past", "pending"))
        self.assertIsNone(one("If we update membrane/router.py it breaks."))
        self.assertIsNone(one("Did you update membrane/router.py?"))
        self.assertIsNone(one("I updated the docs."))            # no specific object
        c = CL.extract_claims("All 524 tests pass now.", "model", ts=T0)[0]
        self.assertEqual((c.kind, c.count), ("tests", 524))
        self.assertEqual(one("membrane/opencode.py cannot bind another port."),
                         ("state", 1, "present", "pending_judge"))
        self.assertIsNone(one("The gate cannot decide."))          # generic, model role
        self.assertEqual(one("The tool cannot select another port.", role="note"),
                         ("state", 1, "present", "pending_judge"))


def field_with(obs_specs):
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    evs = []
    for tool, args, result, dt in obs_specs:
        e = ST.split_turn("x", results=[("", tool, args, result)], ts=T0 + dt)[1]
        evs.append(e)
    f.write_events(evs)
    return f


class TestMatching(unittest.TestCase):
    CLAIM = {"kind": "action", "subjects": ["f:membrane/router.py"], "ts": T0, "polarity": 1}

    def test_edit_inside_the_window_backs_the_claim(self):
        f = field_with([("edit", '{"filePath": "membrane/router.py"}',
                         "Edit applied successfully.", -45 * 60)])
        self.assertEqual(CL.match_action(f, self.CLAIM)[0], "backed")

    def test_edit_outside_the_window_does_not(self):
        f = field_with([("edit", '{"filePath": "membrane/router.py"}',
                         "Edit applied successfully.", -46 * 60)])
        self.assertEqual(CL.match_action(f, self.CLAIM)[0], "unbacked")

    def test_only_failed_edits_contradict(self):
        f = field_with([("edit", '{"filePath": "membrane/router.py"}',
                         "Could not find oldString", -60)])
        self.assertEqual(CL.match_action(f, self.CLAIM)[0], "contradicted")

    def test_a_denied_action_that_happened_is_contradicted(self):
        f = field_with([("edit", '{"filePath": "membrane/router.py"}',
                         "Edit applied successfully.", -60)])
        self.assertEqual(CL.match_action(f, {**self.CLAIM, "polarity": -1})[0], "contradicted")

    def test_writing_shell_commands_count(self):
        f = field_with([("bash", '{"command": "sed -i s/a/b/ membrane/router.py"}', "", -30)])
        self.assertEqual(CL.match_action(f, self.CLAIM)[0], "backed")

    def test_test_counts(self):
        full = '{"command": "python -m unittest discover -s tests"}'
        c = {"kind": "tests", "count": 524, "ts": T0}
        self.assertEqual(CL.match_tests(field_with([("bash", full, "Ran 524 tests\nOK", -120)]), c)[0],
                         "backed")
        self.assertEqual(CL.match_tests(field_with([("bash", full, "Ran 523 tests\nOK", -120)]), c)[0],
                         "contradicted")
        one_file = '{"command": "pytest tests/test_x.py"}'
        self.assertEqual(CL.match_tests(field_with([("bash", one_file, "12 passed", -60)]), c)[0],
                         "unbacked")


class FakeJudge:
    def __init__(self):
        self.calls = 0

    def judge(self, claim, evidence):
        self.calls += 1
        return ("contradicts", "the code walks up") if any("walks up" in e for e in evidence) \
            else ("unrelated", "")


class TestThePortCase(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="ef-port-")
        p = Path(d) / "membrane/opencode.py"
        p.parent.mkdir(parents=True)
        p.write_text(textwrap.dedent('''
            def _pick_listen_port(preferred):
                """A port the proxy can bind: if another port owner holds it the probe walks up to the next free port."""
            '''))
        self.g = codegraph.build_graph(d)
        self.f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host", graph_fn=lambda: self.g)
        self.f.write_events(CE.code_events(self.g))
        self.f.write_events([ST.Event(stream="distilled", role="note", ts=T0,
                                      text="The tool cannot select another or random port.")])
        self.f.process()

    def test_the_note_becomes_a_pending_state_claim(self):
        (c,) = [d for _, d in self.f.b.scan("claims")]
        self.assertEqual((c["kind"], c["status"]), ("state", "pending_judge"))

    def test_the_judge_finds_the_code_and_the_conflict_lands_on_the_symbol(self):
        q = CL.JudgeQueue(self.f, FakeJudge())
        self.assertEqual(q.tick(), 1)
        (c,) = [d for _, d in self.f.b.scan("claims")]
        self.assertEqual(c["status"], "contradicted")
        st = self.f.get("s:membrane.opencode._pick_listen_port")
        self.assertEqual(len(st.conflicts), 1)

    def test_the_queue_respects_idle_and_budget(self):
        j = FakeJudge()
        self.assertEqual(CL.JudgeQueue(self.f, j, idle=lambda: False).tick(), 0)
        self.assertEqual(CL.JudgeQueue(self.f, j, per_hour=0).tick(), 0)
        self.assertEqual(j.calls, 0)


if __name__ == "__main__":
    unittest.main()
