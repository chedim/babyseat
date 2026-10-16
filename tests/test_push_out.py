"""PUSH OUT (0.6.1, the operator: "add the field to push chat out messages like
it can do with voice ... both, and the same should be for voice"): a person's
chat:out entity posts into their latest chat the way voice:out speaks -- Argus
by editing its `say`, and an entity's own script by a `chat` / `voice` key in
its result (a watcher can tell you a sensor changed with no model turn)."""
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import voice_io as VIO  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS  # noqa: E402


def _seat():
    srv = types.SimpleNamespace(entity_store=EntityStore(path=None, shared=False),
                                efield=None, membrane=None, events_pool=None)
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                     lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    seen = []
    pub = r.hub.publish
    r.hub.publish = lambda ev: (seen.append(ev), pub(ev))[1]
    return srv, r, seen


def _chat(r, owner="", updated=None):
    s = r.sessions.create(title="t")
    if owner:
        s["owner"] = owner
    if updated is not None:
        s["time"]["updated"] = updated
    return s["id"]


def _texts(r, sid):
    return ["\n".join(p.get("text", "") for p in m["parts"] if p.get("type") == "text")
            for m in r.messages.list(sid) if m["info"]["role"] == "assistant"]


class TestChatOut(unittest.TestCase):
    def test_an_edit_of_say_posts_into_their_latest_chat(self):
        srv, r, seen = _seat()
        old = _chat(r, "Ann", updated=1000)
        new = _chat(r, "Ann", updated=2000)
        _chat(r, "Bob", updated=3000)                       # someone else's, newer
        out = CIO.entity(srv, "Ann")
        out.properties["say"] = "The garage door is still open."
        res = CIO.on_edit(srv, out)
        self.assertEqual(res, {"posted": True, "session": new})
        self.assertEqual(_texts(r, new), ["The garage door is still open."])
        self.assertEqual(_texts(r, old), [])
        self.assertEqual(out.properties["say"], "")         # consumed
        self.assertEqual([x["text"] for x in out.properties["said"]],
                         ["The garage door is still open."])
        kinds = [e["type"] for e in seen if e.get("sessionID") == new]
        self.assertIn("message.updated", kinds)
        self.assertIn("message.part.updated", kinds)

    def test_no_owned_chat_falls_back_to_their_voice_then_the_newest(self):
        srv, r, _ = _seat()
        loopish = _chat(r, updated=5000)
        r._agents = {loopish: "listener-1"}                 # a loop's session never gets a push
        solo = _chat(r, updated=4000)
        self.assertEqual(CIO.latest_chat(srv, ""), solo)    # single-operator seat: newest non-loop
        VIO.heard(srv, "Cy", "hello", sid="ses_voice")
        self.assertEqual(CIO.latest_chat(srv, "Cy"), "ses_voice")

    def test_off_and_the_gap_are_honoured(self):
        srv, r, _ = _seat()
        sid = _chat(r, "Ann")
        out = CIO.entity(srv, "Ann")
        out.properties["on"] = False
        out.properties["say"] = "nope"
        self.assertEqual(CIO.on_edit(srv, out), {"posted": False, "why": "off"})
        self.assertEqual(_texts(r, sid), [])
        out.properties["on"] = True
        out.properties["say"] = "one"
        self.assertTrue(CIO.on_edit(srv, out)["posted"])
        out.properties["say"] = "two, right away"
        self.assertEqual(CIO.on_edit(srv, out)["why"], "gap")

    def test_no_chat_at_all_says_so(self):
        srv, r, _ = _seat()
        out = CIO.entity(srv, "Ann")
        out.properties["say"] = "hello?"
        self.assertEqual(CIO.on_edit(srv, out), {"posted": False, "why": "no chat"})


