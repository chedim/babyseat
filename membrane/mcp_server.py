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

TOOLS: list[dict] = [
    {
        "name": "query",
        "description": (
            "Read membrane's long-term memory: the nearest past exchanges for "
            "a topic (situation-response pairs with similarity, answered-model "
            "and seat provenance). Read-only, no model call, never learned. Use "
            "before redoing known work, when past-session context would "
            "help, or to check what the field already learned."),
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
            "Membrane's own live state: the entity field's vitals "
            "(live entities, events by stream, entities by tier, "
            "conflicts), the replay A/B ledger, and the last night pass. "
            "Read-only, computed from the running proxy. Use for "
            "memory-health checks."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "learn",
        "description": (
            "Write ONE durable earned lesson into membrane's field. A lesson "
            "is a note, not a transcript: no secrets, no user personal data, "
            "no code dumps, no fenced blocks. The proxy applies the same "
            "guards as any learned turn (dedup, variety gate) and may skip "
            "it. Use when you EARNED something durable (a fixed quirk, the "
            "command that worked, why a gate exists)."),
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
            "Delete memories. Index form (e.g. '#3-7, 9' or '12') DELETES "
            "those cells (capped). Wordier asks return candidate indexes as "
            "a PROPOSAL -- they never delete. After deleting, prefer a "
            "learn that overwrites the lesson with the correction."),
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
        "name": "entity_watch",
        "description": (
            "Manage your banner ENTITY TRACKS. A track shows the entity's "
            "live state in the WHERE WE ARE header every turn "
            "(constant tracking), and an optional boundary turns it into an "
            "EVENT trigger: 'enter N' alerts when the count rises to N+, "
            "'exit N' alerts when it falls below N. Alerts fire ONCE per "
            "transition (re-entry re-arms) and ride the banner as ALERT "
            "lines. Key forms: 'files:router.py', "
            "'models:glm-5.3-flash:cloud', 'symbols:<qualname>', "
            "'shell:git', 'ha:sensor.x'. No boundary = count-only track. "
            "Use to notice field drift around a component you are working "
            "on (e.g. 'enter 5' on a module you are refactoring, 'exit 2' "
            "on a model rung you depend on)."),
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
            },
            "required": ["key"],
        },
    },
    {
        "name": "entity_unwatch",
        "description": (
            "Remove entity tracks: exact key ('files:router.py') or a kind "
            "prefix ('files:' removes all file tracks)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "entity_tracks",
        "description": (
            "List your planted entity tracks: key, label, boundary, current "
            "inside/outside state, and pending alert count. Use before "
            "planting a duplicate track or to review your banner."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "entities_related",
        "description": (
            "Read the entity graph: entities related to a free-text query "
            "(named hits first, then semantic nearness), each with its "
            "mention count and co-occurrence neighbors. Use to explore what "
            "the field knows about a component before acting on it."),
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
            "The entity graph's QUERY LANGUAGE: enumerate/list/count "
            "with a one-line grammar. Terms: 'kind:<k|*>' (the eid's "
            "kind: f/s/m/c/tool/...), 'name~:<prefix>', "
            "'parent:<spec>' (the ISA ancestor chain includes spec), "
            "'children:<spec>' (the reverse walk), 'status:<s>', "
            "'near:<text>' (semantic). '&' conjoins, '|' disjoins. "
            "count=true renders 'N entity(ies)'. Examples: "
            "'kind:tool:* & status:open', 'children:membrane::c:deploy'"),
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
        "description": (
            "Add an abstract entity to the symbolic layer: a decision, "
            "failure mode, component, or goal with a one-line summary. "
            "Merge-on-collision: the same name updates the existing "
            "entity instead of duplicating. Audited to the operator."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class": {"type": "string",
                          "description": "Decision | FailureMode | "
                                         "Component | Goal | Model | Other "
                                         "(default Component)."},
                "name": {"type": "string"},
                "summary": {"type": "string",
                            "description": "One line: what it is / what "
                                           "was decided / what fails."},
                "links": {"type": "array",
                          "description": "Field cell indexes to link."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "entity_edit",
        "description": (
            "Edit an entity: resolve by eid or exact name, patch summary/"
            "links/class (only provided fields change). merged_into marks "
            "a subsumption: the source doc stays, pointing at its "
            "survivor (links unioned; the merge announces). "
            "nudge_script/delete_script: PROGRAMMABLE ENTITIES -- small "
            "python lifecycle scripts (<= 2048/1024 chars) that run at "
            "consolidation time, see the entity's geometry as the "
            "'state' variable (position, ring, velocity, properties, "
            "data), and set 'result' = {render: str, data: dict, "
            "delete: bool}. The nudge's render replaces the track's "
            "default banner line; the delete script retires the entity "
            "(tombstoned, announced). 3 consecutive failures auto-"
            "disable; empty string clears. "
            "thought_script/dreamed_script/tick_script: TRIGGER-"
            "TYPED policy scripts (the getter/setter organ) -- run "
            "off-turn at the thinker/dreamer hooks (mention fires when "
            "the entity appears in a turn's tags, thought when the "
            "thinker extends a branch under it, dreamed on dream "
            "commits, tick on the THINKER'S ~1s heartbeat, paced by "
            "the entity's own `tick_every` property, cooldown-free), "
            "under NARROW budgets (0.25s/1s/2s, the tick 0.25s) with "
            "the READ-ONLY membrane object next "
            "to state (membrane.field.recall/vitals, "
            "membrane.me.describe/velocity/direction, "
            "membrane.entities.get, membrane.timeline.walk, "
            "membrane.intents.open, membrane.speak -- the planner-gated "
            "voice: ONE line per fire, stay-silent holds). 'result' = {render, patch: dict "
            "of this entity's own properties (the monitoring organ: a "
            "getter may MOVE the entity, e.g. toward 'done' "
            "coordinates), delete}. The engine applies; scripts never "
            "mutate directly. The SEAT-FIRST FIELD: your entity store "
            "is LOCAL (authoritative, never auto-synced) and the "
            "global tier holds only PUBLISHED entities. "
            "publish=True exports the entity under a per-origin key "
            "(pub:<origin>:<eid>) -- your decision, your version line "
            "(republishing bumps it); imports elsewhere take the last "
            "STATE, never your code. unpublish=True retracts YOUR "
            "export (their import freezes at its last version). "
            "membrane::presence is published for you once a minute."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string",
                         "description": "eid or exact name."},
                "publish": {"type": "boolean",
                            "description": "export to the global tier "
                                           "(per-origin key, versioned; "
                                           "your call -- the wire "
                                           "carries state, no code)."},
                "unpublish": {"type": "boolean",
                              "description": "retract YOUR export "
                                             "(imports elsewhere freeze "
                                             "at their last version)."},
                "summary": {"type": "string"},
                "links": {"type": "array"},
                "class": {"type": "string"},
                "merged_into": {"type": "string",
                                "description": "eid or name of the "
                                               "survivor entity."},
                "nudge_script": {"type": "string",
                                 "description": "python; runs at "
                                                "consolidation; sets "
                                                "'result'."},
                "delete_script": {"type": "string",
                                  "description": "python; runs first; "
                                                 "set result.delete=True "
                                                 "to retire."},
                "mention_script": {"type": "string",
                                   "description": "python; runs when the "
                                                  "entity is mentioned "
                                                  "(0.25s budget); sets "
                                                  "'result' = {render, "
                                                  "patch, delete}."},
                "thought_script": {"type": "string",
                                   "description": "python; runs when the "
                                                  "thinker extends a "
                                                  "branch under it (1s "
                                                  "budget); sets 'result'."},
                "dreamed_script": {"type": "string",
                                   "description": "python; runs on dream "
                                                  "commit/elect under it "
                                                  "(2s budget); sets "
                                                  "'result'."},
                "tick_script": {"type": "string",
                                "description": "python; runs on the "
                                               "THINKER'S ~1s heartbeat, "
                                               "paced by this entity's "
                                               "`tick_every` property "
                                               "(default 1 = every "
                                               "heartbeat), cooldown-free "
                                               "(0.25s budget); sets "
                                               "'result'. The seat's own "
                                               "clock (tool:clock) is "
                                               "seeded with one."},
                "watch_script": {"type": "string",
                                 "description": "python; the AUTONODE's "
                                                "watcher body -- runs when "
                                                "an external event this "
                                                "entity watches arrives "
                                                "(file changes under the "
                                                "seat dir, MQTT topics, "
                                                "signals). May block but "
                                                "only 4 run at once, a >"
                                                "10s fire strikes the "
                                                "3-strikes lane (the "
                                                "thread can't be killed). "
                                                "The trigger's payload "
                                                "rides state.extra"
                                                ".payload (path/event/"
                                                "topic/name); the seeded "
                                                "tool:filetree is the "
                                                "template."},
                "watch": {"type": "object",
                          "description": "what external events fire the "
                                         "watch_script: {kind: "
                                         "'file'|'mqtt'|'signal', "
                                         "glob/topic/name optional "
                                         "narrowing -- the CHEAP "
                                         "pre-filter; the script does "
                                         "the real filtering}. "
                                         "kind 'file' is the default "
                                         "for any watcher without one."},
                "getter_script": {"type": "string",
                                  "description": "python; the READ-BACK "
                                                 "trigger -- runs when "
                                                 "the entity is READ "
                                                 "(the model's read of a "
                                                 "dynamic diagnostic "
                                                 "entity); sets 'result'"
                                                 "= {state: dict of the "
                                                 "entity's CURRENT facts}"
                                                 " -- applied as "
                                                 "script_data; the 5s "
                                                 "LRU paces. Empty "
                                                 "string clears."},
                "watch_ops": {"type": "array",
                              "description": "the SPATIAL watch ops "
                                             "(the always-watched lane): "
                                             "op 'away' {rel, y} (the "
                                             "entity moved from rel > "
                                             "y), op 'near' {rel, z} "
                                             "(closed to within z), op "
                                             "'around' {rel: '*', z} "
                                             "(anything within z -- the "
                                             "sensor; bursts GROUP into "
                                             "one event). '[]' clears; "
                                             "cap 8. Each change fires "
                                             "a watch-attached/detached "
                                             "event to the pool."},
                "properties": {"type": "object",
                               "description": "bounded property patch "
                                              "(the model's write path "
                                              "for goal state: status "
                                              "'fulfilled'/'dropped', "
                                              "progress counters, "
                                              "etc. -- the engine's "
                                              "script_* keys are "
                                              "untouchable)."},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "entity_remove",
        "description": (
            "Remove an entity (noise cleanup): resolve by eid or exact "
            "name. Ambiguous fragments remove nothing."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "junk_cut",
        "description": (
            "Tune the junk cut: how aggressively machinery (raw tool "
            "output -- ps/lsof tables, test bars, log lines, config "
            "dumps) is kept out of YOUR banner stories and watch "
            "triggers. Per-track (raise for a paranoid trace, lower for "
            "a permissive one), 'all' tracks at once, or the global "
            "default. The screen is anchor-vector based: junk embeds "
            "near anchor entities stored IN the field; prose stays far. "
            "CUT 0..1 -- higher = stricter. Audited to the operator."),
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
        "name": "peer_post",
        "description": (
            "Post a message to the PEER CHANNEL: a chat lane between the "
            "LLMs, visible to the operator on every connected opencode "
            "seat. Use for cross-seat coordination -- handoffs, "
            "questions to another seat's model, state announcements. "
            "Unread messages ride YOUR banner automatically."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "The message (capped 2000)."},
                "reply_to": {"type": "string",
                             "description": "Optional seat id replying to."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "graph",
        "description": "The entity graph's subgraph reader (the model's "
                       "eyes on the universe map): entities related to a "
                       "query -- named hits first, then centroid-nearest "
                       "-- as nodes (id/class/label/position/status/"
                       "curiosity/fn_counts/properties) + edges (ISA/"
                       "links/merged_into). The same data contract the "
                       "universe viewer renders; other clients consume "
                       "the same endpoints.",
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
            "ENTITY INHERITANCE: give an entity a parent concept ('a "
            "letter points to letters'). The child inherits the parent's "
            "constraints (latch level, the `living` flag) down the ISA "
            "chain; the child's geometry sits at the parent's position "
            "plus its own offset. Cycles are rejected."),
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
]


# ---- transport helpers -------------------------------------------------------

class ProxyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


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
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("error", "")
        except Exception:
            detail = e.reason
        raise ProxyError(e.code, str(detail)) from None
    except (urllib.error.URLError, OSError) as e:
        raise ProxyError(503, f"membrane proxy unreachable on :{port} "
                              f"({e})") from None


# ---- tool implementations (all guards live proxy-side) ------------------------

def tool_query(args: dict, port: int) -> str:
    topic = str(args.get("topic") or "").strip()
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
    payload = str(args.get("lesson") or "").strip()
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
    endpoint (the proxy holds the EntityStore; guards live there)."""
    key = str(args.get("key") or "").strip()
    if not key:
        raise ProxyError(400, "key required (e.g. 'files:router.py')")
    out = _http(port, "POST", "/membrane/entity-track", {
        "key": key,
        "boundary": str(args.get("boundary") or ""),
        "label": str(args.get("label") or ""),
    })
    if not out.get("ok"):
        return f"Track not planted: {out.get('error', 'unknown error')}"
    e, x = out.get("enter"), out.get("exit")
    bs = (f" with boundary: enter {e}" if e is not None
          else f" with boundary: exit {x}" if x is not None else "")
    return (f"Entity track planted: {out['key']}{bs}. Its live state "
            f"rides the WHERE WE ARE header each turn; a boundary "
            f"transition (enter/exit) alerts once via the banner.")


def tool_unwatch_entity(args: dict, port: int) -> str:
    key = str(args.get("key") or "").strip()
    if not key:
        raise ProxyError(400, "key required (exact key or kind prefix)")
    out = _http(port, "POST", "/membrane/entity-track/remove", {"key": key})
    n = int(out.get("removed") or 0)
    return f"Removed {n} entity track(s)." if n \
        else "No matching tracks (list them via entity_tracks)."


def tool_list_tracks(args: dict, port: int) -> str:
    out = _http(port, "GET", "/membrane/entity-track")
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
    q = str(args.get("query") or "").strip()
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
            nb = " -- " + ", ".join(f"{n['key']} ({n['shared']} shared)"
                                    for n in r["neighbors"][:3])
        lines.append(f"- {r['key']} ({r['cells']} mentions, {r.get('how', '')})"
                     f"{nb}")
    return "\n".join(lines)


def tool_entity_add(args: dict, port: int) -> str:
    out = _http(port, "POST", "/membrane/entity/add", {
        "class": str(args.get("class") or ""),
        "name": str(args.get("name") or ""),
        "summary": str(args.get("summary") or ""),
        "links": args.get("links") or [],
    })
    if not out.get("ok"):
        return f"Entity not written: {out.get('error', 'unknown error')}"
    how = "merged into the existing entity" if out.get("merged") \
        else "created"
    return (f"Entity {out['name']!r} [{out['class']}] {how}: {out['eid']} "
            f"(visible to entities_related; audited to the "
            f"operator).")


def tool_entity_edit(args: dict, port: int) -> str:
    body = {"spec": str(args.get("spec") or "").strip()}
    for f in ("summary", "links", "class", "merged_into",
              "nudge_script", "delete_script",
              "mention_script", "thought_script", "dreamed_script",
              "tick_script", "watch_script", "getter_script",
              "watch_ops", "watch", "properties"):
        if args.get(f) is not None:
            body[f] = args[f]
    for f in ("publish", "unpublish"):        # the tier-2 gestures
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
                    f"its last STATE, never your code. Publish is "
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
                f"(file changes under the seat dir, MQTT topics, "
                f"signals; the trigger's payload rides state.extra"
                f".payload). Watchers may block but only 4 run at "
                f"once, a >10s fire strikes (3 = auto-disabled). "
                f"The seeded tool:filetree is the template.")
    return (f"Entity {out['eid']} updated: class {out.get('class')}, "
            f"summary {out.get('summary')!r}, links {out.get('links')}.")


def tool_entity_remove(args: dict, port: int) -> str:
    spec = str(args.get("spec") or "").strip()
    if not spec:
        raise ProxyError(400, "spec required (eid or exact name)")
    out = _http(port, "POST", "/membrane/entity/remove", {"spec": spec})
    n = int(out.get("removed") or 0)
    return f"Removed {spec!r}." if n \
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


def tool_peer_post(args: dict, port: int) -> str:
    text = str(args.get("text") or "").strip()
    if not text:
        raise ProxyError(400, "text required")
    out = _http(port, "POST", "/membrane/peer/post", {
        "text": text, "reply_to": str(args.get("reply_to") or "")})
    if not out.get("ok"):
        return f"Post failed: {out.get('error', 'unknown error')}"
    return (f"Posted to the peer channel (visible to the operator on "
            f"every connected seat; unread messages ride their banners).")


def tool_intend(args: dict, port: int) -> str:
    """The model's pen on the steering wheel: create-or-pin a shared Goal
    + give it a priority pin on THIS seat's active set. Speech announces
    every activation; drop intent suppresses seat-locally; fulfill closes
    the goal shared."""
    body = {"text": str(args.get("text") or "").strip()}
    if args.get("ttl"):
        body["ttl"] = str(args["ttl"])
    out = _http(port, "POST", "/membrane/intend", body)
    if not out.get("ok"):
        return f"Intend failed: {out.get('error', 'unknown error')}"
    what = "created + pinned" if out.get("created") else "pinned (existing goal)"
    return (f"Goal active ({what}): {out.get('eid')}\n"
            f"The active set steers the dream loop; the banner shows it. "
            f"Drop intent #N suppresses it on this seat; fulfill #N closes "
            f"it shared.")


def tool_graph(args: dict, port: int) -> str:
    """The model's graph read: the entity graph's subgraph as nodes +
    edges -- the same contract the universe viewer renders."""
    import urllib.parse
    q = urllib.parse.quote(str(args.get("query") or ""))
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
        line = (f"- {e.get('key') or e.get('eid', '?')}"
                f" ({e.get('cells', 0)} cells, {e.get('how', '')})")
        neighbors = [f"{n.get('key') or n.get('eid', '?')}x{n.get('shared', '')}"
                     for n in (e.get("neighbors") or [])][:4]
        line += (f" | neighbors: {', '.join(neighbors)}" if neighbors
                 else "")
        lines.append(line)
    return "\n".join(lines)


def tool_entity_inherit(args: dict, port: int) -> str:
    spec = str(args.get("spec") or "").strip()
    parents = args.get("parents") or []
    if isinstance(parents, str):
        parents = [parents]
    if not spec or not parents:
        raise ProxyError(400, "spec + parents required")
    out = _http(port, "POST", "/membrane/entity/inherit",
                {"spec": spec, "parents": parents})
    if not out.get("ok"):
        return f"Inherit failed: {out.get('error', 'unknown error')}"
    added = out.get("added") or []
    if added:
        return (f"Entity {out['child']} ISA {', '.join(added)} -- "
                f"constraints inherited down the ISA chain (latch, "
                f"`living`); the child's position comes from its own "
                f"lived instances, the parents classify it.")
    return f"Entity {out['child']} parents refreshed (no new links)."


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
    q = str(args.get("q") or "").strip()
    k = max(1, min(50, int(args.get("k") or 20)))
    count = bool(args.get("count"))
    out = _http(port, "POST", "/membrane/entity-query",
                {"q": q, "k": k, "count": count})
    if out.get("text"):
        return str(out["text"])
    return "no entities match"



TOOL_IMPLS = {
    "query": tool_query,
    "state": tool_state,
    "learn": tool_learn,
    "forget": tool_forget,
    "entity_watch": tool_watch_entity,
    "entity_unwatch": tool_unwatch_entity,
    "entity_tracks": tool_list_tracks,
    "entities_related": tool_related_entities,
    "entity_add": tool_entity_add,
    "entity_edit": tool_entity_edit,
    "entity_remove": tool_entity_remove,
    "junk_cut": tool_set_junk_cut,
    "peer_post": tool_peer_post,
    "peer_seats": tool_peer_seats,
    "graph": tool_graph,
    "entity_query": tool_entity_query,
    "entity_inherit": tool_entity_inherit,
}


# ---- the JSON-RPC loop ---------------------------------------------------------

def _rpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _handle(req: dict, port: int) -> dict | None:
    """One request -> one response (or None for notifications)."""
    method = req.get("method") or ""
    req_id = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
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
        return _rpc_result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
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

