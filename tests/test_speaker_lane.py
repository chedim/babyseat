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


class TestTheReplyIsSpokenAsItStreams(unittest.TestCase):
    """2026-10-15, the operator: hands-free voice should feel fluid, snappy, like a
    person. Spoken replies reached the page only after the WHOLE turn -- tool rounds
    included (median 16 s from the end of speech to the first word). Now every
    finished sentence of the voice turn's text is spoken the moment it streams;
    reasoning and code are never spoken; the mic opens after the LAST sentence."""

    def _streaming_run(self, r, srv, events, wait_for=None):
        """A turn that streams `events` [(kind, payload)] and, at a ('wait', text)
        step, waits until that line was spoken -- proving speech DURING the turn."""
        spoken_mid = []

        def run(sid, *a, **k):
            pid = {}
            for kind, val in events:
                if kind == "part":                         # (part key, type)
                    key, typ = val
                    pid[key] = MS._gen_id("prt")
                    r.hub.publish({"type": "message.part.updated", "sessionID": sid,
                                   "part": {"id": pid[key], "sessionID": sid, "type": typ, "text": ""}})
                elif kind == "delta":                      # (part key, text)
                    key, txt = val
                    r.hub.publish({"type": "message.part.delta", "sessionID": sid, "partID": pid[key],
                                   "field": "text", "delta": txt})
                elif kind == "wait":
                    out = srv.entity_store._resolve_spec("voice:out:dmitrii-chechetkin")
                    t0 = time.time()
                    while time.time() - t0 < 3:
                        if any(s["text"] == val for s in (out.properties.get("said") or [])):
                            spoken_mid.append(val)
                            break
                        time.sleep(0.02)
            full = "".join(v[1] for k, v in events if k == "delta" and v[0] == "t")
            return {"parts": [{"type": "text", "text": full}]}
        r.run = run
        return spoken_mid

    def _said(self, srv):
        out = srv.entity_store._resolve_spec("voice:out:dmitrii-chechetkin")
        return [s["text"] for s in (out.properties.get("said") or [])]

    def test_each_sentence_is_spoken_while_the_turn_still_runs(self):
        r, srv, chat, calls = _seat()
        mid = self._streaming_run(r, srv, [
            ("part", ("t", "text")),
            ("delta", ("t", "The battery is at 63 percent. ")),
            ("wait", "The battery is at 63 percent."),
            ("delta", ("t", "It is charging from shore power.")),
        ])
        text, info, parts = _voice_msg(r, chat, "how's the battery?")
        SPK.run(r, SPK.route(r, chat, text, info, parts))
        self.assertEqual(mid, ["The battery is at 63 percent."])    # spoken before the turn ended
        self.assertEqual(self._said(srv), ["The battery is at 63 percent.", "It is charging from shore power."])

    def test_reasoning_and_code_are_never_spoken(self):
        r, srv, chat, calls = _seat()
        self._streaming_run(r, srv, [
            ("part", ("r", "reasoning")),
            ("delta", ("r", "I should check the shunt first. ")),
            ("part", ("t", "text")),
            ("delta", ("t", "Here it is. ```\nprint(1)\n``` That was the code.")),
        ])
        text, info, parts = _voice_msg(r, chat, "show me")
        SPK.run(r, SPK.route(r, chat, text, info, parts))
        said = " ".join(self._said(srv))
        self.assertNotIn("shunt", said)
        self.assertNotIn("print", said)
        self.assertIn("Here it is.", said)
        self.assertIn("That was the code.", said)

    def test_a_narrated_tool_call_is_never_spoken(self):
        r, srv, chat, calls = _seat()
        self._streaming_run(r, srv, [
            ("part", ("t", "text")),
            ("delta", ("t", "I'll plant a goal now. `goal(action='plant', text='comprehensive_test')`")),
        ])
        text, info, parts = _voice_msg(r, chat, "test everything")
        SPK.run(r, SPK.route(r, chat, text, info, parts))
        said = " ".join(self._said(srv))
        self.assertIn("I'll plant a goal now.", said)
        # the narrated tool call (inline code) is never voiced -- it reads
        # as noise aloud, and the goal must be planted by a real tool call
        self.assertNotIn("goal(action='plant'", said)
        self.assertNotIn("comprehensive_test", said)

    def test_the_mic_opens_after_the_last_streamed_sentence(self):
        r, srv, chat, calls = _seat()
        order = []
        pub = r.hub.publish
        r.hub.publish = lambda ev: (order.append(ev.get("type")), pub(ev))[1]
        vin = VIO.entity(srv, "in", WHO, create=False)
        self._streaming_run(r, srv, [
            ("part", ("t", "text")),
            ("delta", ("t", "Two things. First the bilge. ")),
            ("wait", "First the bilge."),
            ("delta", ("t", "Want me to check it?")),
        ])
        inner = r.run

        def run_and_ask(*a, **k):
            res = inner(*a, **k)
            vin.properties["listen"] = 8                     # Memo asks: open the mic
            VIO.on_listen(srv, vin)
            return res
        r.run = run_and_ask
        text, info, parts = _voice_msg(r, chat, "anything?")
        SPK.run(r, SPK.route(r, chat, text, info, parts))
        says = [i for i, t in enumerate(order) if t == "membrane.say"]
        listens = [i for i, t in enumerate(order) if t == "membrane.listen"]
        self.assertEqual(len(says), 3, order)
        self.assertLess(says[-1], listens[0])


class TestTheAnswerComesFirst(unittest.TestCase):
    def test_the_voice_frame_asks_for_the_answer_before_any_tool(self):
        f = SPK.frame("how's the battery?", "the chat is idle")
        self.assertIn("FIRST", f)
        self.assertIn("before any tool", f)

    def test_the_voice_frame_says_to_call_the_tool_not_narrate_it(self):
        f = SPK.frame("test everything", "the chat is idle")
        self.assertIn("never write a tool call into your spoken words", f)
        self.assertIn("goal", f)
        self.assertIn("plant", f)

    def test_the_voice_frame_never_promises_an_unwired_alert(self):
        # issue #102: the lane promised "it will alert you on the next polling
        # cycle" for gh issues, but there is no gh-issue watcher to deliver it.
        f = SPK.frame("alert me when a new issue is filed", "the chat is idle")
        self.assertIn("never promise", f)
        self.assertIn("NO watcher", f)
        self.assertIn("GitHub issues", f)
        self.assertIn("watch_script", f)      # what it CAN wire is named
        self.assertIn("file", f)
        self.assertIn("signal", f)


if __name__ == "__main__":
    unittest.main()
