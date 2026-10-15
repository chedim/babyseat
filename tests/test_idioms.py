"""THE IDIOMS (2026-10-15): the seat's acts read as a language.

The gate (two seats' real fields; act types = the program a call runs; time split, a
shuffle-within-session null): most of what predicts the next act is BURSTINESS (more of
the same: 6.1 -> 2.4 bits/act), but ORDER carries real structure beyond it (+0.31
bits on one seat, null -0.11; +0.04 on the other, null -0.02) -- and it reads as
IDIOMS: grep -> read, read -> grep, grep <-> sed, python -> git; entity_add ->
entity_edit -> entity_watch. The previous act's outcome steers nothing beyond that.

field.idioms() -> the transitions to a DIFFERENT act within a session, ranked by lift
over chance; field.followed_by(eid) -> what a command or tool entity is usually
followed by (the card shows it). Recording only: an idiom never runs anything."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import fieldmap as FM  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]


def _call(session, t, tool, args, cid):
    return ST.Event(stream="observation", tool=tool, args=args, result_head=f"out {cid}",
                    outcome="ok", ts=t, call_id=cid)


def _world():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")
    t = 1000.0
    for i, w in enumerate(WORDS):
        evs = [_call(f"s{i}", t, "shell", {"command": f"grep -n {w} membrane/{w}.py"}, f"{i}a"),
               _call(f"s{i}", t + 1, "read", {"path": f"membrane/{w}.py"}, f"{i}b"),
               _call(f"s{i}", t + 2, "shell", {"command": f"ls -la /tmp/{w}"}, f"{i}c"),
               _call(f"s{i}", t + 3, "shell", {"command": f"ls /tmp/{w}/x"}, f"{i}d"),
               _call(f"s{i}", t + 4, "tick", {}, f"{i}e"),
               _call(f"s{i}", t + 5, "shell", {"command": f"git status {w}"}, f"{i}f")]
        f.write_events(evs, session=f"s{i}")
        t += 100
    f.process()
    return f


class TestTheIdioms(unittest.TestCase):
    def test_an_act_that_follows_another_is_an_idiom(self):
        f = _world()
        got = {(d["from"], d["to"]): d for d in f.idioms(min_n=3)}
        self.assertIn(("grep", "read"), got)
        self.assertEqual(got[("grep", "read")]["n"], len(WORDS))
        self.assertGreater(got[("grep", "read")]["lift"], 1.0)

    def test_more_of_the_same_is_not_an_idiom(self):
        f = _world()
        self.assertFalse([d for d in f.idioms(min_n=1) if d["from"] == d["to"]])

    def test_the_heartbeat_is_not_an_act(self):
        f = _world()
        pairs = {(d["from"], d["to"]) for d in f.idioms(min_n=1)}
        self.assertIn(("ls", "git"), pairs)               # across the tick
        self.assertFalse([p for p in pairs if "tick" in p])

    def test_a_command_entity_says_what_usually_follows_it(self):
        f = _world()
        nxt = f.followed_by("c:grep")
        self.assertEqual(nxt[0]["to"], "read")
        self.assertEqual(f.followed_by("tool:read")[0]["to"], "ls")

    def test_new_acts_are_seen_by_the_next_query(self):
        f = _world()
        f.idioms(min_n=1)                                  # builds the cache
        f.write_events([_call("z", 9000.0, "shell", {"command": "curl -s http://x"}, "z1"),
                        _call("z", 9001.0, "shell", {"command": "jq .a"}, "z2")], session="z")
        f.process()
        self.assertIn(("curl", "jq"), {(d["from"], d["to"]) for d in f.idioms(min_n=1)})

    def test_the_card_shows_it_for_commands_only(self):
        f = _world()
        st = f.get("c:grep")
        self.assertIsNotNone(st)
        card = FM.entity_card(st.to_doc(f.origin), [], [], [], followed_by=f.followed_by("c:grep"))
        self.assertEqual(card["followed_by"][0]["to"], "read")
        fst = f.get("f:membrane/alpha.py")
        if fst is not None:
            self.assertEqual(f.followed_by("f:membrane/alpha.py"), [])


if __name__ == "__main__":
    unittest.main()


class TestTheCountsAreKeptNotRescanned(unittest.TestCase):
    """2026-10-15, the live seat: a command's card took 6 s -- the idiom counts
    were rebuilt by rescanning every tool-call event whenever the field had
    written anything, and a live field writes constantly. The history is scanned
    ONCE; every tool call after that is counted as it is applied."""

    def _scans(self, f):
        real = f.b.scan
        seen = []

        def spy(coll, *a, **k):
            eq = (a[0] if a else k.get("eq")) or {}
            if coll == "events" and eq.get("stream") == "observation":   # the idioms' scan, not processing's
                seen.append(1)
            return real(coll, *a, **k)
        f.b.scan = spy
        return seen

    def test_new_acts_are_counted_without_a_rescan(self):
        f = _world()
        f.idioms(min_n=1)                                   # the one scan
        seen = self._scans(f)
        f.write_events([_call("z", 9000.0, "shell", {"command": "curl -s http://x"}, "z1"),
                        _call("z", 9001.0, "shell", {"command": "jq .a"}, "z2")], session="z")
        f.process()
        self.assertIn(("curl", "jq"), {(d["from"], d["to"]) for d in f.idioms(min_n=1)})
        f.followed_by("c:grep"); f.suggested_methods(min_n=1)
        self.assertEqual(seen, [], "the history was rescanned")

    def test_a_rebuild_never_counts_twice(self):
        f = _world()
        before = {(d["from"], d["to"]): d["n"] for d in f.idioms(min_n=1)}
        f.rebuild()
        self.assertEqual({(d["from"], d["to"]): d["n"] for d in f.idioms(min_n=1)}, before)
