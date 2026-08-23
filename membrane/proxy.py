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
                      hint_system_message)
from .buffer import BufferView, SituationBuffer
from .cell import Cell
from .curiosity import CURIOSITY_MIN
from .pool import CellPool

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


# ---- /settings: runtime Ollama server + story model + optional token ----------
# The NON-secret parts (backend URL + story model) persist to settings.json next
# to the field, so a chosen server/model survives restarts. The optional bearer
# token is in-memory ONLY -- never written to disk, never returned by GET
# /settings or /state -- the same discipline as the passthrough API key.

def _settings_path(srv) -> str:
    return os.path.join(getattr(srv, "worlds_dir", "worlds"), "settings.json")


def _load_settings(srv) -> dict:
    """Read persisted {backend, story_model, world_drive, beat_author} from
    settings.json, or {} if absent. Never holds a token -- the token is in-memory only."""
    try:
        with open(_settings_path(srv), "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    out = {}
    if isinstance(d.get("backend"), str) and d["backend"].strip():
        out["backend"] = d["backend"].strip().rstrip("/")
    if isinstance(d.get("story_model"), str) and d["story_model"].strip():
        out["story_model"] = d["story_model"].strip()
    if isinstance(d.get("world_drive"), str) and d["world_drive"].strip():
        out["world_drive"] = d["world_drive"].strip()
    if isinstance(d.get("beat_author"), str) and d["beat_author"].strip():
        out["beat_author"] = d["beat_author"].strip()
    return out


def _save_settings(srv, backend: str, story_model: str | None,
                   world_drive: str | None = None,
                   beat_author: str | None = None) -> None:
    """Persist the non-secret settings (backend + story model + world drive +
    beat author). The token is deliberately NOT written -- in-memory only."""
    p = _settings_path(srv)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        d = {"backend": backend, "story_model": story_model or ""}
        wd = world_drive if world_drive is not None else getattr(srv, "world_drive", "reactive")
        d["world_drive"] = wd
        ba = beat_author if beat_author is not None else getattr(srv, "beat_author", "llm")
        d["beat_author"] = ba
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


# ---- story-feeding (the /feed endpoint) --------------------------------------
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
# The REACTIVE narrator (one-scene beat that follows a player move). The world
# writes itself AROUND the explorer: after the protagonist acts/speaks, narrate
# the SINGLE next scene (2 to 3 sentences) that follows from it -- what happens
# next, featuring the protagonist and the cast they're with. No ---, no list of
# scenes, no commentary -- just the one beat.
_REACTIVE_SYSTEM = (
    "You are the chronicler of this world. Narrate ONE scene (2 to 3 sentences) "
    "that follows immediately from what just happened -- the next beat of the "
    "ongoing story, featuring the protagonist and whoever they're with. Show what "
    "happens next: action, dialogue, consequence. Stay in the world's voice; keep "
    "the named cast on stage; do not recap or comment. Write ONLY the scene, no "
    "headings, no labels, no --- separators.")
_CHAPTER = 8        # scenes per genesis call (one model call -> ~8 cached feeds)
_LOW_WATER = 3      # refill the cache (one model call) when fewer scenes remain


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
    # pov-follow: the autonomous chronicle follows the ON-STAGE character (the
    # freshest in the narrative, ``cast.on_stage``), not always the POV -- so when
    # the POV fell out of the narrative (faded) the chronicle follows whoever is
    # in the scene ("only follow their POV when they are in the scene"). on_stage
    # returns the POV when it is live (recently tagged), so the usual case is
    # unchanged; the fade is just "stop POVing a stale POV." Seed the chapter with
    # that character's current situation so the LLM continues from where the
    # narrative IS, not where its own thread drifted.
    pov_block = ""
    if getattr(srv, "world_drive", "reactive") == "pov-follow":
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


# ---- shared feed core (used by the HTTP /feed path AND the consolidator) -------
def _feed_snapshot(srv, mem) -> dict:
    """A read-only field snapshot in the /feed stats shape (no LLM, no mutation).
    The base for the ``feeds=0`` persist response and for ``_run_feeds``."""
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
            "consolidating": bool(getattr(srv, "consolidating", False)),
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
    srv.cycle_speed = 1.0                     # playback speed back to 1x
    srv.consolidating = False                 # a fresh/loaded world waits for PLAY
    if rearm_burst:
        srv.initial_burst = getattr(srv, "_initial_burst_arg", 0)
    rec = getattr(mem, "_recent", None)
    if rec is not None:
        try: rec.clear()
        except Exception: pass
    mem._gen_lm = None
    mem._gen_key = None


def _run_feeds(srv, mem, feeds: int, abort_if_disarmed: bool = False) -> dict:
    """Run ``feeds`` story-feeds from the GENESIS CACHE and return the stats dict
    (with ``stories`` for the console). Each feed pops one cached chronicle scene
    and embeds+absorbs it (a cheap ``/api/embed``, NOT a ``/api/chat`` call); the
    cache is refilled (one LLM chapter call -> ~``_CHAPTER`` scenes) when it runs
    below ``_LOW_WATER``. So the cohesive story is told in bulk and the world feeds
    at the rate the feed-rate dial sets, without a model call per feed.

    Serializes on ``srv.feed_lock`` so the HTTP ``/feed`` path and the background
    consolidator never race the rolling narrative (``srv.story_history``) or the
    cache. Absorbs the RAW story embedding (``mem.feed`` no longer nudges).

    ``abort_if_disarmed`` (the consolidator sets it): bail before a new genesis
    call and between feeds when ``srv.consolidating`` flips False, so a PAUSE
    takes effect within a feed or two instead of waiting on a whole chapter. The
    HTTP STEP path leaves it False (a STEP feeds regardless of arm state).

    Each story is tagged ``pov`` when it tagged the POV character, so the viewer
    can narrate only the protagonist's thread (``storyPush`` POV filter).

    CELLS-ACT: when ``beat_author == "cells"`` and training is done
    (``initial_burst`` spent), each feed is authored INLINE by the on-stage
    character's own per-character n-gram voice (``_cells_beat``, no LLM) -- the
    closed loop (generate -> feed -> absorb -> re-tag -> next beat). When no
    character can speak yet (none has lived >= _VOICE_MIN) ``_cells_beat`` returns
    None and we fall back to the LLM genesis cache ("we just narrate it"). Reactive
    beats are never cells-authored -- this path is pov-follow only."""
    stats = _feed_snapshot(srv, mem)
    premise = getattr(srv, "system_prompt", None)
    if not premise or feeds <= 0:
        return stats
    ceiling = float(getattr(srv, "entropy_ceiling", 0.05))   # inert for growth (kept)
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    pov_id = getattr(mem.cast, "pov_id", None)
    stories: list = []
    with srv.feed_lock:
        if abort_if_disarmed and not getattr(srv, "consolidating", True):
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
        # it"). This path is pov-follow only; reactive beats are never cells-authored.
        cells_act = (str(getattr(srv, "beat_author", "llm")) == "cells"
                     and not bool(getattr(srv, "initial_burst", 0)))
        # refill the genesis cache if low -- ONE model call -> ~CHAPTER cached feeds.
        # Skipped in cells-act (the n-gram authors; the cache is only the no-speaker
        # fallback, refilled on demand inside the loop).
        if (not cells_act and len(cache) < _LOW_WATER
                and not (abort_if_disarmed and not getattr(srv, "consolidating", True))):
            cache.extend(_genesis(srv))
            srv.story_cache = cache
        for _ in range(feeds):
            if abort_if_disarmed and not getattr(srv, "consolidating", True):
                break                           # paused between feeds -- stop, don't spin
            passage = None
            if cells_act:
                passage = _cells_beat(srv, mem)    # the on-stage char's n-gram voice (no LLM)
            if passage is None:
                # LLM path: training, or the cells-act "narrate it" fallback when no
                # character can speak yet.
                if not cache:                       # drained; one more chapter before giving up
                    if abort_if_disarmed and not getattr(srv, "consolidating", True):
                        break
                    cache.extend(_genesis(srv))
                    srv.story_cache = cache
                    if not cache:                   # model unavailable -- stop, don't spin
                        stories.append({"seed": "", "text": "", "result": "no-story", "pov": False})
                        break
                passage = cache.pop(0)
            r = mem.feed(passage, ceiling, cycle=cycle)     # raw embed + absorb (no nudging)
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


def _reactive_beat(srv, mem, prompt: str) -> dict:
    """ONE reactive beat: the world writes a single scene that follows from the
    protagonist's last move (REACTIVE world-drive). Unlike the autonomous genesis
    (which narrates a whole chapter from its own rolling thread), this continues
    the story FROM where the explorer IS -- the POV's current situation plus, when
    present, what the player just did/said (``prompt``). One LLM call -> one scene
    (2 to 3 sentences) -> embed + absorb + tag the cast. Returns the /feed stats
    shape with the beat in ``stories`` (marked ``pov``).

    An empty ``prompt`` (the STEP button in reactive mode) asks for the next beat
    from the POV's situation alone. No premise -> no beat (the world has nothing
    to tell)."""
    stats = _feed_snapshot(srv, mem)
    backend = srv.backend
    model = getattr(srv, "warmup_model", None)
    premise = getattr(srv, "system_prompt", "")
    if not model or not backend or not premise:
        stats["stories"] = []
        return stats
    cast = mem.cast
    pov = cast.by_id(getattr(cast, "pov_id", None)) if cast.pov_id is not None else None
    cast_block = ""
    if cast.chars:
        cast_block = ("The cast:\n" +
                      "\n".join(f"- {c.name} — {c.role} — {c.voice}"
                                for c in cast.chars if c.name) + "\n\n")
    pov_line = ""
    if pov is not None and getattr(pov, "last_scene", ""):
        pov_line = f"{pov.name} is currently here:\n{pov.last_scene}\n\n"
    elif pov is not None:
        pov_line = f"{pov.name} is the protagonist.\n\n"
    move = (prompt or "").strip()
    move_line = (f"What {pov.name} just did/said: {move}\n\n"
                 "Narrate the next scene that follows from it.\n\n"
                 if pov is not None and move else
                 "Narrate the next scene from where things stand.\n\n")
    user = (premise + "\n\n" + cast_block + pov_line + move_line +
            "Write ONE scene (2 to 3 sentences), no --- separators.")
    messages = [{"role": "system", "content": _REACTIVE_SYSTEM},
                {"role": "user", "content": user}]
    body = {"model": model, "stream": False,
            "temperature": float(getattr(srv, "story_temperature", 0.7)),
            "messages": messages}
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(backend + "/api/chat", data=data,
                                     headers=_ollama_headers(srv))
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
    except Exception as e:
        print(f"[reactive] beat call failed ({e})", file=sys.stderr)
        stats["stories"] = []
        return stats
    txt = ""
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            txt = msg["content"].strip()
    # the model may still emit a --- list; take the first scene only
    if "---" in txt:
        txt = [s.strip() for s in re.split(r"(?m)^\s*-{2,}\s*$", txt) if s.strip()]
        txt = txt[0] if txt else ""
    if not txt:
        stats["stories"] = []
        return stats
    ceiling = float(getattr(srv, "entropy_ceiling", 0.05))
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    with srv.feed_lock:
        r = mem.feed(txt, ceiling, cycle=cycle)
        stats["fed"] += int(r.get("fed", 0))
        res = "fed" if r.get("fed") else "skipped"
        if not r.get("fed"):
            stats["skipped"] += 1
        tags = r.get("tags") or []
        pov_id = getattr(cast, "pov_id", None)
        # a reactive beat is narrated FROM the POV's situation + the player's move,
        # so it is a POV scene BY CONSTRUCTION -- mark it pov so storyPush always
        # shows the world's response (independent of whether the embedding
        # geometrically tagged the protagonist this beat).
        stats["stories"] = [{"seed": "", "text": txt, "result": res,
                             "pov": pov_id is not None}]
        stats["field_size"] = r.get("field_size", stats["field_size"])
        if "cells_level" in r:
            stats["cells_level"] = round(float(r["cells_level"]), 2)
        stats["free_energy"] = float(r.get("free_energy", stats["free_energy"]))
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
    seed = _gen_tokens(getattr(onstage, "last_scene", ""))[:2]
    cycle = int(getattr(srv, "consolidation_stats", {}).get("cycles", 0))
    digest = hashlib.sha256(
        f"{onstage.id}\x00{cycle}\x00{len(mem.store)}".encode()).digest()
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
        elif self.path in ("/feed", "/feed/"):
            self._feed()
        elif self.path in ("/settings", "/settings/"):
            self._settings()
        elif self.path in ("/cast", "/cast/"):
            self._cast()
        elif self.path in ("/world", "/world/"):
            self._world()
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
            if self.path.split("?")[0] in ("/neighbors", "/neighbors/"):
                self._neighbors(); return
            if self.path.split("?")[0] in ("/worlds", "/worlds/"):
                self._list_worlds(); return
        self._forward()

    def do_DELETE(self):
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

    def _feed(self) -> None:
        """Story-fed growth: the cells' "feed me" -> the LLM tells a short story
        of their world -> embed + MASSAGE to the ceiling -> absorb as a REAL
        exemplar (runtime ``Membrane.feed``). Replaces the random-vector ballast
        with real semantic text; the never-cross ceiling now caps the WORLD's
        variety (cells_rank), held by deforming each story toward the field
        centroid until it fits. Forward cap -- real cells are never trimmed.

        ``feeds=0`` persists the dials ONLY (no LLM call) -- the viewer's slider
        oninput and the consolidation arm/disarm use this to set the ceiling /
        entropy / feed-rate / CPU-target / consolidating flag without growing.
        With ``feeds>0`` each feed is one model call (``_tell_story``); the loop
        lives in ``_run_feeds`` (shared with the background consolidator)."""
        srv = self.server
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        mem = srv.membrane
        # ---- persist the dials (single source of truth) -----------------------
        ceiling = float(body.get("entropy_ceiling", getattr(srv, "entropy_ceiling", 0.05)))
        ceiling = max(0.0, min(1.0, ceiling))
        srv.entropy_ceiling = ceiling                  # inert for growth now (nudging removed)
        # temperature is FIXED server-side for a cohesive chronicle; the entropy dial
        # now sets the FEED RATE (feeds/cycle), not the LLM temperature.
        mode = str(body.get("mode", getattr(srv, "story_mode", "story"))).lower()
        if mode not in ("story", "variety"):
            mode = "story"
        srv.story_mode = mode
        feed_rate = float(body.get("feed_rate", getattr(srv, "feed_rate", 1.0)))
        srv.feed_rate = max(0.0, min(32.0, feed_rate))  # feeds/cycle (continuous; the entropy dial)
        cpu_target = float(body.get("cpu_target", getattr(srv, "cpu_target", 0.2)))
        srv.cpu_target = max(0.0, min(1.0, cpu_target))  # target CPU fraction for digestion
        consolidating = bool(body.get("consolidating", getattr(srv, "consolidating", False)))
        srv.consolidating = consolidating               # arm/disarm the world's own clock
        if "cycle_speed" in body:                       # 1x/2x/4x playback -> world clock rate
            try:
                srv.cycle_speed = max(0.25, min(8.0, float(body.get("cycle_speed", 1.0)))) or 1.0
            except (TypeError, ValueError):
                pass
        # ---- the feed itself --------------------------------------------------
        feeds = int(body.get("feeds", 0))
        if feeds < 0:
            self._json_err(400, "feeds must be >= 0"); return
        wd = str(getattr(srv, "world_drive", "reactive"))
        if body.get("reactive"):
            # a reactive beat (REACTIVE world-drive): one scene following the
            # protagonist's move (the chat path posts the player's message as
            # `prompt`; an empty prompt = a STEP continue-beat).
            if not getattr(srv, "system_prompt", None):
                self._json_err(400, "/feed needs a --system premise"); return
            stats = _reactive_beat(srv, mem, str(body.get("prompt", "")))
        elif feeds == 0:
            stats = _feed_snapshot(srv, mem)            # dial-only persistence, no LLM call
        else:
            if not getattr(srv, "system_prompt", None):
                self._json_err(400, "/feed needs a --system premise (the world it tells "
                                     "stories of)"); return
            if wd == "explore":
                # EXPLORE: no feeding at all -- the world grows only from the
                # player's chat (walk_pov). STEP is a no-op here.
                stats = _feed_snapshot(srv, mem)
            elif wd == "reactive":
                # REACTIVE: a STEP is a continue-beat from the POV's situation.
                stats = _reactive_beat(srv, mem, "")
            else:                                       # pov-follow: autonomous chronicle
                stats = _run_feeds(srv, mem, feeds)
        out = json.dumps(stats).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

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
            if isinstance(body.get("backend"), str) and body["backend"].strip():
                b = body["backend"].strip().rstrip("/")
                if not (b.startswith("http://") or b.startswith("https://")):
                    self._json_err(400, "backend must be an http(s) URL"); return
                srv.backend = b
            if isinstance(body.get("story_model"), str) and body["story_model"].strip():
                srv.warmup_model = body["story_model"].strip()
            # world drive: reactive (default) / pov-follow / explore -- how the world's
            # self-writing relates to the explorer (see _reactive_beat + the consolidator)
            if isinstance(body.get("world_drive"), str) and body["world_drive"].strip():
                wd = body["world_drive"].strip().lower()
                if wd in ("reactive", "pov-follow", "explore"):
                    srv.world_drive = wd
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
                              "world_drive": getattr(srv, "world_drive", "reactive"),
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
                          "world_drive": getattr(srv, "world_drive", "reactive"),
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
        consolidating = bool(getattr(self.server, "consolidating", False))
        consol = getattr(self.server, "consolidation_stats", {})
        cache_depth = len(getattr(self.server, "story_cache", []))
        cl = cells_rank(mem.store)
        ec = _entropy_count(mem.store)
        tail, n_verd = self._audit_tail(24)
        out = json.dumps({"n": n, "n_safe": n_safe,
                          "free_energy": float(free_energy(mem.store)) if n >= 2 else 0.0,
                          "theoretical_max": theo,
                          "entropy_ceiling": round(ceiling, 4),
                          "entropy_cap": max(0, int(round(ceiling * theo))),
                          "temperature": round(temperature, 3),
                          "story_mode": story_mode,
                          "feed_rate": round(feed_rate, 3),
                          "cpu_target": round(cpu_target, 3),
                          "consolidating": consolidating,
                          "consolidation": consol,
                          "cache_depth": cache_depth,
                          "cells_level": round(float(cl), 2),
                          "entropy_count": ec,
                          "exemplars": pts,
                          "cast": cast_view,
                          "pov_id": mem.cast.pov_id,
                          "backend": str(getattr(self.server, "backend", "")),
                          "story_model": getattr(self.server, "warmup_model", None) or "",
                          "has_token": bool(getattr(self.server, "ollama_token", None)),
                          "cycle_speed": float(getattr(self.server, "cycle_speed", 1.0) or 1.0),
                          "initial_burst": int(getattr(self.server, "initial_burst", 0)),
                          "trained": not bool(getattr(self.server, "initial_burst", 0)),
                          "world_drive": str(getattr(self.server, "world_drive", "reactive")),
                          "beat_author": str(getattr(self.server, "beat_author", "llm")),
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
                    help="run N curiosity-driven cells in the background that pull real user turns "
                         "from a shared buffer and grow per-cell fields (observer mode, never author "
                         "a response). 0 = off (default): the live single-Membrane path is unchanged "
                         "except one cheap guard per request. Cells persist across boots (fresh=False) "
                         "and are read-only on the buffer (the feeding boundary).")
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
    ap.add_argument("--entropy-ceiling", type=float, default=0.01, metavar="FRAC",
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
    # When --cells is off (default), buf/pool stay None and the live single-Membrane
    # path pays only one ``if srv.buffer is not None`` guard per request (never taken).
    # When on, real user turns are deposited into a shared thread-safe buffer and N
    # curiosity-driven cells pull from a read-only view in daemon threads, growing
    # per-cell fields. Cells never serve live requests (observer mode) and never feed
    # back (one-way feed). The gate/decide/replay path (runtime.py/gate.py) is untouched.
    pool = None
    buf = None
    if args.cells > 0:
        buf = SituationBuffer()
        view = BufferView(buf)
        cells = []
        for i in range(args.cells):
            cells.append(Cell(
                tag=f"cell-{i}",
                responder=StubResponder(tag=f"cell-{i}"),
                field_path=f"membrane-field-cell-{i}.json",
                audit_path=f"membrane-audit-cell-{i}.jsonl",
                sim_min=args.sim_min,
                consensus_min=args.consensus_min,
                dedup_sim=args.dedup_sim,
                observer=True,            # the never-authors guardrail -- do NOT flip
                vague_filter=False,        # isolate curiosity+veil (the experiment config)
                variety_gate=False,
                variety_temp=False,
                nudge_sigma=0.25,          # the veil: cells diverge (experiment-proven)
                nudge_seed=i + 1,          # per-cell seed -> different manifolds -> specialization
                curiosity_on=True,
                curiosity_min=CURIOSITY_MIN,
                fresh=False,              # persist across boots (load existing field, keep growing)
            ))
        pool = CellPool(cells, view, interval=2.0)
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
    srv.backend = backend.rstrip("/")
    srv.buffer = buf          # None when OFF (the off sentinel the deposit guard checks)
    srv.cell_pool = pool      # None when OFF (nothing for the shutdown finally to stop)
    srv.viewer = args.viewer  # off by default; /viewer + /state are read-only
    srv._viewer_html = None   # lazy-loaded + cached on first GET /viewer
    srv._v_basis = None       # PCA basis, fit once for a stable 3D cloud
    srv.system_prompt = args.system   # opt-in world premise (--system); injected into forwards
    srv.worlds_dir = args.worlds_dir  # where the viewer's save/load/new-world buttons keep slots
    srv.warmup_model = args.warmup_model or args.judge_model   # the one warm-up palette call
    srv.ollama_token = None                                    # optional bearer; in-memory only, never persisted
    srv.world_drive = "reactive"                               # default: the world writes a beat after each move
    srv.beat_author = str(getattr(args, "beat_author", "llm"))  # llm | cells (who authors the autonomous chronicle)
    # apply persisted settings.json (backend + story model + world drive + beat author)
    # over the CLI defaults; the token is in-memory only, so it starts cleared each boot.
    # Then sync the shared embedder + responder to the resolved backend so /api/embed +
    # /api/chat agree (also fixes the old latent mismatch where the embedder hardcoded
    # localhost:11434 regardless of --backend).
    _persisted = _load_settings(srv)
    if _persisted.get("backend"):
        srv.backend = _persisted["backend"]
    if _persisted.get("story_model"):
        srv.warmup_model = _persisted["story_model"]
    if _persisted.get("world_drive") in ("reactive", "pov-follow", "explore"):
        srv.world_drive = _persisted["world_drive"]
    if _persisted.get("beat_author") in ("llm", "cells"):
        srv.beat_author = _persisted["beat_author"]
    _sync_ollama_clients(srv)
    srv.warmup_seed = args.warmup_seed
    srv.palette = srv_palette        # cached palette (built at startup warm-up); the no-model /live loop reuses it
    srv.entropy_ceiling = max(0.0, min(1.0, args.entropy_ceiling))   # inert for growth now (nudging removed); kept for the viewer's variety display
    srv.story_temperature = 0.7                                      # FIXED for a cohesive chronicle (no longer a dial)
    srv.story_mode = "story"                                          # 'story' = continuing chronicle; 'variety' = random vignettes
    srv.story_history = []                                            # the rolling chronicle (last 8 scenes fed; seeds the next chapter)
    srv.story_cache = []                                             # the genesis cache: pending chronicle scenes (one LLM chapter -> ~8 feeds)
    srv.cache_depth = 0
    srv.feed_lock = threading.Lock()                                   # serializes feeders (HTTP /feed + the consolidator)
    srv.feed_rate = max(0.0, min(32.0, float(getattr(args, "feed_rate", 1.0))))  # feeds/cycle (continuous; the ENTROPY dial)
    srv.cpu_target = max(0.0, min(1.0, float(getattr(args, "cpu_target", 0.2))))  # target CPU fraction for digestion
    srv.consolidating = bool(getattr(args, "consolidate", False))       # arm the world's own clock at boot
    srv.cycle_speed = 1.0                                               # world-clock multiplier (1x/2x/4x from the viewer)
    srv.initial_burst = int(getattr(args, "initial_burst", 0))         # auto-pause after N post-first-chapter cycles
    srv._initial_burst_arg = int(getattr(args, "initial_burst", 0))   # re-arm value for NEW WORLD / LOAD
    srv.consolidation_stats = _fresh_consolidation_stats(srv)
    from .consolidate import Consolidator
    srv.consolidator = Consolidator(srv)
    srv.consolidator.start()
    srv.anthropic = anthropic         # which system-prompt shape _apply_system uses (msg vs field)
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
    cells_s = (f"ON ({args.cells} cells, sigma=0.25, curiosity_min={CURIOSITY_MIN}, "
               f"observer/fresh-persist/teed; per-cell field-cell-<i>; pool cadence 2s)"
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