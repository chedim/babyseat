"""THE ACTOR LAYER (the operator's 2026-10-02 design, red first):
membrane is the model's only I/O; the conversation's actors are
ENTITIES, the messages are ENTITIES, and every response carries its
ROUTE back to where it came from.

Pinned here:
1. THE OPERATOR ACTOR: planted once (idempotent); updated by BOTH
   event roles (a user arrival AND the model's response alike --
   symmetric, no crutch); the facts stamp + the position EMA toward
   the event's vec; zero embed calls (the vec rides).
2. THE MSG ENTITIES: one per event (kind 'msg'), parented (the
   operator / the endpoint / the watch-loop actor), carrying
   {text_head, ts, vec, session, role}; the RESPONSE side carries
   reply_to (the msg it answers) + via (the endpoint entity) +
   route_reason + model -- THE ROUTE-BACK METADATA (the answer
   attaches where the question lives).
3. THE ENDPOINT ACTORS: plant_actor(endpoint=...) -- one per LLM
   endpoint (ollama + each cloud rung); model responses attach to
   the endpoint that spoke (via=); presence-published.
4. THE WATCH-LOOP ACTOR: the loops' verdicts mint msg children
   under a 'watch' actor (kind watch_agent) -- the loops' messages
   are field entities too.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


class _Emb:
    model = "fake-1"

    def embed(self, texts):
        out = []
        for t in texts:
            import hashlib
            h = hashlib.sha256(t.encode()).digest()
            v = np.zeros(8)
            for i in range(min(len(h), 8)):
                v[i] = h[i] / 255 - 0.5
            out.append(v)
        return out


def _est():
    return EntityStore(bucket="t-actors", shared=False)


class TestOperatorActor(unittest.TestCase):
    def test_plant_idempotent_and_updated_by_both_roles(self):
        """THE SYMMETRIC LANE: the operator entity exists after the
        first plant; a USER arrival and a MODEL response both update
        it (the facts stamp counts BOTH; the last_role flips)."""
        est = _est()
        self.assertTrue(WA.plant_operator(est))
        self.assertTrue(WA.plant_operator(est))          # idempotent
        r1 = WA.actor_on_event(est, role="user", text="boop?",
                               vec=[1.0] + [0.0] * 7)
        r2 = WA.actor_on_event(est, role="model", text="the gateway "
                               "answers", vec=[0.0, 1.0] + [0.0] * 6,
                               via="endpoint:ollama")
        op = est.docs.get(WA.OPERATOR_EID)
        self.assertEqual(int((op.properties or {}).get("messages") or 0), 2)
        self.assertEqual((op.properties or {}).get("last_role"), "model")
        self.assertGreater(r2.get("ts", 0), 0)

    def test_position_ema_drifts_toward_event(self):
        est = _est()
        WA.plant_operator(est)
        WA.actor_on_event(est, role="user", text="first",
                          vec=[1.0] + [0.0] * 7)
        WA.actor_on_event(est, role="model", text="second",
                          vec=[0.0, 1.0] + [0.0] * 6)
        op = est.docs.get(WA.OPERATOR_EID)
        pos = np.asarray(op.position, dtype=float)
        self.assertEqual(pos.shape, (8,))
        self.assertGreater(float(np.dot(EF_unit(pos),
                                        np.asarray([0.0, 1.0] + [0.0] * 6))),
                           0.5, "the EMA leans toward the newest event")


def EF_unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


class TestMsgEntities(unittest.TestCase):
    def test_user_message_mints_msg_child_of_operator(self):
        est = _est()
        WA.plant_operator(est)
        r = WA.actor_on_event(est, role="user", text="how is the gateway?",
                              vec=[0.0, 1.0] + [0.0] * 6,
                              session="ses_x")
        mid = r.get("msg_eid")
        self.assertTrue(mid)
        m = est.docs.get(mid)
        self.assertIsNotNone(m)
        self.assertEqual((m.properties or {}).get("parent"),
                         WA.OPERATOR_EID)
        self.assertEqual((m.properties or {}).get("role"), "user")
        self.assertEqual((m.properties or {}).get("text_head"),
                         "how is the gateway?")

    def test_response_msg_carries_route_back(self):
        """THE ROUTE-BACK COVENANT: the response's msg carries
        reply_to (the msg it answers), via (the endpoint entity),
        route_reason, model -- the answer attaches where the
        question lives."""
        est = _est()
        WA.plant_actor(est, endpoint="ollama", url="http://localhost:11434")
        q = WA.actor_on_event(est, role="user", text="the ask",
                              vec=[1.0] + [0.0] * 7, session="ses_x")
        a = WA.actor_on_event(est, role="model", text="the answer",
                              vec=[0.0] + [1.0] + [0.0] * 6,
                              reply_to=q.get("msg_eid"),
                              via="ep:ollama", model="glm-5.3-flash:cloud",
                              route_reason="escalate: novel")
        m = est.docs.get(a.get("msg_eid"))
        self.assertIsNotNone(m)
        props = m.properties or {}
        self.assertEqual(props.get("reply_to"), q.get("msg_eid"))
        self.assertEqual(props.get("via"), "ep:ollama")
        self.assertEqual(props.get("route_reason"), "escalate: novel")
        # the endpoint actor EXISTS + counts the response
        ep = est.docs.get("membrane::ep:ollama")
        self.assertIsNotNone(ep)
        self.assertEqual(int((ep.properties or {}).get("responses") or 0), 1)

    def test_reply_route_back_resolves_parent(self):
        """'route it back to where it came from': the response's msg
        parented under the endpoint + reply_to-edge means the ORIGIN
        message's parent chain answers 'where did this come from':
        origin msg -> operator; answer -> endpoint + reply_to -> ask."""
        est = _est()
        WA.plant_operator(est)
        WA.plant_actor(est, endpoint="ollama")
        q = WA.actor_on_event(est, role="user", text="q",
                              vec=[1.0] + [0.0] * 7)
        a = WA.actor_on_event(est, role="model", text="a",
                              vec=[0.0, 1.0] + [0.0] * 6,
                              reply_to=q.get("msg_eid"), via="ep:ollama")
        am = est.docs.get(a.get("msg_eid"))
        self.assertEqual((am.properties or {}).get("parent"),
                         "membrane::ep:ollama")
        qm = est.docs.get(am.properties.get("reply_to"))
        self.assertEqual((qm.properties or {}).get("parent"),
                         WA.OPERATOR_EID)


class TestWatchLoopActor(unittest.TestCase):
    def test_loop_verdict_mints_msg_under_watch_actor(self):
        """The loops' verdicts are messages from the watch actor."""
        est = _est()
        WA.plant_actor(est, endpoint="watch")
        r = WA.actor_on_event(est, role="watch", text="ignored: no action "
                              "warrants reaction", vec=[0.5, 0.5] + [0.0] * 6,
                              via="ep:watch",
                              entity="membrane::f:membrane/router.py")
        m = est.docs.get(r.get("msg_eid"))
        self.assertIsNotNone(m)
        self.assertEqual((m.properties or {}).get("parent"),
                         "membrane::ep:watch")
        self.assertEqual((m.properties or {}).get("entity"),
                         "membrane::f:membrane/router.py")
        # the entity's decision stamp:
        tgt = est.docs.get("membrane::f:membrane/router.py")
        if tgt is not None:
            self.assertEqual(
                ((tgt.properties or {}).get("watch_last_decision") or {})
                .get("verdict"), "ignored")
