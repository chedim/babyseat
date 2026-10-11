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
        self.assertEqual(p.guess(vec(5), "operator")["tools"][0][0], P.NONE)    # '(reply)': a plain answer
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


class TestEveryMove(unittest.TestCase):
    """2026-10-11: every model round is a move -- guessed before it, scored
    by its own response; what follows the previous action is learned."""

    def _runner(self):
        emb = types.SimpleNamespace(embed=lambda xs: [vec(len(x) % 5) for x in xs])
        srv = types.SimpleNamespace(membrane=types.SimpleNamespace(embedder=emb), _models_path=None)
        return types.SimpleNamespace(srv=srv)

    def _call(self, name, **args):
        import json
        return {"function": {"name": name, "arguments": json.dumps(args)}}

    def test_each_round_is_scored_and_the_turn_sums_its_moves(self):
        r = self._runner()
        moves = [[self._call("find", action="memory", q="cabin")],
                 [self._call("entity", action="read", spec="ha:light.cabin")],
                 []]
        for _ in range(4):                                    # the same three-move turn, four times
            for i, tcs in enumerate(moves, 1):
                P.step_start(r, "s1", f"context {i}", "listener", step=i)
                P.step_end(r, "s1", tcs, "done" if not tcs else "", step=i, wait=True)
            summary = P.end(r, "s1")
        self.assertEqual(summary["score"]["moves"], 3)
        self.assertEqual(summary["score"]["top1"], 3)         # by now it knows the whole dance
        self.assertEqual(summary["did"]["tools"], ["find.memory", "entity.read", P.TEXT])
        rep = r.srv._predictor.report()["listener"]
        self.assertEqual(rep["turns"], 12)                    # every move counted

    def test_the_previous_action_predicts_the_next(self):
        p = P.Predictor()
        for _ in range(6):
            p.score({"tools": [], "ents": []}, None, "operator", ["entity.read"], [], prev="find.memory")
            p.score({"tools": [], "ents": []}, None, "operator", ["exec.call"], [], prev="entity.read")
        self.assertEqual(p.guess(None, "operator", prev="find.memory")["tools"][0][0], "entity.read")
        self.assertEqual(p.guess(None, "operator", prev="entity.read")["tools"][0][0], "exec.call")

    def test_what_argus_is_looking_at(self):
        h = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "", "tool_calls": []},
             {"role": "tool", "content": "light is on"}, {"role": "tool", "content": "bms ok"}]
        self.assertEqual(P.context_of(h, "hi"), "light is on\nbms ok")
        self.assertEqual(P.context_of(h[:1], "hi"), "hi")


class TestKindFirst(unittest.TestCase):
    """2026-10-11, the operator: "it always predicts exec :D -- punish it
    severely when it predicts a tool instead of text and vice-versa"."""

    def test_a_kind_miss_teaches_five_times_harder(self):
        p = P.Predictor()
        for _ in range(12):                                   # exec everywhere: the majority to collapse onto
            p.score({"tools": [], "ents": []}, None, "operator", ["exec.call"], [], prev="find.memory")
        for i in range(3):                                    # but after a tool result he writes
            g = p.guess(None, "operator", prev="exec.call")
            r = p.score(g, None, "operator", [P.TEXT], [], prev="exec.call")
            if i == 0:
                self.assertFalse(r["kind"])                   # the first guess: the collapse
        g = p.guess(None, "operator", prev="exec.call")
        self.assertEqual(g["kind"], "text")
        self.assertEqual(g["tools"][0][0], P.TEXT)
        self.assertEqual(p.guess(None, "operator", prev="find.memory")["tools"][0][0], "exec.call")
        self.assertIn("kind", p.report()["operator"])

    def test_the_kinds_survive_a_restart(self):
        d = tempfile.mkdtemp()
        p = P.Predictor(d)
        for _ in range(10):
            p.score({"tools": [["exec.call", 1]], "ents": [], "kind": "tool"}, None, "operator", [P.TEXT], [], prev="x")
        p.save()
        self.assertEqual(P.Predictor(d).guess(None, "operator", prev="x")["kind"], "text")


class TestInsideTheStep(unittest.TestCase):
    """2026-10-11, the operator: every text message and every tool call is a
    move -- inside one model round too, each predicted from the ones before."""

    def _call(self, name, **args):
        import json
        return {"function": {"name": name, "arguments": json.dumps(args)}}

    def test_a_round_is_its_moves_in_order(self):
        mv = P.moves_of([self._call("find", action="memory", q="x"),
                         self._call("entity", action="read", spec="ha:light.cabin")], "Checking the cabin light.")
        self.assertEqual([m["action"] for m in mv], [P.TEXT, "find.memory", "entity.read"])
        self.assertEqual(mv[2]["ents"], ["ha:light.cabin"])
        self.assertEqual(P.moves_of([], ""), [])

    def test_each_move_inside_a_round_is_guessed_from_the_ones_before(self):
        emb = types.SimpleNamespace(embed=lambda xs: [vec(1) for _ in xs])
        srv = types.SimpleNamespace(membrane=types.SimpleNamespace(embedder=emb), _models_path=None)
        r = types.SimpleNamespace(srv=srv)
        tcs = [self._call("find", action="memory", q="x"), self._call("entity", action="read", spec="ha:light.cabin"),
               self._call("exec", action="call", object="ha:light.cabin", method="status")]
        for _ in range(4):                                    # the same round, four turns
            P.step_start(r, "s1", "the cabin light?", "operator", step=1)
            res = P.step_end(r, "s1", tcs, "Looking.", step=1, wait=True)
            P.end(r, "s1")
        self.assertEqual(len(res), 4)                         # text + three calls: four moves in one round
        self.assertEqual([x["tool1"] for x in res], [True, True, True, True])
        self.assertEqual(srv._predictor.report()["operator"]["turns"], 16)

    def test_every_move_leaves_a_phantom_in_its_place(self):
        emb = types.SimpleNamespace(embed=lambda xs: [vec(1) for _ in xs])
        srv = types.SimpleNamespace(membrane=types.SimpleNamespace(embedder=emb), _models_path=None)
        seen = []
        r = types.SimpleNamespace(srv=srv, hub=types.SimpleNamespace(publish=seen.append))
        tcs = [self._call("entity", action="read", spec="ha:light.cabin"), self._call("find", action="memory", q="x")]
        for t in range(3):
            P.step_start(r, "s1", "the cabin light?", "operator", step=1, mid=f"msg_{t}")
            P.step_end(r, "s1", tcs, "", step=1, wait=True, mid=f"msg_{t}")
            P.step_start(r, "s1", "results", "operator", step=2, mid=f"msg_{t}")
            P.step_end(r, "s1", [self._call("goal", action="list")], "", step=2, wait=True, mid=f"msg_{t}")
            P.end(r, "s1")
        last = {i["id"]: i for i in P.phantoms(r, "s1") if i["mid"] == "msg_2"}
        self.assertEqual(sorted(i["n"] for i in last.values()), [0, 1, 2])      # three moves, three places
        self.assertTrue(all(not i["pending"] for i in last.values()))
        self.assertEqual(last["msg_2:0"]["label"], "entity.read ha:light.cabin")
        self.assertTrue(last["msg_2:0"]["hit"] and last["msg_2:1"]["hit"] and last["msg_2:2"]["hit"])
        self.assertTrue(all(e["type"] == "membrane.predict" for e in seen))


if __name__ == "__main__":
    unittest.main()
