"""The LLM as a coprocessor (#454), behind MEMBRANE_COPROC=1 (default off).

Ambiguity is conserved: removing rigid syntax only moves it to the edges.
So the line is drawn RIGID exactly where something executes (decide,
remember, intend change the field) and FREE everywhere else (the prose the
person reads). Membrane compiles the conversation and its memory into the
model's SURFACE (its model map's pick), the model extends it, and membrane
decompiles the extension:

- three surfaces: python-shaped calls (`decide(text)`, `remember(key,
  value)`, `intend(text)`, `assistant.says(text)` and membrane's tool calls),
  labelled text lines (`DECIDE:`, `REMEMBER: k: v`, `INTEND:`, `SAY:`), and
  word=node sentences (`Decided:` / `I decide`, `Remember: X is Y.`,
  `Remind: ...`; every other sentence is prose and the reply IS the text);
- the PARSER is strict: an action counts only when fully, unambiguously
  parsed; anything else is a lint error handed back next turn, never run.
  Python-shaped lines are read with `ast` (literals only): nothing is ever
  exec'd;
- the EXECUTOR compiles each action 1:1 to a cliffhanger word (flat, depth 1)
  recorded with provenance (who, when, conversation, model); a fact's newer
  value wins and its history is kept; receipts return next turn in the
  surface; an identical action repeated in one answer, or repeating the
  previous turn's applied action, is refused as a lint error (a loop made
  exact);
- the INJECTION GUARD: an action counts only when the person's message in
  this turn carries it (a tool round, a web page, recalled memory or the
  model's own idea is held as a visible PROPOSAL, applied only when the
  person confirms it).

No model call anywhere; every failure degrades to the reply as it came.
"""

from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field as dc_field

ENV = "MEMBRANE_COPROC"
SURFACES = ("python", "text", "words")
EXAMPLE_SETS = ("varied", "one")
WORDS = ("decide", "remember", "intend")
# membrane tool names a claude-agent rung must not expose while coproc is on:
# a python-shaped `intend(...)` line must stay a text line, never a tool call
RESERVED_TOOL_NAMES = frozenset({"decide", "remember", "intend", "says", "say"})

# ---- constants (each awaits the operator's approval, see #454's PR) --------
EXAMPLES = 3                 # varied examples per surface (round 2: 3 > 1)
RECENT_EXCHANGES = 3         # this chat's compiled exchanges in the block
FACTS_SHOWN = 20             # newest facts / decisions rendered as memory
TRACE_MIN = 0.3              # share of an action's words the person's message holds (measured: 60/66 right actions)
PROPOSAL_TTL_S = 24 * 3600.0  # a held proposal stays confirmable this long
REPEAT_LOOKBACK_TURNS = 1    # turns back an identical applied action is a loop
ARG_MAX_CHARS = 300          # one decision / fact / intent, not a paste


def enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV, "")).strip() == "1"


# ======================================================================
# the parser
# ======================================================================

@dataclass
class Action:
    word: str                      # decide | remember | intend | tool
    args: dict
    line: int = 0
    src: str = ""


@dataclass
class Lint:
    line: int
    text: str
    why: str


@dataclass
class Parsed:
    surface: str
    text: str
    actions: list = dc_field(default_factory=list)
    lint: list = dc_field(default_factory=list)
    says: str | None = None
    loose: list = dc_field(default_factory=list)   # unparsed prose lines (shown when no says)


_FENCE = re.compile(r"^\s*```")


def _strip_fences(text: str) -> list[str]:
    return ["" if _FENCE.match(ln) else ln for ln in (text or "").splitlines()]


def _clean(s) -> str:
    return " ".join(str(s or "").split())


def _check_arg(word: str, name: str, v) -> str | None:
    if not isinstance(v, str):
        return f"{word}(): {name} must be a string literal"
    if not v.strip():
        return f"{word}(): {name} is empty"
    if word != "says" and len(v) > ARG_MAX_CHARS:
        return f"{word}(): {name} is longer than {ARG_MAX_CHARS} characters"
    return None


_SIG = {"decide": ("text",), "intend": ("text",), "remember": ("key", "value"),
        "says": ("text",)}


