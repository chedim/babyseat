"""THE WAKE DIALECTS, PINNED (issue #17, PR #18): the seat's own model
files its wake specs in TWO dialects -- the native shell-quoted
`llm::wake 'about' 'at' 'prompt'` and the auto-minted call form
`llm.wake {about: 'x', at: '18:00', prompt: 'y'}` -- and the night's
builtins gate must route BOTH to the llm entity, NEVER /bin/sh (where a
call-form spec dies rc=127). The unpinned half of PR #18 (its ::-form
wake door was later re-pinned by the gate's tests):

- the DOTTED shell-quoted form through the gate (the :: form was the
  exhibit, the . form rides the same door);
- `llm::unwake` AND `llm.unwake` route the same way (the original gate
  matched `llm::unwake` only -- a dotted unwake went to the shell);
- the call-form DECODER: a well-formed `{about, at, prompt}` resolves
  the about spec and plants rc 0; a malformed one is refused rc 2 with
  the honest 'unrecognized call-form payload' -- never a shell, never a
  silent plant.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import llm as L  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import MEMBRANE_NS  # noqa: E402

from test_llm_wake import _Srv, BAT  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    srv = _Srv()
    L.bind(srv)
    return f, srv


def _wakes(srv):
    ent = srv.entity_store.docs.get(MEMBRANE_NS + "llm")
    if ent is None:
        return []
    return (ent.properties or {}).get("wakes") or []


class TestBothDialectsRouteTheGate(unittest.TestCase):
    def _run(self, line, f=None):
        f = f or _world()[0]
        srv = L.seat()
        with mock.patch.object(N.subprocess, "run",
                               side_effect=AssertionError("a shell")):
            r = N.run_command_entity(f, srv.entity_store, None, line)
        self.assertEqual(r.get("rc"), 0, (line, r))
        self.assertNotIn("not found", str(r.get("outcome")), r)
        return _wakes(srv)

    def test_the_dotted_shell_quoted_wake_plants(self):
        w = self._run("cd /tmp; llm.wake 'Component:bat' 'in 1h' 'check it'")
        self.assertEqual(len(w), 1, w)
        self.assertEqual(w[0]["about"], BAT, w)       # the spec resolved
        self.assertEqual(w[0]["prompt"], "check it")

    def test_both_unwake_dialects_cancel(self):
        # the original gate's startswith chain knew `llm::unwake` only;
        # a dotted unwake fell through to the shell and died rc=127.
        # (Plants go one at a time: two wakes in the same millisecond
        # mint the same short id, and one unwake cancels both).
        f, srv = _world()
        with mock.patch.object(N.subprocess, "run",
                               side_effect=AssertionError("a shell")):
            for dialect in ("llm::unwake ", "cd /x; llm.unwake "):
                L.wake_from_command(
                    "{about: 'Component:bat', at: 'in 1h', prompt: 'p'}")
                ws = _wakes(srv)
                self.assertEqual(len(ws), 1, ws)
                r = N.run_command_entity(f, srv.entity_store, None,
                                         dialect + ws[0]["id"])
                self.assertEqual(r.get("rc"), 0, (dialect, r))
                self.assertEqual(_wakes(srv), [], (dialect, ws))


class TestTheCallFormDecoder(unittest.TestCase):
    def test_a_well_formed_payload_plants_rc0(self):
        f, srv = _world()
        r = L.wake_from_command(
            "{about: 'Component:bat', at: 'in 2h', prompt: 'sweep the rung'}")
        self.assertEqual(r["rc"], 0, r)
        w = _wakes(srv)
        self.assertEqual(len(w), 1, w)
        self.assertEqual(w[0]["about"], BAT)
        self.assertEqual(w[0]["prompt"], "sweep the rung")

    def test_a_malformed_payload_refuses_rc2_honestly(self):
        # THE GATE'S COVENANT: an unrecognized form must name itself --
        # never a shell (rc 127 lied about WHAT failed), never a silent
        # plant.
        f, srv = _world()
        for payload in ("{about: 'Component:bat'}",                # missing fields
                        "{about: 'f:a', at: 'in 1h', prompt: }",   # torn
                        "{about: 'f:a', at: 'in 1h', prompt: 'p'",  # unclosed
                        ):
            with self.subTest(payload=payload):
                r = L.wake_from_command(payload)
                self.assertEqual(r.get("rc"), 2, r)
                self.assertIn("wake", str(r.get("text")))
        self.assertEqual(_wakes(srv), [], "a malformed payload planted")

    def test_shlex_form_still_works(self):
        # the native dialect rides untouched over the call-form branch
        f, srv = _world()
        r = L.wake_from_command("'Component:bat' 'in 2h' 'check the run'")
        self.assertEqual(r["rc"], 0, r)
        self.assertEqual([x["about"] for x in _wakes(srv)], [BAT])


if __name__ == "__main__":
    unittest.main()