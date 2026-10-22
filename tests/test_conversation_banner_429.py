"""THE CONVERSATION BANNER (#429, step 3 of the PR split's banner work):
behind MEMBRANE_INJECTION=conversation the per-turn block carries THIS
conversation's state, derived from records only (no LLM):

  NOW (one line, local date time + tz), then
  WORKING ON  the person's latest earlier ask in this chat, clipped 160
  ABOUT       focus entities: in >= 2 of this chat's events or named in the
              person's latest message; top 5
  DECIDED     this chat's decisions: the operator ledger, goals fulfilled
              here, coproc decide() records; newest 3
  OPEN        goals planted in this chat and still open; newest 3
  STABLE      facts seen in >= 3 chats over >= 2 days, never contradicted,
              about a focus entity; 3
  SINCE YOUR LAST MESSAGE  focus entities moved from outside this chat;
              3 lines x 200 chars
  ALERT       at most 2 per turn (the rest ride the next turns)
  MORE        the pull pointer (only when the request carries tools)

Every DECIDED / OPEN / STABLE / SINCE line says when, as a relative age with
its source. The whole block stays within 900 chars; an empty section is
omitted; a block with nothing of this conversation is not sent. It refreshes
every turn (no 8-turn delta gate). The old banner stays the default.
"""
import os
import re
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import capture as C  # noqa: E402
from membrane import conv_banner as CB  # noqa: E402
from membrane import lanes as L  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC).timestamp()   # a Thursday, 15:00 UTC
H = 3600.0
D = 86400.0


def _field():
    from membrane import efield as EF
    from membrane.efstore import MemBackend
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


def _turn(f, conv, q, a="", ts=NOW - 600, session="s:1:0", calls=None, results=None):
    from membrane import streams as ST
    f.write_events(ST.split_turn(q, a, calls=calls, results=results, ts=ts),
                   session=session, conv=conv)


def _goal(est, slug, name, **props):
    from membrane.entities import CLASS_GOAL, MEMBRANE_NS, QuantumEntity
    g = QuantumEntity(eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
                      properties={"name": name, "summary": name, "status": "open", **props})
    est.docs[g.eid] = g
    return g


def _seat(tmp):
    from membrane.coproc import Store
    from membrane.entities import EntityStore
    from membrane.ledger import UserLedger
    f = _field()
    srv = types.SimpleNamespace()
    srv.efield = f
    srv.capturer = types.SimpleNamespace(mode="on", sessions=None)
    srv.entity_store = EntityStore(path=str(Path(tmp) / "entities.json"))
    srv.user_ledger = UserLedger(str(Path(tmp) / "field.ledger.jsonl"))
    srv.coproc_store = Store(None)
    srv.paused = False
    srv.membrane = types.SimpleNamespace(audit_path=str(Path(tmp) / "audit.jsonl"),
                                         embedder=None, field_path=str(Path(tmp) / "field.json"))
    return srv, f


def _ledger(srv, text, conv, ts):
    import json
    iso = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(srv.user_ledger.path, "a") as fh:
        fh.write(json.dumps({"ts": iso, "text": text, "conv": conv}) + "\n")


def _decide(srv, text, conv, ts, who="person"):
    srv.coproc_store.append({"kind": "action", "id": srv.coproc_store.next_id(), "ts": ts,
                             "conv": conv, "model": "m", "word": "decide",
                             "args": {"text": text}, "status": "applied", "who": who})


def _remember(srv, key, value, conv, ts):
    srv.coproc_store.append({"kind": "action", "id": srv.coproc_store.next_id(), "ts": ts,
                             "conv": conv, "model": "m", "word": "remember",
                             "args": {"key": key, "value": value}, "status": "applied",
                             "who": "person"})


