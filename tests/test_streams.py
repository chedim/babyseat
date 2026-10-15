"""membrane/streams.py: the discourse/observation split (live turns and
the six audit shapes), outcomes read from head AND tail, shell structure,
and the invariant that discourse never carries tool syntax."""

import re
import unittest

from membrane import streams as S

_TOOL_SYNTAX = re.compile(r"args: ?\{|\w+\(\{|-> \w+\(args\)")

AUDIT = [
    {"query": 'tool bash for: run the tests please\nargs: {"command":"cd /x && .venv/bin/python -m unittest discover -s tests"}',
     "response": "...... Ran 524 tests in 6.2s OK", "ts": 1.0},
    {"query": "tool call for: can you fix the router? -> edit(args)",
     "response": 'edit({"filePath":"/x/membrane/router.py","newString":"a"})', "ts": 2.0},
    {"query": "plan: why does the gate escalate?",
     "response": "The gate escalates because sim is below 0.72. I will lower it.", "ts": 3.0},
    {"query": "reasoning: why does the gate escalate?",
     "response": "The user asks about the gate; let me check gate.py first.", "ts": 4.0},
    {"query": "compact note: tool edit for: nope, does not launch. can it select another port",
     "response": "The tool cannot select another or random port.", "ts": 5.0},
    {"query": 'pls continue bash({"command":"ls -la"})',
     "response": 'Sure, listing now. read({"filePath":"/x/a.py"})', "ts": 6.0},
]


class TestAuditShapes(unittest.TestCase):
    def evs(self, i):
        return S.split_audit_record(AUDIT[i])

    def test_tool_result_is_an_observation_plus_the_served_message(self):
        user, obs = self.evs(0)
        self.assertEqual((user.stream, user.role, user.text),
                         ("discourse", "user", "run the tests please"))
        self.assertEqual(obs.stream, "observation")
        self.assertEqual(obs.tool, "bash")
        self.assertEqual(obs.outcome, "tests-ok")
        self.assertTrue(obs.clipped)
        self.assertEqual(obs.served_text, "run the tests please")

    def test_tool_call_is_a_call_only_observation(self):
        user, obs = self.evs(1)
        self.assertEqual(user.text, "can you fix the router?")
        self.assertEqual((obs.tool, obs.outcome), ("edit", "call-only"))
        self.assertEqual(obs.args["filePath"], "/x/membrane/router.py")

    def test_plan_record_recovers_the_model_prose(self):
        user, model = self.evs(2)
        self.assertEqual(user.text, "why does the gate escalate?")
        self.assertEqual(model.role, "model")
        self.assertIn("sim is below 0.72", model.text)

    def test_reasoning_record_is_reasoning_discourse(self):
        _, r = self.evs(3)
        self.assertEqual((r.stream, r.role), ("discourse", "reasoning"))

    def test_compact_note_is_distilled_only(self):
        (d,) = self.evs(4)
        self.assertEqual((d.stream, d.role), ("distilled", "note"))
        self.assertIn("cannot select another or random port", d.text)

    def test_plain_turn_splits_inline_calls_off(self):
        evs = self.evs(5)
        kinds = [(e.stream, e.role) for e in evs]
        self.assertIn(("discourse", "user"), kinds)
        self.assertIn(("observation", "tool"), kinds)
        user = next(e for e in evs if e.role == "user")
        self.assertEqual(user.text, "pls continue")
        model = next(e for e in evs if e.role == "model")
        self.assertEqual(model.text, "Sure, listing now.")

    def test_no_tool_syntax_ever_reaches_discourse(self):
        for rec in AUDIT:
            for e in S.split_audit_record(rec):
                if e.stream == "discourse":
                    self.assertIsNone(_TOOL_SYNTAX.search(e.text), e.text)


class TestLiveTurn(unittest.TestCase):
    def test_mixed_prose_and_calls_keep_the_prose(self):
        calls = {0: {"id": "c1", "function": {"name": "read",
                                              "arguments": '{"filePath": "a.py"}'}}}
        evs = S.split_turn("look at a.py", "Reading a.py now.", calls,
                           thought="Need to read it first.",
                           results=[("c0", "bash", '{"command": "ls"}', "a.py\nb.py")])
        roles = [(e.stream, e.role) for e in evs]
        self.assertEqual(roles[:3], [("discourse", "user"), ("discourse", "model"),
                                     ("discourse", "reasoning")])
        obs = [e for e in evs if e.stream == "observation"]
        self.assertEqual([(o.tool, o.call_id, o.outcome) for o in obs],
                         [("read", "c1", "call-only"), ("bash", "c0", "ok")])

    def test_long_results_keep_head_and_tail(self):
        big = "x" * 9000 + "\nRan 12 tests in 1s\nFAILED (failures=1)"
        (_, obs) = S.split_turn("run", results=[("c", "bash", "{}", big)])
        self.assertEqual(len(obs.result_head), S.RESULT_HEAD)
        self.assertIn("FAILED", obs.result_tail)
        self.assertEqual(obs.outcome, "tests-failed")
        self.assertEqual(obs.result_len, len(big))


