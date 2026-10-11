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
import zlib
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
                rng = np.random.default_rng(zlib.crc32(lw.encode()))   # stable across runs (hash() is seeded per process)
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
        roles = [(m.get("info") or {}).get("role") for m in r.messages.list(sid)]
        self.assertEqual(roles, ["assistant"])                  # the model's row only -- never a voice row
        self.assertFalse(any((m.get("info") or {}).get("voiceTurn") for m in r.messages.list(sid)))
        self.assertEqual(V.of(srv).of_session(sid)[0]["id"], line["id"])

    def test_loops_and_calls_have_no_voice(self):
        for agent in ("watch: membrane::goal:x", "phone"):
            _, _, _, _, lines = self._turn(agent, wait=1.5)
            self.assertEqual(lines, [], agent)

    def test_it_never_hears_itself(self):
        r, srv, sid, out, lines = self._turn("build")
        self.assertTrue(lines)
        heard = V.WordTable()
        for t in CORPUS * 3 + ["how is the battery?"]:
            heard.observe(t)
        heard.observe("The battery is at ninety percent.", V.LLM_WEIGHT)   # the model counts half
        for ln in V.of(srv).lines.values():                  # ...and its own line, a quarter
            heard.observe(ln["text"], V.SELF_WEIGHT)
        got = V.of(srv).table.uni
        self.assertEqual(set(got), set(heard.uni))
        for w in heard.uni:
            self.assertAlmostEqual(got[w], heard.uni[w])

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


