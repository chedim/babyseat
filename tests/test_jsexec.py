"""THE SEAT RUNS JS IN AN OPTED-IN BROWSER TAB (2026-10-11): the seat entity
implements remote_executor; JS goes to a tab, never a shell. Since #614 JS is
code like any other (exec(action='run', lang='js'), tests/test_code_tree_js.py)."""
import sys
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import jsexec as JS  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402


def _seat(answer=None):
    srv = types.SimpleNamespace(entity_store=EntityStore(path=None, shared=False))
    jobs = []

    def publish(ev):
        if ev.get("type") == "membrane.jsexec":
            jobs.append(ev)
            if answer is not None:                       # the tab answers, a moment later
                threading.Timer(0.05, lambda: JS.result(srv, {"id": ev["job"]["id"], **answer(ev["job"])})).start()
    srv._loop_runner = types.SimpleNamespace(hub=types.SimpleNamespace(publish=publish))
    JS.bind(srv)
    return srv, jobs


class TestJsExec(unittest.TestCase):
    def test_no_opted_in_tab_is_an_honest_error(self):
        srv, _ = _seat()
        r = JS.run("return 1", timeout=1)
        self.assertEqual(r["rc"], 127)
        self.assertIn("opt in", r["err"])

    def test_a_tab_runs_the_job_and_answers(self):
        srv, jobs = _seat(answer=lambda job: {"ok": True, "value": "5", "logs": ["hi"]})
        JS.register(srv, "tab_a", "ses_1", "Dmitrii Chechetkin", "computer")
        r = JS.run("console.log(1); return 2+3", timeout=2)          # #614: arrives as written
        self.assertEqual(r["rc"], 0)
        self.assertIn("=> 5", r["text"])
        self.assertEqual(jobs[0]["sessionID"], "ses_1")                 # only the owner's tab's session
        self.assertEqual(jobs[0]["job"]["code"], "console.log(1); return 2+3")

    def test_the_seat_entity_implements_remote_executor(self):
        """#614: presence is the js executor; its old exec method is retired."""
        srv, _ = _seat()
        est = srv.entity_store
        pres = est.docs.get(MEMBRANE_NS + "presence")
        self.assertIsNotNone(pres)
        of = lambda d: pres.eid in ((d.properties or {}).get("method_of") or [])
        self.assertEqual([d for d in est.docs.values() if of(d) and (d.properties or {}).get("method_name") == "exec"], [])
        self.assertIn("lang='js'", pres.properties.get("executes", ""))
        iface = est._resolve_spec("remote_executor")
        if iface is not None:                                            # seeded on a real seat
            self.assertIn(iface.eid, pres.properties.get("parents") or [])

    def test_turning_a_tab_off_drops_it(self):
        srv, _ = _seat()
        JS.register(srv, "tab_a", "ses_1", "D")
        JS.register(srv, "tab_a", "ses_1", "D", on=False)
        self.assertEqual(JS.tabs(srv), [])


if __name__ == "__main__":
    unittest.main()


class TestTheChatMayShowThings(unittest.TestCase):
    def test_the_chat_can_call_the_seats_exec(self):
        from membrane import server as S
        self.assertIsNone(S._lane_refusal("method_call", "operator"))            # Argus can show things from the chat
