"""THE STREAMED CALL SALVAGE (issue #25, PR #29): two backend shapes that
used to die silently at the seat's /v1 --

1. a backend that streams tool calls WITHOUT `index` sent one chunk per
   argument delta; defaulting the bucket to len(tcs) spawned a separate
   NAMELESS call per delta, every one refused with no tool name (the
   same backend's stream merges fine at the /v1 tee's rule, lanes.py:
   int(tc.get("index", 0) or 0)). THE CONTRACT: index defaults to 0 and
   a blank/None index reads as 0 too -- an unindexed stream reassembles
   ONE call with the full name and arguments; a repeated name is not
   doubled.

2. a call's salvage at the handler's door: a blank name went out MASKED
   as '?' (a real-looking tool name whose refusal pointed at the wrong
   place) and unparseable arguments were silently emptied to {} -- the
   model got told nothing about its call and could send the SAME broken
   call again. THE CONTRACT: the blank name reaches the resolver
   UNMASKED, and bad arguments refuse HONESTLY: the raw text quoted,
   "the call never ran" stated, and the refusal rides the wire back to
   the model as the call's own tool result.
"""
import http.server
import json
import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as MS  # noqa: E402

A_PARTIAL = '{"acti'
A_TAIL = 'on": "read"}'
A_WHOLE = '{"action": "read"}'


def _serve(chunks):
    """One POST plays one SSE script: chunks, then [DONE]."""
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for c in chunks:
                self.wfile.write(("data: " + json.dumps(c) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.close_connection = True
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _streamed(chunks):
    srv = _serve(chunks)
    try:
        return MS.loopback_ladder(srv.server_address[1]).stream(
            {"messages": []}, lambda k, v: False)
    finally:
        srv.shutdown()


def _delta(tool_calls, finish=None):
    ch = {"delta": {"tool_calls": tool_calls}}
    if finish:
        ch["finish_reason"] = finish
    return {"choices": [ch]}


class TestStreamedMerge(unittest.TestCase):
    def test_an_unindexed_stream_is_ONE_call(self):
        # THE EXHIBIT (issue #25): the backend sends the tool call in
        # three deltas, none carrying index. Old rule (index ->
        # len(tcs)): three calls -- one half-named, two nameless, ALL
        # refused. The tee's rule: one merged call.
        out = _streamed([
            _delta([{"id": "c1", "function": {"name": "entity"}}]),
            _delta([{"function": {"arguments": A_PARTIAL}}]),
            _delta([{"function": {"arguments": A_TAIL}}],
                   finish="tool_calls"),
        ])
        tcs = out["choices"][0]["message"]["tool_calls"]
        self.assertEqual(len(tcs), 1, tcs)
        self.assertEqual(tcs[0]["id"], "c1")
        self.assertEqual(tcs[0]["function"]["name"], "entity")
        self.assertEqual(tcs[0]["function"]["arguments"], A_WHOLE)
        self.assertEqual(out["choices"][0]["finish_reason"], "tool_calls")

    def test_a_blank_index_is_still_zero(self):
        # an index of None/'' must read as 0 like a missing one (the
        # tee's int(tc.get("index", 0) or 0)), never a fresh bucket.
        out = _streamed([
            _delta([{"id": "c1", "index": None,
                     "function": {"name": "web", "arguments": A_WHOLE}}]),
            _delta([{"index": "", "function": {"arguments": A_WHOLE}}]),
        ])
        self.assertEqual(len(out["choices"][0]["message"]["tool_calls"]), 1)

    def test_a_repeated_name_is_not_doubled(self):
        # some backends re-send the name with every argument delta; a
        # bare += would have made 'entityentityentity'.
        out = _streamed([
            _delta([{"id": "c9", "function": {"name": "entity"}}]),
            _delta([{"function": {"name": "entity",
                                  "arguments": A_WHOLE}}],
                   finish="tool_calls"),
        ])
        fn = out["choices"][0]["message"]["tool_calls"][0]["function"]
        self.assertEqual(fn["name"], "entity", fn)
        self.assertEqual(fn["arguments"], A_WHOLE)


def _rounds(*msgs):
    """A ladder over len(msgs) model rounds, on the STREAM lane (the
    salvage runs under the streamed turn); each round gets the next
    message."""
    state = {"n": 0}
    msgs_ = msgs

    def ladder(body):
        raise AssertionError("the buffered call is not the streamed round's door")

    def stream(body, on_event):
        msg = msgs_[min(state["n"], len(msgs_) - 1)]
        state["n"] += 1
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}

    ladder.stream = stream
    return ladder


def _runner(ladder):
    return MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(),
                         ladder, lambda *a: "ok", system="", model="m",
                         tools=None)   # tools=None: the family gate's own surface


