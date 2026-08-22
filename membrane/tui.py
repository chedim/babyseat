"""Live ncurses TUI for a running membrane proxy.

A separate process that watches the field + audit files the proxy writes and
renders, live:

  - METRICS   -- the "is it working" readout (reuse review.compute_metrics): absorb
                 rate, hit/miss, re-ask rate, wrong-absorb (safety), value, tokens
                 saved, judge precision.
  - MEMORY    -- field state: size, safe/unsafe, effective rank (variety) + rank
                 ratio, spread, total re-asks/contests, and whether the null-space
                 novelty veto is ON or OFF (the gate's rank-ratio toggle, live).
  - FIELD     -- per-exemplar visualizer: idx, safe (green check / red cross),
                 reask:contest votes, action length, situation preview. Scrollable.
  - LOG       -- tail of the audit (decisions / re-ask outcomes / contests), colored
                 by event and verdict.

It only READS the files (the proxy owns them); start the proxy, then in another
terminal:

  PYTHONPATH=. .venv/bin/python -m membrane.tui \\
      --field membrane-field.json --audit membrane-audit.jsonl

Keys:  q quit · ↑/↓ (or k/j) scroll the field · g/G top/bottom · r force refresh.
No extra dependencies -- stdlib curses + the existing membrane modules.
"""

from __future__ import annotations

import argparse
import curses
import json
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .field import ExemplarStore
from .review import _load, compute_metrics, _fmt_metrics
from .variety import effective_rank, spread, spectrum_entropy, free_energy

# mirror gate.decide: the null-space novelty veto fires only below this rank ratio.
NOVELTY_RANK_RATIO_MAX = 0.5

# color pair ids
(C_HEADER, C_BORDER, C_SAFE, C_UNSAFE, C_ACT, C_HINT, C_ESC,
 C_REASK, C_CONTEST, C_VALUE, C_DIM, C_WARN) = range(12)


def _init_colors() -> None:
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    for cid, fg in [
        (C_HEADER, curses.COLOR_WHITE), (C_BORDER, curses.COLOR_BLUE),
        (C_SAFE, curses.COLOR_GREEN), (C_UNSAFE, curses.COLOR_RED),
        (C_ACT, curses.COLOR_GREEN), (C_HINT, curses.COLOR_CYAN),
        (C_ESC, curses.COLOR_YELLOW), (C_REASK, curses.COLOR_RED),
        (C_CONTEST, curses.COLOR_MAGENTA), (C_VALUE, curses.COLOR_YELLOW),
        (C_DIM, curses.COLOR_WHITE), (C_WARN, curses.COLOR_RED),
    ]:
        curses.init_pair(cid, fg, bg)


# ---- low-level safe write helpers -------------------------------------------
def _wput(win, y, x, s, attr=0, color=0) -> int:
    """Write `s` at (y,x) truncated to the window; return x + len(s). Never raises."""
    try:
        h, w = win.getmaxyx()
    except curses.error:
        return x + len(s)
    if y < 0 or y >= h or x < 0 or x >= w:
        return x + len(s)
    s = s.replace("\n", " ")
    if x + len(s) >= w:
        s = s[: max(0, w - x - 1)]
    if not s:
        return x
    try:
        win.addstr(y, x, s, attr | curses.color_pair(color))
    except curses.error:
        pass
    return x + len(s)


def _wputr(win, y, x, s, attr=0, color=0) -> int:
    """Write and return the advanced x cursor (for segmented rows)."""
    return _wput(win, y, x, s, attr, color)


def _f(x) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else " -- "


def _panel(stdscr, y, x, h, w, title="") -> "curses.window | None":
    if h < 3 or w < 6:
        return None
    win = stdscr.derwin(h, w, y, x)
    win.erase()
    try:
        win.attron(curses.color_pair(C_BORDER))
        win.border()
        win.attroff(curses.color_pair(C_BORDER))
    except curses.error:
        pass
    if title:
        _wput(win, 0, 2, f" {title} ", color=C_BORDER, attr=curses.A_BOLD)
    return win


