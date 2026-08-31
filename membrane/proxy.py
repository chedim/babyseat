"""The HTTP proxy: Membrane as a drop-in front of the Ollama model server.

Topology (see README): the chat window used to call the model server directly.

    you (chat window) --[today]--> model server (Ollama /api/chat)

  You slide Membrane in between:

    you (chat window) --> Membrane (proxy, owns the field) --> model server (Ollama)

  The window now talks to Membrane. Membrane embeds each /api/chat query (local
  Ollama/mxbai), runs the gate, and either REPLAYS a recalled action (no model
  call -- the absorb) or FORWARDS to the real model server (the novel) and logs
  the turn to the field + audit. Membrane is local-first (127.0.0.1 only).

Everything that is NOT /api/chat (e.g. /api/embed, /api/tags, /api/generate) is
forwarded transparently, so the proxy is a true drop-in: point any Ollama client
at http://127.0.0.1:11435 instead of :11434 and the field starts self-building.

Both NON-STREAMING and STREAMING /api/chat are gated. On stream:true the gate runs
first; an absorb synthesizes an NDJSON stream from the recalled action (no model
call), an escalation tees the backend stream to the client while accumulating the
response into the field. Set stream:false for the buffered path; stream:true is what
GUI/IDE clients (e.g. Continue, Open WebUI) send by default.

Run:
  PYTHONPATH=. .venv/bin/python -m membrane.proxy --listen 11435
  # then point a client at http://127.0.0.1:11435 (e.g. OLLAMA_HOST=127.0.0.1:11435)
"""

from __future__ import annotations

import json
import os
import random
import re
import signal
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .runtime import (Membrane, OllamaChatResponder, AnthropicMessagesResponder,
                      OllamaJudge, StubJudge, StubResponder, _last_user, _response_text,
                      _content_text, hint_system_message)
from .buffer import BufferView, SituationBuffer
from .cell import Cell
from .curiosity import CURIOSITY_MIN
from .pool import CellPool
from .agent_events import AgentEvent, ToolUseCollector, user_tool_results, ACTION

HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding",
              "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "upgrade"}

# auth-shaped headers we forward to Anthropic but NEVER log or persist. The
# cortex-experiment-token discipline: the API key is passthrough only.
AUTH_HEADERS = {"x-api-key", "authorization", "anthropic-version"}


def _ollama_headers(srv) -> dict:
    """Headers for an Ollama /api/* call: always JSON, plus a bearer token when
    one is set on the server (the optional /settings token -- in-memory only,
    never persisted). The Anthropic path uses per-request passthrough auth
    instead, so this helper is Ollama-only."""
    h = {"Content-Type": "application/json"}
    if getattr(srv, "ollama_token", None):
        h["Authorization"] = "Bearer " + srv.ollama_token
    return h


def _ollama_chat(backend: str, model: str | None, messages: list,
                 *, temperature: float = 0.5, token: str | None = None,
                 timeout: int = 120) -> str:
    """One non-stream Ollama ``/api/chat`` call -> the assistant text (or ``""``).
    This is the shared shape ``_genesis``, ``_tell_story`` and
    ``warmup.generate_palette`` all duplicate (urllib POST, stream=False,
    ``resp["message"]["content"]``); extracted here for new call sites (the
    render path). The existing sites are left as-is to avoid churning working
    code -- a later pass could migrate them onto this helper."""
    if not model or not backend:
        return ""
    body = {"model": model, "stream": False,
            "temperature": float(temperature), "messages": messages}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(backend + "/api/chat", data=data,
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"[llm] chat call failed ({e})", file=sys.stderr)
        return ""
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                return c.strip()
    return ""


# ---- /settings: runtime Ollama server + story model + optional token ----------
# The NON-secret parts (backend URL + story model) persist to settings.json next
# to the field, so a chosen server/model survives restarts. The optional bearer
# token is in-memory ONLY -- never written to disk, never returned by GET
# /settings or /state -- the same discipline as the passthrough API key.

def _settings_path(srv) -> str:
    return os.path.join(getattr(srv, "worlds_dir", "worlds"), "settings.json")


