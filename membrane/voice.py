"""MEMBRANE'S OWN VOICE (2026-10-09, the operator: "add membrane's own speech
as a third chat participant that me and the LLM teach").

The field is the conceptualizer; the LLM is Argus's formulator (the
language cortex). This is membrane's OWN formulator, grown the way a
child's is: by hearing words spoken while a concept state is active.

  hear       the teachers' text (the operator's messages, the LLM's
             replies, the operator's rewrites x5) feeds a weighted word
             table: (the last 1-3 words -> the next word -> weight)
  aim        the target is where the field is now plus the incoming
             message (the massage's target, massage.target)
  speak      a STEERED walk: the table proposes the next words (grammar),
             the field disposes (meaning) -- each candidate is scored by
             how much it moves the sentence's word-bag toward the target
  grade      its line vs the LLM's reply (closeness, cosine); 👍 hears the
             line as the operator's approval, 👎 unlearns it, a rewrite is
             the strongest lesson

THE COVENANTS:
  - it never hears itself: its own lines feed the table only through the
    operator's 👍 (the operator's words, then)
  - it never enters the field: its lines live in the voice ledger only --
    not in the session's messages, the LLM's history or the recall
  - it has no hands: no tools, no goals, nothing it says actuates
  - chat only (the operator / plan lanes): never a phone call, never a loop
Kill switch: MEMBRANE_VOICE=0.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path

import numpy as np

BOS = "<s>"
ENDS = {".", "?", "!"}
PUNCT = {".", ",", "?", "!", ":", ";"}
PROJ_DIM = 256
PROJ_SEED = 11
BEAM = 6
BRANCH = 10
MAX_TOKENS = 30
MIN_TOKENS = 6
MAX_SENTENCES = 2
LM_WEIGHT = 1.0
STEER_WEIGHT = 100.0
REPEAT_PENALTY = 1.5
REWRITE_WEIGHT = 5.0
VOCAB_WARM = 4000
BOOT_TEXT_CAP = 3_000_000      # characters of history the bootstrap hears (newest first)
KEY_BUDGET = 250_000           # contexts kept; past it the weakest go (~17 MB per 1 MB heard)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_\-./%]*[A-Za-z0-9%]|[A-Za-z0-9]|[.,?!:;]")


def enabled() -> bool:
    return os.environ.get("MEMBRANE_VOICE", "1") not in ("0", "false", "off")


def clean(text: str) -> str:
    """The teachers' prose without the machinery: no code, no urls, no
    markdown marks, no speaker lines, no membrane frames."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    lines = []
    for ln in t.splitlines():
        s = ln.strip()
        if not s or s.startswith(("[HA user:", "membrane:", "[membrane]", "|", ">")):
            continue
        s = re.sub(r"[*_#~]+", " ", s)
        if sum(c.isalpha() or c.isspace() for c in s) < 0.6 * len(s):
            continue
        lines.append(s)
    t = " ".join(lines)
    return re.sub(r"\s+", " ", t).strip()


def tokenize(text: str) -> list[str]:
    return _WORD.findall(clean(text))


def sentences(toks: list[str]) -> list[list[str]]:
    out, cur = [], []
    for t in toks:
        cur.append(t)
        if t in ENDS:
            out.append(cur)
            cur = []
    if len(cur) >= 3:
        out.append(cur + ["."])
    return out


def _k(ctx) -> str:
    return " ".join(w.lower() for w in ctx)


