"""THE TOOL FAMILIES (2026-10-09, the operator's token diet).

The seat used to advertise 24 tools with long descriptions (~4,900 tokens a
turn, entity_edit alone ~2,100). It now advertises ~10 FAMILIES, each with
an `action`, a one-line description and short parameters. Every family
action RESOLVES to an existing tool (mcp_server.TOOL_IMPLS) before the lane
gate, so the implementations and the per-lane rules are exactly what they
were; a lane sees a family only with the actions it may use. The old names
still work when called. The long-form documentation lives behind `help`.
"""
from __future__ import annotations

import json
import re

SCRIPT_KINDS = ("getter", "tick", "nudge", "mention", "thought", "dreamed", "delete", "watch")
RETIRED = {"junk_cut"}          # an operator tuning knob, not the model's

_S = lambda d: {"type": "string", "description": d}       # noqa: E731
_I = lambda d: {"type": "integer", "description": d}      # noqa: E731
_B = lambda d: {"type": "boolean", "description": d}      # noqa: E731
_T = lambda t: {"type": t}                                 # noqa: E731  (a name that says it all)


def _pick(args: dict, *keys) -> dict:
    return {k: args[k] for k in keys if k in args and args[k] is not None}


FAMILIES: dict[str, dict] = {
    "find": {
        "description": "Look things up in your field: memory (past text near q), related "
                       "(entities near q), query (structured, e.g. 'kind:c & name~:bms'), "
                       "graph (entities + their links).",
        "actions": {"memory": "query", "related": "entities_related",
                    "query": "entity_query", "graph": "graph"},
        "params": {"q": _S("what to look for"), "k": _I("how many (optional)"),
                   "count": _B("query: just count")},
        "map": {"memory": lambda a: {"topic": a.get("q"), **_pick(a, "k")},
                "related": lambda a: {"query": a.get("q"), **_pick(a, "k")},
                "query": lambda a: {"q": a.get("q"), **_pick(a, "k", "count")},
                "graph": lambda a: {"query": a.get("q"), **_pick(a, "k")}},
    },
    "entity": {
        "description": "Your entities (your model of the world): read (its live value; "
                       "run=true recomputes it), write (prop=value: how to act), add, edit, remove, inherit.",
        "actions": {"read": "entity_read", "write": "entity_write", "add": "entity_add", "edit": "entity_edit",
                    "remove": "entity_remove", "inherit": "entity_inherit",
                    "enter": "ctx_enter", "exit": "ctx_exit"},
        "params": {"spec": _S("the entity (eid or name)"), "name": _S("add: its name"),
                   "class": _S("add/edit: its class"), "summary": _S("what it is"),
                   "links": {"type": "array", "items": {"type": "string"}, "description": "related entities"},
                   "run": _B("read: recompute now"),
                   "force": _B("remove: even a live method"),
                   "parents": {"type": "array", "items": {"type": "string"}, "description": "inherit: ISA parents"},
                   "properties": {"type": "object", "description": "edit: properties to set"},
                   "watch_ops": {"type": "array", "items": {"type": "object"}, "description": "edit: help('watches')"},
                   "watch_fields": {"type": "array", "items": {"type": "object"}, "description": "edit: motion watches"},
                   "merged_into": _S("edit: merge into"),
                   "prop": _S("write: property"), "value": {"description": "write: value"}},
        "map": {"read": lambda a: _pick({"spec": a.get("spec") or a.get("name") or a.get("object") or a.get("key"), "run": a.get("run")}, "spec", "run"),
                # an add keeps what it was given (2026-10-11 sweep: properties and
                # watches were stripped here -- the add 'succeeded' with nothing set)
                "add": lambda a: {**_pick(a, "class", "name", "summary", "links",
                                          "watch", "watch_ops", "watch_fields", "publish"),
                                  **({"properties": {**(a.get("properties") or {}),
                                                     **({"parents": a["parents"]} if "parents" in a else {})}}
                                     if (a.get("properties") or "parents" in a) else {})},
                "edit": lambda a: {**_pick(a, "spec", "summary", "links", "class", "merged_into",
                                           "watch", "watch_ops", "watch_fields", "publish", "unpublish"),
                                   **({"properties": {**(a.get("properties") or {}),
                                                      **({"parents": a["parents"]} if "parents" in a else {})}}
                                      if (a.get("properties") or "parents" in a) else {})},
                "write": lambda a: {"spec": a.get("spec") or a.get("name"), "prop": a.get("prop"), "value": a.get("value")},
                "remove": lambda a: _pick(a, "spec", "force"),
                "inherit": lambda a: _pick(a, "spec", "parents"),
                "enter": lambda a: {"spec": a.get("spec") or a.get("name")},
                "exit": lambda a: {}},
    },
    "exec": {
        "description": "load/show/packs: .c1f packs (help('packs')). run: code as a field tree. "
                       "define/call/each/inspect: methods.",
        "actions": {"load": "pack", "show": "pack", "packs": "pack", "run": "code_run", "call": "method_call",
                    "each": "method_each", "define": "method_define", "inspect": "method_inspect"},
        "params": {"args": {"type": "object", "description": "{{param}} values"},
                   "name": _S("pack"), "c1f": _S("load: text"),
                   "code": _S("run: code"), "lang": _S("run: sh|js"), "node": _S("run: root"),
                   "object": _S("call: target"), "on": _S("define: owner; run: executor"),
                   "over": _S("each: class|pattern"), "max": _I("each: cap (20)"),
                   "dry_run": _B("each: list only"), "on_fail": _S("each: abort|continue"),
                   "method": _S("method name"), "command": _S("define: template"),
                   "read_only": _B("define: a read"), "guard": _S("define: guard"),
                   "abstract": _B("define: f(a,b), no command"),
                   "steps": {"type": "array", "items": {"type": "object"}, "description": "define: composite steps"}},
        "map": {"run": lambda a: _pick(a, "code", "lang", "node", "on"),
                "load": lambda a: {"action": "load", "name": a.get("name"), "c1f": a.get("c1f")},
                "show": lambda a: {"action": "show", "name": a.get("name")},
                "packs": lambda a: {"action": "list"},
                "call": lambda a: _pick(a, "object", "method", "args"),
                "inspect": lambda a: _pick(a, "object", "method", "args"),
                "each": lambda a: _pick(a, "over", "method", "args", "max", "dry_run", "on_fail"),
                "define": lambda a: _pick(a, "on", "method", "command", "read_only", "guard", "steps", "abstract")},
    },
    "goal": {
        "description": "Your commitments: plant one, list the board, close one with its "
                       "receipt (evidence). An empty board leaves the idle mind unsteered: "
                       "help('goals').",
        "actions": {"plant": "intend", "list": "intents", "close": "fulfill"},
        "params": {"text": _S("the goal"), "ttl": _S("e.g. '2w'"),
                   "spec": _S("goal or card id"), "evidence": _S("the receipt"),
                   "value": {}},
        "map": {"plant": lambda a: _pick(a, "text", "ttl"), "list": lambda a: {},
                "close": lambda a: _pick(a, "spec", "evidence"),
                "answer": lambda a: {"card": a.get("card") or a.get("spec"), "value": a.get("value"),
                                     "quote": a.get("quote") or a.get("text") or ""}},
    },
    "memory": {
        "description": "learn: keep one lesson you earned; forget: drop a memory (by #N or text).",
        "actions": {"learn": "learn", "forget": "forget"},
        "params": {"text": _S("learn: the lesson"), "spec": _S("forget: what to drop")},
        "map": {"learn": lambda a: {"lesson": a.get("text") or a.get("lesson") or a.get("learn")
                                           or a.get("summary")},
                "forget": lambda a: _pick(a, "spec")},
    },
    "track": {
        "description": "Entity tracks ride your header every turn: watch one (boundary "
                       "like 'enter 3'), unwatch, list.",
        "actions": {"watch": "entity_watch", "unwatch": "entity_unwatch", "list": "entity_tracks"},
        "params": {"key": _S("the entity"), "boundary": _S("optional: 'enter N' / 'exit N'"),
                   "label": _T("string"), "owner": _T("string")},
        "map": {"watch": lambda a: _pick(a, "key", "boundary", "label", "owner"),
                "unwatch": lambda a: _pick(a, "key"), "list": lambda a: _pick(a, "owner")},
    },
    "field_watch": {
        "description": "Spatial watches on the field (within your attention budget of 12): "
                       "plant / drop / list (the alert dial is the entity Attention:seat).",
        "actions": {"plant": "field_watch", "drop": "field_watch", "list": "field_watch"},
        "params": {"topic": _S("plant: what to watch"), "spec": _S("drop: which watch"),
                   "owner": _T("string")},
        "map": {"plant": lambda a: {"action": "plant", **_pick(a, "topic", "owner")},
                "drop": lambda a: {"action": "drop", **_pick(a, "spec")},
                "list": lambda a: {"action": "list"}},
    },
    "state": {
        "description": "Membrane's own vitals (the field, the gate, the night pass).",
        "actions": {"": "state"}, "params": {"section": _S("one part only: help('state')")},
        "map": {"": lambda a: _pick(a, "section")},
    },
    "peer": {
        "description": "Other seats: read what is unread, or list seats (there may be none). "
                       "Post by writing Peer:channel.post.",
        "actions": {"unread": "peer_read", "seats": "peer_seats"},
        "params": {},
        "map": {"unread": lambda a: {}, "seats": lambda a: {}},
    },
}

