"""P2: the serve store's multi-writer contract.

MessageStore._flush is a WHOLE-FILE snapshot -- two seats serving out of one
cwd (an old seat that survived its restart, a sibling test seat) turn it into
a last-writer-wins race: a stale boot copy restores an assistant row's EMPTY
opening state and erases the final text, the client's completion re-render
shows a vanished reply, and the exchange reads as 'my message sitting under
the model's previous response'. The pins:

- merge-on-flush: the flush re-reads disk, unions sessions and rows by
  message id, and the newer completion/created ts wins -- a stale empty row
  never erases a final-text row, and a genuinely newer RAM row still lands;
- the GET replay rides chronological created-ts order (stable on ties);
- token accounting: upstream usage SUMS across the tool-loop's rounds; the
  estimate fallback fills when the ladder answers without usage.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.server import (MessageStore, _chronological, _tokens_estimate,
                             _tokens_fill)


def _row(mid, role, created, *, completed=None, text=""):
    tm = {"created": created}
    if completed:
        tm["completed"] = completed
    info = {"id": mid, "sessionID": "s1", "role": role, "time": tm}
    parts = [{"id": mid + "-p", "sessionID": "s1", "messageID": mid,
              "type": "text", "text": text}] if text else []
    return info, parts


class TestMergeOnFlush(unittest.TestCase):
    def test_stale_copy_never_erases_final_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "serve-msgs.json")
            writer = MessageStore(path)          # the seat that owns the turn
            ui, up = _row("m-u", "user", 100, text="hi")
            ai, ap = _row("m-a", "assistant", 101, completed=200,
                          text="the final reply")
            writer.append("s1", ui, up)
            writer.append("s1", ai, ap)
            stale = MessageStore(path)           # a second writer's boot copy
            self.assertEqual((stale.get_msg("s1", "m-a")["parts"] or [{}])[0]
                             .get("text"), "the final reply")
            stale.msgs = {"s1": [                # the pre-completion staleness
                dict(zip(("info", "parts"),
                         _row("m-u", "user", 100, text="hi"))),
                dict(zip(("info", "parts"), _row("m-a", "assistant", 101))),
            ]}
            stale._flush()
            healed = MessageStore(path)
            row = healed.get_msg("s1", "m-a")
            self.assertEqual((row["parts"] or [{}])[0].get("text"),
                             "the final reply")
            self.assertEqual((row["info"]["time"] or {}).get("completed"), 200)

    def test_newer_ram_row_still_lands(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "serve-msgs.json")
            w = MessageStore(path)
            ui, up = _row("m-u", "user", 100, text="hi")
            w.append("s1", ui, up)
            newer, newerp = _row("m-a", "assistant", 101, completed=300,
                                 text="final after tools")
            w.append("s1", newer, newerp)
            healed = MessageStore(path)
            self.assertEqual(
                (healed.get_msg("s1", "m-a")["parts"] or [{}])[0]["text"],
                "final after tools")


class TestChronological(unittest.TestCase):
    def test_store_order_heals_to_created_ts(self):
        ai, _ = _row("m2", "assistant", 500, text="reply")
        ui, _ = _row("m1", "user", 400, text="ask")
        rows = [{"info": ai}, {"info": ui}]   # FULL rows, not bare infos
        self.assertEqual([r["info"]["id"] for r in _chronological(rows)],
                         ["m1", "m2"])

    def test_stable_on_ties(self):
        i1, _ = _row("m1", "user", 100, text="one")
        i2, _ = _row("m2", "user", 100, text="two")
        rows = [{"info": i1}, {"info": i2}]
        self.assertEqual([r["info"]["id"] for r in _chronological(rows)],
                         ["m1", "m2"])


class TestTokens(unittest.TestCase):
    def test_usage_sums_across_rounds(self):
        a = {"tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                        "cache": {"read": 0, "write": 0}}}
        _tokens_fill(a, {"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                   "total_tokens": 15}})
        _tokens_fill(a, {"usage": {"prompt_tokens": 12, "completion_tokens": 7,
                                   "total_tokens": 19}})
        t = a["tokens"]
        self.assertEqual((t["input"], t["output"], t["total"]), (22, 12, 34))

    def test_reasoning_and_cache_details(self):
        a = {"tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                        "cache": {"read": 0, "write": 0}}}
        _tokens_fill(a, {"usage": {"prompt_tokens": 10,
                                   "completion_tokens": 5,
                                   "total_tokens": 15,
                                   "completion_tokens_details":
                                   {"reasoning_tokens": 3},
                                   "prompt_tokens_details":
                                   {"cached_tokens": 4}}})
        t = a["tokens"]
        self.assertEqual(t["reasoning"], 3)
        self.assertEqual(t["cache"]["read"], 4)

    def test_estimate_fallback(self):
        history = [{"role": "system", "content": "x" * 40},
                   {"role": "user", "content": "y" * 8}]
        parts = [{"type": "text", "text": "z" * 12}]
        t = _tokens_estimate(history, parts)
        self.assertEqual(t["input"], 12)
        self.assertEqual(t["output"], 3)
        self.assertEqual(t["total"], 15)

    def test_no_usage_is_a_noop(self):
        a = {"tokens": {"total": 7, "input": 5, "output": 2, "reasoning": 0,
                        "cache": {"read": 0, "write": 0}}}
        _tokens_fill(a, {})
        self.assertEqual(a["tokens"]["total"], 7)


if __name__ == "__main__":
    unittest.main()
