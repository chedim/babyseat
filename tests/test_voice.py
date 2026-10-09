"""MEMBRANE'S OWN VOICE (2026-10-09, the operator: "add membrane's own speech
as a third chat participant that me and the LLM teach", red first).

Pinned: the steered walk says something about where the field points
(grammar from the heard words, meaning from the target); it hears only its
teachers (never itself); 👍/👎/rewrite teach; its lines never reach the
session's messages; the line is graded against the LLM's reply; chat lanes
only.
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

from membrane import server as S  # noqa: E402
from membrane import voice as V  # noqa: E402

TOPICS = {"battery": 0, "charge": 0, "volts": 0, "bms": 0, "cells": 0, "solar": 0,
          "anchor": 1, "chain": 1, "wind": 1, "drift": 1, "holding": 1, "bottom": 1}


class FakeEmbedder:
    """Topic geometry: battery words share an axis, anchor words another."""
    model = "fake"

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(1024)
            for w in V.tokenize(t) or [t]:
                lw = w.lower()
                if lw in TOPICS:
                    v[TOPICS[lw]] += 3.0
                rng = np.random.default_rng(abs(hash(lw)) % (2**32))
                v[2:] += 0.05 * rng.standard_normal(1022)
            out.append(v)
        return np.array(out)


CORPUS = [
    "The battery is at ninety percent and the cells look balanced.",
    "The bms says the battery charge is holding steady.",
    "Solar charge is strong today so the battery will be full.",
    "The anchor is holding well in the sand bottom.",
    "The wind picked up but the anchor chain is not dragging.",
    "We drift a little when the wind turns but the anchor holds.",
    "I think the battery cells need a top balance soon.",
    "The anchor chain is out forty meters on a sand bottom.",
]


def _voice(tmp=None):
    v = V.Voice(FakeEmbedder(), tmp)
    v.bootstrap(CORPUS * 3)
    return v


class TestSpeech(unittest.TestCase):
    def test_it_speaks_toward_the_target(self):
        v = _voice()
        bat = v.speak(FakeEmbedder().embed(["battery charge cells"])[0], "how is the power?")
        anc = v.speak(FakeEmbedder().embed(["anchor chain drift"])[0], "are we safe?")
        self.assertTrue(bat and anc)
        lb, la = bat["text"].lower(), anc["text"].lower()
        self.assertTrue(any(w in lb for w in ("battery", "charge", "cells", "bms", "solar")), lb)
        self.assertTrue(any(w in la for w in ("anchor", "chain", "wind", "drift")), la)
        self.assertTrue(bat["text"][0].isupper())
        self.assertIn(bat["text"][-1], ".?!")

    def test_rendering_and_cleaning(self):
        toks = V.tokenize("[HA user: D]\nThe **BMS** says 92.7% -- see `x=1` https://a.b\n```\ncode\n```")
        self.assertIn("BMS", toks)
        self.assertIn("92.7%", toks)
        self.assertNotIn("code", toks)
        self.assertNotIn("D", toks)

    def test_an_empty_table_stays_silent(self):
        v = V.Voice(FakeEmbedder(), None)
        self.assertIsNone(v.speak(np.ones(1024)))


class TestTeaching(unittest.TestCase):
    def test_rewrite_teaches_strongest_and_down_unlearns(self):
        v = _voice()
        rec = v.speak(FakeEmbedder().embed(["battery"])[0])
        v.feedback(rec["id"], rewrite="Volts are fine and the bms is happy.")
        self.assertIn("volts", dict(v.table.nexts(["<s>"], 50)))
        before = dict(v.table.nexts(V.tokenize(rec["text"])[:1], 50))
        v.feedback(rec["id"], vote="down")
        after = dict(v.table.nexts(V.tokenize(rec["text"])[:1], 50))
        nxt = V.tokenize(rec["text"])[1].lower()
        self.assertLessEqual(after.get(nxt, 0), before.get(nxt, 0))
        self.assertEqual(v.lines[rec["id"]]["vote"], "down")

    def test_grade_against_the_reply(self):
        v = _voice()
        rec = v.speak(FakeEmbedder().embed(["battery"])[0])
        c_same = v.grade(rec["id"], "The battery charge is fine.")
        self.assertGreater(c_same, 0.5)

    def test_persists(self):
        d = tempfile.mkdtemp()
        v = _voice(d)
        rec = v.speak(FakeEmbedder().embed(["anchor"])[0], sid="s1")
        v.feedback(rec["id"], vote="up")
        w = V.Voice(FakeEmbedder(), d)
        self.assertTrue(w.table.t)
        self.assertEqual(w.of_session("s1")[0]["vote"], "up")


class _Ladder:
    def __call__(self, body):
        return {"choices": [{"message": {"content": "The battery is at ninety percent."}}]}


class _Srv:
    pass


class TestTheTurn(unittest.TestCase):
    def _runner(self, d):
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), _Ladder(),
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        return r, srv

    def _turn(self, agent, text="how is the battery?", wait=10):
        d = tempfile.mkdtemp()
        r, srv = self._runner(d)
        V.of(srv).bootstrap(CORPUS * 3)
        q = r.hub.subscribe()
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, text, user_info={"id": "msg_u1", "role": "user", "agent": agent})
        end = time.time() + wait
        evs = []
        while time.time() < end:
            while not q.empty():
                evs.append(q.get())
            lines = [e for e in evs if e.get("type") == "membrane.voice"]
            if any("closeness" in (e.get("line") or e.get("properties", {}).get("line") or {}) for e in lines):
                break
            time.sleep(0.05)
        return r, srv, sid, out, [e for e in evs if e.get("type") == "membrane.voice"]

    def test_a_chat_turn_gets_a_voice_line_outside_the_messages(self):
        r, srv, sid, out, lines = self._turn("build")
        self.assertTrue(lines)
        line = lines[-1].get("line") or lines[-1]["properties"]["line"]
        self.assertIn("closeness", line)
        stored = " ".join(p.get("text") or "" for m in r.messages.list(sid) for p in m.get("parts") or [])
        self.assertNotIn(line["text"], stored)                  # never in the session
        self.assertEqual(V.of(srv).of_session(sid)[0]["id"], line["id"])

    def test_loops_and_calls_have_no_voice(self):
        for agent in ("watch: membrane::goal:x", "phone"):
            _, _, _, _, lines = self._turn(agent, wait=1.5)
            self.assertEqual(lines, [], agent)

    def test_it_never_hears_itself(self):
        r, srv, sid, out, lines = self._turn("build")
        self.assertTrue(lines)
        heard = V.WordTable()
        for t in CORPUS * 3 + ["how is the battery?", "The battery is at ninety percent."]:
            heard.observe(t)
        self.assertEqual(V.of(srv).table.uni, heard.uni)   # the teachers' words, nothing else

if __name__ == "__main__":
    unittest.main()


class TestRoutes(unittest.TestCase):
    def test_the_session_voice_routes(self):
        import json as _j
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        v = V.of(srv)
        v.bootstrap(CORPUS * 3)
        rec = v.speak(FakeEmbedder().embed(["battery"])[0], sid="ses_a")
        other = v.speak(FakeEmbedder().embed(["anchor"])[0], sid="ses_b")
        sessions, messages = S.SessionStore(), S.MessageStore()
        H = S.make_handler(hub=S.SSEHub(), sessions=sessions, messages=messages, runner=None, srv=srv)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            got = _j.load(urllib.request.urlopen(base + "/session/ses_a/voice"))
            self.assertEqual([r["id"] for r in got], [rec["id"]])
            req = urllib.request.Request(base + "/session/ses_a/voice", method="POST",
                                         data=_j.dumps({"id": rec["id"], "vote": "up"}).encode(),
                                         headers={"Content-Type": "application/json"})
            self.assertEqual(_j.load(urllib.request.urlopen(req))["vote"], "up")
            bad = urllib.request.Request(base + "/session/ses_a/voice", method="POST",
                                         data=_j.dumps({"id": other["id"], "vote": "down"}).encode())
            with self.assertRaises(Exception):
                urllib.request.urlopen(bad)            # another session's line
        finally:
            httpd.shutdown()


class TestBoot(unittest.TestCase):
    def test_the_first_turn_boots_in_the_background_once(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), _Ladder(),
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        sid = r.sessions.create(title="t")["id"]
        V.on_turn_start(r, sid, "hello", "m1", "operator")
        V.on_turn_start(r, sid, "hello again", "m2", "operator")   # mid-boot: quiet
        v = V.of(srv)
        end = time.time() + 10
        while time.time() < end and not v._booted:
            time.sleep(0.05)
        self.assertTrue(v._booted)
        self.assertEqual(v.lines, {})


class TestAim(unittest.TestCase):
    def test_the_message_leads_and_a_disagreeing_now_is_ignored(self):
        q = np.zeros(1024); q[0] = 1
        now = np.zeros(1024); now[0] = -1; now[1] = 1          # points away
        self.assertAlmostEqual(float(V.aim(q, now) @ q), 1.0)
        agree = np.zeros(1024); agree[0] = 1; agree[1] = 1
        t = V.aim(q, agree)
        self.assertGreater(float(t @ q), 0.95)                   # leans, never leads
        self.assertGreater(t[1], 0)
        self.assertAlmostEqual(float(V.aim(q, None) @ q), 1.0)


class _CountingLadder(_Ladder):
    def __init__(self):
        self.n = 0

    def __call__(self, body):
        self.n += 1
        self.last = body
        return super().__call__(body)


class TestMembraneOnly(unittest.TestCase):
    def test_the_llm_switch_off(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        lad = _CountingLadder()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lad,
                         lambda n, a, c: "ok", system="", model="m")
        heard = []
        srv.capturer = type("C", (), {"on_turn": lambda self, q, *a, **k: heard.append(q)})()
        r.srv = srv
        V.of(srv).bootstrap(CORPUS * 3)
        q = r.hub.subscribe()
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "is the anchor holding?",
              user_info={"id": "msg_u1", "role": "user", "agent": "build", "membraneOnly": True})
        self.assertEqual(lad.n, 0)                                   # no model call
        end, lines = time.time() + 10, []
        while time.time() < end and not lines:
            while not q.empty():
                e = q.get()
                if e.get("type") == "membrane.voice":
                    lines.append(e)
            time.sleep(0.05)
        self.assertTrue(lines)                                       # the voice answered
        self.assertEqual(heard, ["is the anchor holding?"])          # the field heard the operator
        self.messages_ok = True
        r.messages.append(sid, {"id": "msg_u1", "role": "user", "membraneOnly": True},
                          [{"id": "p1", "type": "text", "text": "is the anchor holding?"}])
        r.run(sid, "and the battery?", user_info={"id": "msg_u2", "role": "user", "agent": "build"})
        self.assertEqual(lad.n, 1)
        sent = [m["content"] for m in lad.last["messages"] if m["role"] == "user"]
        self.assertNotIn("is the anchor holding?", " ".join(sent))   # not the model's history
