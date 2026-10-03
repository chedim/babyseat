"""THE CURSOR SURVIVES THE REGISTRY'S DEATH (the repeating-messages
root cause): the seat's channel cursor rode an EPHEMERAL registry doc
(TTL'd) — the GC'd cursor reset a seat to 0.0 and fetch_unread
re-fetched THE WHOLE CHANNEL from epoch; the banner quoted old lines
again; the models replied to repeats; the spiral. THE CONTRACT:

1. the cursor persists OUTSIDE the ephemeral store (a LOCAL file
   beside the field — the seat's own disk, mode 600), restored at
   boot; the ephemeral doc's copy stays (for a NEW seat's fast
   start) but is never the authority;
2. fetch_unread never refetches from 0.0 when a local cursor
   exists — even after the registry doc expired;
3. the read cursor updates BOTH (the local file on every read);
4. the echo/dedupe filter: a message seen TWICE (same from+text+
   msg-id) is dropped — belt to the cursor's braces;
5. a message-id rides every post (the idempotent key: retry-safe).
"""
import json
import time
from pathlib import Path

from membrane.peers import PeerSubstrate


def _peers(tmp_path, file_cursor=True):
    p = PeerSubstrate(field_path=str(tmp_path / "f.json"), port=11500,
                      model="m")
    if file_cursor:
        p._cursor_file = Path(tmp_path / "chan-cursor.json")
    return p


def test_cursor_persists_to_a_local_file(tmp_path):
    p = _peers(tmp_path)
    p._cursor_file = Path(tmp_path / "chan-cursor.json")
    p.note_cursor(1791061320.7)
    doc = json.loads(p._cursor_file.read_text())
    assert abs(doc["chan_cursor"] - 1791061320.7) < 1.0
    d2 = p._cursor_ts
    p2 = _peers(tmp_path)            # a boot: fresh object, same disk
    p2._cursor_file = p._cursor_file
    p2.restore_cursor()
    assert abs(p2._cursor_ts - d2) < 1.0


def test_fetch_never_refetches_from_epoch(tmp_path):
    p = _peers(tmp_path)
    p._cursor_file = Path(tmp_path / "chan-cursor.json")
    p.note_cursor(1000.0)
    # the ephemeral registry lost its doc (the GC): the cursor's
    # registry ride is GONE — the LOCAL cursor rules
    p._reg, p._reg_bucket = None, None
    p.restore_cursor()
    assert p._cursor_ts == 1000.0    # not 0.0, never from-epoch


def test_dedupe_same_from_text_window(tmp_path):
    p = _peers(tmp_path)
    now = time.time()
    msg = {"from": "proxy-1", "ts": now - 2.0, "text": "hi"}
    # FIRST sight: False (render it); the SECOND (a re-fetch's echo):
    # True (drop); a different message: False (pass); the same one
    # OUTSIDE the window: False again (it's fresh news by then)
    assert p._echo_seen(msg, now=now) is False
    assert p._echo_seen(msg, now=now + 1.0) is True
    assert p._echo_seen({"from": "proxy-1", "ts": now, "text": "hi2"},
                        now=now + 1.0) is False
    assert p._echo_seen(msg, now=now + 3700.0) is False


def test_post_carries_a_message_id(tmp_path):
    p = _peers(tmp_path)
    p._chan = type("C", (), {"upsert": lambda self, k, d: True})()
    calls = {}
    def up(key, doc):
        calls["key"] = key
        calls["doc"] = doc
        return True
    p._chan = type("C", (), {"upsert": staticmethod(up)})()
    p.post("the retry-safe post")
    assert "mid" in calls["doc"] or calls["doc"].get("mid"), calls