def _call_of(node, tools) -> tuple[Action | None, str | None, str | None]:
    """(action, says_text, lint_why) for one statement node."""
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return None, None, "not a call (only decide/remember/intend/assistant.says)"
    call = node.value
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
            and f.value.id == "assistant" and f.attr == "says":
        name = "says"
    elif isinstance(f, ast.Name) and f.id in WORDS:
        name = f.id
    elif isinstance(f, ast.Name) and f.id in tools:
        if call.args:
            return None, None, f"{f.id}(): membrane tools take keyword arguments only"
        kw = {}
        for k in call.keywords:
            if k.arg is None:
                return None, None, f"{f.id}(): no ** arguments"
            try:
                kw[k.arg] = ast.literal_eval(k.value)
            except Exception:
                return None, None, f"{f.id}(): argument {k.arg} is not a literal"
        return Action("tool", {"name": f.id, "kwargs": kw}), None, None
    else:
        try:
            shown = ast.unparse(f)
        except Exception:
            shown = "?"
        return None, None, f"unknown call {shown}()"
    sig = _SIG[name]
    vals: dict = {}
    if len(call.args) > len(sig):
        return None, None, f"{name}(): too many arguments"
    for i, a in enumerate(call.args):
        if not isinstance(a, ast.Constant):
            return None, None, f"{name}(): {sig[i]} must be a string literal"
        vals[sig[i]] = a.value
    for k in call.keywords:
        if k.arg not in sig or k.arg in vals:
            return None, None, f"{name}(): unexpected argument {k.arg}"
        if not isinstance(k.value, ast.Constant):
            return None, None, f"{name}(): {k.arg} must be a string literal"
        vals[k.arg] = k.value.value
    for s in sig:
        if s not in vals:
            return None, None, f"{name}(): missing {s}"
        why = _check_arg(name, s, vals[s])
        if why:
            return None, None, why
    if name == "says":
        return None, vals["text"], None
    return Action(name, {s: vals[s].strip() for s in sig}), None, None


def _parse_python(text: str, tools=()) -> Parsed:
    p = Parsed("python", text)
    lines = _strip_fences(text)
    tools = tuple(t for t in (tools or ()) if t not in WORDS)
    says: list[str] = []

    def take(src: str, lineno: int) -> None:
        try:
            mod = ast.parse(src)
        except SyntaxError:
            p.lint.append(Lint(lineno, src.strip()[:200], "not parseable"))
            p.loose.append(src.strip())
            return
        for st in mod.body:
            act, said, why = _call_of(st, tools)
            seg = (ast.get_source_segment(src, st) or "").strip()
            if why:
                p.lint.append(Lint(lineno + st.lineno - 1, seg[:200], why))
                if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)):
                    p.loose.append(seg)           # a bare word of prose
                continue
            if said is not None:
                says.append(said)
            elif act is not None:
                act.line, act.src = lineno + st.lineno - 1, seg
                p.actions.append(act)

    def parses(src: str) -> bool:
        try:
            ast.parse(src)
            return True
        except SyntaxError:
            return False

    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip() or ln.strip().startswith("#"):
            i += 1
            continue
        if parses(ln.strip()):
            take(ln.strip(), i + 1)
            i += 1
            continue
        # a statement over several lines (a triple-quoted says): extend while
        # the next line is a continuation (a line that stands alone is not)
        j, done = i + 1, False
        while j < n and j - i <= 40:
            nxt = lines[j].strip()
            if nxt and parses(nxt) and not nxt.startswith(("'", '"', ")")):
                break
            chunk = "\n".join(x.strip() if k == i else x for k, x in enumerate(lines[i:j + 1], i))
            if parses(chunk):
                take(chunk, i + 1)
                i, done = j + 1, True
                break
            j += 1
        if not done:
            p.lint.append(Lint(i + 1, ln.strip()[:200], "not parseable"))
            p.loose.append(ln.strip())
            i += 1
    if says:
        p.says = "\n\n".join(says)
    return p


_LABEL = re.compile(r"^(DECIDE|REMEMBER|INTEND|SAY):\s*(.*)$")
_KEY = re.compile(r"^[A-Za-z0-9_][\w .\-/]{0,59}$")


def _remember_kv(word: str, key: str, value: str, lineno: int, src: str):
    key, value = key.strip(), value.strip().rstrip(".").strip()
    if not key or not _KEY.match(key):
        return None, Lint(lineno, src[:200], f"{word}: the key is missing or not a short name")
    for nm, v in (("key", key), ("value", value)):
        why = _check_arg("remember", nm, v)
        if why:
            return None, Lint(lineno, src[:200], why)
    return Action("remember", {"key": key, "value": value}, lineno, src), None


def _parse_text(text: str) -> Parsed:
    p = Parsed("text", text)
    say: list[str] | None = None
    for no, ln in enumerate(_strip_fences(text), 1):
        m = _LABEL.match(ln.strip())
        if not m:
            if say is not None:
                say.append(ln.rstrip())
            elif ln.strip():
                p.lint.append(Lint(no, ln.strip()[:200], "an unlabelled line before SAY:"))
                p.loose.append(ln.strip())
            continue
        lab, rest = m.group(1), m.group(2).strip()
        if lab == "SAY":
            say = [rest]
            continue
        say_was = say
        say = None
        if say_was is not None:
            p.says = "\n".join(say_was).strip()
        if lab == "REMEMBER":
            k, sep, v = rest.partition(":")
            if not sep:
                p.lint.append(Lint(no, ln.strip()[:200], "REMEMBER needs 'key: value'"))
                continue
            act, lint = _remember_kv("REMEMBER", k, v, no, ln.strip())
            (p.actions.append(act) if act else p.lint.append(lint))
            continue
        word = "decide" if lab == "DECIDE" else "intend"
        why = _check_arg(word, "text", rest)
        if why:
            p.lint.append(Lint(no, ln.strip()[:200], why))
        else:
            p.actions.append(Action(word, {"text": rest.rstrip(".").strip()}, no, ln.strip()))
    if say is not None:
        p.says = "\n".join(say).strip()
    return p


