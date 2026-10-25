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
                       "run=true fires its getter), add, edit, remove, inherit (ISA parents). "
                       "Scripts: help('scripts').",
        "actions": {"read": "entity_read", "add": "entity_add", "edit": "entity_edit",
                    "remove": "entity_remove", "inherit": "entity_inherit"},
        "params": {"spec": _S("the entity (eid or name)"), "name": _S("add: its name"),
                   "class": _S("add/edit: its class"), "summary": _S("what it is"),
                   "links": {"type": "array", "items": {"type": "string"}, "description": "related entities"},
                   "run": _B("read: fire the getter now"),
                   "force": _B("remove: even a live method"),
                   "parents": {"type": "array", "items": {"type": "string"}, "description": "inherit: ISA parents"},
                   "scripts": {"type": "object", "description": "kind -> python: " + ", ".join(SCRIPT_KINDS)},
                   "properties": {"type": "object", "description": "edit: properties to set"},
                   "watch_ops": {"type": "array", "items": {"type": "object"}, "description": "edit: geometric watches (help('watches'))"},
                   "watch_fields": {"type": "array", "items": {"type": "object"}, "description": "edit: own-motion watches"},
                   "merged_into": _S("edit: merge into this entity")},
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
                "remove": lambda a: _pick(a, "spec", "force"),
                "inherit": lambda a: _pick(a, "spec", "parents")},
    },
    "exec": {
        "description": "Cliffhanger: define methods on classes, call or inspect them on "
                       "objects; link runs a command. help('cliffhanger').",
        "actions": {"call": "method_call", "each": "method_each", "define": "method_define",
                    "inspect": "method_inspect",
                    "link": "entity_link", "unlink": "entity_unlink"},
        "params": {"commands": {"type": ["string", "array"], "description": "link: command line(s)"},
                   "args": {"type": "object", "description": "{{param}} values"},
                   "object": _S("call: target"), "on": _S("define: owner"),
                   "over": _S("each: class or pattern"), "max": _I("each: cap (20)"),
                   "dry_run": _B("each: list only"), "on_fail": _S("each: abort|continue"),
                   "method": _S("method name"), "command": _S("define: command template"),
                   "read_only": _B("define: a read"), "guard": _S("define: guard"),
                   "abstract": _B("define: f(a,b), no command"),
                   "steps": {"type": "array", "items": {"type": "object"}, "description": "define: composite steps"},
                   "executor": _T("string"), "create": _T("boolean")},
        "map": {"call": lambda a: _pick(a, "object", "method", "args"),
                "inspect": lambda a: _pick(a, "object", "method", "args"),
                "each": lambda a: _pick(a, "over", "method", "args", "max", "dry_run", "on_fail"),
                "define": lambda a: _pick(a, "on", "method", "command", "read_only", "guard", "steps", "abstract"),
                "link": lambda a: _pick({**a, "commands": a.get("commands") or a.get("command"),
                                         "args": a.get("args") or _json_obj(a.get("params"))},
                                        "commands", "executor", "create", "args"),
                "unlink": lambda a: _pick(a, "commands", "executor")},
    },
    "goal": {
        "description": "Your commitments: plant one, list the board, close one with its "
                       "receipt (evidence). An empty board leaves the idle mind unsteered: "
                       "help('goals').",
        "actions": {"plant": "intend", "list": "intents", "close": "fulfill"},
        "params": {"text": _S("plant: the goal"), "ttl": _S("plant: optional, e.g. '2w'"),
                   "spec": _S("close: the goal's id"), "evidence": _S("close: the receipt")},
        "map": {"plant": lambda a: _pick(a, "text", "ttl"), "list": lambda a: {},
                "close": lambda a: _pick(a, "spec", "evidence")},
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
                       "plant / drop / list; dial sets how many watch events may interrupt you.",
        "actions": {"plant": "field_watch", "drop": "field_watch", "list": "field_watch",
                    "dial": "attention"},
        "params": {"topic": _S("plant: what to watch"), "spec": _S("drop: which watch"),
                   "owner": _T("string"), "budget": _I("dial: interrupts per window"),
                   "window_min": _I("dial: window minutes"), "mode": _S("dial: interrupt | digest")},
        "map": {"plant": lambda a: {"action": "plant", **_pick(a, "topic", "owner")},
                "drop": lambda a: {"action": "drop", **_pick(a, "spec")},
                "list": lambda a: {"action": "list"},
                "dial": lambda a: {"action": "set" if any(k in a for k in ("budget", "window_min", "mode")) else "get",
                                   **_pick(a, "budget", "window_min", "mode")}},
    },
    "state": {
        "description": "Membrane's own vitals (the field, the gate, the night pass).",
        "actions": {"": "state"}, "params": {"section": _S("one part only: help('state')")},
        "map": {"": lambda a: _pick(a, "section")},
    },
    "peer": {
        "description": "Other seats: post to the peer channel, read what is unread, or list "
                       "seats (there may be none).",
        "actions": {"post": "peer_post", "unread": "peer_read", "seats": "peer_seats"},
        "params": {"text": _S("post: the message"), "reply_to": _T("string")},
        "map": {"post": lambda a: _pick(a, "text", "reply_to"), "unread": lambda a: {},
                "seats": lambda a: {}},
    },
}

