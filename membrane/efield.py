"""THE ENTITY FIELD: every word, phrase and specific identifier is an
entity; events move them; the field is what the entities have become.

Per entity (EntityState):
  seed    the bare term's embedding -- its everyday meaning
  state   seed moved by LOCAL deltas: for each mention sentence x,
          state <- unit(state + GAIN * (x - state))   (E18/E19: tracking
          the mention SENTENCE, not the whole message, keeps entities
          distinguishable -- 68% vs 59% top-1)
  mean    the running mean of its mention sentences (the settled sense;
          `state` is the current sense, ~the last 2-3 mentions)
  rsum    sum of unit residuals r = unit(x - background[stream]):
          coherence = (|rsum|^2 - n) / (n (n-1)) is exactly the mean
          pairwise cosine of the residuals (E23) -- it separates words
          that MEAN something here from filler
  mass    accreted agreeing evidence (E21: grows with mentions; it is
          importance / centrality, not entity-ness). THE METABOLISM (the
          MEMBRANE_MASS_METABOLISM=1 env flag, default OFF): read-time on
          top of the raw counter, mass_eff() -- a 14-day half-life from
          the state's last ACCRETING touch, unresolved conflicts damp it.
          Dead names sink, re-mentioned ones rise. Nothing stored changes;
          flag off = the raw counter (the deploy-day no-op)
Words and phrases wait in a NURSERY until K_MAT distinct sentences
(buffered mentions replay in order on promotion); specific identifiers
materialize at their first mention. Boilerplate sentences (per-origin
frequency >= BOILER_MIN) move nothing.

The message residual (message minus the best fit of its entities'
states) is NOT split among entities: its size is the turn's SURPRISE and
its direction feeds the RAM-only session context (`now`).

Events are the source of truth: `rebuild()` re-applies them in seq order
and reproduces every state without re-embedding."""

from __future__ import annotations

import os
import functools
import socket
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from . import eids as E
from . import streams as ST
from . import vsa
from .efstore import h16, o8, pack_f16, pack_f32, unpack_f16

# THE DUST CAP (the 'w:showing/w:stay' birth flood, the CPU-bomb's
# second half): a word/phrase entity's STATE is only born when the
# surface carries >= WORD_MIN_LEN letters. The intent: STOPWORD DUST
# ('the', 'was', 'showing', 'stay') never gets a state; RARE SHORT
# TERMS ('peers', 5 letters, a membrane noun) survive -- the common
# words are already caught by the BOILERPLATE frequency gate
# (per-origin frequency >= BOILER_MIN moves nothing). Words of 1-3
# letters are never subjects; 4+ letters live if uncommon.
WORD_MIN_LEN = int(os.environ.get("MEMBRANE_WORD_MIN_LEN", 4))
EF_SCHEMA = 1


def _dust_word(eid: str, surface: str) -> bool:
    """True = the word/phrase entity is DUST (never gets a state):
    too short to be a term."""
    if eid.split(":", 1)[0] not in ("w", "p"):
        return False
    if " " in (surface or ""):          # a phrase: real
        return False
    return len(surface or "") < WORD_MIN_LEN


GAIN = 0.5
K_MAT = 3
# THE WRITE-AMPLIFICATION FIX (the 'membranes are stuck again'
# exhibit): words DO materialize at K_MAT (the recall contract keeps
# its entity-states lane) -- but they carry NO per-event delta rows:
# the measured 6344 live w: entities x ~8 mentions/event = 216k delta
# rows, and the consumers that scan deltas (the night drift's
# _cell_points, refresh_activity, the diff) made every tick O(words x
# deltas) -- the seat burned a core flat. Word states still UPDATE
# per event (cheap), their evidence rides the EVENT-INDEX DELTA
# below (one row per event, kind 'e:' -- the sentence->event route
# past_answers/near_asks need). Specific kinds keep their deltas
# (the drift/diff work is FOR specific entities, few per event).
ACCRETE = 0.15
BOILER_MIN = 8
CTX_MIN_RESID = 0.2
NULL_COH_DEFAULT = (0.16, 0.024)        # E23's random-set coherence (mxbai)
RECENT = 8
DISCOURSE_ROLES = ("user", "model", "reasoning", "note")


def unit(v) -> np.ndarray:
    # a packed vector (f16:/f32: base64) or a decimal list -- same
    # shape-family as the store's rows (the sentence vecs are packed
    # now; a unit() on the raw string is the read-path's miss).
    from .efstore import unpack_f16
    v = unpack_f16(v)
    return v / (np.linalg.norm(v) + 1e-12)


def vec_list(v) -> list[float]:
    """An ANN-indexed vector as JSON floats, rounded to 6 decimals (float32-
    level precision, far below embedder noise): ~2.5x smaller documents."""
    return [round(float(x), 6) for x in v]


def at_stored(v) -> np.ndarray:
    from .efstore import unpack_f16
    return np.asarray(unpack_f16(v), dtype=float)


def f32(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float32).astype(np.float64)


def fit_weights(x, A):
    """Non-negative-clipped least squares of x on the rows of A (E18): the
    reconstruction AND its weights (the event's coordinates in entity terms)."""
    A = np.asarray(A, dtype=float)
    w, *_ = np.linalg.lstsq(A.T, x, rcond=None)
    w = np.clip(w, 0.0, None)
    if w.sum() <= 1e-12:
        w = np.ones(len(A)) / len(A)
    return A.T @ w, w


def fit(x, A) -> np.ndarray:
    """Non-negative-clipped least squares of x on the rows of A (E18)."""
    return fit_weights(x, A)[0]


def coherence_of(rsum, n: int) -> float:
    if n < 2:
        return 0.0
    return float((float(np.dot(rsum, rsum)) - n) / (n * (n - 1)))


# THE CITATION CARTEL (2026-10-08, the seat's own review): "if my own
# echo-mentions increment 'said', you get a feedback loop: I mention it ->
# it looks hot -> it stays in my tracks -> I mention it again". The model's
# echoes count apart and fade; the ranking and the banner read evidence.
ECHO_ROLES = ("model", "reasoning")
ECHO_HALF_LIFE_S = 6 * 3600.0

# THE FIELD'S METABOLISM (2026-10-13, docs/comparison.md §5, borrowed from
# holomem): mass fades on a half-life from the state's LAST ACCRETING touch,
# every accreting mention re-inflates it, an unresolved conflict damps the
# effective weight. Read-time only (echo's rule): nothing mutates, so a
# rebuild replays identically. OFF by default -- deploy day stays a provable
# no-op; the operator turns it on (env + restart, like the sibling knobs).
MASS_METABOLISM = os.environ.get("MEMBRANE_MASS_METABOLISM", "0") == "1"
MASS_HALF_LIFE_S = float(os.environ.get("MEMBRANE_MASS_HALF_LIFE_S")
                         or 14 * 24 * 3600.0)
MASS_CONFLICT_DAMP = 0.5          # damp = 1 / (1 + DAMP * len(conflicts))


def echo_now(st, now: float) -> float:
    """The model-echo count at `now` (half-life ECHO_HALF_LIFE_S)."""
    e = float(getattr(st, "echo", 0.0) or 0.0)
    ts = getattr(st, "echo_ts", None)
    if not e or ts is None:
        return e
    dt = max(0.0, float(now) - float(ts))
    return e * 0.5 ** (dt / ECHO_HALF_LIFE_S)


def mass_eff(st, now: float) -> float:
    """The state's (or state doc's) effective mass at `now`: raw accr decayed
    on a half-life from its last accreting touch, damped by unresolved
    conflicts. Never above accr, never negative. Reads both EntityState
    objects and backend doc dicts; read-time only -- accr stays raw."""
    accr = int((st.get("accr") or 0) if isinstance(st, dict) else st.accr or 0)
    if not MASS_METABOLISM or accr <= 0:
        return float(accr)
    ts = (st.get("last_accrue_ts") if isinstance(st, dict)
          else getattr(st, "last_accrue_ts", None))
    if ts is None:                      # a pre-metabolism snapshot: the last
        ts = (st.get("last_ts") if isinstance(st, dict)   # mention of any
              else getattr(st, "last_ts", None))          # kind is the best
    if ts is None:                      # a clock it never had: no decay yet
        return float(accr)
    dt = max(0.0, float(now) - float(ts))
    conflicts = ((st.get("conflicts") if isinstance(st, dict)
                  else getattr(st, "conflicts", None)) or [])
    return max(0.0, min(accr * 0.5 ** (dt / MASS_HALF_LIFE_S), float(accr))) \
        / (1.0 + MASS_CONFLICT_DAMP * len(conflicts))


def adopt_legacy(st, ts=None) -> None:
    """A PRE-SPLIT state (stream counts, no role counts) converts on its
    next mention: its old discourse -- possibly the model's own storm --
    becomes echo fading from its last-seen time; observation/code stay
    evidence. Free: the state is being rewritten for that mention anyway
    (a full relabel pass rewrote ~250MB of state rows into the journal)."""
    if st.n_by_role or not st.n_by_stream:
        return
    c = st.n_by_stream
    st.n_by_role = {k: v for k, v in c.items() if k in ("observation", "code")}
    st.n_by_role["unsplit"] = 0
    disc = int(c.get("discourse") or 0)
    if disc:
        st.echo = float(disc)
        st.echo_ts = last_seen(st) or ts


def note_mention(st, stream: str, role: str, ts, adopt: bool = True) -> None:
    """One mention, counted per role (discourse) or per stream (the rest);
    a model echo also feeds the decaying echo count. ``adopt=False``: the
    caller already converted a pre-split state BEFORE counting this mention
    in n_by_stream (_update) -- a brand-new state is never 'pre-split'."""
    if adopt:
        adopt_legacy(st, ts)
    key = role if (stream == "discourse" and role) else (stream or "?")
    st.n_by_role[key] = st.n_by_role.get(key, 0) + 1
    if stream == "discourse" and role in ECHO_ROLES:
        t = float(ts if ts is not None else time.time())
        st.echo = echo_now(st, t) + 1.0
        st.echo_ts = t


def event_ts(ts, seq):
    """THE FRESHNESS HOLES (2026-10-08): an event's time -- its own ts, else
    its seq (the per-origin MICROSECOND clock: the same value on every
    replay), else None."""
    if ts is not None:
        return ts
    try:
        # only a real microsecond clock (after 2001); a backfill's
        # synthetic seq (audit line * 16) carries no time
        return int(seq) / 1e6 if seq and int(seq) > 10 ** 15 else None
    except (TypeError, ValueError):
        return None


def last_seen(st):
    """A state's last-moved time (last_ts, else its last_pos seq)."""
    if getattr(st, "last_ts", None) is not None:
        return st.last_ts
    pos = getattr(st, "last_pos", None) or (-1, -1)
    return event_ts(None, pos[0] if pos else None)


def evidence_n(st) -> int:
    """The mentions that are NOT the model's own echo (legacy states with
    no per-role counts fall back to n: their split is unknown)."""
    roles = getattr(st, "n_by_role", None)
    if not roles:
        # pre-split: the old discourse may be the model's own storm -- it
        # is not evidence; observed + code are
        c = getattr(st, "n_by_stream", None) or {}
        return int((c.get("observation") or 0) + (c.get("code") or 0))
    return int(sum(v for k, v in roles.items() if k not in ECHO_ROLES))