def _help_topics(est=None) -> list[str]:
    from . import help_pack
    return help_pack.listing(est)


def help_text(topic: str, est=None) -> str:
    """#1043: the topic's help entity (membrane::help:<topic>, this seat's edits included), else its packaged text."""
    from . import help_pack
    t = (topic or "").strip().lower()
    txt = help_pack.read(t, est) if t else None
    if txt:
        return txt
    return "help topics: " + ", ".join(_help_topics(est)) + " -- e.g. help(topic='packs')"


def _family_def(name: str, fam: dict, actions: list[str]) -> dict:
    props = {}
    if any(actions):
        props["action"] = {"type": "string", "enum": actions}
    props.update(fam["params"])
    return {"name": name, "description": fam["description"],
            "inputSchema": {"type": "object", "properties": props,
                            **({"required": ["action"]} if any(actions) else {})}}


def advertised(lane_tools) -> list[dict]:
    """The family definitions a lane sees (actions filtered to the lane)."""
    allowed = set(lane_tools or ())
    out = []
    for name, fam in FAMILIES.items():
        acts = [a for a, leg in fam["actions"].items() if leg in allowed and leg not in RETIRED]
        if acts:
            out.append(_family_def(name, fam, acts))
    out.append({"name": "help", "description": "The long-form docs (packs, exec, goals, watches, ...); help() lists every topic.",
                "inputSchema": {"type": "object", "properties": {"topic": _S("which topic")}}})
    return out


