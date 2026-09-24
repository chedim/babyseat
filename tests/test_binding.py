"""Pronoun binding: the kettle/lamp deictic fix (membrane/binding.py).

THE FAILURE (field sim 0.9): "turn it off" for the kettle and "turn it off"
for the lamp embed identically -- the pronoun's referent lives outside the
sentence. The field dedups them or replays the wrong device's answer. The
fix is capture-time binding: rewrite before the embed + stamp the cell; the
gate escalates any replay whose binding conflicts with the turn's.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from membrane import binding
from membrane.runtime import Membrane

# ISOLATION: this shell exports MEMBRANE_STORE=couchbase; the explicit
# opt-in bypasses the lab-path guard, so a bare test Membrane joins the
# LIVE shared substrate and its learns pollute the operator's field.
os.environ["MEMBRANE_STORE"] = "json"


class _SeqEmb:
    """Bag-of-words embedder over a 64-dim hash basis: same-shape texts
    ("turn lamp on" / "turn kettle on") share most tokens -> HIGH sim, the
    property that creates the kettle/lamp collision on a real embedder.
    Digit-bearing tokens (the time-prefix stamp) are dropped so two turns
    in different seconds still match."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(64)
            for w in binding._WORD_RE.findall(t.lower()):
                if any(c.isdigit() for c in w):
                    continue
                v[int(__import__("hashlib").sha256(
                    w.encode()).digest()[:2].hex(), 16) % 64] = 1.0
            n = np.linalg.norm(v)
            out.append((v / n if n else v).tolist())
        return out


class _R:
    def respond(self, request_body, hint=None, auth=None):
        return {"choices": [{"message": {"content": "model answer"}}]}


def _mk(tmp: str, **kw) -> Membrane:
    kw.setdefault("ephemeral", False)   # never hydrate the real couchbase tier
    # bag-of-words overlap for turn-lamp-on vs turn-kettle-on is 2/3: the
    # gate must be loose enough to reach `act` (the conflict guard fires on
    # the act path), and the re-ask window must not eat the replay repeat.
    kw.setdefault("sim_min", 0.6)
    kw.setdefault("reask_sim", 1.1)     # disabled: no turn is a "re-ask"
    m = Membrane(responder=_R(), audit_path=str(Path(tmp) / "audit.jsonl"),
                 field_path=str(Path(tmp) / "field.json"), embedder=_SeqEmb(),
                 judge=None, **kw)
    # in-memory only: the cb-only save (field.py:895) requires a live
    # couchbase; the test field never persists.
    m.store.save = lambda *a, **k: None
    return m


def _body(q):
    return {"messages": [{"role": "user", "content": q}]}


class TestBindingUnit(unittest.TestCase):
    def test_substitute_rewrites_first_pronoun(self):
        self.assertEqual(
            binding.substitute("turn it off", {"pronoun": "it",
                                               "referent": "lamp"}),
            "turn lamp off")
        self.assertEqual(
            binding.substitute("Turn it off please",
                               {"pronoun": "it", "referent": "lamp"}),
            "Turn lamp off please")

    def test_substitute_drops_reminders(self):
        q = 'turn it off <system-reminder>x</system-reminder>'
        self.assertEqual(
            binding.substitute(q, {"pronoun": "it", "referent": "lamp"}).strip(),
            "turn lamp off")

    def test_resolve_kettle_not_assistant(self):
        # the prepositional tail is not the referent: the object of the
        # clause before "to" is
        b = binding.resolve("turn it on", ["add a kettle to home assistant"])
        self.assertEqual(b, {"pronoun": "it", "referent": "kettle"})

    def test_resolve_skips_verb_only_turns(self):
        # "run it" has no noun candidate; the walk falls to the older turn
        b = binding.resolve("it", ["run it", "add a kettle to home assistant"])
        self.assertEqual(b, {"pronoun": "it", "referent": "kettle"})

    def test_resolve_none_when_no_antecedent(self):
        self.assertIsNone(binding.resolve("turn it on", ["yes okay"]))
        self.assertIsNone(binding.resolve("turn lamp on", ["the kettle"]))

    def test_conflict_rules(self):
        lamp = {"pronoun": "it", "referent": "lamp"}
        self.assertTrue(binding.conflict("turn it off",
                                         {"pronoun": "it",
                                          "referent": "kettle"}, lamp))
        self.assertFalse(binding.conflict("turn it off",
                                          {"pronoun": "it",
                                           "referent": "lamp"}, lamp))
        # unbound cell: geometry decides (legacy rows never conflict)
        self.assertFalse(binding.conflict("turn it off",
                                          {"pronoun": "it",
                                           "referent": "kettle"}, None))
        # explicit query: never conflicts
        self.assertFalse(binding.conflict("turn the lamp off", None, lamp))
        # unresolved pronoun: a replay would be a guess -> conflict
        self.assertTrue(binding.conflict("turn it off", None, lamp))

    def test_stamp(self):
        extra = {}
        binding.stamp(extra, {"binding": None, "query_raw": "x"})
        self.assertEqual(extra, {})          # no binding -> legacy shape
        binding.stamp(extra, {"binding": {"pronoun": "it", "referent": "lamp"},
                              "query_raw": "turn it off"})
        self.assertEqual(extra["bind"], {"pronoun": "it", "referent": "lamp"})
        self.assertEqual(extra["sit_raw"], "turn it off")


class TestGateIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_bound_query_embeds_bound_text(self):
        m = _mk(self.tmp)
        v, vec0, _, _ = m.gate_turn(_body("add a kettle to home assistant"))
        import numpy as _np
        m._remember("add a kettle to home assistant", _np.asarray(vec0),
                    {"verdict": "x",
                     "query_raw": "add a kettle to home assistant"}, None)
        v2, vec, q, _ = m.gate_turn(_body("turn it on"))
        self.assertEqual(q, "turn kettle on")     # the gate returns the BOUND text
        self.assertEqual([round(x, 6) for x in vec],
                         [round(x, 6) for x in m.embedder.embed(
                             [m._time_prefix() + "turn kettle on"])[0]])

    def test_conflicting_cell_never_replays(self):
        m = _mk(self.tmp)
        # learn the lamp answer as a SAFE canon cell bound to "lamp"
        m._bind_ctx.append("the lamp")
        body_lamp = _body("turn it on")
        verdict, vec, q, _ = m.gate_turn(body_lamp)
        m.handle(body_lamp)                       # learns "model answer"
        m.store.set_safe(0, True)
        # NOW a same-shaped turn bound to a DIFFERENT device: the stored
        # cell's binding conflicts -> escalate-with-hint, never replay.
        m._bind_ctx.clear()
        m._bind_ctx.append("the kettle")
        v, _, q2, _ = m.gate_turn(_body("turn it on"))
        self.assertEqual(q2, "turn kettle on")
        # the guard closes EVERY replay lane: plain escalate, no action, no
        # hint, no near_idx (opencode serves near_idx on a plain escalate).
        self.assertEqual(v["verdict"], "escalate")
        self.assertIn("binding conflict", v["reason"])
        self.assertIsNone(v["ex_idx"])
        self.assertIsNone(v.get("near_idx"))

    def test_same_referent_still_replays(self):
        m = _mk(self.tmp)
        m._bind_ctx.append("the lamp")
        body = _body("turn it on")
        v, *_ = m.gate_turn(body)
        m.handle(body)
        m.store.set_safe(0, True)
        # same binding again: the replay path is untouched
        m._remember("turn it on",
                    m.embedder.embed(["turn lamp on"])[0], v, 0)
        m._bind_ctx.clear()
        m._bind_ctx.append("the lamp")
        v2, _, _, _ = m.gate_turn(_body("turn it on"))
        # same binding: the sketch near-dup lane keeps the servable cell
        # (escalate-with-hint + ex_idx, no conflict) -- the replay layer in
        # opencode serves it; the guard is silent.
        self.assertEqual(v2["verdict"], "escalate-with-hint")
        self.assertEqual(v2.get("ex_idx"), 0)
        self.assertNotIn("binding", str(v2.get("reason")))

    def test_unbound_cell_still_replays_for_pronoun_query(self):
        # legacy row (no extra["bind"]): geometry decides, no conflict guard
        m = _mk(self.tmp, pronoun_bind=True)
        m.store.append(
            __import__("membrane.field", fromlist=["Exemplar"]).Exemplar(
                situation="turn the device on", action="stored answer",
                outcome="confirmed", vec=[1.0] + [0.0] * 15, safe=True))
        v, _, _, _ = m.gate_turn(_body("turn it on"))
        self.assertNotIn("binding conflict", str(v.get("reason")))
        self.assertNotEqual(v["verdict"], "escalate-with-hint")

    def test_bind_ctx_feeds_from_raw_user_words(self):
        m = _mk(self.tmp)
        m._remember("raw", np.zeros(16),
                    {"verdict": "x", "query_raw": "raw"}, None)
        self.assertEqual(list(m._bind_ctx), ["raw"])


class TestLearnStamp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_learned_cell_carries_bind(self):
        m = _mk(self.tmp)
        m._bind_ctx.append("add a kettle to home assistant")
        resp, _ = m.handle(_body("turn it on"))
        self.assertEqual(resp["choices"][0]["message"]["content"],
                         "model answer")
        ex = m.store.exemplars[0]
        self.assertEqual(ex.situation, "turn kettle on")
        self.assertEqual(ex.extra.get("bind"),
                         {"pronoun": "it", "referent": "kettle"})
        self.assertEqual(ex.extra.get("sit_raw"), "turn it on")

    def test_wire_roundtrip_keeps_bind(self):
        # extra keys spread as top-level wire keys -> couchbase/older readers
        m = _mk(self.tmp)
        m._bind_ctx.append("the lamp")
        m.handle(_body("turn it on"))
        rec = json.loads(json.dumps(m.store.exemplars[0].extra))
        self.assertEqual(rec["bind"]["referent"], "lamp")


if __name__ == "__main__":
    unittest.main()