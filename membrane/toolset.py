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
                "add": lambda a: _pick(a, "class", "name", "summary", "links"),
                "edit": lambda a: {**_pick(a, "spec", "summary", "links", "class", "merged_into",
                                           "watch", "watch_ops", "watch_fields", "publish", "unpublish"),
                                   **({"properties": {**(a.get("properties") or {}),
                                                      **({"parents": a["parents"]} if "parents" in a else {})}}
                                      if (a.get("properties") or "parents" in a) else {})},
                "remove": lambda a: _pick(a, "spec", "force"),
                "inherit": lambda a: _pick(a, "spec", "parents")},
    },
    "exec": {
        "description": "Cliffhanger: define methods on classes (abstract = interface), call "
                       "them on objects; link runs a raw command. help('cliffhanger').",
        "actions": {"call": "method_call", "each": "method_each", "define": "method_define",
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
                   "executor": _S("optional"), "create": _B("optional")},
        "map": {"call": lambda a: _pick(a, "object", "method", "args"),
                "each": lambda a: _pick(a, "over", "method", "args", "max", "dry_run", "on_fail"),
                "define": lambda a: _pick(a, "on", "method", "command", "read_only", "guard", "steps", "abstract"),
                "link": lambda a: _pick({**a, "commands": a.get("commands") or a.get("command"),
                                         "args": a.get("args") or _json_obj(a.get("params"))},
                                        "commands", "executor", "create", "args"),
                "unlink": lambda a: _pick(a, "commands", "executor")},
    },
    "goal": {
        "description": "Your commitments: plant one, list the board, close one with its "
                       "receipt (evidence). Loops: help('goals').",
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
                   "label": _S("optional label"), "owner": _S("optional owner")},
        "map": {"watch": lambda a: _pick(a, "key", "boundary", "label", "owner"),
                "unwatch": lambda a: _pick(a, "key"), "list": lambda a: _pick(a, "owner")},
    },
    "field_watch": {
        "description": "Spatial watches on the field (within your attention budget of 12): "
                       "plant / drop / list; dial sets how many watch events may interrupt you.",
        "actions": {"plant": "field_watch", "drop": "field_watch", "list": "field_watch",
                    "dial": "attention"},
        "params": {"topic": _S("plant: what to watch"), "spec": _S("drop: which watch"),
                   "owner": _S("optional owner"), "budget": _I("dial: interrupts per window"),
                   "window_min": _I("dial: window minutes"), "mode": _S("dial: interrupt | digest")},
        "map": {"plant": lambda a: {"action": "plant", **_pick(a, "topic", "owner")},
                "drop": lambda a: {"action": "drop", **_pick(a, "spec")},
                "list": lambda a: {"action": "list"},
                "dial": lambda a: {"action": "set" if any(k in a for k in ("budget", "window_min", "mode")) else "get",
                                   **_pick(a, "budget", "window_min", "mode")}},
    },
    "state": {
        "description": "Membrane's own vitals (the field, the gate, the night pass).",
        "actions": {"": "state"}, "params": {}, "map": {"": lambda a: {}},
    },
    "peer": {
        "description": "Other seats: post to the peer channel, or list seats (there may be none).",
        "actions": {"post": "peer_post", "seats": "peer_seats"},
        "params": {"text": _S("post: the message"), "reply_to": _S("optional")},
        "map": {"post": lambda a: _pick(a, "text", "reply_to"), "seats": lambda a: {}},
    },
}

HELP = {
    "scripts": (
        "Entity scripts are python, set with entity(action='edit', scripts={kind: code}). Kinds: "
        "getter (runs on a read; set a TOP-LEVEL result = {'state': {...live values...}}, also "
        "'render' for the banner and 'patch' to edit properties -- a function's return or stdout is "
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
        "evidence=<receipt>) -- no receipt, no close."),
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
        "fills from the object (its own values over its classes'); other {{params}} come from args. "
        "Dispatch: the object's own method, then its classes (ISA order), then global methods -- "
        "the same name on another class is its own implementation. The receipt lands on the "
        "object. Compose methods with parents (help('exec')). A missing or empty value BLOCKS the "
        "call (nothing runs); outcomes: ok | blocked | error | timeout | partial. The receipt shows "
        "which implementation answered (via), the resolution order and the exact command that ran. "
        "Mark reads read_only=true. GUARDS: define(..., guard=\"result = {'ok': state['self'].get('reachable') "
        "== 'yes'}\") runs first; false = blocked, nothing runs. COMPOSITES: define(..., steps=[{'name': "
        "'soc', 'call': 'read', 'object': 'argus:battery'}, {'name': 'lights', 'call': 'turn_off', 'object': "
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
                            "required": ["action"] if any(actions) else []}}


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
# 'membrane_entity_add', 'm:tool:find' were refused as "not your lane's hand")
_NS = re.compile(r"^(?:(?:membrane|mcp|mem|functions|m:tool|tool)[-_.:/]+)+", re.I)
_FAM_ACT = re.compile(r"^([a-z_]+)[/.:](?:action[-_=:]?)?([a-z_]+)$", re.I)


def canonical(name: str, args: dict) -> tuple[str, dict]:
    """THE NAMES THE MODEL INVENTS (2026-10-10, the operator: "why are
    entity/action-read and exec/action-link not the operator's lane?"): the
    seat called 'entity/action-read', 'membrane-entity_read' -- names no lane
    lists, so even its READS were refused. A namespace prefix drops; a
    'family/action-x' (or family.x, family:x) is the family's call."""
    name = _NS.sub("", str(name or "").strip())
    m = _FAM_ACT.match(name)
    if m and m.group(1).lower() in FAMILIES:
        fam, act = m.group(1).lower(), m.group(2).lower()
        if act in FAMILIES[fam]["actions"]:
            return fam, {**(args or {}), "action": act}
    return name, args


def resolve(name: str, args: dict) -> tuple[str, dict]:
    """(existing tool name, its args) for a family call; an existing tool's
    own name passes through; ValueError names the valid actions."""
    # THE ARGUMENTS IN THE NAME (2026-10-11: a loop sent '{"action":' as the
    # tool's name six times and was told 'not your lane's hand' -- the wrong
    # lesson): say what happened and how to send it
    _bare = str(name or "").strip().strip("\"'` ?")
    if not _bare:
        raise ValueError(
            f"the call had no tool name ({str(name or '')!r}): name the tool -- one of "
            f"{', '.join(sorted(FAMILIES))} -- and put its JSON in the arguments")
    if str(name or "").lstrip()[:1] in ("{", "["):
        raise ValueError(
            f"the call's NAME held its arguments ({str(name)[:80]!r}): put the tool's name -- "
            f"one of {', '.join(sorted(FAMILIES))} -- in the name, and that JSON in the arguments, "
            "e.g. name='entity', arguments={\"action\": \"read\", \"name\": \"presence\"}")
    name, args = canonical(name, args)
    args = dict(args or {})
    fam = FAMILIES.get(name)
    if fam is None:
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
            raise ValueError(f"{name}: unknown action {action!r}; actions: "
                             + ", ".join(a for a in fam["actions"] if a))
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