# ---- the watched state ------------------------------------------------------
class State:
    def __init__(self, audit_path: str, field_path: str):
        self.audit_path = Path(audit_path)
        self.field_path = Path(field_path)
        self.log: deque[str] = deque(maxlen=500)
        self.offset = 0           # byte offset into the audit (tail position)
        self.scroll = 0           # field-table scroll
        self.last_n = -1          # effective_rank is cached by field size (vecs are immutable)
        self.eff_rank = 0.0
        self.spread = 0.0
        self.spec_entropy = 0.0    # Shannon entropy of the variance spectrum (bits)
        self.free_energy = 0.0     # safe-weighted variety minus disorder tax (the VALUE number)
        self.last_error = ""
        self._backfill()

    def _backfill(self) -> None:
        """Start at the audit's end, but seed the log with the last ~200 lines."""
        self.log.clear()
        if not self.audit_path.exists():
            self.offset = 0
            return
        try:
            with self.audit_path.open("rb") as f:
                f.seek(0, 2)
                self.offset = f.tell()
                f.seek(max(0, self.offset - 65536))
                chunk = f.read().decode("utf-8", "replace")
        except OSError as e:
            self.last_error = f"audit backfill: {e}"
            return
        for ln in chunk.splitlines():
            ln = ln.strip()
            if ln:
                self.log.append(ln)

    def tail(self) -> None:
        """Append any new audit lines since our last offset."""
        if not self.audit_path.exists():
            return
        try:
            with self.audit_path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size < self.offset:           # rotated / truncated -> restart
                    self.offset = 0
                if size > self.offset:
                    f.seek(self.offset)
                    chunk = f.read().decode("utf-8", "replace")
                    for ln in chunk.splitlines():
                        ln = ln.strip()
                        if ln:
                            self.log.append(ln)
                    self.offset = size
        except OSError as e:
            self.last_error = f"audit tail: {e}"

    def tick(self) -> dict | None:
        """Reload field + audit tail, recompute metrics + variety. Returns the render bundle."""
        self.tail()
        try:
            store = ExemplarStore.load(self.field_path)
        except Exception as e:                     # field may be mid-write; keep last good frame
            self.last_error = f"field load: {e}"
            return None
        field_dicts = [asdict(e) for e in store.exemplars]

        try:
            _, decisions, outcomes, contests, _ = _load(self.audit_path, self.field_path)
        except Exception as e:
            self.last_error = f"audit load: {e}"
            decisions, outcomes, contests = [], [], []

        n = len(store)
        if n != self.last_n:                        # vecs are immutable -> variety only changes with N
            try:
                self.eff_rank = effective_rank(store) if n >= 2 else 0.0
                self.spread = spread(store) if n >= 2 else 0.0
                self.spec_entropy = spectrum_entropy(store) if n >= 2 else 0.0
                self.free_energy = free_energy(store) if n >= 2 else 0.0
            except Exception as e:
                self.last_error = f"variety: {e}"
            self.last_n = n

        metrics = compute_metrics(decisions, outcomes, contests, field_dicts)
        return {"store": store, "metrics": metrics,
                "decisions": decisions, "outcomes": outcomes, "contests": contests}


# ---- log formatting ---------------------------------------------------------
def _fmt_log(ln: str) -> tuple[str, int]:
    try:
        rec = json.loads(ln)
    except json.JSONDecodeError:
        return ln[:90], C_DIM
    ev = rec.get("event", "decision")
    if ev == "outcome":
        ex = rec.get("ex_idx")
        safe = rec.get("safe", True)
        return (f"re-ask #{ex} reask={rec.get('reask_count')} "
                f"{'DEMOTE' if not safe else 'vote'} sim={_f(rec.get('sim'))} "
                f"q={str(rec.get('query'))[:28]!r}", C_REASK)
    if ev == "contest":
        ex = rec.get("ex_idx")
        src = rec.get("source", "?")
        if rec.get("safe"):
            eff = "RESTORE"
        elif rec.get("restore") is False:
            eff = "record-only"
        else:
            eff = "filed"
        return (f"contest #{ex} [{src}] {eff} reask={rec.get('reask_count')} "
                f"contest={rec.get('contest_count')}", C_CONTEST)
    v = rec.get("verdict", "?")
    col = {"act": C_ACT, "escalate-with-hint": C_HINT, "escalate": C_ESC}.get(v, C_DIM)
    learned = "" if v == "act" else (" +learn" if rec.get("learned") else "")
    return (f"{v:<18} sim={_f(rec.get('sim'))} field={rec.get('field_size')} "
            f"q={str(rec.get('query'))[:26]!r}{learned}", col)