class WordTable:
    """Weighted next-word counts at 1-3 words of context (stupid backoff);
    a word's surface form is the one heard most."""

    def __init__(self):
        self.t: dict[str, dict[str, float]] = {}
        self.uni: dict[str, float] = {}
        self.form: dict[str, str] = {}
        self._formc: dict[str, float] = {}

    def observe(self, text: str, weight: float = 1.0) -> int:
        n = 0
        for s in sentences(tokenize(text)):
            seq = [BOS] + s
            for i in range(1, len(seq)):
                w = seq[i]
                lw = w.lower()
                # the surface form is learned mid-sentence (a sentence's
                # first word is capitalized by position, not by name)
                if i > 1 or lw not in self.form:
                    fk = w if i > 1 else lw
                    c = self._formc.get(fk, 0.0) + weight
                    self._formc[fk] = c
                    if c >= self._formc.get(self.form.get(lw, ""), 0.0):
                        self.form[lw] = fk
                self.uni[lw] = self.uni.get(lw, 0.0) + weight
                for k in (1, 2, 3):
                    if i - k < 0:
                        break
                    key = f"{k}|{_k(seq[i - k:i])}"
                    d = self.t.setdefault(key, {})
                    d[lw] = d.get(lw, 0.0) + weight
                n += 1
        return n

    def unlearn(self, text: str, weight: float = 1.0) -> None:
        for s in sentences(tokenize(text)):
            seq = [BOS] + s
            for i in range(1, len(seq)):
                lw = seq[i].lower()
                for k in (1, 2, 3):
                    if i - k < 0:
                        break
                    d = self.t.get(f"{k}|{_k(seq[i - k:i])}")
                    if d and lw in d:
                        d[lw] = max(0.0, d[lw] - weight)

    def nexts(self, ctx: list[str], n: int = BRANCH) -> list[tuple[str, float]]:
        """The next words (lowercase) with backoff probabilities."""
        out: dict[str, float] = {}
        scale = 1.0
        for k in (3, 2, 1):
            if len(ctx) < k:
                continue
            d = self.t.get(f"{k}|{_k(ctx[-k:])}")
            if d:
                tot = sum(d.values())
                if tot > 0:
                    for w, c in d.items():
                        if c > 0 and w not in out:
                            out[w] = scale * c / tot
            scale *= 0.4
            if len(out) >= n:
                break
        return sorted(out.items(), key=lambda x: -x[1])[:n]

    def prune(self, floor: float = 2.0, levels: str = "32") -> int:
        """Drop the 2-3 word contexts heard less than `floor` (the
        one-offs: ~2/3 of the table, little of its grammar)."""
        before = len(self.t)
        self.t = {k: d for k, d in self.t.items()
                  if not (k[0] in levels and sum(d.values()) < floor)}
        return before - len(self.t)

    def fit_budget(self, budget: int = KEY_BUDGET) -> None:
        if len(self.t) <= budget:
            return
        for levels in ("3", "32"):
            self.prune(2.0, levels)
            if len(self.t) <= budget:
                return

    def to_json(self) -> dict:
        return {"t": self.t, "uni": self.uni, "form": self.form}

    @classmethod
    def from_json(cls, d: dict) -> "WordTable":
        w = cls()
        w.t = d.get("t") or {}
        w.uni = d.get("uni") or {}
        w.form = d.get("form") or {}
        return w


class WordVecs:
    """Each word's embedder vector, projected to PROJ_DIM (cosine holds
    roughly under a seeded Gaussian projection) -- the field's geometry at
    word grain. Misses embed in one batch."""

    def __init__(self, embedder, in_dim: int = 1024):
        self.embedder = embedder
        self.P = np.random.default_rng(PROJ_SEED).standard_normal((PROJ_DIM, in_dim)) / math.sqrt(PROJ_DIM)
        self.v: dict[str, np.ndarray] = {}

    def project(self, vec) -> np.ndarray | None:
        x = np.asarray(vec, dtype=np.float64).ravel()
        if x.size != self.P.shape[1]:
            x = np.resize(x, self.P.shape[1]) if x.size else x
        if not x.size:
            return None
        y = self.P @ x
        n = float(np.linalg.norm(y))
        return (y / n).astype(np.float32) if n > 1e-12 else None

    def ensure(self, words) -> None:
        miss = [w for w in dict.fromkeys(words) if w not in self.v and w not in PUNCT and w != BOS]
        for i in range(0, len(miss), 64):
            chunk = miss[i:i + 64]
            try:
                E = self.embedder.embed(chunk)
            except Exception:
                return
            for w, e in zip(chunk, E):
                p = self.project(e)
                if p is not None:
                    self.v[w] = p

    def save(self, path: Path) -> None:
        if not self.v:
            return
        words = list(self.v)
        np.savez_compressed(str(path), words=np.array(words), M=np.stack([self.v[w] for w in words]).astype(np.float16))

    def load(self, path: Path) -> None:
        try:
            z = np.load(str(path), allow_pickle=False)
            for w, row in zip(z["words"], z["M"]):
                self.v[str(w)] = row.astype(np.float32)
        except Exception:
            pass


