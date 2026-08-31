"""intercept_check.py -- offline verification of the 4-family semantic gate.

No Ollama needed: a deterministic fake embedder embeds words as bag-of-vocab
unit vectors, so the tuned thresholds + fam margin logic run for real.

Covers:
  1. fixed-layer routing for all four families (recall/state/learn/forget)
  2. anti-seed rejection (coding turns NOT stolen)
  3. field-grown layer: operator-note exemplar activates 'learn'
  4. field cache invalidation on forget (seed exemplar removed -> layer off)
  5. --no-field-seeds kill switch (field layer ignored)
  6. degenerate geometry (anti inseparable from seeds) -> layer disables
"""
import sys
import numpy as np

sys.path.insert(0, ".")
from membrane import opencode as oc                       # noqa: E402
from membrane.field import Exemplar, ExemplarStore        # noqa: E402

VOCAB = sorted({w for s in (oc._INTERCEPT_SEEDS + oc._INTERCEPT_ANTI_SEEDS +
                            ["membrane", "field", "learn", "forget", "note",
                             "staging", "deploy", "memory", "config",
                             "retry", "health", "save", "remember"])
                for w in s.replace(":", " ").replace(",", " ").replace(
                    "=", " ").split()})
WIDX = {w: i for i, w in enumerate(VOCAB)}
D = len(VOCAB)


class FakeEmbedder:
    model = "fake-embed"

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(D)
            for w in str(t).lower().replace(":", " ").replace(
                    ",", " ").replace("=", " ").split():
                if w in WIDX:
                    v[WIDX[w]] += 1.0
            n = np.linalg.norm(v)
            out.append(v / n if n > 0 else v + 1e-9)
        return out


class FakeStore:
    def __init__(self, exemplars):
        self.exemplars = exemplars

    def __len__(self):
        return len(self.exemplars)

    def index_of(self, ex):
        return self.exemplars.index(ex)


class FakeMembrane:
    def __init__(self, exemplars=(), embedder=None):
        self.store = FakeStore(list(exemplars))
        self.embedder = embedder


class FakeSrv:
    def __init__(self, mem):
        self.membrane = mem
        self.sem_intercept = True
        self.field_seeds = True


def _note(text):
    return Exemplar(situation=text, action=text, outcome="",
                    vec=[0.0] * D, safe=True)


def reset():
    oc._SEED_CACHE.update({"matrix": None, "thr": None, "tuned": False})


passed, failed = 0, 0


def check(name, cond):
    global passed, failed
    print(("PASS " if cond else "FAIL ") + name)
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


# ---- 1+2: fixed layer, 4 families + anti rejection --------------------------
srv = FakeSrv(FakeMembrane(embedder=FakeEmbedder()))
hits = {}
for q in ["what do you remember about the retry handling",
          "how is your memory health",
          "save this to your memory: the deploy needs RUN_ENV=staging",
          "forget everything about the old config format"]:
    reset()
    h = oc._semantic_intercept_hit(srv, q)
    hits[q.split()[0]] = h[0] if h else None
check("routes recall", hits.get("what") == "recall")
check("routes state", hits.get("how") == "state")
check("routes learn", hits.get("save") == "learn")
check("routes forget", hits.get("forget") == "forget")
reset()
check("anti-seeds rejected",
      all(oc._semantic_intercept_hit(srv, a) is None
          for a in oc._INTERCEPT_ANTI_SEEDS))

# ---- 3: field-grown layer ----------------------------------------------------
reset()
note = _note("membrane: learn that the deploy script needs RUN_ENV=staging")
srv2 = FakeSrv(FakeMembrane(exemplars=[note], embedder=FakeEmbedder()))
h = oc._semantic_intercept_hit(
    srv2, "save this to your memory: the deploy script needs run env staging")
check("learn routes (fixed layer dominates here)",
      h is not None and h[0] == "learn")
# a payload-echo probe (near-verbatim the stored note) reaches the FIELD layer
h2 = oc._semantic_intercept_hit(srv2, "deploy script needs staging")
check("field layer routes learn from operator note",
      h2 is not None and h2[0] == "learn" and h2[2] == "field")

# ---- 4: forget invalidates the field cache ----------------------------------
reset()
topics = oc._field_seed_topics(srv2)
check("field topics harvested", len(topics) == 1)
srv2.membrane.store.exemplars.remove(note)
h = oc._semantic_intercept_hit(srv2, "remember that deploy needs staging env")
check("field layer de-activated after removal", h is None)

# ---- 5: kill switch ----------------------------------------------------------
reset()
srv3 = FakeSrv(FakeMembrane(exemplars=[_note(
    "membrane: learn that x")], embedder=FakeEmbedder()))
srv3.field_seeds = False
check("kill switch hides field layer",
      oc._field_seed_topics(srv3) == [] and
      oc._semantic_intercept_hit(srv3, "learn that x") is None)

# ---- 6: degenerate geometry disables the layer -------------------------------
class FlatEmbedder(FakeEmbedder):
    def embed(self, texts):
        return [np.eye(D)[0].tolist() for _ in texts]


reset()
srvf = FakeSrv(FakeMembrane(embedder=FlatEmbedder()))
check("degenerate geometry disables fixed layer",
      not oc._embed_seed_matrix(srvf) or oc._SEED_CACHE["thr"] is None)
reset()
srvff = FakeSrv(FakeMembrane(exemplars=[_note("membrane: learn that x")],
                             embedder=FlatEmbedder()))
check("degenerate geometry disables field layer",
      oc._embed_field_seed_matrix(srvff) is False)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)