# ---- rendering --------------------------------------------------------------
def _draw_metrics(win, m) -> None:
    h, w = win.getmaxyx()
    for i, line in enumerate(_fmt_metrics(m)):
        y = 1 + i
        if y >= h - 1:
            break
        col, attr = 0, 0
        if line.startswith("value"):
            col, attr = C_VALUE, curses.A_BOLD
        elif line.startswith("wrong-absorb"):
            col = C_UNSAFE if m.get("wrong_absorb_rate", 0) > 0 else 0
        elif line.startswith("hit / miss") and m.get("miss", 0):
            col = C_REASK
        _wput(win, y, 1, line[: w - 2], attr=attr, color=col)


def _draw_memory(win, store, st: State) -> None:
    h, w = win.getmaxyx()
    exs = store.exemplars
    n = len(exs)
    safe = sum(1 for e in exs if e.safe)
    unsafe = n - safe
    reasks = sum(e.reask_count for e in exs)
    contests = sum(e.contest_count for e in exs)
    chars = sum(len(e.action) for e in exs)
    ratio = (st.eff_rank / n) if n else 0.0
    nov_on = (n >= 3 and ratio < NOVELTY_RANK_RATIO_MAX)
    rows = [
        (f"field:      {n} exemplars", C_DIM, 0),
        (f"safe: {safe}   unsafe: {unsafe}", C_SAFE if not unsafe else C_WARN, 0),
        (f"variety:    eff_rank {st.eff_rank:.2f} / {n}  (ratio {ratio:.2f})", 0, 0),
        (f"entropy:    {st.spec_entropy:.3f} nats  (log eff_rank; the field's entropy)", C_DIM, 0),
        (f"free energy:{st.free_energy:.3f}  (safe-weighted variety - disorder tax)", C_HINT, 0),
        (f"novelty:    {'ON  (low-rank -> null-space veto fires)' if nov_on else 'OFF (high-rank -> cosine only)'}",
         C_HINT if nov_on else C_DIM, 0),
        (f"spread:     {st.spread:.3f}  (mean pairwise cos distance)", C_DIM, 0),
        (f"re-asks:    {reasks} total   contests: {contests} total", C_DIM, 0),
        (f"actions:    ~{chars:,} chars stored", C_DIM, 0),
    ]
    for i, (s, col, attr) in enumerate(rows):
        y = 1 + i
        if y >= h - 1:
            break
        _wput(win, y, 1, s[: w - 2], color=col, attr=attr)
    if st.last_error:
        _wput(win, h - 2, 1, f"! {st.last_error[: w - 4]}", color=C_WARN)


def _draw_field(win, store, st: State) -> None:
    h, w = win.getmaxyx()
    exs = store.exemplars
    usable = h - 2                       # rows inside the border
    max_scroll = max(0, len(exs) - (usable - 1))
    st.scroll = max(0, min(st.scroll, max_scroll))
    # header
    _wput(win, 1, 1, "#    S R:C   act  situation", color=C_DIM, attr=curses.A_BOLD)
    for i in range(usable - 1):
        idx = st.scroll + i
        if idx >= len(exs):
            break
        e = exs[idx]
        y = 2 + i
        x = 1
        x = _wputr(win, y, x, f"{idx:<4}", color=C_DIM)
        x = _wputr(win, y, x, "✓ " if e.safe else "✗ ",
                  color=C_SAFE if e.safe else C_UNSAFE, attr=curses.A_BOLD)
        net = e.reask_count - e.contest_count
        vcol = C_REASK if net > 0 else (C_CONTEST if net < 0 else C_DIM)
        x = _wputr(win, y, x, f"{e.reask_count}:{e.contest_count:<3}", color=vcol)
        x = _wputr(win, y, x, f"{len(e.action):<5}", color=C_DIM)
        rest = w - x - 1
        _wput(win, y, x, e.situation[:rest], color=0)
    # scroll indicator
    if len(exs) > usable - 1:
        _wput(win, h - 1, 2, f"[{st.scroll}:{st.scroll + min(usable - 1, len(exs))}/{len(exs)}]",
              color=C_DIM)
    elif not exs:
        _wput(win, h - 2, 2, "(empty field -- the proxy escalates everything; it self-builds)",
              color=C_DIM)


