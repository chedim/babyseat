"""LIVING LANGUAGE (the operator, 2026-11-01: "lets implement 'living language'"; spaCy approved
as an optional dependency; "there was a design of cliffhanger that looked like prose").

Pinned here:
  - a sentence is a code tree in the language `en` (spaCy's parse), printed back exactly, and
    runnable like sh or js: exec(action='run', lang='en', code=...) on tool:language;
  - it compiles to calls: the verb (a verb entity, a phrasal one too) on its objects; an object
    is a pronoun's register, a named entity, or a class's members narrowed by adjectives;
    adjectives are Adjective entities holding a cliffhanger filter; adverbs are run options;
  - what doesn't map is said (unmapped) and nothing runs;
  - a chat message that compiles fully is told to the model as its program;
  - a pronoun whose antecedent is plain words holds that noun phrase, a Concept entity;
  - without spaCy the language says what to install and the pronouns keep their old rules.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import language as LL  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import CLASS_COMPONENT, EntityStore  # noqa: E402

HAVE = LL.available()
SID = "ses_chat"


def _add(est, name, **props):
    d = est.docs[est.add(cls=CLASS_COMPONENT, name=name, summary="x")["eid"]]
    d.properties.update(props)
    return d


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    N.seed_executor_entity(est)
    srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None,
                         "capturer": type("C", (), {"mode": "on"})()})()
    est._srv = srv
    LL.seed(est)
    goal = _add(est, "goal")
    g1 = _add(est, "goal one", parents=[goal.eid], status="open")
    g2 = _add(est, "goal two", parents=[goal.eid], status="closed")
    out = N.method_define(srv, est, f, None, goal.eid, "close", "echo closing {{self.name}}")
    assert out.get("ok"), out
    return srv, f, est, goal, g1, g2


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class TheTree(unittest.TestCase):
    def test_a_sentence_prints_back_exactly(self):
        for s in ("close the open goals quietly", "Please merge it, then restart the seat.",
                  "on field ui redesign -- yes, park that until later"):
            self.assertEqual(LG.print_(LL.parse(s)), s)


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Compiling(unittest.TestCase):
    def test_a_class_narrowed_by_an_adjective(self):
        srv, f, est, goal, g1, g2 = _world()
        [step] = LL.compile(srv, SID, "close the open goals")
        self.assertEqual(step["verb"], "close")
        self.assertEqual(step["targets"], [g1.eid])
        self.assertTrue(step["mapped"])

    def test_a_pronoun_reads_its_register(self):
        srv, f, est, goal, g1, g2 = _world()
        CG._load(srv, SID, "it", [g2.eid])
        [step] = LL.compile(srv, SID, "close it")
        self.assertEqual(step["targets"], [g2.eid])

    def test_a_named_entity(self):
        srv, f, est, goal, g1, g2 = _world()
        [step] = LL.compile(srv, SID, "close goal one")
        self.assertEqual(step["targets"], [g1.eid])

    def test_adverbs_are_options(self):
        srv, f, est, goal, g1, g2 = _world()
        [step] = LL.compile(srv, SID, "close the open goals quietly")
        self.assertEqual(step["options"], {"quiet": True})

    def test_what_doesnt_map_is_said(self):
        srv, f, est, goal, g1, g2 = _world()
        [step] = LL.compile(srv, SID, "frobnicate the goals")
        self.assertFalse(step["mapped"])
        self.assertIn("frobnicate", step["unmapped"])

    def test_two_clauses_are_two_steps(self):
        srv, f, est, goal, g1, g2 = _world()
        steps = LL.compile(srv, SID, "close goal one and close goal two")
        self.assertEqual([s["targets"] for s in steps], [[g1.eid], [g2.eid]])

    def test_an_adjective_is_an_entity_anyone_can_define(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.define_adjective(est, "finished", "x.status == 'closed'")
        [step] = LL.compile(srv, SID, "close the finished goals")
        self.assertEqual(step["targets"], [g2.eid])


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Running(unittest.TestCase):
    def test_exec_run_lang_en(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        out = LG.run(srv, code="close the open goals", lang="en")
        self.assertTrue(out.get("ok"), out)
        self.assertIn("goal one", out.get("output") or "")

    def test_an_unmapped_sentence_runs_nothing(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        out = LG.run(srv, code="frobnicate the goals", lang="en")
        self.assertFalse(out.get("ok"))
        self.assertIn("frobnicate", out.get("output") or out.get("error") or "")


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class MemosReview(unittest.TestCase):
    """Memo on #812: "compiles" must mean it can run; one sentence must not change many things
    unasked; only the owner's chat gets a program; Home Assistant installs nothing; "closed"
    must match fulfilled goals."""

    def test_a_verb_nothing_implements_for_the_noun_does_not_compile(self):
        srv, f, est, goal, g1, g2 = _world()
        other = _add(est, "pull request")
        N.method_define(srv, est, f, None, other.eid, "list", "echo listing")   # a verb, but not on goals
        [step] = LL.compile(srv, SID, "list the open goals")
        self.assertFalse(step["mapped"])

    def test_a_write_on_many_things_needs_all(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        g2.properties["status"] = "open"
        out = LG.run(srv, code="close the open goals", lang="en")
        self.assertFalse(out.get("ok"))
        self.assertIn("all", out.get("output") or "")
        out = LG.run(srv, code="close all the open goals", lang="en")
        self.assertTrue(out.get("ok"), out)
        self.assertIn("goal two", out.get("output") or "")

    def test_a_read_on_many_things_needs_nothing(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        g2.properties["status"] = "open"
        N.method_define(srv, est, f, None, goal.eid, "show", "echo showing {{self.name}}", read_only=True)
        self.assertTrue(LG.run(srv, code="show the open goals", lang="en").get("ok"))

    def test_an_older_boots_seed_is_upgraded_and_an_edit_is_kept(self):
        srv, f, est, goal, g1, g2 = _world()
        d = est.docs[LL._adj_eid("closed")]
        d.properties = {"name": "closed", "filter": "x.status == 'closed'"}       # #812's first boot
        LL.seed(est)
        self.assertIn("fulfilled", est.docs[LL._adj_eid("closed")].properties["filter"])
        LL.define_adjective(est, "closed", "x.status == 'shut'")                 # someone's edit
        LL.seed(est)
        self.assertEqual(est.docs[LL._adj_eid("closed")].properties["filter"], "x.status == 'shut'")

    def test_closed_matches_fulfilled(self):
        srv, f, est, goal, g1, g2 = _world()
        g2.properties["status"] = "fulfilled"
        [step] = LL.compile(srv, SID, "close the closed goals")
        self.assertEqual(step["targets"], [g2.eid])

    def test_no_autoinstall_installs_nothing(self):
        from unittest import mock
        import importlib
        real = importlib.import_module

        def missing(name, *a, **k):
            if name == "spacy":
                raise ImportError("no spacy")
            return real(name, *a, **k)
        with mock.patch.dict(os.environ, {"MEMBRANE_NO_AUTOINSTALL": "1"}), \
                mock.patch("importlib.import_module", missing), mock.patch("subprocess.run") as run:
            self.assertFalse(LL.ensure(log=lambda m: None))
            run.assert_not_called()


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class TheChat(unittest.TestCase):
    def test_only_the_owners_chat_gets_a_program(self):
        from membrane import lanes as L
        srv, f, est, goal, g1, g2 = _world()
        srv._loop_runner = type("R", (), {"sessions": type("S", (), {"get": lambda self, s: {"owner": "guest"}})()})()
        srv._cg_turn = {SID: {}}
        srv._cg_text = {SID: "close the open goals"}
        self.assertEqual(L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False), "BANNER")

    def test_a_compiled_message_is_told_to_the_model(self):
        from membrane import lanes as L
        srv, f, est, goal, g1, g2 = _world()
        srv._cg_turn = {SID: {}}
        srv._cg_text = {SID: "close the open goals"}
        ctx = L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
        self.assertIn("compiles to: close goal one", ctx)
        self.assertNotIn("Running it", ctx)

    def test_a_plain_words_antecedent_is_a_concept(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_conversation_graph import _seat
        from test_pronoun_registers import _say
        srv, runner = _seat()
        _say(srv, runner, 1, "cards?", "Dropping membrane/a.py, membrane/b.py and membrane/c.py.")
        out = _say(srv, runner, 2, "on field ui redesign -- yes, replace both please. But I think we "
                                   "should park that until we have membrane field shape finalized?", "ok")
        eid = (out or {}).get("resolved")
        self.assertTrue(eid and eid.startswith(LL.CONCEPT))
        self.assertEqual(srv.entity_store.docs[eid].properties["name"], "field ui redesign")
        self.assertEqual(CG.register(srv, SID, "that"), eid)

    def test_a_pronoun_is_never_its_own_clauses_subject_and_agrees_in_number(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_conversation_graph import _seat
        from test_pronoun_registers import _say
        srv, runner = _seat()
        _say(srv, runner, 1, "cards?", "Dropping membrane/a.py, membrane/b.py and membrane/c.py.")
        out = _say(srv, runner, 2, "ok, pronouns should be fixed now. Claude fixed them.", "ok")
        eid = (out or {}).get("resolved")
        self.assertEqual(srv.entity_store.docs[eid].properties["name"], "pronouns")


class TurnedOff(unittest.TestCase):
    def test_membrane_language_0_turns_it_off(self):
        from unittest import mock
        old = LL._NLP.get("m")
        LL._NLP["m"] = None
        try:
            with mock.patch.dict(os.environ, {"MEMBRANE_LANGUAGE": "0"}):
                self.assertFalse(LL.enabled())
                self.assertFalse(LL.available())
                self.assertFalse(LL.ensure(log=lambda m: None))
        finally:
            LL._NLP["m"] = old


class WithoutSpacy(unittest.TestCase):
    def test_the_language_says_what_to_install(self):
        old = LL._NLP.get("m")
        LL._NLP["m"] = False
        try:
            self.assertFalse(LL.available())
            with self.assertRaises(LG.NoGrammar):
                LL.parse("close it")
        finally:
            LL._NLP["m"] = old


if __name__ == "__main__":
    unittest.main()
