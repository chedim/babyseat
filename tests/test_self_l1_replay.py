"""#161 RUNG 2 IN THE LAB: the ledger replayed over a REAL seat's history.

The toy world (sandbox.py) planted its answer, so it could not show what a real
store does to the ledger. It does two things:

  * the field's change pulses are mostly the seat's OWN WORDS. 76% of the
    28147 pulses in the live store are owned by the seat's reasoning/model
    turns, and 52% of all pulses are literally "this entity is named in that
    event". Replay them naively and the "body" is the act's own shadow --
    `tool:exec`, `s:?entity_read`, `e:v:<the act's own event>` -- things the
    act itself creates or names.
  * the real log has 48 act kinds of which most number a handful: three acts
    of one kind, one coincidence, and p_act=1.00 against a zero baseline made
    z infinite and crowned a body part out of nothing.

So this file pins (a) the replay's rule that neither the act's own footprint
nor the seat's own narration counts as an answer, and (b) the two things the
real data forced into the ledger: a floor under p_base (finite z) and a
minimum number of acts before a kind may claim an entity.

The events/deltas below are the live store's own shapes, shrunk to a page."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "self_l1"))

import replay as R  # noqa: E402


def _ev(key, ts, stream, role, tool="", src="live"):
    return (key, {"stream": stream, "role": role, "tool": tool, "ts": ts, "src": src})


def _dl(eid, event, ts):
    return {"eid": eid, "event": event, "ts": ts}


def _mini(n_read=30, n_edit=24):
    """A page of seat history: the seat reads, it edits its own component, the
    component's tick script answers the edit, its own words mention the slots,
    and the operator speaks before the seat reaches for a tool."""
    ev, dl = [], []
    for i in range(n_read):
        t = 100.0 + 10 * i
        ev.append(_ev(f"v:r{i}", t, "observation", "tool", "entity_read"))
        # the act's own footprint: the tool entity and its event shadow
        dl += [_dl("tool:entity_read", f"v:r{i}", t), _dl(f"e:v:r{i}", f"v:r{i}", t)]
        # ...and the seat's own words, at the same instant, name the slot back
        ev.append(_ev(f"v:n{i}", t + 0.1, "discourse", "model"))
        dl.append(_dl("s:?entity_read", f"v:n{i}", t + 0.1))
    for i in range(n_edit):
        t = 1000.0 + 10 * i
        ev.append(_ev(f"v:e{i}", t, "observation", "tool", "entity_edit"))
        dl += [_dl("tool:entity_edit", f"v:e{i}", t), _dl(f"e:v:e{i}", f"v:e{i}", t)]
        # the component's own script fires 50 ms later: a MOTOR answer, owned by
        # an event that is neither the act nor the seat's narration
        ev.append(_ev(f"v:k{i}", t + 0.05, "observation", "", "tick", "script:tick"))
        dl.append(_dl("s:?script_data", f"v:k{i}", t + 0.05))
    for i in range(20):
        t = 2000.0 + 20 * i
        ev.append(_ev(f"v:u{i}", t, "discourse", "user"))
        dl.append(_dl("person:operator", f"v:u{i}", t))
        ev.append(_ev(f"v:b{i}", t + 2.0, "observation", "tool", "entity_read"))
        dl.append(_dl("tool:entity_read", f"v:b{i}", t + 2.0))
        ev.append(_ev(f"v:w{i}", t + 2.5, "discourse", "model"))
        dl.append(_dl("m:memo:self", f"v:w{i}", t + 2.5))
    return ev, sorted(dl, key=lambda d: d["ts"]), sorted(ev, key=lambda e: e[1]["ts"])


class TestTheReplayDropsTheSeatsOwnFootprintAndWords(unittest.TestCase):
    def setUp(self):
        self.ev, self.dl, _ = _mini()

    def test_the_agents_acts_are_its_tool_calls(self):
        a = R.acts(self.ev, R.is_agent_act)
        # 30 reads + 24 edits + the 20 tool calls the seat reaches for after the
        # operator speaks; the operator's own turns are not among them
        self.assertEqual(len(a), 74)
        self.assertEqual(sorted(set(k for _, k in a)), ["entity_edit", "entity_read"])

    def test_the_act_shadow_is_not_an_answer(self):
        ch = R.pulses(self.ev, self.dl, footprint=R.act_keys(self.ev, R.is_agent_act),
                      narration=True)
        eids = set(e for _, e in ch)
        self.assertIn("s:?script_data", eids)          # the motor answer survives
        self.assertNotIn("tool:entity_read", eids)     # the act's own footprint
        self.assertNotIn("tool:entity_edit", eids)
        self.assertNotIn("s:?entity_read", eids)       # owned by the seat's words
        self.assertNotIn("m:memo:self", eids)
        self.assertNotIn("e:v:e0", eids)

    def test_the_words_are_counted_when_we_ask_who_owns_the_pulses(self):
        own = dict(R.authors(self.ev, self.dl))
        self.assertEqual(own["the seat's own words"], 50)        # narration pulses
        self.assertEqual(own["the agent's tool calls"], 128)     # 74 acts' own pulses
        self.assertEqual(own["the operator"], 20)
        self.assertEqual(own["the seat's machinery"], 24)        # the tick answers

    def test_the_mention_share_is_reported(self):
        ev = self.ev + [("v:x", {"stream": "discourse", "role": "model", "ts": 9.0,
                                 "mentions": [["tool:entity_read", 0, "x"]], "src": "live"})]
        n_mentioned, n_all = R.mention_share(ev, [_dl("tool:entity_read", "v:x", 9.0)])
        self.assertEqual((n_mentioned, n_all), (1, 1))


class TestTheBodyMapOverTheMiniHistory(unittest.TestCase):
    def setUp(self):
        self.ev, self.dl, _ = _mini()
        self.out = R.replay(self.ev, self.dl, window=2.0)

    def test_only_the_motor_answer_is_body(self):
        self.assertEqual(self.out["cls"]["body"], ["s:?script_data"])

    def test_the_act_shadow_is_not_a_body_part(self):
        body = set(self.out["cls"]["body"])
        self.assertFalse(body & {"tool:entity_read", "tool:entity_edit", "s:?entity_read"})

    def test_the_seat_is_not_a_body_part_of_its_own_words(self):
        # m:memo:self only ever moved because the seat's narration named it
        self.assertNotIn("m:memo:self", self.out["cls"]["body"])

    def test_a_shuffled_history_finds_no_body(self):
        null = R.replay(self.ev, self.dl, window=2.0, shuffle_seed=1)
        self.assertEqual(null["cls"]["body"], [])

    def test_the_operator_turn_answers_in_behaviour(self):
        # the control: acts = the operator speaking. The seat's own narration is
        # still dropped -- and the answer is what the SEAT DOES (it reaches for
        # the tool), not what it says back.
        out = R.replay(self.ev, self.dl, window=2.0, act=R.is_user_act)
        body, other = out["cls"]["body"], out["cls"]["other"]
        self.assertIn("tool:entity_read", set(body) | set(other))
        self.assertNotIn("m:memo:self", set(body) | set(other))


class TestTheSnapshotLeavesTheSeatAlone(unittest.TestCase):
    def test_a_snapshot_copies_the_store_and_never_writes_to_it(self):
        # the live seat is writing into its store while the lab reads: the copy
        # must be a copy (SQLite's own backup) and the live bytes must not move
        import sqlite3
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        live = tmp / "field.cblite" / "field.cblite2"
        live.mkdir(parents=True)
        db = live / "db.sqlite3"
        con = sqlite3.connect(db)
        con.execute("create table t (k text)")
        con.execute("insert into t values ('a pulse')")
        con.commit()
        con.close()
        before = db.read_bytes()
        out = R.snapshot(tmp / "field.cblite", tmp / "snap")
        copy = out / "field.cblite2" / "db.sqlite3"
        self.assertTrue(copy.exists(), "the snapshot kept the store's layout")
        con = sqlite3.connect(copy)
        self.assertEqual(con.execute("select k from t").fetchall(), [("a pulse",)])
        con.close()
        self.assertEqual(db.read_bytes(), before, "the live store was not written to")


if __name__ == "__main__":
    unittest.main()