def _draw_log(win, st: State) -> None:
    h, w = win.getmaxyx()
    usable = h - 2
    lines = list(st.log)
    start = max(0, len(lines) - usable)
    for i, ln in enumerate(lines[start:]):
        text, col = _fmt_log(ln)
        _wput(win, 1 + i, 1, text[: w - 2], color=col)
    if not lines:
        _wput(win, 1, 2, "(waiting for audit events -- drive the proxy through a client)", color=C_DIM)


def _render(stdscr, st: State, bundle: dict | None, field_path: str, audit_path: str) -> None:
    stdscr.erase()
    H, W = stdscr.getmaxyx()

    # header
    n = len(bundle["store"]) if bundle else 0
    head = (f" membrane tui  field={Path(field_path).name}  audit={Path(audit_path).name}  "
            f"N={n}  (q quit · ↑/↓ scroll · r refresh) ")
    _wput(stdscr, 0, 0, head[:W], attr=curses.A_BOLD, color=C_HEADER)

    header_h = 1
    band_h = 9
    log_h = min(12, max(5, H // 4))
    field_h = H - header_h - band_h - log_h
    if field_h < 4:                       # small terminal: shrink the band before the field
        band_h = max(6, H - header_h - 4 - log_h)
        field_h = H - header_h - band_h - log_h

    band_w = W // 2
    # metrics (left) + memory (right)
    mw = _panel(stdscr, header_h, 0, band_h, band_w, "metrics -- is it working?")
    sw = _panel(stdscr, header_h, band_w, band_h, W - band_w, "memory / field state")
    # field visualizer (full width, middle)
    fw = _panel(stdscr, header_h + band_h, 0, field_h, W, "field (exemplars)")
    # log (bottom)
    lw = _panel(stdscr, header_h + band_h + field_h, 0, log_h, W, "audit log (tail)")

    if bundle:
        if mw:
            _draw_metrics(mw, bundle["metrics"])
        if sw:
            _draw_memory(sw, bundle["store"], st)
        if fw:
            _draw_field(fw, bundle["store"], st)
    else:
        msg = "(no field yet -- start the proxy: python -m membrane.proxy --listen 11435)"
        if fw:
            _wput(fw, 1, 2, msg, color=C_WARN)
        if sw:
            _wput(sw, 1, 2, "field: waiting…", color=C_WARN)
    if lw:
        _draw_log(lw, st)

    stdscr.refresh()


def _run(stdscr, args) -> None:
    _init_colors()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.timeout(args.refresh)
    st = State(args.audit, args.field)
    running = True
    while running:
        ch = stdscr.getch()
        if ch != -1:
            if ch in (ord("q"), 27):                 # q or Esc
                running = False
                continue
            elif ch in (curses.KEY_UP, ord("k")):
                st.scroll = max(0, st.scroll - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                st.scroll += 1
            elif ch in (ord("g"),):
                st.scroll = 0
            elif ch in (ord("G"),):
                st.scroll = 10 ** 9                  # clamped in _draw_field
            elif ch in (curses.KEY_PPAGE,):
                st.scroll = max(0, st.scroll - 10)
            elif ch in (curses.KEY_NPAGE,):
                st.scroll += 10
            elif ch in (ord("r"),):
                st.last_n = -1                        # force variety recompute
                st.last_error = ""
        bundle = st.tick()
        _render(stdscr, st, bundle, args.field, args.audit)


def main() -> None:
    ap = argparse.ArgumentParser(description="Live ncurses TUI for a running membrane proxy.")
    ap.add_argument("--audit", default="membrane-audit.jsonl", help="path to the audit log")
    ap.add_argument("--field", default="membrane-field.json", help="path to the exemplar field")
    ap.add_argument("--refresh", type=int, default=500,
                    help="refresh interval in ms (default 500)")
    args = ap.parse_args()
    try:
        curses.wrapper(_run, args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()