def _unit(x):
    n = float(np.linalg.norm(x))
    return x / n if n > 1e-12 else x


def compose(table: WordTable, vecs: WordVecs, target: np.ndarray,
            avoid: set | None = None) -> dict:
    """The steered beam walk: grammar from the table, meaning from the
    field. Returns {text, tokens, alignment}."""
    tot = sum(table.uni.values()) or 1.0

    def idf(w):
        return math.log(1.0 + tot / (1.0 + table.uni.get(w, 0.0)))

    avoid = avoid or set()
    bag = np.zeros(PROJ_DIM, dtype=np.float32)
    words: list[str] = []
    for _ in range(MAX_SENTENCES):
        beams = [(0.0, [BOS], bag.copy(), set(words))]
        done = []
        for _step in range(MAX_TOKENS):
            grown = []
            cand_words = set()
            props = []
            for b in beams:
                nx = table.nexts(b[1])
                props.append(nx)
                cand_words.update(w for w, _ in nx)
            vecs.ensure(cand_words)
            for b, nx in zip(beams, props):
                score, seq, bg, used = b
                cur = float(_unit(bg) @ target) if bg.any() else 0.0
                for w, p in nx:
                    if w == BOS or (w in PUNCT and seq[-1] in PUNCT):
                        continue
                    s = score + LM_WEIGHT * math.log(max(p, 1e-6))
                    nb = bg
                    v = vecs.v.get(w)
                    if v is not None:
                        nb = bg + idf(w) * v
                        s += STEER_WEIGHT * (float(_unit(nb) @ target) - cur)
                    if w in used and w not in PUNCT and len(w) > 3:
                        s -= REPEAT_PENALTY
                    if w in avoid and len(w) > 3:
                        s -= 0.5 * REPEAT_PENALTY
                    nseq = seq + [w]
                    if w in ENDS:
                        if sum(1 for x in nseq if x not in PUNCT and x != BOS) >= MIN_TOKENS:
                            done.append((s / (len(nseq) ** 0.6), nseq, nb))
                        continue
                    grown.append((s, nseq, nb, used | {w}))
            if not grown:
                break
            grown.sort(key=lambda x: -x[0])
            beams = grown[:BEAM]
        if not done:
            break
        done.sort(key=lambda x: -(x[0] + 4.0 * float(_unit(x[2]) @ target)))
        _, seq, nb = done[0]
        before = float(_unit(bag) @ target) if bag.any() else 0.0
        after = float(_unit(nb) @ target)
        if words and after - before < 0.01:
            break
        words += seq[1:]
        bag = nb
    text = render(table, words)
    return {"text": text, "tokens": len(words),
            "alignment": round(float(_unit(bag) @ target), 4) if bag.any() else 0.0}


def render(table: WordTable, words: list[str]) -> str:
    out = ""
    cap = True
    for w in words:
        f = table.form.get(w, w)
        if cap and f[:1].isalpha():
            f = f[:1].upper() + f[1:]
        cap = w in ENDS
        out += f if (w in PUNCT or not out) else " " + f
    return out


