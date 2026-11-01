"""THE SEAT: what one running membrane holds besides its field -- where it
writes its audit, the embedder the boot ladder resolved, the locks and the
tool-call pairing -- and the TURN FRONT, the query side of every chat turn:

    turn(body) -> Turn(query, query_raw, vec, binding, binding_failed, drift)

  query      the latest user message; a pronoun resolved against the last
             few user messages is rewritten with its referent ('turn it
             off' -> 'turn the lamp off') -- the vector must carry the
             referent the words leave out
  vec        the embedding of '[<UTC minute>Z] ' + query: content plus era
             (the router, momentum and the boards read it; the entity field
             embeds its own sentences bare)
  drift      cos to the previous turn's vector (telemetry)

The entity field lives beside it (srv.efield); the seat keeps no memory of
its own."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import binding as _binding


def content_text(content) -> str:
    """A message's text: a string, or the text blocks of a content list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def last_user(body: dict) -> str | None:
    for m in reversed(body.get("messages") or []):
        if m.get("role") != "user":
            continue
        text = content_text(m.get("content"))
        if text and text.strip():
            return text
    return None


QVEC_REUSE_S = 300.0


def time_prefix() -> str:
    """'[2026-09-01T21:34Z] ': the era rides the embed input only."""
    from datetime import datetime, timezone
    return f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}Z] "


@dataclass
class Turn:
    query: str
    query_raw: str
    vec: np.ndarray | None
    binding: dict | None = None
    binding_failed: bool = False
    drift: float | None = None


class Seat:
    def __init__(self, field_path: str | Path, audit_path: str | Path, embedder=None,
                 pronoun_bind: bool = True):
        self.field_path = Path(field_path)
        self.audit_path = Path(audit_path)
        self.embedder = embedder             # resolved by the boot ladder
        self.pronoun_bind = bool(pronoun_bind)
        self.mut_lock = threading.RLock()
        self.server_ref = None
        self._pending_tools: dict = {}       # call id -> the call (paired with its result)
        self._bind_ctx: deque = deque(maxlen=4)
        self._last_vec = None
        self.vsa_lane = None
        self.gate_notes: list = []

    def turn(self, body: dict) -> Turn:
        """The query side of a turn. vec is None for an empty query."""
        query = last_user(body) or ""
        raw = query
        if not query.strip():
            return Turn("", "", None)
        bind, failed = None, False
        # a chat turn the conversation graph looked at binds by ITS reading (the registers);
        # nothing found there is no rewrite. Other clients keep the lexical binder (2026-11-01:
        # it rewrote Memo's "can you benchmark it?" as "... benchmark explain?")
        graph = None
        if self.pronoun_bind and body.get("_turn_sid"):
            try:
                from . import convgraph as _CG
                graph = _CG.capture_binding(self.server_ref, str(body.get("_turn_sid")))
            except Exception:
                graph = None
        if self.pronoun_bind and graph is not None and _binding.has_pronoun(query):
            bind = graph or None
            if bind:
                query = _binding.substitute(query, bind)
            else:
                failed = True
        elif self.pronoun_bind and _binding.has_pronoun(query):
            bind = _binding.resolve(query, list(reversed(self._bind_ctx)))
            if bind is not None:
                query = _binding.substitute(query, bind)
            else:
                failed = True
        vec = None
        if self.embedder is not None:
            # THE SAME QUESTION, ONE EMBEDDING (2026-10-10: tool rounds re-embedded
            # it -- 8-9 s each under auto-chat's load on the Ollama box): reused
            # for QVEC_REUSE_S; the minute stamp inside drifts at most that much
            import time as _time
            cache = self.__dict__.setdefault("_qvec_cache", {})
            hit = cache.get(query)
            if hit is not None and _time.time() - hit[0] < QVEC_REUSE_S:
                vec = hit[1]
            else:
                vec = np.asarray(self.embedder.embed([time_prefix() + query])[0], dtype=float)
                cache[query] = (_time.time(), vec)
                while len(cache) > 64:
                    cache.pop(next(iter(cache)))
        drift = None
        if vec is not None and self._last_vec is not None:
            n = float(np.linalg.norm(vec) * np.linalg.norm(self._last_vec))
            drift = float(vec @ self._last_vec / n) if n > 1e-12 else None
        if vec is not None:
            self._last_vec = vec
        return Turn(query, raw, vec, bind, failed, drift)

    def remember(self, query_raw: str) -> None:
        """A real user turn joins the pronoun context."""
        text = _binding.strip_reminders(query_raw)
        if text and text.strip():
            self._bind_ctx.append(text)
