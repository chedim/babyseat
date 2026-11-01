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


class MemosReview(unittest.TestCase):
    """Memo on #804: the register's content must be readable, "can you X it?" is a request, and
    a reply naming many things can't be re-asked from."""

    def test_the_register_shows_what_it_holds(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")
        p = srv.entity_store.docs[CG.register_eid(SID, "it")].properties
        self.assertEqual(p["holds"], [A])
        self.assertEqual(p["set_by"], CG.msg_eid("msg_u2"))
        self.assertTrue(p["set_ts"])

    def test_a_polite_request_is_a_command(self):
        for text in ("can you merge it?", "could you please merge it", "would you merge it"):
            srv, runner = _seat()
            _say(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
            self.assertTrue((_say(srv, runner, 2, text, "-") or {}).get("reask"), text)

    def test_many_readings_hint_at_the_replys_proposal_instead_of_asking(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "papers?", "See membrane/a.py, membrane/b.py, membrane/c.py and membrane/d.py; "
                                        "we could let surprise set the gain.")
        out = _say(srv, runner, 2, "can you benchmark it?", "running")
        self.assertFalse((out or {}).get("reask"))
        self.assertEqual(runner.posted, [])
        self.assertIn("proposed", CG.take_note(srv, SID) or "")


class MembranesOwnMessages(unittest.TestCase):
    """2026-11-01: a turn membrane started (a watch or a check event, '[watch] ...') was read
    as the person's message: its "it" resolved and its note reached the model."""

    def test_an_injected_message_is_not_resolved_and_is_not_a_person_message(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        out = _say(srv, runner, 2, "[watch] goal:x: check it", "checked")
        self.assertIsNone(out)
        self.assertIsNone(CG.take_note(srv, SID))
        self.assertEqual(srv.entity_store.docs[CG.msg_eid("msg_u2")].properties["role"], "membrane")

    def test_a_note_never_outlives_its_message(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        _say(srv, runner, 2, "delete it", "Deleted.")       # its note is never taken
        _say(srv, runner, 3, "thanks, what else?", "nothing")
        self.assertIsNone(CG.take_note(srv, SID))


class TheOwnMessageFirst(unittest.TestCase):
    """Memo, 2026-11-01: "on field ui redesign -- yes, replace both please. But I think we should
    park that until ..." -- "that" is the redesign named in the same message, not what the
    reply before proposed."""

    def test_the_persons_own_message_comes_first(self):
        from membrane.entities import MEMBRANE_NS, QuantumEntity
        srv, runner = _seat()
        srv.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:ui", cls="Goal",
                                              properties={"name": "membrane/ui.py", "status": "open"}))
        _say(srv, runner, 1, "cards?", "Dropping membrane/a.py, membrane/b.py, membrane/c.py and membrane/d.py.")
        out = _say(srv, runner, 2, "on membrane/ui.py -- yes. But we should park that until later", "parked")
        self.assertEqual((out or {}).get("resolved"), "f:membrane/ui.py")
        self.assertNotIn("proposed", CG.take_note(srv, SID) or "")

    def test_a_demonstrative_before_a_noun_or_a_clause_is_not_a_pronoun(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
        for text in ("I think that we should wait", "merge this PR", "is that the one we wanted?"):
            self.assertIsNone(_say(srv, runner, 2, text, "-"), text)

    def test_a_demonstrative_as_an_object_is(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        self.assertEqual((_say(srv, runner, 2, "park that until later", "ok") or {}).get("resolved"), A)


class TheCaptureBinding(unittest.TestCase):
    """2026-11-01: the seat's old lexical binder rewrote Memo's "can you benchmark it?" as
    "... benchmark explain?". A chat turn the graph looked at binds by the graph's reading."""

    def _seat(self, seen):
        import tempfile
        import types
        from membrane.seat import Seat
        d = Path(tempfile.mkdtemp())
        s = Seat(d / "field.json", d / "audit.jsonl", embedder=None)
        s.server_ref = types.SimpleNamespace(_cg_turn=seen)
        s.remember("the explain function is slow")
        return s

    def _body(self, text, sid=None):
        b = {"messages": [{"role": "user", "content": text}]}
        if sid:
            b["_turn_sid"] = sid
        return b

    def test_the_graphs_reading_is_the_binding(self):
        s = self._seat({"ses": {"resolved": A, "word": "it", "label": "membrane/a.py"}})
        t = s.turn(self._body("can you benchmark it?", "ses"))
        self.assertEqual(t.query, "can you benchmark membrane/a.py?")

    def test_the_graph_found_nothing_so_nothing_is_rewritten(self):
        s = self._seat({"ses": {}})
        t = s.turn(self._body("can you benchmark it?", "ses"))
        self.assertEqual(t.query, "can you benchmark it?")
        self.assertTrue(t.binding_failed)

    def test_another_client_keeps_the_lexical_binder(self):
        s = self._seat({})
        t = s.turn(self._body("turn it off"))
        self.assertNotEqual(t.query, "turn it off")


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
        _say(srv, runner, 1, "status?", "PR #803 is ready.")
        out = _say(srv, runner, 2, "merge it", "merging")
        self.assertEqual((out or {}).get("resolved"), PR)

    def test_an_event_number_is_not_an_issue(self):
        srv, runner = _seat()
        _say(srv, runner, 1, "where?", "this chat's log (#47785) says so; see #12 too")
        self.assertFalse([o for r, o in _out(srv, CG.msg_eid("msg_a1")) if "issues/" in o])

    def test_no_repo_no_number_entities(self):
        srv, runner = _seat()
        CG._repo = lambda srv: None
        _say(srv, runner, 1, "status?", "PR #803 is ready.")
        self.assertNotIn(("refers-to", PR), _out(srv, CG.msg_eid("msg_a1")))


if __name__ == "__main__":
    unittest.main()