class TestAges(unittest.TestCase):
    """The operator's buckets: just now (<5 min), N min, N h (<12 h), this
    morning / this afternoon / yesterday, N days (<7), then the date."""

    def age(self, dt, now=NOW):
        return CB.age(now - dt, now, tz=UTC)

    def test_buckets(self):
        self.assertEqual(self.age(0), "just now")
        self.assertEqual(self.age(299), "just now")
        self.assertEqual(self.age(300), "5 min ago")
        self.assertEqual(self.age(59 * 60 + 59), "59 min ago")
        self.assertEqual(self.age(H), "1h ago")
        self.assertEqual(self.age(11 * H + 3599), "11h ago")
        self.assertEqual(self.age(12 * H), "this morning")          # 03:00 today
        self.assertEqual(self.age(19 * H), "yesterday")            # 20:00 yesterday
        self.assertEqual(self.age(2 * D), "2 days ago")
        self.assertEqual(self.age(6 * D), "6 days ago")
        self.assertEqual(self.age(7 * D), "2026-10-15")

    def test_afternoon_and_short_hops_over_midnight(self):
        late = datetime(2026, 10, 22, 23, 59, tzinfo=UTC).timestamp()
        # 12h+ ago on the same day before noon -> this morning; a same-day
        # afternoon time is always < 12h back, so it reads as hours
        self.assertEqual(CB.age(late - 12 * H - 60, late, tz=UTC), "this morning")
        self.assertEqual(CB.age(late - 10 * H, late, tz=UTC), "10h ago")
        early = datetime(2026, 10, 22, 1, 0, tzinfo=UTC).timestamp()
        self.assertEqual(CB.age(early - 2 * H, early, tz=UTC), "2h ago")   # 23:00 yesterday
        noon_next = datetime(2026, 10, 23, 13, 0, tzinfo=UTC).timestamp()
        # an afternoon > 12h back on an earlier day is yesterday, never 'this afternoon'
        self.assertEqual(CB.age(noon_next - 22 * H, noon_next, tz=UTC), "yesterday")
        # same day, after noon, >= 12h: only reachable in theory; it says afternoon
        self.assertEqual(CB._bucket(datetime(2026, 10, 22, 13, 0, tzinfo=UTC),
                                    datetime(2026, 10, 22, 23, 59, tzinfo=UTC), 13 * H),
                         "this afternoon")

    def test_a_future_or_missing_stamp(self):
        self.assertEqual(CB.age(NOW + 120, NOW, tz=UTC), "just now")
        self.assertEqual(CB.age(None, NOW, tz=UTC), "")

    def test_now_line(self):
        self.assertEqual(CB.now_line(NOW, tz=UTC), "NOW: 2026-10-22 15:00 UTC")
        cet = timezone(timedelta(hours=2), "CEST")
        self.assertEqual(CB.now_line(NOW, tz=cet), "NOW: 2026-10-22 17:00 CEST")


class TestTheConstants(unittest.TestCase):
    def test_the_operator_approved_values(self):
        self.assertEqual(CB.BANNER_CHAR_BUDGET, 900)
        self.assertEqual(CB.FOCUS_K, 5)
        self.assertEqual(CB.FOCUS_MIN_EVENTS, 2)
        self.assertEqual(CB.DECIDED_MAX, 3)
        self.assertEqual(CB.OPEN_MAX, 3)
        self.assertEqual(CB.STABLE_MAX, 3)
        self.assertEqual(CB.STABLE_MIN_CONVS, 3)
        self.assertEqual(CB.STABLE_MIN_DAYS, 2)
        self.assertEqual(CB.NEWS_MAX, 3)
        self.assertEqual(CB.NEWS_CHARS, 200)
        self.assertEqual(CB.ALERTS_MAX, 2)
        self.assertEqual(CB.WORKING_ON_CHARS, 160)