@dataclass
class EntityState:
    eid: str
    kind: str
    surface: str = ""
    tier: str = "nursery"                      # nursery | live | dormant
    seed: np.ndarray | None = None
    state: np.ndarray | None = None
    mean: np.ndarray | None = None
    rsum: np.ndarray | None = None
    n: int = 0
    n_by_stream: dict = field(default_factory=dict)
    net_align: float = 0.0
    accr: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    last_accrue_ts: float | None = None        # the LAST ACCRETING touch (the
                                               # metabolism's clock; derived
                                               # exactly like accr on replay)
    last_pos: tuple = (-1, -1)                 # (seq, sentence index) applied
    recent: list = field(default_factory=list)
    pending: list = field(default_factory=list)  # nursery: [sid, seq, sidx, stream, ev, ts, role]
    distinct: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    activity: float | None = None
    # THE CITATION CARTEL'S CURE (2026-10-08): mentions per ROLE, and the
    # model's own echoes as a DECAYING count of their own (note_mention)
    n_by_role: dict = field(default_factory=dict)
    echo: float = 0.0
    echo_ts: float | None = None
    # THE PREVIOUS MENTION (the delta lane, stage 2a): the last mention
    # sentence, at its STORED precision -- the pbits are the move from it
    last_x: np.ndarray | None = None

    @property
    def coherence(self) -> float:
        return coherence_of(self.rsum, self.n) if self.rsum is not None else 0.0

    @property
    def mass(self) -> int:
        # the EFFECTIVE mass when the metabolism is on (the raw counter is
        # still .accr): the card, the birth ranking and the system card all
        # read this -- dead names sink, re-mentioned ones rise
        if MASS_METABOLISM:
            return int(round(mass_eff(self, time.time())))
        return self.accr

    def specialization(self, bg) -> float | None:
        if self.rsum is None or self.seed is None or self.n == 0:
            return None
        ref = self.seed - (bg if bg is not None else 0.0)
        return float(1.0 - unit(self.rsum) @ unit(ref))

    def to_doc(self, origin: str) -> dict:
        d = {"_scm": EF_SCHEMA, "_k": "ent", "eid": self.eid, "kind": self.kind,
             "surface": self.surface, "origin": origin, "tier": self.tier,
             "n": self.n, "n_by_stream": dict(self.n_by_stream),
             "net_align": self.net_align, "accr": self.accr,
             "coherence": self.coherence, "first_ts": self.first_ts,
             "last_ts": self.last_ts, "last_accrue_ts": self.last_accrue_ts,
             "last_pos": list(self.last_pos),
             "recent": list(self.recent), "conflicts": list(self.conflicts),
             "activity": self.activity, "n_by_role": dict(self.n_by_role),
             "echo": self.echo, "echo_ts": self.echo_ts}
        if self.tier == "nursery":
            d["pending"] = [list(p) for p in self.pending]
            d["distinct"] = list(self.distinct)
        else:
            d["state"] = pack_f16(self.state)   # packed: the 25KB-row exhibit
            d["seed"] = pack_f16(self.seed)
            d["mean"] = pack_f32(self.mean) if self.mean is not None else None
            d["rsum"] = pack_f32(self.rsum)
            if self.last_x is not None:
                d["last_x"] = pack_f16(self.last_x)
        return d

    @classmethod
    def from_doc(cls, d: dict) -> "EntityState":
        scm = d.get("_scm")
        if scm is not None and scm != EF_SCHEMA:
            raise ValueError(f"schema mismatch: doc _scm={scm} != EF_SCHEMA={EF_SCHEMA} (_k={d.get('_k')!r})")
        st = cls(eid=d["eid"], kind=d.get("kind", ""), surface=d.get("surface", ""),
                 tier=d.get("tier", "nursery"), n=int(d.get("n", 0)),
                 n_by_stream=dict(d.get("n_by_stream") or {}),
                 net_align=float(d.get("net_align", 0.0)), accr=int(d.get("accr", 0)),
                 first_ts=d.get("first_ts"), last_ts=d.get("last_ts"),
                 last_accrue_ts=d.get("last_accrue_ts"),
                 last_pos=tuple(d.get("last_pos") or (-1, -1)),
                 recent=list(d.get("recent") or []),
                 pending=[list(p) for p in d.get("pending") or []],
                 distinct=list(d.get("distinct") or []),
                 conflicts=list(d.get("conflicts") or []), activity=d.get("activity"),
                 n_by_role=dict(d.get("n_by_role") or {}),
                 echo=float(d.get("echo") or 0.0), echo_ts=d.get("echo_ts"))
        if st.tier != "nursery":
            st.state = unpack_f16(d["state"])
            st.seed = unpack_f16(d["seed"])
            st.mean = unpack_f16(d["mean"]) if d.get("mean") else None
            st.rsum = unpack_f16(d["rsum"])
            st.last_x = unpack_f16(d["last_x"]) if d.get("last_x") else None
        return st