_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_COPULA = re.compile(r"\s+(?:is|are|=)\s+", re.I)


def _parse_words(text: str) -> Parsed:
    p = Parsed("words", text)
    for no, s in enumerate((x.strip() for x in _SENT.split(text or "")), 1):
        if not s:
            continue
        body = s.rstrip(".!?").strip()
        m = re.match(r"^(?:Decided:|I decide\b)\s*(.*)$", body)
        if m:
            why = _check_arg("decide", "text", m.group(1))
            if why:
                p.lint.append(Lint(no, s[:200], why))
            else:
                p.actions.append(Action("decide", {"text": m.group(1).strip()}, no, s))
            continue
        m = re.match(r"^Remind:\s*(.*)$", body)
        if m:
            why = _check_arg("intend", "text", m.group(1))
            if why:
                p.lint.append(Lint(no, s[:200], why))
            else:
                p.actions.append(Action("intend", {"text": m.group(1).strip()}, no, s))
            continue
        m = re.match(r"^Remember:\s*(.*)$", body)
        if m:
            parts = _COPULA.split(m.group(1), maxsplit=1)
            if len(parts) != 2:
                p.lint.append(Lint(no, s[:200], "Remember: needs 'X is Y'"))
                continue
            act, lint = _remember_kv("Remember:", parts[0], parts[1], no, s)
            (p.actions.append(act) if act else p.lint.append(lint))
    p.says = text.strip()
    return p


def parse(text: str, surface: str, tools=()) -> Parsed:
    """The model's extension -> actions, the reply and lint errors."""
    text = str(text or "")
    if surface == "text":
        return _parse_text(text)
    if surface == "words":
        return _parse_words(text)
    return _parse_python(text, tools)


def reply_of(parsed: Parsed, surface: str) -> str:
    """The words the person sees: the says / SAY part; with none, the
    unparsed prose (a model that ignored the surface still answers)."""
    if surface == "words":
        return parsed.text.strip()
    if parsed.says is not None:
        return parsed.says
    return "\n".join(parsed.loose).strip()


# ======================================================================
# canonical forms, traceability
# ======================================================================

_ARTICLES = ("the ", "a ", "an ", "my ", "our ", "your ")


def fact_key(key: str) -> str:
    k = _clean(key).lower()
    for a in _ARTICLES:
        if k.startswith(a):
            k = k[len(a):]
            break
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_") or "_"


def canonical(a: Action) -> tuple:
    if a.word == "remember":
        return ("remember", fact_key(a.args.get("key", "")),
                _clean(a.args.get("value", "")).lower().rstrip("."))
    if a.word == "tool":
        return ("tool", a.args.get("name"), json.dumps(a.args.get("kwargs"), sort_keys=True))
    return (a.word, _clean(a.args.get("text", "")).lower().rstrip("."))


_STOP = frozenset("""a an the and or but if then so of to in on at by for with from as is are was
were be been being it its this that these those i me my we our you your he she they them his her
their do does did done have has had not no yes please just also too very can could will would
should shall may might must let lets let's from now on about into over under up down out off
what which who whom whose when where why how all any some each every there here than""".split())
_TOK = re.compile(r"[a-z0-9]+(?:[.'/:\-][a-z0-9]+)*")


def _tokens(s: str) -> set:
    out = set()
    for t in _TOK.findall(str(s or "").lower().replace("_", " ")):
        if t in _STOP or (len(t) < 2 and not t.isdigit()):
            continue
        out.add(t)
    return out


def _match(t: str, pool: set) -> bool:
    if t in pool:
        return True
    if len(t) >= 4:
        return any(len(p) >= 4 and (p.startswith(t) or t.startswith(p)) for p in pool)
    return False


def trace_share(a: Action, person_text: str) -> float:
    """The share of the action's content words the person's message holds."""
    if a.word == "remember":
        want = _tokens(a.args.get("key", "")) | _tokens(a.args.get("value", ""))
    else:
        want = _tokens(a.args.get("text", ""))
    have = _tokens(person_text)
    if not want or not have:
        return 0.0
    return sum(1 for t in want if _match(t, have)) / len(want)


def traceable(a: Action, person_text: str) -> bool:
    return trace_share(a, person_text) >= TRACE_MIN


# ======================================================================
# the store (the cliffhanger words' record)
# ======================================================================

