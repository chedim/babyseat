"""The bare banner (operator 2026-11-11): the banner slot can hold a TINY PLACEHOLDER THAT INSTRUCTS, made by the
banner pack itself (a cliffhanger getter, `Banner.bare`), instead of the whole block. `mode` on the chat's banner
instance picks: 'full' (the block), 'help' (the placeholder: nothing recalled), 'off' (nothing). With no `mode`, a
seat that tells the pack `map` (map mode) shows the placeholder and every other seat the full block."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from test_banner_c1f import _fake, seat  # noqa: E402,F401  (the same three-row world)

from membrane import fields_live as FL  # noqa: E402
from membrane import recall_banner as RB  # noqa: E402

ASK = "what is the battery voltage on the boat?"


def _run(seat, monkeypatch, facts=None, ask=ASK, now=100.0, calls=None):
    def fake(ctx, name, args):
        if calls is not None:
            calls.append(name)
        return _fake(ctx, name, args)
    monkeypatch.setattr(RB, "c1f_call", fake)
    r = seat.live.banner(object(), "owner", "ses_op", "ses_op", ask, now=now, facts=facts or {})
    assert r["error"] is None, r
    return r


def _dial(seat, **props):
    d = seat.entity_store.docs[FL.banner_eid("owner", "ses_op")]
    d.properties = {**d.properties, **props}
    seat.entity_store.upsert(d, embedder=None)
    seat.live.settle()


def test_a_map_mode_seat_shows_a_tiny_placeholder_that_instructs(seat, monkeypatch):
    calls = []
    r = _run(seat, monkeypatch, facts={"map": True}, ask="", calls=calls)      # the seat writes no ask on a map seat
    text = r["text"] or ""
    assert text.startswith("BANNER")
    assert FL.banner_eid("owner", "ses_op") in text            # where its settings are
    assert "mine" in text and "entity edit" in text and "class=MyBanner" in text   # and how to do it
    assert len(text) < 900                                     # short
    assert "#3 the bilge pump ran twice" not in text           # no recalled facts
    assert "recall" not in calls and "rerank" not in calls     # and nothing was recalled for it


def test_without_the_map_fact_the_full_block_is_unchanged(seat, monkeypatch):
    r = _run(seat, monkeypatch, facts={"map": False})
    assert "#3 the bilge pump ran twice" in (r["text"] or "")
    assert not (r["text"] or "").startswith("BANNER")


def test_a_map_seat_never_shows_the_full_block_whatever_the_mode(seat, monkeypatch):
    # operator 2026-11-11: it should NEVER load the standard banner in map mode
    _run(seat, monkeypatch, facts={"map": True})
    _dial(seat, mode="full")
    r = _run(seat, monkeypatch, facts={"map": True}, ask=ASK + " again", now=101.0)
    assert (r["text"] or "").startswith("BANNER")                      # full is not honoured on a map seat
    assert "#3 the bilge pump ran twice" not in (r["text"] or "")
    _dial(seat, mode="off")
    r = _run(seat, monkeypatch, facts={"map": True}, ask=ASK + " once more", now=102.0)
    assert not r["text"]                                               # off: nothing


def test_a_seat_that_is_not_map_mode_picks_its_banner_with_the_mode_dial(seat, monkeypatch):
    _run(seat, monkeypatch, facts={"map": False})
    _dial(seat, mode="help")
    r = _run(seat, monkeypatch, facts={"map": False}, ask=ASK + " help", now=101.0)
    assert (r["text"] or "").startswith("BANNER")
    _dial(seat, mode="off")
    r = _run(seat, monkeypatch, facts={"map": False}, ask=ASK + " off", now=102.0)
    assert not r["text"]
    _dial(seat, mode="full")
    r = _run(seat, monkeypatch, facts={"map": False}, ask=ASK + " full", now=103.0)
    assert "#3 the bilge pump ran twice" in (r["text"] or "")


def test_the_placeholder_comes_from_the_pack_not_from_python():
    pack = (Path(FL.__file__).parent / "packs" / "banner.c1f").read_text()
    assert "Banner.bare getter" in pack and "Banner.shown getter" in pack


def test_the_map_block_carries_the_placeholder_first(monkeypatch):
    from membrane import momentum_map as MM
    msgs = [{"role": "system", "content": "doctrine"}, {"role": "user", "content": "now"}]
    fld = __import__("types").SimpleNamespace(_states={}, _bg=lambda s: None)
    monkeypatch.setattr(MM, "lines", lambda *a, **k: ["m:goal:x  ↗ 1.0  a · b · c"])
    out = MM.rewrite(msgs, fld, None, "in: chat", chat="c", banner="BANNER: a tiny placeholder")
    body = out[1]["content"]
    assert body.startswith("BANNER: a tiny placeholder")
    assert MM.HEADER in body and "m:goal:x" in body
    plain = MM.rewrite(msgs, fld, None, "in: chat", chat="c")
    assert not plain[1]["content"].startswith("BANNER")                 # no banner given: as before


def test_conv_banner_bare_asks_the_pack_with_the_map_fact_and_recalls_nothing(seat, monkeypatch):
    from membrane import conv_banner as CB
    from membrane import lanes as L
    calls = []

    def fake(ctx, name, args):
        calls.append(name)
        return _fake(ctx, name, args)
    monkeypatch.setattr(RB, "c1f_call", fake)
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    monkeypatch.setattr(L, "_last_user_text", lambda body: ASK)
    text = CB.bare(seat, {"_turn_sid": "ses_op", "messages": [{"role": "user", "content": ASK}]}, "ses_op")
    assert (text or "").startswith("BANNER")
    assert "recall" not in calls
    monkeypatch.setattr(FL, "CURRENT", None)
    assert CB.bare(seat, {}, "ses_op") == CB.BARE_FALLBACK            # no field engine: the introductory placeholder


def test_without_the_pack_a_map_seat_falls_back_to_the_introductory_placeholder_never_the_standard_banner(seat, monkeypatch):
    from membrane import conv_banner as CB
    from membrane import lanes as L
    monkeypatch.setattr(FL, "CURRENT", None)                           # the pack cannot answer
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    text = CB.bare(seat, {}, "ses_op")
    assert text == CB.BARE_FALLBACK and text.startswith("BANNER") and len(text) < 700


def test_the_standard_banner_builder_refuses_to_run_in_map_mode(seat, monkeypatch):
    from membrane import conv_banner as CB
    from membrane import lanes as L
    calls = []

    def fake(ctx, name, args):
        calls.append(name)
        return _fake(ctx, name, args)
    monkeypatch.setattr(RB, "c1f_call", fake)
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    monkeypatch.setenv("MEMBRANE_EXP_MOMENTUM_MAP", "1")
    body = {"_turn_sid": "ses_op", "messages": [{"role": "user", "content": ASK}]}
    out = CB.build(seat, body, "ses_op")
    assert (out or "").startswith("BANNER")
    assert "recall" not in calls and "rerank" not in calls
    monkeypatch.setattr(FL, "CURRENT", None)                           # even with no field engine at all
    assert (CB.build(seat, body, "ses_op") or "").startswith("BANNER")


def test_map_mode_never_rebuilds_the_history_or_past_answers(seat, monkeypatch):
    # operator 2026-11-11 (the paste): "THE CONVERSATION SO FAR" + "PAST ANSWERS" rode a map-mode turn through the
    # no-context rebuild (_recon_block); map mode has no history and no recall block
    from membrane import entity_block as EB
    from membrane import lanes as L
    called = []
    monkeypatch.setattr(EB, "reconstruct", lambda *a, **k: called.append("reconstruct") or "THE CONVERSATION SO FAR ...")
    monkeypatch.setattr(EB, "render_for", lambda *a, **k: called.append("render_for") or "PAST ANSWERS ...")
    monkeypatch.setattr(L, "_own_session_prefix", lambda srv: "")
    monkeypatch.setattr(L, "_is_loop_turn", lambda srv, body: False)
    monkeypatch.setattr(L, "_banner_top", lambda srv, q: [])
    monkeypatch.delenv("MEMBRANE_EXP_MOMENTUM_MAP", raising=False)
    assert "THE CONVERSATION SO FAR" in (L._recon_block(seat, object(), {}, "q", None) or "")     # other seats: unchanged
    called.clear()
    monkeypatch.setenv("MEMBRANE_EXP_MOMENTUM_MAP", "1")
    assert L._recon_block(seat, object(), {}, "q", None) is None
    assert called == []


def test_the_placeholder_is_asked_of_the_pack_once_per_chat_and_mode(seat, monkeypatch):
    from membrane import conv_banner as CB
    from membrane import lanes as L
    monkeypatch.setattr(RB, "c1f_call", _fake)
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    builds = []
    real = CB.pack_block

    def counted(*a, **k):
        builds.append(1)
        return real(*a, **k)
    monkeypatch.setattr(CB, "pack_block", counted)
    body = {"_turn_sid": "ses_op"}
    first = CB.bare(seat, body, "ses_op")
    assert first.startswith("BANNER")
    assert CB.bare(seat, body, "ses_op") == first and CB.bare(seat, body, "ses_op") == first
    assert len(builds) == 1                                             # not once per turn
    _dial(seat, mode="off")
    assert CB.bare(seat, body, "ses_op") == "" and len(builds) == 2     # the dial changed: asked again


def test_a_failed_build_is_never_shown_in_the_placeholders_place(seat, monkeypatch):
    from membrane import conv_banner as CB
    from membrane import lanes as L
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    monkeypatch.setattr(CB, "pack_block", lambda *a, **k: "▸ the banner failed: boom")
    assert CB.bare(seat, {"_turn_sid": "ses_fail"}, "ses_fail") == CB.BARE_FALLBACK


def _own_banner(seat, getter):
    """The chat's banner instance made of a class of the model's own (it declares its own `text`)."""
    from membrane.entities import MEMBRANE_NS, QuantumEntity
    seat._loop_runner.sessions["ses_own"] = {"owner": "owner"}
    seat.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + "pack:ownbanner", cls="Pack", properties={
        "name": "ownbanner", "c1f": "PACK ownbanner 1\nIMPORT banner 1\n" + getter + "\n"}))
    seat.live.settle()
    eid = FL.banner_eid("owner", "ses_own")
    seat.entity_store.upsert(QuantumEntity(eid=eid, cls="OwnBanner", properties={"name": "my banner"}))
    seat.live.settle()


def test_a_banner_of_the_models_own_class_is_what_a_map_seat_shows(seat, monkeypatch):
    # operator 2026-11-11: "free reign over your banner": the standard class shows the placeholder, the model's own
    # class shows what ITS text says (not cached, not replaced by the fallback)
    from membrane import conv_banner as CB
    from membrane import lanes as L
    monkeypatch.setattr(RB, "c1f_call", _fake)
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    _own_banner(seat, "OwnBanner.mine getter: 'MY BANNER · ' + str(this.chat)")
    out = CB.bare(seat, {"_turn_sid": "ses_own"}, "ses_own")
    assert out.startswith("MY BANNER") and "BANNER: nothing is pushed" not in out
    assert CB.bare(seat, {"_turn_sid": "ses_own"}, "ses_own") == out          # (same text; asked again each turn)


def test_an_error_in_the_models_own_banner_is_shown_not_replaced(seat, monkeypatch):
    from membrane import conv_banner as CB
    from membrane import lanes as L

    def broken(ctx, name, args):
        if name == "recall":
            raise RuntimeError("the store is away")
        return _fake(ctx, name, args)
    monkeypatch.setattr(RB, "c1f_call", broken)
    monkeypatch.setattr(L, "_efield_on", lambda srv: object())
    _own_banner(seat, "OwnBanner.mine getter: join(recall(this.ask, 10, this.session), '\\n')")
    body = {"_turn_sid": "ses_own", "messages": [{"role": "user", "content": ASK}]}
    out = CB.bare(seat, body, "ses_own")
    assert "the banner failed" in out and "the store is away" in out and out != CB.BARE_FALLBACK


# ---- extending Banner (operator 2026-11-11: "our classes are already interfaces, why can't Memo extend Banner?") ----
def _own_pack(seat, text_getter=""):
    from membrane.entities import MEMBRANE_NS, QuantumEntity
    seat.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + "pack:extbanner", cls="Pack", properties={
        "name": "extbanner", "c1f": "PACK extbanner 1\nIMPORT banner 1\nExtBanner.m_head getter: 'MINE · ' + str(this.chat)\n"
                                    + text_getter}))
    seat.live.settle()


def _standard_then(seat, monkeypatch, **switch):
    """The seat makes the chat's banner (a Banner); then the model switches it (class / parents / properties)."""
    seat._loop_runner.sessions["ses_ext"] = {"owner": "owner"}
    monkeypatch.setattr(RB, "c1f_call", _fake)
    eid = FL.banner_eid("owner", "ses_ext")
    seat.live.banner(object(), "owner", "ses_ext", "ses_ext", ASK, now=99.0, facts={})
    d = seat.entity_store.docs[eid]
    for k, v in switch.items():
        if k == "cls":
            d.cls = v
        else:
            d.properties = {**d.properties, k: v}
    seat.entity_store.upsert(d, embedder=None)
    seat.live.settle()
    return eid