HELP = {
    "scripts": (
        "Entity scripts are python, set with entity(action='edit', scripts={kind: code}). Kinds: "
        "getter (runs on a read; set a TOP-LEVEL result = {'state': {...live values...}}, also "
        "'render' for the banner and 'patch' = {script_data, position} only (other keys are refused, "
        "noted as patch_refused) -- a function's return or stdout is "
        "not read; run=true forces a read, else a 5s LRU paces it; 10s budget), tick (periodic), "
        "nudge, mention (on each mention), thought (when the thinker concludes about it), dreamed "
        "(when a dream names it), delete, watch (on external events). "
        "Inside a script, `membrane` reads: membrane.entities.get(spec, run=False) (a read FIRES the "
        "target's getter -- a tick that reads an entity keeps it live; run=True runs it now), "
        "membrane.timeline.walk(n), membrane.field.recall/vitals, membrane.me, membrane.intents.open, "
        "membrane.speak(text), membrane.call_llm(prompt), membrane.wake(at, prompt). "
        "Rewriting a script clears its cooldowns. A real cooldown says 'retry in Ns'."),
    "exec": (
        "exec(action='link', commands='...') runs a command now (dash shell on this seat: use '.' "
        "not 'source') and returns rc + output; the command becomes an entity (stable name; "
        "'label :: cmd' names it). COMPOSITION IS INHERITANCE: entity(action='inherit', "
        "spec=cmd, parents=[...]) makes the parents run first; their output feeds {{parent.head}} / "
        "{{parent.tail}}. A failed step halts the pipeline. THE EDGE IS THE QUEUE: a link stays owed "
        "until its run lands (success or failure); there is no auto-retry -- re-link to re-run. "
        "entity(action='read') on a command shows its last run (rc/head/tail); on the executor, "
        "pending/done. Caps: 32 owed links, 12 steps a pass. REUSE: a command you keep is a "
        "tool -- name it ('light-on :: curl ... {{entity}} ...'), give it {{name}} parameters, "
        "re-run it with exec(action='link', commands='light-on', args={'entity': 'switch.x'}); "
        "the same command line re-runs its existing entity. Your named commands ride your "
        "context ('your commands: ...')."),
    "goals": (
        "goal(action='plant') makes a commitment; the watch loops take it up. A loop ends each pass "
        "with one line: DONE <receipt> / STEP <what moved> / BLOCKED <on whom, or 'on <entity>', or "
        "'retry after 2h'> / ignored: <reason>. Close with goal(action='close', spec=<id>, "
        "evidence=<receipt>) -- no receipt, no close. An EMPTY board leaves membrane's dream loop and "
        "think-ahead unsteered (nothing guides the idle mind's branches): when your work has a "
        "direction worth keeping across sessions, plant it; plant only when it serves the work."),
    "state": (
        "state() is membrane's vitals plus the seat's whole WHERE WE ARE. state(section=...) pulls "
        "one part: tracks (the watched entities' stories), commands (your most-run, with counts), "
        "interfaces, classes (class proposals), loops (the thinking loops), timelines (open "
        "branches), frame (the watch frame), digest (watch events past your alert budget), "
        "changes (what moved since your last message). Other seats: peer(action='seats' / "
        "'unread')."),
    "watches": (
        "Three ways to watch, one budget of 12: track (header lines, entity_watch boundaries), "
        "field_watch (spatial: new memories near a topic), and an entity's watch_ops "
        "({op: near|away|around, rel: <entity>|self, z|y: distance}) / watch_fields "
        "({verb: pos|vel|align, arg}). At the limit membrane refuses with the inventory: drop what "
        "you no longer need."),
    "voice": (
        "Membrane's own voice learns from your 'It meant:' lessons (x2) and Dmitrii's rewrites. A "
        "mistake that is GENERAL belongs in a VOICE RULE, which fixes every future sentence: "
        "entity(action='add', name='voice rule: <what>', summary=...), then "
        "entity(action='edit', spec=<it>, properties={'voice_rule': \"result = {'ok': <a python test "
        "on state['text']>}\"}) -- e.g. {'ok': not state['text'].rstrip().endswith((',', ':'))}. "
        "Put the rule on a CLASS (an entity others inherit from) and it applies only to sentences "
        "about that class's members. A sentence that breaks a rule is never said. Precedence: a "
        "member's own rule, then its classes nearest-first, then global rules; the first veto wins; "
        "every veto is logged with its sentence."),
    "cliffhanger": (
        "Cliffhanger is membrane as an OOP language: entities are objects, ISA parents are classes, "
        "METHODS are commands attached to a class or object, a CALL is an event on an object. "
        "Define once: exec(action='define', on='ha light', method='turn_on', command='. "
        "~/.config/membrane/hass.env; curl -s -X POST -H \"Authorization: Bearer $HASS_TOKEN\" -d "
        "\"{\\\"entity_id\\\": \\\"{{self.entity_id}}\\\"}\" $HASS_URL/api/services/light/turn_on'). Call it on any "
        "member: exec(action='call', object='ha:light.port_strip', method='turn_on'). {{self.<prop>}} "
        "fills from the object (its own values over its classes'); other {{params}} come from args -- "
        "ONLY {{name}} binds ({name}, $name, $1 do not): an arg the command never names is refused, a "
        "positional list args=[a, b] binds to the {{params}} in order, and args.timeout (seconds) only "
        "ever shortens the run. INSPECT (runs nothing): exec(action='inspect', object=..., "
        "method=..., args=...) shows where the call resolves (via, the resolution order, who defines "
        "it along it), the stored command, params and steps, and with args the exact command it "
        "WOULD run or the refusal it WOULD give -- look before you redefine a broken method. "
        "Dispatch: the object's own method, then its classes (ISA order), then global methods -- "
        "the same name on another class is its own implementation. The receipt lands on the "
        "object. Compose methods with parents (help('exec')). A missing or empty value BLOCKS the "
        "call (nothing runs); outcomes: ok | blocked | error | timeout | partial. The receipt shows "
        "which implementation answered (via), the resolution order and the exact command that ran. "
        "Mark reads read_only=true. GUARDS: define(..., guard=\"result = {'ok': state['self'].get('reachable') "
        "== 'yes'}\") runs first; false = blocked, nothing runs. COMPOSITES: define(..., steps=[{'name': "
        "'soc', 'call': 'read', 'object': 'seat:battery'}, {'name': 'lights', 'call': 'turn_off', 'object': "
        "'self'}]) -- steps run in order, stop on failure (partial if earlier ones ran), nest at most 3 "
        "deep; {{steps.soc.state.soc}} reads a step's captured result (JSON output becomes .state), "
        "{{entities.<spec>.<field>}} reads an entity now. Every node is an entity you can read. "
        "FAN-OUT: exec(action='each', over='ha light', method='status', dry_run=true) lists who would "
        "be hit; without dry_run it runs on every member (cap max=20, raise it explicitly; ceiling 150), "
        "on_fail='abort' (default) | 'continue', one summary receipt; as a step: {'name': 'all', "
        "'each': 'ha light', 'call': 'turn_off'}. INTERFACES: exec(action='define', on='remote_executor', "
        "method='exec(code, timeout)', abstract=true) declares a required method (no command) and makes "
        "its owner an interface; an entity that inherits it must implement each one (own or a class "
        "between) -- until then the call is blocked with what is missing, and inherit says what it owes. "
        "remote_executor (exec(code, timeout)) is the first: an environment that runs code; give each "
        "implementation lang / env / trust properties."),
    "classes": (
        "Inheritance: an entity without its own getter/mention/thought/dreamed script runs its "
        "nearest class's, ON ITSELF (state['eid'], its own properties merged over its classes' "
        "defaults -- the member's own values win). Resolution: parents in declared order, all "
        "parents before any grandparent; first found wins. Ticks inherit only where the class sets "
        "tick_inherits: true, staggered across members. inherit_disabled: true on a class stops it "
        "for all members at once. Classify a family in one call: entity(action='inherit', "
        "spec='ha:light.*', parents=['ha light']). New members are never adopted automatically."),
    "lanes": (
        "Your chat holds every hand. Watch loops hold the same but never plant goals. A phone call "
        "reads and answers (no exec or entity writes; plant a caller's request as a goal). Plan "
        "mode only reads."),
}


