"""THE TWO STREAMS (the entity field's intake grammar, pure -- no I/O, no
embedding): a turn is split into what people SAY and what the project
SHOWS, each read the way it is written.

  discourse    the user's message, the model's prose, its reasoning, notes
               -- read as LANGUAGE (sentences -> word/phrase entities)
  observation  one event per tool use: tool, parsed args, result head and
               tail, an outcome class -- read as STRUCTURE (files,
               commands, ports, symbols; the fact is the outcome)
  code         the codebase itself as a standing observation (code_evidence)
  distilled    the old compactor's notes -- a claim source only, never
               applied to entity states (E17 found them wrong at times)

Validated in experiments/e24_two_streams.py: after the split, discourse
carries zero tool syntax, and 77% of the specific entities named in
discourse also appear in observations. The audit-record parser handles
the six capture shapes the audit log holds (split_audit_record)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .rag import strip_machinery_text
from .render import spans as _spans
from .slash import STOP

# ---- the capture shapes -------------------------------------------------------
_TOOL = re.compile(r"^(?:compact note:\s*)?tool (?!call\b)(\w+) for:\s*(.*?)(?:\nargs:\s*(\{.*))?$", re.S)
_CALL = re.compile(r"^(?:compact note:\s*)?tool call for:\s*(.*?)\s*->\s*(\w+)\(args\)\s*$", re.S)
_PREFIX = re.compile(r"^(plan|reasoning):\s*", re.I)
_UNCLOSED = re.compile(r"<(system-reminder|system-warning)>.*$", re.S | re.I)
_ARROW = re.compile(r"\s*->\s*\w+\(args\)")
_ARGS_ANY = re.compile(r"\s*\bargs:\s*\{[^\n]*")
_INLINE_ANY = re.compile(r"\b\w+\(\{[^\n]*")
_INLINE = re.compile(r"\b(\w+)\((\{.*)$", re.S)
_PORT = re.compile(r"(?<![\w.\"]):(\d{4,5})\b|(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{4,5})\b")
# a leading NAME=value word is an assignment, never a program (#148):
# skip it, the head is the command after it (or none at all)
_SHELL_HEADS = re.compile(r"(?:^|;|&&|\|\||\||\$\(|\n)\s*"
                          r"(?:[A-Za-z_]\w*=[^\s;&|()]*\s+)*"
                          r"([a-z][\w./-]*)(?![\w./-]|=)")
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\s*\1\b", re.S)
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_WORD = re.compile(r"[A-Za-z][A-Za-z_]+")
_NOT_HEADS = frozenset(("cd", "echo", "then", "do", "done", "fi", "if", "for",
                        "true", "false", "else", "while", "export", "set"))

RESULT_HEAD = 6000
RESULT_TAIL = 2000
MAX_SENTENCES = 40

# ---- who wrote a user turn (#429) ------------------------------------------------
# Job receipts, task notifications and watch blocks reach the field as user
# turns. They are written by membrane or the client, never by a person, so
# they never count as an earlier ask. The five prefixes are the shapes that
# topped the memory probe's recall (operator-approved, 2026-10-21); a stored
# record without an `author` tag is classified by the same prefixes on read.
RECEIPT_PREFIXES = {
    "[membrane] job #": "receipt",
    "your job #": "receipt",
    "<task-notification>": "notification",
    "[membrane-watch]": "sensor",
    "[membrane-intercept]": "receipt",
}
# On a question-like turn, observation evidence (tool results, readings,
# receipts) ranks this much below what was said and decided (approved).
OBSERVATION_PENALTY = 0.10
# A turn is question-like when it ends with '?' or opens with one of these.
QUESTION_WORDS = frozenset((
    "what", "which", "when", "who", "whom", "whose", "how", "why", "where",
    "did", "does", "do", "is", "are", "was", "were", "can", "could",
    "should", "would", "will"))
_FIRST_WORD = re.compile(r"[a-z]+")


def author_of(text: str) -> str:
    """'receipt' | 'notification' | 'sensor' by RECEIPT_PREFIXES, else 'person'."""
    t = (text or "").lstrip()
    for p, who in RECEIPT_PREFIXES.items():
        if t.startswith(p):
            return who
    return "person"


def event_author(ev: dict) -> str:
    """A stored user event's author: its capture tag, or (an older record)
    its text read by the same prefixes."""
    return ev.get("author") or author_of(ev.get("text") or "")


def is_question_like(text: str) -> bool:
    t = " ".join((text or "").split())
    if not t:
        return False
    if t.endswith("?"):
        return True
    m = _FIRST_WORD.match(t.lower())
    return bool(m) and m.group(0) in QUESTION_WORDS


@dataclass
class Event:
    """One unit of the stream (the events collection's row, pre-embedding)."""
    stream: str                      # discourse | observation | code | distilled
    role: str = ""                   # user | model | reasoning | note | tool | code
    text: str = ""                   # discourse / distilled / code text
    tool: str = ""
    args: dict = field(default_factory=dict)
    result_head: str = ""
    result_tail: str = ""
    result_len: int = 0
    outcome: str = ""
    call_id: str = ""
    served_text: str = ""            # the user message a tool call served
    ts: float | None = None
    src: str = "live"
    clipped: bool = False
    code: dict | None = None         # {file, qual, mod, line, kind, sig, doc}
    author: str = ""                 # a user event's writer (#429): person |
                                     # receipt | notification | sensor
    subject: str = ""                # the entity this event is ABOUT (the
                                     # script lanes name their subject
                                     # explicitly; prose mentions can't
                                     # resolve a synthetic eid)


# ---- cleaning -------------------------------------------------------------------
def clean(text: str) -> str:
    """Machinery spans (closed AND cut-off <system-reminder> tags) out,
    whitespace collapsed."""
    t = strip_machinery_text(text or "")
    return " ".join(_UNCLOSED.sub("", t).split())


def discourse_clean(text: str) -> str:
    """Every capture shape of tool text, removed wherever it sits: the
    '-> bash(args)' marker, an 'args: {...}' run, an inline 'name({...'
    call -- each to the end of its line."""
    t = _ARROW.sub("", text or "")
    t = _ARGS_ANY.sub("", t)
    t = _INLINE_ANY.sub("", t)
    return t.strip()


def unescape(v):
    """Captures sometimes double-escape: a literal backslash-n where the
    command had a newline ('\\nsys.path.insert' -> a fake symbol)."""
    if isinstance(v, str) and "\\n" in v:
        return v.replace("\\n", "\n").replace("\\t", " ").replace('\\"', '"')
    return v


def parse_args(raw) -> dict:
    """Tool arguments -> dict: JSON first, a regex salvage of the common
    keys for clipped/invalid JSON (the audit clips args)."""
    if isinstance(raw, dict):
        return {k: unescape(v) for k, v in raw.items()}
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return {k: unescape(x) for k, x in v.items()} if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        out = {}
        for k in ("filePath", "command", "pattern", "path"):
            m = re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)', str(raw))
            if m:
                out[k] = unescape(m.group(1))
        return out