def test_switching_a_banner_instance_to_a_class_of_its_own_reaches_the_engine(seat, monkeypatch):
    _own_pack(seat, "ExtBanner.mine getter: this.m_head + ' | mine'\n")
    _standard_then(seat, monkeypatch, cls="ExtBanner")
    r = seat.live.banner(object(), "owner", "ses_ext", "ses_ext", ASK + " now", now=100.0, facts={})
    assert r["error"] is None, r
    assert (r["text"] or "").startswith("MINE · ses_ext | mine")          # not the old Banner layout


def test_a_class_that_extends_banner_inherits_what_it_does_not_override(seat, monkeypatch):
    _own_pack(seat, "ExtBanner.mine getter: this.m_head + ' | ' + str(len(this.lines))\n")
    _standard_then(seat, monkeypatch, cls="ExtBanner")                    # no parents named: the seat adds Banner
    r = seat.live.banner(object(), "owner", "ses_ext", "ses_ext", ASK + " again", now=100.0, facts={})
    assert r["error"] is None, r
    assert len(r["lines"]) > 0                                            # Banner's own `lines` getter ran for it
    assert (r["text"] or "").endswith(f"| {len(r['lines'])}")
    d = seat.entity_store.docs[FL.banner_eid("owner", "ses_ext")]
    assert "Banner" in (d.properties.get("parents") or [])


def test_a_hand_made_banner_instance_gets_the_dials_and_its_parent(seat, monkeypatch):
    from membrane.entities import QuantumEntity
    _own_pack(seat, "")
    seat._loop_runner.sessions["ses_ext"] = {"owner": "owner"}
    monkeypatch.setattr(RB, "c1f_call", _fake)
    eid = FL.banner_eid("owner", "ses_ext")
    seat.entity_store.upsert(QuantumEntity(eid=eid, cls="ExtBanner", properties={"name": "mine"}))
    seat.live.settle()
    r = seat.live.banner(object(), "owner", "ses_ext", "ses_ext", ASK, now=100.0, facts={})
    assert r["error"] is None, r                                          # was: Banner.lines: int(None)
    d = seat.entity_store.docs[eid]
    assert d.properties.get("n") and d.properties.get("pool") and "Banner" in (d.properties.get("parents") or [])
