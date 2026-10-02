"""SESSION ABSORPTION (part 1 of the autonode build): at boot the seat
looks for sessions that ran in external agents since it last ran, imports
them (chat rows + entity-field events) and auto-continues the newest.
THE HARD FILTER: sessions answered by Anthropic's commercial models are
ignored WHOLESALE -- eligible = Ollama-backed only, unknown model =
skip (fail-closed)."""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import membrane.absorb as AB  # noqa: E402
from membrane.server import MessageStore, SessionStore, TurnRunner, SSEHub  # noqa: E402

CLAUDE_UUID = "1c2f9a4e-0000-4000-8000-0000000000aa"
OPENCODE_SID = "ses_faad59406ffe0000aaaaaaaaaaaaaaaaaa"


# ---- fixture builders ------------------------------------------------------

def _claude_line(typ, ts="2026-10-01T10:00:00.000Z", **over):
    ln = {"type": typ, "sessionId": CLAUDE_UUID, "timestamp": ts,
          "cwd": "/x/proj", "isSidechain": False}
    ln.update(over)
    return ln


def _asst_line(ts, model, blocks, **over):
    msg = {"role": "assistant", "id": "msg_as1", "type": "message",
           "model": model, "content": blocks}
    return _claude_line("assistant", ts=ts, message=msg, **over)


def _user_text_line(ts, text, **over):
    return _claude_line("user", ts=ts,
                        message={"role": "user", "content": text}, **over)


def _tool_result_line(tool_use_id, content, ts, **over):
    return _claude_line(
        "user", ts=ts,
        message={"role": "user",
                 "content": [{"type": "tool_result", "tool_use_id":
                              tool_use_id, "content": content}]},
        **over)


def _write_claude_project(home: Path, lines, uuid=CLAUDE_UUID) -> Path:
    proj = home / "-x-proj"
    proj.mkdir(parents=True, exist_ok=True)
    fp = proj / f"{uuid}.jsonl"
    with open(fp, "w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")
    return fp


def _mk_opencode_db(path: Path, sess_rows) -> None:
    """sess_rows: [{sid, directory, title, model, updated_ms, msgs: [
    {id, role, ts_ms, providerID, modelID, parts: [...]}]}]"""
    conn = sqlite3.connect(os.fspath(path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT,"
                 " title TEXT, agent TEXT, model TEXT, time_created INT,"
                 " time_updated INT)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,"
                 " time_created INT, time_updated INT, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT,"
                 " session_id TEXT, time_created INT, time_updated INT,"
                 " data TEXT)")
    for s in sess_rows:
        created = s.get("updated_ms") - 60000
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?)",
                     (s["sid"], s["directory"], s["title"], "build",
                      json.dumps(s["model"]), created, s["updated_ms"]))
        for i, m in enumerate(s["msgs"]):
            data = {"role": m["role"], "time": {"created": m["ts_ms"]}}
            if m["role"] == "assistant":
                if m.get("providerID"):
                    data["providerID"] = m["providerID"]
                if m.get("modelID"):
                    data["modelID"] = m["modelID"]
            conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                         (m["id"], s["sid"], m["ts_ms"], m["ts_ms"] + 100,
                          json.dumps(data)))
            for j, p in enumerate(m["parts"]):
                conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                             (f"prt_{s['sid'][:8]}_{i}{j}", m["id"], s["sid"],
                              m["ts_ms"] + j, m["ts_ms"] + j,
                              json.dumps(p)))
    conn.commit()
    conn.close()


# ---- the hard filter -------------------------------------------------------

