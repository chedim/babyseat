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
                   "parents": {"type": "array", "items": {"type": "string"}, "description": "inherit: ISA parents"},
                   "scripts": {"type": "object", "description": "kind -> python: " + ", ".join(SCRIPT_KINDS)},
                   "properties": {"type": "object", "description": "edit: properties to set"},
                   "watch_ops": {"type": "array", "items": {"type": "object"}, "description": "edit: geometric watches (help('watches'))"},
                   "watch_fields": {"type": "array", "items": {"type": "object"}, "description": "edit: own-motion watches"},
                   "merged_into": _S("edit: merge into this entity")},
        "map": {"read": lambda a: _pick(a, "spec", "run"),
                "add": lambda a: _pick(a, "class", "name", "summary", "links"),
                "edit": lambda a: _pick(a, "spec", "summary", "links", "class", "merged_into",
                                        "properties", "watch", "watch_ops", "watch_fields",
                                        "publish", "unpublish"),
                "remove": lambda a: _pick(a, "spec"),
                "inherit": lambda a: _pick(a, "spec", "parents")},
    },
    "exec": {
        "description": "Run shell commands through the executor: link runs them now and "
                       "returns rc + output; unlink drops a queued one. Pipelines: help('exec').",
        "actions": {"link": "entity_link", "unlink": "entity_unlink"},
        "params": {"commands": {"type": ["string", "array"], "description": "a command line (or a list; 'label :: cmd' names one)"},
                   "args": {"type": "object", "description": "values for the command's {{name}} parameters"},
                   "executor": _S("optional executor entity"), "create": _B("create missing command entities")},
        "map": {"link": lambda a: _pick(a, "commands", "executor", "create", "args"),
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
        "map": {"learn": lambda a: {"lesson": a.get("text") or a.get("lesson")},
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
        "nudge, mention (on each mention), thought, dreamed, delete, watch (on external events). "
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


def resolve(name: str, args: dict) -> tuple[str, dict]:
    """(existing tool name, its args) for a family call; an existing tool's
    own name passes through; ValueError names the valid actions."""
    args = dict(args or {})
    fam = FAMILIES.get(name)
    if fam is None:
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
        for kind, code in (args.get("scripts") or {}).items():
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