def _scripts_of(args: dict) -> dict:
    """`scripts` as a dict -- the model sends it as a JSON string too (the
    'str' object has no attribute 'items' crash, 2026-10-09)."""
    sc = args.get("scripts")
    if isinstance(sc, str):
        import json as _json
        try:
            sc = _json.loads(sc)
        except ValueError:
            sc = None
    return sc if isinstance(sc, dict) else {}


_LEGACY_FAMILY = {leg: fam for fam, f in FAMILIES.items() for leg in f["actions"].values()}
_LEGACY_FAMILY["bash"] = "exec"                  # Claude Code's and opencode's name for code_run


def bash_args(args: dict) -> dict:
    """bash {command, timeout ms, description} -> code_run {code, lang sh, timeout s}."""
    out = {"code": args.get("command", args.get("code")), "lang": "sh"}
    try:
        if args.get("timeout") not in (None, ""):
            out["timeout"] = float(args["timeout"]) / 1000.0
    except (TypeError, ValueError):
        pass
    return out


def _json_obj(v):
    """A params object the model sent as a JSON string."""
    if isinstance(v, dict):
        return v
    try:
        o = json.loads(v) if isinstance(v, str) and v.strip().startswith("{") else None
    except ValueError:
        o = None
    return o if isinstance(o, dict) else None