class TestArgusWritesByEditing(unittest.TestCase):
    def test_the_edit_hook_posts(self):
        from membrane.lanes import _active_model_hook
        srv, r, _ = _seat()
        sid = _chat(r, "Ann")
        CIO.ensure(srv, "chat:out:ann")                     # the edit route's first step
        est = srv.entity_store
        est.edit("chat:out:ann", properties={"say": "Dinner's at 7."})
        res = {"ok": True}
        _active_model_hook(srv, est, "chat:out:ann", res)
        self.assertEqual(res["chat_out"], {"posted": True, "session": sid})
        self.assertEqual(_texts(r, sid), ["Dinner's at 7."])

    def test_a_first_edit_creates_the_entity(self):
        srv, r, _ = _seat()
        self.assertIsNone(srv.entity_store._resolve_spec("chat:out:ann"))
        CIO.ensure(srv, "chat:out:Ann")
        self.assertIsNotNone(srv.entity_store._resolve_spec("chat:out:ann"))
        CIO.ensure(srv, "tool:garage")                      # anything else: untouched
        self.assertIsNone(srv.entity_store._resolve_spec("tool:garage"))


class TestScriptsPushToo(unittest.TestCase):
    def _ent(self, est):
        e = QuantumEntity(eid=MEMBRANE_NS + "tool:garage", cls="tool",
                          properties={"name": "garage"})
        est.docs[e.eid] = e
        return e

    def test_a_script_result_posts_to_chat(self):
        srv, r, _ = _seat()
        sid = _chat(r, "Ann")
        e = self._ent(srv.entity_store)
        res = N.apply_entity_update(srv, e, {"chat": "Garage open 10 min.", "to": "Ann"}, "watch", silent=True)
        self.assertEqual(res["pushed"]["chat"]["session"], sid)
        self.assertEqual(_texts(r, sid), ["Garage open 10 min."])

    def test_a_script_result_speaks(self):
        srv, r, seen = _seat()
        VIO.set_state(srv, "Ann", out_on=True)
        VIO.heard(srv, "Ann", "hi", sid="ses_v")
        e = self._ent(srv.entity_store)
        res = N.apply_entity_update(srv, e, {"voice": "Garage is open.", "to": "Ann"}, "tick", silent=True)
        self.assertTrue(res["pushed"]["voice"]["spoken"])
        self.assertEqual([x["line"]["text"] for x in seen if x.get("type") == "membrane.say"],
                         ["Garage is open."])

    def test_a_ticking_script_cannot_flood(self):
        # a tick fires every ~1 s: the same entity pushes at most once per
        # SCRIPT_PUSH_GAP_S, and never the same line twice in a row
        srv, r, _ = _seat()
        sid = _chat(r, "Ann")
        e = self._ent(srv.entity_store)
        N.apply_entity_update(srv, e, {"chat": "open", "to": "Ann"}, "tick", silent=True)
        res = N.apply_entity_update(srv, e, {"chat": "still open", "to": "Ann"}, "tick", silent=True)
        self.assertFalse(res["pushed"]["chat"]["posted"])
        e.properties["push_last_ts"] = time.time() - CIO.SCRIPT_PUSH_GAP_S - 1
        res = N.apply_entity_update(srv, e, {"chat": "open", "to": "Ann"}, "tick", silent=True)
        self.assertEqual(res["pushed"]["chat"]["why"], "repeat")
        self.assertEqual(_texts(r, sid), ["open"])

    def test_no_to_means_the_most_recent_person(self):
        srv, r, _ = _seat()
        _chat(r, "Ann", updated=1000)
        bob = _chat(r, "Bob", updated=2000)
        e = self._ent(srv.entity_store)
        N.apply_entity_update(srv, e, {"chat": "hello whoever is here"}, "watch", silent=True)
        self.assertEqual(_texts(r, bob), ["hello whoever is here"])

    def test_the_thinker_applies_a_tick_that_only_pushes(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_night_mode import _thinker
        srv, r, _ = _seat()
        srv.ambient_mode = "day"
        sid = _chat(r, "Ann")
        e = QuantumEntity(eid=MEMBRANE_NS + "tool:beat", cls="tool",
                          properties={"name": "beat", "tick_every": 1,
                                      "tick_script": "result = {'chat': 'tick says hi', 'to': 'Ann'}"})
        srv.entity_store.docs[e.eid] = e
        th = _thinker(srv, srv.entity_store)
        th._tick_scripts()
        self.assertEqual(_texts(r, sid), ["tick says hi"])


if __name__ == "__main__":
    unittest.main()
