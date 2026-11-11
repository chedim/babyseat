"""STRUCTURES (#932, the operator 2026-11-05: "we need to get rid of spacy"): a sentence's blocks read word by
word from the embedder's own word vectors, not from a parser.

A word's vectors come from llama.cpp with pooling off (one vector per token, a word's pieces averaged). What
the sentence did to a word -- its in-sentence vector with the fitted meaning spill taken out (fit_spill /
unspill), minus its vector alone -- is its LEFTOVER. Each block's centroid of leftovers is learned from samples
(sentences whose words carry blocks: DOER, SELF, VERB, POSSIBLE, OBJ, PLACE, TIME, MANNER, NEG, MODAL, CAUSE,
WH_S / WH_O / WH_ADV, DET, OTHER); the blocks' transitions weigh which block comes next (Viterbi). Questions
and statements have readers of their own. A clause starts between two blocks where the samples mostly start
one. Measured on held-out LoCoMo sentences against the teacher's blocks: messages 0.70 of words, questions
0.83 (an aligned reader before: 0.61 / 0.41).

Two axes on a verb's alone vector joined with its offset, learned from the samples: happened vs possible
(the teacher's possible verbs against its done ones; held-out AUC 0.78) and past vs future of when it was
said (time words dated by dateparser; AUC 0.98).

Constants (the operator's to approve): SPILL_REACH (3 words, approved); SCALES (tried; the one that reads the
samples best is used); each block pair is counted once more than seen.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import numpy as np

SPILL_REACH = 3
SCALES = (1, 2, 5, 10, 20, 50, 100, 200)
HOLE_GRID = (0.001, 0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)   # ridge strengths tried for the hole maps; held-out samples choose (flagged)
_NOT_CONTENT = ("DET", "OTHER")
VERBS = ("VERB", "POSSIBLE")
CONTENT_BLOCKS = ("DOER", "SELF", "VERB", "OBJ", "PLACE", "TIME", "MANNER")
ASKED = {"WH_S": "DOER", "WH_O": "OBJ"}                  # where / when / how: by the wh word's meaning
_SENT = re.compile(r"[^.!?]+[.!?]*")


def _unit(X):
    X = np.asarray(X, dtype=np.float32)
    return X / (np.linalg.norm(X, axis=-1, keepdims=True) + 1e-12)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.findall(str(text or "")) if any(c.isalpha() for c in s)]


class LlamaWords:
    """The embedder's own word vectors (llama.cpp, pooling off) for a model's GGUF: one vector per word (its
    pieces averaged), words as the tokenizer starts them. Cached per text."""

    def __init__(self, gguf: str, binary: str = "llama-embedding", batch: int = 96):
        self.gguf, self.binary, self.batch, self.cache = gguf, binary, batch, {}

    def __call__(self, texts):
        todo = [t for t in dict.fromkeys(texts) if t not in self.cache]
        for i in range(0, len(todo), self.batch):
            part = todo[i:i + self.batch]
            got = self._run(part)
            if len(got) != len(part):                     # out of step: one at a time
                got = [x for t in part for x in (self._run([t]) or [([], np.zeros((0, 1), np.float32))])]
            for t, g in zip(part, got):
                self.cache[t] = g
        return [self.cache[t] for t in texts]

    def _run(self, texts):
        clean = [" ".join(t.split()) for t in texts]
        out = subprocess.run([self.binary, "-m", self.gguf, "--pooling", "none", "--embd-normalize", "-1",
                              "--embd-output-format", "json", "--verbose-prompt", "--embd-separator", "\n",
                              "-p", "\n".join(clean)], capture_output=True, text=True).stdout
        if "{\n" not in out:
            return []
        data = [np.array(d["embedding"], dtype=np.float32) for d in json.loads(out[out.index("{\n"):])["data"]]
        names = re.findall(r"^\s*\d+ -> '(.*)'$", out[:out.index("{\n")], re.M)
        res, cur = [], None
        for n, v in zip(names, data):
            s = n.strip()
            if s == "[CLS]":
                cur = []
                res.append(cur)
                continue
            if cur is None or s == "[SEP]" or not s or all(not c.isalnum() for c in s):
                continue
            if n.startswith(" ") or not cur:
                cur.append([s, [v]])
            else:
                cur[-1][0] += s
                cur[-1][1].append(v)
        res = [r for r in res if r]
        return [([w for w, _ in r], np.array([np.mean(vs, 0) for _, vs in r], dtype=np.float32)) for r in res]


def default_gguf() -> str | None:
    """mxbai's GGUF in Ollama's store (read only)."""
    import glob
    man = os.path.expanduser("~/.ollama/models/manifests/registry.ollama.ai/library/mxbai-embed-large/latest")
    try:
        dig = next(l["digest"] for l in json.load(open(man))["layers"] if l["mediaType"].endswith(".model"))
        p = glob.glob(os.path.expanduser("~/.ollama/models/blobs/") + dig.replace(":", "-"))
        return p[0] if p else None
    except Exception:
        return None


# ---- meaning spill ---------------------------------------------------------------------------------------

def fit_spill(pairs, reach: int = SPILL_REACH) -> np.ndarray:
    """A word's vector in a sentence holds some of its neighbours' meaning: fitted by least squares over
    (sentence vectors, the words' vectors alone): in-sentence ~ sum over offsets o in [-reach, reach] of c[o]
    x the alone vector o words away, plus c[shared] x the sentence's alone-vectors' mean."""
    m = 2 * reach + 2
    G = np.zeros((m, m))
    b = np.zeros(m)
    for V, A in pairs:
        V, A = _unit(V), _unit(A)
        n = len(V)
        if n < 2 or len(A) != n:
            continue
        F = np.zeros((m, n, A.shape[1]))
        for k, o in enumerate(range(-reach, reach + 1)):
            lo, hi = max(0, -o), min(n, n - o)
            if hi > lo:
                F[k, lo:hi] = A[lo + o:hi + o]
        F[-1] = A.mean(0)
        Ff = F.reshape(m, -1)
        G += Ff @ Ff.T
        b += Ff @ V.reshape(-1)
    return np.linalg.solve(G + 1e-9 * np.eye(m), b)


def unspill(V, c) -> np.ndarray:
    """Each word's own meaning in its sentence: the fitted spill taken back out (V = M U, solved for U)."""
    V = _unit(V)
    n = len(V)
    reach = (len(c) - 2) // 2
    M = np.full((n, n), c[-1] / n)
    for k, o in enumerate(range(-reach, reach + 1)):
        for i in range(n):
            if 0 <= i + o < n:
                M[i, i + o] += c[k]
    try:
        return np.linalg.solve(M, V)
    except np.linalg.LinAlgError:
        return V


# ---- the reader ------------------------------------------------------------------------------------------

class Structures:
    """The samples, and reading by them, word by word."""

    def __init__(self, words):
        self.words = words                                # texts -> [(words, vectors)]
        self.samples: list[dict] = []
        self.df: dict = {}
        self._alone: dict = {}
        self._R = None
        self._axis = self._axis_mid = self._time_axis = None
        self._asked: dict = {}
        self._time_rows: list = []

    @staticmethod
    def is_question(text: str) -> bool:
        return str(text or "").strip().endswith("?")

    def learn(self, text: str, labels: list, clauses: list, words=None, vecs=None) -> bool:
        if words is None or vecs is None:
            words, vecs = self.words([text])[0]
        if len(words) != len(labels) or len(words) < 2:
            return False
        for w in set(x.lower() for x in words):
            self.df[w] = self.df.get(w, 0) + 1
        self.samples.append({"text": text, "words": list(words), "labels": list(labels),
                             "clauses": list(clauses), "vecs": np.asarray(vecs, dtype=np.float32)})
        self._R = None
        return True

    @staticmethod
    def _qword(labels: list, words) -> str | None:
        """A question's own question word ("what", "when", ...; "how" with the word after it: "how long")."""
        if not words:
            return None
        i = next((k for k, l in enumerate(labels) if l.startswith("WH_")), None)
        if i is None or i >= len(words):
            return None
        w = str(words[i]).lower()
        return f"{w} {str(words[i + 1]).lower()}" if w == "how" and i + 1 < len(words) else w

    def _keys(self, labels: list, words=None) -> list:
        seq = tuple(r[0] for r in runs_of([("", l) for l in labels]))
        wh = next((l for l in labels if l.startswith("WH_")), None)
        qw = self._qword(labels, words)
        ks = [("word+seq", qw) + seq, ("word", qw)] if qw else []
        return ks + [("seq",) + seq, ("wh", wh)]

    # THE HOLES (2026-11-06, the operator: "every word as a function of its neighbours"; Baroni & Zamparelli's
    # functions, one per ROLE not per word): for each block, a linear map from the mean in-sentence vector of a
    # statement's other content words to the mean vector of that block's words, ridge-fitted on the samples. The
    # strength is the HOLE_GRID value that best predicts the last eighth of the samples (in learning order -- about
    # the last teaching conversation); then the map is refitted on all of them.
    def hole_vecs(self, words, vecs):
        """The vectors the hole maps live in: in-sentence ("raw"), or the reader's leftover -- spill removed, minus
        each word alone ("leftover": a word's part in this sentence, not the sentence's context)."""
        V = _unit(np.asarray(vecs, dtype=np.float32))
        if getattr(self, "hole_space", "raw") != "leftover":
            return V
        A = self._alone_of(list(words))
        R = (self._R or {}).get("statement")
        if A is None or R is None:
            return None
        return self._leftover(np.asarray(vecs, dtype=np.float32), A, R["c"])

    def _hole_rows(self, block: str):
        X, Y = [], []
        for smp in self.samples:
            labs = smp["labels"]
            if any(l.startswith("WH_") for l in labs) or block not in labs:
                continue
            V = self.hole_vecs(smp["words"], smp["vecs"])
            if V is None:
                continue
            ins = [i for i, l in enumerate(labs) if l != block and l not in _NOT_CONTENT]
            outs = [i for i, l in enumerate(labs) if l == block]
            if ins and outs:
                X.append(_unit(V[ins].mean(0)))
                Y.append(_unit(V[outs].mean(0)))
        return (np.asarray(X, dtype=np.float32), np.asarray(Y, dtype=np.float32)) if X else (None, None)

    @staticmethod
    def _ridge(X, Y, lam):
        d = X.shape[1]
        return np.linalg.solve(X.T @ X + lam * np.eye(d, dtype=np.float32), X.T @ Y).T   # W: y ~ W x

    def learn_holes(self, space: str = "raw") -> dict:
        self.hole_space = space
        if space == "leftover" and not self._R:
            self._prepare()
        self.holes = {}
        blocks = {l for smp in self.samples for l in smp["labels"]
                  if l not in _NOT_CONTENT and not l.startswith("WH_")}
        for b in sorted(blocks):
            X, Y = self._hole_rows(b)
            if X is None or len(X) < 2:
                continue
            cut = max(1, len(X) - max(1, len(X) // 8))
            best, fit = HOLE_GRID[0], -2.0
            if cut < len(X):
                for lam in HOLE_GRID:
                    W = self._ridge(X[:cut], Y[:cut], lam)
                    got = float(np.mean(np.sum(_unit(X[cut:] @ W.T) * Y[cut:], axis=1)))
                    if got > fit:
                        best, fit = lam, got
            held = slice(cut, None) if cut < len(X) else slice(None)
            base = float(np.mean(np.sum(X[held] * Y[held], axis=1)))     # no map at all: the other words as is
            tr = slice(None, cut) if cut < len(X) else slice(None)
            cen = float(np.mean(_unit(Y[tr].mean(0)) @ Y[held].T))        # the block's average: the role, no filler
            self.holes[b] = {"W": self._ridge(X, Y, best), "lam": best, "held_cos": fit, "base_cos": base,
                             "centroid_cos": cen, "n": len(X)}
        return self.holes

    def hole(self, words, vecs, labels, block: str):
        """The predicted meaning of `block` in this sentence, from its other content words (None: no map)."""
        got = (getattr(self, "holes", None) or {}).get(block)
        if got is None:
            return None
        V = self.hole_vecs(words, vecs)
        if V is None:
            return None
        ins = [i for i, l in enumerate(labels) if l != block and l not in _NOT_CONTENT and not l.startswith("WH_")]
        if not ins:
            return None
        return _unit(got["W"] @ _unit(V[ins].mean(0)))

    # THE ANSWER MAP (step C, 2026-11-06; the answer-sentence-selection idea, learned from our own labels): a linear
    # map from a question's vector to the direction of its answer sentence -- the evidence sentence minus the mean
    # of its competitors (the shared topic taken out) -- ridge-fitted on labelled (question, sentences, answer) rows
    # in order; the strength is the HOLE_GRID value whose map ranks the true sentence highest on the last eighth
    # (mean reciprocal rank), then refitted on all. A sentence's vector: the mean of its in-sentence word vectors.
    def _sent_vec(self, text: str):
        try:
            words, V = self.words([text])[0]
        except Exception:
            return None
        return _unit(_unit(np.asarray(V, dtype=np.float32)).mean(0)) if len(words) else None

    @staticmethod
    def _mrr(rows, W):
        rr = []
        for qv, S, pos in rows:
            p = _unit(W @ qv) if W is not None else qv
            sc = S @ p
            rr.append(1.0 / (1 + int(np.sum(sc > sc[pos]))))
        return float(np.mean(rr)) if rr else 0.0

    def learn_answers(self, rows) -> dict:
        data, X, Y = [], [], []
        for q, sents, pos in rows:
            qv = self._sent_vec(q)
            S = [self._sent_vec(t) for t in sents]
            if qv is None or len(S) < 2 or any(v is None for v in S):
                continue
            S = np.asarray(S, dtype=np.float32)
            X.append(qv)
            Y.append(S[pos] - np.delete(S, pos, 0).mean(0))
            data.append((qv, S, pos))
        if len(X) < 2:
            self.answers = {"W": None, "lam": None, "mrr": 0.0, "base_mrr": 0.0, "n": len(X)}
            return self.answers
        X, Y = np.asarray(X, dtype=np.float32), np.asarray(Y, dtype=np.float32)
        cut = max(1, len(X) - max(1, len(X) // 8))
        best, fit = HOLE_GRID[0], -1.0
        for lam in HOLE_GRID:
            got = self._mrr(data[cut:], self._ridge(X[:cut], Y[:cut], lam))
            if got > fit:
                best, fit = lam, got
        self.answers = {"W": self._ridge(X, Y, best), "lam": best, "mrr": fit,
                        "base_mrr": self._mrr(data[cut:], None), "n": len(X)}
        return self.answers

    def answer_score(self, question: str, sentence: str) -> float:
        """How well a sentence answers a question under the learned answer map (cosine; -1 when unlearned)."""
        A = getattr(self, "answers", None) or {}
        qv, sv = self._sent_vec(question), self._sent_vec(sentence)
        if A.get("W") is None or qv is None or sv is None:
            return -1.0
        return float(_unit(A["W"] @ qv) @ sv)

    # THE BINDER (2026-11-06, the operator: "we need a better binder design"): which verb each word belongs to, read out
    # of the in-sentence vectors by a learned probe (grammar is linearly recoverable from encoder vectors -- the
    # "structural probe" finding). Trained on the teaching samples: the teacher's clause id of a word IS its governing
    # verb, so a word's verb is the verb word holding the same id. The vectors are reduced to their own EFFECTIVE RANK
    # (exp of the singular values' entropy -- the measure the field already reports), and a logistic scorer over each
    # (word, candidate verb) pair -- their reduced vectors' product and difference, distance, verbs between, the word's
    # part -- or "no verb" picks each word's verb. Its strength: the HOLE_GRID value best on the last eighth of samples.
    BIND_PARTS = ("DOER", "SELF", "OBJ", "PLACE", "TIME", "MANNER", "CAUSE", "NEG", "MODAL", "DET", "OTHER")

    def _bind_rows(self, P, labels, verbs, i):
        n = max(1, len(labels))
        part = [1.0 if labels[i] == x else 0.0 for x in self.BIND_PARTS]
        rows = [np.concatenate([np.zeros(2 * P.shape[1]), [0, 0, 0, 0, 1.0], part])]      # "no verb"
        for j in verbs:
            lo, hi = sorted((i, j))
            between = sum(1 for v in verbs if lo < v < hi)
            rows.append(np.concatenate([P[i] * P[j], P[i] - P[j],
                                        [(j - i) / n, abs(j - i) / n, between / max(1, len(verbs)), 1.0 if j < i else 0.0, 0.0],
                                        part]))
        return np.asarray(rows, dtype=np.float32)

    def learn_binder(self, limit: int = 2000) -> dict:
        """`limit`: at most this many teaching sentences (a memory cap on the pair rows; flagged)."""
        import random
        from sklearn.linear_model import LogisticRegression
        smp = [s for s in self.samples if not any(l.startswith("WH_") for l in s["labels"])
               and any(l in VERBS for l in s["labels"])]
        if len(smp) > limit:
            smp = random.Random(0).sample(smp, limit)
        if len(smp) < 2:
            self.binder = None
            return {"rank": 0}
        X = np.concatenate([_unit(s["vecs"]) for s in smp[:4000]])
        mu = X.mean(0)
        _u, sv, vt = np.linalg.svd(X - mu, full_matrices=False)
        p = sv ** 2 / max(float(np.sum(sv ** 2)), 1e-12)
        p = p[p > 1e-12]
        k = max(1, min(int(round(float(np.exp(-np.sum(p * np.log(p)))))), vt.shape[0]))
        proj = vt[:k]

        def data(rows_of):
            Xs, ys, groups = [], [], []
            for s in rows_of:
                P = (_unit(s["vecs"]) - mu) @ proj.T
                verbs = [j for j, l in enumerate(s["labels"]) if l in VERBS]
                for i, l in enumerate(s["labels"]):
                    if l in VERBS:
                        continue
                    gold = next((j for j in verbs if s["clauses"][j] == s["clauses"][i]), None)
                    R = self._bind_rows(P, s["labels"], verbs, i)
                    y = np.zeros(len(R))
                    y[0 if gold is None else 1 + verbs.index(gold)] = 1
                    Xs.append(R)
                    ys.append(y)
                    groups.append(len(R))
            return (np.concatenate(Xs), np.concatenate(ys), groups) if Xs else (None, None, [])
        cut = max(1, len(smp) - max(1, len(smp) // 8))
        Xt, yt, _g = data(smp[:cut])
        Xh, yh, gh = data(smp[cut:])
        best, fit, model = None, -1.0, None
        for c in HOLE_GRID:
            m = LogisticRegression(C=c, max_iter=300).fit(Xt, yt)
            if Xh is None:
                best, model = c, m
                break
            pr = m.predict_proba(Xh)[:, 1]
            at, ok = 0, 0
            for g in gh:
                ok += int(np.argmax(pr[at:at + g]) == int(np.argmax(yh[at:at + g])))
                at += g
            acc = ok / max(1, len(gh))
            if acc > fit:
                best, fit, model = c, acc, m
        Xa, ya, _ga = data(smp)
        model = LogisticRegression(C=best, max_iter=300).fit(Xa, ya)
        self.binder = {"mu": mu, "proj": proj, "model": model, "rank": k, "C": best, "held_acc": fit, "n": len(smp)}
        return {k2: v for k2, v in self.binder.items() if k2 not in ("mu", "proj", "model")}

    def bind(self, words, vecs, labels) -> list:
        """For each word, the index of the verb it belongs to (a verb: itself; -1: none)."""
        B = getattr(self, "binder", None)
        verbs = [j for j, l in enumerate(labels) if l in VERBS]
        if not B or not verbs:
            return [j if l in VERBS else -1 for j, l in enumerate(labels)]
        P = (_unit(np.asarray(vecs, dtype=np.float32)) - B["mu"]) @ B["proj"].T
        out = []
        for i, l in enumerate(labels):
            if l in VERBS:
                out.append(i)
                continue
            pr = B["model"].predict_proba(self._bind_rows(P, labels, verbs, i))[:, 1]
            a = int(np.argmax(pr))
            out.append(-1 if a == 0 else verbs[a - 1])
        return out

    def learn_asked(self, labels: list, block: str, words=None) -> None:
        """A question's blocks (and its own question word) and the block its answer was found in."""
        for k in self._keys(labels, words):
            c = self._asked.setdefault(k, {})
            c[block] = c.get(block, 0) + 1

    def asked_for(self, labels: list, words=None) -> str | None:
        """The block questions like this one were most often answered in: by its question word and block order,
        else its question word, else its block order, else its question block."""
        for k in self._keys(labels, words):
            if self._asked.get(k):
                return max(self._asked[k], key=self._asked[k].get)
        return None

    def learn_time(self, words: list, vecs, at: list, days: float) -> None:
        """A sample's time words and how many days from when it was said they point (dateparser's)."""
        if at and days:
            self._time_rows.append((list(words), np.asarray(vecs, dtype=np.float32), list(at), days > 0))
            self._R = None

    def keyword(self, word: str) -> bool:
        """Rarer than the typical word of the samples' text (the median word occurrence's sentence count)."""
        if getattr(self, "_bar", None) is None:
            occ = sorted(self.df[w.lower()] for smp in self.samples for w in smp["words"])
            self._bar = occ[len(occ) // 2] if occ else 0
        return self.df.get(word.lower(), 0) < self._bar

    def _alone_of(self, words: list):
        todo = [w for w in dict.fromkeys(words) if w not in self._alone]
        if todo:
            for w, (_ws, vs) in zip(todo, self.words(todo)):
                self._alone[w] = _unit(np.mean(vs, 0)) if len(vs) else None
        A = [self._alone.get(w) for w in words]
        return None if any(a is None for a in A) else np.array(A, dtype=np.float32)

    @staticmethod
    def _leftover(V, A, c):
        return _unit(_unit(unspill(V, c)) - A)

    @staticmethod
    def _feats(V, A, c):
        """A verb's alone vector joined with its offset: the axes' features."""
        off = _unit(_unit(unspill(V, c)) - A) if len(V) > 1 else np.zeros_like(A)
        return _unit(np.hstack([A, off]))

    @staticmethod
    def _viterbi(R, V, A, lam) -> list:
        S = Structures._leftover(V, A, R["c"]) @ R["C"].T
        logT = R["logT"]
        n, m = S.shape
        best = lam * S[0] + logT[-1]
        back = []
        for t in range(1, n):
            cand = best[:, None] + logT[:m] + lam * S[t][None, :]
            back.append(cand.argmax(0))
            best = cand.max(0)
        path = [int(best.argmax())]
        for b in reversed(back):
            path.insert(0, int(b[path[0]]))
        return [R["labs"][i] for i in path]

    def _prepare(self):
        if self._R is not None:
            return
        self._R = {}
        self._bar = None
        self._alone_of([w for s in self.samples for w in s["words"]])     # every word alone, batched
        for kind in ("statement", "question"):
            rows = [(s, self._alone_of(s["words"])) for s in self.samples
                    if self.is_question(s["text"]) == (kind == "question")]
            rows = [(s, A) for s, A in rows if A is not None]
            if not rows:
                continue
            c = fit_spill([(s["vecs"], A) for s, A in rows])
            acc: dict = {}
            for s, A in rows:
                for g, v in zip(s["labels"], self._leftover(s["vecs"], A, c)):
                    acc.setdefault(g, []).append(v)
            labs = sorted(acc)
            li = {g: i for i, g in enumerate(labs)}
            T = np.ones((len(labs) + 1, len(labs)))       # each pair once more than seen: none impossible
            starts: dict = {}
            for s, _A in rows:
                prev = len(labs)
                for i, g in enumerate(s["labels"]):
                    T[prev, li[g]] += 1
                    prev = li[g]
                    if i:
                        n = starts.setdefault((s["labels"][i - 1], g), [0, 0])
                        n[int(s["clauses"][i] != s["clauses"][i - 1])] += 1
            R = {"labs": labs, "C": _unit(np.array([np.mean(acc[g], 0) for g in labs])), "c": c,
                 "logT": np.log(T / T.sum(1, keepdims=True)), "starts": starts}
            R["lam"] = max(SCALES, key=lambda lam: sum(          # the scale that reads the samples best
                sum(a == b for a, b in zip(self._viterbi(R, s["vecs"], A, lam), s["labels"])) for s, A in rows))
            self._R[kind] = R
        # the happened-vs-possible axis and the past-vs-future axis
        acc = {True: [], False: []}
        for kind, R in self._R.items():
            for s in [s for s in self.samples if self.is_question(s["text"]) == (kind == "question")]:
                A = self._alone_of(s["words"])
                if A is None:
                    continue
                for f, l in zip(self._feats(s["vecs"], A, R["c"]), s["labels"]):
                    if l in VERBS:
                        acc[l == "POSSIBLE"].append(f)
        if acc[True] and acc[False]:
            d = np.mean(acc[True], 0) - np.mean(acc[False], 0)
            self._axis = d
            self._axis_mid = (float(np.mean([f @ d for f in acc[True]])) + float(np.mean([f @ d for f in acc[False]]))) / 2
        R0 = self._R.get("statement") or next(iter(self._R.values()), None)
        tacc = {True: [], False: []}
        for words, vecs, at, fut in self._time_rows:
            A = self._alone_of(words)
            if A is not None and R0 is not None:
                F = self._feats(vecs, A, R0["c"])
                tacc[fut].append(np.mean([F[i] for i in at], 0))
        if tacc[True] and tacc[False]:
            d = np.mean(tacc[True], 0) - np.mean(tacc[False], 0)
            self._time_axis = (d, (float(np.mean([f @ d for f in tacc[True]])) +
                                   float(np.mean([f @ d for f in tacc[False]]))) / 2)

    def _reader(self, text):
        return self._R.get("question" if self.is_question(text) else "statement") or next(iter(self._R.values()), None)

    def possible(self, text: str, words: list, vecs, at: list) -> float | None:
        """How far the verbs at `at` lean toward what could / would be (higher: more possible)."""
        self._prepare()
        R, A = self._reader(text), self._alone_of(words)
        if self._axis is None or R is None or A is None or not at:
            return None
        F = self._feats(np.asarray(vecs), A, R["c"])
        return float(np.mean([F[i] @ self._axis for i in at]))

    def time_side(self, text: str, words: list, vecs, at: list) -> str | None:
        """'past' or 'future' of when it was said, for time words dateparser could not place."""
        self._prepare()
        R, A = self._reader(text), self._alone_of(words)
        if self._time_axis is None or R is None or A is None or not at:
            return None
        F = self._feats(np.asarray(vecs), A, R["c"])
        return "future" if float(np.mean([F[i] @ self._time_axis[0] for i in at])) > self._time_axis[1] else "past"

    def read(self, text: str) -> list[dict]:
        """Per sentence: {text, words, labels, clauses, possible: {clause: bool}, side: {clause: past/future}}."""
        if not self.samples:
            return []
        self._prepare()
        sents = sentences(text)
        out = []
        for s, (words, vecs) in zip(sents, self.words(sents)):
            R = self._reader(s)
            A = self._alone_of(words) if len(words) >= 2 else None
            if R is None or A is None:
                continue
            labels = self._viterbi(R, vecs, A, R["lam"])
            cl, k = [], 0
            for i, l in enumerate(labels):                # a clause starts where the samples mostly start one
                n = R["starts"].get((labels[i - 1], l), [1, 0]) if i else [1, 0]
                if i and (n[1] > n[0] or l == "CAUSE"):
                    k += 1
                cl.append(k)
            poss, side = {}, {}
            for c in set(cl):
                vs = [i for i, (l, c_) in enumerate(zip(labels, cl)) if c_ == c and l in VERBS]
                lean = self.possible(s, words, vecs, vs) if vs else None
                poss[c] = any(l in ("MODAL", "POSSIBLE") for l, c_ in zip(labels, cl) if c_ == c) or (
                    lean is not None and self._axis_mid is not None and lean > self._axis_mid)
                ts = [i for i, (l, c_) in enumerate(zip(labels, cl)) if c_ == c and l == "TIME"]
                if ts:
                    side[c] = self.time_side(s, words, vecs, ts)
            out.append({"text": s, "words": words, "said": as_said(s, words), "labels": labels, "clauses": cl,
                        "possible": poss, "side": side})
        return out


# ---- small helpers ---------------------------------------------------------------------------------------

def as_said(text: str, words: list) -> list:
    """The embedder's words (lowercased) in the case the sentence wrote them."""
    out, pos, low = [], 0, text.lower()
    for w in words:
        a = low.find(w.lower(), pos)
        if a < 0:
            out.append(w)
            continue
        out.append(text[a:a + len(w)])
        pos = a + len(w)
    return out


def drop_tails(text: str, pairs: list) -> list:
    """(word, block) pairs without a contraction's tail ("m" of "I'm", "s" of "Here's")."""
    out, pos, low = [], 0, str(text or "").lower()
    for w, l in pairs:
        a = low.find(str(w).lower(), pos)
        if a > 0 and low[a - 1] in "'’" and len(w) <= 2:
            pos = a + len(w)
            continue
        if a >= 0:
            pos = a + len(w)
        out.append((w, l))
    return out


def runs_of(pairs: list) -> list:
    """[(block, [(word, block) before it with no content block], [its words])] for the content blocks, in
    order; a possible verb is a verb."""
    out, lead, cur, lab = [], [], [], None
    for w, l in pairs:
        l = "VERB" if l in VERBS else l
        if l in CONTENT_BLOCKS:
            if l == lab and cur:
                cur.append(w)
                continue
            if cur:
                out.append((lab, lead, cur))
                lead = []
            lab, cur = l, [w]
        else:
            if cur:
                out.append((lab, lead, cur))
                cur, lab, lead = [], None, []
            if not l.startswith("WH_"):
                lead.append((w, l))
    if cur:
        out.append((lab, lead, cur))
    return out


def lemma(word: str, upos: str) -> str:
    w = word.lower()
    try:
        from lemminflect import getLemma
        got = getLemma(w, upos)
        return got[0] if got else w
    except Exception:
        return w