# the prefixes routed models put on tool names (2026-10-11: 'mem_exec',
# 'membrane_entity_add', 'm:tool:find' were refused as "not your lane's hand").
# 2026-10-14 (issue #24): the tongue slip 'membranes-exec' and the spaced
# 'membrane exec' were refused 11 more times -- same prefix, same class.
_NS = re.compile(r"^(?:(?:membranes?|mcp_tool|mcp|mem|functions|m:tool|tool)[-_.:/ ]+)+", re.I)
_FAM_ACT = re.compile(r"^([a-z_]+)[/.:](?:action[-_=:]?)?([a-z_]*)$", re.I)


_READ_NAMES = {"read", "read_file", "readfile", "cat", "view"}
_WRITE_NAMES = {"write", "write_file", "writefile", "create_file"}
_EDIT_NAMES = {"edit", "edit_file", "str_replace", "replace", "multiedit"}
_LIST_NAMES = {"glob", "ls", "list_dir", "list_files", "listdir", "find_files"}


def _usual_tool(name: str, args: dict):
    """#1101: with no shell (MEMBRANE_CLIFFHANGER_ONLY) the usual tools' names reach the field's file doors."""
    from . import cliffhanger_only as _CO
    if not _CO.on():
        return None
    n = str(name or "").lower()
    path = args.get("path") or args.get("file_path") or args.get("file") or args.get("filename")
    if n in _READ_NAMES:
        return "fs_read", {"path": path, **({"offset": args["offset"]} if args.get("offset") else {}),
                           **({"limit": args["limit"]} if args.get("limit") else {})}
    if n in _WRITE_NAMES:
        return "fs_write", {"path": path, "content": args.get("content") if args.get("content") is not None
                            else args.get("text", "")}
    if n in _EDIT_NAMES:
        return "fs_edit", {"path": path, "old": args.get("old_string", args.get("old")),
                           "new": args.get("new_string", args.get("new")),
                           "all": bool(args.get("replace_all"))}
    if n in _LIST_NAMES:
        return "fs_list", {"path": path or args.get("dir") or ".", "pattern": args.get("pattern")}
    return None


def _known_tool(name: str) -> bool:
    if name in _LEGACY_FAMILY or name == "help" or name.startswith(("__", "fs_", "ctx_")):
        return True
    try:
        from .mcp_server import TOOL_IMPLS
        return name in TOOL_IMPLS
    except Exception:                 # noqa: BLE001 -- unknown stays unknown
        return True


def canonical(name: str, args: dict) -> tuple[str, dict]:
    """THE NAMES THE MODEL INVENTS (2026-10-10, the operator: "why are
    entity/action-read and exec/action-link not the operator's lane?"): the
    seat called 'entity/action-read', 'membrane-entity_read' -- names no lane
    lists, so even its READS were refused. A namespace prefix drops; a
    'family/action-x' (or family.x, family:x) is the family's call."""
    # ISSUE #60 (Memo): a route string reused as a tool name kept its leading
    # slash -- '/exec' on a headless seat's watch pulse, refused though exec is mounted
    name = _NS.sub("", str(name or "").strip().lstrip("/").strip())
    alias = _usual_tool(name, args or {})          # #1101: Read/Write/Edit/Glob/LS on a no-bash seat
    if alias is not None:
        return alias
    if name.lower() == "bash":                   # the shell tool's familiar name is code_run
        return "code_run", bash_args(args or {})
    m = _FAM_ACT.match(name)
    if m and m.group(1).lower() in FAMILIES:
        fam, act = m.group(1).lower(), m.group(2).lower()
        if act in FAMILIES[fam]["actions"]:
            return fam, {**(args or {}), "action": act}
        # THE ACTION IN THE SEAM (2026-10-14, issue #24: 'entity/action' --
        # the family named, the action lost): the family's call; with no
        # action of its own the family's honest refusal names the actions
        if not act:
            return fam, {**(args or {}), "action": ""}
    return name, args