class TestRender(unittest.TestCase):
    def r(self, **kw):
        return CB.render(kw, NOW, tz=UTC)

    def test_nothing_of_this_conversation_is_no_block(self):
        self.assertIsNone(self.r())
        # framing alone (the entities a one-off question names, the pull
        # pointer) is not content: a fresh one-off question gets no block
        self.assertIsNone(self.r(about=["f:a.py"], more=True))

    def test_the_shape(self):
        out = self.r(working=CB.Item("make the backup skip big files", "person", NOW - 400),
                     about=["f:tools/backup.py", "s:should_skip"],
                     decided=[CB.Item("size limit stays 2 GB", "person", NOW - 2 * H)],
                     open=[CB.Item("report skipped files", "goal", NOW - 30 * 60)],
                     stable=[CB.Item("backups run at 02:00", "fact · 3 chats", NOW - 19 * H)],
                     since=[CB.Item("f:tools/backup.py: edited", "code", NOW - 60)],
                     alerts=["ALERT: disk crossed its boundary (90%)"],
                     more=True)
        lines = out.split("\n")
        self.assertEqual(lines[0], "NOW: 2026-10-22 15:00 UTC")
        self.assertTrue(lines[1].startswith("THIS CONVERSATION"))
        self.assertIn('WORKING ON: "make the backup skip big files" (person · 6 min ago)', lines)
        self.assertIn("ABOUT: f:tools/backup.py · s:should_skip", lines)
        self.assertIn("DECIDED:", lines)
        self.assertIn("- size limit stays 2 GB (person · 2h ago)", lines)
        self.assertIn("- report skipped files (goal · 30 min ago)", lines)
        self.assertIn("- backups run at 02:00 (fact · 3 chats · yesterday)", lines)
        self.assertIn("SINCE YOUR LAST MESSAGE:", lines)
        self.assertIn("- f:tools/backup.py: edited (code · just now)", lines)
        self.assertIn("ALERT: disk crossed its boundary (90%)", lines)
        self.assertTrue(lines[-1].startswith("MORE: state(section="))
        self.assertLessEqual(len(out), 900)

    def test_an_empty_section_is_omitted(self):
        out = self.r(decided=[CB.Item("x stays", "person", NOW - 60)])
        self.assertNotIn("OPEN:", out)
        self.assertNotIn("STABLE:", out)
        self.assertNotIn("SINCE YOUR LAST MESSAGE", out)
        self.assertNotIn("ABOUT:", out)
        self.assertNotIn("MORE:", out)

    def test_the_budget_holds_with_every_section_full(self):
        long = "word " * 80
        out = self.r(working=CB.Item(long, "person", NOW - 60),
                     about=[f"f:dir/file_{i}.py" for i in range(5)],
                     decided=[CB.Item(long, "person", NOW - i) for i in range(3)],
                     open=[CB.Item(long, "goal", NOW - i) for i in range(3)],
                     stable=[CB.Item(long, "fact · 3 chats", NOW - i) for i in range(3)],
                     since=[CB.Item(long, "code", NOW - i) for i in range(3)],
                     alerts=["ALERT: " + long, "ALERT: " + long], more=True)
        self.assertLessEqual(len(out), CB.BANNER_CHAR_BUDGET)
        self.assertIn("NOW:", out)
        self.assertIn("WORKING ON:", out)
        # every record line that made it still says when and where from
        for ln in out.split("\n"):
            if ln.startswith("- "):
                self.assertRegex(ln, r"\((person|goal|fact · 3 chats|code) · [^)]+\)$")

    def test_working_on_clips_at_160(self):
        out = self.r(working=CB.Item("a" * 400, "person", NOW - 60))
        ln = next(x for x in out.split("\n") if x.startswith("WORKING ON:"))
        quoted = ln[len('WORKING ON: "'):ln.index('" (')]
        self.assertLessEqual(len(quoted), 160)

    def test_since_lines_clip_at_200(self):
        out = self.r(since=[CB.Item("f:a.py: " + "b" * 500, "code", NOW - 60)])
        ln = next(x for x in out.split("\n") if x.startswith("- f:a.py"))
        self.assertLessEqual(len(ln[2:ln.rindex(" (")]), 200)

    def test_caps(self):
        many = [CB.Item(f"d{i}", "person", NOW - i) for i in range(6)]
        out = self.r(decided=many, open=many, since=many, alerts=[f"ALERT: a{i}" for i in range(5)])
        body = out.split("\n")
        self.assertEqual(sum(1 for x in body if x.startswith("ALERT")), 2)
        sec = lambda name: body[body.index(name) + 1:]   # noqa: E731
        for name in ("DECIDED:", "OPEN:", "SINCE YOUR LAST MESSAGE:"):
            rows = []
            for x in sec(name):
                if not x.startswith("- "):
                    break
                rows.append(x)
            self.assertEqual(len(rows), 3, name)


