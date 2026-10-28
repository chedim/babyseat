"""THIS CONVERSATION: the per-turn banner that carries the chat's own state
(#429), the default per-turn block (MEMBRANE_INJECTION=banner restores the old WHERE WE ARE banner
stays the default until this one is measured).

Everything is derived from records; no LLM runs anywhere:

  NOW         one line, the local date, time and time zone
  WORKING ON  the person's latest earlier ask, within the last WINDOW_MESSAGES
              messages (the one being answered counts as one) (#466)
  ABOUT       focus entities: in >= FOCUS_MIN_EVENTS of the events in that
              window, or named in the person's latest message; top FOCUS_K
  DECIDED     this chat's decisions: the operator ledger, goals fulfilled
              here, coproc decide() records (#454)
  OPEN        open goals: those planted in this chat first, then the board's,
              each labelled with its origin (this chat / another chat / board)
  STABLE      facts seen in >= STABLE_MIN_CONVS chats over >= STABLE_MIN_DAYS
              days, never given another value, about a focus entity
  SINCE YOUR LAST MESSAGE  focus entities moved from outside this chat
  ALERT       at most ALERTS_MAX per turn (the rest ride the next turns)
  PAUSED      only while the pause is on
  BLURP       one status line (omitted while Blurp is off)
  MODEL       the model answering this turn, stamped in after routing
  MORE        the pull pointer, only when the request carries tools

Every record line says where it came from and when, as a relative age. An
empty section is omitted; a block with nothing of this conversation (no
ask, decision, goal, fact, news, alert or pause) is not sent. The block is
sent every turn while it has content: it is small and moves with the chat.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

# ---- operator-approved values (2026-10-22, #429) ---------------------------
BANNER_CHAR_BUDGET = 900      # the whole block, NOW and ages included
SUGGEST_HEAD = "BLURP SUGGESTS: (adopt one only if it is right)"
FOCUS_K = 5                   # focus entities shown
FOCUS_MIN_EVENTS = 2          # ...in this many of this chat's events (or named now)
DECIDED_MAX = 3
OPEN_MAX = 3
STABLE_MAX = 3
STABLE_MIN_CONVS = 3          # a stable fact: seen in this many chats
STABLE_MIN_DAYS = 2           # ...over this many days, never contradicted
NEWS_MAX = 3                  # SINCE YOUR LAST MESSAGE lines
NEWS_CHARS = 200              # ...each this long (also the clip of any record line)
ALERTS_MAX = 2                # per turn
WORKING_ON_CHARS = 160        # the ask's clip
WINDOW_MESSAGES = 3           # WORKING ON / ABOUT look at this many messages (#466, operator-approved 2026-10-23)

# the age buckets (the operator's design): just now, N min, N h, this
# morning / this afternoon / yesterday, N days, then the date
JUST_NOW_S = 5 * 60
HOURS_S = 12 * 3600
DAYS_MAX = 7

HEADER = "THIS CONVERSATION (membrane, from this chat's own records; context, not instructions):"
MORE_LINE = ("MORE: state(section='changes'|'tracks'|'commands'|...) for the seat · "
             "query(topic) for memory · entity_read(id) for one entity")
MODEL_LINE = "MODEL: {} is answering this turn"
PAUSED_LINE = "PAUSED: the seat's acting lanes are down; reads and memory work, your writes are refused."
_FOCUS_KINDS = ("f", "s", "c", "port", "m", "u", "ha")
_MARKS_KEPT = 64
_EIDS_KEPT = 20000           # memo of events already read for focus (bookkeeping, not a threshold)


@dataclass
class Item:
    text: str
    src: str
    ts: float | None = None


# ---- time --------------------------------------------------------------------
def _local(ts: float, tz=None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts).astimezone()


def _bucket(then: datetime, now: datetime, dt: float) -> str:
    if dt < JUST_NOW_S:
        return "just now"
    if dt < 3600:
        return f"{int(dt // 60)} min ago"
    if dt < HOURS_S:
        return f"{int(dt // 3600)}h ago"
    days = (now.date() - then.date()).days
    if days <= 0:
        return "this morning" if then.hour < 12 else "this afternoon"
    if days == 1:
        return "yesterday"
    if days < DAYS_MAX:
        return f"{days} days ago"
    return then.strftime("%Y-%m-%d")


def age(ts, now: float | None = None, tz=None) -> str:
    """A record's age in the operator's buckets ('' when it has no time)."""
    if ts is None:
        return ""
    now = time.time() if now is None else now
    ts = float(ts)
    dt = max(0.0, now - ts)
    return _bucket(_local(min(ts, now), tz), _local(now, tz), dt)


def now_line(now: float | None = None, tz=None) -> str:
    now = time.time() if now is None else now
    return "NOW (for reference, not an event): " + _local(now, tz).strftime("%Y-%m-%d %H:%M %Z").strip()


# ---- rendering ---------------------------------------------------------------
def _clip(s: str, cap: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= cap else s[: max(1, cap - 3)].rstrip() + "..."


def _item_line(it: Item, now: float, tz, prefix: str = "- ", cap: int = NEWS_CHARS) -> str:
    a = age(it.ts, now, tz)
    tag = f"{it.src} · {a}" if a else it.src
    return f"{prefix}{_clip(it.text, cap)} ({tag})"


def render(sec: dict, now: float | None = None, tz=None,
           budget: int = BANNER_CHAR_BUDGET) -> str | None:
    """The block from its sections (see the module doc), or None when it
    holds nothing of this conversation. Lines that do not fit the budget
    are dropped, least important first; the order on the page stays."""
    now = time.time() if now is None else now
    paused = bool(sec.get("paused"))
    alerts = [_clip(a, NEWS_CHARS) for a in (sec.get("alerts") or [])][:ALERTS_MAX]
    suggest = [_clip(s, NEWS_CHARS) for s in (sec.get("suggest") or [])]
    working = sec.get("working")
    about = list(sec.get("about") or [])[:FOCUS_K]
    groups = {"DECIDED:": list(sec.get("decided") or [])[:DECIDED_MAX],
              "OPEN:": list(sec.get("open") or [])[:OPEN_MAX],
              "STABLE:": list(sec.get("stable") or [])[:STABLE_MAX],
              "SINCE YOUR LAST MESSAGE:": list(sec.get("since") or [])[:NEWS_MAX]}
    if not (paused or alerts or working or suggest or any(groups.values())):
        return None
    # (priority, slot, line): a lower priority number is kept first; the
    # slot is the line's place on the page
    cand: list[tuple[int, tuple, str]] = []
    if paused:
        cand.append((0, (1, 0), PAUSED_LINE))
    for i, a in enumerate(alerts):
        cand.append((1, (2, i), a))
    if working is not None:
        cand.append((2, (3, 0), _working_line(working, now, tz)))
    prio = {"DECIDED:": 3, "OPEN:": 4, "SINCE YOUR LAST MESSAGE:": 5, "STABLE:": 7}
    order = {"DECIDED:": 5, "OPEN:": 6, "STABLE:": 7, "SINCE YOUR LAST MESSAGE:": 8}
    for head, items in groups.items():
        for i, it in enumerate(items):
            cand.append((prio[head], (order[head], i + 1), _item_line(it, now, tz)))
    if about:
        cand.append((6, (4, 0), "ABOUT: " + " · ".join(about)))
    if sec.get("blurp"):
        cand.append((6.5, (8.5, 0), _clip(sec["blurp"], NEWS_CHARS)))
    for i, s in enumerate(suggest):                    # #576 H: adopt one if it is right, never to please
        cand.append((0.5, (3.5, i + 1), "- " + s))     # kept before the rest: a dropped one would read as ignored
    if suggest:
        cand.append((0.5, (3.5, 0), SUGGEST_HEAD))
    if sec.get("more"):
        cand.append((8, (9, 0), MORE_LINE))
    heads = {order[h]: h for h in groups}
    kept: dict[tuple, str] = {}
    used = len(now_line(now, tz)) + 1 + len(HEADER)
    for p, slot, line in sorted(cand, key=lambda c: (c[0], c[1])):
        cost = len(line) + 1
        h = heads.get(slot[0])
        if h is not None and (slot[0], 0) not in kept:
            cost += len(h) + 1
        if used + cost > budget:
            continue
        used += cost
        kept[slot] = line
        if h is not None:
            kept[(slot[0], 0)] = h
    out = [now_line(now, tz), HEADER] + [kept[s] for s in sorted(kept)]
    return "\n".join(out)


def _working_line(it: Item, now: float, tz) -> str:
    a = age(it.ts, now, tz)
    tag = f"{it.src} · {a}" if a else it.src
    return f'WORKING ON: "{_clip(it.text, WORKING_ON_CHARS)}" ({tag})'


def blurp_line(st) -> str | None:
    """One status line for Blurp from the seat's self status; None while it is
    off or unknown (an empty section is omitted)."""
    if not isinstance(st, dict):
        return None
    mode = str(st.get("mode") or "").strip()
    if mode in ("", "off"):
        return None
    parts = [mode]
    lvl = st.get("level")
    if isinstance(lvl, int) and lvl > 0:
        s = f"{lvl} achievement{'s' if lvl != 1 else ''} earned"
        if st.get("rung_name"):
            s += f" (latest: {st['rung_name']})"
        parts.append(s)
    if st.get("waiting"):
        parts.append(f"waiting on {st['waiting']}")
    return _clip("BLURP: " + " \u00b7 ".join(parts), NEWS_CHARS)


def add_model_line(block: str | None, model) -> str | None:
    """The block with the MODEL line right after its header (the banner is
    built before the router picks the model, so it is stamped in after)."""
    if not block or not model:
        return block
    lines = block.split("\n")
    if HEADER not in lines:
        return block
    i = lines.index(HEADER) + 1
    if i < len(lines) and lines[i].startswith("MODEL: "):
        lines.pop(i)
    lines.insert(i, MODEL_LINE.format(_clip(str(model), 80)))
    return "\n".join(lines)


def restamp(messages, model):
    """Stamp the model line into the banner inside the injected system
    message(s), in place; messages without the banner are left alone."""
    if not messages or not model:
        return messages
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "system":
            continue
        c = m.get("content")
        if not isinstance(c, str) or HEADER not in c:
            continue
        a = c.index(HEADER)
        end = c.find("\n\n", a)
        end = len(c) if end < 0 else end
        # the banner runs from its NOW line (just above the header) to the blank line
        start = c.rfind("\n", 0, max(a - 1, 0)) + 1 if a > 0 else 0
        m["content"] = c[:start] + add_model_line(c[start:end], model) + c[end:]
    return messages


# ---- gathering (records only) ------------------------------------------------
def conv_events(fld, conv: str) -> list[tuple[str, dict]]:
    """This conversation's live events, oldest first (code never)."""
    if fld is None or not conv:
        return []
    rows = [(k, d) for k, d in fld.b.scan("events", {"origin": fld.origin, "conv": conv})
            if d.get("stream") != "code" and d.get("status") != "forgotten"]
    rows.sort(key=lambda kd: kd[1].get("seq") or 0)
    return rows


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def _is_ask(d: dict, cur: str) -> bool:
    from .speakers import strip_speaker_lines
    from .streams import event_author
    if d.get("stream") != "discourse" or d.get("role") != "user" or event_author(d) != "person":
        return False
    t = _norm(strip_speaker_lines(str(d.get("text") or "")))
    return bool(t) and t != cur and not t.startswith("membrane:")