def mounted_name(name: str) -> bool:
    """Whether a tool name is a mounted tool: a family, a legacy member, or
    a dashed spelling of one. The live capture uses it as the gate that
    keeps a REFUSED/unmounted call (the model's invented `f_1`..`f_4`) from
    minting a `tool:` field entity -- turn noise with no world referent
    (issue #110, the same class as #101's stat-at-mint)."""
    n = canonical(name or "", {})[0]
    if n in FAMILIES or n in _LEGACY_FAMILY:
        return True
    if n.replace("-", "_") in _LEGACY_FAMILY:
        return True
    return n == "help"


_OBJECT_PARAMS = ("properties", "args", "scripts", "params", "watch_ops", "watch_fields", "parents", "links")


def _json_objects(args: dict) -> dict:
    """THE OBJECT SENT AS A STRING (2026-10-11: voice-out "never spoke" --
    properties='{"say": "..."}' arrived as a string, the edit said 'updated'
    and set nothing; args='{"code": ...}' made presence.exec{} a shell line):
    an object parameter given as JSON text is parsed."""
    import json as _json
    for k in _OBJECT_PARAMS:
        v = args.get(k)
        if isinstance(v, str) and v.strip()[:1] in ("{", "["):
            try:
                args[k] = _json.loads(v)
            except ValueError:
                pass
    return args


def _unwrap_args(name: str, args: dict) -> dict:
    """THE CALL WRAPPED IN `args` (issue #160: entity_edit {"args": "{\"spec\":
    ..., \"summary\": ...}"} answered 'updated' echoing the OLD summary, and
    entity_read {"args": "{..., \"run\": true}"} stayed 'deferred'): the spec
    was dug out of the wrapper downstream (mcp_server._arg), every other
    field was not -- the edit set nothing, jobs.dispatch never saw run. For a
    tool whose family takes no `args` parameter, the wrapped object IS the
    call: its fields join the arguments (a field sent beside it wins)."""
    inner = args.get("args")
    if not isinstance(inner, dict):
        return args
    fam = FAMILIES.get(name) or FAMILIES.get(_LEGACY_FAMILY.get(name) or "")
    if fam is None or "args" in fam["params"]:
        return args                               # exec's args are its {{param}} values
    outer = {k: v for k, v in args.items() if k != "args"}
    return _json_objects({**inner, **outer})


def _call_in_name(name: str) -> tuple[str, dict] | None:
    """THE WHOLE CALL IN THE NAME SLOT (2026-10-15, serve-msgs: 70 live calls
    arrived with the entire call JSON riding the tool-name field -- the
    streamed assembly split the deltas badly, all 70 on reasoning turns --
    and the arguments slot empty; every one was refused, so the model sent
    70 calls to nothing): a name whose JSON carries the call is un-strangled
    into it -- the no_name_1 unwrap's doctrine, extended. Nothing decodable,
    nameless and actionless, ambiguous: NOT this shape (the honest refusal
    keeps its why; never a guess). Returns (real_name, real_args)."""
    s = str(name or "").strip()
    if not s[:1] in {"{", "["}:
        return None
    try:
        d = json.loads(s)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if "parameters" in d or "arguments" in d:
        # the WRAPPED spelling (2026-10-15, the 70 live calls): the call's
        # args were separated, in "parameters" (older OpenAI twin spelling:
        # "arguments")
        inner = d.get("parameters") or d.get("arguments")
        if isinstance(inner, str):
            inner = _json_obj(inner)
        if not isinstance(inner, dict):
            return None
        inner_name = str(d.get("name") or "")
        if not inner_name:
            owner = _owned_by_one_family(inner)
            return (owner, dict(inner)) if owner else None
        return inner_name, dict(inner)
    # the FLAT spelling (2026-10-11's '{"action": "read", "name": ...}' in the
    # name slot, six refusals): the WHOLE object is the args; the one owner
    # of the action names the family -- a "name" field rides as a PARAM
    # (entity add needs one exactly like that)
    owner = _owned_by_one_family(d)
    if owner:
        return owner, dict(d)
    # a LONE name -- {"name": "exec"} and nothing else -- is a bare tool
    # selection: the family's own refusal lists its actions
    if set(d) == {"name"} and str(d.get("name") or ""):
        return str(d["name"]), {}
    return None