class TestFilter(unittest.TestCase):
    def test_anthropic_models_fail_closed(self):
        self.assertTrue(AB._is_anthropic_model("claude-sonnet-4-5"))
        self.assertTrue(AB._is_anthropic_model("anthropic/gpt"))
        self.assertTrue(AB._is_anthropic_model(""))
        self.assertTrue(AB._is_anthropic_model(None))
        self.assertFalse(AB._is_anthropic_model("qwen3-coder:latest"))
        self.assertFalse(AB._is_anthropic_model(
            "Qwen3-Coder:480b-cloud"))
        self.assertFalse(AB._is_anthropic_model("glm-5.3-flash:cloud"))

    def test_a_claude_session_on_anthropic_models_is_dropped(self):
        """THE OPERATOR'S RULE: one claude-* assistant line poisons the
        WHOLE session -- it is never imported, not even partially."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_claude_project(home, [
                _user_text_line("2026-10-01T10:00:00.000Z", "hello"),
                _asst_line("2026-10-01T10:00:05.000Z", "claude-opus-4-6",
                           [{"type": "text", "text": "the answer"}]),
                _user_text_line("2026-10-01T10:01:00.000Z", "more"),
                _asst_line("2026-10-01T10:01:05.000Z", "qwen3-coder",
                           [{"type": "text", "text": "fine"}]),
            ])
            self.assertEqual(AB.claude_sessions("/x/proj", 0, home=home), [])

    def test_a_claude_session_with_unknown_model_is_dropped(self):
        """FAIL-CLOSED: an assistant line with no model id means we
        cannot prove it was not Anthropic -> drop."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_claude_project(home, [
                _user_text_line("2026-10-01T10:00:00.000Z", "hi"),
                {"type": "assistant", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:05.000Z",
                 "message": {"role": "assistant", "id": "m1",
                             "content": [{"type": "text", "text": "?"}]}},
            ])
            self.assertEqual(AB.claude_sessions("/x/proj", 0, home=home), [])

    def test_an_ollama_backed_claude_session_survives(self):
        """`ollama launch claude` -- Claude Code on local models -- is
        exactly the case absorption wants."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_claude_project(home, [
                _claude_line("ai-title", title="the clock work"),
                _user_text_line("2026-10-01T10:00:00.000Z", "who made the clock?"),
                _asst_line("2026-10-01T10:00:03.000Z", "qwen3-coder:latest", [
                    {"type": "thinking", "thinking": "it was dmitrii"},
                    {"type": "text", "text": "the operator planted it"}]),
            ])
            got = AB.claude_sessions("/x/proj", 0, home=home)
            self.assertEqual(len(got), 1)
            a = got[0]
            self.assertEqual(a.source, "claude")
            self.assertEqual(a.title, "the clock work")
            self.assertEqual(a.model_id, "qwen3-coder:latest")
            self.assertFalse(AB._is_anthropic_model(a.model_id))
            self.assertEqual([r.role for r in a.rows], ["user", "assistant"])
            self.assertEqual(a.rows[0].text, "who made the clock?")
            self.assertEqual(a.rows[1].text, "the operator planted it")
            self.assertEqual(a.rows[1].thought, "it was dmitrii")


# ---- collectors ------------------------------------------------------------

class TestClaudeCollector(unittest.TestCase):
    def test_tool_use_and_tool_result_pair(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_claude_project(home, [
                _user_text_line("2026-10-01T10:00:00.000Z", "list the files"),
                _asst_line("2026-10-01T10:00:02.000Z", "qwen3-coder", [
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                     "input": {"command": "ls"}}]),
                _tool_result_line("toolu_1", "router.py\nseat.py",
                                  "2026-10-01T10:00:03.000Z"),
                _asst_line("2026-10-01T10:00:05.000Z", "qwen3-coder", [
                    {"type": "text", "text": "two files"}]),
            ])
            a = AB.claude_sessions("/x/proj", 0, home=home)[0]
            ar = [r for r in a.rows if r.role == "assistant"]
            self.assertEqual(len(ar), 2)
            self.assertEqual(len(ar[0].calls), 1)
            c = ar[0].calls[0]
            self.assertEqual((c.call_id, c.name), ("toolu_1", "Bash"))
            # the tool_result line MERGES into the call it answers: the
            # assistant's tool part carries the output, and the result
            # line never becomes its own chat row
            self.assertEqual(c.output, "router.py\nseat.py")
            self.assertEqual(ar[1].text, "two files")
            ur = [r for r in a.rows if r.role == "user"]
            self.assertEqual(len(ur), 1)
            self.assertEqual(ur[0].text, "list the files")

    def test_noise_lines_meta_compact_sidechain_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_claude_project(home, [
                {"type": "mode", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:00.000Z"},
                {"type": "file-history-snapshot", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:00.100Z"},
                {"type": "attachment", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:00.200Z"},
                _claude_line("system", subtype="compact_boundary"),
                {"type": "user", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:01.000Z", "isCompactSummary":
                 True, "message": {"role": "user",
                                   "content": "compacted digest"},
                 "isMeta": False},
                {"type": "user", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:02.000Z", "isMeta": True,
                 "message": {"role": "user", "content": "meta line"}},
                _claude_line("assistant", ts="2026-10-01T10:00:03.000Z",
                             isSidechain=True,
                             message={"role": "assistant", "id": "m2",
                                      "model": "claude-sonnet-4-5",
                                      "content": [
                                          {"type": "text",
                                           "text": "subagent"}]}),
                _user_text_line("2026-10-01T10:00:04.000Z", "the real ask"),
                _asst_line("2026-10-01T10:00:05.000Z", "qwen3-coder", [
                    {"type": "text", "text": "the real answer"}]),
                {"type": "assistant", "sessionId": CLAUDE_UUID,
                 "timestamp": "2026-10-01T10:00:06.000Z", "isApiErrorMessage":
                 True, "message": {"role": "assistant", "id": "m3",
                                   "content": []}},
            ])
            a = AB.claude_sessions("/x/proj", 0, home=home)[0]
            texts = [r.text for r in a.rows]
            self.assertEqual(texts, ["the real ask", "the real answer"])
            self.assertEqual([r.role for r in a.rows],
                             ["user", "assistant"])

    def test_subagent_dir_and_sidechain_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            _write_claude_project(home, [
                _user_text_line("2026-10-01T10:00:00.000Z", "hi"),
                _asst_line("2026-10-01T10:00:01.000Z", "qwen3-coder", [
                    {"type": "text", "text": "ok"}])])
            side = home / "-x-proj" / CLAUDE_UUID / "subagents"
            side.mkdir(parents=True)
            with open(side / "agent-x.jsonl", "w") as fh:
                for ln in [_user_text_line("2026-10-01T10:01:00.000Z",
                                           "sub question"),
                           _asst_line("2026-10-01T10:01:01.000Z",
                                      "qwen3-coder",
                                      [{"type": "text", "text": "sub"}])]:
                    fh.write(json.dumps(ln) + "\n")
            a = AB.claude_sessions("/x/proj", 0, home=home)[0]
            self.assertEqual(len(a.rows), 2)

    def test_since_cuts_old_sessions(self):
        """A session whose last activity predates `since_s` is never
        re-collected (timestamps are UTC ISO strings -- no local-time
        drift in the comparison)."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            old = (now - timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z")
            _write_claude_project(home, [
                _user_text_line(old, "hi"),
                _asst_line("2026-10-01T10:00:01.000Z", "qwen3-coder", [
                    {"type": "text", "text": "ok"}])])
            self.assertEqual(AB.claude_sessions(
                "/x/proj", now.timestamp() - 60, home=home), [])
            self.assertEqual(len(AB.claude_sessions(
                "/x/proj", 0, home=home)), 1)


