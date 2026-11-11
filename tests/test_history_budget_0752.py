"""Rosie forgot (2026-11-11): the model saw only the last 12 messages, goal and watch notices
took those slots, and a reply that left the window could only come back as field fragments.
Now the history has TWO parts (operator, 2026-11-11): the recent messages in full while they
fit HISTORY_TOKEN_BUDGET, and before them an index of the older ones (id, who, time; no text)
while it fits HISTORY_INDEX_TOKENS. In a person's chat, machine notices are in neither part. The index lists message entities;
the existing `entity read` on one returns the stored message whole."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from membrane import lanes as LN
from membrane import mcp_server as MS
from membrane import server as SV
from membrane import toolset as TS
from membrane.entities import QuantumEntity
from membrane.rag import Injected


def runner(rows, agent=None):
    """rows: [(role, text)] -> a TurnRunner over one session 'ses_t'."""
    d = tempfile.mkdtemp()
    ss = SV.SessionStore(str(Path(d) / "s.json"))
    ms = SV.MessageStore(str(Path(d) / "m.json"))
    for i, (role, text) in enumerate(rows):
        ms.append("ses_t", {"id": f"msg_{i}", "role": role, "time": {"created": 1_000_000_000 + i * 1000},
                            "sessionID": "ses_t"},
                  [{"id": f"p{i}", "type": "text", "text": text}])
    r = SV.TurnRunner(SV.SSEHub(), ss, ms, None, None, system="", model="glm")
    if agent:
        r.sessions.get = lambda sid, _a=agent: {"id": sid, "agent": _a}
    return r


def chat(n, size=400):
    return [("user" if i % 2 == 0 else "assistant", f"m{i} " + "x" * size) for i in range(n)]


def split(win):
    """(the index block or None, the recent messages)."""
    if win and win[0]["role"] == "system":
        return win[0]["content"], win[1:]
    return None, win


def toks(msgs):
    return sum(SV.history_tokens(m["content"]) for m in msgs)


def head(m):
    return m["content"].split()[0]


class Recent(unittest.TestCase):
    def test_a_long_chat_recent_part_fits_by_tokens(self):
        _idx, recent = split(runner(chat(200))._prior_history("ses_t", ""))
        self.assertLessEqual(toks(recent), SV.HISTORY_TOKEN_BUDGET)
        self.assertEqual(head(recent[-1]), "m199")
        first = int(head(recent[0])[1:])
        self.assertEqual([int(head(m)[1:]) for m in recent], list(range(first, 200)))   # in full, in order

    def test_a_short_chat_is_all_in_and_has_no_index(self):
        idx, recent = split(runner(chat(5, 10))._prior_history("ses_t", ""))
        self.assertIsNone(idx)
        self.assertEqual(len(recent), 5)

    def test_a_giant_message_does_not_wipe_the_recent_part(self):
        rows = chat(30, 40)
        rows[27] = ("assistant", "m27 " + "y" * 200_000)
        with mock.patch.dict(os.environ, {"MEMBRANE_HISTORY_TOKENS": "3000"}):
            _idx, recent = split(runner(rows)._prior_history("ses_t", ""))
        heads = [head(m) for m in recent]
        self.assertIn("m27", heads)
        self.assertIn("m26", heads)                              # messages before it still ride
        self.assertLessEqual(len(recent[heads.index("m27")]["content"]), SV.HISTORY_TEXT_CAP)

    def test_the_start_holds_across_consecutive_turns(self):
        firsts = [head(split(runner(chat(n))._prior_history("ses_t", ""))[1][0])
                  for n in range(100, 100 + 2 * SV.history_anchor_step())]
        self.assertLess(len(set(firsts)), len(firsts))           # the head is reused, not moved every turn

    def test_the_budgets_and_env(self):
        self.assertEqual(SV.HISTORY_TOKEN_BUDGET, 1500)           # the operator's numbers, 2026-11-11
        self.assertEqual(SV.HISTORY_INDEX_TOKENS, 500)
        self.assertEqual((SV.history_token_budget(), SV.history_index_tokens()), (1500, 500))
        with mock.patch.dict(os.environ, {"MEMBRANE_HISTORY_TOKENS": "700",
                                          "MEMBRANE_HISTORY_INDEX_TOKENS": "90"}):
            self.assertEqual((SV.history_token_budget(), SV.history_index_tokens()), (700, 90))

    def test_history_max_still_caps_the_count_when_set(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_HISTORY_MAX": "6", "MEMBRANE_HISTORY_ANCHOR": "1"}):
            _idx, recent = split(runner(chat(40, 10))._prior_history("ses_t", ""))
        self.assertEqual(len(recent), 6)


class Index(unittest.TestCase):
    def test_older_messages_are_indexed_newest_first_within_budget(self):
        win = runner(chat(400))._prior_history("ses_t", "")
        idx, recent = split(win)
        self.assertIsInstance(win[0], Injected)                  # a per-turn block, out of the cached head
        lines = idx.splitlines()
        self.assertEqual(lines[0], SV.INDEX_HEAD)
        self.assertLessEqual(SV.history_tokens(idx), SV.HISTORY_INDEX_TOKENS + len(lines))
        ids = [ln.split(" · ")[0] for ln in lines[1:]]
        first_recent = int(head(recent[0])[1:])
        self.assertEqual(ids[0], f"membrane::msg:msg_{first_recent - 1}")   # newest older message first
        nums = [int(i.rsplit("_", 1)[1]) for i in ids]
        self.assertEqual(nums, sorted(nums, reverse=True))
        self.assertNotIn("membrane::msg:msg_0", ids)             # the oldest dropped first

    def test_an_index_line_is_id_who_time_and_no_text(self):
        rows = chat(200)
        rows[2] = ("user", "[user: Dima]\nm2 secret words here " + "x" * 400)
        with mock.patch.dict(os.environ, {"MEMBRANE_HISTORY_INDEX_TOKENS": "5000"}):
            idx, _recent = split(runner(rows)._prior_history("ses_t", ""))
        line = next(ln for ln in idx.splitlines() if ln.startswith("membrane::msg:msg_2 "))
        parts = line.split(" · ")
        self.assertEqual(parts[:2], ["membrane::msg:msg_2", "Dima"])
        self.assertEqual(len(parts), 3)                          # id · who · time
        self.assertNotIn("secret", idx)
        self.assertNotIn("xxx", idx)


class Notices(unittest.TestCase):
    NOTICES = ["goal membrane::goal:d6535cce584e fulfilled: Re-ran full gradle test\n" + "z" * 3000,
               "[watch] membrane::goal:d6535cce584e: watch event (goal-ended)\ntriage: " + "w" * 3000]

    def test_notices_are_in_neither_part_of_a_persons_chat(self):
        rows = [("user", "[user#owner: owner]\nport the bug classes"), ("assistant", "on it")]
        for n in self.NOTICES * 30:
            rows += [("user", n), ("assistant", "noted")]
        rows += chat(120)
        win = runner(rows)._prior_history("ses_t", "")
        text = "\n".join(m["content"] for m in win)
        self.assertNotIn("goal membrane::goal", text)
        self.assertNotIn("[watch]", text)
        idx, _ = split(win)
        notice_ids = {f"membrane::msg:msg_{i}" for i in range(2, 2 + 2 * 60, 2)}
        self.assertFalse(notice_ids & {ln.split(" · ")[0] for ln in idx.splitlines()[1:]})

    def test_a_loop_session_keeps_its_notices(self):
        rows = [("user", self.NOTICES[0]), ("assistant", "planning")]
        win = runner(rows, agent="planner: membrane::goal:x")._prior_history("ses_t", "")
        self.assertTrue(any("fulfilled" in m["content"] for m in win))


class ReadAMessageInFull(unittest.TestCase):
    LIST = "Likely to repeat on .NET:\n" + "\n".join(f"{i}. item number {i} " + "d" * 300 for i in range(1, 13))
    EID = "membrane::msg:msg_1"

    def rows(self):
        rows = [("user", "[user#owner: owner]\nwhat else could break?"), ("assistant", self.LIST)]
        rows += chat(60, 200)
        rows += [("user", "[user#owner: owner]\n8-12")]
        return rows

    def test_the_rosie_case(self):
        r = runner(self.rows())
        idx, recent = split(r._prior_history("ses_t", ""))
        self.assertFalse(any("12. item number 12" in m["content"] for m in recent))   # left the recent part
        self.assertIn(f"{self.EID} · assistant", idx)                                  # its entity is in the index
        srv = type("S", (), {"_loop_runner": r})()
        got = LN._message_in_full(srv, self.EID, None)                                  # and entity read gives it whole
        self.assertEqual(got["text"], self.LIST)
        self.assertEqual((got["eid"], got["role"], got["session"]), (self.EID, "assistant", "ses_t"))

    def test_a_message_entity_resolves_by_its_mid(self):
        r = runner(self.rows())
        ent = QuantumEntity(eid=self.EID, cls="Message", properties={"mid": "msg_1", "text": "fragment"})
        got = LN._message_in_full(type("S", (), {"_loop_runner": r})(), "anything", ent)
        self.assertEqual(got["text"], self.LIST)                 # the store's text, not the entity's kept one

    def test_without_the_store_row_the_entity_text_stands_in(self):
        r = runner([])
        ent = QuantumEntity(eid="membrane::msg:msg_9", cls="Message",
                            properties={"mid": "msg_9", "text": "kept text", "role": "user", "ts": 1.0})
        got = LN._message_in_full(type("S", (), {"_loop_runner": r})(), "x", ent)
        self.assertEqual(got["text"], "kept text")

    def test_not_a_message(self):
        self.assertIsNone(LN._message_in_full(type("S", (), {"_loop_runner": None})(), "tool:executor", None))

    def test_by_message_id(self):
        r = runner(self.rows())
        self.assertEqual(r.find_message_id("msg_1")["text"], self.LIST)
        self.assertIsNone(r.find_message_id("msg_nope"))

    def test_entity_read_renders_the_full_text(self):
        out = {"ok": True, "eid": self.EID, "cls": "Message", "properties": {"role": "assistant"},
               "message": {"role": "assistant", "who": "assistant", "time": "2026-11-10 14:02",
                           "session": "ses_t", "title": "dotnet port", "text": self.LIST}}
        with mock.patch.object(MS, "_http", return_value=out) as h:
            txt = MS.TOOL_IMPLS["entity_read"]({"spec": self.EID}, 11500)
        self.assertIn("/membrane/entity/read?spec=", h.call_args[0][2])
        self.assertIn("12. item number 12", txt)
        self.assertIn("in dotnet port", txt)

    def test_no_new_tool_and_the_index_says_how_to_read(self):
        self.assertNotIn("message", MS.TOOL_IMPLS)
        self.assertNotIn("message", TS.FAMILIES["find"]["actions"])
        self.assertIn("entity read", SV.INDEX_HEAD)              # the tool diet has no room: the index says it


if __name__ == "__main__":
    unittest.main()
