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
    r = _run(seat, monkeypatch, facts={"map": True}, calls=calls)
    text = r["text"] or ""
    assert text.startswith("BANNER")
    assert FL.banner_eid("owner", "ses_op") in text            # where its settings are
    assert "mode" in text and "entity edit" in text            # and how to change them
    assert len(text) < 320                                     # tiny
    assert "#3 the bilge pump ran twice" not in text           # no recalled facts
    assert "recall" not in calls and "rerank" not in calls     # and nothing was recalled for it


def test_without_the_map_fact_the_full_block_is_unchanged(seat, monkeypatch):
    r = _run(seat, monkeypatch, facts={"map": False})
    assert "#3 the bilge pump ran twice" in (r["text"] or "")
    assert not (r["text"] or "").startswith("BANNER")


def test_the_mode_dial_overrides_the_seat(seat, monkeypatch):
    _run(seat, monkeypatch, facts={"map": True})
    _dial(seat, mode="full")
    r = _run(seat, monkeypatch, facts={"map": True}, ask=ASK + " again", now=101.0)
    assert "#3 the bilge pump ran twice" in (r["text"] or "")          # full, though the seat says map
    _dial(seat, mode="off")
    r = _run(seat, monkeypatch, facts={"map": True}, ask=ASK + " once more", now=102.0)
    assert not r["text"]                                               # off: nothing
    _dial(seat, mode="help")
    r = _run(seat, monkeypatch, facts={"map": False}, ask=ASK + " last", now=103.0)
    assert (r["text"] or "").startswith("BANNER")                      # help, though the seat is not map mode


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
    assert CB.bare(seat, {}, "ses_op") is None                         # no field engine: no placeholder
