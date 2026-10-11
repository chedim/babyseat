"""THE SEAT RUNS JS IN AN OPTED-IN BROWSER TAB (2026-10-11): the seat entity
implements remote_executor; a js:: command goes to a tab, never a shell."""
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
        r = JS.run("'console.log(1); return 2+3'", timeout=2)       # arrives shell-quoted, like a method arg
        self.assertEqual(r["rc"], 0)
        self.assertIn("=> 5", r["text"])
        self.assertEqual(jobs[0]["sessionID"], "ses_1")                 # only the owner's tab's session
        self.assertEqual(jobs[0]["job"]["code"], "console.log(1); return 2+3")

    def test_a_js_command_never_reaches_a_shell(self):
        srv, _ = _seat(answer=lambda job: {"ok": False, "error": "ReferenceError: nope"})
        JS.register(srv, "tab_a", "ses_1", "D")
        out = N.run_command_entity(None, None, None, "js::nope()", timeout_s=2)
        self.assertNotEqual(out["rc"], 0)
        self.assertIn("ReferenceError", out["head"])

    def test_the_seat_entity_implements_remote_executor(self):
        srv, _ = _seat()
        est = srv.entity_store
        pres = est.docs.get(MEMBRANE_NS + "presence")
        self.assertIsNotNone(pres)
        of = lambda d: pres.eid in ((d.properties or {}).get("method_of") or [])
        m = [d for d in est.docs.values() if of(d) and (d.properties or {}).get("method_name") == "exec"]
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].properties.get("command"), "js::{{code}}")
        iface = est._resolve_spec("remote_executor")
        if iface is not None:                                            # seeded on a real seat
            self.assertIn(iface.eid, pres.properties.get("parents") or [])
        JS.bind(srv)                                                     # idempotent
        self.assertEqual(len([d for d in est.docs.values() if of(d)]), 1)

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