def _load_settings(srv) -> dict:
    """Read persisted {backend, story_model, beat_author, story_mode, feed_rate,
    cpu_target, cycle_speed, entropy_ceiling} from settings.json, or {} if
    absent. Never holds a token -- the token is in-memory only. Tolerates old
    files that still carry a ``world_drive`` key (ignored -- world-drive modes
    are gone)."""
    try:
        with open(_settings_path(srv), "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    out = {}

    def _num(key, lo=None, hi=None):
        v = d.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = float(v)
            if lo is not None: v = max(lo, v)
            if hi is not None: v = min(hi, v)
            return v
        return None

    if isinstance(d.get("backend"), str) and d["backend"].strip():
        out["backend"] = d["backend"].strip().rstrip("/")
    if isinstance(d.get("story_model"), str) and d["story_model"].strip():
        out["story_model"] = d["story_model"].strip()
    if isinstance(d.get("beat_author"), str) and d["beat_author"].strip():
        out["beat_author"] = d["beat_author"].strip()
    if isinstance(d.get("story_mode"), str) and d["story_mode"].strip():
        out["story_mode"] = d["story_mode"].strip()
    if isinstance(d.get("representation"), str) and d["representation"].strip():
        out["representation"] = d["representation"].strip()
    for key in ("feed_rate", "cpu_target", "cycle_speed", "entropy_ceiling"):
        v = _num(key)
        if v is not None:
            out[key] = v
    pe = d.get("progression_every")
    if isinstance(pe, (int, float)) and not isinstance(pe, bool):
        out["progression_every"] = int(pe)
    return out


def _save_settings(srv, backend: str, story_model: str | None,
                   beat_author: str | None = None, story_mode: str | None = None,
                   feed_rate: float | None = None, cpu_target: float | None = None,
                   cycle_speed: float | None = None,
                   entropy_ceiling: float | None = None,
                   representation: str | None = None,
                   progression_every: int | None = None) -> None:
    """Persist the non-secret settings: backend + story model + the growth dials
    (beat_author, story_mode, feed_rate, cpu_target, cycle_speed,
    entropy_ceiling, progression_every) + the chronicle representation
    (llm/corrected/raw -- which of the three views the viewer shows by default).
    The token is deliberately NOT written -- in-memory only. ``world_drive`` is
    no longer persisted (the drive modes are gone)."""
    p = _settings_path(srv)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        d = {"backend": backend, "story_model": story_model or ""}
        d["beat_author"] = beat_author if beat_author is not None else getattr(srv, "beat_author", "llm")
        d["story_mode"] = story_mode if story_mode is not None else getattr(srv, "story_mode", "story")
        d["feed_rate"] = float(feed_rate if feed_rate is not None else getattr(srv, "feed_rate", 1.0))
        d["cpu_target"] = float(cpu_target if cpu_target is not None else getattr(srv, "cpu_target", 0.2))
        d["cycle_speed"] = float(cycle_speed if cycle_speed is not None else getattr(srv, "cycle_speed", 1.0))
        d["entropy_ceiling"] = float(entropy_ceiling if entropy_ceiling is not None
                                     else getattr(srv, "entropy_ceiling", 0.05))
        d["representation"] = representation if representation is not None else getattr(srv, "representation", "llm")
        d["progression_every"] = int(progression_every if progression_every is not None
                                     else getattr(srv, "progression_every", 0))
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError as e:
        print(f"[settings] could not persist settings.json ({e})", file=sys.stderr)


def _sync_ollama_clients(srv) -> None:
    """Point the embedder + Ollama responder at the live srv.backend + token.
    Updates attrs IN PLACE so every holder of the shared instances (Cast.load,
    cells, the live chat path) sees the new backend/token without a restart.
    The Anthropic responder is left alone (its key is per-request passthrough)."""
    mem = getattr(srv, "membrane", None)
    if mem is None:
        return
    emb = getattr(mem, "embedder", None)
    if emb is not None:
        emb.base_url = srv.backend.rstrip("/")
        emb.token = getattr(srv, "ollama_token", None)
    resp = getattr(mem, "responder", None)
    if isinstance(resp, OllamaChatResponder):
        resp.backend = srv.backend.rstrip("/")
        resp.token = getattr(srv, "ollama_token", None)


def _ndjson_chunks(model: str, content: str):
    """Yield Ollama-shaped /api/chat stream chunks for a pre-known answer (the absorb).

    The recalled action is split into ~256-char pieces so it reads like a stream to
    a client that renders progressively; the client concatenates ``message.content``
    across chunks, so the join is exact (whitespace/code indentation preserved).
    Ends with a ``done:true`` chunk carrying empty content + zeroed eval stats.
    """
    pieces = [content[i:i + 256] for i in range(0, len(content), 256)] if content else []
    for p in pieces:
        yield {"model": model, "created_at": _now_iso(),
               "message": {"role": "assistant", "content": p}, "done": False}
    yield {"model": model, "created_at": _now_iso(),
           "message": {"role": "assistant", "content": ""},
           "done": True, "total_duration": 0, "load_duration": 0,
           "prompt_eval_count": 0, "prompt_eval_duration": 0,
           "eval_count": 0, "eval_duration": 0}


def _anthropic_sse_events(model: str, content: str):
    """Yield Anthropic ``/v1/messages`` SSE events for a pre-known answer (a
    replay under ``--absorb``). Mirrors ``_ndjson_chunks`` for the Ollama path:
    the recalled action is delivered as ``text_delta`` events so a streaming
    client renders it progressively. Each yield is one ``event:``/``data:`` pair
    plus the blank-line separator, as bytes ready to write to the wire."""
    def frame(etype: str, obj: dict) -> bytes:
        return f"event: {etype}\ndata: {json.dumps(obj)}\n\n".encode()
    msg = {"id": "msg_membrane_replay", "type": "message", "role": "assistant",
           "content": [], "model": model, "stop_reason": None,
           "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    yield frame("message_start", {"type": "message_start", "message": msg})
    yield frame("content_block_start",
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text", "text": ""}})
    pieces = [content[i:i + 256] for i in range(0, len(content), 256)] if content else []
    for p in pieces:
        yield frame("content_block_delta",
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": p}})
    yield frame("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield frame("message_delta",
                {"type": "message_delta",
                 "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                 "usage": {"output_tokens": 0}})
    yield frame("message_stop", {"type": "message_stop"})


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- story-feeding (the consolidator + /world/step) --------------------------
# The cells' "feed me" wired to the LLM: instead of random-vector ballast, the
# LLM tells a short story of their world, which is embedded and absorbed as a
# REAL exemplar (runtime.Membrane.feed). The never-cross ceiling caps the
# world's OWN variety (cells_rank), held by massaging each story toward the
# field centroid. These helpers are the server-side LLM call -- they mirror
# warmup.generate_palette (urllib POST to /api/chat, stream=False).

_STORY_SYSTEM = (
    "You are the voice of this world. Given the world premise and a small seed "
    "(a place, an actor, a mood, an object), narrate ONE brief vivid scene -- "
    "2 to 4 sentences -- that happens there. Write only the scene prose, no "
    "headings, no labels, no commentary. Ground it in the premise's tone.")


def _story_seed(palette: dict, rng: random.Random) -> str:
    """Pick a small on-world seed (place/actor/mood/object) from the cached
    palette so successive stories vary. Returns a single ``; ``-joined line."""
    def pick(key):
        v = palette.get(key)
        return rng.choice(v) if v else ""
    parts = [p for p in (pick("locations"), pick("actors"),
                         pick("moods"), pick("objects")) if p]
    return "; ".join(parts) if parts else ""


_STORY_SYSTEM_STORY = (
    "You are the voice of this world, telling ONE ongoing story set in it. Each "
    "reply is the NEXT scene in that same story -- 2 to 4 sentences -- continuing "
    "from exactly where the previous scene left off: the same characters, places, "
    "and thread, advancing the narrative. Keep it grounded in the premise's tone. "
    "Write only the scene prose, no headings, no labels, no commentary, no recaps.")


# ---- the genesis cache: the LLM writes the world's history in big chapters -----
# To save model calls, the LLM is asked (rarely) to narrate the chronicle of this
# world "from the big bang" as a batch of substantial scenes separated by a line of
# dashes. The scenes are cached locally on srv.story_cache; each FEED pops one
# cached scene and embeds+absorbs it (a cheap /api/embed, NOT a /api/chat call). So
# one LLM call feeds the world ~_CHAPTER times, and the cohesive chronicle is told
# in bulk. The cache refills (one call) when it runs below _LOW_WATER.
_GENESIS_SYSTEM = (
    "You are the chronicler of this world. Narrate its history as a sequence of "
    "distinct, substantial SCENES, each 2 to 3 sentences, advancing "
    "the chronicle forward through time: the cosmology and the first land, then the "
    "life, then the people and the world they make. Each scene must stand on its own "
    "as a coherent piece of the one ongoing story. Separate every scene with a line "
    "that contains only ---. Write ONLY the scenes separated by ---, no headings, no "
    "labels, no commentary, no recaps.")
# The SEED system prompt (first call only): also name the cast before the scenes.
# A short, bounded ensemble (the [[cast]] ledger) is seeded once from this block;
# thereafter the cast grows only by manual admission, so it stays short.
_GENESIS_SYSTEM_SEED = (
    "You are the chronicler of this world. First, in a CAST block, name the "
    "characters you are about to introduce -- one line per character, in the form "
    "'Name — a one-line role — a one-line voice' (3 to 6 characters, the ensemble "
    "this world is about; the voice is HOW they speak or think, in a phrase). Mark "
    "exactly ONE character as the protagonist -- the character the reader IS -- by "
    "prefixing their name with a star (★). Begin the block with a line that is only "
    "'cast:'. Then narrate the history as a sequence of distinct, substantial SCENES, "
    "each 2 to 3 sentences, advancing the chronicle forward through "
    "time: the cosmology and the first land, then the life, then the people and the "
    "world they make. Separate every scene with a line that contains only ---. Write "
    "ONLY the cast block then the scenes, no headings, no commentary, no recaps.")
# Character-driven chronicle (used when the cast is ALREADY seeded at boot via
# --protagonist/--character). A world that begins with named people should be
# narrated about THOSE people -- their schemes, dialogue, and what goes sideways --
# not a cosmogenesis arc. Left to "narrate from the big bang", a small model
# (gemma3:12b) ignores the premise and writes a generic planet-evolution story
# (primordial soup -> a world -> its people); leading with the cast keeps the
# chronicle about the characters the reader is here for.
_GENESIS_SYSTEM_CAST = (
    "You are the chronicler of this world. Narrate it as a sequence of distinct, "
    "substantial SCENES, each 2 to 3 sentences, each scene "
    "FEATURING the named characters -- their schemes, dialogue, conflicts, and what "
    "goes sideways for them. This world begins with its people: do NOT narrate "
    "cosmology, the formation of stars or planets, the origin of life, or any "
    "prehistory before the cast appears. Start in the middle of their lives and move "
    "forward through what they DO and what happens to them. Keep the same characters "
    "on stage across scenes; name them, let them talk. Each scene must stand on its "
    "own as a coherent piece of the one ongoing story. Separate every scene with a "
    "line that contains only ---. Write ONLY the scenes separated by ---, no "
    "headings, no labels, no commentary, no recaps.")
_CHAPTER = 8        # scenes per genesis call (one model call -> ~8 cached feeds)
_LOW_WATER = 3      # refill the cache (one model call) when fewer scenes remain
# The PROGRESSION drip: a periodic LLM-authored ADVANCING beat fed as TRUSTED
# (self_authored=False) so it trains the on-stage character's n-gram and the
# cells-authored story EVOLVES instead of stammering on the frozen genesis
# corpus. Spiral-safe -- the LLM authors it (not the n-gram's own output), so
# training on it is the same as genesis. One scene that moves the story forward.
_PROGRESSION_SYSTEM = (
    "You are the chronicler advancing this world. Write ONE new SCENE (2 to 3 "
    "sentences) that MOVES THE STORY FORWARD: resolve the current tension, pay "
    "off a setup, or introduce a real turn the characters must now deal with -- "
    "do NOT merely restate or continue the same beat. Keep the named characters "
    "on stage, in their own voices; let them act and speak. Stay inside this "
    "world's premise and tone; do not invent a new setting out of nowhere. "
    "Write ONLY the single scene, no headings, no labels, no --- separators, "
    "no recap, no commentary.")
# The render prompt: turn one raw n-gram beat into readable in-voice prose for
# DISPLAY. This is a renderer, not an author -- the raw beat is already the
# canon (absorbed, self_authored, spiral-proof); this gloss is never stored. It
# must stay faithful: no new plot, no new characters, just make the fragment
# coherent and finish a sentence cut off mid-clause. The LLM'd view of a beat.
_RENDER_SYSTEM = (
    "You render prose. The user gives you a raw fragment a character just said "
    "-- it may be lowercase, open mid-thought, or cut off mid-sentence. Turn it "
    "into one or two coherent, readable sentences in that character's voice. "
    "Stay strictly faithful to what the fragment actually says: do NOT invent "
    "new events, characters, places, or facts, and do NOT contradict the "
    "chronicle context. If it is cut off, complete the sentence naturally and "
    "stop. Do not add commentary, labels, or quotation marks around the whole "
    "thing. Output only the rendered prose.")
# The situation prompt: turn a POV's recent LIVED scenes (+ optional new
# development) into a readable in-voice SITUATION REPORT for the chat -- a
# renderer, never the canon. It reads the field's trusted exemplars (stateless
# RAG, no LLM history -- the LLM never sees its own prior renders, so it can't
# spiral); it never _learn/feed/append. Faithful to the scenes: no invented
# events/characters/facts. The situation channel's one call per render.
_SITUATION_SYSTEM = (
    "You write situation reports. The user gives you a character's recent lived "
    "scenes (oldest first, newest last) and optionally a new development. Write "
    "2-4 sentences of in-voice present-tense prose describing where the character "
    "is and what has been happening to them. Stay faithful to the scenes: do NOT "
    "invent events, characters, places, or facts beyond them -- the setting and "
    "situation come FROM the scenes, not your own stock backdrops. Render ONLY "
    "what the scenes actually state; do not add props, rooms, or setting details "
    "that no scene mentions (no invented workbench, bar, ship, etc.). The scenes "
    "may be fragmentary and cut off mid-sentence -- that is expected; convey what "
    "is there without inventing context to bridge the gaps. If a new development "
    "is given, fold it in as the latest beat. Vary the opening sentence between "
    "reports -- do not begin every report the same way (for example avoid a "
    "repeated \"I'm stuck...\" opener); lead with the specific action or detail "
    "that distinguishes this moment. Do not add labels, commentary, or quotation "
    "marks around the whole thing. Output only the report.")
_CHRONICLE_SYSTEM = (
    "You compress a world's recent events into flowing narration. The user gives "
    "you a character's recent lived scenes (oldest first, newest last) -- OR, when "
    "no viewpoint character is set, the whole world's recent beats. Write 1-2 "
    "tight paragraphs of present-tense prose narrating what has been happening: "
    "the arc of events, who did what, where it's heading. Compress repetition -- "
    "if beats stammer the same action, fold them into one beat; do not list every "
    "fragment. Stay faithful to the scenes: do NOT invent events, characters, "
    "places, or props no scene mentions (no invented workbench/bar/ship); the "
    "setting comes FROM the scenes. The scenes may be fragmentary and cut off "
    "mid-sentence -- that is expected; convey the gist without inventing context "
    "to bridge gaps. When a viewpoint character is set, narrate in their voice "
    "(first person, their temperament); otherwise narrate in a close third-person "
    "the world's voice. Do not add labels, commentary, or quotation marks around "
    "the whole thing. Output only the narration.")
_EVENTS_CAP = 2000  # /events ring buffer cap (opt-in client action recorder)


def _parse_seed(txt: str):
    """Split a SEED genesis response into (cast_lines, scenes). The cast block is
    the chunk before the first ``---`` (introduced by a ``cast:`` line); the scenes
    are the remaining ``---``-separated chunks. Returns ([], scenes) when the LLM
    omitted the cast block (a graceful fallback -- seeding just does nothing)."""
    parts = re.split(r"(?m)^\s*-{2,}\s*$", txt)
    first = parts[0] if parts else ""
    if not re.search(r"(?im)^\s*cast\s*:", first):
        return [], [s.strip() for s in parts if s.strip()]
    seen_marker = False
    cast_lines: list = []
    for line in first.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"(?im)^\s*cast\s*:\s*$", s):
            seen_marker = True
            continue
        if seen_marker:
            cast_lines.append(s)
    scenes = [s.strip() for s in parts[1:] if s.strip()]
    return cast_lines, scenes


def _seed_cast(srv, cast_lines: list) -> None:
    """Seed the [[cast]] ledger from a genesis cast block: one batched embed of the
    'Name — role — voice' lines -> admit each as a character, then retro-tag the
    whole existing field so the cosmos colors by character immediately. A line
    whose name begins with a star (★) marks the PROTAGONIST (the character the
    reader IS); that character becomes the POV (the narrator speaks from their
    region, in their voice). Saves the cast and the field. No-op when the block
    is empty or the cast is already seeded."""
    import numpy as np
    mem = srv.membrane
    cast = mem.cast
    if cast.chars:                       # already seeded -- never re-seed (keep cast short)
        return
    entries: list = []          # (name, role, voice, is_protagonist)
    for line in cast_lines:
        is_pov = line.lstrip().startswith("★")
        body = line.lstrip().lstrip("★").strip()
        # split into Name — role — voice (up to three em-dash parts)
        m = re.split(r"\s*[—–]\s*", body, maxsplit=2)
        name = m[0].strip().strip("\"'.,;:")
        role = m[1].strip() if len(m) > 1 else ""
        voice = m[2].strip() if len(m) > 2 else ""
        if name:
            entries.append((name, role, voice, is_pov))
    if not entries:
        return
    descs = [f"{n} — {r} — {v}" if (r or v) else n for n, r, v, _ in entries]
    try:
        vecs = mem.embedder.embed(descs)
    except Exception as e:
        print(f"[genesis] cast embed failed ({e}); cast unseeded", file=sys.stderr)
        return
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    pov_char = None
    for (name, role, voice, is_pov), v in zip(entries, vecs):
        c = cast.admit(name, np.asarray(v, dtype=float), anchor_idx=None,
                       cycle=cycle, role=role, voice=voice)
        if is_pov and c is not None:
            pov_char = c
    if pov_char is not None:
        cast.set_pov(pov_char.id)
    try:
        tagged = cast.retro_tag(mem.store)       # color the existing field at once
        mem.store.save(mem.field_path)
        cast.save(mem.cast_path)
        pov_name = f" (POV: {pov_char.name})" if pov_char else ""
        print(f"[genesis] cast seeded: {len(cast.chars)} characters, "
              f"{tagged} scenes tagged{pov_name}", file=sys.stderr)
    except Exception as e:
        print(f"[genesis] retro-tag failed ({e})", file=sys.stderr)


def _admit_character(mem, spec: str, tag: str = "character"):
    """Admit one ``"Name — role — voice"`` character to the cast at boot (if absent),
    retro-tag the existing field so it colors in at once, and save. Returns the
    Character (existing or newly admitted) or None on failure. Does NOT set POV --
    that's ``_apply_protagonist``'s job. Shared by --protagonist and --character."""
    import numpy as np
    parts = re.split(r"\s*[—–]\s*", spec.strip(), maxsplit=2)
    name = parts[0].strip().strip("\"'.,;:")
    role = parts[1].strip() if len(parts) > 1 else ""
    voice = parts[2].strip() if len(parts) > 2 else ""
    if not name:
        return None
    existing = mem.cast.by_name(name)
    if existing is not None:
        return existing
    seed_text = f"{name} — {role} — {voice}" if (role or voice) else name
    try:
        v = mem.embedder.embed([seed_text])[0]
    except Exception as e:
        print(f"[{tag}] embed failed ({e}); {name} not admitted", file=sys.stderr)
        return None
    c = mem.cast.admit(name, np.asarray(v, dtype=float), anchor_idx=None,
                       cycle=0, role=role, voice=voice)
    if c is None:
        print(f"[{tag}] cast is full (cap {mem.cast.cap}); {name} not admitted",
              file=sys.stderr)
        return None
    try:
        mem.cast.retro_tag(mem.store)        # color the existing field at once
        mem.store.save(mem.field_path)
    except Exception as e:
        print(f"[{tag}] retro-tag failed ({e})", file=sys.stderr)
    mem.cast.save(mem.cast_path)
    print(f"[{tag}] {c.name}"
          + (f" — {c.role}" if c.role else "")
          + (f" — {c.voice}" if c.voice else ""), file=sys.stderr)
    return c


def _apply_protagonist(mem, spec: str) -> None:
    """Apply ``--protagonist "Name — role — voice"``: admit the character to the
    cast if absent (seeded from the embedded 'Name — role — voice' string), set
    them POV, save. If a character of that name already exists, just (re)set them
    POV. Retro-tags the existing field so the protagonist colors it immediately."""
    c = _admit_character(mem, spec, tag="protagonist")
    if c is None:
        return
    mem.cast.set_pov(c.id)
    mem.cast.save(mem.cast_path)
    print(f"[protagonist] POV = {c.name}"
          + (f" — {c.role}" if c.role else "")
          + (f" — {c.voice}" if c.voice else ""), file=sys.stderr)


def _genesis(srv) -> list:
    """ONE model call: narrate the next chapter of the world's chronicle (several
    substantial scenes separated by ``---``) and return them as a list. Continues
    the cohesive story from the last scenes absorbed (``srv.story_history``); the
    first call begins "from the big bang" and ALSO seeds the [[cast]] ledger (the
    SEED prompt asks for a cast block). Returns ``[]`` on any failure (the
    caller skips that feed). Temperature is the fixed cohesive value on ``srv``."""
    backend = srv.backend
    model = getattr(srv, "warmup_model", None)
    premise = getattr(srv, "system_prompt", "")
    if not model or not backend or not premise:
        return []
    history = list(getattr(srv, "story_history", []))[-6:]
    cast = srv.membrane.cast
    # the cast block the model writes FROM (only when the cast is already seeded;
    # an empty cast uses the SEED path below to NAME the ensemble itself).
    cast_block = ""
    if cast.chars:
        cast_block = ("The cast (write about THESE people):\n" +
                      "\n".join(f"- {c.name} — {c.role} — {c.voice}"
                                for c in cast.chars if c.name) + "\n\n")
    # the autonomous chronicle follows the ON-STAGE character (the freshest in
    # the narrative, ``cast.on_stage``), not always the POV -- so when the POV
    # fell out of the narrative (faded) the chronicle follows whoever is in the
    # scene ("only follow their POV when they are in the scene"). on_stage
    # returns the POV when it is live (recently tagged), so the usual case is
    # unchanged; the fade is just "stop POVing a stale POV." Seed the chapter with
    # that character's current situation so the LLM continues from where the
    # narrative IS, not where its own thread drifted. (The world-drive gate is
    # gone -- the chronicle always continues from the on-stage character when one
    # exists; an empty cast uses the SEED path to NAME the ensemble.)
    pov_block = ""
    onstage = cast.on_stage() or (cast.by_id(getattr(cast, "pov_id", None))
                                  if cast.pov_id is not None else None)
    if onstage is not None and getattr(onstage, "last_scene", ""):
        pov_block = (f"{onstage.name} is currently here:\n"
                     + onstage.last_scene + "\n\nContinue from this point.\n\n")
    elif onstage is not None:
        pov_block = f"{onstage.name} is the protagonist.\n\n"
    # the SEED call: first chapter + an empty cast -> name the ensemble once
    seed = (not history) and not cast.chars
    if seed:
        sys_prompt = _GENESIS_SYSTEM_SEED
        user = (premise + "\n\nBegin the chronicle from the big bang: first give the "
                "cast block (cast: then one 'Name — role — voice' line per character, "
                "marking one protagonist with a leading ★), then narrate the FIRST "
                + str(_CHAPTER) + " scenes in order, each 2 to 3 sentences, each separated "
                "by a line with only ---.")
    elif history:
        # continuing an existing chronicle: stay character-driven when the cast
        # is seeded (don't drift back into cosmology), else the world-arc voice.
        sys_prompt = _GENESIS_SYSTEM_CAST if cast.chars else _GENESIS_SYSTEM
        user = (premise + "\n\n" + cast_block + pov_block +
                "The chronicle so far:\n\n" +
                "\n\n".join(history) + "\n\nContinue the chronicle: narrate the NEXT "
                + str(_CHAPTER) + " scenes featuring the cast, each 2 to 3 sentences, "
                "each separated by a line with only ---.")
    else:
        # no history yet + a pre-seeded cast -> character-driven FIRST chapter
        # (NOT "from the big bang", which makes small models ignore the cast and
        # write a generic cosmogenesis). Empty cast can't happen here (seed=True).
        sys_prompt = _GENESIS_SYSTEM_CAST
        user = (premise + "\n\n" + cast_block +
                "Begin the chronicle with these characters in the middle of their "
                "lives: narrate the FIRST " + str(_CHAPTER) + " scenes featuring "
                "them, each 2 to 3 sentences, each separated by a line with only ---.")
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user}]
    temperature = float(getattr(srv, "story_temperature", 0.7))
    body = {"model": model, "stream": False, "temperature": temperature,
            "messages": messages}
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(backend + "/api/chat", data=data,
                                     headers=_ollama_headers(srv))
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"[genesis] chronicle call failed ({e}); cache stays as-is",
              file=sys.stderr)
        return []
    txt = ""
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                txt = c
    if seed:
        cast_lines, scenes = _parse_seed(txt)
        if cast_lines:
            _seed_cast(srv, cast_lines)
    else:
        scenes = [s.strip() for s in re.split(r"(?m)^\s*-{2,}\s*$", txt) if s.strip()]
    return scenes


def _progression_beat(srv) -> str:
    """The periodic DRIP: one LLM-authored scene that ADVANCES the chronicle
    (resolves the current tension or introduces a real turn), fed as a TRUSTED
    beat (``self_authored=False``) so it trains the on-stage character's n-gram
    and the autonomous cells-authored story EVOLVES instead of stammering on the
    frozen genesis corpus. Spiral-safe: the LLM authors it (NOT the n-gram's own
    output), so training the n-gram on it is the same as genesis. Grounded in
    the premise + cast voices + the on-stage character's current situation +
    the recent chronicle. Returns the scene text, or ``""`` on any failure (the
    caller falls back to a cells n-gram beat)."""
    backend = srv.backend
    model = getattr(srv, "warmup_model", None)
    premise = getattr(srv, "system_prompt", "")
    if not model or not backend or not premise:
        return ""
    cast = srv.membrane.cast
    history = list(getattr(srv, "story_history", []))[-6:]
    cast_block = ""
    if cast.chars:
        cast_block = ("The cast (write about THESE people):\n" +
                      "\n".join(f"- {c.name} — {c.role} — {c.voice}"
                                for c in cast.chars if c.name) + "\n\n")
    onstage = cast.on_stage() or (cast.by_id(getattr(cast, "pov_id", None))
                                  if cast.pov_id is not None else None)
    pov_block = ""
    if onstage is not None and getattr(onstage, "last_scene", ""):
        pov_block = (f"{onstage.name} is currently here:\n"
                     + onstage.last_scene + "\n\nAdvance from this point.\n\n")
    elif onstage is not None:
        pov_block = f"{onstage.name} is the protagonist.\n\n"
    user = (premise + "\n\n" + cast_block + pov_block +
            "The chronicle so far:\n\n" + "\n\n".join(history) +
            "\n\nWrite the NEXT single scene that advances the story.")
    messages = [{"role": "system", "content": _PROGRESSION_SYSTEM},
                {"role": "user", "content": user}]
    temperature = float(getattr(srv, "story_temperature", 0.7))
    txt = _ollama_chat(backend, model, messages, temperature=temperature,
                       token=getattr(srv, "ollama_token", None), timeout=180)
    if not txt:
        return ""
    # defensively take the first scene if the model emitted --- separators
    parts = [s.strip() for s in re.split(r"(?m)^\s*-{2,}\s*$", txt) if s.strip()]
    return parts[0] if parts else ""


def _feed_count(rate: float) -> int:
    """A continuous feed rate (feeds per consolidation cycle) -> the integer number
    of feeds to run THIS cycle, with a probabilistic fractional tail: 2.5 feeds/cycle
    means 2 every cycle plus a third on ~half of them (expected 2.5). 0 -> never feed."""
    rate = max(0.0, float(rate))
    base = int(rate)
    frac = rate - base
    return base + (1 if (frac > 0.0 and random.random() < frac) else 0)


def _tell_story(backend: str, model: str | None, premise: str, seed: str,
                timeout: int = 120, temperature: float = 0.9,
                mode: str = "story", history: list | None = None,
                token: str | None = None) -> str:
    """ONE model call for a world scene. Mirrors ``warmup.generate_palette``
    (urllib POST to /api/chat, stream=False).

    ``temperature`` is the entropy dial -- the information quality of each story
    (higher = more novel/surprising, lower = more on-template).

    ``mode``:
      * ``"story"`` (default): a CONTINUING story. ``history`` is the running list
        of recent scenes; it is embedded so the model continues the same thread
        (coherent narrative across ticks). The random ``seed`` is ignored.
      * ``"variety"``: the original mode -- a fresh standalone vignette seeded by
        ``seed`` (a random palette draw), no history. Grows field variety fastest.
    Returns the scene text, or ``""`` on any failure (the caller skips)."""
    if not model or not backend or not premise:
        return ""
    if mode == "story" and history is not None:
        if not history:
            user = premise + "\n\nBegin the story: narrate the opening scene (2 to 4 sentences)."
        else:
            user = (premise + "\n\nThe story so far:\n\n" + "\n\n".join(history) +
                    "\n\nContinue the story: narrate the NEXT scene (2 to 4 sentences) "
                    "that follows directly from the above, advancing the same thread.")
        messages = [{"role": "system", "content": _STORY_SYSTEM_STORY},
                    {"role": "user", "content": user}]
    else:
        user = premise if not seed else f"{premise}\n\nSeed for this scene: {seed}"
        messages = [{"role": "system", "content": _STORY_SYSTEM},
                    {"role": "user", "content": user}]
    body = {"model": model, "stream": False, "temperature": float(temperature),
            "messages": messages}
    try:
        data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(backend + "/api/chat", data=data,
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"[feed] story call failed ({e}); skipping", file=sys.stderr)
        return ""
    txt = ""
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                txt = c
    return txt.strip()


def _render_passage(srv, raw: str, pov_id: int | None = None) -> str:
    """Render one raw n-gram beat into readable in-voice prose -- a DISPLAY
    gloss, never the canon. The raw text is what the field absorbs
    (``self_authored``, excluded from the n-gram corpus, spiral-proof); this
    LLM'd version is shown to the reader and then thrown away. Grounded in the
    world premise + the speaking character's voice + the recent chronicle, so
    the polish is faithful rather than free-floating.

    Degrades to ``raw`` unchanged on ANY failure (no premise, empty raw, no
    model, backend down, parse error): the viewer always has raw + corrected,
    so the chronicle never blanks and the world clock is never blocked."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    mem = srv.membrane
    premise = getattr(srv, "system_prompt", "") or ""
    cast = getattr(mem, "cast", None)
    # the speaking character: the POV when one is given, else whoever is on stage
    ch = None
    if cast is not None:
        if pov_id is not None:
            try:
                ch = cast.by_id(int(pov_id))
            except (TypeError, ValueError):
                ch = None
        if ch is None:
            ch = cast.on_stage()
    voice = ""
    if ch is not None:
        bits = [f"the viewpoint character is {ch.name}"]
        if getattr(ch, "role", ""):
            bits.append(f"role: {ch.role}")
        if getattr(ch, "voice", ""):
            bits.append(f"voice: {ch.voice}")
        voice = "; ".join(bits)
    # the running chronicle (last scenes fed) -- continuity only, not a prompt
    # to continue; the renderer must not add new plot beyond the fragment.
    hist = list(getattr(srv, "story_history", []) or [])[-5:]
    user = premise
    if voice:
        user += "\n\n" + voice
    if hist:
        user += "\n\nThe chronicle so far (for continuity only):\n\n" + \
               "\n\n".join(hist)
    user += ("\n\nRaw fragment to render (a character's own speech, possibly "
             "lowercase and cut off mid-sentence):\n" + raw +
             "\n\nRendered prose:")
    messages = [{"role": "system", "content": _RENDER_SYSTEM},
                {"role": "user", "content": user}]
    model = (getattr(srv, "warmup_model", None)
             or getattr(srv, "story_model", None))
    out = _ollama_chat(getattr(srv, "backend", ""), model, messages,
                       temperature=0.45,
                       token=getattr(srv, "ollama_token", None), timeout=120)
    return out if out else raw


def _render_situation(srv, pov_id: int | None = None,
                      text: str | None = None) -> dict:
    """Render a SITUATION REPORT for the POV (or whoever is on stage) -- a
    readable in-voice summary of where they are and what has been happening,
    grounded in their recent LIVED scenes. A DISPLAY renderer, never the canon:
    it reads ``mem.store.exemplars`` + calls ``_ollama_chat`` and nothing else --
    no ``_learn``, ``feed``, ``store.append``, cast mutation, or save. Spiral-proof
    (the LLM never sees its own prior renders; the field is the only memory).

    Stateless RAG, no LLM history: one call. Retrieves the POV's last ~6 LIVED
    tagged scenes (including cells-authored ``self_authored`` beats -- safe here
    because this path never trains the n-gram; the exclusion is corpus-only);
    ``text`` (the world's response beat on a send, or the preview text) is folded
    in as a "new development". Degrades gracefully on any failure (no char, no
    scenes, backend down): the chat never blanks.

    Returns ``{"situation","raw_scenes","pov_id","scene_count"}`` -- ``raw_scenes``
    is the list of retrieved scene actions (the Raw view); ``pov_id``/``scene_count``
    are the watermark the viewer uses to detect the next POV touch."""
    mem = srv.membrane
    cast = getattr(mem, "cast", None)
    ch = None
    if cast is not None:
        if pov_id is not None:
            try:
                ch = cast.by_id(int(pov_id))
            except (TypeError, ValueError):
                ch = None
        if ch is None:
            ch = cast.on_stage()
    cid = getattr(ch, "id", None)
    premise = getattr(srv, "system_prompt", "") or ""
    # the POV's last ~6 LIVED scenes (their recent history), oldest->newest.
    # This is ALL tagged scenes -- INCLUDING self_authored (cells-authored) beats.
    # The self_authored exclusion exists for the N-GRAM corpus (so it can't train on
    # its own stammer -> spiral); but this is a DISPLAY render that never trains
    # anything, so reading the world's own beats is safe. Grounding on the actual
    # recent lived scenes (not just the static LLM genesis) is what makes the
    # situation EVOLVE with the world -- otherwise every render is frozen on the
    # same genesis chapter and the LLM opens every report the same way ("I'm stuck
    # in this tunnel…"). The LLM renders the raw n-gram beats into readable prose,
    # same as /render does for any fragment.
    scenes: list = []
    exs = list(getattr(mem.store, "exemplars", []) or [])
    if cid is not None:
        scenes = [e for e in exs if cid in getattr(e, "tags", [])]
    scenes = scenes[-6:]
    actions = [getattr(e, "action", "") for e in scenes]
    # the speaking character's voice block (same shape as _render_passage)
    voice = ""
    if ch is not None:
        bits = [f"the viewpoint character is {ch.name}"]
        if getattr(ch, "role", ""):
            bits.append(f"role: {ch.role}")
        if getattr(ch, "voice", ""):
            bits.append(f"voice: {ch.voice}")
        voice = "; ".join(bits)
    user = premise
    if voice:
        user += "\n\n" + voice
    if actions:
        user += "\n\nRecent scenes (oldest first, newest last):\n\n" + \
               "\n\n".join(actions)
    # fold in `text` as a new development UNLESS it is already the last scene -- the
    # send-turn path passes the just-authored beat as `text`, and that beat is now
    # also the POV's last tagged scene (above), so stating it twice would make the
    # LLM repeat itself. The preview path passes the player's typed text, which is
    # NOT in the field, so it still folds in (the prospective "what if").
    _dev = (text and text.strip()) or ""
    if _dev and actions and _dev.strip() == (actions[-1] or "").strip():
        _dev = ""
    if _dev:
        user += "\n\nNew development (fold in as the latest beat):\n" + _dev
    out = ""
    if actions or _dev:
        messages = [{"role": "system", "content": _SITUATION_SYSTEM},
                    {"role": "user", "content": user}]
        model = (getattr(srv, "warmup_model", None)
                 or getattr(srv, "story_model", None))
        out = _ollama_chat(getattr(srv, "backend", ""), model, messages,
                           temperature=0.35,
                           token=getattr(srv, "ollama_token", None), timeout=120)
    # degrade: empty render -> raw scenes joined; no scenes -> last_scene; quiet
    if not out:
        out = "  ·  ".join(a for a in actions if a)
    if not out and ch is not None:
        out = (getattr(ch, "last_scene", "") or "").strip()
    if not out:
        out = "the world is quiet…"
    return {"situation": out,
            "raw_scenes": [a for a in actions if a],
            "pov_id": cid,
            "scene_count": int(getattr(ch, "scene_count", 0))}


def _render_chronicle(srv, pov_id: int | None = None) -> dict:
    """Compress the world's recent autonomous beats into a flowing multi-paragraph
    narration -- the rolling ``what's happening`` view for when the world runs on
    its own clock. A DISPLAY renderer, never the canon: it reads
    ``mem.store.exemplars`` + calls ``_ollama_chat`` and nothing else (no
    ``_learn``/``feed``/``append``/save -> spiral-proof; the ``self_authored``
    exclusion is corpus-only and this path reads those beats safely).

    Scope (matches the viewer's POV chooser): a POV is set -> that character's
    last ~8 LIVED tagged scenes (their thread); no POV -> the whole world's last
    ~16 beats (any tags, any authoring). Stateless RAG, one call. Degrades
    gracefully (no char/scenes, backend down): never blanks. Returns
    ``{chronicle, raw_beats, pov_id, scene_count, cycle}``."""
    mem = srv.membrane
    cast = getattr(mem, "cast", None)
    ch = None
    if cast is not None and pov_id is not None:
        try:
            ch = cast.by_id(int(pov_id))
        except (TypeError, ValueError):
            ch = None
    cid = getattr(ch, "id", None)
    premise = getattr(srv, "system_prompt", "") or ""
    exs = list(getattr(mem.store, "exemplars", []) or [])
    # POV set -> that character's recent lived thread; else the whole world's
    # recent beats (any tags, incl self_authored -- the world's own chronicle).
    # Capped at ~8 so the render stays fast (a 16-beat compression was ~31s on
    # llama3.1:8b -> the live node never updated; 8 beats + 1-2 paragraphs lands
    # in well under half that).
    if cid is not None:
        scenes = [e for e in exs if cid in getattr(e, "tags", [])]
    else:
        scenes = list(exs)
    scenes = scenes[-8:]
    beats = [getattr(e, "action", "") for e in scenes]
    beats = [b for b in beats if b]
    voice = ""
    if ch is not None:
        bits = [f"the viewpoint character is {ch.name}"]
        if getattr(ch, "role", ""):
            bits.append(f"role: {ch.role}")
        if getattr(ch, "voice", ""):
            bits.append(f"voice: {ch.voice}")
        voice = "; ".join(bits)
    user = premise
    if voice:
        user += "\n\n" + voice
    if beats:
        user += ("\n\nRecent events (oldest first, newest last):\n\n"
                 + "\n\n".join(beats))
    out = ""
    if beats:
        messages = [{"role": "system", "content": _CHRONICLE_SYSTEM},
                    {"role": "user", "content": user}]
        model = (getattr(srv, "warmup_model", None)
                 or getattr(srv, "story_model", None))
        out = _ollama_chat(getattr(srv, "backend", ""), model, messages,
                          temperature=0.4,
                          token=getattr(srv, "ollama_token", None), timeout=120)
    if not out:
        out = "  ·  ".join(beats)
    if not out and ch is not None:
        out = (getattr(ch, "last_scene", "") or "").strip()
    if not out:
        out = "the world is quiet…"
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    return {"chronicle": out,
            "raw_beats": beats,
            "pov_id": cid,
            "scene_count": int(getattr(ch, "scene_count", 0)
                               if ch is not None else 0),
            "cycle": cycle}


# ---- shared feed core (used by the consolidator AND /world/step) ---------------
def _feed_snapshot(srv, mem) -> dict:
    """A read-only field snapshot in the feed-stats shape (no LLM, no mutation).
    The base for ``_run_feeds``'s rolling stats."""
    from .variety import cells_rank, free_energy
    exs = mem.store.exemplars
    theo = len(exs[0].vec) if exs else 1024
    ceiling = float(getattr(srv, "entropy_ceiling", 0.05))
    cap = max(0, int(round(ceiling * theo)))
    return {"fed": 0, "massaged": 0, "full": 0, "skipped": 0,
            "field_size": len(mem.store),
            "cells_level": round(float(cells_rank(mem.store)), 2),
            "entropy_ceiling": round(ceiling, 4), "entropy_cap": cap,
            "theoretical_max": theo,
            "temperature": round(float(getattr(srv, "story_temperature", 0.7)), 3),
            "story_mode": str(getattr(srv, "story_mode", "story")),
            "feed_rate": round(float(getattr(srv, "feed_rate", 1.0)), 3),
            "cpu_target": round(float(getattr(srv, "cpu_target", 0.2)), 3),
            "cache_depth": len(getattr(srv, "story_cache", [])),
            "free_energy": float(free_energy(mem.store)) if len(exs) >= 2 else 0.0,
            "entropy_count": 0, "stories": []}


def _fresh_consolidation_stats(srv) -> dict:
    """The full consolidation-stats shape -- boot, NEW WORLD, and LOAD all use
    this so /state's `consolidation` field is populated immediately (the old NEW
    /LOAD path wrote a thin {cycles,cpu_actual} dict that left the HUD blank
    until the next consolidator cycle)."""
    return {"cycles": 0, "cpu_actual": 0.0,
            "cpu_target": round(float(getattr(srv, "cpu_target", 0.0)), 3),
            "swept": 0, "budget": 0.0, "mean_novelty": 0.0,
            "settled": 0, "eff_rank": 0.0, "free_energy": 0.0,
            "fed": 0, "cache": 0, "stories": []}


def _set_world_running(srv, on: bool, user: bool = False) -> None:
    """Set the world-loop state. ``srv.world_running`` is the canonical flag the
    consolidator reads. When the user explicitly arms the world (``user=True``)
    we set ``_user_run`` so the post-training auto-pause is suppressed -- once
    the user has driven the world, it is theirs and training does not yank it
    back to stopped."""
    srv.world_running = bool(on)
    if user and on:
        srv._user_run = True


def _vlog(srv, msg: str) -> None:
    """Log one viewer-driven action to stderr (-> membrane-proxy.log) so the
    operator can peek at what the viewer is doing in real time (``tail -f``).
    Opt-OUT: on by default; disable with ``--no-viewer-log`` (sets
    ``srv.viewer_log = False``). One concise line per action. Complements the
    opt-in ``?rec=1`` raw-DOM-event recorder (which buffers DOM events in
    memory, read on request). The poll endpoints (/state, /neighbors, /events
    GET) are NOT logged -- they'd flood. ``flush=True`` because stderr is
    block-buffered when redirected to a file, so without it a live tail would
    lag."""
    if not getattr(srv, "viewer_log", True):
        return
    print(f"[viewer] {msg}", file=sys.stderr, flush=True)


def _clog(srv, msg: str) -> None:
    """Log one Cortex decision to stderr (-> membrane-proxy.log) so the operator can
    watch the variety of the agent's decisions live (``tail -f``). Opt-OUT: on by
    default; disable with ``--no-cortex-log`` (sets ``srv.cortex_log = False``). One
    concise line per agent tool call (the decision the field measures). Mirrors the
    ``_vlog`` discipline so the [cortex] log is the same opt-out, never auth-bearing
    (only the tool name + the field's sim/novelty/eff_rank)."""
    if not getattr(srv, "cortex_log", True):
        return
    print(f"[cortex] {msg}", file=sys.stderr, flush=True)


def _reset_world_runtime(srv, mem, rearm_burst: bool = True) -> None:
    """Clear the per-world runtime state that NEW / LOAD must reset but the old
    paths left stale: the PCA basis, the re-ask ring, the n-gram cache, the
    chronicle cache + cycle clock, and (optionally) re-arm the initial training
    burst. The field/cast/audit are replaced by the caller."""
    srv._v_basis = None                       # force /state to refit a fresh cloud
    srv.story_history = []
    srv.story_cache = []
    srv.cache_depth = 0
    srv.consolidation_stats = _fresh_consolidation_stats(srv)
    # restore the world clock from the (just-loaded / fresh) cast wrapper so a
    # loaded world keeps its in-world date instead of resetting to cycle 0 (a
    # NEW world's empty cast has cycles=0 -> starts at 0, correctly).
    srv.consolidation_stats["cycles"] = int(getattr(mem.cast, "cycles", 0))
    # a fresh/loaded world waits for PLAY; persisted dials (cycle_speed etc.)
    # are left as loaded -- only the run state resets, not the growth settings.
    _set_world_running(srv, False)
    srv._user_run = False                      # a new/loaded world is not user-driven
    if rearm_burst:
        srv.initial_burst = getattr(srv, "_initial_burst_arg", 0)
    rec = getattr(mem, "_recent", None)
    if rec is not None:
        try: rec.clear()
        except Exception: pass
    mem._gen_lm = None
    mem._gen_key = None


def _run_feeds(srv, mem, feeds: int, abort_if_disarmed: bool = False,
               progression: bool = False) -> dict:
    """Run ``feeds`` story-feeds from the GENESIS CACHE and return the stats dict
    (with ``stories`` for the console). Each feed pops one cached chronicle scene
    and embeds+absorbs it (a cheap ``/api/embed``, NOT a ``/api/chat`` call); the
    cache is refilled (one LLM chapter call -> ~``_CHAPTER`` scenes) when it runs
    below ``_LOW_WATER``. So the cohesive story is told in bulk and the world feeds
    at the rate the feed-rate dial sets, without a model call per feed.

    Serializes on ``srv.feed_lock`` so ``/world/step`` and the background
    consolidator never race the rolling narrative (``srv.story_history``) or the
    cache. Absorbs the RAW story embedding (``mem.feed`` no longer nudges).

    ``abort_if_disarmed`` (the consolidator sets it): bail before a new genesis
    call and between feeds when ``srv.world_running`` flips False, so a STOP
    takes effect within a feed or two instead of waiting on a whole chapter. The
    ``/world/step`` path leaves it False (a STEP feeds regardless of arm state).

    Each story is tagged ``pov`` when it tagged the POV character, so the viewer
    can narrate only the protagonist's thread (``storyPush`` POV filter).

    CELLS-ACT: when ``beat_author == "cells"`` and training is done
    (``initial_burst`` spent), each feed is authored INLINE by the on-stage
    character's own per-character n-gram voice (``_cells_beat``, no LLM) -- the
    closed loop (generate -> feed -> absorb -> re-tag -> next beat). When no
    character can speak yet (none has lived >= _VOICE_MIN) ``_cells_beat`` returns
    None and we fall back to the LLM genesis cache ("we just narrate it"). The
    autonomous chronicle is cells-authored when ``beat_author == "cells"``.

    PROGRESSION DRIP: when ``progression`` is True (the autonomous consolidator
    only -- NOT turn-based sends or ``/world/step``) and ``progression_every``
    is set, every ``progression_every`` cycles ONE feed is an LLM-authored
    ADVANCING beat (``_progression_beat``, trusted ``self_authored=False``) so
    the n-gram's trusted corpus grows and the story evolves instead of
    stammering on the frozen genesis material. Spiral-safe -- the LLM authors
    it, not the n-gram's own output (same as genesis). Fires only in cells-act
    autonomous mode; falls back to a cells beat if the LLM call fails."""
    stats = _feed_snapshot(srv, mem)
    premise = getattr(srv, "system_prompt", None)
    if not premise or feeds <= 0:
        return stats
    ceiling = float(getattr(srv, "entropy_ceiling", 0.05))   # inert for growth (kept)
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    pov_id = getattr(mem.cast, "pov_id", None)
    stories: list = []
    with srv.feed_lock:
        if abort_if_disarmed and not getattr(srv, "world_running", True):
            stats["stories"] = stories           # paused before we start -- no genesis call
            return stats
        history = list(getattr(srv, "story_history", []))
        cache = getattr(srv, "story_cache", None)
        if cache is None:
            cache = []
            srv.story_cache = cache
        # cells-act: after training (initial_burst spent), the autonomous chronicle
        # is authored by the on-stage character's own n-gram voice -- no LLM. When no
        # character can speak yet (none has lived >= _VOICE_MIN) _cells_beat returns
        # None and we fall back to the LLM genesis cache ("until then we just narrate
        # it").
        cells_act = (str(getattr(srv, "beat_author", "llm")) == "cells"
                     and not bool(getattr(srv, "initial_burst", 0)))
        # the periodic DRIP (progression=True from the autonomous consolidator
        # only -- NOT turn-based sends or /world/step): every `progression_every`
        # cycles, ONE feed this call is an LLM-authored ADVANCING beat, TRUSTED
        # (self_authored=False) so it trains the n-gram and the story evolves
        # instead of stammering. Spiral-safe (LLM-authored, not n-gram output).
        # Skipped unless cells_act (no drip during training or llm beat_author).
        prog_every = int(getattr(srv, "progression_every", 0))
        prog_due = (progression and cells_act and prog_every > 0
                    and cycle > 0 and (cycle % prog_every == 0))
        did_prog = False
        # refill the genesis cache if low -- ONE model call -> ~CHAPTER cached feeds.
        # Skipped in cells-act (the n-gram authors; the cache is only the no-speaker
        # fallback, refilled on demand inside the loop).
        if (not cells_act and len(cache) < _LOW_WATER
                and not (abort_if_disarmed and not getattr(srv, "world_running", True))):
            cache.extend(_genesis(srv))
            srv.story_cache = cache
        for _ in range(feeds):
            if abort_if_disarmed and not getattr(srv, "world_running", True):
                break                           # paused between feeds -- stop, don't spin
            passage = None
            self_authored = False
            if cells_act:
                # the drip: one LLM advancing beat (trusted) per due cycle, then
                # back to the cells n-gram voice. Falls back to a cells beat if
                # the LLM call fails (empty), so a model hiccup never stalls a feed.
                if prog_due and not did_prog:
                    did_prog = True
                    pbeat = _progression_beat(srv)
                    if pbeat:
                        passage = pbeat
                        self_authored = False       # TRUSTED -> trains the n-gram
                        print(f"[cells] progression beat: {pbeat[:120]}", file=sys.stderr)
                if passage is None:
                    passage = _cells_beat(srv, mem)    # the on-stage char's n-gram voice (no LLM)
                    if passage is not None:
                        self_authored = True            # this beat is the cell speaking, not the LLM
            if passage is None:
                # LLM path: training, or the cells-act "narrate it" fallback when no
                # character can speak yet.
                if not cache:                       # drained; one more chapter before giving up
                    if abort_if_disarmed and not getattr(srv, "world_running", True):
                        break
                    cache.extend(_genesis(srv))
                    srv.story_cache = cache
                    if not cache:                   # model unavailable -- stop, don't spin
                        stories.append({"seed": "", "text": "", "result": "no-story", "pov": False})
                        break
                passage = cache.pop(0)
            # self_authored beats are absorbed (the world grows + recall stays rich)
            # but EXCLUDED from the n-gram's training corpus + the cells-act seed, so
            # the n-gram speaks only from trusted (LLM-seeded) material and can't
            # spiral on its own stammer.
            r = mem.feed(passage, ceiling, cycle=cycle, self_authored=self_authored)
            stats["fed"] += int(r.get("fed", 0))
            if r.get("fed"):
                res = "fed"
            else:                               # dropped by the dedup/vague guard
                res = "skipped"
                stats["skipped"] += 1
            tags = r.get("tags") or []
            stories.append({"seed": "", "text": passage, "result": res,
                            "pov": pov_id is not None and pov_id in tags})
            history.append(passage)             # advance the chronicle (last 8 scenes)
            if len(history) > 8:
                history = history[-8:]
            srv.story_history = history
            stats["field_size"] = r.get("field_size", stats["field_size"])
            if "cells_level" in r:
                stats["cells_level"] = round(float(r["cells_level"]), 2)
            stats["free_energy"] = float(r.get("free_energy", stats["free_energy"]))
    stats["stories"] = stories
    srv.cache_depth = len(getattr(srv, "story_cache", []))
    return stats




def _cells_beat(srv, mem) -> str | None:
    """The cells-act author: the on-stage character's own per-character n-gram
    voice writes the next beat (NO LLM). The on-stage character is the freshest in
    the narrative (``cast.on_stage`` -- the POV when it is live, whoever is in the
    scene when the POV faded); their ``voice_for`` n-gram is trained on the
    chronicle scenes they lived. A character only authors once it has lived enough
    (>= _VOICE_MIN tagged scenes); until then this returns None and the caller
    falls back to the LLM genesis ("we just narrate it"). The beat is seeded from
    the on-stage char's last_scene tokens; the RNG is deterministic per (char,
    cycle, field size) so a re-feed within a cycle is stable. Returns the passage,
    or None when no character can speak yet (or the cast is empty)."""
    import hashlib
    import random
    from .generator import _tok as _gen_tokens
    cast = mem.cast
    onstage = cast.on_stage()
    if onstage is None:
        return None
    lm = cast.voice_for(onstage.id, mem.store)
    if lm is None:
        return None
    # seed from a TRUSTED (LLM-seeded) lived scene of the on-stage char -- NOT
    # their last_scene (which may be a self-authored stammer; seeding from a
    # stammer starts the next beat mid-gibberish and the n-gram echoes its own
    # output). Pick one deterministically-random per cycle (a slice of the same
    # hash that salts the RNG) so the seed VARIES beat-to-beat -- always seeding
    # from the single most-recent trusted scene pins the trigram to one path and
    # every beat opens identically. Falls back to last_scene only if no trusted
    # scene exists yet. Trusted-only seed => the cell never trains on its own
    # stammer => no spiral.
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    digest = hashlib.sha256(
        f"{onstage.id}\x00{cycle}\x00{len(mem.store)}".encode()).digest()
    cands = [ex for ex in mem.store.exemplars
             if onstage.id in getattr(ex, "tags", []) and ex.action
             and not getattr(ex, "self_authored", False)]
    if cands:
        seed_src = cands[int.from_bytes(digest[8:12], "big") % len(cands)].action
    else:
        seed_src = getattr(onstage, "last_scene", "")
    seed = _gen_tokens(seed_src)[:2]
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    try:
        out = lm.generate(seed, rng, max_tokens=24)
    except Exception as e:
        print(f"[cells] beat generation failed ({e})", file=sys.stderr)
        return None
    out = (out or "").strip()
    if out:
        print(f"[cells] {onstage.name} authors: {out[:120]}", file=sys.stderr)
    return out if out else None


class ProxyHandler(BaseHTTPRequestHandler):
    # protocol_version lets us reuse connections to the client
    protocol_version = "HTTP/1.1"

    # ---- routing ----------------------------------------------------------
    def do_POST(self):
        if self.path == "/api/chat":
            self._chat()
        elif self.path.startswith("/v1/messages"):
            self._messages()
        elif self.path in ("/warmup", "/warmup/"):
            self._warmup()
        elif self.path in ("/live", "/live/"):
            self._live()
        elif self.path in ("/settings", "/settings/"):
            self._settings()
        elif self.path in ("/cast", "/cast/"):
            self._cast()
        elif self.path in ("/world", "/world/"):
            self._world()
        elif self.path in ("/world/run", "/world/run/"):
            self._world_run()
        elif self.path in ("/world/stop", "/world/stop/"):
            self._world_stop()
        elif self.path in ("/world/step", "/world/step/"):
            self._world_step()
        elif self.path in ("/world/controls", "/world/controls/"):
            self._world_controls()
        elif self.path in ("/adjust_narrative", "/adjust_narrative/"):
            self._adjust_narrative()
        elif self.path in ("/preview_narrative", "/preview_narrative/"):
            self._preview_narrative()
        elif self.path in ("/render", "/render/"):
            self._render()
        elif self.path in ("/situation", "/situation/"):
            self._situation()
        elif self.path in ("/chronicle", "/chronicle/"):
            self._chronicle()
        elif self.path in ("/events", "/events/"):
            self._post_events()
        else:
            self._forward()

    def do_GET(self):
        if getattr(self.server, "viewer", False):
            if self.path in ("/viewer", "/viewer/"):
                self._serve_viewer(); return
            if self.path.split("?")[0] in ("/three.min.js",):
                self._serve_asset("three.min.js", "application/javascript"); return
            if self.path in ("/state", "/state/"):
                self._state(); return
            if self.path in ("/settings", "/settings/"):
                self._settings(); return
            if self.path.split("?")[0] in ("/events", "/events/"):
                self._get_events(); return
            if self.path.split("?")[0] in ("/neighbors", "/neighbors/"):
                self._neighbors(); return
            if self.path.split("?")[0] in ("/worlds", "/worlds/"):
                self._list_worlds(); return
        if getattr(self.server, "cortex_mode", False) and self.path.split("?")[0] in ("/cortex/state", "/cortex/state/"):
            self._cortex_state(); return
        self._forward()

    def do_DELETE(self):
        if getattr(self.server, "viewer", False) and self.path.split("?")[0] in ("/events", "/events/"):
            lock = getattr(self.server, "_events_lock", None)
            if lock is not None:
                with lock:
                    self.server._events = []
            else:
                self.server._events = []
            out = json.dumps({"ok": True, "cleared": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        self._forward()

    # ---- the world premise (--system) + the time-skipper (/warmup) ------------
    def _narrator_frame(self) -> str:
        """The narrator frame for the LLM-escalate path: when a POV protagonist is
        designated, tell the model to narrate in third person from their
        perspective, in their voice, treating the user's messages as their
        thoughts. Empty string when no POV (no-op). Inert in --self-author (the
        escalate path generates from the field there, no backend call)."""
        mem = self.server.membrane
        cid = getattr(mem.cast, "pov_id", None)
        if cid is None:
            return ""
        c = mem.cast.by_id(cid)
        if c is None:
            return ""
        bits = [f"You are the narrator of this world. The reader IS {c.name}"]
        if c.role:
            bits[0] += f", {c.role}"
        bits.append("the user's messages are their thoughts and intent")
        bits.append(f"narrate the world in third person from {c.name}'s perspective")
        if c.voice:
            bits.append(f"write in {c.name}'s voice: {c.voice}")
        return ". ".join(bits) + "."

    def _apply_system(self, body: dict) -> None:
        """Inject the opt-in ``--system`` world premise so it actually reaches the
        model. Backend-aware (the two servers take the premise in different shapes):

          * Anthropic ``/v1/messages``: a top-level ``system`` field (only when the
            client sent none -- client over proxy-default). The Anthropic responder
            forwards it; on escalate-with-hint it prepends the hint to this field.
          * Ollama ``/api/chat``: a ``role: system`` MESSAGE prepended to ``messages``
            -- Ollama's /api/chat SILENTLY IGNORES a top-level ``system`` field (that
            field belongs to /api/generate), so the premise must ride as a system
            message or it never reaches the model. Prepended only when the client's
            first message isn't already a system message (a client that sets its own
            system wins, the same client-over-proxy precedence).

        Either way the gate is unaffected: it embeds ``_last_user`` (user messages
        only), so a system message/field never enters the field's situations. The
        premise authors *what world this is* (a constant ground, outside the field,
        re-sent every escalate so it never scrolls off the context window); the
        wandering authors *where you've been* (the field's variety). No-op when
        ``--system`` is unset."""
        sp = getattr(self.server, "system_prompt", None)
        frame = self._narrator_frame()
        if not sp and not frame:
            return
        if frame:
            sp = (sp + "\n\n" + frame) if sp else frame
        if getattr(self.server, "anthropic", False):
            if not body.get("system"):
                body["system"] = sp
            return
        msgs = body.get("messages")
        if isinstance(msgs, list) and (not msgs or msgs[0].get("role") != "system"):
            body["messages"] = [{"role": "system", "content": sp}] + list(msgs)

    def _json_err(self, code: int, msg: str) -> None:
        out = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _send_json(self, obj: dict, code: int = 200) -> None:
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    # ---- /events: an opt-in client action recorder (debug) ----------------------
    # The viewer page, when opened with ?rec=1, captures keyboard/mouse actions and
    # POSTs them here. The proxy keeps an in-memory ring buffer (cap _EVENTS_CAP)
    # that a developer reads back via GET /events ON REQUEST (not streamed, not
    # monitored live). The page redacts password fields so the Ollama token is
    # never logged. DELETE /events clears the buffer. No field/audit persistence,
    # no model call -- pure debug scratch.
    def _post_events(self) -> None:
        srv = self.server
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(n).decode("utf-8", "replace") if n else "{}"
            body = json.loads(raw) if raw.strip() else {}
        except Exception:
            self._json_err(400, "events body is not JSON"); return
        evs = body.get("events") if isinstance(body, dict) else None
        if not isinstance(evs, list):
            self._json_err(400, "events must be {events: [...]}"); return
        lock = getattr(srv, "_events_lock", None)
        buf = getattr(srv, "_events", None)
        if buf is None or lock is None:
            self._json_err(503, "event recorder not initialized"); return
        with lock:
            buf.extend(evs)
            if len(buf) > _EVENTS_CAP:
                del buf[: len(buf) - _EVENTS_CAP]
        out = json.dumps({"ok": True, "buffered": len(evs),
                          "total": len(buf)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _get_events(self) -> None:
        srv = self.server
        lock = getattr(srv, "_events_lock", None)
        buf = getattr(srv, "_events", None)
        evs = []
        if lock is not None and buf is not None:
            with lock:
                evs = list(buf)
        # ?clear=1 drains the buffer after reading (a one-shot "read-and-reset")
        if (self.path.split("?")[1:2] or [""])[0].split("&").count("clear=1") and lock is not None and buf is not None:
            with lock:
                buf.clear()
        out = json.dumps({"events": evs, "count": len(evs)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _warmup(self) -> None:
        """The time-skipper on demand: ONE model call reads the ``--system``
        premise and builds a world palette, then N ``learn_pair`` absorptions of
        premise-grounded ``(situation, stub-action)`` pairs -- NO per-event model
        calls, local mxbai embeddings only. The field becomes the memory of the
        world having lived offscreen, so when the user arrives the cloud is
        populated and the gate has real variety to act on. Requires ``--system``
        (the premise grounds the palette); returns 400 otherwise. The gate path is
        untouched -- this only writes exemplars through the existing ``learn_pair``
        teed-intake seam (runtime.py)."""
        srv = self.server
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        n = int(body.get("n", 25))
        if n < 1:
            self._json_err(400, "n must be >= 1"); return
        premise = getattr(srv, "system_prompt", None)
        if not premise:
            self._json_err(400, "warmup needs a --system premise to ground the "
                                 "world (launch with --system \"...\")"); return
        model = body.get("model") or getattr(srv, "warmup_model", None)
        seed = int(body.get("seed", getattr(srv, "warmup_seed", 0)))
        from .warmup import run_warmup, build_palette
        palette, _src = build_palette(srv.backend, model, premise)
        srv.palette = palette            # cache it so the no-model /live loop can reuse it
        stats = run_warmup(srv.membrane, srv.backend, model, premise, n, seed, palette=palette)
        out = json.dumps(stats).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _live(self) -> None:
        """The offscreen LOOP -- the world living on its own, NO model call.

        Distinct from ``/warmup`` (which spends one model call to build a fresh
        palette each time): ``/live`` REUSES the cached palette (``srv.palette``,
        built once at startup warm-up or on the first ``/live``/``/warmup`` call --
        the single warm-up exemption, the only place the LLM is used) and does TWO
        kinds of no-model growth:

          * ``n``  palette-grounded ``(situation, stub-action)`` absorptions via
            ``learn_pair`` (local mxbai embeddings). These DEDUP on a small palette
            -> saturate to 0 added; that's expected.
          * ``entropy`` raw ENTROPY VECTORS via ``Membrane.inject_entropy``: random
            unit vectors appended straight to the field. ~orthogonal to everything,
            so they never dedup and never become a real query's nearest neighbour
            (gate untouched) -- but each raises the effective rank, so free_energy
            climbs WITHOUT BOUND. This is the flood that keeps the world growing
            past Cortex: entropy injected directly in the field's vectors, in the
            space in-between the real embeddings.

        Zero LLM. This is what the viewer's play/pause/step drives (and what the
        per-message autoflood fires): the cloud grows offscreen while you watch.

        If no palette is cached yet (no ``--warmup`` at startup, no prior
        ``/warmup``/``/live``), build one now -- the one warm-up model call -- and
        cache it, so every subsequent tick is model-free. Falls back to
        premise-words (no model) when there is no model/backend/premise."""
        srv = self.server
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        from .variety import cells_rank, entropy_count as _entropy_count
        mem = srv.membrane
        exs = mem.store.exemplars
        theo = len(exs[0].vec) if exs else 1024      # the THEORETICAL 100%: the embedding dim = max effective rank the field can ever reach (its horizon; never crossed)
        ceiling = float(body.get("entropy_ceiling", getattr(srv, "entropy_ceiling", 0.01)))
        ceiling = max(0.0, min(1.0, ceiling))        # the never-cross line as a fraction of the theoretical 100%
        srv.entropy_ceiling = ceiling               # PERSIST: the dial is the single source of truth now -- /state, the HUD, and the monitor all read this, so they agree and survive across ticks (no more client/srv mismatch that let a stray drag drown the world)
        cap = max(0, int(round(ceiling * theo)))     # the operating ceiling: ballast may fill up to here, never past
        cl = cells_rank(mem.store)                   # the CELLS' level: variety the real cells earned (the structured world; the ballast drowns it past ~cap == cl)
        cur_ent = _entropy_count(mem.store)
        trimmed = 0
        if cur_ent > cap:                            # enforce the never-cross line: drop excess ballast (real cells untouched)
            trimmed = mem.trim_entropy(cap)
            cur_ent = _entropy_count(mem.store)
        n = int(body.get("n", 0))                   # palette-grounded absorptions (dedup on a saturated palette -> 0)
        ent_rate = body.get("entropy_rate")          # the growth-speed dial: fraction of the CAP filled per tick
        if ent_rate is not None:
            # Grow the ballast toward the ceiling, at a speed the user dials. Per tick:
            # dial x cap vectors, never past the cap. The ceiling is the never-cross line
            # (a fraction of the theoretical 100%); once the ballast reaches it the flood
            # stops. Start at 1% (cap ~10 on a 1024-d field) -- the background stays a
            # faint fraction of the world, never approaching the theoretical 100%.
            per_tick = int(round(float(ent_rate) * cap))
            ent = min(max(0, per_tick), max(0, cap - cur_ent))
        else:
            ent = int(body.get("entropy", 0))        # raw count (manual / legacy override)
        if n < 0 or ent < 0:
            self._json_err(400, "n and entropy must be >= 0"); return
        premise = getattr(srv, "system_prompt", None) or " "
        seed = int(body.get("seed", getattr(srv, "warmup_seed", 0)))
        stats = {"added": 0, "entropy": 0, "entropy_trimmed": trimmed,
                 "field_size": len(mem.store), "palette_source": "provided",
                 "theoretical_max": theo, "entropy_ceiling": round(ceiling, 4),
                 "entropy_cap": cap, "cells_level": round(float(cl), 2),
                 "entropy_count": cur_ent}
        # the embeddings: palette-grounded absorptions (no model). Saturate on a
        # small palette -> 0 added; that's fine, the entropy vectors carry the growth.
        # The palette is built ONLY here (and only if n>0) -- the one warm-up model
        # call -- so an entropy-only tick (n=0) is strictly LLM-free.
        if n > 0:
            palette = getattr(srv, "palette", None)
            if palette is None:
                from .warmup import build_palette
                palette, _src = build_palette(srv.backend, getattr(srv, "warmup_model", None),
                                              premise)
                srv.palette = palette
            from .warmup import run_warmup
            s = run_warmup(srv.membrane, srv.backend, None, premise, n, seed,
                           curriculum=False, palette=palette)
            stats["added"] = s.get("added", 0)
            stats["field_size"] = s.get("field_size", stats["field_size"])
        # the in-between: raw entropy vectors (the CMB -- the thermal background)
        # injected into the field's geometry, CAPPED at the ceiling (a fraction of
        # the theoretical 100%, never crossed). ~orthogonal to everything -> never
        # dedup, never nearest -> gate untouched; fills the ballast up to the ceiling
        # at the dialed speed, then stops. Never approaches the theoretical 100%.
        if ent > 0:
            stats["entropy"] = mem.inject_entropy(ent)
            stats["field_size"] = len(mem.store)
            stats["entropy_count"] = _entropy_count(mem.store)
        out = json.dumps(stats).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    # ---- the server-owned world loop (RUN / STOP / STEP) -----------------------
    # The world clock lives on the server, not the viewer. The consolidator
    # (consolidate.py) feeds+digests every cycle while ``srv.world_running`` is True;
    # STOPPED, it idles. ``/world/step`` (STOPPED only) advances the world by one
    # feed+digest without arming the loop.
    def _world_run(self) -> None:
        _vlog(self.server, "RUN")
        _set_world_running(self.server, True, user=True)
        self._send_json({"ok": True, "running": True})

    def _world_stop(self) -> None:
        _vlog(self.server, "STOP")
        _set_world_running(self.server, False)
        self._send_json({"ok": True, "running": False})

    def _world_step(self) -> None:
        """STOPPED-only: one ``_run_feeds(...,1)`` + one ``_digest``. Refused while
        RUNNING (stop first). Bumps the cycle counter + publishes the step's
        stories so the viewer narrates it via ``storyPush``."""
        srv = self.server
        if getattr(srv, "world_running", False):
            _vlog(srv, "STEP refused (world RUNNING)")
            self._json_err(409, "world is RUNNING; stop before stepping"); return
        if not getattr(srv, "system_prompt", None):
            _vlog(srv, "STEP refused (no premise)")
            self._json_err(400, "step needs a --system premise"); return
        _vlog(srv, "STEP")
        mem = srv.membrane
        stats = _run_feeds(srv, mem, 1, abort_if_disarmed=False)
        from .consolidate import _digest
        digest = _digest(srv, mem, budget=min(8, len(mem.store)))
        cs = dict(getattr(srv, "consolidation_stats", {}))
        cs["cycles"] = int(cs.get("cycles", 0)) + 1
        mem.cast.cycles = cs["cycles"]      # mirror so the next cast.save persists the clock
        cs["stories"] = stats.get("stories", [])
        cs["fed"] = stats.get("fed", cs.get("fed", 0))
        # merge the digest's thermo/novelty stats so /state stays live when the
        # world is driven by STEP (no consolidator thread runs while STOPPED, so
        # without this eff_rank/free_energy/mean_novelty would freeze at 0).
        b = float(min(8, len(mem.store)))
        cs["swept"] = digest.get("swept", 0)
        cs["budget"] = round(b, 1)
        cs["mean_novelty"] = round(digest.get("mean_novelty", 0.0), 3)
        cs["settled"] = digest.get("settled", 0)
        cs["eff_rank"] = round(digest.get("eff_rank", 0.0), 2)
        cs["free_energy"] = round(digest.get("free_energy", 0.0), 2)
        cs["cache"] = len(getattr(srv, "story_cache", []))
        cs["cpu_actual"] = 0.0                      # no closed-loop duty on a manual step
        cs["cpu_target"] = round(float(getattr(srv, "cpu_target", 0.0)), 3)
        srv.consolidation_stats = cs
        stats["cycles"] = cs["cycles"]
        self._send_json(stats)

    def _world_controls(self) -> None:
        """The growth dials (single source of truth), persisted to settings.json.
        Any subset of {entropy_ceiling, story_mode, feed_rate, cpu_target,
        cycle_speed, beat_author, representation}; the full set is echoed back."""
        srv = self.server
        body = self._json_body()
        _chg = {k: body[k] for k in ("entropy_ceiling", "story_mode", "feed_rate",
                "cpu_target", "cycle_speed", "beat_author", "representation",
                "progression_every")
                if k in body}
        if _chg:
            _vlog(srv, "controls " + " ".join(f"{k}={_chg[k]}" for k in _chg))
        if "entropy_ceiling" in body:
            try: srv.entropy_ceiling = max(0.0, min(1.0, float(body["entropy_ceiling"])))
            except (TypeError, ValueError): pass
        if "story_mode" in body:
            m = str(body["story_mode"]).lower()
            srv.story_mode = m if m in ("story", "variety") else "story"
        if "feed_rate" in body:
            try: srv.feed_rate = max(0.0, min(32.0, float(body["feed_rate"])))
            except (TypeError, ValueError): pass
        if "cpu_target" in body:
            try: srv.cpu_target = max(0.0, min(1.0, float(body["cpu_target"])))
            except (TypeError, ValueError): pass
        if "cycle_speed" in body:
            try: srv.cycle_speed = max(0.25, min(8.0, float(body["cycle_speed"]))) or 1.0
            except (TypeError, ValueError): pass
        if "beat_author" in body:
            ba = str(body["beat_author"]).lower()
            if ba in ("llm", "cells"):
                srv.beat_author = ba
        if "representation" in body:
            rep = str(body["representation"]).lower()
            srv.representation = rep if rep in ("llm", "corrected", "raw") else "llm"
        if "progression_every" in body:
            try: srv.progression_every = max(0, int(body["progression_every"]))
            except (TypeError, ValueError): pass
        _save_settings(srv, srv.backend, getattr(srv, "warmup_model", None))
        self._send_json({"ok": True,
            "entropy_ceiling": float(getattr(srv, "entropy_ceiling", 0.05)),
            "story_mode": str(getattr(srv, "story_mode", "story")),
            "feed_rate": float(getattr(srv, "feed_rate", 1.0)),
            "cpu_target": float(getattr(srv, "cpu_target", 0.2)),
            "cycle_speed": float(getattr(srv, "cycle_speed", 1.0) or 1.0),
            "beat_author": str(getattr(srv, "beat_author", "llm")),
            "representation": str(getattr(srv, "representation", "llm")),
            "progression_every": int(getattr(srv, "progression_every", 0))})

    def _adjust_narrative(self) -> None:
        """The world-viewer's chat path -- a TURN in a turn-based text RPG: the
        player acts (their adjustment is walked into the world), then the WORLD
        moves one step (the cells author a response beat + the clock ticks), then
        the reply is a SITUATION REPORT narrating that response. POV selected ->
        the I walks the protagonist's region (no exemplar); no POV -> mixed into
        the whole world + absorbed as an untagged exemplar. The pre-mutation
        narration is computed BEFORE the walk (no self-echo). Serialized on the
        feed lock (same lock the consolidator holds) so it can't race an
        autonomous feed."""
        srv = self.server
        mem = srv.membrane
        body = self._json_body()
        text = str(body.get("text", "")).strip()
        if not text:
            self._json_err(400, "text required"); return
        _vlog(srv, f"chat {text[:100]!r}")
        cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
        # mem.adjust serializes its mutation on self.mut_lock (== srv.feed_lock
        # at the proxy) so it can't race the consolidator; do NOT also take the
        # lock here -- that would re-acquire the same non-reentrant Lock -> deadlock.
        narration, pov_active, absorbed = mem.adjust(text, cycle)
        _vlog(srv, f"chat -> pov={mem.cast.pov_id} absorbed={absorbed} narr={len(narration)}c")
        # TURN-BASED: the player's action is a TURN. After absorbing it, advance
        # the world one beat (the cells author the world's response -- no LLM in
        # the live loop when beat_author=cells) + tick the clock, so the
        # characters actually move/respond instead of freezing until the next
        # autonomous cycle. The beat is self_authored (absorbed into the field but
        # excluded from the n-gram corpus -> spiral-proof, see _run_feeds).
        # _run_feeds takes feed_lock itself (adjust already released it), so no
        # deadlock; abort_if_disarmed=False so the turn advances even when the
        # world is STOPPED (the turn-based default -- play with RUN off).
        adv = _run_feeds(srv, mem, 1, abort_if_disarmed=False)
        cs = dict(getattr(srv, "consolidation_stats", {}))
        cs["cycles"] = int(cs.get("cycles", 0)) + 1
        mem.cast.cycles = cs["cycles"]      # mirror so the next cast.save persists the clock
        cs["fed"] = int(cs.get("fed", 0)) + int(adv.get("fed", 0))
        cs["stories"] = adv.get("stories", [])
        srv.consolidation_stats = cs
        # the world's response beat, folded into the situation report as the latest
        # "new development" so the reply narrates what the world just DID (the beat
        # is self_authored -> excluded from the POV's trusted scene list, so it is
        # passed explicitly as the development, not read back from the field). The
        # player's own action is already shown as their chat bubble; this report is
        # the world's reply. Empty beat (no-speaker fallback gave nothing) -> just
        # the POV's recent scenes.
        beat = ""
        for s in reversed(adv.get("stories", [])):
            if s.get("text"):
                beat = str(s["text"]); break
        # The chat reply is a SITUATION REPORT. adjust + the turn feed have
        # released mut_lock, so _render_situation reads the post-turn field
        # lock-free. The render is display-only -- it never _learn/feed/save.
        sit = _render_situation(srv, mem.cast.pov_id, text=beat or None)
        _vlog(srv, f"chat turn -> cycle={cs['cycles']} beat={beat[:60]!r}")
        self._send_json({"situation": sit["situation"],
                         "raw_scenes": sit["raw_scenes"],
                         "scene_count": sit["scene_count"],
                         "pov_id": sit["pov_id"],
                         "pov_active": pov_active, "absorbed": absorbed,
                         "pov": mem.cast.pov_id, "narration": narration,
                         "beat": beat, "cycles": cs["cycles"]})

    def _preview_narrative(self) -> None:
        """Read-only preview of the world's prospective situation for ``text``:
        a stateless situation render (no state walk, no exemplar, no save) with
        the typed text folded in as a "new development". Lets the viewer show the
        world's reply as the player types, before committing."""
        srv = self.server
        body = self._json_body()
        text = str(body.get("text", "")).strip()
        if not text:
            self._send_json({"situation": "", "narration": ""}); return
        _vlog(srv, f"preview {text[:60]!r}")
        sit = _render_situation(srv, srv.membrane.cast.pov_id, text)
        self._send_json({"situation": sit["situation"],
                         "raw_scenes": sit["raw_scenes"],
                         "narration": sit["situation"]})

    def _render(self) -> None:
        """Render one raw passage into readable in-voice prose (the LLM'd view).
        On-demand, stateless: the viewer calls this per beat only when its
        representation toggle is LLM'd, so the world clock (which flows raw beats)
        never blocks on the LLM. Body ``{"text", "pov_id"?}`` ->
        ``{"rendered", "raw"}``. Degrades to raw on any failure."""
        srv = self.server
        body = self._json_body()
        raw = str(body.get("text", "")).strip()
        if not raw:
            self._send_json({"rendered": "", "raw": ""}); return
        pov_id = body.get("pov_id")
        _vlog(srv, f"render {raw[:60]!r}")
        rendered = _render_passage(srv, raw, pov_id=pov_id)
        self._send_json({"rendered": rendered, "raw": raw})

    def _situation(self) -> None:
        """Render a SITUATION REPORT for the POV / on-stage character -- the
        chat's situation channel. Read-only, stateless RAG (no LLM history):
        ``{"pov_id"?, "text"?}`` -> ``{"situation","raw_scenes","pov_id",
        "scene_count"}``. Degrades to last_scene / "the world is quiet…" on a
        thin char or backend failure. Never mutates the field or the cast."""
        srv = self.server
        body = self._json_body()
        pov_id = body.get("pov_id")
        text = str(body.get("text", "") or "").strip() or None
        pid = pov_id
        _vlog(srv, f"situation pov={pid} text={(text or '')[:40]!r}")
        self._send_json(_render_situation(srv, pov_id=pov_id, text=text))

    def _chronicle(self) -> None:
        """Compress the world's recent autonomous beats into a flowing multi-
        paragraph narration -- the rolling ``what's happening`` view for when the
        world runs on its own clock. Read-only, stateless (no LLM history):
        ``{"pov_id"?}`` -> ``{"chronicle","raw_beats","pov_id","scene_count",
        "cycle"}``. POV set -> that character's thread; no POV -> the whole world.
        Degrades gracefully. Never mutates the field or the cast."""
        srv = self.server
        body = self._json_body()
        pov_id = body.get("pov_id")
        _vlog(srv, f"chronicle pov={pov_id}")
        self._send_json(_render_chronicle(srv, pov_id=pov_id))

    # ---- the live 3D viewer (opt-in via --viewer) -----------------------------
    # Read-only surface: GET /viewer serves the static page, GET /state returns a
    # JSON snapshot read IN-PROCESS from the live store (+ the audit tail). The gate
    # path (_chat/_messages/_forward) is untouched -- the page chats through the real
    # /api/chat. The page is same-origin, so no CORS, no key handling, no second server.
    def _cast(self) -> None:
        """Manage the [[cast]] ledger from the menu: retire / pin / admit. Retire
        removes a character AND strips its id from every exemplar's tags (its stars
        revert to neutral). Pin toggles a character's protection. Admit (manual)
        names a new character. Serialized on ``feed_lock`` so it can't race a feed."""
        import numpy as np
        srv = self.server
        mem = srv.membrane
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        action = str(body.get("action", ""))
        cid = int(body.get("id", -1))
        if action == "admit":
            _vlog(srv, f"cast admit name={str(body.get('name',''))[:40]!r}")
        else:
            _vlog(srv, f"cast {action} id={cid}")
        ok = False
        with srv.feed_lock:
            if action == "retire":
                ok = mem.cast.retire(cid, mem.store)
                if ok:
                    mem.store.save(mem.field_path)
                    mem.cast.save(mem.cast_path)
            elif action == "pin":
                ok = mem.cast.pin(cid)
                if ok:
                    mem.cast.save(mem.cast_path)
            elif action == "admit":
                name = str(body.get("name", "")).strip()
                role = str(body.get("role", "")).strip()
                voice = str(body.get("voice", "")).strip()
                if name:
                    try:
                        seed_text = f"{name} — {role} — {voice}" if (role or voice) else name
                        v = mem.embedder.embed([seed_text])[0]
                        c = mem.cast.admit(name, np.asarray(v, dtype=float),
                                           anchor_idx=None,
                                           cycle=int(getattr(srv, "consolidation_stats",
                                                            {}).get("cycles", 0)),
                                           role=role, voice=voice)
                        ok = c is not None
                        if ok:
                            mem.cast.save(mem.cast_path)
                    except Exception:
                        ok = False
            elif action == "pov":
                if cid == -1:                       # "THE WORLD" in the viewer's POV picker
                    mem.cast.pov_id = None
                    ok = True
                else:
                    ok = mem.cast.set_pov(cid)
                if ok:
                    mem.cast.save(mem.cast_path)
        # the viewer's castAction re-renders from THIS response (before the next
        # /state poll), so it must carry the `pov` flag per char -- bare to_json()
        # omits it (pov is a Cast-level pointer, not a Character field) and the
        # POV badge + syncPov() would blank until the next poll tick. Enrich here.
        _view = [dict(c, pov=(mem.cast.pov_id == c["id"]))
                 for c in mem.cast.to_json()]
        out = json.dumps({"ok": ok, "cast": _view}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _list_worlds(self) -> None:
        """GET /worlds -> the saved world slots (dirs under --worlds-dir that
        hold a field.json). Used to populate the viewer's LOAD list."""
        from pathlib import Path
        srv = self.server
        wd = Path(getattr(srv, "worlds_dir", "worlds"))
        names = []
        if wd.is_dir():
            for d in sorted(wd.iterdir()):
                if d.is_dir() and (d / "field.json").exists():
                    names.append(d.name)
        out = json.dumps({"worlds": names}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _world(self) -> None:
        """save/load/new the whole world (field + cast + audit).

        POST JSON {action:"save"|"load"|"new", name:"<slot>"}.
          save -> copy the live field/cast/audit to ``worlds/<name>/``.
          load -> swap the live store + cast in from a saved world (under
                  ``feed_lock`` so it can't race the consolidator) and reset the
                  rolling chronicle; the loaded world is persisted to the LIVE
                  paths so a restart keeps it.
          new  -> empty field + cast + audit (a blank slate; the protagonist is
                  gone -- re-seed with --protagonist/--character on next launch).
        Responds with the post-action cast (enriched with ``pov``) so the viewer
        re-renders immediately."""
        import shutil
        from pathlib import Path
        from .field import ExemplarStore
        from .cast import Cast
        srv = self.server
        mem = srv.membrane
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        action = str(body.get("action", ""))
        name = str(body.get("name", "")).strip()
        # a safe slot name (no path traversal): keep alnum/dash/underscore/space
        safe = re.sub(r"[^\w \-]", "", name).strip()
        _vlog(srv, f"world {action} {safe!r}")
        wd = Path(getattr(srv, "worlds_dir", "worlds"))
        ok = False
        err = ""
        with srv.feed_lock:
            if action == "save":
                if not safe:
                    err = "save needs a name"
                else:
                    try:
                        dest = wd / safe
                        dest.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(mem.field_path, dest / "field.json")
                        shutil.copy2(mem.cast_path, dest / "cast.json")
                        ap = Path(mem.audit_path)
                        if ap.exists():
                            shutil.copy2(ap, dest / "audit.jsonl")
                        else:
                            (dest / "audit.jsonl").touch()
                        ok = True
                    except Exception as e:
                        err = f"save failed: {e}"
            elif action == "load":
                src = wd / safe
                if not safe or not (src / "field.json").exists():
                    err = "no such saved world"
                else:
                    try:
                        mem.store = ExemplarStore.load(src / "field.json")
                        mem.cast = Cast.load(src / "cast.json", embedder=mem.embedder)
                        # persist the loaded world to the LIVE paths (next mutation
                        # + a restart keep it); reset the audit too.
                        mem.store.save(mem.field_path)
                        mem.cast.save(mem.cast_path)
                        ap = Path(mem.audit_path)
                        try:
                            shutil.copy2(src / "audit.jsonl", ap)
                        except Exception:
                            ap.write_text("")
                        _reset_world_runtime(srv, mem, rearm_burst=True)
                        ok = True
                    except Exception as e:
                        err = f"load failed: {e}"
            elif action == "delete":
                src = wd / safe
                live_dir = Path(mem.field_path).resolve().parent
                if not safe or not (src / "field.json").exists():
                    err = "no such saved world"
                elif src.exists() and src.resolve() == live_dir:
                    err = "refuse to delete the live world"
                else:
                    try:
                        shutil.rmtree(src)
                        ok = True
                    except Exception as e:
                        err = f"delete failed: {e}"
            elif action == "new":
                try:
                    mem.store = ExemplarStore()             # empty field
                    mem.cast = Cast(embedder=mem.embedder)   # empty cast (no POV)
                    mem.store.save(mem.field_path)
                    mem.cast.save(mem.cast_path)
                    Path(mem.audit_path).write_text("")
                    _reset_world_runtime(srv, mem, rearm_burst=True)
                    ok = True
                except Exception as e:
                    err = f"new failed: {e}"
            else:
                err = "unknown action (save|load|new|delete)"
        _view = [dict(c, pov=(mem.cast.pov_id == c["id"]))
                 for c in mem.cast.to_json()]
        out = json.dumps({"ok": ok, "error": err, "cast": _view}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _serve_viewer(self):
        # serve the viewer, but never a stale one: re-read when the file's mtime
        # advances (so editing viewer.html while the proxy runs is picked up without
        # a restart) AND tell the browser not to cache (no Cache-Control header let
        # a stale pre-slider page survive a normal reload -- the feeding dials
        # vanished that way).
        import os, time
        srv = self.server
        _vlog(srv, "page load")
        p = os.path.join(os.path.dirname(__file__), "viewer.html")
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self.send_response(404); self.send_header("Content-Type", "text/plain")
            self.end_headers(); self.wfile.write(b"viewer.html not found"); return
        cached = getattr(srv, "_viewer_cache", None)
        if cached is None or cached[0] != mtime:
            with open(p, "rb") as f:
                html = f.read()
            srv._viewer_cache = (mtime, html)
        else:
            html = cached[1]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _serve_asset(self, name: str, ctype: str):
        # serve a vendored file from the package dir (e.g. three.min.js) so the
        # viewer is self-contained and never depends on a CDN -- a CDN load
        # failure used to leave THREE undefined, halting the whole script at the
        # top-level 3D init BEFORE the chat wiring, so the chat showed nothing.
        import os as _os
        p = _os.path.join(_os.path.dirname(__file__), name)
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            self.send_response(404); self.send_header("Content-Type", "text/plain")
            self.end_headers(); self.wfile.write((name + " not found").encode()); return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _settings(self):
        """GET  /settings         -> {backend, story_model, has_token}  (token masked).
        POST /settings {backend?, token?, story_model?}
                            -> apply + persist (token in-memory only)
                            -> {ok, backend, story_model, has_token}.
        The token is never echoed, logged, or written to disk. An explicit empty
        ``token`` clears it; an absent ``token`` key leaves the existing one."""
        srv = self.server
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_err(400, "settings body is not JSON"); return
            _slog = []
            for _k in ("backend", "story_model", "beat_author"):
                if isinstance(body.get(_k), str) and body[_k].strip():
                    _slog.append(f"{_k}={body[_k].strip()}")
            if isinstance(body.get("token"), str) and body["token"].strip():
                _slog.append("token=<redacted>")
            if body.get("clear_token"):
                _slog.append("clear_token")
            if _slog:
                _vlog(srv, "settings " + " ".join(_slog))
            if isinstance(body.get("backend"), str) and body["backend"].strip():
                b = body["backend"].strip().rstrip("/")
                if not (b.startswith("http://") or b.startswith("https://")):
                    self._json_err(400, "backend must be an http(s) URL"); return
                srv.backend = b
            if isinstance(body.get("story_model"), str) and body["story_model"].strip():
                srv.warmup_model = body["story_model"].strip()
            # beat author: llm (genesis model) | cells (on-stage char's n-gram voice)
            if isinstance(body.get("beat_author"), str) and body["beat_author"].strip():
                ba = body["beat_author"].strip().lower()
                if ba in ("llm", "cells"):
                    srv.beat_author = ba
            # token: a non-empty string SETS it; clear_token=true CLEARS it; neither
            # (both absent) leaves the existing token untouched. Never echoed/logged.
            if isinstance(body.get("token"), str) and body["token"].strip():
                srv.ollama_token = body["token"].strip()
            if body.get("clear_token"):
                srv.ollama_token = None
            _sync_ollama_clients(srv)
            _save_settings(srv, srv.backend, getattr(srv, "warmup_model", None))
            out = json.dumps({"ok": True, "backend": srv.backend,
                              "story_model": getattr(srv, "warmup_model", None) or "",
                              "has_token": bool(getattr(srv, "ollama_token", None)),
                              "beat_author": getattr(srv, "beat_author", "llm")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        out = json.dumps({"backend": srv.backend,
                          "story_model": getattr(srv, "warmup_model", None) or "",
                          "has_token": bool(getattr(srv, "ollama_token", None)),
                          "beat_author": getattr(srv, "beat_author", "llm")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _state(self):
        import numpy as np
        from .variety import free_energy, cells_rank, entropy_count as _entropy_count
        mem = self.server.membrane
        exs = mem.store.exemplars
        n = len(exs)
        srv = self.server
        pts = []
        cast_view: list = []
        mu = None
        basis = getattr(srv, "_v_basis", None)
        if n:
            V = np.array([np.asarray(e.vec, dtype=float) for e in exs])
            mu = V.mean(0)
            if basis is None and n >= 4:          # fit once -> a stable cloud that grows, never spins
                try:
                    _, _, Vt = np.linalg.svd(V - mu, full_matrices=False)
                    basis = Vt[:3]; srv._v_basis = basis
                except Exception:
                    basis = None
            if basis is not None:
                coords = (V - mu) @ basis.T
            else:                                  # too few for PCA: spread on first 3 raw dims
                k = min(3, V.shape[1]); coords = np.zeros((n, 3))
                if k:
                    coords[:, :k] = (V - mu)[:, :k] * 3.0
            for i, e in enumerate(exs):
                pts.append({"p": [round(float(coords[i, 0]), 4),
                                  round(float(coords[i, 1]), 4),
                                  round(float(coords[i, 2]), 4)],
                            "safe": bool(e.safe),
                            "reask": int(getattr(e, "reask_count", 0)),
                            "contest": int(getattr(e, "contest_count", 0)),
                            "tags": list(getattr(e, "tags", []))})
        # ---- the cast: project each character's state_vec into the same 3D
        # basis so the viewer can mark each character's "where" in the cosmos.
        # Runs EVEN when the field is empty (n==0): a freshly booted world with
        # --protagonist/--character but no scenes yet still shows its cast --
        # placed on a small ring (no basis to project against); the marks snap
        # to real positions once scenes land and the PCA basis is fit.
        ccast = mem.cast
        if ccast.chars:
            try:
                CV = np.array([np.asarray(c.state_vec, dtype=float)
                               for c in ccast.chars])
                m = len(ccast.chars)
                if basis is not None and mu is not None:
                    ccoords = (CV - mu) @ basis.T
                elif mu is not None:                # small field, no PCA fit yet
                    kk = min(3, CV.shape[1]); ccoords = np.zeros((m, 3))
                    if kk:
                        ccoords[:, :kk] = (CV - mu)[:, :kk] * 3.0
                else:                               # empty field: ring spread by index
                    ang = np.linspace(0, 2*np.pi, m, endpoint=False) if m else []
                    ccoords = (np.stack([0.25*np.cos(ang), 0.25*np.sin(ang),
                                         np.zeros(m)], axis=1)
                               if m else np.zeros((0, 3)))
                for i, c in enumerate(ccast.chars):
                    cast_view.append({
                        "id": c.id, "name": c.name, "role": c.role,
                        "voice": c.voice,
                        "scene_count": int(c.scene_count),
                        "last_cycle": int(c.last_cycle),
                        "anchor_idx": c.anchor_idx, "pinned": bool(c.pinned),
                        "pov": ccast.pov_id == c.id,
                        "last_scene": getattr(c, "last_scene", "") or "",
                        "p": [round(float(ccoords[i, 0]), 4),
                              round(float(ccoords[i, 1]), 4),
                              round(float(ccoords[i, 2]), 4)]})
            except Exception:
                pass
        n_safe = sum(1 for e in exs if e.safe)
        theo = len(exs[0].vec) if exs else 1024
        ceiling = float(getattr(self.server, "entropy_ceiling", 0.01))
        temperature = float(getattr(self.server, "story_temperature", 0.7))
        story_mode = str(getattr(self.server, "story_mode", "story"))
        feed_rate = float(getattr(self.server, "feed_rate", 1.0))
        cpu_target = float(getattr(self.server, "cpu_target", 0.2))
        running = bool(getattr(self.server, "world_running", False))
        consol = getattr(self.server, "consolidation_stats", {})
        cache_depth = len(getattr(self.server, "story_cache", []))
        cl = cells_rank(mem.store)
        ec = _entropy_count(mem.store)
        tail, n_verd = self._audit_tail(24)
        # ---- the self-balancing observer organism: expose the live pool cells so the
        # viewer can render them (colored by covered world-direction, sized by
        # maturity). Each cell's 3D position is its vec-centroid projected through the
        # SAME world PCA basis (mu/basis) the exemplar cloud + cast use, so cells sit
        # among the world they observe. ``dir`` = covered world-direction index (the
        # palette index) or None when the cell is too immature to color. Reads of each
        # cell's store are GIL snapshots (one-cycle-stale at worst), like the rest.
        pool_cells: list = []
        _pool = getattr(srv, "cell_pool", None)
        if _pool is not None:
            from .variety import orient_dirs as _orient_dirs
            _cpu_cap = max(1, round(cpu_target * 30))
            _world_dirs = _orient_dirs(mem.store, _cpu_cap + 1, 0.5) if n else []
            for c in _pool.cells:
                cexs = c.membrane.store.exemplars
                cn = len(cexs)
                cp = [0.0, 0.0, 0.0]
                if cn and basis is not None and mu is not None:
                    try:
                        CV = np.array([np.asarray(e.vec, dtype=float) for e in cexs])
                        cc = (CV.mean(0) - mu) @ basis.T
                        cp = [round(float(cc[0]), 4), round(float(cc[1]), 4),
                              round(float(cc[2]), 4)]
                    except Exception:
                        pass
                try:
                    cfe = float(free_energy(c.membrane.store)) if cn >= 2 else 0.0
                except Exception:
                    cfe = 0.0
                # mature = the cell has GROWN a stable dominant direction (>=
                # _MATURE_MIN_EXEMPLARS), not merely been assigned one at bud. A fresh
                # bud has an orientation (so its gate is active + it has a color to
                # grow into) but is immature until it has grazed enough aligned
                # exemplars; the viewer shows immature buds neutral + small, then
                # colored + full-size once mature. ``dir`` (the palette index) is
                # gated on maturity so an immature bud renders neutral, not the color
                # of a cone it has not yet grown.
                cmature = c.dominant_dir() is not None
                cdir = c.covered_dir(_world_dirs) if (_world_dirs and cmature) else None
                pool_cells.append({
                    "id": c.tag, "tag": c.tag, "p": cp, "dir": cdir,
                    "mature": cmature,
                    "n": cn, "free_energy": round(cfe, 3),
                })
        out = json.dumps({"n": n, "n_safe": n_safe,
                          "free_energy": float(free_energy(mem.store)) if n >= 2 else 0.0,
                          "theoretical_max": theo,
                          "entropy_ceiling": round(ceiling, 4),
                          "entropy_cap": max(0, int(round(ceiling * theo))),
                          "temperature": round(temperature, 3),
                          "story_mode": story_mode,
                          "feed_rate": round(feed_rate, 3),
                          "cpu_target": round(cpu_target, 3),
                          "running": running,
                          "consolidation": consol,
                          "cache_depth": cache_depth,
                          "cells_level": round(float(cl), 2),
                          "entropy_count": ec,
                          "exemplars": pts,
                          "pool_cells": pool_cells,
                          "cast": cast_view,
                          "pov_id": mem.cast.pov_id,
                          "backend": str(getattr(self.server, "backend", "")),
                          "story_model": getattr(self.server, "warmup_model", None) or "",
                          "has_token": bool(getattr(self.server, "ollama_token", None)),
                          "cycle_speed": float(getattr(self.server, "cycle_speed", 1.0) or 1.0),
                          "initial_burst": int(getattr(self.server, "initial_burst", 0)),
                          "trained": not bool(getattr(self.server, "initial_burst", 0)),
                          "beat_author": str(getattr(self.server, "beat_author", "llm")),
                          "representation": str(getattr(self.server, "representation", "llm")),
                          "progression_every": int(getattr(self.server, "progression_every", 0)),
                          "verdicts": tail, "n_verdicts": n_verd}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _neighbors(self):
        """GET /neighbors?q=<text>  or  /neighbors?idx=<n>  -> the top-k nearest
        exemplars (idx, sim, safe, words, action), with the one the self-author
        SPEAK path would actually RECITE flagged (``recalled``: the first action
        with >=15 words, matching ``runtime.Membrane.generate``'s cohesion filter).
        This is the viewer's exploration instrument: it shows the *territory* around
        a query (or around a chosen memory), not just the single recalled scene -- so
        you can walk the field's association graph by clicking a neighbour and asking
        for ITS neighbours. Read-only on the store; embeds only when given text."""
        import numpy as np
        from urllib.parse import urlparse, parse_qs
        mem = self.server.membrane
        store = mem.store
        qs = parse_qs(urlparse(self.path).query)
        COH = 15
        K = 8
        query = qs.get("q", [""])[0]
        rows: list = []
        try:
            if "idx" in qs:
                i = int(qs["idx"][0])
                if i < 0 or i >= len(store):
                    raise ValueError("idx out of range")
                qn = np.asarray(store.exemplars[i].vec, dtype=float)
            else:
                if not query.strip():
                    self._json_out({"query": "", "anchor": None, "neighbors": []})
                    return
                vec = mem._nudge(mem.embedder.embed([query])[0])
                qn = np.asarray(vec, dtype=float)
            qn = qn / (np.linalg.norm(qn) + 1e-12)
            recalled = False
            for ex, sim in store.top_k(qn, k=K):
                a = ex.action or ""
                words = len(a.split())
                is_rec = (not recalled) and words >= COH
                if is_rec:
                    recalled = True
                rows.append({"idx": store.index_of(ex), "sim": round(float(sim), 3),
                             "safe": bool(ex.safe), "words": words,
                             "recalled": is_rec, "action": a})
            anchor = {"query": query} if "idx" not in qs else {"idx": int(qs["idx"][0])}
            self._json_out({"query": query, "anchor": anchor, "neighbors": rows})
        except Exception as e:
            self._json_out({"query": query, "anchor": None,
                            "neighbors": [], "error": str(e)})

    def _json_out(self, obj: dict) -> None:
        out = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _audit_tail(self, k):
        """Last k audit records as a flat {verdict, sim, q} list for the ticker,
        plus the TOTAL count of ticker-worthy records (monotonic, uncapped).
        The tail is capped at k, so its length stops growing once full; the
        viewer compares the monotonic total (not the capped length) to know new
        verdicts have arrived. Maps all three record shapes (decision / outcome
        / contest). Returns (tail_list, total_count)."""
        p = getattr(self.server.membrane, "audit_path", None)
        if not p:
            return [], 0
        try:
            with open(p, "rb") as f:
                lines = f.readlines()
        except OSError:
            return [], 0
        out = []
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

    def _anthropic_auth(self) -> dict:
        """Passthrough auth headers for the Anthropic backend, read per-request
        from the incoming headers. Never stored on the handler/instance (a
        ThreadingHTTPServer would race it), never written to the field/audit, and
        never printed -- the caller logs only the verdict/sim, not the key."""
        auth = {}
        for h in AUTH_HEADERS:
            v = self.headers.get(h)
            if v:
                auth[h] = v
        return auth

    # ---- the feeding boundary (stage 2: multi-cell) -------------------------
    def _maybe_deposit(self, body: dict) -> int | None:
        """Deposit this turn's user text into the shared buffer for the background cell
        pool. Only a REAL human turn enters: ``_last_user`` returns None for
        tool_result-only / empty turns, so the buffer never sees self-generated or
        non-conversational traffic (the safe=given-not-taken guardrail -- situations
        enter only through the boundary). Called only when ``self.server.buffer is not
        None`` (i.e. ``--cells`` on); when off the caller's guard short-circuits before
        here, so the live single-Membrane path pays only one ``is not None`` check.

        Stage 3: deposits with ``meta={"pending": True}`` and returns the situation's
        index -- a handle the caller stores on ``self._deposit_id`` and later passes to
        ``_maybe_attach`` once the forward returns, so the real response is teed into the
        same situation (``attach_response`` -> ``complete=True``). The cell pulls complete
        pairs and learns the real action; the boundary stays one-way."""
        q = _last_user(body)
        if q:
            return self.server.buffer.deposit(q, meta={"pending": True})
        return None

    def _maybe_attach(self, content: str) -> None:
        """Tee the real assistant response into the deposited situation (stage 3). Called
        after the response is produced -- never before -- so the gate/decide/replay path is
        untouched (this is a post-response side-effect). No-op when ``--cells`` is off or
        when this request had nothing to deposit (empty/tool_result turn -> ``_deposit_id``
        is None) or when there is no response text. The cell learns whatever the user
        received (real forward OR a replayed cached action); the response is model output
        only, never auth/keys (``_response_text`` reads only ``content``)."""
        if self._deposit_id is not None and self.server.buffer is not None and content:
            self.server.buffer.attach_response(self._deposit_id, content)

    # ---- the gate path ----------------------------------------------------
    def _chat(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._forward_raw(raw)
            return
        # the feeding boundary (stage 2): deposit the real user turn into the shared
        # buffer for the background cell pool. No-op when --cells is off (srv.buffer is
        # None); only _last_user-non-empty turns enter. One deposit per request,
        # before the stream/non-stream split, so both paths feed the buffer exactly once.
        # Stage 3: the returned id is the handle for the later attach (the response tee);
        # None means nothing was deposited (empty/tool_result turn -> nothing to attach).
        self._apply_system(body)   # the --system premise (constant ground; client's own wins)
        self._deposit_id = None
        if self.server.buffer is not None:
            self._deposit_id = self._maybe_deposit(body)
        # Ollama defaults stream=true; gate the stream path too now (absorb synthesizes
        # NDJSON from the recalled action, escalate tees the backend stream + learns).
        if body.get("stream", True):
            self._chat_stream(raw, body)
            return
        try:
            resp, decision = self.server.membrane.handle(body)
        except Exception as e:                       # never break the chat on a gate error
            print(f"[membrane] gate error, forwarding raw: {e}", file=sys.stderr)
            self._forward_raw(raw)
            return
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        self._maybe_attach(_response_text(resp))   # stage 3: tee the real response to the cell buffer
        sim = decision.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        print(f"[membrane] {decision['verdict']:18s} sim={sim_s}  field={len(self.server.membrane.store)}",
              file=sys.stderr)

    # ---- the gate path (STREAMING /api/chat) --------------------------------
    def _chat_stream(self, raw: bytes, body: dict) -> None:
        """Gate a STREAMING /api/chat turn.

        Absorb (act) -> synthesize NDJSON from the recalled action (NO model call,
        the field's replay delivered as a stream). Escalate -> tee the backend
        stream to the client while accumulating content, then learn the exemplar
        from the accumulated response. If the gate throws BEFORE any bytes are sent
        we fall back to a transparent forward (never break the chat); once headers
        are out, a mid-stream error just closes the connection (the client already
        has partial bytes).
        """
        mem = self.server.membrane
        try:
            verdict, vec, query, _ = mem.gate_turn(body)
        except Exception as e:                       # gate failed pre-headers -> safe to bail out
            print(f"[membrane] gate error, forwarding stream raw: {e}", file=sys.stderr)
            self._forward_raw(raw)
            return
        # committed to a streamed response now
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")      # streamed + close (no chunked framing needed)
        self.close_connection = True
        self.end_headers()
        try:
            if vec is None:                           # empty query: forward the stream, learn nothing
                self._stream_forward(body)
                return
            if verdict["verdict"] == "act" and not mem.observer:
                self._stream_replay(verdict, body, query, vec, mem)
                return
            # observer (any verdict) OR escalate: tee the backend stream + learn.
            # In observer mode a gate ``act`` forwards instead of replaying, and
            # ``record_escalation`` audits ``would_absorb`` for it.
            self._stream_escalate(verdict, body, query, vec, mem)
        except Exception as e:                        # mid-stream: client already has partial bytes
            print(f"[membrane] stream delivery error: {e}", file=sys.stderr)

    def _stream_replay(self, verdict, body, query, vec, mem) -> None:
        """Absorb, delivered as a synthesized NDJSON stream -- Fork C: the boundary
        GENERATES its response from the field-grown n-gram (conditioned on the
        nearest memory), not a verbatim replay of the stored action."""
        model = body.get("model", "membrane")
        content = mem.generate(query, verdict, vec=vec)
        for chunk in _ndjson_chunks(model, content):
            self.wfile.write((json.dumps(chunk) + "\n").encode())
        self.wfile.flush()
        mem.record_act(query, vec, verdict, model, content)
        self._maybe_attach(content)   # stage 3: tee what the boundary actually said to the cell buffer
        sim = verdict.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        gen = content != verdict.get("action", "")
        print(f"[membrane] act(stream)        sim={sim_s}  field={len(mem.store)}"
              f"  {'generated' if gen else 'replay'}", file=sys.stderr)

    def _stream_escalate(self, verdict, body, query, vec, mem) -> None:
        """Escalate: stream the backend response to the client verbatim while teeing
        the content into the field, then learn. The recalled action is injected as
        the same hint system message the non-stream responder uses.

        In ``--self-author`` (stripped) mode there IS no backend call: the cell
        speaks for itself -- generate from the field and stream THAT (the LLM is
        out of the live loop). Nothing is learned; the nearest memory's index is
        remembered so a re-ask can demote the cell that spoke."""
        if mem.self_author:
            model = body.get("model", "membrane")
            content = mem.generate(query, verdict, vec=vec)
            for chunk in _ndjson_chunks(model, content):
                self.wfile.write((json.dumps(chunk) + "\n").encode())
            self.wfile.flush()
            near_idx = verdict.get("ex_idx")
            if near_idx is None and vec is not None:
                import numpy as np
                qn = vec / (np.linalg.norm(vec) + 1e-12)
                nbrs = mem.store.top_k(qn, k=1)
                if nbrs:
                    near_idx = mem.store.index_of(nbrs[0][0])
            mem._remember(query, vec, verdict, near_idx)
            mem._audit(query, verdict, {"message": {"content": content}},
                       ex_idx=near_idx, learned=False)
            sim = verdict.get("sim")
            sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
            print(f"[membrane] {verdict['verdict']:14s}(self-author) sim={sim_s}  "
                  f"field={len(mem.store)}", file=sys.stderr)
            return
        up = dict(body)
        up["stream"] = True
        h = hint_system_message(verdict.get("hint") if verdict["verdict"] == "escalate-with-hint" else None)
        if h:
            up["messages"] = [h] + list(up.get("messages", []))
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/api/chat", data=data,
                                     method="POST", headers=_ollama_headers(self.server))
        parts: list[str] = []
        done_obj: dict = {}
        with urllib.request.urlopen(req, timeout=300) as r:
            while True:                               # NDJSON: one JSON object per line
                line = r.readline()
                if not line:
                    break
                self.wfile.write(line)                # forward verbatim (line keeps its newline)
                self.wfile.flush()
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    done_obj = obj
                c = (obj.get("message") or {}).get("content")
                if c:
                    parts.append(c)
        self.wfile.flush()
        content = "".join(parts)
        model = done_obj.get("model") or body.get("model") or "membrane"
        mem.record_escalation(query, vec, verdict, content, model, False)
        self._maybe_attach(content)                # stage 3: tee the real response to the cell buffer
        sim = verdict.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        tag = f"{verdict['verdict']}(stream)"
        print(f"[membrane] {tag:18s} sim={sim_s}  field={len(mem.store)}", file=sys.stderr)

    def _stream_forward(self, body: dict) -> None:
        """Forward a stream with no gating (only used for the empty-query path once
        the 200/ndjson headers are already sent)."""
        up = dict(body)
        up["stream"] = True
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/api/chat", data=data,
                                     method="POST", headers=_ollama_headers(self.server))
        with urllib.request.urlopen(req, timeout=300) as r:
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
        self.wfile.flush()

    # ---- the Anthropic /v1/messages gate path (Claude Code, observer-only) ----
    def _messages(self):
        """Gate an Anthropic ``/v1/messages`` turn. Same shape as ``_chat``: run
        ``gate_turn`` (which also fires the observer ambient signals -- tool-error,
        re-ask-after-escalate), then deliver. In observer mode (the default for
        ``--anthropic``) every verdict FORWARDS to Anthropic and learns -- never
        replays a coding answer. The API key is passthrough only."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._forward_raw(raw)
            return
        # the feeding boundary (stage 2): one deposit per request, before the
        # stream/non-stream split. No-op when --cells is off; only real user turns enter.
        # Stage 3: the returned id is the handle for the later attach (the response tee).
        self._apply_system(body)   # the --system premise (constant ground; client's own wins)
        self._deposit_id = None
        if self.server.buffer is not None:
            self._deposit_id = self._maybe_deposit(body)
        auth = self._anthropic_auth()
        # ---- Cortex mode: sense the agent's own decisions (tool_use) instead of the
        # world gate. The /v1/messages stream is forwarded verbatim (so Claude Code's
        # tool calls keep working) while each tool_use is embedded, nudges the decision
        # field, and is logged + exposed at /cortex/state. Nothing is gated (Phase 1 is a
        # passive monitor). The world gate (gate_turn / record_escalation) and the cell
        # buffer deposit/attach are bypassed entirely on this path -- the Cortex is a
        # parallel observer of the chat stream, the same pattern as --cells is a parallel
        # observer of the world. Short-circuits like the --cells guard above.
        if getattr(self.server, "cortex_mode", False) and self.server.cortex is not None:
            self._cortex_pair_results(body)         # stamp prior tool_results (the outcomes)
            if body.get("stream", False):
                self._cortex_stream(raw, body, auth)
            else:
                self._cortex_nonstream(raw, body, auth)
            self._cortex_maybe_save()
            return
        if body.get("stream", False):
            self._messages_stream(raw, body, auth)
            return
        try:
            resp, decision = self.server.membrane.handle(body, auth=auth)
        except Exception as e:                       # never break the chat on a gate error
            print(f"[membrane] /v1/messages gate error, forwarding raw: {e}",
                  file=sys.stderr)
            self._forward_raw(raw)
            return
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        self._maybe_attach(_response_text(resp))   # stage 3: tee the real response to the cell buffer
        self._log_messages(decision)

    def _messages_stream(self, raw: bytes, body: dict, auth: dict) -> None:
        """Gate a STREAMING ``/v1/messages`` turn (Claude Code's default).

        Observer (default): ALWAYS forward Anthropic's SSE to the client verbatim
        while teeing ``text_delta`` text into the field; on ``message_stop`` learn
        the exemplar (auditing ``would_absorb`` when the gate had said ``act``).
        Never replays. ``--absorb`` (non-observer): on a gate ``act``, synthesize
        Anthropic SSE from the recalled action (no model call); on escalate (with
        or without hint), tee the upstream SSE + learn -- mirrors the Ollama
        ``_stream_escalate``. If the gate throws before headers, fall back to a
        transparent forward."""
        mem = self.server.membrane
        try:
            verdict, vec, query, _ = mem.gate_turn(body)
        except Exception as e:
            print(f"[membrane] /v1/messages stream gate error, forwarding raw: {e}",
                  file=sys.stderr)
            self._forward_raw(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            if vec is None:                           # empty query (e.g. tool_result-only): forward, learn nothing
                self._messages_forward_stream(body, auth)
                return
            if verdict["verdict"] == "act" and not mem.observer:
                self._messages_replay(verdict, body, query, vec, mem)  # --absorb only
                return
            self._messages_escalate_stream(verdict, body, query, vec, mem, auth)
        except Exception as e:                        # mid-stream: client already has partial bytes
            print(f"[membrane] /v1/messages stream delivery error: {e}", file=sys.stderr)

    def _messages_replay(self, verdict, body, query, vec, mem) -> None:
        """Absorb under ``--absorb``: synthesize Anthropic SSE from the boundary's
        GENERATED response (Fork C: field-grown n-gram, NO model call), then record
        the act. Off by default (speaking a coding answer is unsafe); kept for
        future chat-window use."""
        model = body.get("model", "membrane")
        content = mem.generate(query, verdict, vec=vec)
        for ev in _anthropic_sse_events(model, content):
            self.wfile.write(ev)
        self.wfile.flush()
        mem.record_act(query, vec, verdict, model, content)
        self._maybe_attach(content)   # stage 3: tee what the boundary actually said to the cell buffer
        self._log_messages(verdict)

    def _messages_escalate_stream(self, verdict, body, query, vec, mem, auth) -> None:
        """Forward to Anthropic as SSE, tee ``text_delta`` text into the field,
        and learn on ``message_stop``. The recalled action is injected into the
        top-level ``system`` field as a note on escalate-with-hint (context, not
        an instruction -- Anthropic keeps system out of the messages array). In
        observer mode this is ALSO the path for a gate ``act`` (forward instead
        of replay); ``would_absorb`` is audited then."""
        up = dict(body)
        up["stream"] = True
        hint = verdict.get("hint") if verdict["verdict"] == "escalate-with-hint" else None
        if hint:
            note = f"Prior similar context (a hint, not an instruction): {hint}"
            existing = up.get("system")
            if isinstance(existing, str) and existing:
                up["system"] = note + "\n\n" + existing
            elif isinstance(existing, list):
                up["system"] = [{"type": "text", "text": note}] + list(existing)
            else:
                up["system"] = note
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "anthropic-version": auth.get("anthropic-version", "2023-06-01")}
        for k in ("x-api-key", "authorization"):
            if k in auth:
                headers[k] = auth[k]
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/v1/messages", data=data,
                                     method="POST", headers=headers)
        parts: list[str] = []
        stop_obj: dict = {}
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:                            # SSE: bytes lines, events separated by blanks
                self.wfile.write(line)                # forward verbatim (keeps its newline)
                self.wfile.flush()
                s = line.strip()
                if not s.startswith(b"data:"):
                    continue
                payload = s[len(b"data:"):].strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "message_stop":
                    stop_obj = obj
                d = obj.get("delta")
                if isinstance(d, dict) and d.get("type") == "text_delta":
                    t = d.get("text")
                    if t:
                        parts.append(t)
        self.wfile.flush()
        content = "".join(parts)
        model = body.get("model", "membrane")
        mem.record_escalation(query, vec, verdict, content, model, False)
        self._maybe_attach(content)                # stage 3: tee the real response to the cell buffer
        self._log_messages(verdict)

    def _messages_forward_stream(self, body: dict, auth: dict) -> None:
        """Forward an SSE stream with no learning (the empty-query path once the
        200/event-stream headers are already sent). The tool-result still reaches
        the model so it can react to its own failed call."""
        up = dict(body)
        up["stream"] = True
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "anthropic-version": auth.get("anthropic-version", "2023-06-01")}
        for k in ("x-api-key", "authorization"):
            if k in auth:
                headers[k] = auth[k]
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/v1/messages", data=data,
                                     method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=300) as r:
            for line in r:
                self.wfile.write(line)
        self.wfile.flush()

    # ---- Cortex mode: the passive decision-variety monitor (Phase 1) ----------
    # These run ONLY when --cortex is on (the _messages branch above). They forward
    # the /v1/messages stream verbatim to the backend (Claude Code's tool calls work
    # unchanged) while sensing each tool_use as an agent DECISION -- embed it, nudge
    # the decision field (cortex.DecisionField), log + expose via /cortex/state. The
    # world gate is not called; nothing is gated; the human stays in the loop.

    def _cortex_pair_results(self, body: dict) -> None:
        """Stamp the tool_results from the latest user message onto their pending
        decisions (the outcomes arrive on the NEXT request, as a user message, before
        the model continues). The decision's embedding is the tool_use, not the result,
        so stamping only annotates the stored decision with its outcome + is_error
        (a hook for Phase 2's reject-set)."""
        cf = self.server.cortex
        for tr in user_tool_results(body):
            tid = tr.get("tool_use_id", "")
            if not tid:
                continue
            content = tr.get("content")
            rtext = _content_text(content) or ""
            is_err = bool(tr.get("is_error", False))
            cf.store.stamp_result(tid, rtext, is_err)

    def _cortex_stream(self, raw: bytes, body: dict, auth: dict) -> None:
        """Forward the /v1/messages SSE verbatim to the client while sensing each
        tool_use block as a decision. Mirrors ``_messages_escalate_stream``'s forward
        loop but the tee is tool_use/input_json_delta (via ToolUseCollector) instead of
        text_delta. No learning call to the world gate -- the decision field is the
        only thing mutated, and only by a nudge (the EMA centroid)."""
        cf = self.server.cortex
        up = dict(body)
        up["stream"] = True
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "anthropic-version": auth.get("anthropic-version", "2023-06-01")}
        for k in ("x-api-key", "authorization"):
            if k in auth:
                headers[k] = auth[k]
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/v1/messages", data=data,
                                     method="POST", headers=headers)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        col = ToolUseCollector()
        turn = len(cf.store)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                for line in r:                       # SSE: bytes lines, verbatim to client
                    self.wfile.write(line)
                    self.wfile.flush()
                    s = line.strip()
                    if not s.startswith(b"data:"):
                        continue
                    payload = s[len(b"data:"):].strip()
                    if not payload:
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    done = col.feed(obj)              # completes a tool_use on content_block_stop
                    if done is not None:
                        tool_use_id, name, inp = done
                        ev = AgentEvent(session="", turn=turn, tool_name=name,
                                        input_text=inp, scope=ACTION)
                        ev.tool_use_id = tool_use_id
                        idx = cf.observe(ev)         # embed + nudge + append (None on embed fail)
                        if idx is not None:
                            _clog(self.server,
                                  f"{name:12s} sim={cf.last['similarity']:.2f} "
                                  f"nov={cf.last['novelty']:.3f} "
                                  f"{'SURPRISE ' if cf.last['surprise'] else ''}"
                                  f"n={len(cf.store)}")
        except Exception as e:                       # mid-stream: client already has partial bytes
            print(f"[cortex] stream delivery error: {e}", file=sys.stderr)

    def _cortex_nonstream(self, raw: bytes, body: dict, auth: dict) -> None:
        """The non-streaming /v1/messages path under --cortex (Claude Code streams by
        default, but handle the buffered path too). Forward to the backend, then parse
        the returned ``content`` blocks for ``tool_use`` entries and sense each as a
        decision. The full response is returned to the client unchanged."""
        cf = self.server.cortex
        up = dict(body)
        up["stream"] = False
        headers = {"Content-Type": "application/json",
                   "anthropic-version": auth.get("anthropic-version", "2023-06-01")}
        for k in ("x-api-key", "authorization"):
            if k in auth:
                headers[k] = auth[k]
        data = json.dumps(up).encode()
        req = urllib.request.Request(self.server.backend + "/v1/messages", data=data,
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read())
        except Exception as e:
            print(f"[cortex] nonstream forward error: {e}", file=sys.stderr)
            self._forward_raw(raw)
            return
        turn = len(cf.store)
        for block in resp.get("content", []) or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            ev = AgentEvent(session="", turn=turn, tool_name=block.get("name", "?"),
                            input_text=json.dumps(block.get("input", {})),
                            scope=ACTION)
            ev.tool_use_id = block.get("id", "")
            idx = cf.observe(ev)
            if idx is not None:
                _clog(self.server,
                      f"{ev.tool_name:12s} sim={cf.last['similarity']:.2f} "
                      f"nov={cf.last['novelty']:.3f} n={len(cf.store)}")
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _cortex_maybe_save(self) -> None:
        """Persist the decision field if it changed this request (the EMA centroid +
        the decision log). Cheap relative to a turn; local file; bounded by session
        length. Saves AFTER the stream so the response is not delayed by the write."""
        cf = self.server.cortex
        if not getattr(cf.store, "_dirty", False):
            return
        path = getattr(self.server, "cortex_field_path", "")
        cf.store.save(path)

    def _cortex_state(self) -> None:
        """GET /cortex/state -- the passive monitor's instruments. The macro collapse
        signal (eff_rank / free_energy / orient_dirs) over the decision field, the last
        decision's novelty/similarity/surprise, recent decisions (tool + truncated
        input), and the reject-set size (0 in Phase 1). NOT gated on --viewer: the
        Cortex operator may run it without the 3D viewer. JSON, never auth-bearing."""
        cf = self.server.cortex
        if cf is None:
            out = json.dumps({"cortex": False}).encode()
        else:
            health = cf.health()
            recent = [
                {"tool": d.tool, "input": d.input_text[:160],
                 "is_error": d.is_error, "safe": d.safe, "turn": d.turn}
                for d in cf.store.decisions[-12:]]
            out = json.dumps({
                "cortex": True,
                "alpha": cf.store.alpha,
                **health,
                "last": cf.last,
                "reject_set_size": 0,        # Phase 2
                "recent": recent,
            }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _log_messages(self, decision: dict) -> None:
        """Log a /v1/messages decision. NEVER logs the auth/key -- only verdict + sim."""
        sim = decision.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "  -  "
        tag = decision["verdict"]
        if self.server.membrane.observer and tag == "act":
            tag = "act→fwd"          # observer never replays; show the forward
        print(f"[membrane] {tag:18s} sim={sim_s}  field={len(self.server.membrane.store)}",
              file=sys.stderr)

    # ---- transparent forward (everything else + gate-error fallback) ----
    def _forward(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else None
        self._send_to_backend(raw)

    def _forward_raw(self, raw: bytes) -> None:
        self._send_to_backend(raw)

    def _send_to_backend(self, raw: bytes | None) -> None:
        backend = self.server.backend
        url = backend + self.path
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        # the optional /settings token: inject it for proxied Ollama calls (e.g.
        # the viewer's /api/tags) when the client didn't already send its own auth.
        token = getattr(self.server, "ollama_token", None)
        if token and not any(k.lower() == "authorization" for k in headers):
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=raw, method=self.command, headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            r = e
        self.send_response(r.status)
        for k, v in r.headers.items():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        self.end_headers()
        while True:
            chunk = r.read(4096)
            if not chunk:
                break
            self.wfile.write(chunk)

    def log_message(self, *a):                          # silence the default access log
        pass


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Membrane proxy: a field-first front door in front of Ollama or Anthropic.")
    ap.add_argument("--listen", type=int, default=11435, help="port to listen on (127.0.0.1 only)")
    ap.add_argument("--backend", default=None,
                    help="upstream URL (default: http://localhost:11434 for Ollama, "
                         "https://api.anthropic.com with --anthropic)")
    ap.add_argument("--field", default="membrane-field.json", help="path to the exemplar field store")
    ap.add_argument("--audit", default="membrane-audit.jsonl", help="path to the append-only audit log")
    ap.add_argument("--worlds-dir", default="worlds", help="directory for saved worlds (the viewer's "
                     "save/load buttons copy field+cast+audit here under one slot name)")
    ap.add_argument("--sim-min", type=float, default=0.85,
                    help="cosine cutoff for absorb (conservative for the bootstrap; loosen once audit proves safe)")
    ap.add_argument("--consensus-min", type=float, default=0.60)
    ap.add_argument("--dedup-sim", type=float, default=0.95,
                    help="learn-time guard: don't append a near-identical situation above this cosine "
                         "(stops the field filling with same-situation/different-action copies of vague "
                         "prompts; set >1.0 to disable). Default 0.95.")
    ap.add_argument("--no-neg-followup", dest="neg_followup", action="store_false",
                    help="disable negative-follow-up demotion. By default a short complaint right "
                         "after an absorb ('not working', 'wrong', 'no') demotes the replayed exemplar "
                         "so you needn't repeat the query to signal 'bad'. Can false-demote (e.g. 'no' "
                         "answering a yes/no question -- guarded, not eliminated); pass this to revert "
                         "to verbatim-re-ask-only.")
    ap.add_argument("--demote-threshold", type=int, default=1,
                    help="re-asks needed to demote an exemplar (raise to require more than one re-ask)")
    # ---- live model-judge (default ON; opt out with --no-judge) ----
    # Fires ONLY on an actual demotion (a safe flip) -- the rare moment where adjudication
    # matters, never every turn. The internal self-check (the apoptosis half of the
    # two-authority boundary): a neg-followup false positive that wrongly demotes a good
    # answer gets CONTESTed and restored with no user labor. --no-judge reverts to StubJudge.
    ap.add_argument("--judge", dest="judge", action="store_true", default=True,
                    help="(default on; kept for compatibility -- judge is on unless --no-judge)")
    ap.add_argument("--no-judge", dest="judge", action="store_false",
                    help="disable the live model-judge. By default every demotion (a safe flip) "
                         "triggers a fresh model to adjudicate -- CONTEST restores a correct answer "
                         "that was wrongly demoted (e.g. a neg-followup false positive), ACCEPT lets "
                         "the demotion stand. Fires only on a demotion, never per turn.")
    ap.add_argument("--judge-model", default="granite3.3:8b",
                    help="model that adjudicates demotions (independent of the responder; override with JUDGE_MODEL)")
    ap.add_argument("--judge-timeout", type=int, default=120, help="judge call timeout (seconds)")
    ap.add_argument("--judge-backend", default=None,
                    help="Ollama URL for the judge (defaults to the Ollama backend; the judge stays "
                         "Ollama even in --anthropic mode)")
    # ---- Anthropic /v1/messages adapter + observer-only mode ----
    ap.add_argument("--anthropic", action="store_true",
                    help="gate Anthropic /v1/messages (Claude Code) instead of Ollama /api/chat. "
                         "Backend defaults to https://api.anthropic.com; --observer is implied "
                         "(never replays a coding answer). ANTHROPIC_API_KEY is passed through from "
                         "the client's x-api-key header and NEVER stored/logged.")
    ap.add_argument("--observer", action="store_true",
                    help="observer-only: forward every turn + learn + quality-label, NEVER replay "
                         "(implied by --anthropic). The field's value is its variety, not token savings.")
    ap.add_argument("--absorb", action="store_true",
                    help="re-enable replay (absorb) in --anthropic mode for future chat-window use. "
                         "OFF by default -- replaying a coding answer is unsafe (the right action "
                         "depends on the full tool-using transcript, not a cosine match).")
    ap.add_argument("--self-author", action="store_true",
                    help="STRIPPED mode: the LLM is out of the live loop. Every turn (act AND "
                         "escalate) is the cell speaking for itself -- generated from the field-grown "
                         "n-gram, no model call, nothing learned (no teacher). The field is coupled to "
                         "the USER, not the model: your re-ask demotes the cell that spoke, so the "
                         "cells respond to you. Pair with --no-judge (no model judge). Warm-up is the "
                         "one place the LLM may still be used (to seed).")
    ap.add_argument("--speak", choices=["recall", "ngram"], default="recall",
                    help="How a --self-author cell speaks (the SPEAK organ). 'recall' (default): "
                         "RECITE the nearest lived chronicle scene -- cohesive, on-topic, pure field "
                         "memory (no LLM). 'ngram': the word-trigram fragment (a fresh stochastic "
                         "draw from the field's own stats) -- more generative but incoherent past a "
                         "few tokens (the 'gibberish'). Both keep the re-ask->demote self-correction.")
    ap.add_argument("--protagonist", default=None,
                    help="the character the reader IS: 'Name — role — voice' (em-dash "
                         "separated). Admitted to the cast at boot if absent and set as the "
                         "POV protagonist -- the narrator speaks from their region of the "
                         "field, in their voice, and your chat messages render as their "
                         "thoughts. Optional; off -> no POV (chat unchanged).")
    ap.add_argument("--character", action="append", default=None, metavar='"Name — role — voice"',
                    help="admit a character to the cast at boot (repeatable): 'Name — "
                         "role — voice' (em-dash separated), same shape as --protagonist "
                         "but WITHOUT setting them POV. Use to seed the ensemble around "
                         "the protagonist (e.g. the sidekick). Retro-tags the existing "
                         "field so they color in at once. Optional; repeat for more than one.")
    ap.add_argument("--vague-min-tokens", type=int, default=4,
                    help="learn-time vague filter: skip situations shorter than this many words "
                         "(with no ?/path/digit/identifier/stem) -- stops 'yes'/'ok'/'beep'-class "
                         "junk filling the field. Default 4. Conservative (false-negative = learn).")
    ap.add_argument("--no-vague-filter", dest="vague_filter", action="store_false",
                    help="disable the vague filter (learn every situation, however thin).")
    ap.add_argument("--variety-gate", action="store_true",
                    help="only append an exemplar if it grows the field's effective rank (adds "
                         "variety, not just count). 2x SVD per learn on a big field, so OFF by "
                         "default; the cheap vague+dedup cover most cases. The principled "
                         "entropy-based learn gate.")
    # ---- the field's thermodynamic state: variety recorded per decision, opt-in
    # adaptive temperature (the "only variety can absorb variety" steering) ----
    ap.add_argument("--variety-temp", action="store_true",
                    help="ADAPTIVE TEMPERATURE: ease sim_min toward --sim-min-floor as the "
                         "field's free_energy (safe variety) grows. A low-variety field stays "
                         "conservative; a grown-SAFE field can afford to absorb more. OFF by "
                         "default (static threshold). 'only SAFE variety absorbs variety' -- a "
                         "junk field (variety but all unsafe) does NOT get eager, because the "
                         "disorder tax keeps free_energy low.")
    ap.add_argument("--temp-driver", default="free_energy",
                    choices=["free_energy", "spectrum_entropy"],
                    help="what drives adaptive eagerness: 'free_energy' (default, the safe-"
                         "weighted VALUE number -- safe variety earns eagerness, junk does not) "
                         "or 'spectrum_entropy' (raw variety, ignores safety -- the reckless one).")
    ap.add_argument("--temp-tau", type=float, default=1.0,
                    help="saturating constant in eagerness = val/(val+temp_tau). Smaller = relax "
                         "sooner (need less variety); larger = need more variety before easing.")
    ap.add_argument("--sim-min-floor", type=float, default=0.70,
                    help="the eagerness lower bound -- never get more eager than this. 0.70 is "
                         "the validated safe cosine cutoff (real_knee experiment).")
    # ---- the veil: nudge everything that transits this instance ----
    ap.add_argument("--nudge-sigma", type=float, default=0.0,
                    help="the veil: per-veil Gaussian perturbation of the incoming situation "
                         "embedding (added then renormalized), applied to EVERY message through "
                         "this instance -- both the gate query and the stored exemplar vec. The "
                         "forwarded prompt text is NEVER garbled; only the field's representation "
                         "is veiled. 0.0 = identity veil (off, zero behaviour change). >0 lets a "
                         "low-variety workload build a varied field, and lets independent veils "
                         "(different --nudge-seed) realize divergent variety trajectories from "
                         "the same traffic -- without any central input or orchestrator.")
    ap.add_argument("--nudge-seed", type=int, default=0,
                    help="seed for the veil's nudge RNG. Each veil seeds its own so independent "
                         "instances diverge; the same seed reproduces the same nudge (reproducible).")
    ap.add_argument("--tag", default=None,
                    help="instance label for a multi-veil run. When set, the field/audit paths "
                         "default to membrane-field-<tag>.json / membrane-audit-<tag>.jsonl so "
                         "independent veils own independent fields. Overridden by explicit "
                         "--field/--audit. Also shown in the banner.")
    ap.add_argument("--cells", type=int, default=0,
                    help="seed N curiosity-driven cells of a SELF-BALANCING observer organism that "
                         "parallels the single Membrane. The cells GRAZE the world's field (not a user "
                         "buffer) and the consolidator buds/retires them to the world's variety, capped "
                         "by --cpu-target (population ~ round(cpu_target*30)). Each cell differentiates "
                         "(commits to a cone) as it matures and is colored by its covered direction in "
                         "the viewer. 0 = off (default): the single-Membrane path is unchanged except one "
                         "cheap guard per cycle. N is the initial seed; the lifecycle adjusts from there.")
    # ---- Cortex mode: a variety-governed field that watches the agent's OWN decisions
    # (tool_use blocks). Repurposes the /v1/messages stream path: each tool call is
    # embedded, nudges a temporal "decision field" (rosetta-check's EMA centroid,
    # ported), and the field's variety is measured (membrane's variety.py -- the macro
    # collapse signal rosetta-check lacks). Phase 1 is a PASSIVE MONITOR: it surfaces
    # collapse to the operator ([cortex] log + /cortex/state), it does NOT gate the
    # agent. The world gate (gate_turn) is bypassed on this path. The safe Cortex
    # keeps the human in the loop -- an autonomous gate is the single-controller
    # pattern the "It'll Pave Us" essay warns against. Point Claude Code at this proxy
    # (ANTHROPIC_BASE_URL=http://127.0.0.1:<port>) with an Ollama /v1/messages backend.
    ap.add_argument("--cortex", action="store_true",
                    help="Cortex mode: sense the agent's own tool_use decisions on the "
                         "/v1/messages path (instead of the world gate) and measure their "
                         "variety. A passive monitor -- surfaces collapse via [cortex] log + "
                         "/cortex/state, never gates. Off by default; without it the "
                         "/v1/messages path is byte-for-byte unchanged.")
    ap.add_argument("--cortex-field", default=None, metavar="PATH",
                    help="where the Cortex decision field is persisted (the EMA centroid + "
                         "the decision log). Defaults to <worlds_dir>/cortex.json so it "
                         "resumes across restarts.")
    ap.add_argument("--cortex-alpha", type=float, default=0.9, metavar="A",
                    help="the EMA weight of the decision field: field_after = "
                         "alpha*field_before + (1-alpha)*unit(decision). 0.9 carries context "
                         "strongly; a smaller alpha forgets faster. Default 0.9.")
    ap.add_argument("--cortex-surprise", type=float, default=0.5, metavar="S",
                    help="the cosine threshold below which a decision is 'surprising' "
                         "(foreign to the accumulated context) -- the acquisition signal. "
                         "Default 0.5.")
    ap.add_argument("--no-cortex-log", dest="cortex_log", action="store_false", default=True,
                    help="opt-OUT of the [cortex] per-decision log: by default every tool_use "
                         "the agent emits is logged to stderr (-> membrane-proxy.log) with its "
                         "similarity/novelty so you can tail -f the agent's decision variety. "
                         "Pass this to silence it.")
    # ---- the live 3D viewer (off by default; opt-in, read-only) ----------------
    # Serves GET /viewer (the static Three.js page) and GET /state (a JSON snapshot
    # of the live field, read in-process). The gate path is untouched -- the page
    # chats through the real /api/chat, so you talk to the live cell and watch its
    # field respond in 3D. Local-first (the proxy is 127.0.0.1 only); no key, no
    # new remote calls from the proxy (the browser loads Three.js from a CDN).
    ap.add_argument("--viewer", action="store_true",
                    help="serve the 3D live viewer at /viewer and a field snapshot at /state "
                         "(off by default). Open http://127.0.0.1:<port>/viewer; the page polls "
                         "/state and chats through the real /api/chat gate.")
    ap.add_argument("--no-viewer-log", dest="viewer_log", action="store_false", default=True,
                    help="opt-OUT of the [viewer] action log: by default every viewer-driven "
                         "action (RUN/STOP/STEP/chat/POV/dials/settings/world save-load) is logged "
                         "to stderr (-> membrane-proxy.log) so you can tail -f what the viewer is "
                         "doing. Pass this to silence it.")
    ap.add_argument("--system", default=None,
                    help="the world premise -- a constant ground injected as the model's "
                         "`system` field on every escalate (only when the client sent none). "
                         "Authors WHAT world this is; the wandering authors where you've been. "
                         "Durable: unlike a premise in the opening message, it never scrolls "
                         "off the context window. Also the seed for --warmup.")
    ap.add_argument("--warmup", type=int, default=0, metavar="N",
                    help="the time-skipper: at startup, populate the field with N premise-"
                         "grounded offscreen events (one model call for a world palette + N "
                         "no-model-call learn_pair absorptions). Lets the world 'live' before "
                         "you arrive so the cloud is populated and the gate has real variety. "
                         "Requires --system. 0 = off (default).")
    ap.add_argument("--warmup-model", default=None, metavar="MODEL",
                    help="Ollama model for the ONE warm-up palette call (default: the judge "
                         "model). The N absorptions make no model calls -- local embeddings only.")
    ap.add_argument("--warmup-seed", type=int, default=0, metavar="SEED",
                    help="RNG seed for warm-up situation composition (reproducible).")
    ap.add_argument("--entropy-ceiling", type=float, default=0.1, metavar="FRAC",
                    help="the never-cross line: the max fraction of the theoretical 100%% "
                         "(the embedding dimension = the field's max effective rank) that the "
                         "injected entropy ballast may fill. 0.01 = 1%% (the CMB stays a faint "
                         "background, far below the theoretical 100%%, never crossed). The viewer "
                         "dials this live; the proxy default sets /state's cap. ~0.025 is where the "
                         "ballast equals the cells' level (the drown edge); 1.0 = the theoretical max.")
    ap.add_argument("--feed-rate", dest="feed_rate", type=float, default=1.0, metavar="RATE",
                    help="consolidation: feeds per cycle on the world's own clock (continuous; "
                         "0 = digest only, no feeding). The viewer's ENTROPY slider dials this live. "
                         "Each feed pops one cached chronicle scene (a cheap embed, not a model call).")
    ap.add_argument("--cpu-target", dest="cpu_target", type=float, default=0.2, metavar="FRAC",
                    help="consolidation: target CPU fraction for the digest phase (0..1). The "
                         "closed loop ramps the cell-sweep budget to hit this duty cycle.")
    ap.add_argument("--progression-every", dest="progression_every", type=int, default=0, metavar="N",
                    help="consolidation: every N autonomous cycles, one feed is an LLM-authored "
                         "ADVANCING beat (trusted, trained into the on-stage character's n-gram) so "
                         "the cells-authored story evolves instead of stammering on the frozen "
                         "genesis corpus. 0 = off (cells n-gram only). Fires only in cells-act "
                         "autonomous mode; spiral-safe (LLM-authored, not n-gram output).")
    ap.add_argument("--consolidate", action="store_true",
                    help="arm the consolidation thread at startup (the world feeds + digests on "
                         "its own clock; otherwise the viewer's PLAY arms it).")
    ap.add_argument("--initial-burst", dest="initial_burst", type=int, default=3, metavar="N",
                    help="consolidation: auto-PAUSE after N cycles run past the first chapter "
                         "(the 'initial training' burst; 0 = never auto-pause, runs forever). "
                         "Only meaningful with --consolidate. NEW WORLD / LOAD re-arms it.")
    ap.add_argument("--beat-author", dest="beat_author", default="llm",
                    choices=("llm", "cells"),
                    help="who authors the autonomous pov-follow chronicle beats: "
                         "'llm' (default; the genesis model) or 'cells' (the on-stage "
                         "character's own per-character n-gram voice -- no LLM; engages "
                         "after training). Reactive beats are always LLM. The post-training "
                         "flip sets this to 'cells'.")
    ap.add_argument("--representation", dest="representation", default="llm",
                    choices=("llm", "corrected", "raw"),
                    help="how the viewer displays each beat by default: 'llm' (an LLM "
                         "render of the raw beat -- readable prose, display only, never "
                         "absorbed), 'corrected' (client-side typographic repair, no LLM), "
                         "or 'raw' (the literal n-gram text). Persisted overrides this. The "
                         "viewer toggle switches at runtime.")
    args = ap.parse_args()

    # --tag defaults the field/audit paths so independent veils own independent fields
    # (the decentralized design: no central store, no clobbering). Explicit --field/
    # --audit still win.
    if args.tag:
        if args.field == "membrane-field.json":
            args.field = f"membrane-field-{args.tag}.json"
        if args.audit == "membrane-audit.jsonl":
            args.audit = f"membrane-audit-{args.tag}.jsonl"

    anthropic = args.anthropic
    backend = args.backend or ("https://api.anthropic.com" if anthropic else "http://localhost:11434")
    # observer is implied by --anthropic unless --absorb explicitly re-enables replay
    observer = args.observer or (anthropic and not args.absorb)
    if anthropic and args.absorb and not args.observer:
        observer = False

    judge = (OllamaJudge(backend=args.judge_backend or ("http://localhost:11434" if anthropic else backend),
                        model=args.judge_model, timeout=args.judge_timeout)
             if args.judge else StubJudge())
    responder = (AnthropicMessagesResponder(backend) if anthropic
                 else OllamaChatResponder(backend))
    membrane = Membrane(
        responder=responder,
        field_path=args.field,
        audit_path=args.audit,
        sim_min=args.sim_min,
        consensus_min=args.consensus_min,
        dedup_sim=args.dedup_sim,
        neg_followup=args.neg_followup,
        demote_threshold=args.demote_threshold,
        judge=judge,
        observer=observer,
        self_author=args.self_author,
        speak_mode=args.speak,
        vague_filter=args.vague_filter,
        vague_min_tokens=args.vague_min_tokens,
        variety_gate=args.variety_gate,
        variety_temp=args.variety_temp,
        temp_driver=args.temp_driver,
        temp_tau=args.temp_tau,
        sim_min_floor=args.sim_min_floor,
        nudge_sigma=args.nudge_sigma,
        nudge_seed=args.nudge_seed,
    )
    # ---- the cast: seed the ensemble (--character) then the protagonist --------
    # Admit each --character 'Name — role — voice' to the cast if absent, then the
    # --protagonist (admit + set POV). All run BEFORE warmup/genesis so retro_tag
    # colors the whole cast into the existing field at once and the narrator
    # speaks from the protagonist's region from the first beat. Seeding the cast
    # here also means genesis SKIPS its own cast block (_seed_cast bails on a
    # non-empty cast) -- so the cast is exactly the authored characters, not
    # LLM-chosen -- and just narrates the chronicle (grounded in --system).
    if args.character:
        for spec in args.character:
            _admit_character(membrane, spec, tag="character")
    if args.protagonist:
        _apply_protagonist(membrane, args.protagonist)
    # ---- the feeding boundary + background cell pool (stage 2) ----------------
    # When --cells is off (default), pool stays None and the live single-Membrane
    # path pays only one ``if srv.cell_pool is not None`` guard per cycle (never taken).
    # When on, a SELF-BALANCING observer organism parallels the single Membrane: its
    # cells GRAZE ``srv.membrane.store`` (the world's field -- already-embedded
    # exemplars, veiled per-cell with no embed call) and the Consolidator buds/retires
    # them to the world's variety, capped by ``cpu_target`` (see consolidate._cycle).
    # Cells never serve live requests (observer mode) and never feed back. The
    # user-turn buffer is NOT used by the pool -- user turns reach the cells via the
    # world's own field (a turn the world learns from becomes an exemplar the cells
    # graze), so the feeding boundary (``srv.buffer``) stays None and the
    # ``_maybe_deposit``/``_maybe_attach`` guards short-circuit.
    pool = None
    if args.cells > 0:
        import os as _os
        import math as _math
        _field_dir = _os.path.dirname(_os.path.abspath(args.field)) if args.field else "."
        _field_prefix = _os.path.join(_field_dir, "cell-field-")
        _audit_prefix = _os.path.join(_field_dir, "cell-audit-")
        # the veil strength scales with the embedding dim so the noise is a GENTLE
        # perturbation (norm ~0.5, cos(veiled,original)~0.89) regardless of the embed
        # model. The experiment's nudge_sigma=0.25 is calibrated for its D=8 fake
        # embedder; at the live world's 1024 dims that sigma makes the noise norm ~8x
        # the signal, so veiled vecs are pure noise and the orientation gate (the
        # specialization mechanism) blocks EVERYTHING -> cells never absorb (n=0).
        # 0.5/sqrt(dim) keeps each cell's veiled view aligned to its assigned cone (the
        # gate works, cells partition the field into distinct directions -> distinct
        # colors) while still giving each cell a distinct per-seed manifold.
        _dim = len(membrane.store.exemplars[0].vec) if membrane.store.exemplars else 1024
        _veil_sigma = 0.5 / _math.sqrt(_dim)

        def _cell_factory(tag, field_path, audit_path, nudge_seed):
            return Cell(
                tag=tag,
                responder=StubResponder(tag=tag),
                field_path=field_path,
                audit_path=audit_path,
                sim_min=args.sim_min,
                consensus_min=args.consensus_min,
                dedup_sim=args.dedup_sim,
                observer=True,            # the never-authors guardrail -- do NOT flip
                vague_filter=False,        # isolate curiosity+veil (the experiment config)
                variety_gate=False,
                variety_temp=False,
                nudge_sigma=_veil_sigma,   # dim-scaled veil (see above); 0.25 is D=8-only
                nudge_seed=nudge_seed,     # per-cell seed -> different manifolds -> specialization
                curiosity_on=True,
                curiosity_min=CURIOSITY_MIN,
                fresh=True,               # blank field; grazes the world to grow its cone
            )

        pool = CellPool(membrane, _cell_factory, interval=2.0,
                        field_prefix=_field_prefix, audit_prefix=_audit_prefix)
        # seed the initial cells committed to DISTINCT world directions (so they
        # specialize from the first graze, like balance's buds) rather than as
        # generalists that would absorb the whole field and converge to one color.
        # A fresh/empty world has no separable directions yet -> seed generalists;
        # balance buds oriented cells as the world's variety grows.
        _seed_dirs = []
        if len(membrane.store) >= 2:
            from .variety import orient_dirs as _od
            _seed_dirs = _od(membrane.store, args.cells + 1, 0.5)
        for _i in range(args.cells):
            pool.bud_cell(orientation=_seed_dirs[_i] if _i < len(_seed_dirs) else None)
        pool.start()
    # ---- the time-skipper (--warmup): let the world live before you arrive -----
    # Runs BEFORE the banner so the banner's field_size reflects the populated
    # field. ONE model call for a premise palette + N no-model-call learn_pair
    # absorptions. Requires --system (the premise grounds the palette). The gate
    # path is untouched -- only learn_pair (the teed-intake seam) writes exemplars.
    if args.warmup > 0:
        if not args.system:
            print("[membrane] --warmup needs --system (the premise to ground the world); "
                  "skipping warm-up", file=sys.stderr)
        else:
            from .warmup import run_warmup, build_palette
            # build the palette ONCE (the single warm-up model call -- the only LLM
            # use in the stripped world) and cache it; the startup seed AND the no-model
            # /live loop both reuse it, so the world keeps growing offscreen LLM-free.
            srv_palette, _src = build_palette(backend, args.warmup_model or args.judge_model,
                                             args.system)
            run_warmup(membrane, backend, args.warmup_model or args.judge_model,
                       args.system, args.warmup, args.warmup_seed, palette=srv_palette)
    else:
        srv_palette = None

    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), ProxyHandler)
    srv.membrane = membrane
    # the cast-mutating half of gate_turn (/api/chat) and Membrane.adjust both
    # acquire this lock so neither races the consolidator's _run_feeds (which
    # holds it too). Same object as srv.feed_lock, set below. RLock: the call
    # graph re-acquires it on one thread (adjust -> _learn -> contest).
    membrane.mut_lock = threading.RLock()          # reassigned to srv.feed_lock below
    srv.backend = backend.rstrip("/")
    srv.buffer = None         # the user-turn buffer is unused by the graze pool; None keeps the deposit guards short-circuited
    srv.cell_pool = pool      # None when OFF (nothing for the shutdown finally to stop)
    srv.viewer = args.viewer  # off by default; /viewer + /state are read-only
    srv.viewer_log = bool(getattr(args, "viewer_log", True))  # opt-OUT [viewer] action log (--no-viewer-log)
    srv._viewer_html = None   # lazy-loaded + cached on first GET /viewer
    srv._v_basis = None       # PCA basis, fit once for a stable 3D cloud
    srv.system_prompt = args.system   # opt-in world premise (--system); injected into forwards
    srv.worlds_dir = args.worlds_dir  # where the viewer's save/load/new-world buttons keep slots
    srv.warmup_model = args.warmup_model or args.judge_model   # the one warm-up palette call
    srv.ollama_token = None                                    # optional bearer; in-memory only, never persisted
    srv.beat_author = str(getattr(args, "beat_author", "llm"))  # llm | cells (who authors the autonomous chronicle)
    srv.representation = str(getattr(args, "representation", "llm"))  # llm | corrected | raw (which view the viewer shows)
    # apply persisted settings.json (backend + story model + beat author + the
    # growth dials) over the CLI defaults; the token is in-memory only, so it
    # starts cleared each boot. Then sync the shared embedder + responder to the
    # resolved backend so /api/embed + /api/chat agree (also fixes the old latent
    # mismatch where the embedder hardcoded localhost:11434 regardless of
    # --backend).
    _persisted = _load_settings(srv)
    if _persisted.get("backend"):
        srv.backend = _persisted["backend"]
    if _persisted.get("story_model"):
        srv.warmup_model = _persisted["story_model"]
    if _persisted.get("beat_author") in ("llm", "cells"):
        srv.beat_author = _persisted["beat_author"]
    srv.warmup_seed = args.warmup_seed
    srv.palette = srv_palette        # cached palette (built at startup warm-up); the no-model /live loop reuses it
    srv.entropy_ceiling = max(0.0, min(1.0, args.entropy_ceiling))   # inert for growth now (nudging removed); kept for the viewer's variety display
    srv.story_temperature = 0.7                                      # FIXED for a cohesive chronicle (no longer a dial)
    srv.story_mode = "story"                                          # 'story' = continuing chronicle; 'variety' = random vignettes
    srv.story_history = []                                            # the rolling chronicle (last 8 scenes fed; seeds the next chapter)
    srv.story_cache = []                                             # the genesis cache: pending chronicle scenes (one LLM chapter -> ~8 feeds)
    srv.cache_depth = 0
    srv.feed_lock = threading.RLock()                                  # serializes feeders (/world/step + the consolidator + /adjust_narrative); RLock: runtime's mutation paths nest (adjust -> _learn -> contest)
    srv._events = []                                                   # opt-in client action recorder buffer (?rec=1 viewer)
    srv._events_lock = threading.Lock()                                # guards _events (POST/GET/DELETE from the page)
    srv.feed_rate = max(0.0, min(32.0, float(getattr(args, "feed_rate", 1.0))))  # feeds/cycle (continuous; the ENTROPY dial)
    srv.cpu_target = max(0.0, min(1.0, float(getattr(args, "cpu_target", 0.2))))  # target CPU fraction for digestion
    srv.progression_every = max(0, int(getattr(args, "progression_every", 0)))  # autonomous LLM progression drip (cycles; 0=off)
    # persisted growth dials override the CLI defaults (settings.json is the
    # single source of truth for the dials the viewer edits via /world/controls).
    if "feed_rate" in _persisted:
        srv.feed_rate = max(0.0, min(32.0, float(_persisted["feed_rate"])))
    if "cpu_target" in _persisted:
        srv.cpu_target = max(0.0, min(1.0, float(_persisted["cpu_target"])))
    if "cycle_speed" in _persisted:
        srv.cycle_speed = max(0.25, min(8.0, float(_persisted["cycle_speed"]))) or 1.0
    if "entropy_ceiling" in _persisted:
        srv.entropy_ceiling = max(0.0, min(1.0, float(_persisted["entropy_ceiling"])))
    if "progression_every" in _persisted:
        srv.progression_every = max(0, int(_persisted["progression_every"]))
    if _persisted.get("story_mode") in ("story", "variety"):
        srv.story_mode = _persisted["story_mode"]
    if _persisted.get("representation") in ("llm", "corrected", "raw"):
        srv.representation = _persisted["representation"]
    # the world's own clock: world_running is canonical. Boot STOPPED unless
    # --consolidate arms it; _user_run stays False so the post-training auto-pause
    # still fires for a --consolidate boot.
    _set_world_running(srv, bool(getattr(args, "consolidate", False)))
    srv._user_run = False
    membrane.mut_lock = srv.feed_lock     # gate_turn + adjust serialize on this
    srv.cycle_speed = float(getattr(srv, "cycle_speed", 1.0) or 1.0)  # persisted or 1x
    srv.initial_burst = int(getattr(args, "initial_burst", 0))         # auto-pause after N post-first-chapter cycles
    srv._initial_burst_arg = int(getattr(args, "initial_burst", 0))   # re-arm value for NEW WORLD / LOAD
    srv.consolidation_stats = _fresh_consolidation_stats(srv)
    # restore the world clock from the persisted cast wrapper so a restart
    # continues the in-world date instead of resetting to "cycle 0" while the
    # field still holds 100+ scenes. cast.cycles is kept in sync at every cycle
    # increment (consolidator + STEP) and persisted whenever the cast is saved.
    _restored_cycles = int(getattr(srv.membrane.cast, "cycles", 0))
    srv.consolidation_stats["cycles"] = _restored_cycles
    from .consolidate import Consolidator
    srv.consolidator = Consolidator(srv)
    srv.consolidator.start()
    srv.anthropic = anthropic         # which system-prompt shape _apply_system uses (msg vs field)
    # ---- Cortex mode: the agent-decision variety field (off by default). Built after
    # the membrane so the embedder is available (the Cortex reuses the world's
    # OllamaEmbedder -> decisions land in the SAME vector space as the world's field).
    # When off, srv.cortex is None and the _messages Cortex branch short-circuits, so
    # the /v1/messages path is byte-for-byte the world gate.
    srv.cortex_mode = bool(getattr(args, "cortex", False))
    srv.cortex_log = bool(getattr(args, "cortex_log", True))
    srv.cortex = None
    srv.cortex_field_path = ""
    if srv.cortex_mode:
        from .cortex import DecisionStore, DecisionField
        _cpath = args.cortex_field or os.path.join(args.worlds_dir, "cortex.json")
        _cstore = DecisionStore.load(_cpath, alpha=float(getattr(args, "cortex_alpha", 0.9)))
        srv.cortex = DecisionField(
            _cstore, embedder=membrane.embedder,
            surprise_threshold=float(getattr(args, "cortex_surprise", 0.5)))
        srv.cortex_field_path = _cpath
        if len(_cstore):
            print(f"[cortex] loaded field: {len(_cstore)} decisions, "
                  f"eff_rank~{_cstore.field is not None}", file=sys.stderr)
    judge_s = f"ON (model={args.judge_model}, timeout={args.judge_timeout}s)" if args.judge else "off (StubJudge)"
    neg_s = "on (complaint follow-up demotes; --no-neg-followup to disable)" if args.neg_followup else "off (verbatim re-ask only)"
    mode_s = ("Anthropic /v1/messages" if anthropic else "Ollama /api/chat")
    obs_s = ("OBSERVER (forward+learn, never replay)" if observer
             else ("absorb (replay on act)" if anthropic else "absorb (replay on act)"))
    if args.self_author:
        obs_s = ("SELF-AUTHOR (stripped): every turn -- act AND escalate -- is the cell "
                 "speaking for itself (field n-gram, NO LLM, nothing learned). Re-ask "
                 "demotes the cell that spoke; the field is coupled to you, not the model. "
                 "Warm-up is the only LLM use (the seed palette).")
    vague_s = (f"on (min {args.vague_min_tokens} tokens; --no-vague-filter to disable)"
               if args.vague_filter else "off")
    variety_s = "ON (effective-rank grow gate; 2x SVD/learn)" if args.variety_gate else "off"
    vtemp_s = (f"ON (driver={args.temp_driver}, tau={args.temp_tau}, "
               f"floor={args.sim_min_floor}; sim_min eases as safe variety grows)"
               if args.variety_temp else "off (static sim_min)")
    nudge_s = (f"ON (sigma={args.nudge_sigma}, seed={args.nudge_seed}; "
               f"every transit veiled, prompt text untouched)" if args.nudge_sigma > 0
               else "off (identity veil)")
    cells_s = (f"ON ({args.cells} cells, sigma={round(_veil_sigma, 4)}, curiosity_min={CURIOSITY_MIN}, "
               f"observer/fresh-persist/teed; per-cell cell-field-<i>; pool cadence 2s)"
               if args.cells > 0 else "off (single-Membrane path, unchanged)")
    tag_s = f"  tag={args.tag}" if args.tag else ""
    system_s = ("ON (injected as `system` on every escalate; the world's constant ground)"
                if args.system else "off (no --system; world seeded by its opening message)")
    warmup_s = (f"ON ({args.warmup} offscreen events at startup, model="
                f"{args.warmup_model or args.judge_model})" if args.warmup > 0 else "off")
    print(f"membrane proxy  http://127.0.0.1:{args.listen}  ->  {backend}  [{mode_s}]\n"
          f"  field={args.field}  audit={args.audit}  sim_min={args.sim_min}  "
          f"dedup_sim={args.dedup_sim}  demote_threshold={args.demote_threshold}  "
          f"field_size={len(membrane.store)}{tag_s}\n"
          f"  mode: {obs_s}\n"
          f"  neg-followup: {neg_s}\n"
          f"  vague filter: {vague_s}\n"
          f"  variety gate: {variety_s}\n"
          f"  adaptive temp: {vtemp_s}\n"
          f"  veil nudge: {nudge_s}\n"
          f"  cells: {cells_s}\n"
          f"  premise: {system_s}\n"
          f"  warmup: {warmup_s}\n"
          f"  judge: {judge_s}\n"
          f"  (API key: passthrough only, never stored/logged; gates {mode_s} stream + non-stream)",
          file=sys.stderr)
    # SIGTERM (run.sh's TERM trap, or `kill`) -> KeyboardInterrupt so the except
    # catches it cleanly. Today nothing closes the socket; the finally also joins
    # the cell pool so a background cell mid-pull cannot hang the exit (the daemon
    # threads would die with the process anyway, but joining is bounded and clean).
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[membrane] bye", file=sys.stderr)
    finally:
        if srv.cell_pool is not None:
            srv.cell_pool.stop()
        try:
            srv.consolidator.stop()
        except Exception:
            pass
        srv.server_close()


if __name__ == "__main__":
    main()