class Store:
    """Append-only JSONL next to the field (`<field>.coproc.jsonl`), or in
    memory when `path` is None. Records: actions (applied / proposal /
    lint / refused / failed), confirmations and turns."""

    def __init__(self, path: str | None = None):
        self.path = str(path) if path else None
        self._lock = threading.Lock()
        self.recs: list[dict] = []
        if self.path:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    for ln in f:
                        try:
                            r = json.loads(ln)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if isinstance(r, dict):
                            self.recs.append(r)
            except OSError:
                pass

    def append(self, rec: dict) -> dict:
        with self._lock:
            self.recs.append(rec)
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except OSError:
                    pass
        return rec

    def next_id(self) -> str:
        return "a%d" % (1 + sum(1 for r in self.recs if r.get("kind") == "action"))

    def _actions(self):
        return [r for r in self.recs if r.get("kind") == "action"]

    def facts(self) -> dict:
        out: dict = {}
        for r in self._actions():
            if r.get("word") != "remember" or r.get("status") != "applied":
                continue
            k = fact_key(r["args"].get("key", ""))
            prev = out.get(k)
            hist = (prev["history"] if prev else []) + [
                {"value": r["args"].get("value"), "ts": r.get("ts"), "id": r.get("id")}]
            out[k] = {"key": r["args"].get("key"), "value": r["args"].get("value"),
                      "ts": r.get("ts"), "conv": r.get("conv"), "model": r.get("model"),
                      "who": r.get("who"), "id": r.get("id"), "history": hist}
        return out

    def decisions(self) -> list:
        return [r for r in self._actions()
                if r.get("word") == "decide" and r.get("status") == "applied"]

    def _confirmed(self) -> set:
        return {r.get("confirms") for r in self._actions() if r.get("confirms")}

    def proposals(self, conv: str | None = None, now: float | None = None) -> list:
        now = time.time() if now is None else now
        done = self._confirmed()
        return [r for r in self._actions()
                if r.get("status") == "proposal" and r.get("id") not in done
                and float(r.get("ts") or 0) + PROPOSAL_TTL_S > now
                and (conv is None or r.get("conv") == conv)]

    def get(self, aid: str) -> dict | None:
        for r in self._actions():
            if r.get("id") == aid:
                return r
        return None

    def turns(self, conv: str, n: int) -> list:
        ts = [r for r in self.recs if r.get("kind") == "turn" and r.get("conv") == conv]
        return ts[-n:] if n > 0 else []


# ======================================================================
# the executor: each action -> one cliffhanger word
# ======================================================================

class Executor:
    def __init__(self, store: Store, intend_fn=None, learn_fn=None):
        self.store = store
        self.intend_fn = intend_fn
        self.learn_fn = learn_fn

    def _apply(self, word: str, args: dict) -> str:
        """Run the word; returns a short note ('' = fine)."""
        if word == "intend":
            if self.intend_fn is not None:
                try:
                    r = self.intend_fn(args["text"])
                    return f"on the board ({r})" if r else ""
                except Exception as e:
                    return f"the board refused: {str(e)[:80]}"
            return ""
        if self.learn_fn is not None:
            line = (f"{args['key']}: {args['value']}" if word == "remember"
                    else f"Decided: {args['text']}")
            try:
                self.learn_fn(line)
            except Exception:
                pass
        return ""

    def _prev_applied(self, conv: str) -> set:
        out = set()
        for t in self.store.turns(conv, REPEAT_LOOKBACK_TURNS):
            for r in t.get("receipts") or []:
                if r.get("status") == "applied" and r.get("canon"):
                    out.add(tuple(r["canon"]))
        return out

    def run(self, parsed: Parsed, person_text: str = "", person_turn: bool = True,
            conv: str = "", model: str = "", now: float | None = None,
            extension: str | None = None, record_turn: bool = True) -> list:
        now = time.time() if now is None else now
        base = {"kind": "action", "ts": now, "conv": conv or "", "model": model or ""}
        receipts: list = []
        for lt in parsed.lint:
            receipts.append({"status": "lint", "line": lt.line, "text": lt.text, "why": lt.why})
        prev = self._prev_applied(conv or "")
        seen: set = set()
        for a in parsed.actions:
            canon = canonical(a)
            if a.word == "tool":
                receipts.append({"status": "refused", "word": "tool", "args": a.args,
                                 "line": a.line, "text": a.src,
                                 "why": "membrane tools run through the tool channel, not as text"})
                continue
            if canon in seen or canon in prev:
                receipts.append({"status": "lint", "word": a.word, "args": a.args,
                                 "line": a.line, "text": a.src,
                                 "why": "repeated action, not executed"})
                seen.add(canon)
                continue
            seen.add(canon)
            rec = dict(base, id=self.store.next_id(), word=a.word, args=dict(a.args),
                       canon=list(canon))
            if person_turn and traceable(a, person_text):
                rec.update(status="applied", who="person", why="")
                note = self._apply(a.word, a.args)
                if note:
                    rec["note"] = note
            else:
                rec.update(status="proposal", who="model",
                           why=("no person message this turn (a tool round)"
                                if not person_turn else "not in the person's message"))
            self.store.append(rec)
            receipts.append(rec)
        if record_turn and (receipts or (extension or "").strip() or person_text):
            self.store.append({"kind": "turn", "ts": now, "conv": conv or "",
                               "model": model or "", "person": (person_text or "")[:2000],
                               "extension": (extension if extension is not None
                                             else parsed.text)[:4000],
                               "receipts": [{k: v for k, v in r.items() if k != "kind"}
                                            for r in receipts]})
        return receipts

    def confirm(self, aid: str, now: float | None = None) -> dict:
        """The person's hand on a held proposal: apply it now."""
        now = time.time() if now is None else now
        p = self.store.get(aid)
        if p is None or p.get("status") != "proposal" or \
                aid in self.store._confirmed():
            return {"status": "missing", "id": aid}
        if float(p.get("ts") or 0) + PROPOSAL_TTL_S <= now:
            return {"status": "expired", "id": aid}
        rec = {"kind": "action", "id": self.store.next_id(), "ts": now,
               "conv": p.get("conv", ""), "model": p.get("model", ""),
               "word": p["word"], "args": dict(p["args"]), "canon": p.get("canon"),
               "status": "applied", "who": "person (confirmed)", "why": "",
               "confirms": aid}
        note = self._apply(p["word"], p["args"])
        if note:
            rec["note"] = note
        return self.store.append(rec)


