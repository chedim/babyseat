"""#333, part 2: Blurp speaks by nudging chat:out.

"Speaking: composing by nudges" (decided 2026-10-21):
  - nudging `chat:out` opens a draft; nudging it again sends it -- no
    separate start or send entities;
  - while a draft is open, nudges don't move anything: each one is captured
    into the draft as the move it would have made (the thing's position as if
    nudged; nudges move no field position yet -- decision 2 -- so that is the
    thing's own spot), in order;
  - what the person sees is the inbound decomposition reversed: the captured
    moves become one composed vector, and its non-negative fit (NNLS, #335)
    over the sentences Blurp has heard picks the line -- echolalia, no
    generator (the LLM talks TO Blurp, never AS it);
  - sending an empty draft is an error (energy.error, 0.01);
  - an open draft is dropped at sleep.

The heard sentences are those of the person's private chat with Blurp
(#337, session blurp:<person>): never another chat's, so one person's words
never reach another. The line goes out as Blurp's own (chat_io.on_edit
by="self": its private chat, the sprout mark, `from: self`, the field hears
it as role self), never on a paused seat."""
import time

import numpy as np
import pytest

from membrane import chat_io as CIO
from membrane import energy as EN
from membrane import self_loop as SL
from membrane import self_world as SW
from membrane import server as S
from membrane import streams as ST
from membrane import toybox as TB
from membrane.efield import _nnls
from membrane.eids import field_id
from membrane.entities import CLASS_CONFIG, MEMBRANE_NS, QuantumEntity, entity_id

from tests.test_nudge_333 import DIM, T0, _cid, _lines, _seat  # noqa: F401
from tests.test_nudge_333 import _data  # noqa: F401  autouse fixture

COUT = entity_id(CLASS_CONFIG, CIO.name_of(""))          # the operator's chat:out (in a fresh world)
MOVE = np.full(DIM, 0.01)
HEARD = ["the ball is red.", "i like the tower.", "dinner is at six.", "the cat sleeps.", "rain again today."]


class _Cap:
    def __init__(self):
        self.lines = []

    def on_self(self, text, session=None, ts=None):
        self.lines.append({"self": text, "session": session})

    def on_turn(self, query, prose="", session=None, **kw):
        pass


def _chat_seat(toys=(), record=None):
    srv = _seat(record=record, toys=toys)
    srv.capturer = _Cap()
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                     lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    CIO.entity(srv, "")                                   # the operator's chat:out exists
    assert SW.has(srv, COUT)
    return srv, r


def _hear(srv, texts, session="blurp:operator", role="user"):
    """The field hears these lines in a chat (one sentence each)."""
    fld = srv.efield
    for i, t in enumerate(texts):
        fld.write_events([ST.Event(stream="discourse", role=role, text=t, ts=time.time() + i * 1e-3)],
                         session=session)
    fld.process()


def _thing(srv, name, text):
    """A thing of Blurp's world whose field spot is `text`'s embedding."""
    eid = _cid(name)
    srv.entity_store.upsert(QuantumEntity(eid=eid, cls="Component", properties={"name": name}), embedder=None)
    SW.join(srv, [eid], "test")
    assert TB.place(srv, eid, text=text) is True
    return eid


def _said(r):
    """Every assistant line posted into any chat: (session, text, from)."""
    out = []
    for s in r.sessions.list():
        for m in r.messages.list(s["id"]):
            if m["info"]["role"] == "assistant":
                out.append((s["id"], "\n".join(p.get("text", "") for p in m["parts"]), m["info"].get("from")))
    return out


def _nudges(srv):
    return [r for r in _lines(srv) if r.get("kind") == "nudge"]


def _fees(srv):
    return [r for r in _lines(srv) if r.get("kind") == "error" and r.get("cost")]


# ---- the draft: open, capture, send ------------------------------------------------------

def test_a_nudge_at_chat_out_opens_a_draft_and_posts_nothing():
    srv, r = _chat_seat()
    SL._nudge_once(srv, 5, T0, COUT, MOVE)
    line = _nudges(srv)[-1]
    assert line["eid"] == COUT and line["rc"] == SL.NUDGE_MOVED and line["draft"] == 5
    assert SL._rt(srv)["draft"]["n"] == 5
    assert _said(r) == [] and not _fees(srv)


