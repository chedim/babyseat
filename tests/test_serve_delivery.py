"""THE LATE-REPLY POSITION PIN (red first): a reply whose completion
lands after newer user messages must play at the BOTTOM of the chat --
arrival order, never retroactively re-slotted where its turn started.
Root seen live: _chronological sorted by created-ts only, while
MessageStore writes the assistant row EMPTY ~20ms after the user turn
(created = turn start) and completes it minutes later -- so the late
answer replayed UNDER older messages (operator report: answers arrive
under my messages, not at the bottom). Contract: display key is
info.time.completed when set, else info.time.created."""
from membrane.server import MessageStore, _chronological

SID = "ses_test"


def _user(mid, created, text):
    return {"info": {"id": mid, "role": "user", "time": {"created": created}},
            "parts": [{"type": "text", "text": text}]}


def _assistant(mid, created, completed, text):
    tm = {"created": created}
    if completed is not None:
        tm["completed"] = completed
    return {"info": {"id": mid, "role": "assistant", "time": tm},
            "parts": [{"type": "text", "text": text}]}


def test_late_completed_reply_lands_at_bottom():
    rows = [_user("u1", 1000, "what is X?"),
            _assistant("a1", 1001, 1090, "the long answer"),
            _user("u2", 1040, "next")]
    order = [r["info"]["id"] for r in _chronological(rows)]
    assert order == ["u1", "u2", "a1"], order


def test_normal_flow_reply_stays_under_prompt():
    rows = [_user("u1", 1000, "q"),
            _assistant("a1", 1001, 1002, "a"),
            _user("u2", 1040, "next")]
    order = [r["info"]["id"] for r in _chronological(rows)]
    assert order == ["u1", "a1", "u2"], order


def test_store_late_completion_upsert_reroutes_to_bottom(tmp_path):
    p = str(tmp_path / "serve-msgs.json")
    st = MessageStore(p)
    u1 = _user("u1", 1000, "q")
    a0 = _assistant("a1", 1001, None, "")
    u2 = _user("u2", 1040, "next")
    st.append(SID, u1["info"], u1["parts"])
    st.append(SID, a0["info"], a0["parts"])
    st.append(SID, u2["info"], u2["parts"])
    a1 = _assistant("a1", 1001, 1090, "the long answer")
    st.append(SID, a1["info"], a1["parts"])
    order = [r["info"]["id"] for r in _chronological(st.list(SID))]
    assert order == ["u1", "u2", "a1"], order
