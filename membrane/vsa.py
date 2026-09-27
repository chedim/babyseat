"""THE VSA TOKEN LANE (the rosetta transposition, default-on, lazy
embedder-seeded): hypervector token geometry BESIDE the mxbai cosine
lane. Each turn's text becomes a running hypervector state -- the
"massage" -- computed in O(1) numpy per token, stored on the exemplar
(extra["vsa"]) and answered by Hamming distance (dual-lane recall);
its divergence from recent trajectories is an ADVISORY surprise signal
to the gate.

THE ISOMETRY (why pre-seeding works): the codebook is not random --
it is the embedder's geometry binarized. V_tok = sign(R @ e_tok) with R
a fixed seeded projection: Hamming distance in the codebook approximates
cosine in embedding space, so cat/dog land nearby from day one and the
binding/bundling algebra composes on top. The same (model, dim, seed)
on every seat yields the same codebook, so states in the shared
substrate stay comparable cross-origin (the id stamp guards it).

THE RECURRENCE (the "massage"): each token binds its hypervector to a
recency-anchored rotating role (permutation of the slot vector by
distance-from-turn-end) and the running state is the WEIGHTED MAJORITY
of the bindings -- later tokens weigh more (exponential decay), so the
state is order-sensitive (a permuted stream lands elsewhere) yet
tail-dominant (the last ~128 tokens rule; crosstalk is bounded by the
window, the decay is the forgetting).

THE LANE DISCIPLINE (membrane's covenants, carried):
- supplement, never replace -- mxbai cosine stays the primary lane (the
  E4 lesson: projections floor rank; a quantized geometry must not rule
  the gate).
- degrade, never fail -- no embedder -> deterministic hash vectors (no
  pretrained semantics, the lane runs weaker); any lane error never
  touches the turn.
- derived structure: the field cell remains the truth (the same
  relation TokenTimeline holds -- ONE capture, TWO readouts: the
  discrete transition table and this continuous geometry).

Kill switch: MEMBRANE_VSA=0 disables the lane end to end. Lab field
paths never persist a codebook (the isolation guard, _is_lab_field_path).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np

# ---- knobs -----------------------------------------------------------------
VSA_SEED = 5                  # fixed; changing it forks every codebook id
DEFAULT_DIM = 8192            # 1 KB packed per state
_WINDOW = 128                 # tokens that shape a state (the forgetting)
_ROLES = 16                   # rotating recency roles (mod _ROLES)
_W_BAG = 0.5                  # the bag component's weight (paraphrase lane)
_RECALL_BAR = 0.55            # Hamming-sim floor for the lane's hits

# the advisory bars (the surprise signal; the gate reads the NUMBER,
# the verdict never flips on this lane)
SURPRISE_NEAR = 0.10          # <=: the shape was just seen (not surprising)


def enabled() -> bool:
    """The lane's kill switch: MEMBRANE_VSA=0 (or "off"/"false") -> off."""
    return os.environ.get("MEMBRANE_VSA", "1") not in ("0", "false", "off")


def codebook_id(model: str, dim: int, seed: int, scheme: int) -> str:
    """The codebook's identity: recall compares ONLY same-id states (an
    embedder/model/dimension change forks the geometry -- old rows ride
    read-only until a re-encode pass rebuilds them)."""
    h = hashlib.sha256(f"vsa1|{model}|{dim}|{seed}|{scheme}".encode())
    return h.hexdigest()[:16]


def shared_tokenize(text: str) -> list[str]:
    """THE SHARED TOKENIZER (the one-capture covenant): the exact
    tokenizer TokenTimeline.observe already runs -- both structures see
    the same stream, one pass, two readouts."""
    from .token_timeline import tokenize
    return tokenize(text)


# ---- the algebra (bipolar {0,1} vectors; xor-bind, weighted-majority) ------
def permute(state: np.ndarray, k: int = 1) -> np.ndarray:
    """The coordinate rotation (rosetta's depth/position shift)."""
    return np.roll(state, k)


def bind(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """XOR binding: the algebra's product (its own inverse -- unbind is
    a re-bind), O(1) numpy."""
    return np.bitwise_xor(a, b)


def weighted_majority(items: list[np.ndarray],
                      weights: list[float]) -> np.ndarray:
    """The bundling reduction with decay: each item votes its bipolar
    sign (+1/-1) times its recency weight; a bit is 1 when the weighted
    vote sum is >= 0."""
    acc = np.zeros(items[0].shape, dtype=np.float32)
    for v, w in zip(items, weights):
        acc += np.where(v > 0, 1.0, -1.0) * float(w)
    return (acc >= 0).astype(np.uint8)


def pack_state(state: np.ndarray) -> str:
    """Bool/uint8 array -> base64 of the packed bits (the wire shape:
    ~1 KB at D=8192; strings survive JSON/couchbase untouched)."""
    import base64
    return base64.b64encode(np.packbits(state.astype(np.uint8)).tobytes()) \
        .decode("ascii")


def unpack_state(packed: str) -> np.ndarray:
    import base64
    raw = base64.b64decode(packed)
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))
    return bits.astype(np.uint8)


