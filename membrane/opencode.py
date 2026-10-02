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
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

from .embed import OllamaEmbedder
from .global_field import PEER_ASK_SIM_MIN
from .rag import (RAGProxyHandler, _clip, _inject,
                  _model_chat, doctrine_instructions_text)
from .router import (EXPLORE_LANE, AUTO_CLOUD, AUTO_LOCAL, AUTO_LANES,
                     AUTO_ALIASES, LANE_TOKENS, normalize_lane,
                     Registry)
from .watch import MAX_WATCHES, WatchBoard
from .intent import IntentBoard, MAX_INTENTS

from .seat import Seat

CLOUD_DEFAULT_PRICE = 3.0   # an unknown cloud model's relative money/turn

# name markers meaning "not a chat model" (the picker's screen). CLOUD models
# (e.g. glm-4.7:cloud, deepseek-v4-pro:cloud, gpt-oss:20b-cloud) never match these
# markers, so they always pass.
_NON_CHAT_MARKERS = ("embed", "minilm", "bge", "whisper", "nomic-embed")
# tag markers meaning "an embedding model" (the embed-role candidates)
_EMBED_MARKERS = ("embed", "minilm", "bge", "arctic", "gte")
# the opencode-compatibility screen for the PICKER (same covenant as the
# router's screen in router.py; both sides filter on Ollama's declared caps)
_OC_MIN_CAPS = ("completion", "tools")
_OC_MAX_LOCAL_GIB = 12.0


def _is_non_chat(name: str) -> bool:
    low = (name or "").lower()
    return any(m in low for m in _NON_CHAT_MARKERS)


# ---- the state/math intercept (internal, no model) ---------------------------
# Phrases that only a question ABOUT the membrane's own math/state uses. No bare
# single words ("membrane" appears in nearly every coding turn in this repo).
_STATE_STRONG = ("field state", "field stats", "system state",
                 "state of the system", "state of the field",
                 "free energy", "effective rank", "eff rank", "eff_rank",
                 "rank ratio", "safe frac", "spectrum entropy",
                 "what do you remember", "memory stats", "audit stats",
                 "how many entities", "field size")
_STATE_QSTARTS = ("what", "how", "show", "tell", "give", "print", "report",
                  "dump", "status")
_STATE_EXACT = ("status", "state", "field state", "field stats", "stats",
                "memory", "membrane state", "membrane")
# whole-turn asks that are exactly about the membrane itself. "membrane" merely
# MENTIONED in a longer sentence is never enough -- in this repo nearly every
# turn says "membrane", so a substring rule would hijack normal questions.
_MEMBRANE_DIRECT = ("the membrane", "what is the membrane",
                    "what's the membrane", "whats the membrane",
                    "how is your memory", "how's your memory",
                    "hows your memory")