# ======================================================================
# the renderer
# ======================================================================

_INSTR = {
    "python": (
        "You continue a program: a conversation compiled by a memory system. "
        "Everything shown is read-only. Write ONLY new lines using: "
        "decide(text) for a decision the person just made; remember(key, value) "
        "for a NEW fact the person just stated; intend(text) for a task to do later; "
        "assistant.says(text) for your reply (exactly one, last). Never re-record "
        "facts already in memory. These lines are plain text you write, NOT tools: "
        "do not call any tool to make them. No markdown fences."),
    "text": (
        "You continue a conversation log kept by a memory system. Everything shown "
        "is read-only. Write ONLY new lines, each starting with a label: DECIDE: <a "
        "decision the person just made>; REMEMBER: <key>: <a NEW fact the person "
        "just stated>; INTEND: <a task for later>; SAY: <your reply, exactly one, "
        "last>. Never re-record facts already in memory. No markdown fences."),
    "words": (
        "You reply to the person in plain prose. A memory system reads your reply: "
        "it acts on a sentence that starts with one of these words: \"Decided: ...\" "
        "(or \"I decide ...\") for a decision the person just made; \"Remember: "
        "<thing> is <value>.\" for a NEW fact the person just stated; \"Remind: ...\" "
        "for a task for later. Every other sentence is just talk. Never restate "
        "facts the memory already has as Remember sentences. No markdown."),
}

# dataset-entry style: a normal turn, a no-op (question) turn, an undo
_EX = [
    ("the dinghy anchor is a 5 kg danforth, use it from now on, and remind me to buy a shear pin",
     {"python": 'remember("dinghy_anchor", "5 kg Danforth")\n'
                'decide("use the Danforth anchor from now on")\n'
                'intend("buy a spare shear pin")\n'
                'assistant.says("Noted the anchor, switching to the Danforth, and the shear pin is on the list.")',
      "text": "REMEMBER: dinghy_anchor: 5 kg Danforth\n"
              "DECIDE: use the Danforth anchor from now on\n"
              "INTEND: buy a spare shear pin\n"
              "SAY: Noted the anchor, switching to the Danforth, and the shear pin is on the list.",
      "words": "Remember: the dinghy anchor is a 5 kg Danforth. Decided: use the Danforth "
               "from now on. Remind: buy a spare shear pin. Noted, and the shear pin is on the list."}),
    ("what's the license again?",
     {"python": 'assistant.says("It\'s PolyForm Noncommercial 1.0.0.")',
      "text": "SAY: It's PolyForm Noncommercial 1.0.0.",
      "words": "It's PolyForm Noncommercial 1.0.0."}),
    ("forget the shear pin, I found one. what time is the regatta?",
     {"python": 'decide("drop the shear-pin task: one was found")\n'
                'assistant.says("Done, the shear pin is off the list. I don\'t have the regatta time in memory.")',
      "text": "DECIDE: drop the shear-pin task: one was found\n"
              "SAY: Done, the shear pin is off the list. I don't have the regatta time in memory.",
      "words": "Decided: drop the shear-pin task, one was found. I don't have the regatta "
               "time in memory."}),
]


def instructions(surface: str) -> str:
    return _INSTR.get(surface, _INSTR["python"])


def examples(surface: str, which: str = "varied") -> list:
    ex = [(p, outs[surface]) for p, outs in _EX][:EXAMPLES]
    return ex[:1] if which == "one" else ex


