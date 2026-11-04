"""#932: ENTITIES ARE EVERYTHING (the operator, 2026-11-04: "ideas, things, knowledge"), not only
files, commands and tools. Every noun phrase a message says links -refers-> to what it names:
an entity the field has (by name, a file, a pronoun's register), else its Concept, made on first
use -- so the same thing said in two sessions is one entity both messages link to.

Pinned here:
  - a person's things become Concepts the message refers to ("my bedside lamp" -> bedside lamp);
  - the same thing in a later session is the same entity ("projects" / "project" too);
  - a named field entity is linked as itself, never as a Concept of its name;
  - the words of a path, a command or a PR number are that mention's, never phrases of their own;
  - a bare pronoun nothing holds names no thing (no Concept "i" / "you");
  - a class word links to its Concept, not to every member of the class;
  - "delete it" still reads only the field's own entities from the reply before, so a reply that
    names many things does not turn every pronoun into a re-ask.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import convgraph as CG  # noqa: E402
from membrane import language as LL  # noqa: E402
from membrane.entities import CLASS_CONCEPT, MEMBRANE_NS, QuantumEntity  # noqa: E402
from test_conversation_graph import SID, _out, _seat, _turn  # noqa: E402


def _things(srv, eid):
    docs = srv.entity_store.docs
    return {(docs[o].properties or {}).get("name") if o in docs else o
            for rel, o in _out(srv, eid) if rel == "refers-to"}


@unittest.skipUnless(LL.available(), "living language needs spaCy")
class ThingsAreEntities(unittest.TestCase):
    def test_a_persons_things_become_concepts_the_message_refers_to(self):
        srv, runner = _seat()
        u, _ = _turn(srv, runner, 1, "I replaced the bulb in my bedside lamp with a Philips LED bulb.", "Nice.")
        names = _things(srv, u)
        self.assertIn("bedside lamp", names)
        self.assertIn("philips led bulb", names)
        for rel, o in _out(srv, u):
            if rel == "refers-to" and o.startswith(LL.CONCEPT):
                self.assertEqual(srv.entity_store.docs[o].cls, CLASS_CONCEPT)

    def test_the_same_thing_later_is_the_same_entity(self):
        srv, runner = _seat()
        u1, _ = _turn(srv, runner, 1, "I led two projects at work this spring.", "Great.")
        u2, _ = _turn(srv, runner, 2, "The second project shipped yesterday.", "Congrats.")
        shared = {o for rel, o in _out(srv, u1) if rel == "refers-to"} & \
                 {o for rel, o in _out(srv, u2) if rel == "refers-to"}
        self.assertIn(LL.concept(srv.entity_store, "project"), shared)

    def test_a_named_entity_is_linked_as_itself(self):
        srv, runner = _seat()
        goal = MEMBRANE_NS + "goal:recall"
        srv.entity_store.upsert(QuantumEntity(eid=goal, cls="Goal", properties={"name": "recall check"}))
        u, _ = _turn(srv, runner, 1, "how is the recall check going?", "Running.")
        refs = {o for rel, o in _out(srv, u) if rel == "refers-to"}
        self.assertIn(goal, refs)
        self.assertNotIn(LL.CONCEPT + "recall-check", refs)

    def test_a_paths_words_are_the_files_not_phrases(self):
        srv, runner = _seat()
        _, a = _turn(srv, runner, 1, "look", "I changed membrane/a.py and merged PR #803.")
        refs = {o for rel, o in _out(srv, a) if rel == "refers-to"}
        self.assertIn("f:membrane/a.py", refs)
        self.assertFalse({o for o in refs if o.startswith(LL.CONCEPT + "membrane") or o.endswith("~a.py")})

    def test_a_bare_pronoun_names_no_thing(self):
        srv, runner = _seat()
        u, _ = _turn(srv, runner, 1, "I think you know it.", "I do.")
        self.assertFalse({n for n in _things(srv, u) if n in ("i", "you", "it")})

    def test_a_class_word_links_its_concept_not_every_member(self):
        srv, runner = _seat()
        for n in ("alpha", "beta", "gamma"):
            srv.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + f"goal:{n}", cls="Goal",
                                                  properties={"name": n}))
        u, _ = _turn(srv, runner, 1, "the goals look fine to me", "ok")
        refs = {o for rel, o in _out(srv, u) if rel == "refers-to"}
        self.assertIn(LL.concept(srv.entity_store, "goal"), refs)
        self.assertFalse({MEMBRANE_NS + f"goal:{n}" for n in ("alpha", "beta", "gamma")} & refs)

    def test_it_still_reads_only_the_fields_entities_from_the_reply(self):
        srv, runner = _seat()
        est = srv.entity_store
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:beta", cls="Goal", properties={"name": "beta", "status": "open"}))
        _, a = _turn(srv, runner, 1, "look",
                     "The lamp, the bulb, the shelf, the desk and the chair are fine; beta is open.")
        self.assertIn(MEMBRANE_NS + "goal:beta", {o for rel, o in _out(srv, a) if rel == "refers-to"})
        runner.messages.add(SID, "msg_u2", "user", "close it")
        out = CG.before_turn(srv, runner, SID, {"id": "msg_u2"}, "close it")
        self.assertFalse(out and (out.get("reask") or out.get("hint")))
        self.assertIn(("refers-to", MEMBRANE_NS + "goal:beta"), _out(srv, CG.msg_eid("msg_u2")))


if __name__ == "__main__":
    unittest.main()
