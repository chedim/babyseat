"""THE STREAMED ROUND (2026-10-10, the operator: "it's slow to generate an
answer"): the serve's rounds stream from the proxy -- the words show as they
come, the injection card first; the stored parts are what the buffered path
stored; a stream that fails before any word falls back to the buffered call."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402

CARD = {"injection": "[membrane] from the field: the battery sits near 92%", "massage": {"alignment": 0.9}}


def _runner(ladder):
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                      lambda *a: "ok", system="", model="m", tools=[])
    seen = []
    pub = r.hub.publish
    r.hub.publish = lambda ev: (seen.append(ev), pub(ev))[1]
    return r, seen


class TestStreamedRounds(unittest.TestCase):
    def test_words_stream_and_the_parts_are_the_buffered_ones(self):
        def ladder(body):
            raise AssertionError("the buffered call is not used when streaming works")

        def stream(body, on_event):
            on_event("ext", CARD)
            for w in ("think", "ing"):
                on_event("reasoning", w)
            for w in ("The bat", "tery is ", "at 92%."):
                on_event("text", w)
                self.assertFalse(on_event("tick", None))
            return {"membrane": CARD, "choices": [{"message": {"content": "The battery is at 92%.",
                                                               "reasoning": "thinking"}}], "usage": {}}
        ladder.stream = stream
        r, seen = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "how is the battery?")
        kinds = [p.get("type") for p in out["parts"] if p.get("type") in ("text", "reasoning", "tool")]
        self.assertEqual(kinds, ["tool", "reasoning", "text"])          # the card first, then the words
        text = [p for p in out["parts"] if p.get("type") == "text"]
        self.assertEqual(len(text), 1)
        self.assertEqual(text[0]["text"], "The battery is at 92%.")
        deltas = [e["delta"] for e in seen if e.get("type") == "message.part.delta" and e.get("partID") == text[0]["id"]]
        self.assertEqual("".join(deltas), "The battery is at 92%.")        # live, in order, batched
        self.assertLessEqual(len(deltas), 3)
        self.assertEqual(len([p for p in out["parts"] if p.get("tool") == "membrane"]), 1)

    def test_a_flood_of_tokens_is_a_trickle_of_events(self):
        """2026-10-11: ~100 delta events a second starved HA's relay; batched
        to one per DELTA_COALESCE_S per part -- the same words, in order."""
        import time as _t
        words = [f"w{i} " for i in range(200)]

        def ladder(body):
            raise AssertionError("not used")

        def stream(body, on_event):
            for w in words[:100]:
                on_event("reasoning", w)
                _t.sleep(0.002)
            for w in words[100:]:
                on_event("text", w)
                _t.sleep(0.002)
            return {"choices": [{"message": {"content": "".join(words[100:]), "reasoning": "".join(words[:100])}}]}
        ladder.stream = stream
        r, seen = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go")
        ds = [e for e in seen if e.get("type") == "message.part.delta"]
        self.assertLess(len(ds), 20)                                      # not 200
        by_part = {}
        for e in ds:
            by_part.setdefault(e["partID"], []).append(e["delta"])
        texts = {p["type"]: p["id"] for p in out["parts"] if p.get("type") in ("text", "reasoning")}
        self.assertEqual("".join(by_part[texts["reasoning"]]), "".join(words[:100]))
        self.assertEqual("".join(by_part[texts["text"]]), "".join(words[100:]))
        order = [e["partID"] for e in ds]
        self.assertEqual(order, sorted(order, key=lambda pid: 0 if pid == texts["reasoning"] else 1))  # never interleaved

    def test_a_stream_that_fails_before_any_word_falls_back(self):
        calls = []

        def ladder(body):
            calls.append("buffered")
            return {"choices": [{"message": {"content": "fine"}}]}

        def stream(body, on_event):
            raise OSError("the proxy hung up")
        ladder.stream = stream
        r, _ = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "hi")
        self.assertEqual(calls, ["buffered"])
        self.assertEqual([p["text"] for p in out["parts"] if p.get("type") == "text"], ["fine"])

    def test_tool_rounds_stream_their_lead_in(self):
        replies = [
            {"choices": [{"message": {"content": "Checking the BMS...", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "state", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"content": "All good."}}]},
        ]

        def ladder(body):
            raise AssertionError("not used")

        def stream(body, on_event):
            rep = replies.pop(0)
            on_event("text", rep["choices"][0]["message"]["content"])
            return rep
        ladder.stream = stream
        r, _ = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "how is the BMS?")
        self.assertEqual([p["text"] for p in out["parts"] if p.get("type") == "text"], ["Checking the BMS...", "All good."])

    def test_the_ladder_parses_the_proxys_stream(self):
        import http.server
        import json
        import threading
        chunks = [{"membrane": CARD},
                  {"choices": [{"delta": {"reasoning": "hm"}}]},
                  {"choices": [{"delta": {"content": "Hello "}}]},
                  {"choices": [{"delta": {"content": "there", "tool_calls": [
                      {"index": 0, "id": "c9", "function": {"name": "find", "arguments": '{"q":'}}]}}]},
                  {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " 1}"}}]}, "finish_reason": "tool_calls"}]},
                  {"usage": {"total_tokens": 5}}]
        got_body = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                got_body.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                for c in chunks:
                    self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            call = MS.loopback_ladder(srv.server_address[1])
            ev = []
            out = call.stream({"messages": []}, lambda k, v: ev.append((k, v)) and False)
        finally:
            srv.shutdown()
        self.assertTrue(got_body["stream"] and got_body["membrane_ext"])
        m = out["choices"][0]["message"]
        self.assertEqual(m["content"], "Hello there")
        self.assertEqual(m["reasoning"], "hm")
        self.assertEqual(m["tool_calls"][0]["function"], {"name": "find", "arguments": '{"q": 1}'})
        self.assertEqual(out["membrane"], CARD)
        self.assertEqual(out["usage"], {"total_tokens": 5})
        self.assertEqual([k for k, _ in ev if k != "tick"], ["ext", "reasoning", "text", "text"])


if __name__ == "__main__":
    unittest.main()
