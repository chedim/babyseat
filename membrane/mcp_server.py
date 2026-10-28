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
            "memory-health checks. With `section`, just that part of the "
            "seat's WHERE WE ARE: tracks, commands (your methods, most "
            "called first), interfaces, classes (class proposals), loops, "
            "timelines, frame, digest, or changes (what moved since your "
            "last message)."),
        "inputSchema": {"type": "object", "properties": {
            "section": {"type": "string", "enum": list(_PULL_SECTIONS),
                        "description": "Optional: one part instead of all."}}},
    },
    {
        "name": "intend",
        "description": (
            "Plant YOUR commitment (the steering wheel of membrane's idle "
            "mind): create-or-pin a shared Goal + pin it on this seat's "
            "active set. Open intentions STEER the dream loop "
            "(quarantined timeline branches grow toward them, think-ahead "
            "extends them) and ride recall. An intention is never "
            "auto-closed: fulfillment is explicit (the `fulfill` tool "
            "with its evidence, or the operator's `membrane: fulfill #N` "
            "-- reality's signature; a similarity flag is only a "
            "'maybe-done?' hint). Use when working toward a multi-session "
            "goal or when the field's work has a direction worth keeping."),
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
            "Close an OPEN goal you have PROVEN done: its status flips to "
            "fulfilled on the shared board (every seat's active set drops "
            "it). Requires evidence -- the receipt (a method call's "
            "rc=0 output, the entity state you read back). Never close on "
            "intent or a plan; an unfinished goal stays open and you take "
            "its next concrete step instead."),
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
            "Read YOUR intent board: the seat's active set steered goals "
            "(the dream loop's steering coordinates), open + closed. Use "
            "before planting a duplicate or to review what commitments "
            "are alive."),
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
            "Plant a TRACK: an entity's live state shown in the WHERE WE "
            "ARE header every turn; an optional count boundary ('enter N' / "
            "'exit N') fires ONCE per transition. DELIVERY RIDES THE OWNER: "
            "''/'operator' fires ride the banner as ALERT lines; a "
            "listener's name ('watch:<eid>') delivers the crossing INTO "
            "THE LISTENER'S NEXT TURN. Key forms: 'files:router.py', "
            "'models:glm-5.3-flash:cloud', 'symbols:<qualname>'. "
            "A track DISPLAYS; to WATCH the field use the senses: "
            "field_watch (a topic region: every new memory landing near "
            "it reports), entity_edit watch_ops (relational: near / away "
            "/ around another entity) and watch_fields (an entity's own "
            "motion: pos / align / vel) -- they share one attention "
            "budget; at the limit drop what you no longer need."),
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
        "name": "field_watch",
        "description": (
            "WATCH THE FIELD (a topic region): plant a probe on a topic; "
            "every NEW memory landing inside its radius is reported to "
            "you on your next turn. The radius EARNS its size (a watch "
            "that swallows too much collapses; one that swallows "
            "everything or starves goes dormant). The board holds 7, "
            "inside ONE attention budget shared with watch_ops and "
            "watch_fields: at the limit the plant is REFUSED with the "
            "list of live watches (sense, what, owner, age, hits) -- "
            "drop the ones you no longer need, then plant. Your agent is "
            "stamped as each watch's owner. action: plant (topic) | drop "
            "('#N', a label fragment, or 'all') | list."),
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
        "name": "attention",
        "description": (
            "YOUR ALERT DIAL: how many watch events per window may wake "
            "you as their own interrupt turn; the rest fold into the "
            "'watch digest' lines of your next WHERE WE ARE block "
            "(grouped per entity). mode 'interrupt' (the budget applies) "
            "| 'digest' (no interrupt turns; everything folds -- goal "
            "events always come through). Turn it down when alerts eat "
            "your turns, up when you're missing things. action get | set."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set"]},
                "budget": {"type": "integer", "description": "interrupt turns per window (0..50)"},
                "window_min": {"type": "number", "description": "the rolling window in minutes (1..1440)"},
                "mode": {"type": "string", "enum": ["interrupt", "digest"]},
            },
        },
    },
    {
        "name": "entity_tracks",
        "description": (
            "List entity tracks: key, label, owner, boundary, current "
            "inside/outside state, and pending alert count. LANE-AWARE: "
            "a listener passes its own agent name to see ITS tracks; "
            "the operator sees all (with owners). Use before planting "
            "a duplicate track or to review your watches."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string",
                          "description": "Optional: filter to one owner "
                                         "('watch:<eid>'); '' = all."},
            },
        },
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
            "entity instead of duplicating. For COMMAND entities (class "
            "Tool + a `command` prop): the entity's ISA PARENTS (set via "
            "entity_inherit) compose it into a PIPELINE -- a run "
            "runs the parents first and feeds their outputs "
            "into the child's command via {{parent.head}}/{{parent.tail}} "
            "(the fan-in names a parent: {{parent:<eid>.head}}). Small "
            "reusable steps + composed pipelines = the composition loop. "
            "Audited to the operator."),
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
            "disable, retried on a backoff (60s doubling to 1h; a "
            "passing retry re-enables, entity_read shows the state); "
            "empty string clears. "
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
            "membrane.entities.get(spec, run=False) -- a read fires the "
            "target's getter (run=True: now, fresh values back), "
            "membrane.timeline.walk(n) / walk('text', n), "
            "membrane.intents.open, membrane.call_llm(prompt) and "
            "membrane.wake(at, prompt) (one each per fire), "
            "membrane.speak -- the planner-gated "
            "voice: ONE line per fire, stay-silent holds). 'result' = {render, "
            "patch: {script_data: {...your values...}, position: [...]} -- ONLY those two "
            "(a getter may MOVE the entity, e.g. toward 'done' coordinates; any "
            "other key is refused and noted on the entity as patch_refused), "
            "run: true (the engine runs the entity's own command), delete (a "
            "delete_script only), intent: a goal text YOUR script plants "
            "when it sees the shape of ongoing work (the engine create-or-"
            "pins a shared Goal + pins the seat's active set -- the dream "
            "loop's steering; plant sparingly), chat / voice: a message "
            "to a person (to: '<person>', default whoever chatted last) "
            "-- the engine posts it into their latest chat / speaks it "
            "on their voice:out; at most once per 30s per entity, never "
            "the same line twice}. The engine applies; "
            "scripts never "
            "mutate directly. The SEAT-FIRST FIELD: your entity store "
            "is LOCAL (authoritative, never auto-synced) and the "
            "global tier holds only PUBLISHED entities. "
            "publish=True exports the entity under a per-origin key "
            "(pub:<origin>:<eid>) -- your decision, your version line "
            "(republishing bumps it); seats that imported it take each "
            "newer STATE within a minute (a seat that edited its copy "
            "keeps its edit), never your code. unpublish=True retracts YOUR "
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
                                                "seat dir, signals; "
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
                                         "'file'|'signal', "
                                         "glob/name optional "
                                         "narrowing -- the CHEAP "
                                         "pre-filter; the script does "
                                         "the real filtering}. "
                                         "kind 'file' is the default "
                                         "for any watcher without one."},
                "getter_script": {"type": "string",
                                  "description": (
                                      "python; the READ-BACK trigger -- runs "
                                      "when the entity is READ. Set a TOP-LEVEL "
                                      "`result` dict; the live values go under "
                                      "'state', e.g. result = {'state': "
                                      "{'soc': 81.2, 'amps': -14}} (also "
                                      "'render' for the banner, 'patch' to "
                                      "edit properties). A function's return, "
                                      "stdout or other keys are NOT read. The "
                                      "5s LRU paces; run=true forces. Empty "
                                      "string clears.")},
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
                                             "event to the pool. Counts "
                                             "against the shared "
                                             "attention budget (with "
                                             "field_watch + watch_fields)"
                                             ": at the limit the edit is "
                                             "refused with the live "
                                             "watches -- drop first."},
                "watch_fields": {"type": "array",
                                 "description": "the OWN-MOTION watch "
                                                "filters: [{verb, arg}] "
                                                "with verb 'pos' (the "
                                                "entity moved > arg), "
                                                "'align' (its coherence "
                                                "fell below arg), 'vel' "
                                                "(its velocity rose above "
                                                "arg); fires on the "
                                                "crossing, re-arms on "
                                                "return. Cap 3 per "
                                                "entity, inside the "
                                                "shared attention budget"
                                                ". '[]' clears."},
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
            "name. Ambiguous fragments remove nothing. A live method, or a class "
            "that owns methods, is refused unless force=true (a forced class "
            "removal takes its methods with it)."),
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
    {
        "name": "method_define",
        "description": (
            "CLIFFHANGER DEFINE: give a class or object a METHOD once, then "
            "call it on any object of that kind (method_call, or method_each "
            "over every member). command is a shell template: {{name}} binds "
            "a call's args, {{self.<prop>}} the object's own properties "
            "(e.g. on='light', method='dim', command='curl ... "
            "{{self.entity_id}} {{level}}'). Or steps: a composite of calls "
            "and commands run in order. guard: a python check run first "
            "(result = {'ok': bool, 'why': str}; false blocks the call). "
            "A missing owner is created as a class; re-defining updates the "
            "method. Owner only (a method's command is code)."),
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
            "CLIFFHANGER CALL: run a method on an object -- an entity or a "
            "class. Dispatch walks the object, then its ISA classes, then the "
            "globals (help('cliffhanger')). PROGRAM THE FIELD FIRST: define "
            "the method (method_define), then call it here (code itself runs "
            "with code_run); a call straight at the executor "
            "(tool:executor, remote_executor or an implementation's exec) is "
            "refused. E.g. object='presence', method='restart' restarts the "
            "seat. The receipt (rc, outcome, head) lands on the object."),
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
            "RUN CODE (#614): the code is parsed into a tree of nodes in the field "
            "(one entity per node, shared wherever the same code appears) and its "
            "root is linked to an executor of its language: -execute-> while owed, "
            "-executing-> while it runs, then -result-> a result entity for this run. "
            "lang='sh' (default) runs on tool:executor, lang='js' in an opted-in browser "
            "tab (presence). Re-run a root with "
            "node=<its id> (each run keeps its own result); entity_read on a root "
            "shows its code, state and runs. Returns the root, the result, rc and "
            "the output."),
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
            "Run a shell command: code_run under the name you know (the command "
            "becomes a tree in the field, the run a result entity; re-run with "
            "code_run node=<root>)."),
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
            "CLIFFHANGER EACH: call one method on every member of a class "
            "(over='light') or a name pattern (over='ha:light.*'). dry_run "
            "lists who it would reach without running. Stops at the first "
            "failure unless on_fail='continue'. Owner only."),
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
            "CLIFFHANGER INSPECT: which method a call on an object would run "
            "(its owner, the resolution order, its params and template) without "
            "running it. Read-only."),
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
        "description": (
            "READ an entity and get its value AFTER processing: a dynamic "
            "entity's getter_script fires (the 5s LRU is bypassed with "
            "run=true) and the return carries the entity's current "
            "properties + script_data + any {run: true} result the getter "
            "triggered. The general rule: entity tools answer with the "
            "entity's post-processing value. The whole-properties blob is "
            "clipped at ~800 chars, so a ring-backed property (voice "
            "heard/said) loses its newest entries to that cut -- read it "
            "fully with prop=..., tail=N."),
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
            },
            "required": ["spec"],
        },
    },
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
    from membrane import jwtauth
    return jwtauth.client_headers()


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