def window_events(events, current_text: str = "", n: int = WINDOW_MESSAGES):
    """The events of the last `n` person messages: the one being answered
    counts as one, so the n-1 latest earlier asks and everything after the
    first of them. A shorter chat is all in the window."""
    from .speakers import strip_speaker_lines
    evs = list(events or [])
    cur = _norm(strip_speaker_lines(current_text or ""))
    keep = max(0, n - 1)
    if keep == 0:
        return []
    seen = 0
    for i in range(len(evs) - 1, -1, -1):
        if _is_ask(evs[i][1], cur):
            seen += 1
            if seen == keep:
                return evs[i:]
    return evs


def working_on(events, current_text: str = "", focus_ids=None, fld=None) -> Item | None:
    """The person's latest earlier ask: a person-authored user event that is
    not the message being answered now and not a membrane frame. With focus
    entities, the latest such ask that names one of them (an aside is not
    the work); none names one: the latest ask."""
    from .speakers import strip_speaker_lines
    from .streams import event_author
    events = window_events(events, current_text)
    cur = _norm(strip_speaker_lines(current_text or ""))
    first = None
    for _k, d in reversed(events or []):
        if d.get("stream") != "discourse" or d.get("role") != "user":
            continue
        if event_author(d) != "person":
            continue
        raw = strip_speaker_lines(str(d.get("text") or ""))     # who spoke is not the work
        t = _norm(raw)
        if not t or t == cur or t.startswith("membrane:"):
            continue
        it = Item(" ".join(raw.split()), "person", d.get("ts"))
        if not focus_ids:
            return it
        if first is None:
            first = it
        if _event_eids(fld, d) & set(focus_ids):
            return it
    return first


