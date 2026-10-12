"""THE SPEAKER LANE (2026-10-12, the operator: "a separate per-seat 'speaker'
event handler for voice comms"): a spoken message runs in the seat's speaker
session, beside the chat -- never cutting it; the reply is spoken back through
voice:out; the chat's history skips the routed row. And Argus opens the mic
for an answer (voice:in `listen`) instead of a follow-up window guessing."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402
from membrane import speaker as SPK  # noqa: E402
from membrane import voice_io as VIO  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

WHO = "Dmitrii Chechetkin"


def _seat():
    calls = []

    def ladder(body):
        msgs = body.get("messages") or []
        calls.append(msgs[-1].get("content") if msgs else "")
        return {"choices": [{"message": {"role": "assistant", "content": "The battery is at 63 percent."},
                             "finish_reason": "stop"}]}
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder, lambda *a, **k: "ok",
                      system="", model="m", tools=[])
    r._turn_watchdog = lambda *a, **k: None
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = type("S", (), {"entity_store": est, "_loop_runner": r, "membrane": None, "efield": None})()
    est._srv = srv
    r.srv = srv
    chat = r.sessions.create(title="chat")["id"]
    VIO.set_state(srv, WHO, out_on=True)
    VIO.heard(srv, WHO, "🎙 how's the battery", chat)
    return r, srv, chat, calls


def _voice_msg(r, chat, words):
    text = f"[HA user: {WHO}]\n🎙 [voice:out:dmitrii-chechetkin on] {words}"
    info = {"id": MS._gen_id("msg"), "sessionID": chat, "role": "user", "agent": "build", "voiceIn": True,
            "time": {"created": int(time.time() * 1000)}}
    parts = [{"id": MS._gen_id("prt"), "sessionID": chat, "messageID": info["id"], "type": "text", "text": text}]
    r.messages.append(chat, info, parts)
    return text, info, parts


class TestTheLane(unittest.TestCase):
    def test_a_voice_message_runs_beside_the_chat_and_is_spoken_back(self):
        r, srv, chat, calls = _seat()
        text, info, parts = _voice_msg(r, chat, "how's the battery?")
        routed = SPK.route(r, chat, text, info, parts)
        self.assertIsNotNone(routed)
        sp = routed[0]
        self.assertNotEqual(sp, chat)
        self.assertEqual(MS._lane_of(r.sessions.get(sp).get("agent")), "operator")
        SPK.run(r, routed)
        self.assertIn("[speaker lane]", calls[-1])
        self.assertIn("the chat is idle", calls[-1])
        said = srv.entity_store._resolve_spec("voice:out:dmitrii-chechetkin").properties.get("said") or []
        self.assertEqual(said[-1]["text"], "The battery is at 63 percent.")
        # the chat's history never replays the routed question
        self.assertFalse(any("how's the battery" in m["content"] for m in r._prior_history(chat, "")))

    def test_the_chat_is_not_cut(self):
        r, srv, chat, calls = _seat()
        r._busy[chat] = {"type": "busy"}              # the chat mid-turn
        text, info, parts = _voice_msg(r, chat, "status?")
        routed = SPK.route(r, chat, text, info, parts)
        self.assertIn("BUSY", routed[1])
        self.assertNotIn(chat, getattr(r, "_aborting", set()))

    def test_off_sends_voice_back_to_the_chat(self):
        r, srv, chat, calls = _seat()
        SPK._entity(srv).properties["enabled"] = False
        text, info, parts = _voice_msg(r, chat, "hi")
        self.assertIsNone(SPK.route(r, chat, text, info, parts))

    def test_a_typed_message_is_not_routed(self):
        r, srv, chat, calls = _seat()
        self.assertIsNone(SPK.route(r, chat, "typed", {"id": "m1", "role": "user"}, []))


class TestArgusOpensTheMic(unittest.TestCase):
    def test_listen_is_published_to_the_persons_session(self):
        r, srv, chat, calls = _seat()
        seen = []
        pub = r.hub.publish
        r.hub.publish = lambda ev: (seen.append(ev), pub(ev))[1]
        vin = VIO.entity(srv, "in", WHO, create=False)
        vin.properties["listen"] = 99
        out = VIO.on_listen(srv, vin)
        self.assertEqual(out["listening"], VIO.LISTEN_MAX_S)          # clamped
        ev = [e for e in seen if e.get("type") == "membrane.listen"][0]
        self.assertEqual((ev["sessionID"], ev["seconds"]), (chat, VIO.LISTEN_MAX_S))
        self.assertNotIn("listen", vin.properties)                    # consumed


class TestTheQuestionFirst(unittest.TestCase):
    def test_a_listen_set_mid_turn_opens_after_the_spoken_reply(self):
        r, srv, chat, calls = _seat()
        order = []
        pub = r.hub.publish
        r.hub.publish = lambda ev: (order.append(ev.get("type")), pub(ev))[1]
        vin = VIO.entity(srv, "in", WHO, create=False)

        real_run = r.run

        def run_and_ask(*a, **k):
            res = real_run(*a, **k)
            vin.properties["listen"] = 8                     # Argus opens the mic inside his turn
            VIO.on_listen(srv, vin)
            return res
        r.run = run_and_ask
        text, info, parts = _voice_msg(r, chat, "what do you think?")
        SPK.run(r, SPK.route(r, chat, text, info, parts))
        says = [i for i, t in enumerate(order) if t == "membrane.say"]
        listens = [i for i, t in enumerate(order) if t == "membrane.listen"]
        self.assertTrue(says and listens, order)
        self.assertLess(says[-1], listens[0])                 # the question is out before the mic opens


if __name__ == "__main__":
    unittest.main()