def _person_line(surface: str, text: str) -> str:
    if surface == "python":
        return f"person.says({json.dumps(text, ensure_ascii=False)})"
    if surface == "text":
        return "PERSON: " + " ".join(str(text).split())
    return "Person: " + " ".join(str(text).split())


def _render_examples(surface: str, which: str = "varied") -> str:
    out = []
    for person, ext in examples(surface, which):
        if surface == "words":
            out.append(f"{_person_line(surface, person)}\nReply: {ext}")
        else:
            out.append(f"{_person_line(surface, person)}\n{ext}")
    return "Examples (input -> extension):\n" + "\n\n".join(out)


def _fact_line(surface: str, k: str, f: dict) -> str:
    src = f"{f.get('who') or '?'} . {time.strftime('%Y-%m-%d', time.localtime(float(f.get('ts') or 0)))}"
    v = str(f.get("value") or "")
    if surface == "python":
        ident = k if re.match(r"^[A-Za-z_]", k) else "_" + k
        return f"memory.{ident} = {json.dumps(v, ensure_ascii=False)}   # {src}"
    if surface == "text":
        return f"MEMORY {k}: {v}   -- {src}"
    return f"{str(f.get('key') or k)} is {v} ({src})."


def _word_line(surface: str, word: str, args: dict) -> str:
    if surface == "python":
        if word == "remember":
            return f"remember({json.dumps(args.get('key',''), ensure_ascii=False)}, " \
                   f"{json.dumps(args.get('value',''), ensure_ascii=False)})"
        if word == "tool":
            kw = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}"
                           for k, v in (args.get("kwargs") or {}).items())
            return f"{args.get('name')}({kw})"
        return f"{word}({json.dumps(args.get('text',''), ensure_ascii=False)})"
    if surface == "text":
        if word == "remember":
            return f"REMEMBER: {args.get('key')}: {args.get('value')}"
        if word == "tool":
            return f"TOOL: {args.get('name')}"
        return f"{word.upper()}: {args.get('text')}"
    if word == "remember":
        return f"Remember: {args.get('key')} is {args.get('value')}"
    if word == "decide":
        return f"Decided: {args.get('text')}"
    if word == "intend":
        return f"Remind: {args.get('text')}"
    return f"tool {args.get('name')}"


def render_receipts(surface: str, receipts, lint=()) -> str:
    """Last turn's receipts and lint errors, as lines in the surface."""
    c = "#" if surface == "python" else ("--" if surface == "text" else "")
    rows = []
    allr = list(receipts or []) + [{"status": "lint", "line": lt.line, "text": lt.text,
                                    "why": lt.why} for lt in (lint or ())]
    for r in allr:
        st = r.get("status")
        if st == "lint" and not r.get("word"):
            msg = f"lint, line {r.get('line')}: {r.get('why')}: {r.get('text')} -- not run"
            rows.append(f"{c} {msg}" if c else f"(membrane: {msg})")
            continue
        call = _word_line(surface, r.get("word") or "", r.get("args") or {})
        if st == "applied":
            tail = f"-> {r.get('id')} applied" + (f", {r['note']}" if r.get("note") else "")
        elif st == "proposal":
            tail = (f"-> {r.get('id')} held as a proposal, not applied ({r.get('why')}); "
                    "only the person can confirm it")
        else:
            tail = f"-> {st}: {r.get('why')}"
        if surface == "python":
            rows.append(f"{call}  # {tail}")
        elif surface == "text":
            rows.append(f"{call}  {c} {tail}")
        else:
            rows.append(f"(membrane: {call} {tail})")
    return "\n".join(rows)


def _ext_for(surface: str, rec: dict) -> str:
    return str(rec.get("extension") or "").strip()