class Voice:
    """The third participant: the table, the vectors, the ledger."""

    def __init__(self, embedder, data_dir: str | Path | None = None):
        self.embedder = embedder
        self.dir = Path(data_dir) / "voice" if data_dir else None
        self.table = WordTable()
        self.vecs = WordVecs(embedder)
        self.lines: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._booted = False
        self._dirty = 0
        if self.dir is not None:
            self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            p = self.dir / "table.json"
            if p.exists():
                self.table = WordTable.from_json(json.load(open(p)))
                self._booted = bool(self.table.t)
            self.vecs.load(self.dir / "vecs.npz")
            lp = self.dir / "lines.jsonl"
            if lp.exists():
                for ln in open(lp):
                    try:
                        d = json.loads(ln)
                        self.lines[d["id"]] = {**self.lines.get(d["id"], {}), **d}
                    except Exception:
                        pass
        except Exception as e:
            print(f"[voice] load failed: {e}", file=sys.stderr)

    def save(self) -> None:
        if self.dir is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "table.json.tmp"
            with self._lock:
                self.table.fit_budget()
                json.dump(self.table.to_json(), open(tmp, "w"))
            os.replace(tmp, self.dir / "table.json")
            self.vecs.save(self.dir / "vecs.npz")
            self._dirty = 0
        except Exception as e:
            print(f"[voice] save failed: {e}", file=sys.stderr)

    def _ledger(self, rec: dict) -> None:
        cur = self.lines.setdefault(rec["id"], {})
        cur.update(rec)
        if self.dir is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / "lines.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    # ---- hearing -----------------------------------------------------------
    def bootstrap(self, texts) -> int:
        """The first hearing: the chat history (operator + LLM text)."""
        n, chars = 0, 0
        with self._lock:
            for t in texts:
                if chars > BOOT_TEXT_CAP:
                    break
                chars += len(t or "")
                n += self.table.observe(t)
            self.table.prune()
        top = sorted(self.table.uni.items(), key=lambda x: -x[1])[:VOCAB_WARM]
        self.vecs.ensure([w for w, _ in top])
        self._booted = True
        self.save()
        return n

    def hear(self, text: str, weight: float = 1.0) -> None:
        if not text:
            return
        with self._lock:
            self.table.observe(text, weight)
        self._dirty += 1
        if self._dirty >= 10:
            self.save()

    # ---- speaking ----------------------------------------------------------
    def speak(self, target_vec, message: str = "", sid: str = "", mid: str = "") -> dict | None:
        if not self.table.t or target_vec is None:
            return None
        t = self.vecs.project(target_vec)
        if t is None:
            return None
        with self._lock:
            out = compose(self.table, self.vecs, t, avoid={w.lower() for w in tokenize(message)})
        if not out["text"]:
            return None
        rec = {"id": "vox_" + secrets.token_hex(6), "sid": sid, "mid": mid,
               "text": out["text"], "alignment": out["alignment"],
               "time": int(time.time() * 1000)}
        self._ledger(rec)
        return rec

    # ---- grading -----------------------------------------------------------
    def grade(self, line_id: str, reply: str) -> float | None:
        """Its line vs the LLM's reply (the parent's sentence)."""
        rec = self.lines.get(line_id)
        if not rec or not reply:
            return None
        try:
            E = self.embedder.embed([rec["text"], clean(reply)[:4000] or reply[:4000]])
            a, b = np.asarray(E[0], float), np.asarray(E[1], float)
            c = round(float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)), 4)
        except Exception:
            return None
        self._ledger({"id": line_id, "closeness": c})
        return c

    def feedback(self, line_id: str, vote: str | None = None, rewrite: str | None = None) -> dict | None:
        rec = self.lines.get(line_id)
        if not rec:
            return None
        upd = {"id": line_id}
        if vote in ("up", "down") and rec.get("vote") != vote:
            with self._lock:
                if rec.get("vote") == "up":
                    self.table.unlearn(rec["text"])
                if vote == "up":
                    self.table.observe(rec["text"])
                else:
                    self.table.unlearn(rec["text"])
            upd["vote"] = vote
        if rewrite and rewrite.strip():
            self.hear(rewrite.strip(), REWRITE_WEIGHT)
            upd["rewrite"] = rewrite.strip()[:2000]
        self._ledger(upd)
        self.save()
        return self.lines.get(line_id)

    def of_session(self, sid: str) -> list[dict]:
        return sorted((r for r in self.lines.values() if r.get("sid") == sid),
                      key=lambda r: r.get("time", 0))


# ---- the seat's wiring ------------------------------------------------------
_CHAT_LANES = ("operator", "plan")


def of(srv):
    """The seat's voice (lazy; None when off or no embedder)."""
    if srv is None or not enabled():
        return None
    v = getattr(srv, "_voice", None)
    if v is not None:
        return v
    emb = getattr(getattr(srv, "membrane", None), "embedder", None)
    if emb is None:
        return None
    mp = getattr(srv, "_models_path", None)
    v = Voice(emb, Path(mp).parent if mp else None)
    srv._voice = v
    return v


