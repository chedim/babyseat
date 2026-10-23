"""#470 round 3: GET /session/<id>/message on the serve port reads a PAGE when
asked -- ?limit=N (the newest N) and &before=<message id> (the N before it),
chronological as ever; without the params it returns every message, as the
TUI and the other clients expect."""
import json
import os

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import server as S  # noqa: E402
from tests.test_nerve_pause_348 import _Env, _seat, _serve  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402


def test_the_page_helper():
    rows = [{"info": {"id": "m%d" % i}} for i in range(10)]
    ids = lambda r: [x["info"]["id"] for x in r]
    assert ids(S.message_page(rows, 3, "")) == ["m7", "m8", "m9"]
    assert ids(S.message_page(rows, 3, "m5")) == ["m2", "m3", "m4"]
    assert ids(S.message_page(rows, 3, "m1")) == ["m0"]
    assert ids(S.message_page(rows, 3, "m0")) == []
    assert S.message_page(rows, 3, "nope") == []
    assert ids(S.message_page(rows, None, "")) == ids(rows)        # no limit: everything


class TestThePagedRead(_Env):
    def setUp(self):
        super().setUp()
        self.srv, self.r = _seat(self.tmp.name)
        self.sid = self.r.sessions.create(title="long")["id"]
        for i in range(12):
            self.r.messages.append(self.sid, {"id": "msg_%02d" % i, "role": "user" if i % 2 == 0 else "assistant",
                                              "sessionID": self.sid, "time": {"created": 1000 + i}},
                                   [{"type": "text", "text": "line %d" % i}])
        self.httpd = _serve(self.srv, self.r, False)

    def tearDown(self):
        self.httpd.shutdown()
        super().tearDown()

    def _get(self, path):
        code, b, _ = _req(self.httpd.server_address[1], path, "GET", None, None)
        return code, json.loads(b or b"[]")

    def test_without_params_every_message(self):
        code, rows = self._get("/session/%s/message" % self.sid)
        self.assertEqual((code, len(rows)), (200, 12))

    def test_the_newest_page_then_the_one_before(self):
        code, rows = self._get("/session/%s/message?limit=5" % self.sid)
        self.assertEqual([r["info"]["id"] for r in rows], ["msg_07", "msg_08", "msg_09", "msg_10", "msg_11"])
        code, rows = self._get("/session/%s/message?limit=5&before=msg_07" % self.sid)
        self.assertEqual([r["info"]["id"] for r in rows], ["msg_02", "msg_03", "msg_04", "msg_05", "msg_06"])
        code, rows = self._get("/session/%s/message?limit=5&before=msg_02" % self.sid)
        self.assertEqual([r["info"]["id"] for r in rows], ["msg_00", "msg_01"])