def _is_state_query(q: str) -> bool:
    """Conservative: the turn asks ABOUT the membrane itself (its math/state,
    not the codebase) -> answered internally from the live field. Guards:
    question-shaped (a '?' or a question/imperative opener) except for bare
    exact asks ("status", "field state"), no code fence, short (a state ask is
    short) -- so ordinary coding prose never trips it."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    bare = low.rstrip("?!").strip()
    if bare in _STATE_EXACT or bare in _MEMBRANE_DIRECT:
        return True
    if not ("?" in low or low.startswith(_STATE_QSTARTS)):
        return False
    return any(m in low for m in _STATE_STRONG)


def _mcp_tool_lines() -> list[str]:
    """One line per MCP tool, straight from the server's registry (the
    card can never drift from what opencode actually mounts)."""
    try:
        from .mcp_server import TOOLS
    except Exception:
        return []
    out = []
    for t in TOOLS:
        d = " ".join(str(t.get("description") or "").split())
        first = re.split(r"(?<=\.)\s", d, maxsplit=1)[0]
        if len(first) > 160:
            first = first[:157].rsplit(" ", 1)[0] + "..."
        out.append(f"  - {t.get('name')}: {first}")
    return out


_ENTITY_CARD_NORMAL = [
    "- ENTITY FIELD: on. Membrane remembers per ENTITY (every file, symbol, "
    "command, port, model, phrase and word), not per exchange. A 'SINCE YOUR "
    "LAST MESSAGE' block rides a turn when entities changed after the last "
    "message you processed: changes from OUTSIDE this conversation (code "
    "edits, entity scripts, other sessions) are quoted; your own turns only "
    "count. No block = nothing changed."]
_ENTITY_CARD_NO_CONTEXT = [
    "- ENTITY FIELD: on. Membrane remembers per ENTITY (every file, symbol, "
    "command, port, model, phrase and word), not per exchange. Each turn "
    "carries 'THE CONVERSATION SO FAR' (the session's messages and one-line "
    "tool facts from the event log) and an 'ENTITIES' block for what the "
    "ask names."]
_ENTITY_CARD_COMMON = [
    "- READING THE BLOCKS: '▸ <id> [kind · said N · observed M · code K · "
    "age]' then the sentences that shaped it, verbatim. SAID = someone "
    "wrote it (the user, you, a note); OBSERVED = a tool result showed it; "
    "CODE = the codebase's own definitions and docstrings. Trust order: "
    "observed and code > the user > model prose. A '! conflict' is a claim "
    "the evidence contradicts -- check before you repeat it.",
    "- '#N' is an EVENT number (a message, a tool fact, a code definition, "
    "a note). 'membrane: forget #N' / membrane_forget remove that event and "
    "recompute the entities it moved; 'membrane: learn that ...' writes a "
    "note event."]


def announce_line(a: dict) -> str:
    """One EntityStore announcement -> one banner line, KIND-AWARE.
    The render used to fall everything into the generic new-entity lane:
    a script-error announcement (kind 'script-error', name = the trigger
    that fired) rendered as 'new entity: <eid> [Component] <error>' --
    the kind and the trigger both dropped. Now: script errors render as
    script lines (the model reads its own broken organs at a glance);
    births keep their lane; merges keep theirs."""
    if a.get("kind") == "merged":
        return f"new entity: {a['eid']} — merged {a.get('extra', '')}"
    if str(a.get("kind") or "").startswith("script"):
        return (f"script: {a['eid']} {a.get('name') or '?'} → "
                f"{(a.get('summary') or '')[:140]}")
    clip = a.get("summary") or a.get("name") or ""
    return f"new entity: {a['eid']} [{a.get('cls') or '?'}] {clip}"


_OC_BUILTIN_TOOLS = ("bash", "edit", "write", "read", "grep", "glob", "lsp",
                     "apply_patch", "skill", "todowrite", "webfetch",
                     "websearch", "question")


def _tools_block() -> dict | None:
    """opencode's built-in toolbelt OFF in the generated config (the
    owner's 2026-10-01 call: membrane takes the surface over -- the
    model's exec goes through the field via membrane_run, its memory
    via the membrane_* MCP tools). None -- the block omitted, built-ins
    kept -- when MEMBRANE_BUILTIN_TOOLS=1 (the ablation)."""
    if os.environ.get("MEMBRANE_BUILTIN_TOOLS") == "1":
        return None
    return {t: False for t in _OC_BUILTIN_TOOLS}


def _attach_cmd(opencode: str, port: int, passthrough=(), cont: bool = False) -> list:
    """The TUI-AS-CLIENT command (stage 3.5): the child does not RUN opencode
    -- it ATTACHES to membrane's serve server (opencode-compatible REST/SSE).
    Sessions/messages live in membrane's stores, turns run in the
    TurnRunner, tool parts stream from the SSE hub; opencode is the
    operator's watchpoint, display only. No model flag is passed: the
    SERVER picks the model (the seat's routing ladder)."""
    cmd = [opencode, "attach", f"http://127.0.0.1:{port}"]
    if cont and not any(a in ("-c", "--continue") or
                        a in ("-s", "--session") or
                        a.startswith("--session=") for a in passthrough):
        cmd.append("-c")
    return cmd + list(passthrough)


def _attach_env(env: dict, su: str, sp: str) -> dict:
    """The attach child's env: auth for membrane's serve server, and NO
    OPENCODE_CONFIG/_CONTENT -- the provider/session config is the SERVER's
    business; an attached TUI must not front its own /v1 lane."""
    e = dict(env)
    e["OPENCODE_SERVER_USERNAME"] = su
    e["OPENCODE_SERVER_PASSWORD"] = sp
    e.pop("OPENCODE_CONFIG", None)
    e.pop("OPENCODE_CONFIG_CONTENT", None)
    return e


def _seat_card(srv, mcp_on: bool, serve: bool = False) -> str:
    """THE SEAT CARD: this session's live configuration, rendered for the
    MODEL -- what membrane does in the current mode and how to drive it.
    Appended to the doctrine instructions file at boot: the switches are
    fixed for the session, so the card is byte-stable (the system prefix
    stays cacheable); live NUMBERS stay in `field state` / the banner.
    The card is authoritative where the generic doctrine is not."""
    L = ["", "MEMBRANE SEAT -- this session's live configuration "
         "(authoritative over the general doctrine above where they differ; "
         "fixed for the session -- `field state` / membrane_state reads the "
         "live numbers):"]
    if serve:
        L.append(
            "- SURFACE: headless serve -- your clients are REMOTE "
            "opencode-compatible apps over the network (the room-facing "
            "REST/SSE surface; an attached opencode client is display "
            "only, never a second surface with its own memory). "
            "Everything else is unchanged: same field, same lanes.")
    if getattr(srv, "no_context", False):
        keep = int(getattr(srv, "tool_keep", 3))
        L.append(
            "- CONTEXT: FIELD-ONLY (--no-context). opencode's transcript "
            "before the current turn is NOT sent to you. In its place: a "
            "reconstruction block (this session's last few exchanges, as "
            "the field remembers them) + recalled memories. The current "
            "turn arrives whole: your opening ask, and your tool loop.")
        if keep >= 0:
            L.append(
                f"- IN-TURN ELISION: only the last {keep} tool exchanges of "
                "this turn arrive verbatim; older big tool results and "
                "arguments show a head + a '[membrane: ... elided ...]' "
                "marker. Re-run the tool when you need one verbatim; never "
                "guess at the elided part.")
        L.append(
            "- MISSING EARLIER CONTEXT is expected here, not an error: if "
            "you need something from earlier in the session that is not in "
            "front of you, query the field for it instead of assuming.")
    else:
        L.append(
            "- CONTEXT: full transcript. opencode's history reaches you as "
            "usual; recalled memories ride as a system block just before "
            "the latest user message.")
    if _efield_on(srv) is not None:
        L.extend(_ENTITY_CARD_NO_CONTEXT if getattr(srv, "no_context", False)
                 else _ENTITY_CARD_NORMAL)
        L.extend(_ENTITY_CARD_COMMON)
    ab = float(getattr(srv, "replay_ab", 0.0) or 0.0)
    L.append(
        f"- REPLAY A/B: replayable asks ({ab:.1%} served as pure replay) are "
        "logged as pairs -- the stored answer next to yours, judged for "
        "agreement; you are never bypassed."
        if ab > 0 else
        "- REPLAY A/B: off (shadow) -- replayable asks are logged as pairs "
        "(stored answer next to yours; an idle judge scores the agreement) "
        "but you answer every turn.")
    L.append(
        "- YOUR WRITES: 'membrane: learn that ...' / 'membrane: forget #N' "
        "frames in your reply EXECUTE (audited as origin: agent)."
        if getattr(srv, "agent_writes", True) else
        "- YOUR WRITES: report-only (--no-agent-writes) -- frames in your "
        "prose are proposals the operator confirms; nothing is written.")
    tools = _mcp_tool_lines() if mcp_on else []
    if _tools_block() is not None:
        L.append(
            "- EXEC: you have NO built-in tools this seat (bash/read/edit/"
            "grep/... are switched off in the config) -- every command, "
            "file read and patch goes through membrane_run (one shell "
            "command -> one `c:<head>` membrane entity it runs THROUGH: "
            "the result lands on the entity, errors reach your next "
            "banner). Entity scripts run UNSANDBOXED: imports allowed, "
            "the engine's budgets are the only bounds."
            + ("" if mcp_on else
               " The MCP layer is OFF too, so you have no exec lane at "
               "all right now -- tell the operator to start the seat with "
               "--mcp."))
    if tools:
        L.append("- TOOLS: the membrane MCP server is mounted -- prefer "
                 "these typed tools over prose frames (no one-turn wait, "
                 "same guards):")
        L.extend(tools)
    else:
        L.append("- TOOLS: MCP off -- drive membrane with the prose frames "
                 "(one per reply, last line).")
    L.append("- COMMONS: a claim two or more seats make becomes a commons claim; "
             "the operator promotes one with 'membrane: remember globally: ...'."
             + (" Peers are live: a sibling seat's entities may ride a turn."
                if getattr(srv, "peers", None) is not None else ""))
    router = getattr(srv, "router", None)
    if router is not None:
        b = getattr(router, "cloud_budget", None)   # None = uncapped
        spend = ("uncapped cloud spend" if b is None else
                 "free-tier only" if b <= 0 else f"cloud budget ${b:.2f}/day")
        L.append(f"- ROUTING: membrane picks the model per turn ({spend})"
                 "; you may be a different model than last turn -- do not "
                 "assume your own earlier reasoning is in your weights.")
    if not getattr(srv, "receipts", True):
        L.append("- RECEIPTS: off (--no-receipts).")
    return "\n".join(L) + "\n"


def _efield_of(srv):
    from .efield import EntityField
    fld = getattr(srv, "efield", None)
    return fld if isinstance(fld, EntityField) else None


def _efield_on(srv):
    """The entity field when the seat's capture runs it (mode 'on')."""
    fld = _efield_of(srv)
    cap = getattr(srv, "capturer", None)
    return fld if fld is not None and getattr(cap, "mode", "off") == "on" else None


def _own_session_prefix(srv) -> str:
    """The session ids this seat's live capture writes ('s:<boot>:')."""
    cap = getattr(srv, "capturer", None)
    boot = getattr(getattr(cap, "sessions", None), "boot", None)
    return f"s:{boot}:" if boot is not None else "s:"


def _efield_answer(srv, q: str, budget: int = 2400) -> dict:
    """The entity field's answer to a question: the entities it names (their
    counters, verbatim evidence, relations, conflicts) and the rendered
    block. {} when the seat has no entity field or it cannot answer."""
    fld = _efield_of(srv)
    if fld is None:
        return {}
    try:
        from .entity_block import entities_for, render
        try:
            qvec = fld.emb.embed([q])[0]
        except Exception:
            qvec = None
        views = entities_for(fld, q, qvec)
        return {"entities": views, "block": render(views, budget)}
    except Exception as e:
        return {"entities": [], "block": None, "efield_error": str(e)[:200]}


def _efield_vitals(srv) -> dict | None:
    fld = _efield_of(srv)
    if fld is None:
        return None
    try:
        v = dict(fld.vitals(max_age_s=60))
        cap = getattr(srv, "capturer", None)
        v["mode"] = getattr(cap, "mode", None)
        try:
            from .efgate import ab_report
            v["replay_ab"] = ab_report(fld)
        except Exception:
            v["replay_ab"] = None
        return v
    except Exception as e:
        return {"error": str(e)[:200]}


def _state_report(srv) -> str:
    """The internal answer for a state/math query: live numbers straight off
    the entity field and the audit. No model call."""
    mem = srv.membrane
    lines = [
        "Membrane state (computed live on this server, no model call):",
        f"- backend {srv.backend} | embed model {getattr(mem.embedder, 'model', None) or 'unresolved'}",
    ]
    ev = _efield_vitals(srv)
    if ev and "error" not in ev:
        by_stream = {}
        for k, c in ev["events"].items():
            by_stream[k.split("/")[0]] = by_stream.get(k.split("/")[0], 0) + c
        mu, sd = ev["null_coherence"]
        lines.append(
            f"- entity field on {type(srv.efield.b).__name__} (origin "
            f"{srv.efield.origin}): events "
            + ", ".join(f"{k} {c}" for k, c in sorted(by_stream.items()))
            + "; entities " + ", ".join(f"{k} {c}" for k, c in sorted(ev["entities"].items()))
            + f"; coherence null {mu:.3f}+-{sd:.3f}")
        g = ev.get("geometry") or {}
        if g.get("eff_rank") is not None:
            lines.append(f"- entity geometry (top {g['entities']} by mass): effective rank "
                         f"{g['eff_rank']:.1f} (ratio {g['rank_ratio']:.3f}); free energy "
                         f"{g['free_energy']:.3f} (conflict share {g['conflict_frac']:.3f})")
        ab = ev.get("replay_ab") or {}
        if ab.get("eligible"):
            lines.append(f"- replay A/B: {ab['eligible']} replayable turns ({ab['served_replay']} "
                         f"served), {ab['paired']} paired, answer sim {ab['mean_answer_sim']}, "
                         f"judge {ab['verdicts'] or 'pending'}")
    if getattr(srv, "no_context", False):
        lines.append("- context: FIELD-ONLY (the stateless LLM -- the "
                     "transcript is stripped per turn; coherence comes "
                     "from the reconstruction + the recall)")
    router = getattr(srv, "router", None)
    if router is not None:
        lines.append(router.summary())
    lines.append(f"- audit: {_audit_count(mem.audit_path)} records")
    fw = _frame_wait_summary(srv)
    if fw:
        lines.append(f"- frame waits: {fw['unique_asks']} unique asks, "
                     f"{fw['served']} served, {fw['re_asks']} re-asks"
                     + (f", {fw['buried']} buried (last-line)"
                        if fw.get("buried") else ""))
        viol = fw.get("violations", 0)
        if viol:
            lines.append(f"- frame discipline: {viol} violation(s) "
                         f"({fw.get('viol_stacked', 0)} stacked, "
                         f"{fw.get('viol_buried', 0)} buried mid-prose)")
    # THE NULL-RECALL COUNTER (the honest metric, same spirit as frame
    # waits): the session's all-orthogonal asks -- where the field had
    # nothing and the banner said so. The last miss (query + sim) rides
    # along while it is still the live edge.
    rn = getattr(srv, "_recall_null", None)
    if rn and rn.get("count"):
        tail = ""
        if rn.get("query"):
            tail = (f" (last: {rn['query']!r} @ "
                    f"{rn.get('sim')})")
        lines.append(f"- recall nulls: {rn['count']} this session{tail}")
    wb = getattr(srv, "_watch_board", None)
    if wb is not None:
        now = time.time()
        live = sum(1 for w in wb.watches.values() if now >= w.dormant_until)
        undel = sum(1 for e in wb.events if not e.get("delivered"))
        lines.append(f"- watches: {live} live (cap {MAX_WATCHES}), "
                     f"{undel} undelivered reports")
    ib = getattr(srv, "_intent_board", None)
    if ib is not None:
        open_ = ib.open_intents()
        # THE ACTIVE SET (the goal-backed board): the banner shows the
        # SELECTED goals -- what the dream loop is steered by, right now.
        if open_:
            names = "; ".join(f"#{i.iid} {i.text[:60]}" for i in open_[:3])
            lines.append(f"- intent: {names}"
                         + (f" (+{len(open_) - 3} more)" if len(open_) > 3
                            else ""))
        else:
            lines.append("- intent: none active (goals rank into the set "
                         "from what you express; `membrane: intend that X` "
                         "pins one)")
    # THE MIND-LANE (the inner speech, visible): the thinker's last few
    # digests -- what it chewed and what it decided. One line, bounded.
    th = getattr(srv, "thinker", None)
    if th is not None and getattr(th, "mind_log", None):
        recent = list(th.mind_log)[-2:]
        thoughts = "; ".join(
            f"{e.get('acted')}: {e.get('about', '')}"
            for e in recent)
        lines.append(f"- mind: {thoughts}")
    cg = getattr(srv, "_codegraph", None)
    if cg is not None:
        from .codegraph import summary as cg_summary
        lines.append(f"- " + cg_summary(cg))
    if getattr(srv, "efield", None) is not None:
        from .claims import COMMONS
        commons = [d for _, d in srv.efield.b.scan("claims", {"origin": COMMONS})]
        live = len(srv.gpeers.livelist()) if getattr(srv, "gpeers", None) else 0
        lines.append(f"- commons: {len(commons)} claims "
                     f"({sum(1 for d in commons if d.get('status') == 'consensus')} consensus, "
                     f"{sum(1 for d in commons if d.get('status') == 'disputed')} disputed); "
                     f"peers live {live}")
    ledger = getattr(srv, "user_ledger", None)
    lines.append(f"- user ledger: {ledger.count() if ledger else 0} verbatim "
                 f"decision(s){' (' + ledger.path + ')' if ledger else ' (unavailable)'}")
    return "\n".join(lines)


# ---- the recall intercept (internal, no model, never learned) ---------------
# Turns that ASK what the field remembers are served straight from the field:
# the render is the past answers nearest the topic plus the entities it
# names. The turn is intercepted BEFORE the gate -> it never learns, never
# demotes, never nudges: the field cannot eat its own readback (the collapse
# route). The skills
# shipped in membrane/skills/ (installed into .opencode/skills/) teach the
# agent the canonical phrasings; to the agent
# they look like ordinary skills, but the SKILL.md bodies say they are served
# by membrane itself.
_RECALL_CANON = ("membrane: query the field for ",
                 "membrane: search the field for ",
                 "membrane: what do you remember",
                 "membrane: dump your memory")
_RECALL_FRAMES = ("what do you remember about ", "what do you remember of ",
                  "what have you learned about ", "what have we learned about ",
                  "what did you learn about ", "what do you recall about ",
                  "search your memory for ", "search memory for ",
                  "search the field for ", "query the field for ",
                  "query your memory for ", "retrieve memories about ",
                  "retrieve memories of ", "what do you know about ")
_RECALL_EXACT = ("what do you remember", "what do you recall",
                 "what have you learned", "your memory", "memories",
                 "search your memory", "query the field", "search the field",
                 "dump your memory")
_RECALL_DUMP = ("what do you remember", "what do you recall",
                "dump your memory", "show your memory", "list your memory",
                "what's in your memory", "whats in your memory",
                "what is in your memory", "what have you learned")


def _recall_topic(q: str) -> str:
    """Strip the interrogative frame -> the topic text to embed.
    Returns "" when the ask is a plain dump (show everything recent)."""
    t = " ".join((q or "").split()).lower()
    for c in _RECALL_CANON:                    # canonical skill phrasings first
        if c.endswith("for ") and t.startswith(c):
            return t[len(c):].strip().rstrip("?.! ").strip()
    if t.startswith("membrane: "):
        t = t[len("membrane: "):].strip()
    t = t.rstrip("?.!").strip()
    if t in _RECALL_DUMP or t in _RECALL_EXACT:
        return ""
    for f in _RECALL_FRAMES:
        if t.startswith(f):
            t = t[len(f):].strip()
            for pre in ("about ", "of ", "for ", "on "):
                if t.startswith(pre):
                    t = t[len(pre):].strip()
            return t.rstrip("?.!").strip()
    return t


# ---- THE RECEIPT LANE (E9-validated, experiments/e9_receipt_lane.py):
# the tool-result turn class whose ack is byte-deterministic. 945/945
# byte-exact in the audit replay (edit-ack 917, edit-fail 28); the
# bash-call-echo class (1,344 turns) was REFUSED -- the call body varies
# per turn, not deterministic. Response-conditioned: the class is chosen
# from what the ack TEXT looks like (which the tool result fully
# determines), never from memory.

_EDIT_ACK = "Edit applied successfully."
_EDIT_FAIL = ("Could not find oldString in the file. It must match "
              "exactly, including whitespace, indentation, and line "
              "endings.")
_EDIT_ACK_RE = re.compile(r"^Edit applied successfully\.$")
_EDIT_FAIL_RE = re.compile(
    r"^Could not find oldString in the file\.")


def _receipt_class(tool_rec: dict | None) -> str | None:
    """The deterministic receipt class for a captured tool call + result,
    or None (fall through to the model -- the degrade covenant). The ack
    replays from the TOOL RESULT side (the captured record from
    _capture_tool_results): membrane confirms what the environment already
    said -- the stale-replay guard, structurally. No record, no result, or
    a non-deterministic result => None (never anticipate)."""
    if not tool_rec:
        return None
    name = str(tool_rec.get("name") or "")
    if name != "edit":
        return None                   # only edit acks are byte-deterministic
    result = (tool_rec.get("result") or "").strip()
    # the result-side matchers cover BOTH live opencode result shapes
    # ("The file X was edited successfully" / oldString-miss) and the
    # audit's model phrasings (E9's corpus) -- the lane serves the
    # canonical ack string either way
    if re.search(r"was edited successfully", result) \
            or _EDIT_ACK_RE.match(result):
        return "edit-ack"
    if "Could not find oldString" in result or _EDIT_FAIL_RE.match(result):
        return "edit-fail"
    return None                       # an edit result we cannot format
    # the ack is determined by the TOOL RESULT the previous capture
    # recorded -- membrane looks it up, never anticipates it
    return None


def _is_recall_query(q: str) -> bool:
    """Conservative: the turn asks the FIELD to recall something. Framed asks
    (incl. the canonical skill forms) match on the prefix; bare asks match
    exactly. Same length/fence guards as _is_state_query."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    if any(low.startswith(c) for c in _RECALL_CANON):
        return True
    if any(low.startswith(f) for f in _RECALL_FRAMES):
        return True
    bare = low.rstrip("?!").strip()
    return bare in _RECALL_DUMP


# ---- write/forget intercepts (operator-given memory, pre-gate) ---------------
# The same paradox as recall: these turns are intercepted BEFORE the gate, so
# the field never sees them -- no learn, no dedup, no nudge on the COMMAND
# itself. What is learned is the PAYLOAD (learn), and what is removed is from
# the store directly (forget) -- in both cases with an explicit audit trail so
# the training data records the operator's hand, not a model decision.

_LEARN_CANON = "membrane: learn that "
_LEARN_FRAMES = ("remember that ", "remember: ", "note that ",
                 "memorize that ", "memorise that ")
_FORGET_CANON = "membrane: forget "
_FORGET_FRAMES = ("forget that ", "forget about ", "forget everything about ",
                  "delete the memory of ", "delete your memory of ")
_FORGET_ALL = ("forget everything", "wipe your memory", "erase your memory",
               "delete all memories", "clear your memory")
_FORGET_WINDOW_HINTS = ("last ", "this session", "the session",
                        "that conversation", "this conversation")
_LEARN_TAG = "operator-note"
# soft frames: what a SEMANTICALLY-caught learn/forget usually reads like (the
# payload rides after the intent phrase). Used only for payload extraction --
# detection stays with the lexical matchers + the semantic gate.
_LEARN_SOFT_FRAMES = ("save this to your memory: ", "keep this in mind: ",
                      "note for later: ", "store a note that ",
                      "keep in mind that ", "you should remember that ")
_FORGET_SOFT_FRAMES = ("forget everything about ", "wipe that from your memory: ",
                       "wipe from your memory ", "erase what you learned about ",
                       "delete the memory of ")
_FORGET_LIMIT = 40          # max candidates a single forget may drop
_AGENT_FORGET_LIMIT = 12    # max cells an INDEX-FORM agent forget may drop

# ---- the global-field layer (slice 1): operator promote/forget of the ------
# commons (docs/global-field.md). Same covenant as learn/forget: the operator's
# hand writes the commons, pre-gate, with an explicit audit record; the model
# cannot promote (a model-echoed promote frame is report-only).

_PROMOTE_CANON = "membrane: remember globally: "
_PROMOTE_ALT = "membrane: promote globally: "
_PROMOTE_REF = "membrane: promote #"          # promote event #N
_FORGET_GLOBAL_CANON = "membrane: forget globally "


def _is_promote_query(low: str) -> bool:
    return (low.startswith(_PROMOTE_CANON) or low.startswith(_PROMOTE_ALT)
            or low.startswith(_PROMOTE_REF))


def _is_forget_global_query(low: str) -> bool:
    return low.startswith(_FORGET_GLOBAL_CANON)


def _print_intercept(kind: str, q: str) -> None:
    print(f"[opencode] intercept ({kind}): {q[:60]!r}", file=sys.stderr)


def _commons_write(fld, text: str, origin: str, source: str) -> tuple[str, bool]:
    """One curated commons claim (the operator's hand); (key, new?)."""
    from . import claims as CL
    from .eids import is_specific, mentions_of
    key = CL.commons_key(text)
    cur = fld.b.get("claims", key)
    origins = sorted(set((cur or {}).get("origins") or []) | {origin})
    subj = [e for e, _ in mentions_of(text, fld.graph_fn()) if is_specific(e)]
    fld.b.upsert("claims", key, {
        **(cur or {}), "_k": "commons", "origin": CL.COMMONS, "text": text[:600],
        "origins": origins, "n": int((cur or {}).get("n") or 0) + 1,
        "statuses": sorted(set((cur or {}).get("statuses") or []) | {"curated"}),
        "status": (cur or {}).get("status") or "curated",
        "subjects": sorted(set((cur or {}).get("subjects") or []) | set(subj)),
        "source": source})
    return key, cur is None


def _promote_global(srv, q: str, vec) -> str:
    """'membrane: remember globally: X' -> X becomes a curated commons claim;
    'membrane: promote #N' -> event #N's text does."""
    fld = _efield_of(srv)
    if fld is None:
        return "[membrane-intercept] the entity field is off; nothing promoted."
    mem = srv.membrane
    low = " ".join(q.split()).lower()
    if low.startswith(_PROMOTE_REF):
        m = re.search(r"#(\d+)", low)
        hit = fld.event_by_number(int(m.group(1))) if m else None
        if hit is None or not (hit[1].get("text") or "").strip():
            return ("Membrane promote: give an event number with text, e.g. "
                    "`membrane: promote #12`.\n\n" + _INTERCEPT_MARKER)
        text, src = " ".join(hit[1]["text"].split()), f"event #{m.group(1)}"
    else:
        text = " ".join((_after_frame(q, (_PROMOTE_CANON,)) or
                         _after_frame(q, (_PROMOTE_ALT,)) or "").split())
        src = "operator"
        if not text:
            return ("Membrane promote: nothing to promote -- give the fact, e.g. "
                    "`membrane: remember globally: user runs macOS+zsh`.\n\n"
                    + _INTERCEPT_MARKER)
    key, new = _commons_write(fld, text, fld.origin, src)
    _write_audit(mem, {"event": "promote-global", "origin": "operator", "key": key,
                       "source": src, "text": _clip(text, 200)})
    return (f"Membrane promote (operator hand -> commons): {'written' if new else 're-credited'} "
            f"as {key}.\n    {_clip(text, 300)}\n\n" + _INTERCEPT_MARKER)


def _forget_global(srv, q: str) -> str:
    """'membrane: forget globally <text | everything>' -> delete matching
    commons claims (content-word overlap >= 0.6, or all)."""
    from . import claims as CL
    from .streams import content_words
    fld = _efield_of(srv)
    if fld is None:
        return "[membrane-intercept] the entity field is off; nothing forgotten."
    spec = q[len(_FORGET_GLOBAL_CANON):].strip().rstrip("?.!")
    rows = list(fld.b.scan("claims", {"origin": CL.COMMONS}))
    if not spec or spec.lower().startswith("everything"):
        targets = rows
    else:
        want = set(content_words(spec))
        targets = [(k, d) for k, d in rows if want and len(
            want & set(content_words(d.get("text") or ""))) >= 0.6 * len(want)]
    for k, _ in targets:
        fld.b.delete("claims", k)
    _write_audit(srv.membrane, {"event": "forget-global", "removed": len(targets),
                                "spec": _clip(spec, 200)})
    if not targets:
        return ("Membrane forget (global): nothing in the commons matches; "
                "nothing removed.\n\n" + _INTERCEPT_MARKER)
    lines = [f"Membrane forget (global, operator path): removed {len(targets)} "
             f"commons claim(s)."]
    lines += [f"- {_clip(d.get('text', ''), 160)}" for _, d in targets[:8]]
    return "\n".join(lines) + "\n\n" + _INTERCEPT_MARKER

# appended to every write/forget answer so the client can SEE the turn was
# intercepted pre-gate (never learned via the chat path, never demoted)
_INTERCEPT_MARKER = ("[membrane-intercept] operator write; handled internally, "
                     "the command itself was NOT learned. To force the model "
                     "path, repeat prefixed with 'model:'")


def _write_audit(mem, rec: dict) -> None:
    """One raw JSONL record on the audit log -- the operator's hand, outside the
    decision/outcome/contest schema. Never raises into the chat path."""
    try:
        with open(mem.audit_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[opencode] operator-audit write failed: {_one_line(e)}",
              file=sys.stderr)


def _install_judges(srv) -> None:
    """The idle local model judges state claims and A/B pairs -- only while
    no chat request came in for a minute, bounded per tick and per hour."""
    from .claims import JudgeQueue, OllamaClaimJudge
    from .efgate import ab_judge_pending
    model = getattr(srv, "idle_cortex_model", None)
    if not model:
        return
    base = str(getattr(srv, "backend", "") or "http://localhost:11434").rstrip("/")
    judge = OllamaClaimJudge(model, base_url=base)

    def idle() -> bool:
        return time.time() - float(getattr(srv, "_last_request_ts", 0) or 0) > 60

    jq = JudgeQueue(srv.efield, judge, idle=idle)
    srv.capturer.idle_hooks.append(jq.tick)
    srv.capturer.idle_hooks.append(
        lambda: ab_judge_pending(srv.efield, judge.ask) if idle() else 0)


def _swallow(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(f"[efield] {getattr(fn, '__name__', 'task')} error: {e}", file=sys.stderr)


def _tool_round(body: dict) -> bool:
    """A continuation of the model's own tool loop, not a new user turn: the
    last message is a tool result -- OpenAI's role 'tool' (or an assistant
    message carrying tool_calls), or Anthropic-style tool_result blocks in
    the last user message."""
    msgs = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
    if not msgs:
        return False
    last = msgs[-1]
    if last.get("role") == "tool" or (last.get("role") == "assistant" and last.get("tool_calls")):
        return True
    content = last.get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


class _EventView:
    """An applied event, shaped like the exemplar the watch and intent
    boards read (vec, situation, action, extra['entities'])."""

    def __init__(self, ev: dict, vec):
        self.vec = [float(x) for x in vec]
        self.situation = ev.get("text") or ev.get("served") or ""
        self.action = ""
        from .eids import _LEGACY_KIND
        legacy = {v: k for k, v in _LEGACY_KIND.items()}   # watch filters: 'files:x'
        ents: dict = {}
        for eid, _i, _s in ev.get("mentions") or []:
            kind, _, val = eid.partition(":")
            if kind not in ("w", "p"):
                ents.setdefault(legacy.get(kind, kind), []).append(val)
        self.extra = {"entities": ents}


def _efield_watch_listener(srv):
    """The boards on the entity field: every applied live event (discourse or
    observation; not code, not history) is an arrival; '#N' is its number."""
    def on_applied(key, ev, vec):
        if ev.get("stream") not in ("discourse", "observation"):
            return
        if not str(ev.get("session") or "").startswith(_own_session_prefix(srv)):
            return
        view = _EventView(ev, vec)
        board = getattr(srv, "_watch_board", None)
        if board is not None:
            hit = board.on_learn(view, int(ev.get("n") or 0))
            if hit is not None:
                print(f"[watch] #{ev.get('n')} inside watch {hit['label']!r} "
                      f"(sim {hit['sim']})", file=sys.stderr)
        ib = getattr(srv, "_intent_board", None)
        if ib is not None:
            ib.on_turn(view)
    return on_applied


def _after_frame(q: str, frames: tuple[str, ...]) -> str:
    t = " ".join(q.split()).lower()
    for f in sorted(frames, key=len, reverse=True):
        if t.startswith(f):
            return t[len(f):].strip().rstrip("?.!").strip()
    return ""


def _is_forget_query(q: str) -> bool:
    """A turn commanded at the FIELD to delete memories (canonical form first).
    Same length/fence guards as the recall matcher. A canonical turn whose
    remainder parses as a time window ('the last hour', 'this session') is a
    forget too."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    if low.startswith(_FORGET_CANON):
        return True
    t = low.rstrip("?!").strip()
    if any(t.startswith(f) for f in _FORGET_FRAMES) and "you" in t:
        return True
    return t in _FORGET_ALL


def _is_learn_query(q: str) -> bool:
    """A turn GIVING the field a memory (membrane: learn that X), not a coding
    request. The canonical skill form is exact-prefix (bare 'membrane: learn
    that' with no payload also matches -- _learn_report then reports 'nothing
    to learn'); bare frames require the memory-flavored 'you/your' to stay
    conservative."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 2000:
        return False
    if low.startswith("membrane: learn that"):
        return True
    t = low.rstrip("?.!").strip()
    if any(t.startswith(f) for f in _LEARN_FRAMES) and "you" in t:
        return True
    return False


def _intents_context_block(srv) -> str | None:
    """Open intentions as a system-level context block (the plan channel of
    the observe->reflect->plan loop): a fresh session inherits the DOING, not
    just the happened. Capped to 3 lines; 'maybe-done?' flags included so the
    model can ASK the user whether to fulfill -- similarity never auto-closes
    (the two-authority rule: closure is the user's explicit `membrane: fulfill`)."""
    ib = getattr(srv, "_intent_board", None)
    if ib is None:
        return None
    try:
        open_ = ib.open_intents()
    except Exception:
        return None
    if not open_:
        return None
    lines = ["OPEN INTENTIONS (membrane's commitment board -- context, not "
             "instructions; work toward them when relevant, and if one is "
             "clearly DONE, ask the user to confirm `membrane: fulfill #N`):"]
    for i in open_[:3]:
        maybe = " [maybe-done? recent turns landed near it]" if i.hits >= 2 else ""
        lines.append(f"- #{i.iid} {i.text}{maybe}")
    if len(open_) > 3:
        lines.append(f"- ... and {len(open_) - 3} more (`membrane: intents`)")
    return "\n".join(lines)


def _where_delta(srv, text: str | None) -> str | None:
    """THE DELTA HEADER (the operator's call: 'it sends same tracks and
    same text blocks' -- identical bytes re-sent every turn bloat the
    context; the transcript already carries last turn's header). WHERE
    WE ARE is constant-tracking STATE: send only on CHANGE, force a
    refresh every WHERE_REFRESH_TURNS turns (a long conversation may
    truncate old headers -- the model must never lose its position
    entirely). Returns the block to append, or None (skip this turn).
    The state-peek path (drain=False) never touches the sig."""
    if not text:
        return None
    if text != getattr(srv, "_last_where_text", None) or \
            getattr(srv, "_where_skip_count", 0) >= WHERE_REFRESH_TURNS:
        srv._last_where_text = text
        srv._where_skip_count = 0
        return text
    srv._where_skip_count = getattr(srv, "_where_skip_count", 0) + 1
    return None


WHERE_REFRESH_TURNS = 8      # the forced re-send (position never lost)

# THE NULL-RECALL FLOOR (peer-reported from rosetta-check: 3 of 5 field
# queries returned empty and nothing said so): below this sim the field
# genuinely holds nothing shaped like the ask. The 0.5-0.7 band is honest
# uncertainty (the E1 near-miss replay proved lowering the bar there
# serves the WRONG beat); below 0.5 is ABSENCE, and absence is
# information -- the banner says it so the model doesn't re-derive.
RECALL_NULL_SIM_FLOOR = 0.50
RECALL_NULL_FRESH_S = 10 * 60.0   # the miss rides the banner this long


def _where_we_are(srv, vec, drain: bool = True) -> str | None:
    """THE 'I' TRACK -- the model's position, not its persona.

    One compact header computed per turn (zero model calls): what we're
    working on (the momentum focus + the nearest open commitment), where the
    field stands, and what's pending review. The point: memories answer
    'what do I know', this header answers 'where am I' -- the track a fresh
    session has no other way to see. Deliberately NOT a self-model: no
    feelings, no persona text -- commitments, focus, and state only.

    ``drain``: only the CHAT path drains the entity-track ALERT queue
    (exactly-once delivery to a real model turn). The /membrane/state
    reader passes drain=False -- a PEEK: it shows pending counts without
    consuming them (the state-instrument read must never eat the model's
    alerts -- the curl-drained-the-ALERT bug)."""
    lines: list[str] = []
    # THE MINIMAL HEADER (the operator's call: 'only tracks and
    # notifications'): the focus/commitments/field-stats lines moved to
    # /membrane/state (the operator's instrument) -- the MODEL's header
    # carries only what steers the work: the track stories + the event
    # lines (ALERTs). Identical bytes were re-sent every turn and the
    # field-stats line leaked the stale-shard count; tracks carry their
    # own subjects now (the now-report names the subject).
    # 1. EVENTS FIRST: boundary ALERTS + entity ANNOUNCEMENTS (exactly-once
    #    drains -- the chat path is the only consumer; a state peek passes
    #    drain=False and shows nothing, consuming nothing). The unified
    #    banner's event lane.
    est = getattr(srv, "entity_store", None)
    if est is not None and getattr(est, "tracks", None):
        try:
            # 1. EVENTS: boundary ALERTS + entity ANNOUNCEMENTS (exactly-
            #    once drains -- the chat path is the only consumer; a
            #    state peek passes drain=False and consumes nothing)
            # 2. TRACKS: the printf stories, ID-prefixed; the fallback
            #    carries the ID + the honest empty state.
            fld = _efield_on(srv)
            if fld is None:
                pass                 # the field is off: no tracks, honest silence
            else:
                from .entity_block import track_count
                est.evaluate_tracks(count_fn=lambda k: track_count(fld, k))
                if drain:
                    for a in est.take_alerts():
                        lines.append(f"ALERT: {a['label']} {a['kind']} its "
                                     f"boundary ({a['boundary']}; {a['detail']})")
                        # THE SPEECH LANE (the baby's voice): alerts speak in
                        # the room -- the same event, two deliveries (the
                        # banner whisper + the chat message)
                        speak(srv, f"ALERT: {a['label']} {a['kind']} its "
                                   f"boundary ({a['boundary']})", kind="alert")
                    for a in est.take_announcements():
                        ln = announce_line(a)
                        lines.append(ln)
                        speak(srv, ln, kind="note")
                from .entity_block import now_line, track_line
                for t in est.list_tracks():
                    if t["key"] == "session:now":
                        ln = now_line(fld, getattr(srv, "_efield_boot_seq", 0))
                        if ln:
                            lines.append(ln)
                        continue
                    lines.append(track_line(fld, t["key"], t.get("label") or ""))
        except Exception as e:
            print(f"[opencode] banner render error: {e}", file=sys.stderr)
    # 4. THE NULL-RECALL NOTICE (absence is information): a fresh all-
    #    orthogonal ask rides the header so the model knows the field has
    #    nothing on this direction -- proceed on your own, don't re-derive
    #    from git archaeology. Fresh-only (the miss retires after the window
    #    or when a real hit lands); scoped wording because the fork's
    #    shared-bucket recall may still find what this local field lacks.
    rn = getattr(srv, "_recall_null", None) or {}
    if rn.get("query") and (time.time() - float(rn.get("ts", 0.0))
                            ) < RECALL_NULL_FRESH_S:
        lines.append(
            f"field: no SAFE memory near {rn['query']!r} "
            f"(top sim {rn.get('sim')}) — this field holds nothing on "
            f"that; absence is information ({rn.get('count', 1)} null "
            f"recall(s) this session)")
    # 6. THE OPEN TIMELINES (the dreamer's render): quarantined + confirmed
    #    branches under the true timeline, as tracks -- the model sees what
    #    futures are open from the present (suggestions, never lived).
    est = getattr(srv, "entity_store", None)
    if est is not None:
        try:
            tls = est.open_timelines(limit=3)
            if tls:
                for ent in tls:
                    props = ent.properties or {}
                    status = str(props.get("status") or "?")
                    term = (props.get("terminal") or "")[:70]
                    sim = props.get("branch_sim")
                    sim_s = (f"{float(sim):.2f}" if isinstance(sim, (int, float))
                             else "?")
                    lines.append(f"timeline ({status}, branch_sim {sim_s}): "
                                 f"{term}")
        except Exception as e:
            print(f"[opencode] timeline render error: {e}", file=sys.stderr)
    # 5. THE PEER SUBSTRATES (the operator's multi-seat design): the
    #    REGISTRY heartbeat + the seats line (who else is connected) +
    #    unread PEER-CHANNEL messages (cross-seat chatter, read-once per
    #    seat, visible to the operator on every connected opencode).
    peers = getattr(srv, "peers", None)
    if peers is not None:
        try:
            from .peers import CHANNEL_TEXT_CAP
            peers.heartbeat()
            seats = peers.list_seats()
            others = [s for s in seats
                      if str(s.get("port") or "") != str(peers.port)]
            if others:
                who = ", ".join(
                    f"{str(s.get('cwd', '?')).rsplit('/', 1)[-1]}"
                    f"(:{s.get('port')})" for s in others[:3])
                lines.append(f"seats: {who} also connected")
            for m in peers.fetch_unread():
                frm = str(m.get("from", "?")).replace("proxy-", "")
                text = str(m.get("text", ""))[:CHANNEL_TEXT_CAP]
                lines.append(f"peer[{frm}]: {text}")
        except Exception:
            pass
    if not lines:
        return None
    return ("WHERE WE ARE:\n- " + "\n- ".join(lines))


# ---- the watch layer (spatial triggers, docs: membrane/watch.py) -----------
# The agent plants WATCHES -- (probe, radius) over the field's embedding space;
# every NEW memory landing inside one is reported to the agent as a marked
# [membrane-watch] block on its next model turn. Operator-given like every
# other write: the canonical turns are handled pre-gate; a thought-echo of
# these frames is report-only. Black-hole caps live in watch.py (count cap,
# gravity collapse, dormancy); the board persists beside the field.

_WATCH_CANON = "membrane: watch "
_WATCHES_CANON = "membrane: watches"
_WATCH_DROP = "membrane: drop watch "
_WATCH_THOUGHT_HEAD = "[membrane-watch] your ask was read but NOT executed"


def _watches_board(srv) -> WatchBoard | None:
    b = getattr(srv, "_watch_board", None)
    if b is None:
        try:
            fdir = Path(srv.membrane.field_path).parent
            b = srv._watch_board = WatchBoard.load(
                fdir / "watches.json", embedder=srv.membrane.embedder)
        except Exception as e:
            print(f"[opencode] watch board unavailable: {_one_line(e)}",
                  file=sys.stderr)
            b = srv._watch_board = None
    if b is not None and b.embedder is None and srv.membrane.embedder is not None:
        b.embedder = srv.membrane.embedder
    return b


def _is_watch_query(q: str) -> bool:
    """Operator watch commands (set/list/drop), pre-gate like every write."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    return (low.startswith(_WATCH_CANON) or low.startswith(_WATCHES_CANON)
            or low.startswith(_WATCH_DROP)
            or low.rstrip("?!").strip() == "membrane: watch")


# ---- the intention lane (membrane/intent.py): OPEN COMMITMENTS -- the third
# channel of the observe->reflect->plan loop. `membrane: intend that X` plants
# (with an optional '... for 2w' TTL; expiry keeps zombie goals out of recall),
# `membrane: intents` lists, `membrane: fulfill #N` is reality's signature --
# EXPLICIT closure, never similarity-inferred (cos >= CONFIRM_SIM only flags
# 'maybe-done?' in the listing). Dreams are steered toward open intents (the
# dream_loop D5 gate); recall surfaces them so a fresh session inherits the
# DOING, not just the happened. Operator path here; the thought path mirrors
# the watch lane's (a capped board, so an agent cannot flood commitments).

_INTENT_CANON = "membrane: intend that "
_INTENTS_CANON = "membrane: intents"
_INTENT_FULFILL = "membrane: fulfill "
_INTENT_DROP = "membrane: drop intent "
_INTENT_THOUGHT_HEAD = "[membrane-intercept] your ask was read but NOT executed"


def _intents_board(srv) -> IntentBoard | None:
    b = getattr(srv, "_intent_board", None)
    if b is None:
        try:
            fdir = Path(srv.membrane.field_path).parent
            b = srv._intent_board = IntentBoard.load(
                fdir / "intents.json", embedder=srv.membrane.embedder)
        except Exception as e:
            print(f"[opencode] intent board unavailable: {_one_line(e)}",
                  file=sys.stderr)
            b = srv._intent_board = None
    if b is not None and b.embedder is None and srv.membrane.embedder is not None:
        b.embedder = srv.membrane.embedder
    # THE GOAL-BACKED BOARD (the operator's design: 'the fact that a goal
    # became active makes it an intent'): attach the entity store -- the
    # board's steering becomes the seat's ACTIVE SET over the shared Goal
    # entities (top-K by urgency, hysteresis, no TTL; fulfillment shared,
    # suppression seat-local). The JSON intents fall back to the bare
    # library shape when no entity layer exists.
    if b is not None and b.selection is None:
        est = getattr(srv, "entity_store", None)
        if est is not None:
            try:
                b.attach_entity_store(est)
            except Exception as e:
                print(f"[opencode] goal-backed intents unavailable "
                      f"({_one_line(e)}); json board carries",
                      file=sys.stderr)
    return b


def _is_intent_query(q: str) -> bool:
    """Operator intention commands (plant/list/fulfill/withdraw), pre-gate."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    return (low.startswith(_INTENT_CANON) or low.startswith(_INTENTS_CANON)
            or low.startswith(_INTENT_FULFILL) or low.startswith(_INTENT_DROP)
            or low.rstrip("?!").strip() == "membrane: intents")


def _resolve_goal(est, spec: str):
    """Resolve an open Goal by eid fragment or text substring (the
    fulfill/drop spec over the goal layer)."""
    if est is None:
        return None
    from .entities import CLASS_GOAL, MEMBRANE_NS
    spec = " ".join((spec or "").split()).lower().lstrip("#")
    if not spec:
        return None
    for e in est.docs.values():
        if e.cls != CLASS_GOAL:
            continue
        if (e.properties or {}).get("status", "open") != "open":
            continue
        eid_frag = e.eid.replace(MEMBRANE_NS, "").lower()
        name = " ".join(str((e.properties or {}).get("name") or "")
                        .split()).lower()
        if spec in eid_frag or (len(spec) >= 3 and spec in name):
            return e
    return None


def _membrane_intent_create_or_pin(srv, text: str, ttl: str = ""
                                   ) -> dict:
    """THE INTENT'S CREATE-OR-PIN (the operator's design: 'the fact that a
    goal became active makes it an intent'): match an open Goal by text ->
    pin it on this seat's active set; no match -> create the Goal entity
    (SHARED: goals are knowledge, not speech) + pin. The pin is a priority
    bonus on the urgency ranking (the operator's hand beats the score) --
    never a TTL (goals do not expire). Speech announces the activation.
    Returns {eid, created, pinned}."""
    est = getattr(srv, "entity_store", None)
    board = getattr(srv, "_intent_board", None)
    if est is None or board is None or board.selection is None:
        return {"ok": False, "error": "no goal layer"}
    from .entities import (QuantumEntity, CLASS_GOAL, MEMBRANE_NS,
                           TimelineOps)
    low = " ".join((text or "").split()).lower()
    goal = None
    for e in est.docs.values():
        if e.cls != CLASS_GOAL:
            continue
        props = e.properties or {}
        if (props.get("status") or "open") != "open":
            continue
        name = " ".join(str(props.get("name") or "").split()).lower()
        summary = " ".join(str(props.get("summary") or "").split()).lower()
        if low and (low == name or low in summary or name in low):
            goal = e
            break
    created = False
    if goal is None:
        # the slug rides LAST (':' is safe inside 'membrane::' -- split[1]
        # on 'membrane::tl:<slug>' was '' and every goal collided)
        slug = TimelineOps._tl_eid(text).rsplit(":", 1)[-1]
        eid = f"{MEMBRANE_NS}goal:{slug}"
        goal = QuantumEntity(
            eid=eid, cls=CLASS_GOAL,
            properties={"name": (text or "")[:120], "summary": (text or "")[:600],
                        "status": "open", "born": time.time()},
        )
        try:
            est.upsert(goal, embedder=getattr(srv.membrane, "embedder", None))
            created = True
        except Exception as e:
            return {"ok": False, "error": f"goal upsert failed: {e}"}
    # THE PIN (priority, never expiry): 'for 2w' parses to a window -- the
    # bonus decays over that window (the hand's weight fades, the score
    # carries after)
    bonus = 0.5
    if ttl:
        try:
            from .intent import parse_ttl
            window = parse_ttl(ttl)
            if window:
                bonus = 0.5 * max(0.25, 1.0 - (time.time()
                                               % window) / window)
        except Exception:
            pass
    sel = board.selection
    sel.pinned[goal.eid] = max(sel.pinned.get(goal.eid, 0.0), bonus)
    sel.suppressed.discard(goal.eid)
    return {"ok": True, "eid": goal.eid, "created": created, "pinned": True}


def _intent_cmd(srv, q: str) -> str:
    """Dispatch 'membrane: intend that X [for 2w]' / 'membrane: intents' /
    'membrane: fulfill #N|<fragment>' / 'membrane: drop intent #N|<fragment>'."""
    board = _intents_board(srv)
    if board is None:
        return "[membrane-intercept] intent layer unavailable (no board)."
    low = " ".join(q.split()).lower()
    if low.startswith(_INTENT_DROP):
        spec = low[len(_INTENT_DROP):].strip().rstrip("?.!")
        # THE GOAL-BACKED DROP (seat-local suppression): the goal stays
        # open for other seats/future sessions -- this seat only stops
        # SELECTING it. The json fallback keeps the legacy pop.
        if board.selection is not None:
            est = srv.entity_store
            goal = _resolve_goal(est, spec)
            if goal is not None:
                board.selection.suppressed.add(goal.eid)
                board.selection.pinned.pop(goal.eid, None)
                what = (f"suppressed on this seat: "
                        f"{goal.describe()[:100]} (the goal stays open "
                        f"for other seats)")
                _write_audit(srv.membrane, {"event": "intent", "op": "drop",
                                            "spec": _clip(spec, 120),
                                            "result": what})
                return (f"Membrane intentions: {what}.\n\n"
                        + _INTERCEPT_MARKER)
        n, note = board.drop(spec)
        what = f"withdrew {n} intention(s): {note}" if n \
            else f"nothing withdrawn: {note}"
        _write_audit(srv.membrane, {"event": "intent", "op": "drop",
                                    "spec": _clip(spec, 120), "result": what})
        return (f"Membrane intentions: {what}.\n\n" + _INTERCEPT_MARKER)
    if low.startswith(_INTENTS_CANON):
        return board.report() + "\n\n" + _INTERCEPT_MARKER
    if low.startswith(_INTENT_FULFILL):
        spec = low[len(_INTENT_FULFILL):].strip().rstrip("?.!")
        # THE GOAL-BACKED FULFILL (shared close): the goal's status flips
        # to fulfilled on the SHARED entity -- a project commitment done
        # is done for every seat; the active sets drop it automatically.
        if board.selection is not None:
            est = srv.entity_store
            goal = _resolve_goal(est, spec)
            if goal is not None:
                goal.properties["status"] = "fulfilled"
                goal.properties["fulfilled_ts"] = time.time()
                try:
                    est.upsert(goal)
                except Exception:
                    pass
                what = (f"fulfilled (shared): {goal.describe()[:100]}")
                _write_audit(srv.membrane, {"event": "intent", "op": "fulfill",
                                            "spec": _clip(spec, 120),
                                            "result": what})
                return (f"Membrane intentions: {what}.\n\n"
                        + _INTERCEPT_MARKER)
        n, note = board.fulfill(spec)
        what = f"fulfilled {n}: {note}" if n else f"nothing fulfilled: {note}"
        _write_audit(srv.membrane, {"event": "intent", "op": "fulfill",
                                    "spec": _clip(spec, 120), "result": what})
        return (f"Membrane intentions: {what}.\n\n"
                + board.report() + "\n\n" + _INTERCEPT_MARKER)
    if low.rstrip("?!").strip() == "membrane: intend that":
        return (board.report() + "\n\nPlant with `membrane: intend that <goal>` "
                "(optionally `... for 2w`); fulfill with `membrane: fulfill "
                "#N`; withdraw with `membrane: drop intent #N`. The board is "
                f"capped at {MAX_INTENTS} (oldest/starved evicted); expiry is "
                "the zombie-goal guard.\n\n" + _INTERCEPT_MARKER)
    payload = low[len(_INTENT_CANON):].strip()
    if not payload:
        return (board.report() + "\n\n" + _INTERCEPT_MARKER)
    # trailing 'for 2w' / 'for 10d' -> the TTL
    import re as _re
    m = _re.search(r"\s+for\s+(\d+\s*[dwmh])\s*$", payload)
    ttl = ""
    if m:
        ttl = m.group(1).replace(" ", "")
        payload = payload[:m.start()].strip()
    try:
        it, evicted = board.intend(payload, ttl, None)
    except RuntimeError as e:
        return (f"Membrane intent: cannot plant ({_one_line(e)}).\n\n"
                + _INTERCEPT_MARKER)
    _write_audit(srv.membrane, {"event": "intent", "op": "set",
                                "text": _clip(payload, 120), "iid": it.iid,
                                "expires": round(it.expires, 0),
                                "evicted": evicted})
    ev = (f" (evicted {evicted!r} -- at the {MAX_INTENTS}-intent cap)"
          if evicted else "")
    return (f"Membrane intention #{it.iid} planted: {payload!r}"
            + (f", expires in {ttl}" if ttl else ", default 14d expiry")
            + f". It steers the dream loop and rides recall until fulfilled "
            f"(`membrane: fulfill #{it.iid}`) or expired -- an intention is "
            f"never auto-closed by similarity; cos>=0.9 turns only flag "
            f"'maybe-done?'. Board: `membrane: intents`.{ev}\n\n"
            + _INTERCEPT_MARKER)


# ---- the codegraph lane (docs close-the-gap: Tencent's CodeGraph asset, ----
# retold): `membrane: codegraph <spec>` answers WHO CALLS X / WHAT DOES X
# CALL / blast radius of a file, from a stdlib-AST graph built lazily once per
# server (zero model calls). Read-only by construction -- nothing here writes
# the field -- so it is served pre-gate like recall/state.

_CODEGRAPH_CANON = "membrane: codegraph"

# the [membrane] monologue toggle (the thinking-channel banner): a canonical
# operator frame, pre-gate like every read/write -- zero model calls.
_MONOLOGUE_CANON = "membrane: monologue"

# read-lane marker (codegraph answers nothing was written -- the write marker
# would be a lie)
_CODEGRAPH_MARKER = ("[membrane-intercept] codegraph read; handled internally, "
                     "NOT learned. To force the model path, repeat prefixed "
                     "with 'model:'")

# min seconds between whole-tree mtime sweeps of the codegraph (edits through
# the tool lane refresh instantly; this only bounds the everything-else sweep)
_CG_SWEEP_INTERVAL = 30.0


def _tool_arg_path(fn: dict) -> str:
    """Pull a file path out of one tool call's arguments (edit/write):
    filePath for opencode's tools, file_path as the defensive alias. The
    JSON args may be malformed -- an unparseable call dirties nothing (the
    mtime sweep still catches the edit within its interval)."""
    try:
        a = json.loads(fn.get("arguments") or "{}")
        if isinstance(a, dict):
            return a.get("filePath") or a.get("file_path") or ""
    except Exception:
        pass
    return ""


def _codegraph_note_dirty(srv, paths) -> None:
    """Mark repo-relative .py paths dirty for the next codegraph query."""
    g = getattr(srv, "_codegraph", None)
    if g is None:
        return
    try:
        root = Path(g["root"])
        for p in paths:
            rp = Path(p)
            if not rp.is_absolute():
                rp = root / rp
            rp = rp.resolve()
            if rp.suffix != ".py":
                continue
            rel = rp.relative_to(root).as_posix()
            d = getattr(srv, "_cg_dirty", None)
            if d is None:
                d = srv._cg_dirty = []
            if rel not in d:
                d.append(rel)
    except (ValueError, OSError):
        pass


def _codegraph_graph(srv):
    """The lazily-built AST graph for the project root (the field's parent
    dir). Cached on the server and kept fresh INCREMENTALLY: edit/write tool
    calls mark paths dirty on the spot (see _capture_plan_and_calls), a
    throttled mtime sweep catches edits that bypassed the tool lane, and
    codegraph.rebuild_paths re-parses only what changed. Zero model calls."""
    g = getattr(srv, "_codegraph", None)
    if g is None:
        try:
            from .codegraph import build_graph
            root = Path(srv.membrane.field_path).parent.parent or Path(".")
            g = srv._codegraph = build_graph(root)
        except Exception as e:
            print(f"[opencode] codegraph build failed: {_one_line(e)}",
                  file=sys.stderr)
            return None
    _codegraph_refresh(srv, g)
    return g


def _codegraph_refresh(srv, g) -> None:
    """The incremental bridge: re-parse dirty paths (tool-lane edits) plus a
    throttled whole-tree mtime sweep (everything else). Never fatal."""
    now = time.time()
    rels = list(getattr(srv, "_cg_dirty", ()))
    if now - getattr(srv, "_cg_swept", 0.0) >= _CG_SWEEP_INTERVAL:
        try:
            from .codegraph import stale_paths
            rels += stale_paths(g)
        except Exception:
            pass
        srv._cg_swept = now
    rels = list(dict.fromkeys(rels))
    if not rels:
        return
    try:
        from .codegraph import rebuild_paths
        r = rebuild_paths(g, rels)
    except Exception as e:
        print(f"[opencode] codegraph rebuild failed: {_one_line(e)}",
              file=sys.stderr)
        return
    srv._cg_dirty = []
    cap = getattr(srv, "capturer", None)
    if cap is not None and (r["touched"] or r["dropped"]):
        cap.on_code(g, r["touched"])       # the code as evidence
    if r["touched"] or r["dropped"]:
        print(f"[opencode] codegraph refreshed "
              f"({len(r['touched'])} re-scanned, {len(r['dropped'])} "
              f"dropped)", file=sys.stderr)


def _codegraph_wants(q: str) -> str | None:
    """The codegraph intent gate's RESOLUTION form: the spec the graph
    answers ('callers X', 'signature X', 'stats'), or None when this
    turn is not a graph read. Symbol-like target = snake_case, dotted,
    or .py (a stolen coding turn is worse than a missed shortcut).
    The signature intents ('what are the args of X', 'signature of X',
    'what does X take') answer the question class the call-edge verbs
    never could: what-X-TAKES (the 'default args of _strip_context'
    exhibit)."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return None
    if low.startswith(_CODEGRAPH_CANON):
        spec = low[len(_CODEGRAPH_CANON):].strip().rstrip("?.!") \
            .strip() or "stats"
        return spec
    for opener in ("what calls ", "who calls ", "impact of touching ",
                   "blast radius of "):
        if low.startswith(opener):
            target = low[len(opener):].strip().rstrip("?.!").strip()
            symlike = ("_" in target or "." in target
                       or target.endswith(".py"))
            if symlike and len(low.split()) <= 12:
                return ("callers " + target).strip()
            return None
    for opener, spec_verb in (
            ("what are the args of ", "signature"),
            ("what are the parameters of ", "signature"),
            ("signature of ", "signature"),
            ("what does the signature of ", "signature"),
            ("default args of ", "signature"),
            ("defaults of ", "signature"),
    ):
        if low.startswith(opener):
            target = low[len(opener):].strip().rstrip("?.!").strip()
            if target.startswith("the "):
                target = target[4:].rstrip()
            target = target.rstrip("?.!").strip()
            symlike = ("_" in target or "." in target
                       or target.endswith(".py"))
            if symlike and len(low.split()) <= 14:
                # the target rides CASE-FAITHFUL: the query() resolver
                # matches name-insensitively at the last segment, but
                # the qualname lookup is exact -- slice the ORIGINAL
                # question at the opener's offset
                raw = " ".join((q or "").split())
                start = raw.lower().find(opener)
                tgt_case = raw[start + len(opener):][:len(target)] \
                    if start >= 0 else target
                return f"{spec_verb} {tgt_case}".strip()
            return None
    return None


def _is_codegraph_query(q: str) -> bool:
    return _codegraph_wants(q) is not None


def _codegraph_answer(srv, q: str) -> str:
    from .codegraph import query as cg_query
    g = _codegraph_graph(srv)
    if g is None:
        return ("[membrane-intercept] codegraph unavailable (graph failed "
                "to build).")
    spec = _codegraph_wants(q) or "stats"
    return ("[membrane-intercept] codegraph (auto-refreshes: tool edits "
            "re-scan instantly, other changes within a 30s sweep):\n"
            + cg_query(g, spec) + "\n\n" + _CODEGRAPH_MARKER)


def _watch_report_cmd(srv, q: str) -> str:
    """Dispatch 'membrane: watch X' / 'membrane: watches' / 'membrane: drop watch ...'."""
    board = _watches_board(srv)
    if board is None:
        return "[membrane-intercept] watch layer unavailable (no board)."
    low = " ".join(q.split()).lower()
    if low.startswith(_WATCH_DROP):
        spec = low[len(_WATCH_DROP):].strip().rstrip("?.!")
        n, note = board.drop_watch(spec)
        what = f"dropped {n} watch(es): {note}" if n else f"nothing dropped: {note}"
        _write_audit(srv.membrane, {"event": "watch", "op": "drop",
                                    "spec": _clip(spec, 120), "result": what})
        return (f"Membrane watches: {what}.\n\n" + _INTERCEPT_MARKER)
    if low.startswith(_WATCHES_CANON):
        return board.report() + "\n\n" + _INTERCEPT_MARKER
    if low.rstrip("?!").strip() == "membrane: watch":
        return (board.report() + "\n\nPlant with `membrane: watch <topic>`; "
                "drop with `membrane: drop watch #N|all|<fragment>`.\n\n"
                + _INTERCEPT_MARKER)
    label = low[len(_WATCH_CANON):].strip().rstrip("?.!")
    if not label:
        return (board.report() + "\n\n" + _INTERCEPT_MARKER)
    try:
        w, evicted = board.set_watch(label, None)
    except RuntimeError as e:
        return (f"Membrane watch: cannot plant ({_one_line(e)}).\n\n"
                + _INTERCEPT_MARKER)
    _write_audit(srv.membrane, {"event": "watch", "op": "set",
                                "label": _clip(label, 120), "wid": w.wid,
                                "radius": round(w.radius, 2),
                                "evicted": evicted})
    ev = (f" (evicted {evicted!r} -- at the {MAX_WATCHES}-watch cap)"
          if evicted else "")
    return (f"Membrane watch #{w.wid} planted: {label!r}, radius "
            f"{w.radius:.2f}. Every new memory landing inside reports to you "
            f"as a [membrane-watch] block on your next model turn. "
            f"Black-hole guard: at {MAX_WATCHES} watches max, and a watch "
            f"that swallows too many arrivals collapses (radius shrink) or "
            f"goes dormant. Board: `membrane: watches`.{ev}\n\n"
            + _INTERCEPT_MARKER)


def _watch_set_from_thought(srv, label: str) -> str:
    """A THOUGHT-path watch ask: the agent may plant/drop its own triggers
    (the board is capped + black-hole-guarded in watch.py, so a runaway agent
    cannot flood the field with watches). LISTING is always served."""
    board = _watches_board(srv)
    low = " ".join((label or "").split()).lower()
    if board is None:
        return _WATCH_THOUGHT_HEAD + " the board is unavailable."
    if low.startswith(("drop ", "all")):
        spec = low
        if low.startswith("drop "):
            spec = low[len("drop "):].strip() or "all"
        n, note = board.drop_watch(spec)
        what = (f"dropped {n} watch(es): {note}" if n
                else f"nothing dropped: {note}")
        _write_audit(srv.membrane, {"event": "watch", "op": "drop",
                                    "origin": "agent",
                                    "spec": _clip(spec, 120),
                                    "result": what})
        return ("[membrane-intercept] agent watch drop: " + what + "\n"
                + board.report())
    if low and low not in ("watch", "watch?"):
        try:
            w, evicted = board.set_watch(low, None)
        except RuntimeError as e:
            return ("[membrane-intercept] agent watch: cannot plant "
                    f"({_one_line(e)}).\n" + board.report())
        _write_audit(srv.membrane, {"event": "watch", "op": "set",
                                    "origin": "agent",
                                    "label": _clip(low, 120),
                                    "wid": w.wid,
                                    "radius": round(w.radius, 2),
                                    "evicted": evicted})
        ev = (f" (evicted {evicted!r} -- at the {MAX_WATCHES}-watch cap)" if evicted else "")
        return (f"[membrane-intercept] agent watch planted: #{w.wid} "
                f"{low!r}, radius {w.radius:.2f}. New memories landing "
                f"inside report back as [membrane-watch] blocks.{ev}\n"
                + board.report())
    return _WATCH_THOUGHT_HEAD + "\n" + board.report()


def _window_seconds(spec: str) -> float | None:
    """Parse a time-window forget spec -> seconds, or None when the spec is not
    a window. Forms: 'the last N minutes/hours/days', 'last 30m', 'this
    session', 'the session', 'that conversation', 'the last hour/session'."""
    t = " ".join(spec.split()).lower().strip()
    if t.startswith("the "):
        t = t[4:]
    if t.startswith("last "):
        t = t[5:]
    t = t.strip()
    if t in ("session", "this session", "whole session", "entire session",
             "conversation", "this conversation", "that conversation",
             "everything from this session"):
        return -1.0                      # sentinel: 'session' = since boot
    import re
    m = re.match(r"^(?:(\d+)\s*)?(seconds?|secs?|s|minutes?|mins?|m|hours?|h|"
                 r"days?|d)\b.*$", t)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else 1
    unit = m.group(2)[0]
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return float(n * mult)


def _forget_targets(srv, spec: str) -> list[tuple[int, float]]:
    """Resolve an OPERATOR forget spec to event numbers: '#12' / '3-7' (the
    numbers the blocks cite), a time window of this seat's own sessions
    ('the last hour'; 'this session' = since the seat booted), 'everything'
    (this origin's live events, code aside), else the events with a sentence
    near the spec's meaning (sim >= 0.55, up to 40). [(n, sim)] by number."""
    fld = _efield_of(srv)
    if fld is None:
        return []
    spec_l = spec.strip().lower()
    nums = _parse_agent_forget(spec)
    if nums:
        return [(n, 0.0) for n in nums]
    win = _window_seconds(spec_l)
    if win is not None:
        since = float(getattr(srv, "_boot_ts", 0.0) or 0.0) if win < 0 else time.time() - win
        return [(d["n"], 0.0) for d in fld.events_since(since, _own_session_prefix(srv))
                if d.get("n") is not None]
    if not spec_l or spec_l.startswith("everything") or spec_l in _FORGET_ALL:
        return sorted((d["n"], 0.0) for _, d in fld.b.scan("events", {"origin": fld.origin})
                      if d.get("n") is not None and d.get("stream") != "code"
                      and d.get("status") != "forgotten")
    try:
        vec = srv.membrane.embedder.embed([spec])[0]
    except Exception:
        return []
    return sorted((d["n"], s) for d, s in fld.events_near(vec, k=_FORGET_LIMIT)
                  if d.get("n") is not None)


def _forget_report(srv, spec: str) -> str:
    """The operator's forget (pre-gate, no model call): the events the spec
    resolves to are forgotten and the entities they moved recomputed."""
    mem = srv.membrane
    if _efield_of(srv) is None:
        return ("Membrane forget: the entity field is unavailable; nothing removed."
                "\n\n" + _INTERCEPT_MARKER)
    is_window = _window_seconds(spec) is not None
    targets = _forget_targets(srv, spec)
    if not targets:
        return ("Membrane forget (operator path, no model call): "
                + ("nothing was captured in that window" if is_window
                   else "nothing in the field matches that")
                + "; nothing removed.\n\n" + _INTERCEPT_MARKER)
    fld = srv.efield
    shown = {}
    for n, sim in targets[:8]:
        hit = fld.event_by_number(n)
        if hit:
            shown[n] = (sim, _clip(hit[1].get("text") or hit[1].get("tool") or "", 160))
    res = fld.forget_many([n for n, _ in targets])
    done = [r for r in res if r.get("key") and not r.get("already")]
    _write_audit(mem, {"event": "forget-events", "origin": "operator", "window": is_window,
                       "removed": len(done), "spec": _clip(spec, 200)})
    lines = [f"Membrane forget (operator path, no model call): forgot {len(done)} of "
             f"{len(targets)} event(s); the entities they moved are recomputed."]
    for n, (sim, text) in shown.items():
        lines.append(f"- #{n}{'' if sim == 0.0 else f' (sim {sim:.2f})'}: {text}")
    if len(targets) > 8:
        lines.append(f"- ... and {len(targets) - 8} more")
    return "\n".join(lines) + _INTERCEPT_MARKER


# ---- the agent's self-serve forget (index-form ONLY, executes) --------------
# The thought intercept detects `membrane: forget ...` in my own prose. The
# operator path (semantic forms, windows, "everything") stays human-only --
# the 2026-08-31 overshoot (a semantic forget that wiped the hot field)
# is the proof that free-text deletion from model prose is too blunt. What
# the AGENT may do is drop cells it can SEE: explicit indexes parsed by a
# strict token grammar, capped hard (AGENT_FORGET_LIMIT), audited with
# origin 'agent'. Wordy asks never delete -- they PROPOSE: the reply lists
# candidate indexes (top-K sim) and the agent re-asks in index form.

def _parse_agent_forget(spec: str) -> list[int] | None:
    """Strict index-form parser. Returns a deduped bounded index list, or
    None when the spec is NOT pure index form (wordy -> the proposal path).
    Accepts '#12', '12', '3-7', '3-7, 9' -- nothing else (no words, no
    windows, no 'everything': those are the operator's forms for a reason)."""
    toks = [t.strip().lstrip("#") for t in spec.replace(",", " ").split()]
    if not toks:
        return None
    for t in toks:
        if t.isdigit():
            continue
        if "-" in t:
            parts = t.split("-", 1)
            if not (len(parts) == 2 and parts[0].isdigit()
                    and parts[1].isdigit()):
                return None
            continue
        return None
    idx: set[int] = set()
    for t in toks:
        if "-" in t:
            a, b = (int(p) for p in t.split("-", 1))
            idx.update(range(min(a, b), max(a, b) + 1))
        else:
            idx.add(int(t))
    return sorted(idx)


def _agent_forget(srv, spec: str) -> str:
    """The thought-path forget: index form ('#12', '3-7') forgets those
    events, capped at AGENT_FORGET_LIMIT; anything wordier only PROPOSES the
    nearest events -- free text never deletes from model prose."""
    fld = _efield_of(srv)
    if fld is None or srv.membrane.embedder is None:
        return ("[membrane-intercept] forget unavailable: the entity field or the "
                "embedder is not up yet; retry once it is.")
    nums = _parse_agent_forget(spec)
    if nums is None:
        try:
            near = fld.events_near(srv.membrane.embedder.embed([spec])[0], k=8)
        except Exception:
            near = []
        if not near:
            return ("[membrane-intercept] forget proposal: nothing near that "
                    "(sim >= 0.55). Nothing removed. Point at event numbers instead.")
        lst = ", ".join(f"#{d['n']} (sim {s_:.2f})" for d, s_ in near if d.get("n"))
        return ("[membrane-intercept] wordy asks never delete from agent prose. "
                f"Nearest events: {lst}. To remove, re-ask in index form "
                "(e.g. 'membrane: forget #3-7, 9'). Nothing removed.")
    if len(nums) > _AGENT_FORGET_LIMIT:
        return (f"[membrane-intercept] forget: {len(nums)} events exceed the agent cap "
                f"({_AGENT_FORGET_LIMIT}). Ask the operator for a larger delete. "
                "Nothing removed.")
    res = _efield_forget(srv, nums)
    _write_audit(srv.membrane, {"event": "forget-events", "origin": "agent",
                                "results": res, "spec": _clip(spec, 200)})
    lines = ["[membrane-intercept] agent forget (index form, no model call):"]
    for r in res:
        if r.get("missing"):
            lines.append(f"- #{r['n']}: no such event")
        elif r.get("error"):
            lines.append(f"- #{r['n']}: failed ({r['error']})")
        else:
            lines.append(f"- #{r['n']}: {'already forgotten' if r['already'] else 'forgotten'}; "
                         f"{len(r['entities'])} entities recomputed")
    return "\n".join(lines)


_AGENT_LEARN_MAX_CHARS = 2000   # one note, not a transcript dump


def _learn_refusal(payload: str) -> str | None:
    """Why a lesson is not memory material (None when it is)."""
    if not payload:
        return "nothing to learn (the payload was empty)"
    if "```" in payload:
        return "fenced code blocks are not memory material -- state the LESSON in prose"
    if len(payload) > _AGENT_LEARN_MAX_CHARS:
        return (f"payload too long ({len(payload)} > {_AGENT_LEARN_MAX_CHARS} chars) -- "
                "a lesson is a note, not a transcript")
    return None


def _agent_learn(srv, payload: str) -> str:
    """The thought-path learn (agent writes, on by default): `membrane: learn
    that X` in the model's prose becomes a NOTE event (src 'model'), whose
    claims the evidence checks like any other model claim."""
    payload = (payload or "").strip()
    why = _learn_refusal(payload)
    if why:
        return f"[membrane-intercept] learn: {why}. Nothing written."
    note = _efield_learn(srv, payload, "model")
    if not note:
        return ("[membrane-intercept] learn unavailable: the entity field is not up; "
                "nothing written.")
    _write_audit(srv.membrane, {"event": "agent-learn", "origin": "agent",
                                "tag": "agent-note", "payload": _clip(payload, 400),
                                "note": note.strip()})
    return f"[membrane-intercept] agent learn (no model call):{note}"


def _efield_learn(srv, payload: str, src: str) -> str:
    """The lesson as a NOTE event: ' Written as note #N.' ('' when the field
    is unavailable or the payload is empty)."""
    fld = _efield_of(srv)
    if fld is None or not payload.strip():
        return ""
    try:
        k = fld.learn(payload, src=src)
        d = fld.b.get("events", k) or {}
        return f" Written as note #{d.get('n', '?')}."
    except Exception as e:
        print(f"[efield] learn failed: {e}", file=sys.stderr)
        return ""


def _efield_forget(srv, nums: list[int]) -> list[dict]:
    fld = _efield_of(srv)
    out = []
    for n in nums:
        try:
            r = fld.forget(n)
        except Exception as e:
            r = {"n": n, "error": str(e)[:120]}
        out.append(r or {"n": n, "missing": True})
    return out


def _learn_report(srv, payload: str) -> str:
    """The operator's learn (pre-gate, no model call): a NOTE event, and the
    verbatim words in the user ledger."""
    payload = (payload or "").strip()
    why = _learn_refusal(payload) if payload else "nothing to learn"
    if why:
        return f"Membrane learn (operator path): {why}; nothing written.\n\n" + _INTERCEPT_MARKER
    note = _efield_learn(srv, payload, "user")
    if not note:
        return ("Membrane learn (operator path): the entity field is unavailable; "
                "nothing written.\n\n" + _INTERCEPT_MARKER)
    ledger = getattr(srv, "user_ledger", None)
    if ledger is not None:
        try:
            ledger.append(payload)          # the operator's verbatim words
        except Exception:
            pass
    _write_audit(srv.membrane, {"event": "operator-learn", "tag": _LEARN_TAG,
                                "payload": _clip(payload, 400), "note": note.strip()})
    return (f"Membrane learn (operator path, no model call):{note}\n"
            f"    {_clip(payload, 280)}\n\n" + _INTERCEPT_MARKER)


# ---- semantic fallback for the intercepts (the field grows its own detector) -
# Paradox (superseded, see the field-grown layer below): recall/state asks are
# intercepted BEFORE learning, so the field never holds examples of them -- no
# self-grown detector seemed possible. The FIXED seed is still external: a
# fixed phrase set, embedded once, cosine-matched when the LEXICAL matcher
# misses. High bar (it can steal a real coding turn); the exact-echo recovery
# hatch below fixes a false steal.

_INTERCEPT_SEEDS = [
    # recall family
    "what do you remember about our earlier discussion of retry handling",
    "what have you learned about the deployment pipeline",
    "what do you recall from previous sessions about my preferences",
    "search your memory for the name of that helper function",
    "query the field for anything about rate limiting",
    "what do you know about the flaky test we fixed last week",
    "do you remember why we chose optimistic locking",
    "remind me what we decided about the config format",
    "show me what you remember of the database migration plan",
    # state family
    "how full is your memory",
    "what state is your memory in",
    "tell me about your field statistics",
    "how is your memory health",
    "how many memories do you have right now",
    "are there any new memories since earlier",
    # learn family (the write skill, without the incantation) -- payload VARIED
    # so the family spans payload space (a topic-bound seed set misses real
    # writes whose payload differs from every seed's payload)
    "save this to your memory: the deploy script needs RUN_ENV=staging",
    "save this to your memory: we deploy on tuesdays",
    "remember that the api key rotates every month",
    "remember that the user prefers dark mode",
    "store a note in your field that the cron job moved to 3am",
    "store a note in your field that tests run with pnpm",
    "keep in mind for future sessions that we use pnpm here",
    "keep in mind for future sessions that staging uses port 8080",
    "you should remember that the staging password rotates weekly",
    "you should remember that the retry budget is three attempts",
    "note for later: the retry budget is three attempts",
    "note for later: the linter config lives in biome.json",
    # forget family (the delete skill, without the incantation) -- payload varied
    "forget everything about the old config format",
    "forget everything about the old retry policy",
    "delete the memory of that broken deploy workaround",
    "delete the memory of the deprecated v1 endpoints",
    "wipe that from your memory, the staging notes are stale",
    "erase what you learned about the deprecated api",
    "erase what you learned about the staging credentials",
    "remove from your memory the thing about the flaky windows runner",
]
# near-miss coding turns that must NOT trip the semantic gate
_INTERCEPT_ANTI_SEEDS = [
    "refactor the retry handling in client.py",
    "how do we handle retry logic in the request layer",
    "remember to bump the version before releasing",
    "remember to run the deploy script on tuesdays",
    "add a field to the user model for the last login time",
    "the tests are flaky, find out why and fix them",
    "remind me to review the migration plan before friday",
    "remind me what the api key rotation schedule is",
    "write a unit test for the rate limiter",
    "clean up the deprecated api endpoints this sprint",
    "note that the linter fails on generated files",
    "delete the old log files in /var/tmp",
]

# runtime caches (populated on the MembraneOpenAIHandler's server): the embedded
# seed matrix + threshold for the FIXED layer, and the same for the FIELD-GROWN
# layer (canon-frame asks harvested from the discourse events, cached on the
# origin's event count). Per-call embedding of the query costs one
# /api/embeddings round-trip (~ms local) and only fires when the lexical
# frames miss.
_SEED_CACHE = {"matrix": None, "thr": None, "tuned": False,
               "field": {"size": -1, "matrix": None, "thr": None,
                         "topics": [], "scanned": False}}


def _tune_threshold(srv, Vn, tag: str) -> float | None:
    """Midpoint threshold for one seed layer (tag = 'fixed'|'field') vs the
    shared anti-seed set: halfway between the strongest anti-seed pull to any
    seed (false-steal pressure) and a perfect family match (1.0). None when
    the geometry cannot separate the sets -- 0 would intercept everything;
    None is the honest 'cannot separate' and that layer stays off (lexical
    frames carry it)."""
    mem = srv.membrane
    if mem.embedder is None:
        return None
    anti = np.asarray(mem.embedder.embed(_INTERCEPT_ANTI_SEEDS), dtype=float)
    norms = np.linalg.norm(anti, axis=1)
    norms[norms <= 1e-12] = 1.0
    anti = anti / norms[:, None]
    anti_best = float((anti @ Vn.T).max())
    thr = (1.0 + anti_best) / 2.0
    if thr > 0.99:
        # degenerate geometry (tiny/one-hot embedding space): anti-seeds
        # are indistinguishable from seeds -- the semantic gate would sit
        # unreachable at ~1.0. Disable; the lexical frames carry it.
        print(f"[opencode] intercept {tag} seeds inseparable "
              f"(anti_best={anti_best:.4f}); layer disabled", file=sys.stderr)
        return None
    print(f"[opencode] intercept {tag} layer tuned: thr={thr:.3f} "
          f"(anti_best={anti_best:.3f})", file=sys.stderr)
    return thr


# Fam = "recall" | "state" | "learn" | "forget" -- the four skill families
# the semantic gate routes into. Fixed-layer seeds are labeled lexically here;
# field-grown seeds are labeled by which canon frame they carry.
_FAMILIES = ("recall", "state", "learn", "forget")
_STATE_WORDS = ("state", "stats", "statistics", "health", "full", "many")
_LEARN_WORDS = ("save this to your memory", "remember that", "store a note",
                "keep in mind", "you should remember", "note for later")
_FORGET_WORDS = ("forget", "delete the memory", "wipe", "erase",
                 "remove from your memory")


def _family_of(text: str) -> str:
    """Fixed-layer seed labeling: learn/forget keywords win (they read as
    commands), state words beat recall (a state ask is about the FIELD's own
    math), else recall."""
    low = text.lower()
    if any(w in low for w in _FORGET_WORDS):
        return "forget"
    if any(w in low for w in _LEARN_WORDS):
        return "learn"
    if any(w in low for w in _STATE_WORDS):
        return "state"
    return "recall"


def _seed_topics() -> list:
    """(text, family) for every fixed seed phrase."""
    return [(s, _family_of(s)) for s in _INTERCEPT_SEEDS]


def _embed_seed_matrix(srv) -> bool:
    """Embed every fixed seed once, cache (N, D) unit rows + the tuned
    threshold. False when the embedder is unavailable (the fallback then
    stays off -- lexical-only behavior)."""
    if _SEED_CACHE["matrix"] is not None:
        return True
    mem = srv.membrane
    if mem.embedder is None:
        return False
    topics = _seed_topics()
    try:
        V = np.asarray(mem.embedder.embed([t for t, _ in topics]), dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        thr = _tune_threshold(srv, Vn, "fixed")
    except Exception as e:
        print(f"[opencode] intercept seed embedding failed ({e}); "
              f"semantic fallback off", file=sys.stderr)
        return False
    _SEED_CACHE["matrix"] = (Vn, topics)
    _SEED_CACHE["thr"] = thr
    return True


# ---- field-grown seed layer -------------------------------------------------
# The 524-note paradox ("intercepted asks are never learned, so the field cannot
# grow its own detector") is broken by the CANON-FRAME TURNS that DO enter the
# field: operator-note writes (the learn skill's payload), forget-command
# echoes, and any escaped (`model:`) skill ask the capture takes. Those
# discourse events carry the canonical frames verbatim -- harvest them as a
# second seed layer so the detector grows from the operator's own hand, not
# from raw model traffic (which would let junk reasoning poison the gate).
# Labeling: which canon frame the event text carries -> its family (model
# prose and reasoning are excluded by their stream/role, not by prefix: the
# old captured-thought misroute cannot happen on events).


def _field_seed_topics(srv) -> list:
    """(text, family, #n) for every discourse event carrying a canonical
    skill frame (user/model prose only -- not reasoning turns). Cached on
    the origin's event count; empty when --no-field-seeds."""
    if not getattr(srv, "field_seeds", True):
        return []
    fld = _efield_of(srv)
    if fld is None:
        return []
    n = int(fld._ostats.get("ev_n") or 0)
    c = _SEED_CACHE["field"]
    if c["size"] == n and c["scanned"]:
        return c["topics"]
    topics: list = []
    for _k, d in fld.b.scan("events", {"origin": fld.origin,
                                       "stream": "discourse",
                                       "role": ["user", "model"]}):
        if d.get("status") == "forgotten":
            continue
        text = " ".join((d.get("text") or "").split())
        if not text:
            continue
        low = text.lower()
        # frames only at the START of the text: prose that merely MENTIONS
        # "note that" mid-sentence is not a memory command (prefix-only).
        if low.startswith("model:"):            # escaped asks stay recall seeds
            fam = "recall"
        elif low.startswith(_FORGET_CANON) or any(low.startswith(f) for f in _FORGET_FRAMES):
            fam = "forget"
        elif low.startswith(_LEARN_CANON) or any(low.startswith(f) for f in _LEARN_FRAMES):
            fam = "learn"
        elif any(low.startswith(f) for f in _RECALL_FRAMES):
            fam = "recall"
        elif low.startswith("membrane:"):
            body = low[len("membrane:"):].strip()
            fam = ("learn" if body.startswith("learn") else
                   "forget" if body.startswith("forget") else
                   "state" if body.startswith(("field state", "state")) else
                   "recall")
        else:
            continue
        topics.append((text, fam, d.get("n")))
    c["topics"] = topics
    c["size"] = n
    c["scanned"] = True
    c["matrix"] = None            # force re-embed on next use
    return topics


def _embed_field_seed_matrix(srv) -> bool:
    """Embed + threshold-tune the field-grown layer. False leaves the layer
    off (no seeds, embedder gone, --no-field-seeds, or inseparable)."""
    topics = _field_seed_topics(srv)
    if not topics:
        return False
    c = _SEED_CACHE["field"]
    if c["matrix"] is not None:
        return True
    mem = srv.membrane
    if mem.embedder is None:
        return False
    try:
        V = np.asarray(mem.embedder.embed([t for t, _, _ in topics]),
                       dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        thr = _tune_threshold(srv, Vn, "field")
        # (None -> inseparable -> the layer stays off, the honest 'cannot
        # separate')
    except Exception as e:
        print(f"[opencode] field seed embedding failed ({e}); "
              f"field layer off", file=sys.stderr)
        return False
    c["matrix"] = (Vn, thr)
    return thr is not None


def _semantic_intercept_hit(srv, q: str) -> tuple[str, float, str] | None:
    """4-family semantic verdict: (family, sim, source) for the best-matching
    family over BOTH seed layers (source = 'fixed' | 'field'), or None when
    nothing clears its layer's tuned threshold. A layer tuned to None
    (inseparable geometry) or failed embed simply contributes nothing --
    lexical frames carry on. Family margin: the winner must beat the best of
    every OTHER family by +0.02 (a state ask must not route to recall, a
    learn to a forget, etc.)."""
    if not _embed_seed_matrix(srv):
        return None
    mem = srv.membrane
    layers = []
    if _SEED_CACHE["thr"] is not None:
        layers.append((_SEED_CACHE["matrix"][0], _SEED_CACHE["thr"], "fixed"))
    if _embed_field_seed_matrix(srv):
        Vn, thr = _SEED_CACHE["field"]["matrix"]
        layers.append((np.asarray(Vn, dtype=float), thr, "field"))
    if not layers:
        return None
    try:
        qv = np.asarray(mem.embedder.embed([q]), dtype=float)[0]
        n = float(np.linalg.norm(qv))
        if n <= 1e-12:
            return None
        qv = qv / n
    except Exception:
        return None
    fam_best = {f: 0.0 for f in _FAMILIES}
    hit_src: dict = {}
    hit_txt: dict = {}
    for Vn, thr, src in layers:
        # field topics are (text, family, idx) triples; fixed are (text, family)
        topics = (_SEED_CACHE["matrix"][1] if src == "fixed"
                  else [(t, fam) for t, fam, _ in _SEED_CACHE["field"]["topics"]])
        sims = np.asarray(Vn, dtype=float) @ qv
        for s, (t, fam) in zip(sims, topics):
            if float(s) >= thr and float(s) > fam_best[fam]:
                fam_best[fam] = float(s)
                hit_src[fam] = src
                hit_txt[fam] = t
    fam = max(fam_best, key=lambda f: fam_best[f])
    if fam_best[fam] <= 0.0:
        return None
    others = max((v for f, v in fam_best.items() if f != fam), default=0.0)
    if fam_best[fam] < others + 0.02:
        return None
    print(f"[opencode] semantic intercept: family={fam} "
          f"sim={fam_best[fam]:.3f} source={hit_src[fam]} "
          f"seed={_clip(hit_txt[fam], 60)!r}", file=sys.stderr)
    return fam, fam_best[fam], hit_src[fam]


def _semantic_recall_hit(srv, q: str) -> tuple[bool, bool]:
    """Compat shim over the 4-family verdict (recall/state booleans)."""
    hit = _semantic_intercept_hit(srv, q)
    if hit is None:
        return False, False
    fam = hit[0]
    return fam == "recall", fam == "state"


def _recall_report(srv, topic: str) -> str:
    """The internal recall answer: the field's block for the topic (the past
    answers nearest it + the entities it names), or the newest numbered
    events for a dump. Read-only: no learn, no audit write, no nudge --
    the only embed call is of the TOPIC itself (an outbound query, never
    stored)."""
    fld = _efield_of(srv)
    head = ("Field recall (computed live by membrane, no model call; "
            "this turn was intercepted and NOT learned):")
    if fld is None:
        return head + "\n- the entity field is off (the field is memory)"
    try:
        from .entity_block import render_for
    except ImportError:
        render_for = None
    lines = [head]
    # the EntityField carries the LazyEmbedder; 'unresolved' is the Seat's
    # embedder still None (fld.emb.model is the read-safe view of it)
    if topic and getattr(fld.emb, "model", "?") == "?":
        return head + "\n- embedder unresolved; cannot search the field"
    if topic:
        block = None
        try:
            block = render_for(fld, topic) if render_for else None
        except Exception as e:
            lines.append(f"- (render skipped: {_one_line(e)})")
        if not block:
            lines.append(f"- nothing in the field about {topic!r} yet")
        else:
            lines.append(block)
    else:
        # dump: the newest numbered events, ~40 back from the last seq
        try:
            last_seq = int(fld._ostats.get("seq_last") or 0)
        except Exception:
            last_seq = 0
        rows = []
        try:
            rows = [d for _, d in fld.b.scan("events", {"origin": fld.origin,
                                                        "seq": {"gt": max(0, last_seq - 40)}})]
        except Exception as e:
            lines.append(f"- (event dump skipped: {_one_line(e)})")
        rows = [d for d in rows
                if d.get("stream") in ("discourse", "observation", "code")
                and d.get("status") != "forgotten"][-12:]
        lines.append(f"- field holds {int(fld._ostats.get('ev_n') or 0)} numbered events; newest:")
        for d in rows:
            num = f"#{d['n']} " if d.get("n") is not None else ""
            head_txt = " ".join((d.get("text") or d.get("served") or "").split())
            tag = d.get("stream")
            if d.get("stream") == "observation":
                tag = f"tool {d.get('tool')}"
            lines.append(f"{num}[{tag}] {_clip(head_txt, 200)}")
    lines.append("- trust: ground (observed + code) > user (operator) "
                 "> model (own prior guess -- verify against ground)")
    return "\n".join(lines)


# ---- THE STATELESS LLM (the membrain upgrade: --no-context) ----------------

def _strip_context(messages: list) -> list:
    """THE STRIP (the turn boundary): keep the system message(s) + the
    CURRENT TURN -- from the turn's opening ask (the last real user text
    message at/before the last tool exchange: the ask the agent loop is
    working) through the end (the loop's tool_calls + tool results + any
    trailing follow-up ask ride with it). The transcript history BEFORE
    that drops: the field is the only memory."""
    msgs = messages or []
    # 1. the system prompt(s) always ride -- the model's persona + tools
    #    are not transcript.
    sys_idx = {i for i, m in enumerate(msgs)
               if isinstance(m, dict) and m.get("role") == "system"}

    def _user_text(i: int) -> bool:
        m = msgs[i]
        if not isinstance(m, dict) or m.get("role") != "user":
            return False
        c = m.get("content")
        return isinstance(c, str) and bool(c.strip())

    # 2. the last tool exchange (a tool result or the assistant's
    #    tool_calls): the current loop's tail.
    t_last = None
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool" or (m.get("role") == "assistant"
                                       and m.get("tool_calls")):
            t_last = i
            break
    # 3. the boundary: the turn's opening ask -- the last real user text
    #    at/before the loop's tail; with no tool loop, the last TWO real
    #    user turns ride (THE AMNESIA FIX: the chat-style follow-up used to
    #    drop the model's entire previous reply -- the one-answer bridge
    #    made every non-tool turn start from near-zero; 2 exchanges stay
    #    bounded ~2k chars and keep the conversational continuity).
    scan_end = t_last if t_last is not None else len(msgs) - 1
    start = None
    for i in range(scan_end, -1, -1):
        if _user_text(i):
            start = i
            break
    if start is None and t_last is not None:
        for i in range(len(msgs) - 1, -1, -1):
            if _user_text(i):
                start = i
                break
    if start is None:
        return msgs
    if t_last is None:              # pure chat: the previous exchange rides too
        user_texts = [j for j in range(start) if _user_text(j)]
        if user_texts:
            start = user_texts[-1]
    keep = sys_idx | set(range(start, len(msgs)))
    return [m for i, m in enumerate(msgs) if i in keep]


_ELIDE_MARK = ("[membrane: older tool {what} elided ({n} chars; head shown) -- "
               "the full result is in the field; re-run the tool if you "
               "need it verbatim]")


def _elide_text(text: str, head: int, what: str) -> str:
    return (text[:head].rstrip() + "\n"
            + _ELIDE_MARK.format(what=what, n=len(text)))


def _elide_args(args: str, head: int, min_chars: int) -> str:
    """Shrink the big STRING fields of a tool call's JSON arguments (an
    edit's newString, a write's content) -- the JSON stays valid, the
    small fields (paths, flags) stay whole. Non-JSON args pass as-is."""
    try:
        obj = json.loads(args)
    except (TypeError, ValueError):
        return args
    if not isinstance(obj, dict):
        return args
    changed = False
    for k, v in obj.items():
        if isinstance(v, str) and len(v) > min_chars:
            obj[k] = _elide_text(v, head, "argument")
            changed = True
    return json.dumps(obj) if changed else args


def _elide_tool_tail(messages: list, keep_last: int = 3, head: int = 300,
                     min_chars: int = 800) -> tuple[list, int]:
    """THE IN-TURN ELISION (the stateless LLM's second half): the strip
    bounds the transcript BEFORE the turn, but a long agent loop still
    grows the turn itself without bound. Every tool exchange older than
    the last ``keep_last`` shrinks: a big tool RESULT to its head + a
    marker, a big tool-call ARGUMENT string the same way. The structure
    is untouched -- every message and every tool_call_id stays, so the
    call/result pairing providers enforce still holds. Returns (messages,
    chars_saved); the input dicts are never mutated."""
    msgs = list(messages or [])
    if keep_last < 0:
        return msgs, 0
    tool_idx = [i for i, m in enumerate(msgs)
                if isinstance(m, dict) and m.get("role") == "tool"]
    old = set(tool_idx[:max(0, len(tool_idx) - keep_last)])
    if not old:
        return msgs, 0
    cutoff = max(old)                    # calls after this are recent
    saved = 0
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        if i in old:
            c = m.get("content")
            if isinstance(c, list):      # multi-part: the text parts
                c = "\n".join(p.get("text", "") for p in c
                              if isinstance(p, dict))
            if isinstance(c, str) and len(c) > min_chars:
                new = _elide_text(c, head, "result")
                saved += len(c) - len(new)
                msgs[i] = dict(m, content=new)
        elif (i < cutoff and m.get("role") == "assistant"
              and m.get("tool_calls")):
            calls = []
            for tc in m["tool_calls"]:
                fn = (tc or {}).get("function") or {}
                a = fn.get("arguments")
                if isinstance(a, str) and len(a) > min_chars:
                    na = _elide_args(a, head, min_chars)
                    if na != a:
                        saved += len(a) - len(na)
                        tc = dict(tc, function=dict(fn, arguments=na))
                calls.append(tc)
            msgs[i] = dict(m, tool_calls=calls)
    return msgs, saved


def _last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c
        if isinstance(c, list):
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            t = " ".join(p for p in parts if p)
            if t.strip():
                return t
    return ""


def n_ctx_mem(ctx: str | None) -> int:
    """Count injected memory entries: ALL render formats -- the legacy 'Q: ...'
    lines, the classic 'situation: ...' entries, and the action-first
    'Memory (sim ...): <fact> [situation: ...]' entries (counted by their
    header; the trailing [situation: ...] tag subtracted so it never
    double-counts)."""
    if not ctx:
        return 0
    return (ctx.count("Q: ")
            + ctx.count("situation: ")
            - ctx.count("[situation: ")
            + ctx.count("\nMemory (sim "))


# ---- the surface log (the totality view) ------------------------------------
# Everything membrane exchanges with the model, per turn, as ONE inspectable
# ring: the injected blocks (recall / where-we-are / peer / watch / thought
# asks), the routing decision, and what came back. The operator's window
# onto the API surface between membrane and the model -- the answer to "I
# want to see everything membrane sends/receives". Backed by /membrane/state
# ('surface' key) and 'membrane: surface' in the chat lane.

_SURFACE_CAP = 12          # turns kept (the ring)


def _surface_report(srv) -> str:
    """The totality view as text: the last turns' full API surface -- what
    membrane injected (per channel), the routing, and what the model
    returned. The operator's answer to 'what does membrane send/receive?'"""
    ring = list(getattr(srv, "_surface_ring", None) or [])
    if not ring:
        return ("Membrane surface: no turns recorded yet this session "
                "(the ring fills as chat turns flow).\n\n" + _INTERCEPT_MARKER)
    out = [f"Membrane surface -- the last {len(ring)} turn(s) of the "
           f"membrane<->model API surface (newest last):"]
    for rec in ring[-6:]:
        when = time.strftime("%H:%M:%S", time.localtime(rec.get("ts", 0)))
        head = [f"[{when}] {rec.get('verdict')} sim={rec.get('sim')} "
                f"model={rec.get('model')} route={rec.get('route')} "
                f"({rec.get('route_reason')})"]
        head.append(f"  Q: {rec.get('query', '')}")
        blocks = rec.get("sent_blocks") or []
        if blocks:
            head.append("  SENT:")
            for b in blocks:
                head.append(f"    - {b['block']} ({b['chars']}ch): "
                            f"{b['preview'][:110]}")
        else:
            head.append("  SENT: (nothing injected)")
        rh = rec.get("reply_head") or ""
        tc = " [tool-calls]" if rec.get("tool_calls") else ""
        ln = rec.get("learned")
        head.append(f"  GOT: {_clip(rh, 140) or '(empty)'}{tc}"
                    + (f" [learned #{ln}]" if ln is not None else ""))
        out.append("\n".join(head))
    return "\n----\n".join(out) + "\n\n" + _INTERCEPT_MARKER


def _surface_push(srv, rec: dict) -> None:
    ring = getattr(srv, "_surface_ring", None)
    if ring is None:
        srv._surface_ring = ring = []
    ring.append(rec)
    if len(ring) > _SURFACE_CAP:
        del ring[:len(ring) - _SURFACE_CAP]


def _surface_tail(srv) -> list:
    return list(getattr(srv, "_surface_ring", None) or [])


def _surface_block_summary(ctx: str | None) -> list[dict]:
    """An injected context block -> per-section summaries (the totality view's
    'what we sent' side). Splits on the known block headers so each channel
    (recall / where-we-are / peer / watch / intent / review / ledger) is
    visible separately, with sizes."""
    if not ctx:
        return []
    markers = [
        ("recall", "Recalled memories"),
        ("global", "Global memories"),
        ("where-we-are", "WHERE WE ARE"),
        ("intentions", "OPEN INTENTIONS"),
        ("review", "PENDING HUMAN REVIEWS"),
        ("watch", "[membrane-watch]"),
        ("peer", "[membrane-intercept] a sibling"),
        ("thought", "[membrane-intercept] your own earlier ask"),
        ("ledger", "OPERATOR DECISIONS"),
    ]
    found: list[dict] = []
    low = ctx
    for name, marker in markers:
        i = low.find(marker)
        if i >= 0:
            # block ends at the next known marker (or end)
            ends = [low.find(m, i + 1) for _, m in markers]
            ends = [e for e in ends if e > i]
            end = min(ends) if ends else len(ctx)
            block = ctx[i:end]
            found.append({"block": name,
                          "chars": len(block),
                          "preview": _clip(block, 160)})
    if not found and ctx.strip():
        found.append({"block": "other", "chars": len(ctx),
                      "preview": _clip(ctx, 160)})
    return found


def _surface_block_text(ctx: str, sec: dict) -> str:
    """The FULL text of one sent block (the totality view's payload side --
    the operator reads what the memories SAY, not the header clip)."""
    markers = ["Recalled memories", "Global memories", "WHERE WE ARE",
               "OPEN INTENTIONS", "PENDING HUMAN REVIEWS",
               "[membrane-watch]", "[membrane-intercept] a sibling",
               "[membrane-intercept] your own earlier ask",
               "OPERATOR DECISIONS"]
    i = ctx.find(sec["preview"][:40]) if sec.get("preview") else -1
    if i < 0:
        return sec.get("preview", "")
    ends = [ctx.find(m, i + 1) for m in markers]
    ends = [e for e in ends if e > i]
    end = min(ends) if ends else len(ctx)
    return ctx[i:end]


def _ctx_gave_summary(ctx: str | None, cap: int = 3) -> list[str]:
    """What the field GAVE this turn: the situation/response line per injected
    memory, in injection order. Recognizes ALL entry formats:

    - legacy: 'Q: <situation>' (pre-upgrade rows)
    - classic: 'situation: <what was asked/happened>'
    - action-first: 'Memory (sim 0.80, [src ...]): <fact> [situation: ...]'
      -> the fact is the payload; the trailing [situation: ...] tag drops.
    """
    if not ctx:
        return []
    out: list[str] = []
    for line in ctx.splitlines():
        s = line.strip()
        if s.startswith("Q: "):
            q = s[3:].strip()
        elif s.startswith("situation: "):
            q = s[11:].strip()
        elif s.startswith("Memory (sim ") and "): " in s:
            q = s.split("): ", 1)[1].strip()
            if "[situation:" in q:
                q = q.split("[situation:", 1)[0].strip()
        elif s.startswith("Memory (sim ") and "]: " in s:
            # seat/stamps ride the header before the payload colon:
            # 'Memory (sim .., [src ..]) [seat: proxy-N]: <fact>'
            q = s.split("]: ", 1)[1].strip()
            if "[situation:" in q:
                q = q.split("[situation:", 1)[0].strip()
        else:
            continue
        if q:
            out.append(q)
    return out[:cap]


# ---- the thought intercept (the agent's own asks, answered as context) ------
# The agent's own text can ADDRESS the membrane ("field state", "membrane:
# learn that X"). Detection runs on assistant-role messages of INCOMING
# requests (my thoughts ride there on the next continuation turn), the answer
# is computed internally, and DELIVERED as a clearly-marked system block on
# the outbound request -- so the interaction appears transparently in opencode
# (the model reads it, cites it, and the [membrane-intercept] tag makes clear
# it is proxy machinery, not user text). A once-only ledger keeps the same
# assistant message from being answered on every re-send. Write lanes: learn
# EXECUTES by default (tagged `agent-note`, audited `origin: agent`, same
# learn_pair guards as teed turns); forget executes in capped index form;
# watch plants/drops respect the board's own caps. `--no-agent-writes`
# reverts learn to REPORT-ONLY (propose, never write).

_THOUGHT_BLOCK_HEAD = ("[membrane-intercept] your own earlier ask was handled "
                       "internally by the membrane proxy (NOT learned, NOT a "
                       "model answer):")

_THOUGHT_CAPTURE_PREFIXES = ("reasoning:", "plan:", "call ", "result ",
                             "tool ")


def _is_thought_ask(q: str) -> bool:
    """Conservative lexical match of the assistant text as an intercept ASK.
    Same frames as the operator matchers; plus the guard that a captured
    reasoning/plan echo is never itself an ask."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    if low.startswith(_THOUGHT_CAPTURE_PREFIXES):
        return False
    if low.startswith(_WATCH_CANON) or low.startswith(_WATCHES_CANON):
        return True                # watch asks: report-only via the watch path
    if _is_codegraph_query(low):
        return True                # codegraph reads: answered from the AST
    return (_is_state_query(low) or _is_recall_query(low)
            or _is_learn_query(low) or _is_forget_query(low))


def _last_line_ask(c: str) -> str | None:
    """The discipline-compliant trailing frame of a LONG or fenced reply.

    Whole-message matching (the guards in _is_thought_ask) misses the
    canonical use: a long reply whose LAST line is the frame. W1a: scan the
    last non-empty line only, and only trust the canonical 'membrane: ...'
    form (or a short bare ask) -- a frame merely MENTIONED mid-prose is
    W3a's business, never a detection here."""
    lines = [ln.strip() for ln in (c or "").splitlines() if ln.strip()]
    if not lines:
        return None
    # a line inside an open code fence is code, never an ask
    if sum(ln.count("```") for ln in lines[:-1]) % 2 == 1:
        return None
    last = " ".join(lines[-1].split())
    low = last.lower()
    if not low or len(low) > 300:
        return None
    if low.startswith(_THOUGHT_CAPTURE_PREFIXES):
        return None
    if not (low.startswith("membrane:") or len(low) <= 60):
        return None
    if not _is_thought_ask(last):
        return None
    return last


def _thought_family(t: str, c: str) -> str:
    """Family of a thought ask; `c` is the raw message (state/recall matchers
    apply their own normalization), `t` the normalized ask text."""
    t_low = t.lower()
    if t_low.startswith((_WATCH_CANON, _WATCHES_CANON)):
        return "watch"
    if _is_codegraph_query(t_low):
        return "codegraph"
    return ("state" if _is_state_query(c) else
            "recall" if _is_recall_query(c) else
            "learn" if _is_learn_query(c) else "forget")


# ---- W3a: frame-lint (discipline violations made visible) -------------------
# The lane discipline says at most ONE canonical frame per reply, on its own
# line, last. Violations self-poison the field (stacked or buried frames miss
# the pre-gate intercept and the whole turn is absorbed as junk memory) -- but
# until now they were INVISIBLE: the machinery just silently missed them.
# The lint makes them countable: a `frame_discipline_violation` audit event
# per offending reply, with the kind ("stacked" = two+ canon frames in one
# reply; "buried" = a canon frame mid-prose that is neither the whole message
# nor the trailing line) and a short preview. Never blocks live traffic.
_CANON_FRAME_PREFIXES = ("membrane:", "field state", "field stats",
                         "membrane query", "membrane learn", "membrane forget")

_DEV_FRAME_WARN = ("[membrane-intercept] frame discipline: {kinds}. At most "
                   "ONE canonical frame per reply, on its own line, LAST. "
                   "Stacked or buried frames miss the pre-gate intercept and "
                   "the whole turn absorbs as junk memory. Re-emit the frame "
                   "alone on your next turn.")


def _auth_headers(provider: str, keys: dict) -> dict:
    """Outgoing auth headers for one provider's API call, from the
    memory-only key map (srv.cloud_keys). Unknown provider / no key -> {} (a
    rung without a key can't answer; the ladder treats it as DOWN)."""
    from .auth import _headers
    key = str(keys.get(provider) or "")
    return _headers(provider, key) if key else {}


def _dev_frame_warning(kinds: list[str]) -> str:
    """W3b: the one-shot --dev warning text for the violation kinds."""
    return _DEV_FRAME_WARN.format(kinds=" and ".join(kinds))


def _dev_warn_take(srv) -> str | None:
    """Consume the pending one-shot --dev warning (exactly once)."""
    w = getattr(srv, "_dev_warn", None)
    if w:
        srv._dev_warn = None
    return w


def _frame_lint(srv, text: str, source: str = "assistant") -> int:
    """Count discipline violations in one assistant text; audit each.

    - stacked: two+ canonical frames anywhere in the reply (only the first
      is servable; the rest miss the pre-gate intercept and poison learn).
    - buried: a line that lexically LOOKS like a canon frame sits mid-prose
      (not first, not last) in a multi-line reply -- whole-message matching
      will miss it and the frame never gets served.
    Never raises; returns the violation count (0 = clean)."""
    if not text or "```" in text:
        # fenced code legitimately contains frame-like lines; not prose
        return 0
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0
    canon = [ln for ln in lines
             if " ".join(ln.split()).lower().startswith(_CANON_FRAME_PREFIXES)]
    violations = 0
    kinds: list[str] = []
    if len(canon) >= 2:
        kinds.append("stacked")
        violations += 1
    if len(lines) > 2 and len(canon) == 1:
        idx = lines.index(canon[0])
        if 0 < idx < len(lines) - 1:
            kinds.append("buried")
            violations += 1
    if not violations:
        return 0
    mem = getattr(srv, "membrane", None)
    fw = getattr(srv, "_frame_wait", None)
    if fw is None:
        fw = srv._frame_wait = {}
    for k in kinds:
        fw[f"viol_{k}"] = fw.get(f"viol_{k}", 0) + 1
    fw["violations"] = fw.get("violations", 0) + 1
    # W3b: in --dev mode the violation also plants a ONE-SHOT warning that
    # rides the next model-bound request (trains the habit at the source;
    # never blocks -- live traffic is already served by the time we lint).
    if getattr(srv, "dev", False):
        srv._dev_warn = _dev_frame_warning(kinds)
    try:
        if mem is not None and getattr(mem, "audit_path", ""):
            with open(mem.audit_path, "a") as f:
                f.write(json.dumps({
                    "event": "frame_discipline_violation",
                    "kinds": kinds, "origin": source,
                    "preview": " | ".join(canon)[:200]}) + "\n")
    except Exception:
        pass
    return violations


def _frame_wait_bump(srv, key: int, buried: bool) -> None:
    """W1b counters: unique asks seen (a resend of an UNDELIVERED ask re-adds
    the same key -- sets keep it honest), buried = found via the last-line
    scan. In-memory; the re-ask event also writes the audit trail."""
    fw = getattr(srv, "_frame_wait", None)
    if fw is None:
        fw = srv._frame_wait = {}
    fw.setdefault("asks", set()).add(key)
    if buried:
        fw.setdefault("buried", set()).add(key)


def _frame_wait_summary(srv) -> dict:
    """The W1b/W3a numbers for the state surfaces (sets -> counts)."""
    fw = getattr(srv, "_frame_wait", None)
    if not fw:
        return {}
    out = {"unique_asks": len(fw.get("asks", ())),
           "served": int(fw.get("served", 0)),
           "re_asks": int(fw.get("re_asks", 0)),
           "buried": len(fw.get("buried", ())),
           "hot": bool(fw.get("hot"))}
    # W3a: the discipline counters ride the same summary
    if fw.get("violations"):
        out["violations"] = int(fw["violations"])
        out["viol_stacked"] = int(fw.get("viol_stacked", 0))
        out["viol_buried"] = int(fw.get("viol_buried", 0))
    return out


def _thought_answer(srv, fam: str, text: str) -> str:
    """The internal answer for a thought ask.

    Write lanes, as of the agent-writes change:
    - LEARN EXECUTES (ON by default; `--no-agent-writes` reverts to the old
      report-only covenant): the model's earned lessons go through the same
      guards as teed turns (learn_pair), tagged `agent-note`, audited
      `origin: agent`.
    - FORGET EXECUTES in the capped index form (_agent_forget: strict
      parser, AGENT_FORGET_LIMIT cap, `origin: agent` audit); wordy forms
      fall through to a PROPOSAL, never a deletion.
    - WATCH plants/drops execute (the board's own caps are the guard).
    - STATE/RECALL are reads, always served.
    An executing forget marks the delivery ledger HERE, at execution time:
    the same assistant text re-arrives on every continuation, and after a
    delete the store indexes SHIFT -- serving the re-send would re-delete
    by stale numbers. Executing learns mark too (a dup ask would otherwise
    re-learn on every resend; dedup would catch it, but the report would
    still lie). Proposals never mark (idempotent to re-propose)."""
    if fam == "state":
        return _state_report(srv)
    if fam == "recall":
        return _recall_report(srv, _recall_topic(text))
    if fam == "codegraph":
        return _codegraph_answer(srv, text)
    if " ".join(text.split()).lower().startswith((_WATCH_CANON, _WATCHES_CANON)):
        return _watch_set_from_thought(srv,
                                       " ".join(text.split()).lower()
                                       [len(_WATCH_CANON):].strip())
    if fam == "forget":
        spec = _after_frame(text, (_FORGET_CANON,))
        if not spec:
            t = text.strip()
            spec = t[len("membrane:"):].strip() \
                if t.lower().startswith("membrane:") else t
        answer = _agent_forget(srv, spec)
        if "Nothing removed" not in answer and "nothing" not in answer \
                and "never delete" not in answer:
            ledger = getattr(srv, "_served_thought_asks", None)
            if ledger is None:
                ledger = srv._served_thought_asks = {}
            ledger[hash(("forget", " ".join(text.split()).lower()))] = True
        return answer
    if fam == "learn":
        payload = (_after_frame(text, (_LEARN_CANON,)) or
                   _after_frame(text, _LEARN_FRAMES) or
                   _after_frame(text, _LEARN_SOFT_FRAMES))
        if payload is None:
            # no frame matched cleanly; a bare "membrane: learn that" must
            # NOT fall through to learning the frame itself as the payload.
            t = " ".join(text.split())
            payload = t[len("membrane: learn that"):].strip() \
                if t.lower().startswith("membrane: learn that") else ""
        if not getattr(srv, "agent_writes", True):
            # the old covenant, one flag away: propose, never write
            p = (payload or "").strip()
            return ("[membrane-intercept] learn is report-only "
                    "(--no-agent-writes): the field is written by operator "
                    "turns. Re-ask as a USER message 'membrane: learn that "
                    "<fact>' to write it."
                    + (f"\n    PROPOSED: {_clip(p, 280)}" if p else ""))
        answer = _agent_learn(srv, payload)
        if "Nothing" not in answer and "nothing" not in answer:
            ledger = getattr(srv, "_served_thought_asks", None)
            if ledger is None:
                ledger = srv._served_thought_asks = {}
            ledger[hash(("learn", " ".join(text.split()).lower()))] = True
        return answer
    return (f"[membrane-intercept] your {fam} ask was read but NOT executed: "
            "the field is only written by operator turns. Re-ask with the "
            "canonical form, or use index form ('membrane: forget #3-7') "
            "to delete cells yourself.")


def _assistant_thought_asks(srv, body: dict) -> list:
    """(fam, normalized_text, buried) for every PENDING intercept ask in
    assistant-role messages of the incoming request. Already-served asks
    (the delivery ledger) are skipped -- but NOT silently: a repeat of an
    undelivered ask bumps the W1b re-ask counter (the system carrying the
    timing friction instead of just teaching it). The same assistant message
    re-arrives on every continuation, so marking happens ONLY at delivery --
    an undelivered ask stays pending. `buried` marks a discipline-compliant
    trailing frame found by the last-line scan (W3a's telemetry input)."""
    ledger = getattr(srv, "_served_thought_asks", None) or {}
    out: list = []
    seen: set = set()
    for m in reversed(body.get("messages") or []):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        c = m.get("content")
        if not isinstance(c, str) or not c.strip():
            continue
        t = " ".join(c.split())
        t_low = t.lower()
        buried = False
        if not _is_thought_ask(t):
            buried = True
            t2 = _last_line_ask(c)
            if t2 is None:
                continue
            t = t2
            t_low = t.lower()
            fam = _thought_family(t, t2)
        else:
            fam = _thought_family(t, c)
        key = hash((fam, t_low))
        if key in seen:
            continue
        seen.add(key)
        if ledger.get(key):
            # the undelivered ask re-arriving: count it (W1b), stay silent --
            # the marker in the recall block is what the model sees (W1a)
            try:
                _frame_wait_bump(srv, key, buried)
                fw = getattr(srv, "_frame_wait", None)
                if fw is not None:
                    fw["re_asks"] = fw.get("re_asks", 0) + 1
                    if fw.get("re_asks", 0) >= 2:
                        fw["hot"] = True
                mem = getattr(srv, "membrane", None)
                if mem is not None and getattr(mem, "audit_path", ""):
                    with open(mem.audit_path, "a") as f:
                        f.write(json.dumps({
                            "event": "frame-wait", "fam": fam,
                            "buried": buried, "ask": t[:120]}) + "\n")
            except Exception:
                pass
            continue
        _frame_wait_bump(srv, key, buried)
        out.append((fam, t, buried))
    return out


def _serve_thoughts_served(srv, asks: list) -> None:
    """Mark asks as answered in the once-only ledger (in-memory; a proxy
    reboot forgets it -- the worst case is one repeated answer). Accepts
    (fam, text) or (fam, text, buried) triples; a delivered ask also counts
    in the W1b 'served' total."""
    ledger = getattr(srv, "_served_thought_asks", None)
    if ledger is None:
        ledger = srv._served_thought_asks = {}
    for a in asks:
        fam, t = a[0], a[1]
        ledger[hash((fam, t.lower()))] = True
    try:
        fw = getattr(srv, "_frame_wait", None)
        if fw is None:
            fw = srv._frame_wait = {}
        fw["served"] = fw.get("served", 0) + len(asks)
    except Exception:
        pass


def _thought_block(asks: list) -> str | None:
    """The marked context block for the outbound request (injected next to
    the RAG memories; clearly tagged so opencode shows it as machinery)."""
    if not asks:
        return None
    parts = [_THOUGHT_BLOCK_HEAD]
    for fam, ans in asks:
        parts.append(f"\n[{fam}] {ans.strip()}")
    parts.append("\n[membrane-intercept] end of internal answer. Reference "
                 "this when relevant; do not repeat it verbatim to the user. "
                 "If you already answered the user using a fact this corrects, "
                 "fix it on your next turn")
    parts.append("\nTIMING: if you emitted a canonical frame whose answer is "
                 "NOT in this block, the answer lands on your NEXT turn "
                 "-- emit once, wait, act; do not re-ask")
    return "\n".join(parts)


def _sse_line(obj) -> bytes:
    return ("data: " + json.dumps(obj) + "\n\n").encode()


def _openai_sse_chunks(model: str, content: str, piece: int = 128,
                       reasoning: bool = False):
    """One assistant text as OpenAI-style SSE chunks (the internal state
    answer, or the [membrane] gate monologue). ``reasoning=True`` emits the
    text as reasoning deltas (the thinking channel) instead of content."""
    key = "reasoning" if reasoning else "content"
    id_ = "chatcmpl-membrane-state"
    head = {"id": id_, "object": "chat.completion.chunk", "created": 0,
            "model": model}
    yield _sse_line({**head, "choices": [{"index": 0,
                                          "delta": {"role": "assistant"},
                                          "finish_reason": None}]})
    for i in range(0, len(content), piece):
        yield _sse_line({**head, "choices": [{"index": 0,
                                              "delta": {key: content[i:i + piece]},
                                              "finish_reason": None}]})
    yield _sse_line({**head, "choices": [{"index": 0, "delta": {},
                                          "finish_reason": "stop"}]})
    yield b"data: [DONE]\n\n"


def _known_embed_names(backend: str) -> list:
    """Ollama model names whose tag looks like an embedding model."""
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return ["mxbai-embed-large", "nomic-embed-text"]
    return [m.get("name", "") for m in tags.get("models", [])
            if any(k in m.get("name", "").lower() for k in _EMBED_MARKERS)]


def _probe_embedder(backend: str, model: str):
    """An OllamaEmbedder for ``model`` iff a real embed call succeeds."""
    try:
        # 180s: cold llama-server startup (model load from flash) can take
        # minutes on edge hardware (the agdroid measured ~30s first embed);
        # steady-state is ~50ms. The timeout only binds the first call.
        emb = OllamaEmbedder(base_url=backend, model=model, timeout=180)
        emb.embed(["probe"])
        return emb
    except Exception as e:
        print(f"[opencode] {model} cannot embed ({_one_line(e)})", file=sys.stderr)
        return None


def _one_line(e: Exception) -> str:
    import re
    return re.sub(r"\s+", " ", str(e))[:140]


def _port_free(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _convert_old_commons(srv, path: str) -> int:
    """One-time: the old global-field file's curated notes become commons
    claims (claims.convert_global); flagged in origin_stats."""
    fld = _efield_of(srv)
    p = Path(path)
    if fld is None or not p.is_file() or fld._ostats.get("global_converted"):
        return 0
    try:
        from types import SimpleNamespace
        from .claims import convert_global
        recs = json.loads(p.read_text() or "[]")
        n = convert_global(fld.b, [SimpleNamespace(situation=r.get("situation", ""),
                                                   action=r.get("action", ""))
                                   for r in recs if isinstance(r, dict)], fld.graph_fn())
        fld._ostats["global_converted"] = True
        fld.b.upsert("origin_stats", fld._okey, fld._ostats)
        print(f"[opencode] commons: {n} notes of {p} converted to commons claims",
              file=sys.stderr)
        return n
    except Exception as e:
        print(f"[opencode] commons conversion skipped ({_one_line(e)})", file=sys.stderr)
        return 0


def _peer_heartbeat(srv) -> None:
    """Slice 4: append this membrane's heartbeat every 60s so peers see it
    as live. Failures are silent (peers are optional)."""
    while not srv._gstop.wait(60.0):
        try:
            srv.gpeers.register(srv.listen_port, str(srv.membrane.field_path))
        except Exception:
            pass


def _pick_listen_port(preferred: int) -> int:
    """A port the proxy can actually bind.

    Explicit --listen is respected but not sacred: if it (or the default
    11500) is taken -- typically by ANOTHER project's membrane session, since
    the port is global while the field is per-project -- the probe walks up
    from it. The user-visible default stays 11500: only when several
    membranes run at once does anyone land above it.
    """
    port = preferred
    for _ in range(64):
        if _port_free(port):
            return port
        port += 1
        if port > 65535:
            break
    raise SystemExit(
        f"[opencode] no free port found at or above {preferred} "
        f"(probed 64); pass --listen explicitly")


def _pick_serve_port(preferred: int) -> int:
    """The port `opencode serve` will bind (same walk, own flag in the error)."""
    port = preferred
    for _ in range(64):
        if _port_free(port):
            return port
        port += 1
        if port > 65535:
            break
    raise SystemExit(
        f"[opencode] no free serve port found at or above {preferred} "
        f"(probed 64); pass --serve-port explicitly")


def _lan_ip() -> str:
    """The address a LAN peer would reach this seat on: the OS's own route
    choice to the outside (a UDP 'connect' writes no packet and binds no
    port -- it is read-only). localhost when nothing else applies."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def _serve_credentials(path: str, user: str, password: str | None) -> tuple[str, str, bool]:
    """The basic-auth pair the headless serve speaks (user, password, first_boot).

    `opencode serve` reads OPENCODE_SERVER_USERNAME/_PASSWORD (a password
    without a username serves under the default 'opencode' user) -- the same
    pair the companion app types. A generated password is written ONCE
    (0600, beside the field) and reused on every later boot so a device's
    saved connection survives seat restarts; --serve-pass overrides both,
    --serve-user overrides the user (also persisting it).
    """
    import secrets
    if password:
        return user or "opencode", password, False
    import secrets
    p = Path(path)
    d = None
    try:                     # a kept file wins -- corrupt or missing regenerates
        if p.is_file():
            d = json.loads(p.read_text())
    except Exception:
        d = None
    try:
        if not (d and d.get("user") and d.get("password")):
            d = {"user": user or "opencode",
                 "password": secrets.token_urlsafe(15)}
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, json.dumps(d).encode())
            finally:
                os.close(fd)
            os.replace(tmp, path)            # a reader never sees half a file
            os.chmod(path, 0o600)
            return d["user"], d["password"], True
        return d["user"], d["password"], False
    except Exception as e:
        # no writable home for the file: ephemeral credentials (printed
        # once, gone on restart -- never a silent all-open serve)
        print(f"[opencode] serve auth NOT persisted ({_one_line(e)}); "
              "this boot's pair is ephemeral", file=sys.stderr)
        return user or "opencode", secrets.token_urlsafe(15), True


def _serve_board(serve_port: int, serve_host: str, user: str, password: str,
                 first_boot: bool, cred_path: str) -> None:
    """One board line for the native serve surface: the URL a client
    connects to."""
    addr = "localhost" if serve_host in ("127.0.0.1", "localhost") else _lan_ip()
    shown = password if first_boot else f"<saved in {cred_path}>"
    print(f"[opencode] serve: http://{addr}:{serve_port}  (user {user}, "
          f"password {shown}; basic auth -- clients type these, or the "
          "companion app fills the URL + pair)",
          file=sys.stderr)
    if first_boot:
        print(f"[opencode] serve: credentials persisted at {cred_path} "
              "(0600); they survive restarts", file=sys.stderr)


def _audit_count(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


# ---- total capture: plans, tool calls, tool results, reasoning ---------------
# Everything the model emits between your prompts is evidence: its prose and
# reasoning (discourse events), each tool call and -- on the NEXT request, as a
# role:"tool" message -- its result (one observation event per call). The
# capture hooks hand them to srv.capturer (membrane/capture.py).

_THINK_KEYS = ("reasoning", "reasoning_content", "thinking")



def _msg_reasoning(msg: dict) -> str:
    for kk in _THINK_KEYS:
        v = msg.get(kk)
        if isinstance(v, str) and v.strip():
            return v.strip()
    c = msg.get("content")
    if isinstance(c, list):                      # anthropic-style thinking blocks
        return "\n".join(b.get("thinking", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "thinking"
                         and isinstance(b.get("thinking"), str)).strip()
    return ""


def _delta_reasoning(d: dict) -> str:
    for kk in _THINK_KEYS:
        v = d.get(kk)
        if isinstance(v, str):
            return v
    return ""


def _resp_has_content(resp_bytes: bytes | None) -> bool:
    """Does an OpenAI-shaped chat completion carry an ANSWER (content or
    tool calls)? A 200 with neither is a rung down in disguise (observed:
    openrouter free tiers answer 200-with-empty-body) -- the caller fails
    the rung over and walks the ladder. Unparseable bodies count as EMPTY."""
    if not resp_bytes:
        return False
    try:
        obj = json.loads(resp_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    ch = (obj.get("choices") or [{}])[0]
    msg = ch.get("message") or obj.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str) and c.strip():
        return True
    if isinstance(c, list) and any(
            (isinstance(b, dict) and str(b.get("text") or "").strip())
            for b in c):
        return True
    if msg.get("tool_calls") or ch.get("finish_reason") == "tool_calls":
        return True
    for kk in _THINK_KEYS:                    # reasoning-only answer: content
        v = msg.get(kk)                       # is empty but the turn spoke
        if isinstance(v, str) and v.strip():
            return True
    return False


def _render_calls(calls: dict) -> str:
    """Accumulated tool-call deltas -> one compact line per call."""
    lines = []
    for idx in sorted(calls):
        c = calls[idx]
        fn = c.get("function") or {}
        lines.append(f"{fn.get('name') or '?'}({(fn.get('arguments') or '').strip()})")
    return "\n".join(lines)


def _tool_result_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _mark_field_degraded(srv, err: str) -> None:
    """One failed capture marks the seat field-degraded for 90s: the
    receipt lane stands down (never replay from a cache the field could
    not confirm), until a clean capture re-arms the lane."""
    if srv is None:
        return
    srv._field_degraded = time.time() + 90.0
    srv._field_degraded_err = err


def srv_ref(mem):
    """The proxy server backref off a membrane (None outside the proxy)."""
    return getattr(mem, "server_ref", None)


def _capture_plan_and_calls(mem, query: str, content: str, calls: dict) -> None:
    """One response turn's tool calls: each becomes an observation (the full
    arguments) and registers in mem._pending_tools so its RESULT (next
    request) completes it. File-touching calls (edit/write) also mark the
    codegraph dirty."""
    cap = getattr(srv_ref(mem), "capturer", None)
    if cap is not None and calls:
        cap.on_calls(query, calls)          # full arguments, idempotent by call id
    if calls:
        for idx, c in sorted(calls.items()):
            fn = c.get("function") or {}
            name = fn.get("name") or "?"
            args = (fn.get("arguments") or "").strip()
            cid = c.get("id")
            if cid:
                mem._pending_tools[cid] = {"query": query, "name": name, "args": args}
        _codegraph_note_dirty(srv_ref(mem), [
            _tool_arg_path(c.get("function") or {})
            for c in calls.values()])


def _capture_tool_results(srv, body: dict) -> None:
    """Scan the incoming request for role:'tool' messages: each result, paired
    with its pending call, completes that call's observation (unclipped).
    Runs before the gate."""
    mem = srv.membrane
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        cid = m.get("tool_call_id")
        if not cid or m.get("role") == "assistant":
            continue
        rec = mem._pending_tools.pop(cid, None)
        if rec is None:
            continue                      # pre-boot result or unknown id
        result = _tool_result_text(m)
        cap = getattr(srv, "capturer", None)
        if cap is not None:               # the UNCLIPPED result, by call id
            cap.on_tool_result(cid, rec["name"], rec["args"], result, rec["query"])
        rec["result"] = result            # the receipt lane reads THIS
        srv._tool_results[cid] = rec      # result-side cache (bounded)
        if len(srv._tool_results) > 64:   # the bound: drop the oldest
            srv._tool_results.pop(next(iter(srv._tool_results)))


def _chat_capable_models(backend: str) -> list:
    """Models opencode may SEE and select: the opencode-compatibility screen
    (completion + tools caps, non-chat name markers) — the same covenant the
    router routes under, applied to the picker, /v1/models and the default
    model choice. Cloud entries (size 0 / remote_host) always pass."""
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return []
    out = []
    for m in tags.get("models", []) or []:
        name = m.get("name") or ""
        if not name:
            continue
        is_cloud = bool(m.get("remote_host")) or not m.get("size")
        if is_cloud:
            out.append(name)
            continue
        if _is_non_chat(name):       # unconditional: the name screen is a
            continue                 # floor; caps only ADD restrictions
        caps = list(m.get("capabilities") or [])
        if caps and not all(c in caps for c in _OC_MIN_CAPS):
            continue
        elif _is_non_chat(name):
            continue
        sz = int(m.get("size") or 0)
        if sz and sz / 2**30 > _OC_MAX_LOCAL_GIB:
            continue
        out.append(name)
    return out


def _pick_default_model(models: list, preferred: str | None) -> str | None:
    if preferred:
        for m in models:
            if m == preferred or m.split(":")[0] == preferred:
                return m
    for want in ("glm", "qwen3", "qwen2.5-coder", "llama3.1", "mistral", "gemma"):
        for m in models:
            if m.startswith(want):
                return m
    return models[0] if models else None


def _pick_idle_cortex_model(backend: str, preferred: str | None) -> str:
    """The idle lane's local default: a LOCAL chat-capable model resolved
    from ollama's LIVE tag list (the old hardcoded 'granite3.3:8b' 404s
    forever on backends that never had it -- one dead compact cycle per
    idle period). Cloud rungs are excluded (a background seat must never
    burn cloud tokens -- the idle-cortex split's own covenant), non-chat
    markers are screened. ``preferred`` wins if present and local; the
    hardcoded name survives only as the last resort."""
    fallback = preferred or "granite3.3:8b"
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return fallback
    local = []
    for m in tags.get("models", []) or []:
        name = m.get("name") or ""
        if not name or _is_non_chat(name):
            continue
        if bool(m.get("remote_host")) or not m.get("size"):
            continue                  # a cloud rung: not the idle lane's model
        caps = list(m.get("capabilities") or [])
        if caps and not all(c in caps for c in _OC_MIN_CAPS):
            continue
        local.append(name)
    if not local:
        return fallback
    if preferred:
        for m in local:
            if m == preferred or m.split(":")[0] == preferred:
                return m
    for want in ("granite", "glm", "qwen3", "qwen2.5-coder", "qwen2.5",
                 "llama", "mistral", "gemma", "deepseek"):
        for m in local:
            if m.startswith(want):
                return m
    return local[0]


def _audit_tail(path, k: int = 24):
    """Last k ticker-worthy audit records ({verdict,sim,q}) + the MONOTONIC
    total (the page compares totals to detect new verdicts, not list lengths).
    Maps the three record shapes (decision / outcome / contest), same as the
    world proxy's _audit_tail."""
    out: list = []
    try:
        with open(path, "rb") as f:
            lines = f.readlines()
    except OSError:
        return [], 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        ev = rec.get("event")
        if ev == "decision":
            tag, sim, q = rec.get("verdict"), rec.get("sim"), rec.get("query", "")
        elif ev == "outcome":
            oc = rec.get("outcome", "outcome")
            tag = {"demote": "demote", "re-ask": "re-ask"}.get(oc, oc)
            sim, q = rec.get("sim"), rec.get("query", "")
        elif ev == "contest":
            tag = "contest-restore" if rec.get("restore") else "contest"
            sim, q = None, rec.get("reason") or ""
        else:
            continue
        if not tag:
            continue
        out.append({"verdict": str(tag),
                    "sim": (round(float(sim), 2) if isinstance(sim, (int, float)) else None),
                    "q": str(q)[:48]})
    return out[-k:], len(out)


def _efield_state_rows(srv, fld, m: int = 400) -> list[dict]:
    """The m brightest live states by mass (the cloud's rows)."""
    rows = [d for _, d in fld.b.scan("entity_states", {"origin": fld.origin, "tier": "live"})
            if d.get("state")]
    rows.sort(key=lambda d: -int(d.get("accr") or 0))
    return rows[:m]


def _entity_points(srv, fld, m: int = 400) -> list[dict]:
    """The viewer's cloud on the entity field: the top-m live states by mass,
    PCA 3D in the shared universe projection (the basis fit once per
    process, the entities of the graph co-registered), each point carrying
    its eid, kind (the color), mass (the brightness) and whether it holds a
    conflict."""
    from .universe import UniverseProjection
    rows = _efield_state_rows(srv, fld, m)
    if not rows:
        return []
    V = np.asarray([d["state"] for d in rows], dtype=float)
    proj = getattr(srv, "_universe_proj", None)
    coords = None
    if proj is not None and proj.ready():
        coords = proj.project(V)
    if coords is None:
        mu = V.mean(0)
        basis = getattr(srv, "_ef_basis", None)
        if basis is None and len(rows) >= 4:
            _, _, Vt = np.linalg.svd(V - mu, full_matrices=False)
            basis = srv._ef_basis = Vt[:3]
        coords = (V - mu) @ basis.T if basis is not None else np.zeros((len(rows), 3))
    return [{"p": [round(float(c), 4) for c in coords[i]], "eid": d["eid"],
             "tags": [d.get("kind") or "?"], "mass": int(d.get("accr") or 0),
             "safe": not d.get("conflicts"), "reask": 0,
             "contest": len(d.get("conflicts") or [])}
            for i, d in enumerate(rows)]


class MembraneOpenAIHandler(RAGProxyHandler):
    """The RAG handler + the OpenAI-compatible surface: /v1/models,
    /v1/chat/completions (the RAG gate + the internal state/math intercept),
    /v1/embeddings (the field's own embedder), /membrane/state. Everything
    else forwards to Ollama."""

    # ---- routing ----
    def do_GET(self):
        # THE IDLE CLOCK (the orphaned-proxy fix): every request resets
        # the idle-exit timer -- an active seat never trips it
        self.server._last_request_ts = time.time()
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/v1/models/"):
            self._v1_models(); return
        if path in ("/membrane/state", "/state"):
            self._membrane_state(); return
        if path == "/membrane/entity-track":
            self._entity_track_list(); return
        if path == "/membrane/intend":
            self._membrane_intend(); return
        if path == "/membrane/entity-related":
            self._entity_related(); return
        if path == "/membrane/ask":
            self._membrane_ask(); return
        if path == "/membrane/peer/seats":
            self._peer_seats(); return
        if getattr(self.server, "viewer", False):
            if path in ("/viewer", "/viewer/", "/universe", "/universe/"):
                # THE VIEWER RETIRED (the operator's ruling): the old dev
                # viewer's toy-world surfaces (the POV roster, the story
                # panel) die with the toy world; /viewer now serves the
                # universe map (the field as a universe, the graph
                # overlaid, google-map controls).
                self._serve_universe(); return
            if path in ("/three.min.js", "/PressStart2P.woff2", "/VT323.woff2"):
                self._serve_asset(path.lstrip("/")); return
            if path in ("/neighbors", "/neighbors/"):
                self._neighbors(); return
        if getattr(self.server, "viewer", False):
            if path in ("/universe", "/universe/"):
                self._serve_universe(); return
            if path == "/membrane/map/cloud":
                self._map_cloud(); return
            if path == "/membrane/map/entities":
                self._map_entities(); return
            if path == "/membrane/map/axes":
                self._map_axes(); return
            if path == "/membrane/map/axis":
                # the thin alias: the axis query params ride /cloud (the
                # contract's shape; the reproject uses cloud+entities)
                self._map_cloud(); return
        super().do_GET()

    def do_POST(self):
        self.server._last_request_ts = time.time()
        path = self.path.split("?")[0]
        if path == "/v1/chat/completions":
            self._v1_chat(); return
        if path == "/v1/embeddings":
            self._v1_embeddings(); return
        if path == "/membrane/delegate":
            self._membrane_delegate(); return
        if path == "/membrane/learn":
            self._membrane_learn(); return
        if path == "/membrane/forget":
            self._membrane_forget(); return
        if path == "/membrane/codegraph":
            self._membrane_codegraph(); return
        if path == "/membrane/entity-track":
            self._entity_track_set(); return
        if path == "/membrane/entity-track/remove":
            self._entity_track_remove(); return
        if path == "/membrane/entity-track/list":
            self._entity_track_list(); return
        if path == "/membrane/entity/add":
            self._entity_add(); return
        if path == "/membrane/peer/post":
            self._peer_post(); return
        if path == "/membrane/junk-cut":
            self._junk_cut_set(); return
        if path == "/membrane/entity/edit":
            self._entity_edit(); return
        if path == "/membrane/entity/inherit":
            self._entity_inherit(); return
        if path == "/membrane/entity/remove":
            self._entity_remove(); return
        if path == "/membrane/run":
            self._membrane_run(); return
        if path == "/membrane/intend":
            self._membrane_intend(); return
        if path.startswith("/membrane/"):
            self._json_out({"error": "read-only; GET /membrane/state"}); return
        super().do_POST()

    def _json_err_raw(self, code: int, msg: str) -> None:
        out = json.dumps({"error": {"message": msg, "type": "membrane_error"}}
                         ).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _json_out(self, obj) -> None:
        out = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    # ---- the dev viewer (field view; world controls hidden by body.dev) -----
    def _serve_viewer(self):
        import os, time
        p = os.path.join(os.path.dirname(__file__), "viewer.html")
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self._json_err_raw(404, "viewer.html not found"); return
        cached = getattr(self.server, "_viewer_cache", None)
        if cached is None or cached[0] != mtime:
            with open(p, "rb") as f:
                html = f.read()
            self.server._viewer_cache = (mtime, html)
        else:
            html = cached[1]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    # ---- THE UNIVERSE MAP (the entity graph + the field, one 3D scene) --
    def _universe_projection(self):
        """The projection, fit once per process from BOTH layers' vectors
        (the field's brightest entity states + the graph entities -- one
        universe, one fit, co-registered)."""
        from .universe import UniverseProjection
        proj = getattr(self.server, "_universe_proj", None)
        if proj is not None and proj.ready():
            return proj
        fld = _efield_of(self.server)
        est = getattr(self.server, "entity_store", None)
        vecs = []
        if fld is not None:
            vecs = [d["state"] for d in _efield_state_rows(self.server, fld, m=600)]
        if est is not None:
            for e in est.docs.values():
                v = getattr(e, "position", None) or getattr(e, "embedding", None)
                if v and (not vecs or len(v) == len(vecs[0])):
                    vecs.append(v)
        proj = UniverseProjection()
        proj.fit(vecs)
        self.server._universe_proj = proj
        return proj

    def _serve_universe(self):
        """The universe map: the field as a universe (stars = memories,
        constellations = entities) with google-map-like controls."""
        import os
        p = os.path.join(os.path.dirname(__file__), "universe.html")
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self._json_err_raw(404, "universe.html not found"); return
        cached = getattr(self.server, "_universe_cache", None)
        if cached is None or cached[0] != mtime:
            with open(p, "rb") as f:
                html = f.read()
            self.server._universe_cache = (mtime, html)
        else:
            html = cached[1]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _map_axes_basis(self, qs) -> "object | None":
        """The custom basis from the axis query params (x/y/z = candidate
        ids): resolve the ids to vecs, orthonormalize, return the basis
        (or None = the PCA default)."""
        import numpy as _np
        from urllib.parse import parse_qs
        q = parse_qs(qs or "")
        ids = [q.get(a, [None])[0] for a in ("x", "y", "z")]
        if not any(i and i != "__pca__" for i in ids):
            return None
        est = getattr(self.server, "entity_store", None)
        from .universe import axis_candidates
        cands = axis_candidates(self.server, est)
        by_id = {c["id"]: c["vec"] for c in cands}
        vecs = []
        for i in ids:
            v = by_id.get(i)
            if v:
                vecs.append(_np.asarray(v, dtype=float))
            else:
                vecs.append(None)
        if not any(v is not None for v in vecs):
            return None
        # the basis: the non-null candidates + the PCA fill (orthonormal
        # via Gram-Schmidt against the stored mu)
        proj = self._universe_projection()
        mu = proj._mu
        rows = []
        for v in vecs:
            if v is None:
                rows.append(None)
                continue
            r = v - mu
            n = float(_np.linalg.norm(r))
            rows.append(r / n if n > 1e-12 else None)
        # orthonormalize (drop the nulls, pad with the PCA basis)
        basis = []
        for v in rows:
            if v is None:
                continue
            w = v.copy()
            for b in basis:
                w = w - _np.asarray(b) * float(_np.dot(w, b))
            n = _np.linalg.norm(w)
            if n > 1e-9:
                basis.append((w / n).tolist())
        while len(basis) < 3 and proj._basis is not None:
            for row in proj._basis:
                if len(basis) >= 3:
                    break
                b = _np.asarray(row, dtype=float)
                for done in basis:
                    b = b - _np.asarray(done, dtype=float) * float(
                        _np.dot(b, done))
                n = _np.linalg.norm(b)
                if n > 1e-9:
                    basis.append((b / n).tolist())
        return _np.array(basis[:3], dtype=float)

    def _map_cloud(self):
        """GET /membrane/map/cloud: the field's brightest entity states
        (PCA-projected in the shared universe basis, a star per state,
        brightness = mass) + conflicts. The axis query params (x/y/z
        candidate ids) re-project."""
        import numpy as _np
        fld = _efield_of(self.server)
        if fld is None:
            self._json_out({"stars": [], "scale": 0})
            return
        proj = self._universe_projection()
        if not proj.ready():
            self._json_out({"stars": [], "scale": 0})
            return
        rows = _efield_state_rows(self.server, fld, m=400)
        if not rows:
            self._json_out({"stars": [], "scale": 0})
            return
        V = [d["state"] for d in rows]
        # THE CUSTOM AXIS (the instrument): the query params' basis
        # replaces the PCA default for this request
        custom = self._map_axes_basis(
            self.path.split("?", 1)[-1] if "?" in self.path else "")
        if custom is not None:
            coords = _np.array([(_np.asarray(v, dtype=float) - proj._mu)
                                @ custom.T * proj._scale for v in V],
                               dtype=float)
        else:
            coords = proj.project(V)
        out = []
        if coords is not None:
            for d, c in zip(rows, coords):
                out.append({
                    "p": [round(float(c[0]), 2), round(float(c[1]), 2),
                          round(float(c[2]), 2)],
                    "eid": d.get("eid"),
                    "tags": [d.get("kind") or "?"],
                    "mass": int(d.get("accr") or 0),
                    "safe": not d.get("conflicts"),
                    "contest": len(d.get("conflicts") or []),
                })
        self._json_out({"stars": out, "radius": 50.0})

    def _map_entities(self):
        """GET /membrane/map/entities: the graph (nodes + edges), the
        entities projected in the SAME basis/scale as the cloud (the
        constellation co-registered with the stars)."""
        import numpy as _np
        from .entities import MultiParentOps
        proj = self._universe_projection()
        if not proj.ready():
            self._json_out({"nodes": [], "edges": [], "scale": 0})
            return
        est = getattr(self.server, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        ents = list(est.docs.values())
        vecs = [getattr(e, "position", None) or getattr(e, "embedding", None)
                for e in ents]
        pairs = [(e, v) for e, v in zip(ents, vecs) if v and len(v) == 1024]
        # THE CUSTOM AXIS (the same instrument as the cloud's)
        import numpy as _np
        custom = self._map_axes_basis(
            self.path.split("?", 1)[-1] if "?" in self.path else "")
        if custom is not None:
            coords = _np.array([(_np.asarray(v, dtype=float) - proj._mu)
                                @ custom.T * proj._scale
                                for _e, v in pairs], dtype=float) \
                if pairs else None
        else:
            coords = proj.project([v for _e, v in pairs])
        nodes = []
        eid_set = set()
        if coords is not None:
            for (e, _v), c in zip(pairs, coords):
                props = e.properties or {}
                nodes.append({
                    "id": e.eid, "cls": e.cls,
                    "label": (e.describe() or e.eid)[:80],
                    "p": [round(float(c[0]), 2), round(float(c[1]), 2),
                          round(float(c[2]), 2)],
                    "status": props.get("status"),
                    "curiosity": props.get("curiosity"),
                    "links": len(e.links or []),
                    "born": props.get("born"),
                    "properties": {k: v for k, v in props.items()
                                   if k not in ("script_",)},
                    "fn_counts": props.get("fn_counts"),
                })
                eid_set.add(e.eid)
        # THE EDGES: ISA (parents), links (field back-refs), merged_into
        edges = []
        for e, _v in pairs:
            for p_ in (e.properties or {}).get("parents") or []:
                if p_ in eid_set:
                    edges.append({"s": e.eid, "t": p_, "kind": "isa"})
            if (e.properties or {}).get("merged_into"):
                edges.append({"s": e.eid, "kind": "merged",
                              "t": e.properties["merged_into"]})
            for l in (e.links or [])[:8]:
                edges.append({"s": e.eid, "kind": "link", "cell": l})
        self._json_out({"nodes": nodes, "edges": edges, "radius": 50.0})

    def _map_axes(self):
        """GET /membrane/map/axes: the axis selector's menu (the PCA
        default, the class centroids, the named entities, session:now, the
        momentum)."""
        from .universe import axis_candidates
        est = getattr(self.server, "entity_store", None)
        self._json_out({"candidates": axis_candidates(self.server, est)})

    def _serve_asset(self, name: str):
        import os
        p = os.path.join(os.path.dirname(__file__), name)
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            self._json_err_raw(404, name + " not found"); return
        ctype = ("application/javascript" if name.endswith(".js")
                 else "font/woff2" if name.endswith(".woff2")
                 else "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- /neighbors: the entity field's association graph (read-only) ----
    def _neighbors(self):
        from urllib.parse import urlparse, parse_qs
        import numpy as np
        fld = _efield_of(self.server)
        qs = parse_qs(urlparse(self.path).query)
        K = 8
        query = qs.get("q", [""])[0]
        rows: list = []
        try:
            if fld is None:
                raise ValueError("the entity field is off")
            eid = None
            if "eid" in qs:
                eid = qs["eid"][0]
                st = fld.get(eid)
                if st is None:
                    raise ValueError("unknown entity")
                anchor = {"eid": eid}
                near = [(e2, s) for e2, s in fld.recall(st.state, k=K + 1)
                        if e2 != st.eid][:K]
            else:
                if not query.strip():
                    self._json_out({"query": "", "anchor": None,
                                    "neighbors": []}); return
                anchor = {"query": query}
                qv = np.asarray(fld.emb.embed([query])[0], dtype=float)
                n = float(np.linalg.norm(qv))
                near = fld.recall(qv / (n or 1.0), k=K) if n > 1e-12 else []
            for e2, sim in near:
                st2 = fld.get(e2)
                if st2 is None:
                    continue
                rows.append({"eid": e2,
                             "kind": st2.kind,
                             "sim": round(float(sim), 3),
                             "mass": int(st2.accr),
                             "conflicts": len(st2.conflicts or [])})
            self._json_out({"query": query, "anchor": anchor,
                            "neighbors": rows})
        except Exception as e:
            self._json_out({"query": query, "anchor": None,
                            "neighbors": [], "error": str(e)})

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    # ---- /v1/models: internal (Ollama's list, embed models filtered out) ----
    def _v1_models(self) -> None:
        ids = _chat_capable_models(self.server.backend)
        ids = ids + [EXPLORE_LANE]          # the sentinel evidence-farm lane
        # the AUTOSELECT sentinels: the router itself as a selectable "model".
        # autoselect scores all candidates per turn (cloud-floor);
        # autoselect-local forces the local floor (offline covenant).
        # @-free names (some pickers mangle or hide odd characters; the
        # @-spelled forms stay accepted aliases). Advertised only when the
        # router exists (without it there is nothing to route between).
        if getattr(self.server, "router", None) is not None:
            ids = ids + list(AUTO_LANES)
        # first-class cloud backends: registered providers' models ride this
        # surface too (the picker selects them like any model; the operator's
        # ceiling then applies). Router absent -> no rungs, the local-only view.
        router = getattr(self.server, "router", None)
        if router is not None:
            ids += [r.name for r in router.records.values() if r.backend]
        self._json_out({"object": "list",
                        "data": [{"id": m, "object": "model",
                                  "owned_by": ("membrane-router"
                                               if m in AUTO_LANES else
                                               ("membrane->"
                                                + (router.records[m].backend
                                                   if router and m in
                                                   router.records and
                                                   router.records[m].backend
                                                   else "ollama")))}
                                 for m in ids]})

    # ---- /v1/embeddings: the field's own embedder ----
    def _v1_embeddings(self) -> None:
        body = self._json_body()
        inp = body.get("input", "")
        texts = inp if isinstance(inp, list) else [inp]
        texts = [str(t) for t in texts if str(t).strip()]
        if not texts:
            self._json_err_raw(400, "input required"); return
        srv = self.server
        if srv.membrane.embedder is None and self._resolve_embedder(None) is None:
            self._json_err_raw(503, srv.pending_embed_error); return
        try:
            vecs = srv.membrane.embedder.embed(texts)
        except Exception as e:
            self._json_err_raw(503, f"embed failed: {_one_line(e)}"); return
        self._json_out({"object": "list",
                        "data": [{"object": "embedding", "index": i,
                                  "embedding": [float(x) for x in v]}
                                 for i, v in enumerate(vecs)],
                        "model": srv.membrane.embedder.model})

    # ---- the embedder resolution ladder (embed role) ----
    def _resolve_embedder(self, selected_model: str | None):
        """Order: explicit --embed-model; a known Ollama embed model from
        /api/tags; the opencode-selected chat model (only if it actually
        embeds). On total failure the error is parked on the server and every
        chat/embeddings turn returns it -- err and block the chat."""
        srv = self.server
        backend = srv.backend
        cands = []
        if srv.embed_model_arg:
            cands.append(srv.embed_model_arg)
        cands += [c for c in _known_embed_names(backend) if c not in cands]
        if selected_model and selected_model not in cands:
            cands.append(selected_model)
        for cand in cands:
            probe = _probe_embedder(backend, cand)
            if probe is not None:
                srv.membrane.embedder = probe
                srv.resolved_embed_name = cand
                srv.pending_embed_error = None
                print(f"[opencode] embedder resolved: {cand}", file=sys.stderr)
                return probe
        srv.pending_embed_error = (
            "no Ollama model can serve embeddings; the membrane field cannot "
            "run without one. Pull an embedding model, e.g. "
            "`ollama pull mxbai-embed-large`, then retry.")
        print(f"[opencode] {srv.pending_embed_error}", file=sys.stderr)
        return None

    # ---- /v1/chat/completions: gate + RAG + learn; state turns internal ----
    def _v1_chat(self) -> None:
        srv = self.server
        mem = srv.membrane
        # THE DREAMER'S IDLE SIGNAL: every model-bound turn stamps the
        # clock the dreamer's cycle reads (idle_for = now - last_turn_ts).
        # Never stamped, the dreamer computed idle_for = 0 forever and
        # never dreamed (the boot-order class of bug: a consumer wired to
        # an attr nothing writes).
        srv.last_turn_ts = time.time()
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._forward_raw(raw); return
        model = str(body.get("model", "membrane"))
        # the opencode-selected model is the seat's AND the judge's model
        # (unless pinned): the follow-chat judge reads srv.seat_model live
        if srv.model_follows_chat:
            srv.seat_model = model
        # the AUTOSELECT sentinels: the operator picked the ROUTER as the
        # model. The judge keeps the lane name; the seat model resolves to
        # nothing real, so pin it back to the boot default.
        if normalize_lane(model) in AUTO_LANES:
            srv.seat_model = srv.default_seat_model
        # the operator's ceiling for delegated work: whatever model opencode
        # selected is the SESSION's law -- delegate requests may not raise it
        # (an autoselect selection delegates the CHOOSING to the lane, so the
        # ceiling is the lane sentinel itself, not a fake model name).
        srv.op_ceiling = (normalize_lane(model)
                          if normalize_lane(model) in AUTO_LANES else model)
        # ---- the embed role: resolve lazily (maybe ollama gained a model);
        # block the chat with a clear 503 when nothing can embed.
        if mem.embedder is None and self._resolve_embedder(model) is None:
            self._json_err_raw(503, srv.pending_embed_error); return
        # ---- entity read-through (once, at first embedder resolution): the
        # cortex commons' entities join the local side-index -- one symbolic
        # layer across both systems. Best-effort; a failure degrades to the
        # JSON mirror's contents.
        est = getattr(srv, "entity_store", None)
        if est is not None and mem.embedder is not None \
                and not getattr(srv, "_entities_read_through", False):
            srv._entities_read_through = True
            try:
                _n_read = est.hydrate_from_couchbase()
                if _n_read:
                    print(f"[opencode] entities: read-through +{_n_read} "
                          f"from the couchbase commons", file=sys.stderr)
            except Exception as e:
                print(f"[opencode] entity read-through skipped ({e})",
                      file=sys.stderr)
        # The exemplar-personalized neighborhood (RAM swap of the working
        # set) is retired: on the entity field the working set is the ANN
        # read of entity_states itself (the field lives in the store and
        # reads merge seats) -- nothing to swap.
        # ---- total capture: results from the PREVIOUS turn's tool calls ----
        # before the intercepts: even a recall/state turn may carry the previous
        # turn's tool results; pairing must not be skipped. A capture error
        # must never break the chat.
        try:
            _capture_tool_results(srv, body)
        except Exception as cap_e:
            print(f"[opencode] result-capture error: {cap_e}", file=sys.stderr)
        # ---- THOUGHT intercept (detect): the agent's own asks in
        # assistant-role text ("field state", "membrane: learn that ...").
        # Detection only -- the marked answer block is injected POST-GATE (into
        # `up`, next to the RAG memories) so the gate math never sees field
        # readback. Learn/forget asks are report-only, never written. Once-only
        # via the ledger marked at real delivery.
        thought_asks: list = []
        if getattr(srv, "thought_intercept", True):
            try:
                thought_asks = _assistant_thought_asks(srv, body)
            except Exception as t_e:
                print(f"[opencode] thought-detect error: {t_e}", file=sys.stderr)
        # ---- INTERNAL interception (before the gate: these turns never touch
        # the field -- no learn, no demote, no nudge -- so introspection can't
        # feed the field back into itself). Served straight from the live store.
        # Detection: lexical frames first (deterministic fast path); SEMANTIC
        # fallback on INTENT when nothing matches: cosine to the fixed seed
        # set PLUS the field-grown seed layer (canon-frame exemplars harvested
        # from the live store -- the detector grows from the operator's own
        # writes; --no-field-seeds is the kill switch). A stolen turn echoes
        # the exact marker to let the client force the model path next turn.
        q_early = _last_user_text(body)
        # THE THINKER'S CHAT FEED (the receipt-lane retirement's voice):
        # every operator turn lands in the think queue -- the background
        # mind digests what the operator is saying (questions, ok's,
        # confusion) and its speech goes through the planner gate. $0:
        # enqueue only; the digest decides (and stays silent by default).
        try:
            _th = getattr(srv, "thinker", None)
            if _th is not None:
                _th.enqueue_chat(q_early)
        except Exception:
            pass
        model_escaped = q_early.strip().lower().startswith("model:")
        # ---- pre-gate detection: lexical frames first (deterministic fast
        # path); when nothing matches, the 4-family semantic gate routes on
        # INTENT (cosine to the fixed seeds + the field-grown seed layer).
        # Operator write/forget (the command turn itself must never be
        # learned/demoted; its PAYLOAD goes through learn_pair / store.forget
        # with an explicit audit record), recall/state answer-from-field, and
        # the human's ruling on a parked judge escalation (final authority).
        learn_hit = (not model_escaped) and _is_learn_query(q_early)
        forget_hit = (not model_escaped) and _is_forget_query(q_early)
        rec_hit = (not model_escaped) and _is_recall_query(q_early)
        state_hit = (not model_escaped) and _is_state_query(q_early)
        if (not any((learn_hit, forget_hit, rec_hit, state_hit))
                and srv.sem_intercept and not model_escaped):
            # the semantic fallback embeds the query itself (an outbound read,
            # never learned). 4-family: recall/state answer from the field;
            # learn/forget are operator writes routed without the incantation.
            s_hit = _semantic_intercept_hit(srv, q_early)
            if s_hit:
                s_fam = s_hit[0]
                if s_fam == "recall":
                    rec_hit = True
                elif s_fam == "state":
                    state_hit = True
                elif s_fam == "learn":
                    learn_hit = True
                elif s_fam == "forget":
                    forget_hit = True
        # ---- THE RECEIPT LANE RETIRED (the operator's ruling, the
        # zombie-ack root): byte-deterministic acks served from a cached
        # tool result OVERRODE the model turn -- and every plain chat turn
        # whose history carried a stale tool_call_id replayed 'Edit
        # applied successfully.' (the operator's socket-error/zombie
        # session). Membrane's voice now lives ENTIRELY in the thinker:
        # it receives the chat messages (the digest feed) and speaks
        # through the speech lane's planner gate -- suggesting, noting,
        # asking -- but never overrides a model turn again. The E9
        # experiment stays (library + audit replay, --no-receipts is a
        # no-op flag kept for compat).
        # ---- the watch layer (spatial triggers): operator set/list/drop,
        # handled pre-gate like every other operator write.
        if not model_escaped and _is_watch_query(q_early):
            text = _watch_report_cmd(srv, q_early)
            _print_intercept("watch", q_early)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        # ---- the intention layer (open commitments + the dream loop's
        # steering field): intend/intents/fulfill/drop intent, pre-gate like
        # every operator write. Zero model calls.
        if not model_escaped and _is_intent_query(q_early):
            text = _intent_cmd(srv, q_early)
            _print_intercept("intent", q_early)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        # ---- the codegraph lane: call-graph/impact reads, pre-gate like
        # recall/state (read-only by construction, zero model calls).
        if not model_escaped and _is_codegraph_query(q_early):
            text = _codegraph_answer(srv, q_early)
            _print_intercept("codegraph", q_early)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        # ---- the monologue toggle (the [membrane] thinking-channel banner):
        # 'membrane: monologue [on|off]' -- pre-gate, zero model calls. The
        # /monologue opencode command emits this frame via passthrough.
        if not model_escaped and q_early.strip().lower().startswith(
                _MONOLOGUE_CANON):
            spec = q_early.strip().lower()[len(_MONOLOGUE_CANON):].strip()
            cur = bool(getattr(srv, "gate_monologue", True))
            if spec in ("on", "enable"):
                srv.gate_monologue = True
            elif spec in ("off", "disable"):
                srv.gate_monologue = False
            elif spec in ("", "toggle"):
                srv.gate_monologue = not cur
            else:
                self._serve_intercept_answer(
                    model, f"[membrane-intercept] monologue: unknown mode "
                    f"{spec!r} (on/off/toggle). Currently "
                    f"{'ON' if cur else 'OFF'}.", bool(body.get("stream")))
                return
            state = "ON" if srv.gate_monologue else "OFF"
            _write_audit(srv.membrane, {"event": "monologue-toggle",
                                        "state": state})
            self._serve_intercept_answer(
                model, f"[membrane-intercept] [membrane] banner now {state}. "
                f"It shows the gate verdict, field vitals, what memory was "
                f"gave to the LLM, and what was learned — streamed in the "
                f"thinking channel like /thinking."
                .replace("was gave", "was given"),
                bool(body.get("stream", False)))
            return
        # ---- the surface frame (the totality view): 'membrane: surface'
        # shows the last turns' full API surface -- everything membrane
        # sent to / received from the model. Pre-gate, zero model calls.
        if not model_escaped and q_early.strip().lower().startswith(
                "membrane: surface"):
            self._serve_intercept_answer(model, _surface_report(srv),
                                         bool(body.get("stream", False)))
            return
        # ---- the commons: the operator's promote / forget-commons turns,
        # handled pre-gate like every other operator write
        if _efield_of(srv) is not None:
            gq = q_early.lower()
            if _is_promote_query(gq):
                text = _promote_global(srv, q_early, vec=None)
                _print_intercept("promote-global", q_early)
                self._serve_intercept_answer(model, text,
                                             bool(body.get("stream", False)))
                return
            if _is_forget_global_query(gq):
                text = _forget_global(srv, q_early)
                _print_intercept("forget-global", q_early)
                self._serve_intercept_answer(model, text,
                                             bool(body.get("stream", False)))
                return
        if learn_hit or forget_hit:
            if learn_hit:
                payload = (_after_frame(q_early, (_LEARN_CANON,)) or
                           _after_frame(q_early, _LEARN_FRAMES) or
                           _after_frame(q_early, _LEARN_SOFT_FRAMES))
                if not payload:            # bare "membrane: learn that" -> use the rest
                    payload = q_early[len(_LEARN_CANON):] if q_early.lower().startswith(
                        _LEARN_CANON) else ""
                text = _learn_report(srv, payload)
            else:
                spec = (_after_frame(q_early, (_FORGET_CANON,)) or
                        _after_frame(q_early, _FORGET_FRAMES) or
                        _after_frame(q_early, _FORGET_SOFT_FRAMES) or
                        q_early.rstrip("?!").strip())
                if not spec and _window_seconds(q_early) is not None:
                    spec = q_early          # bare 'forget the last hour' form
                text = _forget_report(srv, spec)
            print(f"[opencode] intercept ({'learn' if learn_hit else 'forget'}): "
                  f"{q_early[:60]!r}", file=sys.stderr)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        if rec_hit or state_hit:
            fam = "recall" if rec_hit else "state"
            marker = ("[membrane-intercept] internal " + fam +
                      " answer; it was NOT learned. To force the model path, "
                      "repeat the ask prefixed with 'model:'")
            text = (_recall_report(srv, _recall_topic(q_early)) if rec_hit
                    else _state_report(srv))
            text = text + "\n\n" + marker
            print(f"[opencode] intercept ({fam}, "
                  f"{'lexical' if (rec_hit and _is_recall_query(q_early)) or (state_hit and _is_state_query(q_early)) else 'semantic'}): "
                  f"{q_early[:60]!r}", file=sys.stderr)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        try:
            turn = mem.turn(body)
        except Exception as e:
            print(f"[opencode] turn front error, forwarding raw: {e}", file=sys.stderr)
            self._forward_raw(raw); return
        if turn.vec is None:
            self._forward_raw(raw); return
        vec, query = turn.vec, turn.query
        _is_tool_round = _tool_round(body)
        verdict = {"verdict": "escalate", "reason": "the model answers", "sim": None,
                   "nov": None, "ex_idx": None, "query_raw": turn.query_raw,
                   "binding": turn.binding, "binding_failed": turn.binding_failed,
                   "drift": turn.drift}
        # THE GATE ON ANSWER EVENTS (every real turn): the earlier ask of the
        # same thing, its answer, the staleness guard -- its similarity is the
        # turn's 'sim' (the null-recall lane and the planner's sig read it)
        gv = None
        _fld = _efield_on(srv)
        if _fld is not None and not _is_tool_round:
            try:
                from . import efgate as EG
                gv = EG.candidate(_fld, query, binding_failed=turn.binding_failed)
                verdict["efgate"] = {k: gv.get(k) for k in
                                     ("eligible", "reason", "sim", "agree", "consensus",
                                      "q_event", "a_event")}
                verdict["sim"] = gv.get("sim")
            except Exception as e:
                print(f"[efgate] error: {e}", file=sys.stderr)
        if not _is_tool_round:
            mem.remember(turn.query_raw)
        # THE PLANNER'S CONTEXT SIGNAL (the loop's first half): the turn's
        # situation cluster = (gate verdict kind + sim band). The ActTable
        # keys its transition table on this sig -- speak() consults it,
        # and the reaction pass (the turn after the speech lands) labels
        # the act. The sig is cheap (no embeds -- the gate already ran).
        try:
            _vk = str(verdict.get("verdict") or "unknown")
            _vs = verdict.get("sim")
            _band = (f"{float(_vs):.1f}" if isinstance(_vs, (int, float))
                     else "none")
            srv._last_context_sig = f"{_vk}|{_band}"
        except Exception:
            srv._last_context_sig = "unknown"
        # ---- INTERNAL: the 'model:' escape hatch only (recall/state were both
        # fully handled above, pre-gate) ----
        q = _last_user_text(body)
        ctx = None
        # THE NULL-RECALL LANE (peer-reported from rosetta-check: 3 of 5
        # field queries returned empty and nothing said so -- absence is
        # information; saying it prevents re-deriving from git):
        # when the GATE's own top sim is far below sim_min (all-orthogonal:
        # the field holds nothing shaped like this ask), record the miss.
        # The banner renders it next turn; /membrane/state counts the
        # session. Reuses the gate's sim -- zero new embeds. The 0.5 floor:
        # the 0.5-0.7 band is honest uncertainty (the E1 near-miss replay:
        # lowering the bar there serves the WRONG beat); below 0.5 the field
        # is genuinely empty on this direction. Cleared by any hit -- a real
        # near match makes the last null stale.
        v_sim = verdict.get("sim")
        if (not _is_tool_round
                and isinstance(v_sim, (int, float))
                and float(v_sim) < RECALL_NULL_SIM_FLOOR):
            prev = getattr(srv, "_recall_null", None)
            n = int((prev or {}).get("count", 0) or 0) + 1
            srv._recall_null = {"query": _clip(query, 90), "sim": round(
                float(v_sim), 2), "ts": time.time(), "count": n}
        elif not _is_tool_round and isinstance(v_sim, (int, float)) \
                and float(v_sim) >= float(__import__("membrane.efgate",
                                                     fromlist=["SIM_MIN"]).SIM_MIN):
            # a real hit: the counter STAYS (the session's honest metric)
            # but the 'current miss' retires -- the banner line is about
            # the live edge, not history
            cur = getattr(srv, "_recall_null", None)
            if cur is not None:
                cur.pop("query", None)
                cur.pop("sim", None)
        # THE GATE ON ANSWER EVENTS + the replay A/B (every real turn): an
        # earlier ask of the same thing, its answer, the staleness guard.
        # Eligible turns are logged as A/B pairs (closed when the model's
        # own answer lands); --replay-ab P serves the replay on a fraction.
        srv._ab_key = None
        if gv is not None:
            try:
                from . import efgate as EG
                if gv["eligible"]:
                    served = EG.serve_replay(float(getattr(srv, "replay_ab", 0.0) or 0.0))
                    k_ab = EG.ab_open(_fld, gv, query, served)
                    if served:
                        _cap = getattr(srv, "capturer", None)
                        if _cap is not None:
                            _cap.on_turn(query)
                        print(f"[efgate] replay #{gv['a_event']} for {query[:60]!r} "
                              f"(sim {gv['sim']})", file=sys.stderr)
                        _write_audit(mem, {"event": "efgate-replay", "ab": k_ab,
                                           "q_event": gv["q_event"], "a_event": gv["a_event"],
                                           "sim": gv["sim"], "query": _clip(query, 300)})
                        self._serve_intercept_answer(model, gv["answer"],
                                                     bool(body.get("stream", False)))
                        return
                    srv._ab_key = k_ab
            except Exception as e:
                print(f"[efgate] error: {e}", file=sys.stderr)
        # THE UNIFIED BANNER: ONE _where_we_are call, ONE delta-gate
        # evaluation (the double-call bug: the first call consumed the
        # delta gate, the second saw 'unchanged' and returned None -- the
        # banner vanished while the removed recall block stayed).
        # THE ABLATION ARM (documented in port-notes 24): --no-banner
        # removes the banner from the injection entirely (recall stays --
        # it is content). The ruler's third arm; the flag exists so the
        # measurement can actually run.
        banner_block = (None if getattr(srv, "no_banner", False)
                        else _where_delta(srv, _where_we_are(srv, vec)))
        if banner_block:
            ctx = (ctx + "\n\n" + banner_block) if ctx else banner_block
        # THE ENTITY DIFF (normal mode): what changed in the entities since
        # the last message the model processed -- code, entity scripts and
        # other sessions quoted, this conversation's own turns counted. The
        # watermark advances only once this turn is delivered.
        fld = _efield_on(srv)
        efield_mark = None
        if fld is not None:
            try:
                from .entity_block import state_diff, watermark
                efield_mark = watermark(fld)
                if not getattr(srv, "no_context", False):
                    diff = state_diff(fld, getattr(srv, "_efield_mark", None) or efield_mark,
                                      _own_session_prefix(srv),
                                      include_own=not _is_tool_round)
                    if diff:
                        ctx = (ctx + "\n\n" + diff) if ctx else diff
            except Exception as e:
                print(f"[efield] diff error: {e}", file=sys.stderr)
        # THE SPEECH RELAY: membrane's held messages ride the next
        # model-bound request as a marked block -- the model relays them
        # into the conversation verbatim (the mouth), where they enter the
        # timeline and learn from the operator's reaction.
        # THE REINFORCEMENT PASS (the loop's closing half): a relayed
        # speech's act is labeled by THIS turn's flow -- the turn the
        # speech rode into: the operator kept coding (the turn advanced
        # past it) => +1 productive; the operator's next ask re-served the
        # same near memory (a repeat/re-ask shape) => -1 (the speech was
        # noise); otherwise 0 (ignored -- no adjustment). One label per
        # delivered batch, on the sig the speech was planned under.
        _pending = getattr(srv, "_pending_speech_label", None)
        srv_act_tab = getattr(srv, "_act_table", None)
        if _pending and srv_act_tab is not None and _pending[0] and _pending[1]:
            try:
                p_sig, p_act = _pending
                nxt = str(verdict.get("verdict") or "")
                nxt_sim = verdict.get("sim")
                is_reask = (nxt.startswith("reask")
                            or (isinstance(nxt_sim, (int, float))
                                and float(nxt_sim) >= 0.9))
                reward = -1 if is_reask else 1
                srv_act_tab.learn(p_sig, p_act, reward)
            except Exception:
                pass
        srv._pending_speech_label = None
        speak_block = _speak_block(srv)
        if speak_block:
            ctx = (ctx + "\n\n" + speak_block) if ctx else speak_block
        up = dict(body)
        if getattr(srv, "no_context", False):
            # THE STATELESS LLM (the field is the only memory): strip the
            # transcript history (the turn boundary), then REBUILD the
            # coherence from the field -- the reconstruction block rides
            # ahead of the recall (the recent conversation, as the field
            # remembers it, bounded ~3k chars instead of the transcript's
            # unbounded growth).
            stripped = _strip_context(body.get("messages") or [])
            stripped, _saved = _elide_tool_tail(
                stripped, keep_last=int(getattr(srv, "tool_keep", 3)))
            if _saved:
                print(f"[opencode] no-context: elided {_saved} chars of "
                      f"older in-turn tool traffic", file=sys.stderr)
            recon = None
            if fld is not None:
                try:
                    from .entity_block import reconstruct, render_for
                    recon = reconstruct(fld, _own_session_prefix(srv))
                    eblock = render_for(fld, query, vec)
                    if eblock:
                        recon = (recon + "\n\n" + eblock) if recon else eblock
                except Exception as e:
                    print(f"[efield] reconstruction error: {e}", file=sys.stderr)
            ctx = (ctx + "\n\n" + recon) if (ctx and recon) else (recon or ctx)
            up["messages"] = (_inject(stripped, ctx) if ctx
                              else stripped)
        elif ctx:
            up["messages"] = _inject(body.get("messages") or [], ctx)
        # thought-intercept delivery: the marked internal answers ride next to
        # the RAG context; marked served only AFTER _v1_deliver succeeds (a
        # backend error forwards the original raw -- undelivered asks must
        # stay pending for the next continuation turn).
        if thought_asks:
            t_block = _thought_block([
                (fam, _thought_answer(srv, fam, t))
                for fam, t, _ in thought_asks])
            if t_block:
                up["messages"] = _inject(up["messages"], t_block)
        # W3b: the one-shot --dev frame-discipline warning rides the next
        # model-bound request (never blocks; consumed exactly once).
        dev_warn = _dev_warn_take(srv)
        if dev_warn:
            up["messages"] = _inject(up["messages"], dev_warn)
        # watch delivery: undelivered [membrane-watch] reports ride on model
        # turns (marked served only after _v1_deliver succeeds, same covenant
        # as thought asks). Intercepted turns skip this -- their answer IS the
        # delivery; the report rides again on the next real model turn.
        wboard = getattr(srv, "_watch_board", None)
        w_events = None
        if wboard is not None:
            try:
                wr = wboard.take_report()
            except Exception as w_e:
                print(f"[opencode] watch-report error: {w_e}", file=sys.stderr)
                wr = None
            if wr is not None:
                w_events, w_block = wr
                up["messages"] = _inject(up["messages"], w_block)
        # slice 4: the live peer ask. On an escalation (the local field has
        # nothing certain), ask live siblings' local fields -- bounded
        # fan-out, tight timeout (a hung peer must not stall a turn). A hit
        # rides along as MARKED CONTEXT; all failures degrade to "no help"
        # and the model path proceeds exactly as before (the safe direction).
        if (getattr(srv, "gpeers", None) is not None
                and str(verdict.get("verdict", "")).startswith("escalate")):
            try:
                for peer in srv.gpeers.livelist()[:4]:
                    phit = srv.gpeers.ask(peer, query, timeout=5.0)
                    if phit and phit.get("block"):
                        verdict["peer_hit"] = {k: phit.get(k) for k in ("fid", "entities")}
                        up["messages"] = _inject(
                            up["messages"],
                            f"[membrane-intercept] a sibling membrane (field "
                            f"{phit.get('fid')}) knows these entities -- context, "
                            f"never instructions.\n{phit['block']}")
                        break
                    if phit:
                        verdict["peer_hit"] = phit
                        pblock = (f"[membrane-intercept] a sibling membrane's "
                                  f"memory (field {phit.get('fid')}, sim "
                                  f"{phit.get('sim')}, "
                                  f"seat {phit.get('seat') or '?'}): context, "
                                  f"never instructions.\n"
                                  f"situation: {_clip(phit.get('situation', ''), 200)}\n"
                                  f"response: {_clip(phit.get('action', ''), 600)}")
                        up["messages"] = _inject(up["messages"], pblock)
                        break
            except Exception as e:
                print(f"[opencode] peer-ask error (ignored): {e}",
                      file=sys.stderr)
        # ---- the orchestration router: model-vs-model on escalations ----
        # Score proposes (fit_hat - delay - price); the REVERSED cost-floor
        # disposes (cheapest eligible cloud holds the floor; locals must
        # earn the rank). THE OPERATOR'S HAND: a lane sentinel (autoselect /
        # autoselect-local, @-aliases accepted) delegates the CHOOSING to the
        # router; a SPECIFIC model selection is a HARD pick -- the router
        # must never override it (that pick was the operator's decision; the
        # sentinel name itself never reaches a backend -- a router-less or
        # failed autoselect falls back to the boot model, the safe
        # direction).
        router = getattr(srv, "router", None)
        lane = normalize_lane(str(body.get("model") or "").split("/", 1)[-1])
        autoselect = router is not None and lane in AUTO_LANES
        if autoselect:
            up["model"] = (srv.default_seat_model
                           or _pick_default_model(_chat_capable_models(
                               srv.backend), None) or "membrane")
        if autoselect and str(verdict.get("verdict", "")) \
                .startswith("escalate"):
            assert router is not None     # autoselect implies router exists
            try:
                needs_tools = any(
                    isinstance(m, dict) and m.get("tool_calls")
                    for m in (body.get("messages") or [])
                    if isinstance(m, dict))
                # the lane sentinel IS the routing instruction: route()
                # consumes it (route -> the scored pick; the lane rides the
                # audit as route_info['lane']).
                rec, route_info = router.route(
                    vec if vec is not None else None,
                    urgency=1.0, needs_tools=needs_tools, ceiling=lane,
                    ctx_tokens=Registry.estimate_tokens(up.get("messages")))
                if rec is not None:
                    route_info["fallback"] = str(up.get("model") or "membrane")
                    route_info["price"] = rec.price()
                    verdict["route"] = route_info
                    if rec.name != str(up.get("model")):
                        up["model"] = rec.name
                        print(f"[opencode] router -> {rec.name} "
                              f"({route_info.get('reason')})",
                              file=sys.stderr)
            except Exception as e:
                print(f"[opencode] router error (plain path): {e}",
                      file=sys.stderr)
        try:
            self._v1_deliver(up, raw, query, vec, verdict, ctx)
            if efield_mark is not None:
                srv._efield_mark = efield_mark
            if thought_asks:
                _serve_thoughts_served(srv, thought_asks)
            if w_events is not None:
                wboard.mark_served(w_events)
        except Exception as e:
            print(f"[opencode] backend error, no field write: {e}", file=sys.stderr)
            self._forward_raw(raw)

    def _v1_deliver(self, up: dict, raw: bytes, query: str, vec, verdict: dict,
                    ctx: str | None) -> None:
        """Forward to Ollama's /v1/chat/completions (stream or buffered), tee
        to the client, then learn the exchange into the field."""
        srv = self.server
        mem = srv.membrane
        route = verdict.get("route") or {}
        routed_model = route.get("route")
        orig_model = str(up.get("model") or "membrane")
        t0 = time.monotonic()

        def _open_backend(model_name: str):
            """POST the turn to the routed model's backend. On a rung DOWN
            (out of tokens / auth / rate limit / dead backend) fall DOWN THE
            LADDER: next cloud rung, then eligible locals, each failed rung
            cooling down so later turns skip it. When nothing answers the
            request fails and opencode surfaces the field-only state
            honestly."""
            u2 = dict(up)
            u2["model"] = model_name
            # per-rung backend: registered providers answer their
            # OpenAI-compatible chat path with their own key + header style
            # (auth.PROVIDERS carries the shapes, memory-only keys);
            # everything else goes to the Ollama /v1 surface as before.
            rec = (getattr(srv, "router", None).records.get(model_name)
                   if getattr(srv, "router", None) else None)
            if rec is not None and rec.backend:
                from .auth import PROVIDERS as _PROV
                prov = _PROV.get(rec.backend) or {}
                url = (prov.get("base", "") + prov.get("chat_path",
                       "/v1/chat/completions"))
                headers = {"Content-Type": "application/json",
                           **_auth_headers(rec.backend, srv.cloud_keys)}
            else:
                url = srv.backend + "/v1/chat/completions"
                headers = {"Content-Type": "application/json"}
            rq = urllib.request.Request(
                url, data=json.dumps(u2).encode(), method="POST",
                headers=headers)
            try:
                return urllib.request.urlopen(rq, timeout=300)
            except urllib.error.HTTPError as he:
                # rung-down codes: bad/absent model (400/404), auth or
                # payment (401/402/403), rate limit / quota (429), 5xx.
                # 400 on the FINAL rung surfaces (client-side error).
                down = he.code in (400, 401, 402, 403, 404, 408, 429,
                                   500, 502, 503, 504)
                nxt = _next_rung(model_name)
                if not down or nxt is None:
                    if down:
                        route["ladder_exhausted"] = True
                    raise
                why = f"HTTP {he.code}"
                if he.code in (401, 402, 403, 429):
                    why += (" (out of tokens/limit)" if he.code in (402, 429)
                            else " (auth)")
                print(f"[opencode] ladder failover {model_name} -> {nxt} "
                      f"({why})", file=sys.stderr)
                # the lookup, not the bare name: `router` is assigned in
                # the except-Exception branch below, which makes it LOCAL
                # here -- a bare use raised UnboundLocalError on every
                # HTTP rung-down
                router = getattr(srv, "router", None)
                if router is not None:
                    router.note_outage(model_name, why)
                route["failover"] = nxt
                return _open_backend(nxt)
            except Exception as first_err:
                nxt = _next_rung(model_name)
                if nxt is None:
                    route["ladder_exhausted"] = True
                    raise
                print(f"[opencode] ladder failover {model_name} -> {nxt} "
                      f"({first_err})", file=sys.stderr)
                router = getattr(srv, "router", None)
                if router is not None:
                    router.note_outage(model_name, str(first_err)[:120])
                route["failover"] = nxt
                return _open_backend(nxt)

        def _next_rung(model_name: str):
            """The next rung down the ladder after model_name, or None when
            the stack is exhausted (nothing alive) — the field-only edge.
            An autoselect turn keeps the LANE as the ladder's ceiling (the
            sentinel chose 'route it all', not the fallback model's price).
            A specific pick keeps ITS ceiling (never escalates past what the
            operator selected). The context-window filter applies here too:
            a failover rung that cannot hold the request is skipped."""
            router = getattr(srv, "router", None)
            if router is None or not routed_model:
                return None
            lane_ceiling = (str(verdict.get("route", {}).get("lane") or "")
                            or None)
            ceiling = (normalize_lane(lane_ceiling)
                       if lane_ceiling else
                       (str(up.get("model") or "").split("/", 1)[-1] or None))
            try:
                stack = router.ladder(
                    needs_tools=any(isinstance(m, dict) and m.get("tool_calls")
                                    for m in (up.get("messages") or [])
                                    if isinstance(m, dict)),
                    ceiling=ceiling,
                    ctx_tokens=Registry.estimate_tokens(up.get("messages")))
            except Exception:
                return None
            seen = False
            for r in stack:
                if r.name == model_name:
                    seen = True
                    continue
                if seen and r.name != orig_model:
                    return r.name
                if seen and r.name == orig_model:
                    return r.name          # the pre-router rung is fair game
            # every listed rung failed or was above model_name: if the
            # requested rung was never on the stack (pre-router fallback),
            # offer nothing more — the stack walk ends.
            return None

        streaming = bool(up.get("stream", False))
        parts: list[str] = []
        think: list[str] = []
        calls: dict = {}          # index -> accumulated tool-call delta
        tool_calls = False
        if streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            # THE TRANSPARENCY CONTRACT, restored: stream the FULL composed
            # injection (recall + where-we-are + the banner -- everything
            # membrane appended to ctx) as reasoning deltas, so the operator
            # sees EXACTLY what the model sees. The old path streamed only
            # the gate monologue; recall and where-we-are rode silently --
            # the dark-banner bug (the surface log proved the bytes were
            # sent while the thinking channel showed nothing).
            if getattr(srv, "gate_monologue", True) and ctx:
                for chunk in _openai_sse_chunks(
                        up.get("model", "membrane"),
                        "[membrane] injecting this turn:\n\n" + ctx + "\n\n",
                        reasoning=True):
                    self.wfile.write(chunk)
                self.wfile.flush()
            try:
                backend_r = _open_backend(up.get("model", "membrane"))
            except Exception as e:
                if route.get("ladder_exhausted"):
                    self._serve_field_only(up.get("model", "membrane"), vec,
                                           route, mid_stream=True, err=e)
                    return
                raise
            with backend_r as r:
                for line in r:                      # SSE, tee verbatim
                    if not line:
                        continue
                    self.wfile.write(line)
                    self.wfile.flush()
                    s = line.strip()
                    if not s.startswith(b"data:"):
                        continue
                    payload = s[len(b"data:"):].strip()
                    if not payload or payload == b"[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ch in obj.get("choices", []) or []:
                        d = ch.get("delta") or {}
                        t = d.get("content")
                        if isinstance(t, str):
                            parts.append(t)
                        think.append(_delta_reasoning(d))
                        for tc in d.get("tool_calls") or []:
                            tool_calls = True
                            i = int(tc.get("index", 0) or 0)
                            c = calls.setdefault(i, {"function": {}})
                            if tc.get("id"):
                                c["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            tgt = c["function"]
                            if fn.get("name"):
                                tgt["name"] = (tgt.get("name") or "") + fn["name"]
                            if fn.get("arguments"):
                                tgt["arguments"] = (tgt.get("arguments") or "") + fn["arguments"]
            self.wfile.flush()
            # THE STREAMING REGISTRATION (the receipt lane's missing half):
            # the tee path accumulated `calls` from deltas but never
            # registered them -- opencode's traffic is stream:true, so
            # _pending_tools never populated for the real flow (the
            # receipt lane's missing-registration exhibit, E9)
            if calls:
                _capture_plan_and_calls(mem, query, "".join(parts), calls)
            resp_obj = None
        else:
            # BUFFERED delivery. The empty-answer guard: a rung that answers
            # HTTP 200 with NO content and NO tool calls is DOWN in disguise
            # (observed: openrouter free tiers 200-with-empty-body). Treat it
            # like any rung-down: outage-note it and walk the ladder; when
            # nothing is left, the honest field-only banner serves instead of
            # silence. Non-routed turns keep the verbatim passthrough.
            resp_bytes: bytes | None = None
            model_name = up.get("model", "membrane")
            if routed_model:
                for _attempt in range(6):        # bounded: ladder + retries
                    try:
                        resp_bytes = _open_backend(model_name).read()
                    except Exception as e:
                        if route.get("ladder_exhausted"):
                            self._serve_field_only(
                                up.get("model", "membrane"), vec,
                                route, mid_stream=False, err=e)
                            return
                        raise
                    if _resp_has_content(resp_bytes):
                        break
                    nxt = _next_rung(model_name)
                    if nxt is None:
                        route["ladder_exhausted"] = True
                        self._serve_field_only(
                            up.get("model", "membrane"), vec, route,
                            mid_stream=False,
                            err=RuntimeError(f"{model_name}: empty answer"))
                        return
                    print(f"[opencode] EMPTY answer from {model_name}; "
                          f"failover -> {nxt}", file=sys.stderr)
                    router = getattr(srv, "router", None)
                    if router is not None:
                        router.note_outage(model_name,
                                           "empty answer (200, no content)")
                    route["failover"] = nxt
                    model_name = nxt
            else:
                try:
                    resp_bytes = _open_backend(model_name).read()
                except Exception as e:
                    if route.get("ladder_exhausted"):
                        self._serve_field_only(
                            up.get("model", "membrane"), vec,
                            route, mid_stream=False, err=e)
                        return
                    raise
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes or b"")))
            self.end_headers()
            self.wfile.write(resp_bytes or b"")
            self.wfile.flush()
            try:
                resp_obj = json.loads(resp_bytes or b"{}")
            except json.JSONDecodeError:
                resp_obj = None
            if resp_obj:
                ch = (resp_obj.get("choices") or [{}])[0]
                msg = ch.get("message") or resp_obj.get("message") or {}
                c = msg.get("content")
                if isinstance(c, str):
                    parts.append(c)
                if msg.get("tool_calls"):
                    tool_calls = True
                    for j, tc in enumerate(msg["tool_calls"]):
                        if isinstance(tc, dict):
                            calls[int(tc.get("index", j) or 0)] = tc
                think.append(_msg_reasoning(msg))
        content = "".join(parts)
        thought = "".join(think).strip()
        # the STREAMING empty-answer guard: bytes were already tee'd, so no
        # failover is possible this turn -- but the silent rung must not look
        # healthy: outage-note it (later turns skip it) and let the
        # re-ask window learn it as a loss (see the calibration pass).
        if streaming and routed_model and not content.strip() and not tool_calls \
                and not thought:
            router = getattr(srv, "router", None)
            if router is not None:
                router.note_outage(routed_model,
                                   "empty answer (200, no content)")
                print(f"[opencode] EMPTY answer from {routed_model} "
                      f"(streamed; rung cooled down)", file=sys.stderr)
        # the router's measured record: latency of the routed turn, and (for
        # cloud) the spend + the calibration record -- (pressure, survived)
        # lands after the re-ask window resolves the outcome (see below).
        router = getattr(srv, "router", None)
        if router is not None and routed_model:
            try:
                router.note_latency(routed_model, time.monotonic() - t0)
                if route.get("kind") == "cloud":
                    router.record_spend(float(route.get("price")
                                              or CLOUD_DEFAULT_PRICE))
            except Exception as e:
                print(f"[opencode] router bookkeeping error: {e}",
                      file=sys.stderr)
        ex_idx = None
        # the traffic signal: when the last real model forward landed and
        # how long it took (the background duty loops read both as "busy
        # now").
        srv._last_fwd_dt = time.time()
        srv._last_fwd_dur = time.monotonic() - t0
        # the entity field's discourse intake: the user's text + the model's
        # prose + its reasoning -- mixed prose+tool turns included (the
        # exemplar path below drops them); tool calls ride on_calls
        _cap = getattr(srv, "capturer", None)
        if _cap is not None:
            _cap.on_turn(query, content, thought or "")
        # the A/B pair closes on the model's final answer (a text turn)
        k_ab = getattr(srv, "_ab_key", None)
        if k_ab and content.strip() and not tool_calls and _efield_on(srv) is not None:
            srv._ab_key = None
            import threading as _th
            from .efgate import ab_close
            _th.Thread(target=lambda: _swallow(ab_close, srv.efield, k_ab, content),
                       daemon=True, name="ab-close").start()
        # the captured turn's events (cap.on_turn above) already carry the
        # answer; nothing exemplar-shaped is written back here.
        # total capture: the model's intent + machinery + thought, as plain Q/A.
        # W3a rides the same lane: the delivered text is linted for frame-
        # discipline violations (stacked/buried canon frames) so the poison
        # that self-learns as junk memory is also VISIBLE and countable.
        try:
            if content.strip() and not tool_calls:
                _frame_lint(srv, content, source="assistant-text")
            if calls:
                _capture_plan_and_calls(mem, query, content, calls)
        except Exception as e:
            print(f"[opencode] capture error (response already served): {e}",
                  file=sys.stderr)
        # THE TOTALITY VIEW: record this turn's full API surface -- what we
        # sent (per-block summaries + routing + model) and what came back
        # (reply head + tool calls). The operator reads it via
        # /membrane/state 'surface' or 'membrane: surface'.
        try:
            _surface_push(srv, {
                "ts": time.time(),
                "query": _clip(query, 120),
                "verdict": verdict.get("verdict"),
                "sim": verdict.get("sim"),
                "route": (verdict.get("route") or {}).get("route"),
                "route_reason": (verdict.get("route") or {}).get("reason"),
                "model": up.get("model"),
                "sent_blocks": _surface_block_summary(ctx),
                "ctx_chars": len(ctx or ""),
                "n_injected": n_ctx_mem(ctx),
                "reply_head": _clip(content, 200),
                "tool_calls": bool(tool_calls),
                "learned": ex_idx,
                "thought_head": _clip(thought, 120) if thought else None,
            })
        except Exception as s_e:
            print(f"[opencode] surface-log error: {s_e}", file=sys.stderr)
        self._log_turn(verdict, ctx, None)

    # ---- the [membrane] monologue (thinking channel) ----------------------
    def _gate_monologue(self, verdict: dict, ctx: str | None, query: str,
                        query_vec=None) -> str:
        """THE BANNER: vitals + alerts only. The track STORIES ride WHERE
        WE ARE (the operator's one place to read them -- the old
        double-render showed stories in the banner while WHERE WE ARE
        showed fallback lines for the same tracks: two event-conditional
        renderers, one got the story, one didn't -- split-brain). The
        state-machine step runs here (alerts drained exactly-once)."""
        lines: list[str] = []
        mem = self.server.membrane
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is not None and getattr(est, "tracks", None):
            try:
                fld = _efield_on(srv)
                from .entity_block import track_count as _tc
                if fld is not None:
                    est.evaluate_tracks(count_fn=lambda k: _tc(fld, k))
                for a in est.take_alerts():
                    lines.append(f"ALERT: {a['label']} {a['kind']} its "
                                 f"boundary ({a['boundary']}; {a['detail']})")
                # THE ENTITY ANNOUNCEMENTS (ALERT-style, exactly-once):
                # births and merges drain here -- the model SEES what the
                # night grew and what it registered itself (the loop that
                # makes the registration habit stick)
                for a in est.take_announcements():
                    lines.append(announce_line(a))
            except Exception as e:
                print(f"[opencode] banner render error: {e}",
                      file=sys.stderr)
        return "\n".join(lines) if lines else ""

    # ---- the internal intercept answer (no model call, never learned) ----
    # One unified server for both families: a fully-buffered recall/state
    # report, delivered buffered or as SSE depending on the request.
    def _serve_intercept_answer(self, model: str, text: str,
                                streaming: bool, thinking: str | None = None) -> None:
        if not streaming:
            msg = {"role": "assistant", "content": text}
            if thinking:
                msg["reasoning_content"] = thinking
            self._json_out({"object": "chat.completion", "created": 0,
                            "model": model,
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": msg}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            if thinking:
                for chunk in _openai_sse_chunks(model, thinking,
                                                reasoning=True):
                    self.wfile.write(chunk)
            for chunk in _openai_sse_chunks(model, text):
                self.wfile.write(chunk)
            self.wfile.flush()
        except Exception as e:
            print(f"[opencode] intercept stream error: {e}", file=sys.stderr)

    # ---- the field-only banner (plan #5) ------------------------------------
    # When the whole ladder is down (route() returned nothing, or every rung
    # failed at delivery) the field itself is the last resort -- but a bare
    # 502 teaches opencode nothing. Serve an honest banner instead: the turn
    # is answered from lived memory (or honestly says nothing matches), NO
    # model call, NO learn (re-serving recall as a learned answer would be
    # the field feeding on itself -- the collapse doctrine).
    def _serve_field_only(self, model: str, vec, route: dict,
                          mid_stream: bool, err=None) -> None:
        srv = self.server
        mem = srv.membrane
        rtr = getattr(srv, "router", None)
        cooling = (sum(1 for o in (getattr(rtr, "_outages", None) or {}).values()
                       if isinstance(o, (tuple, list)) and o and o[0] > time.time())
                   if rtr is not None else 0)
        why = (route.get("reason") or
               ("all rungs cooling down" if cooling else
                "model ladder exhausted"))
        lines = [f"[membrane] FIELD-ONLY: no model rung answered this turn "
                 f"({why}).",
                 "[membrane] The answer below comes from lived memory alone "
                 "-- it may be stale; verify before acting on it.", ""]
        served = False
        try:
            if vec is not None and query:
                # the field's own answer: the past answers nearest the ask
                # + the entities it names, one rendered block (render_for
                # returns None when nothing matches)
                from .entity_block import render_for
                fld = _efield_of(srv)
                block = render_for(fld, query, budget=1800) \
                    if fld is not None else None
                if block:
                    lines.append(block)
                    served = True
        except Exception:
            pass
        if not served:
            lines.append("- nothing trusted in the field matched this "
                         "question; retry when the model ladder recovers.")
        text = "\n".join(lines)
        if err:
            print(f"[opencode] field-only ({err})",
                  file=sys.stderr)
        else:
            print(f"[opencode] field-only ({why})", file=sys.stderr)
        verdict = {"verdict": "field-only", "reason": why, "sim": None,
                   "nov": None, "ex_idx": None, "route": route}
        if mid_stream:
            # headers + (maybe) the gate monologue already went out: the only
            # honest move is an assistant delta carrying the banner.
            try:
                for chunk in _openai_sse_chunks(model, text):
                    self.wfile.write(chunk)
                self.wfile.flush()
            except Exception as e:
                print(f"[opencode] field-only stream error: {e}",
                      file=sys.stderr)
        else:
            self._serve_intercept_answer(model, text, False)
        # captured for the audit trail only -- never learned into the field.
        try:
            self._log_turn(verdict, None, None)
        except Exception:
            pass

    # ---- /membrane/ask: the slice-4 peer endpoint (read-only kNN over the ----
    # LOCAL field). A sibling membrane asks on behalf of its own turn; we answer
    # from our lived knowledge (never the commons -- they have that file too).
    # All failures degrade to a JSON {"hit": false}; the asker falls through
    # to its model (the safe direction).
    # k>1 selects the MCP tool shape: up to k pairs (same hygiene screen:
    # safe, non-capture-lane, above the bar) + field_size. The k=1 shape is
    # byte-compatible with the peer contract (hit/sim/situation/action/ts).
    def _membrane_ask(self) -> None:
        """GET /membrane/ask?q=...[&k=N]: the field's answer to a question --
        'block' (past answers + entities, rendered) and 'entities' (the
        views), plus the v1 keys a sibling seat reads: 'hit', 'sim',
        'situation'/'action' (the best past answer) and 'hits' (k of them)."""
        import urllib.parse
        srv = self.server
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        q = (qs.get("q") or [""])[0].strip()
        try:
            sim_min = float((qs.get("sim_min") or [PEER_ASK_SIM_MIN])[0])
        except ValueError:
            sim_min = PEER_ASK_SIM_MIN
        try:
            k = max(1, min(8, int((qs.get("k") or ["1"])[0])))
        except ValueError:
            k = 1
        out = {"fid": getattr(srv, "fid", "?"), "hit": False, "hits": []}
        fld = _efield_of(srv)
        if q and fld is not None:
            try:
                from .entity_block import past_answers
                rows = past_answers(fld, q, k=k, sim_min=sim_min)
                out["hits"] = [{"sim": r["sim"], "situation": r["q"][:400],
                                "action": " ".join(r["answer"])[:1200], "q_event": r["q_n"],
                                "events": r["a_n"], "seat": fld.origin, "src": "field"}
                               for r in rows]
                if out["hits"]:
                    out.update({"hit": True, **{kk: out["hits"][0][kk] for kk in
                                                ("sim", "situation", "action", "seat")},
                                "ts": _now_iso()})
            except Exception as e:
                out["efield_error"] = str(e)[:200]
            out.update(_efield_answer(srv, q))
            out["field_size"] = int(((_efield_vitals(srv) or {}).get("entities") or {}).get("live", 0))
        self._json_out(out)

    # ---- /membrane/learn + /membrane/forget: the MCP write lanes -----------
    # The MCP tool surface (membrane/mcp_server.py) relays structured writes
    # here. They run the EXACT executors the thought-intercept uses
    # (_agent_learn / _agent_forget): same dedup/variety/length guards, same
    # audit trail (origin: mcp), same agent_writes kill switch. Returns are
    # JSON mirrors of the executors' text (the MCP layer formats for the
    # model); failures degrade to {"written": false} / {"removed": null}.
    def _membrane_learn(self) -> None:
        """POST /membrane/learn {lesson}: the MCP write lane -- a NOTE event."""
        srv = self.server
        payload = str(self._json_body().get("lesson") or "").strip()
        if not payload:
            self._json_err_raw(400, "lesson required"); return
        if not getattr(srv, "agent_writes", True):
            self._json_out({"written": False,
                            "reason": "agent writes are off (--no-agent-writes)"}); return
        why = _learn_refusal(payload)
        if why:
            self._json_out({"written": False, "reason": why}); return
        note = _efield_learn(srv, payload, "model")
        if not note:
            self._json_out({"written": False, "reason": "the entity field is unavailable"})
            return
        _write_audit(srv.membrane, {"event": "agent-learn", "origin": "mcp",
                                    "tag": "agent-note", "payload": _clip(payload, 400),
                                    "note": note.strip()})
        self._json_out({"written": True, "note": note.strip()})

    def _membrane_forget(self) -> None:
        """POST /membrane/forget {spec}: '#N' / '3-7' forget those events
        (agent cap); anything wordier returns the nearest events as
        candidates and deletes nothing."""
        srv = self.server
        spec = str(self._json_body().get("spec") or "").strip()
        if not spec:
            self._json_err_raw(400, "spec required"); return
        fld = _efield_of(srv)
        if fld is None or srv.membrane.embedder is None:
            self._json_out({"removed": None, "candidates": [],
                            "reason": "the entity field or the embedder is unavailable"}); return
        nums = _parse_agent_forget(spec)
        if nums is None:
            try:
                near = fld.events_near(srv.membrane.embedder.embed([spec])[0], k=_FORGET_LIMIT)
            except Exception:
                near = []
            self._json_out({"removed": None, "candidates": [
                {"n": d.get("n"), "sim": round(float(s_), 4),
                 "text": _clip(d.get("text") or d.get("tool") or "", 120)}
                for d, s_ in near if d.get("n") is not None]}); return
        if len(nums) > _AGENT_FORGET_LIMIT:
            self._json_out({"removed": None, "candidates": [],
                            "reason": f"{len(nums)} events exceed the agent cap "
                                      f"({_AGENT_FORGET_LIMIT})"}); return
        res = _efield_forget(srv, nums)
        _write_audit(srv.membrane, {"event": "forget-events", "origin": "mcp",
                                    "results": res, "spec": _clip(spec, 200)})
        self._json_out({"removed": sum(1 for r in res if r.get("key") and not r.get("already")),
                        "events": res})

    # ---- /membrane/delegate: model-pointed work, router-disposed ------------
    def _membrane_delegate(self) -> None:
        """A model (or subagent) hands a subtask to membrane; the ROUTER
        disposes. The caller never names the target: the task routes under
        the operator's ceiling like any turn, on the shared ladder. A
        caller may ASK for the exploration lane (@explore) -- evidence
        farming is a first-class lane, not a chat hijack. Depth 1: a
        delegated turn is a leaf (no nested delegation). Capture: the
        exchange lands as a 'delegate' exemplar, tagged, so model-to-model
        chatter stays separable from human chat."""
        srv = self.server
        mem = srv.membrane
        body = self._json_body()
        task = str(body.get("task") or "").strip()
        if not task:
            self._json_err_raw(400, "task required"); return
        if getattr(srv, "_delegate_busy", False):
            self._json_err_raw(429, "delegate busy (depth guard)"); return
        router = getattr(srv, "router", None)
        if router is None:
            self._json_err_raw(503, "router off; no delegation"); return
        lane = str(body.get("lane") or "").strip().lower()
        # THE OPERATOR'S CEILING IS NOT NEGOTIABLE: the caller may request
        # the explore lane (deliberate farming) but never raise the ceiling
        # above the operator's opencode pick. The request's own "ceiling"
        # field is ignored -- a model cannot mint authority.
        ceiling = str(getattr(srv, "op_ceiling", "") or "").strip() or None
        if lane == "explore":
            ceiling = EXPLORE_LANE
        else:
            ceiling = normalize_lane(ceiling)
        situ = None
        if mem.embedder is not None:
            try:
                situ = mem.embedder.embed([task[:512]])[0]
            except Exception:
                situ = None
        srv._delegate_busy = True
        try:
            rec, info = router.route(situ, urgency=float(body.get("urgency")
                                                        or 1.0),
                                     needs_tools=False, ceiling=ceiling,
                                     ctx_tokens=Registry.estimate_tokens(
                                         [{"content": task[:8000]}]))
        except Exception as e:
            srv._delegate_busy = False
            self._json_err_raw(500, f"router error: {e}"); return
        if rec is None:
            srv._delegate_busy = False
            self._json_out({"ok": False, "reason": info.get("reason"),
                            "field_only": True}); return
        model = rec.name
        messages = [{"role": "system",
                     "content": "You are a delegated subtask worker. "
                                "Answer the task directly, tersely."},
                    {"role": "user", "content": task[:8000]}]
        rq = urllib.request.Request(
            srv.backend + "/v1/chat/completions",
            data=json.dumps({"model": model, "messages": messages,
                             "stream": False}).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(rq, timeout=300) as resp:
                ans = json.loads(resp.read())
        except Exception as e:
            router.note_outage(model, f"delegate: {str(e)[:120]}")
            srv._delegate_busy = False
            self._json_err_raw(502, f"delegated rung down: {e}"); return
        dt = time.monotonic() - t0
        srv._delegate_busy = False
        content = str(((ans.get("choices") or [{}])[0].get("message") or {})
                      .get("content") or "").strip()
        survived = bool(content)
        try:
            router.note_latency(model, dt)
            if info.get("kind") == "cloud":
                router.record_spend(float(info.get("price")
                                          or CLOUD_DEFAULT_PRICE))
            router.note_outcome(model, situ, survived=survived)
        except Exception:
            pass
        cap = getattr(srv, "capturer", None)
        if survived and cap is not None:
            cap.on_turn(f"delegated: {task}", content)
        self._json_out({"ok": survived, "model": model,
                        "reason": info.get("reason"), "explored":
                        bool(info.get("explored")), "latency_s": round(dt, 3),
                        "answer": content[:4000]})

    # ---- /membrane/entity-track: the model's banner lane (MCP relay) ----
    def _peer_post(self) -> None:
        """POST /membrane/peer/post: the cross-seat chat lane (the
        operator's multi-seat design)."""
        srv = self.server
        peers = getattr(srv, "peers", None)
        if peers is None:
            self._json_err_raw(503, "peer substrate unavailable"); return
        body = self._json_body()
        text = str(body.get("text") or "").strip()
        if not text:
            self._json_err_raw(400, "text required"); return
        ok = peers.post(text, reply_to=str(body.get("reply_to") or ""))
        self._json_out({"ok": ok})

    def _peer_seats(self) -> None:
        """GET /membrane/peer/seats: the registry read (who else is
        connected; dead proxies expire by TTL)."""
        peers = getattr(self.server, "peers", None)
        if peers is None:
            self._json_err_raw(503, "peer substrate unavailable"); return
        self._json_out({"seats": peers.list_seats()})

    def _entity_track_set(self) -> None:
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        # the eviction covenant counts cells: refresh the hint so the
        # story-poorest eviction sees the live field
        body = self._json_body()
        key = str(body.get("key") or "").strip()
        if not key:
            self._json_err_raw(400, "key required"); return
        res = est.watch_track(key, boundary=str(body.get("boundary") or ""),
                              label=str(body.get("label") or ""))
        if not res.get("ok"):
            self._json_err_raw(400, str(res.get("error"))); return
        # THE GEOMETRY PREREQUISITE: a fresh track needs its entity doc +
        # seeded ring IMMEDIATELY (scripts read the ring; the night pass
        # may be 15 minutes away). One consolidation pass over this
        # entity's cells, synchronously -- the track's first render has
        # geometry instead of a bare fallback.
        try:
            from .night import consolidate_entities
            if getattr(srv, "efield", None) is None:
                raise RuntimeError("entity field is off: no deltas to drift on")
            consolidate_entities(getattr(srv, "efield"), est, mem=srv.membrane,
                                 materialize=0, only_keys=[key])
        except Exception:
            pass
        self._json_out(res)

    def _entity_track_remove(self) -> None:
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        body = self._json_body()
        key = str(body.get("key") or "").strip()
        if not key:
            self._json_err_raw(400, "key required"); return
        self._json_out({"removed": est.unwatch_track(key)})

    def _entity_related(self) -> None:
        """GET /membrane/entity-related?q=<text>[&k=8]: the entity-graph
        reader -- entities related to the query (named hits first, then
        centroid-nearest), each with its co-occurrence neighbors."""
        srv = self.server
        from urllib.parse import parse_qs
        qs = parse_qs(self.path.split("?", 1)[-1])
        q = (qs.get("q") or [""])[0].strip()
        if not q:
            self._json_err_raw(400, "query param ?q= required"); return
        try:
            k = min(max(int(qs.get("k", ["8"])[0]), 1), 20)
        except ValueError:
            k = 8
        fld = _efield_on(srv)
        if fld is not None:
            try:
                from .entity_block import related
                try:
                    qvec = fld.emb.embed([q])[0]
                except Exception:
                    qvec = None
                self._json_out({"query": q, "related": related(fld, q, qvec, k)}); return
            except Exception as e:
                self._json_err_raw(500, f"related lookup failed: {e}"); return
        self._json_err_raw(503, "the entity field is off"); return

    def _entity_add(self) -> None:
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        body = self._json_body()
        res = est.add(cls=str(body.get("class") or ""),
                      name=str(body.get("name") or ""),
                      summary=str(body.get("summary") or ""),
                      links=body.get("links") or [])
        self._entity_audit("add", res)
        if not res.get("ok"):
            self._json_err_raw(400, str(res.get("error"))); return
        self._json_out(res)

    def _entity_edit(self) -> None:
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        body = self._json_body()
        spec = str(body.get("spec") or "").strip()
        if not spec:
            self._json_err_raw(400, "spec required (eid or name fragment)")
            return
        res = est.edit(spec, summary=body.get("summary"),
                       links=body.get("links"), cls=body.get("class"),
                       merged_into=body.get("merged_into"),
                       nudge_script=body.get("nudge_script"),
                       delete_script=body.get("delete_script"),
                       mention_script=body.get("mention_script"),
                       thought_script=body.get("thought_script"),
                       dreamed_script=body.get("dreamed_script"),
                       tick_script=body.get("tick_script"),
                       properties=body.get("properties"),
                       publish=bool(body.get("publish")),
                       unpublish=bool(body.get("unpublish")))
        self._entity_audit("edit", res)
        if not res.get("ok"):
            self._json_err_raw(400, str(res.get("error"))); return
        self._json_out(res)

    def _entity_remove(self) -> None:
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        body = self._json_body()
        spec = str(body.get("spec") or "").strip()
        if not spec:
            self._json_err_raw(400, "spec required (eid or name fragment)")
            return
        n = est.remove(spec)
        self._entity_audit("remove", {"ok": n > 0, "eid": spec, "count": n})
        self._json_out({"removed": n})

    def _entity_audit(self, op: str, res: dict) -> None:
        """Every model-side entity mutation hits the operator's ledger."""
        try:
            mem = self.server.membrane
            _write_audit(mem, {"event": f"agent-entity-{op}",
                               "origin": "mcp",
                               "eid": str(res.get("eid") or ""),
                               "ok": bool(res.get("ok"))})
        except Exception:
            pass

    def _membrane_intend(self) -> None:
        """POST /membrane/intend: the MCP membrane_intend relay -- the
        create-or-pin + the speech announcement (the activation is the
        visible moment the dream chain starts; drop intent is one
        command away). The method lives ON the handler: the route
        existed but the body sat at module level, so every called lane
        died with RemoteDisconnected (and POST fell to the read-only
        catch-all before the route existed at all)."""
        srv = self.server
        body = self._json_body()
        text = str(body.get("text") or "").strip()
        if not text:
            self._json_err_raw(400, "text required"); return
        res = _membrane_intent_create_or_pin(srv, text,
                                             ttl=str(body.get("ttl") or ""))
        if not res.get("ok"):
            self._json_err_raw(400, str(res.get("error"))); return
        try:
            speak(srv, f"goal active: {text[:120]}", kind="note-pattern")
        except Exception:
            pass
        self._entity_audit("intend", {"ok": True, "eid": res.get("eid"),
                                      "created": res.get("created")})
        self._json_out(res)

    def _entity_inherit(self) -> None:
        """POST /membrane/entity/inherit: the multi-parent ISA relation
        (the operator's design -- 'a letter points to letters', with
        multiple inheritance). Child gains one or more parent concepts;
        constraints propagate across ALL branches; the child's position
        comes from its own lived instances, the parents classify it."""
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        body = self._json_body()
        spec = str(body.get("spec") or "").strip()
        parents = body.get("parents") or []
        if isinstance(parents, str):
            parents = [parents]
        parents = [str(p).strip() for p in parents if str(p).strip()]
        if not spec or not parents:
            self._json_err_raw(400, "spec + parents required"); return
        res = est.set_parents(spec, parents)
        self._entity_audit("inherit", res)
        if not res.get("ok"):
            self._json_err_raw(400, str(res.get("error"))); return
        self._json_out(res)

    def _membrane_codegraph(self) -> None:
        """POST /membrane/codegraph: the MCP surface for the AST graph
        (read-only, same query() text as the CLI + the intercept).
        Body {"spec": "signature _strip_context"} (or {"op": "stats"}).
        The answer carries symbols:<qualname> entity ids -- the graph's
        qualnames ARE the entity key space (entities.py harvests the
        same kind:value form; refine_entities promotes to these
        qualnames), so an MCP client can chain: ask the graph, plant a
        track on the symbol entity with the same key."""
        srv = self.server
        body = self._json_body()
        spec = str(body.get("spec") or body.get("op") or "stats").strip()
        if not spec:
            spec = "stats"
        g = _codegraph_graph(srv)
        if g is None:
            self._json_err_raw(503, "codegraph unavailable (graph failed "
                                "to build)"); return
        from .codegraph import query as cg_query, stats as cg_stats
        text = cg_query(g, spec)
        # the machine-readable half: the symbols the spec resolved to
        # (the entity-id chain: symbols:<qualname> is a plantable track)
        try:
            s = cg_stats(g)
        except Exception:
            s = {}
        self._json_out({"text": text, "symbols": sorted(
            (g.get("symbols") or {}).keys())[:400],
            "n_symbols": s.get("n_symbols"),
            "n_files": s.get("n_files"),
            "shape": "symbols:<qualname> -- the entity key space "
                     "(membrane_entity_add / watch_entity compatible)"})

    def _junk_cut_set(self) -> None:
        """POST /membrane/junk-cut: the live junk-cut knob. spec '#N'
        targets a spatial watch, 'all'/'default'/track-key hit the entity
        store; every change audited."""
        srv = self.server
        body = self._json_body()
        spec = str(body.get("spec") or "").strip()
        cut = body.get("cut")
        if not spec:
            self._json_err_raw(400, "spec required ('#N', track key, "
                                    "'all', or 'default')"); return
        # watch spec?
        if spec.startswith("#") and spec[1:].isdigit():
            board = getattr(srv, "_watch_board", None)
            w = board.watches.get(int(spec[1:])) if board else None
            if w is None:
                self._json_err_raw(400, f"no watch {spec}"); return
            try:
                w.junk_sim = (None if cut in (None, "")
                              else max(0.0, min(1.0, float(cut))))
            except (TypeError, ValueError):
                self._json_err_raw(400, "cut must be a number 0..1"); return
            board.save()
            try:
                _write_audit(srv.membrane,
                             {"event": "agent-junk-cut", "origin": "mcp",
                              "spec": spec, "cut": w.junk_sim,
                              "eid": spec})
            except Exception:
                pass
            self._json_out({"ok": True, "spec": spec, "junk_sim": w.junk_sim,
                            "tracks": 1}); return
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        res = est.set_junk_cut(spec, cut)
        if res.get("ok"):
            try:
                _write_audit(srv.membrane,
                             {"event": "agent-junk-cut", "origin": "mcp",
                              "spec": spec, "cut": res.get("junk_sim"),
                              "eid": spec})
            except Exception:
                pass
        self._json_out(res)

    def _entity_track_list(self) -> None:
        srv = self.server
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        self._json_out({"tracks": est.list_tracks()})

    def _membrane_run(self) -> None:
        """POST /membrane/run {command, timeout_s?}: the run lane -- the
        model's exec goes THROUGH its entities (one command -> one c:
        entity, engine-executed, the result on the doc, an observation in
        the field, errors in the script-error lanes). The kill switch:
        MEMBRANE_RUN=0 refuses (the seat runs zero-built-ins by default;
        this is the operator's ablation)."""
        srv = self.server
        if os.environ.get("MEMBRANE_RUN") == "0":
            self._json_out({"ok": False, "reason": "the run lane is off "
                            "(MEMBRANE_RUN=0)"}); return
        est = getattr(srv, "entity_store", None)
        if est is None:
            self._json_err_raw(503, "entity layer unavailable"); return
        b = self._json_body()
        command = str(b.get("command") or "").strip()
        timeout_s = b.get("timeout_s")
        if not command:
            self._json_err_raw(400, "command required"); return
        try:
            from .night import run_command_entity
            out = run_command_entity(_efield_of(srv), est, srv.membrane,
                                     command, timeout_s=timeout_s)
        except Exception as e:
            self._json_out({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
            return
        out.pop("doc", None)          # the doc summary is the reply's core
        out["command"] = command
        self._json_out(out)

    # ---- /membrane/state: the JSON instrument ----
    def _membrane_state(self) -> None:
        srv = self.server
        # THE STATE CACHE (the slow-endpoint fix): the state query builds
        # the full trajectory hydration + WHERE WE ARE + the surface ring
        # -- O(field_size) per call, 20+ seconds on a fresh boot. Cached
        # with a 60s TTL: the monitoring surface doesn't need to be fresh
        # to the second, and the cache prevents the state query from
        # blocking the serial serve loop (the operator's socket errors).
        cached = getattr(srv, "_state_cache", None)
        if cached and time.time() - cached.get("ts", 0) < 60:
            self._json_out(cached["data"])
            return
        mem = srv.membrane
        fld = _efield_of(srv)
        tail, n_verd = _audit_tail(mem.audit_path)
        vit = _efield_vitals(srv) or {}
        out = {
            "mode": "DEV",
            "backend": srv.backend,
            "embed_model": getattr(mem.embedder, "model", None),
            # the viewer's counters and cloud: live entities, their states
            "field_size": int((vit.get("entities") or {}).get("live", 0)),
            "efield": vit or None,
            "retrieval": {"ctx_chars": srv.rag_ctx_chars,
                          "no_context": bool(getattr(srv, "no_context", False))},
            "exemplars": _entity_points(srv, fld) if fld is not None else [],
            "verdicts": tail,
            "n_verdicts": n_verd,
            # the NightClock's last pass (births, junk, activity, commons)
            "night": (getattr(srv, "night_stats", None) or None),
            "state_intercept": srv.state_intercept,
            "audit_records": _audit_count(mem.audit_path),
            "frame_wait": _frame_wait_summary(srv) or None,
            # the null-recall lane: the session's all-orthogonal asks
            "recall_null": (getattr(srv, "_recall_null", None) or None),
            # THE TOTALITY VIEW: everything membrane sent to / received from
            # the model on the last turns; capped ring, newest last.
            "surface": _surface_tail(srv),
            # the same WHERE WE ARE the seat's model gets (a peek: drains nothing)
            "where_we_are": _where_we_are(srv, None, drain=False),
            "entity_tracks": (getattr(srv, "entity_store", None).list_tracks()
                              if getattr(srv, "entity_store", None) else []),
        }
        _est = getattr(srv, "entity_store", None)
        if _est is not None:
            # THE SCRIPT INSTRUMENT: entities whose scripts errored -- the
            # strike counter, the disable flag, and (since the script lane
            # speaks to the field) the persisted last-error text
            failing, disabled = [], 0
            for eid, ent in (_est.docs or {}).items():
                p = ent.properties or {}
                if not (p.get("script_failures") or p.get("script_disabled")
                        or p.get("script_last_error")):
                    # a run-lane error persists script_last_error without a
                    # strike counter (commands never auto-disable) -- it
                    # still counts as failing here, the finding surface
                    continue
                if p.get("script_disabled"):
                    disabled += 1
                failing.append({"eid": eid,
                                "failures": int(p.get("script_failures") or 0),
                                "disabled": bool(p.get("script_disabled")),
                                "last_error": (p.get("script_last_error")
                                               or "")[:200]})
            out["scripts"] = {"failing": failing[:20], "disabled": disabled}
        if fld is not None:
            from .claims import COMMONS
            commons = [d for _, d in fld.b.scan("claims", {"origin": COMMONS})]
            out["commons"] = {"claims": len(commons),
                              "consensus": sum(1 for d in commons if d.get("status") == "consensus"),
                              "disputed": sum(1 for d in commons if d.get("status") == "disputed"),
                              "peers_live": (len(srv.gpeers.livelist())
                                             if getattr(srv, "gpeers", None) else 0)}
        # THE THINK QUEUE (working memory, on the mind): what the fast
        # clock is chewing, oldest first, bounded by the covenant -- plus
        # THE MIND LOG (the inner speech, visible): what it decided.
        th = getattr(srv, "thinker", None)
        out["think"] = {
            "queue": [{"kind": i.get("kind"),
                       "age_s": round(time.time() - float(i.get("ts", 0)),
                                      1)}
                      for i in getattr(th, "queue", [])],
            "stats": dict(getattr(th, "stats", {}) or {}),
            "mind": list(getattr(th, "mind_log", []) or []),
        }
        # THE CACHE STORE: the built state is cached for 60s (the TTL)
        srv._state_cache = {"data": out, "ts": time.time()}
        self._json_out(out)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Membrane field + opencode: a local field-backed coding "
                    "agent (math/state answered internally; model work proxied "
                    "to Ollama).")
    ap.add_argument("--listen", type=int, default=11500,
                    help="port for the membrane OpenAI-compatible proxy")
    ap.add_argument("--no-viewer", dest="viewer", action="store_false",
                    default=True,
                    help="don't serve the universe map at /viewer (ON by "
                         "default; the old opt-in flag is retired -- the "
                         "map is the seat's face now)")
    # MEMBRANE_NO_CONTEXT=1 makes the stateless seat the DEFAULT for every
    # spawner that sources the seat env (a supervisor, an HA self-heal, a
    # manual launch) -- one env line instead of N launch lines to keep in
    # sync; --with-context overrides it for one run.
    _nc_env = os.environ.get("MEMBRANE_NO_CONTEXT", "").strip().lower() \
        not in ("", "0", "false", "no", "off")
    ap.add_argument("--with-context", dest="no_context", action="store_false",
                    default=_nc_env,   # shared dest: the FIRST default wins
                    help="keep opencode's transcript even when "
                         "MEMBRANE_NO_CONTEXT=1 is set")
    ap.add_argument("--no-context", dest="no_context", action="store_true",
                    default=_nc_env,
                    help="THE STATELESS LLM (the field is the only "
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
    ap.add_argument("--no-open", dest="open_browser", action="store_false", default=True,
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
                         "stdio MCP server exposing membrane_query/learn/"
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
    ap.add_argument("--serve", action="store_true",
                    help="THE NATIVE SERVE (opencode replaced): serve "
                         "membrane's own opencode-compatible REST/SSE "
                         "surface (the companion app's endpoint; basic "
                         "auth generated once beside the field) -- "
                         "session/message/abort/event, the turn loop "
                         "riding THIS seat's /v1 loopback (injection, "
                         "capture, banner, routing all real), tool "
                         "parts streamed as message.part.updated "
                         "events. MEMBRANE_LEGACY_SERVE=1 restores the "
                         "old opencode child lane.")
    ap.add_argument("--serve-port", type=int, default=4096,
                    help="port for the native serve surface (default "
                         "4096; walks up if taken)")
    ap.add_argument("--serve-host", default="0.0.0.0",
                    help="bind address for the serve surface (default "
                         "0.0.0.0 -- WiFi/VPN reach; 127.0.0.1 keeps it "
                         "local-only)")
    ap.add_argument("--serve-user", default="opencode",
                    help="basic-auth user the serve asks for (default "
                         "opencode)")
    ap.add_argument("--serve-pass", default=None,
                    help="basic-auth password override (default: "
                         "generated once and persisted beside the field)")
    ap.add_argument("--no-continue", dest="cont", action="store_false",
                    help="don't resume opencode's last session (default: "
                         "resume; -c/-s in passthrough still win)")
    ap.add_argument("passthrough", nargs="*",
                    help="extra args passed to opencode (use -- to separate)")
    args = ap.parse_args()
    passthrough = args.passthrough
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
        _log_fh = open(log_path, "a", buffering=1)
        sys.stderr = _log_fh

    backend = args.backend.rstrip("/")
    chat_models = _chat_capable_models(backend)
    if not chat_models:
        print(f"[opencode] WARNING: no chat-capable Ollama models at {backend} "
              f"(is Ollama running?)", file=sys.stderr)
    model = _pick_default_model(chat_models, args.model)
    if model is None and args.model:
        model = args.model          # respect an explicit choice even if unlisted

    mem = Seat(args.field, args.audit)   # the seat: audit, embedder ladder, locks

    listen = _pick_listen_port(args.listen)
    if listen != args.listen:
        print(f"[opencode] port {args.listen} busy -> using {listen} "
              f"(pass --listen to pin)", file=sys.stderr)
    serve_wanted = bool(args.serve) or bool(args.launch)
    serve_port = _pick_serve_port(args.serve_port) if serve_wanted else None
    if serve_port is not None and serve_port != args.serve_port:
        print(f"[opencode] serve port {args.serve_port} busy -> using "
              f"{serve_port} (pass --serve-port to pin)", file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", listen), MembraneOpenAIHandler)
    srv.membrane = mem
    srv.viewer = bool(args.viewer)   # dev field viewer at /viewer (OFF default)
    # THE STATELESS LLM (the field is the only memory): --no-context
    # strips the transcript history per turn; coherence comes from the
    # reconstruction block + the recall (the RAG budget scales).
    srv.no_context = bool(getattr(args, "no_context", False))
    srv.tool_keep = int(getattr(args, "tool_keep", 3))
    mem.server_ref = srv          # the router-outcome loop reads srv.router
    srv.backend = backend
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
    # THE EVENT BUS (MQTT, cb's push transport): boot at startup, degrades
    # to silent when the broker is unreachable (the covenant)
    from .events import EventBus
    srv.seat_id = f"proxy-{listen}"
    srv._event_bus = EventBus(srv.seat_id)
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
    srv._intent_board = None
    try:
        srv._intent_board = IntentBoard(_wdir / "intents.json",
                                        embedder=srv.membrane.embedder)
        _n_i = len(srv._intent_board.open_intents())
        print(f"[opencode] intentions: {_n_i} open "
              f"(cap {MAX_INTENTS})", file=sys.stderr)
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
            # the session's first event number: the 'now' line counts what
            # moved since the seat booted
            srv._efield_boot_seq = int(srv.efield._ostats.get("seq_last") or 0)
            if srv.capturer.mode == "on":
                srv.efield.listeners.append(_efield_watch_listener(srv))
                from .efmind import DreamSource, on_applied_fn
                srv.efield.listeners.append(on_applied_fn(srv, lambda: _own_session_prefix(srv)))
                srv.dream_source = DreamSource(srv.efield)
                _install_judges(srv)
            print(f"[efield] capture {srv.capturer.mode} "
                  f"({type(be).__name__})", file=sys.stderr)
    except Exception as e:
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
        # THE CLOCK (the operator's 2026-10-01 ask): plant `tool:clock`
        # once at boot -- idempotent (an already-living, model- or
        # operator-Rewritten clock survives every restart). Degrades
        # silently (the store is the guard).
        try:
            from .night import seed_clock_entity
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
        # THE PEER SUBSTRATES (the operator's design): the proxy REGISTRY
        # (ephemeral bucket, TTL docs, heartbeats -- who else is connected)
        # + the PEER CHANNEL (permanent, a chat lane between the LLMs,
        # visible on every seat's banner). Degraded silently when
        # couchbase is down (the same covenant as the entity layer).
        try:
            from .peers import PeerSubstrate
            srv.peers = PeerSubstrate(
                field_path=str(_wdir / "field.json"),
                port=listen, model=str(srv.seat_model))
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
            _tk = [k for k in srv.entity_store.tracks.keys()
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
    if not getattr(args, "no_thinker", False):
        try:
            from .thinker import Thinker
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
                # THE IDLE SPEECH DRAIN (the operator's requirement: shown
                # in opencode even without a turn): held messages push
                # through the peer channel -- every connected seat's UI
                # shows them immediately, no model turn needed.
                try:
                    drain_speech(srv)
                except Exception:
                    pass
                fld = _efield_on(srv)
                if fld is None:
                    continue
                try:
                    from .efmind import night as _ef_night
                    srv.night_stats = _ef_night(srv, fld,
                                                getattr(srv, "entity_store", None))
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
                max_local_gib=float(args.max_local_gib))
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
            for pname, prov in _auth.PROVIDERS.items():
                key = _auth.load_api_key(pname)
                if not key:
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
    if srv.embed_model_arg:
        emb = _probe_embedder(backend, srv.embed_model_arg)
        if emb is not None:
            mem.embedder = emb
            srv.pending_embed_error = None
    if mem.embedder is None:
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
    # membrane_run / POST /membrane/run.
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
        mcp_state = f"on ({len(_mcp_tool_lines())} membrane_* tools)"
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
    env = dict(os.environ)
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
    # the serve lane's credentials BEFORE the board (both print the pair's
    # provenance): generated once, persisted beside the field, reused by
    # every later boot (a device's saved connection survives restarts)
    serve_su = serve_sp = serve_cred_path = None
    serve_pw_new = False
    serve_addr = None
    legacy_serve = os.environ.get("MEMBRANE_LEGACY_SERVE") == "1"
    # THE SURFACE runs for every launch boot (the TUI attaches to it as a
    # client -- stage 3.5), loopback-only unless --serve exposes it to the
    # room; --serve keeps its credentials/board semantics.
    surf_host = args.serve_host if args.serve else "127.0.0.1"
    if serve_wanted:
        serve_cred_path = str(Path(args.field).resolve().parent
                              / "serve-auth.json")
        serve_su, serve_sp, serve_pw_new = _serve_credentials(
            serve_cred_path, args.serve_user, args.serve_pass)
        if args.serve and legacy_serve:
            env["OPENCODE_SERVER_USERNAME"] = serve_su
            env["OPENCODE_SERVER_PASSWORD"] = serve_sp
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
                    f"  serve: {f'http://{serve_addr}:{serve_port} (native serve, basic auth user {serve_su})' if serve_wanted else 'off'}\n"
          f"  opencode config: {cfg_path}",
          file=sys.stderr)
    if args.serve:
        _serve_board(serve_port, args.serve_host, serve_su, serve_sp,
                     serve_pw_new, serve_cred_path)
    native_up = False
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
            system = ""
            try:
                if instr_path and os.path.isfile(instr_path):
                    system = open(instr_path).read()
            except Exception:
                system = ""
            _srv_model = (srv.seat_model if not srv.model_follows_chat
                          else model or srv.seat_model or "membrane")
            runner = TurnRunner(SSEHub(), sess_store, msg_store,
                                loopback_ladder(listen),
                                mcp_executor(listen),
                                system=system, model=_srv_model)
            start(serve_port or 4096, surf_host, serve_su,
                  serve_sp, cfg, runner, sess_store, msg_store)
            native_up = True
            print(f"[opencode] native serve up: session store "
                  f"{d / 'serve-sessions.json'}; the proxy's /v1 is "
                  "the turn lane", file=sys.stderr)
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
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    if args.launch:
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    child = None
    try:
        # dev viewer: served at /viewer (world controls hidden) + auto-open once
        # the server is bound. Daemon thread; never fatal on browser failure.
        # --no-open or MEMBRANE_NO_OPEN=1 silences the tab for headless runs.
        if args.viewer and args.open_browser:
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
                        cmd, env=_attach_env(env, serve_su, serve_sp))
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
            watched_pid = None
            try:
                if pid_path.exists():
                    watched_pid = int(pid_path.read_text().strip())
            except Exception:
                watched_pid = None
            while True:
                srv.handle_request()
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
            srv.timeout = 0.25
            while child.poll() is None:
                srv.handle_request()
    except KeyboardInterrupt:
        print("\n[membrane-opencode] bye", file=sys.stderr)
    finally:
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
        for p in (cfg_path,):          # the doctrine file is stable: kept
            if not p:
                continue
            try:
                os.unlink(p)
            except OSError:
                pass
        srv.server_close()
        srv._gstop.set()               # the peer heartbeats stop
        if _log_fh is not None:
            try:
                _log_fh.close()
            except OSError:
                pass


# ---- the speech lane: membrane's own voice, pushed into the chat ------------
# THE OPERATOR'S DESIGN: membrane participates in the room -- it speaks when
# it has something worth saying, in both operators' presence, and everything
# it says is SHOWN in opencode (nothing hidden in a side channel). The
# transport is the peer channel (the push: every connected seat's UI shows
# peer posts) + the model-turn injection (the relay: the model carries
# membrane's unread messages into the conversation as marked blocks).

_SPEAK_CAP = 6          # the speech budget: max queued messages (anti-flood)
_SPEAK_TTL_S = 6 * 3600 # held messages expire (stale observations are noise)


def speak(srv, text: str, kind: str = "note") -> bool:
    """Membrane says something in the room. SEAT-LOCAL (the operator's
    call: membrane's speech is private to this seat -- no MQTT broadcast,
    no peer-channel relay, no cross-seat delivery). The message enters
    the speech queue; the next model-bound request carries it as a marked
    block into THIS conversation, where it enters THIS timeline and
    learns from THIS operator's reaction.

    THE PLANNER (the closed loop, observe -> consult -> reinforce): the
    ActTable is CONSULTED before the queue -- the context cluster selects
    the act (the transition table's argmax over what was lived here).
    The caller's proposed act stays when it's what the table wants; the
    table's own pick (or stay-silent) otherwise governs: stay-silent
    drops the message (the honest default -- an unlearned context says
    nothing), any other learned act speaks with that act as the kind.
    Alerts keep their kind (the planner chooses the realization, not
    the content)."""
    q = getattr(srv, "_speak_queue", None)
    if q is None:
        srv._speak_queue = q = []
    srv_act = getattr(srv, "_act_table", None)
    ctx_sig = getattr(srv, "_last_context_sig", "") or "unknown"
    act = kind
    if srv_act is not None:
        planned = srv_act.select(ctx_sig)
        act = kind if planned == kind else planned
        if act == "stay-silent":
            # the planner vetoes: an unlearned (or silent-learned) context
            # holds its tongue. The observation still lands -- silence in
            # this context IS the label the reinforcement learns from.
            srv_act.observe(ctx_sig, "stay-silent")
            return False
        srv_act.observe(ctx_sig, act)
    msg = {"text": str(text)[:2000], "kind": kind, "act": act,
           "sig": ctx_sig, "ts": time.time()}
    q.append(msg)
    del q[:-_SPEAK_CAP]               # bounded: drop the oldest overflow
    return True


def _init_speak(srv) -> None:
    """THE THINKER'S HELLO (planner-gated): one line on boot -- the mind
    exists, the field's size, thinker + dreamer humming. Rides speak()'s
    ActTable gate (stay-silent holds unless the table has learned boot
    contexts speak). A boot failure never silences the hello: this sits
    OUTSIDE the organ-boot try blocks."""
    import sys as _s
    print(f"[thinker] _init_speak: "
          f"ctx={getattr(srv, '_last_context_sig', 'unknown')!r}",
          file=_s.stderr)
    try:
        _fld = getattr(srv, "efield", None)
        _n = ""
        if _fld is not None:
            try:
                _n = f"field {len(_fld.vitals().get('events', {}))} events, "
            except Exception:
                _n = ""
        speak(srv, f"membrane up: {_n}thinker + dreamer humming",
              kind="report-state")
    except Exception:
        pass


def drain_speech(srv) -> list[dict]:
    """The drain: pop every held message (oldest first), push each through
    the peer channel (the operator's requirement: shown in opencode), and
    return them for the model-turn relay (the marked block). Expired
    messages are dropped silently (stale observations are noise).
    The delivered batch's (planning-sig, act) is stashed as the pending
    reinforcement label -- the NEXT turn's flow grades it."""
    q = getattr(srv, "_speak_queue", None) or []
    now = time.time()
    out = []
    while q:
        m = q.pop(0)
        if now - m["ts"] > _SPEAK_TTL_S:
            continue                  # expired: never shown
        # THE PEER PUSH RETIRED (the operator's ruling: membrane's speech
        # is private to ITS OWN seat -- 'only inject its own messages
        # into chat when membrane decides to'): the drain's peer-channel
        # push made every seat's speech cross-seat (the parallel seat's
        # UI showed the announcer's hellos + ask-operators as peer
        # lines). The MODEL RELAY is the only delivery: the marked block
        # rides the next model-bound request IN THE SPEAKING SEAT, where
        # the conversation carries it and the operator's reaction grades
        # it. Idle delivery rides the night tick (the drain call).
        out.append(m)
    if out:
        srv._pending_speech_label = (out[-1].get("sig"), out[-1].get("act"))
    return out
    if out:
        srv._pending_speech_label = (out[-1].get("sig"), out[-1].get("act"))
    return out


def _speak_block(srv) -> str | None:
    """The model-turn relay: drained messages as ONE marked block riding
    the next model-bound request (the same delivery the intercept answers
    and the thought-asks use)."""
    msgs = drain_speech(srv)
    if not msgs:
        return None
    lines = [f"[membrane-says] {m['text']}" for m in msgs]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