def _focus_ok(eid: str, named: bool = False) -> bool:
    """A focus candidate: a specific identifier, never the seat's own tool
    names. An unresolved identifier ('s:?', a name no code defines, often
    the model's own prose) counts only when the person names it now."""
    from . import entity_block as EB
    kind = eid.split(":", 1)[0]
    if kind not in _FOCUS_KINDS:
        return False
    if eid.startswith("s:?") and not named:
        return False
    if eid.startswith("s:?"):                  # the seat's own tool and method names
        name = eid[3:]
        tools = EB._tool_names()
        if name in tools or name.split(".")[-1] in tools:
            return False
    return True


def _event_eids(fld, d: dict) -> set:
    from . import eids as E
    out = set()
    graph = fld.graph_fn() if fld is not None else None
    if d.get("stream") == "observation":
        try:
            pairs = fld._obs_mentions(d, graph)
        except Exception:
            pairs = []
    else:
        pairs = E.mentions_of(d.get("text") or "", graph, getattr(fld, "phrases", None))
    for eid, _s in pairs:
        if _focus_ok(eid):
            out.add(eid)
    return out


def focus(fld, events, latest_text: str = "", k: int = FOCUS_K) -> list[str]:
    """The entities this conversation is about: named in the person's latest
    message (first, in its order), then those in >= FOCUS_MIN_EVENTS of its
    events (most events first, then the most recent)."""
    events = window_events(events, latest_text)
    named: list[str] = []
    if latest_text and fld is not None:
        from .entity_block import named_entities
        try:
            for eid, _s in named_entities(fld, latest_text):
                if _focus_ok(eid, named=True) and eid not in named:
                    named.append(eid)
        except Exception:
            pass
    counts: dict[str, int] = {}
    last: dict[str, int] = {}
    memo = fld.__dict__.setdefault("_conv_eids", {}) if fld is not None else {}
    for key, d in events or []:
        got = memo.get(key)
        if got is None:                         # an event's entities never change: read once
            got = memo[key] = _event_eids(fld, d)
            if len(memo) > _EIDS_KEPT:
                memo.pop(next(iter(memo)))
        for eid in got:
            counts[eid] = counts.get(eid, 0) + 1
            last[eid] = int(d.get("seq") or 0)
    rest = sorted((e for e, n in counts.items() if n >= FOCUS_MIN_EVENTS and e not in named),
                  key=lambda e: (-counts[e], -last[e], e))
    return (named + rest)[:k]


