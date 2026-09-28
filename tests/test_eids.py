"""membrane/eids.py: canonical ids, mentions (specific > phrase > word),
legacy id mapping, and the hash-random entity/role keys."""

import unittest

import numpy as np

from membrane import eids, vsa
from membrane.eids import canon, legacy_to_canon, mentions_of

GRAPH = {"root": "/repo", "files": {"membrane/opencode.py": {}, "membrane/router.py": {}},
         "defs": {"membrane.opencode._pick_listen_port": {"qual": "_pick_listen_port"},
                  "membrane.opencode.main": {"qual": "main"},
                  "membrane.cli.main": {"qual": "main"}},
         "short_index": {"_pick_listen_port": ["membrane.opencode._pick_listen_port"],
                         "main": ["membrane.opencode.main", "membrane.cli.main"]}}


class TestCanon(unittest.TestCase):
    def test_words_and_phrases(self):
        self.assertEqual(canon("w", "Tests"), "w:test")
        self.assertEqual(canon("w", "class"), "w:class")
        self.assertEqual(canon("p", "Receipt Lanes"), "p:receipt lane")

    def test_files_resolve_through_the_graph(self):
        self.assertEqual(canon("f", "opencode.py", GRAPH), "f:membrane/opencode.py")
        self.assertEqual(canon("f", "/repo/membrane/router.py", GRAPH), "f:membrane/router.py")
        self.assertEqual(canon("f", "unknown.py", GRAPH), "f:~unknown.py")
        self.assertEqual(canon("f", "src/app.ts"), "f:src/app.ts")

    def test_symbols_resolve_only_when_unique(self):
        self.assertEqual(canon("s", "_pick_listen_port", GRAPH),
                         "s:membrane.opencode._pick_listen_port")
        self.assertEqual(canon("s", "main", GRAPH), "s:?main")     # ambiguous
        self.assertEqual(canon("s", "nothing_here", GRAPH), "s:?nothing_here")


class TestMentions(unittest.TestCase):
    def test_specific_then_phrase_then_words(self):
        m = mentions_of("I fixed opencode.py: the receipt lane now uses _pick_listen_port",
                        GRAPH, phrase_vocab={"receipt lane"})
        ids = [e for e, _ in m]
        self.assertEqual(ids[:2], ["f:membrane/opencode.py",
                                   "s:membrane.opencode._pick_listen_port"])
        self.assertIn("p:receipt lane", ids)
        self.assertNotIn("w:receipt", ids)       # covered by the phrase
        self.assertNotIn("w:the", ids)           # stopword
        self.assertIn("w:fixed", ids)

    def test_mentions_are_capped(self):
        many = " ".join("".join(chr(97 + (i // 26 ** k) % 26) for k in range(4))
                        + "zz" for i in range(40))
        self.assertEqual(len(mentions_of(many)), eids.MAX_MENTIONS)


class TestLegacy(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(legacy_to_canon("membrane::files:membrane/night.py"),
                         "f:membrane/night.py")
        self.assertEqual(legacy_to_canon("membrane::Component:files:membrane/night.py"),
                         "f:membrane/night.py")
        self.assertEqual(legacy_to_canon("symbols:_pick_listen_port", GRAPH),
                         "s:membrane.opencode._pick_listen_port")
        self.assertEqual(legacy_to_canon("membrane::synth:pure-observer-default"),
                         "p:pure observer default")
        self.assertEqual(legacy_to_canon("models:glm-5.3-flash:cloud"),
                         "m:glm-5.3-flash:cloud")
        self.assertIsNone(legacy_to_canon("membrane::tl:abc123"))


class TestKeys(unittest.TestCase):
    def test_entity_keys_are_deterministic_and_quasi_orthogonal(self):
        a, a2, b = (eids.entity_key("w:router", 2048), eids.entity_key("w:router", 2048),
                    eids.entity_key("w:gate", 2048))
        self.assertTrue(np.array_equal(a, a2))
        self.assertAlmostEqual(vsa.hamming(a, b), 0.5, delta=0.05)

    def test_role_keys_are_mutually_random(self):
        r = [eids.role_key(x, 2048) for x in ("subj", "rel", "obj")]
        for i in range(3):
            for j in range(i + 1, 3):
                self.assertAlmostEqual(vsa.hamming(r[i], r[j]), 0.5, delta=0.05)

    def test_specificity(self):
        self.assertTrue(eids.is_specific("f:a.py"))
        self.assertFalse(eids.is_specific("w:router"))


if __name__ == "__main__":
    unittest.main()


class TestCommandsInProse(unittest.TestCase):
    def test_a_verb_in_prose_is_not_a_command(self):
        from membrane.eids import mentions_of

        def cmds(t):
            return [e for e, _ in mentions_of(t) if e.startswith("c:")]
        self.assertEqual(cmds("why does membrane keep picking the dumbest models"), [])
        self.assertEqual(cmds("make sure the tests pass"), [])
        self.assertEqual(cmds("run `membrane opencode --no-launch` now"), ["c:membrane"])
        self.assertEqual(cmds("use `git rm --cached` here"), ["c:git"])
        self.assertEqual(cmds("$ git status shows it"), ["c:git"])