def compile_block(surface: str, store: Store, conv: str, which: str = "varied",
                  now: float | None = None) -> str:
    """The per-turn system block: the surface, its examples, the memory (facts,
    decisions, open proposals), this chat's recent exchanges and the last
    turn's receipts and lint errors."""
    out = ["[membrane-coproc] " + instructions(surface), _render_examples(surface, which)]
    facts = sorted(store.facts().items(), key=lambda kv: float(kv[1].get("ts") or 0))
    facts = facts[-FACTS_SHOWN:]
    decs = store.decisions()[-FACTS_SHOWN:]
    mem = [_fact_line(surface, k, f) for k, f in facts]
    mem += [_word_line(surface, "decide", d.get("args") or {}) + ("  # " if surface == "python" else "  -- ")
            + f"{d.get('id')} applied" for d in decs]
    head = {"python": "# memory (read-only; a later assignment overrides an earlier one)",
            "text": "-- memory (read-only; newer wins)",
            "words": "What the memory knows (read-only):"}[surface]
    out.append(head + "\n" + ("\n".join(mem) if mem else
                              ("# (empty)" if surface == "python" else
                               "-- (empty)" if surface == "text" else "(nothing yet)")))
    props = store.proposals(conv, now)
    if props:
        out.append({"python": "# held proposals (not applied; the person may confirm)",
                    "text": "-- held proposals (not applied; the person may confirm)",
                    "words": "Held proposals (not applied; the person may confirm):"}[surface]
                   + "\n" + "\n".join(f"{_word_line(surface, p['word'], p['args'])}  [{p['id']}]"
                                      for p in props))
    turns = store.turns(conv, RECENT_EXCHANGES)
    if turns:
        rows = []
        for t in turns:
            rows.append(_person_line(surface, t.get("person") or ""))
            ext = _ext_for(surface, t)
            rows.append(("Reply: " + ext) if surface == "words" else ext)
        out.append({"python": "# recent exchanges in this chat, compiled",
                    "text": "-- recent exchanges in this chat",
                    "words": "Recent exchanges in this chat:"}[surface] + "\n" + "\n".join(rows))
        last = turns[-1].get("receipts") or []
        if last:
            out.append({"python": "# receipts and lint from your last extension",
                        "text": "-- receipts and lint from your last extension",
                        "words": "What the memory did with your last reply:"}[surface]
                       + "\n" + render_receipts(surface, last))
    out.append({"python": "# ---- extend below: the person's message is the last user turn ----",
                "text": "-- ---- extend below: the person's message is the last user turn ----",
                "words": "Reply to the person's last message."}[surface])
    return "\n\n".join(out)


def compile_turn(surface: str, memory: dict, history, person_text: str,
                 which: str = "varied") -> tuple[str, str]:
    """(system, user) for one compiled turn outside the proxy (the harness):
    the surface + examples as the system; memory, the chat so far and the
    person's message as the read-only program to extend."""
    sys_ = instructions(surface) + "\n\n" + _render_examples(surface, which)
    rows = []
    if surface == "python":
        rows += [f"memory.{fact_key(k)} = {json.dumps(str(v), ensure_ascii=False)}"
                 for k, v in (memory or {}).items()]
    elif surface == "text":
        rows += [f"MEMORY {k}: {v}" for k, v in (memory or {}).items()]
    else:
        rows += (["What the memory knows:"] + [f"{k} is {v}." for k, v in (memory or {}).items()]
                 if memory else [])
    for person, ext in history or []:
        rows.append(_person_line(surface, person))
        rows.append(("Reply: " + ext) if surface == "words" else ext)
    rows.append(_person_line(surface, person_text))
    rows.append({"python": "# ---- extend below ----", "text": "-- ---- extend below ----",
                 "words": "Reply:"}[surface])
    return sys_, "\n".join(rows)


def decompile(parsed: Parsed, surface: str, receipts=()) -> str:
    """The reply the person sees, plus a visible line per held proposal."""
    reply = reply_of(parsed, surface)
    held = [r for r in receipts or [] if r.get("status") == "proposal"]
    if held:
        notes = "; ".join(f"{r['id']} {_word_line('words', r['word'], r['args'])}"
                          for r in held)
        reply = (reply + "\n\n" if reply else "") + \
            f"(membrane held as a proposal, not applied: {notes} -- not from your message; " \
            "confirm it on the seat to apply)"
    return reply


# ======================================================================
# the model map
# ======================================================================

def default_map(model: str) -> dict:
    """The measured rounds' defaults: qwen* -> word=node, claude-agent:* and
    everyone else -> python-shaped; three varied examples."""
    n = str(model or "").lower()
    if n.startswith("qwen"):
        return {"surface": "words", "examples": "varied"}
    return {"surface": "python", "examples": "varied"}


def surface_for(model: str, record=None) -> dict:
    out = default_map(model)
    over = getattr(record, "coproc", None) or {}
    if isinstance(over, dict):
        if over.get("surface") in SURFACES:
            out["surface"] = over["surface"]
        if over.get("examples") in EXAMPLE_SETS:
            out["examples"] = over["examples"]
    return out


# ======================================================================
# the proxy seam
# ======================================================================

def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") in (None, "text"))
    return ""


def person_message(messages) -> tuple[bool, str]:
    """(is this a person's turn, the person's text): the last message that is
    not a system block. A tool result (or an assistant prefill) means the
    turn belongs to a tool round, not to the person."""
    for m in reversed(list(messages or [])):
        if not isinstance(m, dict) or m.get("role") == "system":
            continue
        if m.get("role") != "user":
            return False, ""
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "tool_result"
                                       for p in c):
            return False, ""
        return True, _text_of(c)
    return False, ""


def store_for(srv) -> Store:
    st = getattr(srv, "coproc_store", None)
    if st is not None:
        return st
    path = None
    led = getattr(srv, "user_ledger", None)
    lp = str(getattr(led, "path", "") or "")
    if lp.endswith(".ledger.jsonl"):
        path = lp[: -len(".ledger.jsonl")] + ".coproc.jsonl"
    st = Store(path)
    try:
        srv.coproc_store = st
    except Exception:
        pass
    return st