class TestOpencodeCollector(unittest.TestCase):
    def _sess(self, provider="ollama", aid=None, msgs=None,
              directory="/x/proj", sid=OPENCODE_SID):
        aid = aid or "msg_a1"
        return [{"sid": sid, "directory": directory,
                 "title": "witty lagoon", "model":
                 {"providerID": provider, "modelID": "Qwen3-Coder:latest"},
                 "updated_ms": 1759300000000,
                 "msgs": msgs or [
                     {"id": "msg_u1", "role": "user", "ts_ms": 1759300000000,
                      "parts": [{"type": "text", "text": "fix the clock"}]},
                     {"id": aid, "role": "assistant",
                      "ts_ms": 1759300003000, "providerID": provider,
                      "modelID": "Qwen3-Coder:latest",
                      "parts": [
                          {"type": "step-start"},
                          {"type": "tool", "tool": "edit",
                           "callID": "call_1",
                           "state": {"status": "completed",
                                     "input": {"path": "c.py"},
                                     "output": "wrote c.py"}},
                          {"type": "reasoning", "text": "the clock needs it"},
                          {"type": "text", "text": "done"}]},
                 ]}]

    def test_an_ollama_session_is_collected(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "opencode.db"
            _mk_opencode_db(db, self._sess())
            got = AB.opencode_sessions("/x/proj", 0, db=db)
            self.assertEqual(len(got), 1)
            a = got[0]
            self.assertEqual(a.source, "opencode")
            self.assertEqual(a.provider_id, "ollama")
            self.assertEqual(a.model_id, "Qwen3-Coder:latest")
            self.assertEqual([r.msg_id for r in a.rows],
                             ["msg_u1", "msg_a1"])
            ar = a.rows[1]
            self.assertEqual(ar.text, "done")
            self.assertEqual(ar.thought, "the clock needs it")
            self.assertEqual(len(ar.calls), 1)
            c = ar.calls[0]
            self.assertEqual((c.call_id, c.name), ("call_1", "edit"))
            self.assertEqual(c.output, "wrote c.py")

    def test_non_ollama_and_unknown_provider_drop_the_session(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "opencode.db"
            rows = self._sess(provider="anthropic", sid="ses_anth",
                              aid="msg_a_anth")
            rows += self._sess(provider="openai", sid="ses_open",
                               aid="msg_a_open",
                               msgs=[
                                   {"id": "msg_u_open", "role": "user",
                                    "ts_ms": 1759300000000,
                                    "parts": [{"type": "text",
                                               "text": "q2"}]},
                                   {"id": "msg_a_open", "role": "assistant",
                                    "ts_ms": 1759300003000,
                                    "providerID": "openai",
                                    "modelID": "gpt-x",
                                    "parts": [{"type": "text",
                                               "text": "a2"}]}])
            rows += [{"sid": "ses_x", "directory": "/x/proj",
                      "title": "t", "updated_ms": 1759300000000,
                      "model": {"providerID": "ollama", "modelID": "m"},
                      "msgs": [
                          {"id": "msg_u", "role": "user",
                           "ts_ms": 1759300000000,
                           "parts": [{"type": "text", "text": "q"}]},
                          {"id": "msg_a", "role": "assistant",
                           "ts_ms": 1759300001000,
                           # ollama session but a message with NO
                           # provider -- missing is unknown, unknown
                           # is anthropic's chance, so fail-closed
                           "parts": [{"type": "text", "text": "a"}]}]}]
            rows += [{"sid": "ses_ok", "directory": "/x/proj",
                      "title": "t", "updated_ms": 1759300000000,
                      "model": {"providerID": "ollama", "modelID": "m"},
                      "msgs": [
                          {"id": "msg_u_ok", "role": "user",
                           "ts_ms": 1759300000000,
                           "parts": [{"type": "text", "text": "q"}]},
                          {"id": "msg_a_ok", "role": "assistant",
                           "ts_ms": 1759300001000,
                           "providerID": "ollama", "modelID": "m2",
                           "parts": [{"type": "text", "text": "a"}]}]}]
            _mk_opencode_db(db, rows)
            got = AB.opencode_sessions("/x/proj", 0, db=db)
            sids = [a.sid for a in got]
            self.assertEqual(sids, ["ses_ok"])

    def test_directory_mismatch_is_not_collected(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "opencode.db"
            _mk_opencode_db(db, self._sess(directory="/y/other"))
            self.assertEqual(AB.opencode_sessions("/x/proj", 0, db=db), [])

    def test_a_missing_db_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(AB.opencode_sessions(
                "/x/proj", 0, db=Path(d) / "nope.db"), [])


def _claude_session(uid=CLAUDE_UUID):
    """A hand-built AbsorbSession for the importer tests."""
    ts_ms = int(time.mktime((2026, 10, 1, 10, 0, 0, 0, 0, -1)) * 1000)
    return AB.AbsorbSession("claude", uid, "the clock work", ts_ms,
                            ts_ms + 3000, "qwen3-coder:latest", "ollama",
                            [
        AB.TurnRow("xcl_u1", "user", ts_ms, "who made the clock?"),
        AB.TurnRow("xcl_a1", "assistant", ts_ms + 3000,
                   "the operator planted it", thought="it was them",
                   calls=[AB.ToolCall("call_1", "edit",
                                      '{"path": "c.py"}', "wrote")])])


# ---- the importer ----------------------------------------------------------

class TestImporter(unittest.TestCase):
    def _claude_session(self, uid=CLAUDE_UUID):
        return _claude_session(uid)

    def test_rows_land_with_every_load_bearing_field(self):
        """modelID non-empty (an empty one crashes the attached TUI),
        parentID chains to the user row, every part carries time.end --
        an unclosed part pins the TUI's spinner."""
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            sid = AB.import_session(ss, ms, self._claude_session(),
                                    "glm-5.3-flash:cloud", cwd="/x/proj")["sid"]
            self.assertEqual(sid, f"xcl_{CLAUDE_UUID}")
            doc = ss.get(sid)
            self.assertEqual(doc["absorbed"], {"source": "claude",
                                               "sid": CLAUDE_UUID})
            self.assertEqual(doc["model"]["providerID"], "ollama")
            self.assertEqual(doc["model"]["id"], "qwen3-coder:latest")
            self.assertEqual(doc["title"], "the clock work")
            rows = ms.list(sid)
            self.assertEqual([r["info"]["role"] for r in rows],
                             ["user", "assistant"])
            self.assertEqual(rows[0]["info"]["model"], {
                "providerID": "membrane", "modelID":
                "glm-5.3-flash:cloud"})
            ar = rows[1]
            self.assertEqual(ar["info"]["parentID"], "xcl_u1")
            self.assertEqual(ar["info"]["providerID"], "ollama")
            self.assertEqual(ar["info"]["modelID"], "qwen3-coder:latest")
            self.assertEqual(ar["info"]["time"]["completed"],
                             ar["info"]["time"]["created"])
            types = [p["type"] for p in ar["parts"]]
            self.assertEqual(types, ["step-start", "tool", "reasoning",
                                     "text", "step-finish"])
            tool_part = ar["parts"][1]
            self.assertEqual(tool_part["callID"], "call_1")
            self.assertEqual(tool_part["state"]["status"], "completed")
            self.assertEqual(tool_part["state"]["output"], "wrote")
            for p in ar["parts"]:
                if "time" in p and p["type"] != "step-start":
                    self.assertIn("end", p["time"], f"part {p['type']}")

    def test_opencode_sid_is_kept_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            a = self._claude_session()  # shape only
            a = AB.AbsorbSession("opencode", OPENCODE_SID, "t", 0, 0,
                                 "m", "ollama", a.rows)
            sid = AB.import_session(ss, ms, a, "glm")["sid"]
            self.assertEqual(sid, OPENCODE_SID)
            self.assertEqual(ss.get(sid)["absorbed"]["source"], "opencode")

    def test_id_collision_with_a_native_session_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            native = ss.create()
            self.assertTrue(native["id"])
            a = AB.AbsorbSession("opencode", native["id"], "t", 0, 0,
                                 "m", "ollama", [AB.TurnRow("u", "user", 0,
                                                            "hi")])
            with self.assertRaises(ValueError):
                AB.import_session(ss, ms, a, "glm")

    def test_top_up_never_dups_and_never_lowers_recency(self):
        """Re-import the same session: zero new rows; a LATER foreign
        message appends only the tail; an older foreign updated never
        pulls time.updated back (the TUI's -c lane sorts by it)."""
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            a = self._claude_session(uid=OPENCODE_SID)
            a.source = "opencode"
            sid = AB.import_session(ss, ms, a, "glm")["sid"]
            ms.append(sid, {"id": "native_row", "role": "user",
                            "time": {}}, [])
            ss.touch(sid)                    # native recency is now "now"
            r1 = AB.import_session(ss, ms, a, "glm")
            self.assertEqual(r1["n_msgs"], 0)
            self.assertEqual(r1["topup"], True)
            self.assertEqual(len(ms.list(sid)), 3)   # 2 foreign + 1 native
            new_ms = 1759400000000
            a.rows.append(AB.TurnRow("msg_u2", "user", new_ms, "round 2"))
            a.updated_ms = new_ms
            r2 = AB.import_session(ss, ms, a, "glm")
            self.assertEqual(r2["n_msgs"], 1)
            self.assertEqual(len(ms.list(sid)), 4)
            self.assertEqual(ss.get(sid)["time"]["updated"],
                             max(ss.get(sid)["time"]["updated"], new_ms))


# ---- continuation ----------------------------------------------------------

class TestContinuation(unittest.TestCase):
    def test_prior_history_replays_the_absorbed_texts(self):
        """The continuation covenant: the seat's next turn sees the
        absorbed conversation as real history (the narrative)."""
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            a = AB.AbsorbSession("claude", CLAUDE_UUID, "the clock work", 0,
                                 0, "qwen3-coder:latest", "ollama", [
                AB.TurnRow("xcl_u1", "user", 1000, "who made the clock?"),
                AB.TurnRow("xcl_a1", "assistant", 4000,
                           "the operator planted it")])
            sid = AB.import_session(ss, ms, a, "glm")["sid"]
            runner = TurnRunner(SSEHub(), ss, ms, None, None, system="",
                                model="glm")
            hist = runner._prior_history(sid, "")
            contents = " ".join(h["content"] for h in hist)
            self.assertIn("who made the clock?", contents)
            self.assertIn("the operator planted it", contents)

    def test_the_newest_absorbed_session_wins_the_continue_lane(self):
        """Two imports, older first: the newer one carries the freshest
        time.updated -- the TUI -c continues FROM IT."""
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            old = AB.AbsorbSession("opencode", "ses_old", "old", 0,
                                   1759290000000, "m", "ollama", [
                AB.TurnRow("u1", "user", 1759289999000, "hi")])
            new = AB.AbsorbSession("opencode", "ses_new", "new", 0,
                                   1759400000000, "m", "ollama", [
                AB.TurnRow("u2", "user", 1759399999000, "latest")])
            AB.import_session(ss, ms, old, "glm")
            AB.import_session(ss, ms, new, "glm")
            freshest = max(ss.sessions.values(),
                           key=lambda s: s["time"]["updated"])
            self.assertEqual(freshest["id"], "ses_new")


# ---- the field half --------------------------------------------------------

class _CapField:
    def __init__(self):
        self.calls = []

    def write_events(self, evs, session="", seqs=None):
        self.calls.append({"evs": list(evs), "session": session})


class TestFieldHalf(unittest.TestCase):
    def test_import_writes_field_events_with_src_and_session(self):
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            fld = _CapField()
            a = _claude_session()
            a.rows = [
                AB.TurnRow("xcl_u1", "user", 1000, "who made the clock?"),
                AB.TurnRow("xcl_a1", "assistant", 4000, "the operator it",
                           thought="thinking out",
                           calls=[AB.ToolCall("call_1", "edit", '{"p": 1}',
                                              "applied successfully")])]
            rep = AB.import_session(ss, ms, a, "glm", efield=fld)
            self.assertEqual(len(fld.calls), 1)
            self.assertEqual(fld.calls[0]["session"],
                             f"x:claude:{CLAUDE_UUID}")
            evs = fld.calls[0]["evs"]
            roles = [(e.stream, e.role) for e in evs]
            self.assertIn(("discourse", "user"), roles)
            self.assertIn(("discourse", "reasoning"), roles)
            obs = [e for e in evs if e.role == "tool"]
            self.assertEqual(len(obs), 1)
            self.assertEqual(obs[0].call_id, "call_1")
            self.assertEqual(obs[0].outcome, "applied")
            self.assertTrue(all(e.src == "absorb:claude" for e in evs))
            self.assertEqual(rep["n_events"], len(evs))

    def test_import_without_efield_skips_the_field_half(self):
        with tempfile.TemporaryDirectory() as d:
            ss = SessionStore(str(Path(d) / "s.json"))
            ms = MessageStore(str(Path(d) / "m.json"))
            a = _claude_session()
            rep = AB.import_session(ss, ms, a, "glm", efield=None)
            self.assertEqual(rep["n_events"], 0)


# ---- marker + absorb() -----------------------------------------------------

class TestMarkerAndAbsorb(unittest.TestCase):
    def _mk_session_dir(self, src, sid, ts_s, provider="ollama"):
        if src == "claude":
            home = Path(self.d) / "clhome"
            self._written = _write_claude_project(home, [
                _user_text_line("2026-10-01T10:00:00.000Z", "q"),
                _asst_line("2026-10-01T10:00:01.000Z", "qwen3-coder", [
                    {"type": "text", "text": "a"}])])
            got = AB.claude_sessions("/x/proj", 0, home=home)
            return got[0]
        db = Path(self.d) / "oe.db"
        _mk_opencode_db(db, [{"sid": sid, "directory": "/x/proj",
                              "title": "t",
                              "model": {"providerID": provider,
                                        "modelID": "m"},
                              "updated_ms": int(ts_s * 1000),
                              "msgs": [
                                  {"id": "u1", "role": "user",
                                   "ts_ms": int(ts_s * 1000),
                                   "parts": [{"type": "text",
                                              "text": "hello seat"}]},
                                  {"id": "a1", "role": "assistant",
                                   "ts_ms": int(ts_s * 1000) + 1000,
                                   "providerID": "ollama",
                                   "modelID": "m",
                                   "parts": [{"type": "text",
                                              "text": "hi operator"}]}]}])
        return AB.opencode_sessions("/x/proj", 0, db=db)[0]

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_absorb_imports_new_sessions_and_stamps_the_marker(self):
        a = self._mk_session_dir("claude", CLAUDE_UUID, 1759300000)
        mp = str(Path(self.d) / "marker.json")
        ss = SessionStore()
        ms = MessageStore()
        rep = AB.absorb(ss, ms, None, cwd="/x/proj", marker_path=mp,
                        model_id="glm", home=Path(self.d) / "clhome",
                        db=Path(self.d) / "noexist.db")
        self.assertEqual(len(rep["imported"]), 1)
        self.assertEqual(rep["imported"][0]["source"], "claude")
        mk = AB.load_marker(mp)
        self.assertGreater(mk["last_run_ts"], 0)
        self.assertIn(f"claude:{CLAUDE_UUID}", mk["sessions"])
        self.assertTrue(os.path.exists(mp))

    def test_second_boot_tops_up_and_skips_the_rest(self):
        a = self._mk_session_dir("opencode", OPENCODE_SID, 1759300000)
        mp = str(Path(self.d) / "marker.json")
        ss = SessionStore()
        ms = MessageStore()
        AB.absorb(ss, ms, None, cwd="/x/proj", marker_path=mp,
                  model_id="glm", home=Path(self.d) / "nope",
                  db=Path(self.d) / "oe.db")
        # the seat runs; the session GREW while the seat was up
        db = Path(self.d) / "oe.db"
        db.unlink()                            # rebuild the fixture fresh
        _mk_opencode_db(db, [{"sid": OPENCODE_SID, "directory": "/x/proj",
                              "title": "t",
                              "model": {"providerID": "ollama",
                                        "modelID": "m"},
                              "updated_ms": 1759301000000,
                              "msgs": [
                                  {"id": "u1", "role": "user",
                                   "ts_ms": 1759300000000,
                                   "parts": [{"type": "text",
                                              "text": "hello seat"}]},
                                  {"id": "a1", "role": "assistant",
                                   "ts_ms": 1759300001000,
                                   "providerID": "ollama", "modelID": "m",
                                   "parts": [{"type": "text",
                                              "text": "hi operator"}]},
                                  {"id": "u2", "role": "user",
                                   "ts_ms": 1759300900000,
                                   "parts": [{"type": "text",
                                              "text": "round 2"}]},
                                  {"id": "a2", "role": "assistant",
                                   "ts_ms": 1759300910000,
                                   "providerID": "ollama", "modelID": "m",
                                   "parts": [{"type": "text",
                                              "text": "round 2 done"}]}]}])
        rep = AB.absorb(ss, ms, None, cwd="/x/proj", marker_path=mp,
                        model_id="glm", home=Path(self.d) / "nope",
                        db=db)
        self.assertEqual(len(rep["imported"]), 1)
        self.assertTrue(rep["imported"][0]["topup"])
        self.assertEqual(rep["imported"][0]["n_msgs"], 2)

    def test_nothing_new_since_last_run(self):
        mp = str(Path(self.d) / "marker.json")
        ss = SessionStore()
        ms = MessageStore()
        rep1 = AB.absorb(ss, ms, None, cwd="/x/proj", marker_path=mp,
                         model_id="glm", home=Path(self.d) / "nope",
                         db=Path(self.d) / "nope.db")
        self.assertEqual(rep1["imported"], [])
        rep2 = AB.absorb(ss, ms, None, cwd="/x/proj", marker_path=mp,
                         model_id="glm", home=Path(self.d) / "nope",
                         db=Path(self.d) / "nope.db")
        self.assertEqual(rep2["imported"], [])
        self.assertIn("nothing new", AB.banner(rep2))

    def test_max_sessions_caps_the_flood(self):
        """First boot after a long gap: only the NEWEST sessions import
        (cap 5) -- no history dump."""
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "oe.db"
            rows = []
            for i in range(7):
                rows += [{"sid": f"ses_f{i}", "directory": "/x/proj",
                          "title": f"t{i}",
                          "model": {"providerID": "ollama", "modelID": "m"},
                          "updated_ms": 1759000000000 + i * 1000,
                          "msgs": [{"id": f"u{i}", "role": "user",
                                    "ts_ms": 1759000000000 + i * 1000,
                                    "parts": [{"type": "text",
                                               "text": f"q{i}"}]},
                                   {"id": f"a{i}", "role": "assistant",
                                    "ts_ms": 1759000001000 + i * 1000,
                                    "providerID": "ollama", "modelID": "m",
                                    "parts": [{"type": "text",
                                               "text": f"a{i}"}]}]}]
            _mk_opencode_db(db, rows)
            ss, ms = SessionStore(), MessageStore()
            rep = AB.absorb(ss, ms, None, cwd="/x/proj",
                            marker_path=str(Path(d) / "mp"),
                            model_id="glm", home=Path(d) / "nope", db=db)
            self.assertEqual(len(rep["imported"]), AB.ABSORB_MAX_SESSIONS)
            self.assertTrue(all(r["topup"] is False for r in
                                rep["imported"]))
            newest = max(rep["imported"], key=lambda r: r["sid"])
            self.assertIn("ses_f6", [r["sid"] for r in rep["imported"]])

    def test_lab_marker_parent_refuses(self):
        ss, ms = SessionStore(), MessageStore()
        rep = AB.absorb(ss, ms, None, cwd="/x/proj",
                        marker_path=os.path.join(tempfile.gettempdir(),
                                                 "absorb-x.json"),
                        model_id="glm", home=Path(tempfile.mkdtemp()), )
        self.assertEqual(rep["imported"], [])


# ---- gate ------------------------------------------------------------------

class TestGate(unittest.TestCase):
    def test_gate(self):
        real = os.path.dirname(os.path.abspath("membrane"))
        self.assertTrue(AB.absorb_gate(real, False, False, True))
        self.assertFalse(AB.absorb_gate(real, True, False, True))
        self.assertFalse(AB.absorb_gate(real, False, True, True))
        self.assertFalse(AB.absorb_gate(real, False, False, False))
        self.assertFalse(AB.absorb_gate(tempfile.gettempdir(),
                                        False, False, True))


if __name__ == "__main__":
    unittest.main()