class TestGather(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv, self.f = _seat(self.tmp)

    def tearDown(self):
        C.set_conversation("")

    def test_working_on_is_the_latest_earlier_person_ask(self):
        _turn(self.f, "sid:a", "make the backup job skip files over 2 GB", "ok", ts=NOW - 900)
        _turn(self.f, "sid:a", "[membrane] job #12 finished", ts=NOW - 800)
        _turn(self.f, "sid:a", "and log what it skipped?", "sure", ts=NOW - 700)
        _turn(self.f, "sid:b", "an unrelated chat about lunch", ts=NOW - 100)
        evs = CB.conv_events(self.f, "sid:a")
        w = CB.working_on(evs, "now write the test")
        self.assertEqual(w.text, "and log what it skipped?")
        self.assertEqual(w.src, "person")
        self.assertAlmostEqual(w.ts, NOW - 700, delta=1)
        # the current message itself is not 'what we were working on'
        w2 = CB.working_on(evs, "and log what it skipped?")
        self.assertEqual(w2.text, "make the backup job skip files over 2 GB")
        self.assertIsNone(CB.working_on(CB.conv_events(self.f, "sid:new"), "hi"))

    def test_focus_needs_two_events_or_the_latest_message(self):
        _turn(self.f, "sid:a", "look at `tools/backup.py` please", ts=NOW - 900)
        _turn(self.f, "sid:a", "now `tools/backup.py` again, and `notes/once.md`", ts=NOW - 800)
        _turn(self.f, "sid:b", "`tools/backup.py` `other/x.py` `other/x.py`", ts=NOW - 700)
        evs = CB.conv_events(self.f, "sid:a")
        foc = CB.focus(self.f, evs, "what about `lib/new.py`?")
        self.assertIn("f:lib/new.py", foc)                 # named in the latest message
        self.assertIn("f:tools/backup.py", foc)            # 2 events of this chat
        self.assertNotIn("f:notes/once.md", foc)           # 1 event only
        self.assertNotIn("f:other/x.py", foc)              # another chat's
        self.assertEqual(foc[0], "f:lib/new.py")           # the latest message leads

    def test_focus_counts_tool_args_and_keeps_tool_names_out(self):
        calls = {0: {"id": "c1", "function": {"name": "read",
                                              "arguments": '{"filePath": "tools/backup.py"}'}}}
        _turn(self.f, "sid:a", "check it", calls=calls, ts=NOW - 900)
        calls2 = {0: {"id": "c2", "function": {"name": "read",
                                               "arguments": '{"filePath": "tools/backup.py"}'}}}
        _turn(self.f, "sid:a", "and again", calls=calls2, ts=NOW - 800)
        foc = CB.focus(self.f, CB.conv_events(self.f, "sid:a"), "go on")
        self.assertIn("f:tools/backup.py", foc)
        self.assertFalse(any(x.startswith("tool:") for x in foc))

    def test_focus_caps_at_five(self):
        names = " ".join(f"`d/f{i}.py`" for i in range(8))
        _turn(self.f, "sid:a", "look at " + names, ts=NOW - 900)
        _turn(self.f, "sid:a", "again " + names, ts=NOW - 800)   # two events (a same text is one)
        self.assertEqual(len(CB.focus(self.f, CB.conv_events(self.f, "sid:a"), "ok")), 5)

    def test_decided_comes_from_this_chats_records_newest_first(self):
        _ledger(self.srv, "size limit stays 2 GB", "sid:a", NOW - 3 * H)
        _ledger(self.srv, "another chat's decision", "sid:b", NOW - 60)
        _decide(self.srv, "keep the mxbai embedder for now", "sid:a", NOW - 2 * H)
        _decide(self.srv, "the other chat decided this", "sid:b", NOW - 30)
        _goal(self.srv.entity_store, "skip", "backup skips huge files", status="fulfilled",
              fulfilled_ts=NOW - H, fulfilled_conv="sid:a", convs=["sid:a"])
        _goal(self.srv.entity_store, "other", "other chat goal", status="fulfilled",
              fulfilled_ts=NOW - 10, fulfilled_conv="sid:b")
        _decide(self.srv, "oldest of four", "sid:a", NOW - 5 * H)
        got = CB.decided(self.srv, "sid:a")
        self.assertEqual([i.text for i in got],
                         ["goal fulfilled: backup skips huge files",
                          "keep the mxbai embedder for now", "size limit stays 2 GB"])
        self.assertEqual([i.src for i in got], ["goal", "person", "person"])
        self.assertEqual(CB.decided(self.srv, ""), [])

    def test_open_is_this_chats_open_goals(self):
        _goal(self.srv.entity_store, "a", "report skipped files", born=NOW - H, convs=["sid:a"])
        _goal(self.srv.entity_store, "b", "a goal from elsewhere", born=NOW - 60, convs=["sid:b"])
        _goal(self.srv.entity_store, "c", "done here", born=NOW - H, convs=["sid:a"],
              status="fulfilled")
        got = CB.open_items(self.srv, "sid:a")
        self.assertEqual([(i.text, i.src) for i in got], [("report skipped files", "goal")])

    def test_stable_needs_three_chats_two_days_and_no_contradiction(self):
        for i, c in enumerate(("sid:a", "sid:b", "sid:c")):
            _remember(self.srv, "backup.py schedule", "02:00", c, NOW - 3 * D + i * D)
        # two chats only
        for c in ("sid:a", "sid:b"):
            _remember(self.srv, "backup.py owner", "ops", c, NOW - 2 * D)
        # three chats, one day
        for c in ("sid:a", "sid:b", "sid:c"):
            _remember(self.srv, "backup.py format", "tar", c, NOW - 2 * H)
        # three chats over days, then contradicted
        for i, c in enumerate(("sid:a", "sid:b", "sid:c")):
            _remember(self.srv, "backup.py target", "s3", c, NOW - 4 * D + i * D)
        _remember(self.srv, "backup.py target", "nas", "sid:d", NOW - H)
        got = CB.stable(self.srv, ["f:backup.py"], self.f)
        self.assertEqual([i.text for i in got], ["backup.py schedule: 02:00"])
        self.assertEqual(got[0].src, "fact · 3 chats")
        # a fact about nothing this chat is about is not shown
        self.assertEqual(CB.stable(self.srv, ["f:other.py"], self.f), [])

    def test_since_is_focus_news_from_outside_this_chat(self):
        from membrane.entity_block import watermark
        _turn(self.f, "sid:a", "edit `tools/backup.py`", ts=NOW - 900)
        _turn(self.f, "sid:a", "`tools/backup.py` once more", ts=NOW - 800)
        self.f.process()
        mark = watermark(self.f)
        _turn(self.f, "sid:a", "`tools/backup.py` from this chat itself", ts=NOW - 120)
        _turn(self.f, "sid:b", "`tools/backup.py` was changed in another chat", ts=NOW - 60,
              session="s:1:1")
        _turn(self.f, "sid:b", "`unrelated/z.py` moved elsewhere", ts=NOW - 50, session="s:1:1")
        self.f.process()
        got = CB.since(self.f, "sid:a", mark, ["f:tools/backup.py"])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].text.startswith("f:tools/backup.py: "))
        self.assertIn("another chat", got[0].text)
        self.assertEqual(got[0].src, "another chat")
        self.assertNotRegex(got[0].text, r"#\d")             # never an event number
        self.assertEqual(CB.since(self.f, "sid:a", None, ["f:tools/backup.py"]), [])

    def test_alerts_two_per_turn_the_rest_next(self):
        est = types.SimpleNamespace(tracks={"x": {}}, take_alerts=lambda: [
            {"label": f"t{i}", "kind": "crossed", "boundary": "b", "detail": "d"} for i in range(3)])
        srv = types.SimpleNamespace(entity_store=est)
        first = CB.take_alerts(srv)
        self.assertEqual(len(first), 2)
        est.take_alerts = lambda: []
        second = CB.take_alerts(srv)
        self.assertEqual(len(second), 1)
        self.assertIn("t2", second[0])
        self.assertEqual(CB.take_alerts(srv), [])


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv, self.f = _seat(self.tmp)
        _turn(self.f, "sid:a", "make `tools/backup.py` skip files over 2 GB", "ok", ts=NOW - 900)
        _turn(self.f, "sid:a", "and have `tools/backup.py` log what it skipped", "sure", ts=NOW - 700)
        _ledger(self.srv, "size limit stays 2 GB", "sid:a", NOW - 800)
        _goal(self.srv.entity_store, "rep", "report skipped files in the digest",
              born=NOW - 650, convs=["sid:a"])
        self.f.process()

    def body(self, text, tools=False):
        b = {"messages": [{"role": "user", "content": text}]}
        if tools:
            b["tools"] = [{"type": "function", "function": {"name": "state"}}]
        return b

    def test_a_conversation_gets_its_own_state(self):
        out = CB.build(self.srv, self.body("what's next?"), "sid:a", now=NOW, tz=UTC)
        self.assertTrue(out.startswith("NOW: 2026-10-22 15:00 UTC\nTHIS CONVERSATION"))
        self.assertIn('WORKING ON: "and have `tools/backup.py` log what it skipped"', out)
        self.assertIn("ABOUT: f:tools/backup.py", out)
        self.assertIn("- size limit stays 2 GB (person · 13 min ago)", out)
        self.assertIn("- report skipped files in the digest (goal · 10 min ago)", out)
        self.assertNotIn("MORE:", out)                       # no tools on the request
        self.assertLessEqual(len(out), 900)
        # no seat machinery: no tracks, command counts, class proposals, event numbers
        for junk in ("track:", "your commands", "class proposals", "WHERE WE ARE"):
            self.assertNotIn(junk, out)
        self.assertNotRegex(out, r"#\d+")

    def test_the_pull_pointer_rides_only_with_tools(self):
        out = CB.build(self.srv, self.body("go on", tools=True), "sid:a", now=NOW, tz=UTC)
        self.assertIn("MORE: state(section=", out)

    def test_another_conversation_sees_none_of_it(self):
        self.assertIsNone(CB.build(self.srv, self.body("hello there"), "sid:zz", now=NOW, tz=UTC))
        self.assertIsNone(CB.build(self.srv, self.body("hello there"), "", now=NOW, tz=UTC))

    def test_since_waits_for_this_chats_first_delivery(self):
        from membrane.entity_block import watermark
        out = CB.build(self.srv, self.body("go"), "sid:a", now=NOW, tz=UTC)
        self.assertNotIn("SINCE YOUR LAST MESSAGE", out)
        CB.note_delivered(self.srv, "sid:a", watermark(self.f))
        _turn(self.f, "sid:b", "`tools/backup.py` was rewritten by the other chat",
              ts=NOW - 30, session="s:1:1")
        self.f.process()
        out = CB.build(self.srv, self.body("go"), "sid:a", now=NOW, tz=UTC)
        self.assertIn("SINCE YOUR LAST MESSAGE:", out)
        self.assertIn("rewritten by the other chat (another chat · just now)", out)

    def test_paused_rides(self):
        self.srv.paused = True
        with mock.patch.object(L, "nerve_paused", lambda srv: True):
            out = CB.build(self.srv, self.body("hi"), "sid:zz", now=NOW, tz=UTC)
        self.assertIn("PAUSED:", out)


