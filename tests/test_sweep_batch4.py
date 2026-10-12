"""THE SWEEP, BATCH 4 (2026-10-11, from the night's chat): voice-out's `say`
never landed (properties sent as JSON text were ignored while the reply said
'updated'); junk is marked, not deleted -- the engine honours the junk hub;
a retrieval claim no tool call backs ("that fetch landed") reaches the diff."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claims as CL  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class TestObjectsSentAsText(unittest.TestCase):
    def test_properties_and_args_as_json_strings_are_parsed(self):
        name, args = TS.resolve("entity_edit", {"name": "voice:out:dmitrii",
                                                "properties": '{"say": "Can you hear me?"}'})
        self.assertEqual(args["properties"], {"say": "Can you hear me?"})
        name, args = TS.resolve("exec", {"action": "call", "object": "presence", "method": "exec",
                                         "args": '{"code": "1+1"}'})
        self.assertEqual(args["args"], {"code": "1+1"})


def _store():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    for eid, props in (("Component:junk", {"name": "junk", "junk": ["s:?inlet_check"]}),
                       ("Component:husk", {"name": "husk", "parents": ["membrane::Component:junk"]}),
                       ("Component:real", {"name": "real"})):
        est.docs[MEMBRANE_NS + eid] = QuantumEntity(eid=MEMBRANE_NS + eid, cls="Component", properties=props)
    return est


class TestJunkByMarking(unittest.TestCase):
    def test_the_hub_and_its_children(self):
        ids = _store().junk_field_ids()
        self.assertIn("s:?inlet_check", ids)
        self.assertIn("m:component:husk", ids)
        self.assertNotIn("m:component:real", ids)

    def test_the_now_line_skips_junk(self):
        est = _store()

        class _St:
            tier = "live"

        class _F:
            _est_sink = est

            def changes_since(self, s):
                return {"m:component:husk": [1] * 50, "m:component:real": [1] * 3}

            def get(self, eid):
                return _St()

            def is_coherent(self, st):
                return True
        line = EB.now_line(_F(), 0)
        self.assertIn("m:component:real", line)
        self.assertNotIn("husk", line)


class _B:
    def __init__(self, events):
        self.events = events

    def get(self, coll, key):
        return self.events.get(key)


class TestUnbackedRetrieval(unittest.TestCase):
    def _fld(self, obs):
        f = type("F", (), {})()
        f.b = _B({"ev1": {"session": "ses_chat"}})
        return f

    def test_claimed_with_no_tool_call(self):
        c = CL.extract_claims("That fetch plus my promised parallel reads landed in one pass.", "model",
                              ts=1000.0, event="ev1")[0]
        st, _ = CL.match_retrieval(self._fld([]), {"ts": 1000.0, "event": "ev1"}, obs=[])
        self.assertEqual((c.kind, st), ("retrieval", "unbacked"))

    def test_backed_by_a_call_in_its_session(self):
        obs = [{"ts": 990.0, "session": "ses_chat", "tool": "exec", "outcome": "ok"}]
        st, refs = CL.match_retrieval(self._fld(obs), {"ts": 1000.0, "event": "ev1"}, obs=obs)
        self.assertEqual(st, "backed")

    def test_another_sessions_call_is_no_backing(self):
        obs = [{"ts": 990.0, "session": "ses_loop", "tool": "exec", "outcome": "ok"}]
        st, _ = CL.match_retrieval(self._fld(obs), {"ts": 1000.0, "event": "ev1"}, obs=obs)
        self.assertEqual(st, "unbacked")

    def test_plans_and_negations_are_no_claims(self):
        for s in ("I will fetch the page next.", "I did not fetch it.", "Reading it now."):
            self.assertEqual(CL.extract_claims(s, "model"), [], s)


if __name__ == "__main__":
    unittest.main()
