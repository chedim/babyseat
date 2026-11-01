"""PRONOUNS ARE REGISTERS (the operator, 2026-11-01: "pronouns should be like CPU registers
for the conversation ... they move with it"; "it's a set of entities on the field ... it
should be per-person").

Pinned here:
  - each pronoun of each person is a field entity, `register:<person>:<pronoun>`, whose live
    -holds-> edge is what it means now; loading it supersedes the old edge (the history stays)
    and the message that loaded it -sets:<pronoun>-> the same target;
  - a resolution loads the register, so "undo it" after "delete it" -> "Deleted." still means
    the deleted thing; a re-ask's answer loads it too; other forms of a pronoun share one
    register (him -> he, them -> they); registers are per person;
  - the reply before is read first (the freshest mention); the register is the fallback when
    that reply names nothing that applies;
  - the command may be longer ("yes, merge it", "merge it and restart"); a pronoun outside a
    command ("is it done?") resolves silently: never asked back;
  - "#803" and a pasted .../pull/803 link are one entity, the project repo's issue 803.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_conversation_graph import SID, _out, _seat  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import relations as R  # noqa: E402

A, B = "f:membrane/a.py", "f:membrane/b.py"
PR = "u:https://github.com/chedim/babyseat/issues/803"


def _say(srv, runner, n, user, reply, sid=SID):
    """One exchange: the person's message, membrane's look at its pronouns, then (unless it
    asked back) the reply and the graph."""
    u = runner.messages.add(sid, f"msg_u{n}", "user", user)
    out = CG.before_turn(srv, runner, sid, u, user)
    if out and out.get("reask"):
        return out
    a = runner.messages.add(sid, f"msg_a{n}", "assistant", reply)
    CG.on_turn_end(srv, runner, sid, u, user, a, reply)
    return out


def _holds(srv, pronoun, sid=SID):
    return CG.register(srv, sid, pronoun)


class Registers(unittest.TestCase):
    def setUp(self):
        self._repo = CG._repo
        CG._repo = lambda srv: "chedim/babyseat"
        self.addCleanup(setattr, CG, "_repo", self._repo)

    def test_a_resolution_loads_the_register_and_it_carries_over(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        self.assertEqual(_holds(srv, "it"), A)
        out = _say(srv, runner, 3, "undo it", "Restored.")
        self.assertEqual((out or {}).get("resolved"), A)

    def test_the_register_is_an_entity_whose_history_stays(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        _say(srv, runner, 3, "look again", "Now membrane/b.py.")
        _say(srv, runner, 4, "delete it", "Deleted.")
        reg = CG.register_eid(SID, "it")
        self.assertIn(reg, srv.entity_store.docs)
        hist = [r for r in R.history_of(srv.efield.b, srv.efield.origin, reg, "holds")]
        self.assertEqual(sorted(r["obj"] for r in hist), [A, B])
        self.assertEqual([r["obj"] for r in hist if not r.get("superseded")], [B])
        self.assertIn(("sets:it", B), _out(srv, CG.msg_eid("msg_u4")))

    def test_the_register_is_the_fallback_when_the_reply_names_nothing_that_applies(self):
        from membrane.entities import MEMBRANE_NS, QuantumEntity
        srv, runner = _seat()
        srv.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:old", cls="Goal",
                                              properties={"name": "old", "status": "closed"}))
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        runner.messages.add(SID, "msg_a2b", "assistant", "That closes old.")
        R.upsert(srv.efield.b, srv.efield.origin, CG.msg_eid("msg_a2b"), "refers-to", MEMBRANE_NS + "goal:old", "chat")
        srv.entity_store.upsert(QuantumEntity(eid=CG.msg_eid("msg_a2b"), cls=CG.CLASS_MESSAGE, properties={"role": "assistant"}))
        out = _say(srv, runner, 3, "undo it", "Restored.")
        self.assertEqual((out or {}).get("resolved"), A)

    def test_a_reask_answer_loads_the_register(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
        self.assertTrue(_say(srv, runner, 2, "explain it", "-").get("reask"))
        out = _say(srv, runner, 3, "the a.py one", "It does x.")
        self.assertEqual((out or {}).get("resolved"), A)
        self.assertEqual(_holds(srv, "it"), A)
        self.assertIn('"it" means', CG.take_note(srv, SID) or "")

    def test_forms_share_one_register_and_registers_are_per_person(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        self.assertIsNone(_holds(srv, "it", sid="ses_other"))
        _say(srv, runner, 3, "look", "membrane/b.py and membrane/a.py changed.")
        _say(srv, runner, 4, "ping them", "pinged")
        self.assertEqual(_holds(srv, "they"), _holds(srv, "them"))


class LongerCommands(unittest.TestCase):
    def test_yes_merge_it_and_merge_it_and_restart_are_commands(self):
        for text in ("yes, merge it", "merge it and restart", "pls merge it now"):
            srv, runner = _seat()
            _say(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
            self.assertTrue((_say(srv, runner, 2, text, "-") or {}).get("reask"), text)

    def test_a_pronoun_outside_a_command_resolves_silently(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
        self.assertFalse((_say(srv, runner, 2, "is it done?", "yes") or {}).get("reask"))
        self.assertEqual(runner.posted, [])
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "is it done?", "yes")
        self.assertIn('"it" means', CG.take_note(srv, SID) or "")


class PullRequests(unittest.TestCase):
    def setUp(self):
        self._repo = CG._repo
        CG._repo = lambda srv: "chedim/babyseat"
        self.addCleanup(setattr, CG, "_repo", self._repo)

    def test_a_number_and_a_link_are_one_entity(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "status?", "Merged #803.")
        _say(srv, runner, 2, "and the link?", "https://github.com/chedim/babyseat/pull/803")
        for n in (1, 2):
            self.assertIn(("refers-to", PR), _out(srv, CG.msg_eid(f"msg_a{n}")))

    def test_merge_it_finds_the_pr(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "status?", "#803 is ready.")
        out = _say(srv, runner, 2, "merge it", "merging")
        self.assertEqual((out or {}).get("resolved"), PR)

    def test_no_repo_no_number_entities(self):
        srv, runner = _seat()
        CG._repo = lambda srv: None
        _say(srv, runner, 1, "status?", "#803 is ready.")
        self.assertNotIn(("refers-to", PR), _out(srv, CG.msg_eid("msg_a1")))


if __name__ == "__main__":
    unittest.main()
