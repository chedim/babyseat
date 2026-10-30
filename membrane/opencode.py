"""opencode-on-membrane: `python -m membrane.opencode`.

Starts the proxy on the entity field, extends it with the OpenAI-compatible
surface opencode speaks, and launches opencode pointed at it:

    you (opencode) --> membrane (entity field + background mind) --> Ollama

What is INTERNAL vs PROXIED (the split this entrypoint implements):
  * INTERNAL -- everything about the membrane's own state:
      /v1/models       built here (Ollama's tag list, embed models filtered)
      /v1/embeddings   served by the field's own embedder
      /membrane/state  the JSON instrument (field, gate, replay A/B, audit,
                       the background mind's boards)
      chat turns that ask about the system (entities, conflicts, replay
      pairs, the night's births, ...) are ANSWERED HERE from the live
      field -- computed, deterministic, zero model calls.
  * PROXIED -- everything model-shaped: /v1/chat/completions (grounded by
    the entity blocks injected from the field, captured back as events
    every turn) and any other path.

The model selected in opencode is the JUDGE LADDER's pick too. Embeddings are the
one place the selected model may not qualify: the resolver tries
--embed-model > a known Ollama embed model > the selected model, and if
nothing can embed, every chat turn returns 503 (err and block -- the field
cannot run without vectors).

Run:
  PYTHONPATH=. .venv/bin/python -m membrane.opencode
  # -> the proxy listens on 127.0.0.1:11500 and opencode opens pointed at it.
"""

from __future__ import annotations

import hashlib
import re
import json
import copy
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .embed import OllamaEmbedder
from .global_field import PEER_ASK_SIM_MIN
from .rag import (RAGProxyHandler, _clip, _inject, _inject_after,
                  _model_chat, doctrine_instructions_text, think_value)
from .router import (EXPLORE_LANE, AUTO_CLOUD, AUTO_LOCAL, AUTO_LANES,
                     AUTO_ALIASES, LANE_TOKENS, normalize_lane,
                     Registry)
from .watch import MAX_WATCHES, WatchBoard
from .watch_agents import WatchEvents, LoopRunner, \
    start_loop_drainer as WA_start_loop_drainer, \
    loops_banner_line
from .intent import IntentBoard, MAX_INTENTS

from .seat import Seat
# THE IMPORT-HOIST (the GIL/import-war covenant, 2026-10-03): the
# REQUEST path's lazy imports (21 sites ran perTurn/beat) fought the
# interpreter's import lock with the worker threads -- a request
# thread's builtin_exec wedged the whole surface (the seat's HTTP
# lane starved behind a half-initialized module). The hot modules
# import ONCE here; the per-turn sites read the aliases.
from . import entity_block as _eb
from . import efgate as _efg
from . import entities as _ents
from . import streams as _streams
from . import efield as _efield_mod
from .safestderr import SafeStderr
from .entities import (EntityStore, QuantumEntity, MEMBRANE_NS,
                       CLASS_COMPONENT, CLASS_TOOL)


# name markers meaning "not a chat model" (the picker's screen). CLOUD models
# (e.g. glm-4.7:cloud, deepseek-v4-pro:cloud, gpt-oss:20b-cloud) never match these
# markers, so they always pass.
# tag markers meaning "an embedding model" (the embed-role candidates)
# the opencode-compatibility screen for the PICKER (same covenant as the
# router's screen in router.py; both sides filter on Ollama's declared caps)


# THE SPLIT (2026-10-07): the per-turn lanes (the canonical-frame
# intercepts, the banners, the intents/pause builders, the OpenAI
# handler, the speech lane) live in membrane/lanes.py; this module
# keeps the CLI + the boots (main) and re-exports the lane surface
# (the import surface never changed -- every membrane.opencode.X
# import works).
from .lanes import (  # noqa: E402,F401
    CLOUD_DEFAULT_PRICE,
    INTENT_NUDGE_WINDOW_S,
    MembraneOpenAIHandler,
    RECALL_NULL_FRESH_S,
    RECALL_NULL_SIM_FLOOR,
    WHERE_REFRESH_TURNS,
    _AGENT_FORGET_LIMIT,
    _AGENT_LEARN_MAX_CHARS,
    _CANON_FRAME_PREFIXES,
    _CG_SWEEP_INTERVAL,
    _CODEGRAPH_CANON,
    _CODEGRAPH_MARKER,
    _DEV_FRAME_WARN,
    _EDIT_ACK,
    _EDIT_ACK_RE,
    _EDIT_FAIL,
    _EDIT_FAIL_RE,
    _ELIDE_MARK,
    _EMBED_MARKERS,
    _ENTITY_CARD_COMMON,
    _ENTITY_CARD_NORMAL,
    _ENTITY_CARD_NO_CONTEXT,
    _EventView,
    _FAMILIES,
    _FORGET_ALL,
    _FORGET_CANON,
    _FORGET_FRAMES,
    _FORGET_GLOBAL_CANON,
    _FORGET_LIMIT,
    _FORGET_SOFT_FRAMES,
    _FORGET_WINDOW_HINTS,
    _FORGET_WORDS,
    _INTENTS_CANON,
    _INTENT_CANON,
    _INTENT_DROP,
    _INTENT_FULFILL,
    _INTENT_THOUGHT_HEAD,
    _INTERCEPT_ANTI_SEEDS,
    _INTERCEPT_MARKER,
    _INTERCEPT_SEEDS,
    _LEARN_CANON,
    _LEARN_FRAMES,
    _LEARN_SOFT_FRAMES,
    _LEARN_TAG,
    _LEARN_WORDS,
    _MEMBRANE_DIRECT,
    _MONOLOGUE_CANON,
    _NON_CHAT_MARKERS,
    _OC_BUILTIN_TOOLS,
    _OC_MAX_LOCAL_GIB,
    _OC_MIN_CAPS,
    _PAUSE_CANON,
    _PROMOTE_ALT,
    _PROMOTE_CANON,
    _PROMOTE_REF,
    _RECALL_CANON,
    _RECALL_DUMP,
    _RECALL_EXACT,
    _RECALL_FRAMES,
    _RESUME_CANON,
    _SEED_CACHE,
    _SPEAK_CAP,
    _SPEAK_TTL_S,
    _STATE_EXACT,
    _STATE_QSTARTS,
    _STATE_STRONG,
    _STATE_WORDS,
    _SURFACE_CAP,
    _THINK_KEYS,
    _THOUGHT_BLOCK_HEAD,
    _THOUGHT_CAPTURE_PREFIXES,
    _WATCHES_CANON,
    _WATCH_CANON,
    _WATCH_DROP,
    _WATCH_THOUGHT_HEAD,
    _after_frame,
    _agent_forget,
    _agent_learn,
    _assistant_thought_asks,
    _attach_cmd,
    _attach_env,
    _audit_count,
    _audit_tail,
    _auth_headers,
    _capture_plan_and_calls,
    _capture_tool_results,
    _chat_capable_models,
    _codegraph_answer,
    _codegraph_graph,
    _codegraph_note_dirty,
    _codegraph_refresh,
    _codegraph_wants,
    _commons_write,
    _convert_old_commons,
    _ctx_gave_summary,
    _delta_reasoning,
    _dev_frame_warning,
    _dev_warn_take,
    _efield_answer,
    _efield_forget,
    _efield_learn,
    _efield_of,
    _efield_on,
    _efield_state_rows,
    _efield_vitals,
    _efield_watch_listener,
    _elide_args,
    _elide_text,
    _elide_tool_tail,
    _embed_field_seed_matrix,
    _embed_seed_matrix,
    _entity_points,
    _entity_post_process,
    _family_of,
    _field_seed_topics,
    _forget_global,
    _forget_report,
    _forget_targets,
    _frame_lint,
    _frame_wait_bump,
    _frame_wait_summary,
    _init_speak,
    _inject_to_primary_impl,
    _install_inject,
    _install_judges,
    _intent_cmd,
    _intents_board,
    _intents_context_block,
    _is_codegraph_query,
    _is_forget_global_query,
    _is_forget_query,
    _is_intent_query,
    _is_learn_query,
    _is_non_chat,
    _is_pause_query,
    _is_promote_query,
    _is_recall_query,
    _is_state_query,
    _is_thought_ask,
    _is_watch_query,
    _known_embed_names,
    _lan_ip,
    _last_line_ask,
    _last_user_text,
    _learn_refusal,
    _learn_report,
    _mark_field_degraded,
    _mcp_tool_lines,
    _membrane_intent_create_or_pin,
    _msg_reasoning,
    _now_iso,
    _one_line,
    _openai_sse_chunks,
    _own_session_prefix,
    _parse_agent_forget,
    _pause_cmd,
    _peer_heartbeat,
    _pick_default_model,
    _pick_idle_cortex_model,
    _pick_listen_port,
    _pick_serve_port,
    port_pinned,
    _port_free,
    _print_intercept,
    _probe_embedder,
    _promote_global,
    _recall_report,
    _recall_topic,
    _receipt_class,
    _render_calls,
    _resolve_goal,
    _resp_has_content,
    _seat_card,
    _seed_topics,
    _semantic_intercept_hit,
    _semantic_recall_hit,
    _serve_board,
    _serve_thoughts_served,
    _speak_block,
    _sse_line,
    _state_report,
    _strip_context,
    _surface_block_summary,
    _surface_block_text,
    _surface_push,
    _surface_report,
    _surface_tail,
    _swallow,
    _thought_answer,
    _thought_block,
    _thought_family,
    _tool_arg_path,
    _tool_result_text,
    _tool_round,
    _tools_block,
    _tune_threshold,
    _watch_report_cmd,
    _watch_set_from_thought,
    _watches_board,
    _where_delta,
    _where_we_are,
    _window_seconds,
    _write_audit,
    announce_line,
    boot_entity,
    drain_speech,
    n_ctx_mem,
    nerve_paused,
    speak,
    srv_ref,
)


def loop_session(srv, agent: str) -> str:
    """A watch loop's child session (the drainer's create_loop_session).

    With the serve runner wired (srv._loop_runner) the loop gets a REAL
    session in the runner's store, carrying its agent ('watch: <eid>') --
    the runner's agent registry reads it and lanes the turn LISTENER, so
    the loop holds the entity hands (its method calls -> tool:executor).
    2026-10-07: this read `srv.__loop_runner__` (never set) and ALWAYS fell
    back to the bare key below -> the turn laned 'build' -> operator ->
    every exec link refused -> the HA seat's commands never ran.
    Proxy-only seats (no runner) keep the degenerate 'loop::<agent>' key.
    """
    runner = getattr(srv, "_loop_runner", None)
    if runner is not None:
        try:
            store = runner.sessions
            # #570: ONE session per entity -- the pass reuses it and starts it
            # empty (the task text carries the entity's last verdicts), so
            # nothing replays and the store keeps one pass per entity
            with store._lock:
                mine = [x for x in store.sessions.values() if x.get("agent") == agent]
            if mine:
                s = max(mine, key=lambda x: (x.get("time") or {}).get("updated") or 0)
                runner.messages.delete(s["id"])
                store.touch(s["id"])
                return s["id"]
            s = store.create(title=agent)
            # create() already flushed the doc as 'build': stamp the
            # agent AND flush, or a restart revives the loop's session
            # as an operator one (exhibit: ses_c47c... 'watch: ...goal'
            # came back agent build)
            with store._lock:
                s["agent"] = agent
                store.sessions[s["id"]] = s
                store._flush()
            return s["id"]
        except Exception as e:
            print(f"[opencode] loop session create failed ({e}); "
                  "falling back to the bare key", file=sys.stderr)
    return f"loop::{agent}"


def collapse_loop_sessions(runner) -> int:
    """#570, once per boot: an older seat made a session per wake -- keep
    each loop agent's newest, delete the rest with their messages. Chats
    (agent build / plan / unset) are never touched. -> how many went."""
    from .server import _lane_of
    store = runner.sessions
    with store._lock:
        rows = list(store.sessions.values())
    by: dict = {}
    for x in rows:
        agent = str(x.get("agent") or "")
        if agent in ("", "build", "plan") or _lane_of(agent) not in ("listener", "plan"):
            continue
        by.setdefault(agent, []).append(x)
    old = []
    for xs in by.values():
        xs.sort(key=lambda x: (x.get("time") or {}).get("updated") or 0)
        old += [x["id"] for x in xs[:-1]]
    if not old:
        return 0
    runner.messages.delete(*old)                  # one rewrite of the messages file
    with store._lock:
        for sid in old:
            store.sessions.pop(sid, None)
        store._flush()
    return len(old)


def proxy_bind_host(args) -> str:
    """THE /v1 PORT ON THE LAN (2026-10-08, the operator's call): all
    interfaces by default (a wifi-only device: the LAN is the reach);
    --listen-host / MEMBRANE_LISTEN_HOST bind it back to 127.0.0.1. Reads
    are open on the LAN; an off-box caller's writes (the field, entities,
    scripts, methods, models, /v1 and Ollama turns) need the seat token or,
    in JWT mode, a JWT (#253, seatauth.py); localhost needs nothing."""
    return (getattr(args, "listen_host", None)
            or os.environ.get("MEMBRANE_LISTEN_HOST") or "0.0.0.0")


# ONLY A REAL TUI LAUNCH may ignore ctrl+c: a seat riding the
# terminal under an opencode child has the child's message-input to
# protect. `--no-launch`'s dest defaults launch to TRUE, so a plain
# `membrane standalone` (and every supervised/nohup'd boot) ran
# launch-mode signal policy with NO child at all: the seat read ctrl+c
# AND kill -INT as "keep serving" (2026-10-14's exhibit, issue #35)
# and a bg-invoker's inherited SIG_IGN never got restored. Every
# childless serve mode stops on INT via the same graceful lane.
def _stop_signal_policy(launch: bool, standalone: bool) -> str:
    return "ignore" if (launch and not standalone) else "stop"

# THE TEARDOWN'S LEASE (the 2026-10-14 drain exhibit): the graceful
# close chain gets a bound -- if the drain (a big field's close, an
# orphaned model stream) overruns past this lease past the bye, the
# interpreter is cut. The debounced snapshots commit along the way; a
# bound beats a half-alive seat.
TEARDOWN_LEASE_S = 90.0


