"""THE ACT PLANNER (the consult WITHOUT the veto -- the operator's
ruling): speak() still consults the ActTable for the realization (the
transition table's argmax selects the act), but a learned stay-silent
no longer drops -- NOTHING is ever vetoed. The delivered batch's
(sig, act) rides the next turn as the reinforcement label (the
operator's reaction = the grade) -- the only label that ever accrues,
because only delivered speech is graded."""

from membrane.acts import ActTable
from membrane.opencode import speak, drain_speech, _speak_block


class _Srv:
    """A minimal srv: the planner's seams only."""
    def __init__(self):
        self._act_table = ActTable()
        self.peers = None


def test_unlearned_context_still_speaks():
    """THE RULING: an unlearned cluster's stay-silent select loses the
    vote -- the message queues with the caller's kind and delivers."""
    srv = _Srv()
    # untouched consult default (the unlearned cluster's argmax):
    assert srv._act_table.select("unknown") == "stay-silent"
    assert speak(srv, "hello", kind="note") is True
    msgs = drain_speech(srv)
    assert len(msgs) == 1
    assert msgs[0]["act"] == "note"
    # and what the consult OBSERVES is the delivered act, never a
    # silence: the row is fed by what actually went out.
    assert srv._act_table.select("unknown") == "note"


def test_learned_act_speaks_with_table_pick():
    srv = _Srv()
    sig = "escalate|0.8"
    srv._act_table.observe(sig, "note-pattern")   # lived: this cluster spoke
    srv._act_table.observe(sig, "note-pattern")
    srv._last_context_sig = sig
    assert speak(srv, "the field grew", kind="note") is True
    msgs = drain_speech(srv)
    assert len(msgs) == 1
    assert msgs[0]["act"] == "note-pattern"      # the table's pick carried
    assert msgs[0]["sig"] == sig


def test_alert_kind_kept_when_table_agrees():
    """Alerts propose their kind; a table that lived this cluster
    speaking keeps the alert act (the pick equals the kind)."""
    srv = _Srv()
    sig = "replay|0.9"
    srv._act_table.observe(sig, "serve-receipt")
    srv._act_table.observe(sig, "serve-receipt")
    srv._last_context_sig = sig
    assert speak(srv, "ALERT: x", kind="serve-receipt") is True
    assert drain_speech(srv)[0]["act"] == "serve-receipt"


def test_silent_learning_loses_the_vote():
    """A cluster that LEARNED stay-silent still speaks (the veto is
    retired): the learned silence just loses the realization vote, so
    the message delivers with the CALLER'S kind."""
    srv = _Srv()
    sig = "field-only|0.7"
    srv._act_table.observe(sig, "stay-silent")
    srv._act_table.observe(sig, "stay-silent")
    srv._last_context_sig = sig
    assert speak(srv, "chatter", kind="note") is True
    msgs = drain_speech(srv)
    assert len(msgs) == 1
    assert msgs[0]["act"] == "note"


def test_reinforcement_label_rides_next_turn():
    """drain stashes (sig, act); the next turn's flow grades it."""
    srv = _Srv()
    sig = "escalate|0.8"
    srv._act_table.observe(sig, "note-pattern")
    srv._act_table.observe(sig, "note-pattern")
    srv._last_context_sig = sig
    speak(srv, "here", kind="note")
    drain_speech(srv)
    assert srv._pending_speech_label == (sig, "note-pattern")
    # the productive turn: +1
    before = srv._act_table._table[sig]["note-pattern"]
    srv._act_table.learn(sig, "note-pattern", 1)
    assert srv._act_table._table[sig]["note-pattern"] == before + 1
    # the re-ask turn: -1
    srv._act_table.learn(sig, "note-pattern", -1)


def test_speak_block_carries_the_relay():
    """The drain returns the batch; _speak_block renders the marked
    block the model relays."""
    srv = _Srv()
    srv._act_table.observe("x|0.8", "note")
    srv._act_table.observe("x|0.8", "note")
    srv._last_context_sig = "x|0.8"
    speak(srv, "membrane says hi", kind="note")
    block = _speak_block(srv)
    assert block is not None and "[membrane-says]" in block