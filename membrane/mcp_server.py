"""The MCP tool surface (P1): membrane's memory as TOOLS, not prose frames.

The canonical frames (`membrane: query the field for ...`, `membrane: learn
that ...`) are prose-fragile: the model must remember the lane discipline,
output exact phrasing, and wait a FULL turn for the answer. An MCP server
makes the same operations structured tool calls -- typed arguments, no frame
parsing, no one-turn delay, no possibility of the turn being LEARNED.

This module is a STATELESS transport adapter. It owns NO field state: every
tool relays to the RUNNING membrane proxy over 127.0.0.1:<port> (the proxy
that `membrane opencode` started). All guards stay in the proxy -- dedup,
variety gate, length caps, the audit trail, the two-authority boundary. The
MCP layer can only do what the operator's proxy allows; kill it with
--no-mcp and nothing but the convenience is lost.

Tools (each maps 1:1 to a canonical frame / endpoint):
  query    read recall (GET  /membrane/ask?q=...&k=N)     [read-only]
  state    field health/stats (GET /membrane/state?mcp=1) [read-only]
  learn    write one earned lesson (POST /membrane/learn) [guarded]
  forget   delete cells (POST /membrane/forget)           [guarded]

Protocol: MCP stdio transport -- newline-delimited JSON-RPC 2.0 on stdin/
stdout (initialize / tools/list / tools/call + notifications). Only stdlib.
Nothing this server prints to stdout is ever logged; diagnostics go to
stderr. The server never learns anything itself: it holds no field file, no
embedder, and the proxy-side endpoints it calls are the SAME intercepted
paths chat uses (reads are pre-gate; writes run the ordinary learn guards).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "membrane"
SERVER_VERSION = "1.0.0"

# A query/state read can embed + search; a learn may dedup-embed. Generous
# but bounded: a hung proxy fails the tool call, it never wedges opencode.
HTTP_TIMEOUT_S = 120.0   # the relay headroom: a live turn's tool call may
                         # queue behind the field's lock (the capture
                         # worker's burst); 30s false-errored healthy work.
                         # The handlers' own caps (subprocess 20s/600s)
                         # bound the real work.

# THE SHELL'S REPLY (operator report 2026-10-24, "Output is clipped"):
# method_call answers like Claude Code's Bash tool -- a command's FULL output
# (stdout+stderr as run) up to LINK_OUTPUT_CAP chars; past it the head (2/3)
# and the tail (1/3) around a cut marker, the total size and how to read the
# rest. The cap is cut where the command runs (night.run_command_entity), so
# the route's JSON never carries more; the command entity keeps only its short
# run_head/run_tail residue. APPROVED by the operator 2026-10-24 (Claude
# Code's default inline cap).
LINK_OUTPUT_CAP = 30000
# BRIEF RESULTS (the operator, 2026-11-03): inside one answer every old tool result is re-sent on
# every later step, and code_run / bash were 53% of the result tokens. The full output is kept on
# the run's result entity (the field); the reply is the output whole when shorter than BRIEF_WHOLE,
# else the rc, its size, the first and last BRIEF_LINES lines (at most BRIEF_CAP chars) and how to
# page the rest with entity_read lines=A-B.
BRIEF_WHOLE = 256
BRIEF_LINES = 15
BRIEF_CAP = 4000
# the local models' toolset path (server.mcp_executor) keeps the 600-char head
# it always had: their context is small (a pre-existing value, unchanged)
LOCAL_LINK_HEAD = 600


def cut_middle(text: str, cap: int = LINK_OUTPUT_CAP) -> str:
    """`text` whole when it fits in `cap`; else its first 2/3 and last 1/3
    of `cap` around a '… N chars cut …' marker."""
    text = str(text or "")
    if len(text) <= cap:
        return text
    h = cap * 2 // 3
    t = cap - h
    return f"{text[:h]}\n… {len(text) - h - t} chars cut …\n{text[-t:]}"


def _take(lines: list, budget: int, from_end: bool = False) -> tuple[list, int, bool]:
    """Up to BRIEF_LINES of `lines` within `budget` chars -> (shown, how many lines, a line was
    cut). A line cut at the head keeps its start; at the tail its end (where "FAILED: 3" is)."""
    shown, used, cut = [], 0, False
    for ln in lines[:BRIEF_LINES]:
        room = budget - used
        if room <= 0:
            break
        if len(ln) > room:
            shown.append(("…[line cut] " + ln[-room:]) if from_end else (ln[:room] + " …[line cut]"))
            cut = True
            break
        shown.append(ln)
        used += len(ln) + 1
    return shown, len(shown), cut


def _brief_output(r: dict) -> str:
    """A run's output for the tool reply, brief, and never silently cut (Memo on #912): whole when
    short, else its first and last lines within BRIEF_CAP, a header saying exactly what is shown,
    what was not kept, and how to reach the rest (the result entity keeps it)."""
    out = r.get("output")
    if out is None:
        out = r.get("head") or ""
    out = str(out)
    n = int(r.get("output_len") or len(out))
    if not out:
        return "output: (none)"
    if n < BRIEF_WHOLE and len(out) == n:
        return f"output:\n{out}"
    res = r.get("result") or ""
    lost = n > len(out)
    unit = "kept lines" if lost else "lines"     # past the cap, numbers count the KEPT text, not the run's
    kept_note = (f"; only {len(out)} chars were kept, the middle of the run's output is gone, so the "
                 f"{unit} numbered here are not the run's own line numbers -- re-run narrower (sed -n / grep) "
                 f"to see the rest" if lost else "")
    lines = out.split("\n")
    total = len(lines)
    whole = f"; whole: entity_read spec={res} prop=output" if res else ""
    if total <= 2 * BRIEF_LINES and len(out) <= BRIEF_CAP:
        return f"output ({total} {unit}, {n} chars{kept_note}):\n{out}"
    half = (BRIEF_CAP - 600) // 2                # the header's room: the whole reply stays within BRIEF_CAP
    if total == 1:                                # one long line (a JSON blob): its two ends
        return (f"output (1 line, {n} chars{kept_note}; shown: its first and last {half} chars{whole}):\n"
                f"{out[:half]}\n… {len(out) - 2 * half} chars …\n{out[-half:]}")
    head, a, cut_a = _take(lines, half)
    tail_r, b, cut_b = _take(list(reversed(lines[a:])), half, from_end=True)
    tail = list(reversed(tail_r))
    gap = (a + 1, total - b) if total - b >= a + 1 else None
    shown = f"{unit} 1-{a}" + (f" and {total - b + 1}-{total}" if b else "")
    if cut_a or cut_b:                            # a cut line is only whole in the kept output
        more = f"; long lines cut{whole or ''}"
    else:
        more = ""
    if gap and res:
        more += f"; {unit} {gap[0]}-{gap[1]}: entity_read spec={res} lines={gap[0]}-{gap[1]}"
    return (f"output ({total} {unit}, {n} chars{kept_note}; shown: {shown}{more}):\n"
            + "\n".join(head) + ("\n…\n" if gap else "\n") + "\n".join(tail))


def _link_output(r: dict) -> str:
    """One run's output block for the tool reply: the (already cut) output,
    and past the cap its total size and how to read the rest."""
    out = r.get("output")
    if out is None:                       # an older seat / a composition exit
        out = r.get("head") or ""
    out = str(out)
    n = int(r.get("output_len") or len(out))
    if n <= LINK_OUTPUT_CAP:
        return f"output ({n} chars):\n{out}" if out else "output: (none)"
    h = LINK_OUTPUT_CAP * 2 // 3
    return (f"output ({n} chars, cut to the first {h} and the last "
            f"{LINK_OUTPUT_CAP - h}; the full output is not kept -- read the "
            f"rest by re-running narrower: sed -n 'A,Bp' / head -c / tail -c / "
            f"grep):\n{out}")


# the parts of WHERE WE ARE state(section=...) pulls (#429); the same names
# as lanes.PULL_SECTIONS (this module stays a thin HTTP client: no import)
_PULL_SECTIONS = ("tracks", "commands", "interfaces", "classes", "loops",
                  "timelines", "frame", "digest", "changes")

TOOLS: list[dict] = [
    {
        "name": "query",
        "description": (
            'recall(topic,k=3): nearest past exchanges (pair, similarity, model, seat). read-only, no model call. use before redoing work.'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string",
                          "description": "The topic/question to recall."},
                "k": {"type": "integer", "minimum": 1, "maximum": 8,
                      "description": "Max pairs (default 3)."},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "state",
        "description": (
            'field vitals + replay ledger + last night pass; read-only. section=tracks|commands|interfaces|classes|loops|timelines|frame|digest|changes -> that part of WHERE WE ARE (changes = since your last message).'),
        "inputSchema": {"type": "object", "properties": {
            "section": {"type": "string", "enum": list(_PULL_SECTIONS),
                        "description": "Optional: one part instead of all."}}},
    },
    {
        "name": "entities_related",
        "description": (
            'entities related to query (names first, then semantic) with mention counts + co-occurring neighbours. k 1-20 (8).'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Free text: names, paths, symbols."},
                "k": {"type": "integer",
                      "description": "Max entities (1-20, default 8)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "entity_query",
        "description": (
            "enumerate/count entities. q terms: kind:<k|*> (eid kind f/s/m/c/tool/...) | name~:<prefix> | parent:<spec> (ISA ancestor) | children:<spec> | status:<s> | near:<text> (shared words, best first; lists newest first). & and, | or. count=true -> 'N entity(ies)'. e.g. 'kind:tool:* & status:open'."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string",
                      "description": "the query line ('' = match all)."},
                "k": {"type": "integer",
                      "description": "the row cap (default 20)."},
                "count": {"type": "boolean",
                          "description": "the bare count form."},
            },
        },
    },
    {
        "name": "entity_add",
        "description": ('add entity (decision, failure mode, component, goal) with a one-line summary; same name updates. class Tool + `command` prop = command entity, composes with ISA parents (entity_inherit): parents run first and feed the child via {{parent.head}} / {{parent.tail}} ({{parent:<eid>.head}} names one). audited.'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class": {"type": "string",
                          "description": "Decision | FailureMode | "
                                         "Component | Goal | Model | Other, "
                                         "or any class a loaded .c1f pack declares "
                                         "(its getters and setters then run on it; "
                                         "default Component)."},
                "name": {"type": "string"},
                "summary": {"type": "string",
                            "description": "One line: what it is / what "
                                           "was decided / what fails."},
                "links": {"type": "array",
                          "description": "Field cell indexes to link."},
                "properties": {"type": "object",
                               "description": "the entity's property "
                                              "patch, set in the same "
                                              "call (e.g. a Decision "
                                              "card: {'status': 'open', "
                                              "'var': {'type': 'option', "
                                              "'options': [...]}, "
                                              "'pick': ..., 'blocks': "
                                              "[<eids>]})."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "entity_edit",
        "description": ("edit by eid|exact name; only given fields change. merged_into = subsumption. behaviour = .c1f pack (pack tool, help('packs')), never a script prop; class = any class a loaded pack declares. to act: entity_write the prop whose write is the effect (chat:<person>.say, membrane::llm.call, File/Command verb). publish=True exports state only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "eid or exact name."},
                "publish": {"type": "boolean", "description": "export it (state only, never code)."},
                "unpublish": {"type": "boolean", "description": "retract your export."},
                "summary": {"type": "string"},
                "links": {"type": "array"},
                "class": {"type": "string"},
                "merged_into": {"type": "string", "description": "the survivor's eid or name."},
                "watch_ops": {"type": "array", "description": "[{op: near|away|around, rel, z|y}]; help('watches')."},
                "watch_fields": {"type": "array", "description": "[{verb: pos|align|vel, arg}]; help('watches')."},
                "properties": {"type": "object", "description": "bounded property patch (a pack's c1f text, the fields its getters read)."},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "entity_remove",
        "description": (
            'remove entity (noise cleanup) by eid|exact name; ambiguous -> nothing removed. a live method, or a class owning methods, is refused unless force=true (class removal takes its methods).'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string"},
                "force": {"type": "boolean",
                          "description": "remove a live method, or a class together with its methods"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "graph",
        "description": 'subgraph reader: entities related to query (names first, then centroid-nearest) as nodes(id,class,label,position,status,curiosity,fn_counts,properties) + edges(ISA,links,merged_into). k cap 8; empty query = top-linked.',
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "free text (the query's named "
                                         "entities anchor the "
                                         "neighborhood; empty = the "
                                         "top-linked entities)."},
                "k": {"type": "integer",
                      "description": "the neighborhood's cap (default 8)."},
            },
        },
    },
    {
        "name": "entity_inherit",
        "description": (
            'give entity parent concept(s): child inherits constraints (latch level, living flag) down the ISA chain; geometry = parent position + own offset. cycles rejected. spec=child, parents=[...].'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string",
                         "description": "The child (eid or exact name)."},
                "parents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The parent concepts (multiple "
                                   "inheritance allowed).",
                },
            },
            "required": ["spec", "parents"],
        },
    },
    {
        "name": "pack",
        "description": ("field programs: a .c1f pack gives a class getters (keep a value true) + setters (react to a write) (help('packs')). action list | show(name) | load(name, c1f=whole text starting `PACK <name> <n>`): load runs at once, reply carries its load error (broken pack refused, last good keeps running). shipped packs (banner, mcp, ...) are the release's: extend from your own pack (`IMPORT banner 1`, own class)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "show", "load"]},
                "name": {"type": "string", "description": "the pack's name"},
                "c1f": {"type": "string", "description": "load: the pack's whole text"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "entity_write",
        "description": ('act by writing: set one prop; the write runs what reacts to it: pack setters or adapter effects (chat:<person>.say speaks to them; membrane::llm.call wakes a model turn; McpTool:<server>:<tool>.call:run; File write; Command run). receipt lands as <prop>_result: read it back.'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "the entity (eid or exact name)"},
                "prop": {"type": "string", "description": "the property"},
                "value": {"description": "the value (any JSON)"},
            },
            "required": ["spec", "prop"],
        },
    },
    {
        "name": "method_define",
        "description": ("give class|object a METHOD, then method_call / method_each it. command = shell template ({{arg}} = call args, {{self.<prop>}} = object props), e.g. on='light', method='dim', command='curl ... {{self.entity_id}} {{level}}'; or steps = composite in order. guard = python check first -> {'ok':bool,'why':str}. missing owner becomes a class; re-define updates. owner only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "on": {"type": "string",
                       "description": "The owner: the class or object the method "
                                      "belongs to (eid or name)."},
                "method": {"type": "string",
                           "description": "The method's name; with abstract, its "
                                          "signature, e.g. 'dim(level)'."},
                "command": {"type": "string",
                            "description": "The command template ({{arg}}, "
                                           "{{self.prop}})."},
                "read_only": {"type": "boolean",
                              "description": "The method only reads (a sensor)."},
                "guard": {"type": "string",
                          "description": "Optional python check run before the "
                                         "command; state has self, args, steps."},
                "steps": {"type": "array",
                          "items": {"type": "object"},
                          "description": "A composite instead of a command: "
                                         "[{name, call, object?, args?} | "
                                         "{name, each, call, args?, max?} | "
                                         "{name, command}], run in order, a "
                                         "failure halts."},
                "abstract": {"type": "boolean",
                             "description": "An interface's required method: a "
                                            "signature, no command; each member "
                                            "must implement it."},
            },
            "required": ["on", "method"],
        },
    },
    {
        "name": "method_call",
        "description": (
            "run method on entity|class: dispatch object -> ISA classes -> globals (help('cliffhanger')). program the field first: method_define, then call here (code itself: code_run). a call straight at an executor (tool:executor, remote_executor, an implementation's exec) is refused. e.g. object='presence', method='restart'. receipt (rc, outcome, head) lands on the object."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "object": {"type": "string", "description": "The target entity (eid or name)."},
                "method": {"type": "string", "description": "The method's name, e.g. exec."},
                "args": {"type": "object", "description": "The method's {{param}} values."},
            },
            "required": ["object", "method"],
        },
    },
    {
        "name": "code_run",
        "description": (
            "run code: parsed into a node tree in the field (one entity per node, shared), root linked to an executor of its language (-execute-> owed, -executing-> running, -result-> result entity). lang 'sh' (default, tool:executor) | 'js' (opted-in browser tab). re-run: node=<root id> (each run keeps its result). entity_read on a root = code, state, runs. returns root, result, rc, output."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code, e.g. 'ls -la ..'."},
                "lang": {"type": "string", "description": "Its language: 'sh' (default) or 'js'."},
                "node": {"type": "string", "description": "A root to re-run instead of code."},
                "on": {"type": "string", "description": "An executor of that language (default: the language's own)."},
            },
        },
    },
    {
        "name": "bash",
        "description": (
            'run a shell command once = code_run lang sh (tree in the field, result entity; re-run via code_run node=<root>). what you will run again: method_define on its entity ({{params}}), then method_call.'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."},
                "timeout": {"type": "number", "description": "Optional timeout in milliseconds."},
                "description": {"type": "string", "description": "What the command does, in a few words."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "method_each",
        "description": (
            "call one method on every member of a class (over='light') or name pattern ('ha:light.*'). dry_run lists targets only. stops at first failure unless on_fail='continue'. owner only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "over": {"type": "string",
                         "description": "The class (its members) or a '*' "
                                        "name pattern."},
                "method": {"type": "string", "description": "The method's name."},
                "args": {"type": "object",
                         "description": "The method's {{param}} values, the same "
                                        "for every member."},
                "max": {"type": "integer",
                        "description": "Cap on members reached (default 20, "
                                       "at most 150)."},
                "dry_run": {"type": "boolean",
                            "description": "List the members only; run nothing."},
                "on_fail": {"type": "string", "enum": ["abort", "continue"],
                            "description": "abort (default) stops at the first "
                                           "failure; continue runs the rest."},
            },
            "required": ["over", "method"],
        },
    },
    {
        "name": "method_inspect",
        "description": (
            'which method a call would run (owner, resolution order, params, template), without running it. read-only.'),
        "inputSchema": {
            "type": "object",
            "properties": {
                "object": {"type": "string", "description": "The target entity (eid or name)."},
                "method": {"type": "string", "description": "The method's name."},
                "args": {"type": "object", "description": "Optional {{param}} values to bind."},
            },
            "required": ["object", "method"],
        },
    },
    {
        "name": "entity_read",
        "description": ("read entity: pack getter values + properties (run=true bypasses cache). properties blob clipped ~800 chars: prop=<name> reads one in full; tail=N last N items of a list prop; lines=A-B pages text (e.g. a run's output)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string",
                         "description": "eid or name fragment."},
                "run": {"type": "boolean",
                        "description": "Bypass the 5s getter LRU (force a "
                                       "fresh read-through; a getter's "
                                       "{run: true} re-executes)."},
                "prop": {"type": "string",
                         "description": "Extract one property in full (e.g. "
                                        "prop=heard) instead of the clipped "
                                        "whole-properties blob."},
                "tail": {"type": "integer",
                         "description": "With prop: page a list-valued "
                                        "property from its NEWEST end, "
                                        "returning the last N items (e.g. "
                                        "prop=heard, tail=5)."},
                "lines": {"type": "string",
                          "description": "A-B: lines of a run's kept output (or of prop)."},
            },
            "required": ["spec"],
        },
    },
]

# #1101: the context stack and bare method calls -- declared, so a rung that only calls declared tools (the
# Claude SDK) reaches them; the usual file tools only on a no-bash seat (cliffhanger_only.strip_tools)
_OBJ = {"type": "object"}
TOOLS += [
    {"name": "call", "description": "Call a method by its bare name; it resolves in your context (in: …), "
                                    "innermost first. args are its params.",
     "inputSchema": {"type": "object", "properties": {"method": {"type": "string"}, "args": _OBJ},
                     "required": ["method"]}},
    {"name": "entity_enter", "description": "Enter an entity: it joins this chat's context (in: …) until exit.",
     "inputSchema": {"type": "object", "properties": {"spec": {"type": "string"}}, "required": ["spec"]}},
    {"name": "entity_exit", "description": "Leave the innermost entered entity.",
     "inputSchema": {"type": "object", "properties": {}}},
]


# ---- transport helpers -------------------------------------------------------

class ProxyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _jwt_headers() -> dict:
    """JWT MODE (jwtauth.py): the relay presents a token -- an explicit
    MEMBRANE_JWT_TOKEN, else the internal one minted from the secret this
    child inherited from the seat; {} when the seat runs open."""
    from membrane import jwtauth, seatauth
    h = jwtauth.client_headers()
    if h or jwtauth.enabled():
        return h
    tok = seatauth.seat_token()          # JWT off: the seat's own token is the credential (#842)
    return {"Authorization": f"Bearer {tok}"} if tok else {}


from membrane import writes as _W   # noqa: E402  (#576: the relay carries the chain)


def _http(port: int, method: str, path: str, body: dict | None = None,
          timeout: float | None = None) -> dict:
    """One relay call to the seat's proxy. THE TIMEOUT HEADROOM (the
    swirl exhibit): the default relay budget covers a live turn's
    contention -- the run lane's handler can queue behind the entity
    field's lock (the capture worker's burst applies) for tens of
    seconds WITHOUT being dead; a 30s timeout false-errored the tool
    ('proxy unreachable (timed out)') and the model burned rounds
    retrying. The handler-side subprocess cap (20s default / 600s
    max) still bounds the real work."""
    if timeout is None:
        timeout = HTTP_TIMEOUT_S
    url = f"http://127.0.0.1:{int(port)}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **_jwt_headers(),
                                          **_W.headers(default_writer="llm"),
                                          _W.HDR_CALLER: _client_caller()})   # #576
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("error", "")
            # the seat's {"message", "type"} error: the MESSAGE reaches the
            # model as text (a refusal's inventory lines stay readable)
            if isinstance(detail, dict):
                detail = detail.get("message") or detail
        except Exception:
            detail = e.reason
        raise ProxyError(e.code, str(detail)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ProxyError(503, f"membrane proxy unreachable on :{port} "
                              f"({e})") from None


# ---- tool implementations (all guards live proxy-side) ------------------------

def _arg(args: dict, *names: str) -> str:
    """The primary arg under its schema name OR the names the models keep
    reaching for (the ThinkPhone seat, 2026-10-08: entity_read got
    eid/entity/name/key, intend got goal, peer_post got message, learn got
    text -- ~200 'X required' refusals for a value that WAS sent). A
    wrapped `args` JSON string ({"args": "{\"eid\": ...}"}) unwraps."""
    for n in names:
        v = args.get(n)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            return str(v).strip()
    inner = args.get("args")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            inner = None
    if isinstance(inner, dict) and inner is not args:
        return _arg(inner, *names)
    return ""

def tool_query(args: dict, port: int) -> str:
    topic = _arg(args, "topic", "query", "q", "text")
    if not topic:
        raise ProxyError(400, "topic required")
    try:
        k = max(1, min(8, int(args.get("k") or 3)))
    except (TypeError, ValueError):
        k = 3
    qs = urllib.parse.urlencode({"q": topic[:512], "k": k})
    out = _http(port, "GET", f"/membrane/ask?{qs}")
    block = out.get("block")
    if not out.get("hits"):
        if block:
            return block + "\n(read-only; this call was NOT learned)"
        return (f"Nothing SAFE in the field about {topic!r} yet "
                f"(field knows {out.get('field_size', '?')} live entities). "
                "Do not invent memories; proceed and let the turn learn.")
    lines = ([block, ""] if block else []) + \
        [f"Field recall (membrane; read-only, this call was NOT learned):"]
    for h in out["hits"]:
        via = f" [via {h['model']}]" if h.get("model") else ""
        tag = h.get("outcome") or "pending"
        src = h.get("src") or "user"
        seat = h.get("seat") or ""
        seat_l = f" [seat: {seat}]" if seat else ""
        lines.append(f"({h['sim']:.2f} [{tag}]{via}{seat_l} [src: {src}]) "
                     f"situation: {h['situation']}\n"
                     f"    response: {h['action']}")
    return "\n".join(lines)


def tool_state(args: dict, port: int) -> str:
    section = str((args or {}).get("section") or "").strip().lower()
    if section:                                  # #429: pull one part
        from urllib.parse import quote
        out = _http(port, "GET", f"/membrane/state?mcp=1&section={quote(section)}")
        text = str(out.get("text") or "").strip()
        return (f"WHERE WE ARE ({section}):\n{text}" if text
                else f"Nothing in {section} right now.")
    out = _http(port, "GET", "/membrane/state?mcp=1")
    ef = out.get("efield") or {}
    lines = [
        "Membrane state (live from the running proxy):",
        f"- field: {out.get('field_size', '?')} live entities "
        f"| embed model {out.get('embed_model') or 'unresolved'}",
    ]
    if ef and "error" not in ef:
        ev = ef.get("events") or {}
        if ev:
            by = {}
            for k, c in ev.items():
                head = k.split("/")[0]
                by[head] = by.get(head, 0) + c
            lines.append("- events: "
                         + ", ".join(f"{k} {c}"
                                     for k, c in sorted(by.items())))
        ents = ef.get("entities") or {}
        if ents:
            lines.append("- entities: "
                         + ", ".join(f"{k} {c}"
                                     for k, c in sorted(ents.items())))
        g = ef.get("geometry") or {}
        if g.get("eff_rank") is not None:
            lines.append(f"- geometry: eff rank {g['eff_rank']:.2f}")
        ab = ef.get("replay_ab") or {}
        if ab.get("eligible"):
            sim = ab.get("mean_answer_sim")
            sim_l = f"{sim:.2f}" if sim is not None else "?"
            lines.append(f"- replay A/B: {ab['eligible']} eligible, "
                         f"{ab.get('served_replay', 0)} served as pure "
                         f"replay (mean answer sim {sim_l})")
    night = out.get("night")
    if night:
        lines.append(f"- last night: births={night.get('births', 0)} "
                     f"junk={night.get('junk', 0)}")

    # THE 'I' TRACK over MCP: the same WHERE WE ARE the chat seat's model
    # gets -- position, commitments, entity tracks.
    wwa = out.get("where_we_are")
    if wwa:
        lines.append("")
        lines.append(wwa)
    elif out.get("entity_tracks"):
        lines.append("- entity tracks: "
                     + "; ".join(t["key"] for t in out["entity_tracks"]))
    # the SURFACE BANNER over MCP: the last turn's [membrane] monologue lines
    # (route/vitals/gave/learned) -- the same stream the TUI thinking channel
    # gets, delivered in the tool lane so the MCP seat sees the banner too.
    surf = (out.get("surface") or [])
    if surf:
        r = surf[-1]
        b = [f"[membrane] surface (last turn): verdict={r.get('verdict')} "
             f"sim={r.get('sim')} route={r.get('route')} "
             f"model={r.get('model')}"]
        for blk in (r.get("sent_blocks") or []):
            b.append(f"  sent: {blk['block']} ({blk['chars']}ch)")
        if r.get("learned") is not None:
            b.append(f"  learned: #{r['learned']}")
        lines.append("")
        lines.append("\n".join(b))
    return "\n".join(lines)


def tool_learn(args: dict, port: int) -> str:
    payload = _arg(args, "lesson", "text", "value", "content", "data",
                   "fact", "note", "learn", "summary")   # the seat's own spellings (2026-10-09)
    if not payload:
        raise ProxyError(400, "lesson required")
    out = _http(port, "POST", "/membrane/learn", {"lesson": payload})
    if not out.get("written"):
        return ("Agent learn skipped by the field's own guards (dedup or "
                "variety gate): nothing new written. "
                + str(out.get("reason") or ""))
    # THE RECEIPT (the '#None' defect): the route ships the note's
    # EVENT number ('#N' -- the forget-path's handle) in ex_idx.
    n = out.get("ex_idx")
    head = (f"Learned as note #{n}" if n is not None
            else "Learned as a note (number unavailable)")
    return (f"{head} (no model call). Field now "
            f"{out.get('field_size')} events.\n"
            f"    note: {out.get('note', '')[:400]}")


def tool_forget(args: dict, port: int) -> str:
    spec = str(args.get("spec") or "").strip()
    if not spec:
        raise ProxyError(400, "spec required")
    out = _http(port, "POST", "/membrane/forget", {"spec": spec})
    if out.get("events") is not None:
        lines = [f"Forgot {out['removed']} event(s) (the entity field):"]
        for r in out["events"]:
            if r.get("missing"):
                lines.append(f"- #{r['n']}: no such event")
            elif r.get("error"):
                lines.append(f"- #{r['n']}: failed ({r['error']})")
            else:
                lines.append(f"- #{r['n']}: {'already forgotten' if r.get('already') else 'forgotten'}; "
                             f"{len(r.get('entities') or [])} entities recomputed")
        return "\n".join(lines)
    if out.get("removed") is not None:
        lines = [f"Removed {out['removed']} of {out.get('field_size_before')} "
                 f"(index form). Field now {out.get('field_size')}."]
        for r in out.get("removed_cells") or []:
            lines.append(f"- #{r['idx']}: {r['situation']}")
        return "\n".join(lines)
    cands = out.get("candidates") or []
    if not cands:
        return ("Wordy forgets propose, never delete: nothing matched "
                "(the nearest past events only). Nothing removed.")
    lines = ["Wordy asks never delete from tool calls. Nearest events:",
             ", ".join(f"#{c['idx']} (sim {c['sim']:.2f})"
                       for c in cands[:8]),
             "To remove, re-ask forget with explicit indexes. "
             "Nothing removed."]
    return "\n".join(lines)


def tool_watch_entity(args: dict, port: int) -> str:
    """Plant an entity track via the proxy's /membrane/entity-track
    endpoint (the proxy holds the EntityStore; guards live there).
    THE OWNER (the lane's delivery, 2026-10-06): `owner` = whose watch
    this is -- ''/'operator' delivers the boundary crossing as the
    banner's ALERT; a listener's name ('watch:<eid>') routes the
    crossing into the EVENTS POOL (the listener receives it as its
    next turn's message)."""
    key = _arg(args, "key", "entity", "spec", "eid", "name")
    if not key:
        raise ProxyError(400, "key required (e.g. 'files:router.py')")
    out = _http(port, "POST", "/membrane/entity-track", {
        "key": key,
        "boundary": str(args.get("boundary") or ""),
        "label": str(args.get("label") or ""),
        "owner": str(args.get("owner") or ""),
    })
    if not out.get("ok"):
        return f"Track not planted: {out.get('error', 'unknown error')}"
    e, x = out.get("enter"), out.get("exit")
    bs = (f" with boundary: enter {e}" if e is not None
          else f" with boundary: exit {x}" if x is not None else "")
    return (f"Entity track planted: {out['key']}{bs}. Its live state "
            f"rides the WHERE WE ARE header each turn; a boundary "
            f"transition (enter/exit) alerts once via the banner.")


def tool_field_watch(args: dict, port: int) -> str:
    """The board's spatial watches (POST /membrane/watch): plant / drop /
    list, inside the shared attention budget (a full budget answers with
    the live watches -- the model drops what it no longer needs)."""
    action = str(args.get("action") or "list").strip().lower()
    if action not in ("plant", "drop", "list"):
        raise ProxyError(400, "action: plant | drop | list")
    body = {"action": action, "owner": str(args.get("owner") or "")}
    if action == "plant":
        topic = str(args.get("topic") or args.get("label") or "").strip()
        if not topic:
            raise ProxyError(400, "plant needs a topic")
        body["topic"] = topic
    if action == "drop":
        spec = str(args.get("spec") or args.get("topic") or "").strip()
        if not spec:
            raise ProxyError(400, "drop needs a spec: '#N', a label fragment, or 'all'")
        body["spec"] = spec
    out = _http(port, "POST", "/membrane/watch", body)
    return str(out.get("text") or out)


def tool_unwatch_entity(args: dict, port: int) -> str:
    key = str(args.get("key") or "").strip()
    if not key:
        raise ProxyError(400, "key required (exact key or kind prefix)")
    out = _http(port, "POST", "/membrane/entity-track/remove", {"key": key})
    n = int(out.get("removed") or 0)
    return f"Removed {n} entity track(s)." if n \
        else "No matching tracks (list them via entity_tracks)."


def tool_list_tracks(args: dict, port: int) -> str:
    import urllib.parse as _up
    owner = str(args.get("owner") or "").strip()
    qs = "/membrane/entity-track" + (f"?owner={_up.quote(owner)}"
                                     if owner else "")
    out = _http(port, "GET", qs)
    tracks = out.get("tracks") or []
    if not tracks:
        return ("No entity tracks planted. Plant one with "
                "entity_watch (key + optional boundary).")
    lines = ["Your entity tracks (live in the WHERE WE ARE banner):"]
    for t in tracks:
        b = (f", boundary enter {t['enter']}" if t.get("enter") is not None
             else f", boundary exit {t['exit']}" if t.get("exit") is not None
             else "")
        inside = (f", currently {t['inside']}" if t.get("inside") is not None
                  else "")
        pa = f", {t['pending_alerts']} pending alert(s)" \
            if t.get("pending_alerts") else ""
        lines.append(f"- {t['key']}{b}{inside}{pa}")
    return "\n".join(lines)


def tool_related_entities(args: dict, port: int) -> str:
    q = _arg(args, "query", "q", "topic", "text", "spec")
    if not q:
        raise ProxyError(400, "query required")
    k = int(args.get("k") or 8)
    out = _http(port, "GET",
                f"/membrane/entity-related?q={urllib.parse.quote(q)}&k={k}")
    rel = out.get("related") or []
    if not rel:
        return (f"No entities related to {q!r} yet. The index grows as "
                f"events land on the field (deterministic harvest) and as you "
                f"add abstract entities via entity_add.")
    lines = [f"Entities related to {q!r}:"]
    for r in rel:
        nb = ""
        if r.get("neighbors"):
            nb = " -- " + ", ".join(f"{n.get('eid') or n['key']} ({n['shared']} shared)"
                                    for n in r["neighbors"][:3])
        lines.append(f"- {r.get('eid') or r['key']} ({r['cells']} mentions, {r.get('how', '')})"
                     f"{nb}")
    return "\n".join(lines)


# what entity/add itself takes; the rest of an add's args ride a follow-up
# edit (the add-hand used to DROP getter_script and friends silently)
_ADD_FIELDS = ("class", "name", "summary", "links", "spec")
_EDIT_FIELDS = ("nudge_script", "delete_script", "mention_script",
                "thought_script", "dreamed_script", "tick_script",
                "watch_script", "getter_script", "watch", "watch_ops",
                "watch_fields", "properties", "publish")


def tool_entity_add(args: dict, port: int) -> str:
    out = _http(port, "POST", "/membrane/entity/add", {
        "class": str(args.get("class") or ""),
        "name": _arg(args, "name", "spec"),
        "summary": str(args.get("summary") or ""),
        "links": args.get("links") or [],
    })
    if not out.get("ok"):
        return f"Entity not written: {out.get('error', 'unknown error')}"
    how = "merged into the existing entity" if out.get("merged") \
        else "created"
    msg = (f"Entity {out['name']!r} [{out['class']}] {how}: {out['eid']} "
           f"(visible to entities_related; audited to the operator).")
    extra = {k: args[k] for k in _EDIT_FIELDS if args.get(k) not in (None, "", [], {})}
    if extra:
        ed = _http(port, "POST", "/membrane/entity/edit",
                   {"spec": out["eid"], **extra, **({"defer_post": True} if args.get("defer_post") else {})})
        msg += (f" Also set: {', '.join(sorted(extra))}." if ed.get("ok")
                else f" But setting {', '.join(sorted(extra))} failed: "
                     f"{ed.get('error', 'unknown error')} (retry with entity_edit).")
    ignored = sorted(k for k in args if k not in _ADD_FIELDS + _EDIT_FIELDS
                     and k not in ("args",))
    if ignored:
        msg += f" Ignored unknown arg(s): {', '.join(ignored)}."
    return msg


def tool_entity_edit(args: dict, port: int) -> str:
    body = {"spec": _arg(args, "spec", "eid", "entity", "name", "key", "id")}
    for f in ("summary", "links", "class", "merged_into",
              "nudge_script", "delete_script",
              "mention_script", "thought_script", "dreamed_script",
              "tick_script", "watch_script", "getter_script",
              "watch_ops", "watch_fields", "watch", "properties"):
        if args.get(f) is not None:
            body[f] = args[f]
    for f in ("publish", "unpublish", "defer_post"):        # the tier-2 gestures; jobs.py's deferral
        if args.get(f):
            body[f] = True
    out = _http(port, "POST", "/membrane/entity/edit", body)
    if not out.get("ok"):
        return f"Edit failed: {out.get('error', 'unknown error')}"
    if out.get("published"):
        if out["published"].get("ok"):
            return (f"Entity {out['eid']} PUBLISHED to the global tier "
                    f"({out['published']['key']}, version "
                    f"{out['published']['ver']}) -- foreign seats import "
                    f"its STATE (and upgrade to each newer version), never "
                    f"your code. Publish is "
                    f"per-origin (yours); republish to bump the version.")
        return (f"Publish failed: {out['published'].get('error')} "
                f"(the local tier carries; publish again when up).")
    if out.get("unpublished"):
        if out["unpublished"].get("ok"):
            return (f"Entity {out['eid']} UNPUBLISHED (your foreign "
                    f"imports freeze at their last version).")
        return (f"Unpublish failed: {out['unpublished'].get('error')}")
    if out.get("merged_into"):
        return (f"Entity {out['eid']} MARKED merged into "
                f"{out['merged_into']} (the source doc stays, pointing "
                f"at its survivor; links unioned; announced).")
    if args.get("nudge_script") is not None or \
            args.get("delete_script") is not None:
        return (f"Entity {out['eid']} scripts updated -- the nudge runs "
                f"at the next consolidation pass (its 'render' replaces "
                f"the track's default banner line); the delete script "
                f"retires the entity when it sets result.delete=True. "
                f"Empty string clears a script.")
    if args.get("tick_script") is not None:
        return (f"Entity {out['eid']} scripts updated -- the tick "
                f"script now fires on the thinker's ~1s heartbeat "
                f"(paced by the entity's `tick_every` property, "
                f"cooldown-free, 0.25s budget). Empty string clears it.")
    if args.get("watch_script") is not None:
        return (f"Entity {out['eid']} is a WATCHER now -- its "
                f"watch_script fires when an external event arrives "
                f"(file changes under the seat dir, "
                f"signals; the trigger's payload rides state.extra"
                f".payload). Watchers may block but only 4 run at "
                f"once, a >10s fire strikes (3 = auto-disabled). "
                f"The seeded tool:filetree is the template.")
    msg = (f"Entity {out['eid']} updated: class {out.get('class')}, "
           f"summary {out.get('summary')!r}, links {out.get('links')}.")
    if isinstance(out.get("source_edit"), dict):      # #715 B: the file was written
        se = out["source_edit"]
        msg += (f" SOURCE written to {se.get('file')} (span {se.get('span')}; the symbols re-read "
                f"from disk); receipt {se.get('result')}.")
    if out.get("properties_set"):
        msg += f" Properties set: {', '.join(out['properties_set'])}."
    if out.get("properties_truncated"):
        msg += (" Properties TRUNCATED to 2000 chars: "
                + ", ".join(out["properties_truncated"]) + ".")
    if out.get("properties_refused"):
        msg += " Properties REFUSED (not set): " + "; ".join(
            f"{k} ({why})" for k, why in out["properties_refused"].items()) + "."
    return msg


def tool_entity_remove(args: dict, port: int) -> str:
    spec = _arg(args, "spec", "eid", "name", "entity", "key", "id")
    if not spec:
        raise ProxyError(400, "spec required (eid or exact name)")
    force = str(args.get("force") or "").lower() in ("true", "1", "yes")
    out = _http(port, "POST", "/membrane/entity/remove", {"spec": spec, "force": force})
    if out.get("refused"):
        return f"Not removed: {out['refused']}"
    n = int(out.get("removed") or 0)
    return (f"Removed {spec!r}" + (f" and {n - 1} of its methods / steps." if n > 1 else ".")) if n \
        else (f"Nothing removed: no single entity matches {spec!r} "
              f"(ambiguous fragments are refused).")


def tool_set_junk_cut(args: dict, port: int) -> str:
    spec = str(args.get("spec") or "").strip()
    if not spec:
        raise ProxyError(400, "spec required (track key, watch #N, "
                              "'all', or 'default')")
    cut = args.get("cut")
    out = _http(port, "POST", "/membrane/junk-cut", {"spec": spec,
                                                     "cut": cut})
    if not out.get("ok"):
        return f"Junk cut not set: {out.get('error', 'unknown error')}"
    if out.get("default") is not None:
        return (f"Global junk-cut default now {out['default']} (new "
                f"tracks inherit it; existing tracks keep their own).")
    val = out.get("junk_sim")
    return (f"Junk cut {'cleared to the default' if val is None else val} "
            f"for {out['tracks']} track(s) matching {spec!r}. The screen "
            f"applies on the next banner render / watch arrival.")


def tool_intend(args: dict, port: int) -> str:
    """The model's pen on the steering wheel: create-or-pin a shared Goal
    + give it a priority pin on THIS seat's active set. Speech announces
    every activation; drop intent suppresses seat-locally; fulfill closes
    the goal shared."""
    body = {"text": _arg(args, "text", "goal", "intent", "spec")}
    if args.get("ttl"):
        body["ttl"] = str(args["ttl"])
    out = _http(port, "POST", "/membrane/intend", body)
    if not out.get("ok"):
        return f"Intend failed: {out.get('error', 'unknown error')}"
    what = "created" if out.get("created") else "existing goal"
    if out.get("on_set", True):
        return (f"Goal active ({what}, pinned): {out.get('eid')}\n"
                f"The active set steers the dream loop; the banner shows it. "
                f"Drop intent #N suppresses it on this seat; fulfill #N closes "
                f"it shared.")
    cap = out.get("cap", "")
    return (f"Goal open ({what}) but NOT on the active set: {out.get('eid')}\n"
            f"The set is full (cap {cap}) and held goals outrank this pin. "
            f"It stays open and shared; free a slot with drop intent #N or "
            f"fulfill #N, then it can rank in.")


def tool_answer_card(args: dict, port: int) -> str:
    """#845: what the person said in the chat reaches the card (decision_map.relay)."""
    out = _http(port, "POST", "/membrane/decision/relay", {
        "card": _arg(args, "card", "spec", "eid"), "value": args.get("value"),
        "quote": str(args.get("quote") or "")})
    if not out.get("ok"):
        return f"Answer failed: {out.get('error', 'unknown error')}"
    card = out.get("card") or {}
    return f"Card {card.get('status')}: value {card.get('value')} (relayed, with the quote on record)"


def tool_fulfill(args: dict, port: int) -> str:
    """The close with a receipt: an open goal flips to fulfilled (shared)
    when the caller hands the evidence that proves it done."""
    out = _http(port, "POST", "/membrane/fulfill", {
        "spec": _arg(args, "spec", "eid", "goal", "id", "name", "key",
                     "intent"),
        "evidence": _arg(args, "evidence", "receipt", "proof", "summary",
                         "text"),
        "by": str(args.get("by") or "")})
    if not out.get("ok"):
        return f"Fulfill failed: {out.get('error', 'unknown error')}"
    return (f"Goal fulfilled (shared): {out.get('eid')} "
            f"{str(out.get('name') or '')[:100]}\n"
            f"It leaves every seat's active set; the evidence rides the "
            f"goal and the audit.")


def tool_intents(args: dict, port: int) -> str:
    """The board's read: the seat's active set + the closed record. The
    GET /membrane/intents relay answers the rendered listing."""
    out = _http(port, "GET", "/membrane/intents")
    text = str(out.get("text") or "")
    return text or "the intent board is unavailable"


def tool_graph(args: dict, port: int) -> str:
    """The model's graph read: the entity graph's subgraph as nodes +
    edges -- the same contract the universe viewer renders."""
    import urllib.parse
    q = urllib.parse.quote(_arg(args, "query", "q", "text", "spec"))
    k = int(args.get("k") or 8)
    out = _http(port, "GET",
                f"/membrane/entity-related?q={q}&k={k}")
    # the endpoint answers {"query", "related": [{key, cells, neighbors:
    # [{key, shared}], how}]} (the old reader looked for "entities" with
    # eid/cls/properties -- keys it never sends -- and always said
    # "no entities matched")
    items = out.get("related") or out.get("entities") or []
    if not items:
        return "no entities matched the query"
    lines = []
    for e in items[:k]:
        line = (f"- {e.get('eid') or e.get('key', '?')}"
                f" ({e.get('cells', 0)} cells, {e.get('how', '')})")
        neighbors = [f"{n.get('eid') or n.get('key', '?')}x{n.get('shared', '')}"
                     for n in (e.get("neighbors") or [])][:4]
        line += (f" | neighbors: {', '.join(neighbors)}" if neighbors
                 else "")
        lines.append(line)
    return "\n".join(lines)


def _listish(v) -> list:
    """A list the model may have sent as a JSON string ('["ha light"]'
    arrived literally and died 'no parent matches', 2026-10-09)."""
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("[") and t.endswith("]"):
            try:
                d = json.loads(t)
                if isinstance(d, list):
                    return [str(x) for x in d]
            except ValueError:
                pass
        return [t] if t else []
    return [str(x) for x in (v or [])]


def tool_entity_inherit(args: dict, port: int) -> str:
    spec = str(args.get("spec") or args.get("name") or args.get("key") or args.get("child") or "").strip()
    parents = _listish(args.get("parents") or args.get("parent") or [])
    if not spec or not parents:
        raise ProxyError(400, "spec + parents required")
    out = _http(port, "POST", "/membrane/entity/inherit",
                {"spec": spec, "parents": parents})
    if not out.get("ok"):
        return f"Inherit failed: {out.get('error', 'unknown error')}"
    added = out.get("added") or []
    if out.get("pattern"):
        return (f"Classified {len(out.get('linked') or [])} entities ({out['pattern']}) ISA "
                f"{', '.join(added)}" + (f" (minted: {', '.join(out.get('minted') or [])})" if out.get("minted") else "")
                + " -- a script on the class now runs on each member (getter, mention, thought, "
                  "dreamed; tick where the class sets tick_inherits).")
    contract = f" Contract: {out['contract']}." if out.get("contract") else ""
    if added:
        return (f"Entity {out['child']} ISA {', '.join(added)} -- "
                f"constraints inherited down the ISA chain (latch, "
                f"`living`); the child's position comes from its own "
                f"lived instances, the parents classify it." + contract)
    return f"Entity {out['child']} parents refreshed (no new links)." + contract


def tool_peer_read(args: dict, port: int) -> str:
    out = _http(port, "POST", "/membrane/peer/read", {})
    msgs = out.get("messages") or []
    if not msgs:
        return "No unread peer messages."
    return "\n".join([f"{len(msgs)} unread peer message(s):"]
                     + [f"peer[{m.get('from', '?')}]: {m.get('text', '')}" for m in msgs])


def tool_peer_seats(args: dict, port: int) -> str:
    out = _http(port, "GET", "/membrane/peer/seats")
    seats = out.get("seats") or []
    if not seats:
        return ("No seats in the registry (couchbase down, or you are "
                "alone). The registry self-cleans: dead proxies expire.")
    lines = [f"{len(seats)} seat(s) connected:"]
    for s in seats:
        cwd = str(s.get("cwd", "?")).rsplit("/", 1)[-1]
        lines.append(f"- {cwd} (: {s.get('port')}) pid {s.get('pid')} "
                     f"model {s.get('model', '?')}")
    return "\n".join(lines)



# ---- the file tools (opencode-shaped; every effect mints into the field) ----

def tool_entity_query(args: dict, port: int) -> str:
    """The entity graph's enumerate/list/count (the query language)."""
    q = _arg(args, "q", "query", "spec")
    k = max(1, min(50, int(args.get("k") or 20)))
    count = bool(args.get("count"))
    out = _http(port, "POST", "/membrane/entity-query",
                {"q": q, "k": k, "count": count})
    if out.get("text"):
        return str(out["text"])
    return "no entities match"


def tool_method_call_local(args: dict, port: int) -> str:
    """method_call for the local models (server.mcp_executor): the short head."""
    return tool_method_call(args, port, head_cap=LOCAL_LINK_HEAD)


def _steps_arg(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, list) else None


def method_signature(method: str) -> tuple[str, list[str]]:
    """'exec(code, timeout)' -> ('exec', ['code', 'timeout']); a bare name -> (name, [])."""
    m = re.match(r"^\s*([A-Za-z_][\w]*)\s*\((.*)\)\s*$", str(method or ""))
    if not m:
        return str(method or "").strip(), []
    return m.group(1), [x.strip() for x in m.group(2).split(",") if x.strip()]


def tool_method_define(args: dict, port: int) -> str:
    name, sig = method_signature(str(args.get("method") or ""))
    out = _http(port, "POST", "/membrane/method/define", {
        "on": str(args.get("on") or args.get("object") or ""), "method": name,
        "command": str(args.get("command") or args.get("commands") or ""),
        "read_only": bool(args.get("read_only")), "guard": str(args.get("guard") or ""),
        "steps": _steps_arg(args.get("steps")),
        "abstract": str(args.get("abstract") or "").lower() in ("true", "1", "yes"),
        "params": _listish(args.get("params")) or sig})
    if not out.get("ok"):
        return f"define failed: {out.get('error', 'unknown error')}"
    p = out.get("params") or []
    if out.get("abstract"):
        return (f"abstract method {out['method']}({', '.join(p)}) declared -- its owner is an interface; "
                f"each entity inheriting it must implement {out['method'].split('.')[-1]} itself "
                f"(exec(action='define', on=<it or a class between>, method=..., command=...))")
    return (f"method {out['method']}{('{' + ','.join(p) + '}') if p else ''} "
            f"{'defined' if out.get('created') else 'updated'} -- call it on any object that is (or "
            f"inherits from) its owner: exec(action='call', object=..., method=...)")


def _ran(out: dict) -> str:
    """#588: what ran -- the command line, or a composite's steps with their rc --
    and, on a halt, the failing step with its output."""
    steps = out.get("steps") if isinstance(out.get("steps"), dict) else {}
    if str(out.get("fed") or "").strip() or not steps:
        line = f"ran: {str(out.get('fed') or '')[:300]}"
    else:
        line = "ran: " + " > ".join(f"{name} (rc {v.get('rc')})" for name, v in steps.items())
    bad = next(((n, v) for n, v in steps.items() if v.get("rc") not in (0, None)
                or v.get("status") in ("error", "blocked", "timeout")), None)
    if bad and (out.get("error") or out.get("rc") not in (0, None)):
        n, v = bad
        line += f"\nhalted at {n}: rc {v.get('rc')}" + (f" -- {str(v.get('head') or '')[:300]}" if v.get("head") else "")
    return line


def tool_method_call(args: dict, port: int, head_cap: int | None = None) -> str:
    """A method call; the run's full output rides the reply up to
    LINK_OUTPUT_CAP; `head_cap` (the local models'
    path) keeps the short head instead."""
    def shown(out: dict) -> str:
        if head_cap is not None:
            return f"head: {str(out.get('head') or '')[:head_cap]}"
        return _link_output(out)
    a = args.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = None
    if isinstance(a, list):              # positional args (issue #61): the seat binds them in order
        out = _http(port, "POST", "/membrane/method/call", {
            "object": str(args.get("object") or args.get("spec") or ""),
            "method": str(args.get("method") or ""), "args": a})
        if not out.get("object") or out.get("status") == "blocked":
            return (f"call {out.get('status', 'blocked')}: {out.get('error', 'unknown error')}"
                    + (f" [resolution order: {' > '.join(out['mro'])}]" if out.get("mro") else ""))
        return (f"[{out.get('status')}] {out['method']} on {out['object']} (via {out['via']}): "
                f"rc={out.get('rc')} {out.get('outcome') or out.get('status')}\n{_ran(out)}\n"
                f"{shown(out)}")
    a = dict(a) if isinstance(a, dict) else {}
    # THE PARAM BAG (issue #5 residual): the models put the wake's
    # about/at/prompt (and the browser executor's code) at the TOP of the
    # call -- only object/method/args rode the wire, so a
    # `llm.wake about=x at=in 1h` reached the template with NO params and
    # rc=2'd into a usage error instead of planting a wake
    for k in ("about", "at", "prompt", "code"):
        if k in args and k not in a and args[k] not in (None, "") \
                and not isinstance(args[k], (dict, list)):
            a[k] = args[k]
    out = _http(port, "POST", "/membrane/method/call", {
        "object": str(args.get("object") or args.get("spec") or ""),
        "method": str(args.get("method") or ""), "args": a if isinstance(a, dict) else {}})
    if not out.get("object") or out.get("status") == "blocked":
        return (f"call {out.get('status', 'blocked')}: {out.get('error', 'unknown error')}"
                + (f" [resolution order: {' > '.join(out['mro'])}]" if out.get("mro") else ""))
    return (f"[{out.get('status')}] {out['method']} on {out['object']} (via {out['via']}; order "
            f"{' > '.join(out.get('mro') or [])}): rc={out.get('rc')} {out.get('outcome') or out.get('status')}\n"
            f"{_ran(out)}\n{shown(out)}")


def tool_code_run(args: dict, port: int, head_cap: int | None = None) -> str:
    """Run code through its tree (#614); the output rides the reply like a
    method call's (`head_cap`: the local models' short head)."""
    body = {k: str(args[k]) for k in ("code", "lang", "node", "on", "timeout") if args.get(k) not in (None, "")}
    out = _http(port, "POST", "/membrane/code/run", body)
    if not out.get("root") or (not out.get("ok") and out.get("rc") is None):
        return f"run refused: {out.get('error', 'unknown error')}" + (f" [root {out['root']}]" if out.get("root") else "")
    shown = (f"head: {str(out.get('head') or '')[:head_cap]}" if head_cap is not None else _brief_output(out))
    return (f"[{out.get('outcome') or ('ok' if out.get('ok') else 'failed')}] {out.get('text') or ''}\n"
            f"root {out['root']} on {out.get('executor')} -> result {out.get('result')}: rc={out.get('rc')}\n{shown}")


def tool_code_run_local(args: dict, port: int) -> str:
    return tool_code_run(args, port, head_cap=LOCAL_LINK_HEAD)


def tool_bash(args: dict, port: int) -> str:
    """bash: code_run under the shell tool's familiar name (toolset.bash_args)."""
    from .toolset import bash_args
    return tool_code_run(bash_args(args), port)


def tool_bash_local(args: dict, port: int) -> str:
    from .toolset import bash_args
    return tool_code_run_local(bash_args(args), port)


def tool_method_inspect(args: dict, port: int) -> str:
    """A method's read (issue #80): where a call resolves, the definition, and the
    dry run -- nothing runs."""
    a = args.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = None
    out = _http(port, "POST", "/membrane/method/inspect", {
        "object": str(args.get("object") or args.get("spec") or ""),
        "method": str(args.get("method") or ""), "args": a if isinstance(a, (dict, list)) else None})
    return json.dumps(out, indent=1)[:4000]


def tool_method_each(args: dict, port: int) -> str:
    a = args.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = None
    out = _http(port, "POST", "/membrane/method/each", {
        "over": str(args.get("over") or args.get("each") or ""), "method": str(args.get("method") or ""),
        "args": a if isinstance(a, dict) else {}, "max": args.get("max"),
        "dry_run": bool(args.get("dry_run")) and str(args.get("dry_run")).lower() not in ("false", "0"),
        "on_fail": str(args.get("on_fail") or "abort")})
    skip = (f" | skipped {out['n_unconfirmed']} unconfirmed (the class getter never read them -- "
            f"likely text artifacts; check, then entity remove or give them a reading): "
            + ", ".join(out.get("unconfirmed") or [])) if out.get("n_unconfirmed") else ""
    if out.get("dry_run"):
        return (f"dry run: {out['method']} over {out['over']} would reach {out['n']} (max {out['max']}): "
                + ", ".join(out.get("members") or []) + skip)
    head = f"[{out.get('status')}] {out.get('method')} over {out.get('over')}: "
    if out.get("status") == "blocked" and not out.get("results"):
        return head + str(out.get("error")) + skip
    rows = "; ".join(f"{r['object']}:{r['status']}" for r in (out.get("results") or [])[:40])
    return (head + f"{out.get('n_ok', 0)}/{out.get('n')} ok, ran {out.get('ran')}"
            + (" (stopped at the first failure; on_fail='continue' runs the rest)" if out.get("stopped") else "")
            + f" -- {rows}" + skip)


def tool_entity_read(args: dict, port: int) -> str:
    """The read-back tool: the entity's value AFTER processing. `prop`
    extracts one property in full (the whole-properties blob is still
    clipped at ~800 chars, so a ring-backed lane log -- voice heard/said --
    lost its newest entries to that cut); `tail` pages a list-valued
    property from its newest end (issue #105)."""
    import urllib.parse
    spec = urllib.parse.quote(_arg(args, "spec", "eid", "entity", "name",
                                    "key", "id", "object"))     # Cliffhanger's word for a target
    qs = f"/membrane/entity/read?spec={spec}"
    if args.get("run"):
        qs += "&run=1"
    if args.get("nofire"):
        qs += "&nofire=1"
    prop = _arg(args, "prop", "property", "field")
    tail = None
    if args.get("tail") is not None:
        try:
            tail = max(1, int(args.get("tail")))
        except (TypeError, ValueError):
            tail = None
    span = str(args.get("lines") or "").strip()
    if span and not prop:
        prop = "output"                          # a run's kept output (brief results)
    out = _http(port, "GET", qs)
    if not out.get("ok"):
        return f"read failed: {out.get('error', 'unknown error')}"
    m = out.get("message")
    if isinstance(m, dict) and m.get("text") and not prop and not span:
        # a chat message (2026-11-11, Rosie forgot): the stored text in full, not field fragments
        head = " · ".join(x for x in (str(out.get("eid") or ""), m.get("role") or "", m.get("who") or "",
                                      m.get("time") or "",
                                      f"in {m.get('title') or m.get('session')}" if (m.get('title') or m.get('session')) else "")
                          if x)
        return f"message {head}\n{m['text']}"
    props = out.get("properties") or {}
    if span and not isinstance(props.get(prop), str):
        return (f"lines={span!r}: {out.get('eid')} has no text property {prop!r} to page"
                + (f" (it is {type(props[prop]).__name__})" if prop in props else ""))
    if span and isinstance(props.get(prop), str):
        all_lines = props[prop].split("\n")
        total = len(all_lines)
        try:
            a, _, b = span.partition("-")
            a, b = int(a), int(b or a)
        except ValueError:
            return f"lines={span!r}: give A-B, 1-based (it has {total} lines)"
        if a < 1 or a > total or b < a:
            return f"lines={span!r} is outside {prop}: it has lines 1-{total}"
        b = min(b, total)
        page, used, last = [], 0, a - 1
        for i in range(a - 1, b):              # a page never stops silently (Memo on #912)
            ln = all_lines[i]
            room = BRIEF_CAP * 2 - used
            if len(ln) > room:
                if not page:                       # one line longer than a page: say how to get it whole
                    page.append(ln[:room] + f" …[line cut: {len(ln)} chars; whole: prop={prop}]")
                    last = i + 1
                break
            page.append(ln)
            used += len(ln) + 1
            last = i + 1
        more = (f"; cut at {BRIEF_CAP * 2} chars -- continue with lines={last + 1}-{b}" if last < b else "")
        return f"{out.get('eid')} {prop}, lines {a}-{last} of {total}{more}:\n" + "\n".join(page)
    g = out.get("getter") or {}
    lines = [f"entity {out.get('eid')} [{out.get('cls')}] "
             f"(post-processing value):"]
    if prop:
        if prop not in props:
            have = ", ".join(sorted(props)) or "(none)"
            lines.append(f"no property {prop!r} (properties: {have})")
        else:
            val = props[prop]
            if isinstance(val, list):
                items = val[-tail:] if tail else val
                lines.append(f"{prop} ({len(val)} item(s)"
                             f"{f', tail {tail}' if tail else ''}):")
                for it in items:
                    lines.append("- " + json.dumps(it, default=str))
            else:
                lines.append(f"{prop}: " + json.dumps(val, default=str))
    elif props:
        lines.append("properties: " + json.dumps(props, default=str)[:800])
    gg = out.get("goal_graph")
    if isinstance(gg, dict) and not prop:            # #738: what waits on what, what runs, the receipts
        lines.append(f"the goal's graph: {len(gg.get('tasks') or [])} task(s), {len(gg.get('cards') or [])} card(s)")
        for t in gg.get("tasks") or []:
            lines.append(f"- task {t.get('eid')} [{t.get('status')}] {t.get('title')}"
                         + (f" waits on {', '.join(t['waits_on'])}" if t.get("waits_on") else "")
                         + (f" -- receipt: {str(t.get('receipt'))[:120]}" if t.get("receipt") else ""))
        for c in gg.get("cards") or []:
            lines.append(f"- card {c.get('eid')} [{c.get('status')}] {c.get('name')}"
                         + (f" blocks {', '.join(c['blocks'])}" if c.get("blocks") else ""))
    runs = (out.get("pending") or []) + [dict(d, queued="done")
                                         for d in (out.get("done") or [])]
    if runs:
        lines.append(f"the queue: {len(out.get('pending') or [])} pending "
                     f"(owed), {len(out.get('done') or [])} done "
                     f"(processed -- residue lives on each command's doc)")
        for e in runs[:6]:
            lines.append(f"- [{e.get('queued', 'pending')}] "
                         f"{e.get('eid')}: rc={e.get('run_rc')} "
                         f"{e.get('run_outcome')} "
                         f"({e.get('run_ms', 0)}ms) "
                         f"cmd: {str(e.get('command'))[:80]}")
    if g.get("fired"):
        if g.get("error"):
            lines.append(f"getter fired: ERROR {str(g.get('error'))[:120]}")
        elif g.get("run_result"):
            rr = g["run_result"]
            lines.append(f"getter exec: rc={rr.get('rc')} "
                         f"{rr.get('outcome')} head: "
                         f"{str(rr.get('head'))[:160]}")
        else:
            lines.append(f"getter fired: {json.dumps(g.get('state') or {}, default=str)[:300]}")
        if g.get("note"):
            lines.append(f"getter note: {g['note']}")
    elif g.get("reason"):
        lines.append(f"getter not fired ({g.get('reason')}).")
    for e in runs[:6]:
        lines.append(f"- {e.get('eid')}: rc={e.get('run_rc')} "
                     f"{e.get('run_outcome')} "
                     f"({e.get('run_ms', 0)}ms) "
                     f"cmd: {str(e.get('command'))[:80]}")
    return "\n".join(lines)



def tool_pack(args: dict, port: int) -> str:
    """#1043: list / read / write a .c1f pack on the seat's field engine."""
    body = {"action": str(args.get("action") or "list")}
    for f in ("name", "c1f"):
        if args.get(f) not in (None, ""):
            body[f] = str(args[f])
    out = _http(port, "POST", "/membrane/pack", body)
    if body["action"] == "list" and out.get("ok"):
        return "\n".join(f"- {p['name']} ({p['source']})" + (f" ERROR: {p['error']}" if p.get("error") else "")
                         for p in out.get("packs") or []) or "no packs"
    if body["action"] in ("show", "read") and out.get("ok"):
        return out.get("c1f", "") + (f"\n-- ERROR: {out['error']}" if out.get("error") else "")
    if out.get("ok"):
        return f"Pack {out.get('name')} loaded ({out.get('eid')}); classes now: {', '.join(out.get('classes') or [])}"
    err = out.get("error")
    return f"Pack refused: {err.get('message') if isinstance(err, dict) else err}"


def tool_write(args: dict, port: int) -> str:
    """#1043: entity.prop = value -- acting by writing (an entity edit of one property)."""
    spec, prop = _arg(args, "spec", "eid", "entity", "name"), str(args.get("prop") or "").strip()
    if not spec or not prop:
        return "write needs spec and prop"
    out = _http(port, "POST", "/membrane/entity/edit", {"spec": spec, "properties": {prop: args.get("value")}})
    if not out.get("ok"):
        return f"Write failed: {out.get('error', 'unknown error')}"
    return (f"Wrote {out.get('eid') or spec}.{prop}. Whatever reacts to it runs now; an effect's receipt lands as "
            f"{prop}_result on the entity -- read it back before you claim it happened.")


def tool_ctx_enter(args: dict, port: int) -> str:
    out = _http(port, "POST", "/membrane/ctx", {"op": "enter", "spec": str(args.get("spec") or args.get("name") or "")})
    return out.get("line") if out.get("ok") else f"enter failed: {out.get('error')}"


def tool_ctx_exit(args: dict, port: int) -> str:
    out = _http(port, "POST", "/membrane/ctx", {"op": "exit"})
    return out.get("line") if out.get("ok") else f"exit failed: {out.get('error')}"


# the argument schemas of those methods: not in the tool list; `help('tools')` carries each method's arguments
METHOD_TOOL_DEFS = [
    {
            "name": "intend",
            "description": (
                'plant own goal (shared Goal, pinned on this seat). open goals steer the dream loop + ride recall. never auto-closed: close with fulfill+evidence.'),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "The commitment, one sentence."},
                    "ttl": {"type": "string",
                            "description": "Optional priority-pin decay window "
                                           "('2w'/'10d'); the goal itself never "
                                           "expires."},
                },
                "required": ["text"],
            },
        },
    {
            "name": "fulfill",
            "description": (
                'close an open goal PROVEN done: spec=goal, evidence=receipt (rc=0 output / entity read back). never on intent or plan; else take its next step.'),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string",
                             "description": "The goal's eid (or a unique "
                                            "name fragment)."},
                    "evidence": {"type": "string",
                                 "description": "The receipt that proves it "
                                                "done, one or two sentences."},
                },
                "required": ["spec", "evidence"],
            },
        },
    {
            "name": "intents",
            "description": (
                'read own intent board (goals steering this seat), open+closed. check before planting a duplicate.'),
            "inputSchema": {"type": "object", "properties": {}},
        },
    {
            "name": "answer_card",
            "description": (
                "relay the person's chat answer to a Decision card: card=eid, value, quote=their words verbatim (must match one of their messages; kept in ledger). unclear -> ask. chat lane only."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "card": {"type": "string", "description": "The card's eid."},
                    "value": {"description": "The answer, of the card's var type."},
                    "quote": {"type": "string",
                              "description": "The person's own words that give the answer."},
                },
                "required": ["card", "value", "quote"],
            },
        },
    {
            "name": "learn",
            "description": (
                'store ONE durable earned lesson (a note, not a transcript; no secrets, personal data, code, fences). guards: dedup, variety; may skip.'),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "lesson": {"type": "string",
                               "description": "One sentence-ish durable lesson."},
                },
                "required": ["lesson"],
            },
        },
    {
            "name": "forget",
            "description": (
                "spec='#3-7, 9' deletes those cells (capped); wordy spec = proposal only, deletes nothing. after deleting prefer learn with the correction."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string",
                             "description": "'#N' / '#N-M, K' indexes, or a "
                                            "wordy topic (proposal only)."},
                },
                "required": ["spec"],
            },
        },
    {
            "name": "junk_cut",
            "description": (
                "tune the junk cut: how strictly machinery (tool output, ps/lsof tables, test bars, logs, config dumps) stays out of banner stories + watch triggers. per track | 'all' | global default. CUT 0..1, higher = stricter. anchor-vector screen (junk embeds near anchor entities in the field). audited to the operator."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string",
                             "description": "Track key (entity-track key or "
                                            "watch #N), 'all', or 'default'."},
                    "cut": {"type": "number",
                            "description": "0..1 (higher = stricter); null "
                                           "clears back to the default."},
                },
                "required": ["spec", "cut"],
            },
        },
    {
            "name": "entity_watch",
            "description": ("track: entity live state in WHERE WE ARE each turn; boundary 'enter N'|'exit N' fires once per crossing (banner, or owner 'watch:<eid>' next turn). keys files:<path> models:<name> symbols:<qualname>. field topic -> field_watch; relational/motion -> entity_edit watch_ops/watch_fields. one shared attention budget."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string",
                            "description": "Entity key, 'kind:value'."},
                    "boundary": {"type": "string",
                                 "description": "Optional: 'enter N' / 'exit N' "
                                                "(bare N = enter N)."},
                    "label": {"type": "string",
                              "description": "Optional display label."},
                    "owner": {"type": "string",
                              "description": "Whose watch: '' (banner ALERTs) "
                                             "or a listener's agent name "
                                             "('watch:<eid>' -- the crossing "
                                             "rides that listener's next "
                                             "turn)."},
                },
                "required": ["key"],
            },
        },
    {
            "name": "entity_unwatch",
            "description": (
                "remove tracks: key exact ('files:router.py') or kind prefix ('files:' = all)."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                },
                "required": ["key"],
            },
        },
    {
            "name": "field_watch",
            "description": (
                "probe a topic region: each NEW memory inside its radius reported next turn. radius earns its size (swallow all/none -> dormant). board holds 7 in one attention budget (with watch_ops/watch_fields); full -> plant refused with the live list, drop one first. action plant(topic)|drop('#N'|label fragment|'all')|list. owner = your agent."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["plant", "drop", "list"]},
                    "topic": {"type": "string",
                              "description": "plant: what to watch for (the text is embedded)."},
                    "spec": {"type": "string",
                             "description": "drop: '#N', a label fragment, or 'all'."},
                    "owner": {"type": "string",
                              "description": "optional; defaults to your agent."},
                },
                "required": ["action"],
            },
        },
    {
            "name": "entity_tracks",
            "description": (
                "list tracks: key, label, owner, boundary, inside/outside, pending alerts. owner='watch:<eid>' filters, ''=all. check before planting a duplicate."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string",
                              "description": "Optional: filter to one owner "
                                             "('watch:<eid>'); '' = all."},
                },
            },
        },
]


