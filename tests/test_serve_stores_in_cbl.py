"""#799: the chat stores live in the seat's Couchbase Lite store (operator, 2026-11-01:
data in the database, secrets / hand-edited config / the audit log in files). One
document per session and per message; a save writes only what changed; the JSON files
migrate once and stay as backups."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.server import MessageStore, SessionStore  # noqa: E402
from membrane import stats as ST  # noqa: E402

try:
    from membrane.cblstore import CBLiteBackend
    CBLiteBackend(Path(tempfile.mkdtemp()) / "probe.cblite").close()
    HAVE = True
except Exception:
    HAVE = False


def _user(sid, mid, text, t=1000):
    return ({"id": mid, "sessionID": sid, "role": "user", "time": {"created": t}},
            [{"id": "p" + mid, "type": "text", "text": text}])


class _Spy:
    """The database's writes, counted (a save must write only what changed)."""
    def __init__(self, db):
        self.db, self.puts, self.purges = db, [], []

    def __getattr__(self, k):
        return getattr(self.db, k)

    def put_json(self, coll, key, js):
        self.puts.append((coll, key))
        return self.db.put_json(coll, key, js)

    def purge(self, coll, key):
        self.purges.append((coll, key))
        return self.db.purge(coll, key)


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestSessionsInCBL(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.be = CBLiteBackend(self.d / "field.cblite")
        self.addCleanup(self.be.close)
        self.json = self.d / "serve-sessions.json"

    def _store(self, db=None):
        return SessionStore(str(self.json), cbl=db or self.be.db)

    def test_sessions_survive_a_restart_and_no_file_is_written(self):
        s = self._store()
        a = s.create(title="one")
        s.patch(a["id"], title="renamed")
        b = s.create(title="two")
        s.delete(b["id"])
        again = self._store()
        self.assertEqual([x["title"] for x in again.list()], ["renamed"])
        self.assertFalse(self.json.exists())

    def test_a_touch_writes_only_that_session(self):
        s = self._store()
        a, b = s.create(title="a"), s.create(title="b")
        spy = _Spy(self.be.db)
        s._cbl = spy
        s.touch(a["id"])
        self.assertEqual([k for _, k in spy.puts], [a["id"]])
        self.assertTrue(b["id"])

    def test_a_direct_edit_then_flush_is_saved(self):
        s = self._store()
        a = s.create(title="a")
        with s._lock:
            s.sessions[a["id"]]["agent"] = "watch: x"
            s._flush()
        self.assertEqual(self._store().get(a["id"])["agent"], "watch: x")

    def test_an_unchanged_flush_writes_nothing(self):
        s = self._store()
        s.create(title="a")
        spy = _Spy(self.be.db)
        s._cbl = spy
        s.flush()
        self.assertEqual(spy.puts, [])

    def test_the_json_file_migrates_once_and_stays_as_a_backup(self):
        self.json.write_text(json.dumps({"ses_1": {"id": "ses_1", "title": "old",
                                                   "time": {"created": 1, "updated": 1}}}))
        s = self._store()
        self.assertEqual(s.get("ses_1")["title"], "old")
        self.assertFalse(self.json.exists())
        self.assertTrue(list(self.d.glob("serve-sessions.json.migrated-*")))
        self.assertEqual(self._store().get("ses_1")["title"], "old")


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestMessagesInCBL(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.be = CBLiteBackend(self.d / "field.cblite")
        self.addCleanup(self.be.close)
        self.json = self.d / "serve-msgs.json"

    def _store(self):
        return MessageStore(str(self.json), cbl=self.be.db)

    def test_messages_survive_a_restart_in_order(self):
        m = self._store()
        for i in range(5):
            m.append("ses_a", *_user("ses_a", f"msg_{i}", f"hello {i}", t=1000 + i))
        m.append("ses_b", *_user("ses_b", "msg_b", "other"))
        m.flush()
        again = self._store()
        self.assertEqual([r["info"]["id"] for r in again.list("ses_a")], [f"msg_{i}" for i in range(5)])
        self.assertEqual(len(again.list("ses_b")), 1)
        self.assertFalse(self.json.exists())

    def test_a_snapshot_upserts_in_place(self):
        m = self._store()
        info, parts = _user("ses_a", "msg_1", "first")
        m.append("ses_a", info, parts)
        m.append("ses_a", info, [{"id": "p", "type": "text", "text": "second"}])
        m.flush()
        rows = self._store().list("ses_a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parts"][0]["text"], "second")

    def test_a_part_appended_persists(self):
        m = self._store()
        m.append("ses_a", *_user("ses_a", "msg_1", "hi"))
        m.append_part("ses_a", {"id": "x", "type": "text", "text": "a note"})
        m.flush()
        rows = self._store().list("ses_a")
        self.assertEqual(rows[-1]["parts"][-1]["text"], "a note")

    def test_a_new_message_writes_only_itself(self):
        m = self._store()
        for i in range(3):
            m.append("ses_a", *_user("ses_a", f"msg_{i}", "x"))
        m.flush()
        spy = _Spy(self.be.db)
        m._cbl = spy
        m.append("ses_a", *_user("ses_a", "msg_new", "y"))
        self.assertEqual(len(spy.puts), 1)

    def test_delete_forgets_the_sessions_messages(self):
        m = self._store()
        m.append("ses_a", *_user("ses_a", "msg_1", "x"))
        m.append("ses_b", *_user("ses_b", "msg_2", "y"))
        m.flush()
        self.assertTrue(m.delete("ses_a"))
        again = self._store()
        self.assertEqual(again.list("ses_a"), [])
        self.assertEqual(len(again.list("ses_b")), 1)

    def test_snapshot_and_journal_migrate_once(self):
        self.json.write_text(json.dumps({"ses_a": [{"info": {"id": "msg_1", "role": "user",
                                                              "time": {"created": 1}}, "parts": []}]}))
        Path(str(self.json) + ".jnl").write_text(json.dumps(
            {"sid": "ses_a", "row": {"info": {"id": "msg_2", "role": "user", "time": {"created": 2}},
                                     "parts": []}}) + "\n")
        m = self._store()
        self.assertEqual([r["info"]["id"] for r in m.list("ses_a")], ["msg_1", "msg_2"])
        self.assertFalse(self.json.exists())
        self.assertFalse(Path(str(self.json) + ".jnl").exists())
        self.assertTrue(list(self.d.glob("serve-msgs.json.migrated-*")))
        self.assertEqual(len(self._store().list("ses_a")), 2)

    def test_stats_reads_the_database_when_the_file_has_migrated(self):
        m = self._store()
        m.append("ses_a", *_user("ses_a", "msg_1", "x"))
        m.flush()
        raw = ST.read_raw(str(self.json))
        self.assertEqual([r["info"]["id"] for r in raw.get("ses_a", [])], ["msg_1"])


class TestWithoutTheDatabase(unittest.TestCase):
    def test_a_store_without_the_database_keeps_its_files(self):
        d = Path(tempfile.mkdtemp())
        s = SessionStore(str(d / "serve-sessions.json"))
        s.create(title="a")
        self.assertTrue((d / "serve-sessions.json").exists())
        m = MessageStore(str(d / "serve-msgs.json"))
        m.append("ses_a", *_user("ses_a", "msg_1", "x"))
        self.assertTrue((d / "serve-msgs.json.jnl").exists())


if __name__ == "__main__":
    unittest.main()