def test_while_a_draft_is_open_a_nudge_moves_nothing_and_is_captured():
    srv, r = _chat_seat(toys=("ball",))
    ball = _cid("toy:ball")
    TB.place(srv, ball)
    e0 = EN.level(srv)
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, ball, MOVE)
    SL._nudge_once(srv, 3, T0 + 20, ball, MOVE)              # twice: still nothing, still free
    assert TB.peek("toy:ball")["side"] == "self"              # the ball did not roll
    caps = _nudges(srv)[1:]
    assert [c["rc"] for c in caps] == [SL.NUDGE_NOTHING, SL.NUDGE_NOTHING]
    assert all(c["draft"] == 1 for c in caps)
    assert SL._rt(srv)["draft"]["eids"] == [ball, ball]       # in order
    assert EN.level(srv) == pytest.approx(e0) and not _fees(srv)


def test_the_world_gate_still_refuses_while_drafting():
    srv, r = _chat_seat(toys=("ball",))
    ball = _cid("toy:ball")
    SW._doc(srv)["members"].pop(field_id(ball))
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, ball, MOVE)
    assert _nudges(srv)[-1]["rc"] == SL.NUDGE_REFUSED
    assert len(_fees(srv)) == 1
    assert SL._rt(srv)["draft"]["eids"] == []                 # nothing captured


def test_nudging_chat_out_again_sends_the_heard_sentence_that_fits_what_it_captured():
    srv, r = _chat_seat()
    _hear(srv, HEARD[:3])
    lamp = _thing(srv, "lamp", HEARD[1])                       # its spot IS "i like the tower."
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, lamp, MOVE)
    SL._nudge_once(srv, 3, T0 + 20, COUT, MOVE)
    said = _said(r)
    assert len(said) == 1
    sid, text, by = said[0]
    assert text == f"{CIO.SELF_MARK} {HEARD[1]}" and by == "self"
    assert sid == CIO.blurp_chat(srv, "", create=False)       # its private chat with the person
    assert srv.capturer.lines[-1] == {"self": HEARD[1], "session": "blurp:operator"}   # heard as its own
    send = _nudges(srv)[-1]
    assert send["rc"] == SL.NUDGE_MOVED and send["draft"] == 1 and send["moves"] == 1
    assert len(send["said"]) == 1
    assert "draft" not in SL._rt(srv)                          # sent: the draft is closed
    assert not _fees(srv)


def test_only_the_persons_private_chat_is_heard_for_the_line():
    """Another chat's sentences never go out: what it captured fits a line said
    elsewhere best, but only the private chat's sentences are its words."""
    srv, r = _chat_seat()
    _hear(srv, [HEARD[0]], session="s:coding")
    _hear(srv, HEARD[1:3])
    lamp = _thing(srv, "lamp", HEARD[0])
    kite = _thing(srv, "kite", HEARD[1])
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, lamp, MOVE)
    SL._nudge_once(srv, 3, T0 + 20, kite, MOVE)
    SL._nudge_once(srv, 4, T0 + 30, kite, MOVE)
    SL._nudge_once(srv, 5, T0 + 40, COUT, MOVE)
    (_, text, _), = _said(r)
    assert HEARD[1] in text and HEARD[0] not in text


def test_a_line_combines_at_most_say_sentences_heard_sentences(monkeypatch):
    monkeypatch.setattr(SL, "SAY_SENTENCES", 2)
    srv, r = _chat_seat()
    _hear(srv, HEARD)
    things = [_thing(srv, f"t{i}", h) for i, h in enumerate(HEARD)]
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    for i, eid in enumerate(things):
        SL._nudge_once(srv, 2 + i, T0 + 10 * (i + 1), eid, MOVE)
    SL._nudge_once(srv, 9, T0 + 100, COUT, MOVE)
    (_, text, _), = _said(r)
    assert sum(h in text for h in HEARD) == 2
    assert len(_nudges(srv)[-1]["said"]) == 2


def test_sending_an_empty_draft_is_an_error_and_posts_nothing():
    srv, r = _chat_seat()
    _hear(srv, HEARD[:2])
    e0 = EN.level(srv)
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, COUT, MOVE)
    send = _nudges(srv)[-1]
    assert send["rc"] == SL.NUDGE_REFUSED and send["moves"] == 0
    assert len(_fees(srv)) == 1 and EN.level(srv) == pytest.approx(e0 - EN.ERROR_COST)
    assert _said(r) == [] and "draft" not in SL._rt(srv)