def _locked(fn):
    """The applier (the capture worker) and the readers (the chat thread)
    share the state cache and the write-behind buffers."""
    @functools.wraps(fn)
    def wrap(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrap


class EntityField:
    """The facade: write events, embed them, apply them, read the field."""

    def __init__(self, backend, embedder, origin: str, graph_fn=None,
                 phrase_vocab=None, holder: str | None = None):
        self._lock = threading.RLock()
        # called (key, event doc, message vector) after each live event is
        # applied -- the watch and intent boards; must be cheap, never raise
        self.listeners: list = []
        # (key, event doc, the vector of ALL its sentences) for every applied
        # event, boilerplate included (forewarn.py) -- cheap, never raise
        self.step_listeners: list = []
        self.b = backend
        self.emb = embedder
        self.origin = origin
        self.o = o8(origin)
        self.graph_fn = graph_fn or (lambda: None)
        self.phrases = set(phrase_vocab or ())
        self.holder = holder or f"{os.getpid()}@{socket.gethostname()}"
        self._states: dict[str, EntityState] = {}
        self._dirty: set[str] = set()
        self._deltas: dict[str, dict] = {}
        self._deltas_index: dict | None = None
        self._now: dict[str, np.ndarray] = {}      # session -> context (RAM only)
        self._ostats = self.b.get("origin_stats", self._okey) or self._fresh_ostats()

    @property
    def model(self) -> str:
        """Read live: the seat's embedder resolves after boot."""
        return getattr(self.emb, "model", "?") or "?"

    # ---- origin bookkeeping --------------------------------------------------------
    @property
    def _okey(self) -> str:
        return f"o:{self.o}"

    def _fresh_ostats(self) -> dict:
        return {"_scm": EF_SCHEMA, "_k": "ost", "origin": self.origin,
                "bg": {}, "bg_n": {}, "pair": 0.0, "pair_n": 0, "last_x": None,
                "seq_last": 0, "cursor": 0, "boiler": {}, "null_coh": None}

    def _bg(self, stream: str):
        v = self._ostats["bg"].get(stream)
        return unpack_f16(v) if v is not None else None

    def _bg_update(self, stream: str, x) -> None:
        n = int(self._ostats["bg_n"].get(stream, 0))
        bg = self._bg(stream)
        bg = x.copy() if bg is None else bg + (x - bg) / (n + 1)
        self._ostats["bg"][stream] = pack_f16(bg)
        self._ostats["bg_n"][stream] = n + 1
        last = self._ostats.get("last_x")
        if last is not None:
            k = int(self._ostats["pair_n"])
            c = float(unit(unpack_f16(last)) @ unit(x))
            self._ostats["pair"] += (c - self._ostats["pair"]) / (k + 1)
            self._ostats["pair_n"] = k + 1
        self._ostats["last_x"] = pack_f16(x)

    def null_coherence(self) -> tuple[float, float]:
        nc = self._ostats.get("null_coh")
        return tuple(nc) if nc else NULL_COH_DEFAULT

    def is_coherent(self, st: EntityState) -> bool:
        mu, sd = self.null_coherence()
        return st.n >= 4 and st.coherence > mu + 3 * sd

    # ---- writing events --------------------------------------------------------------
    def _next_seq(self, ts) -> int:
        last = int(self._ostats["seq_last"])
        cand = int(ts * 1e6) if ts else last + 1
        if cand <= last:
            cand = last + 1
        self._ostats["seq_last"] = cand
        return cand

    def event_key(self, ev: ST.Event, session: str = "") -> str:
        if ev.stream == "discourse":
            ident = f"d|{session}|{ev.role}|{' '.join(ev.text.lower().split())}"
        elif ev.stream == "observation":
            ident = ("o|cid|" + ev.call_id if ev.call_id else
                     f"o|{ev.tool}|{sorted((ev.args or {}).items())}|{h16(ev.result_head)}")
        elif ev.stream == "code":
            c = ev.code or {}
            ident = f"c|{c.get('file')}|{c.get('mod')}.{c.get('qual')}|{h16(ev.text)}"
        else:
            ident = f"x|{h16(ev.text)}"
        return f"v:{self.o}:{h16(ident)}"

    @_locked
    def write_events(self, evs: list, session: str = "", seqs: list | None = None) -> list[str]:
        """Idempotent: a re-sent event keeps its key and is not re-applied;
        a tool RESULT upgrades its call-only observation (same call id)."""
        keys = []
        for i, ev in enumerate(evs):
            key = self.event_key(ev, session)
            old = self.b.get("events", key)
            if old is not None:
                if (ev.stream == "observation" and old.get("outcome") == "call-only"
                        and ev.outcome != "call-only"):
                    old.update({"result_head": ev.result_head, "result_tail": ev.result_tail,
                                "result_len": ev.result_len, "outcome": ev.outcome,
                                "status": "pending"})
                    if ev.args:
                        old["args"] = ev.args
                    self.b.upsert("events", key, old)
                keys.append(key)
                continue
            seq = seqs[i] if seqs is not None else self._next_seq(ev.ts)
            if seqs is not None and seq > self._ostats["seq_last"]:
                self._ostats["seq_last"] = seq
            doc = {"_scm": EF_SCHEMA, "_k": "ev", "origin": self.origin,
                   "session": session, "stream": ev.stream, "role": ev.role,
                   "text": ev.text, "tool": ev.tool, "args": ev.args,
                   "result_head": ev.result_head, "result_tail": ev.result_tail,
                   "result_len": ev.result_len, "outcome": ev.outcome,
                   "call_id": ev.call_id, "served": ev.served_text,
                   # a LIVE untimed event is stamped at intake (the
                   # freshness holes); a backfill (explicit seqs) keeps
                   # its history untimed, never 'just now'
                   "ts": ev.ts if (ev.ts is not None or seqs is not None) else time.time(),
                   "src": ev.src, "clipped": ev.clipped, "code": ev.code,
                   "seq": seq, "status": "pending", "subject": ev.subject}
            # '#N': the short per-origin number the model and the operator cite
            # (seq orders; it is a microsecond clock, too long to quote)
            doc["n"] = int(self._ostats.get("ev_n") or 0) + 1
            if self.b.insert_if_absent("events", key, doc):
                self._ostats["ev_n"] = doc["n"]
            keys.append(key)
        self.b.upsert("origin_stats", self._okey, self._ostats)
        return keys

    # ---- embedding -------------------------------------------------------------------
    def sentence_id(self, text: str) -> str:
        return "s:" + h16(self.model + "|" + " ".join(text.lower().split()))

    def _sentences_of(self, ev: dict) -> list[str]:
        st = ev.get("stream")
        if st == "discourse":
            # the speaker line is who, not what (speakers.py)
            from .speakers import prose_of
            p = prose_of(ev)
            # THE FACT-ONE EXHIBIT (issue #4): a short note without
            # terminal punctuation yields zero sentences -- it never
            # reaches the embedded status, apply never sees it, no
            # entity_state forms and recall is end-to-end empty. Floor:
            # non-blank prose is at least one sentence.
            s = ST.sentences(p)
            if not s and p and p.strip():
                s = [p.strip()]
            return s
        if st == "observation":
            if ev.get("outcome") == "call-only":
                return []
            e = ST.Event(stream="observation", tool=ev.get("tool") or "",
                         args=ev.get("args") or {}, result_head=ev.get("result_head") or "",
                         outcome=ev.get("outcome") or "")
            return [ST.observation_fact(e)]
        if st == "code":
            return [ev.get("text")] if ev.get("text") else []
        return []

    # ---- the typed edges (graph feature 1) ---------------------------------------
    CO_PAIRS = 3               # co edges written per sentence

    def _co_edges(self, key, ev, X, got, sids, skip, seq, ts) -> None:
        """A sentence naming 2+ SPECIFIC entities (speakers and event-index rows
        out; repeated boilerplate is already skipped) writes their co edges:
        relation bits read the sentence with BOTH entities' settled means
        PROJECTED out (QR orthogonalization of x against span{mean(A), mean(B)}),
        not subtracted -- subtracting leaves the pair's -(mean_A + mean_B) in
        every reading and the kind becomes unreadable (on a real field the
        signatures agreed 82.8% with that pair-only reading; projection keeps
        the relation, 0.983 vs 0.976 on unseen pairs, and drops the pair to
        0.502; experiments/delta_lane rungs 10-11, sigcheck/orth_rel)."""
        from . import relations as R
        by: dict = {}
        for eid, sidx, _surf in ev.get("mentions") or []:
            if sidx in skip or sidx >= len(X) or X[sidx] is None:
                continue
            if not E.is_specific(eid) or eid.startswith(("person:", "e:")):
                continue
            by.setdefault(sidx, set()).add(eid)
        for sidx, eids in by.items():
            if len(eids) < 2:
                continue
            x = X[sidx]
            means = {}
            for e in sorted(eids):
                st = self.get(e)
                if st is None or st.tier != "live" or st.mean is None or st.n < 2:
                    continue
                m = (st.n * np.asarray(st.mean, dtype=float) - x) / (st.n - 1)
                if np.linalg.norm(m) > 1e-9:      # a zero column would make QR
                    means[e] = m                  # project out an invented axis
            ok = sorted(means)
            pairs = [(a, b) for i, a in enumerate(ok) for b in ok[i + 1:]][:self.CO_PAIRS]
            said = str((got.get(sids[sidx]) or {}).get("text") or "")
            for a, b in pairs:
                q, _ = np.linalg.qr(np.stack([means[a], means[b]], axis=1))
                xr = x - q @ (q.T @ x)
                rb = vsa.delta_bits(np.zeros_like(xr), xr)
                if rb is not None:
                    R.upsert_co(self.b, self.origin, a, b, rb, key, seq, ts, said)

    _co_index: dict | None = None

    def edges_like(self, edge, k: int = 5, min_sim: float = 0.55) -> list[dict]:
        """The co edges whose relation signatures are nearest this edge's (a co
        edge doc, or a packed signature): the same KIND of relation between
        other things. Each as {subj, obj, n, said, sim} -- never the bits."""
        import base64
        q = edge.get("sig") if isinstance(edge, dict) else edge
        if not q:
            return []
        wg = getattr(self.b, "write_gen", None)
        gen = wg.get("relations", 0) if isinstance(wg, dict) else None
        idx = self._co_index
        if idx is None or gen is None or idx["gen"] != gen:
            rows = [d for _, d in self.b.scan("relations", {"origin": self.origin, "rel": "co"})
                    if d.get("sig") and not d.get("superseded")]
            M = (np.stack([np.frombuffer(base64.b64decode(d["sig"]), np.uint8) for d in rows])
                 if rows else np.zeros((0, vsa.DELTA_DIM // 8), np.uint8))
            idx = self._co_index = {"gen": gen, "rows": rows, "M": M}
        M = idx["M"]
        if not len(M):
            return []
        qv = np.frombuffer(base64.b64decode(q), dtype=np.uint8)
        sim = 1.0 - np.bitwise_count(M ^ qv).sum(axis=1, dtype=np.int64) / (8.0 * M.shape[1])
        me = {edge.get("subj"), edge.get("obj")} if isinstance(edge, dict) else None
        # one sentence naming 3+ things writes sibling edges with the same reading:
        # they would answer each other for free -- the query's own events never answer
        own = set(edge.get("evidence") or []) if isinstance(edge, dict) else set()
        out = []
        for i in np.argsort(-sim, kind="stable"):
            if sim[i] < min_sim or len(out) >= k:
                break
            d = idx["rows"][i]
            if me is not None and {d["subj"], d["obj"]} == me:
                continue
            if own and own.intersection(d.get("evidence") or ()):
                continue
            out.append({"subj": d["subj"], "obj": d["obj"], "n": d.get("n"),
                        "said": d.get("said") or "", "sim": round(float(sim[i]), 3)})
        return out

    def _obs_mentions(self, ev: dict, graph) -> list[tuple[str, str]]:
        a = ev.get("args") or {}
        out = [(f"tool:{ev.get('tool')}", ev.get("tool") or "")]
        fp = a.get("filePath") or a.get("path")
        if isinstance(fp, str) and fp:
            out.append((E.canon("f", fp, graph), fp))
        cmd = a.get("command")
        if isinstance(cmd, str):
            out += [(E.canon("c", h), h) for h in ST.shell_heads(cmd)[:4]]
            out += [(f"port:{p}", p) for p in ST.ports(cmd)]
        return out

    def embed_pending(self, limit: int = 256, batch: int = 64) -> int:
        evs = list(self.b.scan("events", {"origin": self.origin, "status": "pending"},
                               order=("seq",), limit=limit))
        if not evs:
            return 0
        graph = self.graph_fn()
        plan, texts = [], {}
        for key, ev in evs:
            sents = self._sentences_of(ev)
            sids = [self.sentence_id(s) for s in sents]
            for sid, s in zip(sids, sents):
                texts[sid] = s
            plan.append((key, ev, sents, sids))
        have = self.b.get_multi("sentences", list(texts))
        miss = [sid for sid in texts if sid not in have]
        for lo in range(0, len(miss), batch):
            chunk = miss[lo:lo + batch]
            vecs = self.emb.embed([texts[s] for s in chunk])
            for sid, v in zip(chunk, vecs):
                # THE 11KB-ROW EXHIBIT: sentence vecs rode as decimal
                # text (vec_list, 10.6KB/row) -> the 450MB journal;
                # packed here (the terms lane already is) -- ~1.4KB.
                self.b.insert_if_absent("sentences", sid, {
                    "_k": "sent", "text": texts[sid][:400], "model": self.model,
                    "vec": pack_f16(unit(v)), "first_ts": time.time()})
        surfaces: set = set()
        for key, ev, sents, sids in plan:
            mentions = []
            for i, s in enumerate(sents):
                for eid, surf in E.mentions_of(s, graph, self.phrases):
                    mentions.append([eid, i, surf])
            if ev.get("stream") == "code" and sents:
                c = ev.get("code") or {}
                own = [(f"s:{c['full']}", c.get("qual") or c["full"])] if c.get("full") else []
                if c.get("file"):
                    own.append((E.canon("f", c["file"], graph), c["file"]))
                seen = {m[0] for m in mentions}
                mentions = [[e, 0, s] for e, s in own if e not in seen] + mentions
            if ev.get("stream") == "observation" and sents:
                seen = {m[0] for m in mentions}
                for eid, surf in self._obs_mentions(ev, graph):
                    if eid not in seen:
                        mentions.append([eid, 0, surf])
                        seen.add(eid)
            if ev.get("subject") and sents:
                # THE NAMED SUBJECT (the script lanes): prose mentions can't
                # resolve a synthetic eid, so the event names it like the
                # code stream names its own def (first, above the patterns)
                subj = ev["subject"]
                seen = {m[0] for m in mentions}
                if subj not in seen:
                    mentions.insert(0, [subj, 0,
                                        subj.split(":", 1)[-1] or subj])
            if sents:
                # WHO SAID IT (speakers.py): every intake lane, one place.
                # Its own slot, not a mention: mentions are what a message
                # is ABOUT (the fit, the thinker's tags, the banner's diff)
                from .speakers import speaker_of
                spk = speaker_of(ev)
                if spk is not None:
                    ev["speaker"] = [spk[0], spk[1]]
            ev["sids"] = sids
            ev["mentions"] = mentions
            surfaces.update(m[2] for m in mentions)
            if ev.get("stream") == "distilled":
                ev["status"] = "distilled"
            elif not sids:
                ev["status"] = "waiting" if ev.get("outcome") == "call-only" else "empty"
            else:
                ev["status"] = "embedded"
            self.b.upsert("events", key, ev)
            if ev.get("role") in ("model", "note"):
                try:
                    from . import claims as CL
                    CL.ingest(self, key, ev)
                except Exception:
                    pass                              # claims never block intake
        self._prefetch_seeds(surfaces, batch)
        return len(evs)

    def _term_key(self, surface: str) -> str:
        return "t:" + h16(self.model + "|" + surface.lower())

    def _prefetch_seeds(self, surfaces, batch: int = 64) -> None:
        """Embed the uncached seeds of a chunk's mentions in batches (the
        applier would otherwise make one embedder call per new term; one
        it still makes on a miss is harmless -- same key, same text)."""
        keys = {self._term_key(s): s.lower() for s in surfaces if s}
        have = self.b.get_multi("terms", list(keys))
        miss = [k for k in keys if k not in have]
        for lo in range(0, len(miss), batch):
            chunk = miss[lo:lo + batch]
            vecs = self.emb.embed([keys[k] for k in chunk])
            for k, v in zip(chunk, vecs):
                self.b.insert_if_absent("terms", k, {"_k": "term", "term": keys[k],
                                                     "model": self.model,
                                                     "vec": pack_f16(unit(v))})

    # ---- applying --------------------------------------------------------------------
    def _seed(self, surface: str) -> np.ndarray:
        tkey = self._term_key(surface)
        d = self.b.get("terms", tkey)
        if d is not None:
            return unpack_f16(d["vec"])
        packed = pack_f16(unit(self.emb.embed([surface.lower()])[0]))
        self.b.upsert("terms", tkey, {"_k": "term", "term": surface.lower(),
                                      "model": self.model, "vec": packed})
        # the STORED (float16) seed, even on first sight: a rebuild reads
        # the cache, so the first run must start from the same rounded seed
        return unpack_f16(packed)

    def _state_key(self, eid: str) -> str:
        return f"e:{self.o}:{h16(eid)}"

    @_locked
    def get(self, eid: str) -> EntityState | None:
        st = self._states.get(eid)
        if st is None:
            d = self.b.get("entity_states", self._state_key(eid))
            if d is not None:
                st = EntityState.from_doc(d)
                self._states[eid] = st
        return st

    @staticmethod
    def _mat_bar(eid: str) -> int:
        """The materialization bar per entity kind: specific kinds
        (files/symbols/commands/models -- the subjects a cell is ABOUT)
        live at their first mention; words and phrases at K_MAT
        (distinct sentences) like every generic kind. (The word-bar
        idea was reverted: the recall contract's entity-states lane
        needs word entities; the amplification fix lives in
        _update's word-delta skip.)"""
        return 1 if E.is_specific(eid) else K_MAT

    # the store's names (boot wires it): an m: id that is no model tag must
    # name an entity the store holds -- else it is a fragment
    name_ok = None

    def m_ok(self, eid: str) -> bool:
        bare = eid[2:]
        if E.is_junk_tag(bare):
            return False
        if E.is_model_tag(bare):
            return True
        ok = self.name_ok
        return True if ok is None else bool(ok(bare))

    @_locked
    def purge_ghosts(self, dry_run: bool = True, sample: int = 20) -> dict:
        """THE NURSERY GHOSTS OUT: every m: state that m_ok refuses -- its
        state, its deltas, its relations, and its mentions in the events
        (or a rebuild would mint it again). dry_run counts only."""
        self.flush()
        ghosts = sorted({d.get("eid") for _, d in self.b.scan("entity_states", {"origin": self.origin})
                         if str(d.get("eid") or "").startswith("m:") and not self.m_ok(str(d["eid"]))})
        out = {"ghosts": len(ghosts), "sample": ghosts[:sample], "dry_run": dry_run}
        if dry_run or not ghosts:
            return out
        g = set(ghosts)
        n = {"states": 0, "deltas": 0, "relations": 0, "events": 0}
        for k, d in list(self.b.scan("entity_states", {"origin": self.origin})):
            if d.get("eid") in g:
                self.b.delete("entity_states", k); n["states"] += 1
        for k, d in list(self.b.scan("entity_deltas", {"origin": self.origin})):
            if d.get("eid") in g:
                self.b.delete("entity_deltas", k); n["deltas"] += 1
        for k, d in list(self.b.scan("relations", {"origin": self.origin})):
            if d.get("subj") in g or d.get("obj") in g:
                self.b.delete("relations", k); n["relations"] += 1
        for k, ev in list(self.b.scan("events", {"origin": self.origin})):
            ms = ev.get("mentions") or []
            keep = [m for m in ms if not (m and m[0] in g)]
            if len(keep) != len(ms):
                ev["mentions"] = keep
                self.b.upsert("events", k, ev); n["events"] += 1
        for eid in g:
            self._states.pop(eid, None)
            self._dirty.discard(eid)
        try:
            self._invalidate_delta_index()
        except Exception:
            pass
        out.update(n)
        return out

    def _get_or_new(self, eid: str, surface: str) -> EntityState | None:
        """THE DUST CAP: a too-short word/phrase entity gets NO state
        (None returned; the caller skips it entirely) -- the 'w:showing'
        dust never enters entity_states (the CPU bomb's birth lane)."""
        if _dust_word(eid, surface):
            return None
        if eid.startswith("m:") and not self.m_ok(eid):
            return None                         # a nursery ghost never gets a state
        st = self.get(eid)
        if st is None:
            st = EntityState(eid=eid, kind=eid.split(":", 1)[0], surface=surface)
            self._states[eid] = st
        return st

    def _update(self, st: EntityState, x, stream: str, sid: str, seq: int,
                sidx: int, evkey: str, ts, role: str = "") -> None:
        ts = event_ts(ts, seq)            # an untimed event: its seq's clock
        pos = (seq, sidx)
        if tuple(pos) <= tuple(st.last_pos):
            return                                    # idempotence
        bg = self._bg(stream)
        before = st.state
        align = (float(unit(st.mean) @ x) - float(self._ostats["pair"])
                 if st.mean is not None else 0.0)
        # every value kept at its STORED precision, so a restart (a reload
        # from the backend) never changes a later result
        st.state = at_stored(unit(before + GAIN * (x - before)))
        st.mean = f32(x if st.mean is None else st.mean + (x - st.mean) / (st.n + 1))
        r = unit(x - bg) if bg is not None else x
        st.rsum = f32(r if st.rsum is None else st.rsum + r)
        st.n += 1
        adopt_legacy(st, ts)              # a pre-split state converts first
        st.n_by_stream[stream] = st.n_by_stream.get(stream, 0) + 1
        note_mention(st, stream, role, ts, adopt=False)
        st.net_align += align
        st.accr += int(align >= ACCRETE)
        if align >= ACCRETE:              # the metabolism's clock: derived
            st.last_accrue_ts = ts        # exactly like accr, so a rebuild
                                          # replays it identically
        st.first_ts = st.first_ts if st.first_ts is not None else ts
        st.last_ts = ts if ts is not None else st.last_ts
        st.last_pos = pos
        st.recent = ([s for s in st.recent if s != sid] + [sid])[-RECENT:]
        self._dirty.add(st.eid)
        # THE WORD-DELTA SKIP (the write-amplification fix): words and
        # phrases carry NO per-event delta row -- their states update
        # above (cheap), their event mapping rides the event-index
        # delta (one row per event); a per-word row per mention was
        # 8 rows/event (the 216k-delta bomb), and the deltas' consumers
        # (the night drift's _cell_points, refresh_activity, the diff)
        # are FOR specific entities. Specific kinds keep full deltas.
        if st.eid.split(":", 1)[0] in ("w", "p"):
            return
        dkey = f"d:{self.o}:{h16(st.eid)}:{seq}:{sidx}"
        row = {"_k": "dl", "eid": st.eid, "origin": self.origin,
               "sid": sid, "event": evkey, "stream": stream, "ts": ts,
               "seq": seq, "sidx": sidx, "gain": GAIN, "align": align,
               "cos_before": float(before @ x)}
        # THE DIRECTION OF THE MOVE (the delta lane, stage 1 -- stored, not
        # yet read): cos_before kept how FAR the event sat from the state,
        # the old cortex kept only the norm; the bits keep WHICH WAY it
        # moved (vsa.delta_bits). The kill switch is the VSA lane's own.
        if vsa.enabled():
            db = vsa.delta_bits(before, x)
            if db is not None:
                row["dbits"] = db
            # THE MOVE FROM THE PREVIOUS MENTION (stage 2a): measured stronger
            # than the move from the running state everywhere it was tried
            # (prose transitions 0.861/0.926 vs 0.653/0.740; real sensors
            # 0.496 vs 0.380 -- experiments/delta_lane); the first mention
            # has nothing before it
            if st.last_x is not None:
                pb = vsa.delta_bits(st.last_x, x)
                if pb is not None:
                    row["pbits"] = pb
            st.last_x = unpack_f16(pack_f16(x))      # the stored precision
        self._deltas[dkey] = row

    def _materialize(self, st: EntityState) -> None:
        st.seed = self._seed(st.surface or st.eid.split(":", 1)[-1])
        st.state = at_stored(st.seed)
        st.tier = "live"
        pend = sorted(st.pending, key=lambda p: (p[1], p[2]))
        vecs = self.b.get_multi("sentences", [p[0] for p in pend])
        st.pending, st.distinct = [], []
        for p in pend:
            sid, seq, sidx, stream, evkey, ts = p[:6]
            role = p[6] if len(p) > 6 else ""        # pre-split pendings: no role
            if sid in vecs:
                self._update(st, unit(vecs[sid]["vec"]), stream, sid, seq, sidx, evkey, ts, role)
        self._dirty.add(st.eid)
        _born = getattr(self, "pulse_born", None)
        if _born is not None:
            try:
                _born(st.eid)
            except Exception:
                pass
        self.flush()

    def _apply(self, key: str, ev: dict) -> None:
        stream, seq, ts = ev["stream"], int(ev["seq"]), ev.get("ts")
        sids = ev.get("sids") or []
        got = self.b.get_multi("sentences", sids)
        X = [unit(got[s]["vec"]) if s in got else None for s in sids]
        boiler = self._ostats["boiler"]
        skip = set()
        for i, sid in enumerate(sids):
            if X[i] is None:
                skip.add(i)
                continue
            nk = ST.norm_sentence(got[sid]["text"])
            boiler[nk] = boiler.get(nk, 0) + 1
            if boiler[nk] > BOILER_MIN:
                skip.add(i)
        # THE STEP LISTENERS (forewarn.py): every applied event, boilerplate
        # INCLUDED -- a failure seen for the 9th time is exactly what the
        # forewarning must not miss (the listeners below see only what moves)
        if self.step_listeners:
            have = [x for x in X if x is not None]
            if have:
                x_all = unit(np.sum(have, axis=0))
                for fn in self.step_listeners:
                    try:
                        fn(key, ev, x_all)
                    except Exception:
                        pass
        live_idx = [i for i in range(len(sids)) if i not in skip]
        if not live_idx:
            ev["surprise"] = None
            return
        x_msg = unit(np.sum([X[i] for i in live_idx], axis=0))
        for fn in self.listeners:
            try:
                fn(key, ev, x_msg)
            except Exception:
                pass
        mentioned = {m[0] for m in ev.get("mentions") or [] if m[1] in live_idx}
        live = [(e, st) for e, st in ((e, self.get(e)) for e in sorted(mentioned))
                if st is not None and st.tier == "live"]
        A = [st.state for _, st in live]
        if A:
            xh, w = fit_weights(x_msg, A)
            # THE EVENT'S ENTITY COORDINATES (2026-10-08, entities as axes):
            # how much of this message each mentioned entity explains
            tot = float(w.sum()) or 1.0
            coords = sorted(((e, round(float(wi) / tot, 3)) for (e, _), wi in zip(live, w)
                             if wi / tot >= 0.02), key=lambda c: -c[1])[:8]
            if coords:
                ev["coords"] = [[e, c] for e, c in coords]
            ev["surprise"] = float(1.0 - unit(xh) @ x_msg)
            resid = x_msg - xh
        else:
            ev["surprise"] = 1.0
            resid = x_msg
        sess = ev.get("session") or ""
        if np.linalg.norm(resid) > CTX_MIN_RESID:
            now = self._now.get(sess)
            self._now[sess] = unit(resid) if now is None else unit(0.5 * now + 0.5 * unit(resid))
        for eid, sidx, surf in ev.get("mentions") or []:
            if sidx in skip or sidx >= len(sids):
                continue
            st = self._get_or_new(eid, surf)
            if st is None:
                continue                  # the dust cap: no state born
            if st.tier == "nursery":
                st.pending.append([sids[sidx], seq, sidx, stream, key, ts,
                                   ev.get("role") or ""])
                if sids[sidx] not in st.distinct:
                    st.distinct.append(sids[sidx])
                self._dirty.add(eid)
                if self._mat_bar(eid) <= len(st.distinct):
                    self._materialize(st)
                continue
            self._update(st, X[sidx], stream, sids[sidx], seq, sidx, key, ts,
                         ev.get("role") or "")
        if vsa.enabled():
            try:
                self._co_edges(key, ev, X, got, sids, skip, seq, ts)
            except Exception:
                pass
        self._apply_speaker(ev, key, X, sids, skip, seq, stream, ts)
        for i in live_idx:
            self._bg_update(stream, X[i])
        # THE EVENT-INDEX DELTA (the word-bar's companion): EVERY
        # applied event writes ONE delta row under a meta-entity
        # ('e:<event-key>' -- no state, no drift, kind excluded from
        # reads): the sentence->event index no longer depends on any
        # entity materializing (past_answers/near_asks route through
        # the deltas; the word bar means most events now touch no
        # live entity at all -- without this row the recall loses
        # them wholesale, the test_past_answers exhibit). One row per
        # event: the cost the word bar saved, spent on the index.
        idx_eid = f"e:{key}"
        dkey = f"d:{self.o}:{h16(idx_eid)}:{seq}:0"
        self._deltas[dkey] = {"_k": "dl", "eid": idx_eid,
                              "origin": self.origin,
                              "sid": sids[0] if sids else "",
                              "event": key, "stream": stream, "ts": ts,
                              "seq": seq, "sidx": 0, "gain": 0.0,
                              "align": 0.0, "cos_before": 1.0}
        self._dirty.add(idx_eid)
        if stream == "observation":
            from . import relations as R
            f_eid = next((m[0] for m in ev.get("mentions") or [] if m[0].startswith("f:")), None)
            for subj, rel, obj in R.observation_triples(ev.get("tool") or "",
                                                        ev.get("outcome") or "", f_eid):
                R.upsert(self.b, self.origin, subj, rel, obj, "observation", key, ts)

    SPEAKER_TOPICS = 3

    def _apply_speaker(self, ev: dict, key: str, X, sids, skip, seq, stream, ts) -> None:
        """The speaker learns from every sentence they said (their state is
        where their words land), and talks_about edges join them to the
        message's main topics (the event's entity coordinates)."""
        spk = ev.get("speaker")
        if not spk:
            return
        st = self._get_or_new(spk[0], spk[1])
        if st is None:
            return
        for i, sid in enumerate(sids[:8]):
            if i in skip or X[i] is None:
                continue
            if st.tier == "nursery":
                st.pending.append([sid, seq, i, stream, key, ts, ev.get("role") or ""])
                if sid not in st.distinct:
                    st.distinct.append(sid)
                self._dirty.add(st.eid)
                if self._mat_bar(st.eid) <= len(st.distinct):
                    self._materialize(st)
                continue
            self._update(st, X[i], stream, sid, seq, i, key, ts, ev.get("role") or "")
        from . import relations as R
        for eid, _w in (ev.get("coords") or [])[:self.SPEAKER_TOPICS]:
            if eid != spk[0]:
                R.upsert(self.b, self.origin, spk[0], "talks_about", eid, "discourse", key, ts)

    def apply_pending(self, limit: int = 200) -> int:
        # THE LOCK-HOLD FIX (the swirl exhibit): the old
        # @apply_pending held the field lock for the WHOLE scan+apply+
        # flush -- a 200-event burst (the night tick, a big capture
        # backlog) held it 30s+; the run-lane relay's write_events
        # (a live turn's tool call) blocked behind it and false-errored
        # on its 30s HTTP timeout -> the model burned rounds retrying
        # (the 24-round turn, the two 'proxy unreachable (timed out)'
        # tool errors). Now the LEASE is taken once, the scan runs
        # free, and EACH event applies under a SHORT lock (the state
        # mutations stay atomic; the lock releases between events), the
        # status upsert + cursor ride the same short lock, and flush
        # gets its own.
        if not self.b.lease(f"applier:{self.o}", self.holder, 60.0):
            return 0
        evs = list(self.b.scan("events", {"origin": self.origin, "status": "embedded"},
                               order=("seq",), limit=limit))
        for key, ev in evs:
            with self._lock:
                self._apply(key, ev)
                ev["status"] = "applied"
                self.b.upsert("events", key, ev)
                self._ostats["cursor"] = max(int(self._ostats["cursor"]),
                                             int(ev["seq"]))
        self.flush()
        return len(evs)

    CLAIM_EVAL_EVERY_S = 60.0

    def process(self, limit: int = 256, now: float | None = None) -> int:
        """Embed then apply everything pending (the worker's step); settle
        claims whose evidence window closed (throttled: it scans observations)."""
        n = 0
        while True:
            a = self.embed_pending(limit)
            b = self.apply_pending(limit)
            n += b
            if a == 0 and b == 0:
                break
        t = time.time()
        if t - getattr(self, "_last_claim_eval", 0.0) >= self.CLAIM_EVAL_EVERY_S:
            self._last_claim_eval = t
            # THE PAUSE (the stable point): the claim judge is the idle
            # cortex acting -- gated through the seat's pause_fn (wired
            # at boot; None keeps the judge live, the bare-library form).
            pause_fn = getattr(self, "pause_fn", None)
            try:
                if pause_fn is None or not pause_fn():
                    from . import claims as CL
                    CL.evaluate(self, now=now)
            except Exception:
                pass
        return n

    @_locked
    def flush(self) -> None:
        if self._dirty:
            self.b.upsert_multi("entity_states", {
                self._state_key(e): self._states[e].to_doc(self.origin)
                for e in self._dirty if e in self._states})
            # THE FIELD-WATCH INTAKE (the model's filters over the
            # field): the dirty pass IS the event source -- each
            # changed entity's watch_fields check + the gravity/
            # freeze gate live here ($0: reads the stamps).
            try:
                from .night import note_field_change
                _store = getattr(self, "_est_sink", None)
                if _store is not None:
                    for e in list(self._dirty):
                        # a FIELD id -> its stored entity (the store keys are
                        # membrane::...: docs.get(field id) never matched)
                        _ent = _store.docs.get(e) or _store.by_field_id(e)
                        if _ent is not None:
                            note_field_change(getattr(_store, "_srv",
                                                      None), _store, _ent)
            except Exception:
                pass
            # THE FIELD'S PULSE (viewer v3): the dirty live states re-place
            # into the cached 3D projections (fieldpulse.FieldPulse.moved)
            _moved = getattr(self, "pulse_moved", None)
            if _moved is not None:
                for e in list(self._dirty):
                    _st = self._states.get(e)
                    if _st is not None and _st.tier == "live" and _st.state is not None:
                        try:
                            _moved(e, _st.state)
                        except Exception:
                            pass
            self._dirty.clear()
        if self._deltas:
            g0 = self._dgen()
            self.b.upsert_multi("entity_deltas", self._deltas)
            # THE INDEX KEEPS (2026-10-10: every flush threw the per-entity
            # index away -- the next deltas_of() re-walked 160k+ deltas, and
            # the banner's track lines paid it every round): this flush's own
            # deltas merge in; anything else since (a delete) rebuilds it
            if self._deltas_index is not None and g0 is not None and self._deltas_index_gen == g0:
                self._index_merge(self._deltas.values())
                self._deltas_index_gen = self._dgen()
            else:
                self._invalidate_delta_index()
            self._deltas = {}
        elif self._dgen() is None:
            self._invalidate_delta_index()        # a backend that can't say what changed: the old rule
        self.b.upsert("origin_stats", self._okey, self._ostats)

    @_locked
    def trim_cache(self, max_states: int = 5000) -> None:
        """Drop clean cached states (they reload from the backend on demand):
        a backfill touches tens of thousands of entities."""
        if len(self._states) > max_states:
            self.flush()
            self._states = {}

    @_locked
    def rebuild(self) -> int:
        """Wipe this origin's states and deltas and re-apply its events in
        seq order: the escape hatch for any parameter change (no re-embed)."""
        for coll in ("entity_states", "entity_deltas"):
            for k, _ in list(self.b.scan(coll, {"origin": self.origin})):
                self.b.delete(coll, k)
        keep_seq = self._ostats.get("seq_last", 0)
        null = self._ostats.get("null_coh")
        # the forewarning's LIVE calibration (forewarn.py) is a record of
        # forecasts made in real time -- not derivable, so it survives
        fw = self._ostats.get("forewarn")
        self._ostats = self._fresh_ostats()
        self._ostats["seq_last"], self._ostats["null_coh"] = keep_seq, null
        if fw:
            self._ostats["forewarn"] = fw
        self._states, self._dirty, self._deltas, self._now = {}, set(), {}, {}
        for k, ev in list(self.b.scan("events", {"origin": self.origin, "status": "applied"})):
            ev["status"] = "embedded"
            self.b.upsert("events", k, ev)
        n = 0
        while True:
            m = self.apply_pending(1000)
            n += m
            if not m:
                return n

    # ---- statistics --------------------------------------------------------------------
    def refresh_null(self, sets: int = 200, sizes=(4, 8, 16), seed: int = 23) -> tuple:
        """Coherence of RANDOM sentence sets of this origin (E23's null)."""
        sids = []
        for _, ev in self.b.scan("events", {"origin": self.origin, "status": "applied"}):
            sids.extend(ev.get("sids") or [])
        got = self.b.get_multi("sentences", list(dict.fromkeys(sids)))
        if len(got) < max(sizes) + 1:
            return self.null_coherence()
        rng = np.random.default_rng(seed)
        keys = list(got)
        vals = []
        for n in sizes:
            for _ in range(sets // len(sizes)):
                pick = rng.choice(len(keys), n, replace=False)
                R = np.array([unit(unit(got[keys[i]]["vec"]) - (self._bg("discourse")
                              if self._bg("discourse") is not None else 0.0)) for i in pick])
                vals.append(coherence_of(R.sum(axis=0), n))
        self._ostats["null_coh"] = [float(np.mean(vals)), float(np.std(vals))]
        self.b.upsert("origin_stats", self._okey, self._ostats)
        return tuple(self._ostats["null_coh"])

    def _deltas_index_name(self):
        return "deltas_by_eid"

    def _dgen(self):
        wg = getattr(self.b, "write_gen", None)
        return wg.get("entity_deltas", 0) if isinstance(wg, dict) else None

    def _index_merge(self, rows) -> None:
        """New deltas into the per-entity lists, kept in (seq, sidx) order --
        unique per entity, so the merge equals a rebuild."""
        import bisect
        idx = self._deltas_index
        for d in rows:
            if d.get("origin") != self.origin:
                continue
            lst = idx.setdefault(d.get("eid") or "", [])
            k = (d.get("seq") or 0, d.get("sidx") or 0)
            if not lst or (lst[-1].get("seq") or 0, lst[-1].get("sidx") or 0) < k:
                lst.append(d)
                continue
            keys = [(x.get("seq") or 0, x.get("sidx") or 0) for x in lst]
            i = bisect.bisect_left(keys, k)
            if i < len(lst) and keys[i] == k:
                lst[i] = d                       # the same delta rewritten
            else:
                lst.insert(i, d)

    def _invalidate_delta_index(self) -> None:
        """The index stales on every flush (the deltas changed); the
        next deltas_of() rebuilds once (one pass over the collection,
        amortized across ALL lookups)."""
        self._deltas_index = None

    def rebuild_delta_index(self) -> int:
        """Force the index rebuild NOW (tests + the caller after a
        bulk out-of-band delta write). Returns the indexed count."""
        with self._lock:
            idx: dict[str, list[dict]] = {}
            gen = self._dgen()
            for k, d in self.b.scan("entity_deltas",
                                    {"origin": self.origin},
                                    order=("seq", "sidx")):
                idx.setdefault(d.get("eid") or "", []).append(d)
            self._deltas_index = idx
            self._deltas_index_gen = gen
            return sum(len(v) for v in idx.values())

    def relabel_roles(self, now: float | None = None, batch: int = 200) -> dict:
        """THE PRE-SPLIT COUNTS (2026-10-08, the seat's follow-up): every
        live state without per-role counts is relabelled ONCE -- from its
        deltas (each names its event, the event its role: the true split,
        echoes decayed from their own times), or, for word/phrase states
        (no deltas), its old discourse count becomes echo fading from the
        state's last-seen time. Idempotent (a relabelled state has roles)."""
        now = time.time() if now is None else float(now)
        out = {"relabelled": 0, "from_deltas": 0, "as_echo": 0}
        eids = [d.get("eid") for _, d in self.b.scan(
            "entity_states", {"origin": self.origin, "tier": "live"}) if d.get("eid")]
        todo = []
        for eid in eids:
            st = self.get(eid)
            if st is not None and not st.n_by_role and st.n:
                todo.append(st)
        if not todo:
            return out
        # ONE pass over the deltas, OUTSIDE the lock (deltas_of's index is
        # invalidated by every flush: per-batch use rebuilt it from the whole
        # collection each time, under the lock -- the live turns waited)
        want = {st.eid for st in todo}
        rows_by: dict = {}
        for _, d in self.b.scan("entity_deltas", {"origin": self.origin}):
            e = d.get("eid")
            if e in want:
                rows_by.setdefault(e, []).append(d)
        keys = list({d.get("event") for rows in rows_by.values() for d in rows if d.get("event")})
        roles: dict = {}
        for lo in range(0, len(keys), 500):
            for k, ev in self.b.get_multi("events", keys[lo:lo + 500]).items():
                roles[k] = (ev or {}).get("role") or ""
        for lo in range(0, len(todo), batch):
            with self._lock:
                for st in todo[lo:lo + batch]:
                    ds = rows_by.get(st.eid)
                    if ds:
                        st.n_by_role, st.echo, st.echo_ts = {}, 0.0, None
                        for d in sorted(ds, key=lambda r: (r.get("seq") or 0, r.get("sidx") or 0)):
                            note_mention(st, d.get("stream") or "", roles.get(d.get("event"), ""),
                                         event_ts(d.get("ts"), d.get("seq")), adopt=False)
                        out["from_deltas"] += 1
                    else:
                        c = st.n_by_stream or {}
                        st.n_by_role = {k: v for k, v in c.items() if k in ("observation", "code")}
                        st.n_by_role["unsplit"] = 0        # the marker: relabelled, split unknown
                        st.echo = float(int(c.get("discourse") or 0))
                        st.echo_ts = last_seen(st) or now
                        out["as_echo"] += 1
                    self._dirty.add(st.eid)
                    out["relabelled"] += 1
            self.flush()
            time.sleep(0.05)                               # let the live turns in
        return out

    def deltas_of(self, eid: str) -> list[dict]:
        """THE DELTAS INDEX (the CPU-bomb's load-bearing fix):
        deltas_by_eid is rebuilt on access when staled (the invalidator
        bumps on flush); O(1) per-entity lookups replace per-entity
        full scans (the night's 63M row-checks per tick)."""
        with self._lock:
            gen = self._dgen()
            if self._deltas_index is None or (gen is not None and gen != self._deltas_index_gen):
                idx: dict[str, list[dict]] = {}
                for k, d in self.b.scan("entity_deltas",
                                        {"origin": self.origin},
                                        order=("seq", "sidx")):
                    idx.setdefault(d.get("eid") or "", []).append(d)
                self._deltas_index = idx
                self._deltas_index_gen = gen
            return list(self._deltas_index.get(eid) or [])

    _deltas_index: dict | None = None
    _deltas_index_gen = None

    # ---- the changes like this one (the delta lane, stage 2b) --------------------
    _bits_index: dict | None = None

    def _bits_idx(self, bits: str) -> dict:
        """The packed move bits of every delta row that carries them, one
        uint8 matrix (rows x DELTA_DIM/8); rebuilt when the deltas changed."""
        import base64
        gen = self._dgen()
        cache = self._bits_index or {}
        hit = cache.get(bits)
        if hit is not None and gen is not None and hit["gen"] == gen:
            return hit
        rows, packed = [], []
        for _k, d in self.b.scan("entity_deltas", {"origin": self.origin}):
            b = d.get(bits)
            # a speaker's move is who talked, not what changed (the drift
            # rule): the person rows mirror every sentence they say
            if not b or str(d.get("eid") or "").startswith("person:"):
                continue
            rows.append({"eid": d.get("eid"), "event": d.get("event"), "sid": d.get("sid"),
                         "seq": d.get("seq"), "ts": d.get("ts")})
            packed.append(np.frombuffer(base64.b64decode(b), dtype=np.uint8))
        M = np.stack(packed) if packed else np.zeros((0, vsa.DELTA_DIM // 8), np.uint8)
        hit = {"gen": gen, "rows": rows, "M": M,
               "eid": np.asarray([r["eid"] for r in rows], dtype=object),
               "event": np.asarray([r["event"] for r in rows], dtype=object)}
        cache[bits] = hit
        self._bits_index = cache
        return hit

    def deltas_like(self, query, k: int = 5, scope: str = "all", bits: str = "pbits",
                    min_sim: float = 0.55) -> list[dict]:
        """THE CHANGES LIKE THIS ONE: the past moves nearest a delta row's (or
        a packed bit string's) in Hamming space -- scope 'self' (the same
        entity), 'others', or 'all'; the query's own event never answers.
        Each as what was SAID: {eid, text, n, ts, event, sim} (sim 1.0 =
        identical move, 0.5 = unrelated); never the bits."""
        import base64
        q = query.get(bits) if isinstance(query, dict) else query
        if not q:
            return []
        qeid = query.get("eid") if isinstance(query, dict) else None
        qev = query.get("event") if isinstance(query, dict) else None
        idx = self._bits_idx(bits)
        M = idx["M"]
        if not len(M):
            return []
        qv = np.frombuffer(base64.b64decode(q), dtype=np.uint8)
        sim = 1.0 - np.bitwise_count(M ^ qv).sum(axis=1, dtype=np.int64) / (8.0 * M.shape[1])
        keep = sim >= min_sim
        if qev is not None:
            keep &= idx["event"] != qev
        if qeid is not None and scope == "self":
            keep &= idx["eid"] == qeid
        elif qeid is not None and scope == "others":
            keep &= idx["eid"] != qeid
        cand = np.where(keep)[0]
        order, seen, top = cand[np.argsort(-sim[cand], kind="stable")], set(), []
        for i in order:                   # one sentence = one change, however many entities it moved
            ev = idx["event"][i]
            if ev in seen:
                continue
            seen.add(ev)
            top.append(i)
            if len(top) >= max(0, k):
                break
        if not top:
            return []
        picked = [idx["rows"][i] for i in top]
        sents = self.b.get_multi("sentences", list(dict.fromkeys(r["sid"] for r in picked if r["sid"])))
        evs = self.b.get_multi("events", list(dict.fromkeys(r["event"] for r in picked if r["event"])))
        out = []
        for i, r in zip(top, picked):
            out.append({"eid": r["eid"], "event": r["event"], "ts": r["ts"],
                        "n": (evs.get(r["event"]) or {}).get("n"),
                        "text": str((sents.get(r["sid"]) or {}).get("text") or "")[:400],
                        "sim": round(float(sim[i]), 3)})
        return out

    # the card's floor (the real-field gate, 2026-10-15): the same KIND of change
    # recalled across entities sat at 0.70-0.85 (the same proxy failure on
    # c:head/c:python3/tool:run; 'a new entity appeared on the banner' across
    # three unrelated entities); 0.60-0.67 was topical/stylistic look-alikes
    CHANGES_FLOOR = 0.68

    def latest_changes(self, eid: str, k: int = 3) -> dict:
        """The card's section: this entity's latest change and the past changes
        most like it -- on itself and on others. {} when it has none."""
        rows = [d for d in self.deltas_of(eid) if d.get("pbits")]
        if not rows:
            return {}
        last = max(rows, key=lambda d: (d.get("seq") or 0, d.get("sidx") or 0))
        s = self.b.get("sentences", last.get("sid")) or {}
        ev = self.b.get("events", last.get("event")) or {}
        return {"latest": {"text": str(s.get("text") or "")[:400], "n": ev.get("n"),
                           "ts": last.get("ts")},
                "self": self.deltas_like(last, k=k, scope="self", min_sim=self.CHANGES_FLOOR),
                "others": self.deltas_like(last, k=k, scope="others", min_sim=self.CHANGES_FLOOR)}

    @_locked
    def number_events(self) -> int:
        """Give '#N' numbers to events written before numbering existed (in
        seq order, after the numbered ones)."""
        todo = sorted(((d.get("seq") or 0, k, d) for k, d in self.b.scan(
            "events", {"origin": self.origin}) if d.get("n") is None), key=lambda t: t[:2])
        for _, k, d in todo:
            self._ostats["ev_n"] = int(self._ostats.get("ev_n") or 0) + 1
            d["n"] = self._ostats["ev_n"]
            self.b.upsert("events", k, d)
        self.b.upsert("origin_stats", self._okey, self._ostats)
        return len(todo)

    def learn(self, text: str, src: str = "user", session: str = "learn") -> str | None:
        """An explicit lesson: a NOTE event (discourse, role 'note'; the
        claims layer reads it), applied on the next process()."""
        text = " ".join((text or "").split())
        if not text:
            return None
        ev = ST.Event(stream="discourse", role="note", text=text, ts=time.time(), src=src)
        return self.write_events([ev], session=session)[0]

    @_locked
    def forget(self, n: int) -> dict | None:
        """'forget #N': tombstone the event (its text and results cleared, its
        key kept so a re-capture stays idempotent), drop its deltas and
        claims, and recompute the entities it touched from their remaining
        deltas. The recompute uses today's stream backgrounds (rebuild() is
        the exact path)."""
        hit = self.event_by_number(n)
        if hit is None:
            return None
        key, ev = hit
        if ev.get("status") == "forgotten":
            return {"n": n, "key": key, "entities": [], "already": True}
        self.flush()
        touched = []
        for dk, d in list(self.b.scan("entity_deltas", {"origin": self.origin, "event": key})):
            self.b.delete("entity_deltas", dk)
            touched.append(d["eid"])
        touched = list(dict.fromkeys(touched))
        # a nursery entity may hold the event's mention in its pending buffer
        for eid, _i, _s in ev.get("mentions") or []:
            st = self.get(eid)
            if st is not None and st.tier == "nursery":
                st.pending = [p for p in st.pending if p[4] != key]
                st.distinct = list(dict.fromkeys(p[0] for p in st.pending))
                self._dirty.add(eid)
        for ck, c in list(self.b.scan("claims", {"origin": self.origin, "event": key})):
            c["status"] = "forgotten"
            self.b.upsert("claims", ck, c)
        for f in ("text", "result_head", "result_tail", "served"):
            if ev.get(f):
                ev[f] = ""
        ev.update({"status": "forgotten", "sids": [], "mentions": [], "forgotten_ts": time.time()})
        self.b.upsert("events", key, ev)
        for eid in touched:
            self.recompute(eid)
        self.flush()
        return {"n": n, "key": key, "entities": touched, "already": False}

    @_locked
    def reembed(self, embedder, progress=None) -> int:
        """Move the field to another embedding model: the new model's
        sentence and term caches fill as the events are re-embedded (ids are
        model-scoped; the old model's rows stay), then every live event is
        re-applied in seq order -- rebuild() with a new geometry."""
        self.emb = embedder
        for coll in ("entity_states", "entity_deltas"):
            for k, _ in list(self.b.scan(coll, {"origin": self.origin})):
                self.b.delete(coll, k)
        keep = {k: self._ostats.get(k) for k in ("seq_last", "ev_n", "conflict_n", "conflict_log",
                                                 "backfill", "backfill_code_done", "born")}
        self._ostats = self._fresh_ostats()
        self._ostats.update({k: v for k, v in keep.items() if v is not None})
        self._states, self._dirty, self._deltas, self._now = {}, set(), {}, {}
        n = 0
        for k, ev in list(self.b.scan("events", {"origin": self.origin,
                                                 "status": ["applied", "embedded", "empty"]})):
            ev.update({"status": "pending", "sids": [], "mentions": []})
            self.b.upsert("events", k, ev)
            n += 1
        while self.embed_pending(512) or self.apply_pending(1000):
            if progress:
                progress(self._ostats.get("cursor"))
        self.refresh_null()
        self.flush()
        return n

    def backfill_seedless(self, limit: int = 2000) -> int:
        """THE SEEDLESS BACKLOG (issue #33, 2026-10-14): notes minted
        before 7c77d8d (the sentence-splitter's length gate) never gained
        sids -- their event rows stand at status "empty", apply never saw
        them, no EntityState formed, and recall misses every one (note
        40418: minted 10-12, still nursery, seed none, unresolvable). The
        fix repaired the MINT side; nothing re-scanned the stored rows.
        This does: re-run _sentences_of (the fixed floor -- 'fact one' is
        one sentence now) over the empty rows; a row that now yields
        sentences returns to pending and the ordinary embed/apply flow
        embeds its recovered sentences, forms the state and lets the
        graduation happen on its own. A row the fixed splitter still
        finds nothing for stays put, honestly (its zero is real).
        Idempotent: the sweep self-empties -- a later pass recovers
        nothing. Returns the recovered count."""
        recov = []
        for key, ev in self.b.scan("events", {"origin": self.origin,
                                              "status": "empty"}):
            if self._sentences_of(ev):
                recov.append(key)
        for key in recov:
            ev = self.b.get("events", key)
            if ev is None:
                continue
            ev.update({"status": "pending", "sids": [], "mentions": []})
            self.b.upsert("events", key, ev)
        while recov and (self.embed_pending(limit) or self.apply_pending(limit)):
            pass
        return len(recov)

    def forget_many(self, nums: list[int], rebuild_over: int = 200) -> list[dict]:
        """forget() for several events; past rebuild_over it tombstones them
        all and rebuilds the origin once (cheaper than per-entity recomputes)."""
        if len(nums) <= rebuild_over:
            return [self.forget(n) or {"n": n, "missing": True} for n in nums]
        out = []
        with self._lock:
            for n in nums:
                hit = self.event_by_number(n)
                if hit is None:
                    out.append({"n": n, "missing": True})
                    continue
                key, ev = hit
                if ev.get("status") == "forgotten":
                    out.append({"n": n, "key": key, "entities": [], "already": True})
                    continue
                for ck, c in list(self.b.scan("claims", {"origin": self.origin, "event": key})):
                    c["status"] = "forgotten"
                    self.b.upsert("claims", ck, c)
                for f in ("text", "result_head", "result_tail", "served"):
                    if ev.get(f):
                        ev[f] = ""
                ev.update({"status": "forgotten", "sids": [], "mentions": [],
                           "forgotten_ts": time.time()})
                self.b.upsert("events", key, ev)
                out.append({"n": n, "key": key, "entities": [], "already": False})
            self.rebuild()
        return out

    def events_since(self, ts: float, session_prefix: str = "") -> list[dict]:
        """This origin's live events at or after ts (a session prefix narrows
        them to this seat's sessions); code events never."""
        return sorted((d for _, d in self.b.scan("events", {"origin": self.origin,
                                                            "ts": {"gte": ts}})
                       if d.get("stream") != "code" and d.get("status") != "forgotten"
                       and str(d.get("session") or "").startswith(session_prefix)),
                      key=lambda d: d.get("seq") or 0)

    def events_near(self, vec, k: int = 40, sim_min: float = 0.55) -> list[tuple[dict, float]]:
        """This origin's events with a sentence near vec (sentence ANN), best
        first, one row per event."""
        hits = dict(self.b.vector_top_k("sentences", "vec", vec, k * 4))
        if not hits:
            return []
        best: dict = {}
        for _, d in self.b.scan("entity_deltas", {"origin": self.origin, "sid": list(hits)}):
            s = hits.get(d["sid"], 0.0)
            if s >= sim_min and s > best.get(d["event"], -1):
                best[d["event"]] = s
        evs = self.b.get_multi("events", list(best))
        rows = [(evs[k2], s) for k2, s in best.items()
                if k2 in evs and evs[k2].get("status") != "forgotten"
                and evs[k2].get("stream") != "code"]
        rows.sort(key=lambda t: -t[1])
        return rows[:k]

    @_locked
    def recompute(self, eid: str) -> EntityState | None:
        """Re-derive one entity from its seed and its stored deltas (in seq
        order); back to the nursery when a word loses its distinct
        sentences."""
        old = self.get(eid)
        if old is None:
            return None
        ds = self.deltas_of(eid)
        for d in ds:
            self.b.delete("entity_deltas", f"d:{self.o}:{h16(eid)}:{d['seq']}:{d['sidx']}")
        st = EntityState(eid=eid, kind=old.kind, surface=old.surface,
                         conflicts=list(old.conflicts), activity=old.activity)
        self._states[eid] = st
        # the replay keeps each mention's ROLE (the echo split): the role
        # lives on the event doc the delta names
        _roles = {}
        for _k in {d["event"] for d in ds}:
            try:
                _roles[_k] = (self.b.get("events", _k) or {}).get("role") or ""
            except Exception:
                _roles[_k] = ""
        st.pending = [[d["sid"], d["seq"], d["sidx"], d["stream"], d["event"], d["ts"],
                       _roles.get(d["event"], "")] for d in ds]
        st.distinct = list(dict.fromkeys(d["sid"] for d in ds))
        if st.pending and len(st.distinct) >= self._mat_bar(eid):
            self._materialize(st)
        self._dirty.add(eid)
        return st

    def event_by_number(self, n: int) -> tuple[str, dict] | None:
        for k, d in self.b.scan("events", {"origin": self.origin, "n": int(n)}, limit=1):
            return k, d
        return None

    @_locked
    def changes_since(self, seq: int) -> dict[str, list[dict]]:
        """The deltas applied after event #seq, grouped by entity (in order):
        what moved since a watermark -- discourse, observations, code or an
        entity script's event alike."""
        self.flush()
        # THE INCREMENTAL READ (2026-10-10, the banner asked "since boot" every
        # round: 160k+ deltas walked and sorted each time): the same watermark
        # asked again only reads what came after the last look -- unless the
        # deltas were deleted or written out of order since (write_gen), or
        # the newest event grew more rows (then it is read again in full)
        gen = getattr(self.b, "write_gen", None)
        gen = gen.get("entity_deltas", 0) if isinstance(gen, dict) else None
        memo = self.__dict__.setdefault("_cs_memo", {})
        m = memo.get(seq)
        if m is not None and gen is not None and m["gen"] == gen:
            extra = list(self.b.scan("entity_deltas", {"origin": self.origin, "seq": {"gte": m["upto"]}},
                                     order=("seq", "sidx")))
            fresh = [d for k, d in extra if k not in m["at_upto"] and (d.get("seq") or 0) > seq]
            if not any(d.get("seq") == m["upto"] for d in fresh):
                for d in fresh:
                    self._cs_add(m["out"], d)
                if fresh:
                    m["upto"] = max(d.get("seq") or 0 for d in fresh)
                    m["at_upto"] = {k for k, d in extra if d.get("seq") == m["upto"]}
                return {e: list(rows) for e, rows in m["out"].items()}
        out: dict[str, list[dict]] = {}
        upto, at_upto = seq, set()
        for k, d in self.b.scan("entity_deltas", {"origin": self.origin, "seq": {"gt": seq}},
                                order=("seq", "sidx")):
            sq = d.get("seq") or 0
            if sq > upto:
                upto, at_upto = sq, set()
            if sq == upto:
                at_upto.add(k)
            self._cs_add(out, d)
        if gen is not None:
            memo[seq] = {"gen": gen, "upto": upto, "at_upto": at_upto, "out": out}
            while len(memo) > 4:
                memo.pop(next(iter(memo)))
        return {e: list(rows) for e, rows in out.items()}

    @staticmethod
    def _cs_add(out: dict, d: dict) -> None:
        # the event-index meta-entities ('e:<event-key>' rows, the
        # word-bar's recall route) are BOOKKEEPING, not entity
        # state -- the diff reads real entities only
        if d.get("eid", "").startswith("e:"):
            return
        # a speaker's drift is who talked, not what changed
        if d.get("eid", "").startswith(("person:", "seat:")):
            return
        out.setdefault(d["eid"], []).append(d)

    def refresh_activity(self, eid: str) -> float | None:
        """Drift between the first and second half of an entity's mentions."""
        st = self.get(eid)
        ds = self.deltas_of(eid)
        if st is None or len(ds) < 6:
            return None
        got = self.b.get_multi("sentences", [d["sid"] for d in ds])
        R = [unit(got[d["sid"]]["vec"]) for d in ds if d["sid"] in got]
        h = len(R) // 2
        st.activity = float(1.0 - unit(np.mean(R[:h], axis=0)) @ unit(np.mean(R[h:], axis=0)))
        self._dirty.add(eid)
        return st.activity

    # ---- reading ------------------------------------------------------------------------
    def stats(self, eid: str) -> dict | None:
        st = self.get(eid)
        if st is None:
            return None
        return {"eid": eid, "tier": st.tier, "n": st.n, "n_by_stream": dict(st.n_by_stream),
                "coherence": st.coherence, "coherent": self.is_coherent(st),
                "mass": st.mass, "net_align": st.net_align,
                "specialization": st.specialization(self._bg("discourse")),
                "activity": st.activity, "first_ts": st.first_ts, "last_ts": st.last_ts,
                "conflicts": list(st.conflicts)}

    def resolve(self, surface: str) -> str | None:
        """A surface string -> the id of an entity this field knows."""
        m = E.mentions_of(surface, self.graph_fn(), self.phrases)
        cands = [e for e, _ in m] + [E.canon("p", surface), E.canon("w", surface)]
        for e in cands:
            if self.get(e) is not None:
                return e
        return None

    def recall(self, vec, k: int = 8, kinds=None, tier: str = "live",
               by: str = "state", exclude_session: str | None = None,
               hop: bool = True) -> list[tuple[str, float]]:
        """THE SELF-FEEDING GUARD (the seat's #4 datum: a query's own
        topic words topped its hits -- the read fed itself): pass
        ``exclude_session`` (the CURRENT turn's session id) and
        entities whose ONLY deltas ride that session drop out -- an
        entity with evidence from an EARLIER session still hits."""
        field_name = "state"
        hits = self.b.vector_top_k(
            "entity_states", field_name, vec, k * 4,
            eq={"origin": self.origin, "tier": tier,
                **({"kind": list(kinds)} if kinds else {})})
        out = []
        # ISSUE #1 WORD-HOP: a word entity carries its word as state, so an
        # unfiltered ask (kinds=None) lets word hits score cos ~1.0 and
        # flood top-k, drowning facts. Park word kinds; after the loop hop
        # parked words through their evidence events to the entities
        # co-mentioned there; hop=False restores prior behavior.
        parked: list = []
        speak: list = []   # speaker rows: real evidence, but never above subjects
        for key, s in hits:
            d = self.b.get("entity_states", key)
            if d is None:
                continue
            _pre = d.get("eid") or key
            _pre = _pre.split(":", 1)[0]
            _kd = d.get("kind") or _pre
            if hop and _pre == "e":
                continue
            if hop and kinds is None and (_kd == "word" or _pre == "w"):
                parked.append((d.get("eid") or key, s))
                continue
            if by == "mean" and d.get("mean"):
                s = float(unit(unpack_f16(d["mean"])) @ unit(vec))
            if exclude_session:
                deltas = self.deltas_of(d["eid"])
                if deltas and all(
                        str(d2.get("sid") or "").startswith(
                            str(exclude_session)) or
                        d2.get("sid") == exclude_session
                        for d2 in deltas):
                    continue      # THIS turn's capture only: no hit
            _spe = d.get("eid") or key
            if _spe.startswith(("person:", "seat:")):
                speak.append((_spe, s))   # WHO never outranks WHAT (#1)
                continue
            out.append((d["eid"], s))
        # THE HOP (issue #1): a parked word lends a discounted share of
        # its cosine to the entities co-mentioned with it in its evidence
        # events (event docs carry mention tuples: eid, sidx, surface).
        # Fires only when the ask left room below its real entity hits;
        # a fact-rich ask stays untouched. hop=False disables it.
        n_real = len(out)  # hop guard: hopped candidates rank below real evidence
        if hop and kinds is None and parked and len(out) < k:
            hopped: dict = {}
            for weid, wscore in parked[:6]:
                cnt: dict = {}
                try:
                    rows = self.evidence(weid, k=4)
                except Exception:
                    rows = []
                for row in rows:
                    evd = self.b.get("events", row.get("event") or "")
                    for mt in (evd.get("mentions") or []):
                        mid = mt[0] if mt else ""
                        if not mid or mid == weid:
                            continue
                        if mid.startswith(("w:", "e:", "seat:")):
                            continue
                        if any(mid == oe for oe, _s in out):
                            continue
                        cnt[mid] = cnt.get(mid, 0) + 1
                for mid, c in cnt.items():
                    s2 = wscore * (0.62 if c == 1 else 0.78)
                    hopped[mid] = max(hopped.get(mid, 0.0), s2)
            take = min(len(hopped), k - len(out))
            out.extend(sorted(hopped.items(), key=lambda t: (-t[1], t[0]))[:take])
        real, hopd = out[:n_real], out[n_real:]
        real.sort(key=lambda t: (-t[1], str(t[0])))
        hopd.sort(key=lambda t: (-t[1], str(t[0])))
        speak.sort(key=lambda t: (-t[1], str(t[0])))
        return (real + speak + hopd)[:k]

    def evidence(self, eid: str, k: int = 6, query_vec=None, streams=None) -> list[dict]:
        ds = [d for d in self.deltas_of(eid) if streams is None or d["stream"] in streams]
        # THE WORD-ENTITY ROUTE (the word-delta skip's companion): a
        # word/phrase carries no per-event deltas -- its evidence is
        # rebuilt from the events' OWN mention lists (the events doc
        # carries mentions; the newest 400 events scan is the cap):
        # same shape, the delta rows replaced by mention rows.
        if not ds and eid.split(":", 1)[0] in ("w", "p"):
            synth: list[dict] = []
            for _key, ev in self.b.scan("events", {"origin": self.origin},
                                        order=("seq",), limit=400):
                if ev.get("status") == "superseded":
                    continue
                if streams is not None and ev.get("stream") not in streams:
                    continue
                for mid, msidx, _surf in ev.get("mentions") or []:
                    if mid == eid:
                        synth.append({"eid": eid, "origin": self.origin,
                                      "sid": (ev.get("sids") or [None] * (msidx + 1))[msidx],
                                      "event": _key, "stream": ev.get("stream") or "",
                                      "ts": ev.get("ts"), "seq": ev.get("seq"),
                                      "sidx": msidx})
                        break
            ds = [d for d in synth if d["sid"]]
        evs = self.b.get_multi("events", list(dict.fromkeys(d["event"] for d in ds)))
        ds = [d for d in ds if (evs.get(d["event"]) or {}).get("status") != "superseded"]
        got = self.b.get_multi("sentences", list(dict.fromkeys(d["sid"] for d in ds)))
        rows = []
        for d in ds:
            s = got.get(d["sid"])
            if s is None:
                continue
            score = float(d["seq"])
            if query_vec is not None:
                score = float(unit(s["vec"]) @ unit(query_vec))
            ev = evs.get(d["event"]) or {}
            rows.append({"sid": d["sid"], "text": s["text"], "stream": d["stream"],
                         "role": ev.get("role") or "", "n": ev.get("n"),
                         "speaker": (ev.get("speaker") or [""])[0], "tool": ev.get("tool") or "",
                         "event": d["event"], "ts": d["ts"], "seq": d["seq"], "score": score})
        uniq = {}
        for r in sorted(rows, key=lambda r: -r["score"]):
            uniq.setdefault(r["sid"], r)
        return list(uniq.values())[:k]

    def relations_of(self, eid: str, rel: str | None = None) -> list[dict]:
        from . import relations as R
        return R.of(self.b, self.origin, eid, rel)

    def now(self, session: str = "") -> np.ndarray | None:
        return self._now.get(session)

    # ---- sharing: disjoint writers, merged reads ------------------------------------
    DIVERGE_COS = 0.0          # displacements from the seed pointing apart

    def origins_of(self, eid: str) -> list[dict]:
        """Every origin's live state doc for this entity (this bucket's seats)."""
        return [d for _, d in self.b.scan("entity_states", {"eid": eid, "tier": "live"})]

    def merged(self, eid: str) -> dict | None:
        """The entity across seats: seed + the mass-weighted mean of each
        origin's displacement from it; DIVERGENCE when two seats moved it in
        opposing directions (a cross-seat conflict -- they learned different
        things about the same name)."""
        docs = self.origins_of(eid)
        if not docs:
            return None
        seed = next((unpack_f16(d["seed"]) for d in docs if d.get("seed")), None)
        if seed is None:
            return None
        disp, w, per = [], [], []
        for d in docs:
            x = unpack_f16(d["state"]) - seed
            disp.append(x)
            w.append(max(int(d.get("accr") or 0), 1))
            per.append({"origin": d["origin"], "n": int(d.get("n") or 0),
                        "n_by_stream": d.get("n_by_stream") or {}, "last_ts": d.get("last_ts")})
        W = np.asarray(w, dtype=float)
        state = unit(seed + (W[:, None] * np.asarray(disp)).sum(axis=0) / W.sum())
        diverge = []
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                a, b = disp[i], disp[j]
                na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
                if na > 1e-6 and nb > 1e-6 and float(a @ b) / (na * nb) < self.DIVERGE_COS:
                    diverge.append((docs[i]["origin"], docs[j]["origin"],
                                    round(float(a @ b) / (na * nb), 3)))
        return {"eid": eid, "state": state, "origins": per, "divergence": diverge}

    def working_set(self, vec, k: int = 8, foreign: int = 4) -> list[tuple[str, float, str]]:
        """What this seat reads near a direction: its own states, then other
        seats' states it does not hold itself. (eid, sim, origin)."""
        own = [(e, s, self.origin) for e, s in self.recall(vec, k=k)]
        have = {e for e, _, _ in own}
        out = list(own)
        if foreign:
            for key, s in self.b.vector_top_k("entity_states", "state", vec, (k + foreign) * 3,
                                              eq={"tier": "live"}):
                d = self.b.get("entity_states", key)
                if d is None or d["origin"] == self.origin or d["eid"] in have:
                    continue
                out.append((d["eid"], s, d["origin"]))
                have.add(d["eid"])
                if len(out) >= k + foreign:
                    break
        return out

    def foreign_evidence(self, eid: str, k: int = 1) -> list[dict]:
        """The newest sentences other seats were moved by on this entity."""
        rows = [d for _, d in self.b.scan("entity_deltas", {"eid": eid})
                if d.get("origin") != self.origin]
        rows.sort(key=lambda d: -(d.get("seq") or 0))
        got = self.b.get_multi("sentences", list(dict.fromkeys(d["sid"] for d in rows[:k * 4])))
        out, seen = [], set()
        for d in rows:
            s = got.get(d["sid"])
            if s is None or d["sid"] in seen:
                continue
            seen.add(d["sid"])
            out.append({"origin": d["origin"], "text": s["text"], "stream": d["stream"],
                        "ts": d.get("ts")})
            if len(out) >= k:
                break
        return out

    def geometry(self, m: int = 512, max_age_s: float = 300.0) -> dict:
        """The field's thermodynamics on entity states: the top-m live states
        by mass -> effective rank (exp of the entropy of the normalized
        squared singular values), its ratio to m, and free energy = the
        mass-weighted variety minus a conflict tax (the share of those
        entities carrying a contradicted claim). Cached."""
        cached = getattr(self, "_geom_cache", None)
        if cached and time.time() - cached[0] < max_age_s:
            return cached[1]
        rows = [d for _, d in self.b.scan(
            "entity_states", {"origin": self.origin, "tier": "live"},
            # SQL orders by the RAW counter (the index has no clock): with the
            # metabolism on, a dead-but-heavy name can occupy a top slot until
            # the weights below push it down -- only geometry consumes this
            order=(("accr", "DESC"),), limit=m) if d.get("state")]
        if len(rows) < 2:
            out = {"entities": len(rows), "eff_rank": None, "rank_ratio": None,
                   "free_energy": None, "conflict_frac": None}
        else:
            now = time.time()
            X = np.asarray([unpack_f16(d["state"]) for d in rows], dtype=float)
            sv = np.linalg.svd(X - X.mean(axis=0), compute_uv=False)
            p = sv ** 2 / max(float(np.sum(sv ** 2)), 1e-12)
            p = p[p > 1e-12]
            eff = float(np.exp(-np.sum(p * np.log(p))))
            # the effective mass weights the thermodynamics: dead names lose
            # their pull, conflicted ones their certainty (floor 1 so a state
            # still counts)
            w = np.asarray([max(mass_eff(d, now), 1.0) for d in rows], dtype=float)
            conflicted = np.asarray([1.0 if d.get("conflicts") else 0.0 for d in rows])
            cf = float((w * conflicted).sum() / w.sum())
            variety = eff / len(rows)
            out = {"entities": len(rows), "eff_rank": round(eff, 3),
                   "rank_ratio": round(variety, 4),
                   "free_energy": round(variety - cf, 4), "conflict_frac": round(cf, 4)}
        self._geom_cache = (time.time(), out)
        return out

    def vitals(self, max_age_s: float = 0.0) -> dict:
        """Counts by stream/status and tier (server-side GROUP BY over the
        composite GSI -- no doc ever crosses to the client for a number),
        so read cheaply; readers on the chat path pass max_age_s to reuse
        a recent answer."""
        cached = getattr(self, "_vitals_cache", None)
        if max_age_s and cached and time.time() - cached[0] < max_age_s:
            return cached[1]
        ev = {}
        for r in self.b.count("events", {"origin": self.origin},
                              group=("stream", "status")):
            k = f"{r.get('stream')}/{r.get('status')}"
            ev[k] = int(r.get("n") or 0)
        tiers = {}
        for r in self.b.count("entity_states", {"origin": self.origin},
                              group=("tier",)):
            tiers[r.get("tier") or "?"] = int(r.get("n") or 0)
        out = {"events": ev, "entities": tiers, "null_coherence": self.null_coherence(),
               "geometry": self.geometry(),
               "seq_last": self._ostats.get("seq_last"), "cursor": self._ostats.get("cursor")}
        self._vitals_cache = (time.time(), out)
        return out


def wire_store_names(fld, est, refresh_s: float = 60.0) -> None:
    """name_ok for the field's m: mirror: the store's entity names
    (lowercased, with and without a class head), refreshed every refresh_s
    -- and at once on a miss older than 5 s (a just-added entity)."""
    from .eids import _CLASS_HEADS
    cache = {"ts": 0.0, "names": frozenset()}

    def rebuild():
        names = set()
        for eid in list((getattr(est, "docs", None) or {}).keys()):
            b = str(eid).lower().replace("membrane::", "", 1)
            names.add(b)
            head, _, rest = b.partition(":")
            if rest and head in _CLASS_HEADS:
                names.add(rest)
        cache["names"], cache["ts"] = frozenset(names), time.time()

    def ok(bare: str) -> bool:
        b = str(bare or "").lower()
        age = time.time() - cache["ts"]
        if age > refresh_s:
            rebuild()
        elif b not in cache["names"] and age > 5.0:
            rebuild()
        return b in cache["names"]

    fld.name_ok = ok
