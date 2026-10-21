"""Absorb learns sessions answered by Anthropic models (issue #396).

The operator reversed the "don't remix Claude's answers" stance on
2026-10-21: a Claude Code or OpenCode session answered by an Anthropic
model is imported and learned like any other. Provenance stays (model id,
provider, the claude-agent mark). Sessions the old rule skipped come back
once, even when they are older than the seat's last absorb run, and
sessions already absorbed are never imported twice.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import membrane.absorb as AB  # noqa: E402
from membrane import claude_agent as CA  # noqa: E402
from membrane.server import MessageStore, SessionStore  # noqa: E402
from test_absorb import (_asst_line, _mk_opencode_db,  # noqa: E402
                         _user_text_line, _write_claude_project)

UUID_A = "1c2f9a4e-0000-4000-8000-00000000a396"
UUID_B = "1c2f9a4e-0000-4000-8000-00000000b396"
UUID_C = "1c2f9a4e-0000-4000-8000-00000000c396"


def _anthropic_lines(day="01"):
    return [
        _user_text_line(f"2026-10-{day}T10:00:00.000Z", "where is the clock?"),
        _asst_line(f"2026-10-{day}T10:00:05.000Z", "claude-opus-4-6",
                   [{"type": "text", "text": "in clock.py"}]),
    ]


def _ollama_lines(day="01"):
    return [
        _user_text_line(f"2026-10-{day}T09:00:00.000Z", "q"),
        _asst_line(f"2026-10-{day}T09:00:05.000Z", "qwen3-coder",
                   [{"type": "text", "text": "a"}]),
    ]


class TestCollectors(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_a_claude_code_session_on_an_anthropic_model_is_collected(self):
        home = Path(self.d)
        _write_claude_project(home, _anthropic_lines(), uuid=UUID_A)
        got = AB.claude_sessions("/x/proj", 0, home=home)
        self.assertEqual([a.sid for a in got], [UUID_A])
        a = got[0]
        self.assertEqual(a.model_id, "claude-opus-4-6")
        self.assertEqual(a.provider_id, "anthropic")      # provenance
        self.assertEqual(a.rows[1].text, "in clock.py")
        self.assertEqual(a.rows[1].provider_id, "anthropic")

    def test_a_mixed_session_keeps_each_rows_provider(self):
        home = Path(self.d)
        _write_claude_project(home, _anthropic_lines() + [
            _user_text_line("2026-10-01T10:01:00.000Z", "more"),
            _asst_line("2026-10-01T10:01:05.000Z", "qwen3-coder",
                       [{"type": "text", "text": "fine"}])], uuid=UUID_A)
        a = AB.claude_sessions("/x/proj", 0, home=home)[0]
        provs = [r.provider_id for r in a.rows if r.role == "assistant"]
        self.assertEqual(provs, ["anthropic", "ollama"])

    def test_an_opencode_session_on_anthropic_is_collected(self):
        db = Path(self.d) / "oc.db"
        _mk_opencode_db(db, [{
            "sid": "ses_anth", "directory": "/x/proj", "title": "t",
            "model": {"providerID": "anthropic",
                      "modelID": "claude-sonnet-4-5"},
            "updated_ms": 1759300000000,
            "msgs": [
                {"id": "u1", "role": "user", "ts_ms": 1759300000000,
                 "parts": [{"type": "text", "text": "q"}]},
                {"id": "a1", "role": "assistant", "ts_ms": 1759300001000,
                 "providerID": "anthropic", "modelID": "claude-sonnet-4-5",
                 "parts": [{"type": "text", "text": "a"}]}]}])
        got = AB.opencode_sessions("/x/proj", 0, db=db)
        self.assertEqual([a.sid for a in got], ["ses_anth"])
        self.assertEqual(got[0].provider_id, "anthropic")
        self.assertEqual(got[0].rows[1].model_id, "claude-sonnet-4-5")

    def test_a_claude_agent_row_is_absorbed_with_its_mark(self):
        db = Path(self.d) / "oc.db"
        _mk_opencode_db(db, [{
            "sid": "ses_rung", "directory": "/x/proj", "title": "t",
            "model": {"providerID": "ollama", "modelID": "qwen3:14b"},
            "updated_ms": 1759300000000,
            "msgs": [
                {"id": "u1", "role": "user", "ts_ms": 1759300000000,
                 "parts": [{"type": "text", "text": "q"}]},
                {"id": "a1", "role": "assistant", "ts_ms": 1759300001000,
                 "providerID": "ollama", "modelID": "claude-agent:sonnet",
                 "parts": [{"type": "text", "text": "Claude's words"}]}]}])
        a = AB.opencode_sessions("/x/proj", 0, db=db)[0]
        ss, ms = SessionStore(), MessageStore()
        AB.import_session(ss, ms, a, "glm", cwd="/x/proj")
        info = [r["info"] for r in ms.list("ses_rung")
                if r["info"]["role"] == "assistant"][0]
        self.assertEqual(info["modelID"], "claude-agent:sonnet")
        self.assertEqual(info.get(CA.MARK_KEY), CA.PROVIDER)


class TestAbsorbEntry(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = Path(self.d) / "clhome"
        self.mp = str(Path(self.d) / "serve-absorb.json")
        self.nodb = Path(self.d) / "nope.db"

    def _absorb(self, ss, ms):
        return AB.absorb(ss, ms, None, cwd="/x/proj", marker_path=self.mp,
                         model_id="glm", home=self.home, db=self.nodb)

    def test_an_anthropic_answered_session_is_absorbed_and_learned(self):
        _write_claude_project(self.home, _anthropic_lines(), uuid=UUID_A)

        class _Field:
            def __init__(self):
                self.calls = []

            def write_events(self, evs, session=None):
                self.calls.append((list(evs), session))
        ef = _Field()
        ss, ms = SessionStore(), MessageStore()
        rep = AB.absorb(ss, ms, ef, cwd="/x/proj", marker_path=self.mp,
                        model_id="glm", home=self.home, db=self.nodb)
        self.assertEqual([r["sid"] for r in rep["imported"]], [UUID_A])
        doc = ss.get(AB.CLAUDE_NS + UUID_A)
        self.assertEqual(doc["model"]["providerID"], "anthropic")
        self.assertEqual(doc["model"]["id"], "claude-opus-4-6")
        self.assertTrue(ef.calls and ef.calls[0][0])        # learned

    def _old_marker(self, last_run_ts, sessions=None):
        """A serve-absorb.json written by the old code (version 1)."""
        Path(self.mp).write_text(json.dumps({
            "version": 1, "last_run_ts": last_run_ts,
            "sessions": sessions or {}}))

    def test_a_session_the_old_rule_skipped_becomes_eligible_again(self):
        """The old seat ran AFTER this session ended and skipped it; the
        watermark alone would hide it forever."""
        _write_claude_project(self.home, _anthropic_lines(), uuid=UUID_A)
        self._old_marker(2_000_000_000.0)          # long after 2026-10-01
        ss, ms = SessionStore(), MessageStore()
        rep = self._absorb(ss, ms)
        self.assertEqual([r["sid"] for r in rep["imported"]], [UUID_A])
        mk = AB.load_marker(self.mp)
        self.assertGreaterEqual(mk["version"], 2)
        self.assertIn(f"claude:{UUID_A}", mk["sessions"])
        # the rescan is once: the next boot imports nothing new
        rep2 = self._absorb(ss, ms)
        self.assertEqual(rep2["imported"], [])

    def test_an_old_ollama_session_is_not_rescanned(self):
        """Only sessions the Anthropic rule skipped come back; an old
        eligible session past the watermark stays where the old code
        left it."""
        _write_claude_project(self.home, _ollama_lines(), uuid=UUID_B)
        self._old_marker(2_000_000_000.0)
        ss, ms = SessionStore(), MessageStore()
        self.assertEqual(self._absorb(ss, ms)["imported"], [])

    def test_an_already_absorbed_session_is_not_duplicated(self):
        _write_claude_project(self.home, _ollama_lines(), uuid=UUID_B)
        ss, ms = SessionStore(), MessageStore()
        rep1 = self._absorb(ss, ms)               # absorbed by the new code
        self.assertEqual([r["sid"] for r in rep1["imported"]], [UUID_B])
        n = len(ms.list(AB.CLAUDE_NS + UUID_B))
        # the operator downgrades/upgrades: an old-format marker that
        # already knows the session
        mk = AB.load_marker(self.mp)
        self._old_marker(mk["last_run_ts"], mk["sessions"])
        _write_claude_project(self.home, _anthropic_lines(), uuid=UUID_A)
        rep2 = self._absorb(ss, ms)
        self.assertEqual([r["sid"] for r in rep2["imported"]], [UUID_A])
        self.assertEqual(len(ms.list(AB.CLAUDE_NS + UUID_B)), n)

    def test_a_rescan_past_the_cap_stays_pending_until_drained(self):
        uuids = [f"1c2f9a4e-0000-4000-8000-0000000{i:05d}" for i in range(7)]
        for i, u in enumerate(uuids):
            _write_claude_project(self.home, _anthropic_lines(f"{i + 1:02d}"),
                                  uuid=u)
        self._old_marker(2_000_000_000.0)
        ss, ms = SessionStore(), MessageStore()
        rep1 = self._absorb(ss, ms)
        self.assertEqual(len(rep1["imported"]), AB.ABSORB_MAX_SESSIONS)
        self.assertLess(AB.load_marker(self.mp).get("version", 1), 2)
        rep2 = self._absorb(ss, ms)
        got = {r["sid"] for r in rep1["imported"]} | \
            {r["sid"] for r in rep2["imported"]}
        self.assertEqual(got, set(uuids))
        self.assertGreaterEqual(AB.load_marker(self.mp)["version"], 2)
        self.assertEqual(self._absorb(ss, ms)["imported"], [])


if __name__ == "__main__":
    unittest.main()