def _tool_names() -> tuple:
    try:
        from . import mcp_server as _mcp
        return tuple(t["name"] for t in _mcp.TOOLS)
    except Exception:
        return ()


class Turn:
    """One model turn under coproc: begun before the request goes out (the
    block injected), finished on the reply (parsed, executed, decompiled)."""

    def __init__(self, srv, surface: str, conv: str, model: str, person_turn: bool,
                 person_text: str):
        self.srv, self.surface, self.conv, self.model = srv, surface, conv, model
        self.person_turn, self.person_text = person_turn, person_text
        self.receipts: list = []

    def _hooks(self):
        srv = self.srv

        def intend(text):
            from . import lanes as _L
            res = _L._membrane_intent_create_or_pin(srv, text, planted_by="person")
            if not res.get("ok", True) and res.get("error"):
                raise RuntimeError(res["error"])
            return res.get("eid") or ""

        def learn(text):
            from . import lanes as _L
            return _L._efield_learn(srv, text, "user")
        return intend, learn

    def finish(self, content: str) -> str:
        try:
            if not str(content or "").strip():
                return content
            parsed = parse(content, self.surface, _tool_names())
            intend, learn = self._hooks()
            ex = Executor(store_for(self.srv), intend_fn=intend, learn_fn=learn)
            self.receipts = ex.run(parsed, person_text=self.person_text,
                                   person_turn=self.person_turn, conv=self.conv,
                                   model=self.model, extension=content)
            return decompile(parsed, self.surface, self.receipts)
        except Exception:
            return content


def begin(srv, up: dict):
    """MEMBRANE_COPROC=1: inject the compiled block into the outbound request
    (in place) and return the Turn that finishes the reply; None when off."""
    if not enabled():
        return None
    try:
        from .capture import current_conversation
        from .rag import Injected
        msgs = list(up.get("messages") or [])
        model = str(up.get("model") or "")
        router = getattr(srv, "router", None)
        rec = (getattr(router, "records", None) or {}).get(model) if router is not None else None
        m = surface_for(model, rec)
        conv = current_conversation()
        person_turn, person_text = person_message(msgs)
        block = compile_block(m["surface"], store_for(srv), conv, m["examples"])
        i = len(msgs)
        while i > 0 and isinstance(msgs[i - 1], dict) and msgs[i - 1].get("role") == "user":
            i -= 1
        msgs.insert(i, Injected(role="system", content=block))
        up["messages"] = msgs
        return Turn(srv, m["surface"], conv, model, person_turn, person_text)
    except Exception:
        return None


def state(srv) -> dict:
    """GET /membrane/coproc: the record as the person reads it."""
    st = store_for(srv)
    router = getattr(srv, "router", None)
    maps = {}
    for name, rec in (getattr(router, "records", None) or {}).items():
        maps[name] = surface_for(name, rec)
    return {"enabled": enabled(), "facts": st.facts(), "decisions": st.decisions()[-50:],
            "proposals": st.proposals(), "maps": maps,
            "constants": {"EXAMPLES": EXAMPLES, "RECENT_EXCHANGES": RECENT_EXCHANGES,
                          "FACTS_SHOWN": FACTS_SHOWN, "TRACE_MIN": TRACE_MIN,
                          "PROPOSAL_TTL_S": PROPOSAL_TTL_S,
                          "REPEAT_LOOKBACK_TURNS": REPEAT_LOOKBACK_TURNS,
                          "ARG_MAX_CHARS": ARG_MAX_CHARS}}


def set_map(srv, model: str, surface: str | None = None, examples_: str | None = None) -> dict:
    """POST /membrane/coproc/map: the operator's override on the router record."""
    router = getattr(srv, "router", None)
    rec = (getattr(router, "records", None) or {}).get(model) if router is not None else None
    if rec is None:
        return {"ok": False, "error": f"no router record named {model!r}"}
    cur = dict(getattr(rec, "coproc", None) or {})
    if surface is not None:
        if surface not in SURFACES and surface != "":
            return {"ok": False, "error": f"surface must be one of {SURFACES}"}
        cur["surface"] = surface
    if examples_ is not None:
        if examples_ not in EXAMPLE_SETS and examples_ != "":
            return {"ok": False, "error": f"examples must be one of {EXAMPLE_SETS}"}
        cur["examples"] = examples_
    rec.coproc = {k: v for k, v in cur.items() if v}
    try:
        router.save()
    except Exception:
        pass
    return {"ok": True, "model": model, "map": surface_for(model, rec)}


def confirm(srv, aid: str) -> dict:
    """POST /membrane/coproc/confirm: the person applies a held proposal."""
    t = Turn(srv, "python", "", "", True, "")
    intend, learn = t._hooks()
    return Executor(store_for(srv), intend_fn=intend, learn_fn=learn).confirm(aid)