# The tools that are not field manipulation or querying are METHODS, reached by name through `call` (operator,
# 2026-11-12). Their implementations below are a labelled bridge: phase B moves each into c1f as the system behind it
# (the intent board, watches, dials, memory, the peer channel) moves.
METHOD_TOOLS = ("intend", "fulfill", "intents", "answer_card", "learn", "forget", "junk_cut",
                "entity_watch", "entity_unwatch", "field_watch", "entity_tracks")


def _method_tool_refusal(name: str) -> str | None:
    """The lane rules that applied to the tool when it was listed, applied to the method of the same name."""
    from . import diag_requests as _dr
    from . import server as _srv
    cur = _dr.current() or {}
    agent = str(cur.get("agent") or "")
    lane = _srv._lane_of(agent)
    return _srv._lane_refusal(name, lane) or _srv._graph_refusal(name, agent)


def tool_dunder_call(args: dict, port: int) -> str:
    """#1101 __call: a bare method name, resolved through the context stack, called like method_call."""
    a = args.get("args")
    if not isinstance(a, (dict, list)):
        a = {k: v for k, v in args.items() if k not in ("method", "name", "args")}
    meth = str(args.get("method") or args.get("name") or "")
    legacy = meth in METHOD_TOOLS and meth in TOOL_IMPLS
    if legacy:
        why = _method_tool_refusal(meth)
        if why:
            return why
    try:
        out = _http(port, "POST", "/membrane/ctx", {"op": "call", "method": meth, "args": a})
    except Exception as e:                           # noqa: BLE001 -- a miss for the bridge below
        if not legacy:
            raise
        out = {"ok": False, "error": repr(e)}
    if legacy and not out.get("ok") and not out.get("object"):
        # no method of the model's own by that name in its context: the bridge (the Python behind the old tool)
        return TOOL_IMPLS[meth](a if isinstance(a, dict) else {}, port)
    if not out.get("ok") and not out.get("object"):
        return f"call refused: {out.get('error', 'unknown error')}"
    r = out.get("resolved") or {}
    return (f"[{out.get('status')}] {meth} on {r.get('object') or out.get('object')} (found in {r.get('level')}): "
            f"rc={out.get('rc')} {out.get('outcome') or ''}\n{_brief_output(out)}")


