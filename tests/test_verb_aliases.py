"""VERB ALIASES AND ARGUMENTS (the operator, 2026-11-01: yes to "check PR #812 just works"; Memo
on #812: method names are noun-first -- `pr_checks`, `issue_view` -- and methods were called
with no arguments).

Pinned here:
  - a compound method name is also noted under each of its words that reads as a command verb,
    with the rest of the name as the noun it expects (`pr_checks`: verb check, noun pr); kept
    apart from exact names (`aliased`), so it maps only where the sentence's object agrees;
  - such a call goes to the method's owner with the object bound to its one free parameter
    (a PR's number, a file's path, an entity's name);
  - a method that needs more than one argument is said, not guessed;
  - class-based meanings are unchanged.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_living_language import HAVE, SID, _add, _world  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import language as LL  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import verbs as V  # noqa: E402


def _repo(srv, f, est):
    repo = _add(est, "babyseat repo")
    m = N.method_define(srv, est, f, None, repo.eid, "pr_checks", "echo checks {{num}}")
    assert m.get("ok"), m
    return repo, est.docs[m["eid"]]


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Aliases(unittest.TestCase):
    def setUp(self):
        self._r = CG._repo
        CG._repo = lambda srv: "chedim/babyseat"
        self.addCleanup(setattr, CG, "_repo", self._r)

    def test_a_compound_name_is_noted_under_its_verb_with_its_noun(self):
        srv, f, est, *_ = _world()
        repo, m = _repo(srv, f, est)
        v = est.docs[V.verb_eid("check")]
        self.assertIn({"method": m.eid, "noun": "pr"}, v.properties["aliased"])
        self.assertNotIn(m.eid, v.properties.get("implemented_by") or [])

    def test_check_pr_812_binds_the_number(self):
        srv, f, est, *_ = _world()
        repo, m = _repo(srv, f, est)
        [step] = LL.compile(srv, SID, "check PR #812")
        self.assertTrue(step["mapped"], step)
        self.assertEqual(step["calls"], [{"object": repo.eid, "method": "pr_checks", "args": {"num": "812"},
                                          "writes": True}])
        LL.seed_executor(est)
        out = LG.run(srv, code="check PR #812", lang="en")
        self.assertTrue(out.get("ok"), out)
        self.assertIn("checks 812", out.get("output") or "")

    def test_the_noun_must_agree(self):
        srv, f, est, *_ = _world()
        _repo(srv, f, est)
        [step] = LL.compile(srv, SID, "check goal one")
        self.assertFalse(step["mapped"])

    def test_a_file_binds_its_path(self):
        srv, f, est, *_ = _world()
        tool = _add(est, "linter")
        N.method_define(srv, est, f, None, tool.eid, "lint_file", "echo lint {{path}}", read_only=True)
        LL.seed_executor(est)
        out = LG.run(srv, code="lint the file membrane/a.py", lang="en")
        self.assertTrue(out.get("ok"), out)
        self.assertIn("lint membrane/a.py", out.get("output") or "")

    def test_two_arguments_are_said_not_guessed(self):
        srv, f, est, *_ = _world()
        repo = _add(est, "babyseat repo")
        N.method_define(srv, est, f, None, repo.eid, "pr_comment", "echo {{num}} {{body}}")
        [step] = LL.compile(srv, SID, "comment PR #812")
        self.assertFalse(step["mapped"])
        self.assertTrue(any("body" in u for u in step["unmapped"]), step["unmapped"])

    def test_non_verbs_are_not_aliased_and_boot_clears_stale_ones(self):
        """Live, 2026-11-01: "green it", "ci it", "ab it" read as commands, so `ci_green` and
        `arxiv_abs` made verb:green, verb:ci, verb:ab."""
        srv, f, est, *_ = _world()
        tool = _add(est, "watcher")
        N.method_define(srv, est, f, None, tool.eid, "ci_green", "echo green")
        for w in ("green", "ci", "ab"):
            self.assertIsNone((est.docs.get(V.verb_eid(w)) or type("D", (), {"properties": {}})).properties.get(
                "aliased") or None, w)
        from membrane.entities import QuantumEntity
        est.upsert(QuantumEntity(eid=V.verb_eid("bogus"), cls=V.CLASS_VERB, properties={
            "name": "bogus", "implemented_by": [], "aliased": [{"method": "membrane::gone", "noun": "x"}]}))
        V.boot(srv)
        self.assertNotIn(V.verb_eid("bogus"), est.docs)

    def test_class_meanings_are_unchanged(self):
        srv, f, est, goal, g1, g2 = _world()
        [step] = LL.compile(srv, SID, "close goal one")
        self.assertEqual(step["calls"], [{"object": g1.eid, "method": "close", "args": {}, "writes": True}])


if __name__ == "__main__":
    unittest.main()