class TestTheTurnPath(unittest.TestCase):
    """lanes._state_ctx: the old banner (default) vs the conversation block."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv, self.f = _seat(self.tmp)
        _turn(self.f, "sid:a", "make `tools/backup.py` skip big files", "ok", ts=time.time() - 900)
        _ledger(self.srv, "size limit stays 2 GB", "sid:a", time.time() - 800)
        self.f.process()
        self.srv.no_banner = False
        self.srv.no_context = False
        self.srv._recall_null = None
        self.srv.peers = None

    def tearDown(self):
        C.set_conversation("")

    def ctx(self, body, mode):
        self.srv.injection_mode = mode
        C.set_conversation("sid:a")
        return L._state_ctx(self.srv, body, None, False)

    def test_the_mode_switch(self):
        s = types.SimpleNamespace(injection_mode=None)
        with mock.patch.dict(os.environ, {"MEMBRANE_INJECTION": "conversation"}):
            self.assertEqual(L._injection_mode(s), "conversation")
        with mock.patch.dict(os.environ, {"MEMBRANE_INJECTION": "massage"}):
            self.assertEqual(L._injection_mode(s), "massage")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEMBRANE_INJECTION", None)
            self.assertEqual(L._injection_mode(s), "banner")      # the default stays

    def test_conversation_mode_sends_only_the_conversation_block(self):
        body = {"messages": [{"role": "user", "content": "what's next?"}]}
        ctx, _mark = self.ctx(body, "conversation")
        self.assertIn("THIS CONVERSATION", ctx)
        self.assertIn("size limit stays 2 GB", ctx)
        for old in ("WHERE WE ARE", "OPEN INTENTIONS", "THE INTENT BOARD IS EMPTY",
                    "SINCE YOUR LAST MESSAGE (membrane's field"):
            self.assertNotIn(old, ctx)

    def test_it_refreshes_every_turn(self):
        body = {"messages": [{"role": "user", "content": "what's next?"}]}
        outs = [self.ctx(body, "conversation")[0] for _ in range(3)]
        self.assertTrue(all(o and "THIS CONVERSATION" in o for o in outs))

    def test_the_surface_ring_names_the_block(self):
        body = {"messages": [{"role": "user", "content": "what's next?"}]}
        ctx, _ = self.ctx(body, "conversation")
        secs = L._surface_block_summary(ctx)
        self.assertEqual([s["block"] for s in secs], ["conversation"])
        self.assertEqual(secs[0]["chars"], len(ctx))

    def test_the_old_banner_path_is_untouched(self):
        body = {"messages": [{"role": "user", "content": "what's next?"}]}
        ctx, _ = self.ctx(body, "banner")
        self.assertNotIn("THIS CONVERSATION", ctx or "")


class TestRecordsCarryTheConversation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv, self.f = _seat(self.tmp)

    def tearDown(self):
        C.set_conversation("")

    def test_the_ledger_keeps_the_chat(self):
        C.set_conversation("sid:a")
        L._learn_report(self.srv, "size limit stays 2 GB")
        ent = self.srv.user_ledger.entries()
        self.assertEqual(ent[-1]["conv"], "sid:a")
        self.assertEqual(ent[-1]["text"], "size limit stays 2 GB")

    def test_a_planted_goal_and_its_fulfilment_name_the_chat(self):
        from membrane.intent import IntentBoard
        self.srv._intent_board = IntentBoard(Path(self.tmp) / "intents.json")
        self.srv.events_pool = None
        C.set_conversation("sid:a")
        res = L._membrane_intent_create_or_pin(self.srv, "report skipped files", planted_by="operator")
        g = self.srv.entity_store.docs[res["eid"]]
        self.assertEqual(g.properties["convs"], ["sid:a"])
        C.set_conversation("sid:b")
        L._membrane_intent_create_or_pin(self.srv, "report skipped files", planted_by="operator")
        self.assertEqual(g.properties["convs"], ["sid:a", "sid:b"])
        L._intent_cmd(self.srv, "membrane: fulfill report skipped")
        self.assertEqual(g.properties["status"], "fulfilled")
        self.assertEqual(g.properties["fulfilled_conv"], "sid:b")

    def test_a_request_without_a_key_falls_back_to_the_last_chat(self):
        # the MCP tool's HTTP call carries no chat; the last chat request names it
        C.set_conversation("sid:last")
        import threading
        seen = []
        t = threading.Thread(target=lambda: seen.append(C.conversation_now()))
        t.start()
        t.join()
        self.assertEqual(seen, ["sid:last"])


if __name__ == "__main__":
    unittest.main()