def _fs_path(p) -> str:
    import os as _os
    return _os.path.expanduser(str(p or "").strip())


def tool_fs_read(args: dict, port: int) -> str:
    """#1101 (no-bash): Read path -> the file's text, as the field's read(path) getter sees it."""
    p = _fs_path(args.get("path"))
    if not p:
        return "read: a path is required"
    try:
        with open(p, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return f"read {p}: {e}"
    off = max(0, int(args.get("offset") or 1) - 1)
    lim = int(args.get("limit") or 2000)
    body = "\n".join(f"{i + 1:6}\t{ln}" for i, ln in enumerate(lines[off:off + lim], start=off))
    return f"read({p!r}) -- {len(lines)} lines\n{body}"


def tool_fs_write(args: dict, port: int) -> str:
    p = _fs_path(args.get("path"))
    if not p:
        return "write: a path is required"
    try:
        import os as _os
        if _os.path.dirname(p):
            _os.makedirs(_os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(str(args.get("content") or ""))
    except OSError as e:
        return f"File:{p}.write failed: {e}"
    return f"File:{p}.write done ({len(str(args.get('content') or ''))} chars)"


def tool_fs_edit(args: dict, port: int) -> str:
    p = _fs_path(args.get("path"))
    old, new = args.get("old"), args.get("new")
    if not p or old is None or new is None:
        return "edit: path, old_string and new_string are required"
    try:
        with open(p) as f:
            text = f.read()
    except OSError as e:
        return f"edit {p}: {e}"
    n = text.count(str(old))
    if n == 0:
        return f"edit {p}: old_string not found"
    if n > 1 and not args.get("all"):
        return f"edit {p}: old_string occurs {n} times -- give more context or replace_all=true"
    text = text.replace(str(old), str(new)) if args.get("all") else text.replace(str(old), str(new), 1)
    return tool_fs_write({"path": p, "content": text}, port).replace("write done", "write done (edit)")


def tool_fs_list(args: dict, port: int) -> str:
    import fnmatch
    import os as _os
    p = _fs_path(args.get("path") or ".")
    pat = args.get("pattern")
    try:
        if pat and any(c in str(pat) for c in "*?[") and ("/" in str(pat) or "**" in str(pat)):
            import glob as _glob
            hits = sorted(_glob.glob(_os.path.join(p, str(pat)), recursive=True))[:300]
            return f"listdir({p!r}, {pat!r}) -- {len(hits)}\n" + "\n".join(hits)
        rows = sorted(_os.listdir(p))
    except OSError as e:
        return f"listdir {p}: {e}"
    if pat:
        rows = [r for r in rows if fnmatch.fnmatch(r, str(pat))]
    out = []
    for r in rows[:300]:
        full = _os.path.join(p, r)
        out.append(f"{r}/" if _os.path.isdir(full) else r)
    return f"listdir({p!r}) -- {len(rows)}\n" + "\n".join(out)


TOOL_IMPLS = {
    "ctx_enter": tool_ctx_enter,
    "ctx_exit": tool_ctx_exit,
    "entity_enter": tool_ctx_enter,
    "entity_exit": tool_ctx_exit,
    "__call": tool_dunder_call,
    "call": tool_dunder_call,
    "fs_read": tool_fs_read,
    "fs_write": tool_fs_write,
    "fs_edit": tool_fs_edit,
    "fs_list": tool_fs_list,
    "query": tool_query,
    "state": tool_state,
    "intend": tool_intend,
    "intents": tool_intents,
    "fulfill": tool_fulfill,
    "answer_card": tool_answer_card,
    "learn": tool_learn,
    "forget": tool_forget,
    "entity_watch": tool_watch_entity,
    "field_watch": tool_field_watch,
    "entity_unwatch": tool_unwatch_entity,
    "entity_tracks": tool_list_tracks,
    "entities_related": tool_related_entities,
    "entity_add": tool_entity_add,
    "entity_edit": tool_entity_edit,
    "pack": tool_pack,
    "entity_write": tool_write,
    "entity_remove": tool_entity_remove,
    "junk_cut": tool_set_junk_cut,
    "peer_seats": tool_peer_seats,
    "peer_read": tool_peer_read,
    "graph": tool_graph,
    "entity_query": tool_entity_query,
    "entity_inherit": tool_entity_inherit,
    "method_define": tool_method_define,
    "method_call": tool_method_call,
    "code_run": tool_code_run,
    "bash": tool_bash,
    "method_each": tool_method_each,
    "method_inspect": tool_method_inspect,
    "entity_read": tool_entity_read,
}
# the local models' toolset path (server.mcp_executor): the same tools, with
# method_call's short head (their context is small)
LOCAL_TOOL_IMPLS = {**TOOL_IMPLS, "method_call": tool_method_call_local,
                    "code_run": tool_code_run_local, "bash": tool_bash_local}


# ---- the JSON-RPC loop ---------------------------------------------------------

def _rpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


_CLIENT = ""      # #646: the client's name + version from initialize


def _client_caller() -> str:
    """The caller header: the client's name/version and this process's pid."""
    return f"{_CLIENT} pid {os.getpid()}" if _CLIENT else f"pid {os.getpid()}"


def _handle(req: dict, port: int) -> dict | None:
    """One request -> one response (or None for notifications)."""
    method = req.get("method") or ""
    req_id = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        global _CLIENT
        ci = params.get("clientInfo") or {}
        _CLIENT = " ".join(str(ci.get(k) or "") for k in ("name", "version")).strip()[:100]
        return _rpc_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        from . import cliffhanger_only as _CO
        return _rpc_result(req_id, {"tools": _CO.strip_tools(TOOLS)})
    if method == "tools/call":
        # ISSUE #60 (Memo): a route string as a tool name ('/exec') -- the slash
        # is no part of the name the registry advertises
        name = str(params.get("name") or "").strip().lstrip("/")
        impl = TOOL_IMPLS.get(name)
        if impl is None:
            return _rpc_error(req_id, -32602, f"unknown tool: {name}")
        try:
            text = impl(params.get("arguments") or {}, port)
        except ProxyError as e:
            # tool-level failure, not a protocol failure: MCP wants the
            # error IN the result (isError) so the model can read it.
            return _rpc_result(req_id, {
                "content": [{"type": "text",
                             "text": f"membrane tool error: {e.message}"}],
                "isError": True})
        except Exception as e:
            return _rpc_result(req_id, {
                "content": [{"type": "text",
                             "text": f"membrane tool error: {e}"}],
                "isError": True})
        return _rpc_result(req_id, {
            "content": [{"type": "text", "text": text}]})
    if req_id is None:
        return None                     # unknown notification: ignore
    return _rpc_error(req_id, -32601, f"method not found: {method}")


def serve(port: int, stdin=sys.stdin, stdout=sys.stdout) -> None:
    """Newline-delimited JSON-RPC over stdio until EOF."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _emit(stdout, _rpc_error(None, -32700, "parse error"))
            continue
        try:
            resp = _handle(req, port)
        except Exception as e:          # never let the loop die
            resp = _rpc_error(req.get("id") if isinstance(req, dict) else None,
                              -32603, f"internal error: {e}")
        if resp is not None:
            _emit(stdout, resp)


def _emit(stdout, obj: dict) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()


# ---- entry ---------------------------------------------------------------------

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        prog="membrane mcp",
        description="Membrane's memory as MCP tools (stdio; relays to the "
                    "running membrane proxy on 127.0.0.1:<port>).")
    ap.add_argument("--port", type=int, default=11500,
                    help="port of the RUNNING membrane proxy (default 11500)")
    args = ap.parse_args()
    # one boot-line on stderr (stdout is protocol-only)
    print(f"[membrane-mcp] stdio server up; proxy=127.0.0.1:{args.port}",
          file=sys.stderr)
    serve(args.port)


if __name__ == "__main__":
    main()