def _call_in_args(args: dict) -> dict | None:
    """THE LOST NAME WITH A WRAPPED CALL (shape 2, 2026-10-15: 9 live calls
    rode '"tool": ""' with the call's JSON wrapped as a string in the
    arguments -- {"args": "<json>"}): unwrap the first decodable string that
    holds an object, or None."""
    for k in ("args", "input", "arguments", "parameters", "args_json"):
        o = _json_obj((args or {}).get(k))
        if isinstance(o, dict):
            return o
    return None


# ISSUE #182: a chat template's own example spelling in the name slot --
# '{function_name}', '{toolName}', '{{name}}', '<function-name>' -- the model
# copied the template's placeholder instead of naming a tool
_PLACEHOLDER = re.compile(r"\{\{?\s*[a-z_][\w\- ]*\s*\}?\}|<\s*[a-z_][\w\-]*\s*>", re.I)
# where a placeholder call carries the name it meant ("name" is a real param
# of entity add, so it names the call only beside wrapped arguments)
_NAME_KEYS = ("fn_name", "function_name", "tool_name", "function", "tool")


def is_tool_name(name) -> bool:
    """A name the manifest may serve: a plain identifier, never a template
    placeholder or a JSON fragment (issue #182)."""
    return bool(re.fullmatch(r"[A-Za-z_][\w.\-]*", str(name or "")))


def _owned_by_one_family(inner: dict) -> str:
    act = str(inner.get("action") or "")
    owners = [f for f, fd in FAMILIES.items() if act and act in fd["actions"]]
    return owners[0] if len(owners) == 1 else ""


def _closest_family(args: dict) -> str:
    """The family whose parameters share the most keys with a nameless call's
    arguments (issue #274: '' with {"focus": "self"} was refused seven times,
    no hint where to go); `state` -- how things are, no arguments -- when none
    of them fits any tool."""
    keys = {k for k in args if k != "action"}
    scored = [(len(keys & set(d["params"])), f) for f, d in FAMILIES.items()]
    best = max(scored, key=lambda t: t[0])[0] if scored else 0
    hits = [f for n, f in scored if n == best]
    return hits[0] if best and len(hits) == 1 else "state"


_PLACEHOLDER_NAME_KEYS = ("fn_name", "function_name", "tool_name", "name", "tool", "function")
_PLACEHOLDER_ARG_KEYS = ("args_json", "arguments", "args", "params", "parameters", "input")


def _placeholder_call(placeholder: str, args: dict) -> tuple[str, dict]:
    """A call whose name is a template placeholder: the tool is named by one of
    its fields (a family), its arguments by another (JSON);
    else the honest refusal with a filled-in example."""
    named = next((str(args[k]).strip() for k in _PLACEHOLDER_NAME_KEYS
                  if isinstance(args.get(k), str) and args[k].strip()), "")
    raw = next((args[k] for k in _PLACEHOLDER_ARG_KEYS if k in args), None)
    inner = None
    if isinstance(raw, dict):
        inner = raw
    elif isinstance(raw, str) and raw.strip()[:1] == "{":
        try:
            inner = json.loads(raw)
        except ValueError:
            inner = None
    head, _, rest = named.partition(" ")
    fam = canonical(head, {})[0] if head else ""
    if fam in FAMILIES:
        if inner is not None:
            return resolve(fam, dict(inner))
        text = rest.strip() or (raw.strip() if isinstance(raw, str) else "")
        if not text:
            return resolve(fam, {})
    raise ValueError(
        f"the call's name {placeholder!r} is a template placeholder from your call format, not a tool: "
        f"write the real tool's name in its place -- one of {', '.join(sorted(FAMILIES))} -- and its "
        "arguments as JSON. For example, to call a method: name exec, arguments "
        '{"action": "call", "object": "presence", "method": "status"}; to read an entity: name entity, arguments '
        '{"action": "read", "name": "presence"}')


