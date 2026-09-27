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
          importance / centrality, not entity-ness)
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
from .efstore import h16, o8, pack_f16, pack_f32, unpack_f16

EF_SCHEMA = 1
GAIN = 0.5
K_MAT = 3
ACCRETE = 0.15
BOILER_MIN = 8
CTX_MIN_RESID = 0.2
NULL_COH_DEFAULT = (0.16, 0.024)        # E23's random-set coherence (mxbai)
RECENT = 8
DISCOURSE_ROLES = ("user", "model", "reasoning", "note")


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) + 1e-12)


def vec_list(v) -> list[float]:
    """An ANN-indexed vector as JSON floats, rounded to 6 decimals (float32-
    level precision, far below embedder noise): ~2.5x smaller documents."""
    return [round(float(x), 6) for x in v]


def at_stored(v) -> np.ndarray:
    return np.asarray(vec_list(v), dtype=float)


def f32(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float32).astype(np.float64)


def fit(x, A) -> np.ndarray:
    """Non-negative-clipped least squares of x on the rows of A (E18)."""
    A = np.asarray(A, dtype=float)
    w, *_ = np.linalg.lstsq(A.T, x, rcond=None)
    w = np.clip(w, 0.0, None)
    if w.sum() <= 1e-12:
        w = np.ones(len(A)) / len(A)
    return A.T @ w