def _iso_ts(s: str) -> float | None:
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _coproc_store(srv):
    from . import coproc as CP
    st = getattr(srv, "coproc_store", None)
    if isinstance(st, CP.Store):
        return st
    led = getattr(srv, "user_ledger", None)
    lp = str(getattr(led, "path", "") or "")
    if not lp.endswith(".ledger.jsonl"):
        return None
    import os
    if not os.path.exists(lp[: -len(".ledger.jsonl")] + ".coproc.jsonl"):
        return None
    try:
        return CP.store_for(srv)
    except Exception:
        return None


def _ledger_entries(srv) -> list[dict]:
    led = getattr(srv, "user_ledger", None)
    fn = getattr(led, "entries", None)
    if not callable(fn):
        return []
    try:
        out = fn()
        return out if isinstance(out, list) else []
    except Exception:
        return []


def _goals(srv) -> list:
    est = getattr(srv, "entity_store", None)
    docs = getattr(est, "docs", None)
    if not isinstance(docs, dict):
        return []
    from .entities import CLASS_GOAL
    return [e for e in list(docs.values()) if getattr(e, "cls", None) == CLASS_GOAL]


def _board_intents(srv) -> list:
    """The json intent board's entries (a seat without the goal layer plants
    there; the goal layer's own entries are the Goal entities)."""
    from .intent import IntentBoard, Intention
    b = getattr(srv, "_intent_board", None)
    if not isinstance(b, IntentBoard):
        return []
    return [i for i in list(b.intents.values()) if isinstance(i, Intention)]