def resolve(name: str, args: dict) -> tuple[str, dict]:
    """(existing tool name, its args) for a family call; an existing tool's
    own name passes through; ValueError names the valid actions."""
    # THE ARGUMENTS IN THE NAME (2026-10-11: a loop sent '{"action":' as the
    # tool's name six times and was told 'not your lane's hand' -- the wrong
    # lesson): say what happened and how to send it
    _bare = str(name or "").strip().strip("\"'` ?")
    # ISSUE #74: 80 calls on one seat carried the tool name '{}' -- an EMPTY
    # JSON object, no arguments in it -- with the whole call intact beside it;
    # every one bounced as 'the NAME held its arguments'. An empty-JSON name is
    # no name: the wrapper's own family routes it, else the action's one owner.
    if re.fullmatch(r"\{\s*\}|\[\s*\]|null|none|undefined", _bare, re.I):
        _bare = ""
    # ISSUE #182: after seat restarts every call came named '{function_name}'
    # -- the chat template's example, not the call's arguments -- and 13/13
    # bounced as 'the NAME held its arguments'. A placeholder is no name: the
    # call routes by what rides beside it, else the refusal says so.
    _placeholder = _bare if _PLACEHOLDER.fullmatch(_bare) else ""
    if _placeholder:
        _bare = ""
    if not _bare:
        # its arguments may have arrived wrapped as a string: acknowledged,
        # echoed verbatim, so the resend carries them intact
        _inner = _call_in_args(args)
        if _inner is not None:
            _outer = next((str(v).strip() for k in ("name",) + _NAME_KEYS
                           if isinstance(v := (args or {}).get(k), str) and v.strip()), "")
            if _outer and canonical(_outer, {})[0] in FAMILIES:
                return resolve(_outer, dict(_inner))
            _owner = _owned_by_one_family(_inner)
            if _owner:
                return resolve(_owner, dict(_inner))
            raise ValueError(
                f"the call's name was LOST but its arguments arrived "
                f"({json.dumps(_inner)[:160]}): resend naming the tool -- one of "
                f"{', '.join(sorted(FAMILIES))} -- with the same arguments")
        # the family NAMED beside a lost name (issue #182: {"tool_name":
        # "exec", "action": "link", ...}) is the call's; its other keys are
        # the arguments
        _named = next((str(v).strip() for k in _NAME_KEYS
                       if isinstance(v := (args or {}).get(k), str)
                       and canonical(v.strip(), {})[0] in FAMILIES), "")
        if _named:
            return resolve(_named, {k: v for k, v in (args or {}).items() if k not in _NAME_KEYS})
        # a whole call beside a lost name routes by its action's one owner --
        # a bare {"action": ...} with nothing to act on still asks for the name
        _owner = _owned_by_one_family(dict(args or {}))
        if _owner and any(k != "action" for k in (args or {})):
            return resolve(_owner, dict(args or {}))
        if _placeholder:
            # nothing above named the call: a family in a name field,
            # the arguments as a JSON string, else the refusal with an example
            return _placeholder_call(_placeholder, dict(args or {}))
        raise ValueError(
            f"the call had no tool name ({str(name or '')!r}): name the tool -- one of "
            f"{', '.join(sorted(FAMILIES))} -- and put its JSON in the arguments "
            f"(closest: {_closest_family(dict(args or {}))})")
    if str(name or "").lstrip()[:1] in ("{", "["):
        _un = _call_in_name(name)
        if _un is not None:
            return resolve(_un[0], _un[1])
        raise ValueError(
            f"the call's NAME held its arguments ({str(name)[:80]!r}): put the tool's name -- "
            f"one of {', '.join(sorted(FAMILIES))} -- in the name, and that JSON in the arguments, "
            "e.g. name='entity', arguments={\"action\": \"read\", \"name\": \"presence\"}")
    name, args = canonical(name, args)
    args = _unwrap_args(name, _json_objects(dict(args or {})))
    # THE NAMELESS CALL (2026-10-12, a seat inside Home Assistant: the backend's
    # parser lost the tool's name -- 'no_name_1' -- and wrapped the arguments as
    # {"input": "<json>"}; told "no tool is named 'no_name_1'", the model sent it
    # 14 more times): unwrap it, and an action only ONE family has names the call.
    # 2026-10-14 (issue #24): opencode's wire lost the name the same way as
    # 'functions._name_0' -- the same artifact, another spelling.
    if re.fullmatch(r"(?:no_)?_?name(_\d+)?", str(name or "")):
        inner = args
        if set(args) == {"input"} and isinstance(args.get("input"), str):
            try:
                inner = _json_objects(dict(json.loads(args["input"]) or {}))
            except (ValueError, TypeError):
                inner = args
        act = str(inner.get("action") or "")
        owners = [f for f, d in FAMILIES.items() if act and act in d["actions"]]
        if len(owners) == 1:
            return resolve(owners[0], inner)
        raise ValueError(
            "the call arrived WITHOUT its tool name (the model server lost it): send it again "
            f"naming the tool -- one of {', '.join(sorted(FAMILIES))} -- with the same arguments")
    fam = FAMILIES.get(name)
    if fam is None:
        # THE DASHED SPELLING (2026-10-14, issue #24: the card invited the
        # typed names and the wire refused 112 calls across 63 invented
        # spellings -- 'entity-read' is the card's entity_read): a dashed
        # legacy name is the typed tool it meant, its arguments untouched.
        sugared = str(name).replace("-", "_")
        if sugared != name and sugared in _LEGACY_FAMILY:
            return sugared, args
        # an OLD member's name carrying a family action (entity_link +
        # action='each' -- the seat's habit, 2026-10-10) is the family's call
        owner = _LEGACY_FAMILY.get(name)
        act = str(args.get("action") or "")
        if owner and act and act in FAMILIES[owner]["actions"] and FAMILIES[owner]["actions"][act] != name:
            return resolve(owner, args)
        if name == "entity_edit" and "parents" in args:
            # the old name drops unknown fields: parents ride the properties patch
            # (the order-replacing path) -- the probe's swap died here twice
            args["properties"] = {**(args.get("properties") or {}), "parents": args.pop("parents")}
        if name == "entity_edit" and "spec" not in args and args.get("name"):
            args["spec"] = args["name"]
        if name == "entity_read" and "spec" not in args:
            for alias in ("key", "name", "object", "eid"):
                if args.get(alias):
                    args["spec"] = args.pop(alias)
                    break
        if name in ("entity_edit", "entity_add") and "scripts" in args:
            # the old names take the scripts object too (it was silently dropped)
            for kind, code in _scripts_of(args).items():
                if kind in SCRIPT_KINDS:
                    args[f"{kind}_script"] = code
            args.pop("scripts", None)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)) and not _known_tool(name):
            # #1101 __call: a name no tool has is a cliffhanger method of that name, its arguments the params,
            # resolved through the context stack (callctx) -- never created
            return "__call", {"method": name, "args": {k: v for k, v in args.items() if k != "action"}}
        return name, args
    action = str(args.pop("action", "") or "")
    if action not in fam["actions"]:
        if "" in fam["actions"] and not action:
            action = ""
        else:
            # THE BARE ACTION FAMILY (issue #2: 'state' has exactly one
            # action, the empty one -- its listing printed bare 'actions: ',
            # undiscoverable): say how the empty action is called
            acts = [a for a in fam["actions"] if a]
            listing = ", ".join(acts) if acts else \
                f"(none -- {name} takes no action, call it bare)"
            raise ValueError(f"{name}: unknown action {action!r}; actions: {listing}")
    legacy = fam["actions"][action]
    mapped = {k: v for k, v in fam["map"][action](args).items() if v is not None}
    if name == "entity" and action in ("edit", "add"):
        for kind, code in _scripts_of(args).items():
            if kind in SCRIPT_KINDS:
                mapped[f"{kind}_script"] = code
        for k, v in args.items():
            if k.endswith("_script"):
                mapped[k] = v
    return legacy, mapped


def legacy_names(defs) -> set[str]:
    """The existing tools a list of advertised definitions reaches."""
    out = set()
    for d in defs:
        fam = FAMILIES.get(d["name"])
        if fam is None:
            out.add(d["name"])
            continue
        acts = d["inputSchema"]["properties"].get("action", {}).get("enum", [""])
        out.update(fam["actions"][a] for a in acts)
    return out
