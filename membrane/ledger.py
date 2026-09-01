"""W4b: the user ledger -- the operator's verbatim decision log.

The highest-value memories are the operator's OWN words: explicit decisions,
corrections, preferences. The field paraphrases and compresses; the ledger
keeps them VERBATIM. Properties:

- append-only JSONL next to the field (``<field>.ledger.jsonl``), one line
  per decision: ``{"ts": iso, "text": str}``.
- MODEL-UNWRITABLE: only the operator's learn lane (a USER turn hitting the
  pre-gate learn executor) appends. Agent notes, MCP writes, and captures
  never touch it. Readable by anyone.
- USER-EDITABLE on disk: a plain file the operator may prune by hand; the
  reader re-reads on mtime change (a cached parse + vector cache keyed by
  (mtime, size)).
- Recalled VERBATIM when near the current turn (cosine over the embedder),
  marked as operator-authored so the model treats it as law, not recall.

Corrupt lines are skipped, never fatal (the operator may be mid-edit).
"""

import json
import os
import time
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UserLedger:
    """Append-only verbatim log of operator decisions, recallable by topic."""

    MAX_ENTRIES = 200          # the recall surface reads at most this many
    MAX_TEXT = 2000            # one decision, not a paste

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._cache_key = None
        self._entries: list[dict] = []          # newest last

    # ---- disk ----
    def _read(self) -> list[dict]:
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        if key == self._cache_key:
            return self._entries
        out: list[dict] = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                        t = str(rec.get("text") or "").strip()
                        if t:
                            out.append({"ts": str(rec.get("ts") or ""),
                                        "text": t})
                    except (json.JSONDecodeError, ValueError):
                        continue        # mid-edit / hand-pruned line
        except OSError:
            return self._entries
        out = out[-self.MAX_ENTRIES:]
        self._cache_key, self._entries = key, out
        return out

    def append(self, text: str) -> bool:
        """One operator decision, verbatim (only edge whitespace trimmed --
        the operator's exact words are the point). Returns True when written."""
        t = (text or "").strip()
        if not t:
            return False
        t = t[: self.MAX_TEXT]
        rec = {"ts": _now_iso(), "text": t}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            return False
        self._cache_key = None                # force re-read on next use
        return True

    # ---- recall ----
    def _vecs(self, embedder):
        entries = self._read()
        cache = getattr(self, "_vec_cache", None)
        key = (self._cache_key, len(entries))
        if cache is not None and cache.get("key") == key:
            return entries, cache["vecs"]
        vecs = None
        if entries and embedder is not None:
            try:
                vecs = embedder.embed([e["text"] for e in entries])
            except Exception:
                vecs = None
        self._vec_cache = {"key": key, "vecs": vecs}
        return entries, vecs

    def top_k(self, q, embedder, k: int = 2, sim_min: float = 0.55):
        """Ledger lines near the current turn, VERBATIM ([(text, sim)]).
        Read-only; every failure degrades to no help."""
        if q is None:
            return []
        import numpy as np
        entries, vecs = self._vecs(embedder)
        if not entries or vecs is None or len(vecs) != len(entries):
            return []
        qn = np.asarray(q, dtype=float)
        n = float(np.linalg.norm(qn))
        if n < 1e-12:
            return []
        qn = qn / n
        out = []
        for e, v in zip(entries, vecs):
            v = np.asarray(v, dtype=float)
            vn = float(np.linalg.norm(v))
            if vn < 1e-12:
                continue
            sim = float(qn @ (v / vn))
            if sim >= sim_min:
                out.append((e, sim))
        out.sort(key=lambda t: -t[1])
        return out[:k]

    def block(self, q, embedder, sim_min: float = 0.55) -> str | None:
        """The marked verbatim block for an outbound request (or None)."""
        hits = self.top_k(q, embedder, sim_min=sim_min)
        if not hits:
            return None
        lines = ["[membrane-ledger] the OPERATOR's verbatim decisions "
                 "(written by the operator, not the model; law, not recall):"]
        for e, sim in hits:
            lines.append(f"- ({sim:.2f}) {e['text']}")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._read())