def _build_ap():
    import argparse
    ap = argparse.ArgumentParser(
        description="Membrane field + opencode: a local field-backed coding "
                    "agent (math/state answered internally; model work proxied "
                    "to Ollama).")
    ap.add_argument("--listen", type=int, default=11500,
                    help="port for the membrane OpenAI-compatible proxy "
                         "(default 11500, walks up if taken; passed = "
                         "pinned: busy exits)")
    ap.add_argument("--listen-host", dest="listen_host", default=None,
                    help="the /v1 proxy's bind address (default 0.0.0.0 -- "
                         "the LAN; its write/exec routes carry NO auth; "
                         "127.0.0.1 = this device only; env "
                         "MEMBRANE_LISTEN_HOST)")
    ap.add_argument("--no-viewer", dest="viewer", action="store_false",
                    default=True,
                    help="don't serve the universe map at /viewer (ON by "
                         "default; the old opt-in flag is retired -- the "
                         "map is the seat's face now)")
    # MEMBRANE_NO_CONTEXT=1 makes the stateless seat the DEFAULT for every
    # spawner that sources the seat env (a supervisor, an HA self-heal, a
    # manual launch) -- one env line instead of N launch lines to keep in
    # sync; --with-context overrides it for one run.
    # ON BY DEFAULT (operator, 2026-10-29): the field is the memory, the
    # transcript is not replayed; MEMBRANE_NO_CONTEXT=0 or --with-context
    # keeps opencode's history for a seat that wants it
    _nc_env = os.environ.get("MEMBRANE_NO_CONTEXT", "1").strip().lower() \
        not in ("0", "false", "no", "off")
    ap.add_argument("--with-context", dest="no_context", action="store_false",
                    default=_nc_env,   # shared dest: the FIRST default wins
                    help="keep opencode's transcript history (the default "
                         "is --no-context; MEMBRANE_NO_CONTEXT=0 does the same)")
    ap.add_argument("--no-context", dest="no_context", action="store_true",
                    default=_nc_env,
                    help="the default: THE STATELESS LLM (the field is the only "
                         "memory): strip opencode's transcript history per "
                         "turn -- keep the system prompts + the current "
                         "turn's exchanges (the turn boundary), drop "
                         "everything before it. Coherence and history "
                         "come FROM MEMBRANE: the reconstruction block "
                         "(the last N learned turns) + the recall (the "
                         "RAG budget scales generously: k x8, ctx x3). "
                         "No prompt-too-long crashes, ever: the context "
                         "is bounded by the field, not the session's age.")
    ap.add_argument("--tool-keep", type=int, default=3, metavar="N",
                    help="with --no-context: the current turn's last N "
                         "tool exchanges ride verbatim; older big tool "
                         "results/arguments shrink to a head + a marker "
                         "(the call/result pairing is kept). -1 disables "
                         "the in-turn elision.")
    ap.add_argument("--open", dest="open_browser", action="store_true", default=False, help="open the universe map in the browser on start (default off)")
    ap.add_argument("--no-open", dest="open_browser", action="store_false", default=False,
                    help="don't open the universe map in the browser on start")
    ap.add_argument("--backend", default="http://localhost:11434",
                    help="upstream Ollama URL")
    ap.add_argument("--model", default=None, metavar="MODEL",
                    help="the model opencode opens with (default: best "
                         "available from Ollama's tags)")
    ap.add_argument("--field", default="rag-field.json")
    ap.add_argument("--audit", default="rag-audit.jsonl")
    ap.add_argument("--embed-model", default=None, metavar="MODEL",
                    help="the field's embedding model (default: auto -- known "
                         "Ollama embed models, then the selected model; chat "
                         "BLOCKS when nothing can embed)")
    ap.add_argument("--ctx-chars", type=int, default=2400)
    ap.add_argument("--compact-model", default=None, metavar="MODEL",
                    help="pin the seat's model of record (the one the "
                         "router-less backstop and the follow-chat judge "
                         "use; default: follows the model selected in "
                         "opencode)")
    ap.add_argument("--idle-cortex-model", default=None, metavar="MODEL",
                    help="model for BACKGROUND loops only (claim + A/B "
                         "judging, entity scripts, synthesis); default: "
                         "the first local Ollama chat model by family "
                         "preference (granite, glm, qwen3, "
                         "qwen2.5-coder, ...) resolved from the LIVE tag "
                         "list, local-first so a stray stuck background "
                         "seat never burns cloud tokens. --compact-model "
                         "overrides for both lanes.")
    ap.add_argument("--no-banner", dest="no_banner", action="store_true",
                    help="the ablation arm: inject NO banner (tracks/"
                         "alerts/announcements off; recall stays) -- "
                         "port-notes 24's measurement flag")
    ap.set_defaults(no_banner=False)
    ap.add_argument("--no-router", "--disable-llm-router", dest="router",
                    action="store_false",
                    help="disable the LLM orchestration router and always "
                         "escalate to the single --backend model (the kill "
                         "switch if routing goes crazy)")
    ap.add_argument("--dev", dest="dev", action="store_true",
                    help="developer mode: a canonical-frame discipline "
                         "violation in an assistant reply (stacked or buried "
                         "frames, W3a's lint) plants a ONE-SHOT marked "
                         "warning that rides the next model-bound request. "
                         "Never blocks live traffic")
    ap.add_argument("--no-receipts", dest="receipts",
                    action="store_false",
                    help="kill switch: the receipt lane (E9 -- "
                         "byte-deterministic edit acks served model-free) "
                         "falls back to full model path")
    ap.add_argument("--no-dreamer", dest="no_dreamer", action="store_true",
                    help="stand down the dreamer (no timeline generation "
                         "on idle; the field stays exactly as lived)")
    ap.add_argument("--no-thinker", dest="no_thinker", action="store_true",
                    help="stand down the thinker (no background "
                         "deliberation; nothing is chewed off-turn)")
    ap.add_argument("--no-night", dest="no_night", action="store_true",
                    help="hold day mode forever (the mind never sleeps: "
                         "the 1s pulse runs at idle; the animal covenant "
                         "off)")
    ap.add_argument("--ambient-pulse", default="1.0/30.0", metavar="DAY/NIGHT",
                    help="the ambient mind's heartbeats in seconds, day/night "
                         "(default 1.0/30.0: the 1s hum when awake, a 30s "
                         "pulse at night; night enters on 60s idle and any "
                         "signal -- a turn, a script error, a peer miss, a "
                         "watch fire -- wakes it)")
    ap.add_argument("--cloud-budget", type=float, default=0.0, metavar="USD",
                    help="max relative-money spend per day on cloud-routed "
                         "escalations (the cost ladder's hard cap; default 0 "
                         "= FREE-TIER rungs only -- free pins cost ~0.1 and "
                         "a $0 budget keeps the free pool reachable while "
                         "paid rungs need an explicit budget). Score "
                         "proposes; budget disposes.")
    ap.add_argument("--explore", type=float, default=0.05, metavar="EPS",
                    help="fraction of escalations that try the least-tried "
                         "OPencode-ELIGIBLE local model that holds in memory "
                         "(0 disables; fresh candidates can otherwise never "
                         "build a record)")
    ap.add_argument("--prefer-local", dest="prefer_local",
                    action="store_true",
                    help="restore the classic LOCAL-FIRST cost floor (the "
                         "router's default is cloud-first: the cheapest "
                         "eligible cloud holds the floor and locals must "
                         "earn the rank with a win-rate record). Use for "
                         "offline / privacy / zero-cloud operations.")
    ap.add_argument("--max-local-gib", type=float, default=12.0, metavar="GIB",
                    help="never cold-load a local model bigger than this "
                         "(protected RAM budget; 32GB boxes: 8-14 is sane)")
    ap.add_argument("--price", action="append", default=[], metavar="M=USD",
                    help="override the relative money per turn for a cloud "
                         "model (substring match, repeatable), e.g. "
                         "--price glm-5.3-flash=1.0 --price claude=6.0")
    ap.add_argument("--ladder-include", dest="ladder_include",
                    action="append", default=[], metavar="MODEL",
                    help="add an OPT-IN rung (the claude-agent ones, issue "
                         "#384) to the router's own choosing (autoselect and "
                         "the failover ladder); without it such a rung "
                         "answers only when picked. Repeatable, e.g. "
                         "--ladder-include claude-agent:haiku")
    ap.add_argument("--router-state", default=None, metavar="PATH",
                    help="where the router persists model records (wins, "
                         "imprints, latency EWMAs, calibration, spend); "
                         "default <field-dir>/router.json")
    ap.add_argument("--replay-ab", type=float, default=0.0, metavar="P",
                    help="entity field: serve the replay on a fraction P of the "
                         "turns the gate finds replayable (default 0: every such "
                         "turn is only logged as an A/B pair -- the replay next to "
                         "the model's own answer; `membrane review --ab` reports)")
    ap.add_argument("--no-state-intercept", dest="state_intercept",
                    action="store_false",
                    help="proxy every turn to the model, even membrane math/"
                         "state questions")
    ap.add_argument("--no-sem-intercept", dest="sem_intercept",
                    action="store_false",
                    help="disable the semantic fallback for recall/state "
                         "interception (lexical frames only)")
    ap.add_argument("--no-field-seeds", dest="field_seeds",
                    action="store_false",
                    help="disable the FIELD-GROWN seed layer of the semantic "
                         "intercept gate (fixed canonical seeds only) -- the "
                         "kill switch if the self-grown layer ever misroutes")
    ap.add_argument("--no-gate-monologue", dest="gate_monologue",
                    action="store_false",
                    help="do not stream the [membrane] gate monologue into "
                         "opencode's thinking channel")
    ap.add_argument("--no-doctrine-move", dest="doctrine_move",
                    action="store_false",
                    help="keep injecting the membrane doctrine into every "
                         "request (the default ships it ONCE via the generated "
                         "opencode config's instructions file -- a byte-stable "
                         "system prefix every provider can cache -- and the "
                         "proxy skips its per-turn copy when the stamp line "
                         "is present)")
    ap.add_argument("--no-seat-card", dest="seat_card", action="store_false",
                    help="don't append the seat card (this session's live "
                         "switches -- context mode, replay, writes, the MCP "
                         "tool list, routing -- rendered for the model) to "
                         "the doctrine instructions file")
    ap.add_argument("--no-thought-intercept", dest="thought_intercept",
                    action="store_false",
                    help="do not answer the AGENT's own asks found in its "
                         "assistant-turn text (default ON: a 'field state' in "
                         "the model's prose is served as a marked context "
                         "block next turn; learn executes as an agent-note, "
                         "forget via index form)")
    ap.add_argument("--no-agent-writes", dest="agent_writes",
                    action="store_false",
                    help="revert agent write authority: thought-path 'membrane: "
                         "learn that' becomes report-only (the model proposes, "
                         "the operator's user-message turns do the writing). "
                         "Forget keeps its capped index-form lane either way")
    ap.add_argument("--no-mcp", dest="mcp", action="store_false",
                    help="do not wire the MCP tool surface into the generated "
                         "opencode config (default ON: opencode gets a local "
                         "stdio MCP server exposing query/learn/"
                         "forget/state as structured tools; the frames keep "
                         "working either way)")
    ap.add_argument("--no-lsp", dest="lsp", action="store_false",
                    help="do not enable LSP servers in the generated opencode "
                         "config (default ON: built-in servers like pyright/"
                         "gopls/typescript auto-start for matching files and "
                         "feed diagnostics to the agent; requires the server "
                         "binaries to be installed or auto-installable)")
    ap.add_argument("--global-field", default=None, metavar="PATH",
                    help="enable the global-field layer (docs/global-field.md):"
                         " a shared commons of operator-promoted memories "
                         "across all membranes. Default PATH: "
                         "~/.membrane/global-field.json. Recall tops up from "
                         "the commons (provenance-tagged); "
                         "'membrane: remember globally: X' / "
                         "'membrane: promote #N' write it.")
    ap.add_argument("--no-global", dest="use_global",
                    action="store_false",
                    help="disable the global-field layer entirely")
    ap.add_argument("--global-sim-min", type=float, default=0.55,
                    help="cosine floor for commons top-up in recall")
    ap.add_argument("--no-launch", dest="launch", action="store_false",
                    help="run only the proxy (don't start opencode)")
    ap.add_argument("--force-seat", action="store_true",
                    help="boot past the single-instance lock (kill the "
                         "old seat yourself; multiple-seat rigs ride "
                         "MEMBRANE_MULTI_SEAT=1)")
    ap.add_argument("--serve", action="store_true",
                    help="THE NATIVE SERVE (opencode replaced): serve "
                         "membrane's own opencode-compatible REST/SSE "
                         "surface (the companion app's endpoint) -- "
                         "session/message/abort/event, the turn loop "
                         "riding THIS seat's /v1 loopback (injection, "
                         "capture, banner, routing all real), tool "
                         "parts streamed as message.part.updated "
                         "events. MEMBRANE_LEGACY_SERVE=1 restores the "
                         "old opencode child lane.")
    ap.add_argument("--serve-port", type=int, default=4096,
                    help="port for the native serve surface (default "
                         "4096, walks up if taken; passed = pinned: "
                         "busy exits)")
    ap.add_argument("--serve-host", default="0.0.0.0",
                    help="bind address for the serve surface (default "
                         "0.0.0.0 -- WiFi/VPN reach; 127.0.0.1 keeps it "
                         "local-only)")
    ap.add_argument("--standalone", action="store_true",
                    help="STANDALONE MODE: the serve also serves the web UI "
                         "(/, ui_serve.py) and opens it in the browser")
    ap.add_argument("--no-browser", action="store_true",
                    help="standalone mode: don't open the UI in the browser "
                         "(it opens only on an interactive launch anyway)")
    ap.add_argument("--no-continue", dest="cont", action="store_false",
                    help="don't resume opencode's last session (default: "
                         "resume; -c/-s in passthrough still win)")
    ap.add_argument("--no-absorb", action="store_true",
                    help="don't absorb external-agent sessions on boot")
    ap.add_argument("--new-session", action="store_true",
                    help="skip absorption AND resume: start fresh")
    ap.add_argument("--no-autonode", action="store_true",
                    help="don't run the trigger layer (no file watchers)")
    ap.add_argument("--think", choices=("on", "off", "auto"), default="auto",
                    help="why the backend reasons: on forces think:true on "
                         "every forwarded body, off think:false (cards like "
                         "qwen3 / R1 distills stop cold; glm cloud reasons "
                         "either way), auto (the default) forwards the "
                         "payload verbatim -- a non-thinking card never "
                         "learns a think:true it 400s on")
    ap.add_argument("passthrough", nargs="*",
                    help="extra args passed to opencode (use -- to separate)")
    return ap