def decided(srv, conv: str) -> list[Item]:
    """This chat's decisions, newest first: the operator ledger's entries
    said here, goals fulfilled here, coproc decide() records made here."""
    if not conv:
        return []
    out: list[Item] = []
    for e in _ledger_entries(srv):
        if e.get("conv") == conv:
            out.append(Item(e.get("text") or "", "person", _iso_ts(e.get("ts"))))
    st = _coproc_store(srv)
    if st is not None:
        for r in st.decisions():
            if r.get("conv") == conv:
                who = str(r.get("who") or "person")
                out.append(Item(str((r.get("args") or {}).get("text") or ""),
                                "person" if who.startswith("person") else who, r.get("ts")))
    for i in _board_intents(srv):
        if i.status == "fulfilled" and i.fulfilled_conv == conv:
            out.append(Item("goal fulfilled: " + i.text, "goal", i.fulfilled or None))
    for g in _goals(srv):
        p = g.properties or {}
        if p.get("status") == "fulfilled" and p.get("fulfilled_conv") == conv:
            out.append(Item("goal fulfilled: " + str(p.get("name") or g.eid), "goal",
                            p.get("fulfilled_ts")))
    out = [i for i in out if i.text.strip()]
    out.sort(key=lambda i: -(float(i.ts) if i.ts is not None else 0.0))
    return out[:DECIDED_MAX]


def _picked_up(srv, eid: str, p: dict) -> str:
    """Who has the goal: the loop on it now (the pool's live hold, so a pass
    its seat lost never reads as running), else who worked it last."""
    def hm(ts) -> str:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    start, end = float(p.get("picked_up_ts") or 0), float(p.get("picked_up_end_ts") or 0)
    holds = getattr(getattr(srv, "loops", None), "holds", None)
    if end < start and callable(holds) and holds(eid):
        return f"being worked by {p['picked_up_by']} since {hm(start)}"
    return f"last worked by {p['picked_up_by']} at {hm(max(start, end))}"