_EMISSION_BULLET = re.compile(
    r"Tool call\s*\*{0,2}\s*\(([^)\n]+)\)\s*:\s?", re.IGNORECASE)


def _balanced_json(s: str) -> str:
    """The balanced {...} at s's start (leading whitespace ok), string-
    aware -- a brace inside a quoted value never closes the object. No
    close by end-of-string = the raw text as it stands (which the round
    loop's _args_bad path then refuses honestly, never a guess)."""
    i = s.find("{")
    if i < 0:
        return ""
    depth, instr, esc = 0, False, False
    for j in range(i, len(s)):
        c = s[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
    return s[i:].strip()


def emission_calls(text: str) -> list[tuple[str, str]]:
    """THE EMISSION SALVAGE (the five bare one-liners, 2026-10-14): a
    per-turn model sometimes WRITES its call as a display bullet instead
    of emitting a tool_call --

        🧰 **Tool call** (exec): `{"action": "link", ...}`

    -- nothing ran, no receipt rode back, the turn just ended. This
    reads the bullet shape back out of the prose: one (tool, RAW-args)
    pair per bullet, the model's own JSON text untouched, so the round
    loop's ordinary gate parses it (and refuses honestly on a bad or
    unterminated object, never guesses). A bullet with no object after
    it is not a call. Prose discussing JSON without the bullet shape is
    left alone."""
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        m = _EMISSION_BULLET.search(line)
        if not m:
            continue
        name = (m.group(1) or "").strip()
        rest = line[m.end():]
        raw = _balanced_json(rest.lstrip("`* \t"))
        if not raw:
            continue
        out.append((name, raw.strip()))
    return out


def shell_skeleton(cmd: str) -> str:
    """The command with heredoc bodies and quoted strings removed: what is
    left is shell structure (commands, flags, paths). Python run inside a
    heredoc or `-c "..."` is not a command."""
    return _QUOTED.sub(" ", _HEREDOC.sub(" ", cmd or ""))


def shell_heads(cmd: str) -> list[str]:
    out = []
    for h in _SHELL_HEADS.findall(shell_skeleton(unescape(cmd or ""))):
        h = h.rsplit("/", 1)[-1]
        if h not in _NOT_HEADS and h not in out:
            out.append(h)
    return out


def ports(text: str) -> list[str]:
    return list(dict.fromkeys(a or b for a, b in _PORT.findall(text or "")))


# ---- observation outcomes -------------------------------------------------------
_RAN = re.compile(r"Ran (\d+) tests?|(\d+) passed")
# a runner's failure COUNTERS (read on the lowered text): pytest's
# '2 failed' / '1 error' summary and its 'FAILED path::test' lines,
# unittest's 'FAILED (failures=1, errors=2)' -- a zero count is not one
_RUNNER_FAIL = re.compile(r"\b[1-9]\d* (?:failed|errors?)\b"
                          r"|\((?:failures|errors)=[1-9]"
                          r"|^failed \S+::", re.M)


def tests_ran(head: str, tail: str = "") -> int | None:
    m = _RAN.search((tail or "") + "\n" + (head or "")) or _RAN.search(head or "")
    return int(m.group(1) or m.group(2)) if m else None


def is_full_suite(cmd: str) -> bool:
    c = cmd or ""
    if "unittest discover" in c:
        return True
    return (bool(re.search(r"(^|[\s;&|])pytest(\s|$)", c)) and "::" not in c
            and not re.search(r"pytest\s+\S+\.py", c))


def outcome(tool: str, head: str, tail: str = "",
            rc: int | None = None) -> str:
    """The observation's fact class, read from the result head AND tail
    (unittest prints 'Ran N tests' / 'FAILED' at the END).

    THE RC TRUMPS THE TEXT (the 'rc=0 error' residue exhibit,
    2026-10-06): a non-None rc is the GROUND truth of a shell run -- the
    text heuristic (an 'error' MENTION in a successful command's
    output: grep hits, log excerpts, a test's own error-path prints)
    no longer overrides it: rc==0 + text-error-words => the honest
    'ok (mentions error)' still reads as OK's fact class; rc!=0 => an
    error class carries it even when the text was silent. Callers
    without an rc (capture-side events, pre-rc eras) keep the text
    heuristic as exactly before."""
    r = ((head or "") + "\n" + (tail or "")).lower()
    if tool in ("edit", "write"):
        return "applied" if "successfully" in r else ("failed" if r.strip() else "unknown")
    if tests_ran(head, tail) is not None:
        # THE TIER READS THE RC (issue #48): a rc=0 `git log` whose
        # COMMIT MESSAGES matched _RAN ('N passed on this tree') and
        # mentioned 'failed' was stamped tests-failed -- the word is the
        # output's content, not the run's fact. BUT the pipe lies about
        # the rc: /bin/sh runs without pipefail and the seats run their
        # suites as `pytest -q 2>&1 | tail -4` (rc = tail's 0). So the
        # runner's own failure COUNTERS ('2 failed', 'FAILED (failures=',
        # a 'FAILED path::test' line) are the fact whatever the rc; the
        # bare word only counts when rc is non-zero or absent.
        if _RUNNER_FAIL.search(r):
            return "tests-failed"
        bad = bool(re.search(r"failed|failures=|errors=|\bfail\b", r))
        return "tests-failed" if bad and rc != 0 else "tests-ok"
    # the not-found TEXT classes come AFTER the rc gate: a run's exit
    # code is ground truth, and 'file or directory not found' inside a
    # FAILED pytest collection said nothing about the command's fate --
    # the shell run whose rc!=0 was relabeled 'not-found' (issue #13).
    not_found = "not found" in r or "no files found" in r or "no such file" in r
    if rc is not None:
        if rc == 0:
            # the text-error heuristic yields: a MENTION in a
            # zero-exit run is the output's content, not the run's
            # fact (the residue line honest both ways: 'ok' carries)
            return "ok"
        return "error"
    if not_found:
        return "not-found"
    if "traceback" in r or "error" in r[:400] or "failed" in r[:400]:
        return "error"
    return "ok" if r.strip() else "empty"


def observation_fact(ev: Event) -> str:
    """The ONE embedded sentence of an observation: '<tool> <target>:
    <outcome>. <first informative result line>'."""
    a = ev.args or {}
    target = a.get("filePath") or a.get("path") or ""
    if not target and a.get("command"):
        target = " ".join(shell_heads(a["command"])[:3])
    if not target and a.get("pattern"):
        target = f"'{a['pattern'][:60]}'"
    detail = ""
    for line in (ev.result_head or "").splitlines():
        line = " ".join(line.split())
        if len(line) >= 12:
            detail = line[:160]
            break
    base = f"{ev.tool} {target}".strip()
    return f"{base}: {ev.outcome or 'pending'}." + (f" {detail}" if detail else "")


# ---- sentences -------------------------------------------------------------------
def content_words(s: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(s or "")
            if len(w) >= 3 and w.lower() not in STOP]


def norm_sentence(s: str) -> str:
    """The boilerplate key: lowercase, digits folded, head only."""
    return re.sub(r"\d+", "0", " ".join((s or "").lower().split()))[:80]


def sentences(text: str) -> list[str]:
    """Discourse sentences worth embedding: 25-400 chars, >= 2 content
    words (E23's rule), in order, deduped, capped."""
    out, seen = [], set()
    for s in _spans(text or "", max_len=400):
        s = " ".join(s.split())
        if 25 <= len(s) <= 400 and len(content_words(s)) >= 2 and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
        if len(out) >= MAX_SENTENCES:
            break
    return out


# ---- live turns ------------------------------------------------------------------
def _obs(tool: str, args, result: str | None, call_id: str, served: str,
         ts, src: str = "live", clipped: bool = False) -> Event:
    res = unescape(result) if result is not None else None
    head = (res or "")[:RESULT_HEAD]
    tail = (res or "")[-RESULT_TAIL:] if res and len(res) > RESULT_HEAD else ""
    return Event(stream="observation", role="tool", tool=tool or "?",
                 args=parse_args(args), result_head=head, result_tail=tail,
                 result_len=len(res or ""),
                 outcome=("call-only" if res is None else outcome(tool, head, tail)),
                 call_id=call_id or "", served_text=served, ts=ts, src=src,
                 clipped=clipped)


def self_line(text: str, ts: float | None = None) -> list[Event]:
    """Blurp's own line (#337) -> one discourse event with role `self`: its
    words stay its own on the field, never the model's prose."""
    t = discourse_clean(clean(text))
    return [Event(stream="discourse", role="self", text=t, ts=ts)] if t else []


def split_turn(query: str, prose: str = "", calls: dict | None = None,
               thought: str = "", results: list | None = None,
               ts: float | None = None) -> list[Event]:
    """One live turn -> events. calls: {idx: {id, function: {name,
    arguments}}} (the proxy's accumulator shape); results: [(call_id,
    name, args, result_text)]. Mixed prose+tool turns keep their prose."""
    evs: list[Event] = []
    q = clean(query)
    if q:
        evs.append(Event(stream="discourse", role="user", text=q, ts=ts,
                         author=author_of(q)))
    p = discourse_clean(clean(prose))
    if p:
        evs.append(Event(stream="discourse", role="model", text=p, ts=ts))
    t = discourse_clean(clean(thought))
    if t:
        evs.append(Event(stream="discourse", role="reasoning", text=t, ts=ts))
    for c in (calls or {}).values():
        fn = (c or {}).get("function") or {}
        evs.append(_obs(fn.get("name", ""), fn.get("arguments", ""), None,
                        (c or {}).get("id", ""), q, ts))
    for cid, name, args, result in (results or []):
        evs.append(_obs(name, args, result or "", cid, q, ts))
    return evs


# ---- the audit log ---------------------------------------------------------------
def split_audit_record(rec: dict) -> list[Event]:
    """One audit 'decision' record -> events. The six shapes:
      'tool <name> for: <msg>\\nargs: {json}'  + result  -> obs + user msg
      'tool call for: <msg> -> name(args)'   + call     -> call obs + user msg
      'plan: <q>'                            + prose    -> user q + model prose
      'reasoning: <q>'                       + thought  -> user q + reasoning
      'compact note: ...'                               -> distilled
      a plain turn (inline 'name({...' calls split off)  -> user + model
    Audit text is clipped and whitespace-collapsed (mark clipped)."""
    q, resp, ts = rec.get("query") or "", rec.get("response") or "", rec.get("ts")
    src = "audit"
    if q.startswith("compact note"):
        return [Event(stream="distilled", role="note", ts=ts, src=src,
                      text=clean(q)[:800] + " => " + clean(resp)[:800])]
    m = _TOOL.match(q)
    if m:
        tool, msg, raw = m.group(1), discourse_clean(clean(m.group(2))), m.group(3)
        out = [_obs(tool, raw or "", resp, "", msg, ts, src, clipped=True)]
        if msg:
            out.insert(0, Event(stream="discourse", role="user", text=msg, ts=ts, src=src,
                                author=author_of(msg)))
        return out
    c = _CALL.match(q)
    if c:
        msg = discourse_clean(clean(c.group(1)))
        m2 = _INLINE.search(resp or "")
        args = m2.group(2).rstrip(")") if m2 else ""
        out = [_obs(c.group(2), args, None, "", msg, ts, src, clipped=True)]
        if msg:
            out.insert(0, Event(stream="discourse", role="user", text=msg, ts=ts, src=src,
                                author=author_of(msg)))
        return out
    out: list[Event] = []
    pm = _PREFIX.match(q)
    role = "model"
    if pm:
        role = "reasoning" if pm.group(1).lower() == "reasoning" else "model"
        q = q[pm.end():]
    qc = clean(q)
    mi = _INLINE.search(qc)
    if mi:
        out.append(_obs(mi.group(1), mi.group(2).rstrip(")"), None, "",
                        qc[:mi.start()].strip(), ts, src, clipped=True))
        qc = qc[:mi.start()].strip()
    qc = discourse_clean(qc)
    if qc:
        out.insert(0, Event(stream="discourse", role="user", text=qc, ts=ts, src=src,
                            author=author_of(qc)))
    ra = discourse_clean(clean(resp))
    mr = _INLINE.search(ra)
    if mr:
        ra = ra[:mr.start()].strip()
    if ra:
        out.append(Event(stream="discourse", role=role, text=ra, ts=ts, src=src))
    return out