class TestHonestRefusals(unittest.TestCase):
    def _tool_round(self, tool_calls):
        """Round 1 sends the broken calls; round 2 says done. The tool
        parts of round 1 come back."""
        ladder = _rounds({"tool_calls": tool_calls}, {"content": "done."})
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        out = r.run(sid, "go")
        return [p for p in out["parts"] if p.get("type") == "tool"]

    def test_a_blank_name_is_refused_unmasked(self):
        # THE EXHIBIT: the parser lost the tool's name. The old door:
        # name went out as '?' -- a fake real name in the part's tool
        # field (and on the wire as history). The contract: unmasked, empty.
        parts = self._tool_round([{"id": "c1", "type": "function",
                                   "function": {"name": "",
                                                "arguments": "{}"}}])
        self.assertEqual(len(parts), 1, parts)
        self.assertEqual(parts[0]["tool"], "", parts[0])
        outtext = parts[0]["state"]["output"]
        self.assertIn("had no tool name", outtext, outtext)
        # the refusal IS the result: completed against {}, never a hang
        self.assertEqual(parts[0]["state"]["status"], "completed", parts[0])

    def test_unparseable_arguments_refuse_and_quote_the_raw(self):
        # THE EXHIBIT (issue #12's family): '{"acti' streamed in --
        # json.loads raised, args silently emptied, the call 'ran'
        # against {}. The contract: the raw text quoted, never ran.
        raw = A_PARTIAL
        parts = self._tool_round([{"id": "c2", "type": "function",
                                   "function": {"name": "entity",
                                                "arguments": raw}}])
        self.assertEqual(len(parts), 1, parts)
        outtext = parts[0]["state"]["output"]
        self.assertIn("the call's ARGUMENTS did not parse", outtext, outtext)
        self.assertIn(f"({raw}):", outtext, outtext)
        self.assertIn("the call never ran", outtext, outtext)
        self.assertEqual(parts[0]["state"]["input"], {}, parts[0])   # never ran against a guess
        self.assertEqual(parts[0]["state"]["status"], "completed", parts[0])

    def test_a_non_object_arguments_refuses_too(self):
        # 'not isinstance(args, dict)' went to {} silently the same way.
        raw = "[1, 2]"
        parts = self._tool_round([{"id": "c3", "type": "function",
                                   "function": {"name": "entity",
                                                "arguments": raw}}])
        outtext = parts[0]["state"]["output"]
        self.assertIn("the call's ARGUMENTS did not parse", outtext, outtext)
        self.assertIn(f"({raw}):", outtext, outtext)
        self.assertIn("the call never ran", outtext, outtext)

    def test_a_broken_call_rides_the_wire_back_to_the_model(self):
        # the refusal is a REAL completed result on the wire: role:'tool'
        # with the broken call's own id -- the model that made the typo
        # learns from the next round's context (it literally reads it).
        broken = {"id": "cx", "type": "function",
                  "function": {"name": "entity",
                               "arguments": '{"act": '}}
        seen = []

        def ladder(body):
            raise AssertionError("the buffered call is not the streamed round's door")

        def stream(body, on_event):
            seen.append(body)
            if len(seen) == 1:
                return {"choices": [{"message": {"tool_calls": [broken]},
                                     "finish_reason": "tool_calls"}]}
            return {"choices": [{"message": {"content": "learned."},
                                 "finish_reason": "stop"}]}

        ladder.stream = stream
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "go")
        self.assertEqual(len(seen), 2, "the broken round must loop a second round")
        rows = [m for m in seen[1]["messages"]
                if m.get("role") == "tool" and m.get("tool_call_id") == "cx"]
        self.assertEqual(len(rows), 1)
        self.assertIn("the call never ran", rows[0].get("content", ""))
        self.assertIn("the call's ARGUMENTS did not parse",
                      rows[0].get("content", ""))


if __name__ == "__main__":
    unittest.main()