def history_texts(runner):
    """The teachers' words in the chat history: the operator's and the
    LLM's text parts, chat sessions only (never the loops' or a call's)."""
    from .server import _lane_of
    sessions = getattr(getattr(runner, "sessions", None), "sessions", None) or {}
    newest = sorted(sessions.items(), key=lambda kv: -int((((kv[1] or {}).get("time") or {}).get("updated")) or 0))
    for sid, doc in newest:
        if _lane_of((doc or {}).get("agent")) not in _CHAT_LANES:
            continue
        if str((doc or {}).get("title") or "").startswith(("watch:", "Phone:")):
            continue
        try:
            rows = runner.messages.list(sid)
        except Exception:
            continue
        for r in rows or []:
            for p in r.get("parts") or []:
                if p.get("type") == "text" and not p.get("synthetic") and p.get("text"):
                    yield p["text"]


NOW_PULL = 0.25


def aim(qv, now) -> np.ndarray:
    """The voice's target: the message first; the field's present leans in
    only where it agrees with the message (the 'now' state is a residual --
    mixed in blindly at half weight it cancelled the message's pull: the
    live seat's first line aligned -0.01 with its own target)."""
    q = _unit(np.asarray(qv, dtype=float))
    if now is None:
        return q
    n = _unit(np.asarray(now, dtype=float))
    if n.shape != q.shape or float(n @ q) <= 0.0:
        return q
    return _unit(q + NOW_PULL * float(n @ q) * n)


def on_turn_start(runner, sid: str, text: str, mid: str, lane: str) -> None:
    """Speak first (a thread: never delays the LLM)."""
    srv = getattr(runner, "srv", None)
    if lane not in _CHAT_LANES or not text:
        return
    v = of(srv)
    if v is None:
        return

    if not v._booted:
        # the first hearing runs once, in the background; the voice stays
        # quiet until it has heard (a turn mid-bootstrap gets no line)
        if getattr(v, "_booting", False):
            return
        v._booting = True

        def boot():
            try:
                n = v.bootstrap(history_texts(runner))
                print(f"[voice] heard the chat history: {n} words, {len(v.table.t)} contexts",
                      file=sys.stderr)
            except Exception as e:
                print(f"[voice] bootstrap failed: {e!r}", file=sys.stderr)
            finally:
                v._booting = False

        threading.Thread(target=boot, daemon=True, name="voice-boot").start()
        return

    def go():
        try:
            from .lanes import _efield_of
            from . import massage
            fld = _efield_of(srv)
            qv = np.asarray(v.embedder.embed([clean(text) or text])[0], dtype=float)
            tgt = aim(qv, massage.field_now(fld) if fld is not None else None)
            rec = v.speak(tgt, message=text, sid=sid, mid=mid)
            if rec is not None:
                runner._voice_last = getattr(runner, "_voice_last", {})
                runner._voice_last[sid] = rec["id"]
                runner.hub.publish({"type": "membrane.voice", "sessionID": sid, "line": dict(rec)})
        except Exception as e:
            print(f"[voice] speak failed: {e!r}", file=sys.stderr)

    th = threading.Thread(target=go, daemon=True, name="voice-speak")
    runner._voice_threads = getattr(runner, "_voice_threads", {})
    runner._voice_threads[sid] = th
    th.start()


def on_turn_end(runner, sid: str, user_text: str, reply: str, lane: str) -> None:
    """The teachers speak: the operator's message and the LLM's reply are
    heard; the line it said this turn is graded against the reply."""
    srv = getattr(runner, "srv", None)
    if lane not in _CHAT_LANES:
        return
    v = of(srv)
    if v is None:
        return

    th = (getattr(runner, "_voice_threads", {}) or {}).pop(sid, None)

    def go():
        try:
            if th is not None:
                th.join(60)              # its line first, then the grade
            v.hear(user_text)
            v.hear(reply)
            lid = (getattr(runner, "_voice_last", {}) or {}).pop(sid, None)
            if lid:
                c = v.grade(lid, reply)
                if c is not None:
                    runner.hub.publish({"type": "membrane.voice", "sessionID": sid,
                                        "line": dict(v.lines.get(lid) or {})})
        except Exception as e:
            print(f"[voice] hear failed: {e!r}", file=sys.stderr)

    threading.Thread(target=go, daemon=True, name="voice-hear").start()