def test_with_nothing_heard_it_has_no_words_and_it_is_free():
    srv, r = _chat_seat()
    lamp = _thing(srv, "lamp", "a lamp.")
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, lamp, MOVE)
    SL._nudge_once(srv, 3, T0 + 20, COUT, MOVE)
    send = _nudges(srv)[-1]
    assert send["rc"] == SL.NUDGE_NOTHING and send["moves"] == 1 and send["said"] == []
    assert _said(r) == [] and not _fees(srv) and "draft" not in SL._rt(srv)


def test_a_nudge_at_another_persons_chat_out_while_drafting_is_captured_not_sent():
    srv, r = _chat_seat()
    other = CIO.entity(srv, "Ann").eid
    SW.join_person(srv, "Ann")
    TB.place(srv, other)
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, other, MOVE)
    assert _nudges(srv)[-1]["rc"] == SL.NUDGE_NOTHING
    assert SL._rt(srv)["draft"]["eids"] == [other] and _said(r) == []


# ---- the pause and sleep -----------------------------------------------------------------

def test_a_paused_seat_sends_nothing_and_keeps_the_draft():
    srv, r = _chat_seat()
    _hear(srv, HEARD[:2])
    lamp = _thing(srv, "lamp", HEARD[0])
    SL._nudge_once(srv, 1, T0, COUT, MOVE)
    SL._nudge_once(srv, 2, T0 + 10, lamp, MOVE)
    srv.paused = True
    SL._nudge_once(srv, 3, T0 + 20, COUT, MOVE)
    assert _said(r) == []
    assert _nudges(srv)[-1]["rc"] == SL.NUDGE_NOTHING and not _fees(srv)
    assert SL._rt(srv)["draft"]["eids"] == [lamp]


def test_an_open_draft_is_dropped_at_sleep_and_named_on_the_sleep_line():
    srv, r = _chat_seat()
    lamp = _thing(srv, "lamp", "a lamp.")
    SL._nudge_once(srv, 4, T0, COUT, MOVE)
    SL._nudge_once(srv, 5, T0 + 10, lamp, MOVE)
    t = time.time()
    EN.tick(srv, t, free=10 ** 12)
    EN.tick(srv, t + 1, cpu_s=1 / EN.CPU_PER_S, free=10 ** 12)
    sleep = [x for x in SL._read(srv) if x.get("kind") == "sleep"][-1]
    assert sleep["dropped"] == {"draft": 4, "moves": 1}
    assert "draft" not in SL._rt(srv)


# ---- chat:out on the field, and the cycle ------------------------------------------------

def test_once_it_nudges_chat_out_gets_a_field_spot_so_it_can_be_aimed_at():
    rec = [{"ts": T0, "kind": "birth", "by": "operator"},
           {"ts": T0 + 1, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9}]
    srv, r = _chat_seat(record=rec)
    SL.set_mode(srv, "on")
    SL._rt(srv)["isolate"] = {"feel", "nudge"}
    assert SL._spot(srv, COUT) is None
    SL.cycle(srv, now=T0 + 100)
    assert SL._spot(srv, COUT) is not None
    assert COUT in SL._spots(srv, DIM)


def test_the_cycle_speaks_only_by_nudging_and_every_line_is_blurps(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 2 * 11.0)
    rec = [{"ts": T0, "kind": "birth", "by": "operator"},
           {"ts": T0 + 1, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9}]
    srv, r = _chat_seat(record=rec)
    _hear(srv, HEARD)
    _thing(srv, "lamp", HEARD[2])
    SL.set_mode(srv, "on")
    SL._rt(srv)["isolate"] = {"feel", "nudge"}
    for k in range(200):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    ns = [x for x in _nudges(srv) if x["eid"] == COUT]
    assert any(x.get("moves") is None and x["rc"] == SL.NUDGE_MOVED for x in ns)     # it opened drafts
    sends = [x for x in ns if x.get("moves") is not None]
    assert sends                                                                     # ...and sent them
    posted = [x for x in sends if x["rc"] == SL.NUDGE_MOVED]
    said = _said(r)
    assert len(said) == len(posted)
    for _, text, by in said:
        assert by == "self" and text.startswith(CIO.SELF_MARK)
        assert any(h in text for h in HEARD)


# ---- the capped fit ----------------------------------------------------------------------

def test_the_capped_fit_uses_at_most_max_on_columns():
    rng = np.random.default_rng(3)
    M = rng.normal(size=(DIM, 8))
    x = M[:, :5] @ np.array([1.0, 0.8, 0.6, 0.4, 0.2])
    full = _nnls(M, x)
    assert (full > 1e-9).sum() >= 4
    capped = _nnls(M, x, max_on=2)
    assert (capped > 0).sum() <= 2 and capped.min() >= 0.0