def open_items(srv, conv: str) -> list[Item]:
    """Open goals, labelled with where they came from: those planted in this
    chat first, then another chat's and the board's, newest first (#466)."""
    if not conv:
        return []
    rows: list[tuple[int, Item]] = []
    now = time.time()
    try:
        from .watch_agents import quiet_goals
        quiet = {q["eid"]: q["why"] for q in quiet_goals(srv, now=now)}
    except Exception:
        quiet = {}

    def origin(planted) -> tuple[int, str]:
        planted = [c for c in ([planted] if isinstance(planted, str) else (planted or [])) if c]
        if conv and conv in planted:
            return 0, "goal \u00b7 this chat"
        return (1, "goal \u00b7 another chat") if planted else (2, "goal \u00b7 board")

    for i in _board_intents(srv):
        if i.status == "open" and not (i.expires and now >= i.expires):
            r, src = origin(i.conv)
            rows.append((r, Item(i.text, src, i.created or None)))
    for g in _goals(srv):
        p = g.properties or {}
        if (p.get("status") or "open") == "open":
            r, src = origin(p.get("convs"))
            if p.get("picked_up_by") and p.get("picked_up_ts"):
                src += " \u00b7 " + _picked_up(srv, g.eid, p)
            if g.eid in quiet:
                src += " \u00b7 " + quiet[g.eid]
            rows.append((r, Item(str(p.get("name") or g.eid), src, p.get("born"))))
    rows.sort(key=lambda ri: (ri[0], -(float(ri[1].ts) if ri[1].ts is not None else 0.0)))
    return [it for _r, it in rows][:OPEN_MAX]


def _about(text: str, focus_ids, fld) -> bool:
    from . import eids as E
    low = (text or "").lower()
    graph = fld.graph_fn() if fld is not None else None
    ment = {e for e, _s in E.mentions_of(text or "", graph)}
    for f in focus_ids or []:
        if f in ment:
            return True
        surf = f.split(":", 1)[-1].lower()
        if len(surf) >= 3 and surf in low:
            return True
    return False


def stable(srv, focus_ids, fld=None, tz=None) -> list[Item]:
    """Facts said in >= STABLE_MIN_CONVS chats over >= STABLE_MIN_DAYS days
    and never given another value, about a focus entity. Sources: coproc
    remember() records (a key's value) and the operator ledger (its verbatim
    entries; a ledger line has no key, so nothing can contradict it)."""
    if not focus_ids:
        return []
    groups: dict[str, dict] = {}
    st = _coproc_store(srv)
    if st is not None:
        from .coproc import fact_key
        for r in st._actions():
            if r.get("word") != "remember" or r.get("status") != "applied":
                continue
            a = r.get("args") or {}
            g = groups.setdefault("k|" + fact_key(a.get("key", "")), {
                "text": f"{a.get('key')}: {a.get('value')}", "values": set(), "seen": []})
            g["values"].add(_norm(a.get("value")))
            g["seen"].append((r.get("conv") or "", r.get("ts")))
    for e in _ledger_entries(srv):
        g = groups.setdefault("l|" + _norm(e.get("text")), {
            "text": e.get("text") or "", "values": {""}, "seen": []})
        g["seen"].append((e.get("conv") or "", _iso_ts(e.get("ts"))))
    out = []
    for g in groups.values():
        if len(g["values"]) != 1:
            continue                                # given another value: contradicted
        convs = {c for c, _t in g["seen"] if c}
        stamps = [float(t) for _c, t in g["seen"] if t is not None]
        days = {_local(t, tz).date() for t in stamps}
        if len(convs) < STABLE_MIN_CONVS or len(days) < STABLE_MIN_DAYS:
            continue
        if not _about(g["text"], focus_ids, fld):
            continue
        out.append(Item(g["text"], f"fact · {len(convs)} chats", max(stamps) if stamps else None))
    out.sort(key=lambda i: -(float(i.ts) if i.ts is not None else 0.0))
    return out[:STABLE_MAX]