def coherence_of(rsum, n: int) -> float:
    if n < 2:
        return 0.0
    return float((float(np.dot(rsum, rsum)) - n) / (n * (n - 1)))


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
    last_pos: tuple = (-1, -1)                 # (seq, sentence index) applied
    recent: list = field(default_factory=list)
    pending: list = field(default_factory=list)  # nursery: [sid, seq, sidx, stream, ev, ts]
    distinct: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    activity: float | None = None

    @property
    def coherence(self) -> float:
        return coherence_of(self.rsum, self.n) if self.rsum is not None else 0.0

    @property
    def mass(self) -> int:
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
             "last_ts": self.last_ts, "last_pos": list(self.last_pos),
             "recent": list(self.recent), "conflicts": list(self.conflicts),
             "activity": self.activity}
        if self.tier == "nursery":
            d["pending"] = [list(p) for p in self.pending]
            d["distinct"] = list(self.distinct)
        else:
            d["state"] = vec_list(self.state)
            d["seed"] = pack_f16(self.seed)
            d["mean"] = pack_f32(self.mean) if self.mean is not None else None
            d["rsum"] = pack_f32(self.rsum)
        return d

    @classmethod
    def from_doc(cls, d: dict) -> "EntityState":
        st = cls(eid=d["eid"], kind=d.get("kind", ""), surface=d.get("surface", ""),
                 tier=d.get("tier", "nursery"), n=int(d.get("n", 0)),
                 n_by_stream=dict(d.get("n_by_stream") or {}),
                 net_align=float(d.get("net_align", 0.0)), accr=int(d.get("accr", 0)),
                 first_ts=d.get("first_ts"), last_ts=d.get("last_ts"),
                 last_pos=tuple(d.get("last_pos") or (-1, -1)),
                 recent=list(d.get("recent") or []),
                 pending=[list(p) for p in d.get("pending") or []],
                 distinct=list(d.get("distinct") or []),
                 conflicts=list(d.get("conflicts") or []), activity=d.get("activity"))
        if st.tier != "nursery":
            st.state = np.asarray(d["state"], dtype=float)
            st.seed = unpack_f16(d["seed"])
            st.mean = unpack_f16(d["mean"]) if d.get("mean") else None
            st.rsum = unpack_f16(d["rsum"])
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
                   "call_id": ev.call_id, "served": ev.served_text, "ts": ev.ts,
                   "src": ev.src, "clipped": ev.clipped, "code": ev.code,
                   "seq": seq, "status": "pending"}
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
            return ST.sentences(ev.get("text") or "")
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
                self.b.insert_if_absent("sentences", sid, {
                    "_k": "sent", "text": texts[sid][:400], "model": self.model,
                    "vec": vec_list(unit(v)), "first_ts": time.time()})
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

    def _get_or_new(self, eid: str, surface: str) -> EntityState:
        st = self.get(eid)
        if st is None:
            st = EntityState(eid=eid, kind=eid.split(":", 1)[0], surface=surface)
            self._states[eid] = st
        return st

    def _update(self, st: EntityState, x, stream: str, sid: str, seq: int,
                sidx: int, evkey: str, ts) -> None:
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
        st.n_by_stream[stream] = st.n_by_stream.get(stream, 0) + 1
        st.net_align += align
        st.accr += int(align >= ACCRETE)
        st.first_ts = st.first_ts if st.first_ts is not None else ts
        st.last_ts = ts if ts is not None else st.last_ts
        st.last_pos = pos
        st.recent = ([s for s in st.recent if s != sid] + [sid])[-RECENT:]
        self._dirty.add(st.eid)
        dkey = f"d:{self.o}:{h16(st.eid)}:{seq}:{sidx}"
        self._deltas[dkey] = {"_k": "dl", "eid": st.eid, "origin": self.origin,
                              "sid": sid, "event": evkey, "stream": stream, "ts": ts,
                              "seq": seq, "sidx": sidx, "gain": GAIN, "align": align,
                              "cos_before": float(before @ x)}

    def _materialize(self, st: EntityState) -> None:
        st.seed = self._seed(st.surface or st.eid.split(":", 1)[-1])
        st.state = at_stored(st.seed)
        st.tier = "live"
        pend = sorted(st.pending, key=lambda p: (p[1], p[2]))
        vecs = self.b.get_multi("sentences", [p[0] for p in pend])
        st.pending, st.distinct = [], []
        for sid, seq, sidx, stream, evkey, ts in pend:
            if sid in vecs:
                self._update(st, unit(vecs[sid]["vec"]), stream, sid, seq, sidx, evkey, ts)
        self._dirty.add(st.eid)

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
        A = [st.state for st in (self.get(e) for e in sorted(mentioned))
             if st is not None and st.tier == "live"]
        if A:
            xh = fit(x_msg, A)
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
            if st.tier == "nursery":
                st.pending.append([sids[sidx], seq, sidx, stream, key, ts])
                if sids[sidx] not in st.distinct:
                    st.distinct.append(sids[sidx])
                self._dirty.add(eid)
                if E.is_specific(eid) or len(st.distinct) >= K_MAT:
                    self._materialize(st)
                continue
            self._update(st, X[sidx], stream, sids[sidx], seq, sidx, key, ts)
        for i in live_idx:
            self._bg_update(stream, X[i])
        if stream == "observation":
            from . import relations as R
            f_eid = next((m[0] for m in ev.get("mentions") or [] if m[0].startswith("f:")), None)
            for subj, rel, obj in R.observation_triples(ev.get("tool") or "",
                                                        ev.get("outcome") or "", f_eid):
                R.upsert(self.b, self.origin, subj, rel, obj, "observation", key, ts)

    @_locked
    def apply_pending(self, limit: int = 200) -> int:
        if not self.b.lease(f"applier:{self.o}", self.holder, 60.0):
            return 0
        evs = list(self.b.scan("events", {"origin": self.origin, "status": "embedded"},
                               order=("seq",), limit=limit))
        for key, ev in evs:
            self._apply(key, ev)
            ev["status"] = "applied"
            self.b.upsert("events", key, ev)
            self._ostats["cursor"] = max(int(self._ostats["cursor"]), int(ev["seq"]))
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
            try:
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
            self._dirty.clear()
        if self._deltas:
            self.b.upsert_multi("entity_deltas", self._deltas)
            self._deltas = {}
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
        self._ostats = self._fresh_ostats()
        self._ostats["seq_last"], self._ostats["null_coh"] = keep_seq, null
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

    def deltas_of(self, eid: str) -> list[dict]:
        return [d for _, d in self.b.scan(
            "entity_deltas", {"origin": self.origin, "eid": eid}, order=("seq", "sidx"))]

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
        st.pending = [[d["sid"], d["seq"], d["sidx"], d["stream"], d["event"], d["ts"]] for d in ds]
        st.distinct = list(dict.fromkeys(d["sid"] for d in ds))
        if st.pending and (E.is_specific(eid) or len(st.distinct) >= K_MAT):
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
        out: dict[str, list[dict]] = {}
        for _, d in self.b.scan("entity_deltas", {"origin": self.origin, "seq": {"gt": seq}},
                                order=("seq", "sidx")):
            out.setdefault(d["eid"], []).append(d)
        return out

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
               by: str = "state") -> list[tuple[str, float]]:
        field_name = "state"
        hits = self.b.vector_top_k(
            "entity_states", field_name, vec, k * 4,
            eq={"origin": self.origin, "tier": tier,
                **({"kind": list(kinds)} if kinds else {})})
        out = []
        for key, s in hits:
            d = self.b.get("entity_states", key)
            if d is None:
                continue
            if by == "mean" and d.get("mean"):
                s = float(unit(unpack_f16(d["mean"])) @ unit(vec))
            out.append((d["eid"], s))
        out.sort(key=lambda t: -t[1])
        return out[:k]

    def evidence(self, eid: str, k: int = 6, query_vec=None, streams=None) -> list[dict]:
        ds = [d for d in self.deltas_of(eid) if streams is None or d["stream"] in streams]
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
            rows.append({"sid": d["sid"], "text": s["text"], "stream": d["stream"],
                         "event": d["event"], "ts": d["ts"], "seq": d["seq"], "_s": score})
        uniq = {}
        for r in sorted(rows, key=lambda r: -r["_s"]):
            uniq.setdefault(r["sid"], r)
        return [{k2: v for k2, v in r.items() if k2 != "_s"} for r in list(uniq.values())[:k]]

    def relations_of(self, eid: str, rel: str | None = None) -> list[dict]:
        from . import relations as R
        return R.of(self.b, self.origin, eid, rel)

    def now(self, session: str = "") -> np.ndarray | None:
        return self._now.get(session)

    def geometry(self, m: int = 512, max_age_s: float = 300.0) -> dict:
        """The field's thermodynamics on entity states: the top-m live states
        by mass -> effective rank (exp of the entropy of the normalized
        squared singular values), its ratio to m, and free energy = the
        mass-weighted variety minus a conflict tax (the share of those
        entities carrying a contradicted claim). Cached."""
        cached = getattr(self, "_geom_cache", None)
        if cached and time.time() - cached[0] < max_age_s:
            return cached[1]
        rows = [d for _, d in self.b.scan("entity_states", {"origin": self.origin, "tier": "live"})
                if d.get("state")]
        rows.sort(key=lambda d: -int(d.get("accr") or 0))
        rows = rows[:m]
        if len(rows) < 2:
            out = {"entities": len(rows), "eff_rank": None, "rank_ratio": None,
                   "free_energy": None, "conflict_frac": None}
        else:
            X = np.asarray([d["state"] for d in rows], dtype=float)
            sv = np.linalg.svd(X - X.mean(axis=0), compute_uv=False)
            p = sv ** 2 / max(float(np.sum(sv ** 2)), 1e-12)
            p = p[p > 1e-12]
            eff = float(np.exp(-np.sum(p * np.log(p))))
            w = np.asarray([max(int(d.get("accr") or 0), 1) for d in rows], dtype=float)
            conflicted = np.asarray([1.0 if d.get("conflicts") else 0.0 for d in rows])
            cf = float((w * conflicted).sum() / w.sum())
            variety = eff / len(rows)
            out = {"entities": len(rows), "eff_rank": round(eff, 3),
                   "rank_ratio": round(variety, 4),
                   "free_energy": round(variety - cf, 4), "conflict_frac": round(cf, 4)}
        self._geom_cache = (time.time(), out)
        return out

    def vitals(self, max_age_s: float = 0.0) -> dict:
        """Counts by stream/status and tier. Scans the origin's events, so
        readers on the chat path pass max_age_s to reuse a recent answer."""
        cached = getattr(self, "_vitals_cache", None)
        if max_age_s and cached and time.time() - cached[0] < max_age_s:
            return cached[1]
        ev = {}
        for _, d in self.b.scan("events", {"origin": self.origin}):
            k = f"{d.get('stream')}/{d.get('status')}"
            ev[k] = ev.get(k, 0) + 1
        tiers = {}
        for _, d in self.b.scan("entity_states", {"origin": self.origin}):
            tiers[d.get("tier")] = tiers.get(d.get("tier"), 0) + 1
        out = {"events": ev, "entities": tiers, "null_coherence": self.null_coherence(),
               "geometry": self.geometry(),
               "seq_last": self._ostats.get("seq_last"), "cursor": self._ostats.get("cursor")}
        self._vitals_cache = (time.time(), out)
        return out
