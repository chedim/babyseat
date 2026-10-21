"""Absorb finds Claude Code's transcript folder by Claude Code's own naming
(issue #401) and learns OpenCode sessions from every provider (issue #404).

#401: Claude Code names a project's transcript folder by replacing every
character of the path that is not a letter or a digit with '-'. Absorb
kept '.', so a path with a '.' in it never matched. The old form is still
tried, so nothing that ever matched stops matching.

#404: the provider allowlist is gone. Every OpenCode provider is absorbed
with its provider kept on each assistant row; a message with no provider
id is recorded as 'unknown'. The one provider that stays out is
'membrane': the seat's own OpenCode config routes through it, so such a
session is the seat's own traffic and absorbing it would feed it back.

Sessions either old rule hid are older than the seat's watermark, so they
come back through the one-time rescan (marker version 3).
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
from membrane.server import MessageStore, SessionStore  # noqa: E402
from test_absorb import (_asst_line, _mk_opencode_db,  # noqa: E402
                         _user_text_line)

CWD = "/Users/name.surname/projects/my_proj"
NEW_SLUG = "-Users-name-surname-projects-my-proj"
OLD_SLUG = "-Users-name.surname-projects-my_proj"
UUID_N = "1c2f9a4e-0000-4000-8000-00000000a401"
UUID_O = "1c2f9a4e-0000-4000-8000-00000000b401"


def _lines(day="01", model="qwen3-coder"):
    return [
        _user_text_line(f"2026-10-{day}T09:00:00.000Z", "q"),
        _asst_line(f"2026-10-{day}T09:00:05.000Z", model,
                   [{"type": "text", "text": "a"}]),
    ]


def _write(home: Path, slug: str, uuid: str, lines) -> None:
    proj = home / slug
    proj.mkdir(parents=True, exist_ok=True)
    with open(proj / f"{uuid}.jsonl", "w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")


def _oc(sid, provider, msg_provider="same", directory=CWD,
        updated_ms=1759300000000):
    """One OpenCode session; msg_provider None = the assistant message
    names no provider."""
    mp = provider if msg_provider == "same" else msg_provider
    asst = {"id": f"msg_a_{sid}", "role": "assistant",
            "ts_ms": updated_ms - 1000, "modelID": "some-model",
            "parts": [{"type": "text", "text": "a"}]}
    if mp is not None:
        asst["providerID"] = mp
    return {"sid": sid, "directory": directory, "title": "t",
            "updated_ms": updated_ms,
            "model": {"providerID": provider, "modelID": "some-model"},
            "msgs": [{"id": f"msg_u_{sid}", "role": "user",
                      "ts_ms": updated_ms - 2000,
                      "parts": [{"type": "text", "text": "q"}]}, asst]}


class TestFolderName(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = Path(self.d)

    def test_every_non_alphanumeric_character_becomes_a_dash(self):
        self.assertEqual(AB._project_slug(CWD), NEW_SLUG)
        self.assertEqual(AB._project_slug("/a b/c-d/E9"), "-a-b-c-d-E9")

    def test_the_old_form_is_still_offered(self):
        self.assertEqual(AB._project_slugs(CWD), [NEW_SLUG, OLD_SLUG])
        # a path the two rules name alike yields one folder, not two
        self.assertEqual(AB._project_slugs("/x/proj"), ["-x-proj"])

    def test_sessions_are_found_in_claude_codes_folder(self):
        _write(self.home, NEW_SLUG, UUID_N, _lines())
        got = AB.claude_sessions(CWD, 0, home=self.home)
        self.assertEqual([a.sid for a in got], [UUID_N])
        # the old code never saw this folder: the rescan must reach it
        self.assertTrue(got[0].old_rule_skipped)

    def test_sessions_in_the_old_folder_still_match(self):
        _write(self.home, OLD_SLUG, UUID_O, _lines())
        got = AB.claude_sessions(CWD, 0, home=self.home)
        self.assertEqual([a.sid for a in got], [UUID_O])
        self.assertFalse(got[0].old_rule_skipped)

    def test_both_folders_are_read_and_a_session_is_listed_once(self):
        _write(self.home, NEW_SLUG, UUID_N, _lines("02"))
        _write(self.home, OLD_SLUG, UUID_O, _lines("01"))
        _write(self.home, OLD_SLUG, UUID_N, _lines("02"))   # same sid
        got = AB.claude_sessions(CWD, 0, home=self.home)
        self.assertEqual([a.sid for a in got], [UUID_N, UUID_O])


class TestEveryProvider(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.db = Path(self.d) / "opencode.db"

    def _got(self, rows):
        _mk_opencode_db(self.db, rows)
        return {a.sid: a for a in AB.opencode_sessions(CWD, 0, db=self.db)}

    def test_any_provider_is_absorbed_and_kept_on_the_row(self):
        got = self._got([_oc("ses_openai", "openai"),
                         _oc("ses_router", "openrouter"),
                         _oc("ses_ollama", "ollama")])
        self.assertEqual(set(got), {"ses_openai", "ses_router",
                                    "ses_ollama"})
        a = got["ses_openai"]
        self.assertEqual(a.provider_id, "openai")
        self.assertEqual([r.provider_id for r in a.rows
                          if r.role == "assistant"], ["openai"])
        self.assertEqual(got["ses_router"].rows[1].provider_id,
                         "openrouter")

    def test_a_message_with_no_provider_is_kept_as_unknown(self):
        got = self._got([_oc("ses_x", "ollama", msg_provider=None)])
        self.assertIn("ses_x", got)
        self.assertEqual(got["ses_x"].rows[1].provider_id, "unknown")

    def test_a_session_with_no_provider_is_kept_as_unknown(self):
        got = self._got([_oc("ses_y", "", msg_provider="openai")])
        self.assertEqual(got["ses_y"].provider_id, "unknown")
        self.assertEqual(got["ses_y"].rows[1].provider_id, "openai")

    def test_the_seats_own_provider_stays_out(self):
        got = self._got([
            _oc("ses_m", "membrane"),                         # session
            _oc("ses_m2", "ollama", msg_provider="membrane"),  # one msg
        ])
        self.assertEqual(got, {})

    def test_sessions_the_allowlist_dropped_are_marked_for_the_rescan(self):
        got = self._got([_oc("ses_openai", "openai"),
                         _oc("ses_x", "ollama", msg_provider=None),
                         _oc("ses_ok", "ollama")])
        self.assertTrue(got["ses_openai"].old_rule_skipped)
        self.assertTrue(got["ses_x"].old_rule_skipped)
        self.assertFalse(got["ses_ok"].old_rule_skipped)


class TestRescan(unittest.TestCase):
    """A seat that already ran #396 stamped marker version 2; sessions the
    folder name or the allowlist hid are older than its watermark."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.home = Path(self.d) / "clhome"
        self.db = Path(self.d) / "opencode.db"
        self.mp = str(Path(self.d) / "serve-absorb.json")

    def _absorb(self, ss, ms):
        return AB.absorb(ss, ms, None, cwd=CWD, marker_path=self.mp,
                         model_id="glm", home=self.home, db=self.db)

    def _v2_marker(self, sessions=None):
        Path(self.mp).write_text(json.dumps({
            "version": 2, "last_run_ts": 2_000_000_000.0,
            "sessions": sessions or {}}))

    def test_hidden_sessions_come_back_once(self):
        _write(self.home, NEW_SLUG, UUID_N, _lines())
        _write(self.home, OLD_SLUG, UUID_O, _lines())       # seen before
        _mk_opencode_db(self.db, [_oc("ses_openai", "openai"),
                                  _oc("ses_ok", "ollama")])
        self._v2_marker()
        ss, ms = SessionStore(), MessageStore()
        rep = self._absorb(ss, ms)
        self.assertEqual(sorted(r["sid"] for r in rep["imported"]),
                         sorted([UUID_N, "ses_openai"]))
        mk = AB.load_marker(self.mp)
        self.assertGreaterEqual(mk["version"], 3)
        self.assertEqual(self._absorb(ss, ms)["imported"], [])

    def test_imported_rows_keep_their_provider(self):
        _mk_opencode_db(self.db, [_oc("ses_openai", "openai")])
        ss, ms = SessionStore(), MessageStore()
        self._absorb(ss, ms)
        provs = [r["info"].get("providerID") for r in ms.list("ses_openai")
                 if r["info"]["role"] == "assistant"]
        self.assertEqual(provs, ["openai"])


if __name__ == "__main__":
    unittest.main()
