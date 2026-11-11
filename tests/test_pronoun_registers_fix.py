"""Pronoun registers, fixed (operator 2026-11-11, "our pronoun registers are bad" -> keep them, fixed).
Rosie's live registers had then->concept:net, there->concept:restart, it->concept:rule-yaml,
this->concept:rosie, that->concept:ts-extractors. Pinned here:
  1. real pronouns only: "then" and "there" are never registers;
  2. concrete holders only: a living-language Concept is never a reading (nothing held, nothing told);
  3. per person, not per chat: "it" follows the speaker across their chats;
  4. expiry: REGISTER_TTL_TURNS (3, the operator) of the person's messages without the held thing
     being mentioned by the person or touched by the turn -> nothing held or told;
  5. the model is told only valid readings;
  6. a bad register from before (concept-only holder, a non-pronoun key) is ignored and emptied."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_conversation_graph import SID, _seat  # noqa: E402
from test_pronoun_registers import A, _say  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402

CONCEPT = MEMBRANE_NS + "concept:rule-yaml"


def _concept(srv, eid=CONCEPT, name="rule yaml"):
    srv.entity_store.upsert(QuantumEntity(eid=eid, cls="Concept", properties={"name": name}), embedder=None)


class RealPronounsOnly(unittest.TestCase):
    def test_then_and_there_are_not_pronouns(self):
        self.assertNotIn("then", CG.FORMS)
        self.assertNotIn("there", CG.FORMS)
        self.assertIsNone(CG._PRON.search("ok then, restart there"))

    def test_then_never_loads_a_register(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "restart then", "ok")
        regs = [e for e in srv.entity_store.docs if e.startswith(CG.REG) and e.endswith(":then")]
        self.assertEqual(regs, [])


class ConcreteHoldersOnly(unittest.TestCase):
    def test_a_concept_is_not_concrete(self):
        self.assertFalse(CG._concrete(CONCEPT))
        self.assertFalse(CG._concrete("w:restart"))
        for eid in (A, "u:https://github.com/chedim/babyseat/issues/803", MEMBRANE_NS + "goal:abc",
                    CG.msg_eid("msg_1"), "person:operator", "c:gradle test"):
            self.assertTrue(CG._concrete(eid), eid)

    def test_a_concept_reading_is_neither_held_nor_told(self):
        from membrane import language as LL
        srv, runner = _seat()
        _concept(srv)
        orig = LL.concept_before
        LL.concept_before = lambda est, text, at: CONCEPT
        self.addCleanup(setattr, LL, "concept_before", orig)
        _say(srv, runner, 1, "cards?", "ok")
        out = _say(srv, runner, 2, "the rule yaml thing looks wrong, park that until later", "ok")
        self.assertFalse((out or {}).get("resolved"))
        self.assertIsNone(CG.register(srv, SID, "that"))
        self.assertIsNone(CG.take_note(srv, SID))


class PerPerson(unittest.TestCase):
    def test_it_follows_the_speaker_across_chats(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "[user: Dima]\nlook", "I changed membrane/a.py.", sid="ses_one")
        _say(srv, runner, 2, "[user: Dima]\ndelete it", "Deleted.", sid="ses_one")
        self.assertEqual(CG.register(srv, "ses_two", "it", person="Dima"), A)
        out = _say(srv, runner, 3, "[user: Dima]\nundo it", "Restored.", sid="ses_two")
        self.assertEqual((out or {}).get("resolved"), A)

    def test_two_people_keep_their_own(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "[user: Dima]\nlook", "I changed membrane/a.py.", sid="ses_one")
        _say(srv, runner, 2, "[user: Dima]\ndelete it", "Deleted.", sid="ses_one")
        self.assertIsNone(CG.register(srv, "ses_one", "it", person="Ann"))
        self.assertEqual(CG.register(srv, "ses_one", "it", person="Dima"), A)


class Expiry(unittest.TestCase):
    def test_the_ttl_is_the_operators_three(self):
        self.assertEqual(CG.REGISTER_TTL_TURNS, 3)

    def test_a_stale_reading_expires(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        self.assertEqual(CG.register(srv, SID, "it"), A)
        for n in range(CG.REGISTER_TTL_TURNS):
            _say(srv, runner, 10 + n, f"unrelated chat number {n}", "ok")
        self.assertIsNone(CG.register(srv, SID, "it"))
        out = _say(srv, runner, 99, "undo it", "?")
        self.assertFalse((out or {}).get("resolved"))
        self.assertIsNone(CG.take_note(srv, SID))

    def test_the_person_mentioning_it_keeps_it_fresh(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        for n in range(CG.REGISTER_TTL_TURNS + 2):
            _say(srv, runner, 10 + n, "membrane/a.py again", "ok")
        self.assertEqual(CG.register(srv, SID, "it"), A)

    def test_run_it_again_four_messages_later_while_the_work_kept_touching_it(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "run it", "Ran membrane/a.py.")
        for n in range(4):                              # the person says other things; the work touches a.py
            _say(srv, runner, 10 + n, f"keep going {n}", "Still working in membrane/a.py." if n < 3 else "Done.")
        # the reply right before names nothing: only a register kept fresh by the touches can answer
        out = _say(srv, runner, 99, "run it again", "Ran.")
        self.assertEqual((out or {}).get("resolved"), A)


class OldBadRegisters(unittest.TestCase):
    def test_a_concept_only_register_is_ignored_and_emptied_when_touched(self):
        srv, runner = _seat()
        _concept(srv)
        _say(srv, runner, 1, "hello", "hi")               # the person is known for this chat
        reg = CG.register_eid(CG.person_key(srv, SID), "it")
        srv.entity_store.upsert(QuantumEntity(eid=reg, cls=CG.CLASS_REGISTER, properties={
            "pronoun": "it", "holds": [CONCEPT]}), embedder=None)
        R.upsert(srv.efield.b, srv.efield.origin, reg, "holds", CONCEPT, "register")
        self.assertIsNone(CG.register(srv, SID, "it"))   # ignored on read
        out = _say(srv, runner, 2, "undo it", "?")
        self.assertFalse((out or {}).get("resolved"))
        self.assertIsNone(CG.take_note(srv, SID))
        self.assertEqual(srv.entity_store.docs[reg].properties.get("holds"), [])   # emptied

    def test_a_legacy_then_register_is_emptied_when_its_chat_is_touched(self):
        srv, runner = _seat()
        legacy = CG.REG + SID + ":then"
        srv.entity_store.upsert(QuantumEntity(eid=legacy, cls=CG.CLASS_REGISTER, properties={
            "pronoun": "then", "holds": [MEMBRANE_NS + "concept:net"]}), embedder=None)
        R.upsert(srv.efield.b, srv.efield.origin, legacy, "holds", MEMBRANE_NS + "concept:net", "register")
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        self.assertEqual(srv.entity_store.docs[legacy].properties.get("holds"), [])


if __name__ == "__main__":
    unittest.main()
