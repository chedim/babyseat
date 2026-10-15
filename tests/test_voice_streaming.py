"""CHUNKED STT + TURN-TAKING (2026-10-15, Phase 2 & 3 of the speaker lane).

Phase 2 -- streaming the person's speech, "more is coming": a spoken
utterance used to arrive as ONE message after the speaker fell silent. Now
the page streams it: interim transcripts (final: false) arrive while the
person is still talking, and the final transcript (final: true) starts
exactly one turn. Interim chunks never start a turn themselves -- they show
in the voice log as they arrive and coalesce into the turn their final
transcript starts.

Phase 3 -- the "listen" control: the follow-up mic has a shorter lock
(LOCK_S, not the full 30 s), auto-pauses while Argus speaks (the ear never
hears his own voice), and gains pause/resume so a turn can hold the mic
instead of opening it blindly.
"""
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
    seen = []
    pub = r.hub.publish
    r.hub.publish = lambda ev: (seen.append(ev), pub(ev))[1]
    return r, srv, chat, calls, seen


def _voice_msg(r, chat, words, stream=None, final=True):
    text = f"[HA user: {WHO}]\n🎙 [voice:out:dmitrii-chechetkin on] {words}"
    info = {"id": MS._gen_id("msg"), "sessionID": chat, "role": "user", "agent": "build", "voiceIn": True,
            "time": {"created": int(time.time() * 1000)}}
    if stream is not None:
        info["stream"] = stream
        info["final"] = bool(final)
    parts = [{"id": MS._gen_id("prt"), "sessionID": chat, "messageID": info["id"], "type": "text", "text": text}]
    r.messages.append(chat, info, parts)
    return text, info, parts


class TestChunkedSpeech(unittest.TestCase):
    def test_interim_chunks_never_run_and_coalesce_into_one_turn(self):
        r, srv, chat, calls, seen = _seat()
        t1, i1, p1 = _voice_msg(r, chat, "the battery", stream="s1", final=False)
        self.assertEqual(SPK.route(r, chat, t1, i1, p1), SPK.INTERIM)
        t2, i2, p2 = _voice_msg(r, chat, "shore power", stream="s1", final=False)
        self.assertEqual(SPK.route(r, chat, t2, i2, p2), SPK.INTERIM)
        self.assertEqual(calls, [])                       # no turn yet
        t3, i3, p3 = _voice_msg(r, chat, "the battery, shore power", stream="s1", final=True)
        routed = SPK.route(r, chat, t3, i3, p3)
        self.assertIsNotNone(routed)
        self.assertNotEqual(routed, SPK.INTERIM)
        SPK.run(r, routed)
        self.assertEqual(len(calls), 1)                   # ONE turn for the whole utterance
        self.assertIn("the battery", calls[-1])
        self.assertIn("shore power", calls[-1])
        streams = [e for e in seen if e.get("type") == "membrane.stream"]
        self.assertEqual([e.get("final") for e in streams], [False, False])
        self.assertIn("the battery", streams[0]["text"])
        self.assertIn("shore power", streams[1]["text"])

    def test_an_empty_final_falls_back_to_the_streamed_words(self):
        r, srv, chat, calls, seen = _seat()
        t1, i1, p1 = _voice_msg(r, chat, "the battery", stream="s1", final=False)
        SPK.route(r, chat, t1, i1, p1)
        t2, i2, p2 = _voice_msg(r, chat, "shore power", stream="s1", final=False)
        SPK.route(r, chat, t2, i2, p2)
        t3, i3, p3 = _voice_msg(r, chat, "", stream="s1", final=True)   # whisper flushed nothing
        routed = SPK.route(r, chat, t3, i3, p3)
        self.assertIsNotNone(routed)
        self.assertNotEqual(routed, SPK.INTERIM)
        SPK.run(r, routed)
        self.assertEqual(len(calls), 1)
        self.assertIn("the battery", calls[-1])
        self.assertIn("shore power", calls[-1])

    def test_a_voice_message_without_a_stream_still_runs_as_before(self):
        r, srv, chat, calls, seen = _seat()
        text, info, parts = _voice_msg(r, chat, "how's the battery?")
        routed = SPK.route(r, chat, text, info, parts)
        self.assertIsNotNone(routed)
        self.assertNotEqual(routed, SPK.INTERIM)
        SPK.run(r, routed)
        self.assertEqual(len(calls), 1)

    def test_interim_rows_are_skipped_from_the_chats_history(self):
        r, srv, chat, calls, seen = _seat()
        text, info, parts = _voice_msg(r, chat, "the battery", stream="s1", final=False)
        SPK.route(r, chat, text, info, parts)
        self.assertTrue(any((m["info"] or {}).get("voiceInterim")
                            for m in r.messages.list(chat)))
        self.assertFalse(any("the battery" in m["content"]
                             for m in r._prior_history(chat, "")))


class TestTurnTaking(unittest.TestCase):
    def test_the_followup_lock_is_shorter(self):
        r, srv, chat, calls, seen = _seat()
        vin = VIO.entity(srv, "in", WHO, create=False)
        srv._speaker_turn_live = 1
        vin.properties["listen"] = 99
        out = VIO.on_listen(srv, vin)
        self.assertIn("deferred", out)
        self.assertEqual(out["listening"], VIO.LOCK_S)    # the shorter lock, not the 30 s cap
        self.assertEqual(vin.properties["listen_pending"], VIO.LOCK_S)

    def test_listen_pauses_and_resumes(self):
        r, srv, chat, calls, seen = _seat()
        vin = VIO.entity(srv, "in", WHO, create=False)
        vin.properties["listening_for_s"] = 12
        vin.properties["listen"] = "pause"
        VIO.on_listen(srv, vin)
        ev = [e for e in seen if e.get("type") == "membrane.listen"][-1]
        self.assertEqual((ev["seconds"], ev.get("pause")), (0, True))
        self.assertEqual(vin.properties["listen_hold"], 12)
        vin.properties["listen"] = "resume"
        VIO.on_listen(srv, vin)
        ev = [e for e in seen if e.get("type") == "membrane.listen"][-1]
        self.assertEqual(ev["seconds"], 12)
        self.assertFalse(ev.get("pause"))

    def test_the_ear_auto_pauses_while_argus_speaks_then_resumes(self):
        r, srv, chat, calls, seen = _seat()
        vin = VIO.entity(srv, "in", WHO, create=False)
        vin.properties["on"] = True
        vin.properties["listening_for_s"] = 12
        out = VIO.entity(srv, "out", WHO, create=False)
        out.properties["say"] = "Checking now."
        VIO.on_edit(srv, out)
        ev = [e for e in seen if e.get("type") == "membrane.listen"][-1]
        self.assertEqual((ev["seconds"], ev.get("pause")), (0, True))
        SPK.release_listen(srv)                          # the turn ended: the ear reopens
        ev = [e for e in seen if e.get("type") == "membrane.listen"][-1]
        self.assertEqual(ev["seconds"], 12)
        self.assertFalse(ev.get("pause"))


if __name__ == "__main__":
    unittest.main()
