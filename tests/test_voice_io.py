"""VOICE IN / OUT (2026-10-11): per-person voice:in / voice:out entities the
page's buttons flip; spoken messages start turns but live in the voice log;
Argus speaks by editing voice:out's `say` -- never by the page reading the chat."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import voice_io as VIO  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _seat():
    srv = types.SimpleNamespace(entity_store=EntityStore(path=None, shared=False))
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    seen = []
    pub = r.hub.publish
    r.hub.publish = lambda ev: (seen.append(ev), pub(ev))[1]
    return srv, r, seen


class TestVoiceIO(unittest.TestCase):
    def test_the_buttons_flip_the_persons_entities(self):
        srv, r, _ = _seat()
        st = VIO.set_state(srv, "Dmitrii Chechetkin", in_on=True, in_mode="addressed", out_on=True, device="phone")
        self.assertEqual(st["in"], {"on": True, "mode": "addressed", "device": "phone"})
        self.assertTrue(srv.entity_store._resolve_spec("voice:out:dmitrii-chechetkin").properties["on"])
        VIO.set_state(srv, "Dmitrii Chechetkin", in_mode="nonsense")
        self.assertEqual(srv.entity_store._resolve_spec("voice:in:dmitrii-chechetkin").properties["mode"], "off")

    def test_argus_speaks_by_editing_say_and_only_while_it_is_on(self):
        srv, r, seen = _seat()
        VIO.set_state(srv, "Dmitrii Chechetkin", out_on=False)
        VIO.heard(srv, "Dmitrii Chechetkin", "is the camera up", sid="ses_1")
        out = srv.entity_store._resolve_spec("voice:out:dmitrii-chechetkin")
        out.properties["say"] = "Camera's awake."
        self.assertEqual(VIO.on_edit(srv, out), {"spoken": False, "why": "off"})
        self.assertIn("off", out.properties["say_refused"])
        VIO.set_state(srv, "Dmitrii Chechetkin", out_on=True)
        out.properties["say"] = "Camera's awake. First frame is in."
        self.assertEqual(VIO.on_edit(srv, out), {"spoken": True, "session": "ses_1"})
        ev = [e for e in seen if e.get("type") == "membrane.say"][-1]
        self.assertEqual((ev["sessionID"], ev["line"]["text"]), ("ses_1", "Camera's awake. First frame is in."))
        self.assertEqual(out.properties["say"], "")                      # consumed
        out.properties["say"] = "Again right away."
        self.assertEqual(VIO.on_edit(srv, out)["why"], "gap")            # one line per SAY_GAP_S
        log = VIO.read(srv, "Dmitrii Chechetkin")
        self.assertEqual([x["text"] for x in log["in"]["log"]], ["is the camera up"])
        self.assertEqual([x["text"] for x in log["out"]["log"]], ["Camera's awake. First frame is in."])

    def test_a_spoken_message_starts_the_turn_and_is_marked_voice_in(self):
        srv, r, _ = _seat()
        sid = r.sessions.create(title="t")["id"]
        info = {"id": "msg_v1", "sessionID": sid, "role": "user", "voiceIn": True, "time": {"created": 1}}
        parts = [{"id": "p1", "type": "text", "text": "[HA user: Dmitrii Chechetkin]\n🎙 is the camera up"}]
        r.messages.append(sid, info, parts)                              # the route stores the row first
        r.run(sid, parts[0]["text"], user_info=info, user_parts=parts)
        rows = r.messages.list(sid)
        self.assertTrue(any((m["info"] or {}).get("voiceIn") for m in rows))
        self.assertTrue(any((m["info"] or {}).get("role") == "assistant" for m in rows))   # it answered


if __name__ == "__main__":
    unittest.main()


class TestTheStamp(unittest.TestCase):
    def test_the_mic_mark_carries_the_voice_out_state_and_the_log_keeps_the_words(self):
        srv, r, _ = _seat()
        VIO.set_state(srv, "Dmitrii Chechetkin", out_on=True)
        t = VIO.stamp(srv, "Dmitrii Chechetkin", "[HA user: Dmitrii Chechetkin]\n🎙 is the camera up")
        self.assertIn("🎙 [voice:out:dmitrii-chechetkin on] is the camera up", t)
        VIO.heard(srv, "Dmitrii Chechetkin", "🎙 [voice:out:dmitrii-chechetkin on] is the camera up")
        self.assertEqual(VIO.read(srv, "Dmitrii Chechetkin")["in"]["log"][-1]["text"], "is the camera up")
        VIO.set_state(srv, "Dmitrii Chechetkin", out_on=False)
        self.assertIn("[voice:out:dmitrii-chechetkin off]", VIO.stamp(srv, "Dmitrii Chechetkin", "🎙 hi"))
