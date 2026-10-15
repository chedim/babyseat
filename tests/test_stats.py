"""Tests for membrane/stats.py -- the seat's token/call accounting and the
naive-full-history baseline that shows whether the bounded window + recall
actually save tokens (no model call; reads a serve-msgs.json store)."""
from __future__ import annotations

import json
import itertools

import pytest

from membrane.stats import (
    accounting,
    baseline,
    load_store,
    CHARS_PER_TOKEN,
    HISTORY_MAX_MESSAGES,
    HISTORY_TEXT_CAP,
)

_ids = itertools.count(1)


def _tok(inp: int = 0, out: int = 0, reason: int = 0, cache_read: int = 0) -> dict:
    return {"total": inp + out, "input": inp, "output": out,
            "reasoning": reason, "cache": {"read": cache_read, "write": 0}}


def _row(role: str, text: str = "", tokens: dict | None = None,
         parts: list | None = None, sid: str = "s") -> dict:
    info = {"id": f"{role}-{next(_ids)}", "sessionID": sid, "role": role}
    if tokens is not None:
        info["tokens"] = tokens
    elif role == "assistant":
        info["tokens"] = _tok()
    ps = parts if parts is not None else ([{"type": "text", "text": text}]
                                          if text else [])
    return {"info": info, "parts": ps}


def test_accounting_counts_and_tokens():
    store = {
        "a": [
            _row("user", "x" * 40),
            _row("assistant", "x" * 80, tokens=_tok(100, 20, 5, 50)),
        ],
        "b": [
            _row("user", "x" * 12),
            _row("assistant", "x" * 16, tokens=_tok(40, 8)),
        ],
    }
    a = accounting(store)
    assert a["sessions"] == 2
    assert a["user_msgs"] == 2
    assert a["assistant_turns"] == 2
    assert a["input"] == 140
    assert a["output"] == 28
    assert a["reasoning"] == 5
    assert a["cache_read"] == 50
    assert a["tool_parts"] == 0
    assert a["turns_with_rounds"] == 0
    assert a["round_sum"] == 0
    assert a["injection_sizes"] == []


def test_accounting_extracts_rounds_and_injection_sizes():
    store = {
        "c": [
            _row("assistant", "", tokens=_tok(), parts=[
                {"type": "tool", "tool": "membrane",
                 "state": {"status": "completed", "input": {"round": 3},
                           "title": "membrane injection · ~702 tokens",
                           "output": "..."},
                 "time": {}},
                {"type": "text", "text": "reply"},
            ]),
        ],
    }
    a = accounting(store)
    assert a["tool_parts"] == 1
    assert a["turns_with_rounds"] == 1
    assert a["round_sum"] == 3
    assert a["injection_sizes"] == [702]


def test_baseline_short_session_no_window_benefit():
    # Under HISTORY_MAX_MESSAGES prior rows, naive == bounded (nothing capped).
    rows = [
        _row("user", "x" * 40),
        _row("assistant", "x" * 80, tokens=_tok(inp=10)),
        _row("user", "x" * 12),
        _row("assistant", "x" * 16, tokens=_tok(inp=20)),
        _row("user", "x" * 8),
        _row("assistant", "x" * 20, tokens=_tok(inp=30)),
    ]
    b = baseline({"s": rows})
    # per user turn, prior text chars // CHARS_PER_TOKEN:
    #   U0 -> 0, U1 -> (40+80)//4=30, U2 -> (40+80+12+16)//4=37
    assert b["turns"] == 3
    assert b["naive_full"] == 0 + 30 + 37
    assert b["bounded"] == 0 + 30 + 37
    assert b["window_saved"] == 0
    assert b["actual_input"] == 10 + 20 + 30
    assert b["net_vs_naive"] == (0 + 30 + 37) - 60
    assert b["overhead"] == 60 - (0 + 30 + 37)


def test_baseline_window_and_text_cap():
    # 16 prior assistant rows; the last one is 8000 chars. The bounded window
    # keeps the last 12 only and caps any one message at HISTORY_TEXT_CAP.
    prior = [_row("assistant", "x" * (8 * i)) for i in range(1, 16)]
    prior.append(_row("assistant", "x" * 8000))
    rows = prior + [_row("user", "x" * 4)]
    b = baseline({"s": rows})
    # naive: all 16 prior = (8+16+...+120)=960 + 8000 = 8960 chars -> 2240
    assert b["naive_full"] == 8960 // CHARS_PER_TOKEN
    # bounded: last 12 (i=5..15 -> 40..120 = 880) + min(8000,cap)=4000
    assert b["bounded"] == (880 + HISTORY_TEXT_CAP) // CHARS_PER_TOKEN
    assert b["window_saved"] == b["naive_full"] - b["bounded"]
    assert b["window_saved"] > 0
    # one turn with 16 prior rows -> the "13-50 prior" bucket
    buckets = {x["label"]: x for x in b["buckets"]}
    assert buckets["13-50 prior"]["turns"] == 1
    assert buckets["13-50 prior"]["naive"] == b["naive_full"]
    assert buckets["13-50 prior"]["bounded"] == b["bounded"]


def test_load_store_dedupes_and_mends(tmp_path):
    p = tmp_path / "serve-msgs.json"
    p.write_text(json.dumps({"s": [
        {"info": {"id": "u1", "role": "user", "sessionID": "s"},
         "parts": [{"type": "text", "text": "hello"}]},
        {"info": {"id": "a1", "role": "assistant", "sessionID": "s"},
         "parts": [{"type": "text", "text": "world"}]},
        {"info": {"id": "a1", "role": "assistant", "sessionID": "s",
                  "tokens": _tok(7, 0, 0, 0)},
         "parts": [{"type": "text", "text": "world"}]},
    ]}))
    store = load_store(str(p))
    assert list(store) == ["s"]
    rows = store["s"]
    assert len(rows) == 2          # the duplicate a1 collapses to one row
    a1 = next(r for r in rows if r["info"]["id"] == "a1")
    # last wins: the token-bearing snapshot replaces the mended empty one
    assert a1["info"]["tokens"]["input"] == 7
    assert isinstance(a1["info"]["tokens"]["cache"], dict)


def test_render_accounting_is_not_empty():
    from membrane.stats import render_accounting
    a = accounting({"s": [
        _row("user", "x" * 8),
        _row("assistant", "x" * 8, tokens=_tok(1, 1)),
    ]})
    out = render_accounting(a)
    assert isinstance(out, str) and out.strip()
    assert "sessions" in out