class TestAutoChat(unittest.TestCase):
    def _runner(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        lad = _CountingLadder()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lad,
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        V.of(srv).bootstrap(CORPUS * 3)
        return r, srv, lad

    def setUp(self):
        self._pause = V.AUTO_PAUSE_S
        V.AUTO_PAUSE_S = 0.02

    def tearDown(self):
        V.AUTO_PAUSE_S = self._pause

    def test_they_loop_until_stopped_and_the_voice_never_hears_itself(self):
        r, srv, lad = self._runner()
        sid = r.sessions.create(title="t")["id"]
        V.auto_set(r, sid, True)
        end = time.time() + 15
        while time.time() < end and lad.n < 3:
            time.sleep(0.05)
        self.assertGreaterEqual(lad.n, 3)                              # rounds go on by themselves
        r.abort(sid)                                                    # Stop ends it
        self.assertFalse(V.auto_state(r, sid)["on"])
        time.sleep(0.5)
        n = lad.n
        time.sleep(0.5)
        self.assertLessEqual(lad.n, n + 1)                              # at most the round in flight
        rows = r.messages.list(sid)
        vt = [m for m in rows if (m.get("info") or {}).get("voiceTurn")]
        self.assertTrue(vt)
        self.assertTrue(all(p["text"].startswith(V.VOICE_PREFIX) for m in vt for p in m["parts"]))
        heard = V.WordTable()
        for t in CORPUS * 3:
            heard.observe(t)
        for _ in range(lad.n):
            heard.observe("The battery is at ninety percent.")
        v = V.of(srv)
        for ln in v.lines.values():
            heard.observe(ln["text"], V.SELF_WEIGHT)
        self.assertEqual(set(v.table.uni), set(heard.uni))              # teachers + itself, quietly

    def test_a_voice_turn_brings_no_prose_to_the_field(self):
        from membrane import speakers as SP
        ev = {"stream": "discourse", "role": "user", "text": V.frame("battery charge fine")}
        self.assertEqual(SP.prose_of(ev), "")
        self.assertEqual(SP.speaker_of(ev), SP.VOICE)


class TestTeacher(unittest.TestCase):
    def test_the_lesson_line(self):
        r = "Close! The battery is fine.\nIt meant: The battery is charging well."
        self.assertEqual(V.it_meant(r), "The battery is charging well.")
        self.assertEqual(V.strip_meant(r), "Close! The battery is fine.")
        self.assertEqual(V.it_meant("**It meant:** the anchor holds."), "the anchor holds.")
        self.assertEqual(V.it_meant("no lesson here"), "")
        self.assertIn("It meant:", V.frame("x"))

    def test_it_stops_opening_the_same_way(self):
        v = _voice()
        t = v.vecs.project(FakeEmbedder().embed(["battery charge cells"])[0])
        first = V.compose(v.table, v.vecs, t)
        ws = [w.lower() for w in V.tokenize(first["text"]) if w not in V.PUNCT]
        again = V.compose(v.table, v.vecs, t, stale_starts={(ws[0], ws[1])})
        ws2 = [w.lower() for w in V.tokenize(again["text"]) if w not in V.PUNCT]
        self.assertNotEqual(tuple(ws2[:2]), (ws[0], ws[1]), (first["text"], again["text"]))

    def test_a_voice_turn_teaches_with_the_lesson_and_logs_the_pair(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), _Ladder(),
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        v = V.of(srv)
        v.bootstrap(CORPUS * 3)
        rec = v.speak(FakeEmbedder().embed(["battery"])[0], sid="s1")
        r._voice_last = {"s1": rec["id"]}
        V.on_turn_end(r, "s1", V.frame(rec["text"]),
                      "Nice try.\nIt meant: Volts look healthy tonight.", "operator", voice_turn=True)
        end = time.time() + 5
        while time.time() < end and "closeness" not in v.lines[rec["id"]]:
            time.sleep(0.05)
        self.assertAlmostEqual(v.table.uni.get("volts", 0), V.MEANT_WEIGHT)      # the lesson, x2
        self.assertNotIn("membrane", v.table.uni)                                # never the frame
        pairs = (Path(d) / "voice" / "pairs.jsonl").read_text().splitlines()
        self.assertIn("Volts look healthy tonight.", pairs[-1])


class TestHearingItself(unittest.TestCase):
    def test_the_gap_leans_the_next_aim_and_fades(self):
        v = _voice()
        E = FakeEmbedder()
        gap = v.learn_gap("s1", "the anchor chain is out", "the battery charge is fine")
        self.assertGreater(gap, 0.1)
        q = E.embed(["how are we"])[0]
        bat = V._unit(E.embed(["battery charge"])[0])
        plain = V._unit(q)
        leaned = v.steer("s1", q)
        self.assertGreater(float(leaned @ bat), float(plain @ bat))     # toward what it meant
        n1 = float(np.linalg.norm(v.correction["s1"]))
        v.steer("s1", q)
        self.assertLess(float(np.linalg.norm(v.correction.get("s1", np.zeros(1)))), n1)   # fades
        self.assertAlmostEqual(float(v.steer("other", q) @ plain), 1.0)   # per session

    def test_its_own_lines_still_never_teach_it(self):
        v = _voice()
        before = dict(v.table.uni)
        v.learn_gap("s1", "zzqx unheard words", "the battery charge is fine")
        self.assertEqual(v.table.uni, before)                           # the gap steers, it never hears


class TestItHearsItself(unittest.TestCase):
    def test_its_line_counts_a_quarter_and_a_down_vote_takes_it_back(self):
        v = _voice()
        before = {k: dict(d) for k, d in v.table.t.items()}
        rec = v.speak(FakeEmbedder().embed(["battery charge"])[0])
        quarter = V.WordTable()
        quarter.observe(rec["text"], V.SELF_WEIGHT)
        for k, d in quarter.t.items():
            for w, c in d.items():
                self.assertAlmostEqual(v.table.t[k][w] - before.get(k, {}).get(w, 0), c)
        k0, w0 = next((k, w) for k, d in quarter.t.items() for w in d)
        after_speak = v.table.t[k0][w0]
        v.feedback(rec["id"], vote="down")
        self.assertLess(v.table.t[k0][w0], after_speak)                  # the down vote takes it back


class TestStuckTeacher(unittest.TestCase):
    def test_a_repeated_lesson_teaches_nothing_new(self):
        v = _voice()
        self.assertFalse(v.is_repeat_lesson("s1", "the anchor chain is out forty meters"))
        self.assertTrue(v.is_repeat_lesson("s1", "the anchor chain is out forty meters"))
        self.assertTrue(v.stuck["s1"])
        self.assertFalse(v.is_repeat_lesson("s1", "the battery charge is fine today"))

    def test_a_voice_turn_with_a_repeat_lesson_is_not_heard(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), _Ladder(),
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        v = V.of(srv)
        v.bootstrap(CORPUS * 3)
        v.is_repeat_lesson("s1", "Volts look healthy tonight.")
        rec = v.speak(FakeEmbedder().embed(["battery"])[0], sid="s1")
        r._voice_last = {"s1": rec["id"]}
        before = v.table.uni.get("volts", 0)
        V.on_turn_end(r, "s1", V.frame(rec["text"]), "Again.\nIt meant: Volts look healthy tonight.",
                      "operator", voice_turn=True)
        end = time.time() + 5
        while time.time() < end and "lesson" not in v.lines[rec["id"]]:
            time.sleep(0.05)
        self.assertEqual(v.lines[rec["id"]].get("lesson"), "repeat")
        self.assertEqual(v.table.uni.get("volts", 0), before)                # not heard again

    def test_fresh_topic_picks_a_live_specific_entity(self):
        class _St:
            def __init__(self, eid, tier, n):
                self.eid, self.tier, self.n, self.surface = eid, tier, n, eid
                self.state = np.ones(4)
        fld = type("F", (), {"_states": {"a": _St("c:uptime", "live", 5), "b": _St("w:the", "live", 50),
                                         "c": _St("person:x", "live", 9), "d": _St("f:x.py", "nursery", 9)}})()
        for _ in range(5):
            self.assertTrue(V.fresh_topic(fld)[0].startswith("c:uptime"))
        self.assertIn("never repeat", V.frame("x"))


class TestTopicNote(unittest.TestCase):
    def test_the_teacher_is_told_what_it_aimed_at(self):
        self.assertIn("trying to describe: c:uptime", V.frame("x", "c:uptime"))
        self.assertIn("answering your last reply", V.frame("x"))


class TestRecall(unittest.TestCase):
    def _pool(self, now=None):
        P = V.SentencePool()
        E = FakeEmbedder()
        P.add(E, ["The battery charge is holding steady through the night.",
                  "The anchor chain is out forty meters on a sand bottom.",
                  "The bms reads the battery at 92 percent right now."], "reply", ts=now)
        return P, E

    def test_whole_sentences_toward_the_target(self):
        P, E = self._pool()
        out = V.recall(P, E.embed(["anchor chain wind"])[0])
        self.assertTrue(out["text"].startswith("The anchor chain"))

    def test_stale_facts_are_skipped_fresh_ones_kept(self):
        P, E = self._pool(now=time.time() - 3 * V.FRESH_S)
        out = V.recall(P, E.embed(["battery bms percent"])[0])
        self.assertNotIn("92", out["text"])                     # old number: not said
        P2, _ = self._pool()
        self.assertIn("92", V.recall(P2, E.embed(["battery bms percent"])[0], max_sents=2)["text"])

    def test_no_echo_and_exclusions(self):
        P, E = self._pool()
        msg = "The anchor chain is out forty meters on a sand bottom."
        out = V.recall(P, E.embed([msg])[0], message_vec=E.embed([msg])[0])
        self.assertNotIn("forty meters", out["text"])
        out2 = V.recall(P, E.embed(["anchor chain"])[0], exclude=[msg])
        self.assertNotIn("forty meters", out2["text"])

    def test_the_ab_takes_turns_and_tags_the_mode(self):
        v = _voice()
        v.pool, _ = self._pool()
        modes = [v.speak(FakeEmbedder().embed(["battery"])[0])["mode"] for _ in range(4)]
        self.assertEqual(modes, ["recall", "walk", "recall", "walk"])

    def test_the_register_shift_and_persistence(self):
        d = tempfile.mkdtemp()
        v = V.Voice(FakeEmbedder(), d)
        v.bootstrap(CORPUS * 3)
        v.pool, _ = self._pool()
        v.note_pair(np.ones(1024), np.r_[np.ones(512), -np.ones(512)])
        self.assertIsNotNone(v.shift())
        v.save()
        w = V.Voice(FakeEmbedder(), d)
        self.assertEqual(len(w.pool), 3)
        self.assertEqual(w._n_pairs, 1)
        self.assertTrue(w._pool_booted)

    def test_pool_sentences_filter(self):
        got = V.pool_sentences("- a bullet line that is long enough to pass the size. "
                               "A real sentence about the battery charge today. It meant: no.")
        self.assertEqual(got, ["A real sentence about the battery charge today."])


class TestVoiceRules(unittest.TestCase):
    def setUp(self):
        V._rule_strikes.clear()

    def _est(self):
        from membrane.entities import EntityStore, CLASS_COMPONENT
        est = EntityStore(path=None, shared=False)
        r = est.add(cls=CLASS_COMPONENT, name="voice rule no dangling if", summary="x")
        est.docs[r["eid"]].properties["voice_rule"] = "result = {'ok': not state['text'].startswith('If ')}"
        c = est.add(cls=CLASS_COMPONENT, name="ha:light class", summary="x")
        cls = est.docs[c["eid"]]
        cls.properties["voice_rule"] = "result = {'ok': 'light' in state['text'].lower()}"
        k = est.add(cls=CLASS_COMPONENT, name="ha:light.port_strip", summary="x")
        est.docs[k["eid"]].properties["parents"] = [cls.eid]
        b = est.add(cls=CLASS_COMPONENT, name="voice rule broken", summary="x")
        est.docs[b["eid"]].properties["voice_rule"] = "raise ValueError('oops')"
        return est

    def test_global_and_class_scoped_rules(self):
        rules = V.voice_rules(self._est())
        self.assertEqual(len(rules), 3)
        self.assertIsNotNone(V.rule_violation(rules, "If the loop is an echo."))
        self.assertIsNone(V.rule_violation(rules, "The battery is fine."))      # no member named: class rule idle
        self.assertIsNotNone(V.rule_violation(rules, "The port strip is on."))   # mentions a member, breaks its rule
        self.assertIsNone(V.rule_violation(rules, "The port strip light is on."))

    def test_recall_skips_what_a_rule_forbids(self):
        P = V.SentencePool()
        E = FakeEmbedder()
        P.add(E, ["If the anchor chain is out forty meters.", "The anchor chain holds on a sand bottom."])
        rules = V.voice_rules(self._est())
        out = V.recall(P, E.embed(["anchor chain"])[0], rules=rules, max_sents=1)
        self.assertFalse(out["text"].startswith("If "))

    def test_a_broken_rule_is_set_aside_not_fatal(self):
        est = self._est()
        rules = V.voice_rules(est)
        for _ in range(4):
            V.rule_violation(rules, "Anything.")
        est._voice_rules_cache = None
        self.assertEqual(len(V.voice_rules(est)), 2)
        self.assertIn("help('voice')", V.frame("x"))


class TestExploreTogether(unittest.TestCase):
    def test_explore_is_the_default_and_has_no_grading(self):
        r = type("R", (), {"_auto": {}, "hub": S.SSEHub(), "_busy": {"s1": 1}, "messages": S.MessageStore()})()
        st = V.auto_set(r, "s1", True)
        self.assertEqual(st["mode"], "explore")
        f = V.frame_explore("I saw the port strip.", "ha:light.port_strip (latest: on)")
        self.assertIn("Explore it together", f)
        self.assertNotIn("It meant", f)
        self.assertEqual(V.auto_set(r, "s1", True, "teach")["mode"], "teach")

    def test_a_finding_is_heard_at_full_weight_and_logged(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), _Ladder(),
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        r._auto = {"s1": {"rounds": 1, "mode": "explore"}}
        v = V.of(srv)
        v.bootstrap(CORPUS * 3)
        rec = v.speak(FakeEmbedder().embed(["battery"])[0], sid="s1")
        r._voice_last = {"s1": rec["id"]}
        r._auto = {}                                     # stop before the next round fires
        r._auto_mode_probe = None
        V.auto_set(r, "s1", False)
        r._auto["s1"] = {"rounds": 1, "mode": "explore"}
        before = v.table.uni.get("volts", 0)
        V.on_turn_end(r, "s1", V.frame_explore(rec["text"]), "Volts read fine on the bank today.",
                      "operator", voice_turn=True)
        r._auto.pop("s1", None)
        end = time.time() + 5
        while time.time() < end and "closeness" not in v.lines[rec["id"]]:
            time.sleep(0.05)
        self.assertAlmostEqual(v.table.uni.get("volts", 0) - before, V.EXPLORE_WEIGHT)
        pairs = (Path(d) / "voice" / "pairs.jsonl").read_text().splitlines()
        self.assertIn('"kind": "found"', pairs[-1])


class TestMembranesRole(unittest.TestCase):
    """2026-10-10, the operator: auto-chat and membrane's role are separate
    switches -- in auto-chat with prediction the LLM works on by itself
    like an agent, never hearing membrane, and membrane predicts it."""

    def setUp(self):
        self._pause = V.AUTO_PAUSE_S
        V.AUTO_PAUSE_S = 0.02

    def tearDown(self):
        V.AUTO_PAUSE_S = self._pause

    def test_predict_role_auto_chat_is_the_llm_alone_and_membrane_guesses_it(self):
        r, srv, lad = TestAutoChat._runner(self)
        sid = r.sessions.create(title="t")["id"]
        self.assertEqual(V.auto_state(r, sid)["role"], "babble")
        self.assertEqual(V.role_set(r, sid, "predict"), "predict")
        self.assertFalse(V.auto_state(r, sid)["on"])                    # the role alone starts nothing
        V.auto_set(r, sid, True)
        end = time.time() + 15
        while time.time() < end and lad.n < 3:
            time.sleep(0.05)
        r.abort(sid)
        self.assertGreaterEqual(lad.n, 3)
        rows = r.messages.list(sid)
        users = [m for m in rows if (m.get("info") or {}).get("role") == "user"]
        self.assertTrue(users and all((m["info"]).get("autoTurn") for m in users))
        self.assertFalse(any((m["info"]).get("voiceTurn") for m in users))
        sent = " ".join(m["content"] for m in lad.last["messages"] if m["role"] == "user")
        self.assertNotIn(V.VOICE_PREFIX.strip(), sent)                  # Argus never hears membrane
        self.assertIn("carry on by yourself", sent)
        time.sleep(0.5)
        rep = srv._predictor.report()
        self.assertGreaterEqual(rep["operator"]["turns"], 2)           # and membrane predicted his turns
        v = V.of(srv)
        self.assertEqual(v.table.uni.get("nobody", 0), 0)              # the boilerplate is never heard

    def test_babble_role_keeps_the_voice_turns(self):
        r = type("R", (), {"_auto": {}, "hub": S.SSEHub(), "_busy": {"s1": 1}, "messages": S.MessageStore()})()
        V.role_set(r, "s1", "nonsense")
        self.assertEqual(V.role(r, "s1"), "babble")
        self.assertEqual(V.auto_set(r, "s1", True)["role"], "babble")


class TestLearningFromMisses(unittest.TestCase):
    def test_a_real_turns_reply_is_the_answer_key_and_leans_the_next_aim(self):
        d = tempfile.mkdtemp()
        srv = _Srv()
        srv._models_path = str(Path(d) / "models.json")
        srv.membrane = type("M", (), {"embedder": FakeEmbedder()})()
        r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), _Ladder(),
                         lambda n, a, c: "ok", system="", model="m")
        r.srv = srv
        v = V.of(srv)
        v.bootstrap(CORPUS * 3)
        rec = v.speak(FakeEmbedder().embed(["battery"])[0], sid="s1")
        r._voice_last = {"s1": rec["id"]}
        V.on_turn_end(r, "s1", "how is the anchor?", "The anchor holds on sand. Wind is light.", "operator")
        end = time.time() + 5
        while time.time() < end and "miss_gap" not in v.lines[rec["id"]]:
            time.sleep(0.05)
        self.assertIn("miss_gap", v.lines[rec["id"]])
        self.assertIn("s1", v.correction)                              # the next aim leans toward it
        pairs = (Path(d) / "voice" / "pairs.jsonl").read_text()
        self.assertIn("The anchor holds on sand.", pairs)
