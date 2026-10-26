# THE JS DOOR'S RECEIPTS (issue #2): the door runs ANY payload as
# JavaScript -- "echo hi" rode the round trip, died as SyntaxError or sat
# till the timeout, and the receipt came back rc=1 bare "error" or
# "=> None" with no reason; a shell payload must be refused LOUDLY at the
# door, a failed run must say why, and a value the tab never returned must
# not be printed as "None".
import types
import unittest

from membrane import jsexec as JS
from membrane import toolset as T


class _Hub:
    """The honest tab: every job is answered inside the publish turn, exactly
    as a page would (value/error/logs) -- tests stay off the wire."""

    def __init__(self, srv, answer):
        self._srv = srv
        self._answer = answer
        self.jobs = []

    def publish(self, ev):
        self.jobs.append(ev)
        if ev.get("type") == "membrane.jsexec":
            self._answer(JS.result, ev["job"])


def _world(answer):
    srv = types.SimpleNamespace(_js_tabs={},
                                _loop_runner=None)
    hub = _Hub(srv, answer)
    runner = types.SimpleNamespace(hub=hub)
    srv._loop_runner = runner
    JS.bind(srv)
    JS.register(srv, "t1", "s1", "p")
    return srv, hub


class TestTheDoorRefusesShell(unittest.TestCase):
    def tearDown(self):
        with JS._lock:
            JS._jobs.clear()

    def test_a_shell_line_is_refused_loudly_before_the_round_trip(self):
        srv, hub = _world(lambda result, job: result(srv, {"person": "p"}))
        r = JS.run("echo hi", tab="t1")
        self.assertEqual(r["rc"], 2)
        self.assertIn("JavaScript", r["err"])
        self.assertIn("echo hi", r["err"])
        self.assertFalse(hub.jobs, "the payload must not ride the round trip")

    def test_the_door_refuses_a_shell_line_even_when_a_tab_is_dead(self):
        srv, hub = _world(lambda result, job: result(srv, {"person": "p"}))
        r = JS.run("ls -la", timeout=0.5)
        self.assertEqual(r["rc"], 2)
        self.assertFalse(hub.jobs)

    def test_real_js_still_runs(self):
        srv, hub = _world(lambda result, job: result(
            srv, {"id": job["id"], "person": "p", "ok": True, "value": "2", "logs": []}))
        r = JS.run("2 + 2", tab="t1")
        self.assertEqual(r["rc"], 0)
        self.assertIn("=> 2", r["text"])

    def test_a_real_js_failure_says_why(self):
        srv, hub = _world(lambda result, job: result(
            srv, {"id": job["id"], "person": "p", "ok": False,
                  "error": "SyntaxError: Unexpected identifier 'hi'", "logs": []}))
        r = JS.run("echo('hi", tab="t1")
        self.assertEqual(r["rc"], 1)
        self.assertIn("SyntaxError", r["text"])


class TestNoFakeValues(unittest.TestCase):
    def tearDown(self):
        with JS._lock:
            JS._jobs.clear()

    def test_an_undefined_return_is_not_printed_as_None(self):
        srv, hub = _world(lambda result, job: result(
            srv, {"id": job["id"], "person": "p", "ok": True, "logs": ["did the thing"]}))
        r = JS.run("console.log('did the thing')", tab="t1")
        self.assertEqual(r["rc"], 0)
        self.assertNotIn("=> None", r["text"])
        self.assertIn("undefined", r["text"])
        self.assertIn("did the thing", r["text"])

    def test_silently_nothing_is_not_silent(self):
        srv, hub = _world(lambda result, job: result(
            srv, {"id": job["id"], "person": "p", "ok": True}))
        r = JS.run("await Promise.resolve(0)", tab="t1")
        self.assertEqual(r["rc"], 0)
        self.assertTrue(r["text"].strip(), "rc 0 with nothing to show must still say something")


class TestActionsListing(unittest.TestCase):
    def test_a_bare_action_family_names_how_to_call_it(self):
        try:
            T.resolve("state", {"action": "list"})
        except ValueError as e:
            msg = str(e)
        self.assertFalse(msg.rstrip().endswith("actions:"),
                         "an empty listing: 'actions: ' is undiscoverable")
        self.assertIn("no action", msg)


if __name__ == "__main__":
    unittest.main()