def since(fld, conv: str, mark, focus_ids) -> list[Item]:
    """What moved a focus entity after this chat's last delivered turn, from
    outside this chat (code, scripts, other chats): the newest line each."""
    if fld is None or not mark or not focus_ids:
        return []
    ch = fld.changes_since(int(mark.get("seq") or 0))
    want = [e for e in focus_ids if e in ch]
    if not want:
        return []
    evs = fld.b.get_multi("events", list({d["event"] for e in want for d in ch[e]}))
    out = []
    for eid in want:
        ext = []
        for d in ch[eid]:
            ev = evs.get(d["event"]) or {}
            if ev.get("stream") == "code" or not conv or ev.get("conv") != conv:
                ext.append((d, ev))
        if not ext:
            continue
        d, ev = ext[-1]
        sent = (fld.b.get("sentences", d.get("sid")) or {}).get("text") or ev.get("text") or ""
        if ev.get("stream") == "code":
            src = "code"
        elif str(ev.get("src") or "").startswith("script"):
            src = str(ev.get("src"))
        elif ev.get("conv"):
            src = "another chat"
        else:
            src = "outside this chat"
        out.append(Item(f"{eid}: {' '.join(str(sent).split())}", src, ev.get("ts") or d.get("ts")))
        if len(out) >= NEWS_MAX:
            break
    return out


def take_alerts(srv, on_new=None) -> list[str]:
    """At most ALERTS_MAX alert lines this turn; the rest wait for the next
    turns (nothing is dropped). `on_new(line)` hears each fresh one once."""
    held = list(getattr(srv, "_conv_alerts", None) or [])
    est = getattr(srv, "entity_store", None)
    if est is not None and getattr(est, "tracks", None) and callable(getattr(est, "take_alerts", None)):
        try:
            for a in est.take_alerts():
                ln = f"ALERT: {a.get('label')} {a.get('kind')} its boundary ({a.get('boundary')})"
                held.append(ln)
                if on_new is not None:
                    try:
                        on_new(ln)
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        srv._conv_alerts = held[ALERTS_MAX:]
    except Exception:
        pass
    return held[:ALERTS_MAX]


def note_delivered(srv, conv: str, mark) -> None:
    """This chat's turn reached the model: SINCE YOUR LAST MESSAGE counts
    from here next time."""
    if not conv or not mark:
        return
    marks = getattr(srv, "_conv_marks", None)
    if not isinstance(marks, dict):
        marks = {}
        try:
            srv._conv_marks = marks
        except Exception:
            return
    marks.pop(conv, None)
    marks[conv] = dict(mark)
    while len(marks) > _MARKS_KEPT:
        marks.pop(next(iter(marks)))


def _suggestions(srv, conv: str) -> list[str]:
    """#576 H: Blurp's proposals on accepted branches, for this chat's next turn."""
    try:
        from . import ch_blurp as _CHB
        return _CHB.take_suggestions(srv, conv)
    except Exception:
        return []


def _blurp(srv) -> str | None:
    try:
        from .seat_panel import _self_status
        return blurp_line(_self_status(srv))
    except Exception:
        return None


def build(srv, body: dict, conv: str, now: float | None = None, tz=None,
          on_alert=None) -> str | None:
    """The block for this turn (None when it holds nothing of this chat)."""
    from . import lanes as _L
    now = time.time() if now is None else now
    fld = _L._efield_on(srv)
    latest = _L._last_user_text(body or {})
    events = conv_events(fld, conv)
    about = focus(fld, events, latest) if fld is not None else []
    marks = getattr(srv, "_conv_marks", None)
    mark = marks.get(conv) if isinstance(marks, dict) and conv else None
    sec = {
        "paused": _L.nerve_paused(srv),
        "alerts": take_alerts(srv, on_alert),
        "working": working_on(events, latest, about, fld),
        "about": about,
        "decided": decided(srv, conv),
        "open": open_items(srv, conv),
        "stable": stable(srv, about, fld, tz),
        "since": since(fld, conv, mark, about),
        "blurp": _blurp(srv),
        "suggest": _suggestions(srv, conv),
        "more": bool((body or {}).get("tools")),
    }
    return render(sec, now, tz)