def main() -> None:
    args = _build_ap().parse_args()
    # JWT MODE (jwtauth.py, docs/auth.md): off unless a secret is set; a bad
    # secret (short, empty, unreadable) refuses the boot -- never half-open
    from . import jwtauth as _JWT
    _JWT.boot_check()
    # THE OWNER'S DOOR (#253, seatauth.py): JWT mode off, the seat token
    # off-box writes need -- made on first boot, its path in the log
    from . import seatauth as _SA
    _SA.boot()
    # the data dir the UI routes and settings.json read (settings.py)
    os.environ.setdefault("MEMBRANE_DATA_DIR", str(Path(args.field).resolve().parent))
    if args.standalone:
        args.serve = True
        os.environ["MEMBRANE_STANDALONE"] = "1"
    # THE NAME CEREMONY (naming.py): a new seat asks its operator for its name --
    # standalone, and a seat Home Assistant runs (its supervisor sets the env);
    # never a seat that has one already in its doctrine (the boat's seat)
    naming_on = args.standalone or os.environ.get("MEMBRANE_NAME_CEREMONY") == "1"
    passthrough = args.passthrough
    if args.new_session:
        args.cont = False
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    # In launch mode opencode owns the terminal, so membrane's own stderr
    # (the [opencode]/[rag]/[compact] debug lines) would corrupt the TUI.
    # Redirect it to a log file; the terminal stays clean for opencode.
    # The CLI passes a field under .membrane/ per project: keep the log
    # beside it so a project's runtime state stays in one directory.
    _log_fh = None
    if args.launch:
        log_path = "membrane-opencode.log"
        if args.field != "rag-field.json":
            fdir = os.path.dirname(os.path.abspath(args.field))
            if os.path.basename(fdir) == ".membrane":
                log_path = os.path.join(fdir, "opencode.log")
        # write-through + lock-serialized (not a buffered handle): a shared
        # line-buffered TextIOWrapper lets two threads into the buffered layer
        # at once and CPython 3.14 aborts on that (issue #121).
        _log_fh = SafeStderr(log_path)
        sys.stderr = _log_fh
    else:
        # #347: serve / standalone print to stdout and stderr from ~20 threads too;
        # the same write-through guard, so a shutdown race can never SIGABRT
        from .safestderr import install_safe_stdio
        install_safe_stdio()

    backend = args.backend.rstrip("/")
    chat_models = _chat_capable_models(backend)
    if not chat_models:
        print(f"[opencode] WARNING: no chat-capable Ollama models at {backend} "
              f"(is Ollama running?)", file=sys.stderr)
    model = _pick_default_model(chat_models, args.model)
    if model is None and args.model:
        model = args.model          # respect an explicit choice even if unlisted

    mem = Seat(args.field, args.audit)   # the seat: audit, embedder ladder, locks
    pid_path = Path(getattr(args, "field", ".membrane/field.json")).parent \
        / "membrane.pid"
    # THE SINGLE-INSTANCE COVENANT (the orphan root, 2026-10-06: 'why
    # are we getting orphans?!'): a closed TUI's 'restart' relaunched a
    # NEW seat on a NEW port while the old one lived on detached: a
    # family of invisible proxies on one field. THE CHECK RUNS BEFORE
    # THE WRITE (the check reads the OLD lock -- writing first poisons
    # the read with our own pid = the check forever passes):
    #   - a LIVE pid that IS a membrane process = refuse with the
    #     pointer (kill it or 'membrane reset'; --force overrides)
    #   - a DEAD pid / a non-membrane pid (recycled) = a ghost: claimed
    # MEMBRANE_MULTI_SEAT=1 opts out (multi-seat rigs keep working).
    if not (getattr(args, "force_seat", False)
            or os.environ.get("MEMBRANE_MULTI_SEAT") == "1"):
        try:
            _running = int(pid_path.read_text().strip()) \
                if pid_path.exists() else None
        except Exception:
            _running = None
        if _running and _running != os.getpid():
            _is_ours = False
            try:
                os.kill(_running, 0)   # alive?
                # THE RECYCLE GUARD: a pid file's dead-seat pid gets
                # recycled by any process -- the lock only bites when
                # the pid IS a membrane process:
                # /proc first (BusyBox ps -- HA's Alpine image -- has no -p: the
                # lock silently never bit there), ps on systems without /proc
                try:
                    with open(f"/proc/{_running}/cmdline", "rb") as _f:
                        _cmdline = _f.read().replace(b"\0", b" ").decode("utf-8", "replace")
                except OSError:
                    _cmdline = subprocess.run(
                        ["ps", "-p", str(_running), "-o", "command="],
                        capture_output=True, text=True, timeout=4).stdout
                _is_ours = "membrane" in _cmdline
            except Exception:
                _is_ours = False
            if _is_ours:
                print(f"[opencode] the seat on this field is ALIVE "
                      f"(pid {_running}) -- kill it or 'membrane reset' "
                      f"first; --force overrides", file=sys.stderr)
                raise SystemExit(2)
    try:
        pid_path.write_text(str(os.getpid()))
    except Exception:
        pass                       # the lock's write degrades (the seats
                                 # keep their no-lock behavior)

    # the probes ask the address each real bind will use (the wildcard
    # 0.0.0.0 a loopback probe blesses even while another seat holds it --
    # the silent Errno 48 boot death, 2026-10-13)
    # #760: a port the person passed is pinned (busy = a loud exit); the
    # defaults and a restart's carried ports walk
    from .restarts import was_reexec as _was_reexec
    _argv = sys.argv[1:]
    _listen_pinned = port_pinned(_argv, "--listen", reexec=_was_reexec())
    _serve_pinned = port_pinned(_argv, "--serve-port", reexec=_was_reexec())
    listen = _pick_listen_port(args.listen, host=proxy_bind_host(args),
                               pinned=_listen_pinned)
    if listen != args.listen:
        print(f"[opencode] port {args.listen} busy -> using {listen} "
              f"(pass --listen to pin)", file=sys.stderr)
    serve_wanted = bool(args.serve) or bool(args.launch)
    serve_port = (_pick_serve_port(args.serve_port,
                                   host=args.serve_host if args.serve
                                   else "127.0.0.1",
                                   pinned=_serve_pinned)
                  if serve_wanted else None)
    if serve_port is not None and serve_port != args.serve_port:
        print(f"[opencode] serve port {args.serve_port} busy -> using "
              f"{serve_port} (pass --serve-port to pin)", file=sys.stderr)
    # THE PICK'S RESERVATION (the port race's fix, 2026-10-06): the
    # picked port is BOUND NOW (a listening socket, one pass in length)
    # -- the bind race's window closes: two seats booting together can
    # no longer both 'pick' 4096; the loser re-picks honestly. The
    # reservation rides srv and the native serve adopts it (never a
    # rebind); an unused reservation closes at teardown.
    _serve_socket = None
    # the reservation binds the SERVE's own host (the adopted socket IS the
    # listener: a loopback reservation silently ignored --serve-host)
    _serve_bind = args.serve_host if args.serve else "127.0.0.1"
    if serve_port is not None:
        import socket as _sk
        _serve_socket = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        _serve_socket.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 1)
        try:
            _serve_socket.bind((_serve_bind, serve_port))
            _serve_socket.listen(128)
        except OSError as e:
            _serve_socket.close()
            _serve_socket = None
            if _serve_pinned:          # #760: a pinned port never walks
                raise SystemExit(
                    f"[opencode] serve port {serve_port} was taken mid-boot "
                    f"and --serve-port pins it ({e})")
            serve_port = _pick_serve_port(serve_port + 1, host=_serve_bind)
            print(f"[opencode] the serve port was taken mid-boot -> "
                  f"re-picked {serve_port} ({e})", file=sys.stderr)
            _serve_socket = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
            _serve_socket.setsockopt(_sk.SOL_SOCKET, _sk.SO_REUSEADDR, 1)
            _serve_socket.bind((_serve_bind, serve_port))
            _serve_socket.listen(128)
    try:                               # #648: a restart keeps the ports this boot holds
        from . import restarts as _RS648
        _RS648.RESTART["ports"] = (listen, serve_port)
    except Exception:
        pass
    srv = ThreadingHTTPServer((proxy_bind_host(args), listen), MembraneOpenAIHandler)
    srv.membrane = mem
    srv.paused = False                 # a cold start runs; a restart's re-exec keeps the person's pause (#395)
    try:
        from .restarts import apply_carried_pause as _apply_carried_pause
        _apply_carried_pause(srv)
    except Exception as e:
        print(f"[seat] the restart's pause not applied ({e})", file=sys.stderr)
    srv.viewer = bool(args.viewer)   # dev field viewer at /viewer (OFF default)
    # THE STATELESS LLM (the field is the only memory): --no-context
    # strips the transcript history per turn; coherence comes from the
    # reconstruction block + the recall (the RAG budget scales).
    srv.no_context = bool(getattr(args, "no_context", False))
    srv.tool_keep = int(getattr(args, "tool_keep", 3))
    mem.server_ref = srv          # the router-outcome loop reads srv.router
    srv.backend = backend
    try:                                   # offline mode (offline.py): the walks
        from . import offline as _OFF
        _OFF.start(srv, backend)
    except Exception as e:
        print(f"[seat] offline probe not started ({e})", file=sys.stderr)
    # THE ABLATION FLAG (port-notes 24): --no-banner arms the
    # banner-off measurement
    srv.no_banner = bool(getattr(args, "no_banner", False))
    srv.rag_ctx_chars = max(200, int(args.ctx_chars))
    srv.model_follows_chat = args.compact_model is None
    srv.seat_model = args.compact_model or model or "granite3.3:8b"
    # THE IDLE-CORTEX SPLIT: background loops (claim + A/B judging, the
    # entity synthesis scripts) run on a LOCAL model by default -- a stray
    # stuck background seat can never burn cloud tokens again. An explicit
    # --compact-model pins BOTH lanes (chat-stamped + idle); --idle-cortex-
    # model overrides just the idle lane. The judge continues to follow
    # the operator's chat selection (1-per-turn, bounded by the turn itself).
    # The default resolves from ollama's LIVE tag list (_pick_idle_cortex_
    # model); the fallback reads the tag list first and only then guesses.
    srv.idle_cortex_model = (args.idle_cortex_model or args.compact_model
                             or _pick_idle_cortex_model(backend, model))
    srv.default_seat_model = srv.seat_model   # the autoselect-lane pin-back
    srv.pending_embed_error = None
    srv.state_intercept = bool(args.state_intercept)
    srv.sem_intercept = bool(args.sem_intercept)
    srv.field_seeds = bool(args.field_seeds)
    srv.thought_intercept = bool(args.thought_intercept)
    srv.receipts = bool(getattr(args, "receipts", True))   # the receipt lane
    # THE EVENT BUS: RETIRED (the operator's 2026-10-03 call: MQTT is
    # gone entirely -- the lane carried one rarely-used watch kind and
    # cost the flapping reconnect storm per boot; cross-seat transport
    # is the peer_chat couch lane; file/signal triggers still ride the
    # autonode). No bus, no paho thread, no reconnect loops.
    srv.seat_id = f"proxy-{listen}"
    srv._event_bus = None
    # THE SPEECH-ACT TABLE (Phase W): the quantized act vocabulary and
    # the transition table for membrane's closed-loop participation
    from .acts import ActTable
    srv._act_table = ActTable()
    srv.dev = bool(getattr(args, "dev", False))
    srv._dev_warn = None

    # ---- W4b: the user ledger (the operator's verbatim decision log) ----
    # A plain JSONL next to the field; only USER turns write it (the
    # operator learn executor above); the model reads, never writes.
    srv.user_ledger = None
    try:
        from .ledger import UserLedger
        srv.user_ledger = UserLedger(str(args.field) + ".ledger.jsonl")
    except Exception:
        srv.user_ledger = None
    srv.agent_writes = bool(getattr(args, "agent_writes", True))
    srv._served_thought_asks = {}
    srv.gate_monologue = bool(args.gate_monologue)
    srv.replay_ab = max(0.0, min(1.0, float(args.replay_ab)))
    srv.embed_model_arg = args.embed_model
    srv.resolved_embed_name = args.embed_model

    # ---- the peers and the commons ----
    # Peers: every live seat registers itself (the registry heartbeat) and
    # answers /membrane/ask from its own field; --no-global turns it off. The
    # commons are claims in the shared store (claims.promote_commons at night,
    # the operator's `remember globally:`); the old commons file is converted
    # once.
    srv._gstop = threading.Event()
    srv.global_field = None
    srv.global_sim_min = max(0.0, min(1.0, float(args.global_sim_min)))
    from .global_field import field_id_for as _fid_for
    srv.fid = _fid_for(args.field)
    if args.use_global:
        try:
            from .global_field import Peers, field_id_for
            srv.listen_port = listen
            srv.gpeers = Peers(field_id_for(args.field))
            srv.gpeers.register(listen, str(args.field))
            print(f"[opencode] gpeers: {len(srv.gpeers.livelist())} other membrane(s) live",
                  file=sys.stderr)
            threading.Thread(target=_peer_heartbeat, args=(srv,),
                             name="peer-heartbeat", daemon=True).start()
        except Exception as e:
            print(f"[opencode] peers disabled ({_one_line(e)})", file=sys.stderr)
        _convert_old_commons(srv, args.global_field
                             or os.path.expanduser("~/.membrane/global-field.json"))

    # ---- the watch layer (spatial triggers, membrane/watch.py) ----
    # Probes live beside the field (<field-dir>/watches.json); events are
    # RAM-only (a restart loses undelivered reports -- same as thought asks).
    srv._watch_board = None
    try:
        _wdir = Path(args.field).parent
        # the entity store boots LATER than this block; the gate reads it
        # lazily per call
        _est = lambda: getattr(srv, "entity_store", None)

        def _watch_junk_gate(vec, w) -> bool:
            """Per-watch junk cut: machinery never trips a spatial watch.
            The watch's own junk_sim, else the entity-track default; the
            anchor screen comes from the entity store (lazily embedded)."""
            est = _est()
            if est is None:
                return True
            cut = getattr(w, "junk_sim", None)
            cut = JUNK_CUT_DEFAULT if cut is None else cut
            if not est.junk_screen._vecs and mem.embedder is None:
                return True
            return est.junk_screen.sim_of(vec, mem.embedder) < cut

        srv._watch_board = WatchBoard(_wdir / "watches.json",
                                      junk_gate=_watch_junk_gate)
        _n_w = len(srv._watch_board.watches)
        print(f"[opencode] watches: {_n_w} planted "
              f"(cap {MAX_WATCHES})", file=sys.stderr)
    except Exception as e:
        print(f"[opencode] watch layer disabled ({_one_line(e)})",
              file=sys.stderr)
    # THE ALERT DIAL (2026-10-08, the seat's review): watch events past the
    # budget fold into the WHERE WE ARE digest instead of interrupt turns;
    # the seat sets it with the attention tool (persisted next to the field)
    try:
        import json as _json
        from .attention import AlertGate
        srv._attention_path = str(_wdir / "attention.json")
        srv._models_path = str(_wdir / "models.json")      # the minds' models
        try:                                                # the saved picks, before any mind runs
            from .minds_models import prime as _mm_prime
            _mm_prime(srv)
        except Exception as e:
            print(f"[seat] minds' models not primed ({e})", file=sys.stderr)
        _saved = None
        if os.path.exists(srv._attention_path):
            _saved = _json.loads(open(srv._attention_path).read() or "{}")
        srv._alert_gate = AlertGate.from_settings(_saved)
        _g = srv._alert_gate.settings()
        print(f"[opencode] alert dial: {_g['budget']} interrupt turns per "
              f"{_g['window_min']:g} min, mode {_g['mode']}", file=sys.stderr)
    except Exception as e:
        srv._alert_gate = None
        print(f"[opencode] alert dial off ({_one_line(e)})", file=sys.stderr)
    srv._intent_board = None
    try:
        # no count here: the goal layer mounts later in the boot ('the
        # intent board is mounted (goals: N open)') -- a count now read the
        # bare json board and said '0 open' over a full board
        srv._intent_board = IntentBoard(_wdir / "intents.json",
                                        embedder=srv.membrane.embedder)
    except Exception as e:
        print(f"[opencode] intent layer disabled ({_one_line(e)})",
              file=sys.stderr)
    # ---- the entity store (the shared symbolic layer) ----
    # Derived entities (deterministic + idle-cortex synthesis) live in three
    # homes: RAM, <field>.entities.json, and cortex-core._default.entities
    # (the QuantumEntity collection -- one symbolic layer shared with the
    # cortex-experiment brain). Boot reads the JSON; read-through from the
    # couchbase commons is best-effort after the embedder resolves.
    srv._boot_ts = time.time()
    srv._tool_results = {}     # the receipt lane's result-side cache (E9)
    # THE ENTITY FIELD's intake (the rewrite; MEMBRANE_EFIELD defaults on,
    # off|shadow as lab ablations): events from the hooks, written by a
    # worker thread -- never in the chat
    # path. Couchbase for a real seat; RAM for lab paths or an unreachable
    # cluster (open_backend degrades, never raises).
    srv.capturer = srv.efield = None
    srv.efield_locked = False
    try:
        from .capture import Capturer, LazyEmbedder, mode_from_env
        if mode_from_env() != "off":
            from .efield import EntityField
            from .efstore import open_backend
            from .substrate import _origin_tag
            be = open_backend(args.field)
            srv.efield = EntityField(
                be, LazyEmbedder(mem),
                _origin_tag(args.field),
                # read the CACHED graph only: refreshing mutates it, and
                # that belongs to the chat thread
                graph_fn=lambda: getattr(srv, "_codegraph", None))
            srv.capturer = Capturer(srv.efield)
            # THE SELF-FEEDING GUARD'S CUT (the seat's #4): the fields
            # recall excludes entities whose ONLY deltas ride the
            # CURRENT turn's session (the boot's own session prefix
            # cut -- the query's own words can't feed the read)
            srv.efield._current_session_cut = _own_session_prefix(srv)
            # THE MODEL'S EVENT POOL (the reactive lane, live now):
            srv.events_pool = srv._events_pool = WatchEvents()
            # (the store's watch lanes put through it: wired where the store
            # is created, below -- touching srv.entity_store HERE raised
            # 'no attribute entity_store' and the outer except skipped the
            # rest of this block on every boot since 2026-10-02: no field
            # listeners, no dream corpus, no judges, no pulse)
            # the session's first event number: the 'now' line counts what
            # moved since the seat booted
            srv._efield_boot_seq = int(srv.efield._ostats.get("seq_last") or 0)
            # THE SEEDLESS BACKLOG (issue #33): notes minted pre-7c77d8d
            # stand 'empty' with no sids -- the boot sweep re-pends what the
            # fixed splitter now recovers, and the ordinary embed/apply
            # flow graduates them; a row that still yields nothing keeps
            # its honest 'empty'. Never breaks the boot.
            try:
                _seeded = srv.efield.backfill_seedless()
                if _seeded:
                    print(f"[seat] seedless backfill: {_seeded} pre-fix "
                          "note(s) re-embedded", file=sys.stderr)
            except Exception as e:
                print(f"[seat] seedless backfill skipped ({e})",
                      file=sys.stderr)
            # THE WORD INDEX (#429 hybrid recall): built from the events
            # in RAM on its own thread; recall runs without the word lane
            # until it is ready, and capture keeps it current after
            try:
                from . import lexical as _LX
                _LX.warm(srv.efield)
            except Exception as e:
                print(f"[seat] word index off ({e})", file=sys.stderr)
            # THE RERANKER (#680): its model loads in the background now,
            # not on the first topical turn after a restart
            try:
                from . import recall_rerank
                recall_rerank.warm(peers=lambda: srv.gpeers.livelist()
                                   if getattr(srv, "gpeers", None) else [])
            except Exception as e:
                print(f"[seat] reranker warm skipped ({e})", file=sys.stderr)
            # the tool-output chunks (#429) are read only on question-like
            # asks: build their vector matrix now, not on the first question
            _wv = getattr(srv.efield.b, "warm_vectors", None)
            if _wv is not None:
                try:
                    _wv("chunks", "vec")
                    _wv("sentences", "vec")    # #720: an indexed store's first query, off the first turn
                except Exception:
                    pass
            # THE FIELD'S PULSE (viewer v3): field + mind frames on the
            # serve's hub (the hub binds once the runner exists)
            try:
                from .fieldpulse import FieldPulse, efield_listener
                # started with the serve's runner (below): a thread born
                # this early in the boot never beat on the live seat
                srv._field_pulse = FieldPulse(srv)
                srv.efield.pulse_moved = srv._field_pulse.moved
                srv.efield.pulse_born = srv._field_pulse.born
                srv.efield.listeners.append(efield_listener(srv))
            except Exception as e:
                print(f"[seat] field pulse off ({e})", file=sys.stderr)
            # THE WATCH FRAME (entities as axes): what the seat watches is
            # the frame every applied message is measured along
            try:
                from .frame import Frame, listener as _frame_listener
                srv._frame = Frame(srv)
                srv.efield.listeners.append(_frame_listener(srv))
            except Exception as e:
                print(f"[seat] watch frame off ({e})", file=sys.stderr)
            # THE FOREWARNING (stage 3a, recording only): before a tool result
            # exists, what followed the most similar past moments; the pool
            # rebuilds from the field's history in the background
            try:
                from .forewarn import Forewarn
                srv._forewarn = Forewarn(srv.efield)
                srv.efield.step_listeners.append(srv._forewarn.listener)
                srv._forewarn.start()
            except Exception as e:
                print(f"[seat] forewarning off ({e})", file=sys.stderr)
            if srv.capturer.mode == "on":
                srv.efield.listeners.append(_efield_watch_listener(srv))
                from .efmind import DreamSource, on_applied_fn
                srv.efield.listeners.append(on_applied_fn(srv, lambda: _own_session_prefix(srv)))
                srv.dream_source = DreamSource(srv.efield)
                # THE PAUSE'S CLAIM GATE (the idle judge is acting):
                srv.efield.pause_fn = lambda: nerve_paused(srv)
                _install_judges(srv)
            print(f"[efield] capture {srv.capturer.mode} "
                  f"({type(be).__name__})", file=sys.stderr)
            # (THE PRE-SPLIT COUNTS: no boot relabel -- a full pass rewrote
            # every state row into the append-only journal, ~250MB; pre-split
            # states convert on their next mention: efield.adopt_legacy)
    except Exception as e:
        try:
            from .cblstore import StoreBusy as _StoreBusy
            if isinstance(e, _StoreBusy):
                # the field writer lock is held by a live serve (issue
                # #127): the entity/read door refuses with a clear
                # 'serve-holds-field-lock' instead of closing the conn.
                srv.efield_locked = True
        except Exception:
            pass
        print(f"[efield] capture off ({e})", file=sys.stderr)
    # (srv.receipts is set above from --no-receipts; a re-assignment here
    # to True used to silently kill the kill switch)
    srv.entity_store = None
    srv._presence_roster = []
    try:
        from .entities import EntityStore, JUNK_CUT_DEFAULT
        # THE SEAT-FIRST FIELD: the store's identity is the PERSISTED
        # origin tag (``<start-folder>@<hostname>`` -- every boot of this
        # folder reuses it), so the pub- and import lanes address THIS
        # seat by the same name on every restart.
        from .substrate import _origin_tag
        srv.entity_store = EntityStore(path=str(_wdir / "entities.json"),
                                       origin=_origin_tag(args.field))
        # THE SEAT'S ROOT (the stale-route retire's reference): the
        # start folder = the field path's resolved parent, or its
        # parent when the parent IS .membrane
        srv.entity_store.root = str(_wdir.parent) \
            if _wdir.parent.name == ".membrane" else str(_wdir)
        # THE FIELD-WATCH WIRING (the change lane's intake): the
        # field's flush reads the store as its sink (--est-sink) and
        # the store carries the seat back (the pool lives on the seat)
        srv.entity_store._srv = srv
        if getattr(srv, "events_pool", None) is not None:
            srv.entity_store._events_pool = srv.events_pool
        if getattr(srv, "efield", None) is not None:
            srv.efield._est_sink = srv.entity_store
        # THE BOOT ENTITY (the operator's ask): one immutable record
        # per boot (host/root/field/pid/argv/boot_ts) + the lineage
        # link + the presence's current-boot facts:
        try:
            boot_entity(srv.entity_store, root=srv.entity_store.root,
                        field=str(args.field),
                        pid=os.getpid(),
                        argv=sys.argv[-6:],
                        boot_ts=srv._boot_ts)
        except Exception as _be:
            print(f"[opencode] boot entity skipped ({_be})",
                  file=sys.stderr)
        # THE MODEL'S EVENT POOL (the reactive layer's lane): the watch
        # fires, the script errors/overruns, the peer misses -- the
        # loops' reads land here; the store's watch lanes put through
        # it (attach/detach events).
        # (the pool is wired above with the field: resetting it here dropped
        # every field:changed event -- 2026-10-11's sweep)
        # THE CLOCK (the operator's 2026-10-01 ask): plant `tool:clock`
        # once at boot -- idempotent (an already-living, model- or
        # operator-Rewritten clock survives every restart). Degrades
        # silently (the store is the guard).
        try:
            from .night import seed_clock_entity
            try:                                  # the operator: the default rel of every watch op
                from .watch_agents import plant_operator
                plant_operator(srv.entity_store)
            except Exception as e:
                print(f"[opencode] operator entity not planted ({e})", file=sys.stderr)
            if seed_clock_entity(srv.entity_store):
                print("[seat] the clock entity is planted (tool:clock, "
                      "fires on the thinker's heartbeat)", file=sys.stderr)
        except Exception as e:
            print(f"[seat] clock seeding skipped ({e})", file=sys.stderr)
        # THE PRESENCE ORGAN (the seat-first field's heartbeat): the seat
        # announces itself the moment it boots -- published to the global
        # tier (the ONE seat-auto lane) and read as the roster by every
        # other seat's thinker. Degrades couchbase-down: the LOCAL doc
        # still lands; the thinker's 60-beat pulse carries the publish up.
        try:
            from .thinker import _git_identity
            _pfacts = dict(_git_identity())
            _pfacts.update({"uptime": 0, "beats": 0,
                            "docs": len(srv.entity_store.docs)})
            _pres = srv.entity_store.update_presence(_pfacts)
            srv._presence_roster = srv.entity_store.peers()
            if _pres.get("ok"):
                print(f"[seat] presence published ({srv.entity_store.origin}; "
                      f"{len(srv._presence_roster)} peer(s) on the roster)",
                      file=sys.stderr)
            else:
                print("[seat] presence planted locally (couchbase down; "
                      "the thinker's pulse carries it up)", file=sys.stderr)
        except Exception as e:
            print(f"[seat] presence seeding skipped ({e})", file=sys.stderr)
        # THE STEERING WHEEL'S MOUNT (the boot-half of the intent lane,
        # 2026-10-06): the board was built BEFORE the entity store existed
        # and no boot attached them -- every tool lane read the raw board
        # and hit 'no goal layer' (`_intents_board()`'s lazy attach was
        # reachable only from the frame lane). The attach is idempotent
        # (attach_entity_store guards) -- one call below mounts the wheel
        # (_intents_board, after the executor's seeding);
        # the boot also RE-DERIVES the wakes from the board (the durable
        # steering: open intents re-queue their lanes -- boot_wakes below,
        # after the pool lives).
        # THE AUTONODE (the operator's 2026-10-01 design): seed the
        # filetree template, then run the trigger layer -- file metadata
        # under the starting dir, MQTT topics, signals -- firing every
        # watcher entity's watch_script. Degrades never raising (the
        # seat works without watchers); --no-autonode opts out.
        try:
            from .autonode import seed_filetree_entity
            if seed_filetree_entity(srv.entity_store):
                print("[seat] the filetree watcher is planted "
                      "(tool:filetree -- give any entity a watch_script "
                      "to watch like it)", file=sys.stderr)
        except Exception as e:
            print(f"[seat] filetree seeding skipped ({e})", file=sys.stderr)
        try:                                  # #715: fs:dir / fs:file / fs:code, the classes an entity extends
            from .fstree import seed_classes
            seed_classes(srv.entity_store)
        except Exception as e:
            print(f"[seat] fs classes seeding skipped ({e})", file=sys.stderr)
        # THE EXECUTOR (2026-10-06): the exec organ -- commands link to
        # it and the engine runs them inline (the 'executes' edge is the
        # ledger); idempotent like every seeding.
        try:
            from .night import seed_executor_entity
            if seed_executor_entity(srv.entity_store):
                print("[seat] the executor is planted (tool:executor -- "
                      "methods run commands through it)", file=sys.stderr)
        except Exception as e:
            print(f"[seat] executor seeding skipped ({e})", file=sys.stderr)
        # ONE SHAPE LOGIC (#741): retire the passthrough methods (marked, never deleted)
        # and note every other method on its shape's node, so a run that has a method
        # runs as it; idempotent.
        try:
            from .lang import boot_methods
            _bm = boot_methods(srv)
            if _bm["retired"]:
                print(f"[seat] #741 retired passthrough methods: {', '.join(_bm['retired'])}",
                      file=sys.stderr)
        except Exception as e:
            print(f"[seat] method shapes skipped ({e})", file=sys.stderr)
        # THE WARM-UP (2026-10-10: the first turn after a restart paid 11.8 s
        # for the intent seeds -- a full scan + every seed embedded cold):
        # done in the background right after boot, nobody waits for it
        def _warm_seeds(_srv=srv):
            try:
                time.sleep(20)
                from . import lanes as _L
                _L._embed_seed_matrix(_srv)
                _L._field_seed_topics(_srv)
                _L._embed_field_seed_matrix(_srv)
                print("[seat] intent seeds warmed", file=sys.stderr)
            except Exception as e:
                print(f"[seat] seed warm-up skipped ({e})", file=sys.stderr)
        threading.Thread(target=_warm_seeds, name="seed-warmup", daemon=True).start()
        # THE FIRST INTERFACE (2026-10-10): remote_executor -- exec(code, timeout)
        try:
            from .night import seed_interfaces
            if seed_interfaces(srv.entity_store):
                print("[seat] interface planted: remote_executor (exec(code, timeout))", file=sys.stderr)
        except Exception as e:
            print(f"[seat] interface seeding skipped ({e})", file=sys.stderr)
        srv.autonode = None
        if not args.no_autonode:
            try:
                from .autonode import Autonode
                srv.autonode = Autonode(srv, srv.entity_store,
                                        root=os.getcwd(),
                                        bus=getattr(srv, "_event_bus",
                                                    None))
                srv.autonode.install_signals()
                srv.autonode.start()
                print("[seat] the autonode is listening (file/mqtt/"
                      "signal triggers -> watcher entities; --no-autonode "
                      "to silence)", file=sys.stderr)
            except Exception as e:
                srv.autonode = None
                print(f"[seat] autonode off ({e})", file=sys.stderr)
        # THE STEERING WHEEL'S MOUNT + THE BOOT'S BOOK-BALANCE (the
        # durability plan, 2026-10-06): three boot-time acts on the
        # intent lane -- all before the wake derivation:
        #   1. ATTACH the board to the entity store (the wheel was
        #      built pre-store; the lazy attach was only reachable from
        #      the frame lane -- every tool route hit 'no goal layer').
        #   2. EXPIRE the executor's stale debts WITH their receipts
        #      (the phantom-debt fix: an owed edge past its TTL dies
        #      WITH its receipt -- cancellation as durable as the debt;
        #      never re-run).
        #   3. RE-DERIVE the wakes from the board (the durable
        #      steering): the boot reads the OPEN UNCLAIMED intents and
        #      re-queues their lanes (an 'intent-planted' event per
        #      goal) -- the listeners wake on the board's authority, not
        #      on runtime state. The pool is live by now (the field's
        #      boot wired it); the loops drain it when the day starts.
        try:
            _ib = _intents_board(srv)
            if _ib is not None and _ib.selection is not None:
                print(f"[seat] the intent board is mounted (goals: "
                      f"{len(_ib.open_intents())} open)", file=sys.stderr)
        except Exception as e:
            print(f"[seat] intent board mount skipped ({e})", file=sys.stderr)
        try:
            if getattr(srv, "entity_store", None) is not None \
                    and getattr(srv, "membrane", None) is not None:
                from .night import expire_stale_edges
                receipts = expire_stale_edges(srv, srv.entity_store,
                                              _efield_on(srv), srv.membrane)
                if receipts:
                    print(f"[seat] the executor's ledger: {len(receipts)} "
                          "stale edge(s) expired-with-receipt at boot",
                          file=sys.stderr)
        except Exception as e:
            print(f"[seat] executor expiry skipped ({e})", file=sys.stderr)
        try:
            _ib2 = _intents_board(srv)
            _pool2 = getattr(srv, "events_pool", None)
            if _ib2 is not None and _pool2 is not None:
                from .watch_agents import boot_wake_goals
                from .entities import CLASS_GOAL
                # THE BOOT'S WAKE rides the pulse's rules (2026-10-08:
                # re-firing every open goal on every boot was 37% of the
                # loops' tool calls): stale goals only, with the backoff,
                # parked goals wait for the operator
                _n_goals = sum(
                    1 for _d in list((srv.entity_store.docs or {}).values())
                    if _d is not None and _d.cls == CLASS_GOAL
                    and ((_d.properties or {}).get("status") or "open") == "open")
                _n_wakes = boot_wake_goals(srv)
                print(f"[seat] the board re-derives the wakes: "
                      f"{_n_wakes}/{_n_goals} open goal(s) re-queued to "
                      "the listeners", file=sys.stderr)
        except Exception as e:
            print(f"[seat] boot wakes skipped ({e})", file=sys.stderr)
        # THE PEER SUBSTRATES (the operator's design): the proxy REGISTRY
        # (ephemeral bucket, TTL docs, heartbeats -- who else is connected)
        # + the PEER CHANNEL (permanent, a chat lane between the LLMs,
        # visible on every seat's banner). Degraded silently when
        # couchbase is down (the same covenant as the entity layer).
        try:
            from .peers import PeerSubstrate
            # THE ROSTER FALLBACK (stage 5): when the ephemeral registry
            # reads empty (down, or a seat-first world), the peers reads
            # answer from the seat's own presence organ (est.peers()).
            _roster = getattr(srv, "entity_store", None)
            srv.peers = PeerSubstrate(
                field_path=str(_wdir / "field.json"),
                port=listen, model=str(srv.seat_model),
                roster_fn=(_roster.peers if _roster is not None else None))
            srv.peers.register()
            print(f"[opencode] peers: registered as {srv.peers.seat_id} "
                  f"(heartbeat {int(srv.peers.heartbeat_s)}s)",
                  file=sys.stderr)
        except Exception as e:
            srv.peers = None
            print(f"[opencode] peers unavailable ({e})", file=sys.stderr)
        # the DEFAULT track (the operator's design): 'now' -- the session's
        # living present. The model is a vessel (constant, uninformative);
        # the conversation's direction is the thing worth watching. The
        # story renders from the fresh tail (nothing tags 'now'; everything
        # is now); the position drifts with the momentum EMA + fresh states.
        evicted_boot = srv.entity_store._enforce_cap()
        if evicted_boot:
            print(f"[opencode] track cap: evicted {evicted_boot} "
                  f"(story-poorest)", file=sys.stderr)
        # THE GEOMETRY BOOTSTRAP (the cell-story fix): every planted
        # track consolidates AT BOOT (not only at plant time) -- tracks
        # planted before the seeding code existed (or whose entity doc
        # the reharvest/eid-dual fix orphaned) get their geometry now,
        # so the render is geometry-first instead of cell recitation.
        try:
            # THE TRACK-ENTITY GUARANTEE first: doc-less tracks (planted
            # before the seeding code, or model-added via entity_add)
            # get their docs NOW -- then consolidate to seed the rings
            for k in list(srv.entity_store.tracks.keys()):
                if k != "session:now":
                    srv.entity_store.ensure_track_entity(k)
            from .night import consolidate_entities, run_scripted_entities
            _bfld = getattr(srv, "efield", None)
            if _bfld is None:
                raise RuntimeError("entity field is off: no deltas to drift on")
            # only_keys=[] means 'no filter' in night.py -- it would drift
            # EVERY overlay doc at boot (2248 here); bootstrap tracks only,
            # and only when there are any.
            _tk = [k for k in list(srv.entity_store.tracks.keys())
                   if k != "session:now"]
            if _tk:
                consolidate_entities(_bfld, srv.entity_store,
                                     mem=mem, materialize=0,
                                     only_keys=_tk)
            # THE SCRIPT PASS AT BOOT: a script edit between boots must
            # echo immediately (the stale script_render otherwise rides
            # the banner until the next 15-min night tick)
            out = run_scripted_entities(_bfld, srv.entity_store,
                                        mem=mem, force=True)
            if out.get("errors"):
                print(f"[opencode] script pass: {out['errors']} error(s)",
                      file=sys.stderr)
        except Exception as e:
            print(f"[opencode] track geometry bootstrap skipped ({e})",
                  file=sys.stderr)
        if "session:now" not in srv.entity_store.tracks:
            srv.entity_store.watch_track(
                "session:now", label="now (the session's living present)")
        _n_e = len(srv.entity_store.docs)
        print(f"[opencode] entities: {_n_e} known "
              f"(cortex-core write-through: {'on' if srv.entity_store._entities_collection() else 'json-only'})",
              file=sys.stderr)
    except Exception as e:
        print(f"[opencode] entity layer disabled ({_one_line(e)})",
              file=sys.stderr)

    def _entity_sink(ents, links):
        """The synthesis scripts' commit path: merge + write-through the
        curated overlay."""
        est = getattr(srv, "entity_store", None)
        if est is None:
            return
        for ent in ents:
            for i in (links or []):
                if i not in ent.links:
                    ent.links.append(i)
            try:
                est.upsert(ent, embedder=srv.membrane.embedder)
            except Exception:
                pass
    srv.entity_sink = _entity_sink
    # THE DREAMER (the timeline organ, dreamer v2): idleness becomes
    # timeline generation -- dreamed branches as Timeline entities under
    # session:now (the only true timeline), steered by open intentions,
    # elected by reality's vote, GC'd by the ephemeral clock. The cell
    # pool's replacement (the same idle-cognition role, on the main
    # field, no parallel Membranes). --no-dreamer stands it down.
    srv.dreamer = None
    if not getattr(args, "no_dreamer", False):
        try:
            from .dreamer import Dreamer
            srv.dreamer = Dreamer(srv, idle=60.0)
            srv.dreamer.start()
        except Exception as e:
            print(f"[opencode] dreamer boot failed (non-fatal): {e}",
                  file=sys.stderr)
    # THE THINKER (the fast clock, separate from the dreamer -- the
    # operator's design: 'thinking is different from dreaming'): rumination
    # -- the last stimuli digested (captures, dream outcomes, night
    # findings) and, in silence, quarantined branches extended. Working
    # memory = 5 items, drop-oldest; voice always planner-gated; model
    # calls capped per minute. --no-thinker stands it down.
    srv.thinker = None
    # THE THINK DIAL (args.think -> the body's think default): auto =
    # None = the payload rides verbatim, the seat says nothing.
    srv.think = think_value(getattr(args, "think", None))
    if not getattr(args, "no_thinker", False):
        try:
            from .thinker import Thinker, NIGHT_PULSE_S
            # THE NIGHT MODE'S KNOBS (the animal covenant): the pulse
            # pair + the night hold live on the seat; the thinker's
            # _hum reads both every beat.
            srv.ambient_night = not getattr(args, "no_night", False)
            _pair = "1.0/30.0"
            try:
                _pair = str(getattr(args, "ambient_pulse", "") or "1.0/30.0")
                _day, _night = (_x.strip() for _x in _pair.split("/", 1))
                srv.ambient_pulse = (max(0.2, float(_day)),
                                     max(2.0, float(_night)))
            except Exception:
                srv.ambient_pulse = (1.0, NIGHT_PULSE_S)
            srv.ambient_mode = "day"
            srv.thinker = Thinker(srv)
            srv.thinker.start()
        except Exception as e:
            print(f"[opencode] thinker boot failed (non-fatal): {e}",
                  file=sys.stderr)
    # THE BOOT-CONTEXT SEED (the init-speak enabler): the boot context's
    # ActTable row is observed report-state x2 BEFORE the hello -- the
    # table has LEARNED boot contexts speak, so _init_speak's consult
    # selects report-state (not the stay-silent veto) on every fresh
    # boot. Without this seed the hello is vetoed forever (the init
    # speak was a no-op on any fresh boot -- the unlearned-table
    # stay-silent default).
    try:
        srv._last_context_sig = "boot|0.5"
        srv._act_table.observe("boot|0.5", "report-state")
        srv._act_table.observe("boot|0.5", "report-state")
    except Exception:
        pass
    _init_speak(srv)
    if _efield_on(srv) is None:
        print("[opencode] night: idle -- the entity field is unavailable "
              "(no backend; the NightClock does not start)", file=sys.stderr)
    # THE STANDALONE NIGHT: the NightClock runs the night on the entity
    # field (the entity scripts, births, junk, the activity, the daily
    # coherence null) on its own 15-minute timer -- no compactor rides it.
    else:
        import threading as _th

        def _night_loop():
            while True:
                time.sleep(900)          # a 15-minute tick
                # THE IDLE SPEECH DRAIN IS RETIRED (the fourth lock, the
                # operator's post-mortem): this tick called drain_speech()
                # and DISCARDED the return -- popping every held message
                # every 15 minutes and showing none (the peer-push comment
                # above was stale: the push was retired, the drain had
                # become a silent eater that also graded speech the
                # operator never saw). Off-turn the opencode TUI cannot be
                # written (no open stream; membrane never fabricates a
                # client request) -- so held speech now simply SURVIVES
                # the idle gap: _SPEAK_TTL_S bounds staleness, _SPEAK_CAP
                # bounds volume, and the next model-bound request relays
                # it (the [membrane-says] footer). The pending label is
                # set by that drain only.
                # THE PAUSE (the stable point): the night's bodies defer
                # -- overlay scripts, consolidation, materialization are
                # the machine ACTING; the cadence continues so the resume
                # is automatic (the next tick after unpause runs).
                if nerve_paused(srv):
                    continue
                fld = _efield_on(srv)
                if fld is None:
                    continue
                try:
                    from .efmind import night as _ef_night
                    srv.night_stats = _ef_night(srv, fld,
                                                getattr(srv, "entity_store", None))
                    # THE METABOLISM'S NIGHT HALF (the durability plan):
                    # every 15-min pass expires the executor's stale
                    # debts WITH their receipts (a boot can't miss what
                    # grows between boots)
                    from .night import expire_stale_edges as _exp
                    _exp(srv, getattr(srv, "entity_store", None), fld,
                         getattr(srv, "membrane", None))
                    # the code trees past code_runs_keep are deleted (lang.collect)
                    from . import lang as _LGC
                    _gc = _LGC.collect(srv)
                    if _gc.get("nodes") or _gc.get("results"):
                        print(f"[night] code trees collected: {_gc}", file=sys.stderr)
                    b = srv.night_stats.get("births")
                    if b:
                        print(f"[night] born: {', '.join(b)}", file=sys.stderr)
                        th = getattr(srv, "thinker", None)
                        if th is not None:
                            th.enqueue("night",
                                       {"materialized": len(b), "drifted": 0})
                except Exception as e:
                    print(f"[night] loop error ({e})", file=sys.stderr)

        _th.Thread(target=_night_loop, daemon=True,
                   name="membrane-night").start()
        print("[opencode] night: standalone NightClock on (15-min tick)",
              file=sys.stderr)
    # THE LOOP POOL (the reactive layer, the operator's 2026-10-02
    # design): 3 concurrent non-operator thinking loops + 1 dedicated
    # operator lane; the watch-events queue DRAINS into real turns --
    # a loop = a child session whose AGENT NAME carries the processed
    # entity's id; the model reacts or ignores; the verdict stamps the
    # entity. Serve mode: the loops ride the TurnRunner (a loop's turn
    # IS a serve turn); proxy-only: the loop turns ride _model_chat
    # (one deliberation per loop, no tools -- the honest degrade).
    srv.loops = None
    if getattr(srv, "events_pool", None) is not None:
        srv.loops = LoopRunner(max_loops=max(1, int(os.environ.get("MEMBRANE_LOOPS_MAX") or 3)))   # an HA box: fewer
        srv.loops._srv = srv
        srv.loops.titles = []          # the running agents' names (the banner)

        # THE SESSION-MAKER (the storm's last exhibit, 2026-10-07: the
        # DOUBLE LAMBDA -- the outer lambda's call returned the INNER
        # lambda uncalled, so every loop 'sid' was a FUNCTION OBJECT:
        # the ladder's body json died with 'Object of type function is
        # not JSON serializable' => every loop turn = 'model ladder
        # failed' = the executor 'not executing'). One plain function.
        def _make_loop_session(agent, title=None):
            return loop_session(srv, agent)
        srv.create_loop_session = _make_loop_session
        srv._loop_runner = None        # wired by the serve boot below

        def _run_loop_turn(srv=srv):
            def go(session_id, text, agent):
                runner = getattr(srv, "_loop_runner", None)
                if runner is not None:
                    out = runner.run(session_id, text)
                    # #561: no bgusage tally here: the serve turn is a stored row, the stats count it from there
                    parts = (out or {}).get("parts") or []
                    txt = "\n".join(str(p.get("text") or "")
                                    for p in parts if p.get("type") == "text")
                    trail = []
                    for p in parts:
                        if p.get("type") != "tool":
                            continue
                        st = p.get("state") or {}
                        res = str(st.get("output") or st.get("error") or "")
                        trail.append(f"{p.get('tool')} {st.get('status', '')}: "
                                     f"{' '.join(res.split())[:60]}")
                    # the WHOLE text: the verdict is its last line
                    if isinstance((out or {}).get("quota"), dict):
                        # out of tokens (quota.py): no verdict, the event waits
                        return {"text": "", "quota": out["quota"]}
                    return {"text": txt[-4000:], "verdict_text": txt[:200],
                            "trail": trail[-6:],
                            # the turn's model (#254: self trusts an advisor per model)
                            "model": ((out or {}).get("info") or {}).get("modelID")}
                # the proxy-only degrade: ONE model call, no tools
                from .rag import _model_chat
                model = (getattr(srv, "idle_cortex_model", "") or "")
                out = _model_chat(srv)(getattr(srv, "backend", ""),
                                       model,
                                       [{"role": "system", "content":
                                         "You are a membrane watch loop: "
                                         "decide, briefly."},
                                        {"role": "user",
                                         "content": text}], timeout=45,
                                       lane="watch")
                return {"verdict_text": (out or "")[:200], "model": model or None}
            return go
        srv.run_loop_turn = _run_loop_turn()
        try:                               # #576 G: the retired link commands go (once; a backup is kept)
            from . import scripts as _SCR
            if getattr(srv, "entity_store", None) is not None:
                _rep = _SCR.migrate(srv.entity_store)
                if _rep["stripped"] or _rep["deleted"]:
                    print(f"[opencode] old commands: {_rep['stripped']} program entities stripped, "
                          f"{_rep['deleted']} variants deleted (backup {_rep['backup']})", file=sys.stderr)
        except Exception as e:
            print(f"[opencode] command migration failed ({e})", file=sys.stderr)
        try:                               # #576 B: the cliffhanger VM hears every write
            from . import ch as _CH
            if getattr(srv, "entity_store", None) is not None and getattr(srv, "ch_vm", None) is None:
                srv.ch_vm = _CH.VM(srv.entity_store, paused=lambda: bool(nerve_paused(srv)))
                srv.ch_vm.attach()
                srv.ch_vm.start()
                from . import ch_blurp as _CHB    # #576 D2: Blurp's learner, its purse closed until Blurp reports
                _CHB.attach(srv)
                _CHB.anew(srv)                   # once per generation: Blurp starts anew
        except Exception as e:
            print(f"[opencode] the cliffhanger VM failed to start ({e})", file=sys.stderr)
        from .watch_agents import enabled_loops as _wa_on, start_goal_pulse
        if _wa_on():                       # MEMBRANE_WATCH_LOOPS=0: the documented kill switch (never read before)
            WA_start_loop_drainer(srv)
            start_goal_pulse(srv)
            try:                           # #738: goals as a graph -- the triggers, the worker pool
                from . import goalgraph as _GG
                _n = _GG.seed(srv)
                _r = _GG.reowe(srv)
                print(f"[opencode] goal graph: {_n} triggers, {_r} task(s) back in the worker pool",
                      file=sys.stderr)
            except Exception as e:
                print(f"[opencode] goal graph seeding skipped ({e})", file=sys.stderr)
        else:
            print("[opencode] watch loops OFF (MEMBRANE_WATCH_LOOPS=0)", file=sys.stderr)
        print("[opencode] the loop pool is live (3 loops + the "
              "operator's lane; the drainer consumes the watch events)",
              file=sys.stderr)
    # the orchestration router (model-vs-model): discovered from the live
    # Ollama (locals AND remote-backed cloud entries), imprints seeded in the
    # background, records persisted beside the field. OFF with --no-router.
    srv.router = None
    if args.router:
        try:
            from .router import Registry, PRICE_TABLE
            table = list(PRICE_TABLE)
            for spec in (args.price or []):
                name, _, usd = spec.rpartition("=")
                if name and usd:
                    table.insert(0, (name.lower(), float(usd)))
            import membrane.router as _rt
            _rt.PRICE_TABLE = table
            rstate = (args.router_state
                      or str(Path(args.field).parent / "router.json"))
            srv.router = Registry(
                backend=backend, embedder=mem.embedder,
                state_path=rstate, explore=float(args.explore),
                cloud_budget=args.cloud_budget,
                prefer_local=bool(args.prefer_local),
                max_local_gib=float(args.max_local_gib),
                opt_in=set(getattr(args, "ladder_include", None) or ()))
            # Cloud providers as first-class backends: for every provider in
            # auth.PROVIDERS with a key configured (`membrane auth <p>` or
            # its env var), the provider's models register as cloud rungs
            # (backend=<provider>) and ride the same ladder -- cheapest-
            # eligible floor, operator ceiling, outages, spend. Free-tier
            # providers pin near-floor prices so a healthy local still wins.
            # No keys -> no rungs (silent, local).
            from . import auth as _auth
            srv.cloud_keys = {}
            srv.cloud_models = []
            for pname, prov in list(_auth.PROVIDERS.items()) \
                    + list(_auth.AGENT_PROVIDERS.items()):
                key = _auth.load_api_key(pname)
                if not key:
                    continue
                if prov.get("kind") == "agent-sdk":
                    # issue #384: Claude through the Agent SDK -- no listing;
                    # on the anthropic API key by default (#413), the CLI
                    # login only as an explicit opt-in; opt-in rungs with
                    # membrane's tools only, learned like any model
                    # (membrane/claude_agent.py)
                    try:
                        from . import claude_agent as _CA
                        _CA.register_rungs(srv.router)
                        srv.cloud_models.append(pname)
                        try:
                            _CA.check_available()
                            _ok = ("CLI login (development opt-in)"
                                   if _CA.auth_mode() == _CA.MODE_LOGIN
                                   else "Anthropic API key")
                            _ok = f"SDK + CLI found, {_ok}"
                        except _CA.Unavailable as _ue:
                            _ok = f"NOT usable yet: {_ue}"
                        print(f"[opencode] {pname}: rungs "
                              f"{', '.join(_CA.rung_names())} (opt-in, "
                              f"membrane's tools only; {_ok})",
                              file=sys.stderr)
                    except Exception as p_e:
                        print(f"[opencode] {pname} rungs skipped ({p_e})",
                              file=sys.stderr)
                    continue
                srv.cloud_keys[pname] = key      # memory only
                try:
                    pairs = _auth.fetch_models_info(pname, key)
                    price = prov.get("default_price")
                    n_p = srv.router.add_cloud_models(
                        [mid for mid, _f, _ctx in pairs], provider=pname,
                        price=price,
                        free_ids={mid for mid, f, _ctx in pairs if f})
                    if n_p:
                        n_free = sum(
                            1 for mid, _f, _ctx in pairs
                            if (rec := srv.router.records.get(mid)) is not None
                            and rec.price() <= 0.1)
                        srv.cloud_models.append(pname)
                        print(f"[opencode] {pname}: {n_p} model(s) registered "
                              f"as cloud rungs (key {_auth.mask(key)})"
                              + (f", {n_free} free-tier expert(s) at near-floor"
                                 if n_free else ""), file=sys.stderr)
                except Exception as p_e:
                    print(f"[opencode] {pname} rungs skipped ({p_e})",
                          file=sys.stderr)
            # imprints now seed LAZILY (plan #6): the interview thread starts
            # on the first route()/ladder() that could use a claim -- models
            # that never become routable never cost a boot chat call.
            n_local = sum(1 for r in srv.router.records.values()
                          if r.kind == "local")
            print(f"[opencode] router: {len(srv.router.records)} candidates "
                  f"({n_local} local, {len(srv.router.records) - n_local} "
                  f"cloud), imprints seeding in background", file=sys.stderr)
        except Exception as e:
            print(f"[opencode] router disabled ({e}); escalating directly "
                  f"to {backend}", file=sys.stderr)
            srv.router = None

    # resolve the embedder AT BOOT: the field must be able to embed before a
    # single turn (a fresh field also needs nothing, but the resolver proves
    # the embed role early rather than on the first chat).
    # a backend that doesn't answer at all fails every probe -- after up to 180 s
    # each (an HA child waited minutes for nothing): one quick check first
    _backend_up = True
    try:
        with urllib.request.urlopen(backend + "/api/version", timeout=8):
            pass
    except urllib.error.HTTPError:
        pass                               # it answered (an OpenAI-only server, say)
    except Exception as e:
        _backend_up = False
        print(f"[opencode] backend {backend} not answering ({type(e).__name__}): no embed "
              "probes -- the seat runs offline until it does", file=sys.stderr)
    if srv.embed_model_arg and _backend_up:
        emb = _probe_embedder(backend, srv.embed_model_arg)
        if emb is not None:
            mem.embedder = emb
            srv.pending_embed_error = None
    if mem.embedder is None and _backend_up:
        # ladder: known embed models, then the selected chat model
        for cand in _known_embed_names(backend) + ([model] if model else []):
            if cand in [srv.embed_model_arg]:
                continue
            probe = _probe_embedder(backend, cand)
            if probe is not None:
                mem.embedder = probe
                srv.resolved_embed_name = cand
                print(f"[opencode] embedder resolved: {cand}", file=sys.stderr)
                break

    # now that the embedder is proven, the router's imprint seeding can run
    # (it embeds the models' self-descriptions); a router built before this
    # point holds embedder=None and would never seed.
    if srv.router is not None and mem.embedder is not None \
            and srv.router.embedder is None:
        srv.router.embedder = mem.embedder

    srv.cell_pool = None

    # ---- the opencode config + launch ----
    # first-class cloud backends join the picker: any router record with an
    # explicit backend tag (provider name) is a selectable model -- selecting
    # one sets the operator's ceiling (cloud-capped-at-its-price) like any pick.
    cloud_models = [r.name for r in
                    (getattr(srv.router, "records", {}).values()
                     if getattr(srv, "router", None) else [])
                    if getattr(r, "backend", "") and r.kind == "cloud"]
    # the AUTOSELECT sentinels ride the picker too (the router itself as a
    # selectable "model"): @-free names (some pickers mangle odd characters;
    # @-spelled forms stay accepted aliases). Only with a router present.
    if getattr(srv, "router", None) is not None:
        auto_models = list(AUTO_LANES)
    else:
        auto_models = []
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "provider": {
            "membrane": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Membrane (field)",
                "options": {"baseURL": f"http://127.0.0.1:{listen}/v1"},
                "models": {m: {"name": m}
                           for m in chat_models + cloud_models + auto_models},
            },
        },
    }
    # JWT mode: the provider's key is the env placeholder opencode resolves
    # (the child's MEMBRANE_JWT_TOKEN, jwtauth.child_env) -- no token on disk
    cfg["provider"]["membrane"]["options"] = _JWT.provider_options(
        cfg["provider"]["membrane"]["options"])
    # ---- doctrine split (P0b) -------------------------------------------------
    # The STATIC membrane doctrine ships ONCE as an instructions file (a
    # byte-stable member of the system prefix -- every provider's prompt cache
    # holds it for the whole session) instead of riding the per-turn RAG tail.
    # The proxy recognizes the stamp line and skips its own per-turn copy
    # (rag._DOCTRINE_STAMP). --no-doctrine-move reverts to per-turn injection.
    # Fork stand-down: the fork's /api/chat stamps its own doctrine; this seat
    # ships NO instructions file when the fork owns the field.
    instr_path = None
    # TRUST-THE-DELIVERY: the doctrine ships once (the instructions file
    # when THIS seat launches opencode on its generated config; the
    # transcript's first injection when the fork owns the field) -- _inject
    # never re-stamps the ~2k preamble per turn. With --no-launch an
    # EXTERNAL opencode runs on its own config: delivery is only real if
    # that config lists the stable file below, so the proxy trusts the
    # stamp check (_has_doctrine_stamp) instead -- no stamp, per-turn
    # injection (the agdroid exhibit: a static opencode.json + the blind
    # trust = a seat whose model never saw the doctrine at all).
    import membrane.rag as _rag
    if getattr(args, "doctrine_move", True) and args.launch:
        _rag._inject.doctrine_delivered = True
    if getattr(args, "doctrine_move", True):
        try:
            # the STABLE path next to the field: an external opencode's
            # config can list it ("instructions": [...]) and it survives
            # seat restarts (never unlinked at shutdown)
            instr_path = str(Path(args.field).resolve().parent
                             / "doctrine.md")
            text = doctrine_instructions_text()
            # the seat card: THIS session's switches, for the model
            # (--no-seat-card keeps the doctrine generic)
            if getattr(args, "seat_card", True):
                text += _seat_card(srv, bool(getattr(args, "mcp", True)),
                                   serve=bool(getattr(args, "serve", False)))
            if naming_on:                  # THE NAME CEREMONY (naming.py): its own name, in its own prompt
                from . import naming as _NM
                text += _NM.card_line() + _NM.feedback_line()
            from . import ha_bridge as _HB                 # Home Assistant around this seat: its
            if _HB.hass_available():                       # child env, or a token HA pushed
                text += _HB.card_line()                    # into ~/.config/membrane/hass.env
            tmp = instr_path + ".tmp"
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, instr_path)      # atomic: a reader never
            cfg["instructions"] = [instr_path]   # sees half a file
            if not args.launch:
                print(f"[opencode] doctrine + seat card: {instr_path} "
                      "(an external opencode picks it up only if its "
                      "config lists it under \"instructions\"; until "
                      "then it rides per turn)", file=sys.stderr)
        except Exception as e:
            instr_path = None
            print(f"[opencode] doctrine-move skipped ({e}); per-turn "
                  "injection stays", file=sys.stderr)
    if model:
        cfg["model"] = f"membrane/{model}"
        cfg["small_model"] = f"membrane/{model}"
    # ---- MCP tool surface (P1) ---------------------------------------------------
    # The structured lane for the canonical frames: a local stdio MCP server
    # (membrane/mcp_server.py) relays typed tool calls to THIS proxy's
    # /membrane/* endpoints -- no prose-frame parsing, no one-turn wait, and
    # the same proxy-side guards. --no-mcp reverts to frames-only.
    # THE TAKEOVER (stage B): opencode's own built-in toolbelt turns OFF
    # here (zero built-ins -- the owner's call); the model's exec lane is
    # run / POST /membrane/run.
    mcp_state = "off (--no-mcp)"
    if getattr(args, "mcp", True):
        # prefer the venv python RUNNING this process over the ambient PATH
        # python (an opencode child must be able to re-import membrane)
        py = sys.executable or "python3"
        cfg["mcp"] = {"membrane": {
            "type": "local",
            "command": [py, "-m", "membrane.mcp_server", "--port", str(listen)],
            "enabled": True,
            "timeout": 60_000,
        }}
        mcp_state = f"on ({len(_mcp_tool_lines())} MCP tools)"
    else:
        cfg["mcp"] = {}
    tools_off = _tools_block()
    if tools_off:
        cfg["tools"] = tools_off
    # ---- LSP servers (built-ins) ----------------------------------------------
    # Diagnostics lane for the agent: pyright/gopls/typescript etc. start
    # automatically when matching files open. --no-lsp reverts to off.
    # pyright gets venvPath/venv pinned to the project .venv (cortex sibling
    # session evidence: unpinned, it resolves the ambient interpreter and
    # flags phantom unresolved-import errors for venv deps); projects without
    # a .venv fall through to pyright's own resolution.
    # NOTE: a per-server entry in opencode's schema REQUIRES `command` (a
    # missing one is a config-validation hard error -- opencode dies at boot).
    lsp_state = "off (--no-lsp)"
    if getattr(args, "lsp", True):
        root = Path(args.field).resolve().parent.parent or Path(".")
        venv = root / ".venv"
        pyright_bin = shutil.which("pyright-langserver")
        venv_ok = venv.is_dir() and (venv / "bin" / "python").exists()
        if venv_ok and pyright_bin:
            cfg["lsp"] = {"pyright": {
                "command": [pyright_bin, "--stdio"],
                "initialization": {"python": {
                    "venvPath": venv.parent.as_posix(),
                    "venv": venv.name,
                }},
            }}
            lsp_state = "on (pyright pinned to project venv, binary found)"
        elif venv_ok and not pyright_bin:
            # the pinned-command entry REQUIRES a real binary; with the
            # executable missing, an explicit command=[not-found, --stdio]
            # makes the server silently dead (observed: 'lsp not connected').
            # Fall back to the built-ins -- opencode ships its own server
            # resolution there, and a working unpinned pyright beats a
            # pinned absent one. The venv pin returns once the binary is.
            cfg["lsp"] = True
            lsp_state = ("on (built-in servers; pyright binary NOT on PATH "
                         "-- venv pin skipped, install pyright to re-enable)")
        else:
            cfg["lsp"] = True
            lsp_state = "on (built-in servers)"
    fd, cfg_path = tempfile.mkstemp(prefix="membrane-opencode-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    # JWT mode: the launched opencode child carries the seat's token in its
    # env only (the config's apiKey reads it; attach speaks it as Basic)
    env = _JWT.child_env(dict(os.environ))
    env["OPENCODE_CONFIG"] = cfg_path

    fld = getattr(srv, "efield", None)
    fld_s = "UNAVAILABLE"
    if fld is not None:
        try:
            v = fld.vitals()
            fld_s = (f"{type(fld.b).__name__}, " +
                     ", ".join(f"{k} {c}" for k, c in sorted(v["entities"].items())))
        except Exception:
            fld_s = type(fld.b).__name__
    serve_addr = None
    legacy_serve = os.environ.get("MEMBRANE_LEGACY_SERVE") == "1"
    # THE SURFACE runs for every launch boot (the TUI attaches to it as a
    # client -- stage 3.5), loopback-only unless --serve exposes it to the
    # room; no auth baked in -- a proxy in front is the operator's lane.
    surf_host = args.serve_host if args.serve else "127.0.0.1"
    if serve_wanted:
        serve_addr = ("localhost" if surf_host in ("127.0.0.1", "localhost")
                      else _lan_ip())
    print(f"membrane opencode  http://127.0.0.1:{listen}/v1  ->  {backend}\n"
          f"  field={args.field}  audit={args.audit}\n"
          f"  chain: opencode -> membrane (entity field + replay A/B) -> Ollama\n"
          f"  default model: {model or '?'}  |  seat model: {srv.seat_model}"
          f"{' (follows opencode selection)' if srv.model_follows_chat else ' (pinned)'}\n"
          f"  embedder: {getattr(mem.embedder, 'model', None) or 'UNRESOLVED -- chat blocked until an embed model is available'}\n"
          f"  entity field: {fld_s} (capture "
          f"{getattr(getattr(srv, 'capturer', None), 'mode', 'off')})\n"
          f"  state/math: {'answered internally from the live field' if srv.state_intercept else 'off'}\n"
          f"  thoughts: {'agent asks answered as marked context' if srv.thought_intercept else 'off'}\n"
          f"  dev: {'on (one-shot frame-discipline warnings)' if srv.dev else 'off (--dev)'}\n"
          f"  peers: {'on' if getattr(srv, 'peers', None) else 'off'}\n"
          f"  user ledger: {srv.user_ledger.count() if srv.user_ledger else 0} verbatim decisions\n"
          f"  watches: {'on (' + str(len(srv._watch_board.watches)) + ' planted, cap ' + str(MAX_WATCHES) + ')' if getattr(srv, '_watch_board', None) is not None else 'off'}\n"
          f"  gpeers: {len(srv.gpeers.livelist()) if getattr(srv, 'gpeers', None) else 0} live\n"
          f"  replay A/B: {'serve ' + format(getattr(srv, 'replay_ab', 0.0), '.0%') + ' of eligible turns' if getattr(srv, 'replay_ab', 0.0) > 0 else 'shadow (pairs logged, never served)'}\n"
          f"  momentum: {'on (working-context EMA: session:now + the momentum axis)' if getattr(srv, 'momentum', False) else 'off (--momentum)'}\n"
          f"  router: {'on (' + str(len(srv.router.records)) + ' candidates' + (', prefer-local' if getattr(srv.router, 'prefer_local', False) else ', cloud-floor') + ')' if getattr(srv, 'router', None) else 'off (--disable-llm-router to keep it off)'}\n"
          f"  idle judges: claims + A/B pairs"
          f" (model {getattr(srv, 'idle_cortex_model', None) or 'unresolved'})\n"
          f"  mcp tools: {mcp_state}\n"
          f"  lsp: {lsp_state}\n"
                    f"  serve: {f'http://{serve_addr}:{serve_port} (native serve)' if serve_wanted else 'off'}\n"
          f"  opencode config: {cfg_path}",
          file=sys.stderr)
    if args.serve:
        _serve_board(serve_port, args.serve_host)
    native_up = False
    _absorb_th = None                  # #436: the boot's absorb thread
    if serve_wanted and not legacy_serve:
        # THE REPLACEMENT (stage 3): no opencode child -- the native
        # server (membrane/server.py) IS the serve surface:
        # opencode-compatible REST/SSE (the companion app's endpoint),
        # the turn loop riding THIS seat's /v1 loopback (the gate,
        # injection, capture, banner and routing all run through the
        # same code path every other client uses), tool calls executed
        # in-process against the MCP tool impls and streamed as
        # message.part.updated events. Works with or without --launch.
        try:
            from .server import (SessionStore, MessageStore,
                                 TurnRunner, SSEHub, loopback_ladder,
                                 mcp_executor, start)
            d = Path(args.field).resolve().parent
            sess_store = SessionStore(str(d / "serve-sessions.json"))
            msg_store = MessageStore(str(d / "serve-msgs.json"))
            _serve_hub = SSEHub()             # the ONE room's event bus
            msg_store.hub = _serve_hub        # the row-before-part covenant
            system = ""
            try:
                if instr_path and os.path.isfile(instr_path):
                    system = open(instr_path).read()
            except Exception:
                system = ""
            _srv_model = (srv.seat_model if not srv.model_follows_chat
                          else model or srv.seat_model or "membrane")
            _ladder_cell = {"runner": None}   # the runner's late binding
            _ladder = loopback_ladder(listen, cell=_ladder_cell)
            runner = TurnRunner(_serve_hub, sess_store, msg_store,
                                _ladder,
                                mcp_executor(listen),
                                system=system, model=_srv_model)
            runner._serve_socket = _serve_socket   # THE PICK'S RESERVATION
            # THE LOOPS' RUNNER (the reactive layer): the loops' turns
            # are real serve turns on their child sessions
            srv._loop_runner = runner
            try:                                   # #570: an older seat's session per wake, collapsed
                _gone = collapse_loop_sessions(runner)
                if _gone:
                    print(f"[opencode] collapsed {_gone} old loop sessions (one per entity now)",
                          file=sys.stderr)
            except Exception as e:
                print(f"[opencode] loop session collapse failed ({e})", file=sys.stderr)
            # THE MINDS' MODELS: the saved picks for the loops + the cortex
            # (+ the think dial; an explicit CLI on/off beats the file, auto defers)
            try:
                from .minds_models import load as _mm_load
                _mm_load(srv, cli_think=getattr(args, "think", None))
            except Exception as e:
                print(f"[seat] minds' models not restored ({e})", file=sys.stderr)
            # THE ACTIVE MODELS: the role entities are planted and reapplied
            try:
                from . import active_models as _AM
                _AM.load(srv)
            except Exception as e:
                print(f"[seat] active models not loaded ({e})", file=sys.stderr)
            if getattr(srv, "_field_pulse", None) is not None:
                srv._field_pulse.start()
            runner.srv = srv               # /agent reads the live minds
            # OUT OF TOKENS (quota.py): a pause kept across the restart is
            # read back and its timer re-armed: what waits goes at its reset / probe time
            try:
                from . import quota as _QUOTA
                for _qp in _QUOTA.snapshot():
                    print(f"[seat] {_qp['model']} is out of tokens until "
                          f"{_QUOTA.hhmm(_qp['resumes_at'])} ({_qp['kind']})", file=sys.stderr)
                _QUOTA.arm_all()
            except Exception as e:
                print(f"[seat] the out-of-tokens timers were not re-armed ({e})", file=sys.stderr)
            runner._audit_mem = srv.membrane   # the lane-refusal audits
            _ladder_cell["runner"] = runner   # the runner's late binding

            start(serve_port or 4096, surf_host, cfg, runner,
                  sess_store, msg_store)
            native_up = True
            if naming_on:
                try:                       # the entity `name`: agreed with the operator, never picked alone
                    from . import naming as _NM
                    _NM.plant(srv)
                except Exception as e:
                    print(f"[seat] name entity not planted ({e})", file=sys.stderr)
            from .restarts import was_reexec as _was_reexec
            _reexec = _was_reexec()        # read + cleared on every boot (#243)
            try:                           # #711: a message that came in during the restart gets its turn
                from .restarts import answer_dropped as _answer_dropped
                _answer_dropped(srv)
            except Exception as e:
                print(f"[seat] the restart's dropped messages not answered ({e})", file=sys.stderr)
            if args.standalone:            # STANDALONE MODE: the UI on the serve port (ui_serve.py)
                from . import ui_serve as _UIS
                _UIS.build_id()            # the page code this boot serves, fixed now (#243)
                _ui_url = _UIS.browser_url(surf_host, serve_port or 4096)
                print(f"[seat] membrane UI: {_ui_url}", file=sys.stderr)
                if (not _reexec and not args.no_browser
                        and sys.stdin.isatty()):   # a restart's tab reconnects (#243)
                    _UIS.open_when_up(serve_port or 4096, _ui_url)
            try:                           # the seat as a panel: state, events, controls
                from . import seat_panel as _SP
                _SP.start(srv)
                from . import score as _SC        # the score: the field's share of the work (an entity)
                _SC.start(srv)
                from . import consequences as _CQ  # what followed its decisions, 1 h / 24 h / 72 h on
                _CQ.start(srv)
                from . import outcomes as _OC      # the shadow gate: guesses your ignores, blind; skips nothing
                threading.Thread(target=_OC.start, args=(srv,), daemon=True, name="outcomes-boot").start()
                try:                               # the field's m: mirror names only what the store holds
                    from .efield import wire_store_names
                    if getattr(srv, "efield", None) is not None and getattr(srv, "entity_store", None) is not None:
                        wire_store_names(srv.efield, srv.entity_store)
                except Exception as e:
                    print(f"[seat] m: name check not wired ({e})", file=sys.stderr)
                from . import llm as _LLM         # the model as an entity: llm.call(about, prompt)
                _LLM.bind(srv)
                _LLM.start_wakes(srv)             # the seat's own deadlines: llm.wake(about, at, prompt)
                try:                               # what waits to be armed (proximity watches, thought/dreamed)
                    from .watch_agents import paint_dormant
                    paint_dormant(srv)
                except Exception as e:
                    print(f"[seat] dormant inventory skipped ({e})", file=sys.stderr)
                from . import restarts as _RS     # restarts: told to the seat; a staged one is his to do
                _RS.bind(srv)
                _RS.on_boot(srv)
                # #690: older events get their whole vector stored once, in the
                # background now that the seat has booted (the vector index's
                # catch-up pace: 500 a batch, the readers' turn between)
                if getattr(srv, "efield", None) is not None:
                    def _wvec_backfill(_f=srv.efield):
                        try:
                            n = _f.backfill_wvec()
                            if n:
                                print(f"[efield] whole vectors stored on {n} older events", file=sys.stderr)
                        except Exception as e:
                            print(f"[efield] whole-vector backfill stopped ({e})", file=sys.stderr)
                    threading.Thread(target=_wvec_backfill, daemon=True, name="wvec-backfill").start()
                _serve_hub.on_lost = lambda: _SP.note(srv, "stream-cut")
                from . import jsexec as _JS      # the seat is a remote_executor: JS in an opted-in tab
                _JS.bind(srv)
                from . import self_loop as _SELF  # #161: self -- born off, the operator's switch wakes it
                _SELF.bind(srv)
                from . import toybox as _TB       # #239: self's toys, the built-in kit (toybox.add/list/...)
                _TB.bind(srv)
                from . import adapters as _AD     # #576 part E: effects are writes (chat, toys, llm, restart)
                _AD.start(srv)
                from . import self_super as _SUP  # #440: its own process (default), or in-process with MEMBRANE_SELF_PROC=0
                _self_host = surf_host if surf_host not in ("0.0.0.0", "::", "") else "127.0.0.1"
                _SUP.boot(srv, f"http://{_self_host}:{serve_port or 4096}",   # the native serve: its /membrane/self/*
                          log_path=d / "self-proc.log")
            except Exception as e:
                print(f"[seat] seat panel not started ({e})", file=sys.stderr)
            print(f"[opencode] native serve up: session store "
                  f"{d / 'serve-sessions.json'}; the proxy's /v1 is "
                  "the turn lane", file=sys.stderr)
            # ---- absorb external-agent sessions (part of the seat's
            # boot breath): anything OpenCode/Claude Code did since the
            # seat's last run lands in the store, and the newest
            # absorbed session wins -c purely by recency. Flagged
            # off --no-absorb / --new-session; lab seats never absorb.
            # #436: ON ITS OWN THREAD -- the scan reads every transcript
            # of the project (1+ GB on a long-lived one); inline it held
            # the main thread before the proxy's serve loop, so :11500
            # sat in LISTEN accepting nothing. A TUI launch waits for it
            # before its child (below), so -c still finds the newest.
            try:
                from .absorb import boot_absorb
                _absorb_th = boot_absorb(
                    sess_store, msg_store, getattr(srv, "efield", None),
                    field_path=str(args.field), new_session=args.new_session,
                    no_absorb=args.no_absorb, native_up=native_up,
                    cwd=os.getcwd(), marker_path=str(d / "serve-absorb.json"),
                    model_id=_srv_model)
            except Exception as _e:
                print(f"[absorb] failed ({_e}) -- seat continues",
                      file=sys.stderr)
        except Exception as e:
            print(f"[opencode] native serve FAILED ({e}) -- seat keeps "
                  "running without the room-facing surface",
                  file=sys.stderr)

    # ---- signal + child lifecycle -------------------------------------------
    # In launch mode opencode owns the terminal: ctrl+c must interrupt opencode
    # (clear the current message), NOT kill membrane. So membrane IGNORES SIGINT
    # and instead shuts itself down when the opencode child exits (polled below)
    # or on SIGTERM. In proxy-only mode there is no child, so SIGINT is the
    # normal "stop the server" signal and is handled as before.
    # THE STOP-SIGNAL COVENANT (2026-10-14, the boot nobody could stop): a
    # serve-bearing seat with no opencode child (standalone, serve, a
    # nohup'd/supervised boot) inherited SIG_IGN for SIGINT from its
    # invoker's context and NEVER restored it -- both the launcher and the
    # serve read ctrl+c or a group kill -INT as "keep serving" (the exhibit:
    # both pids survived a direct SIGINT to their process group). Only a
    # TUI-launch seat has a child whose ctrl+c the seat must not steal.
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    _tui_launch = _stop_signal_policy(args.launch, args.standalone) == "ignore"
    if _tui_launch:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGINT, _term)

    # THE DIAGNOSTIC EAR: RETIRED (2026-10-04, the live seat's POST
    # hang exhibit: a SIGUSR1 handler INSIDE the main thread's
    # accept-poll = the main blocked inside faulthandler's dump while
    # the sockets queue = the seat's whole HTTP lane frozen behind my
    # own diagnostic -- the dumper's cost lands on the accept loop's
    # thread). The evidence lanes: the watchdog's dump-to-file (its
    # own thread, GIL-free) + the per-turn clocks.

    child = None
    try:
        # dev viewer: served at /viewer (world controls hidden) + auto-open once
        # the server is bound. Daemon thread; never fatal on browser failure.
        # --open opens the tab; MEMBRANE_NO_OPEN=1 also silences. Default: NO auto-open.
        if (args.viewer or os.environ.get("MEMBRANE_VIEWER", "").lower() in ("1", "on", "true", "yes")) and args.open_browser:
            def _open_dev_viewer():
                time.sleep(1.0)
                if os.environ.get("MEMBRANE_NO_OPEN"):
                    return
                try:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{listen}/viewer")
                except Exception as e:
                    print(f"[opencode] viewer: browser open skipped ({e})",
                          file=sys.stderr)
            threading.Thread(target=_open_dev_viewer, name="dev-viewer-open",
                             daemon=True).start()
        if args.launch:
            tui_direct = os.environ.get("MEMBRANE_TUI_DIRECT") == "1"
            # #436: a TUI child resumes the newest session (-c), so it
            # waits for the absorb; a launch serves the proxy on the main
            # thread only after the child starts, so nothing waits longer
            # than it did before
            if _absorb_th is not None and not args.standalone:
                _absorb_th.join()
            if args.serve and legacy_serve:
                # MEMBRANE_LEGACY_SERVE=1: the OLD lane -- `opencode serve`
                # as a child (the generated config's provider baseURL -> the
                # proxy; the REST surface is opencode's own). Without the
                # env the native surface (above) IS the serve lane.
                opencode = shutil.which("opencode")
                if opencode is None:
                    print("[opencode] `opencode` not on PATH; no legacy "
                          "serve child (proxy + native surface only)",
                          file=sys.stderr)
                else:
                    cmd = [opencode, "serve", "--hostname", args.serve_host,
                           "--port", str(serve_port or 4096)]
                    print(f"[opencode] launching legacy serve: "
                          f"{' '.join(cmd)}", file=sys.stderr)
                    child = subprocess.Popen(cmd, env=env)
                    try:
                        pid_path = (Path(args.field).parent / "opencode.pid")
                        pid_path.write_text(str(child.pid))
                    except Exception:
                        pass
            elif serve_wanted and not native_up:
                # the native surface failed to bind -- no child to point at
                print("[opencode] serve surface is down; no TUI child "
                      "(proxy only -- point any client at a surface "
                      "manually)", file=sys.stderr)
            elif args.standalone:
                pass                       # standalone: the browser is the client, no TUI
            elif not tui_direct and not legacy_serve:
                # THE CLIENT (stage 3.5): the TUI attaches to membrane's
                # serve server -- sessions/messages in membrane's stores,
                # turns in the TurnRunner, tool parts from the SSE hub.
                # opencode is display only.
                opencode = shutil.which("opencode")
                if opencode is None:
                    print("[opencode] `opencode` not on PATH; serve surface "
                          "up without an attached TUI (point a client at "
                          "it manually)", file=sys.stderr)
                else:
                    cmd = _attach_cmd(opencode, serve_port or 4096,
                                      passthrough, cont=args.cont)
                    print(f"[opencode] launching TUI as a client "
                          f"(attach): {' '.join(cmd)}", file=sys.stderr)
                    child = subprocess.Popen(
                        cmd, env=_attach_env(env),
                        stderr=subprocess.DEVNULL)
                    try:
                        pid_path = (Path(args.field).parent
                                    / "opencode.pid")
                        pid_path.write_text(str(child.pid))
                    except Exception:
                        pass
            else:
                opencode = shutil.which("opencode")
                if opencode is None:
                    print("[opencode] `opencode` not on PATH; proxy-only "
                          "(point opencode at the /v1 URL above)",
                          file=sys.stderr)
                else:
                    extra = []
                    if model and not any(
                            a.startswith("--model") or
                            a.startswith("-m") for a in passthrough):
                        extra = ["--model", f"membrane/{model}"]
                    # resume by default: opencode -c continues the last
                    # session. An explicit -c/-s/--session in passthrough
                    # wins; --no-continue opts out (fresh session).
                    if args.cont and not any(
                            a in ("-c", "--continue") or
                            a in ("-s", "--session") or
                            a.startswith("--session=") for a in
                            passthrough):
                        extra += ["-c"]
                    cmd = [opencode] + extra + list(passthrough)
                    print(f"[opencode] launching: {' '.join(cmd)}",
                          file=sys.stderr)
                    child = subprocess.Popen(cmd, env=env)
                    # THE LIFE-HANDSHAKE: write the child's PID --
                    # proxy-only instances (a second terminal, a manual
                    # relaunch) poll this file and exit when the seat
                    # dies (the operator's rule: the proxy's life is the
                    # seat's life)
                    try:
                        pid_path = (Path(args.field).parent
                                    / "opencode.pid")
                        pid_path.write_text(str(child.pid))
                    except Exception:
                        pass
        if child is None:
            # proxy-only mode: THE PROXY'S LIFE IS THE SEAT'S LIFE (the
            # operator's rule: exits IMMEDIATELY when the opencode seat
            # closes -- no grace period, no idle watchdog). The handshake:
            # launch mode writes its opencode child's PID to
            # <field>.opencode.pid; proxy-only polls that PID every
            # second and exits the moment it's gone (an orphaned proxy
            # serving a stale banner is worse than no proxy).
            srv.timeout = 1.0
            pid_path = (Path(getattr(srv.membrane, "field_path", ""))
                        .parent / "opencode.pid")
            # THE SECOND WATCHDOG -- retired: the SIGUSR1 diagnostic's
            # cost (a faulthandler dump on the accept thread) wedged the
            # seat's whole lane behind it (2026-10-04).
            # --no-launch: THE FOLLOW IS NOT MINE -- a headless boot
            # ignores (and clears) a stale pid file: the operator's
            # rule binds a LAUNCHING proxy to its child, never a
            # headless one to a dead launcher's ghost.
            if not args.launch or args.standalone:   # standalone has no TUI to follow
                try:
                    if pid_path.exists():
                        pid_path.unlink()
                except OSError:
                    pass
            else:
                # a DEAD TUI's pid is a ghost, never followed: the proxy exited at once
                # on a stale file (an HA child, a seat without opencode on PATH)
                try:
                    _old = int(pid_path.read_text().strip()) if pid_path.exists() else 0
                    if _old:
                        os.kill(_old, 0)
                except PermissionError:
                    pass                   # alive (another user's): followed as before
                except (ValueError, ProcessLookupError, OSError):
                    try:
                        pid_path.unlink()
                    except OSError:
                        pass
            watched_pid = None
            try:
                if pid_path.exists():
                    watched_pid = int(pid_path.read_text().strip())
            except Exception:
                watched_pid = None
            # THE THREADED PROXY (the wedge's root, 2026-10-06): the
            # seat served ONE request at a time ON THE MAIN THREAD
            # (handle_request per loop) -- a turn's OWN model call held
            # the proxy for its whole ladder; the turn's tool relays
            # queued BEHIND it (the tool watchdog's 300+ 'STILL PENDING
            # 45s' fires per seat = the lanes' serial queue), the TUI's
            # long-lived SSE event streams held the main thread
            # (everything queued behind an open stream), the watcher
            # scripts' probes waited behind turns. THE PROXY NOW SERVES
            # THREADED (its own daemon thread -- a ThreadingHTTPServer
            # handles each connection on its own thread; a slow turn
            # never blocks a tool relay, an SSE stream, or an embed).
            # The main thread's job: the pid-follow + the lifetime.
            srv._th = threading.Thread(target=srv.serve_forever,
                                       daemon=True,
                                       name="membrane-proxy")
            srv._th.start()
            while True:
                time.sleep(0.5)
                if watched_pid:
                    try:
                        os.kill(watched_pid, 0)
                    except OSError:
                        print("\n[membrane-opencode] the opencode seat "
                              "exited -- proxy following (the operator's "
                              "rule: the proxy's life is the seat's life)",
                              file=sys.stderr)
                        break
        else:
            # launch mode: serve until the opencode child exits (or a signal).
            # serve_forever() blocks, so poll the child on a short timeout loop.
            srv.timeout = 1.0
            while child.poll() is None:
                srv.handle_request()
    except KeyboardInterrupt:
        print("\n[membrane-opencode] bye", file=sys.stderr)
    finally:
        # THE TEARDOWN'S BOUND: the chain gets its every chance, then
        # the lease cuts it (the lease itself: TEARDOWN_LEASE_S above).
        _teardown_halt = threading.Timer(TEARDOWN_LEASE_S, lambda: (
            print(f"\n[opencode] teardown lease ({TEARDOWN_LEASE_S:g}s) "
                  "expired -- cutting the drain", file=sys.__stderr__),
            os._exit(70))[-1])
        _teardown_halt.start()
        # THE PICK'S RESERVATION (teardown): an unused bind closes -- a
        # failing boot never holds the serve port against its own retry
        try:
            if _serve_socket is not None and \
                    runner is not None and \
                    getattr(runner, "_serve_socket", None) is None:
                _serve_socket.close()
                _serve_socket = None
        except Exception:
            pass
        # THE LOCK'S RELEASE (the single-instance covenant's other half):
        # the lock is removed ONLY when it still names THIS pid (a
        # foreign boot's lock survives our exit)
        try:
            if pid_path.exists() and \
                    pid_path.read_text().strip() == str(os.getpid()):
                pid_path.unlink()
        except Exception:
            pass
        # THE AUTONODE'S TEARDOWN: stop the trigger layer before the field
        # flushes below (a half-landed fire then never writes into a
        # closing backend).
        try:
            if getattr(srv, "autonode", None) is not None:
                srv.autonode.close()
        except Exception:
            pass
        # THE SEED PERSISTENCE (the personalized field's enabler):
        # session:now is RAM-only by covenant (never a shared doc), but
        # the seat's OWN seed file is the boot's neighborhood center: the
        # position the PREVIOUS session ended on seeds the NEXT boot's
        # working set (where the operator left off is where they return).
        try:
            est = getattr(srv, "entity_store", None)
            now_ent = (est.docs.get("membrane::session:now")
                       if est is not None else None)
            pos = getattr(now_ent, "position", None)
            if pos:
                seed_path = str(mem.field_path) + ".seed.json"
                import json as _json
                _json.dump({"position": list(pos),
                            "ts": time.time()},
                           open(seed_path, "w"))
                print(f"[opencode] seed persisted "
                      f"({len(pos)}d -> {seed_path})", file=sys.stderr)
        except Exception:
            pass
        if child is not None:
            try:
                child.terminate(); child.wait(timeout=10)
            except Exception:
                pass
        try:                                   # #440: Blurp's own process (SIGTERM, then SIGKILL)
            from . import self_super as _SUP
            _SUP.close_all()
        except Exception:
            pass
        if getattr(srv, "dreamer", None) is not None:
            try:
                srv.dreamer.stop()
            except Exception:
                pass
        if getattr(srv, "thinker", None) is not None:
            try:
                srv.thinker.stop()
            except Exception:
                pass
        # THE FIELD'S LAST FLUSH (the file home, stage 4): the debounced
        # snapshots close their window NOW -- a clean stop loses nothing.
        try:
            _fld = getattr(srv, "efield", None)
            if _fld is not None:
                _fld.flush()
                getattr(getattr(_fld, "b", None), "flush", lambda: None)()
        except Exception:
            pass
        # the couchbase clusters FIRST (peers substrate, the field's store,
        # the entity layer): each close() joins the SDK's asio IO threads so
        # no C-extension thread touches the interpreter during Py_Finalize
        # (a live _core.so thread at finalize is a SIGSEGV).
        for closer in (
                getattr(getattr(srv, "peers", None), "close", None),
                getattr(getattr(srv, "entity_store", None), "close", None)):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                pass
        try:                           # #720: a planned restart keeps the warm matrices
            from .restarts import save_warm
            save_warm(srv)
        except Exception:
            pass
        for p in (cfg_path,):          # the doctrine file is stable: kept
            if not p:
                continue
            try:
                os.unlink(p)
            except OSError:
                pass
        srv.server_close()
        srv._gstop.set()               # the peer heartbeats stop
        from .restarts import restart_line
        if restart_line():
            print(restart_line(), file=sys.stderr, flush=True)
        if _log_fh is not None:
            try:
                _log_fh.close()
            except OSError:
                pass
        try:
            _teardown_halt.cancel()
        except Exception:
            pass
        from .restarts import reexec_if_requested
        reexec_if_requested(srv)        # #151: a self-restart comes back (#395: the person's pause too)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # launch/serve mode owns the terminal's stderr (it is a log file
        # now), so a
        # boot that dies here wrote its only trace into a file nobody is
        # reading -- the silent Errno 48 boot death took an hour to find
        # (2026-10-13). A fatal boot echoes its traceback to the REAL
        # stderr too: the supervisor's log sees the why.
        if sys.stderr is not sys.__stderr__:
            import traceback
            try:
                traceback.print_exc(file=sys.__stderr__)
            except Exception:
                pass
        raise