def tool_attention(args: dict, port: int) -> str:
    """The seat's alert dial (POST /membrane/attention)."""
    action = str(args.get("action") or ("set" if any(
        args.get(k) is not None for k in ("budget", "window_min", "mode")) else "get")).lower()
    body = {"action": action}
    for k in ("budget", "window_min", "mode"):
        if args.get(k) is not None:
            body[k] = args[k]
    out = _http(port, "POST", "/membrane/attention", body)
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


def tool_peer_post(args: dict, port: int) -> str:
    text = _arg(args, "text", "message", "msg", "content")
    if not text:
        raise ProxyError(400, "text required")
    out = _http(port, "POST", "/membrane/peer/post", {
        "text": text, "reply_to": str(args.get("reply_to") or "")})
    if not out.get("ok"):
        raise ProxyError(503, f"post failed: {out.get('error') or 'unknown error'}")
    return (f"Posted to the peer channel (visible to the operator on "
            f"every connected seat; unread messages ride their banners).")


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
    what = "created + pinned" if out.get("created") else "pinned (existing goal)"
    return (f"Goal active ({what}): {out.get('eid')}\n"
            f"The active set steers the dream loop; the banner shows it. "
            f"Drop intent #N suppresses it on this seat; fulfill #N closes "
            f"it shared.")


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
    shown = (f"head: {str(out.get('head') or '')[:head_cap]}" if head_cap is not None else _link_output(out))
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
    out = _http(port, "GET", qs)
    if not out.get("ok"):
        return f"read failed: {out.get('error', 'unknown error')}"
    props = out.get("properties") or {}
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



TOOL_IMPLS = {
    "query": tool_query,
    "state": tool_state,
    "intend": tool_intend,
    "intents": tool_intents,
    "fulfill": tool_fulfill,
    "learn": tool_learn,
    "forget": tool_forget,
    "entity_watch": tool_watch_entity,
    "field_watch": tool_field_watch,
    "attention": tool_attention,
    "entity_unwatch": tool_unwatch_entity,
    "entity_tracks": tool_list_tracks,
    "entities_related": tool_related_entities,
    "entity_add": tool_entity_add,
    "entity_edit": tool_entity_edit,
    "entity_remove": tool_entity_remove,
    "junk_cut": tool_set_junk_cut,
    "peer_post": tool_peer_post,
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
        return _rpc_result(req_id, {"tools": TOOLS})
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