if __name__ == "__main__":
    unittest.main()


class TestChatChain(unittest.TestCase):
    def test_messages_form_double_linked_list(self):
        """THE CHAIN: each msg carries prev_msg (and the prior msg's
        next_msg back-edge updates); the ACTOR carries last_msg --
        one read of the operator walks the whole conversation either
        direction."""
        est = _est()
        WA.plant_operator(est)
        r1 = WA.actor_on_event(est, role="user", text="first",
                               vec=[1.0] + [0.0] * 7, session="s1")
        r2 = WA.actor_on_event(est, role="model", text="second",
                               vec=[0.0, 1.0] + [0.0] * 6, session="s1")
        r3 = WA.actor_on_event(est, role="user", text="third",
                               vec=[1.0, 1.0] + [0.0] * 6, session="s1")
        m2 = est.docs.get(r2["msg_eid"])
        m3 = est.docs.get(r3["msg_eid"])
        m1 = est.docs.get(r1["msg_eid"])
        self.assertEqual((m2.properties or {}).get("prev_msg"),
                         r1["msg_eid"])
        self.assertEqual((m1.properties or {}).get("next_msg"),
                         r2["msg_eid"])
        self.assertEqual((m3.properties or {}).get("prev_msg"),
                         r2["msg_eid"])
        op = est.docs.get(WA.OPERATOR_EID)
        self.assertEqual((op.properties or {}).get("last_msg"),
                         r3["msg_eid"])

    def test_walk_the_chain_backwards(self):
        """The convenience walk: from the actor's last_msg, prev_msg
        hops reach the conversation's start (the model's future
        history lane)."""
        est = _est()
        WA.plant_operator(est)
        ids = [WA.actor_on_event(est, role="user", text=f"t{i}",
                                 vec=[float(i + 1) / 6] + [0.0] * 6,
                                 session="s")["msg_eid"]
               for i in range(4)]
        read_back = []
        eid = (est.docs.get(WA.OPERATOR_EID).properties or {}).get("last_msg")
        hops = 0
        while eid and hops < 10:
            m = est.docs.get(eid)
            read_back.append(m.properties.get("text_head"))
            eid = (m.properties or {}).get("prev_msg")
            hops += 1
        self.assertEqual([t.split()[0] for t in read_back],
                         ["t3", "t2", "t1", "t0"])


if __name__ == "__main__":
    unittest.main()