def help_text(topic: str) -> str:
    t = (topic or "").strip().lower()
    if t in HELP:
        return HELP[t]
    return "help topics: " + ", ".join(sorted(HELP)) + " -- e.g. help(topic='scripts')"


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
    out.append({"name": "help", "description": "The long-form docs: " + ", ".join(sorted(HELP)) + ".",
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


def canonical(name: str, args: dict) -> tuple[str, dict]:
    """THE NAMES THE MODEL INVENTS (2026-10-10, the operator: "why are
    entity/action-read and exec/action-link not the operator's lane?"): the
    seat called 'entity/action-read', 'membrane-entity_read' -- names no lane
    lists, so even its READS were refused. A namespace prefix drops; a
    'family/action-x' (or family.x, family:x) is the family's call."""
    # ISSUE #60 (Memo): a route string reused as a tool name kept its leading
    # slash -- '/exec' on a headless seat's watch pulse, refused though exec is mounted
    name = _NS.sub("", str(name or "").strip().lstrip("/").strip())
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
    its fields (a family, or 'exec <command>'), its arguments by another (JSON);
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
        if fam == "exec" and text:
            return resolve("exec", {"action": "link", "command": text})
        if not text:
            return resolve(fam, {})
    raise ValueError(
        f"the call's name {placeholder!r} is a template placeholder from your call format, not a tool: "
        f"write the real tool's name in its place -- one of {', '.join(sorted(FAMILIES))} -- and its "
        "arguments as JSON. For example, to run a command: name exec, arguments "
        '{"action": "link", "command": "ls -a"}; to read an entity: name entity, arguments '
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
            # nothing above named the call: 'exec <command>' in a name field,
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