class TestParsing(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(S.outcome("edit", "Edit applied successfully."), "applied")
        self.assertEqual(S.outcome("read", "File not found: x"), "not-found")
        self.assertEqual(S.outcome("bash", "Traceback (most recent call last)"), "error")
        self.assertEqual(S.outcome("bash", "head", "Ran 5 tests\nOK"), "tests-ok")
        self.assertEqual(S.tests_ran("", "Ran 524 tests in 6s"), 524)

    def test_outcome_rc_trumps_notfound_text(self):
        # issue #13: a FAILED pytest collection printed 'file or
        # directory not found' and got run_outcome 'not-found' -- the
        # rc gate must come first wherever an rc exists
        self.assertEqual(S.outcome("c:pytest", "ERROR: file or directory not found: tests/foo.py\n1 error", rc=1), "error")
        self.assertEqual(S.outcome("c:python", "FileNotFoundError: [Errno 2] No such file or directory", rc=1), "error")
        self.assertEqual(S.outcome("c:presence.exec", "/bin/sh: 1: x: not found", rc=127), "error")
        self.assertEqual(S.outcome("c:note", "note: /tmp/x not found", rc=0), "ok")
        # without an rc, the text heuristic keeps its not-found class
        self.assertEqual(S.outcome("read", "File not found: x", rc=None), "not-found")
        self.assertEqual(S.outcome("c:make", "make: *** Error 1", rc=1), "error")

    def test_full_suite_detection(self):
        self.assertTrue(S.is_full_suite("python -m unittest discover -s tests"))
        self.assertTrue(S.is_full_suite("cd x && pytest -q"))
        self.assertFalse(S.is_full_suite("pytest tests/test_x.py"))
        self.assertFalse(S.is_full_suite("python -m unittest tests.test_x"))

    def test_shell_heads_ignore_heredoc_and_quoted_python(self):
        cmd = ("cd /x && python3 - <<'EOF'\nimport sys\ndef main(): print(1)\nEOF\n"
               "grep -n foo a.py | head -3; python -c \"def x(): pass\"")
        self.assertEqual(S.shell_heads(cmd), ["python3", "grep", "head", "python"])

    def test_double_escaped_args_are_unescaped(self):
        a = S.parse_args('{"command": "python - <<EOF\\\\nsys.path.insert(0)\\\\nEOF"}')
        self.assertIn("\nsys.path.insert", a["command"])

    def test_ports_skip_json_values(self):
        self.assertEqual(S.ports('curl localhost:11500/v1 {"timeout":60000}'), ["11500"])

    def test_observation_fact(self):
        (_, obs) = S.split_turn("x", results=[("c", "edit", '{"filePath": "a.py"}',
                                                "Edit applied successfully.")])
        self.assertEqual(S.observation_fact(obs),
                         "edit a.py: applied. Edit applied successfully.")

    def test_sentences_rule(self):
        s = S.sentences("ok. The router picks the cheapest model for each turn. "
                        "The router picks the cheapest model for each turn.")
        self.assertEqual(s, ["The router picks the cheapest model for each turn."])


class TestTheTierReadsTheRc(unittest.TestCase):
    """THE MENTION, NOT THE FACT (issue #48, 2026-10-15): a plain rc=0
    `git log` was stamped tests-failed because _RAN ('Ran N tests|N
    passed') matched the log's COMMIT MESSAGES (this very repo's commits
    end in 'N passed on this tree') and the same log's failure-worded
    text sealed it: the sniff ran regardless of rc. But rc is the
    run's ground truth (the residue exhibit's own doctrine), and
    claims.py reads outcome == tests-failed as UN-BACKED -- a read-only
    command's mention cost the seat a real claim for a word."""

    def test_a_rc0_log_that_mentions_the_words_is_not_failed(self):
        log = ("abc123 the call in the name slot (the salvages): red->green; "
               "1768 passed on this tree) (#50)\n"
               "dead5a a bad byte... the model sent 70 calls and failed to "
               "pass arguments to tools; the poison link completes instead "
               "of crashing the beat")
        self.assertEqual(S.outcome("c:git", log, rc=0), "tests-ok")

    def test_a_failing_test_run_is_still_failed(self):
        self.assertEqual(S.outcome("bash", "Ran 5 tests",
                                   "FAILED (failures=2)", rc=1),
                         "tests-failed")

    def test_a_passing_test_run_is_ok(self):
        self.assertEqual(S.outcome("bash", "Ran 5 tests", "OK", rc=0),
                         "tests-ok")


if __name__ == "__main__":
    unittest.main()
