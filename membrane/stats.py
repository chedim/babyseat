"""The seat's token/call accounting + the naive-full-history baseline.

`membrane stats` reads a serve-msgs store (`.membrane/serve-msgs.json`) and
answers the operator's question with NUMBERS, not a hunch:

  * accounting -- sessions / user messages / assistant turns, tokens
    (input, output, reasoning, cache-read) from the upstream usage the
    ladder persisted, tool-call parts, tool-loop rounds, models, and the
    size of the recall injection. Read-only; no model call.
  * background  -- the model calls made OUTSIDE a serve turn (thinker,
    watch degrade, claim judge, router interview, /delegate), tallied per
    lane and model in `bg-usage.json` beside the store (`bgusage`, #200).
  * baseline    -- the "do we actually save tokens?" comparison. A naive
    client with no window and no memory replays the FULL transcript every
    turn (history grows O(n^2)); membrane replays only its bounded window
    (`HISTORY_MAX_MESSAGES` rows x `HISTORY_TEXT_CAP` chars) plus a small
    recall injection and the tool loop. The baseline isolates the window's
    saving from the recall/tool-loop tax and reports the net against the
    naive client, bucketed by how long the conversation had grown.

The chars->token estimate is ~4 chars/token -- the SAME estimate the seat
uses when the ladder answers without upstream usage (`server._tokens_estimate`),
so the baseline is honest on both sides of the comparison.

Read-only: the store is `json.loads`'d, never flushed -- safe to point at a
live seat's file. For the operator's "copies only" field rule, pass a copy
with `--store` if you prefer.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

CHARS_PER_TOKEN = 4          # matches server._tokens_estimate (~4 chars/token)
HISTORY_MAX_MESSAGES = 12    # matches server.HISTORY_MAX_MESSAGES
HISTORY_TEXT_CAP = 4000      # matches server.HISTORY_TEXT_CAP

_ZERO_TOKENS = {"total": 0, "input": 0, "output": 0, "reasoning": 0,
                "cache": {"read": 0, "write": 0}}
_INJ_RE = re.compile(r"~(\d+) tokens")


def read_raw(path: str) -> dict[str, list[dict]]:
    """The store as it stands on disk (#542): the snapshot `path`, then the
    append journal `path + ".jnl"` replayed over it -- one JSON line per
    changed row, {"sid", "row"}. Rows union by message id, the newer
    completed/created ts wins (a tie goes to the later line), first
    positions stay. A torn last line (a crash mid-append) is skipped."""
    # THE JOURNAL IS READ FIRST: a compaction replaces the snapshot, then
    # empties the journal, so journal-then-snapshot always sees either the
    # old journal + a snapshot that holds it, or both old files. The reverse
    # order could pair the old snapshot with the emptied journal (#542).
    try:
        text = Path(path + ".jnl").read_text()
    except FileNotFoundError:
        text = ""
    try:
        base = json.loads(Path(path).read_text())
    except FileNotFoundError:
        base = {}
    if not isinstance(base, dict):
        base = {}
    if not text:
        return base
    pos: dict = {}                      # (sid, mid) -> index in its list
    for sid, rows in base.items():
        for i, r in enumerate(rows):
            pos[(sid, str((r.get("info") or {}).get("id") or ""))] = i
    for ln in text.splitlines():
        try:
            e = json.loads(ln)
            sid, row = e["sid"], e["row"]
            info = row.get("info") or {}
        except Exception:
            continue                    # torn or foreign line
        mid = str(info.get("id") or "")
        if not mid:
            continue
        tm = info.get("time") or {}
        ver = tm.get("completed") or tm.get("created") or 0
        rows = base.setdefault(sid, [])
        i = pos.get((sid, mid))
        if i is None:
            pos[(sid, mid)] = len(rows)
            rows.append(row)
        else:
            ot = (rows[i].get("info") or {}).get("time") or {}
            if (ot.get("completed") or ot.get("created") or 0) <= ver:
                rows[i] = row
    return base


def load_store(path: str) -> dict[str, list[dict]]:
    """Read + dedupe a serve-msgs store, mirroring `server.MessageStore`'s
    load: rows collapse on message id (last snapshot wins, first position
    stays), and assistant rows missing a tokens dict are mended to zeros."""
    raw = read_raw(path)
    out: dict[str, list[dict]] = {}
    for sid, rows in raw.items():
        merged: dict[str, dict] = {}
        ordered: list[str] = []
        for r in rows:
            info = r.get("info") or {}
            mid = str(info.get("id") or "")
            if info.get("role") == "assistant" and \
                    not isinstance(info.get("tokens"), dict):
                info["tokens"] = dict(_ZERO_TOKENS)
                r["info"] = info
            if mid not in merged:
                ordered.append(mid)
            merged[mid] = r
        out[sid] = [merged[m] for m in ordered]
    return out


def _tokens(row: dict) -> tuple[int, int, int, int]:
    t = (row.get("info") or {}).get("tokens") or {}
    cache = t.get("cache")
    read = (cache.get("read") if isinstance(cache, dict) else 0) or 0
    return (t.get("input", 0) or 0, t.get("output", 0) or 0,
            t.get("reasoning", 0) or 0, read)


def _row_text(row: dict) -> str:
    """The conversation text a row contributes -- the SAME skip filters the
    seat's `_prior_history` applies, so both sides of the baseline see the
    same transcript (membraneOnly / speakerRouted / voiceInterim never
    replay; only user/assistant text parts)."""
    info = row.get("info") or {}
    if info.get("membraneOnly") or info.get("speakerRouted") or \
            info.get("voiceInterim"):
        return ""
    if info.get("role") not in ("user", "assistant"):
        return ""
    return "\n".join(
        str(p.get("text") or "") for p in row.get("parts") or []
        if isinstance(p, dict) and p.get("type") == "text").strip()


def _tok_est(chars: int) -> int:
    return max(1, chars // CHARS_PER_TOKEN)


def _bucket(prior: int) -> str:
    if prior <= HISTORY_MAX_MESSAGES:
        return "1-12 prior"
    if prior <= 50:
        return "13-50 prior"
    if prior <= 200:
        return "51-200 prior"
    return "200+ prior"


def accounting(store: dict[str, list[dict]]) -> dict:
    sessions = len(store)
    user_msgs = assistant_turns = 0
    tin = tout = treason = tcache = 0
    tool_parts = 0
    turns_with_rounds = round_sum = 0
    injection_sizes: list[int] = []
    models: Counter = Counter()
    for rows in store.values():
        for row in rows:
            info = row.get("info") or {}
            role = info.get("role")
            if role == "user":
                user_msgs += 1
            elif role == "assistant":
                assistant_turns += 1
            i, o, r, c = _tokens(row)
            tin += i
            tout += o
            treason += r
            tcache += c
            if info.get("modelID"):
                models[info.get("modelID")] += 1
            maxr = 0
            for p in row.get("parts") or []:
                if not isinstance(p, dict) or p.get("type") != "tool":
                    continue
                tool_parts += 1
                st = p.get("state") or {}
                inp = st.get("input") or {}
                if isinstance(inp, dict) and isinstance(inp.get("round"), int):
                    maxr = max(maxr, inp["round"])
                m = _INJ_RE.search(str(st.get("title") or ""))
                if m:
                    injection_sizes.append(int(m.group(1)))
            if role == "assistant" and maxr:
                turns_with_rounds += 1
                round_sum += maxr
    return {
        "sessions": sessions,
        "user_msgs": user_msgs,
        "assistant_turns": assistant_turns,
        "input": tin, "output": tout, "reasoning": treason, "cache_read": tcache,
        "tool_parts": tool_parts,
        "turns_with_rounds": turns_with_rounds, "round_sum": round_sum,
        "injection_sizes": sorted(injection_sizes),
        "models": dict(models),
    }


def _history_replay(store: dict[str, list[dict]]) -> dict:
    naive_full = bounded = turns = 0
    buckets: Counter = Counter()
    for rows in store.values():
        conv = []
        for row in rows:
            role = (row.get("info") or {}).get("role")
            n = len(_row_text(row))
            if n:
                conv.append((role, n))
        for i, (role, _n) in enumerate(conv):
            if role != "user":
                continue
            prior = conv[:i]
            n = len(prior)
            naive = sum(_tok_est(n_) for _r, n_ in prior)
            win = prior[-HISTORY_MAX_MESSAGES:]
            bnd = sum(_tok_est(min(n_, HISTORY_TEXT_CAP)) for _r, n_ in win)
            naive_full += naive
            bounded += bnd
            turns += 1
            buckets[_bucket(n)] += naive
            buckets[_bucket(n) + ":bounded"] += bnd
            buckets[_bucket(n) + ":turns"] += 1
    out: list[dict] = []
    for label in ("1-12 prior", "13-50 prior", "51-200 prior", "200+ prior"):
        if buckets.get(label + ":turns"):
            out.append({"label": label, "turns": buckets[label + ":turns"],
                        "naive": buckets[label], "bounded": buckets[label + ":bounded"]})
    return {"naive_full": naive_full, "bounded": bounded, "turns": turns,
            "buckets": out}


def baseline(store: dict[str, list[dict]]) -> dict:
    h = _history_replay(store)
    actual_input = sum(_tokens(r)[0] for rows in store.values() for r in rows)
    return {
        "naive_full": h["naive_full"],
        "bounded": h["bounded"],
        "actual_input": actual_input,
        "window_saved": h["naive_full"] - h["bounded"],
        "overhead": actual_input - h["bounded"],
        "net_vs_naive": h["naive_full"] - actual_input,
        "turns": h["turns"],
        "buckets": h["buckets"],
    }


def render_accounting(a: dict) -> str:
    turns = a["assistant_turns"] or 1
    avg = a["input"] + a["output"]
    cache_pct = (a["cache_read"] / a["input"] * 100) if a["input"] else 0.0
    inj = a["injection_sizes"]
    inj_line = ""
    if inj:
        inj_line = (f"\n  recall injection: {len(inj)} cards · "
                    f"median ~{inj[len(inj) // 2]} · "
                    f"p99 ~{inj[int(len(inj) * 0.99)]} · "
                    f"max ~{inj[-1]} tokens")
    lines = [
        "seat accounting (serve-msgs store):",
        f"  sessions:        {a['sessions']}",
        f"  user messages:   {a['user_msgs']}",
        f"  assistant turns: {a['assistant_turns']}",
        f"  input tokens:    {a['input']:,}",
        f"  output tokens:   {a['output']:,}",
        f"  reasoning:       {a['reasoning']:,}",
        f"  cache-read:      {a['cache_read']:,} ({cache_pct:.1f}% of input)",
        f"  in+out / turn:   {avg // turns:,}",
        f"  tool-call parts: {a['tool_parts']:,}",
        f"  tool-loop turns: {a['turns_with_rounds']:,} "
        f"(avg {a['round_sum'] / a['turns_with_rounds']:.2f} rounds)"
        if a["turns_with_rounds"] else "  tool-loop turns: 0",
    ]
    lines.append(inj_line)
    if a["models"]:
        lines.append("  models:")
        for m, c in sorted(a["models"].items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"    {m or '?':32s} {c:,}")
    return "\n".join(x for x in lines if x)


def render_background(bg: dict | None, a: dict) -> str:
    if not bg:
        return ("background model calls: not recorded "
                "(no bg-usage.json beside the store)")
    from .bgusage import totals
    t = totals(bg)
    fg = a["input"] + a["output"]
    share = (t["input"] + t["output"]) / ((t["input"] + t["output"] + fg) or 1)
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(bg.get("since") or 0))
    lines = [f"background model calls (bg-usage.json, since {since}):"]
    for lane, models in sorted((bg.get("lanes") or {}).items()):
        for m, r in sorted(models.items(), key=lambda kv: -kv[1].get("input", 0)):
            lines.append(f"    {lane:16s} {m or '?':28s} {r.get('calls', 0):>6,} calls  "
                         f"in {r.get('input', 0):,}  out {r.get('output', 0):,}  "
                         f"reasoning {r.get('reasoning', 0):,}")
    lines.append(f"  total: {t['calls']:,} calls · in {t['input']:,} · "
                 f"out {t['output']:,} · reasoning {t['reasoning']:,}")
    lines.append(f"  share of all in+out tokens: {share * 100:.1f}% "
                 f"(the foreground store counts {fg:,})")
    return "\n".join(lines)


def render_baseline(b: dict) -> str:
    lines = [
        "savings baseline (naive full-history replay vs the seat):",
        f"  full-history replay would send:  {b['naive_full']:,} tokens",
        f"  the bounded window replays:      {b['bounded']:,} tokens",
        f"  window saving:                   {b['window_saved']:,} tokens",
        f"  the seat actually sent (input):  {b['actual_input']:,} tokens",
        f"  recall + tool-loop overhead:     {b['overhead']:,} tokens",
        f"  net vs the naive client:         {b['net_vs_naive']:,} tokens "
        f"({'SAVING' if b['net_vs_naive'] > 0 else 'COST'})",
        f"  (turns measured: {b['turns']}; "
        f"chars/token ~{CHARS_PER_TOKEN} -- the seat's own estimate)",
    ]
    if b["buckets"]:
        lines.append("  by conversation length (prior rows):")
        for bu in b["buckets"]:
            lines.append(
                f"    {bu['label']:14s} {bu['turns']:>6d} turns  "
                f"naive {bu['naive']:,}  window {bu['bounded']:,}  "
                f"save {bu['naive'] - bu['bounded']:,}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="membrane stats",
                                 description="token/call accounting + savings baseline")
    ap.add_argument("--store", default=".membrane/serve-msgs.json",
                    help="path to a serve-msgs.json (read-only; copy is fine)")
    ap.add_argument("--bg", default=None,
                    help="path to a bg-usage.json (default: beside --store)")
    ap.add_argument("--baseline", action="store_true",
                    help="also print the naive-full-history savings baseline")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args(argv)
    store = load_store(args.store)
    a = accounting(store)
    from .bgusage import FILE as _BG, load as _load_bg
    bg = _load_bg(args.bg or Path(args.store).parent / _BG)
    if args.baseline:
        b = baseline(store)
    if args.json:
        out = {"accounting": a, "background": bg}
        if args.baseline:
            out["baseline"] = b
        print(json.dumps(out, indent=2))
        return 0
    print(render_accounting(a))
    print()
    print(render_background(bg, a))
    if args.baseline:
        print()
        print(render_baseline(b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
