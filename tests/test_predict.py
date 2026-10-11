"""MEMBRANE PREDICTS ARGUS (2026-10-10): before each turn a guess of the
tools and entities Argus will use; scored against what he did; learned
from. A guess is never executed and never reaches his context."""
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import predict as P  # noqa: E402


def vec(seed):
    v = np.random.default_rng(seed).standard_normal(64).astype(np.float32)
    return v / np.linalg.norm(v)


def tool(name, **inp):
    return {"type": "tool", "tool": name, "state": {"status": "completed", "input": inp}}


class TestWhatArgusDid(unittest.TestCase):
    def test_a_turns_tool_parts_become_actions_and_entities(self):
        tools, ents = P.actions_of_parts([
            {"type": "text", "text": "hi"},
            tool("entity/action-read", key="membrane::Component:ha:light.cabin"),
            tool("exec", action="call", object="ha:light.cabin", method="status"),
            tool("find", action="memory", q="what about the cabin light"),
            tool("entity_read", spec="m:mqtt:bridge")])
        self.assertEqual(tools, ["entity.read", "exec.call", "find.memory"])
        self.assertEqual(ents, ["ha:light.cabin", "mqtt:bridge"])


class TestTheGuessLearns(unittest.TestCase):
    def test_the_nearest_past_turns_vote_and_the_score_counts(self):
        p = P.Predictor()
        q_light, q_bms = vec(1), vec(2)
        for _ in range(3):
            p.score({"tools": [], "ents": []}, q_light + 0.05 * vec(9), "operator", ["entity.read"], ["ha:light.cabin"])
            p.score({"tools": [], "ents": []}, q_bms + 0.05 * vec(8), "operator", ["exec.call"], ["c:bms.cells"])
        g = p.guess(q_light, "operator")
        self.assertEqual(g["tools"][0][0], "entity.read")
        self.assertEqual(g["ents"][0][0], "ha:light.cabin")
        r = p.score(g, q_light, "operator", ["entity.read"], ["ha:light.cabin"])
        self.assertTrue(r["tool1"] and r["ent3"])
        g2 = p.guess(q_bms, "operator")
        self.assertEqual(g2["tools"][0][0], "exec.call")
        rep = p.report()["operator"]
        self.assertEqual(rep["turns"], 7)
        self.assertEqual(rep["last50"]["tool_top1"], round(1 / 7, 3))     # only the scored guess hit

    def test_a_turn_without_tools_is_predicted_too(self):
        p = P.Predictor()
        for i in range(3):
            p.score({"tools": [], "ents": []}, vec(5) + 0.05 * vec(i + 20), "operator", [], [])
        self.assertEqual(p.guess(vec(5), "operator")["tools"][0][0], P.NONE)
        self.assertTrue(p.score(p.guess(vec(5), "operator"), vec(5), "operator", [], [])["tool1"])

    def test_the_sources_that_are_right_get_louder(self):
        p = P.Predictor()
        for _ in range(10):
            g = p.guess(None, "listener", named=["ha:light.cabin"], near=[("w:noise", 0.9)])
            p.score(g, None, "listener", ["entity.read"], ["ha:light.cabin"])
        self.assertGreater(p.report()["sources"]["named"], p.report()["sources"]["near"])
        g = p.guess(None, "listener", named=["ha:light.cabin"], near=[("w:noise", 0.99)])
        self.assertEqual(g["ents"][0][0], "ha:light.cabin")

    def test_it_survives_a_restart(self):
        d = tempfile.mkdtemp()
        p = P.Predictor(d)
        for i in range(10):
            p.score({"tools": [], "ents": []}, vec(3), "operator", ["goal.list"], [])
        p2 = P.Predictor(d)
        self.assertEqual(p2.report()["operator"]["turns"], 10)
        self.assertEqual(p2.guess(vec(3), "operator")["tools"][0][0], "goal.list")


class TestTheSeatWiring(unittest.TestCase):
    def test_start_and_end_pair_up_per_session_and_nothing_runs(self):
        emb = types.SimpleNamespace(embed=lambda xs: [vec(7) for _ in xs])
        srv = types.SimpleNamespace(membrane=types.SimpleNamespace(embedder=emb), _models_path=None)
        runner = types.SimpleNamespace(srv=srv)
        P.start_async(runner, "s1", "turn on the cabin light", "operator")
        r = P.end(runner, "s1", [tool("entity_read", spec="ha:light.cabin")])
        self.assertEqual(r["did"], {"tools": ["entity.read"], "ents": ["ha:light.cabin"]})
        self.assertIsNone(P.end(runner, "s1", []))                  # once per turn
        self.assertEqual(srv._predictor.report()["operator"]["turns"], 1)


if __name__ == "__main__":
    unittest.main()