def hamming(a: np.ndarray, b: np.ndarray) -> float:
    """Fraction of differing bits (0.0 identical, ~0.5 unrelated)."""
    return float(np.count_nonzero(np.asarray(a) != np.asarray(b))) \
        / max(len(a), 1)


def hash_token_vec(tok: str, dim: int) -> np.ndarray:
    """The no-embedder fallback: a deterministic hash bit-pattern (no
    pretrained semantics -- the lane still binds, just weaker recall)."""
    seed = int(hashlib.sha256(tok.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    return (rng.random(dim) >= 0.5).astype(np.uint8)


# ---- the codebook -----------------------------------------------------------
class VSACodebook:
    """Token -> bipolar hypervector, lazily embedder-seeded.

    First sighting of a token: one embed call of the BARE token string
    through the store's embedder (the same-turn LRU rides), quantized
    via sign(R @ e) with R the fixed seeded projection. Persisted at
    <data-dir>/vsa-codebook.json so boots skip the fills; the id stamp
    keeps cross-version coherence honest."""

    FORMAT = "bipolar-sign-proj-1"
    INPUT_DIM = 1024            # the projection's input width (mxbai)

    def __init__(self, embedder=None, dim: int = DEFAULT_DIM,
                 seed: int = VSA_SEED, model: str | None = None,
                 data_dir: str | Path | None = None):
        self.embedder = embedder
        self.dim = int(dim)
        self.seed = int(seed)
        self.model = model or (getattr(embedder, "model", "") or "hash")
        # sha256, never hash(): str hashes are salted PER PROCESS, so the
        # old scheme forked the id (and every role vector) on each boot --
        # the persisted codebook never matched, no two seats agreed
        scheme = int(hashlib.sha256(self.FORMAT.encode()).hexdigest()[:8],
                     16) % 10_000
        self.id = codebook_id(self.model, self.dim, self.seed, scheme)
        self._book: dict[str, np.ndarray] = {}
        self._proj = self._matrix()
        self._role_cache: dict[str, np.ndarray] = {}
        self._data_dir = Path(data_dir) if data_dir else None

    def _matrix(self) -> np.ndarray:
        """The fixed projection (one per dim; the same matrix every
        call -- the _sketch_matrix pattern, seeded deterministically)."""
        rng = np.random.default_rng(self.seed)
        return rng.standard_normal((self.dim, self.INPUT_DIM))

    def token_vec(self, tok: str) -> np.ndarray:
        """One token -> its bipolar hypervector (lazy, cached)."""
        v = self._book.get(tok)
        if v is None:
            v = self._make(tok)
            self._book[tok] = v
        return v

    def _make(self, tok: str) -> np.ndarray:
        if self.embedder is not None:
            try:
                e = np.asarray(self.embedder.embed([tok])[0], dtype=float)
                if e.size and float(np.linalg.norm(e)) > 1e-12:
                    if e.size < self.INPUT_DIM:
                        e = np.pad(e, (0, self.INPUT_DIM - e.size))
                    return ((self._proj @ e[:self.INPUT_DIM]) >= 0) \
                        .astype(np.uint8)
            except Exception:
                pass                  # degrade to the hash lane
        return hash_token_vec(tok, self.dim)

    def role_vec(self, role: str) -> np.ndarray:
        """Structural role vectors: seeded-random (structural, not
        semantic -- the rosetta design carries over unchanged)."""
        r = self._role_cache.get(role)
        if r is None:
            rng = np.random.default_rng(
                int(hashlib.sha256(f"role|{self.id}|{role}".encode())
                    .hexdigest()[:8], 16))
            r = (rng.random(self.dim) >= 0.5).astype(np.uint8)
            self._role_cache[role] = r
        return r

    def persist(self, data_dir: str | Path | None = None) -> None:
        """Write the codebook next to the field (a boot skips the
        fills); lab paths skip persistence (the isolation guard)."""
        d = Path(data_dir) if data_dir else self._data_dir
        if d is None:
            return
        try:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "vsa-codebook.tmp"
            tmp.write_text(json.dumps({
                "id": self.id, "model": self.model, "dim": self.dim,
                "seed": self.seed, "format": self.FORMAT,
                "tokens": {t: pack_state(v) for t, v in self._book.items()}}))
            os.replace(tmp, d / "vsa-codebook.json")
        except Exception:
            pass                    # persistence is best-effort, never fatal


# ---- the lane ---------------------------------------------------------------
class VSALane:
    """One membrane's token-geometry lane: encode turns into states,
    recall by Hamming, read surprise advisory-side."""

    def __init__(self, codebook: VSACodebook):
        self.codebook = codebook
        self.slot = codebook.role_vec("slot")
        self._history: list[np.ndarray] = []     # recent final states

    @classmethod
    def make(cls, embedder=None, dim: int = DEFAULT_DIM,
             data_dir: str | Path | None = None) -> "VSALane | None":
        """The boot seam: None when the kill switch is up (the caller
        runs lane-less -- zero behaviour change)."""
        if not enabled():
            return None
        cb = VSACodebook(embedder=embedder, dim=dim, data_dir=data_dir)
        lane = cls(cb)
        lane._load(data_dir)
        return lane

    def _load(self, data_dir: str | Path | None) -> None:
        """Boot hydration of the persisted codebook (same id only)."""
        d = Path(data_dir) if data_dir else self.codebook._data_dir
        if d is None:
            return
        try:
            p = Path(d) / "vsa-codebook.json"
            if not p.exists():
                return
            d_json = json.loads(p.read_text())
            if d_json.get("id") != self.codebook.id:
                return                    # a forked geometry: rebuild lazy
            for t, packed in (d_json.get("tokens") or {}).items():
                self.codebook._book[t] = unpack_state(packed)
        except Exception:
            pass

    # -- the recurrence: recency-weighted positional majority -----------------
    def encode(self, text: str, checkpoints: int = 0
               ) -> np.ndarray | None:
        """Text -> the final state (the turn after all tokens massaged
        it). THREE components, one weighted majority (the measured
        shape; every bar in tests/test_vsa.py is calibrated to it):

        - BAG: each distinct token once (paraphrase recall -- the
          isometry's cosine preservation).
        - RECENCY ROLES: each token bound to the slot permuted by its
          distance from the END (order; the tail weighs most -- decay
          is the forgetting).
        - ADJACENT PAIRS: token_i bound to token_i+1 at a distinct
          offset (the local order structure; a reordered stream breaks
          every pair, so the state moves).

        Weights: bag 0.5/sqrt(count), unigrams and pairs decayed by
        0.75^distance over the last `_WINDOW` tokens. None for empty
        text (the row rides cosine-only)."""
        text = (text or "").strip()
        if not text:
            return None
        toks = shared_tokenize(text)
        if not toks:
            return None
        toks = toks[-_WINDOW:]
        n = len(toks)
        items: list[np.ndarray] = []
        weights: list[float] = []
        counts: dict[str, int] = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        for t, c in counts.items():
            items.append(self.codebook.token_vec(t))
            weights.append(_W_BAG * float(c) ** 0.5)
        decay = 0.75
        for i, t in enumerate(toks):
            d = n - 1 - i                     # distance from the end
            items.append(bind(self.codebook.token_vec(t),
                              permute(self.slot, d % 16)))
            weights.append(decay ** d)
            if i + 1 < n:
                items.append(bind(self.codebook.token_vec(t),
                                  permute(self.codebook.token_vec(toks[i + 1]),
                                          7)))
                weights.append((decay ** d) * 1.0)
        state = self._state(items, weights)
        self._remember(state)
        return state

    @staticmethod
    def _state(items, weights) -> np.ndarray:
        return weighted_majority(items, weights)

    def _remember(self, state: np.ndarray) -> None:
        self._history.append(state)
        if len(self._history) > 64:
            self._history.pop(0)

    def __repr__(self) -> str:
        return f"VSALane(cb={self.codebook.id[:8]}, hist={len(self._history)})"

    # -- recall ----------------------------------------------------------------
    def observe(self, text: str) -> np.ndarray | None:
        """Encode + keep in the recent ring (the surprise baseline)."""
        return self.encode(text)

    def hits(self, store_or_pairs, query: str, k: int = 5
             ) -> list[tuple[int, float]]:
        """The lane's answer: [(index_or_key, similarity)] over rows that
        carry a same-codebook state. Accepts an ExemplarStore (delegates
        to its vsa_top_k) or a plain [(key, vsa_dict)] iterable (the
        isolation-friendly path). Rows without vsa data are invisible
        here -- cosine rules them."""
        q = self.encode(query)
        if q is None:
            return []
        if hasattr(store_or_pairs, "vsa_top_k"):
            return store_or_pairs.vsa_top_k(self, q, k=k) or []
        out = []
        for key, vsa in (store_or_pairs if hasattr(store_or_pairs,
                                                    "__iter__") else []):
            if not isinstance(vsa, dict) or vsa.get("cb") != self.codebook.id:
                continue                      # foreign codebook: never compared
            try:
                s = unpack_state(vsa["s"])
            except Exception:
                continue
            sim = 1.0 - hamming(q, s)
            if sim >= _RECALL_BAR:
                out.append((key, sim))
        out.sort(key=lambda t: -t[1])
        return out[:k]

    # -- the advisory signal -----------------------------------------------------
    def surprise(self, text: str) -> float | None:
        """How far the live trajectory diverges from the recent ones
        (0.0 = a shape just seen). Advisory ONLY: the gate scales its
        existing dials by this number; a verdict never flips on it.
        None when the lane has no earlier history (honest cold start)."""
        q = self.encode(text)
        if q is None or len(self._history) < 2:
            return None
        best = min(hamming(q, h) for h in self._history[:-1])
        return round(min(1.0, max(0.0, best)), 4)